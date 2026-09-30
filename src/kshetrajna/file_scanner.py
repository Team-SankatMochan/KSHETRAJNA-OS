import os
import stat
from pathlib import Path
from typing import Generator, Tuple, Callable

MAX_DEPTH = 20
MAX_FILES = 50000
MAX_FILE_SIZE = 100 * 1024 * 1024
MAX_EXCERPT_BYTES = 4096
SCAN_BATCH_SIZE = 200

FILE_ATTRIBUTE_REPARSE_POINT = 0x400

TEXT_EXTENSIONS = frozenset({
    'txt', 'md', 'csv', 'json', 'log', 'py', 'js', 'ts', 'html',
    'css', 'yaml', 'yml', 'toml', 'jsx', 'tsx'
})

SENSITIVE_NAMES = frozenset({
    '.env', '.env.local', '.env.production', '.env.development',
    'id_rsa', 'id_ed25519', 'id_ecdsa',
    '.npmrc', '.pypirc', '.netrc', '.pgpass',
    'credentials.json', 'service-account.json',
    'secrets.yaml', 'secrets.yml', 'vault.json',
})

SENSITIVE_EXTENSIONS = frozenset({'pem', 'key', 'p12', 'pfx', 'jks', 'keystore'})

SENSITIVE_DIRS = frozenset({'.git', '.ssh', '.aws'})

def extract_excerpt(file_path: Path, max_bytes: int = MAX_EXCERPT_BYTES) -> str:
    """Read the first max_bytes of a text file, stripped of control chars."""
    try:
        if file_path.stat().st_size > MAX_FILE_SIZE:
            return ""
        
        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            text = f.read(max_bytes)
        
        # Binary check: density of null bytes or non-printable chars.
        if '\0' in text:
            return ""

        return ''.join(c for c in text if c in ('\n', '\t') or c.isprintable())
    except (PermissionError, OSError, UnicodeDecodeError):
        return ""

def walk(root: Path, is_cancelled: Callable[[], bool] = lambda: False) -> Generator[Tuple[str, os.DirEntry, os.stat_result], None, None]:
    """Iterative directory walk using os.scandir."""
    try:
        resolved_root = root.resolve(strict=True)
    except OSError:
        return

    seen_count = 0
    stack = [(root, "", 0)]  # (absolute_path, relative_prefix, depth)

    while stack:
        if is_cancelled():
            break

        current, prefix, depth = stack.pop()
        if depth > MAX_DEPTH:
            continue
        try:
            entries = list(os.scandir(current))
        except (PermissionError, OSError):
            continue

        for entry in sorted(entries, key=lambda e: e.name.lower()):
            if is_cancelled():
                return

            try:
                info = entry.stat(follow_symlinks=False)
            except (PermissionError, OSError):
                continue

            attrs = getattr(info, 'st_file_attributes', 0)
            if attrs & FILE_ATTRIBUTE_REPARSE_POINT:
                continue

            relative = prefix + "/" + entry.name if prefix else entry.name

            try:
                resolved_candidate = Path(entry.path).resolve(strict=False)
                if not resolved_candidate.is_relative_to(resolved_root):
                    continue
            except (OSError, ValueError):
                continue

            if entry.is_dir(follow_symlinks=False):
                stack.append((Path(entry.path), relative, depth + 1))
            else:
                seen_count += 1
                if seen_count > MAX_FILES:
                    # Truncate scan
                    return
                yield (relative, entry, info)
