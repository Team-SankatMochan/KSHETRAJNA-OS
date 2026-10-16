#!/usr/bin/env python3
"""Opt-in, stdlib-only measurements on disposable synthetic files, never user roots."""

import argparse
import contextlib
import json
import logging
import math
import os
import platform
import re
import signal
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_FILES = 200_000
DEFAULT_COUNTS = (10_000, 50_000, 200_000)
MAX_REPEATS = 10
MAX_ACTIONS = 20
MAX_TIMEOUT = 3600


class MeasurementError(RuntimeError):
    """A public, path-free measurement failure."""


def bounded_integer(low, high):
    def parse(value):
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise argparse.ArgumentTypeError("expected an integer") from None
        if not low <= number <= high:
            raise argparse.ArgumentTypeError(f"expected {low}..{high}")
        return number

    return parse


def summarize(samples):
    """Seconds; p95 is the nearest-rank statistic, including for small samples."""
    values = sorted(float(value) for value in samples)
    if not values or any(not math.isfinite(value) or value < 0 for value in values):
        raise ValueError("timings must be nonempty, finite and nonnegative")
    return {
        "samples": len(values),
        "median_seconds": statistics.median(values),
        "p95_seconds": values[math.ceil(0.95 * len(values)) - 1],
        "min_seconds": values[0],
        "max_seconds": values[-1],
    }


def run_process(command, *, timeout, cwd=ROOT, env=None):
    """Capture output; bound waits and terminate the process tree on timeout."""
    process = subprocess.Popen(
        command, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=(os.name == "posix"),
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            # taskkill is a Windows system utility, not a downloaded dependency.
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
            if process.poll() is None:
                process.kill()
        process.communicate(timeout=10)
        raise MeasurementError("child process timed out") from None
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def environment_metadata(root=ROOT):
    """Allowlisted metadata only: no hostname, username, paths or environment dump."""
    with (root / "pyproject.toml").open("rb") as stream:
        build_version = tomllib.load(stream)["project"]["version"]
    # Read the user-visible source version without importing application services.
    import ast

    tree = ast.parse((root / "src/kshetrajna/__init__.py").read_text(encoding="utf-8"))
    visible_version = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets
        ):
            visible_version = ast.literal_eval(node.value)
    revision, dirty = None, None
    try:
        result = run_process(["git", "rev-parse", "HEAD"], cwd=root, timeout=5)
        value = result.stdout.decode("ascii", errors="replace").strip()
        if result.returncode == 0 and re.fullmatch(r"[0-9a-f]{40,64}", value):
            revision = value
        result = run_process(["git", "status", "--porcelain"], cwd=root, timeout=5)
        if result.returncode == 0:
            dirty = bool(result.stdout.strip())
    except (OSError, MeasurementError):
        pass
    return {
        "build_version": build_version,
        "user_visible_version": visible_version,
        "versions_match": build_version == visible_version,
        "source_revision": revision,
        "working_tree_dirty": dirty,
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.system(),
        "architecture": platform.machine(),
        "os_name": os.name,
        "sqlite": sqlite3.sqlite_version,
    }


def peak_rss():
    """Optional process-lifetime high-water mark, not per-operation memory use."""
    if sys.platform not in ("linux", "darwin"):
        return {"status": "unavailable", "reason": "no supported stdlib RSS counter"}
    try:
        import resource

        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return {
            "status": "available", "peak_bytes": int(value * (1 if sys.platform == "darwin" else 1024)),
            "source": "getrusage.ru_maxrss", "scope": "worker_process_lifetime",
        }
    except (ImportError, OSError, ValueError):
        return {"status": "unavailable", "reason": "RSS counter unavailable"}


def create_fixture(root, count):
    """Only a new, empty directory is accepted. File count includes the empty target."""
    if type(count) is not int or not 2 <= count <= MAX_FILES:
        raise ValueError("fixture count out of bounds")
    root = Path(root)
    root.mkdir()  # Never adopt, clear or mutate an existing folder.
    (root / "action-target.txt").write_bytes(b"")
    for index in range(1, count):
        parent = root / f"batch-{index // 1000:04d}"
        parent.mkdir(exist_ok=True)
        with (parent / f"sample-{index:08d}.txt").open("xb") as stream:
            stream.write(f"Synthetic offline fixture {index:08d}\n".encode("ascii"))


def wait_for_scan(service, root_id, timeout):
    deadline = time.monotonic() + timeout
    worker = service._scan_thread
    with service._scan_done:
        finished = service._scan_done.wait_for(
            lambda: root_id not in service._active_scans,
            timeout=max(0, deadline - time.monotonic()),
        )
    if not finished:
        service.cancel_scan(root_id)
        raise MeasurementError("scan timed out")
    if worker is not None:
        worker.join(max(0, deadline - time.monotonic()))
        if worker.is_alive():
            raise MeasurementError("scan worker did not stop")
    root = service.store.get_root(root_id)
    if not root:
        raise MeasurementError("fixture root disappeared")
    return root


