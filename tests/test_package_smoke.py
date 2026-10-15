"""Exercise the native-package smoke protocol using a disposable demo process."""

import sys
import tempfile
import unittest
from pathlib import Path

from scripts.smoke_windows_bundle import session


class PackageSmokeTests(unittest.TestCase):
    def test_demo_smoke_uses_session_token_and_shuts_down_cleanly(self):
        with tempfile.TemporaryDirectory() as directory:
            result = session(Path(sys.executable), Path(directory) / "unused", demo=True)
        self.assertEqual(result, {
            "mode": "demo", "assets": "passed", "approval_guards": "passed", "shutdown": "passed",
        })


if __name__ == "__main__":
    unittest.main()
