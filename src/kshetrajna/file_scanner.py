import os
import stat
import html
import re
import zipfile
from xml.etree import ElementTree
from pathlib import Path
from typing import Generator, Tuple, Callable
from .pdf_preview import extract_pdf_excerpt, MAX_PDF_BYTES

MAX_DEPTH = 20
MAX_FILES = 50000
MAX_FILE_SIZE = 100 * 1024 * 1024
MAX_EXCERPT_BYTES = 4096
MAX_ARCHIVE_PREVIEW_BYTES = 64 * 1024
SCAN_BATCH_SIZE = 200
INSPECTION_VERSION = 2

FILE_ATTRIBUTE_REPARSE_POINT = 0x400


class ScanTruncated(Exception):
    """Raised when a scan reaches the maximum number of files."""


TEXT_EXTENSIONS = frozenset({
    'txt', 'md', 'csv', 'tsv', 'json', 'log', 'out', 'trace', 'py', 'js', 'ts', 'html',
    'css', 'scss', 'yaml', 'yml', 'toml', 'jsx', 'tsx', 'java', 'c', 'h', 'cc', 'cpp',
    'cxx', 'hpp', 'cs', 'go', 'rs', 'php', 'rb', 'swift', 'kt', 'kts', 'sql', 'sh',
    'bash', 'ps1', 'bat', 'ini', 'xml', 'rtf'
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


def is_sensitive_file(name: str, extension: str, relative: str) -> bool:
    name = name.casefold()
    parts = relative.replace('\\', '/').casefold().split('/')
    return (name in SENSITIVE_NAMES or name.startswith('.env.')
            or extension.casefold() in SENSITIVE_EXTENSIONS
            or any(part in SENSITIVE_DIRS for part in parts))

def extract_excerpt(file_path: Path, max_bytes: int = MAX_EXCERPT_BYTES) -> str:
    """Read the first max_bytes of a text file, stripped of control chars."""
    try:
        if file_path.stat().st_size > MAX_FILE_SIZE:
            return ""
        
        # Read a byte bound rather than a character bound.  This keeps the
        # privacy/performance promise true for multi-byte UTF-8 content.
        with open(file_path, 'rb') as f:
            raw = f.read(max_bytes)
        text = raw.decode('utf-8', errors='ignore')
        
        # Binary check: density of null bytes or non-printable chars.
        if '\0' in text:
            return ""

        return ''.join(c for c in text if c in ('\n', '\t') or c.isprintable())
    except (PermissionError, OSError, UnicodeDecodeError):
        return ""


def extract_office_excerpt(file_path: Path, max_bytes: int = MAX_EXCERPT_BYTES) -> str:
    """Extract a small readable preview from safe Office Open XML parts.

    Office documents are ZIP containers. Both compressed input and decompressed
    XML are bounded; macros, external links, and embedded objects are not read.
    """
    try:
        if file_path.stat().st_size > MAX_FILE_SIZE:
            return ""
        extension = file_path.suffix.casefold()
        pattern = {
            ".docx": r"word/document\.xml",
            ".pptx": r"ppt/slides/slide\d+\.xml",
            ".xlsx": r"xl/(?:sharedStrings|worksheets/sheet\d+)\.xml",
        }.get(extension)
        if not pattern:
            return ""
        chunks: list[str] = []
        total = 0
        with zipfile.ZipFile(file_path) as archive:
            names = [name for name in archive.namelist() if re.fullmatch(pattern, name)]
            def part_order(name):
                numbers = re.findall(r"\d+", name)
                return (0 if 'sharedStrings' in name else 1, int(numbers[-1]) if numbers else 0)
            for name in sorted(names, key=part_order)[:32]:
                if total >= MAX_ARCHIVE_PREVIEW_BYTES:
                    break
                with archive.open(name) as member:
                    raw = member.read(MAX_ARCHIVE_PREVIEW_BYTES - total)
                total += len(raw)
                if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
                    continue
                xml_text = raw.decode("utf-8", errors="ignore")
                # Pull completed text nodes even when a large XML part ends
                # beyond the budget; do not mistake XML tags for document text.
                parser = ElementTree.XMLPullParser(events=("end",))
                parts = []
                try:
                    parser.feed(raw)
                    for _, node in parser.read_events():
                        if node.tag.rsplit('}', 1)[-1] == 't' and node.text:
                            parts.append(node.text)
                        node.clear()
                except ElementTree.ParseError:
                    # Some Office producers emit a bounded fragment without
                    # all namespace declarations. Text-node fallback remains
                    # inert: it never resolves links or executes content.
                    parts.extend(html.unescape(part) for part in re.findall(
                        r"<[^>]*:?t[^>]*>([^<]*)</[^>]*:?t>", xml_text, re.I))
                text = " ".join(parts)
                if text:
                    chunks.append(text)
                if sum(len(chunk.encode("utf-8")) for chunk in chunks) >= max_bytes:
                    break
        preview = "\n".join(chunks).encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")
        return "".join(c for c in preview if c in ("\n", "\t") or c.isprintable())
    except (OSError, zipfile.BadZipFile, RuntimeError, ValueError):
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
            if attrs & FILE_ATTRIBUTE_REPARSE_POINT or entry.is_symlink():
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
            elif stat.S_ISREG(info.st_mode):
                seen_count += 1
                if seen_count > MAX_FILES:
                    # Do not silently turn a partial index into a successful scan.
                    raise ScanTruncated
                yield (relative, entry, info)