def require(condition, message):
    # Do not use assert: benchmark checks must survive python -O.
    if not condition:
        raise MeasurementError(message)


def measure_cancellation(service, root_id, timeout):
    """Cancel an active real scan at an event-controlled, completed batch write."""
    entered, release = threading.Event(), threading.Event()
    original_flush = service._flush_batch

    def observed_flush(batch):
        original_flush(batch)
        entered.set()
        if not release.wait(timeout):
            raise MeasurementError("cancellation barrier timed out")

    service._flush_batch = observed_flush
    try:
        service.rescan_root(root_id)
        require(entered.wait(timeout), "scan did not reach cancellation boundary")
        start = time.perf_counter()
        service.cancel_scan(root_id)
        release.set()
        root = wait_for_scan(service, root_id, timeout)
        elapsed = time.perf_counter() - start
        require(root["status"] == "cancelled", "scan cancellation was not observed")
        return elapsed
    finally:
        release.set()
        worker = service._scan_thread
        if worker is not None:
            service.cancel_scan(root_id)
            worker.join(timeout)
        service._flush_batch = original_flush


def benchmark_worker(count, actions, timeout):
    if type(actions) is not int or not 1 <= actions <= MAX_ACTIONS:
        raise ValueError("action count out of bounds")
    sys.path.insert(0, str(ROOT / "src"))
    from kshetrajna.file_service import FileService
    from kshetrajna.file_storage import FileStore, QUARANTINE_DIR_NAME

    timings = {name: [] for name in (
        "scan_initial", "search_hit", "search_miss", "quarantine", "restore", "scan_cancel_controlled",
    )}
    with tempfile.TemporaryDirectory(prefix="kshetrajna-benchmark-") as temporary:
        base = Path(temporary)
        root_path = base / "fixture"
        create_fixture(root_path, count)
        store = FileStore(base / "catalog.db")
        service = FileService(store)
        try:
            start = time.perf_counter()
            approval = service.approve_locations(
                [{"path": str(root_path), "label": "Disposable synthetic benchmark"}], consent=True,
            )
            require(len(approval["approved"]) == 1 and not approval["skipped"], "fixture approval failed")
            root_id = approval["approved"][0]["id"]
            root = wait_for_scan(service, root_id, timeout)
            timings["scan_initial"].append(time.perf_counter() - start)
            require(root["status"] == "ready", "fixture scan did not complete fully")
            require(root["file_count"] == count, "scan count did not match fixture")
            target = service.search("action-target.txt", root_id)["results"]
            require(len(target) == 1, "action target was not indexed")
            file_id = target[0]["id"]
            for _ in range(actions):
                for name, query, expected in (
                    ("search_hit", f"sample-{count - 1:08d}.txt", 1),
                    ("search_miss", "nonexistent-synthetic-marker", 0),
                ):
                    start = time.perf_counter()
                    result = service.search(query, root_id)["results"]
                    timings[name].append(time.perf_counter() - start)
                    require(len(result) == expected, "search result count mismatch")
                start = time.perf_counter()
                deleted = service.delete_empty_file(file_id, confirm=True)
                timings["quarantine"].append(time.perf_counter() - start)
                action_id = deleted["action_id"]
                quarantined = root_path / QUARANTINE_DIR_NAME / action_id
                require(deleted["quarantined"] and quarantined.is_file(), "quarantine was not durable")
                require(not (root_path / "action-target.txt").exists(), "quarantine left original")
                require(store.get_file(file_id) is None, "quarantine left catalog entry")
                require(store.get_root(root_id)["file_count"] == count - 1, "quarantine count mismatch")
                start = time.perf_counter()
                restored = service.restore_quarantined_file(action_id, confirm=True)
                timings["restore"].append(time.perf_counter() - start)
                require(restored["status"] == "restored", "restore failed")
                require((root_path / "action-target.txt").read_bytes() == b"", "restored bytes changed")
                require(not quarantined.exists(), "restore left quarantine copy")
                require(store.get_file(file_id) is not None, "restore did not repair catalog")
                require(store.get_root(root_id)["file_count"] == count, "restore count mismatch")
            timings["scan_cancel_controlled"].append(measure_cancellation(service, root_id, timeout))
            require(store.get_root(root_id)["file_count"] == count, "cancelled rescan removed catalog files")
            result = {"count": count, "timings": timings, "rss": peak_rss()}
        finally:
            worker = service._scan_thread
            if worker is not None:
                for pending in list(service._active_scans):
                    service.cancel_scan(pending)
                worker.join(timeout)
                require(not worker.is_alive(), "fixture worker did not stop before cleanup")
    return result


