"""Fixed Windows maintenance actions, reviewed individually; no shell executor."""

import ctypes
import os
import subprocess
import tempfile
import time
import uuid
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path

from .actions import WindowsPriorityDriver
from .intelligence import PRIORITY_ALLOWLIST
from .telemetry import PROCESS_MEMORY_COUNTERS, _bind


TOOLS = {
    "disk_cleanup": {"title": "Run Windows Disk Cleanup", "effect": "Opens cleanmgr. Choose categories and confirm deletion in Windows.",
                     "undo": "Files deleted by Disk Cleanup may be permanently removed. Kshetrajna cannot undo that deletion."},
    "storage_settings": {"title": "Open Windows storage cleanup", "effect": "Opens Storage settings for temporary files and Storage Sense. Windows owns the final cleanup controls.",
                         "undo": "Opening settings changes nothing. Deletions you approve there may be permanent."},
    "open_temp": {"title": "Review temporary files", "effect": "Opens your current temporary folder in Explorer, equivalent to opening %TEMP%. No files are selected or deleted automatically.",
                  "undo": "Opening a folder changes nothing. Review files and skip anything in use before deleting them yourself."},
    "store_cache": {"title": "Reset Microsoft Store cache", "effect": "Starts the Windows wsreset utility. The Store may open when it finishes. This resets Store cache, not RAM.",
                    "undo": "Cache reset has no automatic undo. Cache data is rebuilt as the Store is used."},
}


class WindowsMaintenanceDriver:
    def __init__(self):
        self.children = []

    def execute(self, plan):
        if plan["action"] == "trim_memory":
            return self.trim(plan["target"])
        action = plan["action"]
        if action not in TOOLS:
            raise ValueError("Unknown maintenance action")
        if action == "storage_settings":
            os.startfile("ms-settings:storagesense")
        else:
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            get_system = _bind(kernel, "GetSystemDirectoryW", wintypes.UINT, [wintypes.LPWSTR, wintypes.UINT])
            directory = ctypes.create_unicode_buffer(32768)
            length = get_system(directory, len(directory))
            if not length or length >= len(directory):
                raise OSError("Windows system directory unavailable")
            system = Path(directory.value)
            if action == "open_temp":
                command = [str(system.parent / "explorer.exe"), str(Path(tempfile.gettempdir()).resolve())]
            else:
                command = [str(system / ("cleanmgr.exe" if action == "disk_cleanup" else "wsreset.exe"))]
            self.children = [child for child in self.children if child.poll() is None]
            self.children.append(subprocess.Popen(command, cwd=str(system), shell=False,
                                                  creationflags=subprocess.CREATE_NO_WINDOW))
        return {"status": "launched", "message": "Windows tool launched. Complete its interface; cleanup completion and reclaimed space are not verified by this app."}

    def trim(self, target):
        driver = WindowsPriorityDriver()
        driver._background_only(target)
        # QUERY_LIMITED_INFORMATION | SET_QUOTA, identity and session checked.
        handle = driver._open_verified(target, access=0x1100)
        try:
            def resident():
                memory = PROCESS_MEMORY_COUNTERS()
                memory.cb = ctypes.sizeof(memory)
                if driver.probe.process_memory(handle, ctypes.byref(memory), memory.cb):
                    return int(memory.working_set_size)
                return None
            before = resident()
            empty = _bind(ctypes.WinDLL("psapi", use_last_error=True), "EmptyWorkingSet",
                          wintypes.BOOL, [wintypes.HANDLE])
            driver._background_only(target)
            if not empty(handle):
                raise ctypes.WinError(ctypes.get_last_error())
            after = resident()
            return {"status": "completed", "before_bytes": before, "after_bytes": after,
                    "resident_reduction_mb": round((before - after) / 1024**2, 2) if before is not None and after is not None else None,
                    "message": "Working-set trim completed for this PID. Pages reload on demand; this may slow the app. Resident reduction is not a measured speedup or permanent RAM saving. No automatic undo."}
        finally:
            driver.probe.close_handle(handle)


