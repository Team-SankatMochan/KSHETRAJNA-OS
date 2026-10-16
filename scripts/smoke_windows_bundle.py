"""Check the actual packaged interpreter, defaults, assets and graceful shutdown."""

import argparse
import http.client
import json
import re
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit


def call_http(port, path, *, body=None, token=None, origin="same", host=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    headers = {"Origin": f"http://127.0.0.1:{port}", "Content-Type": "application/json"}
    if origin is None:
        headers.pop("Origin")
    elif origin != "same":
        headers["Origin"] = origin
    if token:
        headers["X-Kshetrajna-Token"] = token
    if host:
        headers["Host"] = host
    connection.request("GET" if body is None else "POST", path,
                       None if body is None else json.dumps(body), headers)
    response = connection.getresponse()
    data = response.read()
    status = response.status
    connection.close()
    return status, data


def session(executable: Path, data: Path, *, demo: bool, cwd=None, env=None, isolated=False):
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    command = [str(executable), *(["-I"] if isolated else []), "-u", "-B", "-m", "kshetrajna", "--no-browser", "--port", str(port)]
    command += ["--demo"] if demo else ["--data-dir", str(data)]
    process = subprocess.Popen(command, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        deadline = time.monotonic() + 30
        while True:
            if process.poll() is not None:
                raise RuntimeError("Packaged app exited early: " + process.communicate()[0])
            try:
                status, content = call_http(port, "/")
                if status == 200:
                    break
            except OSError:
                pass
            if time.monotonic() > deadline:
                raise RuntimeError("Packaged app did not start in time")
            time.sleep(.2)
        page = content.decode("utf-8")
        token = re.search(r'name="csrf-token" content="([^"]+)"', page).group(1)
        assert "Quit Kshetrajna" in page
        assets = {"/app.js", "/app.css", "/chat.js", "/chat.css", "/files.js"}
        for reference in re.findall(r'''(?:src|href)=["']([^"']+)["']''', page):
            parsed = urlsplit(reference)
            if parsed.path.endswith((".js", ".css", ".svg", ".png", ".ico")):
                assert not parsed.scheme and not parsed.netloc, "Packaged dashboard must use local assets"
                assets.add(parsed.path + ("?" + parsed.query if parsed.query else ""))
        for asset in sorted(assets):
            status, content = call_http(port, asset)
            assert status == 200 and content, asset
        assert call_http(port, "/", host="example.invalid")[0] == 403
        assert call_http(port, "/api/state")[0] == 403
        state = json.loads(call_http(port, "/api/state", token=token)[1])
        assert state["mode"] == ("demo" if demo else "live")
        if not demo:
            for setting in ("enabled", "allow_local_ai", "allow_maintenance", "allow_priority_changes", "allow_process_control"):
                assert state["settings"][setting] is False, setting
        assert call_http(port, "/api/shutdown", body={"confirm": True}, token="invalid")[0] == 403
        for origin in (None, "https://example.invalid", "null"):
            assert call_http(port, "/api/shutdown", body={"confirm": True}, token=token, origin=origin)[0] == 403
        for confirm in (False, "true", 1):
            assert call_http(port, "/api/shutdown", body={"confirm": confirm}, token=token)[0] == 400
        assert call_http(port, "/api/state", token=token)[0] == 200
        assert call_http(port, "/api/shutdown", body={"confirm": True}, token=token)[0] == 200
        output = process.communicate(timeout=20)[0]
        assert process.returncode == 0, output
        return {"mode": state["mode"], "assets": "passed", "approval_guards": "passed", "shutdown": "passed"}
    finally:
        if process.poll() is None:
            # Only this isolated smoke-test child; it never enables personal PC actions.
            process.terminate()
            process.communicate(timeout=10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--architecture", choices=("x64", "arm64"), required=True)
    args = parser.parse_args()
    executable = (args.bundle / "runtime" / "python.exe").resolve()
    check = subprocess.run([str(executable), "-B", "-m", "kshetrajna.bundle_install", "--source", str(args.bundle.resolve()), "--verify"],
                           capture_output=True, text=True, timeout=30, check=True)
    print(check.stdout.strip())
    runtime = subprocess.check_output([str(executable), "-B", "-c",
        "import json,platform,struct,sqlite3,kshetrajna; print(json.dumps({'machine':platform.machine(),'bits':struct.calcsize('P')*8,'version':kshetrajna.__version__,'python':platform.python_version()}))"], text=True)
    identity = json.loads(runtime)
    assert identity["bits"] == 64
    assert identity["python"] == "3.13.16"
    assert identity["machine"].lower() in ({"amd64", "x86_64"} if args.architecture == "x64" else {"arm64", "aarch64"})
    with tempfile.TemporaryDirectory(prefix="kshetrajna-package-smoke-") as test_directory:
        fixture = Path(test_directory)
        diagnostics = [str(executable), "-B", "-m", "kshetrajna.diagnostics", "--live", "--output", str(fixture / "device.json")]
        if args.architecture == "arm64":
            diagnostics.append("--require-native-arm64")
        subprocess.run(diagnostics, capture_output=True, text=True, timeout=30, check=True)
        results = [session(executable, fixture / "live-history", demo=False, cwd=fixture, isolated=True),
                   session(executable, fixture / "unused-demo-history", demo=True, cwd=fixture, isolated=True)]
    print(json.dumps({"runtime": identity, "sessions": results, "diagnostics": "passed"}, indent=2))


if __name__ == "__main__":
    main()
