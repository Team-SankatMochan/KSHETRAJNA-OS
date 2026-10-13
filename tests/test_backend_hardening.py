import http.client
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from kshetrajna.collector import Collector
from kshetrajna.config import ConfigStore
from kshetrajna.demo import DemoProbe
from kshetrajna.file_actions import FileActionManager
from kshetrajna.file_demo import seed_file_demo
from kshetrajna.file_service import FileService
from kshetrajna.file_storage import FileStore
from kshetrajna.server import DashboardServer
from kshetrajna.storage import EventStore


class TestBackendHardening(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = FileStore(Path(self.temp.name) / "files.db")
        self.service = FileService(self.store)

    def tearDown(self):
        # The fixture intentionally creates an action row; remove it before the
        # service's root cleanup because file_actions has a restrictive FK.
        with self.store._connect() as db:
            db.execute("DELETE FROM file_actions")
        self.service.delete_all()

    def wait_scan(self, root_id):
        while True:
            root = self.store.get_root(root_id)
            if root["status"] in ("ready", "error", "cancelled", "truncated", "removed"):
                return root

    def test_scan_limit_is_reported_as_truncated(self):
        root = Path(self.temp.name) / "root"
        root.mkdir()
        for index in range(3):
            (root / f"{index}.txt").write_text(str(index))

        # Keep the production limit untouched; this makes the boundary quick to
        # exercise and proves the scanner communicates the truncation to the
        # service instead of silently returning a ready scan.
        with patch("kshetrajna.file_scanner.MAX_FILES", 2):
            root_id = self.service.add_root(str(root), "Root")["id"]
            result = self.wait_scan(root_id)

        self.assertEqual(result["status"], "truncated")
        with self.store._connect() as db:
            state = db.execute("SELECT files_seen FROM scan_state WHERE root_id=?", (root_id,)).fetchone()
        self.assertEqual(state["files_seen"], 2)

    def test_windows_large_file_identity_round_trip_is_lossless(self):
        with self.store._connect() as db:
            db.execute("INSERT INTO roots (id,path,created_at,status) VALUES (1,?,'now','ready')", (self.temp.name,))
            db.execute("INSERT INTO files (id,root_id,relative_path,name,indexed_at) VALUES (1,1,'doc.txt','doc.txt','now')")
        action = {"id":"large-identity", "proposal_id":"large-proposal", "root_id":1,
                  "file_id":1, "kind":"RENAME", "source_relative":"doc.txt",
                  "destination_relative":"next.txt", "expected_dev":2**80,
                  "expected_ino":2**100+123, "post_dev":2**81+9,
                  "post_ino":2**101+789, "created_at":"now", "expires_at":"later",
                  "status":"proposed", "mode":"demo"}
        self.store.save_file_action(action)
        loaded = self.store.get_file_action(action_id=action["id"])
        identity_fields = ("expected_dev", "expected_ino", "post_dev", "post_ino")
        for field in identity_fields:
            with self.subTest(path="insert", field=field):
                self.assertEqual(loaded[field], action[field])
        updated = {field: 2**(110+index)+456 for index, field in enumerate(identity_fields)}
        self.store.update_file_action(action["id"], updated)
        loaded = self.store.get_file_action(action_id=action["id"])
        journal = self.store.get_file_actions()[0]
        for field, expected in updated.items():
            with self.subTest(path="update", field=field):
                self.assertEqual(loaded[field], expected)
                self.assertEqual(journal[field], expected)

    def test_root_validation_rejects_files_and_symlink_roots(self):
        file_path = Path(self.temp.name) / "not-a-directory"
        file_path.write_text("file")
        with self.assertRaisesRegex(ValueError, "directory"):
            self.service.add_root(str(file_path), "File")
        with self.assertRaisesRegex(ValueError, "Path traversal"):
            self.service.add_root(str(file_path.parent / ".." / file_path.name), "Traversal")

        target = Path(self.temp.name) / "target"
        target.mkdir()
        link = Path(self.temp.name) / "link"
        try:
            link.symlink_to(target, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        with self.assertRaisesRegex(ValueError, "Symlinks and reparse points"):
            self.service.add_root(str(link), "Link")

    def test_post_mutation_race_returns_failed(self):
        root = Path(self.temp.name) / "root"
        root.mkdir()
        source = root / "doc.txt"
        source.write_text("hello")
        with self.store._connect() as db:
            db.execute("INSERT INTO roots (id, path, created_at, status) VALUES (1, ?, 'now', 'ready')", (str(root),))
            db.execute("INSERT INTO files (id, root_id, relative_path, name, indexed_at) VALUES (1, 1, 'doc.txt', 'doc.txt', 'now')")

        manager = FileActionManager(self.store)
        action = manager.create_proposal(1, "RENAME", "renamed.txt")
        original_rename = os.rename

        def rename_then_race(src, dst):
            original_rename(src, dst)
            Path(dst).unlink()

        with patch("kshetrajna.file_actions.os.rename", side_effect=rename_then_race):
            result = manager.apply_action(action["proposal_id"])

        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.store.get_file_action(action_id=action["id"])["status"], "failed")

    def test_file_catalog_extension_stays_normalized_after_undo(self):
        root = Path(self.temp.name) / "root"
        root.mkdir()
        source = root / "doc.txt"
        source.write_text("hello")
        with self.store._connect() as db:
            db.execute("INSERT INTO roots (id, path, created_at, status) VALUES (1, ?, 'now', 'ready')", (str(root),))
            db.execute("INSERT INTO files (id, root_id, relative_path, name, extension, indexed_at) VALUES (1, 1, 'doc.txt', 'doc.txt', 'txt', 'now')")

        manager = FileActionManager(self.store)
        action = manager.create_proposal(1, "RENAME", "renamed.txt")
        manager.apply_action(action["proposal_id"])
        self.assertEqual(self.store.get_file(1)["extension"], "txt")
        manager.undo_action(action["id"])
        self.assertEqual(self.store.get_file(1)["extension"], "txt")


class TestMalformedFileApiIds(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        config = ConfigStore(Path(self.temp.name))
        events = EventStore(Path(self.temp.name) / "events.db")
        collector = Collector(DemoProbe(), config, events)
        self.file_store = FileStore(Path(self.temp.name) / "files.db")
        self.file_service = FileService(self.file_store, demo=True)
        seed_file_demo(self.file_store)
        self.server = DashboardServer(0, collector, config, events, demo=True)
        self.server.file_service = self.file_service
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.file_service.delete_all()

    def request(self, method, path, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
        headers = {"Host": f"127.0.0.1:{self.server.server_port}"}
        if method == "POST":
            headers.update({
                "Origin": f"http://127.0.0.1:{self.server.server_port}",
                "X-Kshetrajna-Token": self.server.token,
                "Content-Type": "application/json",
            })
            body = json.dumps(body or {})
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        data = json.loads(response.read())
        connection.close()
        return response.status, data

    def test_malformed_ids_are_controlled_bad_requests(self):
        for path in (
            "/api/files/root/not-an-id",
            "/api/files/file/not-an-id/excerpt",
            "/api/files/search?root_id=not-an-id",
            "/api/files/search?root_id=",
        ):
            status, body = self.request("GET", path)
            self.assertEqual(status, 400, path)
            self.assertIn("error", body)

        status, body = self.request("POST", "/api/files/roots/rescan", {"root_id": "not-an-id"})
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_search_rejects_non_text_query(self):
        with self.assertRaisesRegex(ValueError, "Search query"):
            self.file_service.search(None)


if __name__ == "__main__":
    unittest.main()
