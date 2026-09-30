import unittest
import os
from pathlib import Path
import tempfile
import sqlite3

from kshetrajna.file_storage import FileStore
from kshetrajna.file_actions import FileActionManager

class TestFileActionsStep4(unittest.TestCase):
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

    def test_undo_action(self):
        action = self.manager.create_proposal(1, "RENAME", "doc2.txt")
        self.manager.apply_action(action["proposal_id"])
        
        result = self.manager.undo_action(action["id"])
        self.assertEqual(result["status"], "rolled_back")
        self.assertTrue(self.doc_path.exists())
        self.assertFalse((self.root_path / "doc2.txt").exists())
        
        updated = self.store.get_file_action(action_id=action["id"])
        self.assertEqual(updated["status"], "rolled_back")

    def test_undo_occupied_path(self):
        action = self.manager.create_proposal(1, "RENAME", "doc2.txt")
        self.manager.apply_action(action["proposal_id"])
        
        self.doc_path.write_text("occupied")
        
        with self.assertRaisesRegex(ValueError, "occupied"):
            self.manager.undo_action(action["id"])
            
    def test_undo_identity_mismatch(self):
        action = self.manager.create_proposal(1, "RENAME", "doc2.txt")
        self.manager.apply_action(action["proposal_id"])
        
        (self.root_path / "doc2.txt").write_text("modified")
        
        with self.assertRaisesRegex(ValueError, "Destination file size changed"):
            self.manager.undo_action(action["id"])

if __name__ == "__main__":
    unittest.main()
