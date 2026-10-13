"""Bounded, dependency-free PDF inspection for the local dashboard preview."""

import re
import zlib

PDF_EXTENSIONS = frozenset({"pdf"})
MAX_PDF_BYTES = 32 * 1024 * 1024
MAX_PDF_DECOMPRESSED_BYTES = 2 * 1024 * 1024
MAX_PDF_TEXT_BYTES = 4096


def _pdf_literal(data: bytes, start: int) -> tuple[str, int]:
    """Read one PDF literal string, handling escaped parentheses."""
    output = bytearray()
    depth = 1
    index = start + 1
    while index < len(data):
        value = data[index]
        if value == 0x5c:  # backslash
            index += 1
            if index >= len(data):
                break
            escaped = data[index]
            mapping = {ord('n'): b'\n', ord('r'): b'\r', ord('t'): b'\t', ord('b'): b'\b', ord('f'): b'\f'}
            if escaped in mapping:
                output.extend(mapping[escaped])
            elif escaped in (ord('('), ord(')'), ord('\\')):
                output.append(escaped)
            elif 48 <= escaped <= 55:
                digits = bytes([escaped])
                while index + 1 < len(data) and len(digits) < 3 and 48 <= data[index + 1] <= 55:
                    index += 1
                    digits += bytes([data[index]])
                output.append(int(digits, 8))
            else:
                output.append(escaped)
        elif value == 0x28:
            depth += 1
            output.append(value)
        elif value == 0x29:
            depth -= 1
            if depth == 0:
                return output.decode('utf-8', errors='replace'), index + 1
            output.append(value)
        else:
            output.append(value)
        index += 1
    return output.decode('utf-8', errors='replace'), index


def _text_from_content(data: bytes, limit: int) -> str:
    """Extract visible text operators from a bounded PDF content stream."""
    visible = bytearray()
    for match in re.finditer(rb'\((?:\\.|[^()\\])*\)', data, re.DOTALL):
        value, _ = _pdf_literal(data, match.start())
        if value.strip():
            visible.extend(value.encode('utf-8', errors='replace'))
            visible.extend(b' ')
        if len(visible) >= limit:
            break
    # Hex strings are commonly used for text with a simple byte encoding.
    if len(visible) < limit:
        for match in re.finditer(rb'<([0-9A-Fa-f\s]{2,})>', data):
            raw = re.sub(rb'\s+', b'', match.group(1))
            if len(raw) % 2:
                continue
            value = bytes.fromhex(raw.decode('ascii')).decode('utf-16-be', errors='ignore')
            if value.strip():
                visible.extend(value.encode('utf-8'))
                visible.extend(b' ')
            if len(visible) >= limit:
                break
    text = visible[:limit].decode('utf-8', errors='replace')
    return ''.join(character for character in text if character in ('\n', '\t') or character.isprintable()).strip()


def inspect_pdf(data: bytes) -> dict:
    if not data.startswith(b'%PDF-'):
        raise ValueError('This file is not a PDF')
    if len(data) > MAX_PDF_BYTES:
        raise ValueError('PDF preview supports files up to 32 MiB')
    page_count = len(re.findall(rb'/Type\s*/Page\b', data))
    return {
        'format': 'PDF',
        'content_type': 'application/pdf',
        'pages': page_count or None,
        'size_bytes': len(data),
        'inspection': 'pdf-signature-and-bounded-text',
    }


def extract_pdf_excerpt(data: bytes, max_bytes: int = MAX_PDF_TEXT_BYTES) -> str:
    details = inspect_pdf(data)
    streams: list[bytes] = [data]
    total = 0
    for match in re.finditer(rb'stream\r?\n(.*?)\r?\nendstream', data, re.DOTALL):
        compressed = match.group(1)
        try:
            decompressor = zlib.decompressobj()
            expanded = decompressor.decompress(compressed, MAX_PDF_DECOMPRESSED_BYTES - total)
            if expanded:
                streams.append(expanded)
                total += len(expanded)
            if total >= MAX_PDF_DECOMPRESSED_BYTES:
                break
        except zlib.error:
            continue
    text = ' '.join(_text_from_content(stream, max_bytes) for stream in streams)
    return text[:max_bytes].strip()
