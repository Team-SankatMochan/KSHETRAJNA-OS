"""Validate release syntax; optional hash-pinned actionlint, optional parse-only PowerShell."""

import argparse
import ast
import base64
import hashlib
import io
import json
import platform
import re
import subprocess
import tarfile
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from release_tools import ROOT, check_python

ACTIONLINT_URL = "https://github.com/rhysd/actionlint/releases/download/v1.7.7/actionlint_1.7.7_linux_amd64.tar.gz"
ACTIONLINT_SHA256 = "023070a287cd8cccd71515fedc843f1985bf96c436b7effaecce67290e7e0757"
# Upstream checksum source: same release's actionlint_1.7.7_checksums.txt.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download-actionlint", action="store_true", help="Optional Linux x64 validator; installs only under .tools")
    parser.add_argument("--powershell", help="Parse script ASTs only, never run installer/signing code")
    args = parser.parse_args()
    check_python()
    for path in sorted((ROOT / "scripts").glob("*.py")) + [ROOT / "tests/test_release_tools.py"]:
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for path in sorted((ROOT / "packaging/windows").glob("*.json")) + [ROOT / "scripts/toolchain-sources.json"]:
        json.loads(path.read_text(encoding="utf-8"))
    ET.parse(ROOT / "packaging/windows/Package.wxs")
    workflows = sorted((ROOT / ".github/workflows").glob("*.yml"))
    for path in workflows:
        for line in path.read_text(encoding="utf-8").splitlines():
            if "uses:" in line:
                action = line.split("uses:", 1)[1].split("#", 1)[0].strip()
                if not action.startswith("./") and not re.fullmatch(r"actions/[a-z-]+@[a-f0-9]{40}", action):
                    raise ValueError("Unpinned workflow action: " + action)
    if args.powershell:
        for path in sorted((ROOT / "packaging/windows").glob("*.ps1")):
            source = base64.b64encode(path.read_bytes()).decode("ascii")
            command = ("$source=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + source + "'));"
                       "$tokens=$null;$errors=$null;[void][System.Management.Automation.Language.Parser]::ParseInput($source,[ref]$tokens,[ref]$errors);"
                       "if($errors.Count){$errors | ForEach-Object {Write-Error $_.Message};exit 1}")
            encoded = base64.b64encode(command.encode("utf-16le")).decode("ascii")
            subprocess.run([args.powershell, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded], check=True, timeout=30)
        print("PowerShell AST parsing passed; no lifecycle/signing scripts executed")
    if args.download_actionlint:
        if platform.system() != "Linux" or platform.machine().lower() not in {"amd64", "x86_64"}:
            raise ValueError("The optional actionlint binary pin is for Linux x64")
        with urllib.request.urlopen(ACTIONLINT_URL, timeout=60) as response:
            data = response.read(5_000_001)
        if len(data) > 5_000_000 or hashlib.sha256(data).hexdigest() != ACTIONLINT_SHA256:
            raise ValueError("Actionlint checksum mismatch")
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as package:
            member = package.getmember("actionlint")
            if not member.isfile() or member.size > 10_000_000:
                raise ValueError("Invalid actionlint payload")
            executable = ROOT / ".tools/actionlint/actionlint"
            executable.parent.mkdir(parents=True, exist_ok=True)
            executable.write_bytes(package.extractfile(member).read())
            executable.chmod(0o755)
        # These explicit runner labels are provisioned externally. This actionlint
        # data set predates Windows ARM hosted runners; declarations aren't evidence of availability.
        config = executable.parent / "actionlint.yaml"
        config.write_text("self-hosted-runner:\n  labels: [windows-11-arm, x64, arm64, kshetrajna-signing-ephemeral, kshetrajna-standard-user-ephemeral]\n", encoding="utf-8")
        subprocess.run([str(executable), "-shellcheck=", "-pyflakes=", "-config-file", str(config), *map(str, workflows)], check=True, cwd=ROOT)
        print("Actionlint 1.7.7 passed (shellcheck/pyflakes not run)")
    print("Release Python/JSON/XML syntax and immutable-action pins passed")


if __name__ == "__main__":
    main()
