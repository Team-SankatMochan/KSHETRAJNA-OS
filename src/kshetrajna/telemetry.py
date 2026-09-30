"""Read-only Win32 probes. No process or power mutation APIs are loaded."""

import ctypes
import ntpath
import sys
from ctypes import wintypes
from datetime import datetime, timezone

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


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cb_size", wintypes.UINT), ("time", wintypes.DWORD)]


def _bind(dll, name, result, args):
    function = getattr(dll, name)
    function.restype = result
    function.argtypes = args
    return function


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
        self._previous_system: tuple[int, int] | None = None
        self._previous_processes: dict[int, tuple[int, int]] = {}

    def reset(self) -> None:
        self._previous_system = None
        self._previous_processes.clear()

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
        )
