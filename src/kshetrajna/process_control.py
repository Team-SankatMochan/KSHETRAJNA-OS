"""User-reviewed application closure. Low CPU never establishes safe termination."""

import ctypes
import copy
import ntpath
import os
import secrets
import time
from ctypes import wintypes
from datetime import datetime, timezone

from .telemetry import FILETIME, WindowsProbe, _bind
from .config import TELEMETRY_PERMISSIONS, opaque_id

APP_ALLOWLIST = frozenset({"chrome.exe", "msedge.exe", "firefox.exe", "code.exe",
                          "notepad.exe", "notepad++.exe", "discord.exe", "spotify.exe",
                          "steam.exe", "vlc.exe"})
UNDO = "Closing or terminating this process has no undo. Save work first. Child processes may remain."


class WindowsProcessDriver:
    def __init__(self):
        self.probe = WindowsProbe()
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        self.session = _bind(self.kernel, "ProcessIdToSessionId", wintypes.BOOL,
                             [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)])
        self.critical = _bind(self.kernel, "IsProcessCritical", wintypes.BOOL,
                              [wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)])
        self.open_token = _bind(self.advapi, "OpenProcessToken", wintypes.BOOL,
                                [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)])
        self.token_info = _bind(self.advapi, "GetTokenInformation", wintypes.BOOL,
                                [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                 wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)])
        self.equal_sid = _bind(self.advapi, "EqualSid", wintypes.BOOL, [ctypes.c_void_p, ctypes.c_void_p])
        self.current_process = _bind(self.kernel, "GetCurrentProcess", wintypes.HANDLE, [])

    def _sid_buffer(self, process):
        token = wintypes.HANDLE()
        if not self.open_token(process, 0x0008, ctypes.byref(token)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            needed = wintypes.DWORD()
            self.token_info(token, 1, None, 0, ctypes.byref(needed))  # TokenUser
            if not 0 < needed.value <= 65536:
                raise ValueError("Process ownership is unavailable")
            buffer = ctypes.create_string_buffer(needed.value)
            if not self.token_info(token, 1, buffer, len(buffer), ctypes.byref(needed)):
                raise ctypes.WinError(ctypes.get_last_error())
            return buffer
        finally:
            self.probe.close_handle(token)

    def _verified(self, target, *, force):
        if (target["name"].casefold() not in APP_ALLOWLIST or
                target["pid"] in (0, 4, os.getpid()) or not target.get("created_ticks")):
            raise ValueError("This process is outside the application control list")
        if self.probe._foreground() in (None, target["pid"]):
            raise ValueError("Foreground tracking is unavailable or the app is now foreground")
        target_session, own_session = wintypes.DWORD(), wintypes.DWORD()
        if (not self.session(target["pid"], ctypes.byref(target_session)) or
                not self.session(os.getpid(), ctypes.byref(own_session)) or
                target_session.value != own_session.value):
            raise ValueError("Only your current interactive session is eligible")
        handle = self.probe.open_process(0x1000 | (0x100001 if force else 0), False, target["pid"])
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            critical = wintypes.BOOL()
            if not self.critical(handle, ctypes.byref(critical)) or critical.value:
                raise ValueError("Critical-process status prevents this action")
            target_owner = self._sid_buffer(handle)
            own_owner = self._sid_buffer(self.current_process())
            if not self.equal_sid(ctypes.c_void_p.from_buffer(target_owner).value,
                                  ctypes.c_void_p.from_buffer(own_owner).value):
                raise ValueError("The process belongs to a different Windows user")
            path, size = ctypes.create_unicode_buffer(32768), wintypes.DWORD(32768)
            created, exited, kernel, user = FILETIME(), FILETIME(), FILETIME(), FILETIME()
            if (not self.probe.process_name(handle, 0, path, ctypes.byref(size)) or
                    not self.probe.process_times(handle, ctypes.byref(created), ctypes.byref(exited),
                                                 ctypes.byref(kernel), ctypes.byref(user))):
                raise ctypes.WinError(ctypes.get_last_error())
            if ntpath.basename(path.value).casefold() != target["name"].casefold() or created.ticks != target["created_ticks"]:
                raise ValueError("Process identity changed")
            system = ntpath.normcase(os.environ.get("SystemRoot", r"C:\Windows"))
            if ntpath.normcase(path.value).startswith(system.rstrip("\\") + "\\"):
                raise ValueError("Windows system applications cannot be controlled")
            return handle
        except BaseException:
            self.probe.close_handle(handle)
            raise

    def execute(self, plan):
        target, force = plan["target"], plan["action"] == "terminate"
        handle = self._verified(target, force=force)
        try:
            if self.probe._foreground() in (None, target["pid"]):
                raise ValueError("Target became foreground")
            if force:
                terminate = _bind(self.kernel, "TerminateProcess", wintypes.BOOL, [wintypes.HANDLE, wintypes.UINT])
                if not terminate(handle, 1):
                    raise ctypes.WinError(ctypes.get_last_error())
                wait = _bind(self.kernel, "WaitForSingleObject", wintypes.DWORD, [wintypes.HANDLE, wintypes.DWORD])
                finished = wait(handle, 2000) == 0
                return {"status": "terminated" if finished else "termination_requested",
                        "message": "Selected PID exited." if finished else "Termination requested; exit is not yet confirmed."}
            callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            enum = _bind(self.user, "EnumWindows", wintypes.BOOL, [callback_type, wintypes.LPARAM])
            visible = _bind(self.user, "IsWindowVisible", wintypes.BOOL, [wintypes.HWND])
            exit_code = _bind(self.kernel, "GetExitCodeProcess", wintypes.BOOL,
                              [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)])
            post = _bind(self.user, "PostMessageW", wintypes.BOOL,
                         [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM])
            count = 0
            @callback_type
            def callback(hwnd, _):
                nonlocal count
                pid = wintypes.DWORD()
                self.probe.window_process(hwnd, ctypes.byref(pid))
                code = wintypes.DWORD()
                still_alive = exit_code(handle, ctypes.byref(code)) and code.value == 259
                if still_alive and pid.value == target["pid"] and visible(hwnd) and post(hwnd, 0x0010, 0, 0):
                    count += 1
                return True
            if not enum(callback, 0):
                raise ctypes.WinError(ctypes.get_last_error())
            if not count:
                raise ValueError("No visible window accepted a close request. No process was terminated.")
            return {"status": "close_requested", "message": f"Asked {count} window(s) to close. Save prompts may still be open; exit is not confirmed."}
        finally:
            self.probe.close_handle(handle)


