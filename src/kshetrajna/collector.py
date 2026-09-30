"""Serial collection loop; settings changes and deletion cannot race a sample."""

import logging
import threading
import time

from .config import ConfigStore, Settings
from .models import Snapshot
from .storage import EventStore


class Collector:
    def __init__(self, probe, config: ConfigStore, store: EventStore):
        self.probe = probe
        self.config = config
        self.store = store
        self._gate = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_foreground: tuple[int | None, str | None] | None = None
        self._was_idle: bool | None = None
        self._active = False
        self.last_error = None
        self.last_sample_at = None
        self.service = None

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="kshetrajna-collector", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=10)

    def update_settings(self, patch: dict) -> Settings:
        with self._gate:
            previous = self.config.load()
            settings = self.config.update(patch)
            if not settings.enabled or settings.track_processes != previous.track_processes or settings.track_foreground != previous.track_foreground:
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

    def delete_history(self) -> None:
        with self._gate:
            self.store.clear()
            self.probe.reset()
            self._last_foreground = None
            self._was_idle = None
            logging.getLogger(__name__).info("Recorded history deleted")

    def collect_once(self) -> Snapshot | None:
        with self._gate:
            settings = self.config.load()
            if not settings.enabled:
                if self._active:
                    self.probe.reset()
                    self._last_foreground = None
                    self._was_idle = None
                    self._active = False
                self.store.prune(settings.retention_days)
                return None
            self._active = True
            snapshot = self.probe.sample(
                track_processes=settings.track_processes,
                track_foreground=settings.track_foreground,
                max_processes=settings.max_processes,
            )
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
