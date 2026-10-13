import http.client
import json
import struct
import tempfile
import threading
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from kshetrajna.collector import Collector
from kshetrajna.config import ConfigStore
from kshetrajna.demo import DemoProbe
from kshetrajna.file_service import FileService
from kshetrajna.file_storage import FileStore
from kshetrajna.image_preview import inspect_image
from kshetrajna.pdf_preview import extract_pdf_excerpt, inspect_pdf
from kshetrajna.server import DashboardServer
from kshetrajna.storage import EventStore


def png_bytes(width=2, height=1):
    def chunk(kind, payload):
        return (struct.pack('>I', len(payload)) + kind + payload
                + struct.pack('>I', zlib.crc32(kind + payload) & 0xFFFFFFFF))
    scanlines = (b'\x00' + b'\x32\x96\xe0' * width) * height
    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(scanlines)) + chunk(b'IEND', b''))


def pdf_bytes(text='Hello from the PDF'):
    content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    return (b"%PDF-1.4\n"
            b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
            b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
            b"3 0 obj << /Type /Page /Parent 2 0 R /Contents 4 0 R >> endobj\n"
            + b"4 0 obj << /Length " + str(len(content)).encode() + b" >> stream\n"
            + content + b"\nendstream endobj\n%%EOF\n")


class ImageHeaderTests(unittest.TestCase):
    def test_formats_and_dimensions_are_detected_from_content(self):
        self.assertEqual(inspect_image(png_bytes())['content_type'], 'image/png')
        jpeg = b'\xff\xd8\xff\xc0' + struct.pack('>HBHHB', 8, 8, 480, 640, 0)
        gif = b'GIF89a' + struct.pack('<HHBBB', 320, 240, 0, 0, 0)
        webp_chunk = b'VP8X' + struct.pack('<I', 10) + b'\x00' * 4 + (799).to_bytes(3, 'little') + (599).to_bytes(3, 'little')
        webp = b'RIFF' + struct.pack('<I', len(webp_chunk) + 4) + b'WEBP' + webp_chunk
        for data, width, height, mime in ((jpeg, 640, 480, 'image/jpeg'),
                                          (gif, 320, 240, 'image/gif'),
                                          (webp, 800, 600, 'image/webp')):
            with self.subTest(mime=mime):
                details = inspect_image(data)
                self.assertEqual((details['width'], details['height']), (width, height))
                self.assertEqual(details['content_type'], mime)

    def test_rejects_active_content_truncation_and_excessive_dimensions(self):
        for data in (b'<svg xmlns="http://www.w3.org/2000/svg"/>', b'\xff\xd8', b''):
            with self.subTest(data=data), self.assertRaises(ValueError):
                inspect_image(data)
        enormous = bytearray(png_bytes())
        enormous[16:24] = struct.pack('>II', 32768, 32768)
        with self.assertRaisesRegex(ValueError, 'megapixels'):
            inspect_image(bytes(enormous))

    def test_pdf_signature_page_count_and_bounded_text(self):
        data = pdf_bytes()
        self.assertEqual(inspect_pdf(data)['pages'], 1)
        self.assertIn('Hello from the PDF', extract_pdf_excerpt(data))
        with self.assertRaises(ValueError):
            inspect_pdf(b'%NOT-A-PDF')


class ImagePreviewHttpTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.root = self.directory / 'approved'
        self.root.mkdir()
        self.store = FileStore(self.directory / 'files.db')
        self.files = FileService(self.store)
        self.root_id = self.store.add_root(str(self.root), 'Approved')
        self.store.update_root_status(self.root_id, 'ready')
        config = ConfigStore(self.directory)
        events = EventStore(self.directory / 'events.db')
        collector = Collector(DemoProbe(), config, events)
        self.server = DashboardServer(0, collector, config, events, demo=True)
        self.server.file_service = self.files
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=3)
        self.server.server_close()
        self.temporary.cleanup()

    def add_image(self, relative='guide-contact-sheet.png', data=None):
        data = png_bytes() if data is None else data
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        with self.store._connect() as db:
            return db.execute(
                "INSERT INTO files(root_id, relative_path, name, extension, size_bytes, indexed_at) "
                "VALUES(?,?,?,?,?,?)",
                (self.root_id, relative, target.name, target.suffix[1:], len(data), 'now'),
            ).lastrowid

    def add_pdf(self, relative='course-notes.pdf', data=None):
        data = pdf_bytes() if data is None else data
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        with self.store._connect() as db:
            return db.execute(
                "INSERT INTO files(root_id, relative_path, name, extension, size_bytes, indexed_at) "
                "VALUES(?,?,?,?,?,?)",
                (self.root_id, relative, target.name, 'pdf', len(data), 'now'),
            ).lastrowid

    def call(self, path, *, token=True, host=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        headers = {}
        if token:
            headers['X-Kshetrajna-Token'] = self.server.token
        if host:
            headers['Host'] = host
        try:
            connection.request('GET', path, headers=headers)
            response = connection.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            connection.close()

    def test_image_preview_returns_actual_bytes_and_details(self):
        file_id = self.add_image()
        status, body, headers = self.call(f'/api/files/file/{file_id}/image')
        self.assertEqual(status, 200)
        self.assertEqual(body, png_bytes())
        self.assertEqual(headers['Content-Type'], 'image/png')
        self.assertEqual(headers['X-Image-Width'], '2')
        self.assertEqual(headers['X-Image-Height'], '1')
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(headers['Cross-Origin-Resource-Policy'], 'same-origin')
        status, body, _ = self.call(f'/api/files/file/{file_id}/excerpt')
        self.assertEqual(status, 200)
        details = json.loads(body)
        self.assertEqual(details['file']['image_preview_url'], f'/api/files/file/{file_id}/image')
        self.assertEqual(details['excerpt'], '')
        self.assertEqual(details['file']['insights']['purpose'], 'Contact sheet / reference image')
        _, _, headers = self.call('/')
        self.assertIn("img-src 'self' blob:", headers['Content-Security-Policy'])

    def test_image_read_requires_session_and_approved_id(self):
        file_id = self.add_image()
        self.assertEqual(self.call(f'/api/files/file/{file_id}/image', token=False)[0], 403)
        self.assertEqual(self.call(f'/api/files/file/{file_id}/image', host='evil.example')[0], 403)
        self.assertEqual(self.call('/api/files/file/999/image')[0], 400)
        self.assertEqual(self.call('/api/files/file/not-an-id/image')[0], 400)

    def test_sensitive_spoofed_and_oversized_images_are_rejected(self):
        sensitive = self.add_image('.aws/snapshot.png')
        status, body, _ = self.call(f'/api/files/file/{sensitive}/image')
        self.assertEqual(status, 400)
        status, body, _ = self.call(f'/api/files/file/{sensitive}/excerpt')
        self.assertNotIn('image_preview_url', json.loads(body)['file'])
        spoofed = self.add_image('fake.png', b'<html><script>alert(1)</script></html>')
        self.assertEqual(self.call(f'/api/files/file/{spoofed}/image')[0], 400)
        normal = self.add_image('normal.png')
        with patch('kshetrajna.file_service.MAX_IMAGE_BYTES', 10):
            self.assertEqual(self.call(f'/api/files/file/{normal}/image')[0], 400)

    def test_replaced_symlink_file_and_root_are_rejected(self):
        file_id = self.add_image()
        outside = self.directory / 'outside'
        outside.mkdir()
        (outside / 'guide-contact-sheet.png').write_bytes(png_bytes())
        source = self.root / 'guide-contact-sheet.png'
        source.unlink()
        try:
            source.symlink_to(outside / 'guide-contact-sheet.png')
        except OSError:
            self.skipTest('symlinks unavailable')
        self.assertEqual(self.call(f'/api/files/file/{file_id}/image')[0], 400)
        source.unlink()
        source.write_bytes(png_bytes())
        self.root.rename(self.directory / 'original-root')
        self.root.symlink_to(outside, target_is_directory=True)
        self.assertEqual(self.call(f'/api/files/file/{file_id}/image')[0], 400)

    def test_pdf_preview_returns_inline_document_and_excerpt(self):
        file_id = self.add_pdf()
        status, body, headers = self.call(f'/api/files/file/{file_id}/pdf')
        self.assertEqual(status, 200)
        self.assertEqual(body, pdf_bytes())
        self.assertEqual(headers['Content-Type'], 'application/pdf')
        self.assertEqual(headers['Content-Disposition'], 'inline')
        self.assertEqual(headers['X-PDF-Pages'], '1')
        status, body, _ = self.call(f'/api/files/file/{file_id}/excerpt')
        details = json.loads(body)
        self.assertIn('Hello from the PDF', details['excerpt'])
        self.assertEqual(details['file']['pdf_preview_url'], f'/api/files/file/{file_id}/pdf')

    def test_pdf_preview_requires_session_and_rejects_spoofed_content(self):
        file_id = self.add_pdf('fake.pdf', b'<html>not a PDF</html>')
        self.assertEqual(self.call(f'/api/files/file/{file_id}/pdf')[0], 400)
        normal = self.add_pdf()
        self.assertEqual(self.call(f'/api/files/file/{normal}/pdf', token=False)[0], 403)

    def test_demo_never_reads_an_image_from_disk(self):
        file_id = self.add_image()
        self.files.demo = True
        with patch('kshetrajna.file_service.os.open', side_effect=AssertionError('unexpected disk read')):
            status, body, _ = self.call(f'/api/files/file/{file_id}/image')
        self.assertEqual(status, 400)


if __name__ == '__main__':
    unittest.main()
