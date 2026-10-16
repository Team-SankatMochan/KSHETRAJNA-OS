#!/usr/bin/env python3
"""Offline source verification. Not a wheel, bundled-runtime, or native OS gate."""

import argparse
import collections
import contextlib
import json
import os
import re
import shutil
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.benchmark_core import (  # noqa: E402 - runnable without installation
    MeasurementError, bounded_integer, environment_metadata, run_process, write_report,
)

BASELINE_TOTAL = 276
BASELINE_MODULES = {"test_quarantine": 42, "test_scan_lifecycle": 14}


def syntax_check(root=ROOT):
    paths = sorted(path for directory in ("src", "scripts", "tests")
                   for path in (root / directory).rglob("*.py") if "__pycache__" not in path.parts)
    failures = []
    for path in paths:
        try:
            # compile(bytes) honors source encoding declarations and writes no bytecode.
            compile(path.read_bytes(), path.relative_to(root).as_posix(), "exec")
        except (OSError, SyntaxError, ValueError):
            failures.append(path.relative_to(root).as_posix())
    return {"status": "passed" if paths and not failures else "failed", "files_checked": len(paths),
            "failed_files": failures, "mode": "compile in memory; no pyc output"}


def javascript_check(root=ROOT, *, timeout=15):
    paths = sorted([*(root / "src/kshetrajna/web").glob("*.js"), *(root / "web-demo").glob("*.js")])
    node = shutil.which("node")
    if node is None:
        return {"status": "skipped", "reason": "optional node executable not available",
                "files_discovered": len(paths), "files_checked": 0}
    version = None
    try:
        result = run_process([node, "--version"], cwd=root, timeout=5)
        value = result.stdout.decode("ascii", errors="replace").strip()
        if result.returncode == 0 and re.fullmatch(r"v\d+\.\d+\.\d+", value):
            version = value
    except (OSError, MeasurementError):
        pass
    failures, checked = [], 0
    for path in paths:
        try:
            result = run_process([node, "--check", str(path)], cwd=root, timeout=timeout)
            checked += 1
            if result.returncode:
                failures.append(path.relative_to(root).as_posix())
        except (OSError, MeasurementError):
            failures.append(path.relative_to(root).as_posix())
    return {"status": "passed" if paths and not failures else "failed", "files_discovered": len(paths),
            "files_checked": checked, "failed_files": failures, "node_version": version,
            "mode": "node --check; no browser execution"}


def leaf_tests(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from leaf_tests(test)
        else:
            yield test


def safe_test_id(test):
    value = test.id().split(" (", 1)[0]
    return value if re.fullmatch(r"[\w.]{1,240}", value, flags=re.ASCII) else "redacted-test-id"


class SummaryResult(unittest.TestResult):
    """Keep real unittest accounting, but never serialize assertion values or traces."""

    def __init__(self):
        super().__init__()
        self.failed_ids = set()

    def addFailure(self, test, err):
        self.failed_ids.add(safe_test_id(test))
        super().addFailure(test, err)

    def addError(self, test, err):
        self.failed_ids.add(safe_test_id(test))
        super().addError(test, err)

    def addSubTest(self, test, subtest, err):
        if err is not None:
            self.failed_ids.add(safe_test_id(test))
        super().addSubTest(test, subtest, err)

    def addUnexpectedSuccess(self, test):
        self.failed_ids.add(safe_test_id(test))
        super().addUnexpectedSuccess(test)


def unittest_worker(patterns):
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(ROOT / "tests"))
    suite, seen = unittest.TestSuite(), set()
    with open(os.devnull, "w") as quiet, contextlib.redirect_stdout(quiet), contextlib.redirect_stderr(quiet):
        for pattern in patterns:
            discovered = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern=pattern)
            for test in leaf_tests(discovered):
                if test.id() not in seen:
                    seen.add(test.id())
                    suite.addTest(test)
        counts = collections.Counter(test.__class__.__module__ for test in leaf_tests(suite))
        discovered_count = suite.countTestCases()
        result = SummaryResult()
        started = time.perf_counter()
        suite.run(result)
        elapsed = time.perf_counter() - started
    return {
        "status": "passed" if discovered_count and result.wasSuccessful() else "failed",
        "discovered": discovered_count, "tests_run": result.testsRun, "failures": len(result.failures),
        "errors": len(result.errors), "skipped": len(result.skipped),
        "expected_failures": len(result.expectedFailures), "unexpected_successes": len(result.unexpectedSuccesses),
        "failed_test_ids": sorted(result.failed_ids), "module_counts": dict(sorted(counts.items())),
        "elapsed_seconds": elapsed,
    }


