"""Bounded raster-image inspection for the local dashboard preview.

The browser decodes the image. The backend verifies a supported file signature,
dimensions, and resource bounds; it does not infer visual subjects from pixels.
"""

import struct

IMAGE_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "gif", "webp", "bmp"})
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
MAX_IMAGE_SIDE = 32_768


def _jpeg_dimensions(data: bytes) -> tuple[int, int]:
    offset = 2
    frame_markers = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
    while offset < len(data):
        if data[offset] != 0xFF:
            break
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            break
        marker = data[offset]
        offset += 1
        if marker in {0xD9, 0xDA}:
            break
        if marker in {0x01, *range(0xD0, 0xD9)}:
            continue
        if offset + 2 > len(data):
            break
        length = int.from_bytes(data[offset:offset + 2], 'big')
        if length < 2 or offset + length > len(data):
            break
        if marker in frame_markers and length >= 8:
            height, width = struct.unpack_from('>HH', data, offset + 3)
            return width, height
        offset += length
    raise ValueError("JPEG dimensions could not be read; the image may be incomplete")


def inspect_image(data: bytes) -> dict:
    """Identify browser-supported raster content independently of its suffix."""
    if not data:
        raise ValueError("The image is empty")
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Image preview supports files up to 20 MiB")
    if (len(data) >= 33 and data.startswith(b'\x89PNG\r\n\x1a\n')
            and data[8:16] == b'\x00\x00\x00\rIHDR'):
        width, height = struct.unpack_from('>II', data, 16)
        format_name, content_type = "PNG", "image/png"
    elif data.startswith(b'\xff\xd8'):
        width, height = _jpeg_dimensions(data)
        format_name, content_type = "JPEG", "image/jpeg"
    elif len(data) >= 13 and data[:6] in (b'GIF87a', b'GIF89a'):
        width, height = struct.unpack_from('<HH', data, 6)
        format_name, content_type = "GIF", "image/gif"
    elif len(data) >= 25 and data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        if int.from_bytes(data[4:8], 'little') + 8 != len(data):
            raise ValueError("The WebP image is incomplete")
        chunk = data[12:16]
        if chunk == b'VP8X' and len(data) >= 30 and int.from_bytes(data[16:20], 'little') >= 10:
            width = 1 + int.from_bytes(data[24:27], 'little')
            height = 1 + int.from_bytes(data[27:30], 'little')
        elif chunk == b'VP8 ' and len(data) >= 30 and data[23:26] == b'\x9d\x01\x2a':
            width, height = struct.unpack_from('<HH', data, 26)
            width, height = width & 0x3FFF, height & 0x3FFF
        elif chunk == b'VP8L' and data[20] == 0x2F:
            packed = int.from_bytes(data[21:25], 'little')
            width, height = 1 + (packed & 0x3FFF), 1 + ((packed >> 14) & 0x3FFF)
        else:
            raise ValueError("The WebP image header is unsupported")
        format_name, content_type = "WebP", "image/webp"
    elif len(data) >= 26 and data[:2] == b'BM':
        dib_size = int.from_bytes(data[14:18], 'little')
        if dib_size == 12:
            width, height = struct.unpack_from('<HH', data, 18)
        elif dib_size >= 40 and len(data) >= 54:
            width, height = struct.unpack_from('<ii', data, 18)
            height = abs(height)
        else:
            raise ValueError("The BMP image header is unsupported")
        format_name, content_type = "BMP", "image/bmp"
    else:
        raise ValueError("This file is not a supported PNG, JPEG, GIF, WebP, or BMP image")

    if not (0 < width <= MAX_IMAGE_SIDE and 0 < height <= MAX_IMAGE_SIDE):
        raise ValueError("Image dimensions are invalid or too large for the preview")
    if width * height > MAX_IMAGE_PIXELS:
        raise ValueError("Image preview supports up to 40 megapixels")
    orientation = "square" if width == height else "landscape" if width > height else "portrait"
    return {"format": format_name, "content_type": content_type,
            "width": width, "height": height, "orientation": orientation,
            "size_bytes": len(data), "inspection": "signature-and-dimensions"}
