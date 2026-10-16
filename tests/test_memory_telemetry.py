import ctypes
import gc
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from kshetrajna.diagnostics import abi_checks, live_check
from kshetrajna.models import ProcessSample, Snapshot
from kshetrajna.storage import EventStore
from kshetrajna.telemetry import PERFORMANCE_INFORMATION, WindowsProbe


GIB = 1024**3
OPTIONAL = ("memory_commit_total_bytes", "memory_commit_limit_bytes",
            "memory_system_cache_bytes", "memory_low_signal", "disk_total_bytes", "disk_free_bytes")


class FakeNative:
    """Controlled function results, not evidence of native Win32 compatibility."""

    def __init__(self):
        self.ticks = 0
        self.kernel = SimpleNamespace(**{name: Mock(return_value=0) for name in (
            "GetSystemTimes", "GlobalMemoryStatusEx", "OpenProcess", "CloseHandle",
            "QueryFullProcessImageNameW", "GetProcessTimes", "GetTickCount64",
            "CreateMemoryResourceNotification", "QueryMemoryResourceNotification",
            "GetWindowsDirectoryW", "GetDriveTypeW", "GetDiskFreeSpaceExW")})
        self.user = SimpleNamespace(**{name: Mock(return_value=0) for name in (
            "GetForegroundWindow", "GetWindowThreadProcessId", "GetLastInputInfo")})
        self.psapi = SimpleNamespace(**{name: Mock(return_value=0) for name in (
            "EnumProcesses", "GetProcessMemoryInfo", "GetPerformanceInfo")})
        self.kernel.GetSystemTimes.side_effect = self.system_times
        self.kernel.GlobalMemoryStatusEx.side_effect = self.physical
        self.psapi.GetPerformanceInfo.side_effect = self.performance
        self.kernel.CreateMemoryResourceNotification.return_value = 0x100000007
        self.kernel.QueryMemoryResourceNotification.side_effect = self.low_memory
        self.kernel.GetWindowsDirectoryW.side_effect = self.windows_directory
        self.kernel.GetDriveTypeW.return_value = 3
        self.kernel.GetDiskFreeSpaceExW.side_effect = self.disk_space
        self.kernel.CloseHandle.return_value = 1

    def load(self, name, **kwargs):
        return {"kernel32": self.kernel, "user32": self.user, "psapi": self.psapi}[name]

    def system_times(self, idle, kernel, user):
        self.ticks += 100
        idle._obj.low, kernel._obj.low, user._obj.low = self.ticks // 5, self.ticks, self.ticks
        return 1

    def physical(self, pointer):
        pointer._obj.total_phys, pointer._obj.avail_phys = 16 * GIB, 8 * GIB
        return 1

    def performance(self, pointer, size):
        info = pointer._obj
        if info.cb != ctypes.sizeof(PERFORMANCE_INFORMATION) or size != info.cb:
            raise AssertionError("Incorrect PERFORMANCE_INFORMATION size")
        info.page_size = 4096
        info.commit_total, info.commit_limit = 2_000_000, 8_000_000
        info.system_cache = 1_500_000
        return 1

    def low_memory(self, handle, pointer):
        pointer._obj.value = 0
        return 1

    def windows_directory(self, buffer, size):
        buffer.value = "C:\\Windows"
        return len(buffer.value)

    def disk_space(self, root, free, total, total_free):
        if root != "C:\\":
            raise AssertionError("Unexpected volume lookup")
        free._obj.value, total._obj.value = 100 * GIB, 500 * GIB
        return 1


