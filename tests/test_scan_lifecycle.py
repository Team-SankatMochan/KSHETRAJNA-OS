"""Event-controlled scan/root lifecycle regressions; every wait is bounded."""
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from kshetrajna.download_watch import DownloadWatcher
from kshetrajna.file_service import FileService
from kshetrajna.file_storage import FileStore


class ScanLifecycleTests(unittest.TestCase):
    WAIT = 3

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = FileStore(Path(self.temp.name) / 'files.db')
        self.service = FileService(self.store)
        self.releases = []
        self.threads = []

    def tearDown(self):
        # Release fixtures before stopping patches or removing the database,
        # including when an assertion fails against the original implementation.
        for event in self.releases:
            event.set()
        worker = self.service._scan_thread
        if worker is not None:
            self.threads.append(worker)
        for thread in self.threads:
            thread.join(timeout=self.WAIT)
            self.assertFalse(thread.is_alive(), 'Lifecycle thread did not terminate')

    def path(self, name):
        path = Path(self.temp.name) / name
        path.mkdir()
        return path

    def root(self, name='root'):
        path = self.path(name)
        return self.store.add_root(str(path), name), path

    def spawn(self, target):
        done, errors = threading.Event(), []

        def run():
            try:
                target()
            except Exception as error:
                errors.append(error)
            finally:
                done.set()

        thread = threading.Thread(target=run, daemon=True)
        self.threads.append(thread)
        thread.start()
        return done, errors

    def finished(self, job):
        done, errors = job
        self.assertTrue(done.wait(self.WAIT), 'Lifecycle operation did not finish')
        return errors

    def join_scan(self):
        worker = self.service._scan_thread
        if worker is not None:
            self.threads.append(worker)
            worker.join(timeout=self.WAIT)
            self.assertFalse(worker.is_alive(), 'Scan worker did not terminate')

    def block_flush(self):
        root_id, path = self.root()
        (path / 'note.txt').write_text('scan fixture', encoding='utf-8')
        entered, release, written = (threading.Event() for _ in range(3))
        self.releases.append(release)
        flush = self.service._flush_batch

        def paused_flush(batch):
            entered.set()
            if not release.wait(self.WAIT * 3):
                raise RuntimeError('Test did not release the scan write')
            flush(batch)
            written.set()

        self.enterContext(patch.object(self.service, '_flush_batch', side_effect=paused_flush))
        self.service.rescan_root(root_id)
        self.assertTrue(entered.wait(self.WAIT), 'Scan did not reach its catalog write')
        return root_id, path, release, written

    def start_delete(self, root_id):
        cancelled = threading.Event()
        cancel = self.service._cancel_events[root_id]
        set_cancel = cancel.set

        def notify_cancel():
            set_cancel()
            cancelled.set()

        self.enterContext(patch.object(cancel, 'set', side_effect=notify_cancel))
        job = self.spawn(lambda: self.service.delete_all(confirm=True))
        self.assertTrue(cancelled.wait(self.WAIT), 'Deletion did not request cancellation')
        return job

    def assert_gate_released(self, root_id):
        self.assertTrue(self.service._gate.acquire(timeout=self.WAIT), 'Lifecycle gate stayed locked')
        try:
            self.assertNotIn(root_id, self.service._active_scans)
            self.assertNotIn(root_id, self.service._cancel_events)
        finally:
            self.service._gate.release()

    def test_missing_root_releases_scan_ownership(self):
        root_id, _ = self.root()
        self.service._active_scans.add(root_id)
        self.service._cancel_events[root_id] = threading.Event()
        self.store.remove_root(root_id)

        self.assertEqual(self.finished(self.spawn(lambda: self.service._run_scan(root_id))), [])
        self.assert_gate_released(root_id)

    def test_removed_root_releases_scan_ownership_and_finishes_removal(self):
        root_id, path = self.root()
        self.service._active_scans.add(root_id)
        self.service._cancel_events[root_id] = threading.Event()
        self.store.update_root_status(root_id, 'removed')

        self.assertEqual(self.finished(self.spawn(lambda: self.service._run_scan(root_id))), [])
        self.assert_gate_released(root_id)
        self.assertIsNone(self.store.get_root(root_id))
        self.assertTrue(path.is_dir())

    def test_removed_root_cannot_be_rescanned(self):
        root_id, _ = self.root()
        self.store.update_root_status(root_id, 'removed')
        errors = self.finished(self.spawn(lambda: self.service.rescan_root(root_id)))
        self.join_scan()
        self.assertEqual(len(errors), 1, 'Rescan accepted a removed root')
        self.assertIsInstance(errors[0], ValueError)
        self.assertRegex(str(errors[0]), '[Rr]oot.*(removed|not found)')
        self.assertEqual(self.store.get_root(root_id)['status'], 'removed')
        self.assert_gate_released(root_id)

    def test_queued_root_removal_does_not_stall_scan_worker(self):
        root_id, _, release, _ = self.block_flush()
        queued_id, queued_path = self.root('queued')
        self.service.rescan_root(queued_id)
        self.service.remove_root(queued_id)
        release.set()
        self.join_scan()

        self.assert_gate_released(root_id)
        self.assert_gate_released(queued_id)
        self.assertEqual(self.store.get_root(root_id)['status'], 'ready')
        self.assertIsNone(self.store.get_root(queued_id))
        self.assertTrue(queued_path.is_dir())

    def test_scan_removal_waits_for_final_write_without_polling_threads(self):
        root_id, path, release, written = self.block_flush()
        removed, observations = threading.Event(), []
        remove = self.store.remove_root
        thread_type = threading.Thread

        def observe_remove(target_id):
            root = self.store.get_root(target_id)
            observations.append((written.is_set(), root['status'] if root else None,
                                 self.service._gate._is_owned()))
            remove(target_id)
            removed.set()

        def track_thread(*args, **kwargs):
            thread = thread_type(*args, **kwargs)
            self.threads.append(thread)
            return thread

        self.enterContext(patch.object(self.store, 'remove_root', side_effect=observe_remove))
        with patch('kshetrajna.file_service.threading.Thread', side_effect=track_thread) as threads:
            self.service.remove_root(root_id)
            self.service.remove_root(root_id)
        self.assertFalse(removed.is_set(), 'Root deleted while its scan could still write')
        self.assertEqual(self.store.get_root(root_id)['status'], 'removed')
        release.set()
        self.join_scan()
        self.assertTrue(removed.wait(self.WAIT), 'Pending root removal never completed')
        self.assertEqual(observations, [(True, 'removed', True)])
        self.assertEqual(threads.call_count, 0, 'Removal created polling threads')
        self.assert_gate_released(root_id)
        self.assertIsNone(self.store.get_root(root_id))
        self.assertEqual(self.store.get_stats()['total_files'], 0)
        self.assertEqual((path / 'note.txt').read_text(encoding='utf-8'), 'scan fixture')

    def assert_admission_blocked_during_delete(self, operation):
        root_id, path, release, written = self.block_flush()
        idle_id, _ = self.root('idle')
        new_path = self.path('new')
        before = {r['id'] for r in self.store.get_roots()}
        cleared, clear_observations = threading.Event(), []
        clear = self.store.clear_all

        def observe_clear():
            clear_observations.append((written.is_set(), bool(self.service._active_scans)))
            clear()
            cleared.set()

        self.enterContext(patch.object(self.store, 'clear_all', side_effect=observe_clear))
        deletion = self.start_delete(root_id)
        errors = self.finished(self.spawn(lambda: operation(idle_id, new_path)))
        self.assertEqual(len(errors), 1, 'Root operation was admitted during catalog deletion')
        self.assertIsInstance(errors[0], ValueError)
        self.assertRegex(str(errors[0]), 'delet')
        self.assertEqual({r['id'] for r in self.store.get_roots()}, before)
        self.assertFalse(cleared.is_set(), 'Catalog deleted before the scan write finished')
        release.set()
        self.assertEqual(self.finished(deletion), [])
        self.join_scan()
        self.assertEqual(clear_observations, [(True, False)])
        self.assertEqual(self.store.get_roots(), [])
        self.assertTrue((path / 'note.txt').exists())
        self.assert_gate_released(root_id)

    def test_add_root_rejected_while_catalog_deletion_waits(self):
        self.assert_admission_blocked_during_delete(
            lambda _, path: self.service.add_root(str(path), 'New', consent=True))

    def test_location_approval_rejected_while_catalog_deletion_waits(self):
        self.assert_admission_blocked_during_delete(
            lambda _, path: self.service.approve_locations(
                [{'path': str(path), 'label': 'New'}], consent=True))

    def test_rescan_rejected_while_catalog_deletion_waits(self):
        self.assert_admission_blocked_during_delete(
            lambda root_id, _: self.service.rescan_root(root_id))

    def test_second_catalog_deletion_rejected_without_waiting(self):
        root_id, _, release, _ = self.block_flush()
        deletion = self.start_delete(root_id)
        errors = self.finished(self.spawn(lambda: self.service.delete_all(confirm=True)))
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], ValueError)
        self.assertRegex(str(errors[0]), 'delet')
        release.set()
        self.assertEqual(self.finished(deletion), [])

    def test_delete_wait_releases_recursively_acquired_gate(self):
        root_id, _, release, _ = self.block_flush()
        delete_all = self.service.delete_all

        def nested_delete(*, confirm=False):
            with self.service._gate:
                delete_all(confirm=confirm)

        with patch.object(self.service, 'delete_all', side_effect=nested_delete):
            deletion = self.start_delete(root_id)
            release.set()
            self.assertEqual(self.finished(deletion), [])
        self.join_scan()
        self.assertEqual(self.store.get_roots(), [])
        self.assert_gate_released(root_id)

    def test_barrier_covers_the_drain_to_clear_boundary(self):
        root_id, _ = self.root()
        clear = self.store.clear_all
        admissions, active_at_clear = [], []

        def interleaved_clear():
            # Probe admission at the exact check-then-delete boundary. This is
            # reentrant so it does not depend on which waiter wins the RLock.
            try:
                self.service.rescan_root(root_id)
            except ValueError as error:
                admissions.append(error)
            active_at_clear.append(bool(self.service._active_scans))
            clear()

        with patch.object(self.service, '_scan_worker'), \
                patch.object(self.store, 'clear_all', side_effect=interleaved_clear):
            self.assertEqual(self.finished(self.spawn(lambda: self.service.delete_all(confirm=True))), [])
        self.assertEqual(len(admissions), 1, 'New scan entered between drain and clear_all')
        self.assertRegex(str(admissions[0]), 'delet')
        self.assertEqual(active_at_clear, [False])
        self.assert_gate_released(root_id)

    def test_clear_all_failure_releases_barrier(self):
        root_id, _ = self.root()
        failure = RuntimeError('clear_all failed')
        with patch.object(self.store, 'clear_all', side_effect=failure):
            self.assertEqual(self.finished(self.spawn(lambda: self.service.delete_all(confirm=True))), [failure])

        self.service.rescan_root(root_id)
        self.join_scan()
        added = self.service.add_root(str(self.path('added')), 'Added', consent=True)
        approved = self.service.approve_locations(
            [{'path': str(self.path('approved')), 'label': 'Approved'}], consent=True)
        self.join_scan()
        self.assertEqual(len(approved['approved']), 1)
        self.assertEqual(approved['skipped'], [])
        for target_id in (root_id, added['id'], approved['approved'][0]['id']):
            self.assertEqual(self.store.get_root(target_id)['status'], 'ready')
            self.assert_gate_released(target_id)
        self.service.delete_all(confirm=True)
        self.assertEqual(self.store.get_roots(), [])

    def test_uncooperative_scan_times_out_without_deleting_catalog(self):
        root_id, _, release, _ = self.block_flush()
        # A zero deadline exercises the timeout deterministically, without
        # depending on scheduler speed or waiting the production timeout.
        with patch.object(self.service, '_SCAN_STOP_TIMEOUT', 0, create=True), \
                patch.object(self.store, 'clear_all', wraps=self.store.clear_all) as clear:
            errors = self.finished(self.spawn(lambda: self.service.delete_all(confirm=True)))
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], ValueError)
        self.assertRegex(str(errors[0]), '[Tt]imed out')
        clear.assert_not_called()
        self.assertTrue(self.service._cancel_events[root_id].is_set())
        self.assertIsNotNone(self.store.get_root(root_id))
        release.set()
        self.join_scan()
        self.service.rescan_root(root_id)
        self.join_scan()
        self.assertEqual(self.store.get_root(root_id)['status'], 'ready')

    def test_watcher_skips_catalog_work_while_deletion_waits(self):
        root_id, _, release, _ = self.block_flush()
        watched_id, watched_path = self.root('watched')
        self.store.update_root_status(watched_id, 'ready')
        self.service.set_folder_watch(watched_id, True)
        (watched_path / 'arrival.txt').write_text('arrival', encoding='utf-8')
        watcher = DownloadWatcher(self.service)
        deletion = self.start_delete(root_id)

        with patch('kshetrajna.download_watch.os.scandir') as scandir, \
                patch.object(watcher, '_status') as status:
            self.assertEqual(self.finished(self.spawn(lambda: watcher.poll(now=10))), [])
        scandir.assert_not_called()
        status.assert_not_called()
        release.set()
        self.assertEqual(self.finished(deletion), [])


if __name__ == '__main__':
    unittest.main()
