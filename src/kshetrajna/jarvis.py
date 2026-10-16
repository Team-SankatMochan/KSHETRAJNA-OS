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
import ntpath
import os
import re
import sys
import tempfile
import time
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler

from .config import default_data_dir, opaque_id, public_error
from .file_demo import seed_file_demo
from .file_service import FileService
from .file_storage import FileStore
from .instance import InstanceLock
from .known_folders import discover_known_folders


MAX_JSON_LINE = 64 * 1024


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


def urlopen(request, *, timeout):
    # Keep credentials on the approved loopback endpoint, ignoring environment proxies.
    return build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=timeout)


def _require_bool(value, name: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{name} must be a boolean")
    return value


def _positive_id(value, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    if value > 2**63 - 1:
        raise ValueError(f"{name} is outside the supported database ID range")
    return value


def _cli_positive_id(value: str) -> int:
    try:
        return _positive_id(int(value), "ID")
    except ValueError:
        raise argparse.ArgumentTypeError("must be a positive integer in the database ID range") from None


def _action_id(value, name: str = "action_id") -> str:
    return opaque_id(value, name)


def _root_path_key(path: str) -> str:
    if FileService._validate_root_form(path):
        return ntpath.normcase(ntpath.normpath(path))
    return os.path.normcase(os.path.abspath(path))


def _loopback_base(url: str) -> str:
    try:
        if not isinstance(url, str) or any(character.isspace() for character in url):
            raise ValueError
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        raise ValueError("Invalid loopback dashboard URL") from None
    if parsed.scheme not in ("http", "https") or parsed.hostname not in ("127.0.0.1", "localhost"):
        raise ValueError("Jarvis integration accepts loopback URLs only")
    if parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        raise ValueError("Jarvis integration URL must not contain credentials or query data")
    if port == 0 or parsed.netloc.endswith(":") or parsed.path not in ("", "/"):
        raise ValueError("Use the loopback dashboard base URL with a valid port and no path")
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/")


class JarvisCommandBridge:
    """Dispatch safe sorting/search commands to an existing FileService."""

    _CLEANUP_EMPTY_PREVIEW = frozenset({"empty_preview", "cleanup_empty"})
    _CLEANUP_DUPLICATE_PREVIEW = frozenset({
        "duplicate_preview", "duplicates_preview", "cleanup_duplicates",
    })
    _CLEANUP_EMPTY_DELETE = frozenset({"empty_delete", "cleanup_empty_delete"})
    _CLEANUP_DUPLICATE_DELETE = frozenset({
        "duplicate_delete", "duplicates_delete", "cleanup_duplicate_delete",
    })
    _QUARANTINE_LIST = frozenset({
        "quarantine", "quarantine_list", "quarantine_preview", "quarantine_items",
        "list_quarantine", "preview_quarantine",
    })
    _QUARANTINE_RESTORE = frozenset({
        "quarantine_restore", "restore_quarantine", "restore_quarantined",
    })

    def __init__(self, file_service: FileService):
        self.file_service = file_service

    @classmethod
    def _validate_args(cls, command: str, args: dict) -> None:
        if "consent" in args:
            _require_bool(args["consent"], "consent")
        scope = ("root_id", "path", "known_folder")
        if sum(args.get(key) is not None for key in scope) > 1:
            raise ValueError("Choose only one of root_id, path, or known_folder")
        if args.get("root_id") is not None:
            _positive_id(args["root_id"], "root_id")
        for key in ("path", "known_folder"):
            if args.get(key) is not None and (not isinstance(args[key], str) or not args[key]):
                raise ValueError(f"{key} must be nonempty text")
        if args.get("path") is not None:
            _root_path_key(args["path"])
        if args.get("known_folder") is not None and args.get("consent") is not True:
            raise ValueError("Known-folder discovery for approval requires consent=true (CLI: --consent); use root_id for approved roots")
        if "size_min" in args:
            raise ValueError("size_min is not supported by the command bridge")
        if "action_id" in args and command not in cls._QUARANTINE_RESTORE:
            raise ValueError(f"{command} does not support action_id")
        if command in cls._QUARANTINE_LIST:
            unsupported = ("path", "known_folder", "label", "query", "plan_id", "file_id", "action_id")
            if any(args.get(key) is not None for key in unsupported) or "confirm" in args or "create_folders" in args:
                raise ValueError(f"{command} supports only an optional root_id")
        if command in cls._QUARANTINE_RESTORE:
            unsupported = ("root_id", "path", "known_folder", "label", "query", "plan_id", "file_id")
            if any(args.get(key) is not None for key in unsupported) or "create_folders" in args:
                raise ValueError(f"{command} supports only action_id and confirm")
            _action_id(args.get("action_id"))
            if args.get("confirm") is not True:
                raise ValueError(f"{command} requires confirm=true")
        if command in cls._CLEANUP_EMPTY_DELETE | cls._CLEANUP_DUPLICATE_DELETE:
            _positive_id(args.get("file_id"), "file_id")
            if args.get("confirm") is not True:
                raise ValueError(f"{command} requires confirm=true after reviewing the preview")
        if command == "sort_preview":
            if not any(args.get(key) is not None for key in scope):
                raise ValueError("Provide root_id, path, or known_folder")
            _require_bool(args.get("create_folders", False), "create_folders")
        if command in ("sort_apply", "sort_undo"):
            _action_id(args.get("plan_id"), "plan_id")
            if _require_bool(args.get("confirm", False), "confirm") is not True:
                raise ValueError(f"{command} requires confirm=true")

    def _wait_for_root(self, root_id: int, timeout: float = 60.0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            root = self.file_service.store.get_root(root_id)
            if not root:
                raise ValueError("Approved root disappeared")
            if root["status"] not in ("pending", "scanning"):
                if root["status"] not in ("ready", "truncated"):
                    raise ValueError(f"Folder scan ended with status {root['status']}")
                return root
            time.sleep(0.05)
        raise ValueError("Folder scan timed out")

    def _resolve_root(self, args: dict) -> int:
        root_id = args.get("root_id")
        if root_id is not None:
            self._wait_for_root(root_id)
            return root_id

        path = args.get("path")
        known_id = args.get("known_folder")
        if known_id is not None:
            match = next((folder for folder in discover_known_folders() if folder["id"] == known_id.casefold()), None)
            if not match:
                raise ValueError("Known folder is unavailable; refresh discovery")
            path = match["path"]
        if not isinstance(path, str) or not path:
            raise ValueError("Provide root_id, path, or known_folder")

        path_key = _root_path_key(path)
        for root in self.file_service.store.get_roots():
            try:
                if root["status"] != "removed" and _root_path_key(root["path"]) == path_key:
                    self._wait_for_root(root["id"])
                    return root["id"]
            except ValueError:
                continue
        if args.get("consent") is not True:
            raise ValueError("Approving a new folder requires consent=true (CLI: --consent)")
        added = self.file_service.add_root(path, args.get("label", Path(path).name or "Approved folder"), consent=args["consent"])
        self._wait_for_root(added["id"])
        return added["id"]

    def execute(self, command: str, **args) -> dict:
        self._validate_args(command, args)
        if command == "discover_folders":
            return {"folders": discover_known_folders()}
        if command == "approved_folders":
            return {"roots": self.file_service.state()["roots"]}
        if command == "file_search":
            query = args.get("query")
            if not isinstance(query, str) or len(query) > 200:
                raise ValueError("query must be text of at most 200 characters")
            return self.file_service.search(query, args.get("root_id"))
        if command in self._CLEANUP_EMPTY_PREVIEW:
            root_id = self._resolve_root(args) if any(
                args.get(key) is not None for key in ("path", "known_folder")
            ) else args.get("root_id")
            return self.file_service.get_empty_files(root_id)
        if command in self._CLEANUP_DUPLICATE_PREVIEW:
            root_id = self._resolve_root(args) if any(
                args.get(key) is not None for key in ("path", "known_folder")
            ) else args.get("root_id")
            return self.file_service.get_duplicate_groups(root_id)
        if command in self._CLEANUP_EMPTY_DELETE:
            return self.file_service.delete_empty_file(
                args.get("file_id"), confirm=args.get("confirm", False)
            )
        if command in self._CLEANUP_DUPLICATE_DELETE:
            return self.file_service.delete_duplicate_file(
                args.get("file_id"), confirm=args.get("confirm", False)
            )
        if command in self._QUARANTINE_LIST:
            return {"items": self.file_service.get_quarantine_items(args.get("root_id"))}
        if command in self._QUARANTINE_RESTORE:
            return self.file_service.restore_quarantined_file(
                args.get("action_id"), confirm=True
            )
        if command == "sort_preview":
            root_id = self._resolve_root(args)
            return self.file_service.generate_sort_plan(root_id, _require_bool(args.get("create_folders", False), "create_folders"))
        if command == "sort_apply":
            return self.file_service.apply_sort_plan(args.get("plan_id"), confirm=args.get("confirm"))
        if command == "sort_undo":
            return self.file_service.undo_sort_plan(args.get("plan_id"), confirm=args.get("confirm"))
        raise ValueError("Unknown Jarvis command")


class JarvisHttpClient:
    """Call the running dashboard through its loopback-only API."""

    def __init__(self, base_url: str, timeout: float = 10.0):
        self.base_url = _loopback_base(base_url)
        self.timeout = timeout
        self.token = self._load_token()

    def _load_token(self) -> str:
        try:
            with urlopen(Request(self.base_url + "/", method="GET"), timeout=self.timeout) as response:
                html = response.read(256 * 1024).decode("utf-8")
        except (OSError, ValueError, HTTPException) as error:
            raise ValueError("Could not read the loopback dashboard; check the URL and that it is running") from None
        match = re.search(r'<meta\s+name="csrf-token"\s+content="([^"]+)"', html)
        if not match:
            raise ValueError("Kshetrajna dashboard token was not found")
        return match.group(1)

    def _get(self, path: str) -> dict:
        request = Request(self.base_url + path, method="GET", headers={
            "X-Kshetrajna-Token": self.token,
        })
        return self._request(request)

    def _request(self, request: Request) -> dict:
        try:
            with urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read())
        except HTTPError as error:
            message = "Dashboard request failed"
            try:
                details = json.loads(error.read())
                if isinstance(details, dict) and isinstance(details.get("error"), str):
                    message = details["error"].replace(self.token, "[redacted]")
            except (OSError, ValueError, HTTPException):
                pass
            raise ValueError(message) from None
        except (OSError, ValueError, HTTPException) as error:
            raise ValueError("Dashboard request failed; check that it is running and returning JSON") from None
        if not isinstance(result, dict):
            raise ValueError("Dashboard returned an invalid JSON response")
        return result

    def _resolve_remote_root(self, args: dict):
        root_id = args.get("root_id")
        if root_id is not None:
            return root_id
        known_id = args.get("known_folder")
        if known_id:
            folders = self._get("/api/files/known-folders")["folders"]
            match = next((folder for folder in folders if folder["id"] == known_id.casefold()), None)
            if not match:
                raise ValueError("Known folder is unavailable; refresh discovery")
            args["path"] = match["path"]
        if args.get("path"):
            path = _root_path_key(args["path"])
            roots = self._get("/api/files/state")["roots"]
            for root in roots:
                try:
                    if root["status"] != "removed" and _root_path_key(root["path"]) == path:
                        return root["id"]
                except ValueError:
                    continue
            if args.get("consent") is not True:
                raise ValueError("Approving a new folder requires consent=true (CLI: --consent)")
            added = self._post("/api/files/roots/add", {
                "path": args["path"], "label": args.get("label", Path(args["path"]).name), "consent": args["consent"],
            })
            root_id = added["id"]
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                root = next((root for root in self._get("/api/files/state")["roots"] if root["id"] == root_id), None)
                if root is None:
                    raise ValueError("Approved root disappeared")
                if root["status"] not in ("pending", "scanning"):
                    if root["status"] not in ("ready", "truncated"):
                        raise ValueError(f"Folder scan ended with status {root['status']}")
                    break
                time.sleep(.1)
            else:
                raise ValueError("Folder scan timed out")
            return root_id
        raise ValueError("Provide root_id, path, or known_folder")

    def _cleanup_preview(self, command: str, args: dict) -> dict:
        root_id = self._resolve_remote_root(args) if any(
            args.get(key) is not None for key in ("root_id", "path", "known_folder")
        ) else None
        query = ""
        if root_id is not None:
            query = "?" + urlencode({"root_id": root_id})
        if command in JarvisCommandBridge._CLEANUP_EMPTY_PREVIEW:
            return self._get("/api/files/empty" + query)
        return self._get("/api/files/duplicates" + query)

    def _quarantine_list(self, args: dict) -> dict:
        query = ""
        if args.get("root_id") is not None:
            query = "?" + urlencode({"root_id": args["root_id"]})
        return self._get("/api/files/quarantine" + query)

    def execute(self, command: str, **args) -> dict:
        JarvisCommandBridge._validate_args(command, args)
        if command in JarvisCommandBridge._CLEANUP_EMPTY_PREVIEW | JarvisCommandBridge._CLEANUP_DUPLICATE_PREVIEW:
            return self._cleanup_preview(command, args)
        if command in JarvisCommandBridge._CLEANUP_EMPTY_DELETE:
            return self._post("/api/files/empty/delete", {
                "file_id": args.get("file_id"), "confirm": args.get("confirm", False),
            })
        if command in JarvisCommandBridge._CLEANUP_DUPLICATE_DELETE:
            return self._post("/api/files/duplicates/delete", {
                "file_id": args.get("file_id"), "confirm": args.get("confirm", False),
            })
        if command in JarvisCommandBridge._QUARANTINE_LIST:
            return self._quarantine_list(args)
        if command in JarvisCommandBridge._QUARANTINE_RESTORE:
            return self._post("/api/files/quarantine/restore", {
                "action_id": args.get("action_id"), "confirm": True,
            })
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
            root_id = self._resolve_remote_root(args)
            return self._post("/api/files/sort/preview", {"root_id": root_id, "create_folders": _require_bool(args.get("create_folders", False), "create_folders")})
        if command == "sort_apply":
            return self._post("/api/files/sort/apply", {"plan_id": args.get("plan_id"), "confirm": args.get("confirm")})
        if command == "sort_undo":
            return self._post("/api/files/sort/undo", {"plan_id": args.get("plan_id"), "confirm": args.get("confirm")})
        raise ValueError("Unknown Jarvis command")

    def _post(self, path: str, body: dict) -> dict:
        request = Request(self.base_url + path, data=json.dumps(body).encode("utf-8"), method="POST", headers={
            "Content-Type": "application/json",
            "Origin": self.base_url,
            "X-Kshetrajna-Token": self.token,
        })
        return self._request(request)


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
                result = {"ok": False, "error": public_error(error)}
        output_stream.write(json.dumps(result, separators=(",", ":")) + "\n")
        output_stream.flush()
    return 0


def _build_local_bridge(data_dir: Path | None, demo: bool):
    temporary = tempfile.TemporaryDirectory(prefix="kshetrajna-jarvis-") if demo else None
    directory = Path(temporary.name) if temporary else (data_dir or default_data_dir())
    directory.mkdir(parents=True, exist_ok=True)
    try:
        try:
            instance = InstanceLock(directory)
        except RuntimeError:
            raise ValueError(
                "Another Kshetrajna instance is using this data directory. "
                "Use --url with its loopback dashboard URL, or stop the other instance and retry."
            ) from None
        store = FileStore(directory / "files.db")
        if demo and not store.get_roots():
            seed_file_demo(store)
        service = FileService(store, demo=demo)
        bridge = JarvisCommandBridge(service)
        # Keep the same data-directory lock as the dashboard for the lifetime
        # of this local bridge. URL mode uses the dashboard's existing lock.
        bridge._instance_lock = instance
        return bridge, temporary
    except BaseException:
        if "instance" in locals():
            instance.close()
        if temporary:
            temporary.cleanup()
        raise


def main(argv=None, default_command=None) -> int:
    parser = argparse.ArgumentParser(
        description="Reviewed Sort AI, cleanup, and recovery commands, locally or through the loopback dashboard.",
        epilog=("Preview first: empty_preview and duplicate_preview only list candidates. "
                "Cleanup deletion is reversible quarantine rather than immediate permanent removal and requires --file-id ID --confirm. "
                "Use quarantine_list (or quarantine_preview) to review recovery records; "
                "restore requires --action-id ID --confirm. "
                "A new folder selected with --path or --known-folder requires --consent to approve and scan it. "
                "With kshetrajna-sort (python -m kshetrajna.cli), use cleanup empty|duplicates "
                "or quarantine list|preview|restore and sort preview|apply|undo; sort options alone default to sort_preview, "
                "cleanup options alone to empty_preview. Use --jsonl explicitly for JSON-lines input."))
    parser.add_argument("command", nargs="?", default=default_command,
                        choices=(
                            "discover_folders", "approved_folders", "file_search",
                            "sort_preview", "sort_apply", "sort_undo",
                            *sorted(JarvisCommandBridge._CLEANUP_EMPTY_PREVIEW
                                    | JarvisCommandBridge._CLEANUP_DUPLICATE_PREVIEW
                                    | JarvisCommandBridge._CLEANUP_EMPTY_DELETE
                                    | JarvisCommandBridge._CLEANUP_DUPLICATE_DELETE
                                    | JarvisCommandBridge._QUARANTINE_LIST
                                    | JarvisCommandBridge._QUARANTINE_RESTORE),
                         ))
    parser.add_argument("--url", help="Running loopback dashboard URL, for example http://127.0.0.1:8765")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--jsonl", action="store_true", help="Read newline-delimited JSON commands from stdin")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--path", help="Approve and scan a folder for a preview")
    scope.add_argument("--known-folder", help="Discovered folder ID to approve and scan")
    scope.add_argument("--root-id", type=_cli_positive_id, help="Positive approved root ID; omit for cleanup across approved roots")
    parser.add_argument("--consent", action="store_true", help="Explicitly approve access to a new --path or --known-folder")
    parser.add_argument("--label")
    parser.add_argument("--query")
    parser.add_argument("--plan-id")
    parser.add_argument("--file-id", type=_cli_positive_id, help="Positive file ID returned by a cleanup preview")
    parser.add_argument("--action-id", help="Exact quarantine action ID returned by a cleanup result")
    parser.add_argument("--create-folders", action="store_true")
    parser.add_argument("--confirm", action="store_true", help="Required for apply, undo, cleanup, and quarantine restore")
    args = parser.parse_args(argv)
    if args.url is not None and args.demo:
        parser.error("Choose --url or --demo, not both")
    if args.command is None and not args.jsonl:
        parser.print_help()
        return 0
    temporary = None
    local_bridge = None
    try:
        payload = {key: value for key, value in vars(args).items()
                   if key in {"path", "known_folder", "label", "root_id", "query", "plan_id", "file_id", "action_id", "create_folders", "confirm", "consent"}
                   and value is not None and value is not False}
        if not args.jsonl:
            JarvisCommandBridge._validate_args(args.command, payload)
        if args.url is not None:
            bridge = JarvisHttpClient(args.url)
        else:
            bridge, temporary = _build_local_bridge(args.data_dir, args.demo)
            local_bridge = bridge
        if args.jsonl:
            return serve_jsonl(bridge)
        print(json.dumps({"ok": True, "result": bridge.execute(args.command, **payload), "command": args.command}, indent=2))
        return 0
    except ValueError as error:
        print(json.dumps({"ok": False, "error": public_error(error)}))
        return 2
    except (OSError, HTTPException, TypeError, KeyError):
        print(json.dumps({"ok": False, "error": "Command could not be completed; check the dashboard, paths, and permissions"}))
        return 2
    finally:
        if local_bridge is not None:
            instance = getattr(local_bridge, "_instance_lock", None)
            if instance is not None:
                instance.close()
        if temporary:
            temporary.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
