"""Compare two fresh unsigned builds under one SOURCE_DATE_EPOCH (never signed bytes)."""

import argparse
import os
import json
import tempfile
from pathlib import Path

from release_tools import build_wheel, check_python, digest, epoch, git


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=("wheel", "windows"), required=True)
    parser.add_argument("--architecture", choices=("x64", "arm64"))
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--artifact", type=Path, help="Also require the candidate payload to match both builds")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    check_python()
    os.environ["SOURCE_DATE_EPOCH"] = str(epoch())
    with tempfile.TemporaryDirectory(prefix="kshetrajna-repro-") as name:
        root = Path(name)
        if args.kind == "wheel":
            outputs = [build_wheel(root / lane) for lane in ("first", "second")]
        else:
            from build_windows_bundle import build
            if not args.architecture:
                parser.error("Windows builds require --architecture")
            outputs = [build(args.architecture, root / lane / "payload.zip", allow_dirty=args.allow_dirty)
                       for lane in ("first", "second")]
        hashes = [digest(p) for p in outputs]
        if hashes[0] != hashes[1]:
            raise ValueError("Unsigned reproducibility mismatch: " + repr(hashes))
        if args.artifact and digest(args.artifact) != hashes[0]:
            raise ValueError("Candidate payload does not match the reproducible builds")
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps({"status": "passed", "kind": args.kind, "architecture": args.architecture,
                                               "source_commit": git("rev-parse", "HEAD"),
                                               "dirty_source": bool(git("status", "--porcelain")),
                                               "source_date_epoch": epoch(), "sha256": hashes[0]}, indent=2) + "\n", encoding="utf-8")
        print(f"Unsigned {args.kind} reproducible; SOURCE_DATE_EPOCH={epoch()}; sha256={hashes[0]}")


if __name__ == "__main__":
    main()
