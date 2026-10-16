"""Tool contracts, including real small-fixture measurements and failure exits."""

import contextlib
import io
import json
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import benchmark_core as benchmark
from scripts import verify_source as verifier


class BenchmarkStatisticsTests(unittest.TestCase):
    def test_median_and_nearest_rank_p95(self):
        result = benchmark.summarize(range(1, 21))
        self.assertEqual(result["samples"], 20)
        self.assertEqual(result["median_seconds"], 10.5)
        self.assertEqual(result["p95_seconds"], 19)
        self.assertEqual(result["min_seconds"], 1)
        self.assertEqual(result["max_seconds"], 20)

    def test_small_samples_are_not_fabricated_percentile_estimates(self):
        self.assertEqual(benchmark.summarize([0.4])["p95_seconds"], 0.4)
        result = benchmark.summarize([3, 1])
        self.assertEqual(result["median_seconds"], 2)
        self.assertEqual(result["p95_seconds"], 3)

    def test_invalid_samples_are_rejected(self):
        for values in ([], [-1], [math.nan], [math.inf], [-math.inf]):
            with self.subTest(values=values), self.assertRaises(ValueError):
                benchmark.summarize(values)

    def test_configured_bounds_are_enforced_at_argument_boundary(self):
        for arguments in (
            ["--counts", "1"], ["--counts", "200001"], ["--counts", "not-a-number"],
            ["--repeats", "0"], ["--repeats", "11"], ["--actions", "0"],
            ["--actions", "21"], ["--timeout", "0"], ["--timeout", "3601"],
        ):
            with self.subTest(arguments=arguments), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    benchmark.parser().parse_args(arguments)
                self.assertEqual(error.exception.code, 2)
        args = benchmark.parser().parse_args([])
        self.assertEqual(args.counts, [10_000, 50_000, 200_000])
        self.assertFalse(args.run)


class BenchmarkFixtureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)

    def test_fixture_has_exact_count_one_empty_target_and_sharded_synthetic_text(self):
        root = self.base / "fixture"
        benchmark.create_fixture(root, 1002)
        files = [path for path in root.rglob("*") if path.is_file()]
        self.assertEqual(len(files), 1002)
        self.assertEqual([path.name for path in files if path.stat().st_size == 0], ["action-target.txt"])
        self.assertEqual(len(list((root / "batch-0000").iterdir())), 999)
        self.assertEqual(len(list((root / "batch-0001").iterdir())), 2)
        self.assertEqual((root / "batch-0001/sample-00001001.txt").read_bytes(),
                         b"Synthetic offline fixture 00001001\n")

    def test_existing_directory_is_not_adopted_or_cleared(self):
        root = self.base / "existing"
        root.mkdir()
        sentinel = root / "sentinel.bin"
        sentinel.write_bytes(b"preserve synthetic sentinel")
        with self.assertRaises(FileExistsError):
            benchmark.create_fixture(root, 8)
        self.assertEqual(sentinel.read_bytes(), b"preserve synthetic sentinel")
        self.assertEqual(len(list(root.iterdir())), 1)

    def test_programmatic_fixture_bounds_fail_before_creating_directory(self):
        for count in (True, 1, 200001, 2.5):
            with self.subTest(count=count), self.assertRaises(ValueError):
                benchmark.create_fixture(self.base / "never-created", count)
            self.assertFalse((self.base / "never-created").exists())

    def test_report_output_cannot_overwrite_existing_file(self):
        path = self.base / "existing.json"
        path.write_text("preserve report", encoding="utf-8")
        with self.assertRaises(FileExistsError), contextlib.redirect_stdout(io.StringIO()):
            benchmark.write_report({"schema": 1}, path)
        self.assertEqual(path.read_text(encoding="utf-8"), "preserve report")


