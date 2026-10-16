"""Offline release primitives. Inventory is evidence of contents, not an attestation."""

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from email.parser import BytesParser
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
PYTHON_VERSION = "3.13.16"


def check_python():
    if sys.implementation.name != "cpython" or sys.version_info[:3] != (3, 13, 16):
        raise ValueError("Release checks require CPython " + PYTHON_VERSION)


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def git(*args, root=ROOT):
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


def epoch():
    value = os.environ.get("SOURCE_DATE_EPOCH")
    value = git("log", "-1", "--format=%ct") if value is None else value
    if not re.fullmatch(r"[0-9]+", value):
        raise ValueError("SOURCE_DATE_EPOCH must be an integer UTC timestamp")
    result = int(value)
    if not 315532800 <= result <= 4354819198:
        raise ValueError("SOURCE_DATE_EPOCH must fit the ZIP 1980..2107 date range")
    return result


def zip_time():
    return datetime.datetime.fromtimestamp(epoch(), datetime.UTC).timetuple()[:6]


def safe_name(name):
    path = PurePosixPath(name)
    if (not name or path.is_absolute() or "\\" in name or ":" in name
            or any(p in ("", ".", "..") or p.endswith((" ", "."))
                   or any(ord(c) < 32 for c in p)
                   or p.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL",
                       *{f"COM{i}" for i in range(1, 10)}, *{f"LPT{i}" for i in range(1, 10)}}
                   for p in name.split("/"))):
        raise ValueError("Unsafe artifact member: " + name)
    return path


