import unittest
import os
import json
from pathlib import Path
import tempfile
import sqlite3
from kshetrajna.file_storage import FileStore
from kshetrajna.file_service import FileService

class TestFileActionsStep6(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.temp_dir.name) / "root"
        self.root_path.mkdir()
        
        self.db_path = Path(self.temp_dir.name) / "files.db"
        self.store = FileStore(self.db_path)
        self.service = FileService(self.store, demo=False)
        
        self.doc_path = self.root_path / "doc.txt"
        self.doc_path.write_text("hello")
        
        with self.store._connect() as db:
            db.execute("INSERT INTO roots (id, path, created_at, status) VALUES (1, ?, 'now', 'ready')", (str(self.root_path),))
            db.execute("INSERT INTO files (id, root_id, relative_path, name, indexed_at) VALUES (1, 1, 'doc.txt', 'doc.txt', 'now')")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_http_integration(self):
        # propose
        action = self.service.create_proposal(1, "RENAME", "doc2.txt")
        self.assertEqual(action["status"], "proposed")
        
        # get journal
        journal = self.service.get_action_journal()
        self.assertEqual(len(journal), 1)
        self.assertEqual(journal[0]["id"], action["id"])
        
        # dismiss
        self.service.dismiss_proposal(action["proposal_id"])
        self.assertEqual(self.service.get_action_journal()[0]["status"], "dismissed")
        
        # propose again
        action2 = self.service.create_proposal(1, "RENAME", "doc2.txt")
        self.assertEqual(action2["status"], "proposed")
        
        # apply
        self.service.apply_action(action2["proposal_id"])
        self.assertEqual(self.service.get_action_journal()[0]["status"], "applied")
        
        # undo
        self.service.undo_action(action2["id"])
        self.assertEqual(self.service.get_action_journal()[0]["status"], "rolled_back")

    def test_demo_mode_simulation(self):
        demo_service = FileService(self.store, demo=True)
        action = demo_service.create_proposal(1, "RENAME", "doc2.txt")
        self.assertEqual(action["mode"], "demo")
        
        res = demo_service.apply_action(action["proposal_id"])
        self.assertEqual(res["status"], "applied")
        self.assertTrue(self.doc_path.exists()) # Not actually moved
        
        undo_res = demo_service.undo_action(action["id"])
        self.assertEqual(undo_res["status"], "rolled_back")

if __name__ == "__main__":
    unittest.main()
