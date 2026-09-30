import os
import secrets
from datetime import datetime, timezone, timedelta
from pathlib import Path
import logging

from .file_storage import FileStore

logger = logging.getLogger(__name__)

class FileActionManager:
    def __init__(self, store: FileStore):
        self.store = store

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)
        
    def _iso(self, dt: datetime) -> str:
        return dt.isoformat()

    def _is_invalid_basename(self, name: str) -> bool:
        if not name or len(name) == 0:
            return True
        if name[-1] in (' ', '.'):
            return True
        if any(c in name for c in '<>:"/\\|?*\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f\x10\x11\x12\x13\x14\x15\x16\x17\x18\x19\x1a\x1b\x1c\x1d\x1e\x1f'):
            return True
            
        base = name.split('.')[0].upper()
        reserved = {"CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"}
        if base in reserved:
            return True
        return False

    def validate_rename_proposal(self, source_name: str, dest_name: str) -> None:
        if self._is_invalid_basename(dest_name):
            raise ValueError(f"Invalid filename: {dest_name}")
        
        # Preserve extension
        src_ext = Path(source_name).suffix.lower()
        dst_ext = Path(dest_name).suffix.lower()
        if src_ext != dst_ext:
            raise ValueError("Extension change is not allowed")

    def validate_move_proposal(self, dest_relative: str) -> None:
        if ".." in dest_relative or dest_relative.startswith("/") or dest_relative.startswith("\\") or ":" in dest_relative:
            raise ValueError("Invalid destination path")

    def create_proposal(
        self,
        file_id: int,
        kind: str,
        destination_relative: str,
        mode: str = "live"
    ) -> dict:
        """
        Create a file mutation proposal.
        """
        if kind not in ("RENAME", "MOVE"):
            raise ValueError(f"Invalid action kind: {kind}")
            
        with self.store._connect() as db:
            file_row = db.execute("SELECT r.path as root_path, f.root_id, f.relative_path, f.name FROM files f JOIN roots r ON f.root_id = r.id WHERE f.id=?", (file_id,)).fetchone()
            if not file_row:
                raise ValueError("File not found")
                
        if not destination_relative or destination_relative.strip() == "":
            raise ValueError("Destination cannot be empty")
            
        if kind == "RENAME":
            self.validate_rename_proposal(file_row["name"], destination_relative)
            dest_rel = str(Path(file_row["relative_path"]).parent / destination_relative).replace('\\', '/')
            if dest_rel.startswith('./'):
                dest_rel = dest_rel[2:]
        elif kind == "MOVE":
            self.validate_move_proposal(destination_relative)
            dest_rel = destination_relative
            # Ensure parent exists
            parent = (Path(file_row["root_path"]) / dest_rel).parent
            if not parent.exists() or not parent.is_dir():
                raise ValueError("Destination directory does not exist")
            
        # Opaque IDs
        action_id = secrets.token_urlsafe(16)
        proposal_id = secrets.token_urlsafe(16)
        
        now = self._now()
        expires_at = now + timedelta(minutes=5)
        
        expected_size = None
        expected_mtime_ns = None
        expected_ctime_ns = None
        expected_dev = None
        expected_ino = None
        
        # Capture Identity
        root_path = Path(file_row["root_path"])
        abs_source = root_path / file_row["relative_path"]
        try:
            st = abs_source.stat(follow_symlinks=False)
            expected_size = st.st_size
            expected_mtime_ns = st.st_mtime_ns
            expected_ctime_ns = getattr(st, "st_ctime_ns", None)
            expected_dev = st.st_dev
            expected_ino = st.st_ino
        except FileNotFoundError:
            pass # Identity capturing can fail gracefully here; it will fail during apply if file is missing.
            
        action = {
            "id": action_id,
            "proposal_id": proposal_id,
            "root_id": file_row["root_id"],
            "file_id": file_id,
            "kind": kind,
            "source_relative": file_row["relative_path"],
            "destination_relative": dest_rel,
            
            "expected_size": expected_size,
            "expected_mtime_ns": expected_mtime_ns,
            "expected_ctime_ns": expected_ctime_ns,
            "expected_dev": expected_dev,
            "expected_ino": expected_ino,
            
            "created_at": self._iso(now),
            "expires_at": self._iso(expires_at),
            "status": "proposed",
            "mode": mode
        }
        
        self.store.save_file_action(action)
        return action

    def dismiss_proposal(self, proposal_id: str) -> None:
        action = self.store.get_file_action(proposal_id=proposal_id)
        if not action:
            return
        if action["status"] == "proposed":
            self.store.update_file_action(action["id"], {"status": "dismissed"})
            
    def _validate_path_safety(self, root_path: Path, target_path: Path) -> None:
        resolved_root = root_path.resolve(strict=True)
        resolved_target = target_path.resolve(strict=False)
        if not resolved_target.is_relative_to(resolved_root):
            raise ValueError("Path escapes approved root")
            
        # Reparse / symlink defense for source/dest parent
        # We manually check if parents are reparse points to be safe on Windows.
        current = resolved_target
        while current != resolved_root:
            if current.exists() and current.is_symlink():
                raise ValueError("Symlink encountered")
            try:
                st = current.stat(follow_symlinks=False)
                if hasattr(st, "st_file_attributes") and (st.st_file_attributes & 0x400):
                    raise ValueError("Reparse point encountered")
            except FileNotFoundError:
                pass
            current = current.parent

    def apply_action(self, proposal_id: str) -> dict:
        action = self.store.get_file_action(proposal_id=proposal_id)
        if not action:
            raise ValueError("Proposal not found")
        if action["status"] != "proposed":
            raise ValueError(f"Proposal cannot be executed. Status is {action['status']}")
            
        now = self._now()
        if now > datetime.fromisoformat(action["expires_at"]):
            self.store.update_file_action(action["id"], {"status": "expired"})
            raise ValueError("Proposal expired")
            
        with self.store._connect() as db:
            root_row = db.execute("SELECT path, status FROM roots WHERE id=?", (action["root_id"],)).fetchone()
            if not root_row or root_row["status"] not in ("ready", "pending", "error"):
                raise ValueError("Root is invalid or being scanned")
                
        # Demo simulation check
        if action["mode"] == "demo":
            # Simulate success
            self.store.update_file_action(action["id"], {"status": "applied", "prepared_at": self._iso(now), "applied_at": self._iso(now)})
            return {"status": "applied", "message": "SIMULATED FILE ACTION"}

        root_path = Path(root_row["path"]).resolve(strict=True)
        abs_source = root_path / action["source_relative"]
        abs_dest = root_path / action["destination_relative"]
        
        self._validate_path_safety(root_path, abs_source)
        self._validate_path_safety(root_path, abs_dest)
        
        # Identity revalidation
        try:
            st = abs_source.stat(follow_symlinks=False)
            if hasattr(st, "st_file_attributes") and (st.st_file_attributes & 0x400):
                raise ValueError("Source is a reparse point")
            if abs_source.is_symlink():
                raise ValueError("Source is a symlink")
                
            if action["expected_size"] is not None and st.st_size != action["expected_size"]:
                raise ValueError("Source file size changed")
            if action["expected_mtime_ns"] is not None and st.st_mtime_ns != action["expected_mtime_ns"]:
                raise ValueError("Source file modified time changed")
        except FileNotFoundError:
            self.store.update_file_action(action["id"], {"status": "stale", "error": "Source missing"})
            raise ValueError("Source file missing")
            
        if abs_dest.exists():
            self.store.update_file_action(action["id"], {"status": "failed", "error": "Destination exists"})
            raise ValueError("Destination exists")
            
        # PREPARED state
        self.store.update_file_action(action["id"], {"status": "prepared", "prepared_at": self._iso(self._now())})
        
        # Mutation
        try:
            # On Windows, os.rename safely refuses if destination exists.
            os.rename(abs_source, abs_dest)
        except BaseException as e:
            self.store.update_file_action(action["id"], {"status": "failed", "error": str(e)})
            raise ValueError(f"Mutation failed: {e}")
            
        # Post-action identity
        try:
            pst = abs_dest.stat(follow_symlinks=False)
            post_updates = {
                "post_size": pst.st_size,
                "post_mtime_ns": pst.st_mtime_ns,
                "post_dev": pst.st_dev,
                "post_ino": pst.st_ino,
                "status": "applied",
                "applied_at": self._iso(self._now())
            }
        except FileNotFoundError:
            post_updates = {"status": "failed", "error": "Destination vanished right after rename"}
            
        self.store.update_file_action(action["id"], post_updates)
        
        # Update file catalog
        if post_updates.get("status") == "applied":
            with self.store._connect() as db:
                db.execute(
                    "UPDATE files SET relative_path=?, name=?, extension=? WHERE id=?",
                    (action["destination_relative"], abs_dest.name, abs_dest.suffix, action["file_id"])
                )
                
        return {"status": "applied", "message": "RENAMED"}

    def undo_action(self, action_id: str) -> dict:
        action = self.store.get_file_action(action_id=action_id)
        if not action:
            raise ValueError("Action not found")
        if action["status"] != "applied":
            raise ValueError(f"Cannot undo action in status {action['status']}")
            
        with self.store._connect() as db:
            root_row = db.execute("SELECT path, status FROM roots WHERE id=?", (action["root_id"],)).fetchone()
            if not root_row or root_row["status"] not in ("ready", "pending", "error"):
                raise ValueError("Root is invalid or being scanned")
                
        if action["mode"] == "demo":
            self.store.update_file_action(action["id"], {"status": "rolled_back", "undone_at": self._iso(self._now())})
            return {"status": "rolled_back", "message": "SIMULATED UNDO"}
            
        root_path = Path(root_row["path"]).resolve(strict=True)
        abs_source = root_path / action["source_relative"]
        abs_dest = root_path / action["destination_relative"]
        
        self._validate_path_safety(root_path, abs_source)
        self._validate_path_safety(root_path, abs_dest)
        
        if abs_source.exists():
            raise ValueError("Undo cannot be completed safely because the original path is occupied.")
            
        try:
            st = abs_dest.stat(follow_symlinks=False)
            if hasattr(st, "st_file_attributes") and (st.st_file_attributes & 0x400):
                raise ValueError("Destination is a reparse point")
            if abs_dest.is_symlink():
                raise ValueError("Destination is a symlink")
                
            if action["post_size"] is not None and st.st_size != action["post_size"]:
                raise ValueError("Destination file size changed")
            if action["post_mtime_ns"] is not None and st.st_mtime_ns != action["post_mtime_ns"]:
                raise ValueError("Destination file modified time changed")
        except FileNotFoundError:
            raise ValueError("Destination file missing")
            
        try:
            os.rename(abs_dest, abs_source)
        except BaseException as e:
            self.store.update_file_action(action["id"], {"status": "undo_failed", "error": f"Undo failed: {e}"})
            raise ValueError(f"Undo failed: {e}")
            
        self.store.update_file_action(action["id"], {"status": "rolled_back", "undone_at": self._iso(self._now())})
        
        with self.store._connect() as db:
            db.execute(
                "UPDATE files SET relative_path=?, name=?, extension=? WHERE id=?",
                (action["source_relative"], abs_source.name, abs_source.suffix, action["file_id"])
            )
            
        return {"status": "rolled_back", "message": "UNDONE"}

    def reconcile_actions(self) -> None:
        """Run at startup to reconcile unresolved actions."""
        with self.store._connect() as db:
            rows = db.execute("SELECT * FROM file_actions WHERE status='prepared'").fetchall()
            
        for row in rows:
            action = dict(row)
            root_row = self.store.get_root_details(action["root_id"]) if hasattr(self.store, "get_root_details") else None
            if not root_row:
                with self.store._connect() as db:
                    root_row_db = db.execute("SELECT path FROM roots WHERE id=?", (action["root_id"],)).fetchone()
                    if not root_row_db:
                        self.store.update_file_action(action["id"], {"status": "conflict", "error": "Root missing"})
                        continue
                    root_path = Path(root_row_db["path"])
            else:
                root_path = Path(root_row["path"])
                
            abs_source = root_path / action["source_relative"]
            abs_dest = root_path / action["destination_relative"]
            
            src_exists = abs_source.exists()
            dst_exists = abs_dest.exists()
            
            if src_exists and not dst_exists:
                self.store.update_file_action(action["id"], {"status": "failed", "error": "Reconciled: Source exists, dest missing"})
            elif not src_exists and dst_exists:
                try:
                    st = abs_dest.stat(follow_symlinks=False)
                    # Verify identity
                    match = True
                    if action["expected_size"] is not None and st.st_size != action["expected_size"]:
                        match = False
                    if action["expected_mtime_ns"] is not None and st.st_mtime_ns != action["expected_mtime_ns"]:
                        match = False
                    if match:
                        self.store.update_file_action(action["id"], {
                            "status": "applied", 
                            "applied_at": self._iso(self._now()),
                            "post_size": st.st_size,
                            "post_mtime_ns": st.st_mtime_ns,
                            "post_dev": st.st_dev,
                            "post_ino": st.st_ino
                        })
                        with self.store._connect() as db:
                            db.execute(
                                "UPDATE files SET relative_path=?, name=?, extension=? WHERE id=?",
                                (action["destination_relative"], abs_dest.name, abs_dest.suffix, action["file_id"])
                            )
                    else:
                        self.store.update_file_action(action["id"], {"status": "conflict", "error": "Reconciled: Dest exists but identity mismatch"})
                except OSError:
                    self.store.update_file_action(action["id"], {"status": "conflict", "error": "Reconciled: Dest exists but inaccessible"})
            else:
                self.store.update_file_action(action["id"], {"status": "conflict", "error": "Reconciled: Both or neither exist"})

    def get_journal(self) -> list[dict]:
        return self.store.get_file_actions()
