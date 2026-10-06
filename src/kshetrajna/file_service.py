import os
import json
import threading
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path, PureWindowsPath
import logging

from .file_storage import FileStore
from .file_scanner import (walk, extract_excerpt, TEXT_EXTENSIONS, SENSITIVE_NAMES,
                           SENSITIVE_EXTENSIONS, SENSITIVE_DIRS, ScanTruncated)
from .file_insights import generate_insights
from .file_actions import FileActionManager
from .sort_ai import build_suggestions

PROTECTED_PREFIXES = [
    os.environ.get('SystemRoot', r'C:\Windows').lower(),
    os.environ.get('ProgramFiles', r'C:\Program Files').lower(),
    os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)').lower(),
    os.environ.get('ProgramData', r'C:\ProgramData').lower(),
    r'C:\$Recycle.Bin'.lower(),
    r'C:\System Volume Information'.lower(),
    str(Path(os.environ.get("LOCALAPPDATA", r"C:\Users\Default\AppData\Local")) / "Kshetrajna").lower()
]

class FileService:
    def __init__(self, store: FileStore, demo: bool = False):
        self.store = store
        self.demo = demo
        self.action_manager = FileActionManager(store)
        if not self.demo:
            self.action_manager.reconcile_actions()
        self._gate = threading.RLock()
        self._scan_thread: threading.Thread | None = None
        self._cancel_events: dict[int, threading.Event] = {}
        self._active_scans: set[int] = set()
        self._reconcile_sort_plans()

    def state(self) -> dict:
        with self._gate:
            roots = self.store.get_roots()
            stats = self.store.get_stats()
            return {"mode": "demo" if self.demo else "live", "roots": roots, "stats": stats,
                    "sort_plans": self.store.get_sort_plans()}

    @staticmethod
    def _positive_id(value, name: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"Invalid {name}")
        return value

    @staticmethod
    def _is_reparse_point(path: Path) -> bool:
        try:
            if path.is_symlink():
                return True
            stat_result = path.stat(follow_symlinks=False)
        except FileNotFoundError:
            return False
        except OSError as error:
            raise ValueError("Cannot access path attributes") from error
        return bool(getattr(stat_result, "st_file_attributes", 0) & 0x400)

    @staticmethod
    def _path_is_within(path: Path, parent: Path) -> bool:
        try:
            return path == parent or path.is_relative_to(parent)
        except ValueError:
            return False

    def _validate_root_path(self, path_str: str) -> Path:
        if not isinstance(path_str, str) or not path_str or len(path_str) < 3 or '\0' in path_str:
            raise ValueError("Invalid path format")

        if path_str.startswith((r'\\', "//")):
            raise ValueError("Network and UNC paths are not supported")
        if any(part in (".", "..") for part in path_str.replace('\\', '/').split('/')):
            raise ValueError("Path traversal is not allowed")

        # Keep validation deterministic when the API is exercised on a
        # non-Windows development machine.  pathlib treats ``C:\\`` as a
        # relative POSIX name there, which otherwise hides the more useful
        # drive/protected-path error and makes cross-platform tests brittle.
        has_windows_drive = len(path_str) >= 2 and path_str[0].isalpha() and path_str[1] == ':'
        if has_windows_drive:
            windows_path = PureWindowsPath(path_str)
            windows_normalized = str(windows_path).replace('/', '\\').rstrip('\\').lower()
            if len(windows_path.parts) == 1:
                raise ValueError("Root drives cannot be scanned directly")
            windows_protected = (
                r"c:\windows", r"c:\program files", r"c:\program files (x86)",
                r"c:\programdata", r"c:\$recycle.bin", r"c:\system volume information",
            )
            if any(windows_normalized == protected or windows_normalized.startswith(protected + '\\')
                   for protected in windows_protected):
                raise ValueError("System and protected paths cannot be scanned")
            if os.name != 'nt':
                raise ValueError("Windows paths can only be scanned on Windows")

        lexical_path = Path(path_str)
        # Check the path as supplied before resolve(): resolve() follows a link,
        # so checking only the resolved target would allow a symlink root.
        try:
            if lexical_path.is_symlink() or self._is_reparse_point(lexical_path):
                raise ValueError("Symlinks and reparse points cannot be used as roots")
            path = lexical_path.resolve(strict=True)
        except ValueError:
            raise
        except OSError as error:
            raise ValueError("Path does not exist") from error

        if not path.is_dir():
            raise ValueError("Path must be a directory")

        path_lower = str(path).replace('/', '\\').lower().rstrip('\\/')
        if len(path_lower) == 2 and path_lower[1] == ':':
            raise ValueError("Root drives cannot be scanned directly")

        for protected in PROTECTED_PREFIXES:
            protected_lower = str(protected).replace('/', '\\').lower().rstrip('\\/')
            if path_lower == protected_lower or path_lower.startswith(protected_lower + '\\'):
                raise ValueError("System and protected paths cannot be scanned")

        # Reject links/reparse points in any component of the supplied path,
        # not just the final directory.
        current = lexical_path
        while True:
            if self._is_reparse_point(current):
                raise ValueError("Symlinks and reparse points cannot be used as roots")
            if current.parent == current:
                break
            current = current.parent

        with self._gate:
            roots = self.store.get_roots()
            if len(roots) >= 20:
                raise ValueError("Maximum of 20 roots allowed")

            now_ts = datetime.now(timezone.utc).timestamp()
            recent_creations = [r for r in roots if (now_ts - datetime.fromisoformat(r["created_at"]).timestamp()) < 60]
            if len(recent_creations) >= 5:
                raise ValueError("Rate limit exceeded: max 5 roots per minute")

            for root in roots:
                try:
                    root_path = Path(root["path"]).resolve(strict=True)
                except OSError:
                    # An old root may have disappeared; it cannot overlap a new
                    # root and will be cleaned up by the normal removal path.
                    continue
                if path == root_path:
                    raise ValueError("Root already exists")
                if self._path_is_within(path, root_path):
                    raise ValueError(f"Path is a descendant of existing root: {root['path']}")
                if self._path_is_within(root_path, path):
                    raise ValueError(f"Path is an ancestor of existing root: {root['path']}")

        return path

    def add_root(self, path_str: str, label: str) -> dict:
        if self.demo:
            raise ValueError("Roots cannot be added in demo mode")
            
        with self._gate:
            path = self._validate_root_path(path_str)
            root_id = self.store.add_root(str(path), label)
            self.rescan_root(root_id)
            return self.store.get_root(root_id)

    def rescan_root(self, root_id: int) -> None:
        root_id = self._positive_id(root_id, "root_id")
        with self._gate:
            root = self.store.get_root(root_id)
            if not root:
                raise ValueError("Root not found")
            
            if root_id in self._active_scans:
                raise ValueError("Scan already in progress for this root")

            self._active_scans.add(root_id)
            cancel_event = threading.Event()
            self._cancel_events[root_id] = cancel_event
            self.store.update_root_status(root_id, "scanning")

            if self.demo:
                self._finish_scan(root_id)
                return

            if self._scan_thread is None or not self._scan_thread.is_alive():
                self._scan_thread = threading.Thread(target=self._scan_worker, daemon=True)
                self._scan_thread.start()

    def _scan_worker(self):
        while True:
            with self._gate:
                if not self._active_scans:
                    break
                root_id = next(iter(self._active_scans))
            
            self._run_scan(root_id)
            
            with self._gate:
                self._active_scans.discard(root_id)
                self._cancel_events.pop(root_id, None)

    def _run_scan(self, root_id: int):
        root = self.store.get_root(root_id)
        if not root or root["status"] == "removed":
            return
            
        cancel_event = self._cancel_events.get(root_id)
        is_cancelled = lambda: cancel_event.is_set() if cancel_event else False
        
        start_time = datetime.now(timezone.utc).isoformat()
        try:
            with self.store._connect() as db:
                db.execute("UPDATE scan_state SET started_at=? WHERE root_id=?", (start_time, root_id))
                last_pass = db.execute("SELECT last_pass FROM scan_state WHERE root_id=?", (root_id,)).fetchone()[0]
                current_pass = last_pass + 1
                db.execute("UPDATE scan_state SET last_pass=? WHERE root_id=?", (current_pass, root_id))
            
            root_path = Path(root["path"])
            
            seen = 0
            new = 0
            changed = 0
            errors = 0
            truncated = False

            batch = []

            try:
                entries = walk(root_path, is_cancelled)
                for rel_path, entry, info in entries:
                    if is_cancelled():
                        break

                    seen += 1

                    name = entry.name
                    is_dir = entry.is_dir(follow_symlinks=False)
                    size = info.st_size
                    mtime = datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat()
                    ctime = datetime.fromtimestamp(info.st_ctime, timezone.utc).isoformat()
                    ext = Path(name).suffix[1:].lower() if not is_dir else ""

                    # Check if unchanged
                    with self.store._connect() as db:
                        existing = db.execute("SELECT size_bytes, modified_at, excerpt FROM files WHERE root_id=? AND relative_path=?", (root_id, rel_path)).fetchone()

                    excerpt = ""
                    mime = ""
                    if existing:
                        if existing["size_bytes"] == size and existing["modified_at"] == mtime:
                            excerpt = existing["excerpt"]
                        else:
                            changed += 1
                            excerpt = self._get_excerpt(root_path / rel_path, name, ext, is_dir, rel_path)
                    else:
                        new += 1
                        excerpt = self._get_excerpt(root_path / rel_path, name, ext, is_dir, rel_path)

                    indexed_at = datetime.now(timezone.utc).isoformat()
                    batch.append((root_id, rel_path, name, ext, size, mtime, ctime, int(is_dir), mime, excerpt, current_pass, indexed_at))

                    if len(batch) >= 200:
                        self._flush_batch(batch)
                        batch = []
            except ScanTruncated:
                truncated = True

            if batch and not is_cancelled():
                self._flush_batch(batch)
                
            with self.store._connect() as db:
                end_time = datetime.now(timezone.utc).isoformat()
                
                if is_cancelled():
                    status = "cancelled"
                elif truncated:
                    status = "truncated"
                else:
                    status = "ready"
                    
                    # Clean stale files
                    res = db.execute("DELETE FROM files WHERE root_id=? AND scan_pass < ?", (root_id, current_pass))
                    removed = res.rowcount
                    db.execute("UPDATE scan_state SET files_removed=? WHERE root_id=?", (removed, root_id))
                    
                db.execute("UPDATE scan_state SET finished_at=?, files_seen=?, files_new=?, files_changed=?, errors=? WHERE root_id=?", 
                           (end_time, seen, new, changed, errors, root_id))
                           
                # Update root aggregates
                count = db.execute("SELECT COUNT(*) FROM files WHERE root_id=? AND is_directory=0", (root_id,)).fetchone()[0]
                total_size = db.execute("SELECT SUM(size_bytes) FROM files WHERE root_id=? AND is_directory=0", (root_id,)).fetchone()[0] or 0
                
            self.store.update_root_status(root_id, status)
            self.store.update_root_stats(root_id, count, total_size)
            
        except Exception as e:
            logging.getLogger(__name__).exception("Scan failed")
            self.store.update_root_status(root_id, "error", str(e))
        finally:
            with self._gate:
                if root_id in self._active_scans:
                    self._active_scans.remove(root_id)
                if root_id in self._cancel_events:
                    del self._cancel_events[root_id]

    def _flush_batch(self, batch):
        with self.store._connect() as db:
            db.executemany("""
                INSERT INTO files (root_id, relative_path, name, extension, size_bytes, modified_at, created_at, is_directory, mime_guess, excerpt, scan_pass, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(root_id, relative_path) DO UPDATE SET
                    size_bytes=excluded.size_bytes,
                    modified_at=excluded.modified_at,
                    excerpt=excluded.excerpt,
                    scan_pass=excluded.scan_pass,
                    indexed_at=excluded.indexed_at
            """, batch)

    def _get_excerpt(self, full_path: Path, name: str, ext: str, is_dir: bool, rel_path: str) -> str:
        if is_dir:
            return ""
            
        if name in SENSITIVE_NAMES or ext in SENSITIVE_EXTENSIONS:
            return ""
            
        parts = rel_path.split('/')
        if any(d in SENSITIVE_DIRS for d in parts):
            return ""
            
        if ext in TEXT_EXTENSIONS:
            return extract_excerpt(full_path)
            
        return ""

    def cancel_scan(self, root_id: int) -> None:
        root_id = self._positive_id(root_id, "root_id")
        with self._gate:
            root = self.store.get_root(root_id)
            if not root:
                raise ValueError("Root not found")
            if root_id in self._cancel_events:
                self._cancel_events[root_id].set()

    def remove_root(self, root_id: int) -> None:
        root_id = self._positive_id(root_id, "root_id")
        with self._gate:
            root = self.store.get_root(root_id)
            if not root:
                raise ValueError("Root not found")
            if self.store.has_unresolved_file_actions(root_id):
                raise ValueError("Resolve file action recovery records before removing this root")
                
            self.store.update_root_status(root_id, "removed")
            if root_id in self._cancel_events:
                self._cancel_events[root_id].set()
                
            # Wait for scan to exit before cascade delete
            if root_id in self._active_scans:
                def delayed_delete():
                    while root_id in self._active_scans:
                        time.sleep(0.1)
                    self.store.remove_root(root_id)
                threading.Thread(target=delayed_delete, daemon=True).start()
            else:
                self.store.remove_root(root_id)

    def delete_all(self) -> None:
        with self._gate:
            for root_id in list(self._cancel_events.keys()):
                self._cancel_events[root_id].set()
            
        while self._active_scans:
            time.sleep(0.1)
                
        with self._gate:
            self.store.clear_all()

    @staticmethod
    def _sort_plan_view(plan: dict) -> dict:
        result = dict(plan)
        result["create_folders"] = bool(result.get("create_folders"))
        try:
            result["created_dirs"] = json.loads(result.get("created_dirs") or "[]")
        except (TypeError, json.JSONDecodeError):
            result["created_dirs"] = []
        result["suggestions"] = [dict(s) for s in result.get("suggestions", [])]
        for suggestion in result["suggestions"]:
            suggestion["selected"] = bool(suggestion.get("selected"))
        return result

    def _sort_plan(self, plan_id: str) -> dict:
        if not isinstance(plan_id, str) or not plan_id or len(plan_id) > 100:
            raise ValueError("Invalid sort plan")
        plan = self.store.get_sort_plan(plan_id)
        if not plan:
            raise ValueError("Sort plan not found")
        return plan

    def generate_sort_plan(self, root_id: int, create_folders: bool = False) -> dict:
        root_id = self._positive_id(root_id, "root_id")
        if not isinstance(create_folders, bool):
            raise ValueError("create_folders must be a boolean")
        with self._gate:
            root = self.store.get_root(root_id)
            if not root:
                raise ValueError("Root not found")
            if root["status"] != "ready":
                raise ValueError("Wait for a complete scan before generating Sort AI suggestions")
            files = self.store.get_sortable_files(root_id)
            suggestions = build_suggestions(files, Path(root["path"]), create_folders=create_folders)
            now = datetime.now(timezone.utc)
            plan = {
                "id": uuid.uuid4().hex,
                "root_id": root_id,
                "created_at": now.isoformat(),
                "expires_at": (now + timedelta(minutes=5)).isoformat(),
                "status": "preview",
                "mode": "demo" if self.demo else "live",
                "create_folders": create_folders,
                "created_dirs": "[]",
                "error": None,
            }
            for suggestion in suggestions:
                suggestion["id"] = uuid.uuid4().hex
                suggestion["created_at"] = now.isoformat()
            self.store.save_sort_plan(plan, suggestions)
            return self._sort_plan_view({**plan, "suggestions": suggestions})

    def update_sort_plan(self, plan_id: str, suggestions: list[dict]) -> dict:
        if not isinstance(suggestions, list) or len(suggestions) > 500:
            raise ValueError("Suggestions must be a list of at most 500 items")
        with self._gate:
            plan = self._sort_plan(plan_id)
            if plan["status"] not in ("preview", "partial"):
                raise ValueError(f"Sort plan cannot be edited in status {plan['status']}")
            if datetime.now(timezone.utc) > datetime.fromisoformat(plan["expires_at"]):
                self.store.update_sort_plan(plan_id, {"status": "expired"})
                raise ValueError("Sort plan expired")
            root = self.store.get_root(plan["root_id"])
            if not root:
                raise ValueError("Root not found")
            known = {row["id"]: row for row in plan["suggestions"]}
            for patch in suggestions:
                if not isinstance(patch, dict) or patch.get("id") not in known:
                    raise ValueError("Unknown sort suggestion")
                if type(patch.get("selected")) is not bool:
                    raise ValueError("selected must be a boolean")
                destination = patch.get("destination_relative", known[patch["id"]]["destination_relative"])
                if not isinstance(destination, str) or len(destination) > 240:
                    raise ValueError("Invalid destination path")
                destination = destination.replace('\\', '/')
                self.action_manager.validate_move_proposal(destination)
                source = known[patch["id"]]["source_relative"]
                blocked = None
                destination_path = Path(root["path"]) / destination
                if destination.lower() == source.lower():
                    blocked = "Destination is unchanged"
                elif destination_path.exists():
                    blocked = "A file already exists at the proposed destination"
                if not destination_path.parent.is_dir() and not plan["create_folders"]:
                    blocked = "Destination folder does not exist; enable folder creation for this plan"
                self.store.update_sort_suggestion(patch["id"], {
                    "destination_relative": destination,
                    "selected": int(patch["selected"] and blocked is None),
                    "status": "blocked" if blocked else "suggested",
                    "error": blocked,
                })
            return self._sort_plan_view(self._sort_plan(plan_id))

    def _prepare_sort_directories(self, plan: dict, selected: list[dict], root_path: Path) -> list[str]:
        required: set[Path] = set()
        for suggestion in selected:
            required.add((root_path / suggestion["destination_relative"]).parent)
        missing: set[Path] = set()
        for directory in required:
            current = directory
            while not current.exists():
                missing.add(current)
                current = current.parent
            self.action_manager._validate_path_safety(root_path, directory)
        if missing and not plan["create_folders"]:
            raise ValueError("A destination folder is missing; regenerate with folder creation enabled")
        created = sorted((str(path.relative_to(root_path)).replace('\\', '/') for path in missing), key=lambda value: value.count('/'))
        # The plan record is durable before any directory or file mutation.
        self.store.update_sort_plan(plan["id"], {"status": "applying", "created_dirs": json.dumps(created)})
        for directory in sorted(missing, key=lambda path: len(path.parts)):
            self.action_manager._validate_path_safety(root_path, directory)
            directory.mkdir(exist_ok=False)
        return created

    def _cleanup_sort_directories(self, plan: dict, root_path: Path) -> None:
        try:
            directories = json.loads(plan.get("created_dirs") or "[]")
        except (TypeError, json.JSONDecodeError):
            directories = []
        for relative in sorted(directories, key=lambda value: value.count('/'), reverse=True):
            directory = root_path / relative
            try:
                self.action_manager._validate_path_safety(root_path, directory)
                directory.rmdir()
            except (OSError, ValueError):
                # A non-empty or changed directory is deliberately left for
                # the user; cleanup never removes user-created content.
                continue

    def apply_sort_plan(self, plan_id: str) -> dict:
        with self._gate:
            plan = self._sort_plan(plan_id)
            if plan["status"] not in ("preview", "partial"):
                raise ValueError(f"Sort plan cannot be applied in status {plan['status']}")
            if datetime.now(timezone.utc) > datetime.fromisoformat(plan["expires_at"]):
                self.store.update_sort_plan(plan_id, {"status": "expired"})
                raise ValueError("Sort plan expired")
            root = self.store.get_root(plan["root_id"])
            if not root or root["status"] != "ready":
                raise ValueError("Root is not ready for organization")
            selected = [s for s in plan["suggestions"] if s["selected"] and s["status"] in ("suggested", "blocked")]
            if not selected:
                raise ValueError("Select at least one unblocked suggestion")

            root_path = Path(root["path"]).resolve(strict=True) if not self.demo else Path(root["path"])
            try:
                if self.demo:
                    # Demo plans exercise the same journal and selection flow,
                    # but never create directories or touch the filesystem.
                    self.store.update_sort_plan(plan["id"], {"status": "applying", "created_dirs": "[]"})
                else:
                    self._prepare_sort_directories(plan, selected, root_path)
            except Exception as error:
                self.store.update_sort_plan(plan_id, {"status": "failed", "error": str(error)})
                raise

            applied = []
            failed = []
            for suggestion in selected:
                try:
                    action = self.action_manager.create_proposal(
                        suggestion["file_id"], "MOVE", suggestion["destination_relative"], plan["mode"]
                    )
                    self.store.update_sort_suggestion(suggestion["id"], {"status": "applying", "action_id": action["id"], "error": None})
                    result = self.action_manager.apply_action(action["proposal_id"])
                    if result.get("status") != "applied":
                        raise ValueError(result.get("message", "File action did not complete"))
                    self.store.update_sort_suggestion(suggestion["id"], {"status": "applied", "error": None})
                    applied.append({"suggestion_id": suggestion["id"], "action_id": action["id"], "status": "applied"})
                except (OSError, ValueError, TypeError) as error:
                    self.store.update_sort_suggestion(suggestion["id"], {"status": "failed", "error": str(error)})
                    failed.append({"suggestion_id": suggestion["id"], "status": "failed", "error": str(error)})

            status = "applied" if applied and not failed else "partial" if applied else "failed"
            self.store.update_sort_plan(plan_id, {"status": status, "error": None if not failed else "Some suggestions could not be applied"})
            if not applied:
                self._cleanup_sort_directories({**plan, "created_dirs": json.dumps(json.loads(self.store.get_sort_plan(plan_id)["created_dirs"]))}, root_path)
            return {"plan": self._sort_plan_view(self._sort_plan(plan_id)), "status": status,
                    "applied": applied, "failed": failed}

    def undo_sort_plan(self, plan_id: str) -> dict:
        with self._gate:
            plan = self._sort_plan(plan_id)
            if plan["status"] not in ("applied", "partial", "undo_partial"):
                raise ValueError(f"Sort plan cannot be undone in status {plan['status']}")
            root = self.store.get_root(plan["root_id"])
            if not root:
                raise ValueError("Root not found")
            rolled_back = []
            failed = []
            for suggestion in reversed(plan["suggestions"]):
                if suggestion["status"] != "applied" or not suggestion.get("action_id"):
                    continue
                try:
                    result = self.action_manager.undo_action(suggestion["action_id"])
                    if result.get("status") != "rolled_back":
                        raise ValueError(result.get("message", "Undo did not complete"))
                    self.store.update_sort_suggestion(suggestion["id"], {"status": "rolled_back", "error": None})
                    rolled_back.append(suggestion["id"])
                except (OSError, ValueError, TypeError) as error:
                    self.store.update_sort_suggestion(suggestion["id"], {"status": "undo_failed", "error": str(error)})
                    failed.append({"suggestion_id": suggestion["id"], "status": "undo_failed", "error": str(error)})
            status = "rolled_back" if not failed else "undo_partial"
            self.store.update_sort_plan(plan_id, {"status": status, "error": None if not failed else "Some file actions need manual recovery"})
            cleanup_root = Path(root["path"]).resolve(strict=True) if not self.demo else Path(root["path"])
            self._cleanup_sort_directories(self._sort_plan(plan_id), cleanup_root)
            return {"plan": self._sort_plan_view(self._sort_plan(plan_id)), "status": status,
                    "rolled_back": rolled_back, "failed": failed}

    def _reconcile_sort_plans(self) -> None:
        """Reconcile a crash between an individual action and plan bookkeeping."""
        for summary in self.store.get_sort_plans():
            if summary["status"] != "applying":
                continue
            plan = self.store.get_sort_plan(summary["id"])
            if not plan:
                continue
            for suggestion in plan["suggestions"]:
                action_id = suggestion.get("action_id")
                if not action_id or suggestion["status"] != "applying":
                    continue
                action = self.store.get_file_action(action_id=action_id)
                if action and action["status"] == "applied":
                    self.store.update_sort_suggestion(suggestion["id"], {"status": "applied", "error": None})
                elif action:
                    self.store.update_sort_suggestion(suggestion["id"], {"status": "failed", "error": action.get("error") or action["status"]})
            refreshed = self.store.get_sort_plan(summary["id"])
            statuses = [s["status"] for s in refreshed["suggestions"]]
            if statuses and all(status == "applied" for status in statuses if status not in ("blocked", "unselected")):
                self.store.update_sort_plan(summary["id"], {"status": "applied"})
            else:
                self.store.update_sort_plan(summary["id"], {"status": "partial"})

    def get_root_details(self, root_id: int) -> dict:
        root_id = self._positive_id(root_id, "root_id")
        with self._gate:
            root = self.store.get_root(root_id)
            if not root:
                raise ValueError("Root not found")
            recent = self.store.get_recent_files(root_id)
            return {"root": root, "recent_files": recent}

    def search(self, query: str, root_id: int | None = None) -> dict:
        if not isinstance(query, str):
            raise ValueError("Search query must be text")
        if root_id is not None:
            root_id = self._positive_id(root_id, "root_id")
        with self._gate:
            results = self.store.search_files(query, root_id)
            for result in results:
                if result.get("root_path"):
                    result["path"] = str(Path(result["root_path"]) / result["relative_path"])
            return {"results": results}

    def get_insights(self) -> dict:
        with self._gate:
            return self.store.get_insights_data()

    def get_file_excerpt(self, file_id: int) -> dict:
        file_id = self._positive_id(file_id, "file_id")
        with self._gate:
            file = self.store.get_file(file_id)
            if not file:
                raise ValueError("File not found")
                
            excerpt = file.pop("excerpt")
            root = self.store.get_root(file["root_id"])
            if root:
                file["path"] = str(Path(root["path"]) / file["relative_path"])
            file["insights"] = generate_insights(file["name"], file["extension"], file["relative_path"], excerpt)
            return {"file": file, "excerpt": excerpt}

    def _finish_scan(self, root_id: int):
        with self._gate:
            self.store.update_root_status(root_id, "ready")
            self._active_scans.discard(root_id)
            self._cancel_events.pop(root_id, None)

    # --- File Actions ---
    def _assert_no_scan(self, root_id: int):
        with self._gate:
            if root_id in self._active_scans:
                raise ValueError("Wait for the current scan to finish before changing files.")

    def create_proposal(self, file_id: int, kind: str, destination_relative: str) -> dict:
        file_id = self._positive_id(file_id, "file_id")
        mode = "demo" if self.demo else "live"
        file_row = self.store.get_file(file_id)
        if file_row:
            self._assert_no_scan(file_row["root_id"])
        return self.action_manager.create_proposal(file_id, kind, destination_relative, mode)

    def apply_action(self, proposal_id: str) -> dict:
        action = self.store.get_file_action(proposal_id=proposal_id)
        if action:
            self._assert_no_scan(action["root_id"])
        with self._gate:
            return self.action_manager.apply_action(proposal_id)

    def undo_action(self, action_id: str) -> dict:
        action = self.store.get_file_action(action_id=action_id)
        if action:
            self._assert_no_scan(action["root_id"])
        with self._gate:
            return self.action_manager.undo_action(action_id)

    def dismiss_proposal(self, proposal_id: str) -> None:
        self.action_manager.dismiss_proposal(proposal_id)

    def get_action_journal(self) -> list[dict]:
        return self.action_manager.get_journal()
