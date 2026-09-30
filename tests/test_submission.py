import http.client
import json
import tempfile
import threading
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from kshetrajna.actions import SimulatedPriorityDriver, WindowsPriorityDriver, NORMAL, BELOW_NORMAL
from kshetrajna.instance import InstanceLock
from kshetrajna.collector import Collector
from kshetrajna.config import ConfigStore
from kshetrajna.demo import DemoProbe, SCENARIOS, seed_demo
from kshetrajna.intelligence import analyze, recommend
from kshetrajna.server import DashboardServer
from kshetrajna.service import AssistantService
from kshetrajna.storage import EventStore


class RecordingDriver(SimulatedPriorityDriver):
    def __init__(self, store):
        self.store = store
        self.applied = 0
        self.restored = 0
        self.fail_restore = False

    def apply(self, plan):
        assert self.store.pending_decisions()[0]["status"] == "prepared"
        self.applied += 1

    def restore(self, plan):
        if self.fail_restore:
            raise OSError("controlled test failure")
        self.restored += 1
        return "rolled_back"


class SubmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = ConfigStore(Path(self.temp.name))
        self.config.update({"enabled": True})
        self.store = EventStore(Path(self.temp.name) / "events.db")
        self.probe = DemoProbe()
        seed_demo(self.store, self.probe)
        self.collector = Collector(self.probe, self.config, self.store)
        self.collector.collect_once()
        self.driver = RecordingDriver(self.store)
        self.service = AssistantService(self.collector, self.config, self.store, demo=True, driver=self.driver)

    def proposal_id(self):
        return next(p["id"] for p in self.service.state()["recommendations"] if p["kind"] == "priority")

    def test_context_personal_patterns_and_prediction(self):
        # A next-app suggestion waits for measured dwell in the current app.
        self.store.record(self.probe.sample(at=datetime.now(timezone.utc) + timedelta(seconds=5)), [])
        state = self.service.state()
        self.assertEqual(state["intelligence"]["context"], "Development")
        self.assertGreater(state["intelligence"]["active_minutes"], 10)
        self.assertTrue(state["intelligence"]["workflows"])
        self.assertIsNotNone(state["intelligence"]["prediction"])
        for scenario in SCENARIOS:
            self.service.change_scenario(scenario)
            self.collector.collect_once()
            self.assertEqual(self.service.state()["intelligence"]["context"], scenario)

    def test_no_data_has_no_fabricated_predictions(self):
        model = analyze([], None, [])
        self.assertEqual(model["confidence"], 0)
        self.assertEqual(recommend(None, model), [])
        self.assertIsNone(model["prediction"])

    def test_predictions_respect_collection_privacy_freshness_and_deletion(self):
        self.config.update({"enabled": False})
        self.assertIsNone(self.service.state()["intelligence"]["prediction"])
        self.assertIn("paused", self.service.state()["intelligence"]["prediction_reason"])
        self.config.update({"enabled": True, "track_foreground": False})
        self.assertIsNone(self.service.state()["intelligence"]["prediction"])
        self.config.update({"track_foreground": True})
        self.service._clock = lambda: datetime.now(timezone.utc).timestamp() + 120
        self.assertIn("fresh", self.service.state()["intelligence"]["prediction_reason"])
        self.store.clear()
        model = self.service.state()["intelligence"]
        self.assertEqual(model["workflows"], [])
        self.assertEqual(model["sequences"], [])
        self.assertEqual(model["prediction_quality"]["evaluated"], 0)

    def test_live_opt_in_and_pause_both_gate_approval(self):
        self.service.demo = False
        with self.assertRaisesRegex(ValueError, "Enable live"):
            self.service.approve(self.proposal_id())
        self.config.update({"allow_priority_changes": True, "enabled": False})
        with self.assertRaisesRegex(ValueError, "Enable collection"):
            self.service.approve(self.proposal_id())
        self.assertEqual(self.driver.applied, 0)

    def test_journal_before_apply_manual_undo_and_evaluation(self):
        decision = self.service.approve(self.proposal_id())
        self.assertEqual(self.driver.applied, 1)
        self.assertEqual(decision["status"], "active")
        for i in range(4):
            self.store.record(self.probe.sample(at=datetime.now(timezone.utc) + timedelta(seconds=i + 1)), [])
        restored = self.service.undo(decision["id"])
        self.assertEqual(restored["status"], "rolled_back")
        self.assertEqual(restored["evaluation"]["samples_after"], 4)
        self.assertIn("other workload", restored["evaluation"]["conclusion"])
        self.assertEqual(self.driver.restored, 1)

    def test_one_trial_and_fresh_sample_required(self):
        self.service.approve(self.proposal_id())
        with self.assertRaisesRegex(ValueError, "active trial"):
            self.service.approve(self.proposal_id())
        self.service.restore_all()
        self.service._clock = lambda: datetime.now(timezone.utc).timestamp() + 120
        with self.assertRaisesRegex(ValueError, "stale"):
            self.service.approve(self.proposal_id())

    def test_expiry_and_restart_recover_from_durable_journal(self):
        decision = self.service.approve(self.proposal_id())
        self.service._clock = lambda: decision["expires_at"] + 1
        self.service.tick()
        self.assertFalse(self.store.pending_decisions())
        self.service._clock = lambda: datetime.now(timezone.utc).timestamp()
        self.service.approve(self.proposal_id())
        restarted = AssistantService(self.collector, self.config, self.store, demo=True, driver=self.driver)
        restarted.restore_all()
        self.assertFalse(self.store.pending_decisions())
        self.assertEqual(self.driver.restored, 2)

    def test_pause_restores_and_feedback_suppresses(self):
        proposal_id = self.proposal_id()
        decision = self.service.approve(proposal_id)
        self.collector.update_settings({"enabled": False})
        self.service.tick()
        self.assertFalse(self.store.pending_decisions())
        self.service.feedback(decision["id"], "not_helpful")
        self.assertNotIn(proposal_id, [p["id"] for p in self.service.state()["recommendations"]])

    def test_delete_restores_and_failed_restore_keeps_recovery_record(self):
        decision = self.service.approve(self.proposal_id())
        self.driver.fail_restore = True
        with self.assertLogs("kshetrajna.service", level="ERROR"):
            with self.assertRaisesRegex(ValueError, "recovery record"):
                self.service.delete_history()
        self.assertEqual(self.store.pending_decisions()[0]["status"], "restore_failed")
        self.assertIsNotNone(self.store.latest())
        self.driver.fail_restore = False
        self.service.delete_history()
        self.assertIsNone(self.store.latest())
        self.assertFalse(self.store.decisions())

    def test_workspace_membership_validation_and_reset(self):
        self.service.save_workspace("Development", ["Code.exe", "chrome.exe"])
        self.service.activate_workspace("Development")
        self.assertEqual(self.service.state()["active_workspace"], "Development")
        with self.assertRaises(ValueError):
            self.service.save_workspace("Injected app", ["powershell.exe -Command anything"])
        self.service.activate_workspace(None)
        self.assertIsNone(self.service.state()["active_workspace"])
        self.assertEqual(self.driver.applied, 0)

    def test_data_directory_cannot_be_used_by_two_instances(self):
        first = InstanceLock(Path(self.temp.name))
        try:
            with self.assertRaises(RuntimeError):
                InstanceLock(Path(self.temp.name))
        finally:
            first.close()
        next_instance = InstanceLock(Path(self.temp.name))
        next_instance.close()

    def test_native_driver_never_overwrites_external_priority(self):
        driver = WindowsPriorityDriver.__new__(WindowsPriorityDriver)
        driver.probe = SimpleNamespace(close_handle=lambda handle: None, _foreground=lambda: 500)
        driver._open_verified = lambda target: 1
        current = [NORMAL]
        writes = []
        driver.get_priority = lambda handle: current[0]
        driver.set_priority = lambda handle, value: writes.append(value) or current.__setitem__(0, value) or True
        target = {"pid": 90001, "name": "chrome.exe", "created_ticks": 100}
        plan = driver.prepare(target)
        driver.apply(plan)
        self.assertEqual(writes, [BELOW_NORMAL])
        current[0] = 0x8000  # An external actor changed the priority.
        self.assertEqual(driver.restore(plan), "superseded")
        self.assertEqual(writes, [BELOW_NORMAL])
        current[0] = BELOW_NORMAL
        self.assertEqual(driver.restore(plan), "rolled_back")
        self.assertEqual(writes, [BELOW_NORMAL, NORMAL])
        driver.probe._foreground = lambda: target["pid"]
        with self.assertRaisesRegex(ValueError, "foreground"):
            driver.prepare(target)

    def test_native_driver_rejects_reused_process_identity(self):
        driver = WindowsPriorityDriver.__new__(WindowsPriorityDriver)
        closed = []

        def name(handle, flags, buffer, size):
            buffer.value = "chrome.exe"
            return True

        def times(handle, created, exited, kernel, user):
            created._obj.low = 124
            return True

        driver._session = lambda pid: 1
        driver.probe = SimpleNamespace(open_process=lambda *args: 7, close_handle=closed.append,
                                       process_name=name, process_times=times)
        with self.assertRaisesRegex(ValueError, "identity changed"):
            driver._open_verified({"pid": 90001, "name": "chrome.exe", "created_ticks": 123})
        self.assertEqual(closed, [7])

    def test_http_demo_full_cycle_assets_and_origin_guards(self):
        server = DashboardServer(0, self.collector, self.config, self.store, demo=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), thread.join(), server.server_close()))
        port = server.server_port

        def call(method, path, body=None, origin=None, token=None, host=None):
            with closing(http.client.HTTPConnection("127.0.0.1", port, timeout=3)) as connection:
                headers = {"Origin": origin or f"http://127.0.0.1:{port}",
                           "X-Kshetrajna-Token": token or server.token}
                if host:
                    headers["Host"] = host
                connection.request(method, path, json.dumps(body) if body is not None else None, headers)
                response = connection.getresponse()
                return response.status, response.read(), response.getheader("Content-Security-Policy")

        for asset in ("/", "/app.js", "/app.css", "/intelligence.js", "/intelligence.css"):
            status, content, policy = call("GET", asset)
            self.assertEqual(status, 200, asset)
            self.assertTrue(content)
            self.assertIn("frame-ancestors 'none'", policy)
        self.assertEqual(call("GET", "/api/state", host="evil.example")[0], 403)
        self.assertEqual(call("POST", "/api/actions/approve", {}, origin="https://evil.example")[0], 403)
        self.assertEqual(call("POST", "/api/settings", [], token="invalid")[0], 403)
        self.assertEqual(call("POST", "/api/settings", [])[0], 400)
        status, body, _ = call("POST", "/api/actions/approve", {"id": self.proposal_id()})
        self.assertEqual(status, 200, body)
        decision = json.loads(body)["decision"]
        self.assertEqual(decision["mode"], "demo")
        self.assertEqual(call("POST", "/api/actions/undo", {"id": decision["id"]})[0], 200)
        status, body, _ = call("GET", "/api/report")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["mode"], "demo")


if __name__ == "__main__":
    unittest.main()
