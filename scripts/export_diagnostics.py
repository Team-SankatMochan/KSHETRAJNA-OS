"""Export a local diagnostic JSON by strict allowlist. Never uploads or opens history."""

import argparse
import json
import math
from pathlib import Path


def redact(report):
    device = report.get("device", {})
    safe = {"schema": 1, "scope": "allowlisted local diagnostic summary; not release qualification"}
    safe["checks"] = {k: v for k, v in report.get("checks", {}).items()
                      if k in {"python_3_13", "64_bit_python", "native_arm64", "sqlite"} and type(v) is bool}
    safe["device"] = {k: v for k, v in device.items() if
                      (k in {"native_machine", "process_machine"} and v in {"x86", "x64", "ARM64"})
                      or (k == "pointer_bits" and type(v) is int and v in {32, 64})
                      or (k == "emulated" and type(v) is bool)
                      or (k == "python" and v == "3.13.16")}
    for section in ("win32_abi", "live_telemetry"):
        value = report.get(section, {}).get("status")
        if value in {"passed", "partial", "failed", "not_run", "not_applicable"}:
            safe[section] = {"status": value}
    timings = report.get("live_telemetry", {}).get("collection", {})
    safe["collection"] = {k: v for k, v in timings.items() if k in {"median_ms", "p95_ms", "max_ms"}
                          and type(v) in {int, float} and math.isfinite(v) and 0 <= v <= 1_000_000}
    return safe


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Existing explicit diagnostic report; never a history database")
    parser.add_argument("--output", type=Path, required=True, help="New local JSON file; no overwrite")
    args = parser.parse_args()
    with args.input.open("rb") as stream:
        data = stream.read(1_000_001)
    if len(data) > 1_000_000:
        raise ValueError("Diagnostic input exceeds 1 MB")
    document = redact(json.loads(data))
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(document, output, indent=2, allow_nan=False)
        output.write("\n")


if __name__ == "__main__":
    main()
