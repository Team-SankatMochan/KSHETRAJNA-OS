"""Synthetic shutdown coverage; no Windows handles or device sampling."""

import threading
import unittest
from threading import Thread
from unittest.mock import Mock, patch

from kshetrajna.collector import Collector
from kshetrajna.config import Settings
from kshetrajna.models import Snapshot


WAIT = 2


class ControlledWorker:
    def __init__(self, *, alive=False, exit_on_join=False):
        self.alive = alive
        self.exit_on_join = exit_on_join
        self.join_timeouts = []
        self.events = []

    def start(self):
        self.alive = True

    def join(self, timeout=None):
        self.events.append("join")
        self.join_timeouts.append(timeout)
        if self.exit_on_join:
            self.alive = False

    def is_alive(self):
        self.events.append("is_alive")
        return self.alive


class ControlledProbe:
    def __init__(self):
        self.close_calls = 0
        self.sample_calls = 0
        self.sampling = False
        self.closed_during_sample = False
        self.sample_entered = threading.Event()
        self.sample_release = None
        self.on_sample = None
        self.on_close = None
        self.snapshot = Snapshot(
            observed_at="2026-01-01T00:00:00+00:00",
            cpu_percent=0.0,
            memory_total_bytes=1024,
            memory_available_bytes=512,
            foreground_pid=None,
            foreground_app=None,
            idle_seconds=None,
            processes=[],
        )

    def sample(self, **kwargs):
        self.sample_calls += 1
        self.sampling = True
        self.sample_entered.set()
        try:
            if self.on_sample:
                self.on_sample()
            if self.sample_release and not self.sample_release.wait(WAIT):
                raise TimeoutError("Test did not release the synthetic sample")
            return self.snapshot
        finally:
            self.sampling = False

    def close(self):
        self.close_calls += 1
        self.closed_during_sample |= self.sampling
        if self.on_close:
            self.on_close()


