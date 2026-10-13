"""Verify and install a self-contained bundle within the current user's profile."""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath


def _plain_path(path: Path) -> None:
    """Reject links/junctions in an existing path before copying or replacing it."""
    for candidate in (path, *path.parents):
        if candidate.is_symlink() or (hasattr(candidate, "is_junction") and candidate.is_junction()):
            raise ValueError("Installation paths cannot contain links or junctions")


def bundle_file(root: Path, name: str) -> Path:
    relative = PurePosixPath(name)
    if (not name or "\\" in name or ":" in name or relative.is_absolute()
            or any(not part or part in (".", "..") or part.endswith((".", " "))
                   or any(ord(character) < 32 for character in part)
                   or part.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *{f"COM{i}" for i in range(1, 10)}, *{f"LPT{i}" for i in range(1, 10)}}
                   for part in name.split("/"))):
        raise ValueError("Invalid bundle member path")
    result = root.joinpath(*relative.parts)
    _plain_path(result)
    if not result.resolve().is_relative_to(root.resolve()):
        raise ValueError("Bundle member escaped its root")
    return result


def digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def verify_bundle(source: Path) -> dict:
    source = Path(source).absolute()
    _plain_path(source)
    manifest_path = source / "bundle.json"
    if not manifest_path.is_file() or manifest_path.stat().st_size > 1_000_000:
        raise ValueError("A valid bundle manifest is required")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (not isinstance(manifest, dict)
            or manifest.get("application") != "Kshetrajna" or manifest.get("schema") != 1
            or not re.fullmatch(r"\d+\.\d+\.\d+", str(manifest.get("version", "")))
            or manifest.get("architecture") not in ("x64", "arm64")
            or not re.fullmatch(r"[a-f0-9]{40}", str(manifest.get("source_commit", "")))):
        raise ValueError("Invalid bundle identity")
    files = manifest.get("files")
    if not isinstance(files, dict) or not 1 <= len(files) <= 2000:
        raise ValueError("Invalid bundle inventory")
    required = {"runtime/python.exe", "runtime/pythonw.exe", "Launch.cmd", "Install.cmd",
                "app/src/kshetrajna/__main__.py", "app/src/kshetrajna/web/index.html"}
    if not required.issubset(files):
        raise ValueError("Required bundle files are missing")
    seen = set()
    for name, expected in files.items():
        if not isinstance(name, str) or not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected):
            raise ValueError("Invalid bundle checksum")
        if name.casefold() in seen or name == "bundle.json":
            raise ValueError("Duplicate or invalid bundle member")
        seen.add(name.casefold())
        member = bundle_file(source, name)
        if not member.is_file() or digest(member) != expected:
            raise ValueError(f"Bundle integrity check failed: {name}")
    return manifest


def install_bundle(source: Path, programs_directory: Path) -> Path:
    """Immutable version folders preserve earlier installs and never contain history."""
    source = Path(source).absolute()
    manifest = verify_bundle(source)
    programs_directory = Path(programs_directory).absolute()
    _plain_path(programs_directory)
    root = programs_directory / "Kshetrajna"
    _plain_path(root)
    root.mkdir(parents=True, exist_ok=True)
    name = f"{manifest['version']}-{manifest['architecture']}-{manifest['source_commit'][:12]}"
    target = root / name
    _plain_path(target)
    if target.exists():
        installed = verify_bundle(target)
        if installed != manifest:
            raise ValueError("An unrelated installation occupies this location")
        return target
    # TemporaryDirectory cleanup is restricted to this verified, fresh staging root.
    with tempfile.TemporaryDirectory(prefix=".install-", dir=root) as staging_name:
        staging = Path(staging_name)
        _plain_path(staging)
        if staging.resolve().parent != root.resolve() or not staging.name.startswith(".install-"):
            raise ValueError("Invalid installation staging directory")
        payload = staging / "payload"
        payload.mkdir()
        for relative in manifest["files"]:
            original = bundle_file(source, relative)
            destination = bundle_file(payload, relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original, destination)
        (payload / "bundle.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        verify_bundle(payload)
        if target.exists():
            raise ValueError("Installation destination changed; retry after reviewing it")
        payload.rename(target)
    return target


def create_desktop_shortcut(installed: Path, manifest: dict) -> None:
    if sys.platform != "win32":
        raise ValueError("Desktop shortcuts require Windows")
    verify_bundle(installed)
    # A version-specific name avoids overwriting any existing user shortcut.
    label = f"Kshetrajna {manifest['version']} {manifest['architecture']} {manifest['source_commit'][:7]}"
    environment = {**os.environ, "KSHETRAJNA_INSTALL": str(installed), "KSHETRAJNA_SHORTCUT_LABEL": label}
    script = r"""
$ErrorActionPreference = 'Stop'
$desktop = [Environment]::GetFolderPath('DesktopDirectory')
if (-not $desktop) { throw 'Windows did not provide a desktop location' }
$path = Join-Path $desktop ($env:KSHETRAJNA_SHORTCUT_LABEL + '.lnk')
if (Test-Path -LiteralPath $path) { throw 'This desktop shortcut already exists; it was preserved' }
$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut($path)
$link.TargetPath = Join-Path $env:KSHETRAJNA_INSTALL 'runtime\pythonw.exe'
$link.Arguments = '-B -m kshetrajna.desktop_launcher'
$link.WorkingDirectory = $env:KSHETRAJNA_INSTALL
$link.Description = 'Kshetrajna local Windows assistant'
$link.Save()
"""
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                            env=environment, text=True, capture_output=True,
                            creationflags=subprocess.CREATE_NO_WINDOW, timeout=20)
    if result.returncode:
        raise OSError("The desktop shortcut could not be created. Use Launch.cmd in the installation folder.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify or install the local Windows bundle")
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--verify", action="store_true", help="Check bundle integrity without installing")
    args = parser.parse_args()
    try:
        manifest = verify_bundle(args.source)
        if args.verify:
            print(f"Verified Kshetrajna {manifest['version']} {manifest['architecture']}")
            return 0
        if sys.platform != "win32" or not os.environ.get("LOCALAPPDATA"):
            raise ValueError("Installation requires a Windows user profile")
        programs = Path(os.environ["LOCALAPPDATA"]) / "Programs"
        print(f"Kshetrajna {manifest['version']} for Windows {manifest['architecture']}")
        print(f"Install a verified copy under {programs / 'Kshetrajna'}.")
        print("History stays separately in your Windows profile. No model is downloaded.")
        if input("Install for this Windows user? [y/N] ").strip().lower() != "y":
            print("Installation cancelled.")
            return 0
        installed = install_bundle(args.source, programs)
        print(f"Installed: {installed}")
        if input("Create a desktop shortcut? [y/N] ").strip().lower() == "y":
            try:
                create_desktop_shortcut(installed, manifest)
                print("Desktop shortcut created.")
            except OSError as error:
                print(str(error))
        print("Open Launch.cmd or the desktop shortcut to start. Use Quit in Privacy to stop.")
        return 0
    except (OSError, ValueError, EOFError) as error:
        print(f"Setup could not finish: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