class MaintenanceService:
    def __init__(self, collector, config, store, *, demo=False, driver=None):
        self.collector, self.config, self.store = collector, config, store
        self.demo, self.driver = demo, driver
        self.plans = {}
        self.clock = time.time

    def offers(self):
        offers = [{"id": key, "action": key, **value} for key, value in TOOLS.items()]
        latest = self.store.latest()
        settings = self.config.load()
        if (latest and settings.enabled and settings.track_processes and settings.track_foreground and
                0 <= self.clock() - datetime.fromisoformat(latest["observed_at"]).timestamp() <= 30):
            for target in latest["processes"]:
                if (target["name"].casefold() in PRIORITY_ALLOWLIST and target.get("created_ticks") and
                        target["pid"] != latest.get("foreground_pid") and target["working_set_bytes"] >= 100 * 1024**2):
                    offers.append({"id": f'memory:{target["pid"]}:{target["created_ticks"]}',
                                   "action": "trim_memory", "target": target,
                                   "title": f'Trim resident memory: {target["name"]} (PID {target["pid"]})',
                                   "effect": "One-time EmptyWorkingSet for this background process only. Its pages may be reloaded immediately, causing page faults or slower responses. This does not clear all system RAM.",
                                   "undo": "No automatic undo. Windows reloads pages when needed; the original resident set cannot be restored exactly."})
        return offers[:8]

    def _gate(self):
        if not self.demo and not self.config.load().allow_maintenance:
            raise ValueError("Enable Windows maintenance in Privacy controls before reviewing an action")

    def preview(self, action_id):
        with self.collector._gate:
            self._gate()
            offer = next((o for o in self.offers() if o["id"] == action_id), None)
            if not offer:
                raise ValueError("This maintenance action is unavailable; refresh the dashboard")
            now = self.clock()
            self.plans = {key: value for key, value in self.plans.items() if value["expires_at"] > now}
            if len(self.plans) >= 20:
                raise ValueError("Too many open previews; wait one minute")
            plan = {**offer, "id": uuid.uuid4().hex, "offer_id": action_id, "expires_at": now + 60}
            self.plans[plan["id"]] = plan
            return dict(plan)

    def execute(self, plan_id):
        if not isinstance(plan_id, str):
            raise ValueError("Invalid approval")
        with self.collector._gate:
            self._gate()
            plan = self.plans.pop(plan_id, None)
            if not plan or plan["expires_at"] <= self.clock():
                raise ValueError("Preview expired or already used; review again")
            if not any(o["id"] == plan["offer_id"] for o in self.offers()):
                raise ValueError("The target changed or became foreground; review again")
            if self.store.pending_decisions():
                raise ValueError("Finish or undo the active priority trial before maintenance")
            for entry in self.store.decisions():
                if (entry.get("kind") == "maintenance" and entry.get("offer_id") == plan["offer_id"] and
                        self.clock() - datetime.fromisoformat(entry["created_at"]).timestamp() < 60):
                    raise ValueError("Wait 60 seconds before repeating this action")
            entry = {"id": plan_id, "kind": "maintenance", "offer_id": plan["offer_id"],
                     "title": plan["title"], "action": plan["action"], "undo": plan["undo"],
                     "mode": "demo" if self.demo else "live", "created_at": datetime.now(timezone.utc).isoformat(),
                     "status": "maintenance_requested",
                     "result": {"message": "Execution was requested. If interrupted, outcome is unknown; it will not be replayed automatically."}}
            self.store.save_decision(entry)
            try:
                if self.demo:
                    result = {"status": "simulated", "message": "Simulation recorded. No Windows tools launched, files deleted, caches reset or memory trimmed."}
                else:
                    if self.driver is None:
                        self.driver = WindowsMaintenanceDriver()
                    result = self.driver.execute(plan)
                entry.update(status=result["status"], result=result)
            except (OSError, ValueError, AttributeError):
                entry.update(status="maintenance_failed", result={"message": "Windows could not complete this action. It may be unavailable, the target may have changed, or access was denied. No automatic retry."})
            self.store.save_decision(entry)
            return entry
