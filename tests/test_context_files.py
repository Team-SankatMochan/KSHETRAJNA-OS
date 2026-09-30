import unittest
from datetime import datetime, timezone, timedelta
from kshetrajna.context_files import surface_contextual_files
from kshetrajna.file_storage import FileStore
import tempfile
from pathlib import Path

class TestContextFiles(unittest.TestCase):
    def test_surfacing_logic(self):
        now = datetime.now(timezone.utc).isoformat()
        old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        
        all_files = [
            {"id": 1, "name": "architecture_notes.txt", "relative_path": "architecture_notes.txt", "excerpt": "Kshetrajna Architecture Notes", "modified_at": old},
            {"id": 2, "name": "document(7).txt", "relative_path": "document(7).txt", "excerpt": "Qualcomm Benchmark Notes", "modified_at": old},
            {"id": 3, "name": "Recipe Notes", "relative_path": "recipes.txt", "excerpt": "Recipe Notes", "modified_at": old},
            {"id": 4, "name": "College Fees", "relative_path": "fees.txt", "excerpt": "College Fees", "modified_at": old},
            {"id": 5, "name": "Travel Plans", "relative_path": "travel.txt", "excerpt": "Travel Plans", "modified_at": old},
            {"id": 6, "name": "random.txt", "relative_path": "random.txt", "excerpt": "hello", "modified_at": now},
            {"id": 7, "name": "architecture.txt", "relative_path": "architecture.txt", "excerpt": "Just a single token architecture", "modified_at": old}
        ]
        
        # empty catalog
        self.assertEqual(surface_contextual_files("Development", None, [], []), [])
        
        dev_results = surface_contextual_files("Development", None, [], all_files)
        
        # correct relevant-file surfacing
        dev_titles = [r["title"] for r in dev_results]
        self.assertIn("Kshetrajna Architecture Notes", dev_titles)
        self.assertIn("Qualcomm Benchmark Notes", dev_titles)
        
        # unrelated recent files not surfaced
        self.assertNotIn("College Fees", dev_titles)
        self.assertNotIn("Recipe Notes", dev_titles)
        self.assertNotIn("Travel Plans", dev_titles)
        
        # recency alone insufficient
        self.assertNotIn("hello", dev_titles)
        
        # single token "architecture" does NOT surface without secondary evidence (like recency or another tag)
        self.assertNotIn("Just a single token architecture", dev_titles)
        
        # stable tie ordering
        res1 = surface_contextual_files("Development", None, [], all_files)
        res2 = surface_contextual_files("Development", None, [], all_files)
        self.assertEqual([r["file_id"] for r in res1], [r["file_id"] for r in res2])
        # assert order is deterministic: relevance DESC, id ASC
        for i in range(len(res1) - 1):
            if res1[i]["relevance"] == res1[i+1]["relevance"]:
                self.assertLess(res1[i]["file_id"], res1[i+1]["file_id"])
            else:
                self.assertGreater(res1[i]["relevance"], res1[i+1]["relevance"])
                
        # 1000 candidate files still return <= 8
        large_catalog = [{"id": i, "name": f"kshetrajna_architecture_{i}.txt", "relative_path": "", "excerpt": "", "modified_at": now} for i in range(10, 1010)]
        large_results = surface_contextual_files("Development", None, [], large_catalog)
        self.assertLessEqual(len(large_results), 8)

    def test_candidate_query_is_bounded(self):
        with tempfile.TemporaryDirectory() as d:
            store = FileStore(Path(d) / "files.db")
            now = datetime.now(timezone.utc).isoformat()
            with store._connect() as db:
                db.execute("INSERT INTO roots (id, path, label, created_at, status) VALUES (1, 'C:\\', 'Root', ?, 'ready')", (now,))
                for i in range(500):
                    db.execute("INSERT INTO files (root_id, relative_path, name, indexed_at) VALUES (1, ?, ?, ?)", (f"file{i}", f"file{i}", now))
            
            candidates = store.get_candidate_files()
            self.assertLessEqual(len(candidates), 250)

if __name__ == "__main__":
    unittest.main()
