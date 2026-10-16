"""Exercise an installed wheel or container runtime from outside the source tree."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

from smoke_windows_bundle import session


def installed(executable, workspace, *, require_non_root=False):
    environment = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "PYTHONHOME"}}
    probe = """import importlib.resources as r,json,os,platform,kshetrajna
from pathlib import Path
assert platform.python_version() == '3.13.16'
p=Path(kshetrajna.__file__).resolve()
assert 'site-packages' in p.parts, str(p)
assert r.files('kshetrajna').joinpath('web/index.html').is_file()
print(json.dumps({'python':platform.python_version(),'installed':True,'uid':os.getuid() if hasattr(os,'getuid') else None}))
"""
    identity = json.loads(subprocess.check_output([str(executable), "-I", "-B", "-c", probe], cwd=workspace, env=environment, text=True))
    if require_non_root and (identity["uid"] is None or identity["uid"] == 0):
        raise ValueError("Container runtime must run as a non-root UID")
    result = session(executable, workspace / "unused", demo=True, cwd=workspace, env=environment, isolated=True)
    return {"runtime": identity, "session": result}


def smoke_wheel(wheel):
    wheel = Path(wheel).resolve()
    with tempfile.TemporaryDirectory(prefix="kshetrajna-installed-wheel-") as name:
        workspace = Path(name)
        venv.EnvBuilder(with_pip=True).create(workspace / "venv")
        executable = workspace / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        subprocess.run([str(executable), "-I", "-m", "pip", "install", "--no-index", "--no-deps", "--no-compile", str(wheel)],
                       cwd=workspace, check=True)
        return installed(executable, workspace)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--wheel-dir", type=Path)
    group.add_argument("--installed", action="store_true")
    parser.add_argument("--require-non-root", action="store_true")
    args = parser.parse_args()
    if args.wheel_dir:
        wheels = list(args.wheel_dir.glob("*.whl"))
        if len(wheels) != 1:
            raise ValueError("Expected exactly one wheel")
        result = smoke_wheel(wheels[0])
    else:
        with tempfile.TemporaryDirectory(prefix="kshetrajna-container-smoke-") as name:
            result = installed(Path(sys.executable), Path(name), require_non_root=args.require_non_root)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
