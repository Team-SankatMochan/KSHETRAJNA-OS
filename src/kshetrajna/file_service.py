import os
import stat
import json
import threading
import uuid
import hashlib
from datetime import datetime, timezone, timedelta
import pathlib
from pathlib import Path, PureWindowsPath
import logging

from .file_storage import FileStore, QUARANTINE_DIR_NAME
from .file_scanner import (walk, extract_excerpt, extract_office_excerpt, TEXT_EXTENSIONS,
                           ScanTruncated,
                           is_sensitive_file, INSPECTION_VERSION)
from .file_insights import generate_insights, analyze_catalog, OFFICE_EXTENSIONS, PROJECT_MARKERS
from .file_actions import FileActionManager, PROTECTED_ACTION_NAMES
from .sort_ai import build_suggestions, automation_eligible
from .organization import organization_plan, folder_inventory
from .image_preview import IMAGE_EXTENSIONS, MAX_IMAGE_BYTES, inspect_image
from .pdf_preview import PDF_EXTENSIONS, MAX_PDF_BYTES, inspect_pdf, extract_pdf_excerpt

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
    _SCAN_STOP_TIMEOUT = 10

    def __init__(self, store: FileStore, demo: bool = False):
        self.store = store
        self.demo = demo
        self.action_manager = FileActionManager(store)
        if not self.demo:
            self.action_manager.reconcile_actions()
        self._gate = threading.RLock()
        self._scan_done = threading.Condition(self._gate)
        self._catalog_deleting = False
        self._scan_thread: threading.Thread | None = None
        self._cancel_events: dict[int, threading.Event] = {}
        self._active_scans: set[int] = set()
        self._reconcile_sort_plans()

    def state(self) -> dict:
        with self._gate:
            roots = self.store.get_roots()
            with self.store._connect() as db:
                progress = {row['root_id']: dict(row) for row in db.execute('SELECT * FROM scan_state')}
            for root in roots:
                root['scan'] = progress.get(root['id'], {})
            stats = self.store.get_stats()
            return {"mode": "demo" if self.demo else "live", "roots": roots, "stats": stats,
                    "sort_plans": self.store.get_sort_plans()}

    @staticmethod
    def _positive_id(value, name: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= 2**63 - 1:
            raise ValueError(f"Invalid {name}")
        return value

    def _assert_catalog_available(self):
        if self._catalog_deleting:
            raise ValueError("File catalog deletion is in progress; retry when it finishes")

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

    def _validate_root_path(self, path_str: str, *, batch=False) -> Path:
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
            if len(recent_creations) >= 5 and not batch:
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
            self._assert_catalog_available()
            path = self._validate_root_path(path_str)
            root_id = self.store.add_root(str(path), label)
            self.rescan_root(root_id)
            return self.store.get_root(root_id)

    def approve_locations(self, locations, *, consent=False) -> dict:
        if self.demo or consent is not True or not isinstance(locations, list) or not 1 <= len(locations) <= 20:
            raise ValueError('Approve between 1 and 20 local locations with consent=true')
        results, skipped = [], []
        with self._gate:
            self._assert_catalog_available()
            for item in locations:
                if not isinstance(item, dict) or not isinstance(item.get('label', ''), str):
                    raise ValueError('Invalid location')
                try:
                    path = self._validate_root_path(item.get('path'), batch=True)
                    root_id = self.store.add_root(str(path), item.get('label', path.name)[:100])
                    with self.store._connect() as db:
                        db.execute('UPDATE roots SET scan_limit=200000 WHERE id=?', (root_id,))
                    self.rescan_root(root_id)
                    results.append(self.store.get_root(root_id))
                except (ValueError, OSError) as error:
                    skipped.append({'path': item.get('path'), 'reason': str(error)})
        return {'approved': results, 'skipped': skipped}

    def get_organization(self) -> dict:
        with self._gate:
            projects = self.store.get_projects()
            roots = {r['id']: r for r in self.store.get_roots()}
            for project in projects:
                project['root_path'] = roots.get(project['root_id'], {}).get('path', '')
            return {'projects': projects, 'folders': folder_inventory(self.store, inspect=not self.demo),
                    'roots': list(roots.values())}

    def save_project_scope(self, name, aliases, root_id, folder, *, confirm=False) -> dict:
        if confirm is not True or not isinstance(name, str) or not 2 <= len(name.strip()) <= 80:
            raise ValueError('Project scope needs a name and confirm=true')
        if not isinstance(aliases, list) or len(aliases) > 12 or any(not isinstance(a, str) or not 2 <= len(a.strip()) <= 80 for a in aliases):
            raise ValueError('Use at most 12 project aliases of 2–80 characters')
        root_id = self._positive_id(root_id, 'root_id')
        if not isinstance(folder, str) or len(folder) > 200:
            raise ValueError('Invalid project folder')
        self.action_manager.validate_move_proposal(folder + '/scope-check.txt')
        with self._gate:
            root = self.store.get_root(root_id)
            if not root or root['status'] not in ('ready', 'truncated'):
                raise ValueError('Approve and scan the project destination first')
            if not self.demo:
                self.action_manager._validate_path_safety(Path(root['path']), Path(root['path']) / folder)
            project_id = self.store.save_project(name.strip(), [a.strip() for a in aliases], root_id, folder)
            return {'id': project_id, 'saved': True}

    def remove_project_scope(self, project_id) -> dict:
        project_id = self._positive_id(project_id, 'project_id')
        with self._gate, self.store._connect() as db:
            db.execute('DELETE FROM organization_projects WHERE id=?', (project_id,))
            # Existing previews may refer to the removed rule. Require a fresh review.
            db.execute("UPDATE sort_plans SET status='expired' WHERE status IN ('preview','partial')")
        return {'removed': True}

    def set_folder_watch(self, root_id, enabled) -> dict:
        root_id = self._positive_id(root_id, 'root_id')
        if self.demo or type(enabled) is not bool:
            raise ValueError('Watching requires a live approved folder and a boolean enabled value')
        with self._gate:
            root = self.store.get_root(root_id)
            if not root or root['status'] == 'removed':
                raise ValueError('Approved root not found')
            if enabled:
                self.action_manager._validate_path_safety(Path(root['path']), Path(root['path']))
            with self.store._connect() as db:
                db.execute('UPDATE roots SET watch_enabled=?,watch_message=? WHERE id=?',
                           (int(enabled), 'Waiting for settled new or changed files.' if enabled else 'Watching paused.', root_id))
            return self.store.get_root(root_id)

    def rescan_root(self, root_id: int) -> None:
        root_id = self._positive_id(root_id, "root_id")
        with self._gate:
            self._assert_catalog_available()
            root = self.store.get_root(root_id)
            if not root or root['status'] == 'removed':
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
                    self._scan_thread = None
                    return
                root_id = next(iter(self._active_scans))
            
            self._run_scan(root_id)
            
            # _run_scan publishes status and clears its gate atomically. Do
            # not erase a rescan requested after that terminal status appears.

    def _run_scan(self, root_id: int):
        status, note = "error", None
        count = total_size = None
        try:
            root = self.store.get_root(root_id)
            if not root or root["status"] == "removed":
                status = "removed"
                return

            cancel_event = self._cancel_events.get(root_id)
            is_cancelled = lambda: cancel_event.is_set() if cancel_event else False
            start_time = datetime.now(timezone.utc).isoformat()
            with self.store._connect() as db:
                db.execute("UPDATE scan_state SET started_at=?, finished_at=NULL, files_seen=0 WHERE root_id=?", (start_time, root_id))
                last_pass = db.execute("SELECT last_pass FROM scan_state WHERE root_id=?", (root_id,)).fetchone()[0]
                current_pass = last_pass + 1
                db.execute("UPDATE scan_state SET last_pass=? WHERE root_id=?", (current_pass, root_id))
            
            root_path = Path(root["path"])
            self.action_manager._validate_path_safety(root_path, root_path)
            if not root_path.is_dir():
                raise ValueError("Approved folder is unavailable")
            
            seen = 0
            new = 0
            changed = 0
            errors = 0
            truncated = False

            batch = []
            scan_notes = {}
            with self.store._connect() as db:
                previous_files = {row['relative_path']: dict(row) for row in db.execute(
                    'SELECT relative_path,size_bytes,modified_at,excerpt,inspection_version FROM files WHERE root_id=?', (root_id,))}

            try:
                entries = (walk(root_path, is_cancelled, scan_notes) if root.get('scan_limit', 50000) == 50000
                           else walk(root_path, is_cancelled, scan_notes, max_files=root['scan_limit']))
                for rel_path, entry, info in entries:
                    if is_cancelled():
                        break

                    if self._is_quarantine_relative(rel_path):
                        # Quarantine contents are app-managed recovery data, not
                        # user files and must never enter the file catalog.
                        continue

                    seen += 1

                    name = entry.name
                    is_dir = entry.is_dir(follow_symlinks=False)
                    size = info.st_size
                    mtime = datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat()
                    ctime = datetime.fromtimestamp(info.st_ctime, timezone.utc).isoformat()
                    ext = Path(name).suffix[1:].lower() if not is_dir else ""

                    # Check if unchanged
                    existing = previous_files.get(rel_path)

                    excerpt = ""
                    mime = ""
                    if existing:
                        if (existing["size_bytes"] == size and existing["modified_at"] == mtime
                                and existing["inspection_version"] == INSPECTION_VERSION):
                            excerpt = existing["excerpt"]
                        else:
                            changed += 1
                            excerpt = self._get_excerpt(root_path / rel_path, name, ext, is_dir, rel_path)
                    else:
                        new += 1
                        excerpt = self._get_excerpt(root_path / rel_path, name, ext, is_dir, rel_path)

                    indexed_at = datetime.now(timezone.utc).isoformat()
                    batch.append((root_id, rel_path, name, ext, size, mtime, ctime, int(is_dir), mime, excerpt, current_pass, indexed_at, INSPECTION_VERSION))

                    if len(batch) >= 200:
                        self._flush_batch(batch)
                        with self.store._connect() as db:
                            db.execute('UPDATE scan_state SET files_seen=? WHERE root_id=?', (seen, root_id))
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
                    
                errors += scan_notes.get('unreadable', 0)
                db.execute("UPDATE scan_state SET finished_at=?, files_seen=?, files_new=?, files_changed=?, errors=? WHERE root_id=?", 
                           (end_time, seen, new, changed, errors, root_id))
                           
                # Update root aggregates
                count = db.execute("SELECT COUNT(*) FROM files WHERE root_id=? AND is_directory=0", (root_id,)).fetchone()[0]
                total_size = db.execute("SELECT SUM(size_bytes) FROM files WHERE root_id=? AND is_directory=0", (root_id,)).fetchone()[0] or 0
                
            messages = []
            for key, description in (('linked_or_cloud', 'linked/cloud-only entries'), ('excluded_folders', 'protected/generated folders'),
                                     ('unreadable', 'unreadable entries'), ('depth_limit', 'folders beyond the depth limit')):
                if scan_notes.get(key):
                    messages.append(f"{scan_notes[key]} {description}")
            note = 'Skipped ' + ', '.join(messages) + '.' if messages else None
            
        except Exception as e:
            logging.getLogger(__name__).exception("Scan failed")
            status, note = "error", str(e)
        finally:
            self._finish_scan(root_id, status, error=note, file_count=count, total_size=total_size)

    def _flush_batch(self, batch):
        with self.store._connect() as db:
            db.executemany("""
                INSERT INTO files (root_id, relative_path, name, extension, size_bytes, modified_at, created_at, is_directory, mime_guess, excerpt, scan_pass, indexed_at, inspection_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(root_id, relative_path) DO UPDATE SET
                    size_bytes=excluded.size_bytes,
                    modified_at=excluded.modified_at,
                    excerpt=excluded.excerpt,
                    scan_pass=excluded.scan_pass,
                    indexed_at=excluded.indexed_at,
                    inspection_version=excluded.inspection_version
            """, batch)

    def _get_excerpt(self, full_path: Path, name: str, ext: str, is_dir: bool, rel_path: str) -> str:
        if is_dir:
            return ""
            
        if is_sensitive_file(name, ext, rel_path):
            return ""
            
        if ext in TEXT_EXTENSIONS:
            return extract_excerpt(full_path)
        if ext in OFFICE_EXTENSIONS:
            return extract_office_excerpt(full_path)
        if ext in PDF_EXTENSIONS:
            try:
                if full_path.stat().st_size > MAX_PDF_BYTES:
                    return ''
                return extract_pdf_excerpt(full_path.read_bytes())
            except (OSError, ValueError):
                return ""
            
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
                
            # Active scans finish the cascade under this gate after their last write.
            if root_id not in self._active_scans:
                self.store.remove_root(root_id)

    def delete_all(self) -> None:
        with self._gate:
            self._assert_catalog_available()
            self._catalog_deleting = True
            try:
                for event in self._cancel_events.values():
                    event.set()
                # Condition.wait_for releases the gate so scans can finish.
                if not self._scan_done.wait_for(lambda: not self._active_scans,
                                                timeout=self._SCAN_STOP_TIMEOUT):
                    raise ValueError("Timed out waiting for scans to stop; retry deleting the file catalog")
                self.store.clear_all()
            finally:
                self._catalog_deleting = False

    def _sort_plan_view(self, plan: dict) -> dict:
        result = dict(plan)
        root = self.store.get_root(result["root_id"])
        result["root_path"] = root["path"] if root else ""
        result["root_label"] = root["label"] if root else ""
        root_path = Path(result["root_path"])
        result["create_folders"] = bool(result.get("create_folders"))
        try:
            result['report'] = json.loads(result.get('report') or '{}')
        except (TypeError, json.JSONDecodeError):
            result['report'] = {}
        try:
            result["created_dirs"] = json.loads(result.get("created_dirs") or "[]")
        except (TypeError, json.JSONDecodeError):
            result["created_dirs"] = []
        result["suggestions"] = [dict(s) for s in result.get("suggestions", [])]
        folder_cache = {}
        approved_roots = {r['id']: r for r in self.store.get_roots()}
        for suggestion in result["suggestions"]:
            source_root = approved_roots.get(suggestion.get('source_root_id') or result['root_id'])
            destination_root = approved_roots.get(suggestion.get('destination_root_id') or result['root_id'])
            suggestion['source_root_path'] = source_root['path'] if source_root else ''
            suggestion['destination_root_path'] = destination_root['path'] if destination_root else ''
            suggestion['destination_root_label'] = destination_root['label'] if destination_root else 'Unavailable'
            suggestion["selected"] = bool(suggestion.get("selected"))
            suggestion['automation_eligible'] = automation_eligible(suggestion)
            normalized = suggestion["destination_relative"].replace("\\", "/")
            folder = normalized.rsplit("/", 1)[0] if '/' in normalized else ''
            suggestion["destination_folder"] = folder
            if self.demo:
                # Demo paths are synthetic; displaying an idea must not inspect
                # a matching real filesystem path on the machine running it.
                suggestion["destination_folder_exists"] = folder in result["created_dirs"]
            else:
                try:
                    target_root = Path(suggestion['destination_root_path'])
                    destination = target_root / folder
                    key = (str(target_root), folder)
                    if key not in folder_cache:
                        self.action_manager._validate_path_safety(target_root, destination)
                        folder_cache[key] = destination.is_dir()
                    suggestion["destination_folder_exists"] = folder_cache[key]
                except (OSError, ValueError):
                    suggestion["destination_folder_exists"] = False
        result['automatic_count'] = sum(s['automation_eligible'] for s in result['suggestions'])
        result['review_count'] = sum(s['status'] in ('suggested', 'blocked', 'failed') and not s['automation_eligible'] for s in result['suggestions'])
        result['batch_limit'] = 500
        source_root_ids = result.get('report', {}).get('source_roots') or [result['root_id']]
        partial_roots = [approved_roots[root_id] for root_id in source_root_ids
                         if approved_roots.get(root_id, {}).get('status') == 'truncated']
        result['scan_partial'] = bool(partial_roots)
        result['partial_source_roots'] = [
            {'id': partial['id'], 'label': partial['label'], 'path': partial['path']}
            for partial in partial_roots
        ]
        return result

    def _sort_plan(self, plan_id: str) -> dict:
        if not isinstance(plan_id, str) or not plan_id or len(plan_id) > 100:
            raise ValueError("Invalid sort plan")
        plan = self.store.get_sort_plan(plan_id)
        if not plan:
            raise ValueError("Sort plan not found")
        return plan

    def generate_sort_plan(self, root_id: int, create_folders: bool = False, *, prefer_existing=True,
                           include_nested=False, route_downloads_to_desktop=True, root_ids=None) -> dict:
        root_id = self._positive_id(root_id, "root_id")
        if any(type(value) is not bool for value in (create_folders, prefer_existing, include_nested,
                                                     route_downloads_to_desktop)):
            raise ValueError("Sort preferences must be boolean")
        with self._gate:
            root = self.store.get_root(root_id)
            if not root:
                raise ValueError("Root not found")
            if root["status"] not in ("ready", "truncated"):
                raise ValueError("Wait for a complete scan before generating Sort AI suggestions")
            ids = [root_id] if root_ids is None else root_ids
            if not isinstance(ids, list) or not 1 <= len(ids) <= 20 or any(type(i) is not int or i <= 0 for i in ids):
                raise ValueError('Choose 1–20 approved source roots')
            roots = [self.store.get_root(i) for i in dict.fromkeys(ids)]
            if any(not r or r['status'] not in ('ready', 'truncated') for r in roots):
                raise ValueError('Wait for the selected scans to finish')
            inventory = folder_inventory(self.store, inspect=not self.demo)
            approved_roots = {r['id']: r for r in self.store.get_roots()}
            projects = [{**p, 'root_path': approved_roots[p['root_id']]['path']}
                        for p in self.store.get_projects()
                        if p['root_id'] in approved_roots
                        and approved_roots[p['root_id']]['status'] in ('ready', 'truncated')]
            suggestions = []
            report = {'considered': 0, 'kept': 0, 'suggested': 0, 'advice': 0, 'remembered_keep': 0,
                      'reasons': {}, 'project_candidates': [], 'source_roots': [r['id'] for r in roots]}
            for source_root in roots:
                files = self.store.get_sortable_files(source_root['id'])
                items, accounting = organization_plan(files, source_root, projects, inventory,
                    create_folders=create_folders, inspect=not self.demo, prefer_existing=prefer_existing,
                    include_nested=include_nested, route_downloads_to_desktop=route_downloads_to_desktop,
                    approved_roots=list(approved_roots.values()),
                    scope_root_ids=[selected['id'] for selected in roots])
                for suggestion in items:
                    suggestion['source_root_id'] = source_root['id']
                suggestions.extend(items)
                for key in ('considered', 'kept', 'suggested', 'advice'):
                    report[key] += accounting[key]
                for key, count in accounting['reasons'].items():
                    report['reasons'][key] = report['reasons'].get(key, 0) + count
                report['project_candidates'].extend([{**p, 'source_root_id': source_root['id']} for p in accounting['project_candidates']])
                with self.store._connect() as db:
                    report['remembered_keep'] += db.execute('SELECT COUNT(*) FROM files WHERE root_id=? AND sort_keep_size=size_bytes AND sort_keep_modified IS modified_at', (source_root['id'],)).fetchone()[0]
            # Reject collisions between different source locations in the same batch.
            destinations = {}
            for item in suggestions:
                key = (item['destination_root_id'], item['destination_relative'].casefold())
                if key in destinations:
                    for conflict in (item, destinations[key]):
                        conflict.update(status='blocked', selected=False, review_required=True,
                                        error='Multiple source locations target this filename; choose a different destination.')
                destinations[key] = item
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
                'report': report,
            }
            for suggestion in suggestions:
                suggestion["id"] = uuid.uuid4().hex
                suggestion["created_at"] = now.isoformat()
            self.store.save_sort_plan(plan, suggestions)
            return self._sort_plan_view(self._sort_plan(plan["id"]))

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
                if known[patch["id"]]["status"] in ("applied", "rolled_back", "undo_failed"):
                    raise ValueError("Completed file actions cannot be edited")
                item = known[patch['id']]
                target_id = patch.get('destination_root_id', item.get('destination_root_id') or plan['root_id'])
                target_id = self._positive_id(target_id, 'destination_root_id')
                target_root = self.store.get_root(target_id)
                if not target_root or target_root['status'] not in ('ready', 'truncated'):
                    raise ValueError('Destination must be an approved, scanned folder')
                self._assert_no_scan(target_id)
                destination = patch.get("destination_relative", item["destination_relative"])
                if not isinstance(destination, str) or len(destination) > 240:
                    raise ValueError("Invalid destination path")
                destination = destination.replace('\\', '/')
                self.action_manager.validate_move_proposal(destination)
                source = known[patch["id"]]["source_relative"]
                blocked = None
                destination_path = Path(target_root['path']) / destination
                if not self.demo:
                    self.action_manager._validate_path_safety(Path(target_root['path']), destination_path)
                if target_id == (item.get('source_root_id') or plan['root_id']) and destination.lower() == source.lower():
                    blocked = "Destination is unchanged"
                elif destination_path.exists():
                    blocked = "A file already exists at the proposed destination"
                if not destination_path.parent.is_dir() and not plan["create_folders"]:
                    blocked = "Destination folder does not exist; enable folder creation for this plan"
                self.store.update_sort_suggestion(patch["id"], {
                    "destination_relative": destination,
                    'destination_root_id': target_id,
                    "selected": int(patch["selected"] and blocked is None),
                    "status": "blocked" if blocked else "suggested",
                    "error": blocked,
                })
            return self._sort_plan_view(self._sort_plan(plan_id))

    def approve_sort_idea(self, plan_id: str, *, folder=None, suggestion_id=None, suggestion_ids=None,
                          confirm=False) -> dict:
        """Apply exactly the conversational folder/file idea the user approved."""
        if confirm is not True:
            raise ValueError("Approving a sort idea requires confirm=true")
        if sum(value is not None for value in (folder, suggestion_id, suggestion_ids)) != 1:
            raise ValueError("Choose a folder idea or a single file idea")
        if suggestion_ids is not None and (not isinstance(suggestion_ids, list) or not 1 <= len(suggestion_ids) <= 500
                or any(not isinstance(value, str) for value in suggestion_ids)):
            raise ValueError('Choose between 1 and 500 suggestion IDs')
        with self._gate:
            plan = self._sort_plan(plan_id)
            if plan["status"] not in ("preview", "partial"):
                raise ValueError("This organization idea is no longer available")
            eligible = [s for s in plan["suggestions"] if s["status"] == "suggested"]
            selected = [s for s in eligible if
                        (suggestion_id is not None and s["id"] == suggestion_id) or
                        (suggestion_ids is not None and s['id'] in suggestion_ids) or
                        (folder is not None and s["destination_relative"].rsplit("/", 1)[0] == folder)]
            if not selected:
                raise ValueError("No eligible files remain in this idea")
            chosen = {s["id"] for s in selected}
            if suggestion_ids is not None and chosen != set(suggestion_ids):
                raise ValueError('A selected file is no longer eligible; refresh the preview')
            self._clear_sort_selection(plan_id)
            patches = [{"id": s["id"], "selected": True} for s in selected[:500]]
            self.update_sort_plan(plan_id, patches)
            return self.apply_sort_plan(plan_id, keep_pending=True)

    def _clear_sort_selection(self, plan_id):
        with self.store._connect() as db:
            db.execute("UPDATE sort_suggestions SET selected=0 WHERE plan_id=? AND status IN ('suggested','blocked')", (plan_id,))

    def apply_confident_sort(self, plan_id: str, *, confirm=False) -> dict:
        """One approved automatic batch; ambiguous files remain pending."""
        if confirm is not True:
            raise ValueError('Automatic sorting requires confirm=true')
        with self._gate:
            plan = self._sort_plan(plan_id)
            candidates = [s for s in plan['suggestions'] if automation_eligible(s)][:500]
            if not candidates:
                raise ValueError('No clear matches remain; review the uncertain files below')
            return self.approve_sort_idea(plan_id, suggestion_ids=[s['id'] for s in candidates], confirm=True)

    def keep_sort_files(self, plan_id: str, suggestion_ids, *, confirm=False) -> dict:
        """Remember a keep decision until the indexed size/timestamp changes."""
        if confirm is not True or not isinstance(suggestion_ids, list) or not 1 <= len(suggestion_ids) <= 500 or any(not isinstance(value, str) for value in suggestion_ids):
            raise ValueError('Keeping files requires confirm=true and 1 to 500 suggestion IDs')
        with self._gate:
            plan = self._sort_plan(plan_id)
            chosen = [s for s in plan['suggestions'] if s['id'] in suggestion_ids and s['status'] in ('suggested', 'blocked', 'failed')]
            if {s['id'] for s in chosen} != set(suggestion_ids):
                raise ValueError('A chosen file is no longer pending')
            with self.store._connect() as db:
                for suggestion in chosen:
                    db.execute('UPDATE files SET sort_keep_size=size_bytes, sort_keep_modified=modified_at WHERE id=? AND root_id=? AND relative_path=?',
                               (suggestion['file_id'], suggestion.get('source_root_id') or plan['root_id'], suggestion['source_relative']))
                    db.execute("UPDATE sort_suggestions SET selected=0,status='dismissed',error=NULL WHERE id=?", (suggestion['id'],))
            return self._sort_plan_view(self._sort_plan(plan_id))

    def _prepare_sort_directories(self, plan: dict, selected: list[dict], root_path: Path) -> list[str]:
        # Each destination remains tied to its approved root in the durable journal.
        roots = {r['id']: r for r in self.store.get_roots()}
        missing = {}
        for suggestion in selected:
            root_id = suggestion.get('destination_root_id') or plan['root_id']
            root = roots.get(root_id)
            if not root or root['status'] not in ('ready', 'truncated'):
                raise ValueError('Destination root is unavailable or being scanned')
            self._assert_no_scan(root_id)
            base = Path(root['path'])
            directory = (base / suggestion['destination_relative']).parent
            self.action_manager._validate_path_safety(base, directory)
            current = directory
            while not current.exists():
                missing[(root_id, current.as_posix())] = (base, current)
                current = current.parent
        if missing and not plan['create_folders']:
            raise ValueError('A destination folder is missing; regenerate with folder creation enabled')
        records = [{'root_id': root_id, 'relative': path.relative_to(base).as_posix()}
                   for (root_id, _), (base, path) in missing.items()]
        previous = json.loads(plan.get('created_dirs') or '[]')
        combined = previous + [r for r in records if r not in previous]
        self.store.update_sort_plan(plan['id'], {'status': 'applying', 'created_dirs': json.dumps(combined)})
        for base, directory in sorted(missing.values(), key=lambda pair: len(pair[1].parts)):
            self.action_manager._validate_path_safety(base, directory)
            directory.mkdir(exist_ok=False)
        return records

    def _prepare_legacy_sort_directories(self, plan: dict, selected: list[dict], root_path: Path) -> list[str]:
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
        previous = json.loads(plan.get("created_dirs") or "[]")
        journal_dirs = list(dict.fromkeys([*previous, *created]))
        # The plan record is durable before any directory or file mutation.
        self.store.update_sort_plan(plan["id"], {"status": "applying", "created_dirs": json.dumps(journal_dirs)})
        for directory in sorted(missing, key=lambda path: len(path.parts)):
            self.action_manager._validate_path_safety(root_path, directory)
            directory.mkdir(exist_ok=False)
        return created

    def _cleanup_sort_directories(self, plan: dict, root_path: Path) -> None:
        if self.demo:
            return
        try:
            directories = json.loads(plan.get("created_dirs") or "[]")
        except (TypeError, json.JSONDecodeError):
            directories = []
        def relative_value(value):
            return value.get('relative', '') if isinstance(value, dict) else value
        for record in sorted(directories, key=lambda value: relative_value(value).count('/'), reverse=True):
            try:
                base = root_path
                if isinstance(record, dict):
                    target = self.store.get_root(record['root_id'])
                    if not target or target['status'] == 'scanning':
                        continue
                    base = Path(target['path'])
                directory = base / relative_value(record)
                self.action_manager._validate_path_safety(base, directory)
                directory.rmdir()
            except (OSError, ValueError):
                # A non-empty or changed directory is deliberately left for
                # the user; cleanup never removes user-created content.
                continue

    def apply_sort_plan(self, plan_id: str, *, keep_pending=False) -> dict:
        with self._gate:
            plan = self._sort_plan(plan_id)
            if plan["status"] not in ("preview", "partial"):
                raise ValueError(f"Sort plan cannot be applied in status {plan['status']}")
            if datetime.now(timezone.utc) > datetime.fromisoformat(plan["expires_at"]):
                self.store.update_sort_plan(plan_id, {"status": "expired"})
                raise ValueError("Sort plan expired")
            root = self.store.get_root(plan["root_id"])
            if not root or root["status"] not in ("ready", "truncated"):
                raise ValueError("Root is not ready for organization")
            selected = [s for s in plan["suggestions"] if s["selected"] and s["status"] == "suggested"]
            if not selected:
                raise ValueError("Select at least one unblocked suggestion")
            if len(selected) > 500:
                raise ValueError('Select at most 500 files per batch')
            for suggestion in selected:
                self._assert_no_scan(suggestion.get('source_root_id') or plan['root_id'])
                self._assert_no_scan(suggestion.get('destination_root_id') or plan['root_id'])

            root_path = Path(root["path"]).resolve(strict=True) if not self.demo else Path(root["path"])
            try:
                if self.demo:
                    # Demo plans exercise the same journal and selection flow,
                    # but never create directories or touch the filesystem.
                    previous = json.loads(plan.get("created_dirs") or "[]")
                    folders = [s["destination_relative"].rsplit("/", 1)[0] for s in selected]
                    self.store.update_sort_plan(plan["id"], {
                        "status": "applying", "created_dirs": json.dumps(list(dict.fromkeys([*previous, *folders])))})
                else:
                    self._prepare_sort_directories(plan, selected, root_path)
            except Exception as error:
                self.store.update_sort_plan(plan_id, {"status": "failed", "error": str(error)})
                raise

            applied = []
            failed = []
            for suggestion in selected:
                action = None
                try:
                    with self.store._connect() as db:
                        source = db.execute('SELECT root_id,relative_path FROM files WHERE id=?', (suggestion['file_id'],)).fetchone()
                    if not source or source['root_id'] != (suggestion.get('source_root_id') or plan['root_id']) or source['relative_path'] != suggestion['source_relative']:
                        raise ValueError('The source moved since this preview; rescan before sorting')
                    action = self.action_manager.create_proposal(
                        suggestion["file_id"], "MOVE", suggestion["destination_relative"], plan["mode"],
                        destination_root_id=suggestion.get('destination_root_id') or plan['root_id'],
                    )
                    self.store.update_sort_suggestion(suggestion["id"], {"status": "applying", "action_id": action["id"], "error": None})
                    result = self.action_manager.apply_action(action["proposal_id"])
                    if result.get("status") != "applied":
                        raise ValueError(result.get("message", "File action did not complete"))
                    self.store.update_sort_suggestion(suggestion["id"], {"status": "applied", "error": None})
                    applied.append({"suggestion_id": suggestion["id"], "action_id": action["id"], "status": "applied"})
                except (OSError, ValueError, TypeError) as error:
                    action_id = action.get('id') if action else suggestion.get('action_id')
                    if action_id:
                        try:
                            act = self.store.get_file_action(action_id)
                            if act and act.get("status") == 'proposed':
                                self.store.update_file_action(action_id, {"status": "failed", "error": str(error)})
                        except Exception:
                            pass
                    self.store.update_sort_suggestion(suggestion["id"], {"status": "failed", "error": str(error)})
                    failed.append({"suggestion_id": suggestion["id"], "status": "failed", "error": str(error)})

            remaining = self._sort_plan(plan_id)["suggestions"]
            has_applied = any(s["status"] == "applied" for s in remaining)
            has_pending = keep_pending and any(s["status"] == "suggested" for s in remaining)
            status = "partial" if has_applied and (failed or has_pending) else "applied" if has_applied else "failed"
            self.store.update_sort_plan(plan_id, {"status": status, "error": None if not failed else "Some suggestions could not be applied"})
            if not has_applied:
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
                if suggestion["status"] not in ("applied", "undo_failed") or not suggestion.get("action_id"):
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
            data = self.store.get_insights_data()
            rows = self.store.get_review_candidate_files()
            files = []
            refreshed = []
            for row in rows:
                if is_sensitive_file(row["name"], row["extension"], row["relative_path"]):
                    continue
                if not self.demo and row.get("inspection_version", 0) != INSPECTION_VERSION:
                    try:
                        root_path = Path(row["root_path"])
                        full_path = root_path / row["relative_path"]
                        self.action_manager._validate_path_safety(root_path, full_path)
                        row["excerpt"] = self._get_excerpt(
                            full_path, row["name"], row["extension"], False, row["relative_path"])
                        refreshed.append((row["id"], row["excerpt"]))
                    except (OSError, ValueError):
                        row["excerpt"] = ""
                files.append(row)
            if refreshed:
                with self.store._connect() as db:
                    db.executemany("UPDATE files SET excerpt=?, inspection_version=? WHERE id=?",
                                   [(excerpt, INSPECTION_VERSION, file_id) for file_id, excerpt in refreshed])
            decisions = analyze_catalog(files)
            counts = {}
            for decision in decisions:
                decision["path"] = str(Path(decision["root_path"]) / decision["relative_path"])
                action = decision["recommended_action"]
                counts[action] = counts.get(action, 0) + 1
            data["file_decisions"] = decisions[:100]
            data["decision_counts"] = counts
            data["analysis_scope"] = {"analyzed": len(decisions), "catalog_limit": 2000,
                                      "display_limit": 100, "bounded_content_bytes": 4096}
            data["review_candidates"] = [item for item in decisions if item["review_required"]][:30]
            return data

    def get_file_excerpt(self, file_id: int) -> dict:
        file_id = self._positive_id(file_id, "file_id")
        with self._gate:
            file = self.store.get_file(file_id)
            if not file:
                raise ValueError("File not found")
                
            excerpt = file.pop("excerpt")
            root = self.store.get_root(file["root_id"])
            if root:
                root_path = Path(root["path"])
                full_path = root_path / file["relative_path"]
                file["path"] = str(full_path)
                if not self.demo:
                    self.action_manager._validate_path_safety(root_path, full_path)
                if not self.demo:
                    # Inspect the current approved file, not a stale cached
                    # excerpt. Excluded/sensitive types remain unread.
                    excerpt = self._get_excerpt(full_path, file["name"], file["extension"],
                                                bool(file["is_directory"]), file["relative_path"])
            peers = self.store.get_sortable_files(file["root_id"])
            row = {**file, "excerpt": excerpt, "root_label": root["label"] if root else ''}
            peers = [peer for peer in peers if peer["id"] != file_id] + [row]
            decisions = analyze_catalog(peers)
            file["insights"] = next(item for item in decisions if item["file_id"] == file_id)
            if not is_sensitive_file(file["name"], file["extension"], file["relative_path"]):
                if file["extension"].casefold() in IMAGE_EXTENSIONS and not self.demo:
                    file["image_preview_url"] = f"/api/files/file/{file_id}/image"
                if file["extension"].casefold() in PDF_EXTENSIONS and not self.demo:
                    file["pdf_preview_url"] = f"/api/files/file/{file_id}/pdf"
            return {"file": file, "excerpt": excerpt}

    def get_file_image(self, file_id: int) -> dict:
        """Read one explicitly previewed raster image inside an approved root."""
        file_id = self._positive_id(file_id, "file_id")
        if self.demo:
            raise ValueError("Synthetic demo files do not read images from your device")
        with self._gate:
            file = self.store.get_file(file_id)
            if not file:
                raise ValueError("File not found")
            if (file["is_directory"] or file["extension"].casefold() not in IMAGE_EXTENSIONS
                    or is_sensitive_file(file["name"], file["extension"], file["relative_path"])):
                raise ValueError("This file is not eligible for an image preview")
            root = self.store.get_root(file["root_id"])
            if not root or root["status"] == "removed":
                raise ValueError("Approved root is unavailable")
            root_path = Path(root["path"])
            full_path = root_path / file["relative_path"]
            self.action_manager._validate_path_safety(root_path, full_path)
            before = full_path.stat(follow_symlinks=False)
            if not stat.S_ISREG(before.st_mode):
                raise ValueError("Only regular image files can be previewed")
            if before.st_size > MAX_IMAGE_BYTES:
                raise ValueError("Image preview supports files up to 20 MiB")
            flags = (os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
                     | getattr(os, 'O_NONBLOCK', 0))
            descriptor = os.open(full_path, flags)
            with os.fdopen(descriptor, 'rb') as stream:
                opened = os.fstat(stream.fileno())
                if (not stat.S_ISREG(opened.st_mode)
                        or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                        != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)):
                    raise ValueError("Image changed while opening the preview; try again")
                data = stream.read(MAX_IMAGE_BYTES + 1)
                after = os.fstat(stream.fileno())
            self.action_manager._validate_path_safety(root_path, full_path)
            if (len(data) != opened.st_size
                    or (after.st_size, after.st_mtime_ns) != (opened.st_size, opened.st_mtime_ns)):
                raise ValueError("Image changed while loading the preview; try again")
            return {"data": data, **inspect_image(data)}

    def get_file_pdf(self, file_id: int) -> dict:
        """Read one approved PDF for the in-dashboard viewer."""
        file_id = self._positive_id(file_id, "file_id")
        if self.demo:
            raise ValueError("Synthetic demo files do not read PDFs from your device")
        with self._gate:
            file = self.store.get_file(file_id)
            if not file:
                raise ValueError("File not found")
            if (file["is_directory"] or file["extension"].casefold() not in PDF_EXTENSIONS
                    or is_sensitive_file(file["name"], file["extension"], file["relative_path"])):
                raise ValueError("This file is not eligible for a PDF preview")
            root = self.store.get_root(file["root_id"])
            if not root or root["status"] == "removed":
                raise ValueError("Approved root is unavailable")
            root_path = Path(root["path"])
            full_path = root_path / file["relative_path"]
            self.action_manager._validate_path_safety(root_path, full_path)
            before = full_path.stat(follow_symlinks=False)
            if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_PDF_BYTES:
                raise ValueError("PDF preview supports regular files up to 32 MiB")
            flags = (os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
                     | getattr(os, 'O_NONBLOCK', 0))
            descriptor = os.open(full_path, flags)
            with os.fdopen(descriptor, 'rb') as stream:
                opened = os.fstat(stream.fileno())
                if (not stat.S_ISREG(opened.st_mode)
                        or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                        != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)):
                    raise ValueError("PDF changed while opening the preview; try again")
                data = stream.read(MAX_PDF_BYTES + 1)
                after = os.fstat(stream.fileno())
            self.action_manager._validate_path_safety(root_path, full_path)
            if (len(data) != opened.st_size
                    or (after.st_size, after.st_mtime_ns) != (opened.st_size, opened.st_mtime_ns)):
                raise ValueError("PDF changed while loading the preview; try again")
            return {"data": data, **inspect_pdf(data)}

    def organize_file(self, file_id: int, *, confirm=False) -> dict:
        """Apply the automatically chosen, server-generated destination."""
        if confirm is not True:
            raise ValueError("Organizing requires confirm=true")
        file_id = self._positive_id(file_id, "file_id")
        with self._gate:
            file = self.store.get_file(file_id)
            if not file:
                raise ValueError("File not found")
            plan = self.generate_sort_plan(file["root_id"], create_folders=True)
            idea = next((s for s in plan["suggestions"] if s["file_id"] == file_id), None)
            if not idea or idea["status"] != "suggested":
                raise ValueError("No safe organization action remains for this file")
            return self.approve_sort_idea(plan["id"], suggestion_id=idea["id"], confirm=True)

    def _finish_scan(self, root_id: int, status="ready", *, error=None,
                     file_count=None, total_size=None):
        with self._gate:
            try:
                root = self.store.get_root(root_id)
                if root and root['status'] == 'removed':
                    self.store.remove_root(root_id)
                elif root:
                    if file_count is not None and total_size is not None:
                        self.store.update_root_stats(root_id, file_count, total_size)
                    self.store.update_root_status(root_id, status, error)
            finally:
                # Publish completion atomically with status/removal, even on failure.
                self._active_scans.discard(root_id)
                self._cancel_events.pop(root_id, None)
                self._scan_done.notify_all()

    # --- File Actions ---
    def _assert_no_scan(self, root_id: int):
        with self._gate:
            if root_id in self._active_scans:
                raise ValueError("Wait for the current scan to finish before changing files.")

    def create_proposal(self, file_id: int, kind: str, destination_relative: str, destination_root_id=None) -> dict:
        file_id = self._positive_id(file_id, "file_id")
        mode = "demo" if self.demo else "live"
        file_row = self.store.get_file(file_id)
        if file_row:
            self._assert_no_scan(file_row["root_id"])
        if destination_root_id is not None:
            destination_root_id = self._positive_id(destination_root_id, 'destination_root_id')
            self._assert_no_scan(destination_root_id)
        return self.action_manager.create_proposal(file_id, kind, destination_relative, mode, destination_root_id=destination_root_id)

    def apply_action(self, proposal_id: str, *, confirm=False) -> dict:
        action = self.store.get_file_action(proposal_id=proposal_id)
        if action:
            if action.get("kind") == "DELETE" and confirm is not True:
                raise ValueError("Deleting a file requires confirm=true")
            self._assert_no_scan(action["root_id"])
            self._assert_no_scan(action.get('destination_root_id') or action['root_id'])
        with self._gate:
            return self.action_manager.apply_action(proposal_id)

    def undo_action(self, action_id: str) -> dict:
        action = self.store.get_file_action(action_id=action_id)
        if action:
            self._assert_no_scan(action["root_id"])
            self._assert_no_scan(action.get('destination_root_id') or action['root_id'])
        with self._gate:
            return self.action_manager.undo_action(action_id)

    def dismiss_proposal(self, proposal_id: str) -> None:
        self.action_manager.dismiss_proposal(proposal_id)

    def get_action_journal(self) -> list[dict]:
        return self.action_manager.get_journal()

    def get_quarantine_items(self, root_id=None) -> list[dict]:
        """Return restore metadata without exposing file contents or root paths."""
        if root_id is not None:
            root_id = self._positive_id(root_id, "root_id")
            if not self.store.get_root(root_id):
                raise ValueError("Root not found")
        with self._gate:
            items = []
            for row in self.store.get_quarantine_actions(root_id):
                size = row.get("quarantine_size")
                if size is None:
                    size = row.get("expected_size")
                items.append({
                    "action_id": row["id"],
                    "root_id": row["root_id"],
                    "original_root_id": row["root_id"],
                    "original_root_label": row.get("root_label", "") or "",
                    "original_root": {
                        "id": row["root_id"], "label": row.get("root_label", "") or "",
                    },
                    "original_relative_path": row["source_relative"],
                    "quarantine_state": row.get("quarantine_state") or row.get("status"),
                    "status": row.get("status"),
                    "size_bytes": size,
                    "modified_at": row.get("original_modified_at"),
                    "expected_mtime_ns": row.get("expected_mtime_ns"),
                    "expected_ctime_ns": row.get("expected_ctime_ns"),
                    "quarantine_mtime_ns": row.get("quarantine_mtime_ns"),
                    "quarantine_ctime_ns": row.get("quarantine_ctime_ns"),
                    "created_at": row.get("created_at"),
                    "quarantined_at": row.get("quarantined_at"),
                    "restored_at": row.get("restored_at"),
                    "content_evidence": {
                        "sha256": row.get("quarantine_sha256") or row.get("expected_sha256"),
                        "size_bytes": size,
                    },
                })
            return items

    def restore_quarantined_file(self, action_id: str, *, confirm=False) -> dict:
        if (not isinstance(action_id, str) or not action_id or len(action_id) > 200
                or not action_id.isascii()
                or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for character in action_id)):
            raise ValueError("Invalid action_id")
        if confirm is not True:
            raise ValueError("Restoring a quarantined file requires confirm=true")
        if self.demo:
            raise ValueError("Restoring quarantined files is not available in demo mode")
        action = self.store.get_file_action(action_id=action_id)
        if not action:
            raise ValueError("Quarantine action not found")
        with self._gate:
            self._assert_no_scan(action["root_id"])
            return self.action_manager.restore_quarantined_file(action_id, confirm=True)


    # --- Empty-file operations ---

    # Structural and project-control names share the file-action protection list.
    _PROTECTED_CLEANUP_NAMES = PROTECTED_ACTION_NAMES

    @staticmethod
    def _is_quarantine_relative(relative_path: str) -> bool:
        if not isinstance(relative_path, str) or "\0" in relative_path:
            return False
        normalized = relative_path.replace("\\", "/").strip("/").casefold()
        prefix = QUARANTINE_DIR_NAME.casefold()
        return normalized == prefix or normalized.startswith(prefix + "/")

    def _cleanup_name_is_protected(self, file: dict) -> bool:
        """Return whether a catalog row is never eligible for cleanup."""
        return (
            self._is_quarantine_relative(file.get("relative_path", ""))
            or
            file.get("name", "").casefold() in self._PROTECTED_CLEANUP_NAMES
            or is_sensitive_file(file.get("name", ""), file.get("extension", ""), file.get("relative_path", ""))
        )

    def _cleanup_project_directories(self, root_id: int | None = None) -> dict[int, set[str]]:
        """Return project-tree roots whose contents must stay together."""
        params: tuple = ()
        root_filter = ""
        if root_id is not None:
            root_filter = " AND f.root_id=?"
            params = (root_id,)
        marker_names = tuple(marker.casefold() for marker in PROJECT_MARKERS)
        placeholders = ",".join("?" for _ in marker_names)
        with self.store._connect() as db:
            rows = db.execute(
                "SELECT f.root_id, f.relative_path FROM files f "
                "JOIN roots r ON r.id=f.root_id "
                "JOIN scan_state s ON s.root_id=f.root_id "
                "WHERE r.status IN ('ready','truncated') AND f.scan_pass=s.last_pass "
                f"AND lower(f.name) IN ({placeholders}){root_filter}",
                (*marker_names, *params),
            ).fetchall()
        project_dirs: dict[int, set[str]] = {}
        for row in rows:
            relative = str(row["relative_path"]).replace("\\", "/")
            parent = pathlib.PurePosixPath(relative).parent.as_posix()
            project_dirs.setdefault(row["root_id"], set()).add("" if parent == "." else parent.casefold())
        return project_dirs

    @staticmethod
    def _cleanup_file_is_in_project(file: dict, project_dirs: dict[int, set[str]]) -> bool:
        relative = str(file.get("relative_path", "")).replace("\\", "/").casefold()
        return any(not directory or relative == directory or relative.startswith(directory + "/")
                   for directory in project_dirs.get(file.get("root_id"), set()))

    def _cleanup_file_snapshot(self, file: dict, *, expected_size: int | None = None) -> tuple[Path, os.stat_result] | None:
        """Validate a catalog row against the current approved filesystem.

        Cleanup never follows a link and never treats a stale catalog row as a
        deletion target.  ``None`` is returned for an unavailable/stale row so
        list endpoints remain deterministic while delete endpoints can provide
        a specific conflict error.
        """
        root = self.store.get_root(file["root_id"])
        if not root or root.get("status") not in ("ready", "truncated"):
            return None
        root_path = pathlib.Path(root["path"])
        if self._is_quarantine_relative(file.get("relative_path", "")):
            return None
        full_path = root_path / file["relative_path"]
        with self.store._connect() as db:
            current = db.execute(
                "SELECT f.scan_pass, s.last_pass FROM files f JOIN scan_state s ON s.root_id=f.root_id WHERE f.id=?",
                (file.get("id"),),
            ).fetchone()
        if not current or current["scan_pass"] != current["last_pass"]:
            return None
        try:
            self.action_manager._validate_path_safety(root_path, full_path)
            info = full_path.stat(follow_symlinks=False)
        except (OSError, ValueError):
            return None
        if not stat.S_ISREG(info.st_mode) or full_path.is_symlink():
            return None
        if getattr(info, "st_file_attributes", 0) & 0x400:
            return None
        if expected_size is not None and info.st_size != expected_size:
            return None
        return full_path, info

    def _require_cleanup_root(self, root_id: int | None) -> None:
        if root_id is None:
            return
        root = self.store.get_root(root_id)
        if not root:
            raise ValueError("Root not found")
        if root.get("status") not in ("ready", "truncated"):
            raise ValueError("Root is not ready for cleanup; finish or rescan it first")

    def get_empty_files(self, root_id=None):
        """Return current zero-byte regular files excluding protected markers."""
        if root_id is not None:
            root_id = self._positive_id(root_id, "root_id")
        self._require_cleanup_root(root_id)
        if self.demo:
            # Demo catalog records are synthetic; never inspect their paths.
            return {"empty_files": [], "total": 0, "scan_partial": False,
                    "candidate_limit": self._CLEANUP_QUERY_LIMIT}
        with self._gate:
            project_dirs = self._cleanup_project_directories(root_id)
            with self.store._connect() as db:
                if root_id is not None:
                    rows = db.execute(
                        "SELECT f.id, f.root_id, f.relative_path, f.name, f.extension,"
                        " f.size_bytes, f.modified_at, r.path as root_path, r.label as root_label"
                        " FROM files f JOIN roots r ON f.root_id=r.id JOIN scan_state s ON s.root_id=f.root_id"
                        " WHERE f.root_id=? AND f.scan_pass=s.last_pass AND f.size_bytes=0 AND f.is_directory=0"
                        " ORDER BY f.root_id, f.relative_path LIMIT ?",
                        (root_id, self._CLEANUP_QUERY_LIMIT + 1),
                    ).fetchall()
                else:
                    rows = db.execute(
                        "SELECT f.id, f.root_id, f.relative_path, f.name, f.extension,"
                        " f.size_bytes, f.modified_at, r.path as root_path, r.label as root_label"
                        " FROM files f JOIN roots r ON f.root_id=r.id JOIN scan_state s ON s.root_id=f.root_id"
                        " WHERE f.scan_pass=s.last_pass AND f.size_bytes=0 AND f.is_directory=0"
                        " ORDER BY f.root_id, f.relative_path LIMIT ?",
                        (self._CLEANUP_QUERY_LIMIT + 1,),
                    ).fetchall()
            scan_partial = len(rows) > self._CLEANUP_QUERY_LIMIT
            rows = rows[:self._CLEANUP_QUERY_LIMIT]
            files = []
            for row in rows:
                d = dict(row)
                if (self._cleanup_name_is_protected(d)
                        or self._cleanup_file_is_in_project(d, project_dirs)):
                    continue
                snapshot = self._cleanup_file_snapshot(d, expected_size=0)
                if snapshot is None:
                    continue
                d["path"] = str(pathlib.Path(d["root_path"]) / d["relative_path"])
                files.append(d)
            return {"empty_files": files, "total": len(files), "scan_partial": scan_partial,
                    "candidate_limit": self._CLEANUP_QUERY_LIMIT}

    def delete_empty_file(self, file_id, *, confirm=False):
        """Move a confirmed-empty file into reversible quarantine."""
        if confirm is not True:
            raise ValueError("Deleting an empty file requires confirm=true")
        file_id = self._positive_id(file_id, "file_id")
        if self.demo:
            raise ValueError("File deletion is not available in demo mode")
        with self._gate:
            file = self.store.get_file(file_id)
            if not file:
                raise ValueError("File not found")
            if file["is_directory"]:
                raise ValueError("Cannot delete a directory with this endpoint")
            if self._cleanup_name_is_protected(file):
                if is_sensitive_file(file["name"], file["extension"], file["relative_path"]):
                    raise ValueError("This file is a protected sensitive file")
                raise ValueError("This file is a protected structural or project marker")
            if self._cleanup_file_is_in_project(
                    file, self._cleanup_project_directories(file["root_id"])):
                raise ValueError("This file is inside a protected project tree")
            root = self.store.get_root(file["root_id"])
            if not root or root.get("status") == "removed":
                raise ValueError("Root not found")
            full_path = pathlib.Path(root["path"]) / file["relative_path"]
            try:
                self.action_manager._validate_path_safety(pathlib.Path(root["path"]), full_path)
            except OSError as error:
                raise ValueError("Approved root is unavailable") from error
            except ValueError:
                raise
            snapshot = self._cleanup_file_snapshot(file)
            if snapshot is None:
                raise ValueError("File is missing, not a regular file, or is a symlink/reparse point")
            full_path, info = snapshot
            if info.st_size != 0:
                raise ValueError(
                    f"File is no longer empty (size is {info.st_size} bytes); rescan before deleting"
                )
            self._assert_no_scan(file["root_id"])
            digest = self._file_sha256(full_path, expected_size=0)
            if digest is None:
                raise ValueError("File changed while validating its content; rescan before deleting")
            action = self.action_manager.create_proposal(
                file_id, "DELETE", file["relative_path"], "live", expected_sha256=digest,
                quarantine=True,
            )
            result = self.action_manager.apply_action(action["proposal_id"])
            return {
                "deleted": result.get("status") in ("applied", "deleted", "quarantined"),
                "quarantined": result.get("status") == "quarantined",
                "action_id": action["id"],
                "action": result,
            }

    # --- Duplicate-file operations ---

    _HASH_CHUNK = 65536  # 64 KiB read chunks
    _MAX_HASH_BYTES = 100 * 1024 * 1024
    _CLEANUP_QUERY_LIMIT = 100000  # bounded local catalog work per preview

    @staticmethod
    def _file_sha256(path, *, expected_size=None):
        """Return SHA-256 only when a bounded, stable regular file was read."""
        h = hashlib.sha256()
        descriptor = None
        try:
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags)
            with os.fdopen(descriptor, "rb") as stream:
                descriptor = None
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode) or before.st_size > FileService._MAX_HASH_BYTES:
                    return None
                if expected_size is not None and before.st_size != expected_size:
                    return None
                total = 0
                while chunk := stream.read(FileService._HASH_CHUNK):
                    total += len(chunk)
                    if total > FileService._MAX_HASH_BYTES:
                        return None
                    h.update(chunk)
                after = os.fstat(stream.fileno())
                if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)):
                    return None
                return h.hexdigest()
        except (OSError, ValueError):
            return None
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass

    def get_duplicate_groups(self, root_id=None, *, size_min=1):
        """Find sets of files with identical SHA-256 digests.

        Groups are sorted by wasted bytes (largest first). Only non-empty,
        non-directory files are examined. Catalog work is bounded by
        ``_CLEANUP_QUERY_LIMIT`` and reports when that bound is reached.
        """
        if root_id is not None:
            root_id = self._positive_id(root_id, "root_id")
        self._require_cleanup_root(root_id)
        if self.demo:
            return {"groups": [], "total_groups": 0, "total_wasted_bytes": 0,
                    "scan_partial": False, "scanned_files": 0,
                    "hash_limit_bytes": self._MAX_HASH_BYTES}
        with self._gate:
            project_dirs = self._cleanup_project_directories(root_id)
            with self.store._connect() as db:
                if root_id is not None:
                    rows = db.execute(
                        "SELECT f.id, f.root_id, f.relative_path, f.name, f.extension,"
                        " f.size_bytes, f.modified_at, r.path as root_path, r.label as root_label"
                        " FROM files f JOIN roots r ON f.root_id=r.id JOIN scan_state s ON s.root_id=f.root_id"
                        " WHERE f.root_id=? AND f.scan_pass=s.last_pass AND f.size_bytes>=? AND f.is_directory=0"
                        " ORDER BY f.size_bytes DESC, f.root_id, f.relative_path LIMIT ?",
                        (root_id, size_min, self._CLEANUP_QUERY_LIMIT + 1),
                    ).fetchall()
                else:
                    rows = db.execute(
                        "SELECT f.id, f.root_id, f.relative_path, f.name, f.extension,"
                        " f.size_bytes, f.modified_at, r.path as root_path, r.label as root_label"
                        " FROM files f JOIN roots r ON f.root_id=r.id JOIN scan_state s ON s.root_id=f.root_id"
                        " WHERE f.scan_pass=s.last_pass AND f.size_bytes>=? AND f.is_directory=0"
                        " ORDER BY f.size_bytes DESC, f.root_id, f.relative_path LIMIT ?",
                        (size_min, self._CLEANUP_QUERY_LIMIT + 1),
                    ).fetchall()
            scan_partial = len(rows) > self._CLEANUP_QUERY_LIMIT
            rows = rows[:self._CLEANUP_QUERY_LIMIT]
        # First pass uses the live size, not stale catalog metadata.  A file
        # changing during this stat/hash cycle is discarded rather than being
        # treated as an exact duplicate on old evidence.
        by_size = {}
        skipped = 0
        for row in rows:
            d = dict(row)
            if (self._cleanup_name_is_protected(d)
                    or self._cleanup_file_is_in_project(d, project_dirs)):
                skipped += 1
                continue
            snapshot = self._cleanup_file_snapshot(d)
            if snapshot is None:
                skipped += 1
                continue
            full_path, info = snapshot
            if info.st_size < size_min or info.st_size == 0:
                skipped += 1
                continue
            d["size_bytes"] = info.st_size
            d["path"] = str(full_path)
            by_size.setdefault(info.st_size, []).append(d)
        # Second pass: hash only size-collision candidates
        by_hash = {}
        hash_skipped = 0
        for size, candidates in by_size.items():
            if len(candidates) < 2:
                continue
            for d in candidates:
                full_path = pathlib.Path(d["root_path"]) / d["relative_path"]
                digest = self._file_sha256(full_path, expected_size=size)
                if digest is None:
                    skipped += 1
                    hash_skipped += 1
                    continue
                d["sha256"] = digest
                by_hash.setdefault(digest, []).append(d)
        # Collect groups with >= 2 members
        groups = []
        total_wasted = 0
        for digest, members in by_hash.items():
            if len(members) < 2:
                continue
            size = members[0]["size_bytes"]
            wasted = size * (len(members) - 1)
            total_wasted += wasted
            groups.append({
                "sha256": digest,
                "size_bytes": size,
                "count": len(members),
                "wasted_bytes": wasted,
                "files": members,
            })
        groups.sort(key=lambda g: (-g["wasted_bytes"], g["sha256"]))
        return {
            "groups": groups[:100],
            "total_groups": len(groups),
            "groups_returned": min(len(groups), 100),
            "groups_truncated": len(groups) > 100,
            "total_wasted_bytes": total_wasted,
            "scan_partial": scan_partial,
            "scanned_files": len(rows),
            "hash_limit_bytes": self._MAX_HASH_BYTES,
            "skipped_files": skipped,
            "hash_skipped_files": hash_skipped,
        }

    def delete_duplicate_file(self, file_id, *, confirm=False):
        """Move one verified duplicate copy into reversible quarantine.

        A live matching peer must survive the journal's final content recheck.
        """
        if confirm is not True:
            raise ValueError("Deleting a duplicate requires confirm=true")
        file_id = self._positive_id(file_id, "file_id")
        if self.demo:
            raise ValueError("File deletion is not available in demo mode")
        with self._gate:
            file = self.store.get_file(file_id)
            if not file:
                raise ValueError("File not found")
            if file["is_directory"]:
                raise ValueError("Cannot delete a directory")
            if self._cleanup_name_is_protected(file):
                if is_sensitive_file(file["name"], file["extension"], file["relative_path"]):
                    raise ValueError("This file is a protected sensitive file")
                raise ValueError("This file is a protected structural or project marker")
            if self._cleanup_file_is_in_project(
                    file, self._cleanup_project_directories(file["root_id"])):
                raise ValueError("This file is inside a protected project tree")
            root = self.store.get_root(file["root_id"])
            if not root or root.get("status") == "removed":
                raise ValueError("Root not found")
            self._assert_no_scan(file["root_id"])
            # Rebuild the exact current group immediately before journaling.
            # This both protects the last survivor and avoids trusting a stale
            # duplicate preview.
            groups = self.get_duplicate_groups()
            group = next((candidate for candidate in groups["groups"]
                          if any(member["id"] == file_id for member in candidate["files"])), None)
            if not group or len(group["files"]) < 2:
                raise ValueError("No duplicate copy remains; refusing to delete the last survivor")
            digest = group["sha256"]
            peer_ids = [peer["id"] for peer in group["files"] if peer["id"] != file_id]
            action = self.action_manager.create_proposal(
                file_id, "DELETE", file["relative_path"], "live",
                expected_sha256=digest, expected_peer_ids=peer_ids,
                quarantine=True,
            )
            result = self.action_manager.apply_action(action["proposal_id"])
            return {
                "deleted": result.get("status") in ("applied", "deleted", "quarantined"),
                "quarantined": result.get("status") == "quarantined",
                "action_id": action["id"],
                "action": result,
            }
