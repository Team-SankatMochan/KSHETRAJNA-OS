import unittest
import os
import time
from pathlib import Path
import tempfile
import sqlite3
from datetime import datetime, timezone, timedelta

from kshetrajna.file_storage import FileStore
from kshetrajna.file_actions import FileActionManager

class TestFileActionsStep3(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.temp_dir.name) / "root"
        self.root_path.mkdir()
        
        self.db_path = Path(self.temp_dir.name) / "files.db"
        self.store = FileStore(self.db_path)
        self.manager = FileActionManager(self.store)
        
        self.doc_path = self.root_path / "doc.txt"
        self.doc_path.write_text("hello")
        
        with self.store._connect() as db:
            db.execute("INSERT INTO roots (id, path, created_at, status) VALUES (1, ?, 'now', 'ready')", (str(self.root_path),))
            db.execute("INSERT INTO files (id, root_id, relative_path, name, indexed_at) VALUES (1, 1, 'doc.txt', 'doc.txt', 'now')")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_apply_action(self):
        action = self.manager.create_proposal(1, "RENAME", "doc2.txt")
        result = self.manager.apply_action(action["proposal_id"])
        
        self.assertEqual(result["status"], "applied")
        self.assertFalse(self.doc_path.exists())
        self.assertTrue((self.root_path / "doc2.txt").exists())
        
        # Verify post-identity
        updated_action = self.store.get_file_action(action_id=action["id"])
        self.assertEqual(updated_action["status"], "applied")
        self.assertIsNotNone(updated_action["post_size"])
        
        # Verify catalog updated
        with self.store._connect() as db:
            row = db.execute("SELECT name, relative_path FROM files WHERE id=1").fetchone()
            self.assertEqual(row["name"], "doc2.txt")
            self.assertEqual(row["relative_path"], "doc2.txt")

    def test_identity_mismatch_prevents_apply(self):
        action = self.manager.create_proposal(1, "RENAME", "doc2.txt")
        # modify file size
        self.doc_path.write_text("hello world")
        
        with self.assertRaisesRegex(ValueError, "Source file size changed"):
            self.manager.apply_action(action["proposal_id"])
            
        updated = self.store.get_file_action(action_id=action["id"])
        self.assertNotEqual(updated["status"], "prepared") # failed earlier

    def test_recovery(self):
        action = self.manager.create_proposal(1, "RENAME", "doc2.txt")
        # manually set to prepared
        self.store.update_file_action(action["id"], {"status": "prepared"})
        
        # Case 1: source exists, dest missing -> failed
        self.manager.reconcile_actions()
        self.assertEqual(self.store.get_file_action(action_id=action["id"])["status"], "failed")
        
        # Reset
        self.store.update_file_action(action["id"], {"status": "prepared"})
        # Case 2: source missing, dest exists -> applied (needs matching size)
        self.doc_path.rename(self.root_path / "doc2.txt")
        self.manager.reconcile_actions()
        self.assertEqual(self.store.get_file_action(action_id=action["id"])["status"], "applied")
        
        # Reset
        self.store.update_file_action(action["id"], {"status": "prepared"})
        # Case 3: both exist -> conflict
        self.doc_path.write_text("hello")
        self.manager.reconcile_actions()
        self.assertEqual(self.store.get_file_action(action_id=action["id"])["status"], "conflict")
        
        # Reset
        self.store.update_file_action(action["id"], {"status": "prepared"})
        # Case 4: neither exist -> conflict
        self.doc_path.unlink()
        (self.root_path / "doc2.txt").unlink()
        self.manager.reconcile_actions()
        self.assertEqual(self.store.get_file_action(action_id=action["id"])["status"], "conflict")

if __name__ == "__main__":
    unittest.main()
