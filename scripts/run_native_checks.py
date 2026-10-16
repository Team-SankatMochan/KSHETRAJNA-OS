"""Run every test with the selected native interpreter and record actual results."""

import argparse
import json
import os
import platform
import subprocess
import sys
import sysconfig
import unittest
from pathlib import Path

from release_tools import ROOT, PYTHON_VERSION, check_python, git


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--architecture", choices=("x64", "arm64"), required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--bundle", type=Path)
    args = parser.parse_args()
    check_python()
    if args.bundle:
        # The embeddable runtime omits unittest. Borrow only the pinned host stdlib
        # for the harness, keeping application imports on the bundle's isolated _pth.
        executable = args.bundle.resolve() / "runtime/python.exe"
        code = ("import sys; sys.path.append(" + repr(sysconfig.get_path("stdlib")) + "); "
                "sys.path.insert(0," + repr(str(ROOT / "scripts")) + "); "
                "from run_native_checks import main; main()")
        subprocess.run([str(executable), "-I", "-B", "-c", code, "--architecture", args.architecture,
                        "--report", str(args.report.resolve())], cwd=ROOT, check=True)
        return
    expected = "win-amd64" if args.architecture == "x64" else "win-arm64"
    if sys.platform != "win32" or sysconfig.get_platform() != expected:
        raise ValueError("Expected native " + expected)
    if platform.machine().lower() not in ({"amd64", "x86_64"} if args.architecture == "x64" else {"arm64", "aarch64"}):
        raise ValueError("Emulation is not native architecture evidence")
    # Source checks explicitly set PYTHONPATH=src; packaged checks use isolated _pth.
    import kshetrajna
    package_path = Path(kshetrajna.__file__).resolve()
    packaged = not package_path.is_relative_to(ROOT / "src")
    if packaged:
        bundle = package_path.parents[3]
        identity = json.loads((bundle / "bundle.json").read_text(encoding="utf-8"))
        if identity["source_commit"] != git("rev-parse", "HEAD") or identity["dirty_source"] is not False:
            raise ValueError("Packaged tests require the exact clean candidate revision")
    sys.path.insert(0, str(ROOT))  # Tests also import the repository's scripts namespace.
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    report = {"schema": 1, "source_commit": git("rev-parse", "HEAD"), "architecture": args.architecture,
              "python": PYTHON_VERSION, "runtime": "packaged" if packaged else "source",
              "tests": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
              "skips": len(result.skipped), "status": "passed" if result.wasSuccessful() and not result.skipped else "failed"}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if report["status"] != "passed" or result.testsRun == 0:
        raise SystemExit("Native qualification failed (including skips); see the actual test output")


if __name__ == "__main__":
    main()
