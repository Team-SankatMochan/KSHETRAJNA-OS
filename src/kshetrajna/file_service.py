import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
import logging

from .file_storage import FileStore
from .file_scanner import walk, extract_excerpt, TEXT_EXTENSIONS, SENSITIVE_NAMES, SENSITIVE_EXTENSIONS, SENSITIVE_DIRS, MAX_FILES
from .file_insights import generate_insights
from .file_demo import seed_file_demo

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
        self._gate = threading.RLock()
        self._scan_thread: threading.Thread | None = None
        self._cancel_events: dict[int, threading.Event] = {}
        self._active_scans: set[int] = set()

    def state(self) -> dict:
        with self._gate:
            roots = self.store.get_roots()
            stats = self.store.get_stats()
            return {"mode": "demo" if self.demo else "live", "roots": roots, "stats": stats}

    def _validate_root_path(self, path_str: str) -> Path:
        if not path_str or len(path_str) < 3 or '\0' in path_str:
            raise ValueError("Invalid path format")
        
        if path_str.startswith(r'\\'):
            raise ValueError("Network and UNC paths are not supported")
            
        try:
            path = Path(path_str).resolve(strict=True)
        except OSError:
            raise ValueError("Path does not exist")

        path_lower = str(path).lower()
        if path_lower.endswith(":\\"):
            raise ValueError("Root drives cannot be scanned directly")
            
        for protected in PROTECTED_PREFIXES:
            if path_lower.startswith(protected):
                raise ValueError("System and protected paths cannot be scanned")

        # Symlink check
        try:
            if path.stat(follow_symlinks=False).st_file_attributes & 0x400:
                raise ValueError("Symlinks and reparse points cannot be used as roots")
        except OSError:
            raise ValueError("Cannot access path attributes")

        with self._gate:
            roots = self.store.get_roots()
            if len(roots) >= 20:
                raise ValueError("Maximum of 20 roots allowed")
                
            now_ts = datetime.now(timezone.utc).timestamp()
            recent_creations = [r for r in roots if (now_ts - datetime.fromisoformat(r["created_at"]).timestamp()) < 60]
            if len(recent_creations) >= 5:
                raise ValueError("Rate limit exceeded: max 5 roots per minute")

            for root in roots:
                root_path = Path(root["path"])
                if path == root_path:
                    raise ValueError("Root already exists")
                if path.is_relative_to(root_path):
                    raise ValueError(f"Path is a descendant of existing root: {root['path']}")
                if root_path.is_relative_to(path):
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
            
            batch = []
            
            for rel_path, entry, info in walk(root_path, is_cancelled):
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
                    
            if batch and not is_cancelled():
                self._flush_batch(batch)
                
            with self.store._connect() as db:
                end_time = datetime.now(timezone.utc).isoformat()
                
                if is_cancelled():
                    status = "cancelled"
                elif seen > MAX_FILES:
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
        with self._gate:
            root = self.store.get_root(root_id)
            if not root:
                raise ValueError("Root not found")
            if root_id in self._cancel_events:
                self._cancel_events[root_id].set()

    def remove_root(self, root_id: int) -> None:
        with self._gate:
            root = self.store.get_root(root_id)
            if not root:
                raise ValueError("Root not found")
                
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

    def get_root_details(self, root_id: int) -> dict:
        with self._gate:
            root = self.store.get_root(root_id)
            if not root:
                raise ValueError("Root not found")
            recent = self.store.get_recent_files(root_id)
            return {"root": root, "recent_files": recent}

    def search(self, query: str, root_id: int | None = None) -> dict:
        with self._gate:
            results = self.store.search_files(query, root_id)
            return {"results": results}

    def get_insights(self) -> dict:
        with self._gate:
            return self.store.get_insights_data()

    def get_file_excerpt(self, file_id: int) -> dict:
        with self._gate:
            file = self.store.get_file(file_id)
            if not file:
                raise ValueError("File not found")
                
            excerpt = file.pop("excerpt")
            file["insights"] = generate_insights(file["name"], file["extension"], file["relative_path"], excerpt)
            return {"file": file, "excerpt": excerpt}

    def _finish_scan(self, root_id: int):
        with self._gate:
            self.store.update_root_status(root_id, "ready")
            self._active_scans.discard(root_id)
            self._cancel_events.pop(root_id, None)
