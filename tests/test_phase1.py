import http.client
import json
import tempfile
import threading
import unittest
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kshetrajna.collector import Collector
from kshetrajna.config import ConfigStore, Settings
from kshetrajna.models import ProcessSample, Snapshot
from kshetrajna.server import DashboardServer
from kshetrajna.storage import EventStore


def snapshot(*, at=None, app="editor.exe", idle=1.0):
    return Snapshot(
        observed_at=(at or datetime.now(timezone.utc)).isoformat(),
        cpu_percent=22.5,
        memory_total_bytes=16 * 1024**3,
        memory_available_bytes=8 * 1024**3,
        foreground_pid=42,
        foreground_app=app,
        idle_seconds=idle,
        processes=[ProcessSample(42, app, 3.5, 500_000_000)],
    )


class FakeProbe:
    def __init__(self):
        self.calls = 0
        self.resets = 0
        self.next_snapshot = snapshot()

    def sample(self, **kwargs):
        self.calls += 1
        return self.next_snapshot

    def reset(self):
        self.resets += 1


class PhaseOneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        directory = Path(self.temp.name)
        self.config = ConfigStore(directory)
        self.store = EventStore(directory / "events.db")
        self.probe = FakeProbe()
        self.collector = Collector(self.probe, self.config, self.store)

    def test_consent_and_validation(self):
        self.assertFalse(self.config.load().enabled)
        self.assertIsNone(self.collector.collect_once())
        self.assertEqual(self.probe.calls, 0)
        with self.assertRaises(ValueError):
            self.config.update({"enabled": 1})
        with self.assertRaises(ValueError):
            self.config.update({"sample_interval_seconds": 1})
        with self.assertRaises(ValueError):
            self.config.update({"unknown": True})
        self.assertFalse(self.config.load().enabled)

    def test_collection_activity_retention_and_delete(self):
        self.collector.update_settings({"enabled": True})
        self.collector.collect_once()
        self.assertEqual(self.store.latest()["foreground_app"], "editor.exe")
        self.assertEqual(self.store.latest()["processes"][0]["pid"], 42)
        self.assertEqual(self.store.activity()[0]["kind"], "foreground_changed")
        self.probe.next_snapshot = snapshot(app="browser.exe", idle=65)
        self.collector.collect_once()
        kinds = [event["kind"] for event in self.store.activity()]
        self.assertIn("became_idle", kinds)
        self.assertIn("foreground_changed", kinds)
        self.collector.update_settings({"enabled": False})
        self.assertIsNone(self.collector.collect_once())
        self.assertEqual(self.probe.calls, 2)
        self.collector.delete_history()
        self.assertIsNone(self.store.latest())
        self.assertEqual(self.store.activity(), [])

    def test_retention_deletes_old_snapshots_and_process_rows(self):
        old = datetime.now(timezone.utc) - timedelta(days=9)
        self.store.record(snapshot(at=old), [])
        self.store.record(snapshot(), [])
        self.collector.collect_once()  # Retention applies even while capture is paused.
        self.assertEqual(len(self.store.history()), 1)
        with self.store._connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM process_samples").fetchone()[0], 1)

    def test_dashboard_is_loopback_and_writes_require_token(self):
        server = DashboardServer(0, self.collector, self.config, self.store)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), thread.join(), server.server_close()))
        self.assertEqual(server.server_address[0], "127.0.0.1")
        port = server.server_port
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        connection.request("GET", "/api/state")
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertFalse(json.loads(response.read())["settings"]["enabled"])
        connection.request("POST", "/api/settings", json.dumps({"enabled": True}),
                           {"Origin": f"http://127.0.0.1:{port}", "Content-Type": "application/json"})
        response = connection.getresponse()
        self.assertEqual(response.status, 403)
        response.read()
        self.assertFalse(self.config.load().enabled)
        connection.request("POST", "/api/settings", json.dumps({"enabled": True}),
                           {"Origin": f"http://127.0.0.1:{port}",
                            "X-Kshetrajna-Token": server.token, "Content-Type": "application/json"})
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        response.read()
        self.assertTrue(self.config.load().enabled)
        connection.close()


if __name__ == "__main__":
    unittest.main()
