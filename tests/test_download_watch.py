import tempfile
import time
import unittest
from pathlib import Path

from kshetrajna.download_watch import DownloadWatcher
from kshetrajna.file_service import FileService
from kshetrajna.file_storage import FileStore


class DownloadWatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'Downloads'
        self.path.mkdir()
        self.store = FileStore(Path(self.temp.name) / 'files.db')
        self.service = FileService(self.store)
        self.root_id = self.service.add_root(str(self.path), 'Downloads', consent=True)['id']
        self.wait()
        self.service.set_folder_watch(self.root_id, True)
        self.watcher = DownloadWatcher(self.service, settle_seconds=4)
        self.watcher.poll(now=0)

    def wait(self):
        deadline = time.monotonic() + 10
        while self.service._active_scans:
            self.assertLess(time.monotonic(), deadline)
            time.sleep(.01)
        thread = self.service._scan_thread
        if thread and thread.is_alive():
            thread.join(timeout=5)

    def tearDown(self):
        self.watcher.stop()
        self.wait()
        self.temp.cleanup()

    def test_finished_download_is_indexed_and_checked_without_moving(self):
        (self.path / 'proposal.pdf').write_bytes(b'pdf fixture')
        self.watcher.poll(now=1)
        self.assertEqual(self.store.get_sortable_files(self.root_id), [])
        self.watcher.poll(now=5)
        self.wait()
        self.watcher.poll(now=7)
        self.assertEqual(len(self.store.get_sortable_files(self.root_id)), 1)
        self.assertEqual(self.store.get_sort_plans()[0]['status'], 'preview')
        self.assertTrue((self.path / 'proposal.pdf').exists())
        self.assertFalse((self.path / 'Documents').exists())

    def test_partial_and_changing_downloads_are_debounced(self):
        partial = self.path / 'image.jpg.crdownload'
        partial.write_bytes(b'partial')
        self.watcher.poll(now=1)
        self.watcher.poll(now=8)
        self.assertEqual(self.store.get_sortable_files(self.root_id), [])
        final = partial.with_suffix('')
        partial.rename(final)
        self.watcher.poll(now=9)
        final.write_bytes(b'changed and still downloading')
        self.watcher.poll(now=12)
        self.watcher.poll(now=13)
        self.assertEqual(self.store.get_sortable_files(self.root_id), [])
        self.watcher.poll(now=16)
        self.wait()
        self.assertEqual(len(self.store.get_sortable_files(self.root_id)), 1)

    def test_disabled_watch_and_demo_do_not_scan(self):
        self.service.set_folder_watch(self.root_id, False)
        (self.path / 'new.txt').write_text('fixture')
        self.watcher.poll(now=1)
        self.watcher.poll(now=8)
        self.assertEqual(self.store.get_sortable_files(self.root_id), [])
        self.assertEqual(self.watcher._snapshots, {})
        demo = DownloadWatcher(FileService(self.store, demo=True))
        demo.start()
        self.assertIsNone(demo._thread)

    def test_arrival_expires_old_approval_and_does_not_move_new_file(self):
        (self.path / 'first.txt').write_text('existing')
        self.service.rescan_root(self.root_id)
        self.wait()
        plan = self.service.generate_sort_plan(self.root_id, True)
        self.watcher.poll(now=1)
        self.watcher.poll(now=5)
        self.wait()
        with self.assertRaisesRegex(ValueError, 'expired'):
            self.service.apply_confident_sort(plan['id'], confirm=True)
        self.assertTrue((self.path / 'first.txt').exists())
