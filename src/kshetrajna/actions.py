"""Narrow, explicit action boundary. Only one process priority class is changed."""

import ctypes
import ntpath
import os
from ctypes import wintypes

from .intelligence import PRIORITY_ALLOWLIST
from .telemetry import FILETIME, WindowsProbe, _bind

NORMAL = 0x20
BELOW_NORMAL = 0x4000


class SimulatedPriorityDriver:
    simulated = True

    def prepare(self, target):
        return {**target, "original_priority": NORMAL, "applied_priority": BELOW_NORMAL}

    def apply(self, plan):
        pass

    def restore(self, plan):
        return "rolled_back"


class WindowsPriorityDriver:
    simulated = False

    def __init__(self):
        self.probe = WindowsProbe()
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.get_priority = _bind(kernel, "GetPriorityClass", wintypes.DWORD, [wintypes.HANDLE])
        self.set_priority = _bind(kernel, "SetPriorityClass", wintypes.BOOL, [wintypes.HANDLE, wintypes.DWORD])
        self.get_session = _bind(kernel, "ProcessIdToSessionId", wintypes.BOOL,
                                 [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)])

    def _session(self, pid):
        value = wintypes.DWORD()
        if not self.get_session(pid, ctypes.byref(value)):
            raise ctypes.WinError(ctypes.get_last_error())
        return value.value

    def _open_verified(self, target, access=0x1200):
        if (target["name"].casefold() not in PRIORITY_ALLOWLIST or
                target["pid"] in (0, 4, os.getpid()) or not target.get("created_ticks")):
            raise ValueError("Target is outside the permitted application list")
        if self._session(target["pid"]) != self._session(os.getpid()):
            raise ValueError("Only this interactive session is eligible")
        handle = self.probe.open_process(access, False, target["pid"])
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            path = ctypes.create_unicode_buffer(32768)
            length = wintypes.DWORD(len(path))
            created, exited, kernel, user = FILETIME(), FILETIME(), FILETIME(), FILETIME()
            if not self.probe.process_name(handle, 0, path, ctypes.byref(length)):
                raise ctypes.WinError(ctypes.get_last_error())
            if not self.probe.process_times(handle, ctypes.byref(created), ctypes.byref(exited),
                                             ctypes.byref(kernel), ctypes.byref(user)):
                raise ctypes.WinError(ctypes.get_last_error())
            if created.ticks != target["created_ticks"] or ntpath.basename(path.value).casefold() != target["name"].casefold():
                raise ValueError("Process identity changed; refusing to act on a reused PID")
            return handle
        except BaseException:
            self.probe.close_handle(handle)
            raise

    def _background_only(self, target):
        foreground = self.probe._foreground()
        if foreground is None:
            raise ValueError("No interactive foreground window; live trial unavailable")
        if foreground == target["pid"]:
            raise ValueError("The target is now foreground; refresh the recommendation")

    def prepare(self, target):
        self._background_only(target)
        handle = self._open_verified(target)
        try:
            priority = self.get_priority(handle)
            if priority != NORMAL:
                raise ValueError("Only an application currently at Normal priority is eligible")
            return {**target, "original_priority": NORMAL, "applied_priority": BELOW_NORMAL}
        finally:
            self.probe.close_handle(handle)

    def apply(self, plan):
        self._background_only(plan)
        handle = self._open_verified(plan)
        try:
            if self.get_priority(handle) != plan["original_priority"]:
                raise ValueError("Priority changed since preparation")
            if not self.set_priority(handle, BELOW_NORMAL):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            self.probe.close_handle(handle)

    def restore(self, plan):
        try:
            handle = self._open_verified(plan)
        except OSError as error:
            if getattr(error, "winerror", None) in (87, 1168):
                return "target_exited"
            raise
        except ValueError as error:
            if "identity changed" in str(error):
                return "target_exited"
            raise
        try:
            current = self.get_priority(handle)
            if current == NORMAL:
                return "rolled_back"
            if not current:
                raise ctypes.WinError(ctypes.get_last_error())
            if current != BELOW_NORMAL:
                return "superseded"
            if not self.set_priority(handle, NORMAL):
                raise ctypes.WinError(ctypes.get_last_error())
            return "rolled_back"
        finally:
            self.probe.close_handle(handle)
