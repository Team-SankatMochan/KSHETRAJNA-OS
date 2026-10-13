import unittest
from pathlib import Path
import tempfile
import sqlite3
from datetime import datetime, timezone

from kshetrajna.file_storage import FileStore
from kshetrajna.file_actions import FileActionManager

class TestFileActionsStep1(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.temp_dir.name) / "root"
        self.root_path.mkdir()
        self.folder_path = self.root_path / "folder"
        self.folder_path.mkdir()
        
        self.db_path = Path(self.temp_dir.name) / "files.db"
        self.store = FileStore(self.db_path)
        self.manager = FileActionManager(self.store)
        
        with self.store._connect() as db:
            db.execute("INSERT INTO roots (id, path, created_at, status) VALUES (1, ?, 'now', 'ready')", (str(self.root_path),))
            db.execute("INSERT INTO files (id, root_id, relative_path, name, indexed_at) VALUES (1, 1, 'doc.txt', 'doc.txt', 'now')")
            
    def tearDown(self):
        self.temp_dir.cleanup()

    def test_proposals(self):
        # opaque proposal ID
        action = self.manager.create_proposal(1, "RENAME", "doc2.txt")
        self.assertIsNotNone(action)
        self.assertTrue(len(action["id"]) >= 16)
        self.assertTrue(len(action["proposal_id"]) >= 16)
        self.assertEqual(action["status"], "proposed")
        
        # expiry
        created = datetime.fromisoformat(action["created_at"])
        expires = datetime.fromisoformat(action["expires_at"])
        self.assertEqual((expires - created).total_seconds(), 300)
        
        # dismissed proposal
        self.manager.dismiss_proposal(action["proposal_id"])
        updated = self.store.get_file_action(action_id=action["id"])
        self.assertEqual(updated["status"], "dismissed")
        
        # invalid file_id
        with self.assertRaises(ValueError):
            self.manager.create_proposal(999, "RENAME", "doc2.txt")
            
        delete = self.manager.create_proposal(1, "DELETE", "doc.txt")
        self.assertEqual(delete["destination_relative"], "doc.txt")

    def test_rename_validation(self):
        # successful rename
        action = self.manager.create_proposal(1, "RENAME", "doc2.txt")
        self.assertEqual(action["destination_relative"], "doc2.txt")
        
        # extension change rejected
        with self.assertRaisesRegex(ValueError, "Extension change is not allowed"):
            self.manager.create_proposal(1, "RENAME", "doc.md")
            
        # invalid characters
        with self.assertRaisesRegex(ValueError, "Invalid filename"):
            self.manager.create_proposal(1, "RENAME", "doc<2>.txt")
            
        # reserved names
        with self.assertRaisesRegex(ValueError, "Invalid filename"):
            self.manager.create_proposal(1, "RENAME", "CON.txt")
        with self.assertRaisesRegex(ValueError, "Invalid filename"):
            self.manager.create_proposal(1, "RENAME", "nul.txt")
            
        # trailing spaces/dots
        with self.assertRaisesRegex(ValueError, "Invalid filename"):
            self.manager.create_proposal(1, "RENAME", "doc2.txt ")
        with self.assertRaisesRegex(ValueError, "Invalid filename"):
            self.manager.create_proposal(1, "RENAME", "doc2.txt.")
            
    def test_move_validation(self):
        # successful move
        action = self.manager.create_proposal(1, "MOVE", "folder/doc.txt")
        self.assertEqual(action["destination_relative"], "folder/doc.txt")
        
        # traversal rejected
        with self.assertRaisesRegex(ValueError, "Invalid destination path"):
            self.manager.create_proposal(1, "MOVE", "../doc.txt")
            
        # absolute path rejected
        with self.assertRaisesRegex(ValueError, "Invalid destination path"):
            self.manager.create_proposal(1, "MOVE", "/doc.txt")
        with self.assertRaisesRegex(ValueError, "Invalid destination path"):
            self.manager.create_proposal(1, "MOVE", "C:\\doc.txt")

    def test_delete_is_explicitly_journaled_and_not_undoable(self):
        source = self.root_path / "doc.txt"
        source.write_text("remove only after review", encoding="utf-8")
        action = self.manager.create_proposal(1, "DELETE", "doc.txt")
        result = self.manager.apply_action(action["proposal_id"])
        self.assertEqual(result["status"], "deleted")
        self.assertFalse(source.exists())
        self.assertIsNone(self.store.get_file(1))
        self.assertEqual(self.store.get_file_action(action_id=action["id"])["status"], "deleted")
        with self.assertRaisesRegex(ValueError, "status deleted"):
            self.manager.undo_action(action["id"])

if __name__ == "__main__":
    unittest.main()