def parser():
    command = argparse.ArgumentParser(description=__doc__)
    command.add_argument("--run", action="store_true", help="explicit consent to create disposable synthetic files")
    command.add_argument("--counts", nargs="+", type=bounded_integer(2, MAX_FILES), default=list(DEFAULT_COUNTS))
    command.add_argument("--repeats", type=bounded_integer(1, MAX_REPEATS), default=3)
    command.add_argument("--actions", type=bounded_integer(1, MAX_ACTIONS), default=3)
    command.add_argument("--timeout", type=bounded_integer(1, MAX_TIMEOUT), default=900,
                         help="hard timeout in seconds for each fresh worker, including fixture creation")
    command.add_argument("--json-output", type=Path, help="optional report destination (parent must exist)")
    command.add_argument("--_worker", action="store_true", help=argparse.SUPPRESS)
    return command


def write_report(report, destination=None):
    text = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if destination is not None:
        # Exclusive creation protects existing reports and accidental source destinations.
        with destination.open("x", encoding="utf-8") as stream:
            stream.write(text)
    sys.stdout.write(text)


def run_benchmarks(args):
    if len(args.counts) > 3 or len(set(args.counts)) != len(args.counts):
        raise MeasurementError("use at most three distinct fixture counts")
    report = {
        "schema": 1, "kind": "disposable_source_benchmark", "status": "not_run",
        "environment": environment_metadata(), "results": [],
        "configuration": {"counts": args.counts, "repeats": args.repeats, "actions_per_repeat": args.actions,
                          "worker_timeout_seconds": args.timeout},
        "scope": {
            "fixture": "synthetic text; one empty cleanup target; at most 1000 files per subfolder",
            "scan": "initial scan into fresh catalog; includes root approval; OS caches not controlled",
            "search": "one exact filename hit and one miss; separate timings; result limit 100",
            "actions": "confirmed empty-file quarantine and restore on same filesystem",
            "cancellation": "active rescan after real catalog flush; event barrier release overhead included",
            "rss": "optional worker process lifetime peak; not per-operation or process-tree RSS",
            "package_native_matrix": "not_run", "throughput_targets": "not_evaluated",
        },
    }
    if not args.run:
        report["reason"] = "requires --run; no fixture created"
        return report
    report["status"] = "passed"
    for count in args.counts:
        combined, memory = {}, []
        for _ in range(args.repeats):
            command = [sys.executable, "-B", str(Path(__file__).resolve()), "--run", "--_worker",
                       "--counts", str(count), "--actions", str(args.actions), "--timeout", str(args.timeout)]
            try:
                child = run_process(command, timeout=args.timeout)
                if child.returncode:
                    raise MeasurementError("measurement worker failed")
                result = json.loads(child.stdout)
                require(result["count"] == count, "worker fixture count mismatch")
                for name, samples in result["timings"].items():
                    combined.setdefault(name, []).extend(samples)
                memory.append(result["rss"])
            except (OSError, MeasurementError, ValueError, KeyError, TypeError):
                report["status"] = "failed"
                report["results"].append({"count": count, "status": "failed",
                                          "completed_repeats": len(memory), "reason": "worker failed or timed out"})
                return report
        available = [sample["peak_bytes"] for sample in memory if sample["status"] == "available"]
        report["results"].append({
            "count": count, "status": "passed", "completed_repeats": len(memory),
            "timings": {name: summarize(samples) for name, samples in combined.items()},
            "rss": {"status": "available", "max_peak_bytes": max(available), "workers_measured": len(available),
                    "workers_total": len(memory), "scope": "worker_process_lifetime"} if available else memory[0],
        })
    return report


def main(argv=None):
    command = parser()
    args = command.parse_args(argv)
    if len(args.counts) > 3 or len(set(args.counts)) != len(args.counts):
        command.error("use at most three distinct fixture counts")
    if args._worker:
        if not args.run or len(args.counts) != 1:
            command.error("worker needs --run and exactly one count")
        try:
            # Application logging / exceptions may contain paths: never include them in artifacts.
            logging.disable(logging.CRITICAL)
            with open(os.devnull, "w") as quiet, contextlib.redirect_stdout(quiet), contextlib.redirect_stderr(quiet):
                report = benchmark_worker(args.counts[0], args.actions, args.timeout)
            write_report(report)
            return 0
        except Exception:
            write_report({"status": "failed", "reason": "measurement worker failed"})
            return 1
    try:
        report = run_benchmarks(args)
        write_report(report, args.json_output)
        return int(report["status"] == "failed")
    except (OSError, ValueError, MeasurementError):
        write_report({"schema": 1, "kind": "disposable_source_benchmark", "status": "failed",
                      "reason": "configuration, metadata or report destination unavailable"})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
