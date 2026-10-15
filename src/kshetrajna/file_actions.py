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
        if not isinstance(dest_relative, str) or not dest_relative or '\0' in dest_relative:
            raise ValueError("Invalid destination path")

        # Treat both slash styles as separators.  The service may run on a
        # different OS than the path was supplied for, so checking Path alone
        # is not sufficient for Windows traversal strings such as ..\\x.
        parts = dest_relative.replace('\\', '/').split('/')
        if (not parts or any(part in ('', '.', '..') for part in parts)
                or Path(dest_relative).is_absolute()
                or Path(dest_relative).drive
                or Path(dest_relative).root
                or dest_relative.startswith(('/', '\\'))
                or ':' in dest_relative):
            raise ValueError("Invalid destination path")
        if self._is_invalid_basename(parts[-1]):
            raise ValueError(f"Invalid filename: {parts[-1]}")

    def create_proposal(
        self,
        file_id: int,
        kind: str,
        destination_relative: str,
        mode: str = "live",
        destination_root_id: int | None = None,
    ) -> dict:
        """
        Create a file mutation proposal.
        """
        if kind not in ("RENAME", "MOVE", "DELETE"):
            raise ValueError(f"Invalid action kind: {kind}")
            
        with self.store._connect() as db:
            file_row = db.execute("SELECT r.path as root_path, f.root_id, f.relative_path, f.name, f.is_directory FROM files f JOIN roots r ON f.root_id = r.id WHERE f.id=?", (file_id,)).fetchone()
            if not file_row:
                raise ValueError("File not found")
            if file_row["is_directory"]:
                raise ValueError("Directories cannot be deleted or moved by a file action")
        target_root_id = file_row['root_id'] if destination_root_id is None else destination_root_id
        if type(target_root_id) is not int or target_root_id <= 0:
            raise ValueError('Invalid destination root')
        if kind != 'MOVE' and target_root_id != file_row['root_id']:
            raise ValueError('Only moves can use a different approved root')
        target_root = self.store.get_root(target_root_id)
        if not target_root or target_root['status'] not in ('ready', 'pending', 'error', 'truncated'):
            raise ValueError('Destination root is missing or being scanned')
                
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
            if mode != "demo":
                parent = (Path(target_root['path']) / dest_rel).parent
                if not parent.exists() or not parent.is_dir():
                    raise ValueError("Destination directory does not exist")
        else:
            # A delete action keeps the original relative path as its durable
            # target so startup recovery can determine whether it completed.
            dest_rel = file_row["relative_path"]
            
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
            "destination_root_id": target_root_id,
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

    def _destination_root(self, action: dict) -> Path:
        root = self.store.get_root(action.get('destination_root_id') or action['root_id'])
        if not root or root['status'] not in ('ready', 'pending', 'error', 'truncated'):
            raise ValueError('Destination root is missing or being scanned')
        return Path(root['path'])

    def _catalog_location(self, action: dict, path: Path, *, undo=False) -> None:
        root_id = action['root_id'] if undo else (action.get('destination_root_id') or action['root_id'])
        relative = action['source_relative'] if undo else action['destination_relative']
        with self.store._connect() as db:
            db.execute('UPDATE files SET root_id=?,relative_path=?,name=?,extension=? WHERE id=?',
                       (root_id, relative, path.name, path.suffix[1:].lower(), action['file_id']))
        self.store.refresh_root_totals(action['root_id'], action.get('destination_root_id') or action['root_id'])

    def dismiss_proposal(self, proposal_id: str) -> None:
        action = self.store.get_file_action(proposal_id=proposal_id)
        if not action:
            return
        if action["status"] == "proposed":
            self.store.update_file_action(action["id"], {"status": "dismissed"})
            
    @staticmethod
    def _is_reparse_point(path: Path) -> bool:
        try:
            if path.is_symlink():
                return True
            stat_result = path.stat(follow_symlinks=False)
        except FileNotFoundError:
            return False
        return bool(getattr(stat_result, "st_file_attributes", 0) & 0x400)

    def _validate_path_safety(self, root_path: Path, target_path: Path) -> None:
        resolved_root = root_path.resolve(strict=True)
        resolved_target = target_path.resolve(strict=False)
        if not resolved_target.is_relative_to(resolved_root):
            raise ValueError("Path escapes approved root")

        # Check the lexical path as well as the resolved path.  Resolving first
        # erases an in-root symlink, which could otherwise redirect a mutation
        # through a link to a different directory inside the root.
        lexical_root = Path(os.path.abspath(os.fspath(root_path)))
        lexical_target = Path(os.path.abspath(os.fspath(target_path)))
        if not lexical_target.is_relative_to(lexical_root):
            raise ValueError("Path escapes approved root")

        current = lexical_target
        while True:
            if self._is_reparse_point(current):
                raise ValueError("Symlink or reparse point encountered")
            if current == lexical_root:
                break
            parent = current.parent
            if parent == current:
                raise ValueError("Path escapes approved root")
            current = parent

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
            if not root_row or root_row["status"] not in ("ready", "pending", "error", "truncated"):
                raise ValueError("Root is invalid or being scanned")
                
        # Demo simulation check
        if action["mode"] == "demo":
            if action["kind"] == "DELETE":
                with self.store._connect() as db:
                    db.execute(
                        "UPDATE file_actions SET status=?, prepared_at=?, applied_at=?, file_id=NULL WHERE id=?",
                        ("deleted", self._iso(now), self._iso(now), action["id"]),
                    )
                    db.execute("DELETE FROM files WHERE id=?", (action["file_id"],))
                return {"status": "deleted", "message": "SIMULATED PERMANENT DELETE"}
            self.store.update_file_action(action["id"], {"status": "applied", "prepared_at": self._iso(now), "applied_at": self._iso(now)})
            return {"status": "applied", "message": "SIMULATED FILE ACTION"}

        root_path = Path(root_row["path"])
        destination_root = self._destination_root(action)
        abs_source = root_path / action["source_relative"]
        abs_dest = destination_root / action["destination_relative"]
        
        self._validate_path_safety(root_path, abs_source)
        self._validate_path_safety(destination_root, abs_dest)
        
        # Identity revalidation
        try:
            st = abs_source.stat(follow_symlinks=False)
            if action['kind'] == 'MOVE' and st.st_dev != abs_dest.parent.stat().st_dev:
                self.store.update_file_action(action['id'], {'status': 'failed', 'error': 'Cross-drive moves are not supported'})
                raise ValueError('Cross-drive moves are not supported; choose a destination on the same drive')
            if hasattr(st, "st_file_attributes") and (st.st_file_attributes & 0x400):
                self.store.update_file_action(action["id"], {"status": "failed", "error": "Source is a reparse point"})
                raise ValueError("Source is a reparse point")
            if abs_source.is_symlink():
                self.store.update_file_action(action["id"], {"status": "failed", "error": "Source is a symlink"})
                raise ValueError("Source is a symlink")
                
            if action["expected_size"] is not None and st.st_size != action["expected_size"]:
                self.store.update_file_action(action["id"], {"status": "failed", "error": "Source file size changed"})
                raise ValueError("Source file size changed")
            if action["expected_mtime_ns"] is not None and st.st_mtime_ns != action["expected_mtime_ns"]:
                self.store.update_file_action(action["id"], {"status": "failed", "error": "Source file modified time changed"})
                raise ValueError("Source file modified time changed")
            if action['expected_dev'] is not None and (st.st_dev != action['expected_dev'] or st.st_ino != action['expected_ino']):
                self.store.update_file_action(action["id"], {"status": "failed", "error": "Source file identity changed"})
                raise ValueError('Source file identity changed')
            current_file = self.store.get_file(action['file_id'])
            if not current_file or current_file['root_id'] != action['root_id'] or current_file['relative_path'] != action['source_relative']:
                self.store.update_file_action(action["id"], {"status": "failed", "error": "Source catalog location changed"})
                raise ValueError('Source catalog location changed')
            with self.store._connect() as db:
                collision = db.execute('SELECT id FROM files WHERE root_id=? AND relative_path=? AND id!=?',
                    (action.get('destination_root_id') or action['root_id'], action['destination_relative'], action['file_id'])).fetchone()
            if collision:
                self.store.update_file_action(action["id"], {"status": "failed", "error": "Destination is already indexed; rescan before moving"})
                raise ValueError('Destination is already indexed; rescan before moving')
        except FileNotFoundError:
            self.store.update_file_action(action["id"], {"status": "stale", "error": "Source missing"})
            raise ValueError("Source file missing")
            
        if abs_dest.exists():
            if action["kind"] == "DELETE" and abs_dest == abs_source:
                pass
            else:
                self.store.update_file_action(action["id"], {"status": "failed", "error": "Destination exists"})
                raise ValueError("Destination exists")

        # A delete is irreversible by design. It still receives the same
        # journal-before-mutation and identity checks as a move, but its file
        # catalog row is removed only in the transaction that marks it deleted.
        if action["kind"] == "DELETE":
            self.store.update_file_action(action["id"], {"status": "prepared", "prepared_at": self._iso(self._now())})
            try:
                os.remove(abs_source)
            except BaseException as e:
                self.store.update_file_action(action["id"], {"status": "failed", "error": str(e)})
                raise ValueError(f"Deletion failed: {e}")
            with self.store._connect() as db:
                db.execute(
                    "UPDATE file_actions SET status=?, applied_at=?, file_id=NULL, error=NULL WHERE id=?",
                    ("deleted", self._iso(self._now()), action["id"]),
                )
                db.execute("DELETE FROM files WHERE id=?", (action["file_id"],))
            return {"status": "deleted", "message": "DELETED PERMANENTLY"}

        # PREPARED state
        self.store.update_file_action(action["id"], {"status": "prepared", "prepared_at": self._iso(self._now())})
        
        # Mutation
        try:
            # On Windows, os.rename safely refuses if destination exists.
            os.rename(abs_source, abs_dest)
        except BaseException as e:
            self.store.update_file_action(action["id"], {"status": "failed", "error": str(e)})
            raise ValueError(f"Mutation failed: {e}")
            
        # Post-action identity.  A successful rename followed by a race (for
        # example, the destination being removed or replaced) is not an
        # applied action, even though the OS mutation already happened.
        try:
            pst = abs_dest.stat(follow_symlinks=False)
            identity_matches = (
                action["expected_dev"] is None
                or (pst.st_dev == action["expected_dev"] and pst.st_ino == action["expected_ino"])
            )
            if not identity_matches:
                post_updates = {
                    "status": "failed",
                    "error": "Destination identity changed right after rename",
                }
            else:
                post_updates = {
                    "post_size": pst.st_size,
                    "post_mtime_ns": pst.st_mtime_ns,
                    "post_dev": pst.st_dev,
                    "post_ino": pst.st_ino,
                    "status": "applied",
                    "applied_at": self._iso(self._now())
                }
        except OSError as error:
            post_updates = {"status": "failed", "error": f"Destination vanished right after rename: {error}"}

        self.store.update_file_action(action["id"], post_updates)

        # Update file catalog only after post-mutation verification succeeds.
        if post_updates.get("status") == "applied":
            self._catalog_location(action, abs_dest)
            return {"status": "applied", "message": "RENAMED"}

        return {"status": "failed", "message": post_updates["error"]}

    def undo_action(self, action_id: str) -> dict:
        action = self.store.get_file_action(action_id=action_id)
        if not action:
            raise ValueError("Action not found")
        if action["status"] not in ('applied', 'undo_failed'):
            raise ValueError(f"Cannot undo action in status {action['status']}")
            
        with self.store._connect() as db:
            root_row = db.execute("SELECT path, status FROM roots WHERE id=?", (action["root_id"],)).fetchone()
            if not root_row or root_row["status"] not in ("ready", "pending", "error", "truncated"):
                raise ValueError("Root is invalid or being scanned")
                
        if action["mode"] == "demo":
            self.store.update_file_action(action["id"], {"status": "rolled_back", "undone_at": self._iso(self._now())})
            return {"status": "rolled_back", "message": "SIMULATED UNDO"}
            
        root_path = Path(root_row["path"])
        destination_root = self._destination_root(action)
        abs_source = root_path / action["source_relative"]
        abs_dest = destination_root / action["destination_relative"]
        
        self._validate_path_safety(root_path, abs_source)
        self._validate_path_safety(destination_root, abs_dest)
        
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
            if action["post_dev"] is not None and (st.st_dev != action["post_dev"] or st.st_ino != action["post_ino"]):
                raise ValueError("Destination file identity changed")
        except FileNotFoundError:
            raise ValueError("Destination file missing")
            
        try:
            os.rename(abs_dest, abs_source)
        except BaseException as e:
            self.store.update_file_action(action["id"], {"status": "undo_failed", "error": f"Undo failed: {e}"})
            raise ValueError(f"Undo failed: {e}")
            
        self.store.update_file_action(action["id"], {"status": "rolled_back", "undone_at": self._iso(self._now())})
        
        self._catalog_location(action, abs_source, undo=True)
            
        return {"status": "rolled_back", "message": "UNDONE"}

    def reconcile_actions(self) -> None:
        """Run at startup to reconcile unresolved actions."""
        with self.store._connect() as db:
            rows = db.execute("SELECT * FROM file_actions WHERE status='prepared'").fetchall()
            
        for row in rows:
            action = self.store.get_file_action(action_id=row["id"])
            if not action:
                continue
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
            try:
                destination_root = self._destination_root(action)
                abs_dest = destination_root / action['destination_relative']
                self._validate_path_safety(root_path, abs_source)
                self._validate_path_safety(destination_root, abs_dest)
            except (ValueError, OSError):
                self.store.update_file_action(action["id"], {"status": "conflict", "error": "Reconciled: Unsafe or missing approved path"})
                continue

            if action["kind"] == "DELETE":
                if abs_source.exists():
                    self.store.update_file_action(action["id"], {"status": "failed", "error": "Reconciled: Delete did not occur"})
                else:
                    with self.store._connect() as db:
                        db.execute("UPDATE file_actions SET status=?, applied_at=?, file_id=NULL WHERE id=?",
                                   ("deleted", self._iso(self._now()), action["id"]))
                        db.execute("DELETE FROM files WHERE id=?", (action["file_id"],))
                continue
            
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
                    if action["expected_dev"] is not None and (st.st_dev != action["expected_dev"] or st.st_ino != action["expected_ino"]):
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
                        self._catalog_location(action, abs_dest)
                    else:
                        self.store.update_file_action(action["id"], {"status": "conflict", "error": "Reconciled: Dest exists but identity mismatch"})
                except OSError:
                    self.store.update_file_action(action["id"], {"status": "conflict", "error": "Reconciled: Dest exists but inaccessible"})
            else:
                self.store.update_file_action(action["id"], {"status": "conflict", "error": "Reconciled: Both or neither exist"})

    def get_journal(self) -> list[dict]:
        return self.store.get_file_actions()
