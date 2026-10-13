"""Build native Windows packages from hash-pinned official CPython runtimes."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import urllib.request
import urllib.parse
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
PYTHON_VERSION = "3.13.16"
RUNTIMES = {
    "x64": ("https://www.python.org/ftp/python/3.13.16/python-3.13.16-embed-amd64.zip",
            "97dae5274cc54867065e8d5a3226e48c35017ed332a0fdb0e27d5b5821961297"),
    "arm64": ("https://www.python.org/ftp/python/3.13.16/python-3.13.16-embed-arm64.zip",
              "790d097697a2020477549a6764d89d22194c88d6e42caebcb2db63791e004c94"),
}


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def runtime_archive(architecture: str, cache: Path) -> Path:
    url, expected = RUNTIMES[architecture]
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / f"python-{PYTHON_VERSION}-{architecture}.zip"
    if archive.exists():
        if archive.is_symlink() or sha256(archive) != expected:
            raise ValueError("Cached runtime checksum mismatch; review/remove that cache file")
        return archive
    request = urllib.request.Request(url, headers={"User-Agent": "Kshetrajna-bundle-builder/0.4"})
    with urllib.request.urlopen(request, timeout=60) as response:
        if urllib.parse.urlsplit(response.url).hostname != "www.python.org":
            raise ValueError("Unexpected runtime download host")
        content = response.read(30 * 1024 * 1024 + 1)
    if len(content) > 30 * 1024 * 1024 or hashlib.sha256(content).hexdigest() != expected:
        raise ValueError("Official CPython runtime checksum mismatch")
    archive.write_bytes(content)
    return archive


def extract_runtime(archive: Path, directory: Path) -> None:
    with zipfile.ZipFile(archive) as package:
        members = package.infolist()
        if len(members) > 1000 or sum(member.file_size for member in members) > 100 * 1024 * 1024:
            raise ValueError("Unexpected runtime archive size")
        seen = set()
        for member in members:
            name = member.filename
            path = PurePosixPath(name)
            if (path.is_absolute() or "\\" in name or ":" in name or ".." in path.parts
                    or name.casefold() in seen or (member.external_attr >> 16) & 0o170000 == 0o120000):
                raise ValueError("Unsafe runtime archive member")
            seen.add(name.casefold())
            target = directory.joinpath(*path.parts)
            if not target.resolve().is_relative_to(directory.resolve()):
                raise ValueError("Runtime extraction escaped its root")
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(package.read(member))


def write_text(path: Path, text: str) -> None:
    path.write_bytes(text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))


def build(architecture: str, output: Path, *, allow_dirty=False) -> Path:
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    git = ["git", "-c", f"safe.directory={ROOT.as_posix()}"]
    commit = subprocess.check_output(git + ["rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = bool(subprocess.check_output(git + ["status", "--porcelain"], cwd=ROOT, text=True).strip())
    if dirty and not allow_dirty:
        raise ValueError("Commit source changes before building a publishable bundle")
    runtime = runtime_archive(architecture, ROOT / "build" / "runtime-cache")
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".kshetrajna-build-", dir=output.parent) as staging_name:
        stage = Path(staging_name)
        if stage.resolve().parent != output.parent or not stage.name.startswith(".kshetrajna-build-"):
            raise ValueError("Unexpected build staging directory")
        package = stage / "Kshetrajna"
        runtime_directory = package / "runtime"
        runtime_directory.mkdir(parents=True)
        extract_runtime(runtime, runtime_directory)
        write_text(runtime_directory / "python313._pth", "python313.zip\n.\n../app/src\n")
        source = ROOT / "src" / "kshetrajna"
        destination = package / "app" / "src" / "kshetrajna"
        for member in sorted(source.rglob("*")):
            if member.is_symlink() or (hasattr(member, "is_junction") and member.is_junction()):
                raise ValueError("Source bundle cannot include links")
            if not member.is_file() or member.suffix.lower() not in {".py", ".js", ".css", ".html", ".svg", ".png", ".ico"}:
                continue
            if "__pycache__" in member.parts:
                continue
            if not member.resolve().is_relative_to(source.resolve()):
                raise ValueError("Source member escaped the application directory")
            target = destination / member.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(member, target)
        write_text(package / "Launch.cmd", '@echo off\nsetlocal\nstart "" /b "%~dp0runtime\\pythonw.exe" -B -m kshetrajna.desktop_launcher %*\n')
        write_text(package / "Console.cmd", '@echo off\nsetlocal\n"%~dp0runtime\\python.exe" -B -m kshetrajna.chat_launcher %*\nif errorlevel 1 pause\n')
        write_text(package / "Install.cmd", '@echo off\nsetlocal\n"%~dp0runtime\\python.exe" -B -m kshetrajna.bundle_install --source "%~dp0."\npause\n')
        shutil.copyfile(ROOT / "LICENSE", package / "LICENSE.txt")
        write_text(package / "README.txt", f"""Kshetrajna {version} - Windows {architecture}