class MemoryTelemetryTests(unittest.TestCase):
    def make_probe(self, native=None):
        native = native or FakeNative()
        with patch("kshetrajna.telemetry.sys.platform", "win32"), \
             patch("kshetrajna.telemetry.ctypes.WinDLL", native.load, create=True):
            probe = WindowsProbe()
        self.addCleanup(probe.close)
        return probe, native

    def sample(self, probe):
        return probe.sample(track_processes=False, track_foreground=False, max_processes=12)

    def test_supported_bindings_and_page_conversion_above_four_gib(self):
        probe, native = self.make_probe()
        sample = self.sample(probe)
        self.assertEqual(sample.memory_commit_total_bytes, 8_192_000_000)
        self.assertEqual(sample.memory_commit_limit_bytes, 32_768_000_000)
        self.assertEqual(sample.memory_system_cache_bytes, 6_144_000_000)
        self.assertEqual(sample.memory_available_bytes, 8 * GIB)
        self.assertEqual(sample.memory_total_bytes, 16 * GIB)
        self.assertIs(sample.memory_low_signal, False)
        self.assertEqual(sample.disk_free_bytes, 100 * GIB)
        self.assertEqual(sample.disk_total_bytes, 500 * GIB)
        self.assertEqual(native.psapi.GetPerformanceInfo.restype, wintypes.BOOL)
        self.assertEqual(native.psapi.GetPerformanceInfo.argtypes,
                         [ctypes.POINTER(PERFORMANCE_INFORMATION), wintypes.DWORD])
        self.assertEqual(ctypes.sizeof(PERFORMANCE_INFORMATION),
                         104 if ctypes.sizeof(ctypes.c_void_p) == 8 else 56)
        self.assertEqual(native.kernel.CreateMemoryResourceNotification.restype, wintypes.HANDLE)
        self.assertEqual(native.kernel.QueryMemoryResourceNotification.argtypes,
                         [wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)])
        self.assertNotIn("C:\\", str(sample.to_dict()))

    def test_performance_failure_preserves_physical_and_cpu_and_low_signal(self):
        probe, native = self.make_probe()
        self.sample(probe)
        for failure in (0, OSError("unavailable")):
            with self.subTest(failure=failure):
                if isinstance(failure, Exception):
                    native.psapi.GetPerformanceInfo.side_effect = failure
                else:
                    native.psapi.GetPerformanceInfo.side_effect = None
                    native.psapi.GetPerformanceInfo.return_value = failure
                sample = self.sample(probe)
                self.assertEqual(sample.cpu_percent, 90)
                self.assertEqual(sample.memory_total_bytes, 16 * GIB)
                self.assertEqual(sample.memory_available_bytes, 8 * GIB)
                self.assertIsNone(sample.memory_commit_total_bytes)
                self.assertIsNone(sample.memory_commit_limit_bytes)
                self.assertIsNone(sample.memory_system_cache_bytes)
                self.assertIs(sample.memory_low_signal, False)

    def test_missing_optional_exports_do_not_block_legacy_collection(self):
        native = FakeNative()
        del native.psapi.GetPerformanceInfo
        del native.kernel.CreateMemoryResourceNotification
        del native.kernel.QueryMemoryResourceNotification
        del native.kernel.GetDiskFreeSpaceExW
        probe, _ = self.make_probe(native)
        sample = self.sample(probe)
        for name in OPTIONAL:
            self.assertIsNone(getattr(sample, name))
        self.assertEqual(sample.memory_available_bytes, 8 * GIB)

    def test_invalid_page_size_is_unavailable_not_zero(self):
        probe, native = self.make_probe()
        def invalid(pointer, size):
            native.performance(pointer, size)
            pointer._obj.page_size = 0
            return 1
        native.psapi.GetPerformanceInfo.side_effect = invalid
        sample = self.sample(probe)
        self.assertIsNone(sample.memory_commit_total_bytes)
        self.assertIsNone(sample.memory_system_cache_bytes)

    def test_notification_is_reused_reset_closed_once_and_reopened_lazily(self):
        probe, native = self.make_probe()
        native.kernel.CreateMemoryResourceNotification.assert_not_called()
        self.sample(probe)
        self.sample(probe)
        native.kernel.CreateMemoryResourceNotification.assert_called_once_with(0)
        self.assertEqual(native.kernel.QueryMemoryResourceNotification.call_count, 2)
        probe.reset()
        native.kernel.CloseHandle.assert_called_once_with(0x100000007)
        self.assertIsNone(probe._previous_system)
        self.sample(probe)
        self.assertEqual(native.kernel.CreateMemoryResourceNotification.call_count, 2)
        probe.close()
        probe.close()
        self.assertEqual(native.kernel.CloseHandle.call_count, 2)

    def test_notification_creation_and_query_failure_remain_unknown(self):
        probe, native = self.make_probe()
        native.kernel.CreateMemoryResourceNotification.return_value = None
        for _ in range(3):
            self.assertIsNone(self.sample(probe).memory_low_signal)
        native.kernel.CreateMemoryResourceNotification.assert_called_once()
        native.kernel.QueryMemoryResourceNotification.assert_not_called()
        native.kernel.CloseHandle.assert_not_called()
        probe.reset()
        native.kernel.CreateMemoryResourceNotification.return_value = 29
        native.kernel.QueryMemoryResourceNotification.side_effect = OSError("private detail")
        self.assertIsNone(self.sample(probe).memory_low_signal)
        native.kernel.QueryMemoryResourceNotification.side_effect = lambda h, p: 0
        self.assertIsNone(self.sample(probe).memory_low_signal)
        native.kernel.QueryMemoryResourceNotification.side_effect = lambda h, p: setattr(p._obj, "value", 1) or 1
        self.assertIs(self.sample(probe).memory_low_signal, True)
        self.assertEqual(native.kernel.CreateMemoryResourceNotification.call_count, 2)

    def test_finalizer_releases_notification_without_retaining_probe(self):
        native = FakeNative()
        with patch("kshetrajna.telemetry.sys.platform", "win32"), \
             patch("kshetrajna.telemetry.ctypes.WinDLL", native.load, create=True):
            probe = WindowsProbe()
        self.sample(probe)
        del probe
        gc.collect()
        native.kernel.CloseHandle.assert_called_once_with(0x100000007)

    def test_disk_probe_rejects_network_removable_and_truncated_paths(self):
        for directory, kind in (("\\\\server\\share\\Windows", 3), ("C:\\Windows", 2),
                                ("C:Windows", 3), ("C:\\Windows", 4)):
            with self.subTest(directory=directory, kind=kind):
                probe, native = self.make_probe()
                def windows_directory(buffer, size):
                    buffer.value = directory
                    return len(directory)
                native.kernel.GetWindowsDirectoryW.side_effect = windows_directory
                native.kernel.GetDriveTypeW.return_value = kind
                sample = self.sample(probe)
                self.assertIsNone(sample.disk_free_bytes)
                self.assertIsNone(sample.disk_total_bytes)
                native.kernel.GetDiskFreeSpaceExW.assert_not_called()
        probe, native = self.make_probe()
        native.kernel.GetWindowsDirectoryW.side_effect = lambda buffer, size: size
        self.assertIsNone(self.sample(probe).disk_free_bytes)
        native.kernel.GetDiskFreeSpaceExW.assert_not_called()

    def test_disk_failure_is_optional_and_does_not_discard_memory(self):
        probe, native = self.make_probe()
        native.kernel.GetDiskFreeSpaceExW.side_effect = OSError("private path")
        sample = self.sample(probe)
        self.assertIsNone(sample.disk_free_bytes)
        self.assertEqual(sample.memory_commit_total_bytes, 8_192_000_000)

    def test_diagnostic_closes_probe_on_failure_and_reports_optional_availability(self):
        sample = Snapshot(datetime.now(timezone.utc).isoformat(), 20, 16 * GIB, 8 * GIB,
                          None, None, None, [ProcessSample(1, "private.exe", 1, GIB)],
                          memory_low_signal=False)
        probe = Mock()
        probe.sample.return_value = sample
        probe._process.return_value = None
        with patch("kshetrajna.diagnostics.WindowsProbe", return_value=probe), \
             patch("kshetrajna.diagnostics.time.sleep"):
            result = live_check(samples=2)
        probe.close.assert_called_once()
        self.assertEqual(result["optional_counter_samples"]["memory_low_signal"], 2)
        self.assertEqual(result["optional_counter_samples"]["memory_commit_total_bytes"], 0)
        self.assertIsNone(result["health"]["commit"]["total_bytes"])
        self.assertNotIn("private.exe", str(result))
        probe.reset_mock()
        probe.sample.side_effect = OSError("sample failed")
        with patch("kshetrajna.diagnostics.WindowsProbe", return_value=probe):
            with self.assertRaises(OSError):
                live_check(samples=2)
        probe.close.assert_called_once()

    @unittest.skipUnless(sys.platform == "win32", "Native Windows ABI requires Windows")
    def test_native_performance_information_layout(self):
        result = abi_checks()
        self.assertEqual(result["status"], "passed")
        expected_size = 104 if ctypes.sizeof(ctypes.c_void_p) == 8 else 56
        self.assertEqual(result["actual"]["PERFORMANCE_INFORMATION"], expected_size)
        self.assertEqual(result["actual_offsets"], result["expected_offsets"])


class MemoryPersistenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "events.db"

    def test_positional_snapshot_and_process_callers_are_unchanged(self):
        process = ProcessSample(12, "app.exe", 3, 900, 42)
        sample = Snapshot("2026-10-16T12:00:00+00:00", 20, 16 * GIB, 8 * GIB,
                          12, "app.exe", 0, [process])
        self.assertEqual(sample.processes, [process])
        self.assertEqual(sample.foreground_app, "app.exe")
        for name in OPTIONAL:
            self.assertIsNone(sample.to_dict()[name])
        store = EventStore(self.path)
        store.record(sample, [])
        self.assertEqual(store.latest()["processes"][0]["created_ticks"], 42)

    def test_nullable_legacy_migration_preserves_old_rows_and_new_round_trip(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TABLE snapshots (id INTEGER PRIMARY KEY, observed_at TEXT NOT NULL, "
                       "cpu_percent REAL NOT NULL, memory_total_bytes INTEGER NOT NULL, "
                       "memory_available_bytes INTEGER NOT NULL, foreground_pid INTEGER, "
                       "foreground_app TEXT, idle_seconds REAL)")
            db.execute("INSERT INTO snapshots VALUES (1,?,?,?,?,?,?,?)",
                       ("2026-10-16T11:00:00+00:00", 25, 16 * GIB, 8 * GIB, None, None, None))
        store = EventStore(self.path)
        for row in (store.latest(), store.history()[0], store.observations()[0]):
            for name in OPTIONAL:
                self.assertIsNone(row[name])
            self.assertEqual(row["cpu_percent"], 25)
        for signal in (False, True, None):
            sample = Snapshot(datetime.now(timezone.utc).isoformat(), 20, 16 * GIB, 8 * GIB,
                              None, None, None, memory_commit_total_bytes=12 * GIB,
                              memory_commit_limit_bytes=32 * GIB,
                              memory_system_cache_bytes=6 * GIB, memory_low_signal=signal,
                              disk_total_bytes=500 * GIB, disk_free_bytes=100 * GIB)
            store.record(sample, [])
            store = EventStore(self.path)  # migration must also be idempotent
            for row in (store.latest(), store.history()[-1], store.observations()[-1]):
                for name in OPTIONAL:
                    self.assertEqual(row[name], getattr(sample, name))
                self.assertIs(row["memory_low_signal"], signal)
        self.assertEqual(len(store.history()), 4)


if __name__ == "__main__":
    unittest.main()
