"""Tests for empty-file and duplicate-file detection/deletion in FileService."""

import hashlib
import http.client
import json
import os
import time
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from kshetrajna.file_storage import FileStore
from kshetrajna.file_service import FileService
from kshetrajna.collector import Collector
from kshetrajna import cli
from kshetrajna.config import ConfigStore
from kshetrajna.demo import DemoProbe
from kshetrajna.jarvis import JarvisCommandBridge, JarvisHttpClient
from kshetrajna.server import DashboardServer
from kshetrajna.storage import EventStore


class TestEmptyFileDetection(unittest.TestCase):
    """Unit tests for get_empty_files and delete_empty_file."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = FileStore(Path(self.temp.name) / "files.db")
        self.service = FileService(self.store)
        self.root_path = Path(self.temp.name) / "root"
        self.root_path.mkdir()

    def tearDown(self):
        self.service.delete_all()

    def _wait_scan(self, root_id):
        while True:
            root = self.store.get_root(root_id)
            if root["status"] in ("ready", "error", "cancelled", "truncated"):
                break
            time.sleep(0.05)

    def test_empty_files_detected_after_scan(self):
        """Zero-byte files are listed; non-empty files are not."""
        (self.root_path / "empty.txt").write_bytes(b"")
        (self.root_path / "notempty.txt").write_bytes(b"hello")

        root_id = self.service.add_root(str(self.root_path), "Root")["id"]
        self._wait_scan(root_id)

        result = self.service.get_empty_files(root_id)
        names = [f["name"] for f in result["empty_files"]]
        self.assertIn("empty.txt", names)
        self.assertNotIn("notempty.txt", names)
        self.assertEqual(result["total"], 1)

    def test_protected_empty_files_excluded(self):
        """__init__.py, .gitkeep etc. must NOT appear as empty-file candidates."""
        for protected in ("__init__.py", ".gitkeep", ".keep", "py.typed", "__init__.pyi"):
            (self.root_path / protected).write_bytes(b"")

        root_id = self.service.add_root(str(self.root_path), "Root")["id"]
        self._wait_scan(root_id)

        result = self.service.get_empty_files(root_id)
        self.assertEqual(result["total"], 0, f"Protected files leaked: {result['empty_files']}")

    def test_empty_files_without_root_id_filter(self):
        """get_empty_files() with no root_id returns across all roots."""
        root2 = Path(self.temp.name) / "root2"
        root2.mkdir()
        (self.root_path / "a.txt").write_bytes(b"")
        (root2 / "b.txt").write_bytes(b"")

        r1 = self.service.add_root(str(self.root_path), "R1")["id"]
        r2 = self.service.add_root(str(root2), "R2")["id"]
        self._wait_scan(r1)
        self._wait_scan(r2)

        result = self.service.get_empty_files()
        names = [f["name"] for f in result["empty_files"]]
        self.assertIn("a.txt", names)
        self.assertIn("b.txt", names)

    def test_delete_empty_file_requires_confirm(self):
        """Calling without confirm=True must raise."""
        (self.root_path / "e.txt").write_bytes(b"")
        root_id = self.service.add_root(str(self.root_path), "Root")["id"]
        self._wait_scan(root_id)
        file_id = self.service.get_empty_files(root_id)["empty_files"][0]["id"]
        with self.assertRaisesRegex(ValueError, "confirm=true"):
            self.service.delete_empty_file(file_id)

    def test_delete_empty_file_success_and_journaled(self):
        """delete_empty_file removes the file and journals the action."""
        empty = self.root_path / "gone.txt"
        empty.write_bytes(b"")
        root_id = self.service.add_root(str(self.root_path), "Root")["id"]
        self._wait_scan(root_id)
        file_id = self.service.get_empty_files(root_id)["empty_files"][0]["id"]

        result = self.service.delete_empty_file(file_id, confirm=True)
        self.assertTrue(result["deleted"])
        self.assertFalse(empty.exists(), "File must be physically removed")

        # Action must appear in the journal
        journal = self.service.get_action_journal()
        self.assertTrue(
            any(a["kind"] == "DELETE" and "gone.txt" in a["source_relative"] for a in journal)
        )

    def test_delete_empty_file_rejects_nonempty(self):
        """A file that grew between scan and delete must be rejected."""
        f = self.root_path / "surprise.txt"
        f.write_bytes(b"")
        root_id = self.service.add_root(str(self.root_path), "Root")["id"]
        self._wait_scan(root_id)
        file_id = self.service.get_empty_files(root_id)["empty_files"][0]["id"]

        # Write content before delete is called
        f.write_bytes(b"now i have content")
        with self.assertRaisesRegex(ValueError, "no longer empty"):
            self.service.delete_empty_file(file_id, confirm=True)

    def test_delete_empty_file_blocked_in_demo_mode(self):
        """demo=True must prevent deletion."""
        demo_service = FileService(self.store, demo=True)
        with self.assertRaisesRegex(ValueError, "demo mode"):
            demo_service.delete_empty_file(1, confirm=True)

    def test_delete_protected_empty_file_rejected(self):
        """Attempting to delete __init__.py (by file_id) should fail gracefully."""
        init = self.root_path / "__init__.py"
        init.write_bytes(b"")
        root_id = self.service.add_root(str(self.root_path), "Root")["id"]
        self._wait_scan(root_id)

        # Fetch the file_id directly from DB (it won't appear in get_empty_files)
        with self.store._connect() as db:
            row = db.execute(
                "SELECT id FROM files WHERE name='__init__.py' AND root_id=?", (root_id,)
            ).fetchone()
        self.assertIsNotNone(row, "__init__.py should be indexed")
        with self.assertRaisesRegex(ValueError, "protected"):
            self.service.delete_empty_file(row["id"], confirm=True)

    def test_sensitive_and_project_markers_are_not_cleanup_candidates(self):
        """Credentials and project-control files stay out of both previews."""
        names = (".env", "private.pem", "requirements.txt", "package.json", ".gitattributes")
        for name in names:
            (self.root_path / name).write_bytes(b"")
        root_id = self.service.add_root(str(self.root_path), "Root")["id"]
        self._wait_scan(root_id)
        result = self.service.get_empty_files(root_id)
        self.assertEqual(result["empty_files"], [])
        with self.store._connect() as db:
            rows = db.execute("SELECT id FROM files WHERE root_id=? ORDER BY name", (root_id,)).fetchall()
        for row in rows:
            with self.assertRaisesRegex(ValueError, "protected"):
                self.service.delete_empty_file(row["id"], confirm=True)

    def test_missing_cleanup_root_is_an_explicit_error(self):
        with self.assertRaisesRegex(ValueError, "Root not found"):
            self.service.get_empty_files(99999)
        with self.assertRaisesRegex(ValueError, "Root not found"):
            self.service.get_duplicate_groups(99999)

    def test_cleanup_rejects_unready_root_instead_of_using_stale_catalog(self):
        (self.root_path / "stale.txt").write_bytes(b"")
        root_id = self.service.add_root(str(self.root_path), "Root")["id"]
        self._wait_scan(root_id)
        self.store.update_root_status(root_id, "error", error="Root became unavailable")
        with self.assertRaisesRegex(ValueError, "not ready"):
            self.service.get_empty_files(root_id)
        with self.assertRaisesRegex(ValueError, "not ready"):
            self.service.get_duplicate_groups(root_id)

    def test_cleanup_excludes_catalog_rows_from_an_older_scan_pass(self):
        empty = self.root_path / "old-index.txt"
        empty.write_bytes(b"")
        root_id = self.service.add_root(str(self.root_path), "Root")["id"]
        self._wait_scan(root_id)
        with self.store._connect() as db:
            db.execute("UPDATE scan_state SET last_pass=last_pass+1 WHERE root_id=?", (root_id,))
        self.assertEqual(self.service.get_empty_files(root_id)["empty_files"], [])

    def test_generic_delete_proposal_rejects_an_error_root(self):
        file_path = self.root_path / "protected-by-status.txt"
        file_path.write_text("content", encoding="utf-8")
        root_id = self.service.add_root(str(self.root_path), "Root")["id"]
        self._wait_scan(root_id)
        with self.store._connect() as db:
            file_id = db.execute(
                "SELECT id FROM files WHERE root_id=? AND relative_path=?", (root_id, file_path.name)
            ).fetchone()[0]
        self.store.update_root_status(root_id, "error", error="unavailable")
        with self.assertRaisesRegex(ValueError, "ready or truncated"):
            self.service.create_proposal(file_id, "DELETE", file_path.name)

    def test_project_tree_is_excluded_but_general_empty_files_remain(self):
        project = self.root_path / "project"
        project.mkdir()
        (project / "package.json").write_text("{}", encoding="utf-8")
        (project / "scratch.txt").write_bytes(b"")
        general = self.root_path / "general.txt"
        general.write_bytes(b"")
        root_id = self.service.add_root(str(self.root_path), "Root")["id"]
        self._wait_scan(root_id)

        names = {file["name"] for file in self.service.get_empty_files(root_id)["empty_files"]}
        self.assertIn("general.txt", names)
        self.assertNotIn("scratch.txt", names)

        with self.store._connect() as db:
            project_file_id = db.execute(
                "SELECT id FROM files WHERE root_id=? AND relative_path=?",
                (root_id, "project/scratch.txt"),
            ).fetchone()[0]
        with self.assertRaisesRegex(ValueError, "project tree"):
            self.service.delete_empty_file(project_file_id, confirm=True)

    def test_symlink_is_not_a_cleanup_target(self):
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks are unavailable")
        target = self.root_path / "target.txt"
        target.write_text("target", encoding="utf-8")
        link = self.root_path / "link.txt"
        try:
            link.symlink_to(target)
        except OSError:
            self.skipTest("symlinks require platform permission")
        root_id = self.service.add_root(str(self.root_path), "Root")["id"]
        self._wait_scan(root_id)
        # The scanner correctly omits links; inject a stale catalog row to
        # prove the destructive endpoint still rejects one if it is present.
        with self.store._connect() as db:
            db.execute(
                "INSERT INTO files (root_id,relative_path,name,extension,size_bytes,is_directory,indexed_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (root_id, "link.txt", "link.txt", "txt", 0, 0, "now"),
            )
            file_id = db.execute("SELECT id FROM files WHERE root_id=? AND relative_path=?", (root_id, "link.txt")).fetchone()[0]
        self.assertEqual(self.service.get_empty_files(root_id)["total"], 0)
        with self.assertRaisesRegex(ValueError, "symlink|reparse|regular"):
            self.service.delete_empty_file(file_id, confirm=True)

    def test_local_bridge_exposes_reviewed_cleanup_commands(self):
        (self.root_path / "bridge-empty.txt").write_bytes(b"")
        root_id = self.service.add_root(str(self.root_path), "Root")["id"]
        self._wait_scan(root_id)
        bridge = JarvisCommandBridge(self.service)

        preview = bridge.execute("cleanup_empty", root_id=root_id)
        file_id = next(file["id"] for file in preview["empty_files"] if file["name"] == "bridge-empty.txt")
        with self.assertRaisesRegex(ValueError, "confirm=true"):
            bridge.execute("empty_delete", file_id=file_id)
        result = bridge.execute("empty_delete", file_id=file_id, confirm=True)
        self.assertTrue(result["deleted"])


class TestDuplicateFileDetection(unittest.TestCase):
    """Unit tests for get_duplicate_groups and delete_duplicate_file."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = FileStore(Path(self.temp.name) / "files.db")
        self.service = FileService(self.store)
        self.root_path = Path(self.temp.name) / "root"
        self.root_path.mkdir()

    def tearDown(self):
        self.service.delete_all()

    def _wait_scan(self, root_id):
        while True:
            root = self.store.get_root(root_id)
            if root["status"] in ("ready", "error", "cancelled", "truncated"):
                break
            time.sleep(0.05)

    def test_no_duplicates_when_all_unique(self):
        """Unique files produce no duplicate groups."""
        (self.root_path / "a.txt").write_bytes(b"aaa")
        (self.root_path / "b.txt").write_bytes(b"bbb")
        root_id = self.service.add_root(str(self.root_path), "Root")["id"]
        self._wait_scan(root_id)

        result = self.service.get_duplicate_groups(root_id)
        self.assertEqual(result["total_groups"], 0)
        self.assertEqual(result["groups"], [])
        self.assertEqual(result["total_wasted_bytes"], 0)

    def test_exact_duplicates_grouped(self):
        """Two files with identical content → one group with count=2."""
        content = b"duplicate content for test"
        (self.root_path / "copy1.txt").write_bytes(content)
        (self.root_path / "copy2.txt").write_bytes(content)
        (self.root_path / "unique.txt").write_bytes(b"different")
        root_id = self.service.add_root(str(self.root_path), "Root")["id"]
        self._wait_scan(root_id)

        result = self.service.get_duplicate_groups(root_id)
        self.assertEqual(result["total_groups"], 1)
        group = result["groups"][0]
        self.assertEqual(group["count"], 2)
        self.assertEqual(group["size_bytes"], len(content))
        self.assertEqual(group["wasted_bytes"], len(content))  # (count-1) * size
        names = {f["name"] for f in group["files"]}
        self.assertEqual(names, {"copy1.txt", "copy2.txt"})

    def test_sha256_correct(self):
        """The sha256 field matches Python's hashlib output."""
        content = b"sha256 verification payload"
        (self.root_path / "f1.dat").write_bytes(content)
        (self.root_path / "f2.dat").write_bytes(content)
        root_id = self.service.add_root(str(self.root_path), "Root")["id"]
        self._wait_scan(root_id)

        result = self.service.get_duplicate_groups(root_id)
        expected_hash = hashlib.sha256(content).hexdigest()
        self.assertEqual(result["groups"][0]["sha256"], expected_hash)

    def test_groups_sorted_by_wasted_bytes(self):
        """Largest wasted-bytes groups appear first."""
        big = b"X" * 1024
        small = b"Y" * 10
        (self.root_path / "big1.bin").write_bytes(big)
        (self.root_path / "big2.bin").write_bytes(big)
        (self.root_path / "small1.bin").write_bytes(small)
        (self.root_path / "small2.bin").write_bytes(small)
        root_id = self.service.add_root(str(self.root_path), "Root")["id"]
        self._wait_scan(root_id)

        result = self.service.get_duplicate_groups(root_id)
        self.assertEqual(result["total_groups"], 2)
        self.assertGreater(
            result["groups"][0]["wasted_bytes"],
            result["groups"][1]["wasted_bytes"],
        )

    def test_demo_mode_returns_empty_groups(self):
        """Demo mode must never read disk for duplicates."""
        demo = FileService(self.store, demo=True)
        result = demo.get_duplicate_groups()
        self.assertEqual(result["groups"], [])
        self.assertEqual(result["total_groups"], 0)

    def test_delete_duplicate_file_requires_confirm(self):
        """Omitting confirm raises ValueError."""
        content = b"need confirm"
        (self.root_path / "d1.txt").write_bytes(content)
        (self.root_path / "d2.txt").write_bytes(content)
        root_id = self.service.add_root(str(self.root_path), "Root")["id"]
        self._wait_scan(root_id)
        file_id = self.service.get_duplicate_groups(root_id)["groups"][0]["files"][0]["id"]
        with self.assertRaisesRegex(ValueError, "confirm=true"):
            self.service.delete_duplicate_file(file_id)

    def test_delete_duplicate_file_success(self):
        """delete_duplicate_file removes one copy and journals it; other copy survives."""
        content = b"identical twins"
        f1 = self.root_path / "twin1.txt"
        f2 = self.root_path / "twin2.txt"
        f1.write_bytes(content)
        f2.write_bytes(content)
        root_id = self.service.add_root(str(self.root_path), "Root")["id"]
        self._wait_scan(root_id)

        group = self.service.get_duplicate_groups(root_id)["groups"][0]
        victim_id = group["files"][0]["id"]
        victim_name = group["files"][0]["name"]
        survivor_name = group["files"][1]["name"]

        result = self.service.delete_duplicate_file(victim_id, confirm=True)
        self.assertTrue(result["deleted"])

        # Exactly one file must remain
        victim = self.root_path / victim_name
        survivor = self.root_path / survivor_name
        self.assertFalse(victim.exists(), f"{victim_name} should be gone")
        self.assertTrue(survivor.exists(), f"{survivor_name} must survive")

        # Check journal
        journal = self.service.get_action_journal()
        self.assertTrue(
            any(a["kind"] == "DELETE" and victim_name in a["source_relative"] for a in journal)
        )

    def test_duplicate_groups_cross_approved_roots(self):
        other_root = Path(self.temp.name) / "other-root"
        other_root.mkdir()
        payload = b"cross-root exact content"
        (self.root_path / "one.bin").write_bytes(payload)
        (other_root / "two.bin").write_bytes(payload)
        first = self.service.add_root(str(self.root_path), "First")["id"]
        second = self.service.add_root(str(other_root), "Second")["id"]
        self._wait_scan(first)
        self._wait_scan(second)
        groups = self.service.get_duplicate_groups()
        self.assertEqual(groups["total_groups"], 1)
        self.assertEqual({file["root_id"] for file in groups["groups"][0]["files"]}, {first, second})

    def test_duplicate_cleanup_ignores_files_inside_project_trees(self):
        project = self.root_path / "project"
        project.mkdir()
        (project / "pyproject.toml").write_text("[project]", encoding="utf-8")
        (project / "copy-a.bin").write_bytes(b"project duplicate")
        (project / "copy-b.bin").write_bytes(b"project duplicate")
        (self.root_path / "general-a.bin").write_bytes(b"general duplicate")
        (self.root_path / "general-b.bin").write_bytes(b"general duplicate")
        root_id = self.service.add_root(str(self.root_path), "Root")["id"]
        self._wait_scan(root_id)

        groups = self.service.get_duplicate_groups(root_id)["groups"]
        self.assertEqual(len(groups), 1)
        self.assertEqual({file["name"] for file in groups[0]["files"]}, {"general-a.bin", "general-b.bin"})

    def test_duplicate_delete_never_removes_last_survivor(self):
        payload = b"one survivor only"
        first = self.root_path / "first.bin"
        second = self.root_path / "second.bin"
        first.write_bytes(payload)
        second.write_bytes(payload)
        root_id = self.service.add_root(str(self.root_path), "Root")["id"]
        self._wait_scan(root_id)
        group = self.service.get_duplicate_groups(root_id)["groups"][0]
        self.service.delete_duplicate_file(group["files"][0]["id"], confirm=True)
        with self.assertRaisesRegex(ValueError, "last survivor|No duplicate"):
            self.service.delete_duplicate_file(group["files"][1]["id"], confirm=True)
        self.assertEqual(sum(path.exists() for path in (first, second)), 1)

    def test_duplicate_content_change_is_rejected_without_deletion(self):
        first = self.root_path / "first.bin"
        second = self.root_path / "second.bin"
        first.write_bytes(b"same-size-a")
        second.write_bytes(b"same-size-a")
        root_id = self.service.add_root(str(self.root_path), "Root")["id"]
        self._wait_scan(root_id)
        victim_id = self.service.get_duplicate_groups(root_id)["groups"][0]["files"][0]["id"]
        first.write_bytes(b"different-x")  # same length, so size-only checks are insufficient
        with self.assertRaisesRegex(ValueError, "No duplicate|last survivor"):
            self.service.delete_duplicate_file(victim_id, confirm=True)
        self.assertTrue(first.exists())

    def test_hash_budget_skips_large_duplicate_candidates(self):
        (self.root_path / "one.bin").write_bytes(b"12345")
        (self.root_path / "two.bin").write_bytes(b"12345")
        root_id = self.service.add_root(str(self.root_path), "Root")["id"]
        self._wait_scan(root_id)
        with patch.object(FileService, "_MAX_HASH_BYTES", 4):
            result = self.service.get_duplicate_groups(root_id)
        self.assertEqual(result["groups"], [])

    def test_delete_duplicate_file_blocked_in_demo(self):
        """Demo mode blocks all deletions."""
        demo = FileService(self.store, demo=True)
        with self.assertRaisesRegex(ValueError, "demo mode"):
            demo.delete_duplicate_file(1, confirm=True)

    def test_file_sha256_static_method(self):
        """_file_sha256 returns None for missing files, correct digest otherwise."""
        f = Path(self.temp.name) / "hashme.bin"
        f.write_bytes(b"data to hash")
        expected = hashlib.sha256(b"data to hash").hexdigest()
        self.assertEqual(FileService._file_sha256(f), expected)
        self.assertIsNone(FileService._file_sha256(Path(self.temp.name) / "nonexistent"))


