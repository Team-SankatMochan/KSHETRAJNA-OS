import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from kshetrajna.maintenance import WindowsMaintenanceDriver, inspect_temp_files


class TemporaryMaintenanceTests(unittest.TestCase):
    def test_preview_is_bounded_and_skips_recent_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old_file = root / "old.tmp"
            recent_file = root / "recent.tmp"
            old_file.write_text("old", encoding="utf-8")
            recent_file.write_text("recent", encoding="utf-8")
            old_time = time.time() - (25 * 60 * 60)
            import os
            os.utime(old_file, (old_time, old_time))
            with patch("kshetrajna.maintenance.tempfile.gettempdir", return_value=directory):
                candidates = inspect_temp_files()
            self.assertEqual([candidate["name"] for candidate in candidates], ["old.tmp"])

    def test_cleanup_rejects_traversal_and_changed_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.tmp"
            target.write_text("before", encoding="utf-8")
            info = target.stat()
            target.write_text("changed", encoding="utf-8")
            with patch("kshetrajna.maintenance.tempfile.gettempdir", return_value=directory):
                result = WindowsMaintenanceDriver().cleanup_temp([
                    {"relative_path": "target.tmp", "name": "target.tmp", "size_bytes": info.st_size, "mtime_ns": info.st_mtime_ns},
                    {"relative_path": "../outside.tmp", "name": "outside.tmp", "size_bytes": 1, "mtime_ns": 1},
                ])
            self.assertEqual(result["status"], "completed")
            self.assertEqual(len(result["deleted"]), 0)
            self.assertEqual(len(result["skipped"]), 2)
            self.assertTrue(target.exists())


if __name__ == "__main__":
    unittest.main()
