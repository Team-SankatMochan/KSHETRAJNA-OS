import unittest
import os
from pathlib import Path
import tempfile
import sqlite3

from kshetrajna.file_storage import FileStore
from kshetrajna.file_actions import FileActionManager

class TestFileActionsStep5(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.temp_dir.name) / "root"
        self.root_path.mkdir()
        self.folder_path = self.root_path / "folder"
        self.folder_path.mkdir()
        
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

    def test_move_action(self):
        action = self.manager.create_proposal(1, "MOVE", "folder/doc.txt")
        result = self.manager.apply_action(action["proposal_id"])
        
        self.assertEqual(result["status"], "applied")
        self.assertTrue((self.root_path / "folder" / "doc.txt").exists())
        self.assertFalse(self.doc_path.exists())
        
        # undo move
        undo = self.manager.undo_action(action["id"])
        self.assertEqual(undo["status"], "rolled_back")
        self.assertTrue(self.doc_path.exists())
        self.assertFalse((self.root_path / "folder" / "doc.txt").exists())
        
    def test_move_missing_directory(self):
        with self.assertRaisesRegex(ValueError, "Destination directory does not exist"):
            self.manager.create_proposal(1, "MOVE", "missing/doc.txt")

if __name__ == "__main__":
    unittest.main()