class CollectorShutdownTests(unittest.TestCase):
    def setUp(self):
        self.probe = ControlledProbe()
        self.config = Mock()
        self.config.load.return_value = Settings(enabled=True, device_consent=True)
        self.store = Mock()
        self.collector = Collector(self.probe, self.config, self.store)

    def run_thread(self, operation):
        errors = []
        done = threading.Event()

        def run():
            try:
                operation()
            except BaseException as error:
                errors.append(error)
            finally:
                done.set()

        thread = Thread(target=run, daemon=True)
        thread.start()
        return thread, done, errors

    def assert_finished(self, thread, done, errors):
        self.assertTrue(done.wait(WAIT), "Shutdown or collection did not finish")
        thread.join(timeout=WAIT)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])

    def test_stop_without_worker_closes_once_and_sets_shutdown_events(self):
        self.collector.stop()
        self.collector.stop()

        self.assertTrue(self.collector._stop.is_set())
        self.assertTrue(self.collector._wake.is_set())
        self.assertIsNone(self.collector._thread)
        self.assertEqual(self.probe.close_calls, 1)

    def test_stop_closes_only_after_worker_exit_is_confirmed(self):
        for initially_alive in (False, True):
            with self.subTest(initially_alive=initially_alive):
                probe = ControlledProbe()
                collector = Collector(probe, self.config, self.store)
                worker = ControlledWorker(alive=initially_alive, exit_on_join=True)
                collector._thread = worker
                probe.on_close = lambda: worker.events.append("close")

                collector.stop()

                self.assertEqual(worker.join_timeouts, [10])
                self.assertEqual(probe.close_calls, 1)
                self.assertLess(worker.events.index("join"), worker.events.index("is_alive"))
                self.assertLess(worker.events.index("is_alive"), worker.events.index("close"))

    def test_join_timeout_does_not_close_live_worker_and_later_stop_retries(self):
        worker = ControlledWorker(alive=True)
        self.collector._thread = worker

        self.collector.stop()

        self.assertEqual(worker.join_timeouts, [10])
        self.assertEqual(self.probe.close_calls, 0)
        self.assertTrue(self.collector._stop.is_set())
        self.assertTrue(self.collector._wake.is_set())
        worker.alive = False

        self.collector.stop()
        self.collector.stop()

        self.assertEqual(self.probe.close_calls, 1)
        self.assertEqual(worker.join_timeouts, [10, 10, 10])

    def test_legacy_probe_without_close_is_supported(self):
        for worker in (None, ControlledWorker()):
            with self.subTest(worker=worker):
                collector = Collector(object(), self.config, self.store)
                collector._thread = worker

                collector.stop()
                collector.stop()

                self.assertTrue(collector._stop.is_set())

    def test_external_sample_defers_close_without_blocking_shutdown(self):
        for worker in (None, ControlledWorker()):
            with self.subTest(worker=worker):
                probe = ControlledProbe()
                probe.sample_release = threading.Event()
                collector = Collector(probe, self.config, self.store)
                collector._thread = worker
                sample_thread, sample_done, sample_errors = self.run_thread(collector.collect_once)
                stop_thread = None
                try:
                    self.assertTrue(probe.sample_entered.wait(WAIT))
                    stop_thread, stop_done, stop_errors = self.run_thread(collector.stop)
                    self.assert_finished(stop_thread, stop_done, stop_errors)
                    self.assertEqual(probe.close_calls, 0)
                    self.assertFalse(probe.closed_during_sample)
                finally:
                    probe.sample_release.set()
                    sample_thread.join(timeout=WAIT)
                    if stop_thread:
                        stop_thread.join(timeout=WAIT)

                self.assert_finished(sample_thread, sample_done, sample_errors)
                self.store.record.assert_called_with(probe.snapshot, [])
                collector.stop()
                self.assertEqual(probe.close_calls, 1)
                self.assertFalse(probe.closed_during_sample)

    def test_reentrant_stop_from_external_sample_does_not_close_under_sample(self):
        self.probe.on_sample = self.collector.stop

        result = self.collector.collect_once()

        self.assertIs(result, self.probe.snapshot)
        self.assertEqual(self.probe.close_calls, 0)
        self.assertFalse(self.probe.closed_during_sample)
        self.collector.stop()
        self.assertEqual(self.probe.close_calls, 1)

    def test_stop_from_worker_does_not_join_itself_or_close(self):
        self.collector._thread = threading.current_thread()

        self.collector.stop()

        self.assertTrue(self.collector._stop.is_set())
        self.assertEqual(self.probe.close_calls, 0)

    def test_concurrent_repeated_stop_does_not_close_twice(self):
        close_entered = threading.Event()
        close_release = threading.Event()

        def block_close():
            close_entered.set()
            if not close_release.wait(WAIT):
                raise TimeoutError("Test did not release synthetic close")

        self.probe.on_close = block_close
        first_thread, first_done, first_errors = self.run_thread(self.collector.stop)
        second_thread = None
        try:
            self.assertTrue(close_entered.wait(WAIT))
            second_thread, second_done, second_errors = self.run_thread(self.collector.stop)
            self.assert_finished(second_thread, second_done, second_errors)
            self.assertEqual(self.probe.close_calls, 1)
        finally:
            close_release.set()
            first_thread.join(timeout=WAIT)
            if second_thread:
                second_thread.join(timeout=WAIT)

        self.assert_finished(first_thread, first_done, first_errors)
        self.collector.stop()
        self.assertEqual(self.probe.close_calls, 1)

    def test_explicit_sample_after_stop_can_be_closed_by_later_stop(self):
        self.collector.stop()

        self.assertIs(self.collector.collect_once(), self.probe.snapshot)
        self.collector.stop()
        self.collector.stop()

        self.assertEqual(self.probe.sample_calls, 1)
        self.assertEqual(self.probe.close_calls, 2)

    def test_failed_sample_still_allows_later_cleanup(self):
        def fail_sample():
            raise RuntimeError("Synthetic sampling failure")

        self.probe.on_sample = fail_sample
        with self.assertRaisesRegex(RuntimeError, "Synthetic sampling failure"):
            self.collector.collect_once()

        self.collector.stop()
        self.collector.stop()

        self.assertEqual(self.probe.close_calls, 1)
        self.assertFalse(self.probe.closed_during_sample)
        self.store.record.assert_not_called()

    def test_stop_during_start_neither_joins_unstarted_worker_nor_closes_probe(self):
        worker = ControlledWorker()
        start_entered = threading.Event()
        start_release = threading.Event()

        def block_start():
            start_entered.set()
            if not start_release.wait(WAIT):
                raise TimeoutError("Test did not release synthetic worker startup")
            worker.alive = True

        worker.start = block_start
        with patch("kshetrajna.collector.threading.Thread", return_value=worker):
            start_thread, start_done, start_errors = self.run_thread(self.collector.start)
            try:
                self.assertTrue(start_entered.wait(WAIT))
                self.collector.stop()
                self.assertEqual(worker.join_timeouts, [])
                self.assertEqual(self.probe.close_calls, 0)
            finally:
                start_release.set()
                start_thread.join(timeout=WAIT)

        self.assert_finished(start_thread, start_done, start_errors)
        self.collector.stop()
        self.assertEqual(self.probe.close_calls, 0)
        worker.alive = False
        self.collector.stop()
        self.assertEqual(self.probe.close_calls, 1)

    def test_start_stop_preserves_one_shot_worker_semantics(self):
        worker = ControlledWorker(exit_on_join=True)
        with patch("kshetrajna.collector.threading.Thread", return_value=worker) as thread_factory:
            self.collector.start()
            self.collector.start()
            self.collector.stop()
            self.collector.start()

        thread_factory.assert_called_once()
        self.assertIs(self.collector._thread, worker)
        self.assertFalse(worker.alive)
        self.assertTrue(self.collector._stop.is_set())
        self.assertEqual(self.probe.close_calls, 1)


if __name__ == "__main__":
    unittest.main()
