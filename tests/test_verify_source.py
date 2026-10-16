"""Source verification must keep grandchildren on this checkout's source tree."""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import verify_source as verifier


class SourceWorkerEnvironmentTests(unittest.TestCase):
    def check_real_children(self, *, shadow_package):
        with tempfile.TemporaryDirectory(prefix="kshetrajna-verifier-") as temporary:
            launch = Path(temporary) / "outside checkout"
            launch.mkdir()
            env = os.environ.copy()
            env.pop("PYTHONPATH", None)
            private_error = "synthetic_private_import_error"
            if shadow_package:
                package = launch / "wrong source" / "kshetrajna"
                package.mkdir(parents=True)
                (package / "__init__.py").write_text(
                    f"raise RuntimeError({private_error!r})\n", encoding="utf-8",
                )
                # A competing package must never win in the worker or grandchildren.
                env["PYTHONPATH"] = str(package.parent)
            child = verifier.run_process(
                [sys.executable, "-B", str(verifier.ROOT / "scripts/verify_source.py"),
                 "--pattern", "test_action_store.py",
                 "--pattern", "test_package_smoke.py", "--js", "off", "--timeout", "90"],
                cwd=launch, env=env, timeout=105,
            )
            report = json.loads(child.stdout)
            self.assertEqual(child.returncode, 0, report)
            self.assertEqual(report["status"], "passed")
            tests = report["unittest"]
            # The ledger's os._exit child and smoke's -m app must both really pass.
            self.assertEqual(set(tests["module_counts"]), {
                "test_action_store", "test_package_smoke",
            })
            self.assertTrue(all(count > 0 for count in tests["module_counts"].values()))
            self.assertEqual(tests["tests_run"], sum(tests["module_counts"].values()))
            self.assertEqual(tests["discovered"], tests["tests_run"])
            for key in ("failures", "errors", "skipped", "expected_failures", "unexpected_successes"):
                self.assertEqual(tests[key], 0, key)
            self.assertEqual(tests["failed_test_ids"], [])
            self.assertEqual(report["baseline"]["status"], "not_applicable")
            self.assertEqual(child.stderr, b"")
            for private in (str(verifier.ROOT), temporary, private_error, "PYTHONPATH", "Traceback"):
                self.assertNotIn(private, child.stdout.decode())

    def test_crash_children_and_package_session_with_pythonpath_unset(self):
        self.check_real_children(shadow_package=False)

    def test_crash_children_and_package_session_ignore_wrong_pythonpath(self):
        self.check_real_children(shadow_package=True)

    def test_environment_is_scoped_to_worker_and_raw_child_errors_stay_private(self):
        summary = {"status": "passed", "discovered": 1, "tests_run": 1, "module_counts": {}}
        private = "synthetic_private_child_error " + str(verifier.ROOT)
        for stdout, returncode in ((json.dumps(summary).encode(), 0), (private.encode(), 1)):
            output = io.StringIO()
            with self.subTest(returncode=returncode), \
                    patch.dict(os.environ, {"PYTHONPATH": "wrong-caller-source"}), \
                    patch.object(verifier, "environment_metadata", return_value={"versions_match": True}), \
                    patch.object(verifier, "syntax_check", return_value={"status": "passed"}), \
                    patch.object(verifier, "run_process", return_value=subprocess.CompletedProcess(
                        [], returncode, stdout, private.encode())) as spawn, \
                    contextlib.redirect_stdout(output):
                before = dict(os.environ)
                code = verifier.main(["--pattern", "test_control_fixture.py", "--js", "off"])
                self.assertEqual(dict(os.environ), before)
                worker_env = spawn.call_args.kwargs["env"]
                self.assertIsNot(worker_env, os.environ)
                self.assertEqual(worker_env, {**before, "PYTHONPATH": str(verifier.ROOT / "src")})
            self.assertEqual(code, returncode)
            report = json.loads(output.getvalue())
            self.assertEqual(report["status"], "passed" if returncode == 0 else "failed")
            for value in (private, str(verifier.ROOT), "wrong-caller-source", "PYTHONPATH"):
                self.assertNotIn(value, output.getvalue())


if __name__ == "__main__":
    unittest.main()
