"""Atomic settings replacement retries must not publish unpersisted consent."""

import errno
import json
import os
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import call, patch

from kshetrajna.config import CONSENT_PERMISSIONS, ConfigStore, Settings


def windows_error(code):
    error = OSError(errno.EACCES, "Synthetic Windows replacement failure")
    error.winerror = code
    return error


class ConfigReplaceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.config = ConfigStore(self.directory)
        self.previous = Settings(enabled=True, device_consent=True, allow_process_control=True)
        self.versions = dict.fromkeys(CONSENT_PERMISSIONS, 3)
        self.config.path.write_text(json.dumps({**asdict(self.previous),
            "_consent_versions": self.versions}), encoding="utf-8")
        self.config.load()
        self.old_bytes = self.config.path.read_bytes()
        self.consents = self.config._consents.copy()
        self.update = {"device_consent": False, "allow_process_control": False,
                       "sample_interval_seconds": 9}
        # Isolate the platform simulation from pathlib and pin all retry sleeps.
        self.os = self.enterContext(patch("kshetrajna.config.os", wraps=os))
        self.os.name = "nt"
        self.sleep = self.enterContext(patch("kshetrajna.config.time", create=True)).sleep

    def assert_unchanged(self):
        self.assertEqual(self.config.path.read_bytes(), self.old_bytes)
        self.assertEqual(self.config._consents, self.consents)
        self.assertEqual(self.config._consent_versions, self.versions)
        self.assertEqual(self.config.load(), self.previous)
        self.assertEqual(ConfigStore(self.directory).consent_versions(*CONSENT_PERMISSIONS), self.versions)
        self.assertEqual(list(self.directory.glob("config-*.tmp")), [])

    def assert_failed_replace(self, error, attempts):
        self.os.reset_mock()
        self.sleep.reset_mock()
        with self.assertRaises(type(error)) as caught:
            self.config.update(self.update)
        self.assertIs(caught.exception, error)
        self.assertEqual(self.os.replace.call_count, attempts)
        replacement = self.os.replace.call_args
        self.assertEqual(self.os.replace.call_args_list, [replacement] * attempts)
        self.os.fsync.assert_called_once()
        self.os.unlink.assert_called_once_with(replacement.args[0])
        self.assertEqual(self.sleep.call_args_list, [call(0.05)] * (attempts - 1))
        self.assert_unchanged()

    def test_transient_conflicts_reuse_closed_fsynced_temp_and_publish_once(self):
        files = []
        attempts = []
        failures = [windows_error(code) for code in (5, 32, 33, 5)]
        expected = Settings.from_dict({**asdict(self.previous), **self.update})
        versions = {**self.versions, "device_consent": 4, "allow_process_control": 4}

        def open_temp(*args, **kwargs):
            file = os.fdopen(*args, **kwargs)
            files.append(file)
            return file

        def replace(source, target):
            self.assertEqual(len(files), 1)
            self.assertTrue(files[0].closed)
            self.os.fsync.assert_called_once()
            self.assertEqual(target, self.config.path)
            self.assertEqual(target.read_bytes(), self.old_bytes)
            self.assertEqual(self.config._consents, self.consents)
            self.assertEqual(self.config._consent_versions, self.versions)
            payload = Path(source).read_bytes()
            self.assertEqual(json.loads(payload), {**asdict(expected), "_consent_versions": versions})
            attempts.append((source, payload))
            if len(attempts) <= len(failures):
                raise failures[len(attempts) - 1]
            os.replace(source, target)
            self.assertEqual(self.config._consent_versions, self.versions)

        self.os.fdopen.side_effect = open_temp
        self.os.replace.side_effect = replace
        self.assertEqual(self.config.update(self.update), expected)
        self.assertEqual(attempts, [attempts[0]] * 5)
        self.assertEqual(self.sleep.call_args_list, [call(0.05)] * 4)
        self.os.unlink.assert_not_called()
        self.assertEqual(self.config._consents, {key: getattr(expected, key) for key in CONSENT_PERMISSIONS})
        self.assertEqual(self.config._consent_versions, versions)
        self.assertEqual(self.config.load(), expected)
        self.assertEqual(ConfigStore(self.directory).consent_versions(*CONSENT_PERMISSIONS), versions)
        self.assertEqual(list(self.directory.glob("config-*.tmp")), [])

    def test_persistent_conflicts_exhaust_bound_and_preserve_settings(self):
        for code in (5, 32, 33):
            with self.subTest(winerror=code):
                error = windows_error(code)
                self.os.replace.side_effect = error
                self.assert_failed_replace(error, 5)

    def test_non_windows_conflicts_are_not_retried(self):
        self.os.name = "posix"
        for code in (5, 32, 33):
            with self.subTest(winerror=code):
                error = windows_error(code)
                self.os.replace.side_effect = error
                self.assert_failed_replace(error, 1)

    def test_other_errors_are_not_retried(self):
        errors = (PermissionError(errno.EACCES, "No Windows error code"),
                  OSError(errno.EIO, "I/O failure"), windows_error(2), windows_error(112),
                  windows_error(1314), RuntimeError("Non-OS failure"))
        for error in errors:
            with self.subTest(error=error, winerror=getattr(error, "winerror", None)):
                self.os.replace.side_effect = error
                self.assert_failed_replace(error, 1)

    def test_permanent_error_after_transient_stops_immediately(self):
        error = windows_error(112)
        self.os.replace.side_effect = [windows_error(32), error]
        self.assert_failed_replace(error, 2)

    def test_fsync_failure_is_not_retried_and_temp_is_cleaned(self):
        error = windows_error(5)
        self.os.fsync.side_effect = error
        with self.assertRaises(OSError) as caught:
            self.config.update(self.update)
        self.assertIs(caught.exception, error)
        self.os.fsync.assert_called_once()
        self.os.replace.assert_not_called()
        self.os.unlink.assert_called_once()
        self.sleep.assert_not_called()
        self.assert_unchanged()

    def test_uncontended_replace_does_not_sleep(self):
        for platform in ("nt", "posix"):
            with self.subTest(platform=platform):
                self.os.name = platform
                self.os.reset_mock()
                self.assertEqual(self.config.update({}), self.previous)
                self.os.replace.assert_called_once()
                self.sleep.assert_not_called()
                self.os.unlink.assert_not_called()
                self.assertEqual(self.config._consents, self.consents)
                self.assertEqual(self.config._consent_versions, self.versions)
                self.assertEqual(list(self.directory.glob("config-*.tmp")), [])
