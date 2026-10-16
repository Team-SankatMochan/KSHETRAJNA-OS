"""Pure, read-only diagnoses of individual counters; never an optimization claim."""

import math
from datetime import datetime, timezone

from .models import Snapshot


def _bytes(value) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _status(percent: float | None, fresh: bool, elevated: float, high: float) -> str:
    if not fresh or percent is None:
        return "unknown"
    return "high" if percent >= high else "elevated" if percent >= elevated else "normal"


def summarize_health(latest: Snapshot | dict | None, *, now: datetime | None = None,
                     stale_after_seconds: float = 30.0) -> dict:
    """Separate CPU, physical, commit, and OS evidence, with explicit freshness.

    Rule thresholds (elevated/high): CPU 50/75%, physical use 70/85%, commit
    80/90%, disk use 90/95%. These are review cues, not measured performance.
    Stale/undated readings retain valid values but cannot give a current diagnosis.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Health evaluation requires a timezone-aware clock")
    if not math.isfinite(stale_after_seconds) or stale_after_seconds <= 0:
        raise ValueError("Health freshness interval must be positive and finite")
    row = latest.to_dict() if isinstance(latest, Snapshot) else latest or {}
    observed_at, age, freshness = None, None, "unknown"
    try:
        observed = datetime.fromisoformat(row["observed_at"])
        if observed.tzinfo is not None and observed.utcoffset() is not None:
            observed_at = observed.isoformat()
            elapsed = (now - observed).total_seconds()
            if elapsed >= -5:  # Allow a small clock skew, never a future-dated history.
                age = max(0.0, elapsed)
                freshness = "fresh" if age <= stale_after_seconds else "stale"
    except (KeyError, TypeError, ValueError, OverflowError):
        pass
    fresh = freshness == "fresh"
    cpu = row.get("cpu_percent")
    cpu = float(cpu) if type(cpu) in (int, float) and math.isfinite(cpu) and 0 <= cpu <= 100 else None
    physical_total = _bytes(row.get("memory_total_bytes"))
    physical_available = _bytes(row.get("memory_available_bytes"))
    physical_percent = None
    if physical_total and physical_available is not None and physical_available <= physical_total:
        physical_percent = 100 * (1 - physical_available / physical_total)
    commit_total = _bytes(row.get("memory_commit_total_bytes"))
    commit_limit = _bytes(row.get("memory_commit_limit_bytes"))
    commit_percent = (100 * commit_total / commit_limit
                      if commit_limit and commit_total is not None else None)
    signal = row.get("memory_low_signal")
    signal = signal if type(signal) is bool else None
    physical_status = _status(physical_percent, fresh, 70, 85)
    commit_status = _status(commit_percent, fresh, 80, 90)
    signal_status = "unknown" if not fresh or signal is None else "high" if signal else "normal"
    memory_states = (physical_status, commit_status, signal_status)
    memory_pressure = ("high" if "high" in memory_states else
                       "elevated" if "elevated" in memory_states else
                       "normal" if all(state == "normal" for state in memory_states) else "unknown")
    disk_total, disk_free = _bytes(row.get("disk_total_bytes")), _bytes(row.get("disk_free_bytes"))
    disk_free_percent = 100 * disk_free / disk_total if disk_total and disk_free is not None and disk_free <= disk_total else None
    return {
        "observed_at": observed_at, "evaluated_at": now.isoformat(),
        "age_seconds": round(age, 3) if age is not None else None,
        "stale_after_seconds": stale_after_seconds, "freshness": freshness,
        "cpu": {"percent": cpu, "status": _status(cpu, fresh, 50, 75)},
        "physical": {"total_bytes": physical_total, "available_bytes": physical_available,
                     "used_percent": round(physical_percent, 2) if physical_percent is not None else None,
                     "status": physical_status,
                     "note": "Available physical RAM includes reusable standby pages; it is not wasted memory."},
        "commit": {"total_bytes": commit_total, "limit_bytes": commit_limit,
                   "used_percent": round(commit_percent, 2) if commit_percent is not None else None,
                   "status": commit_status,
                   "note": "Committed allocations are not resident RAM; the current commit limit can change."},
        "system_cache": {"bytes": _bytes(row.get("memory_system_cache_bytes")),
                         "overlaps_available": True,
                         "note": "System cache includes standby pages and the system working set. It overlaps available RAM; never add the counters or purge standby pages."},
        "os_low_memory": {"signal": signal, "status": signal_status,
                          "note": "A false low-memory signal does not imply the high-memory signal is set."},
        "memory_pressure": memory_pressure,
        "disk": {"total_bytes": disk_total, "free_bytes": disk_free,
                 "free_percent": round(disk_free_percent, 2) if disk_free_percent is not None else None,
                 "status": _status(100 - disk_free_percent if disk_free_percent is not None else None,
                                   fresh, 90, 95),
                 "scope": "windows_system_volume",
                 "note": "Caller-visible capacity on one fixed Windows volume, subject to quotas; not all disks or potential cleanup savings."},
        "note": "Single sampled observations are review cues, not proof of a cause or a performance improvement.",
    }
