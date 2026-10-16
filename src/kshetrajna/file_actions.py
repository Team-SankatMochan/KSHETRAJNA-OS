import os
import errno
import secrets
import hashlib
import json
import stat
from datetime import datetime, timezone, timedelta
from pathlib import Path
import logging

from .file_storage import FileStore, QUARANTINE_DIR_NAME
from .file_scanner import is_sensitive_file
from .file_insights import PROJECT_MARKERS

logger = logging.getLogger(__name__)

# File actions are deliberately bounded even when an approved location contains
# a very large file.  Cleanup passes use the same bound before they create a
# DELETE journal record.  The limit is a safety boundary, not a claim that a
# file was fully compared when it is exceeded.
HASH_CHUNK_BYTES = 64 * 1024
MAX_HASH_BYTES = 100 * 1024 * 1024
PROTECTED_ACTION_NAMES = frozenset({
    "__init__.py", "__init__.pyi", ".gitkeep", ".keep", ".gitignore",
    ".editorconfig", ".gitattributes", ".gitmodules", "py.typed",
    *(marker.casefold() for marker in PROJECT_MARKERS),
})

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
        *,
        expected_sha256: str | None = None,
        expected_peer_ids: list[int] | None = None,
        quarantine: bool = False,
    ) -> dict:
        """
        Create a file mutation proposal.
        """
        if kind not in ("RENAME", "MOVE", "DELETE"):
            raise ValueError(f"Invalid action kind: {kind}")
        if quarantine and kind != "DELETE":
            raise ValueError("Quarantine is only available for delete actions")
        if expected_sha256 is not None and (
                not isinstance(expected_sha256, str)
                or len(expected_sha256) != hashlib.sha256().digest_size * 2
                or any(character not in "0123456789abcdef" for character in expected_sha256.lower())):
            raise ValueError("Invalid expected SHA-256 digest")
        if expected_sha256 is not None:
            expected_sha256 = expected_sha256.lower()
        if expected_peer_ids is not None and (
                not isinstance(expected_peer_ids, list)
                or any(type(peer_id) is not int or peer_id <= 0 for peer_id in expected_peer_ids)):
            raise ValueError("Invalid duplicate survivor records")
            
        if type(quarantine) is not bool:
            raise ValueError("Invalid quarantine setting")

        with self.store._connect() as db:
            file_row = db.execute(
                "SELECT r.path as root_path, f.root_id, f.relative_path, f.name, f.extension, "
                "f.modified_at AS original_modified_at, f.is_directory "
                "FROM files f JOIN roots r ON f.root_id = r.id WHERE f.id=?", (file_id,)
            ).fetchone()
            if not file_row:
                raise ValueError("File not found")
            if file_row["is_directory"]:
                raise ValueError("Directories cannot be deleted or moved by a file action")
            if self._is_quarantine_relative(file_row["relative_path"]):
                raise ValueError("Quarantine-managed files cannot be mutated directly")
            if kind == "DELETE":
                if file_row["name"].casefold() in PROTECTED_ACTION_NAMES:
                    raise ValueError("Protected structural or project marker cannot be deleted")
                if is_sensitive_file(file_row["name"], Path(file_row["name"]).suffix[1:], file_row["relative_path"]):
                    raise ValueError("Protected sensitive file cannot be deleted")
        target_root_id = file_row['root_id'] if destination_root_id is None else destination_root_id
        if type(target_root_id) is not int or target_root_id <= 0:
            raise ValueError('Invalid destination root')
        if kind != 'MOVE' and target_root_id != file_row['root_id']:
            raise ValueError('Only moves can use a different approved root')
        target_root = self.store.get_root(target_root_id)
        if not target_root or target_root['status'] not in ('ready', 'pending', 'error', 'truncated'):
            raise ValueError('Destination root is missing or being scanned')
        if kind == "DELETE" and target_root["status"] not in ("ready", "truncated"):
            raise ValueError("Delete actions require a ready or truncated approved root")
                
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
            "expected_sha256": expected_sha256,
            "expected_peer_ids": json.dumps(expected_peer_ids) if expected_peer_ids is not None else None,

            "quarantine_requested": int(quarantine and kind == "DELETE"),
            "quarantine_state": "proposed" if quarantine and kind == "DELETE" else "",
            "quarantine_relative": f"{QUARANTINE_DIR_NAME}/{action_id}" if quarantine and kind == "DELETE" else None,
            "original_modified_at": file_row["original_modified_at"],
            
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

    @staticmethod
    def _is_quarantine_relative(relative_path: str) -> bool:
        if not isinstance(relative_path, str) or "\0" in relative_path:
            return False
        normalized = relative_path.replace("\\", "/").strip("/").casefold()
        prefix = QUARANTINE_DIR_NAME.casefold()
        return normalized == prefix or normalized.startswith(prefix + "/")

    def _validate_quarantine_relative(self, action: dict) -> str:
        relative = action.get("quarantine_relative")
        action_id = action.get("id")
        expected = f"{QUARANTINE_DIR_NAME}/{action_id}"
        if (not isinstance(relative, str) or relative.replace("\\", "/") != expected
                or not isinstance(action_id, str) or not action_id
                or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for character in action_id)):
            raise ValueError("Invalid quarantine record")
        return relative

    def _quarantine_directory(self, root_path: Path) -> Path:
        directory = root_path / QUARANTINE_DIR_NAME
        self._validate_path_safety(root_path, directory)
        try:
            if directory.exists() or directory.is_symlink():
                if self._is_reparse_point(directory) or not directory.is_dir():
                    raise ValueError("Quarantine directory is unsafe")
            else:
                directory.mkdir(mode=0o700)
            self._validate_path_safety(root_path, directory)
        except FileExistsError:
            self._validate_path_safety(root_path, directory)
            if not directory.is_dir() or self._is_reparse_point(directory):
                raise ValueError("Quarantine directory is unsafe")
        except OSError as error:
            raise ValueError("Quarantine directory is unavailable") from error
        return directory

    def _quarantine_path(self, root_path: Path, action: dict) -> Path:
        relative = self._validate_quarantine_relative(action)
        directory = self._quarantine_directory(root_path)
        target = root_path / relative
        self._validate_path_safety(root_path, target)
        if target.parent != directory:
            raise ValueError("Invalid quarantine record")
        return target

    @staticmethod
    def _verify_open_descriptor(descriptor: int, expected: os.stat_result,
                                expected_sha256: str | None = None) -> None:
        current = os.fstat(descriptor)
        if (not stat.S_ISREG(current.st_mode)
                or (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns)
                != (expected.st_dev, expected.st_ino, expected.st_size, expected.st_mtime_ns)):
            raise ValueError("Source file changed before deletion")
        if expected_sha256 is not None:
            if current.st_size > MAX_HASH_BYTES:
                raise ValueError("Source file exceeds the verification limit")
            digest = hashlib.sha256()
            total = 0
            while True:
                chunk = os.read(descriptor, HASH_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_HASH_BYTES:
                    raise ValueError("Source file exceeds the verification limit")
                digest.update(chunk)
            after = os.fstat(descriptor)
            if ((after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                    != (expected.st_dev, expected.st_ino, expected.st_size, expected.st_mtime_ns)):
                raise ValueError("Source file changed while verifying deletion")
            if digest.hexdigest() != expected_sha256:
                raise ValueError("Source file content changed before deletion")

    def _unlink_verified(self, root_path: Path, relative_path: str, expected: os.stat_result,
                         expected_sha256: str | None = None) -> None:
        """Unlink through verified directory descriptors where the platform supports it.

        Path-based unlinking is vulnerable to an ancestor directory being
        swapped for a link after validation. POSIX descriptor-relative,
        no-follow traversal keeps the approved directory chain stable. Windows
        retains the existing reparse checks; its native handle implementation
        remains a separate platform qualification concern.
        """
        components = relative_path.replace("\\", "/").split("/")
        if (not components or any(not part or part in (".", "..") for part in components)
                or os.name == "nt" or os.open not in getattr(os, "supports_dir_fd", set())):
            descriptor = None
            try:
                descriptor = os.open(
                    root_path / relative_path,
                    os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0),
                )
                self._verify_open_descriptor(descriptor, expected, expected_sha256)
            finally:
                if descriptor is not None:
                    os.close(descriptor)
            os.remove(root_path / relative_path)
            return

        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptors = []
        file_descriptor = None
        try:
            current = os.open(root_path, directory_flags)
            descriptors.append(current)
            for component in components[:-1]:
                current = os.open(component, directory_flags, dir_fd=current)
                descriptors.append(current)
            parent_fd = descriptors[-1]
            name = components[-1]
            file_descriptor = os.open(
                name,
                os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent_fd,
            )
            self._verify_open_descriptor(file_descriptor, expected, expected_sha256)
            os.unlink(name, dir_fd=parent_fd)
        finally:
            if file_descriptor is not None:
                try:
                    os.close(file_descriptor)
                except OSError:
                    pass
            for descriptor in reversed(descriptors):
                try:
                    os.close(descriptor)
                except OSError:
                    pass

    @staticmethod
    def _set_copy_timestamps(descriptor: int, expected: os.stat_result) -> None:
        """Timestamp the exclusively created copy without reopening its path."""
        if os.name != "nt":
            os.utime(descriptor, ns=(expected.st_atime_ns, expected.st_mtime_ns))
            return

        import ctypes
        import msvcrt
        from ctypes import wintypes
        from .telemetry import FILETIME, _bind

        def filetime(nanoseconds: int) -> FILETIME:
            ticks = nanoseconds // 100 + 116444736000000000
            if not 0 <= ticks < 2**64:
                raise ValueError("Copy timestamp is outside the Windows FILETIME range")
            return FILETIME(ticks & 0xFFFFFFFF, ticks >> 32)

        set_file_time = _bind(
            ctypes.WinDLL("kernel32", use_last_error=True), "SetFileTime", wintypes.BOOL,
            [wintypes.HANDLE, ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME)],
        )
        accessed = filetime(expected.st_atime_ns)
        modified = filetime(expected.st_mtime_ns)
        if not set_file_time(msvcrt.get_osfhandle(descriptor), None,
                             ctypes.byref(accessed), ctypes.byref(modified)):
            raise ctypes.WinError(ctypes.get_last_error())

    def _move_without_overwrite(self, root_path: Path, source_relative: str,
                                 source_path: Path, destination_path: Path,
                                 expected: os.stat_result,
                                 expected_sha256: str | None = None) -> os.stat_result:
        """Move without replacing a target, returning its verified identity.

        A copy has a new inode; callers must bind post-move checks to the
        verified destination, not assume the source inode survived the move.
        """
        if destination_path.exists() or destination_path.is_symlink() or self._is_reparse_point(destination_path):
            raise ValueError("Destination exists")
        try:
            # A hard-link followed by descriptor-relative unlink gives POSIX a
            # no-replace move; it also leaves both copies if unlink is refused.
            os.link(source_path, destination_path, follow_symlinks=False)
        except FileExistsError as error:
            raise ValueError("Destination exists") from error
        except OSError as error:
            if error.errno not in {
                    errno.EXDEV, errno.EOPNOTSUPP, errno.ENOTSUP, errno.ENOSYS,
                    errno.EPERM, errno.EACCES}:
                raise
            # Some filesystems do not support hard links. Copy into an
            # exclusive destination, then unlink the still-verified source;
            # never fall back to rename(), whose POSIX semantics can replace a
            # destination created by a racing same-user process.
            source_descriptor = None
            destination_descriptor = None
            destination_identity = None
            try:
                source_descriptor = os.open(
                    source_path,
                    os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0),
                )
                before = os.fstat(source_descriptor)
                if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                        expected.st_dev, expected.st_ino, expected.st_size, expected.st_mtime_ns):
                    raise ValueError("Source file changed before quarantine")
                destination_descriptor = os.open(
                    destination_path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
                    0o600,
                )
                destination_stat = os.fstat(destination_descriptor)
                destination_identity = (destination_stat.st_dev, destination_stat.st_ino)
                with os.fdopen(source_descriptor, "rb") as source, os.fdopen(destination_descriptor, "wb") as destination:
                    source_descriptor = destination_descriptor = None
                    source_digest = hashlib.sha256()
                    while chunk := source.read(HASH_CHUNK_BYTES):
                        destination.write(chunk)
                        source_digest.update(chunk)
                    destination.flush()
                    os.fsync(destination.fileno())
                    after = os.fstat(source.fileno())
                    if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (
                            expected.st_dev, expected.st_ino, expected.st_size, expected.st_mtime_ns):
                        raise ValueError("Source file changed while copying")
                    self._set_copy_timestamps(destination.fileno(), expected)
                copied = destination_path.stat(follow_symlinks=False)
                if (not stat.S_ISREG(copied.st_mode)
                        or (copied.st_dev, copied.st_ino) != destination_identity
                        or (copied.st_size, copied.st_mtime_ns)
                        != (expected.st_size, expected.st_mtime_ns)):
                    raise ValueError("Quarantine identity changed while copying")
                digest = expected_sha256 or source_digest.hexdigest()
                if self._hash_regular_file(destination_path, expected_size=expected.st_size) != digest:
                    raise ValueError("Destination content changed while copying")
                verified = destination_path.stat(follow_symlinks=False)
                if (not stat.S_ISREG(verified.st_mode)
                        or (verified.st_dev, verified.st_ino, verified.st_size, verified.st_mtime_ns,
                            getattr(verified, "st_ctime_ns", None))
                        != (copied.st_dev, copied.st_ino, copied.st_size, copied.st_mtime_ns,
                            getattr(copied, "st_ctime_ns", None))):
                    raise ValueError("Quarantine identity changed while verifying copy")
                self._unlink_verified(root_path, source_relative, expected, digest)
                return verified
            except FileExistsError as collision:
                raise ValueError("Destination exists") from collision
            except BaseException:
                if destination_identity is not None:
                    try:
                        current = destination_path.stat(follow_symlinks=False)
                        if (current.st_dev, current.st_ino) == destination_identity:
                            destination_path.unlink()
                    except OSError:
                        pass
                raise
            finally:
                if source_descriptor is not None:
                    try:
                        os.close(source_descriptor)
                    except OSError:
                        pass
                if destination_descriptor is not None:
                    try:
                        os.close(destination_descriptor)
                    except OSError:
                        pass

        try:
            linked = destination_path.stat(follow_symlinks=False)
            if ((linked.st_dev, linked.st_ino, linked.st_size, linked.st_mtime_ns)
                    != (expected.st_dev, expected.st_ino, expected.st_size, expected.st_mtime_ns)):
                raise ValueError("Quarantine identity changed while moving")
            self._unlink_verified(root_path, source_relative, expected, expected_sha256)
            return linked
        except BaseException:
            try:
                current = destination_path.stat(follow_symlinks=False)
                if ((current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns)
                        == (expected.st_dev, expected.st_ino, expected.st_size, expected.st_mtime_ns)):
                    destination_path.unlink()
            except OSError:
                pass
            raise

    @staticmethod
    def _hash_regular_file(path: Path, expected_size: int | None = None) -> str | None:
        """Hash a regular file with bounded memory and stable identity.

        Returning ``None`` means the file was missing, was not a regular file,
        exceeded the hash budget, or changed while it was being read.  Cleanup
        treats every such result as a conflict rather than deleting a file on
        stale evidence.
        """
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = None
        try:
            descriptor = os.open(path, flags)
            with os.fdopen(descriptor, "rb") as stream:
                descriptor = None
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_HASH_BYTES:
                    return None
                if expected_size is not None and before.st_size != expected_size:
                    return None
                digest = hashlib.sha256()
                total = 0
                while True:
                    chunk = stream.read(HASH_CHUNK_BYTES)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_HASH_BYTES:
                        return None
                    digest.update(chunk)
                after = os.fstat(stream.fileno())
                if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)):
                    return None
                return digest.hexdigest()
        except (OSError, ValueError):
            return None
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass

    def _has_expected_duplicate_peer(self, action: dict) -> bool:
        """Confirm that a journaled duplicate still has a live survivor."""
        try:
            peer_ids = json.loads(action.get("expected_peer_ids") or "[]")
        except (TypeError, json.JSONDecodeError):
            return False
        if not isinstance(peer_ids, list) or not peer_ids or not action.get("expected_sha256"):
            return False
        target_size = action.get("expected_size")
        rows = []
        # SQLite's default variable limit is commonly 999; duplicate scans
        # may legitimately produce more peers than that, so query in chunks.
        for offset in range(0, len(peer_ids), 900):
            chunk = peer_ids[offset:offset + 900]
            with self.store._connect() as db:
                rows.extend(db.execute(
                    "SELECT f.id, f.relative_path, r.path AS root_path, r.status "
                    "FROM files f JOIN roots r ON r.id=f.root_id "
                    "WHERE f.id IN ({})".format(",".join("?" for _ in chunk)),
                    tuple(chunk),
                ).fetchall())
        for row in rows:
            if row["status"] not in ("ready", "truncated"):
                continue
            root_path = Path(row["root_path"])
            candidate = root_path / row["relative_path"]
            try:
                self._validate_path_safety(root_path, candidate)
                digest = self._hash_regular_file(candidate, expected_size=target_size)
            except (OSError, ValueError):
                continue
            if digest == action.get("expected_sha256"):
                return True
        return False

    def _apply_quarantine(self, action: dict, root_path: Path, abs_source: Path,
                          source_stat: os.stat_result) -> dict:
        target = self._quarantine_path(root_path, action)
        if target.exists() or target.is_symlink() or self._is_reparse_point(target):
            self.store.update_file_action(action["id"], {
                "status": "dismissed", "quarantine_state": "dismissed",
                "error": "Quarantine destination already exists",
            })
            raise ValueError("Quarantine destination already exists")

        digest = action.get("expected_sha256")
        if digest is None:
            digest = self._hash_regular_file(abs_source, expected_size=source_stat.st_size)
            if digest is None:
                self.store.update_file_action(action["id"], {
                    "status": "dismissed", "quarantine_state": "dismissed",
                    "error": "Source content could not be verified",
                })
                raise ValueError("Source content could not be verified")
            action["expected_sha256"] = digest
            self.store.update_file_action(action["id"], {"expected_sha256": digest})

        # The prepared record is durable before the filesystem mutation.
        self.store.update_file_action(action["id"], {
            "status": "prepared", "quarantine_state": "prepared",
            "prepared_at": self._iso(self._now()), "error": None,
        })
        try:
            moved_stat = self._move_without_overwrite(
                root_path, action["source_relative"], abs_source, target, source_stat, digest)
            quarantine_stat = target.stat(follow_symlinks=False)
            quarantine_digest = self._hash_regular_file(target, expected_size=source_stat.st_size)
            if (not stat.S_ISREG(quarantine_stat.st_mode)
                    or (quarantine_stat.st_dev, quarantine_stat.st_ino,
                        quarantine_stat.st_size, quarantine_stat.st_mtime_ns)
                    != (moved_stat.st_dev, moved_stat.st_ino,
                        moved_stat.st_size, moved_stat.st_mtime_ns)
                    or quarantine_digest != digest):
                raise ValueError("Quarantine content or identity changed")
        except BaseException as error:
            source_exists = abs_source.exists() or abs_source.is_symlink()
            target_exists = target.exists() or target.is_symlink()
            self.store.update_file_action(action["id"], {
                "status": "quarantine_failed" if target_exists and not source_exists else (
                    "quarantine_conflict" if target_exists and source_exists else "dismissed"
                ),
                "quarantine_state": "quarantine_failed" if target_exists and not source_exists else (
                    "quarantine_conflict" if target_exists and source_exists else "dismissed"
                ),
                "error": str(error),
            })
            if isinstance(error, ValueError):
                raise
            raise ValueError(f"Quarantine failed: {error}") from error

        now = self._now()
        identity = self.store._encode_identity({
            "quarantine_dev": quarantine_stat.st_dev, "quarantine_ino": quarantine_stat.st_ino,
        })
        with self.store._connect() as db:
            db.execute(
                "UPDATE file_actions SET status=?, quarantine_state=?, quarantined_at=?, applied_at=?, "
                "quarantine_size=?, quarantine_mtime_ns=?, quarantine_ctime_ns=?, quarantine_dev=?, "
                "quarantine_ino=?, quarantine_sha256=?, error=NULL WHERE id=?",
                ("quarantined", "quarantined", self._iso(now), self._iso(now),
                 quarantine_stat.st_size, quarantine_stat.st_mtime_ns,
                 getattr(quarantine_stat, "st_ctime_ns", None), identity["quarantine_dev"],
                 identity["quarantine_ino"], digest, action["id"]),
            )
            db.execute(
                "DELETE FROM files WHERE id=? AND root_id=? AND relative_path=?",
                (action["file_id"], action["root_id"], action["source_relative"]),
            )
        self.store.refresh_root_totals(action["root_id"])
        return {"status": "quarantined", "message": "QUARANTINED", "action_id": action["id"]}

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
            if action["kind"] == "DELETE" and root_row["status"] not in ("ready", "truncated"):
                raise ValueError("Delete actions require a ready or truncated approved root")
                
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
            if action.get("expected_ctime_ns") is not None and getattr(st, "st_ctime_ns", None) != action["expected_ctime_ns"]:
                self.store.update_file_action(action["id"], {"status": "failed", "error": "Source file creation/change time changed"})
                raise ValueError("Source file creation/change time changed")
            if action.get("expected_sha256") is not None:
                digest = self._hash_regular_file(abs_source, expected_size=action.get("expected_size"))
                if digest != action["expected_sha256"]:
                    self.store.update_file_action(action["id"], {"status": "failed", "error": "Source file content changed"})
                    raise ValueError("Source file content changed")
                # Hashing is intentionally followed by another identity check;
                # a file can be replaced between the initial stat and the hash.
                latest = abs_source.stat(follow_symlinks=False)
                if ((latest.st_dev, latest.st_ino, latest.st_size, latest.st_mtime_ns)
                        != (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)):
                    self.store.update_file_action(action["id"], {"status": "failed", "error": "Source file changed while validating content"})
                    raise ValueError("Source file changed while validating content")
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

        if action["kind"] == "DELETE":
            if action.get("expected_peer_ids") and not self._has_expected_duplicate_peer(action):
                self.store.update_file_action(action["id"], {"status": "failed", "error": "No duplicate survivor remains"})
                raise ValueError("No duplicate survivor remains")
            # A final no-follow stat is the last journal-side identity check
            # immediately before the selected delete mutation.
            try:
                latest = abs_source.stat(follow_symlinks=False)
                if ((latest.st_dev, latest.st_ino, latest.st_size, latest.st_mtime_ns)
                        != (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)):
                    self.store.update_file_action(action["id"], {"status": "failed", "error": "Source file changed before deletion"})
                    raise ValueError("Source file changed before deletion")
            except FileNotFoundError:
                self.store.update_file_action(action["id"], {"status": "stale", "error": "Source missing"})
                raise ValueError("Source file missing")
            if action.get("quarantine_requested"):
                # Reviewed cleanup is reversible: the action moves the source
                # into protected quarantine and keeps a restore record.
                return self._apply_quarantine(action, root_path, abs_source, st)
            # The legacy generic DELETE path is irreversible by design. Its
            # catalog row is removed only after the permanent unlink succeeds.
            self.store.update_file_action(action["id"], {"status": "prepared", "prepared_at": self._iso(self._now())})
            try:
                self._unlink_verified(root_path, action["source_relative"], st, action.get("expected_sha256"))
            except BaseException as e:
                self.store.update_file_action(action["id"], {"status": "failed", "error": str(e)})
                raise ValueError(f"Deletion failed: {e}")
            with self.store._connect() as db:
                db.execute(
                    "UPDATE file_actions SET status=?, applied_at=?, file_id=NULL, error=NULL WHERE id=?",
                    ("deleted", self._iso(self._now()), action["id"]),
                )
                db.execute("DELETE FROM files WHERE id=?", (action["file_id"],))
            self.store.refresh_root_totals(action["root_id"])
            if action.get("destination_root_id") and action["destination_root_id"] != action["root_id"]:
                self.store.refresh_root_totals(action["destination_root_id"])
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

    def _restore_catalog_location(self, action: dict, root_id: int, path: Path,
                                  restored_stat: os.stat_result) -> int:
        """Recreate only the deleted catalog row after a verified restore."""
        modified_at = action.get("original_modified_at")
        if not isinstance(modified_at, str) or not modified_at:
            modified_at = datetime.fromtimestamp(
                restored_stat.st_mtime, timezone.utc).isoformat()
        with self.store._connect() as db:
            collision = db.execute(
                "SELECT id FROM files WHERE root_id=? AND relative_path=?",
                (root_id, action["source_relative"]),
            ).fetchone()
            if collision:
                if action.get("file_id") == collision["id"]:
                    return collision["id"]
                raise ValueError("Original path is already indexed")
            file_id = action.get("file_id")
            if file_id is not None and db.execute(
                    "SELECT id FROM files WHERE id=?", (file_id,)).fetchone():
                # A scan can reuse a deleted INTEGER PRIMARY KEY. Preserve that
                # unrelated row and bind this restore to a newly allocated ID.
                file_id = None
            columns = (
                "id, root_id, relative_path, name, extension, size_bytes, modified_at, "
                "created_at, is_directory, mime_guess, excerpt, scan_pass, indexed_at"
            )
            scan_state = db.execute(
                "SELECT COALESCE(last_pass, 1) FROM scan_state WHERE root_id=?", (root_id,)
            ).fetchone()
            values = (
                file_id, root_id, action["source_relative"], path.name,
                path.suffix[1:].lower(), restored_stat.st_size, modified_at, None, 0,
                "", "", (scan_state[0] if scan_state else 1) or 1, self._iso(self._now()),
            )
            if file_id is None:
                inserted = db.execute(
                    "INSERT INTO files (root_id, relative_path, name, extension, size_bytes, modified_at, "
                    "created_at, is_directory, mime_guess, excerpt, scan_pass, indexed_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", values[1:]
                )
                file_id = inserted.lastrowid
            else:
                db.execute(f"INSERT INTO files ({columns}) VALUES ({','.join('?' for _ in values)})", values)
            db.execute("UPDATE file_actions SET file_id=? WHERE id=?", (file_id, action["id"]))
            return file_id

    def _quarantine_identity_matches(self, action: dict, quarantine_path: Path, *,
                                     allow_linked_ctime=False) -> os.stat_result:
        try:
            info = quarantine_path.stat(follow_symlinks=False)
        except FileNotFoundError:
            raise ValueError("Quarantine record is missing")
        if (not stat.S_ISREG(info.st_mode)
                or quarantine_path.is_symlink()
                or getattr(info, "st_file_attributes", 0) & 0x400):
            raise ValueError("Quarantine record is not a regular file")
        if action.get("quarantine_size") is not None and info.st_size != action["quarantine_size"]:
            raise ValueError("Quarantined file size changed")
        if action.get("quarantine_mtime_ns") is not None and info.st_mtime_ns != action["quarantine_mtime_ns"]:
            raise ValueError("Quarantined file modified time changed")
        if (not allow_linked_ctime and action.get("quarantine_ctime_ns") is not None
                and getattr(info, "st_ctime_ns", None) != action["quarantine_ctime_ns"]):
            raise ValueError("Quarantined file identity timestamp changed")
        if action.get("quarantine_dev") is not None and (
                info.st_dev != action["quarantine_dev"] or info.st_ino != action["quarantine_ino"]):
            raise ValueError("Quarantined file identity changed")
        expected_size = action.get("expected_size")
        if expected_size is not None and info.st_size != expected_size:
            raise ValueError("Quarantined file size changed")
        digest = action.get("quarantine_sha256") or action.get("expected_sha256")
        if not digest or self._hash_regular_file(quarantine_path, expected_size=info.st_size) != digest:
            raise ValueError("Quarantined file content changed")
        latest = quarantine_path.stat(follow_symlinks=False)
        if ((latest.st_dev, latest.st_ino, latest.st_size, latest.st_mtime_ns,
             getattr(latest, "st_ctime_ns", None))
                != (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
                    getattr(info, "st_ctime_ns", None))):
            raise ValueError("Quarantined file changed during verification")
        return info

    def _complete_linked_restore(self, action: dict, root_path: Path,
                                 original: Path, quarantine_path: Path) -> dict:
        """Finish only a proven restore hard-link checkpoint, not a path collision."""
        try:
            if any(action.get(key) is None for key in (
                    "quarantine_dev", "quarantine_ino", "quarantine_size", "quarantine_mtime_ns")):
                raise ValueError("Interrupted restore has incomplete quarantine identity")
            quarantine_info = self._quarantine_identity_matches(
                action, quarantine_path, allow_linked_ctime=True)
            original_info = original.stat(follow_symlinks=False)
            identity = (quarantine_info.st_dev, quarantine_info.st_ino,
                        quarantine_info.st_size, quarantine_info.st_mtime_ns)
            if (not stat.S_ISREG(original_info.st_mode)
                    or self._is_reparse_point(original)
                    or (original_info.st_dev, original_info.st_ino,
                        original_info.st_size, original_info.st_mtime_ns) != identity):
                raise ValueError("Interrupted restore original identity changed")
            digest = action.get("quarantine_sha256") or action.get("expected_sha256")
            if self._hash_regular_file(original, expected_size=original_info.st_size) != digest:
                raise ValueError("Interrupted restore original content changed")
            latest = original.stat(follow_symlinks=False)
            if ((latest.st_dev, latest.st_ino, latest.st_size, latest.st_mtime_ns,
                 getattr(latest, "st_ctime_ns", None))
                    != (*identity, getattr(original_info, "st_ctime_ns", None))):
                raise ValueError("Interrupted restore original changed during verification")
            self.store.update_file_action(action["id"], {
                "status": "restore_prepared", "quarantine_state": "restore_prepared", "error": None,
            })
            # Catalog creation and journal-ID rebinding commit together. A
            # failure to unlink leaves a verified, indexed original for retry.
            action["file_id"] = self._restore_catalog_location(
                action, action["root_id"], original, original_info)
            try:
                self._validate_path_safety(root_path, original)
                latest = original.stat(follow_symlinks=False)
                if ((latest.st_dev, latest.st_ino, latest.st_size, latest.st_mtime_ns,
                     getattr(latest, "st_ctime_ns", None))
                        != (*identity, getattr(original_info, "st_ctime_ns", None))):
                    raise ValueError("Interrupted restore original changed during verification")
                self._unlink_verified(
                    root_path, action["quarantine_relative"], quarantine_info, digest)
                restored = original.stat(follow_symlinks=False)
                if ((restored.st_dev, restored.st_ino, restored.st_size, restored.st_mtime_ns) != identity
                        or self._hash_regular_file(original, expected_size=restored.st_size) != digest):
                    raise ValueError("Interrupted restore original changed while completing restore")
            finally:
                self.store.refresh_root_totals(action["root_id"])
        except BaseException as error:
            self.store.update_file_action(action["id"], {
                "status": "restore_failed", "quarantine_state": "restore_failed", "error": str(error),
            })
            if isinstance(error, ValueError):
                raise
            raise ValueError(f"Interrupted restore failed: {error}") from error
        self.store.update_file_action(action["id"], {
            "status": "restored", "quarantine_state": "restored",
            "restored_at": self._iso(self._now()), "error": None,
        })
        return {"status": "restored", "message": "RESTORED", "action_id": action["id"]}

    def _resolve_quarantine_conflict(self, action: dict, root_path: Path,
                                     original: Path, quarantine_path: Path) -> dict:
        """Discard only a verified duplicate when the original never moved."""
        try:
            original_info = original.stat(follow_symlinks=False)
        except FileNotFoundError:
            raise ValueError("Original path is missing")
        if (not stat.S_ISREG(original_info.st_mode)
                or original.is_symlink()
                or self._is_reparse_point(original)):
            raise ValueError("Original path is not a regular file")
        checks = (
            (action.get("expected_size"), original_info.st_size, "Original file size changed"),
            (action.get("expected_mtime_ns"), original_info.st_mtime_ns, "Original file modified time changed"),
            (action.get("expected_dev"), original_info.st_dev, "Original file identity changed"),
            (action.get("expected_ino"), original_info.st_ino, "Original file identity changed"),
        )
        for expected, actual, message in checks:
            if expected is not None and actual != expected:
                raise ValueError(message)
        digest = action.get("expected_sha256")
        if not digest or self._hash_regular_file(original, expected_size=original_info.st_size) != digest:
            raise ValueError("Original file content changed")
        with self.store._connect() as db:
            row = db.execute(
                "SELECT id FROM files WHERE root_id=? AND relative_path=?",
                (action["root_id"], action["source_relative"]),
            ).fetchone()
        if not row or (action.get("file_id") is not None and row["id"] != action["file_id"]):
            raise ValueError("Original catalog identity changed")
        quarantine_info = self._quarantine_identity_matches(
            action, quarantine_path, allow_linked_ctime=True
        )
        self._unlink_verified(
            root_path,
            action["quarantine_relative"],
            quarantine_info,
            action.get("quarantine_sha256") or action.get("expected_sha256"),
        )
        now = self._now()
        self.store.update_file_action(action["id"], {
            "status": "restored", "quarantine_state": "restored",
            "restored_at": self._iso(now), "error": None,
        })
        self.store.refresh_root_totals(action["root_id"])
        return {
            "status": "restored",
            "message": "QUARANTINE_CONFLICT_RESOLVED",
            "action_id": action["id"],
        }

    def restore_quarantined_file(self, action_id: str, *, confirm=False) -> dict:
        if confirm is not True:
            raise ValueError("Restoring a quarantined file requires confirm=true")
        if (not isinstance(action_id, str) or not action_id or len(action_id) > 200
                or not action_id.isascii()
                or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for character in action_id)):
            raise ValueError("Invalid action_id")
        action = self.store.get_file_action(action_id=action_id)
        if not action or not action.get("quarantine_requested"):
            raise ValueError("Quarantine action not found")
        if action.get("mode") == "demo":
            raise ValueError("Restoring quarantined files is not available in demo mode")
        if action.get("quarantine_state") not in (
                "quarantined", "restore_failed", "quarantine_failed", "quarantine_conflict"):
            raise ValueError(f"Quarantine cannot be restored in state {action.get('quarantine_state') or action.get('status')}")
        root = self.store.get_root(action["root_id"])
        if not root or root.get("status") not in ("ready", "truncated"):
            raise ValueError("Original root is unavailable or being scanned")
        root_path = Path(root["path"])
        original = root_path / action["source_relative"]
        quarantine_path = self._quarantine_path(root_path, action)
        self._validate_path_safety(root_path, original)
        if original.exists() or original.is_symlink() or self._is_reparse_point(original):
            if action.get("quarantine_state") == "quarantine_conflict":
                return self._resolve_quarantine_conflict(action, root_path, original, quarantine_path)
            if action.get("quarantine_state") == "restore_failed":
                return self._complete_linked_restore(action, root_path, original, quarantine_path)
            raise ValueError("Original path is occupied")
        if not original.parent.is_dir():
            raise ValueError("Original destination directory is missing")
        with self.store._connect() as db:
            collision = db.execute(
                "SELECT id FROM files WHERE root_id=? AND relative_path=?",
                (action["root_id"], action["source_relative"]),
            ).fetchone()
            if collision:
                raise ValueError("Original path is already indexed")
        quarantine_stat = self._quarantine_identity_matches(action, quarantine_path)
        self.store.update_file_action(action_id, {
            "status": "restore_prepared", "quarantine_state": "restore_prepared",
            "error": None,
        })
        try:
            moved_stat = self._move_without_overwrite(
                root_path, action["quarantine_relative"], quarantine_path, original,
                quarantine_stat, action.get("quarantine_sha256") or action.get("expected_sha256"))
            restored_stat = original.stat(follow_symlinks=False)
            if ((restored_stat.st_dev, restored_stat.st_ino, restored_stat.st_size,
                 restored_stat.st_mtime_ns)
                    != (moved_stat.st_dev, moved_stat.st_ino,
                        moved_stat.st_size, moved_stat.st_mtime_ns)
                    or self._hash_regular_file(original, expected_size=restored_stat.st_size)
                    != (action.get("quarantine_sha256") or action.get("expected_sha256"))):
                raise ValueError("Restored file content or identity changed")
            self._restore_catalog_location(action, action["root_id"], original, restored_stat)
        except BaseException as error:
            updates = {
                "status": "restore_failed", "quarantine_state": "restore_failed", "error": str(error),
            }
            try:
                if not (original.exists() or original.is_symlink() or self._is_reparse_point(original)):
                    # Link creation/rollback changes ctime. Rebaseline only
                    # after proving the retained source still has its identity,
                    # metadata and digest from before this attempted restore.
                    retained = self._quarantine_identity_matches(
                        action, quarantine_path, allow_linked_ctime=True)
                    if ((retained.st_dev, retained.st_ino, retained.st_size, retained.st_mtime_ns)
                            == (quarantine_stat.st_dev, quarantine_stat.st_ino,
                                quarantine_stat.st_size, quarantine_stat.st_mtime_ns)):
                        updates["quarantine_ctime_ns"] = getattr(retained, "st_ctime_ns", None)
            except (OSError, ValueError):
                pass
            self.store.update_file_action(action_id, updates)
            if isinstance(error, ValueError):
                raise
            raise ValueError(f"Restore failed: {error}") from error
        now = self._now()
        self.store.update_file_action(action_id, {
            "status": "restored", "quarantine_state": "restored",
            "restored_at": self._iso(now), "error": None,
        })
        self.store.refresh_root_totals(action["root_id"])
        return {"status": "restored", "message": "RESTORED", "action_id": action_id}

    def reconcile_actions(self) -> None:
        """Run at startup to reconcile unresolved actions."""
        with self.store._connect() as db:
            rows = db.execute(
                "SELECT * FROM file_actions WHERE status IN ('prepared','restore_prepared') "
                "OR (status='proposed' AND quarantine_requested=1)"
            ).fetchall()
            
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
                quarantine_path = (
                    self._quarantine_path(root_path, action)
                    if action.get("quarantine_requested") else None
                )
            except (ValueError, OSError):
                self.store.update_file_action(action["id"], {"status": "conflict", "error": "Reconciled: Unsafe or missing approved path"})
                continue

            if action["status"] == "proposed" and action.get("quarantine_requested"):
                source_exists = abs_source.exists() or abs_source.is_symlink()
                quarantine_exists = quarantine_path.exists() or quarantine_path.is_symlink()
                if source_exists and not quarantine_exists:
                    self.store.update_file_action(action["id"], {
                        "status": "dismissed", "quarantine_state": "dismissed",
                        "error": "Reconciled: quarantine proposal was not started",
                    })
                elif not source_exists and quarantine_exists:
                    try:
                        quarantine_stat = self._quarantine_identity_matches(action, quarantine_path)
                        now = self._now()
                        identity = self.store._encode_identity({
                            "quarantine_dev": quarantine_stat.st_dev, "quarantine_ino": quarantine_stat.st_ino,
                        })
                        with self.store._connect() as db:
                            db.execute(
                                "UPDATE file_actions SET status=?, quarantine_state=?, quarantined_at=?, applied_at=?, "
                                "quarantine_size=?, quarantine_mtime_ns=?, quarantine_ctime_ns=?, quarantine_dev=?, "
                                "quarantine_ino=?, quarantine_sha256=?, error=NULL WHERE id=?",
                                ("quarantined", "quarantined", self._iso(now), self._iso(now),
                                 quarantine_stat.st_size, quarantine_stat.st_mtime_ns,
                                 getattr(quarantine_stat, "st_ctime_ns", None), identity["quarantine_dev"],
                                 identity["quarantine_ino"], action.get("quarantine_sha256") or action.get("expected_sha256"), action["id"]),
                            )
                            db.execute(
                                "DELETE FROM files WHERE id=? AND root_id=? AND relative_path=?",
                                (action["file_id"], action["root_id"], action["source_relative"]),
                            )
                        self.store.refresh_root_totals(action["root_id"])
                    except (OSError, ValueError) as error:
                        self.store.update_file_action(action["id"], {
                            "status": "quarantine_conflict", "quarantine_state": "quarantine_conflict",
                            "error": f"Reconciled: {error}",
                        })
                else:
                    self.store.update_file_action(action["id"], {
                        "status": "quarantine_conflict", "quarantine_state": "quarantine_conflict",
                        "error": "Reconciled: both or neither quarantine path exists",
                    })
                continue

            if action["status"] == "restore_prepared" and action.get("quarantine_requested"):
                source_exists = abs_source.exists() or abs_source.is_symlink()
                quarantine_exists = quarantine_path.exists() or quarantine_path.is_symlink()
                if source_exists and not quarantine_exists:
                    try:
                        restored_stat = abs_source.stat(follow_symlinks=False)
                        if (restored_stat.st_size != action.get("expected_size")
                                or self._hash_regular_file(abs_source, expected_size=restored_stat.st_size)
                                != (action.get("quarantine_sha256") or action.get("expected_sha256"))):
                            raise ValueError("Reconciled: Restored identity mismatch")
                        self._restore_catalog_location(action, action["root_id"], abs_source, restored_stat)
                        self.store.update_file_action(action["id"], {
                            "status": "restored", "quarantine_state": "restored",
                            "restored_at": self._iso(self._now()), "error": None,
                        })
                        self.store.refresh_root_totals(action["root_id"])
                    except (OSError, ValueError) as error:
                        self.store.update_file_action(action["id"], {
                            "status": "restore_failed", "quarantine_state": "restore_failed",
                            "error": str(error),
                        })
                elif not source_exists and quarantine_exists:
                    self.store.update_file_action(action["id"], {
                        "status": "restore_failed", "quarantine_state": "restore_failed",
                        "error": "Reconciled: Restore did not occur",
                    })
                elif source_exists and quarantine_exists:
                    try:
                        self._complete_linked_restore(action, root_path, abs_source, quarantine_path)
                    except ValueError:
                        # The restore-specific failure remains retryable; never
                        # route it through quarantine-before-move recovery.
                        pass
                else:
                    self.store.update_file_action(action["id"], {
                        "status": "restore_failed", "quarantine_state": "restore_failed",
                        "error": "Reconciled: Neither restore path exists",
                    })
                continue

            if action["kind"] == "DELETE":
                if action.get("quarantine_requested"):
                    source_exists = abs_source.exists() or abs_source.is_symlink()
                    quarantine_exists = quarantine_path.exists() or quarantine_path.is_symlink()
                    if not source_exists and quarantine_exists:
                        try:
                            quarantine_stat = self._quarantine_identity_matches(action, quarantine_path)
                            now = self._now()
                            identity = self.store._encode_identity({
                                "quarantine_dev": quarantine_stat.st_dev, "quarantine_ino": quarantine_stat.st_ino,
                            })
                            with self.store._connect() as db:
                                db.execute(
                                    "UPDATE file_actions SET status=?, quarantine_state=?, quarantined_at=?, applied_at=?, "
                                    "quarantine_size=?, quarantine_mtime_ns=?, quarantine_ctime_ns=?, quarantine_dev=?, "
                                    "quarantine_ino=?, quarantine_sha256=?, error=NULL WHERE id=?",
                                    ("quarantined", "quarantined", self._iso(now), self._iso(now),
                                     quarantine_stat.st_size, quarantine_stat.st_mtime_ns,
                                     getattr(quarantine_stat, "st_ctime_ns", None), identity["quarantine_dev"],
                                     identity["quarantine_ino"],
                                     action.get("quarantine_sha256") or action.get("expected_sha256"), action["id"]),
                                )
                                db.execute(
                                    "DELETE FROM files WHERE id=? AND root_id=? AND relative_path=?",
                                    (action["file_id"], action["root_id"], action["source_relative"]),
                                )
                            self.store.refresh_root_totals(action["root_id"])
                        except (OSError, ValueError) as error:
                            self.store.update_file_action(action["id"], {
                                "status": "quarantine_conflict", "quarantine_state": "quarantine_conflict",
                                "error": f"Reconciled: {error}",
                            })
                    elif source_exists and not quarantine_exists:
                        try:
                            # Resolve only a proven no-op, never an occupied or
                            # changed original inferred from path presence alone.
                            original_stat = abs_source.stat(follow_symlinks=False)
                            expected_identity = tuple(action.get(key) for key in (
                                "expected_dev", "expected_ino", "expected_size", "expected_mtime_ns"))
                            original_identity = (
                                original_stat.st_dev, original_stat.st_ino,
                                original_stat.st_size, original_stat.st_mtime_ns)
                            if (not stat.S_ISREG(original_stat.st_mode)
                                    or any(value is None for value in expected_identity)
                                    or original_identity != expected_identity
                                    or (action.get("expected_ctime_ns") is not None
                                        and getattr(original_stat, "st_ctime_ns", None)
                                        != action["expected_ctime_ns"])):
                                raise ValueError("Original file identity changed or is unverified")
                            digest = action.get("expected_sha256")
                            if not digest or self._hash_regular_file(
                                    abs_source, expected_size=original_stat.st_size) != digest:
                                raise ValueError("Original file content changed or is unverified")
                            latest = abs_source.stat(follow_symlinks=False)
                            if ((latest.st_dev, latest.st_ino, latest.st_size, latest.st_mtime_ns,
                                 getattr(latest, "st_ctime_ns", None))
                                    != (*original_identity, getattr(original_stat, "st_ctime_ns", None))):
                                raise ValueError("Original file changed during reconciliation")
                            catalog = self.store.get_file(action.get("file_id"))
                            if (not catalog or catalog["root_id"] != action["root_id"]
                                    or catalog["relative_path"] != action["source_relative"]):
                                raise ValueError("Original catalog identity changed")
                            try:
                                quarantine_path.stat(follow_symlinks=False)
                            except FileNotFoundError:
                                pass
                            else:
                                raise ValueError("Quarantine destination appeared during reconciliation")
                            self.store.update_file_action(action["id"], {
                                "status": "dismissed", "quarantine_state": "dismissed",
                                "error": "Reconciled: verified original was not quarantined",
                            })
                        except (OSError, ValueError) as error:
                            self.store.update_file_action(action["id"], {
                                "status": "quarantine_conflict", "quarantine_state": "quarantine_conflict",
                                "error": f"Reconciled: {error}",
                            })
                    else:
                        self.store.update_file_action(action["id"], {
                            "status": "quarantine_conflict", "quarantine_state": "quarantine_conflict",
                            "error": "Reconciled: Both or neither quarantine path exists",
                        })
                    continue
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
