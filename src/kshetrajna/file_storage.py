import sqlite3
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def escape_like(query: str) -> str:
    """Escape LIKE wildcards."""
    return query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


class FileStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS roots (
                    id          INTEGER PRIMARY KEY,
                    path        TEXT    NOT NULL UNIQUE,
                    label       TEXT    NOT NULL DEFAULT '',
                    created_at  TEXT    NOT NULL,
                    status      TEXT    NOT NULL DEFAULT 'pending'
                                CHECK(status IN ('pending','scanning','ready','error','removed','cancelled','truncated')),
                    file_count  INTEGER NOT NULL DEFAULT 0,
                    total_bytes INTEGER NOT NULL DEFAULT 0,
                    last_scan   TEXT,
                    error       TEXT
                );

                CREATE TABLE IF NOT EXISTS files (
                    id           INTEGER PRIMARY KEY,
                    root_id      INTEGER NOT NULL REFERENCES roots(id) ON DELETE CASCADE,
                    relative_path TEXT   NOT NULL,
                    name         TEXT    NOT NULL,
                    extension    TEXT    NOT NULL DEFAULT '',
                    size_bytes   INTEGER NOT NULL DEFAULT 0,
                    modified_at  TEXT,
                    created_at   TEXT,
                    is_directory INTEGER NOT NULL DEFAULT 0,
                    mime_guess   TEXT    NOT NULL DEFAULT '',
                    excerpt      TEXT    NOT NULL DEFAULT '',
                    scan_pass    INTEGER NOT NULL DEFAULT 1,
                    indexed_at   TEXT    NOT NULL,
                    UNIQUE(root_id, relative_path)
                );
                CREATE INDEX IF NOT EXISTS idx_files_root    ON files(root_id);
                CREATE INDEX IF NOT EXISTS idx_files_ext     ON files(extension);
                CREATE INDEX IF NOT EXISTS idx_files_name    ON files(name COLLATE NOCASE);
                CREATE INDEX IF NOT EXISTS idx_files_excerpt ON files(excerpt);

                CREATE TABLE IF NOT EXISTS scan_state (
                    root_id     INTEGER PRIMARY KEY REFERENCES roots(id) ON DELETE CASCADE,
                    last_pass   INTEGER NOT NULL DEFAULT 0,
                    started_at  TEXT,
                    finished_at TEXT,
                    files_seen  INTEGER NOT NULL DEFAULT 0,
                    files_new   INTEGER NOT NULL DEFAULT 0,
                    files_changed INTEGER NOT NULL DEFAULT 0,
                    files_removed INTEGER NOT NULL DEFAULT 0,
                    errors      INTEGER NOT NULL DEFAULT 0
                );
            """)

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA secure_delete=ON")
            db.execute("PRAGMA journal_mode=WAL")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def get_roots(self) -> list[dict]:
        with self._connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM roots ORDER BY created_at DESC")]

    def get_root(self, root_id: int) -> dict | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM roots WHERE id=?", (root_id,)).fetchone()
            return dict(row) if row else None

    def add_root(self, path: str, label: str) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            cursor = db.execute(
                "INSERT INTO roots (path, label, created_at, status) VALUES (?, ?, ?, 'pending')",
                (path, label, now)
            )
            root_id = cursor.lastrowid
            db.execute(
                "INSERT INTO scan_state (root_id, last_pass) VALUES (?, 0)",
                (root_id,)
            )
            return root_id

    def update_root_status(self, root_id: int, status: str, error: str | None = None) -> None:
        with self._connect() as db:
            db.execute("UPDATE roots SET status=?, error=? WHERE id=?", (status, error, root_id))

    def update_root_stats(self, root_id: int, file_count: int, total_bytes: int) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            db.execute("UPDATE roots SET file_count=?, total_bytes=?, last_scan=? WHERE id=?", 
                       (file_count, total_bytes, now, root_id))

    def remove_root(self, root_id: int) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM roots WHERE id=?", (root_id,))

    def clear_all(self) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM roots")
            db.execute("DELETE FROM files")
            db.execute("DELETE FROM scan_state")
        with self._connect() as db:
            db.execute("VACUUM")

    def search_files(self, query: str, root_id: int | None = None, limit: int = 100) -> list[dict]:
        if not query:
            return []
        safe_q = f"%{escape_like(query[:200])}%"
        with self._connect() as db:
            if root_id is not None:
                rows = db.execute(
                    "SELECT id, root_id, relative_path, name, extension, size_bytes, modified_at, mime_guess, excerpt "
                    "FROM files WHERE root_id=? AND (name LIKE ? ESCAPE '\\' OR relative_path LIKE ? ESCAPE '\\' OR excerpt LIKE ? ESCAPE '\\') LIMIT ?",
                    (root_id, safe_q, safe_q, safe_q, limit)
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT id, root_id, relative_path, name, extension, size_bytes, modified_at, mime_guess, excerpt "
                    "FROM files WHERE name LIKE ? ESCAPE '\\' OR relative_path LIKE ? ESCAPE '\\' OR excerpt LIKE ? ESCAPE '\\' LIMIT ?",
                    (safe_q, safe_q, safe_q, limit)
                ).fetchall()
            return [dict(row) for row in rows]

    def get_file(self, file_id: int) -> dict | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
            return dict(row) if row else None

    def get_recent_files(self, root_id: int, limit: int = 50) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT id, root_id, relative_path, name, extension, size_bytes, modified_at, mime_guess "
                "FROM files WHERE root_id=? ORDER BY modified_at DESC LIMIT ?",
                (root_id, limit)
            ).fetchall()
            return [dict(row) for row in rows]

    def get_stats(self) -> dict:
        with self._connect() as db:
            count = db.execute("SELECT SUM(file_count) FROM roots").fetchone()[0] or 0
            size = db.execute("SELECT SUM(total_bytes) FROM roots").fetchone()[0] or 0
            return {"total_files": count, "total_bytes": size}

    def get_candidate_files(self, limit: int = 250) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT id, root_id, relative_path, name, extension, size_bytes, modified_at, mime_guess, excerpt "
                "FROM files ORDER BY modified_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(row) for row in rows]

    def get_insights_data(self) -> dict:
        with self._connect() as db:
            types = db.execute("SELECT extension, COUNT(*) as count, SUM(size_bytes) as total_size FROM files GROUP BY extension ORDER BY count DESC LIMIT 20").fetchall()
            largest = db.execute("SELECT id, root_id, relative_path, name, extension, size_bytes FROM files ORDER BY size_bytes DESC LIMIT 20").fetchall()
            newest = db.execute("SELECT id, root_id, relative_path, name, extension, size_bytes, modified_at FROM files ORDER BY modified_at DESC LIMIT 20").fetchall()
            totals = self.get_stats()
            return {
                "type_breakdown": [dict(t) for t in types],
                "largest": [dict(l) for l in largest],
                "newest": [dict(n) for n in newest],
                "totals": totals
            }
