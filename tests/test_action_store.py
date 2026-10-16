"""Durable ledger tests; all mutations are confined to temporary databases."""

import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import traceback
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from kshetrajna import action_store as ledger
from kshetrajna.action_store import (
    ActionStore, ActionStoreError, MAX_JSON_BYTES, MAX_PAGE_SIZE, SCHEMA_VERSION,
    digest_evidence, public_action,
)
from kshetrajna.capabilities import CAPABILITIES, get_capability


class ActionStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "actions.db"
        self.now = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)
        self.store = ActionStore(self.path, clock=lambda: self.now)
        self.target = {"path": "C:/private/person/report.txt", "created_ticks": 2**64 - 1}
        self.evidence = {"size": 2**63 + 123, "identity": [1, 2**80], "missing": None}

    def create(self, **kwargs):
        return self.store.create("file.quarantine", self.target, self.evidence, **kwargs)

    def approve(self, **kwargs):
        action = self.create(**kwargs)
        return self.store.consent(action["id"], action["evidence_digest"], confirm=True)

    def claim(self, key="execute-1", **kwargs):
        action = self.approve(**kwargs)
        return self.store.claim(action["id"], key, action["evidence_digest"], preflight={"passed": True})

    def assert_code(self, code, function, *args, **kwargs):
        with self.assertRaises(ActionStoreError) as caught:
            function(*args, **kwargs)
        self.assertEqual(code, caught.exception.code)
        self.assertNotIn("private", str(caught.exception))

    def test_create_persists_lossless_private_record_and_reopens(self):
        action = self.create()
        self.assertEqual("proposed", action["status"])
        self.assertEqual(1, action["version"])
        self.assertEqual(self.target, action["target"])
        self.assertEqual(self.evidence, action["evidence"])
        self.assertIsNone(action["consent"])
        for field in ("preflight", "outcome", "verification", "measurement", "recovery"):
            self.assertIsNone(action[field])
        self.assertEqual("2026-10-16T12:00:00.000000+00:00", action["created_at"])
        self.assertEqual("2026-10-16T12:05:00.000000+00:00", action["expires_at"])
        reopened = ActionStore(self.path)
        self.assertEqual(action, reopened.get(action["id"]))
        self.assertEqual(["created"], [e["type"] for e in reopened.events(action["id"])])
        action["target"]["path"] = "changed"
        self.assertEqual(self.target, reopened.get(action["id"])["target"])

    def test_evidence_digest_is_canonical_sha256_without_integer_rounding(self):
        expected = hashlib.sha256(json.dumps(
            self.evidence, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False).encode("utf-8")).hexdigest()
        self.assertEqual(expected, digest_evidence(self.evidence))
        self.assertEqual(expected, digest_evidence(dict(reversed(list(self.evidence.items())))))
        self.assertNotEqual(digest_evidence({"n": 2**64}), digest_evidence({"n": 2**64 + 1}))

    def test_lifecycle_and_private_results_are_durable(self):
        action = self.claim()
        self.assertEqual("executing", action["status"])
        self.assertEqual(3, action["version"])
        self.assertIsNotNone(action["consent"]["used_at"])
        action = self.store.complete(action["id"], "verifying", outcome={"path": "private"})
        action = self.store.complete(action["id"], "measuring", verification={"matched": True})
        action = self.store.complete(action["id"], "succeeded", measurement={"bytes": None})
        self.assertEqual({"path": "private"}, action["outcome"])
        self.assertEqual({"matched": True}, action["verification"])
        self.assertEqual({"bytes": None}, action["measurement"])
        self.assertEqual(action, ActionStore(self.path).get(action["id"]))
        events = self.store.events(action["id"])
        self.assertEqual(list(range(1, 7)), [e["version"] for e in events])
        self.assertEqual(["created", "consented", "claimed", "verifying", "measuring", "succeeded"],
                         [e["type"] for e in events])
        self.assert_code("invalid_transition", self.store.complete, action["id"], "failed")

    def test_consent_requires_literal_true_and_claim_requires_approval(self):
        action = self.create()
        digest = action["evidence_digest"]
        for confirm in (False, None, 0, 1, "true", "false", [], {}):
            self.assert_code("confirmation_required", self.store.consent,
                             action["id"], digest, confirm=confirm)
        self.assert_code("confirmation_required", self.store.consent, action["id"], digest)
        self.assert_code("invalid_transition", self.store.claim, action["id"], "key", digest)
        self.assertEqual(1, len(self.store.events(action["id"])))

    def test_used_consent_and_claim_cannot_be_replayed(self):
        action = self.approve()
        self.assert_code("invalid_transition", self.store.consent,
                         action["id"], action["evidence_digest"], confirm=True)
        claimed = self.store.claim(action["id"], "once", action["evidence_digest"], preflight={"passed": True})
        for key in ("once", "another"):
            self.assert_code("invalid_transition", self.store.claim,
                             action["id"], key, action["evidence_digest"])
        self.store.complete(claimed["id"], "failed")
        self.assert_code("invalid_transition", self.store.claim,
                         action["id"], "once", action["evidence_digest"])

    def test_expiry_is_exact_and_never_extended_by_consent(self):
        action = self.create(expires_seconds=10)
        self.now += timedelta(seconds=9)
        approved = self.store.consent(action["id"], action["evidence_digest"], confirm=True)
        self.assertEqual(action["expires_at"], approved["expires_at"])
        self.now += timedelta(seconds=1)
        self.assert_code("expired", self.store.claim, action["id"], "key", action["evidence_digest"])
        self.assertEqual("expired", self.store.get(action["id"])["status"])
        pending = self.create(expires_seconds=1)
        self.now += timedelta(seconds=1)
        self.assert_code("expired", self.store.consent,
                         pending["id"], pending["evidence_digest"], confirm=True)

    def test_digest_mismatch_fails_without_consuming_approval(self):
        action = self.create()
        wrong = "0" * 64
        self.assert_code("evidence_mismatch", self.store.consent, action["id"], wrong, confirm=True)
        self.store.consent(action["id"], action["evidence_digest"], confirm=True)
        self.assert_code("evidence_mismatch", self.store.claim, action["id"], "key", wrong)
        self.assertEqual("executing", self.store.claim(
            action["id"], "key", action["evidence_digest"], preflight={"passed": True})["status"])

    def test_create_idempotency_returns_original_but_never_authorizes_replay(self):
        original = self.create(idempotency_key="request")
        self.now += timedelta(seconds=5)
        self.assertEqual(original, self.create(idempotency_key="request"))
        self.assertEqual(1, len(self.store.events(original["id"])))
        self.assert_code("idempotency_conflict", self.store.create,
                         "file.move", self.target, self.evidence, idempotency_key="request")
        self.store.consent(original["id"], original["evidence_digest"], confirm=True)
        self.assert_code("idempotency_conflict", self.store.claim,
                         original["id"], "different", original["evidence_digest"])
        self.store.claim(original["id"], "request", original["evidence_digest"], preflight={"passed": True})
        repeated = self.create(idempotency_key="request")
        self.assertEqual("executing", repeated["status"])
        self.assert_code("invalid_transition", self.store.claim,
                         repeated["id"], "request", repeated["evidence_digest"])

    def test_execution_keys_are_global_even_for_otherwise_identical_actions(self):
        one = self.claim(key="global")
        two = self.approve()
        self.assertNotEqual(one["id"], two["id"])
        self.assert_code("idempotency_conflict", self.store.claim,
                         two["id"], "global", two["evidence_digest"])
        self.assertEqual("approved", self.store.get(two["id"])["status"])

    def test_two_stores_claim_compare_and_swap_allows_exactly_one(self):
        action = self.approve()
        stores = [self.store, ActionStore(self.path, clock=lambda: self.now)]
        barrier = threading.Barrier(2)

        def claim(index):
            barrier.wait()
            try:
                return stores[index].claim(action["id"], f"race-{index}", action["evidence_digest"],
                                           preflight={"passed": True})["status"]
            except ActionStoreError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(claim, range(2)))
        self.assertCountEqual(["executing", "invalid_transition"], results)
        self.assertEqual(3, len(self.store.events(action["id"])))

    def test_two_stores_creation_key_is_atomic(self):
        stores = [self.store, ActionStore(self.path, clock=lambda: self.now)]
        barrier = threading.Barrier(2)

        def create(index):
            barrier.wait()
            return stores[index].create("file.move", {}, {}, idempotency_key="race-create")["id"]

        with ThreadPoolExecutor(max_workers=2) as executor:
            ids = list(executor.map(create, range(2)))
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(1, len(self.store.events(ids[0])))

    def test_registry_is_frozen_closed_and_does_not_execute_anything(self):
        expected = {"file.move", "file.rename", "file.quarantine", "file.restore",
                    "file.delete_permanent", "system.priority_trial", "system.memory_trim",
                    "system.process_close", "system.process_terminate", "system.temp_cleanup",
                    "system.external_tool", "system.model_unload"}
        self.assertEqual(expected, set(CAPABILITIES))
        danger = get_capability("file.delete_permanent")
        self.assertEqual("danger", danger.risk)
        self.assertEqual("expert", danger.visibility)
        self.assertEqual("allow_permanent_delete", danger.optin_setting)
        self.assertEqual("none", danger.recovery)
        self.assertIsNone(get_capability("file.restore").optin_setting)
        with self.assertRaises(TypeError):
            CAPABILITIES["shell"] = danger
        self.assert_code("unknown_kind", self.store.create, "shell", {}, {})

    def test_strict_bounded_json_and_arguments(self):
        cycle = []
        cycle.append(cycle)
        invalid = [None, [], "x", {1: "non-string key"}, {"n": float("nan")},
                   {"n": float("inf")}, {"n": 2**257}, {"v": (1, 2)},
                   {"v": object()}, {"v": "\ud800"}, {"v": cycle},
                   {"v": "x" * MAX_JSON_BYTES}, {"v": [0] * 5000}]
        for value in invalid:
            with self.subTest(value_type=type(value).__name__):
                self.assert_code("invalid_json", self.store.create, "file.move", value, {})
                self.assert_code("invalid_json", self.store.create, "file.move", {}, value)
        for value in (0, -1, True, 1.0, "300", 3601):
            self.assert_code("invalid_expiry", self.create, expires_seconds=value)
        for value in ("basic", "expert", "LIVE", True, None):
            self.assert_code("invalid_mode", self.create, mode=value)
        for value in ("", True, 1, "x" * 257, "line\nbreak"):
            self.assert_code("invalid_key", self.create, idempotency_key=value)
        self.assertEqual([], self.store.list_actions()["items"])

    def test_public_projection_and_events_never_return_arbitrary_private_data(self):
        action = self.claim(key="private-key")
        action = self.store.complete(action["id"], "failed", outcome={"error": "C:/private"},
                                     recovery={"path": "C:/private/recovery"})
        public = public_action(action)
        self.assertEqual(public, self.store.list_actions()["items"][0])
        self.assertTrue(public["has_outcome"])
        self.assertFalse(public["has_measurement"])
        for field in ("target", "evidence", "outcome", "preflight", "measurement", "verification",
                      "recovery", "idempotency_key"):
            self.assertNotIn(field, public)
        encoded = json.dumps({"action": public, "events": self.store.events(action["id"])})
        self.assertNotIn("private", encoded)
        self.assertNotIn("report.txt", encoded)
        self.assertNotIn("C:/", encoded)

    def test_pagination_is_bounded_stable_and_has_no_overlap_on_insert(self):
        ids = [self.create()["id"] for _ in range(7)]
        page = self.store.list_actions(limit=3)
        self.assertEqual(list(reversed(ids[-3:])), [x["id"] for x in page["items"]])
        self.create()
        collected = [x["id"] for x in page["items"]]
        while page["next_cursor"] is not None:
            page = self.store.list_actions(limit=3, cursor=page["next_cursor"])
            collected.extend(x["id"] for x in page["items"])
        self.assertEqual(list(reversed(ids)), collected)
        for limit in (0, -1, True, "5", 1.0, MAX_PAGE_SIZE + 1):
            self.assert_code("invalid_limit", self.store.list_actions, limit=limit)
        for cursor in (True, 1, "", "bad", "0", "-1", "1; DROP TABLE actions", "9" * 100):
            self.assert_code("invalid_cursor", self.store.list_actions, cursor=cursor)

    def test_reconciliation_covers_all_inflight_rows_not_history_page(self):
        records = []
        for i in range(105):
            action = self.claim(key=f"batch-{i}")
            if i % 3:
                action = self.store.complete(action["id"], "verifying")
            if i % 3 == 2:
                action = self.store.complete(action["id"], "measuring")
            records.append(action)
        irreversible = self.store.create("system.memory_trim", {"pid": 123}, {"created_ticks": 999})
        self.store.consent(irreversible["id"], irreversible["evidence_digest"], confirm=True)
        self.store.claim(irreversible["id"], "trim", irreversible["evidence_digest"], preflight={"passed": True})
        pending = self.create()
        approved = self.approve()
        reconciled = ActionStore(self.path, clock=lambda: self.now).reconcile_interrupted()
        self.assertEqual(106, len(reconciled))
        for record in records:
            current = self.store.get(record["id"])
            self.assertEqual("recovery_required", current["status"])
            self.assertIsNone(current["outcome"])
            self.assertIsNone(current["measurement"])
            self.assert_code("invalid_transition", self.store.claim,
                             current["id"], current["idempotency_key"], current["evidence_digest"])
        self.assertEqual("unknown", self.store.get(irreversible["id"])["status"])
        self.assertEqual("proposed", self.store.get(pending["id"])["status"])
        self.assertEqual("approved", self.store.get(approved["id"])["status"])
        self.assertEqual([], self.store.reconcile_interrupted())

    def test_completion_cannot_skip_claim_or_go_backwards_or_change_old_results(self):
        action = self.approve()
        self.assert_code("invalid_transition", self.store.complete, action["id"], "succeeded")
        self.store.claim(action["id"], "key", action["evidence_digest"], preflight={"passed": True})
        self.store.complete(action["id"], "measuring", outcome={"bytes": 123})
        self.assert_code("invalid_transition", self.store.complete, action["id"], "verifying")
        self.assert_code("immutable_result", self.store.complete,
                         action["id"], "succeeded", outcome={"bytes": 456})
        self.assert_code("invalid_json", self.store.complete,
                         action["id"], "succeeded", measurement={"bytes": float("nan")})
        self.assertEqual("measuring", self.store.get(action["id"])["status"])

    def test_not_found_and_strict_ids_digests_keys(self):
        missing = "0" * 32
        self.assertIsNone(self.store.get(missing))
        self.assert_code("not_found", self.store.events, missing)
        self.assert_code("not_found", self.store.consent, missing, "0" * 64, confirm=True)
        for value in (None, True, "../private", "", 123):
            self.assert_code("invalid_id", self.store.get, value)
        action = self.approve()
        for digest in (None, 1, "a" * 63, "A" * 64):
            self.assert_code("invalid_digest", self.store.claim, action["id"], "key", digest)
        self.assert_code("invalid_key", self.store.claim, action["id"], None, action["evidence_digest"])

    def test_wal_full_schema_and_atomic_event_failure(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual("wal", db.execute("PRAGMA journal_mode").fetchone()[0])
            self.assertEqual(SCHEMA_VERSION, db.execute("PRAGMA user_version").fetchone()[0])
            db.execute("CREATE TRIGGER reject_event BEFORE INSERT ON action_events "
                       "BEGIN SELECT RAISE(ABORT, 'private OS exception'); END")
        self.assert_code("storage_error", self.create)
        self.assertEqual([], self.store.list_actions()["items"])
        with self.store._transaction() as db:
            self.assertEqual(2, db.execute("PRAGMA synchronous").fetchone()[0])

    def test_future_version_and_missing_or_foreign_schema_fail_closed(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
        self.assert_code("unsupported_schema", ActionStore, self.path)
        self.assert_code("unsupported_schema", self.create)
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(SCHEMA_VERSION + 1, db.execute("PRAGMA user_version").fetchone()[0])
        foreign = Path(self.temp.name) / "foreign.db"
        with closing(sqlite3.connect(foreign)) as db, db:
            db.execute("CREATE TABLE legacy (payload TEXT)")
        self.assert_code("unsupported_schema", ActionStore, foreign)
        missing = Path(self.temp.name) / "missing" / "actions.db"
        self.assert_code("missing_store", ActionStore, missing, require_existing=True)
        self.assertFalse(missing.parent.exists())

    def test_schema_zero_only_migrates_an_empty_database_and_existing_schema_is_checked(self):
        empty = Path(self.temp.name) / "empty.db"
        with closing(sqlite3.connect(empty)):
            pass
        migrated = ActionStore(empty)
        self.assertEqual([], migrated.list_actions()["items"])
        self.assertEqual([], ActionStore(empty, require_existing=True).list_actions()["items"])
        with closing(sqlite3.connect(empty)) as db, db:
            db.execute("DROP TABLE action_events")
        self.assert_code("unsupported_schema", ActionStore, empty)
        self.assert_code("unsupported_schema", migrated.get, "0" * 32)

    def test_clock_requires_aware_datetime_and_normalizes_to_utc(self):
        store = ActionStore(self.path, clock=lambda: datetime(2026, 1, 1, 3,
                            tzinfo=timezone(timedelta(hours=3))))
        self.assertEqual("2026-01-01T00:00:00.000000+00:00",
                         store.create("file.restore", {}, {})["created_at"])
        for clock in (lambda: datetime(2026, 1, 1), lambda: "today"):
            invalid = ActionStore(self.path, clock=clock)
            self.assert_code("invalid_clock", invalid.create, "file.move", {}, {})

    def test_cutover_requires_initialized_store_and_missing_live_db_is_not_recreated(self):
        empty = Path(self.temp.name) / "uninitialized.db"
        with closing(sqlite3.connect(empty)):
            pass
        self.assert_code("unsupported_schema", ActionStore, empty, require_existing=True)
        self.path.unlink()
        self.assert_code("missing_store", self.create)
        self.assertFalse(self.path.exists())

    def test_runtime_durability_downgrade_fails_closed(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual("delete", db.execute("PRAGMA journal_mode=DELETE").fetchone()[0])
        self.assert_code("storage_error", self.create)

    def test_failed_claim_event_rolls_back_consumption_and_execution_key(self):
        action = self.approve()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TRIGGER reject_claim BEFORE INSERT ON action_events "
                       "WHEN NEW.type='claimed' BEGIN SELECT RAISE(ABORT, 'private error'); END")
        self.assert_code("storage_error", self.store.claim,
                         action["id"], "key", action["evidence_digest"], preflight={"passed": True})
        current = self.store.get(action["id"])
        self.assertEqual(action, current)
        self.assertIsNone(current["consent"]["used_at"])
        self.assertIsNone(current["idempotency_key"])
        self.assertEqual(2, len(self.store.events(action["id"])))
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("DROP TRIGGER reject_claim")
        self.assertEqual("executing", self.store.claim(
            action["id"], "key", action["evidence_digest"], preflight={"passed": True})["status"])

    def test_competing_actions_cannot_consume_same_global_execution_key(self):
        actions = [self.approve(), self.approve()]
        stores = [self.store, ActionStore(self.path, clock=lambda: self.now)]
        barrier = threading.Barrier(2)

        def claim(index):
            barrier.wait()
            action = actions[index]
            try:
                return stores[index].claim(action["id"], "shared", action["evidence_digest"],
                                           preflight={"passed": True})["status"]
            except ActionStoreError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertCountEqual(["executing", "idempotency_conflict"], list(executor.map(claim, range(2))))
        self.assertCountEqual(["executing", "approved"], [self.store.get(a["id"])["status"] for a in actions])

    def test_process_exit_leaves_committed_intent_and_restart_never_replays(self):
        child_path = Path(self.temp.name) / "child.db"
        program = (
            "import os, sys; from kshetrajna.action_store import ActionStore; "
            "s=ActionStore(sys.argv[1]); a=s.create('file.rename', {'path':'private'}, {}); "
            "s.consent(a['id'], a['evidence_digest'], confirm=True); "
            "s.claim(a['id'], 'before-exit', a['evidence_digest'], preflight={'passed':True}); os._exit(23)"
        )
        result = subprocess.run([sys.executable, "-B", "-c", program, str(child_path)],
                                capture_output=True, timeout=15, check=False)
        self.assertEqual(23, result.returncode, result.stderr.decode())
        reopened = ActionStore(child_path, require_existing=True)
        record = reopened.list_actions()["items"][0]
        self.assertEqual("executing", record["status"])
        self.assertEqual(3, len(reopened.events(record["id"])))
        self.assertEqual("recovery_required", reopened.reconcile_interrupted()[0]["status"])
        self.assert_code("invalid_transition", reopened.claim,
                         record["id"], "before-exit", record["evidence_digest"])

    def test_schema_marker_loss_and_bad_options_fail_closed(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("DELETE FROM action_meta")
        self.assert_code("unsupported_schema", ActionStore, self.path)
        self.assert_code("unsupported_schema", self.store.list_actions)
        self.assert_code("invalid_option", ActionStore, self.path, require_existing="false")

    def test_json_exact_bound_depth_unicode_and_integer_types(self):
        maximum = {"v": "x" * (MAX_JSON_BYTES - 8)}
        action = self.store.create("file.move", maximum, {"max": 2**256 - 1, "min": -(2**256 - 1)})
        self.assertEqual(maximum, action["target"])
        self.assertEqual(2**256 - 1, action["evidence"]["max"])
        invalid = [{"v": "x" * (MAX_JSON_BYTES - 7)}, {"é": "é" * (MAX_JSON_BYTES // 2)},
                   {"a": "a" * 40000, "b": "b" * 40000}, {"k" * 257: 1}]
        nested = {}
        for _ in range(14):
            nested = {"v": nested}
        invalid.append(nested)
        for value in invalid:
            self.assert_code("invalid_json", digest_evidence, value)
        self.assertNotEqual(digest_evidence({"n": True}), digest_evidence({"n": 1}))
        self.assertNotEqual(digest_evidence({"n": 1}), digest_evidence({"n": 1.0}))
        self.assertEqual(digest_evidence({"unicode": "é界"}),
                         self.store.create("file.rename", {}, {"unicode": "é界"})["evidence_digest"])

    def test_expired_creation_retry_never_refreshes_approval(self):
        action = self.create(idempotency_key="expiry", expires_seconds=1)
        self.now += timedelta(minutes=5)
        repeated = self.create(idempotency_key="expiry", expires_seconds=1)
        self.assertEqual(action["id"], repeated["id"])
        self.assertEqual(action["expires_at"], repeated["expires_at"])
        self.assertEqual("expired", repeated["status"])
        self.assert_code("expired", self.store.consent,
                         repeated["id"], repeated["evidence_digest"], confirm=True)
        self.assert_code("idempotency_conflict", self.create,
                         idempotency_key="expiry", expires_seconds=300)

    def test_complete_event_failure_does_not_record_success_or_results(self):
        action = self.claim()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TRIGGER reject_finish BEFORE INSERT ON action_events "
                       "WHEN NEW.type='succeeded' BEGIN SELECT RAISE(ABORT, 'C:/private OS error'); END")
        with self.assertRaises(ActionStoreError) as caught:
            self.store.complete(action["id"], "succeeded", measurement={"bytes": 100})
        self.assertEqual("storage_error", caught.exception.code)
        rendered = "".join(traceback.format_exception(caught.exception))
        self.assertNotIn("C:/private OS error", rendered)
        self.assertTrue(caught.exception.__suppress_context__)
        self.assertEqual(action, self.store.get(action["id"]))
        self.assertEqual("recovery_required", self.store.reconcile_interrupted()[0]["status"])

    def test_two_terminal_completions_have_one_winner_and_no_rewritten_event(self):
        action = self.claim()
        stores = [self.store, ActionStore(self.path, clock=lambda: self.now)]
        barrier = threading.Barrier(2)

        def complete(index):
            barrier.wait()
            try:
                return stores[index].complete(action["id"], "failed")["status"]
            except ActionStoreError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertCountEqual(["failed", "invalid_transition"], list(executor.map(complete, range(2))))
        events = self.store.events(action["id"])
        self.assertEqual([1, 2, 3, 4], [e["version"] for e in events])

    def test_public_projection_keeps_only_the_recorded_capability_metadata(self):
        action = self.create()
        action["capability"]["private_extra"] = self.target
        self.assertNotIn("private", json.dumps(public_action(action)))
        projected = public_action(action)
        projected["capability"]["risk"] = "changed"
        self.assertEqual("moderate", action["capability"]["risk"])

    def test_invalid_path_and_clock_options(self):
        for path in (None, 1, "", "invalid\x00path"):
            self.assert_code("invalid_path", ActionStore, path)
        self.assert_code("invalid_clock", ActionStore, self.path, clock="now")

    def test_two_stores_can_initialize_the_same_new_database(self):
        for attempt in range(10):
            path = Path(self.temp.name) / f"initialization-{attempt}.db"
            barrier = threading.Barrier(2)

            def initialize(_):
                barrier.wait()
                store = ActionStore(path)
                return store.create("file.move", {}, {}, idempotency_key="shared")["id"]

            with ThreadPoolExecutor(max_workers=2) as executor:
                ids = list(executor.map(initialize, range(2)))
            self.assertEqual(ids[0], ids[1])

    def test_claim_requires_nonempty_passed_bounded_preflight_and_persists_it(self):
        action = self.approve()
        args = (action["id"], "preflight", action["evidence_digest"])
        self.assert_code("preflight_required", self.store.claim, *args)
        self.assert_code("preflight_required", self.store.claim, *args, preflight={})
        for passed in (None, False, 0, 1, "true"):
            self.assert_code("preflight_failed", self.store.claim, *args, preflight={"passed": passed})
        self.assert_code("invalid_json", self.store.claim, *args,
                         preflight={"passed": True, "raw": "x" * MAX_JSON_BYTES})
        self.assertIsNone(self.store.get(action["id"])["preflight"])
        preflight = {"passed": True, "identity": 2**80, "path": "C:/private/preflight"}
        claimed = self.store.claim(*args, preflight=preflight)
        self.assertEqual(preflight, ActionStore(self.path).get(action["id"])["preflight"])
        self.assertTrue(public_action(claimed)["has_preflight"])
        self.assertNotIn("private", json.dumps(public_action(claimed)))

    def test_observed_expiry_is_durable_even_when_claim_errors_and_clock_goes_back(self):
        action = self.approve(expires_seconds=1)
        original = self.now
        self.now += timedelta(seconds=2)
        self.assert_code("expired", self.store.claim, action["id"], "expired", action["evidence_digest"])
        self.assertEqual("expired", self.store.get(action["id"])["status"])
        self.assertEqual("expired", self.store.events(action["id"])[-1]["type"])
        self.now = original
        reopened = ActionStore(self.path, clock=lambda: self.now)
        self.assert_code("clock_rollback", reopened.claim, action["id"], "expired", action["evidence_digest"])
        self.now += timedelta(seconds=3)
        self.assert_code("expired", reopened.claim, action["id"], "expired", action["evidence_digest"])

    def test_clock_rollback_fences_pending_approvals_across_reopen_and_catchup(self):
        action = self.approve()
        self.now += timedelta(seconds=20)
        self.assert_code("evidence_mismatch", self.store.claim, action["id"], "clock", "0" * 64)
        self.now -= timedelta(seconds=10)
        self.assert_code("clock_rollback", self.store.claim, action["id"], "clock", action["evidence_digest"])
        self.assertEqual("revoked", self.store.get(action["id"])["status"])
        self.assertEqual("clock_rollback", self.store.events(action["id"])[-1]["type"])
        self.now += timedelta(seconds=11)
        reopened = ActionStore(self.path, clock=lambda: self.now)
        self.assert_code("revoked", reopened.claim, action["id"], "clock", action["evidence_digest"])
        self.assert_code("revoked", reopened.consent, action["id"], action["evidence_digest"], confirm=True)

    def test_runtime_path_errors_are_normalized_and_do_not_leak_traceback_context(self):
        with patch.object(Path, "is_file", side_effect=PermissionError("C:/private/account")):
            with self.assertRaises(ActionStoreError) as caught:
                self.store.get("0" * 32)
        self.assertEqual("storage_error", caught.exception.code)
        self.assertNotIn("C:/private/account", "".join(traceback.format_exception(caught.exception)))

    def test_schema_rejects_matching_columns_with_wrong_structural_constraints(self):
        with closing(sqlite3.connect(self.path)) as db:
            statements = [row[0] for row in db.execute(
                "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL "
                "AND name NOT LIKE 'sqlite_%' ORDER BY type DESC, name")]
        changes = [
            ("version INTEGER NOT NULL", "version TEXT NOT NULL"),
            ("seq INTEGER PRIMARY KEY AUTOINCREMENT", "seq INTEGER"),
            ("id TEXT NOT NULL UNIQUE", "id TEXT NOT NULL"),
            (" REFERENCES actions(id)", ""),
            ("kind TEXT NOT NULL", "kind TEXT"),
            ("CHECK(mode IN ('live','demo'))", ""),
            ("('live','demo')", "('li ve','demo')"),
            ("idempotency_digest TEXT UNIQUE", "idempotency_digest TEXT"),
            (" REFERENCES actions(id)", " REFERENCES actions(id) ON DELETE CASCADE"),
        ]
        for index, (old, new) in enumerate(changes):
            with self.subTest(constraint=old):
                path = Path(self.temp.name) / f"malformed-{index}.db"
                self.assertTrue(any(old in statement for statement in statements))
                with closing(sqlite3.connect(path)) as db, db:
                    for statement in statements:
                        db.execute(statement.replace(old, new))
                    db.execute("INSERT INTO action_meta (id,format) VALUES (1,'kshetrajna.actions')")
                    db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                self.assert_code("unsupported_schema", ActionStore, path)

    def test_revoke_pending_is_scoped_durable_and_never_revokes_executing_recovery(self):
        proposed = self.create()
        approved = self.approve()
        executing = self.claim()
        other = self.store.create("system.model_unload", {}, {})
        changed = self.store.revoke_pending(kinds=["file.quarantine"])
        self.assertCountEqual([proposed["id"], approved["id"]], [a["id"] for a in changed])
        self.assertTrue(all(a["status"] == "revoked" for a in changed))
        reopened = ActionStore(self.path, clock=lambda: self.now)
        self.assertEqual("revoked", reopened.get(approved["id"])["status"])
        self.assert_code("revoked", reopened.claim, approved["id"], "reenabled", approved["evidence_digest"])
        self.assertEqual("revoked", reopened.events(approved["id"])[-1]["type"])
        self.assertEqual("executing", reopened.get(executing["id"])["status"])
        self.assertEqual([other["id"]], [a["id"] for a in reopened.revoke_pending()])
        self.assertEqual([], reopened.revoke_pending())
        for kinds in ([], "file.move", [True], ["shell"], ["file.move", "file.move"]):
            self.assert_code("invalid_scope", self.store.revoke_pending, kinds=kinds)

    def irreversible_completed(self, key="C:/private/request"):
        action = self.store.create("system.memory_trim", self.target, self.evidence, idempotency_key=key)
        self.store.consent(action["id"], action["evidence_digest"], confirm=True)
        self.store.claim(action["id"], key, action["evidence_digest"],
                         preflight={"passed": True, "path": "C:/private/preflight"})
        return self.store.complete(action["id"], "failed", outcome={"path": "C:/private/result"})

    def test_scoped_history_erasure_redacts_private_data_but_keeps_audit_and_replay_tombstone(self):
        action = self.irreversible_completed()
        result = self.store.erase_history([action["id"]], confirm=True)
        self.assertEqual([action["id"]], result["erased"])
        self.assertEqual([], result["preserved"])
        self.assertEqual("checkpointed", result["storage_cleanup"])
        reopened = ActionStore(self.path, clock=lambda: self.now)
        redacted = reopened.get(action["id"])
        for field in ("target", "evidence", "preflight", "outcome", "verification", "measurement",
                      "recovery", "idempotency_key"):
            self.assertIsNone(redacted[field])
        self.assertIsNotNone(redacted["erased_at"])
        self.assertEqual(action["evidence_digest"], redacted["evidence_digest"])
        self.assertEqual("history_erased", reopened.events(action["id"])[-1]["type"])
        repeated = reopened.create("system.memory_trim", self.target, self.evidence,
                                   idempotency_key="C:/private/request")
        self.assertEqual(redacted, repeated)
        other = self.approve()
        self.assert_code("idempotency_conflict", reopened.claim, other["id"],
                         "C:/private/request", other["evidence_digest"], preflight={"passed": True})
        self.assert_code("invalid_transition", reopened.claim, action["id"], "new", action["evidence_digest"])
        with closing(sqlite3.connect(self.path)) as db:
            stored = db.execute("SELECT * FROM actions WHERE id=?", (action["id"],)).fetchone()
            self.assertNotIn("C:/private", repr(stored))

    def test_erasure_preserves_unresolved_and_recoverable_work_and_is_explicit(self):
        unresolved = self.claim()
        completed = self.claim(key="recoverable-success")
        self.store.complete(completed["id"], "succeeded")
        self.store.reconcile_interrupted()
        pending = self.create()
        ids = [unresolved["id"], completed["id"], pending["id"]]
        before = [self.store.get(id) for id in ids]
        result = self.store.erase_history(ids, confirm=True)
        self.assertEqual([], result["erased"])
        self.assertCountEqual(ids, [item["id"] for item in result["preserved"]])
        self.assertEqual(before, [self.store.get(id) for id in ids])
        for confirm in (False, None, 1, "true"):
            self.assert_code("confirmation_required", self.store.erase_history, ids, confirm=confirm)
        for scope in (None, [], "all", [True], [pending["id"]] * 2, ["0" * 32] * 101):
            self.assert_code("invalid_scope", self.store.erase_history, scope, confirm=True)

    def test_erasure_failure_rolls_back_private_payload_removal_and_event(self):
        action = self.irreversible_completed()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TRIGGER reject_erase BEFORE INSERT ON action_events "
                       "WHEN NEW.type='history_erased' BEGIN SELECT RAISE(ABORT,'private'); END")
        self.assert_code("storage_error", self.store.erase_history, [action["id"]], confirm=True)
        self.assertEqual(action, self.store.get(action["id"]))

    def test_erasure_reports_pending_wal_cleanup_with_existing_reader(self):
        action = self.irreversible_completed()
        with closing(sqlite3.connect(self.path)) as reader:
            reader.execute("BEGIN")
            reader.execute("SELECT target_json FROM actions").fetchall()
            result = self.store.erase_history([action["id"]], confirm=True)
            self.assertEqual("pending", result["storage_cleanup"])
            reader.rollback()
        retried = self.store.erase_history([action["id"]], confirm=True)
        self.assertEqual([], retried["erased"])
        self.assertEqual("already_erased", retried["preserved"][0]["reason"])
        self.assertEqual("checkpointed", retried["storage_cleanup"])

    def test_priority_trials_are_expert_capabilities(self):
        self.assertEqual("expert", get_capability("system.priority_trial").visibility)

    def legacy_fixture(self, name="legacy-v1.db"):
        path = Path(self.temp.name) / name
        with self.store._transaction() as db:
            actions = [dict(row) for row in db.execute("SELECT * FROM actions ORDER BY seq")]
            events = [dict(row) for row in db.execute("SELECT * FROM action_events ORDER BY seq")]
        with closing(sqlite3.connect(path)) as db, db:
            for statement in ledger._SCHEMA_V1:
                db.execute(statement)
            db.execute("INSERT INTO action_meta VALUES (1,'kshetrajna.actions')")
            for table, rows in (("actions", actions), ("action_events", events)):
                columns = [row[1] for row in db.execute(f"PRAGMA table_info({table})")]
                for row in rows:
                    if table == "actions":
                        row["preflight_json"] = None
                    values = [row[column] for column in columns]
                    db.execute(f"INSERT INTO {table} ({','.join(columns)}) VALUES "
                               f"({','.join('?' for _ in columns)})", values)
            db.execute("PRAGMA user_version=1")
        return path

    def test_v1_migration_preserves_ledger_and_keys_but_invalidates_old_approvals(self):
        approved = self.approve(idempotency_key="legacy-approval")
        inflight = self.claim(key="legacy-claim")
        finished = self.irreversible_completed(key="legacy-finished")
        path = self.legacy_fixture()
        migrated = ActionStore(path, clock=lambda: self.now, require_existing=True)
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(SCHEMA_VERSION, db.execute("PRAGMA user_version").fetchone()[0])
        old = migrated.get(approved["id"])
        self.assertEqual("revoked", old["status"])
        self.assertEqual(self.target, old["target"])
        self.assertEqual("schema_migrated", migrated.events(old["id"])[-1]["type"])
        self.assertEqual("executing", migrated.get(inflight["id"])["status"])
        self.assertIsNone(migrated.get(inflight["id"])["preflight"])
        self.assertEqual({**finished, "preflight": None}, migrated.get(finished["id"]))
        self.assertEqual(old, migrated.create("file.quarantine", self.target, self.evidence,
                                             idempotency_key="legacy-approval"))
        self.assertEqual("recovery_required", migrated.reconcile_interrupted()[0]["status"])
        self.assertEqual([], ActionStore(path, clock=lambda: self.now).reconcile_interrupted())

    def test_v1_migration_failure_is_atomic_and_retry_is_safe(self):
        action = self.approve()
        path = self.legacy_fixture()
        with patch.object(ActionStore, "_event", side_effect=sqlite3.OperationalError("private migration error")):
            self.assert_code("storage_error", ActionStore, path, clock=lambda: self.now)
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(1, db.execute("PRAGMA user_version").fetchone()[0])
            self.assertEqual("approved", db.execute("SELECT status FROM actions").fetchone()[0])
            self.assertEqual(2, db.execute("SELECT COUNT(*) FROM action_events").fetchone()[0])
        migrated = ActionStore(path, clock=lambda: self.now)
        self.assertEqual("revoked", migrated.get(action["id"])["status"])

    def test_revoke_and_claim_race_cannot_leave_a_revoked_action_executable(self):
        action = self.approve()
        other = ActionStore(self.path, clock=lambda: self.now)
        barrier = threading.Barrier(2)

        def claim():
            barrier.wait()
            try:
                return self.store.claim(action["id"], "race-revoke", action["evidence_digest"],
                                        preflight={"passed": True})["status"]
            except ActionStoreError as error:
                return error.code

        def revoke():
            barrier.wait()
            return other.revoke_pending()

        with ThreadPoolExecutor(max_workers=2) as executor:
            claimed = executor.submit(claim)
            revoked = executor.submit(revoke)
            status, rows = claimed.result(), revoked.result()
        if status == "executing":
            self.assertEqual([], rows)
        else:
            self.assertEqual("revoked", status)
            self.assertEqual([action["id"]], [row["id"] for row in rows])
        self.assertEqual(status, self.store.get(action["id"])["status"])

    def test_recovery_finalization_and_reconciliation_remain_available_on_clock_rollback(self):
        one = self.claim()
        two = self.claim(key="other-inflight")
        self.now -= timedelta(seconds=1)
        self.assertEqual("failed", self.store.complete(one["id"], "failed")["status"])
        self.assertEqual([two["id"]], [row["id"] for row in self.store.reconcile_interrupted()])
        self.assert_code("clock_rollback", self.create)

    def test_bulk_revocation_is_not_limited_by_display_pagination_and_records_an_event(self):
        ids = [self.create()["id"] for _ in range(105)]
        revoked = self.store.revoke_pending()
        self.assertCountEqual(ids, [row["id"] for row in revoked])
        self.assertTrue(all(self.store.events(id)[-1]["type"] == "revoked" for id in ids))

    def test_revocation_event_failure_rolls_back_the_entire_scope(self):
        one, two = self.approve(), self.approve()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TRIGGER reject_revoke BEFORE INSERT ON action_events "
                       "WHEN NEW.type='revoked' BEGIN SELECT RAISE(ABORT,'private'); END")
        self.assert_code("storage_error", self.store.revoke_pending)
        self.assertEqual(one, self.store.get(one["id"]))
        self.assertEqual(two, self.store.get(two["id"]))

    def test_erasure_unknown_id_is_atomic_and_revoked_unclaimed_history_can_be_redacted(self):
        action = self.create(idempotency_key="revoked-private")
        self.store.revoke_pending()
        before = self.store.get(action["id"])
        self.assert_code("not_found", self.store.erase_history, [action["id"], "0" * 32], confirm=True)
        self.assertEqual(before, self.store.get(action["id"]))
        result = self.store.erase_history([action["id"]], confirm=True)
        self.assertEqual([action["id"]], result["erased"])
        self.assert_code("revoked", self.store.claim, action["id"], "new", action["evidence_digest"],
                         preflight={"passed": True})

    def test_runtime_connection_cleanup_errors_are_also_normalized(self):
        connection = Mock()
        connection.in_transaction = True
        connection.rollback.side_effect = OSError("C:/private/rollback")
        with patch.object(ledger.sqlite3, "connect", return_value=connection):
            with self.assertRaises(ActionStoreError) as caught:
                with self.store._connection():
                    pass
        self.assertEqual("storage_error", caught.exception.code)
        self.assertNotIn("C:/private/rollback", "".join(traceback.format_exception(caught.exception)))
        connection.close.assert_called_once()

    def test_unknown_irreversible_history_is_preserved_for_manual_investigation(self):
        action = self.store.create("system.memory_trim", self.target, self.evidence)
        self.store.consent(action["id"], action["evidence_digest"], confirm=True)
        self.store.claim(action["id"], "unknown", action["evidence_digest"], preflight={"passed": True})
        action = self.store.complete(action["id"], "unknown", outcome={"private": "manual investigation"})
        result = self.store.erase_history([action["id"]], confirm=True)
        self.assertEqual([], result["erased"])
        self.assertEqual([{"id": action["id"], "reason": "unresolved"}], result["preserved"])
        self.assertEqual(action, self.store.get(action["id"]))


if __name__ == "__main__":
    unittest.main()
