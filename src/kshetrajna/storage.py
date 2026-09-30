"""Local SQLite event store with bounded history and secure row deletion."""

import sqlite3
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import Snapshot


class EventStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS snapshots (
                    id INTEGER PRIMARY KEY,
                    observed_at TEXT NOT NULL,
                    cpu_percent REAL NOT NULL,
                    memory_total_bytes INTEGER NOT NULL,
                    memory_available_bytes INTEGER NOT NULL,
                    foreground_pid INTEGER,
                    foreground_app TEXT,
                    idle_seconds REAL
                );
                CREATE INDEX IF NOT EXISTS snapshots_time ON snapshots(observed_at);
                CREATE TABLE IF NOT EXISTS process_samples (
                    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
                    pid INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    cpu_percent REAL NOT NULL,
                    working_set_bytes INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS activity_events (
                    id INTEGER PRIMARY KEY,
                    observed_at TEXT NOT NULL,
                    kind TEXT NOT NULL CHECK(kind IN ('foreground_changed','became_idle','became_active')),
                    pid INTEGER,
                    app_name TEXT
                );
                CREATE INDEX IF NOT EXISTS activity_time ON activity_events(observed_at);
                CREATE INDEX IF NOT EXISTS process_snapshot ON process_samples(snapshot_id);
                CREATE TABLE IF NOT EXISTS decisions (
                    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, status TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS workspaces (
                    name TEXT PRIMARY KEY, apps TEXT NOT NULL
                );
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(process_samples)")}
            if "created_ticks" not in columns:
                db.execute("ALTER TABLE process_samples ADD COLUMN created_ticks INTEGER NOT NULL DEFAULT 0")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA secure_delete=ON")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def record(self, snapshot: Snapshot, events: list[tuple[str, int | None, str | None]]) -> None:
        with self._connect() as db:
            cursor = db.execute(
                "INSERT INTO snapshots (observed_at,cpu_percent,memory_total_bytes,memory_available_bytes,foreground_pid,foreground_app,idle_seconds) VALUES (?,?,?,?,?,?,?)",
                (snapshot.observed_at, snapshot.cpu_percent, snapshot.memory_total_bytes,
                 snapshot.memory_available_bytes, snapshot.foreground_pid, snapshot.foreground_app,
                 snapshot.idle_seconds),
            )
            db.executemany(
                "INSERT INTO process_samples (snapshot_id,pid,name,cpu_percent,working_set_bytes,created_ticks) VALUES (?,?,?,?,?,?)",
                [(cursor.lastrowid, p.pid, p.name, p.cpu_percent, p.working_set_bytes, p.created_ticks)
                 for p in snapshot.processes],
            )
            db.executemany(
                "INSERT INTO activity_events (observed_at,kind,pid,app_name) VALUES (?,?,?,?)",
                [(snapshot.observed_at, kind, pid, name) for kind, pid, name in events],
            )

    def latest(self) -> dict | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()
            if row is None:
                return None
            processes = db.execute(
                "SELECT pid,name,cpu_percent,working_set_bytes,created_ticks FROM process_samples WHERE snapshot_id=? ORDER BY cpu_percent DESC, working_set_bytes DESC",
                (row["id"],),
            ).fetchall()
            result = dict(row)
            result.pop("id")
            result["processes"] = [dict(p) for p in processes]
            return result

    def history(self, limit: int = 90) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT observed_at,cpu_percent,memory_total_bytes,memory_available_bytes FROM snapshots ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(row) for row in reversed(rows)]

    def activity(self, limit: int = 30) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT observed_at,kind,pid,app_name FROM activity_events ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    def prune(self, retention_days: int, *, now: datetime | None = None) -> None:
        cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=retention_days)).isoformat()
        with self._connect() as db:
            db.execute("DELETE FROM snapshots WHERE observed_at < ?", (cutoff,))
            db.execute("DELETE FROM activity_events WHERE observed_at < ?", (cutoff,))
            db.execute("DELETE FROM decisions WHERE created_at < ? AND status NOT IN ('prepared','active','restore_failed')", (cutoff,))

    def clear(self) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM process_samples")
            db.execute("DELETE FROM snapshots")
            db.execute("DELETE FROM activity_events")
            db.execute("DELETE FROM decisions WHERE status NOT IN ('prepared','active','restore_failed')")
            db.execute("DELETE FROM workspaces")
        with self._connect() as db:
            db.execute("VACUUM")

    def observations(self, limit: int = 720) -> list[dict]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM snapshots ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            return [dict(row) for row in reversed(rows)]

    def decisions(self, limit: int = 100) -> list[dict]:
        with self._connect() as db:
            rows = db.execute("SELECT payload FROM decisions ORDER BY created_at DESC LIMIT ?", (limit,))
            return [json.loads(row[0]) for row in rows]

    def pending_decisions(self) -> list[dict]:
        with self._connect() as db:
            return [json.loads(row[0]) for row in db.execute(
                "SELECT payload FROM decisions WHERE status IN ('prepared','active','restore_failed')")]

    def save_decision(self, decision: dict) -> None:
        with self._connect() as db:
            db.execute("INSERT INTO decisions VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,payload=excluded.payload",
                       (decision["id"], decision["created_at"], decision["status"], json.dumps(decision)))

    def workspaces(self) -> list[dict]:
        with self._connect() as db:
            return [{"name": row[0], "apps": json.loads(row[1])}
                    for row in db.execute("SELECT name,apps FROM workspaces ORDER BY name")]

    def save_workspace(self, name: str, apps: list[str]) -> None:
        with self._connect() as db:
            db.execute("INSERT INTO workspaces VALUES (?,?) ON CONFLICT(name) DO UPDATE SET apps=excluded.apps",
                       (name, json.dumps(apps)))
