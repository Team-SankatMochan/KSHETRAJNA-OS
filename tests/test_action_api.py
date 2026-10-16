"""Real loopback API checks; synthetic telemetry and recording drivers only."""

import http.client
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from kshetrajna.action_store import ActionStore, ActionStoreError
from kshetrajna.collector import Collector
from kshetrajna.config import ConfigStore
from kshetrajna.demo import DemoProbe
from kshetrajna.models import ProcessSample, Snapshot
from kshetrajna.server import DashboardServer
from kshetrajna.storage import EventStore


class RecordingDriver:
    def __init__(self, result, observer):
        self.result, self.observer = result, observer
        self.calls = []

    def execute(self, plan):
        self.observer(plan)
        self.calls.append(json.loads(json.dumps(plan)))
        return self.result


class ActionApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.config = ConfigStore(self.directory)
        self.config.update({"enabled": True, "device_consent": True,
                            "allow_maintenance": True, "allow_process_control": True})
        self.events = EventStore(self.directory / "events.db")
        self.events.record(Snapshot(datetime.now(timezone.utc).isoformat(), 12, 16 * 1024**3,
            8 * 1024**3, 90002, "synthetic-foreground.exe", 0,
            [ProcessSample(90001, "chrome.exe", 0, 500 * 1024**2, 123)]), [])
        self.collector = Collector(DemoProbe(), self.config, self.events, demo=False)
        self.server = DashboardServer(0, self.collector, self.config, self.events)
        self.install_drivers()
        self.start_server()
        self.addCleanup(self.stop_server)

    def install_drivers(self):
        def intent_precedes_mutation(plan):
            records = self.server.action_store.list_actions()["items"]
            claims = [self.server.action_store.get(record["id"]) for record in records
                      if record["status"] == "executing"]
            self.assertTrue(any(record["target"]["plan_id"] == plan["id"] and
                                record["consent"]["used_at"] is not None and
                                record["preflight"]["scope"] == "adapter_binding_only" for record in claims))
        self.maintenance_driver = RecordingDriver(
            {"status": "completed", "before_bytes": None, "after_bytes": None,
             "resident_reduction_mb": None, "message": "synthetic-private-path-C:\\private"},
            intent_precedes_mutation)
        self.process_driver = RecordingDriver({"status": "terminated", "message": "synthetic-private-owner"},
                                              intent_precedes_mutation)
        self.server.service.maintenance.driver = self.maintenance_driver
        self.server.chat.processes.driver = self.process_driver

    def start_server(self):
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop_server(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()

    def request(self, method, path, body=None, *, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        request_headers = {"X-Kshetrajna-Token": self.server.token,
                           "Origin": f"http://127.0.0.1:{self.server.server_port}",
                           "Content-Type": "application/json"}
        request_headers.update(headers or {})
        connection.request(method, path, None if body is None else json.dumps(body), request_headers)
        response = connection.getresponse()
        data = json.loads(response.read())
        connection.close()
        return response.status, data

    def test_capabilities_are_explicit_about_wiring(self):
        status, data = self.request("GET", "/api/v1/capabilities")
        self.assertEqual(status, 200, data)
        capabilities = {item["kind"]: item for item in data["items"]}
        self.assertEqual(len(capabilities), 12)
        self.assertEqual(capabilities["system.memory_trim"]["api_state"], "wired")
        self.assertEqual(capabilities["file.move"]["api_state"], "deferred")
        self.assertEqual(capabilities["system.temp_cleanup"]["api_state"], "deferred")
        self.assertFalse(data["canonical_authority_complete"])

    def proposal(self, kind="system.memory_trim", **extra):
        selector = {"tool": "storage_settings"} if kind == "system.external_tool" else {"pid": 90001}
        status, data = self.request("POST", "/api/v1/actions", {"kind": kind, **selector, **extra})
        self.assertEqual(status, 201, data)
        return data["action"]

    def consent(self, action):
        status, data = self.request("POST", f'/api/v1/actions/{action["id"]}/consent',
                                    {"confirm": True, "evidence_digest": action["evidence_digest"]})
        self.assertEqual(status, 200, data)
        return data["action"]

    def execute(self, action, **extra):
        return self.request("POST", f'/api/v1/actions/{action["id"]}/execute', {
            "confirm": True, "evidence_digest": action["evidence_digest"],
            "idempotency_key": f'execute-{action["id"]}', **extra})

    def test_memory_trim_persists_intent_and_honest_nullable_result(self):
        action = self.proposal()
        approved = self.consent(action)
        self.assertEqual(approved["status"], "approved")
        status, data = self.execute(action)
        self.assertEqual(status, 200, data)
        self.assertEqual(data["action"]["status"], "succeeded")
        record = self.server.action_store.get(action["id"])
        self.assertIsNone(record["outcome"]["result"]["before_bytes"])
        self.assertEqual(len(self.maintenance_driver.calls), 1)
        self.assertEqual(self.events.decisions()[0]["id"], record["target"]["plan_id"])
        status, events = self.request("GET", f'/api/v1/actions/{action["id"]}/events')
        self.assertEqual(status, 200)
        self.assertEqual([event["type"] for event in events["items"]],
                         ["created", "consented", "claimed", "succeeded"])
        self.assertNotIn("synthetic-private", json.dumps(data) + json.dumps(events))

    def test_missing_false_and_nonboolean_confirm_cannot_approve_or_execute(self):
        action = self.proposal()
        for operation in ("consent", "execute"):
            if operation == "execute":
                self.consent(action)
            for extra in ({}, *({"confirm": value} for value in (False, None, 0, 1, "true", [], {}))):
                with self.subTest(operation=operation, extra=extra):
                    payload = {"evidence_digest": action["evidence_digest"], **extra}
                    if operation == "execute":
                        payload["idempotency_key"] = "one-use"
                    status, data = self.request("POST", f'/api/v1/actions/{action["id"]}/{operation}', payload)
                    self.assertEqual((status, data), (400, {"error": "confirmation_required"}))
        self.assertEqual(self.maintenance_driver.calls, [])

    def test_digest_mismatch_and_replay_are_rejected(self):
        action = self.proposal()
        status, data = self.request("POST", f'/api/v1/actions/{action["id"]}/consent',
                                    {"confirm": True, "evidence_digest": "0" * 64})
        self.assertEqual((status, data), (409, {"error": "evidence_mismatch"}))
        self.consent(action)
        self.assertEqual(self.execute(action, evidence_digest="0" * 64),
                         (409, {"error": "evidence_mismatch"}))
        self.assertEqual(self.execute(action)[0], 200)
        self.assertEqual(self.execute(action), (409, {"error": "invalid_transition"}))
        self.assertEqual(len(self.maintenance_driver.calls), 1)

    def test_concurrent_execute_claims_once(self):
        action = self.proposal()
        self.consent(action)
        barrier = threading.Barrier(2)
        def attempt(_):
            barrier.wait(timeout=5)
            return self.execute(action)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, range(2)))
        self.assertEqual(sorted(result[0] for result in results), [200, 409], results)
        self.assertEqual(len(self.maintenance_driver.calls), 1)

    def test_execution_key_cannot_be_rebound_to_another_action(self):
        first = self.proposal()
        self.consent(first)
        self.assertEqual(self.execute(first, idempotency_key="global-execution-key")[0], 200)
        second = self.proposal("system.process_close")
        self.consent(second)
        self.assertEqual(self.execute(second, idempotency_key="global-execution-key"),
                         (409, {"error": "idempotency_conflict"}))
        self.assertEqual(self.process_driver.calls, [])

    def test_process_terminate_requires_exact_name_and_close_is_not_verified_exit(self):
        terminate = self.proposal("system.process_terminate")
        self.consent(terminate)
        self.assertEqual(self.execute(terminate), (400, {"error": "typed_name_mismatch"}))
        self.assertEqual(self.execute(terminate, typed_name="Chrome.exe"), (400, {"error": "typed_name_mismatch"}))
        typed_confirmation = terminate["review"]["typed_confirmation"]
        self.assertEqual(typed_confirmation, {"field": "typed_name", "exact_value": "chrome.exe"})
        status, result = self.execute(terminate, **{typed_confirmation["field"]: typed_confirmation["exact_value"]})
        self.assertEqual((status, result["action"]["status"]), (200, "succeeded"))
        close = self.proposal("system.process_close")
        self.consent(close)
        self.process_driver.result = {"status": "close_requested"}
        status, result = self.execute(close)
        self.assertEqual((status, result["action"]["status"]), (200, "unknown"))

    def test_external_launch_and_unconfirmed_termination_are_unknown(self):
        tool = self.proposal("system.external_tool")
        self.consent(tool)
        self.maintenance_driver.result = {"status": "launched"}
        self.assertEqual(self.execute(tool)[1]["action"]["status"], "unknown")
        process = self.proposal("system.process_terminate")
        self.consent(process)
        self.process_driver.result = {"status": "termination_requested"}
        self.assertEqual(self.execute(process, typed_name="chrome.exe")[1]["action"]["status"], "unknown")

    def test_all_fixed_external_tools_are_mapped_without_client_paths(self):
        for tool in ("disk_cleanup", "storage_settings", "open_temp", "store_cache"):
            action = self.proposal("system.external_tool", tool=tool)
            self.assertEqual(self.server.action_store.get(action["id"])["evidence"]["plan"]["action"], tool)
        for tool in ("temp_cleanup", "C:\\private.exe", [], None):
            self.assertEqual(self.request("POST", "/api/v1/actions", {
                "kind": "system.external_tool", "tool": tool})[0], 400)

    def test_review_round_trips_every_wired_kind(self):
        selections = [(kind, {}) for kind in ("system.memory_trim", "system.process_close",
                                               "system.process_terminate")]
        selections += [("system.external_tool", {"tool": tool}) for tool in
                       ("disk_cleanup", "storage_settings", "open_temp", "store_cache")]
        for kind, selector in selections:
            with self.subTest(kind=kind, selector=selector):
                action = self.proposal(kind, **selector)
                review = action.get("review")
                self.assertIsNotNone(review, "Canonical proposals must be independently reviewable")
                self.assertTrue(review["available"])
                status, detail = self.request("GET", f'/api/v1/actions/{action["id"]}')
                self.assertEqual(status, 200)
                self.assertEqual(detail["action"]["review"], review)
                persisted = self.server.action_store.get(action["id"])
                for field in ("title", "effect", "undo"):
                    self.assertEqual(review[field], persisted["evidence"]["plan"][field])
                self.assertTrue(review["caveats"])
                self.assertEqual(review["freshness"]["proposed_at"], action["created_at"])
                self.assertEqual(review["freshness"]["expires_at"], action["expires_at"])
                if kind == "system.external_tool":
                    self.assertEqual(review["tool"], selector["tool"])
                    self.assertIsNone(review["process"])
                    self.assertIsNone(review["freshness"]["observed_at"])
                else:
                    self.assertEqual(review["process"], {"name": "chrome.exe", "pid": 90001, "created_ticks": 123})
                    self.assertEqual(review["observations"]["working_set_bytes"], 500 * 1024**2)
                    self.assertEqual(review["observations"]["cpu_percent"], 0)
                    self.assertEqual(review["freshness"]["observed_at"], self.events.latest()["observed_at"])
                self.assertLess(len(json.dumps(review)), 4096)
                for private in ("consent_versions", "consent_version", "permissions", "plan_id", "offer_id",
                                "adapter", "target", "driver", "synthetic-private", "C:\\\\"):
                    self.assertNotIn(private, json.dumps(review))

    def test_review_uses_persisted_snapshot_not_changed_live_plan_or_latest_sample(self):
        action = self.proposal("system.process_close")
        record = self.server.action_store.get(action["id"])
        self.server.chat.processes.plans[record["target"]["plan_id"]]["target"]["pid"] = 999
        self.events.clear()
        status, detail = self.request("GET", f'/api/v1/actions/{action["id"]}')
        self.assertEqual(status, 200)
        self.assertEqual(detail["action"]["review"], action["review"])
        self.assertEqual(self.request("POST", f'/api/v1/actions/{action["id"]}/consent', {
            "confirm": True, "evidence_digest": action["evidence_digest"]}), (409, {"error": "plan_changed"}))

    def test_review_does_not_copy_extra_private_plan_identity_or_result_fields(self):
        for kind in ("system.memory_trim", "system.process_close", "system.process_terminate", "system.external_tool"):
            with self.subTest(kind=kind):
                action = self.proposal(kind)
                original = self.server.action_store.get(action["id"])
                evidence = original["evidence"]
                plan = evidence["plan"]
                plan["driver_payload"] = {"path": "C:\\private\\secret-token", "result": "synthetic-private"}
                if "target" in plan:
                    plan["target"].update(path="C:\\private\\app.exe", reason="synthetic-private")
                    original["target"]["identity"] = plan["target"]
                stored = self.server.action_store.create(kind, original["target"], evidence)
                status, detail = self.request("GET", f'/api/v1/actions/{stored["id"]}')
                self.assertEqual(status, 200)
                # Proposal creation times differ, but the recorded facts and copy do not.
                review = detail["action"]["review"]
                self.assertTrue(review["available"])
                self.assertEqual(review["process"], action["review"]["process"])
                self.assertEqual(review["observations"], action["review"]["observations"])
                for private in ("driver_payload", "synthetic-private", "private", "secret-token", '"path"', '"result"'):
                    self.assertNotIn(private, json.dumps(detail))

    def test_erased_review_is_unavailable_without_old_evidence(self):
        action = self.proposal()
        self.consent(action)
        self.execute(action)
        status, erased = self.request("POST", "/api/v1/actions/erase-history", {
            "confirm": True, "ids": [action["id"]]})
        self.assertEqual(status, 200)
        self.assertEqual(erased["erased"], [action["id"]])
        status, detail = self.request("GET", f'/api/v1/actions/{action["id"]}')
        self.assertEqual(status, 200)
        self.assertEqual(detail["action"]["review"], {"available": False})
        for private in ("chrome.exe", "90001", "created_ticks", "Trim resident", "synthetic-private"):
            self.assertNotIn(private, json.dumps(detail))

    def test_deferred_legacy_and_malformed_evidence_never_becomes_review_or_execution(self):
        action = self.proposal()
        original = self.server.action_store.get(action["id"])
        cases = []
        for kind, evidence in (("file.move", original["evidence"]),
                               ("system.memory_trim", {}),
                               ("system.memory_trim", {"adapter": "maintenance_process_v1", "plan": {}})):
            cases.append(self.server.action_store.create(kind, original["target"], evidence))
        for field, value in (("title", "C:\\private\\secret"), ("effect", {"secret": "private"}),
                             ("undo", "x" * 5000), ("action", "temp_cleanup"), ("target", []),
                             ("expires_at", "C:\\private")):
            evidence = json.loads(json.dumps(original["evidence"]))
            evidence["plan"][field] = value
            cases.append(self.server.action_store.create("system.memory_trim", original["target"], evidence))
        for field, value in (("plan", []), ("permissions", []), ("observed_at", {"secret": "private"}),
                             ("mode", "demo")):
            evidence = json.loads(json.dumps(original["evidence"]))
            evidence[field] = value
            cases.append(self.server.action_store.create("system.memory_trim", original["target"], evidence))
        for record in cases:
            with self.subTest(id=record["id"]):
                status, detail = self.request("GET", f'/api/v1/actions/{record["id"]}')
                self.assertEqual(status, 200)
                self.assertEqual(detail["action"]["review"], {"available": False})
                self.assertNotIn("private", json.dumps(detail))
                status, _ = self.request("POST", f'/api/v1/actions/{record["id"]}/consent', {
                    "confirm": True, "evidence_digest": record["evidence_digest"]})
                self.assertEqual(status, 409)
        self.assertEqual(self.maintenance_driver.calls + self.process_driver.calls, [])

    def test_revocation_and_reenable_persistently_invalidate_pending_approval(self):
        for kind, key in (("system.memory_trim", "allow_maintenance"),
                          ("system.process_close", "allow_process_control"),
                          ("system.memory_trim", "track_processes"),
                          ("system.process_close", "track_foreground"),
                          ("system.memory_trim", "enabled"),
                          ("system.process_close", "device_consent")):
            with self.subTest(kind=kind, key=key):
                self.config.update({"enabled": True, "device_consent": True})
                action = self.proposal(kind)
                self.consent(action)
                self.assertEqual(self.request("POST", "/api/settings", {key: False})[0], 200)
                self.assertEqual(self.request("POST", "/api/settings", {key: True})[0], 200)
                self.assertEqual(self.execute(action), (409, {"error": "revoked"}))
        self.assertEqual(self.maintenance_driver.calls + self.process_driver.calls, [])

    def test_other_config_store_disable_reenable_cannot_resurrect_plan(self):
        for key in ("enabled", "device_consent", "track_processes", "track_foreground", "allow_maintenance"):
            action = self.proposal()
            self.consent(action)
            other = ConfigStore(self.directory)
            other.update({key: False})
            other.update({key: True})
            self.assertEqual(self.execute(action), (409, {"error": "stale_approval"}))
        self.assertEqual(self.maintenance_driver.calls, [])

    def test_stale_changed_and_missing_plans_fail_closed(self):
        action = self.proposal()
        self.consent(action)
        record = self.server.action_store.get(action["id"])
        plan = self.server.service.maintenance.plans[record["target"]["plan_id"]]
        plan["effect"] = "changed after review"
        self.assertEqual(self.execute(action), (409, {"error": "plan_changed"}))
        self.server.service.maintenance.plans.clear()
        self.assertEqual(self.execute(action), (409, {"error": "plan_unavailable"}))
        other = self.proposal()
        self.consent(other)
        maintenance = self.server.service.maintenance
        with patch.object(maintenance, "clock", return_value=maintenance.clock() + 61):
            self.assertEqual(self.execute(other), (409, {"error": "plan_unavailable"}))
        self.assertEqual(self.maintenance_driver.calls, [])

    def test_domain_fresh_identity_preflight_remains_authoritative(self):
        action = self.proposal("system.process_close")
        self.consent(action)
        self.events.record(Snapshot(datetime.now(timezone.utc).isoformat(), 12, 16 * 1024**3,
            8 * 1024**3, 90002, "synthetic-foreground.exe", 0,
            [ProcessSample(90001, "chrome.exe", 0, 500 * 1024**2, 999)]), [])
        status, data = self.execute(action)
        self.assertEqual((status, data["action"]["status"]), (200, "unknown"))
        self.assertEqual(self.process_driver.calls, [])
        self.assertIsNotNone(self.server.action_store.get(action["id"])["consent"]["used_at"])
        self.assertEqual(self.execute(action)[0], 409)

    def test_mode_mismatch_fails_closed(self):
        action = self.proposal()
        self.consent(action)
        self.server.service.maintenance.demo = True
        self.assertEqual(self.execute(action), (409, {"error": "mode_mismatch"}))
        self.assertEqual(self.maintenance_driver.calls, [])

    def test_durable_expiry_fails_closed_even_if_domain_clock_is_frozen(self):
        action = self.proposal()
        self.consent(action)
        ledger = self.server.action_store
        future = datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() + 61, timezone.utc)
        with patch.object(ledger, "_clock", return_value=future):
            self.assertEqual(self.execute(action), (409, {"error": "expired"}))
        self.assertEqual(ledger.get(action["id"])["status"], "expired")
        self.assertEqual(self.maintenance_driver.calls, [])

    def test_missing_store_and_failed_finalization_do_not_allow_replay(self):
        action = self.proposal()
        self.consent(action)
        ledger = self.server.action_store
        with patch.object(ledger, "claim", side_effect=ActionStoreError("missing_store")):
            self.assertEqual(self.execute(action), (503, {"error": "missing_store"}))
        self.assertEqual(self.maintenance_driver.calls, [])
        with patch.object(ledger, "complete", side_effect=ActionStoreError("storage_error")):
            self.assertEqual(self.execute(action), (503, {"error": "storage_error"}))
        self.assertEqual(ledger.get(action["id"])["status"], "executing")
        self.assertEqual(self.execute(action), (409, {"error": "invalid_transition"}))
        self.assertEqual(len(self.maintenance_driver.calls), 1)

    def test_domain_failure_never_reports_success_or_leaks_private_os_error(self):
        action = self.proposal()
        self.consent(action)
        self.maintenance_driver.execute = Mock(side_effect=OSError("C:\\private\\secret-token"))
        status, data = self.execute(action)
        self.assertEqual((status, data["action"]["status"]), (200, "unknown"))
        self.assertNotIn("private", json.dumps(data))
        self.assertEqual(self.execute(action)[0], 409)

    def test_restart_fences_pending_and_interrupted_intent_without_replay(self):
        approved = self.proposal()
        self.consent(approved)
        interrupted = self.proposal("system.process_close")
        self.consent(interrupted)
        self.server.action_store.claim(interrupted["id"], "crash-intent", interrupted["evidence_digest"],
                                       preflight={"passed": True})
        self.stop_server()
        self.server = DashboardServer(0, self.collector, ConfigStore(self.directory), self.events,
                                      action_store=ActionStore(self.directory / "actions.db", require_existing=True))
        self.install_drivers()
        self.start_server()
        self.assertEqual(self.execute(approved), (409, {"error": "revoked"}))
        status, data = self.request("GET", f'/api/v1/actions/{interrupted["id"]}')
        self.assertEqual((status, data["action"]["status"]), (200, "unknown"))
        self.assertEqual(self.execute(interrupted)[0], 409)
        self.assertEqual(self.maintenance_driver.calls + self.process_driver.calls, [])

    def test_list_and_events_are_paginated_and_allowlist_redacted(self):
        actions = [self.proposal() for _ in range(3)]
        self.consent(actions[-1])
        self.execute(actions[-1])
        status, first = self.request("GET", "/api/v1/actions?limit=2")
        self.assertEqual(status, 200)
        self.proposal("system.process_close")
        status, second = self.request("GET", f'/api/v1/actions?limit=2&cursor={first["next_cursor"]}')
        self.assertEqual(status, 200)
        self.assertEqual([item["id"] for item in first["items"] + second["items"]],
                         [action["id"] for action in reversed(actions)])
        self.assertIsNone(second["next_cursor"])
        action_id = actions[-1]["id"]
        status, events = self.request("GET", f"/api/v1/actions/{action_id}/events?limit=2")
        self.assertEqual(status, 200)
        status, later = self.request("GET", f'/api/v1/actions/{action_id}/events?limit=2&cursor={events["next_cursor"]}')
        self.assertEqual(status, 200)
        self.assertEqual([event["version"] for event in events["items"] + later["items"]], [1, 2, 3, 4])
        status, detail = self.request("GET", f"/api/v1/actions/{action_id}")
        self.assertEqual(status, 200)
        display_history = json.dumps([first, second, events, later])
        for private in ("chrome.exe", "90001", "created_ticks", '"review"'):
            self.assertNotIn(private, display_history)
        self.assertEqual(detail["action"]["review"]["process"],
                         {"name": "chrome.exe", "pid": 90001, "created_ticks": 123})
        public = json.dumps([first, second, events, later, detail])
        for private in ("consent_versions", '"target"', '"identity"', '"permissions"', "offer_id",
                         "synthetic-private", "adapter_binding_only", "plan_id", "idempotency_key"):
            self.assertNotIn(private, public)

    def test_auth_host_origin_and_unknown_routes(self):
        for method, path, body, headers, error in (
            ("GET", "/api/v1/capabilities", None, {"X-Kshetrajna-Token": ""}, "authentication_required"),
            ("GET", "/api/v1/actions", None, {"Origin": "https://evil.invalid"}, "origin_rejected"),
            ("POST", "/api/v1/actions", {}, {"Origin": ""}, "origin_rejected"),
            ("GET", "/api/v1/actions", None, {"Host": "evil.invalid"}, "host_rejected")):
            self.assertEqual(self.request(method, path, body, headers=headers), (403, {"error": error}))
        for method in ("GET", "POST"):
            for path in ("/api/v1", "/api/v1/unknown", "/api/v1/actions/x/unknown"):
                self.assertEqual(self.request(method, path, {} if method == "POST" else None),
                                 (404, {"error": "not_found"}))
        self.assertEqual(self.request("GET", "/api/v1/actions/" + "a" * 32), (404, {"error": "not_found"}))
        self.assertEqual(self.request("GET", "/api/v1/actions/capabilities"), (400, {"error": "invalid_id"}))
        self.assertEqual(self.request("PUT", "/api/v1/actions", {}), (405, {"error": "method_not_allowed"}))

    def test_request_validation_rejects_untrusted_evidence_and_pagination(self):
        for extra in ({"target": {"pid": 1}}, {"evidence": {}}, {"preflight": {"passed": True}},
                      {"mode": "demo"}, {"expires_seconds": 999}, {"pid": True}):
            self.assertEqual(self.request("POST", "/api/v1/actions", {
                "kind": "system.memory_trim", "pid": 90001, **extra})[0], 400)
        for body in ({"kind": "file.move", "pid": 90001}, {"kind": "system.temp_cleanup"}):
            self.assertEqual(self.request("POST", "/api/v1/actions", body),
                             (409, {"error": "capability_deferred"}))
        for query in ("limit=0", "limit=101", "limit=true", "limit=1&limit=2", "cursor=0",
                      "cursor=01", "cursor=abc", "extra=1", "limit=2&cursor=3&extra=1"):
            self.assertEqual(self.request("GET", "/api/v1/actions?" + query),
                             (400, {"error": "invalid_pagination"}))
        self.assertEqual(self.request("POST", "/api/v1/actions", {}, headers={"Content-Type": "text/plain"}),
                         (415, {"error": "json_required"}))

    def test_invalid_json_duplicate_keys_and_framing_have_stable_errors(self):
        for raw in ('[]', '{"kind":NaN}', '{"kind":"x","kind":"system.memory_trim"}', '{', '"string"'):
            connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
            connection.request("POST", "/api/v1/actions", raw, {
                "Origin": f"http://127.0.0.1:{self.server.server_port}",
                "X-Kshetrajna-Token": self.server.token, "Content-Type": "application/json"})
            response = connection.getresponse()
            self.assertEqual((response.status, json.loads(response.read())), (400, {"error": "invalid_json"}))
            connection.close()
        self.assertEqual(self.request("POST", "/api/v1/actions", {}, headers={"Content-Length": "4097"}),
                         (413, {"error": "invalid_request_size"}))
        self.assertEqual(self.request("POST", "/api/v1/actions", {}, headers={"Transfer-Encoding": "chunked"}),
                         (413, {"error": "invalid_request_size"}))

    def test_canonical_erasure_preserves_pending_and_uncertain_recovery_records(self):
        pending = self.proposal()
        uncertain = self.proposal("system.process_close")
        self.consent(uncertain)
        self.process_driver.result = {"status": "close_requested"}
        self.execute(uncertain)
        status, data = self.request("POST", "/api/v1/actions/erase-history", {
            "confirm": True, "ids": [pending["id"], uncertain["id"]]})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["erased"], [])
        self.assertEqual([item["reason"] for item in data["preserved"]], ["pending", "unresolved"])
        self.assertEqual(len(self.events.decisions()), 1)

    def test_legacy_delete_keeps_actions_db_and_blocks_pending_domain_recovery(self):
        action = self.proposal()
        self.events.save_decision({"id": "synthetic-recovery", "created_at": datetime.now(timezone.utc).isoformat(),
                                   "status": "restore_failed", "plan": {}})
        with patch.object(self.server.service, "restore_all"):
            status, data = self.request("POST", "/api/delete", {"confirm": True})
        self.assertEqual(status, 400, data)
        self.assertEqual(len(self.events.pending_decisions()), 1)
        self.assertIsNotNone(self.server.action_store.get(action["id"]))
        self.assertEqual(self.server.action_store.get(action["id"])["status"], "revoked")