class ProcessControlService:
    def __init__(self, collector, config, store, *, demo=False, driver=None):
        self.collector, self.config, self.store = collector, config, store
        self.demo, self.driver = demo, driver
        self.plans = {}
        self.clock = time.time

    def candidates(self):
        settings = self.config.load()
        if (not settings.enabled or (not self.demo and settings.device_consent is not True) or
                not settings.track_processes or not settings.track_foreground):
            return []
        latest = self.store.latest()
        if (not latest or latest.get("foreground_pid") is None or
                not 0 <= self.clock() - datetime.fromisoformat(latest["observed_at"]).timestamp() <= 30):
            return []
        history = self.store.process_window(now=datetime.fromtimestamp(self.clock(), timezone.utc))
        result = []
        for target in latest["processes"]:
            if (target["name"].casefold() not in APP_ALLOWLIST or not target.get("created_ticks") or
                    target["pid"] in (0, 4, os.getpid(), latest["foreground_pid"])):
                continue
            rows = [r for r in history if r["pid"] == target["pid"] and r["created_ticks"] == target["created_ticks"]]
            times = sorted({datetime.fromisoformat(r["observed_at"]).timestamp() for r in rows})
            gap_limit = max(15, settings.sample_interval_seconds * 2.5)
            low_cpu = (len(times) >= 3 and times[-1] - times[0] >= 30 and
                       all(b - a <= gap_limit for a, b in zip(times, times[1:])) and
                       all(r["cpu_percent"] <= 0.5 and r["foreground_pid"] is not None and
                           r["foreground_pid"] != target["pid"] for r in rows))
            result.append({**target, "low_cpu_background": low_cpu,
                           "reason": "Low CPU and no foreground use in the observed window; may still be doing useful work." if low_cpu else "Background app. There is not enough evidence to classify it as inactive.",
                           "observed_seconds": round(times[-1] - times[0]) if times else 0})
        return sorted(result, key=lambda p: p["working_set_bytes"], reverse=True)[:12]

    def _gate(self):
        if not self.demo and not self.config.load().allow_process_control:
            raise ValueError("Enable reviewed app controls in Privacy before closing an app")

    def preview(self, pid, action):
        if type(pid) is not int or action not in ("close", "terminate"):
            raise ValueError("Choose one PID and a supported action")
        with self.collector._gate, self.config._lock:
            self._gate()
            consent_versions = self.config.consent_versions("allow_process_control", *TELEMETRY_PERMISSIONS)
            target = next((p for p in self.candidates() if p["pid"] == pid), None)
            if not target:
                raise ValueError("Target is unavailable, foreground, protected or outside the application list")
            now = self.clock()
            self.plans = {k: p for k, p in self.plans.items() if p["expires_at"] > now}
            if len(self.plans) >= 20:
                raise ValueError("Too many pending previews")
            plan = {"id": secrets.token_urlsafe(24), "target": target, "action": action,
                    "expires_at": now + 60, "undo": UNDO,
                    "title": f"{'Force terminate' if action == 'terminate' else 'Request close'}: {target['name']} (PID {pid})",
                    "effect": "Force termination can lose unsaved work and interrupt useful background tasks." if action == "terminate" else "Ask this app's windows to close normally. Save prompts may appear.",
                    "mode": "demo" if self.demo else "live", "consent_versions": consent_versions}
            self.plans[plan["id"]] = plan
            return copy.deepcopy(plan)

    def execute(self, plan_id, *, confirm=False, typed_name=""):
        if confirm is not True:
            raise ValueError("Explicit confirmation is required")
        opaque_id(plan_id, "plan_id")
        with self.collector._gate, self.config._lock:
            self._gate()
            plan = self.plans.get(plan_id)
            if not plan or plan["expires_at"] <= self.clock():
                raise ValueError("Preview expired or already used")
            if plan["consent_versions"] != self.config.consent_versions("allow_process_control", *TELEMETRY_PERMISSIONS):
                self.plans.pop(plan_id)
                raise ValueError("Process-control consent changed; request a fresh preview")
            if plan["action"] == "terminate" and typed_name != plan["target"]["name"]:
                raise ValueError("Type the exact app name to confirm force termination")
            current = next((p for p in self.candidates() if p["pid"] == plan["target"]["pid"]), None)
            if not current or current["created_ticks"] != plan["target"]["created_ticks"]:
                raise ValueError("Process changed; request a fresh preview")
            if self.store.pending_decisions():
                raise ValueError("Finish the active resource trial first")
            self.plans.pop(plan_id)
            entry = {"id": plan_id, "kind": "process_control", "title": plan["title"],
                     "action": plan["action"], "target": plan["target"], "undo": UNDO,
                     "mode": plan["mode"], "created_at": datetime.now(timezone.utc).isoformat(),
                     "status": "process_requested", "result": {"message": "Outcome unknown if interrupted. Never replay automatically."}}
            self.store.save_decision(entry)
            try:
                if self.demo:
                    result = {"status": "simulated", "message": "Simulated app control. No real process was closed or terminated."}
                else:
                    if self.driver is None:
                        self.driver = WindowsProcessDriver()
                    result = self.driver.execute(plan)
                entry.update(status=result["status"], result=result)
            except (OSError, ValueError):
                entry.update(status="process_failed", result={"message": "Target changed, refused closure or denied access. No automatic retry."})
            self.store.save_decision(entry)
            return entry
