"""Read-only Win32 probes. No process or power mutation APIs are loaded."""

import ctypes
import ntpath
import sys
from ctypes import wintypes
from datetime import datetime, timezone
from weakref import finalize

from .models import ProcessSample, Snapshot


class FILETIME(ctypes.Structure):
    _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

    @property
    def ticks(self) -> int:
        return (self.high << 32) | self.low


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("length", wintypes.DWORD),
        ("memory_load", wintypes.DWORD),
        ("total_phys", ctypes.c_ulonglong),
        ("avail_phys", ctypes.c_ulonglong),
        ("total_page_file", ctypes.c_ulonglong),
        ("avail_page_file", ctypes.c_ulonglong),
        ("total_virtual", ctypes.c_ulonglong),
        ("avail_virtual", ctypes.c_ulonglong),
        ("avail_extended_virtual", ctypes.c_ulonglong),
    ]


class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("page_fault_count", wintypes.DWORD),
        ("peak_working_set_size", ctypes.c_size_t),
        ("working_set_size", ctypes.c_size_t),
        ("quota_peak_paged_pool_usage", ctypes.c_size_t),
        ("quota_paged_pool_usage", ctypes.c_size_t),
        ("quota_peak_non_paged_pool_usage", ctypes.c_size_t),
        ("quota_non_paged_pool_usage", ctypes.c_size_t),
        ("pagefile_usage", ctypes.c_size_t),
        ("peak_pagefile_usage", ctypes.c_size_t),
    ]


class PERFORMANCE_INFORMATION(ctypes.Structure):
    # Win32 DWORD is always 32 bits, including when inspecting fixtures on Linux.
    _fields_ = [
        ("cb", ctypes.c_uint32),
        ("commit_total", ctypes.c_size_t),
        ("commit_limit", ctypes.c_size_t),
        ("commit_peak", ctypes.c_size_t),
        ("physical_total", ctypes.c_size_t),
        ("physical_available", ctypes.c_size_t),
        ("system_cache", ctypes.c_size_t),
        ("kernel_total", ctypes.c_size_t),
        ("kernel_paged", ctypes.c_size_t),
        ("kernel_nonpaged", ctypes.c_size_t),
        ("page_size", ctypes.c_size_t),
        ("handle_count", ctypes.c_uint32),
        ("process_count", ctypes.c_uint32),
        ("thread_count", ctypes.c_uint32),
    ]


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cb_size", wintypes.UINT), ("time", wintypes.DWORD)]


def _bind(dll, name, result, args):
    function = getattr(dll, name)
    function.restype = result
    function.argtypes = args
    return function


def _optional_bind(dll, name, result, args):
    try:
        return _bind(dll, name, result, args)
    except (AttributeError, OSError):
        return None


