"""Opt-in device qualification. Reports aggregates only, never app names/PIDs."""

import argparse
import ctypes
import json
import math
import os
import platform
import sqlite3
import statistics
import sys
import sysconfig
import time
from contextlib import closing
from ctypes import wintypes
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .patterns import learn_patterns
from .telemetry import (FILETIME, LASTINPUTINFO, MEMORYSTATUSEX,
                        PROCESS_MEMORY_COUNTERS, WindowsProbe, _bind)


def architecture():
    result = {"python": platform.python_version(), "python_platform": sysconfig.get_platform(),
              "os": platform.system(), "os_version": platform.version(),
              "pointer_bits": ctypes.sizeof(ctypes.c_void_p) * 8,
              "native_machine": None, "process_machine": None, "emulated": None}
    if sys.platform == "win32":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        current = _bind(kernel, "GetCurrentProcess", wintypes.HANDLE, [])
        query = _bind(kernel, "IsWow64Process2", wintypes.BOOL,
                      [wintypes.HANDLE, ctypes.POINTER(wintypes.USHORT), ctypes.POINTER(wintypes.USHORT)])
        process, native = wintypes.USHORT(), wintypes.USHORT()
        if not query(current(), ctypes.byref(process), ctypes.byref(native)):
            raise ctypes.WinError(ctypes.get_last_error())
        names = {0x014c: "x86", 0x8664: "x64", 0xaa64: "ARM64"}
        result.update(native_machine=names.get(native.value, hex(native.value)),
                      process_machine=names.get(process.value or native.value, hex(process.value or native.value)),
                      emulated=bool(process.value))
    return result


def abi_checks():
    if sys.platform != "win32":
        return {"status": "not_applicable", "reason": "Win32 ABI checks require Windows."}
    expected = {"FILETIME": 8, "LASTINPUTINFO": 8, "MEMORYSTATUSEX": 64,
                "PROCESS_MEMORY_COUNTERS": 72 if ctypes.sizeof(ctypes.c_void_p) == 8 else 40}
    actual = {kind.__name__: ctypes.sizeof(kind) for kind in
              (FILETIME, LASTINPUTINFO, MEMORYSTATUSEX, PROCESS_MEMORY_COUNTERS)}
    return {"status": "passed" if actual == expected else "failed", "expected": expected, "actual": actual}


def summary(values):
    ordered = sorted(values)
    return {"median_ms": round(statistics.median(ordered), 3),
            "p95_ms": round(ordered[math.ceil(len(ordered) * .95) - 1], 3),
            "max_ms": round(ordered[-1], 3)}


def pattern_benchmark(repeats=5):
    # Fixed fixtures allow repeatable comparisons without reading user history.
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    apps = ["Editor", "Browser", "Terminal", "Meeting", "Browser", "Notes"]
    rows = [{"observed_at": (start + timedelta(seconds=i * 2)).isoformat(),
             "foreground_app": apps[(i // 5) % len(apps)], "idle_seconds": 0} for i in range(12000)]
    learn_patterns(rows, rows[-1])  # warm-up excluded
    timings = []
    for _ in range(repeats):
        began = time.perf_counter()
        model = learn_patterns(rows, rows[-1])
        timings.append((time.perf_counter() - began) * 1000)
    return {"source": "synthetic fixed fixture; not personal prediction accuracy", "samples": len(rows),
            "repeats": repeats, **summary(timings), "replay": model["prediction_quality"]}


def live_check(samples=6):
    """Read Windows counters without changing settings or writing telemetry history."""
    probe = WindowsProbe()
    probe.sample(track_processes=True, track_foreground=True, max_processes=12)
    timings, process_counts, memory, foreground = [], [], [], 0
    cpu_began, wall_began = time.process_time(), time.perf_counter()
    for _ in range(samples):
        time.sleep(1)
        began = time.perf_counter()
        sample = probe.sample(track_processes=True, track_foreground=True, max_processes=12)
        timings.append((time.perf_counter() - began) * 1000)
        if not (0 <= sample.cpu_percent <= 100 and
                0 < sample.memory_total_bytes and
                0 <= sample.memory_available_bytes <= sample.memory_total_bytes):
            raise ValueError("Windows returned invalid aggregate CPU or memory counters")
        process_counts.append(len(sample.processes))
        foreground += sample.foreground_app is not None
        own = probe._process(os.getpid())
        if own:
            memory.append(own[1] / 1024**2)
    elapsed = time.perf_counter() - wall_began
    cpu_seconds = time.process_time() - cpu_began
    return {"status": "passed" if max(process_counts) else "partial",
            "samples": samples, "collection": summary(timings),
            "observed_process_count_min": min(process_counts),
            "observed_process_count_max": max(process_counts),
            "foreground_available_samples": foreground,
            "diagnostic_process_peak_sampled_working_set_mb": round(max(memory), 2) if memory else None,
            "diagnostic_process_cpu_seconds": round(cpu_seconds, 4),
            "wall_seconds": round(elapsed, 3),
            "single_core_cpu_percent": round(100 * cpu_seconds / elapsed, 3),
            "note": "One-second diagnostic sampling; not dashboard overhead, battery life or optimization benefit. Foreground may be unavailable on CI/locked desktops."}


def run_report(live=False, require_native_arm64=False):
    device = architecture()
    checks = {"python_3_13": sys.version_info[:2] == (3, 13),
              "64_bit_python": device["pointer_bits"] == 64}
    if require_native_arm64:
        checks["native_arm64"] = (device["native_machine"] == "ARM64" and
                                  device["process_machine"] == "ARM64" and device["emulated"] is False)
    with closing(sqlite3.connect(":memory:")) as db:
        checks["sqlite"] = db.execute("SELECT 1").fetchone()[0] == 1
    abi = abi_checks()
    report = {"generated_at": datetime.now(timezone.utc).isoformat(), "device": device,
              "checks": checks, "win32_abi": abi, "pattern_benchmark": pattern_benchmark(),
              "live_telemetry": {"status": "not_run"},
              "privacy": "Contains environment and aggregate diagnostic metrics; no app names, process IDs, titles, URLs or saved user history.",
              "qualification": "Device smoke test only. No Snapdragon performance score, battery claim or optimization benefit is inferred."}
    if live:
        try:
            report["live_telemetry"] = live_check()
        except (OSError, RuntimeError, ValueError) as error:
            report["live_telemetry"] = {"status": "failed", "error_type": type(error).__name__}
    report["passed"] = all(checks.values()) and abi["status"] != "failed" and (
        not live or report["live_telemetry"]["status"] == "passed")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Take six read-only Windows samples; store aggregate results only")
    parser.add_argument("--require-native-arm64", action="store_true", help="Fail if this is not native Windows ARM64 Python")
    parser.add_argument("--output", type=Path, help="Write a JSON report to this file")
    args = parser.parse_args(argv)
    try:
        report = run_report(args.live, args.require_native_arm64)
        content = json.dumps(report, indent=2)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(content + "\n", encoding="utf-8")
        print(content)
        return 0 if report["passed"] else 1
    except (OSError, RuntimeError, ValueError, AttributeError) as error:
        print("Diagnostics failed: " + type(error).__name__, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
