"""Validated local settings. Capture is off on first launch."""

import json
import os
import tempfile
import threading
from dataclasses import asdict, dataclass
from pathlib import Path


def default_data_dir() -> Path:
    root = os.environ.get("LOCALAPPDATA")
    return (Path(root) if root else Path.home() / "AppData" / "Local") / "Kshetrajna"


@dataclass(frozen=True)
class Settings:
    enabled: bool = False
    sample_interval_seconds: int = 5
    retention_days: int = 7
    max_processes: int = 12
    track_processes: bool = True
    track_foreground: bool = True
    idle_threshold_seconds: int = 60
    allow_priority_changes: bool = False

    @classmethod
    def from_dict(cls, value: dict) -> "Settings":
        if not isinstance(value, dict):
            raise ValueError("Settings must be a JSON object")
        allowed = set(cls.__dataclass_fields__)
        if set(value) - allowed:
            raise ValueError("Unknown setting")
        merged = {**asdict(cls()), **value}
        for key in ("enabled", "track_processes", "track_foreground", "allow_priority_changes"):
            if type(merged[key]) is not bool:
                raise ValueError(f"{key} must be a boolean")
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
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.path = self.directory / "config.json"
        self._lock = threading.RLock()

    def load(self) -> Settings:
        with self._lock:
            if not self.path.exists():
                return Settings()
            return Settings.from_dict(json.loads(self.path.read_text(encoding="utf-8")))

    def update(self, patch: dict) -> Settings:
        if not isinstance(patch, dict):
            raise ValueError("Settings update must be a JSON object")
        with self._lock:
            settings = Settings.from_dict({**asdict(self.load()), **patch})
            self.directory.mkdir(parents=True, exist_ok=True)
            fd, temp_path = tempfile.mkstemp(prefix="config-", suffix=".tmp", dir=self.directory)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as file:
                    json.dump(asdict(settings), file, indent=2)
                    file.flush()
                    os.fsync(file.fileno())
                os.replace(temp_path, self.path)
            finally:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
            return settings