1. Extract the whole archive with Windows 'Extract All'.
2. Open Launch.cmd. No Python or Git installation is needed.
   Or open Install.cmd for a verified per-user copy and optional desktop shortcut.
3. Enable collection and action controls in Privacy. Review each proposed change.
4. Use Quit Kshetrajna in Privacy to stop. Closing a browser tab does not stop it.

If startup fails, open Console.cmd for details. For another port use:
Console.cmd --port 8767

AI conversation is optional: separately install Ollama and a local model,
then select that installed model and enable local AI in Chat. No API key is needed.
No model, NPU provider or automatic download is included.

The runtime is the official CPython {PYTHON_VERSION} embeddable Windows package.
Python license: runtime/LICENSE.txt. Kshetrajna license: LICENSE.txt.
bundle.json records source identity, runtime origin and file SHA-256 values.
Checksums detect corruption; this archive is not a signed application installer.

Preferences/history: %LOCALAPPDATA%\\Kshetrajna (separate from program files).
To update: Quit, extract a new bundle, and optionally run its Install.cmd.
Earlier installed versions are preserved. Do not overwrite a running version.
To remove: Quit and remove the extracted or installed version folder and its shortcut.
History and original user files are preserved; use Privacy to delete stored history.

Source: https://github.com/Team-SankatMochan/KSHETRAJNA-OS
Setup: https://github.com/Team-SankatMochan/KSHETRAJNA-OS/blob/experimental/sort-ai-jarvis/docs/WINDOWS_QUICKSTART.md
""")
        inventory = {member.relative_to(package).as_posix(): sha256(member)
                     for member in sorted(package.rglob("*")) if member.is_file()}
        manifest = {"schema": 1, "application": "Kshetrajna", "version": version,
                    "architecture": architecture, "source_commit": commit, "dirty_source": dirty,
                    "python_version": PYTHON_VERSION, "runtime_url": RUNTIMES[architecture][0],
                    "runtime_sha256": RUNTIMES[architecture][1], "files": inventory}
        (package / "bundle.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zipped:
            for member in sorted(package.rglob("*")):
                if not member.is_file():
                    continue
                info = zipfile.ZipInfo("Kshetrajna/" + member.relative_to(package).as_posix(), (2026, 10, 14, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                zipped.writestr(info, member.read_bytes())
    output.with_suffix(output.suffix + ".sha256").write_text(f"{sha256(output)}  {output.name}\n", encoding="ascii")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--architecture", choices=RUNTIMES, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--allow-dirty", action="store_true", help="Local inspection only; never release this bundle")
    args = parser.parse_args()
    output = args.output or ROOT / "dist" / f"Kshetrajna-Windows-{args.architecture}.zip"
    print(build(args.architecture, output, allow_dirty=args.allow_dirty))


if __name__ == "__main__":
    main()
