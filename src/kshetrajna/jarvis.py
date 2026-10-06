"""Small, local-only command bridge for Jarvis-style assistants.

The bridge exposes reviewed file operations as named commands. It has no
arbitrary shell, registry, process, or filesystem command. A Jarvis caller can
either use :class:`JarvisCommandBridge` in-process or invoke this module with
newline-delimited JSON. When the dashboard is running, ``JarvisHttpClient``
connects to its loopback API and reuses the dashboard's per-session token.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from .config import default_data_dir
from .file_demo import seed_file_demo
from .file_service import FileService
from .file_storage import FileStore
from .known_folders import discover_known_folders


MAX_JSON_LINE = 64 * 1024


def _require_bool(value, name: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{name} must be a boolean")
    return value


def _loopback_base(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or parsed.hostname not in ("127.0.0.1", "localhost"):
        raise ValueError("Jarvis integration accepts loopback URLs only")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Jarvis integration URL must not contain credentials or query data")
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/")


class JarvisCommandBridge:
    """Dispatch safe sorting/search commands to an existing FileService."""

    def __init__(self, file_service: FileService):
        self.file_service = file_service

    def _wait_for_root(self, root_id: int, timeout: float = 60.0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            root = self.file_service.store.get_root(root_id)
            if not root:
                raise ValueError("Approved root disappeared")
            if root["status"] not in ("pending", "scanning"):
                if root["status"] != "ready":
                    raise ValueError(f"Folder scan ended with status {root['status']}")
                return root
            time.sleep(0.05)
        raise ValueError("Folder scan timed out")

    def _resolve_root(self, args: dict) -> int:
        root_id = args.get("root_id")
        if root_id is not None:
            if isinstance(root_id, bool) or not isinstance(root_id, int) or root_id <= 0:
                raise ValueError("root_id must be a positive integer")
            self._wait_for_root(root_id)
            return root_id

        path = args.get("path")
        known_id = args.get("known_folder")
        if path is not None and known_id is not None:
            raise ValueError("Choose path or known_folder, not both")
        if known_id is not None:
            if not isinstance(known_id, str):
                raise ValueError("known_folder must be text")
            match = next((folder for folder in discover_known_folders() if folder["id"] == known_id.casefold()), None)
            if not match:
                raise ValueError("Known folder is unavailable; refresh discovery")
            path = match["path"]
        if not isinstance(path, str) or not path:
            raise ValueError("Provide root_id, path, or known_folder")

        path_obj = Path(path).resolve(strict=True)
        for root in self.file_service.store.get_roots():
            try:
                if Path(root["path"]).resolve(strict=True) == path_obj:
                    self._wait_for_root(root["id"])
                    return root["id"]
            except OSError:
                continue
        added = self.file_service.add_root(str(path_obj), args.get("label", path_obj.name or "Approved folder"))
        self._wait_for_root(added["id"])
        return added["id"]

    def execute(self, command: str, **args) -> dict:
        if command == "discover_folders":
            return {"folders": discover_known_folders()}
        if command == "approved_folders":
            return {"roots": self.file_service.state()["roots"]}
        if command == "file_search":
            query = args.get("query")
            if not isinstance(query, str) or len(query) > 200:
                raise ValueError("query must be text of at most 200 characters")
            return self.file_service.search(query, args.get("root_id"))
        if command == "sort_preview":
            root_id = self._resolve_root(args)
            return self.file_service.generate_sort_plan(root_id, _require_bool(args.get("create_folders", False), "create_folders"))
        if command == "sort_apply":
            if _require_bool(args.get("confirm", False), "confirm") is not True:
                raise ValueError("sort_apply requires confirm=true after reviewing the preview")
            return self.file_service.apply_sort_plan(args.get("plan_id"))
        if command == "sort_undo":
            if _require_bool(args.get("confirm", False), "confirm") is not True:
                raise ValueError("sort_undo requires confirm=true")
            return self.file_service.undo_sort_plan(args.get("plan_id"))
        raise ValueError("Unknown Jarvis command")


class JarvisHttpClient:
    """Call the running dashboard through its loopback-only API."""

    def __init__(self, base_url: str, timeout: float = 10.0):
        self.base_url = _loopback_base(base_url)
        self.timeout = timeout
        self.token = self._load_token()

    def _load_token(self) -> str:
        with urlopen(Request(self.base_url + "/", method="GET"), timeout=self.timeout) as response:
            html = response.read(256 * 1024).decode("utf-8")
        match = re.search(r'<meta\s+name="csrf-token"\s+content="([^"]+)"', html)
        if not match:
            raise ValueError("Kshetrajna dashboard token was not found")
        return match.group(1)

    def _get(self, path: str) -> dict:
        with urlopen(Request(self.base_url + path, method="GET"), timeout=self.timeout) as response:
            return json.loads(response.read())

    def _post(self, path: str, body: dict) -> dict:
        request = Request(self.base_url + path, data=json.dumps(body).encode("utf-8"), method="POST", headers={
            "Content-Type": "application/json",
            "Origin": self.base_url,
            "X-Kshetrajna-Token": self.token,
        })
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read())
        except Exception as error:
            if hasattr(error, "read"):
                try:
                    details = json.loads(error.read())
                    raise ValueError(details.get("error", "Dashboard request failed")) from error
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass
            raise ValueError("Dashboard request failed") from error

    def execute(self, command: str, **args) -> dict:
        if command == "discover_folders":
            return self._get("/api/files/known-folders")
        if command == "approved_folders":
            return self._get("/api/files/state")
        if command == "file_search":
            query = args.get("query")
            if not isinstance(query, str) or len(query) > 200:
                raise ValueError("query must be text of at most 200 characters")
            query_string = urlencode({"q": query})
            if args.get("root_id") is not None:
                query_string += "&" + urlencode({"root_id": args["root_id"]})
            return self._get("/api/files/search?" + query_string)
        if command == "sort_preview":
            root_id = args.get("root_id")
            if root_id is None:
                known_id = args.get("known_folder")
                if known_id:
                    folders = self._get("/api/files/known-folders")["folders"]
                    match = next((folder for folder in folders if folder["id"] == known_id.casefold()), None)
                    if not match:
                        raise ValueError("Known folder is unavailable; refresh discovery")
                    args["path"] = match["path"]
                if args.get("path"):
                    roots = self._get("/api/files/state")["roots"]
                    path = str(Path(args["path"]).resolve(strict=False)).casefold()
                    existing = next((root for root in roots if str(Path(root["path"]).resolve(strict=False)).casefold() == path), None)
                    if existing:
                        root_id = existing["id"]
                    else:
                        added = self._post("/api/files/roots/add", {"path": args["path"], "label": args.get("label", Path(args["path"]).name)})
                        root_id = added["id"]
                        deadline = time.monotonic() + 60
                        while time.monotonic() < deadline:
                            root = next((root for root in self._get("/api/files/state")["roots"] if root["id"] == root_id), None)
                            if root is None:
                                raise ValueError("Approved root disappeared")
                            if root["status"] != "scanning":
                                if root["status"] != "ready":
                                    raise ValueError(f"Folder scan ended with status {root['status']}")
                                break
                            time.sleep(.1)
                if root_id is None:
                    raise ValueError("Provide root_id, path, or known_folder")
            return self._post("/api/files/sort/preview", {"root_id": root_id, "create_folders": _require_bool(args.get("create_folders", False), "create_folders")})
        if command == "sort_apply":
            if _require_bool(args.get("confirm", False), "confirm") is not True:
                raise ValueError("sort_apply requires confirm=true after reviewing the preview")
            return self._post("/api/files/sort/apply", {"plan_id": args.get("plan_id")})
        if command == "sort_undo":
            if _require_bool(args.get("confirm", False), "confirm") is not True:
                raise ValueError("sort_undo requires confirm=true")
            return self._post("/api/files/sort/undo", {"plan_id": args.get("plan_id")})
        raise ValueError("Unknown Jarvis command")


def serve_jsonl(bridge, input_stream=None, output_stream=None) -> int:
    input_stream = input_stream or sys.stdin
    output_stream = output_stream or sys.stdout
    for raw_line in input_stream:
        if len(raw_line) > MAX_JSON_LINE:
            result = {"ok": False, "error": "JSON command is too large"}
        else:
            try:
                request = json.loads(raw_line)
                if not isinstance(request, dict) or not isinstance(request.get("command"), str):
                    raise ValueError("Each command must be an object with a string command")
                command = request.pop("command")
                result = {"ok": True, "result": bridge.execute(command, **request)}
            except json.JSONDecodeError:
                result = {"ok": False, "error": "Invalid JSON"}
            except (ValueError, TypeError, KeyError, OSError) as error:
                result = {"ok": False, "error": str(error)}
        output_stream.write(json.dumps(result, separators=(",", ":")) + "\n")
        output_stream.flush()
    return 0


def _build_local_bridge(data_dir: Path | None, demo: bool):
    temporary = tempfile.TemporaryDirectory(prefix="kshetrajna-jarvis-") if demo else None
    directory = Path(temporary.name) if temporary else (data_dir or default_data_dir())
    directory.mkdir(parents=True, exist_ok=True)
    store = FileStore(directory / "files.db")
    if demo and not store.get_roots():
        seed_file_demo(store)
    service = FileService(store, demo=demo)
    return JarvisCommandBridge(service), temporary


def main(argv=None, default_command=None) -> int:
    parser = argparse.ArgumentParser(description="Kshetrajna loopback/Jarvis command bridge")
    parser.add_argument("command", nargs="?", default=default_command,
                        choices=("discover_folders", "approved_folders", "file_search", "sort_preview", "sort_apply", "sort_undo"))
    parser.add_argument("--url", help="Running loopback dashboard URL, for example http://127.0.0.1:8765")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--jsonl", action="store_true", help="Read newline-delimited JSON commands from stdin")
    parser.add_argument("--path")
    parser.add_argument("--known-folder")
    parser.add_argument("--label")
    parser.add_argument("--root-id", type=int)
    parser.add_argument("--query")
    parser.add_argument("--plan-id")
    parser.add_argument("--create-folders", action="store_true")
    parser.add_argument("--confirm", action="store_true", help="Required for apply and undo")
    args = parser.parse_args(argv)
    if args.url and args.demo:
        parser.error("Choose --url or --demo, not both")
    if args.url:
        bridge, temporary = JarvisHttpClient(args.url), None
    else:
        bridge, temporary = _build_local_bridge(args.data_dir, args.demo)
    try:
        if args.jsonl or args.command is None:
            return serve_jsonl(bridge)
        payload = {key: value for key, value in vars(args).items()
                   if key in {"path", "known_folder", "label", "root_id", "query", "plan_id", "create_folders", "confirm"} and value is not None}
        print(json.dumps({"ok": True, "result": bridge.execute(args.command, **payload), "command": args.command}, indent=2))
        return 0
    except (ValueError, OSError) as error:
        print(json.dumps({"ok": False, "error": str(error)}))
        return 2
    finally:
        if temporary:
            temporary.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
