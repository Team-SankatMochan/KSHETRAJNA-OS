"""Validated local settings. Capture is off on first launch."""

import json
import os
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path


TELEMETRY_PERMISSIONS = ("enabled", "device_consent", "track_processes", "track_foreground")
CONSENT_PERMISSIONS = (*TELEMETRY_PERMISSIONS, "allow_local_ai", "allow_maintenance",
                       "allow_process_control", "allow_permanent_delete", "allow_priority_changes")


def default_data_dir() -> Path:
    root = os.environ.get("LOCALAPPDATA")
    return (Path(root) if root else Path.home() / "AppData" / "Local") / "Kshetrajna"


def opaque_id(value, name="action_id") -> str:
    if (not isinstance(value, str) or not 1 <= len(value) <= 200 or not value.isascii()
            or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in value)):
        raise ValueError(f"Invalid {name}")
    return value


def public_error(error) -> str:
    message = str(error)
    cause = error
    seen = set()
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        if isinstance(cause, OSError) and (cause is error or str(cause) in message):
            return "Operation unavailable; the target may have changed or access was denied"
        cause = cause.__cause__ or (None if cause.__suppress_context__ else cause.__context__)
    return message


@dataclass(frozen=True)
class Settings:
    enabled: bool = False
    device_consent: bool = False
    sample_interval_seconds: int = 5
    retention_days: int = 7
    max_processes: int = 12
    track_processes: bool = True
    track_foreground: bool = True
    idle_threshold_seconds: int = 60
    allow_priority_changes: bool = False
    allow_maintenance: bool = False
    allow_process_control: bool = False
    allow_local_ai: bool = False
    local_ai_model: str = ""
    allow_permanent_delete: bool = False

    @classmethod
    def from_dict(cls, value: dict) -> "Settings":
        if not isinstance(value, dict):
            raise ValueError("Settings must be a JSON object")
        allowed = set(cls.__dataclass_fields__)
        if set(value) - allowed:
            raise ValueError("Unknown setting")
        merged = {**asdict(cls()), **value}
        for key in ("enabled", "device_consent", "track_processes", "track_foreground", "allow_priority_changes", "allow_maintenance", "allow_process_control", "allow_local_ai", "allow_permanent_delete"):
            if type(merged[key]) is not bool:
                raise ValueError(f"{key} must be a boolean")
        model = merged["local_ai_model"]
        if not isinstance(model, str) or len(model) > 150 or any(ord(c) < 32 for c in model):
            raise ValueError("Invalid local model name")
        bounds = {
            "sample_interval_seconds": (2, 60),
            "retention_days": (1, 30),
            "max_processes": (1, 30),
            "idle_threshold_seconds": (15, 900),
        }
        for key, (low, high) in bounds.items():
            if type(merged[key]) is not int or not low <= merged[key] <= high:
                raise ValueError(f"{key} must be between {low} and {high}")
        return cls(**merged)


class ConfigStore:
    _locks = {}
    _locks_gate = threading.Lock()

    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.path = self.directory / "config.json"
        # Multiple services in this process can open the same settings file.
        key = os.path.normcase(os.path.abspath(self.path))
        with self._locks_gate:
            self._lock = self._locks.setdefault(key, threading.RLock())
        self._consents = {}
        self._consent_versions = {}

    def _observe_consents(self, settings, persisted):
        if (not isinstance(persisted, dict) or set(persisted) - set(CONSENT_PERMISSIONS) or
                any(type(v) is not int or not 0 <= v < 2**63 for v in persisted.values())):
            raise ValueError("Invalid stored consent versions")
        for key in CONSENT_PERMISSIONS:
            value = getattr(settings, key)
            previous_version = self._consent_versions.get(key, 0)
            version = max(previous_version, persisted.get(key, 0))
            if key in self._consents and self._consents[key] != value and version == previous_version:
                version += 1
            self._consent_versions[key] = version
            self._consents[key] = value
        return settings

    def consent_version(self, key):
        return self.consent_versions(key)[key]

    def consent_versions(self, *keys):
        if set(keys) - set(CONSENT_PERMISSIONS):
            raise ValueError("Unknown consent capability")
        with self._lock:
            self.load()
            return {key: self._consent_versions[key] for key in keys}

    def load(self) -> Settings:
        with self._lock:
            if not self.path.exists():
                return self._observe_consents(Settings(), {})
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("Settings must be a JSON object")
            versions = data.pop("_consent_versions", {})
            return self._observe_consents(Settings.from_dict(data), versions)

    def update(self, patch: dict) -> Settings:
        if not isinstance(patch, dict):
            raise ValueError("Settings update must be a JSON object")
        with self._lock:
            previous = self.load()
            settings = Settings.from_dict({**asdict(previous), **patch})
            versions = {key: self._consent_versions[key] + int(getattr(previous, key) != getattr(settings, key))
                        for key in CONSENT_PERMISSIONS}
            if any(version >= 2**63 for version in versions.values()):
                raise ValueError("Stored consent version is outside the supported range")
            self.directory.mkdir(parents=True, exist_ok=True)
            fd, temp_path = tempfile.mkstemp(prefix="config-", suffix=".tmp", dir=self.directory)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as file:
                    json.dump({**asdict(settings), "_consent_versions": versions}, file, indent=2)
                    file.flush()
                    os.fsync(file.fileno())
                # Retry Windows access/sharing conflicts with the same fsynced file.
                for attempt in range(5):
                    try:
                        os.replace(temp_path, self.path)
                        break
                    except OSError as error:
                        if (os.name != "nt" or getattr(error, "winerror", None) not in (5, 32, 33)
                                or attempt == 4):
                            raise
                        time.sleep(0.05)
            finally:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
            self._consents = {key: getattr(settings, key) for key in CONSENT_PERMISSIONS}
            self._consent_versions = versions
            return settings
