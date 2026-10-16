"""Small, explicit records shared by collection and persistence."""

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class ProcessSample:
    pid: int
    name: str
    cpu_percent: float
    working_set_bytes: int
    created_ticks: int = 0


@dataclass(frozen=True)
class Snapshot:
    observed_at: str
    cpu_percent: float
    memory_total_bytes: int
    memory_available_bytes: int
    foreground_pid: int | None
    foreground_app: str | None
    idle_seconds: float | None
    processes: list[ProcessSample] = field(default_factory=list)
    memory_commit_total_bytes: int | None = None
    memory_commit_limit_bytes: int | None = None
    memory_system_cache_bytes: int | None = None
    memory_low_signal: bool | None = None
    disk_total_bytes: int | None = None
    disk_free_bytes: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)
