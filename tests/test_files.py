import unittest
import tempfile
import threading
import os
import time
import json
import http.client
from zipfile import ZipFile
from contextlib import closing
from pathlib import Path

from kshetrajna.file_storage import FileStore, escape_like
from kshetrajna.file_scanner import walk, extract_excerpt
from kshetrajna.file_insights import generate_insights
from kshetrajna.file_service import FileService
from kshetrajna.server import DashboardServer
from kshetrajna.collector import Collector
from kshetrajna.config import ConfigStore
from kshetrajna.storage import EventStore
from kshetrajna.demo import DemoProbe

class TestFileStorageAndScanner(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = FileStore(Path(self.temp.name) / "files.db")
        self.service = FileService(self.store)

    def tearDown(self):
        self.service.delete_all(confirm=True)
        
    def test_escape_like(self):
        self.assertEqual(escape_like("test_file%"), "test\\_file\\%")

    def test_root_normalization_and_validation(self):
        with self.assertRaisesRegex(ValueError, "Network and UNC paths"):
            self.service.add_root(r"\\server\share", "Net", consent=True)

        with self.assertRaisesRegex(ValueError, "Root drives cannot be scanned"):
            self.service.add_root("C:\\", "Drive", consent=True)

        with self.assertRaisesRegex(ValueError, "System and protected paths"):
            self.service.add_root("C:\\Windows", "Windows", consent=True)

        p = Path(self.temp.name) / "root1"
        p.mkdir()
        r1 = self.service.add_root(str(p), "R1", consent=True)["id"]
        
        with self.assertRaisesRegex(ValueError, "Root already exists"):
            self.service.add_root(str(p), "Dup", consent=True)

        p2 = p / "sub"
        p2.mkdir()
        with self.assertRaisesRegex(ValueError, "descendant of existing root"):
            self.service.add_root(str(p2), "Desc", consent=True)

        with self.assertRaisesRegex(ValueError, "ancestor of existing root"):
            self.service.add_root(str(Path(self.temp.name)), "Anc", consent=True)

    def test_scanner_skips_reparse_points(self):
        if os.name != 'nt':
            self.skipTest("Windows specific reparse point test")
            
        p = Path(self.temp.name) / "root"
        p.mkdir()
        target = Path(self.temp.name) / "target"
        target.mkdir()
        (target / "file.txt").write_text("hello")
        
        try:
            os.symlink(target, p / "link", target_is_directory=True)
        except OSError:
            self.skipTest("Requires Developer Mode or Admin for symlinks")
            
        root_id = self.service.add_root(str(p), "Root", consent=True)["id"]
        self.wait_scan(root_id)
        
        root = self.service.store.get_root(root_id)
        self.assertEqual(root["file_count"], 0)

    def test_excerpt_extraction_bounds_and_binary(self):
        p = Path(self.temp.name) / "test.txt"
        p.write_text("a" * 5000)
        self.assertEqual(len(extract_excerpt(p)), 4096)

        p.write_text("€" * 5000, encoding="utf-8")
        self.assertLessEqual(len(extract_excerpt(p).encode("utf-8")), 4096)

        p.write_bytes(b"hello\0world")
        self.assertEqual(extract_excerpt(p), "")

        p.write_bytes(b"hello\x80world")
        # Should replace invalid UTF-8 but handle it
        self.assertIn("hello", extract_excerpt(p))

    def test_insights_strip_extension_case_insensitively(self):
        insight = generate_insights("REPORT.PY", "py", "REPORT.PY", "")
        self.assertEqual(insight["display_title"], "Report")
        self.assertEqual(insight["suggested_category"], "Code")

    def test_content_insights_explain_data_and_review_flags(self):
        data = generate_insights(
            "logs.csv", "csv", "logs.csv", "name,status\napi,ok\nworker,failed\n", size_bytes=42
        )
        self.assertEqual(data["suggested_category"], "Data")
        self.assertIn("tabular data", data["content_summary"])
        self.assertFalse(data["review_required"])

        placeholder = generate_insights(
            "Untitled-1.c", "c", "DSA/Untitled-1.c", "#include <stdio.h>\nint main() {}", size_bytes=32
        )
        self.assertTrue(placeholder["review_required"])
        self.assertIn("placeholder", placeholder["review_reason"])

        generated = generate_insights("dsa.exe", "exe", "DSA/dsa.exe", "", size_bytes=1024)
        self.assertTrue(generated["review_required"])
        self.assertIn("generated", generated["review_reason"])
        self.assertIn("source file", generated["review_recommendation"])

    def test_content_decision_uses_subject_and_project_evidence(self):
        audit = generate_insights(
            "public_gtm_tracking_audit.json", "json", "rest_clients_discovery/public_gtm_tracking_audit.json",
            '{"placeholder":"example", "tracking_id":"GTM-ABC"}', size_bytes=12000,
        )
        self.assertEqual(audit["topic"], "Tracking Audit")
        self.assertEqual(audit["suggested_folder"], "Projects/Tracking Audit")
        self.assertEqual(audit["recommended_action"], "keep")
        self.assertNotIn("not complete valid JSON", audit["content_summary"])

        study = generate_insights(
            "Course Plan Deep Learning (1).docx", "docx", "Downloads/Course Plan Deep Learning (1).docx",
            "Deep learning course plan\nForward propagation activations\n", size_bytes=12000,
        )
        self.assertEqual(study["topic"], "Deep Learning")
        self.assertEqual(study["suggested_folder"], "Study/Deep Learning")
        self.assertEqual(study["recommended_action"], "organize")

        video = generate_insights(
            "WhatsApp Video 2026-10-08 at 17.02.54 (1).mp4", "mp4", "Downloads/WhatsApp Video 2026-10-08 at 17.02.54 (1).mp4", "", size_bytes=9000,
        )
        self.assertEqual(video["recommended_action"], "keep")
        self.assertEqual(video["suggested_folder"], "Media/WhatsApp")

        cache = generate_insights("audit_site.cpython-312.pyc", "pyc", "__pycache__/audit_site.cpython-312.pyc", "", size_bytes=1000)
        self.assertEqual(cache["recommended_action"], "cleanup")

    def test_file_insights_surface_review_candidates(self):
        p = Path(self.temp.name) / "root"
        p.mkdir()
        (p / "Untitled-1.c").write_text("#include <stdio.h>\nint main() {}", encoding="utf-8")
        (p / "dsa.exe").write_bytes(b"MZ" + b"\0" * 100)
        (p / "metrics.csv").write_text("name,status\napi,ok\n", encoding="utf-8")

        root_id = self.service.add_root(str(p), "Root", consent=True)["id"]
        self.wait_scan(root_id)
        candidates = self.service.get_insights()["review_candidates"]
        names = {candidate["name"] for candidate in candidates}
        self.assertIn("Untitled-1.c", names)
        self.assertIn("dsa.exe", names)
        self.assertNotIn("metrics.csv", names)

    def test_office_preview_extracts_bounded_document_text_on_review(self):
        p = Path(self.temp.name) / "root"
        p.mkdir()
        document = p / "Course Plan Deep Learning (1).docx"
        with ZipFile(document, "w") as archive:
            archive.writestr(
                "word/document.xml",
                "<w:document><w:body><w:p><w:r><w:t>Deep learning course plan</w:t></w:r></w:p></w:body></w:document>",
            )

        root_id = self.service.add_root(str(p), "Root", consent=True)["id"]
        self.wait_scan(root_id)
        file_id = self.service.store.get_recent_files(root_id, 10)[0]["id"]
        preview = self.service.get_file_excerpt(file_id)
        self.assertIn("Deep learning course plan", preview["excerpt"])
        self.assertIn("Office document", preview["file"]["insights"]["content_summary"])

    def test_sensitive_files(self):
        p = Path(self.temp.name) / "root"
        p.mkdir()
        (p / ".env").write_text("secret=1")
        (p / "id_rsa").write_text("key")
        (p / "test.pem").write_text("cert")
        
        git = p / ".git"
        git.mkdir()
        (git / "config").write_text("gitconfig")

        root_id = self.service.add_root(str(p), "Root", consent=True)["id"]
        self.wait_scan(root_id)

        files = self.service.store.get_recent_files(root_id, 10)
        for f in files:
            file_data = self.service.get_file_excerpt(f["id"])
            self.assertEqual(file_data["excerpt"], "")

    def test_incremental_scan(self):
        p = Path(self.temp.name) / "root"
        p.mkdir()
        f1 = p / "f1.txt"
        f1.write_text("1")
        
        root_id = self.service.add_root(str(p), "Root", consent=True)["id"]
        self.wait_scan(root_id)
        
        with self.service.store._connect() as db:
            state = dict(db.execute("SELECT * FROM scan_state WHERE root_id=?", (root_id,)).fetchone())
        self.assertEqual(state["files_seen"], 1)
        self.assertEqual(state["files_new"], 1)

        f1.write_text("12")
        f2 = p / "f2.txt"
        f2.write_text("2")

        self.service.rescan_root(root_id)
        self.wait_scan(root_id)

        with self.service.store._connect() as db:
            state = dict(db.execute("SELECT * FROM scan_state WHERE root_id=?", (root_id,)).fetchone())
        self.assertEqual(state["files_seen"], 2)
        self.assertEqual(state["files_changed"], 1)
        self.assertEqual(state["files_new"], 1)

        f1.unlink()
        self.service.rescan_root(root_id)
        self.wait_scan(root_id)
        
        with self.service.store._connect() as db:
            state = dict(db.execute("SELECT * FROM scan_state WHERE root_id=?", (root_id,)).fetchone())
        self.assertEqual(state["files_removed"], 1)
        self.assertEqual(state["files_seen"], 1)

    def test_search_and_sql_injection(self):
        p = Path(self.temp.name) / "root"
        p.mkdir()
        (p / "target_100%.txt").write_text("hello _world")
        
        root_id = self.service.add_root(str(p), "Root", consent=True)["id"]
        self.wait_scan(root_id)

        result = self.service.search("target_100%").get("results")
        self.assertEqual(len(result), 1)
        self.assertEqual(Path(result[0]["path"]).resolve(), (p / "target_100%.txt").resolve())
        self.assertEqual(len(self.service.search("_world").get("results")), 1)
        self.assertEqual(len(self.service.search("'; DROP TABLE files; --").get("results")), 0)

    def test_cancellation_and_concurrent(self):
        p = Path(self.temp.name) / "root"
        p.mkdir()
        for i in range(100):
            (p / f"{i}.txt").write_text("1")

        root_id = self.service.store.add_root(str(p), "Root")
        self.service.rescan_root(root_id)
        
        with self.assertRaisesRegex(ValueError, "already in progress"):
            self.service.rescan_root(root_id)
            
        self.service.cancel_scan(root_id)
        self.wait_scan(root_id)

        root = self.service.store.get_root(root_id)
        self.assertEqual(root["status"], "cancelled")

    def test_root_removal_cascade(self):
        p = Path(self.temp.name) / "root"
        p.mkdir()
        (p / "f.txt").write_text("1")
        root_id = self.service.add_root(str(p), "R", consent=True)["id"]
        self.wait_scan(root_id)
        self.assertEqual(self.service.store.get_stats()["total_files"], 1)
        
        self.service.remove_root(root_id)
        time.sleep(0.2)
        self.assertEqual(self.service.store.get_stats()["total_files"], 0)

    def test_demo_mode_does_not_touch_fs(self):
        service = FileService(self.store, demo=True)
        with self.assertRaisesRegex(ValueError, "Roots cannot be added"):
            service.add_root("C:\\Fake", "Fake", consent=True)
        
        from kshetrajna.file_demo import seed_file_demo
        seed_file_demo(self.store)
        
        state = service.state()
        self.assertEqual(len(state["roots"]), 2)
        
        res = service.search("Qualcomm")
        self.assertEqual(len(res["results"]), 1)

    def wait_scan(self, root_id):
        while True:
            root = self.service.store.get_root(root_id)
            if root["status"] in ("ready", "error", "cancelled", "truncated", "removed"):
                break
            time.sleep(0.1)

class TestFileHTTP(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        
        self.config = ConfigStore(Path(self.temp.name))
        self.store = EventStore(Path(self.temp.name) / "events.db")
        self.probe = DemoProbe()
        self.collector = Collector(self.probe, self.config, self.store)
        
        self.file_store = FileStore(Path(self.temp.name) / "files.db")
        self.file_service = FileService(self.file_store, demo=True)
        from kshetrajna.file_demo import seed_file_demo
        seed_file_demo(self.file_store)
        
        self.server = DashboardServer(0, self.collector, self.config, self.store, demo=True)
        self.server.file_service = self.file_service # Patched in server later, simulating it here
        
        # We also need to patch the server to actually handle /api/files endpoints
        # Let's just test that the isolation works and rely on the handlers we will add.

    def tearDown(self):
        self.server.server_close()
        self.file_service.delete_all(confirm=True)
        
    def test_database_isolation(self):
        from kshetrajna.demo import seed_demo
        seed_demo(self.store, self.probe)
        
        self.file_service.delete_all(confirm=True)
        self.assertEqual(len(self.file_store.get_roots()), 0)
        self.assertIsNotNone(self.store.latest()) # demo seed populates this
        
        from kshetrajna.file_demo import seed_file_demo
        seed_file_demo(self.file_store)
        
        self.collector.delete_history(confirm=True)
        self.assertIsNone(self.store.latest())
        self.assertEqual(len(self.file_store.get_roots()), 2)

if __name__ == "__main__":
    unittest.main()
