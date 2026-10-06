import tempfile
import time
import unittest
import http.client
import json
import threading
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from kshetrajna.collector import Collector
from kshetrajna.config import ConfigStore
from kshetrajna.demo import DemoProbe
from kshetrajna.file_service import FileService
from kshetrajna.file_storage import FileStore
from kshetrajna.jarvis import JarvisCommandBridge, JarvisHttpClient, serve_jsonl
from kshetrajna.known_folders import discover_known_folders
from kshetrajna.server import DashboardServer
from kshetrajna.storage import EventStore


class SortAiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root_path = Path(self.temp.name) / "approved"
        self.root_path.mkdir()
        (self.root_path / "main.py").write_text("print('hello')", encoding="utf-8")
        (self.root_path / "notes.txt").write_text("meeting notes", encoding="utf-8")
        self.store = FileStore(Path(self.temp.name) / "files.db")
        self.service = FileService(self.store)
        self.root_id = self.service.add_root(str(self.root_path), "Approved")["id"]
        for _ in range(100):
            if self.store.get_root(self.root_id)["status"] != "scanning":
                break
            time.sleep(0.01)

    def tearDown(self):
        if self.store.get_roots():
            self.service.delete_all()
        self.temp.cleanup()

    def test_preview_edit_apply_and_reverse_batch(self):
        plan = self.service.generate_sort_plan(self.root_id, create_folders=True)
        self.assertEqual(plan["status"], "preview")
        self.assertEqual(len(plan["suggestions"]), 2)
        self.assertTrue(all(s["selected"] for s in plan["suggestions"]))

        first = plan["suggestions"][0]
        plan = self.service.update_sort_plan(plan["id"], [{
            "id": first["id"],
            "selected": False,
            "destination_relative": first["destination_relative"],
        }])
        result = self.service.apply_sort_plan(plan["id"])
        self.assertEqual(result["status"], "applied")
        self.assertEqual(len(result["applied"]), 1)
        self.assertEqual(len(result["failed"]), 0)
        self.assertEqual(len(list(self.root_path.rglob("*.txt"))), 1)

        undone = self.service.undo_sort_plan(plan["id"])
        self.assertEqual(undone["status"], "rolled_back")
        self.assertTrue((self.root_path / "notes.txt").exists())
        self.assertFalse((self.root_path / "Documents").exists())

    def test_conflicts_are_visible_and_not_selected(self):
        (self.root_path / "Documents").mkdir()
        (self.root_path / "Documents" / "notes.txt").write_text("existing", encoding="utf-8")
        plan = self.service.generate_sort_plan(self.root_id, create_folders=True)
        notes = next(s for s in plan["suggestions"] if s["source_relative"] == "notes.txt")
        self.assertFalse(notes["selected"])
        self.assertEqual(notes["status"], "blocked")
        self.assertIn("already exists", notes["reason"])

    def test_catalog_deletion_keeps_unresolved_action_recoverable(self):
        file_id = self.store.search_files("main.py")[0]["id"]
        action = self.service.create_proposal(file_id, "RENAME", "renamed.py")
        with self.assertRaisesRegex(ValueError, "recovery records"):
            self.service.delete_all()
        with self.assertRaisesRegex(ValueError, "recovery records"):
            self.service.remove_root(self.root_id)
        self.service.dismiss_proposal(action["proposal_id"])
        self.service.delete_all()
        self.assertEqual(self.store.get_roots(), [])

    def test_demo_batch_never_creates_or_moves_fixture_files(self):
        demo_service = FileService(self.store, demo=True)
        plan = demo_service.generate_sort_plan(self.root_id, create_folders=True)
        result = demo_service.apply_sort_plan(plan["id"])
        self.assertEqual(result["status"], "applied")
        self.assertTrue((self.root_path / "main.py").exists())
        self.assertFalse((self.root_path / "Code").exists())
        self.assertEqual(demo_service.undo_sort_plan(plan["id"])["status"], "rolled_back")

    def test_batch_reports_partial_completion_without_overwrite(self):
        plan = self.service.generate_sort_plan(self.root_id, create_folders=True)
        edits = [{"id": suggestion["id"], "selected": True,
                  "destination_relative": "Documents/same.txt"}
                 for suggestion in plan["suggestions"]]
        plan = self.service.update_sort_plan(plan["id"], edits)
        result = self.service.apply_sort_plan(plan["id"])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(result["applied"]), 1)
        self.assertEqual(len(result["failed"]), 1)
        self.assertEqual(self.service.undo_sort_plan(plan["id"])["status"], "rolled_back")
        self.assertTrue((self.root_path / "main.py").exists())
        self.assertTrue((self.root_path / "notes.txt").exists())

    def test_jarvis_bridge_requires_explicit_apply_confirmation(self):
        bridge = JarvisCommandBridge(self.service)
        plan = bridge.execute("sort_preview", root_id=self.root_id, create_folders=True)
        with self.assertRaisesRegex(ValueError, "confirm=true"):
            bridge.execute("sort_apply", plan_id=plan["id"])
        result = bridge.execute("sort_apply", plan_id=plan["id"], confirm=True)
        self.assertEqual(result["status"], "applied")
        self.assertEqual(bridge.execute("sort_undo", plan_id=plan["id"], confirm=True)["status"], "rolled_back")

    def test_jarvis_jsonl_rejects_unknown_commands(self):
        from io import StringIO
        output = StringIO()
        serve_jsonl(JarvisCommandBridge(self.service), StringIO('{"command":"nope"}\n'), output)
        response = json.loads(output.getvalue())
        self.assertFalse(response["ok"])
        self.assertIn("Unknown Jarvis command", response["error"])

    def test_known_folder_discovery_does_not_enumerate_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory)
            (profile / "Downloads").mkdir()
            (profile / "Downloads" / "private.txt").write_text("not read", encoding="utf-8")
            with patch.dict("os.environ", {"USERPROFILE": directory}, clear=False):
                folders = discover_known_folders()
            self.assertEqual([(folder["id"], folder["path"]) for folder in folders],
                             [("downloads", str(profile / "Downloads"))])


class SortAiHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name) / "approved"
        root.mkdir()
        (root / "sample.py").write_text("print('ok')", encoding="utf-8")
        self.files = FileService(FileStore(Path(self.temp.name) / "files.db"))
        self.root_id = self.files.add_root(str(root), "Approved")["id"]
        for _ in range(100):
            if self.files.store.get_root(self.root_id)["status"] != "scanning":
                break
            time.sleep(0.01)
        config = ConfigStore(Path(self.temp.name))
        config.update({"enabled": True})
        events = EventStore(Path(self.temp.name) / "events.db")
        collector = Collector(DemoProbe(), config, events)
        self.server = DashboardServer(0, collector, config, events, demo=True)
        self.server.file_service = self.files
        self.server.service.file_service = self.files
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        if self.files.store.get_roots():
            self.files.delete_all()
        self.temp.cleanup()

    def call(self, method, path, body=None):
        port = self.server.server_port
        with closing(http.client.HTTPConnection("127.0.0.1", port, timeout=3)) as connection:
            connection.request(method, path, json.dumps(body) if body is not None else None, {
                "Origin": f"http://127.0.0.1:{port}",
                "X-Kshetrajna-Token": self.server.token,
                "Content-Type": "application/json",
            })
            response = connection.getresponse()
            return response.status, json.loads(response.read())

    def test_preview_endpoint_returns_reviewable_plan(self):
        status, plan = self.call("POST", "/api/files/sort/preview", {"root_id": self.root_id, "create_folders": True})
        self.assertEqual(status, 200)
        self.assertEqual(plan["status"], "preview")
        self.assertTrue(plan["suggestions"])
        status, detail = self.call("GET", "/api/files/sort/plan/" + plan["id"])
        self.assertEqual(status, 200)
        self.assertEqual(detail["id"], plan["id"])
        status, _ = self.call("POST", "/api/files/sort/update", {"plan_id": plan["id"], "suggestions": "bad"})
        self.assertEqual(status, 400)

    def test_jarvis_http_client_reuses_dashboard_token(self):
        bridge = JarvisHttpClient(f"http://127.0.0.1:{self.server.server_port}")
        result = bridge.execute("sort_preview", root_id=self.root_id, create_folders=True)
        self.assertEqual(result["status"], "preview")


if __name__ == "__main__":
    unittest.main()