class BenchmarkProcessTests(unittest.TestCase):
    def test_no_run_is_explicitly_not_measured_and_does_not_spawn_measurement_worker(self):
        with patch.object(benchmark, "environment_metadata", return_value={}), \
                patch.object(benchmark, "run_process") as spawn:
            report = benchmark.run_benchmarks(benchmark.parser().parse_args([]))
        spawn.assert_not_called()
        self.assertEqual(report["status"], "not_run")
        self.assertEqual(report["results"], [])
        self.assertIn("--run", report["reason"])

    def test_duplicate_or_excessive_sizes_are_not_accepted(self):
        for arguments in (["--counts", "2", "2"], ["--counts", "2", "3", "4", "5"]):
            with self.subTest(arguments=arguments), self.assertRaises(benchmark.MeasurementError):
                benchmark.run_benchmarks(benchmark.parser().parse_args(arguments))

    def test_nonzero_and_timeout_are_real_process_failures(self):
        child = benchmark.run_process([sys.executable, "-B", "-c", "raise SystemExit(7)"], timeout=10)
        self.assertEqual(child.returncode, 7)
        with self.assertRaisesRegex(benchmark.MeasurementError, "timed out"):
            benchmark.run_process([sys.executable, "-B", "-c", "import time; time.sleep(60)"], timeout=0.1)

    def test_worker_failure_cannot_be_reported_as_measurement_success(self):
        args = benchmark.parser().parse_args(["--run", "--counts", "8", "--repeats", "1"])
        for failure in (subprocess.CompletedProcess([], 7, b"private raw failure", b"private path"),
                        subprocess.CompletedProcess([], 0, b"not json", b"")):
            with self.subTest(returncode=failure.returncode), \
                    patch.object(benchmark, "environment_metadata", return_value={}), \
                    patch.object(benchmark, "run_process", return_value=failure):
                report = benchmark.run_benchmarks(args)
            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["results"][0]["completed_repeats"], 0)
            self.assertNotIn("private", json.dumps(report))

    def test_real_small_benchmark_cli_reports_all_operations_without_private_paths(self):
        child = benchmark.run_process(
            [sys.executable, "-B", str(benchmark.ROOT / "scripts/benchmark_core.py"), "--run",
             "--counts", "8", "--repeats", "2", "--actions", "2", "--timeout", "30"], timeout=75,
        )
        self.assertEqual(child.returncode, 0, "small disposable benchmark failed")
        report = json.loads(child.stdout)
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["configuration"]["counts"], [8])
        result = report["results"][0]
        self.assertEqual(result["completed_repeats"], 2)
        expected = {"scan_initial", "search_hit", "search_miss", "quarantine", "restore", "scan_cancel_controlled"}
        self.assertEqual(set(result["timings"]), expected)
        for name, timing in result["timings"].items():
            self.assertEqual(timing["samples"], 2 if name.startswith("scan_") else 4)
            self.assertGreaterEqual(timing["min_seconds"], 0)
            self.assertGreaterEqual(timing["p95_seconds"], timing["median_seconds"])
        self.assertIn(result["rss"]["status"], ("available", "unavailable"))
        self.assertTrue(report["environment"]["versions_match"])
        self.assertNotIn(str(benchmark.ROOT), child.stdout.decode())
        self.assertNotIn(tempfile.gettempdir() + "/", child.stdout.decode())
        self.assertNotIn("excerpt", child.stdout.decode())
        self.assertEqual(report["scope"]["package_native_matrix"], "not_run")
        self.assertEqual(report["scope"]["throughput_targets"], "not_evaluated")