class WindowsProbe:
    """One-user-session probe; protected processes are silently omitted."""

    def __init__(self):
        if sys.platform != "win32":
            raise RuntimeError("Windows telemetry requires Windows")
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        user = ctypes.WinDLL("user32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        ptr = ctypes.POINTER
        self.get_system_times = _bind(kernel, "GetSystemTimes", wintypes.BOOL,
                                      [ptr(FILETIME), ptr(FILETIME), ptr(FILETIME)])
        self.memory_status = _bind(kernel, "GlobalMemoryStatusEx", wintypes.BOOL,
                                   [ptr(MEMORYSTATUSEX)])
        self.enum_processes = _bind(psapi, "EnumProcesses", wintypes.BOOL,
                                    [ptr(wintypes.DWORD), wintypes.DWORD, ptr(wintypes.DWORD)])
        self.open_process = _bind(kernel, "OpenProcess", wintypes.HANDLE,
                                  [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD])
        self.close_handle = _bind(kernel, "CloseHandle", wintypes.BOOL, [wintypes.HANDLE])
        self.process_name = _bind(kernel, "QueryFullProcessImageNameW", wintypes.BOOL,
                                  [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ptr(wintypes.DWORD)])
        self.process_memory = _bind(psapi, "GetProcessMemoryInfo", wintypes.BOOL,
                                    [wintypes.HANDLE, ptr(PROCESS_MEMORY_COUNTERS), wintypes.DWORD])
        self.process_times = _bind(kernel, "GetProcessTimes", wintypes.BOOL,
                                   [wintypes.HANDLE, ptr(FILETIME), ptr(FILETIME), ptr(FILETIME), ptr(FILETIME)])
        self.foreground_window = _bind(user, "GetForegroundWindow", wintypes.HWND, [])
        self.window_process = _bind(user, "GetWindowThreadProcessId", wintypes.DWORD,
                                    [wintypes.HWND, ptr(wintypes.DWORD)])
        self.last_input = _bind(user, "GetLastInputInfo", wintypes.BOOL, [ptr(LASTINPUTINFO)])
        self.tick_count = _bind(kernel, "GetTickCount64", ctypes.c_ulonglong, [])
        self.performance_info = _optional_bind(psapi, "GetPerformanceInfo", wintypes.BOOL,
                                               [ptr(PERFORMANCE_INFORMATION), wintypes.DWORD])
        self.create_memory_notification = _optional_bind(
            kernel, "CreateMemoryResourceNotification", wintypes.HANDLE, [ctypes.c_int])
        self.query_memory_notification = _optional_bind(
            kernel, "QueryMemoryResourceNotification", wintypes.BOOL,
            [wintypes.HANDLE, ptr(wintypes.BOOL)])
        self.windows_directory = _optional_bind(kernel, "GetWindowsDirectoryW", wintypes.UINT,
                                                [wintypes.LPWSTR, wintypes.UINT])
        self.drive_type = _optional_bind(kernel, "GetDriveTypeW", wintypes.UINT, [wintypes.LPCWSTR])
        self.disk_space = _optional_bind(kernel, "GetDiskFreeSpaceExW", wintypes.BOOL,
                                         [wintypes.LPCWSTR, ptr(ctypes.c_ulonglong),
                                          ptr(ctypes.c_ulonglong), ptr(ctypes.c_ulonglong)])
        self._previous_system: tuple[int, int] | None = None
        self._previous_processes: dict[int, tuple[int, int]] = {}
        self._notification_handle = None
        self._notification_finalizer = None
        self._notification_attempted = False

    def close(self) -> None:
        """Release the optional notification; reset allows lazy reuse afterwards."""
        cleanup = self._notification_finalizer
        self._notification_handle = None
        self._notification_finalizer = None
        if cleanup is not None:
            cleanup()

    def reset(self) -> None:
        self.close()
        self._notification_attempted = False
        self._previous_system = None
        self._previous_processes.clear()

    def _performance_memory(self) -> tuple[int | None, int | None, int | None]:
        if self.performance_info is None:
            return None, None, None
        info = PERFORMANCE_INFORMATION()
        info.cb = ctypes.sizeof(info)
        try:
            if not self.performance_info(ctypes.byref(info), info.cb) or not info.page_size:
                return None, None, None
        except OSError:
            return None, None, None
        # These counters are pages, not bytes. Cache overlaps available physical RAM.
        committed = int(info.commit_total) * int(info.page_size) if info.commit_limit else None
        limit = int(info.commit_limit) * int(info.page_size) if info.commit_limit else None
        return committed, limit, int(info.system_cache) * int(info.page_size)

    def _low_memory_signal(self) -> bool | None:
        if self.create_memory_notification is None or self.query_memory_notification is None:
            return None
        try:
            if not self._notification_attempted:
                self._notification_attempted = True
                handle = self.create_memory_notification(0)  # LowMemoryResourceNotification
                if handle:
                    self._notification_handle = handle
                    self._notification_finalizer = finalize(self, self.close_handle, handle)
            if self._notification_handle is None:
                return None
            state = wintypes.BOOL()
            if self.query_memory_notification(self._notification_handle, ctypes.byref(state)):
                return bool(state.value)
        except OSError:
            pass
        return None

    def _disk_capacity(self) -> tuple[int | None, int | None]:
        """Caller-visible capacity on the fixed Windows volume only; never enumerate."""
        if self.windows_directory is None or self.drive_type is None or self.disk_space is None:
            return None, None
        try:
            directory = ctypes.create_unicode_buffer(260)
            length = self.windows_directory(directory, len(directory))
            if not 0 < length < len(directory):
                return None, None
            drive, tail = ntpath.splitdrive(directory.value)
            if (len(drive) != 2 or drive[0] not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
                    or drive[1] != ":" or not tail.startswith("\\")):
                return None, None
            root = drive + "\\"
            if self.drive_type(root) != 3:  # DRIVE_FIXED; exclude remote/removable volumes.
                return None, None
            free, total = ctypes.c_ulonglong(), ctypes.c_ulonglong()
            if not self.disk_space(root, ctypes.byref(free), ctypes.byref(total), None):
                return None, None
            if not total.value or free.value > total.value:
                return None, None
            return int(total.value), int(free.value)
        except OSError:
            return None, None

    def _system(self) -> tuple[int, int, int, int]:
        idle, kernel, user = FILETIME(), FILETIME(), FILETIME()
        if not self.get_system_times(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
            raise ctypes.WinError(ctypes.get_last_error())
        memory = MEMORYSTATUSEX()
        memory.length = ctypes.sizeof(memory)
        if not self.memory_status(ctypes.byref(memory)):
            raise ctypes.WinError(ctypes.get_last_error())
        return idle.ticks, kernel.ticks + user.ticks, memory.total_phys, memory.avail_phys

    def _pids(self) -> list[int]:
        count = 1024
        while count <= 32768:
            ids = (wintypes.DWORD * count)()
            needed = wintypes.DWORD()
            if not self.enum_processes(ids, ctypes.sizeof(ids), ctypes.byref(needed)):
                raise ctypes.WinError(ctypes.get_last_error())
            if needed.value < ctypes.sizeof(ids):
                return [pid for pid in ids[:needed.value // ctypes.sizeof(wintypes.DWORD)] if pid]
            count *= 2
        raise RuntimeError("Process enumeration exceeded 32768 entries")

    def _process(self, pid: int) -> tuple[str, int, int, int] | None:
        handle = self.open_process(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return None
        try:
            path = ctypes.create_unicode_buffer(32768)
            length = wintypes.DWORD(len(path))
            if not self.process_name(handle, 0, path, ctypes.byref(length)):
                return None
            memory = PROCESS_MEMORY_COUNTERS()
            memory.cb = ctypes.sizeof(memory)
            if not self.process_memory(handle, ctypes.byref(memory), memory.cb):
                return None
            created, exited, kernel, user = FILETIME(), FILETIME(), FILETIME(), FILETIME()
            if not self.process_times(handle, ctypes.byref(created), ctypes.byref(exited),
                                      ctypes.byref(kernel), ctypes.byref(user)):
                return None
            return ntpath.basename(path.value), int(memory.working_set_size), created.ticks, kernel.ticks + user.ticks
        finally:
            self.close_handle(handle)

    def _foreground(self) -> int | None:
        window = self.foreground_window()
        if not window:
            return None
        pid = wintypes.DWORD()
        self.window_process(window, ctypes.byref(pid))
        return pid.value or None

    def _idle_seconds(self) -> float | None:
        last = LASTINPUTINFO()
        last.cb_size = ctypes.sizeof(last)
        if not self.last_input(ctypes.byref(last)):
            return None
        elapsed_ms = ((self.tick_count() & 0xFFFFFFFF) - last.time) & 0xFFFFFFFF
        return round(elapsed_ms / 1000, 1)

    def sample(self, *, track_processes: bool, track_foreground: bool,
               max_processes: int) -> Snapshot:
        idle, total, memory_total, memory_available = self._system()
        previous = self._previous_system
        total_delta = total - previous[1] if previous else 0
        idle_delta = idle - previous[0] if previous else 0
        cpu = max(0.0, min(100.0, 100 * (1 - idle_delta / total_delta))) if total_delta > 0 else 0.0
        self._previous_system = (idle, total)
        commit_total, commit_limit, system_cache = self._performance_memory()
        low_memory = self._low_memory_signal()
        disk_total, disk_free = self._disk_capacity()

        foreground_pid = self._foreground() if track_foreground else None
        idle_seconds = self._idle_seconds() if track_foreground else None
        current: dict[int, tuple[int, int]] = {}
        observed: list[ProcessSample] = []
        foreground_app = None
        pids = self._pids() if track_processes else ([foreground_pid] if foreground_pid else [])
        for pid in pids:
            info = self._process(pid)
            if info is None:
                continue
            name, working_set, created, process_ticks = info
            if pid == foreground_pid:
                foreground_app = name
            if not track_processes:
                continue
            old = self._previous_processes.get(pid)
            process_delta = process_ticks - old[1] if old and old[0] == created else 0
            percent = max(0.0, min(100.0, 100 * process_delta / total_delta)) if total_delta > 0 else 0.0
            current[pid] = (created, process_ticks)
            observed.append(ProcessSample(pid, name, round(percent, 2), working_set, created))
        self._previous_processes = current
        observed.sort(key=lambda item: (item.cpu_percent, item.working_set_bytes), reverse=True)
        selected = observed[:max_processes]
        if foreground_pid and track_processes and not any(item.pid == foreground_pid for item in selected):
            active = next((item for item in observed if item.pid == foreground_pid), None)
            if active:
                selected = selected[:max_processes - 1] + [active]
        return Snapshot(
            observed_at=datetime.now(timezone.utc).isoformat(),
            cpu_percent=round(cpu, 2),
            memory_total_bytes=memory_total,
            memory_available_bytes=memory_available,
            foreground_pid=foreground_pid,
            foreground_app=foreground_app,
            idle_seconds=idle_seconds,
            processes=selected,
            memory_commit_total_bytes=commit_total,
            memory_commit_limit_bytes=commit_limit,
            memory_system_cache_bytes=system_cache,
            memory_low_signal=low_memory,
            disk_total_bytes=disk_total,
            disk_free_bytes=disk_free,
        )