class TestCleanupHTTP(unittest.TestCase):
    """The destructive endpoints must enforce confirmation at the API edge."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name) / "root"
        root.mkdir()
        (root / "empty.txt").write_bytes(b"")
        (root / "one.txt").write_bytes(b"duplicate")
        (root / "two.txt").write_bytes(b"duplicate")
        self.files = FileService(FileStore(Path(self.temp.name) / "files.db"))
        self.root_id = self.files.add_root(str(root), "Root")["id"]
        while self.files.store.get_root(self.root_id)["status"] == "scanning":
            time.sleep(0.01)
        config = ConfigStore(Path(self.temp.name))
        events = EventStore(Path(self.temp.name) / "events.db")
        self.server = DashboardServer(0, Collector(DemoProbe(), config, events), config, events)
        self.server.file_service = self.files
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=3)
        self.server.server_close()
        if self.files.store.get_roots():
            self.files.delete_all()

    def call(self, path, body, *, token=True):
        with closing(http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)) as connection:
            headers = {
                "Origin": f"http://127.0.0.1:{self.server.server_port}",
                "Content-Type": "application/json",
            }
            if token:
                headers["X-Kshetrajna-Token"] = self.server.token
            connection.request("POST", path, json.dumps(body), headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())

    def test_private_cleanup_get_requires_dashboard_token(self):
        with closing(http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)) as connection:
            connection.request("GET", "/api/files/empty", headers={"Host": f"127.0.0.1:{self.server.server_port}"})
            response = connection.getresponse()
            self.assertEqual(response.status, 403)
            response.read()

    def test_out_of_range_cleanup_ids_return_bad_request_without_mutation(self):
        for endpoint in ("/api/files/empty", "/api/files/duplicates"):
            with closing(http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)) as connection:
                connection.request("GET", endpoint + "?root_id=" + str(2**63), headers={
                    "X-Kshetrajna-Token": self.server.token,
                })
                response = connection.getresponse()
                self.assertEqual(response.status, 400)
                self.assertIn("root_id", json.loads(response.read())["error"])
            status, body = self.call(endpoint + "/delete", {"file_id": 2**63, "confirm": True})
            self.assertEqual(status, 400)
            self.assertIn("file_id", body["error"])
        self.assertEqual(self.files.get_action_journal(), [])

    def test_empty_and_duplicate_delete_require_confirm_true(self):
        empty_id = self.files.get_empty_files(self.root_id)["empty_files"][0]["id"]
        status, body = self.call("/api/files/empty/delete", {"file_id": empty_id})
        self.assertEqual(status, 400)
        self.assertIn("confirm=true", body["error"])
        status, body = self.call("/api/files/empty/delete", {"file_id": empty_id, "confirm": True})
        self.assertEqual(status, 200)
        self.assertTrue(body["deleted"])

        duplicate_id = self.files.get_duplicate_groups(self.root_id)["groups"][0]["files"][0]["id"]
        status, body = self.call("/api/files/duplicates/delete", {"file_id": duplicate_id, "confirm": False})
        self.assertEqual(status, 400)
        self.assertIn("confirm=true", body["error"])

        action_id = self.files.store.search_files("one.txt", self.root_id)[0]["id"]
        status, body = self.call("/api/files/actions/propose", {
            "file_id": action_id, "kind": "DELETE", "destination": "one.txt",
        })
        self.assertEqual(status, 400)
        self.assertIn("confirm=true", body["error"])

    def test_http_bridge_can_preview_and_delete_cleanup_files(self):
        bridge = JarvisHttpClient(f"http://127.0.0.1:{self.server.server_port}")
        preview = bridge.execute("empty_preview", root_id=self.root_id)
        empty_id = preview["empty_files"][0]["id"]
        with self.assertRaisesRegex(ValueError, "confirm=true"):
            bridge.execute("empty_delete", file_id=empty_id)
        result = bridge.execute("cleanup_empty_delete", file_id=empty_id, confirm=True)
        self.assertTrue(result["deleted"])


class TestCliCleanupAliases(unittest.TestCase):
    def test_cleanup_namespace_defaults_and_aliases_to_preview_commands(self):
        with patch.object(cli, "bridge_main", return_value=0) as bridge_main, patch.object(
                cli.sys, "argv", ["kshetrajna-sort", "cleanup", "duplicates", "--root-id", "7"]):
            self.assertEqual(cli.main(), 0)
        bridge_main.assert_called_once_with(["duplicate_preview", "--root-id", "7"])


if __name__ == "__main__":
    unittest.main()
