"""Durable intent/consent ledger; this module never performs a domain action.

create/get/consent/claim/complete/reconcile_interrupted/revoke_pending return PRIVATE records for
trusted local adapters. Never serialize them to HTTP, assistant context or logs.
Use public_action(record) for those boundaries; list_actions and events are
already public projections. Arbitrary result JSON is private even when an adapter
labels it an error, measurement or recovery instruction.

Domain journals remain authoritative for execution and recovery during rollout.
No cross-database crash atomicity or undo is implied. Reconciliation must run only
after the service has exclusive mutation ownership, not beside active executors.
"""

import hashlib
import json
import math
import re
import sqlite3
import uuid
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

from .capabilities import get_capability


SCHEMA_VERSION = 2
MAX_JSON_BYTES = 65536
MAX_PAGE_SIZE = 100
MAX_EXPIRY_SECONDS = 3600
_FORMAT = "kshetrajna.actions"
_ID = re.compile(r"[0-9a-f]{32}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_INFLIGHT = ("executing", "verifying", "measuring")
_TERMINAL = ("succeeded", "failed", "unknown", "recovery_required")
_TRANSITIONS = {
    "executing": ("verifying", "measuring", *_TERMINAL),
    "verifying": ("measuring", *_TERMINAL),
    "measuring": _TERMINAL,
}
_RESULTS = ("outcome", "verification", "measurement", "recovery")


class ActionStoreError(ValueError):
    """Stable public error code, without private SQLite/OS exception details."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(f"Action ledger: {code}")


def _fail(code):
    raise ActionStoreError(code) from None


def _json_object(value):
    """Canonical, bounded JSON with exact integers (up to 256 bits), no coercion."""
    if type(value) is not dict:
        _fail("invalid_json")
    nodes = 0
    string_bytes = 0

    def visit(item, depth):
        nonlocal nodes, string_bytes
        nodes += 1
        if nodes > 4096 or depth > 12:
            _fail("invalid_json")
        kind = type(item)
        if item is None or kind is bool:
            return
        if kind is int:
            if item.bit_length() > 256:
                _fail("invalid_json")
        elif kind is float:
            if not math.isfinite(item):
                _fail("invalid_json")
        elif kind is str:
            if len(item) > MAX_JSON_BYTES:
                _fail("invalid_json")
            try:
                string_bytes += len(item.encode("utf-8"))
            except UnicodeError:
                _fail("invalid_json")
            if string_bytes > MAX_JSON_BYTES:
                _fail("invalid_json")
        elif kind is list:
            if len(item) > 4096:
                _fail("invalid_json")
            for child in item:
                visit(child, depth + 1)
        elif kind is dict:
            if len(item) > 4096:
                _fail("invalid_json")
            for key, child in item.items():
                if type(key) is not str or len(key) > 256:
                    _fail("invalid_json")
                visit(key, depth + 1)
                visit(child, depth + 1)
        else:
            _fail("invalid_json")

    visit(value, 0)
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False)
        if len(encoded.encode("utf-8")) > MAX_JSON_BYTES:
            _fail("invalid_json")
        return encoded
    except (ValueError, UnicodeError, RecursionError):
        _fail("invalid_json")


def digest_evidence(evidence: dict) -> str:
    """SHA256 of canonical UTF-8 evidence; action ID separately binds the target."""
    return hashlib.sha256(_json_object(evidence).encode("utf-8")).hexdigest()


def _identifier(value):
    if type(value) is not str or not _ID.fullmatch(value):
        _fail("invalid_id")


def _digest(value):
    if type(value) is not str or not _DIGEST.fullmatch(value):
        _fail("invalid_digest")


def _key(value, *, optional=False):
    if optional and value is None:
        return
    if (type(value) is not str or not 1 <= len(value) <= 256
            or any(not 33 <= ord(char) <= 126 for char in value)):
        _fail("invalid_key")


def public_action(record: dict) -> dict:
    """Allowlisted display record. No arbitrary target, evidence or result JSON."""
    result = {key: record[key] for key in (
        "id", "version", "kind", "mode", "status", "created_at", "updated_at",
        "expires_at", "evidence_digest",
        "erased_at",
    )}
    result["capability"] = {key: record["capability"][key] for key in (
        "kind", "risk", "visibility", "optin_setting", "recovery",
    )}
    consent = record["consent"]
    result["consent"] = None if consent is None else {
        "confirmed": True, "approved_at": consent["approved_at"], "used_at": consent["used_at"],
    }
    for field in ("preflight", *_RESULTS):
        result[f"has_{field}"] = record[field] is not None
    return result


_SCHEMA_V1 = (
    "CREATE TABLE action_meta (id INTEGER PRIMARY KEY CHECK(id=1), format TEXT NOT NULL)",
    """CREATE TABLE actions (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        id TEXT NOT NULL UNIQUE,
        version INTEGER NOT NULL CHECK(version >= 1),
        kind TEXT NOT NULL,
        mode TEXT NOT NULL CHECK(mode IN ('live','demo')),
        status TEXT NOT NULL CHECK(status IN
            ('proposed','approved','executing','verifying','measuring',
             'succeeded','failed','unknown','recovery_required')),
        target_json TEXT NOT NULL,
        evidence_json TEXT NOT NULL,
        evidence_digest TEXT NOT NULL,
        capability_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        consented_at TEXT,
        claimed_at TEXT,
        idempotency_key TEXT UNIQUE,
        request_digest TEXT NOT NULL,
        preflight_json TEXT,
        outcome_json TEXT,
        verification_json TEXT,
        measurement_json TEXT,
        recovery_json TEXT
    )""",
    "CREATE INDEX actions_status ON actions(status)",
    """CREATE TABLE action_events (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        action_id TEXT NOT NULL REFERENCES actions(id),
        version INTEGER NOT NULL,
        type TEXT NOT NULL,
        occurred_at TEXT NOT NULL,
        from_status TEXT,
        to_status TEXT NOT NULL,
        UNIQUE(action_id, version)
    )""",
)

_SCHEMA = (
    "CREATE TABLE action_meta (id INTEGER PRIMARY KEY CHECK(id=1), format TEXT NOT NULL, high_water_at TEXT)",
    """CREATE TABLE actions (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        id TEXT NOT NULL UNIQUE,
        version INTEGER NOT NULL CHECK(version >= 1),
        kind TEXT NOT NULL,
        mode TEXT NOT NULL CHECK(mode IN ('live','demo')),
        status TEXT NOT NULL CHECK(status IN
            ('proposed','approved','executing','verifying','measuring',
             'succeeded','failed','unknown','recovery_required','expired','revoked')),
        target_json TEXT,
        evidence_json TEXT,
        evidence_digest TEXT NOT NULL,
        capability_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        consented_at TEXT,
        claimed_at TEXT,
        idempotency_key TEXT UNIQUE,
        idempotency_digest TEXT UNIQUE,
        request_digest TEXT NOT NULL,
        preflight_json TEXT,
        outcome_json TEXT,
        verification_json TEXT,
        measurement_json TEXT,
        recovery_json TEXT,
        erased_at TEXT
    )""",
    _SCHEMA_V1[2],
    _SCHEMA_V1[3],
)


def _normalized_ddl(sql):
    parts = re.split(r"('(?:''|[^'])*')", sql)
    return "".join(part if index % 2 else re.sub(r'\s+|"', '', part).lower()
                   for index, part in enumerate(parts))


def _schema_signature(db):
    """Compare actual SQLite constraints, not just column-name lookalikes."""
    tables = []
    for table in ("action_meta", "actions", "action_events"):
        columns = tuple(tuple(row) for row in db.execute(f"PRAGMA table_xinfo({table})"))
        indexes = []
        for index in db.execute(f"PRAGMA index_list({table})"):
            # SQLite supplies the identifier. Quote it rather than interpolating SQL.
            quoted = '"' + index[1].replace('"', '""') + '"'
            detail = tuple(tuple(row) for row in db.execute(f"PRAGMA index_xinfo({quoted})"))
            indexes.append((index[2], index[3], index[4], detail))
        foreign_keys = tuple(tuple(row) for row in db.execute(f"PRAGMA foreign_key_list({table})"))
        sql = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
        # Exact known DDL also checks CHECK clauses and AUTOINCREMENT. Whitespace
        # and SQLite's optional quotes around our fixed table names are immaterial.
        normalized = _normalized_ddl(sql[0]) if sql else None
        tables.append((columns, tuple(sorted(indexes)), foreign_keys, normalized))
    return tuple(tables)


@lru_cache(maxsize=2)
def _expected_schema(version):
    with closing(sqlite3.connect(":memory:")) as db:
        for statement in (_SCHEMA_V1 if version == 1 else _SCHEMA):
            db.execute(statement)
        return _schema_signature(db)


def _key_digest(key):
    return hashlib.sha256(key.encode("ascii")).hexdigest() if key is not None else None


class ActionStore:
    """SQLite file ledger. ``require_existing`` fails closed after cutover.

    The optional clock supplies aware datetimes. Connections are per operation,
    WAL/FULL, and write transactions use BEGIN IMMEDIATE across store instances.
    Missing stores are never recreated by an already constructed instance.
    """

    def __init__(self, path, *, clock=None, require_existing=False):
        if type(require_existing) is not bool:
            _fail("invalid_option")
        if not isinstance(path, (str, Path)) or not str(path) or "\x00" in str(path):
            _fail("invalid_path")
        if clock is not None and not callable(clock):
            _fail("invalid_clock")
        self._clock = clock if clock is not None else lambda: datetime.now(timezone.utc)
        try:
            self.path = Path(path).absolute()
            if require_existing and not self.path.is_file():
                _fail("missing_store")
            if not require_existing:
                self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._connection(allow_create=not require_existing) as db:
                db.execute("BEGIN IMMEDIATE")
                version = db.execute("PRAGMA user_version").fetchone()[0]
                if version == 0:
                    if require_existing:
                        _fail("unsupported_schema")
                    tables = db.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
                    if tables:
                        _fail("unsupported_schema")
                    for statement in _SCHEMA:
                        db.execute(statement)
                    db.execute("INSERT INTO action_meta (id,format) VALUES (1, ?)", (_FORMAT,))
                    db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                elif version == 1:
                    self._migrate_v1(db)
                self._check_schema(db)
                db.commit()
                if db.execute("PRAGMA journal_mode=WAL").fetchone()[0] != "wal":
                    _fail("storage_error")
        except OSError:
            _fail("storage_error")

    @contextmanager
    def _connection(self, *, allow_create=False):
        db = None
        try:
            if not allow_create and not self.path.is_file():
                _fail("missing_store")
            uri = self.path.as_uri() + ("?mode=rwc" if allow_create else "?mode=rw")
            db = sqlite3.connect(uri, uri=True, timeout=10, isolation_level=None)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA secure_delete=ON")
            yield db
        except (sqlite3.Error, OSError):
            _fail("storage_error")
        finally:
            if db is not None:
                try:
                    try:
                        if db.in_transaction:
                            db.rollback()
                    finally:
                        db.close()
                except (sqlite3.Error, OSError):
                    _fail("storage_error")

    @staticmethod
    def _check_schema(db, version=SCHEMA_VERSION):
        if db.execute("PRAGMA user_version").fetchone()[0] != version:
            _fail("unsupported_schema")
        if _schema_signature(db) != _expected_schema(version):
            _fail("unsupported_schema")
        marker = db.execute("SELECT format FROM action_meta WHERE id=1").fetchone()
        if marker is None or marker[0] != _FORMAT:
            _fail("unsupported_schema")

    def _migrate_v1(self, db):
        """Only our previous ledger format; never import/move domain journals."""
        self._check_schema(db, version=1)
        actions = [dict(row) for row in db.execute("SELECT * FROM actions ORDER BY seq")]
        events = [dict(row) for row in db.execute("SELECT * FROM action_events ORDER BY seq")]
        observed = [self._timestamp(self._now())]
        observed.extend(row["updated_at"] for row in actions)
        high_water = max(observed)
        for table in ("action_events", "actions", "action_meta"):
            db.execute(f"DROP TABLE {table}")
        for statement in _SCHEMA:
            db.execute(statement)
        db.execute("INSERT INTO action_meta VALUES (1,?,?)", (_FORMAT, high_water))
        for row in actions:
            row["idempotency_digest"] = _key_digest(row["idempotency_key"])
            columns = ",".join(row)
            placeholders = ",".join("?" for _ in row)
            db.execute(f"INSERT INTO actions ({columns}) VALUES ({placeholders})", tuple(row.values()))
        for row in events:
            columns = ",".join(row)
            placeholders = ",".join("?" for _ in row)
            db.execute(f"INSERT INTO action_events ({columns}) VALUES ({placeholders})", tuple(row.values()))
        # V1 did not durably fence time or require preflight. Old unconsumed
        # approvals must be reviewed again; migration cannot manufacture consent.
        self._fence_pending(db, high_water, "revoked", "schema_migrated")
        db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    @contextmanager
    def _transaction(self, *, write=False):
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            self._check_schema(db)
            if db.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
                _fail("storage_error")
            yield db
            db.commit()

    def _fence_pending(self, db, now, status, event_type, *, kinds=None, expired_only=False):
        query = "SELECT * FROM actions WHERE status IN ('proposed','approved') AND claimed_at IS NULL"
        parameters = []
        if expired_only:
            query += " AND expires_at<=?"
            parameters.append(now)
        if kinds is not None:
            query += " AND kind IN (" + ",".join("?" for _ in kinds) + ")"
            parameters.extend(kinds)
        rows = db.execute(query + " ORDER BY seq", parameters).fetchall()
        changed = []
        for row in rows:
            updated = db.execute("UPDATE actions SET status=?, updated_at=?, version=version+1 "
                                 "WHERE id=? AND status=? AND version=? AND claimed_at IS NULL",
                                 (status, now, row["id"], row["status"], row["version"]))
            if updated.rowcount != 1:
                _fail("invalid_transition")
            current = self._row(db, row["id"])
            self._event(db, current, event_type, now, row["status"])
            changed.append(self._record(current))
        return changed

    @contextmanager
    def _mutation(self, *, require_fresh_time=False):
        """Persist time fences even when the requested mutation is rejected.

        Reads do not observe/advance time. Mutations serialize observations in
        this database; a rollback invalidates all unused approvals permanently.
        Recovery/finalization can continue at the last observed UTC timestamp,
        but new intent, consent and execution fail until the clock catches up.
        """
        with self._transaction(write=True) as db:
            observed = self._timestamp(self._now())
            high_water = db.execute("SELECT high_water_at FROM action_meta WHERE id=1").fetchone()[0]
            rollback = high_water is not None and observed < high_water
            now = max(observed, high_water) if high_water is not None else observed
            db.execute("UPDATE action_meta SET high_water_at=? WHERE id=1", (now,))
            self._fence_pending(db, now, "expired", "expired", expired_only=True)
            if rollback:
                self._fence_pending(db, now, "revoked", "clock_rollback")
                if require_fresh_time:
                    db.commit()
                    _fail("clock_rollback")
            db.execute("SAVEPOINT ledger_request")
            try:
                yield db, now
            except ActionStoreError:
                db.execute("ROLLBACK TO ledger_request")
                db.execute("RELEASE ledger_request")
                db.commit()
                raise
            db.execute("RELEASE ledger_request")

    def _now(self):
        try:
            value = self._clock()
            if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
                _fail("invalid_clock")
            return value.astimezone(timezone.utc)
        except Exception:
            _fail("invalid_clock")

    @staticmethod
    def _timestamp(value):
        return value.isoformat(timespec="microseconds")

    @staticmethod
    def _record(row):
        result = {key: row[key] for key in (
            "id", "version", "kind", "mode", "status", "created_at", "updated_at",
            "expires_at", "evidence_digest", "idempotency_key", "erased_at",
        )}
        for field in ("target", "evidence", "capability", "preflight", *_RESULTS):
            value = row[f"{field}_json"]
            result[field] = json.loads(value) if value is not None else None
        result["consent"] = None if row["consented_at"] is None else {
            "confirmed": True, "approved_at": row["consented_at"],
            "evidence_digest": row["evidence_digest"], "used_at": row["claimed_at"],
        }
        return result

    @staticmethod
    def _row(db, action_id):
        row = db.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone()
        if row is None:
            _fail("not_found")
        return row

    @staticmethod
    def _event(db, row, event_type, now, from_status):
        db.execute("INSERT INTO action_events "
                   "(action_id,version,type,occurred_at,from_status,to_status) VALUES (?,?,?,?,?,?)",
                   (row["id"], row["version"], event_type, now, from_status, row["status"]))

    def create(self, kind, target, evidence, mode="live", expires_seconds=300,
               idempotency_key=None):
        """Persist immutable intent. A matching creation key returns its old record.

        Keys are globally unique in this store. Repeating creation never renews
        expiry, grants consent or makes an executed action executable again.
        """
        try:
            capability = get_capability(kind)
        except ValueError:
            _fail("unknown_kind")
        if type(mode) is not str or mode not in ("live", "demo"):
            _fail("invalid_mode")
        if type(expires_seconds) is not int or not 1 <= expires_seconds <= MAX_EXPIRY_SECONDS:
            _fail("invalid_expiry")
        _key(idempotency_key, optional=True)
        target_json = _json_object(target)
        evidence_json = _json_object(evidence)
        evidence_hash = hashlib.sha256(evidence_json.encode("utf-8")).hexdigest()
        # Include original expiry duration, not wall time, in creation-key identity.
        request = json.dumps([kind, target_json, evidence_json, mode, expires_seconds],
                             separators=(",", ":"), ensure_ascii=False)
        request_hash = hashlib.sha256(request.encode("utf-8")).hexdigest()
        with self._mutation(require_fresh_time=True) as (db, timestamp):
            if idempotency_key is not None:
                previous = db.execute("SELECT * FROM actions WHERE idempotency_digest=?",
                                      (_key_digest(idempotency_key),)).fetchone()
                if previous is not None:
                    if previous["request_digest"] != request_hash:
                        _fail("idempotency_conflict")
                    return self._record(previous)
            try:
                expires_at = self._timestamp(datetime.fromisoformat(timestamp) + timedelta(seconds=expires_seconds))
            except OverflowError:
                _fail("invalid_clock")
            action_id = uuid.uuid4().hex
            db.execute("""INSERT INTO actions
                (id,version,kind,mode,status,target_json,evidence_json,evidence_digest,
                 capability_json,created_at,updated_at,expires_at,idempotency_key,idempotency_digest,request_digest)
                VALUES (?,1,?,?,'proposed',?,?,?,?,?,?,?,?,?,?)""",
                       (action_id, kind, mode, target_json, evidence_json, evidence_hash,
                         _json_object(capability.as_dict()), timestamp, timestamp,
                        expires_at, idempotency_key, _key_digest(idempotency_key), request_hash))
            row = self._row(db, action_id)
            self._event(db, row, "created", timestamp, None)
            return self._record(row)

    def get(self, id):
        """Return a trusted PRIVATE record, or None for an absent valid ID."""
        _identifier(id)
        with self._transaction() as db:
            row = db.execute("SELECT * FROM actions WHERE id=?", (id,)).fetchone()
            return self._record(row) if row is not None else None

    def list_actions(self, limit=50, cursor=None):
        """Newest-first PUBLIC records, keyset-paginated by a decimal cursor.

        Inserts between pages do not move the continuation boundary. The cursor
        is not an authorization token and can safely be treated as opaque.
        """
        if type(limit) is not int or not 1 <= limit <= MAX_PAGE_SIZE:
            _fail("invalid_limit")
        boundary = None
        if cursor is not None:
            if (type(cursor) is not str or not re.fullmatch(r"[1-9][0-9]{0,18}", cursor)
                    or int(cursor) > 2**63 - 1):
                _fail("invalid_cursor")
            boundary = int(cursor)
        with self._transaction() as db:
            if boundary is None:
                rows = db.execute("SELECT * FROM actions ORDER BY seq DESC LIMIT ?", (limit + 1,)).fetchall()
            else:
                rows = db.execute("SELECT * FROM actions WHERE seq < ? ORDER BY seq DESC LIMIT ?",
                                  (boundary, limit + 1)).fetchall()
            return {
                "items": [public_action(self._record(row)) for row in rows[:limit]],
                "next_cursor": str(rows[limit - 1]["seq"]) if len(rows) > limit else None,
            }

    def events(self, id):
        """Public immutable lifecycle events, oldest first (at most seven per action).

        No arbitrary append/update/delete API is exposed. The monotone lifecycle
        bounds each action's event list; cross-action queries use list_actions.
        This audit trail is not tamper-proof against the owning OS user.
        """
        _identifier(id)
        with self._transaction() as db:
            self._row(db, id)
            return [dict(row) for row in db.execute(
                "SELECT action_id,version,type,occurred_at,from_status,to_status "
                "FROM action_events WHERE action_id=? ORDER BY version", (id,))]

    @staticmethod
    def _approval_checks(row, evidence_digest, now, expected_status):
        if row["status"] in ("expired", "revoked"):
            _fail(row["status"])
        if row["status"] != expected_status or row["claimed_at"] is not None:
            _fail("invalid_transition")
        if row["expires_at"] <= now:
            _fail("expired")
        if row["evidence_digest"] != evidence_digest:
            _fail("evidence_mismatch")

    def consent(self, id, evidence_digest, confirm=False):
        """Approve this immutable action/evidence once; only literal True consents."""
        _identifier(id)
        _digest(evidence_digest)
        if confirm is not True:
            _fail("confirmation_required")
        with self._mutation(require_fresh_time=True) as (db, now):
            row = self._row(db, id)
            self._approval_checks(row, evidence_digest, now, "proposed")
            updated = db.execute("UPDATE actions SET status='approved', consented_at=?, "
                                 "updated_at=?, version=version+1 "
                                 "WHERE id=? AND status='proposed' AND version=? AND claimed_at IS NULL",
                                 (now, now, id, row["version"]))
            if updated.rowcount != 1:
                _fail("invalid_transition")
            approved = self._row(db, id)
            self._event(db, approved, "consented", now, row["status"])
            return self._record(approved)

    def claim(self, id, idempotency_key, evidence_digest, preflight=None):
        """Atomically consume approval and persist execution intent BEFORE mutation.

        Even the same key cannot claim twice. The domain adapter must freshly
        validate its target/evidence and permissions; this is only the ledger's
        compare-and-swap, not an OS preflight or permission grant. A nonempty
        bounded preflight object with literal passed=True is required and saved
        in the same transaction as the one-use claim and execution-intent event.
        """
        _identifier(id)
        _key(idempotency_key)
        _digest(evidence_digest)
        with self._mutation(require_fresh_time=True) as (db, now):
            row = self._row(db, id)
            self._approval_checks(row, evidence_digest, now, "approved")
            if row["consented_at"] is None:
                _fail("invalid_transition")
            key_hash = _key_digest(idempotency_key)
            if row["idempotency_digest"] is not None and row["idempotency_digest"] != key_hash:
                _fail("idempotency_conflict")
            other = db.execute("SELECT id FROM actions WHERE idempotency_digest=? AND id<>?",
                               (key_hash, id)).fetchone()
            if other is not None:
                _fail("idempotency_conflict")
            if preflight is None or (type(preflight) is dict and not preflight):
                _fail("preflight_required")
            preflight_json = _json_object(preflight)
            if preflight.get("passed") is not True:
                _fail("preflight_failed")
            updated = db.execute("UPDATE actions SET status='executing', claimed_at=?, "
                                 "updated_at=?, idempotency_key=?, idempotency_digest=?, "
                                 "preflight_json=?, version=version+1 "
                                 "WHERE id=? AND status='approved' AND version=? "
                                 "AND claimed_at IS NULL AND preflight_json IS NULL "
                                 "AND expires_at>? AND evidence_digest=?",
                                 (now, now, idempotency_key, key_hash, preflight_json,
                                  id, row["version"], now, evidence_digest))
            if updated.rowcount != 1:
                _fail("invalid_transition")
            claimed = self._row(db, id)
            self._event(db, claimed, "claimed", now, row["status"])
            return self._record(claimed)

    def complete(self, id, status, *, outcome=None, verification=None, measurement=None, recovery=None):
        """Record an adapter's stage/terminal outcome, never infer missing metrics.

        None leaves a result absent or unchanged. Non-null result objects are
        write-once. Stages may be skipped but cannot be repeated or reversed.
        Terminal records cannot be finalized again, including uncertain records.
        """
        _identifier(id)
        if type(status) is not str or status not in ("verifying", "measuring", *_TERMINAL):
            _fail("invalid_transition")
        values = dict(zip(_RESULTS, (outcome, verification, measurement, recovery)))
        encoded = {field: _json_object(value) if value is not None else None
                   for field, value in values.items()}
        with self._mutation() as (db, now):
            row = self._row(db, id)
            if status not in _TRANSITIONS.get(row["status"], ()) or row["claimed_at"] is None:
                _fail("invalid_transition")
            for field, value in encoded.items():
                old = row[f"{field}_json"]
                if old is not None and value is not None and old != value:
                    _fail("immutable_result")
            updated = db.execute("UPDATE actions SET status=?, updated_at=?, version=version+1, "
                                 "outcome_json=COALESCE(outcome_json, ?), "
                                 "verification_json=COALESCE(verification_json, ?), "
                                 "measurement_json=COALESCE(measurement_json, ?), "
                                 "recovery_json=COALESCE(recovery_json, ?) "
                                 "WHERE id=? AND version=? AND status=?",
                                 (status, now, *encoded.values(), id, row["version"], row["status"]))
            if updated.rowcount != 1:
                _fail("invalid_transition")
            completed = self._row(db, id)
            self._event(db, completed, status, now, row["status"])
            return self._record(completed)

    def reconcile_interrupted(self):
        """Fence ALL unfinished claims as uncertain. Never execute, retry or undo.

        Run at startup under exclusive service ownership. Existing domain
        journals decide recovery; a policy describes a possible recovery route,
        not proof of rollback. Return the changed PRIVATE records.
        """
        with self._mutation() as (db, now):
            rows = db.execute("SELECT * FROM actions WHERE status IN (?,?,?) ORDER BY seq",
                              _INFLIGHT).fetchall()
            if not rows:
                return []
            changed = []
            for row in rows:
                policy = json.loads(row["capability_json"])["recovery"]
                status = "unknown" if policy == "none" else "recovery_required"
                updated = db.execute("UPDATE actions SET status=?, updated_at=?, version=version+1 "
                                     "WHERE id=? AND status=? AND version=?",
                                     (status, now, row["id"], row["status"], row["version"]))
                if updated.rowcount != 1:
                    _fail("invalid_transition")
                interrupted = self._row(db, row["id"])
                self._event(db, interrupted, "interrupted", now, row["status"])
                changed.append(self._record(interrupted))
            return changed

    def revoke_pending(self, kinds=None):
        """Durably revoke unused proposals/approvals before disabling a capability.

        None selects all kinds. Otherwise pass a nonempty, duplicate-free list
        of registered kind strings. In-flight work and necessary recovery are
        never revoked by this method. Return changed PRIVATE records.
        """
        if kinds is not None:
            if type(kinds) is not list or not kinds or len(kinds) > 12:
                _fail("invalid_scope")
            checked = []
            for kind in kinds:
                try:
                    get_capability(kind)
                except ValueError:
                    _fail("invalid_scope")
                if kind in checked:
                    _fail("invalid_scope")
                checked.append(kind)
            kinds = checked
        with self._mutation() as (db, now):
            return self._fence_pending(db, now, "revoked", "revoked", kinds=kinds)

    def erase_history(self, ids, *, confirm=False):
        """Explicitly redact selected resolved history; never delete action rows.

        Pass 1..100 distinct IDs and literal confirm=True. Return erased IDs and
        preserved IDs with reasons. Pending, in-flight and uncertain work stays
        private-but-intact. Claimed recoverable actions or any recovery payload
        are conservatively retained: only authoritative adapters can establish
        that domain recovery is resolved, and this API does not guess it.

        Replay hashes, approval metadata and append-only events remain as audit
        tombstones. secure_delete and a best-effort WAL truncation address local
        SQLite remnants, NOT backups, other readers' snapshots or secure media
        erasure. storage_cleanup='pending' explicitly reports a pinned WAL.
        """
        if confirm is not True:
            _fail("confirmation_required")
        if (type(ids) is not list or not 1 <= len(ids) <= MAX_PAGE_SIZE
                or any(type(id) is not str or not _ID.fullmatch(id) for id in ids)
                or len(set(ids)) != len(ids)):
            _fail("invalid_scope")
        erased, preserved = [], []
        with self._mutation() as (db, now):
            rows = [self._row(db, id) for id in ids]
            for row in rows:
                reason = None
                if row["erased_at"] is not None:
                    reason = "already_erased"
                elif row["status"] in ("proposed", "approved"):
                    reason = "pending"
                elif row["status"] not in ("succeeded", "failed", "expired", "revoked"):
                    reason = "unresolved"
                elif (row["recovery_json"] is not None or
                      (row["claimed_at"] is not None and json.loads(row["capability_json"])["recovery"] != "none")):
                    reason = "recovery_retained"
                if reason is not None:
                    preserved.append({"id": row["id"], "reason": reason})
                    continue
                updated = db.execute("UPDATE actions SET target_json=NULL, evidence_json=NULL, "
                                     "preflight_json=NULL, outcome_json=NULL, verification_json=NULL, "
                                     "measurement_json=NULL, recovery_json=NULL, idempotency_key=NULL, "
                                     "erased_at=?, updated_at=?, version=version+1 "
                                     "WHERE id=? AND version=? AND erased_at IS NULL",
                                     (now, now, row["id"], row["version"]))
                if updated.rowcount != 1:
                    _fail("invalid_transition")
                redacted = self._row(db, row["id"])
                self._event(db, redacted, "history_erased", now, row["status"])
                erased.append(row["id"])
        with self._connection() as db:
            self._check_schema(db)
            if db.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
                _fail("storage_error")
            db.execute("PRAGMA busy_timeout=0")
            busy, _, _ = db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        return {"erased": erased, "preserved": preserved,
                "storage_cleanup": "pending" if busy else "checkpointed"}