def baseline_check(tests, patterns):
    if patterns != ["test*.py"]:
        return {"status": "not_applicable", "reason": "focused selection, not the full source suite"}
    counts = tests.get("module_counts", {})
    passed = tests.get("discovered", 0) >= BASELINE_TOTAL and all(
        counts.get(module, 0) >= count for module, count in BASELINE_MODULES.items()
    )
    return {"status": "passed" if passed else "failed", "reference_total": BASELINE_TOTAL,
            "reference_module_counts": BASELINE_MODULES,
            "note": "discovery floor only; unittest results determine behavior success"}


def parser():
    command = argparse.ArgumentParser(description=__doc__)
    command.add_argument("--pattern", action="append", help="repeatable unittest filename glob; default test*.py")
    command.add_argument("--timeout", type=bounded_integer(1, 3600), default=600,
                         help="hard timeout in seconds for unittest child process")
    command.add_argument("--js", choices=("auto", "off", "required"), default="auto",
                         help="auto reports node absence as skipped; required makes that a failure")
    command.add_argument("--json-output", type=Path, help="optional new report file; parent must exist")
    command.add_argument("--_unittest-worker", action="store_true", help=argparse.SUPPRESS)
    return command


def main(argv=None):
    command = parser()
    args = command.parse_args(argv)
    patterns = args.pattern or ["test*.py"]
    if any(not re.fullmatch(r"[A-Za-z0-9_.*?\[\]-]{1,120}\.py", pattern) for pattern in patterns):
        command.error("patterns must be filename globs ending in .py, without paths")
    if args._unittest_worker:
        try:
            report = unittest_worker(patterns)
        except Exception:
            report = {"status": "failed", "reason": "unittest discovery or runner failed"}
        write_report(report)
        return int(report["status"] != "passed")
    try:
        report = {
            "schema": 1, "kind": "offline_source_verification", "environment": environment_metadata(),
            "runtime": {"status": "passed" if sys.version_info[:2] == (3, 13) else "failed",
                        "required": "Python 3.13.x"},
            "scope": {"package_build": "not_run", "bundled_runtime": "not_run", "native_platform_matrix": "not_run",
                      "benchmarks": "not_run; separate opt-in script",
                      "network": "no external network; unittest may use loopback fixtures",
                      "failure_details": "test identifiers only; rerun unittest locally for assertion details"},
            "syntax": syntax_check(),
        }
        child_command = [sys.executable, "-B", str(Path(__file__).resolve()), "--_unittest-worker"]
        for pattern in patterns:
            child_command.extend(["--pattern", pattern])
        # sys.path changes in the worker do not reach its subprocesses. Pin their
        # source imports too, without changing the parent or trusting caller paths.
        worker_env = os.environ.copy()
        worker_env["PYTHONPATH"] = str(ROOT / "src")
        try:
            child = run_process(child_command, timeout=args.timeout, env=worker_env)
            tests = json.loads(child.stdout)
            if child.returncode or not isinstance(tests, dict):
                if not isinstance(tests, dict):
                    tests = {"status": "failed", "reason": "invalid unittest summary"}
                tests["status"] = "failed"
        except (OSError, MeasurementError, ValueError):
            tests = {"status": "failed", "reason": "unittest child failed or timed out"}
        report["unittest"] = tests
        report["baseline"] = baseline_check(tests, patterns)
        report["javascript"] = ({"status": "skipped", "reason": "explicit --js off", "files_checked": 0}
                                if args.js == "off" else javascript_check())
        if args.js == "required" and report["javascript"]["status"] == "skipped":
            report["javascript"]["status"] = "failed"
        report["status"] = "failed" if (
            not report["environment"]["versions_match"]
            or any(report[key]["status"] == "failed" for key in ("runtime", "syntax", "unittest", "baseline", "javascript"))
        ) else "passed"
        write_report(report, args.json_output)
        return int(report["status"] == "failed")
    except (OSError, ValueError, KeyError):
        write_report({"schema": 1, "kind": "offline_source_verification", "status": "failed",
                      "reason": "metadata or report destination unavailable"})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