class SourceVerificationTests(unittest.TestCase):
    def test_baseline_includes_42_quarantine_and_14_lifecycle_not_just_total(self):
        tests = {"discovered": 276, "module_counts": {"test_quarantine": 42, "test_scan_lifecycle": 14}}
        self.assertEqual(verifier.baseline_check(tests, ["test*.py"])["status"], "passed")
        for module in ("test_quarantine", "test_scan_lifecycle"):
            changed = {"discovered": 300, "module_counts": dict(tests["module_counts"])}
            changed["module_counts"][module] -= 1
            self.assertEqual(verifier.baseline_check(changed, ["test*.py"])["status"], "failed")
        self.assertEqual(verifier.baseline_check(tests, ["test_action_chaos.py"])["status"], "not_applicable")

    def test_syntax_failure_reports_relative_source_filename_not_source_or_private_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "src").mkdir()
            (root / "src/broken.py").write_text("if (private_synthetic_literal:\n", encoding="utf-8")
            report = verifier.syntax_check(root)
            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["files_checked"], 1)
            self.assertEqual(report["failed_files"], ["src/broken.py"])
            self.assertNotIn(temporary, json.dumps(report))
            self.assertNotIn("private_synthetic_literal", json.dumps(report))
            self.assertFalse(list(root.rglob("*.pyc")))

    def test_missing_node_is_a_visible_skip_and_never_runs_a_check(self):
        with patch.object(verifier.shutil, "which", return_value=None), \
                patch.object(verifier, "run_process") as spawn:
            report = verifier.javascript_check()
        spawn.assert_not_called()
        self.assertEqual(report["status"], "skipped")
        self.assertEqual(report["files_checked"], 0)
        self.assertGreater(report["files_discovered"], 0)

    def test_javascript_error_is_failure_not_optional_skip_and_discards_raw_diagnostics(self):
        with patch.object(verifier.shutil, "which", return_value="node"), \
                patch.object(verifier, "run_process", return_value=subprocess.CompletedProcess(
                    [], 1, b"", b"private raw JavaScript diagnostics")) as spawn:
            report = verifier.javascript_check()
        self.assertEqual(report["status"], "failed")
        self.assertEqual(spawn.call_count, report["files_discovered"] + 1)  # version probe + checks
        self.assertEqual(len(report["failed_files"]), report["files_discovered"])
        self.assertIsNone(report["node_version"])
        self.assertNotIn("private", json.dumps(report))

    def test_required_js_changes_missing_node_from_visible_skip_to_failed_gate(self):
        summary = {"status": "passed", "discovered": 1, "tests_run": 1, "module_counts": {}}
        for mode, status, exit_code in (("auto", "skipped", 0), ("required", "failed", 1)):
            output = io.StringIO()
            with self.subTest(mode=mode), \
                    patch.object(verifier, "environment_metadata", return_value={"versions_match": True}), \
                    patch.object(verifier, "syntax_check", return_value={"status": "passed"}), \
                    patch.object(verifier, "run_process", return_value=subprocess.CompletedProcess(
                        [], 0, json.dumps(summary).encode(), b"")), \
                    patch.object(verifier, "javascript_check", return_value={"status": "skipped", "reason": "node absent"}), \
                    contextlib.redirect_stdout(output):
                code = verifier.main(["--pattern", "test_control_fixture.py", "--js", mode])
            report = json.loads(output.getvalue())
            self.assertEqual(code, exit_code)
            self.assertEqual(report["javascript"]["status"], status)
            self.assertEqual(report["status"], "passed" if exit_code == 0 else "failed")

    def test_failure_subtest_error_and_unexpected_success_keep_real_unittest_accounting(self):
        class SyntheticCase(unittest.TestCase):
            def test_failure(self):
                self.fail("private assertion value")

            def test_subtest(self):
                with self.subTest(value="private parameter"):
                    self.assertEqual(1, 2)

            def test_error(self):
                raise RuntimeError("private exception")

            @unittest.expectedFailure
            def test_unexpected_success(self):
                self.assertTrue(True)

        result = verifier.SummaryResult()
        unittest.defaultTestLoader.loadTestsFromTestCase(SyntheticCase).run(result)
        self.assertFalse(result.wasSuccessful())
        self.assertEqual(result.testsRun, 4)
        self.assertEqual(len(result.failures), 2)
        self.assertEqual(len(result.errors), 1)
        self.assertEqual(len(result.unexpectedSuccesses), 1)
        self.assertNotIn("private", json.dumps(sorted(result.failed_ids)))

    def test_zero_test_selection_fails_in_real_verifier_child(self):
        child = benchmark.run_process(
            [sys.executable, "-B", str(benchmark.ROOT / "scripts/verify_source.py"), "--pattern",
             "test_nonexistent_verification_fixture.py", "--js", "off", "--timeout", "30"], timeout=45,
        )
        self.assertEqual(child.returncode, 1)
        report = json.loads(child.stdout)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["unittest"]["discovered"], 0)
        self.assertEqual(report["unittest"]["tests_run"], 0)
        self.assertEqual(report["javascript"]["status"], "skipped")
        self.assertEqual(report["baseline"]["status"], "not_applicable")

    def test_real_focused_verifier_preserves_module_counts_and_scope_labels(self):
        child = benchmark.run_process(
            [sys.executable, "-B", str(benchmark.ROOT / "scripts/verify_source.py"), "--pattern",
             "test_scan_lifecycle.py", "--pattern", "test_scan_lifecycle.py", "--js", "off",
             "--timeout", "60"], timeout=75,
        )
        self.assertEqual(child.returncode, 0, "focused source verifier failed")
        report = json.loads(child.stdout)
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["unittest"]["tests_run"], 14)
        self.assertEqual(report["unittest"]["module_counts"], {"test_scan_lifecycle": 14})
        self.assertEqual(report["baseline"]["status"], "not_applicable")
        self.assertEqual(report["syntax"]["status"], "passed")
        self.assertEqual(report["scope"]["bundled_runtime"], "not_run")
        self.assertEqual(report["scope"]["native_platform_matrix"], "not_run")
        self.assertNotIn(str(benchmark.ROOT), child.stdout.decode())


if __name__ == "__main__":
    unittest.main()