def scan_content(name, content):
    parts = PurePosixPath(name).parts
    lower = name.lower()
    if (any(p.startswith(".") or p in {"__pycache__", "node_modules", "tests", "reports"} for p in parts)
            or lower.endswith((".db", ".db-wal", ".db-shm", ".log", ".pem", ".key", ".pfx", ".p12"))
            or parts[-1].lower() in {"config.json", "device.json", "diagnostics.json"}):
        raise ValueError("Private/development content in artifact: " + name)
    if re.search(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\bgh[pousr]_[A-Za-z0-9]{36,}|\bAKIA[A-Z0-9]{16}\b", content):
        raise ValueError("Credential signature in artifact: " + name)


def scan_archive(path):
    """Bounded ZIP inspection before extraction; rejects collisions and special files."""
    files = {}
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        if len(members) > 5000 or sum(m.file_size for m in members) > 250 * 1024 * 1024:
            raise ValueError("Artifact exceeds content limits")
        seen = set()
        for member in members:
            name = member.filename
            safe_name(name.rstrip("/") if member.is_dir() else name)
            key = name.rstrip("/").casefold()
            mode = member.external_attr >> 16 & 0o170000
            if key in seen or mode not in (0, 0o100000, 0o040000) or member.flag_bits & 1:
                raise ValueError("Duplicate, special or encrypted artifact member")
            seen.add(key)
            if not member.is_dir():
                content = archive.read(member)
                scan_content(name, content)
                files[name] = content
    if Path(path).suffix == ".whl":
        metadata = [n for n in files if n.endswith(".dist-info/METADATA")]
        if len(metadata) != 1:
            raise ValueError("Wheel must contain one distribution")
        message = BytesParser().parsebytes(files[metadata[0]])
        if message["Name"] != "kshetrajna" or message.get_all("Requires-Dist"):
            raise ValueError("Unexpected wheel distribution/runtime dependencies")
        allowed_meta = {"METADATA", "WHEEL", "RECORD", "entry_points.txt", "top_level.txt", "licenses/LICENSE", "LICENSE"}
        prefix = metadata[0].removesuffix("METADATA")
        for name in files:
            if name.startswith("kshetrajna/"):
                if PurePosixPath(name).suffix not in {".py", ".html", ".css", ".js", ".svg", ".png", ".ico"}:
                    raise ValueError("Unexpected wheel payload: " + name)
            elif not name.startswith(prefix) or name[len(prefix):] not in allowed_meta:
                raise ValueError("Unexpected wheel member: " + name)
        if "kshetrajna/web/index.html" not in files:
            raise ValueError("Wheel lacks dashboard assets")
    return files


def scan_bundle(path):
    files = scan_archive(path)
    if any(not n.startswith("Kshetrajna/") for n in files):
        raise ValueError("Unexpected bundle root")
    members = {n.removeprefix("Kshetrajna/"): b for n, b in files.items()}
    manifest = json.loads(members.pop("bundle.json"))
    if set(members) != set(manifest["files"]):
        raise ValueError("Bundle has missing or unlisted content")
    for name, content in members.items():
        if hashlib.sha256(content).hexdigest() != manifest["files"][name]:
            raise ValueError("Bundle content checksum mismatch: " + name)
    if manifest.get("python_version") != PYTHON_VERSION:
        raise ValueError("Wrong bundle runtime")
    return manifest


def build_wheel(output):
    check_python()
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if list(output.glob("*.whl")):
        raise ValueError("Wheel output directory already contains a wheel")
    environment = {**os.environ, "SOURCE_DATE_EPOCH": str(epoch()), "PYTHONDONTWRITEBYTECODE": "1"}
    # Direct PEP 517 backend avoids network resolution and implicit build isolation.
    with tempfile.TemporaryDirectory(prefix="kshetrajna-wheel-inputs-") as name:
        stage = Path(name)
        for member in [ROOT / n for n in ("pyproject.toml", "README.md", "LICENSE")] + sorted((ROOT / "src/kshetrajna").rglob("*")):
            if member.is_symlink() or (hasattr(member, "is_junction") and member.is_junction()):
                raise ValueError("Wheel inputs cannot contain links")
            if not member.is_file() or "__pycache__" in member.parts:
                continue
            target = stage / member.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(member, target)
        subprocess.run([sys.executable, "-B", "-c", "import setuptools.build_meta as b; b.build_wheel(" + repr(str(output)) + ")"],
                       cwd=stage, env=environment, check=True)
    wheels = list(output.glob("*.whl"))
    if len(wheels) != 1:
        raise ValueError("Expected exactly one wheel")
    scan_archive(wheels[0])
    return wheels[0]


def inventory(artifact, output):
    """SPDX 2.3 of observed files and explicitly known distributions; no fake provenance."""
    artifact = Path(artifact)
    files = scan_archive(artifact)
    bundle = artifact.suffix == ".zip"
    if bundle:
        manifest = scan_bundle(artifact)
        version = manifest["version"]
    else:
        name = next(n for n in files if n.endswith(".dist-info/METADATA"))
        version = BytesParser().parsebytes(files[name])["Version"]
    app = {"SPDXID": "SPDXRef-App", "name": "Kshetrajna", "versionInfo": version,
           "downloadLocation": "NOASSERTION", "filesAnalyzed": False,
           "licenseConcluded": "MIT", "licenseDeclared": "MIT", "copyrightText": "NOASSERTION"}
    packages = [app]
    relations = [{"spdxElementId": "SPDXRef-DOCUMENT", "relationshipType": "DESCRIBES", "relatedSpdxElement": "SPDXRef-App"}]
    if bundle:
        packages.append({"SPDXID": "SPDXRef-CPython", "name": "CPython Windows embeddable distribution",
                         "versionInfo": PYTHON_VERSION, "downloadLocation": manifest["runtime_url"],
                         "checksums": [{"algorithm": "SHA256", "checksumValue": manifest["runtime_sha256"]}],
                         "filesAnalyzed": False, "licenseConcluded": "NOASSERTION", "licenseDeclared": "PSF-2.0",
                         "copyrightText": "NOASSERTION",
                         "comment": "Distribution includes third-party code. Full bundled license notices are in runtime/LICENSE.txt; PSF-2.0 is CPython's license, not a relicensing of those components."})
        relations.append({"spdxElementId": "SPDXRef-App", "relationshipType": "DEPENDS_ON", "relatedSpdxElement": "SPDXRef-CPython"})
        components = json.loads((ROOT / "packaging/windows/runtime-components.json").read_text(encoding="utf-8"))
        for item in components["components"]:
            if not any("Kshetrajna/runtime/" + n in files for n in item["evidence_files"]):
                continue
            identifier = "SPDXRef-Component-" + item["name"]
            packages.append({"SPDXID": identifier, "name": item["name"], "versionInfo": item["version"],
                             "downloadLocation": item["source"], "filesAnalyzed": False,
                             "licenseConcluded": "NOASSERTION", "licenseDeclared": item["license"],
                             "copyrightText": "NOASSERTION", "comment": item["license_source"]})
            relations.append({"spdxElementId": "SPDXRef-CPython", "relationshipType": "CONTAINS", "relatedSpdxElement": identifier})
    records = []
    for index, (name, content) in enumerate(sorted(files.items())):
        identifier = f"SPDXRef-File-{index}"
        records.append({"SPDXID": identifier, "fileName": "./" + name,
                        "checksums": [{"algorithm": "SHA256", "checksumValue": hashlib.sha256(content).hexdigest()}],
                        "licenseConcluded": "NOASSERTION", "licenseInfoInFiles": ["NOASSERTION"], "copyrightText": "NOASSERTION"})
        relations.append({"spdxElementId": "SPDXRef-CPython" if "/runtime/" in name else "SPDXRef-App",
                          "relationshipType": "CONTAINS", "relatedSpdxElement": identifier})
    document = {"spdxVersion": "SPDX-2.3", "dataLicense": "CC0-1.0", "SPDXID": "SPDXRef-DOCUMENT",
                "name": artifact.name + " known-component inventory", "documentNamespace": "https://spdx.org/spdxdocs/kshetrajna-" + digest(artifact),
                "creationInfo": {"creators": ["Tool: Kshetrajna release_tools.py"],
                                 "created": datetime.datetime.fromtimestamp(epoch(), datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")},
                "comment": "Observed archive files and known distributions only; not an exhaustive transitive license audit, vulnerability assessment, or attested provenance. Upstream runtime license notices remain authoritative.",
                "packages": packages, "files": records, "relationships": relations}
    if bundle:
        document["hasExtractedLicensingInfos"] = components["extracted_licenses"]
    Path(output).write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check-python", "build-wheel", "scan", "inventory"))
    parser.add_argument("artifact", type=Path, nargs="?")
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    check_python()
    if args.command == "build-wheel":
        print(build_wheel(args.output))
    elif args.command == "scan":
        (scan_bundle if args.artifact.suffix == ".zip" else scan_archive)(args.artifact)
        print("Content scan passed: " + args.artifact.name)
    elif args.command == "inventory":
        inventory(args.artifact, args.output)


if __name__ == "__main__":
    main()
