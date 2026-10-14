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

                CREATE TABLE IF NOT EXISTS file_actions (
                    id                  TEXT PRIMARY KEY,
                    proposal_id         TEXT UNIQUE NOT NULL,
                    root_id             INTEGER NOT NULL REFERENCES roots(id),
                    file_id             INTEGER,
                    kind                TEXT NOT NULL,
                    source_relative     TEXT NOT NULL,
                    destination_relative TEXT NOT NULL,
                    
                    expected_size        INTEGER,
                    expected_mtime_ns    INTEGER,
                    expected_ctime_ns    INTEGER,
                    expected_dev         INTEGER,
                    expected_ino         INTEGER,
                    
                    post_dev             INTEGER,
                    post_ino             INTEGER,
                    post_size            INTEGER,
                    post_mtime_ns        INTEGER,
                    
                    created_at           TEXT NOT NULL,
                    expires_at           TEXT NOT NULL,
                    prepared_at          TEXT,
                    applied_at           TEXT,
                    undone_at            TEXT,
                    
                    status               TEXT NOT NULL,
                    error                TEXT,
                    mode                 TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sort_plans (
                    id             TEXT PRIMARY KEY,
                    root_id        INTEGER NOT NULL REFERENCES roots(id) ON DELETE CASCADE,
                    created_at     TEXT NOT NULL,
                    expires_at     TEXT NOT NULL,
                    status         TEXT NOT NULL,
                    mode           TEXT NOT NULL,
                    create_folders INTEGER NOT NULL DEFAULT 0,
                    created_dirs   TEXT NOT NULL DEFAULT '[]',
                    error          TEXT
                );

                CREATE TABLE IF NOT EXISTS sort_suggestions (
                    id                   TEXT PRIMARY KEY,
                    plan_id              TEXT NOT NULL REFERENCES sort_plans(id) ON DELETE CASCADE,
                    file_id              INTEGER REFERENCES files(id) ON DELETE SET NULL,
                    source_relative      TEXT NOT NULL,
                    destination_relative TEXT NOT NULL,
                    reason               TEXT NOT NULL,
                    confidence           INTEGER NOT NULL,
                    selected             INTEGER NOT NULL DEFAULT 0,
                     status               TEXT NOT NULL DEFAULT 'suggested',
                     error                TEXT,
                     action_id            TEXT,
                     created_at           TEXT NOT NULL,
                     content_summary     TEXT NOT NULL DEFAULT '',
                     content_inspected   INTEGER NOT NULL DEFAULT 0,
                     review_required     INTEGER NOT NULL DEFAULT 0,
                     review_reason       TEXT NOT NULL DEFAULT ''
                 );
                CREATE INDEX IF NOT EXISTS idx_sort_suggestions_plan ON sort_suggestions(plan_id);
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(sort_suggestions)")}
            migrations = {
                "content_summary": "TEXT NOT NULL DEFAULT ''",
                "content_inspected": "INTEGER NOT NULL DEFAULT 0",
                "review_required": "INTEGER NOT NULL DEFAULT 0",
                "review_reason": "TEXT NOT NULL DEFAULT ''",
            }
            for column, definition in migrations.items():
                if column not in columns:
                    db.execute(f"ALTER TABLE sort_suggestions ADD COLUMN {column} {definition}")
            for table, additions in {
                'file_actions': {'destination_root_id': 'INTEGER REFERENCES roots(id)'},
                'sort_suggestions': {'destination_root_id': 'INTEGER REFERENCES roots(id)', 'source_root_id': 'INTEGER REFERENCES roots(id)', 'project_name': "TEXT NOT NULL DEFAULT ''"},
                'sort_plans': {'report': "TEXT NOT NULL DEFAULT '{}'"},
                'roots': {'scan_limit': 'INTEGER NOT NULL DEFAULT 50000'},
            }.items():
                present = {row[1] for row in db.execute(f'PRAGMA table_info({table})')}
                for column, definition in additions.items():
                    if column not in present:
                        db.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')
            db.execute('''CREATE TABLE IF NOT EXISTS organization_projects (
                id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                aliases TEXT NOT NULL DEFAULT '[]', root_id INTEGER NOT NULL REFERENCES roots(id),
                folder TEXT NOT NULL, created_at TEXT NOT NULL)''')
            file_columns = {row[1] for row in db.execute("PRAGMA table_info(files)")}
            if "inspection_version" not in file_columns:
                db.execute("ALTER TABLE files ADD COLUMN inspection_version INTEGER NOT NULL DEFAULT 0")
            for column, definition in {'sort_keep_size':'INTEGER', 'sort_keep_modified':'TEXT'}.items():
                if column not in file_columns:
                    db.execute(f'ALTER TABLE files ADD COLUMN {column} {definition}')

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
            unresolved = db.execute(
                "SELECT COUNT(*) FROM file_actions WHERE (root_id=? OR destination_root_id=?) AND status IN ('proposed','prepared','active','applied','undo_failed')",
                (root_id, root_id),
            ).fetchone()[0]
            if unresolved:
                raise ValueError("Resolve file action recovery records before removing this root")
            db.execute("DELETE FROM file_actions WHERE root_id=? OR destination_root_id=?", (root_id, root_id))
            db.execute("DELETE FROM organization_projects WHERE root_id=?", (root_id,))
            db.execute("DELETE FROM roots WHERE id=?", (root_id,))

    def has_unresolved_file_actions(self, root_id: int | None = None) -> bool:
        with self._connect() as db:
            if root_id is None:
                row = db.execute(
                    "SELECT 1 FROM file_actions WHERE status IN ('proposed','prepared','active','applied','undo_failed') LIMIT 1"
                ).fetchone()
            else:
                row = db.execute(
                    "SELECT 1 FROM file_actions WHERE (root_id=? OR destination_root_id=?) AND status IN ('proposed','prepared','active','applied','undo_failed') LIMIT 1",
                    (root_id, root_id),
                ).fetchone()
            return row is not None

    def clear_all(self) -> None:
        with self._connect() as db:
            unresolved = db.execute(
                "SELECT COUNT(*) FROM file_actions WHERE status IN ('proposed','prepared','active','applied','undo_failed')"
            ).fetchone()[0]
            if unresolved:
                raise ValueError("Resolve file action recovery records before deleting the file catalog")
            db.execute("DELETE FROM file_actions")
            db.execute("DELETE FROM organization_projects")
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
                    "SELECT f.id, f.root_id, r.path AS root_path, f.relative_path, f.name, f.extension, f.size_bytes, f.modified_at, f.mime_guess, f.excerpt "
                    "FROM files f JOIN roots r ON r.id=f.root_id WHERE f.root_id=? AND (f.name LIKE ? ESCAPE '\\' OR f.relative_path LIKE ? ESCAPE '\\' OR f.excerpt LIKE ? ESCAPE '\\') LIMIT ?",
                    (root_id, safe_q, safe_q, safe_q, limit)
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT f.id, f.root_id, r.path AS root_path, f.relative_path, f.name, f.extension, f.size_bytes, f.modified_at, f.mime_guess, f.excerpt "
                    "FROM files f JOIN roots r ON r.id=f.root_id WHERE f.name LIKE ? ESCAPE '\\' OR f.relative_path LIKE ? ESCAPE '\\' OR f.excerpt LIKE ? ESCAPE '\\' LIMIT ?",
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

    def get_review_candidate_files(self, limit: int = 2000) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT f.id, f.root_id, r.path AS root_path, r.label AS root_label, "
                "f.relative_path, f.name, f.extension, f.size_bytes, f.modified_at, f.excerpt, f.inspection_version "
                "FROM files f JOIN roots r ON r.id=f.root_id "
                "WHERE f.is_directory=0 ORDER BY f.modified_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(row) for row in rows]

    def get_sortable_files(self, root_id: int) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT id, root_id, relative_path, name, extension, size_bytes, modified_at, "
                "created_at, is_directory, mime_guess, excerpt FROM files "
                "WHERE root_id=? AND is_directory=0 AND (sort_keep_size IS NULL OR sort_keep_size!=size_bytes OR sort_keep_modified IS NOT modified_at) "
                "AND ((SELECT status FROM roots WHERE id=files.root_id)!='truncated' OR scan_pass=(SELECT last_pass FROM scan_state WHERE root_id=files.root_id)) ORDER BY relative_path COLLATE NOCASE",
                (root_id,),
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

    @staticmethod
    def _encode_identity(values: dict) -> dict:
        result = dict(values)
        for key in ("expected_dev", "expected_ino", "post_dev", "post_ino"):
            value = result.get(key)
            if type(value) is int and not -(2**63) <= value < 2**63:
                # NTFS identity values can exceed SQLite's signed 64-bit range.
                # A nonnumeric prefix also prevents INTEGER affinity rounding.
                result[key] = "bigint:" + str(value)
        return result

    @staticmethod
    def _decode_identity(values: dict) -> dict:
        result = dict(values)
        for key in ("expected_dev", "expected_ino", "post_dev", "post_ino"):
            value = result.get(key)
            if isinstance(value, str) and value.startswith("bigint:"):
                if len(value) > 256:
                    raise ValueError("Invalid stored file identity")
                result[key] = int(value[7:])
        return result

    def save_file_action(self, action: dict) -> None:
        action = self._encode_identity(action)
        with self._connect() as db:
            cols = list(action.keys())
            places = ", ".join("?" * len(cols))
            query = f"INSERT INTO file_actions ({', '.join(cols)}) VALUES ({places})"
            db.execute(query, tuple(action.values()))

    def update_file_action(self, action_id: str, updates: dict) -> None:
        if not updates:
            return
        updates = self._encode_identity(updates)
        with self._connect() as db:
            cols = list(updates.keys())
            set_clause = ", ".join(f"{c}=?" for c in cols)
            query = f"UPDATE file_actions SET {set_clause} WHERE id=?"
            db.execute(query, tuple(updates.values()) + (action_id,))

    def get_file_action(self, action_id: str = None, proposal_id: str = None) -> dict | None:
        with self._connect() as db:
            if action_id:
                row = db.execute("SELECT * FROM file_actions WHERE id=?", (action_id,)).fetchone()
            elif proposal_id:
                row = db.execute("SELECT * FROM file_actions WHERE proposal_id=?", (proposal_id,)).fetchone()
            else:
                return None
            return self._decode_identity(dict(row)) if row else None
            
    def get_file_actions(self) -> list[dict]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM file_actions ORDER BY created_at DESC LIMIT 100").fetchall()
            return [self._decode_identity(dict(row)) for row in rows]

    def save_sort_plan(self, plan: dict, suggestions: list[dict]) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO sort_plans (id,root_id,created_at,expires_at,status,mode,create_folders,created_dirs,error) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (plan["id"], plan["root_id"], plan["created_at"], plan["expires_at"], plan["status"],
                 plan["mode"], int(plan.get("create_folders", False)), plan.get("created_dirs", "[]"), plan.get("error")),
            )
            db.execute('UPDATE sort_plans SET report=? WHERE id=?', (json.dumps(plan.get('report', {})), plan['id']))
            db.executemany(
                "INSERT INTO sort_suggestions "
                "(id,plan_id,file_id,source_relative,destination_relative,reason,confidence,selected,status,error,action_id,created_at,content_summary,content_inspected,review_required,review_reason) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [(s["id"], plan["id"], s.get("file_id"), s["source_relative"], s["destination_relative"],
                   s["reason"], s["confidence"], int(s.get("selected", False)), s.get("status", "suggested"),
                   s.get("error"), s.get("action_id"), s["created_at"], s.get("content_summary", ""),
                   int(s.get("content_inspected", False)), int(s.get("review_required", False)),
                   s.get("review_reason", "")) for s in suggestions],
            )
            db.executemany('UPDATE sort_suggestions SET destination_root_id=?,source_root_id=?,project_name=? WHERE id=?',
                           [(s.get('destination_root_id'), s.get('source_root_id'), s.get('project_name', ''), s['id']) for s in suggestions])

    def get_projects(self) -> list[dict]:
        with self._connect() as db:
            rows = [dict(row) for row in db.execute('SELECT * FROM organization_projects ORDER BY name COLLATE NOCASE')]
        for row in rows:
            row['aliases'] = json.loads(row['aliases'])
        return rows

    def save_project(self, name: str, aliases: list[str], root_id: int, folder: str) -> int:
        with self._connect() as db:
            db.execute('''INSERT INTO organization_projects(name,aliases,root_id,folder,created_at)
                VALUES(?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET aliases=excluded.aliases,
                root_id=excluded.root_id,folder=excluded.folder''',
                (name, json.dumps(aliases), root_id, folder, datetime.now(timezone.utc).isoformat()))
            return db.execute('SELECT id FROM organization_projects WHERE name=? COLLATE NOCASE', (name,)).fetchone()[0]

    def refresh_root_totals(self, *root_ids: int) -> None:
        with self._connect() as db:
            for root_id in set(root_ids):
                count, size = db.execute('SELECT COUNT(*),COALESCE(SUM(size_bytes),0) FROM files WHERE root_id=? AND is_directory=0', (root_id,)).fetchone()
                db.execute('UPDATE roots SET file_count=?,total_bytes=? WHERE id=?', (count, size, root_id))

    def get_sort_plan(self, plan_id: str) -> dict | None:
        with self._connect() as db:
            plan = db.execute("SELECT * FROM sort_plans WHERE id=?", (plan_id,)).fetchone()
            if not plan:
                return None
            result = dict(plan)
            result["suggestions"] = [dict(row) for row in db.execute(
                "SELECT * FROM sort_suggestions WHERE plan_id=? ORDER BY source_relative COLLATE NOCASE",
                (plan_id,),
            )]
            return result

    def get_sort_plans(self, limit: int = 20) -> list[dict]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM sort_plans ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
            return [dict(row) for row in rows]

    def update_sort_plan(self, plan_id: str, updates: dict) -> None:
        if not updates:
            return
        with self._connect() as db:
            columns = list(updates)
            db.execute(
                f"UPDATE sort_plans SET {', '.join(f'{column}=?' for column in columns)} WHERE id=?",
                tuple(updates[column] for column in columns) + (plan_id,),
            )

    def update_sort_suggestion(self, suggestion_id: str, updates: dict) -> None:
        if not updates:
            return
        with self._connect() as db:
            columns = list(updates)
            db.execute(
                f"UPDATE sort_suggestions SET {', '.join(f'{column}=?' for column in columns)} WHERE id=?",
                tuple(updates[column] for column in columns) + (suggestion_id,),
            )
