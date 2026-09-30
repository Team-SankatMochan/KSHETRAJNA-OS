import unittest
from datetime import datetime, timezone
from kshetrajna.context_files import surface_contextual_files

class TestContextFiles(unittest.TestCase):
    def test_surfacing_logic(self):
        now = datetime.now(timezone.utc).isoformat()
        
        all_files = [
            {"id": 1, "name": "architecture_notes.txt", "relative_path": "architecture_notes.txt", "excerpt": "Kshetrajna Architecture Notes"},
            {"id": 2, "name": "document(7).txt", "relative_path": "document(7).txt", "excerpt": "Qualcomm Benchmark Notes"},
            {"id": 3, "name": "Recipe Notes", "relative_path": "recipes.txt", "excerpt": "Recipe Notes"},
            {"id": 4, "name": "College Fees", "relative_path": "fees.txt", "excerpt": "College Fees"},
            {"id": 5, "name": "Travel Plans", "relative_path": "travel.txt", "excerpt": "Travel Plans"},
            {"id": 6, "name": "random.txt", "relative_path": "random.txt", "excerpt": "hello", "modified_at": now}
        ]
        
        # disabled or empty catalog
        self.assertEqual(surface_contextual_files("Development", [], None, [], [], {}), [])
        
        dev_results = surface_contextual_files("Development", [], None, [], all_files, {})
        
        # correct relevant-file surfacing
        dev_titles = [r["title"] for r in dev_results]
        self.assertIn("Kshetrajna Architecture Notes", dev_titles)
        self.assertIn("Qualcomm Benchmark Notes", dev_titles)
        
        # unrelated recent files not surfaced
        self.assertNotIn("College Fees", dev_titles)
        self.assertNotIn("Recipe Notes", dev_titles)
        self.assertNotIn("Travel Plans", dev_titles)
        
        # recency alone insufficient
        self.assertNotIn("hello", [r["title"] for r in dev_results])
        self.assertNotIn("random.txt", [r["basename"] for r in dev_results])
        
        # reasons are generated and deterministic ranking
        for r in dev_results:
            self.assertTrue(len(r["reasons"]) > 0)
            self.assertFalse("C:\\" in r.get("basename", ""))
            
        dev_results2 = surface_contextual_files("Development", [], None, [], all_files, {})
        self.assertEqual([r["file_id"] for r in dev_results], [r["file_id"] for r in dev_results2])
        
        # workspace membership boosts relevance
        ws_results = surface_contextual_files("Development", [], {"name": "architecture"}, [], all_files, {})
        arch_res_no_ws = next(r for r in dev_results if r["title"] == "Kshetrajna Architecture Notes")
        arch_res_ws = next(r for r in ws_results if r["title"] == "Kshetrajna Architecture Notes")
        self.assertGreater(arch_res_ws["relevance"], arch_res_no_ws["relevance"])
        
        # Meeting context returns different files
        meeting_results = surface_contextual_files("Meeting", [], None, [], all_files, {})
        meeting_titles = [r["title"] for r in meeting_results]
        self.assertIn("Qualcomm Benchmark Notes", meeting_titles)
        self.assertNotEqual(dev_titles, meeting_titles)

if __name__ == "__main__":
    unittest.main()
