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

    def to_dict(self) -> dict:
        return asdict(self)
