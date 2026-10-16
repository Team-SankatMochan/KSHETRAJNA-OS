"""Serial collection loop; settings changes and deletion cannot race a sample."""

import logging
import threading
import time

from .config import ConfigStore, Settings
from .models import Snapshot
from .storage import EventStore


class Collector:
    def __init__(self, probe, config: ConfigStore, store: EventStore, *, demo=False):
        self.probe = probe
        self.config = config
        self.store = store
        self.demo = demo
        self._gate = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._sampling = 0
        self._probe_closed = False
        self._last_foreground: tuple[int | None, str | None] | None = None
        self._was_idle: bool | None = None
        self._active = False
        self.last_error = None
        self.last_sample_at = None
        self.service = None

    def start(self) -> None:
        with self._gate:
            if self._thread is None:
                thread = threading.Thread(target=self._run, name="kshetrajna-collector", daemon=True)
                thread.start()
                self._thread = thread

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None:
            if thread is threading.current_thread():
                return
            thread.join(timeout=10)
            if thread.is_alive():
                return
        # An external sample may still hold the gate; a later stop can retry cleanup.
        if not self._gate.acquire(blocking=False):
            return
        try:
            if self._sampling or self._probe_closed:
                return
            if self._thread is not None and self._thread.is_alive():
                return
            close = getattr(self.probe, "close", None)
            if close is not None:
                close()
            self._probe_closed = True
        finally:
            self._gate.release()

    def update_settings(self, patch: dict) -> Settings:
        if not isinstance(patch, dict):
            raise ValueError("Settings update must be a JSON object")
        with self._gate:
            previous = self.config.load()
            if (patch.get("enabled") is True and not self.demo and
                    patch.get("device_consent", previous.device_consent) is not True):
                raise ValueError("Device collection requires explicit device consent")
            if not self.demo and patch.get("device_consent") is False:
                patch = {**patch, "enabled": False}
            settings = self.config.update(patch)
            if (not settings.enabled or (not self.demo and settings.device_consent is not True) or
                    settings.track_processes != previous.track_processes or settings.track_foreground != previous.track_foreground):
                self.probe.reset()
                self._last_foreground = None
                self._was_idle = None
                self._active = False
            logging.getLogger(__name__).info(
                "Settings changed: enabled=%s, process_tracking=%s, foreground_tracking=%s",
                settings.enabled, settings.track_processes, settings.track_foreground,
            )
            self._wake.set()
            return settings

    def delete_history(self, *, confirm=False) -> None:
        if confirm is not True:
            raise ValueError("Deleting recorded history requires confirm=true")
        with self._gate:
            self.store.clear()
            self.probe.reset()
            self._last_foreground = None
            self._was_idle = None
            logging.getLogger(__name__).info("Recorded history deleted")

    def collect_once(self) -> Snapshot | None:
        with self._gate:
            settings = self.config.load()
            if not settings.enabled or (not self.demo and settings.device_consent is not True):
                if self._active:
                    self.probe.reset()
                    self._last_foreground = None
                    self._was_idle = None
                    self._active = False
                self.store.prune(settings.retention_days)
                return None
            self._active = True
            self._probe_closed = False
            self._sampling += 1
            try:
                snapshot = self.probe.sample(
                    track_processes=settings.track_processes,
                    track_foreground=settings.track_foreground,
                    max_processes=settings.max_processes,
                )
            finally:
                self._sampling -= 1
            events = self._events(snapshot, settings)
            self.store.record(snapshot, events)
            self.store.prune(settings.retention_days)
            self.last_sample_at = snapshot.observed_at
            self.last_error = None
            return snapshot

    def _events(self, snapshot: Snapshot, settings: Settings) -> list[tuple[str, int | None, str | None]]:
        if not settings.track_foreground:
            return []
        events: list[tuple[str, int | None, str | None]] = []
        current = (snapshot.foreground_pid, snapshot.foreground_app)
        if current != self._last_foreground and snapshot.foreground_pid is not None:
            events.append(("foreground_changed", *current))
        self._last_foreground = current
        if snapshot.idle_seconds is not None:
            idle = snapshot.idle_seconds >= settings.idle_threshold_seconds
            if self._was_idle is not None and idle != self._was_idle:
                events.append(("became_idle" if idle else "became_active", snapshot.foreground_pid, snapshot.foreground_app))
            self._was_idle = idle
        return events

    def _run(self) -> None:
        next_sample = 0.0
        while not self._stop.is_set():
            try:
                if time.monotonic() >= next_sample:
                    self.collect_once()
                    next_sample = time.monotonic() + self.config.load().sample_interval_seconds
                if self.service:
                    self.service.tick()
            except Exception:
                self.last_error = "Collection failed. Check the local diagnostic log."
                logging.getLogger(__name__).exception("Telemetry cycle failed")
                if self.service:
                    try:
                        self.service.tick()
                    except Exception:
                        logging.getLogger(__name__).exception("Recovery cycle failed")
                next_sample = time.monotonic() + 5
            changed = self._wake.wait(1)
            self._wake.clear()
            if changed:
                next_sample = 0.0
