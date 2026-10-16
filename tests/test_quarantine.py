"""Contract and security tests for reversible cleanup quarantine records."""

import http.client
import errno
import json
import os
import sqlite3
import stat
import threading
import time
import tempfile
import unittest
from contextlib import closing, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from kshetrajna.collector import Collector
from kshetrajna.config import ConfigStore
from kshetrajna.demo import DemoProbe
from kshetrajna.file_service import FileService
from kshetrajna.file_storage import FileStore, QUARANTINE_DIR_NAME
from kshetrajna.server import DashboardServer
from kshetrajna.storage import EventStore


class QuarantineFixture(unittest.TestCase):
    """Create an isolated approved folder and file catalog for every case."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db_path = Path(self.temp.name) / "files.db"
        self.store = FileStore(self.db_path)
        self.service = FileService(self.store)
        self.root_path = Path(self.temp.name) / "root"
        self.root_path.mkdir()
        self.server = None
        self.thread = None

    def _wait_scan(self, root_id, service=None):
        service = service or self.service
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            root = service.store.get_root(root_id)
            if root and root["status"] in ("ready", "error", "cancelled", "truncated"):
                self.assertIn(root["status"], ("ready", "truncated"), root.get("error"))
                return root
            time.sleep(0.01)
        self.fail(f"scan did not finish for root {root_id}")

    def _add_root(self, files, label="Root"):
        for relative, content in files.items():
            path = self.root_path / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                path.write_bytes(content)
            else:
                path.write_text(content, encoding="utf-8")
        root_id = self.service.add_root(str(self.root_path), label, consent=True)["id"]
        self._wait_scan(root_id)
        return root_id

    def _quarantine_path(self, action_id):
        return self.root_path / QUARANTINE_DIR_NAME / action_id

    def _set_fixture_times(self, path, ns):
        # These are trusted, test-owned regular files, not production targets.
        path = Path(path)
        info = path.stat(follow_symlinks=False)
        self.assertTrue(path.resolve().is_relative_to(self.root_path.resolve()))
        self.assertTrue(stat.S_ISREG(info.st_mode))
        self.assertFalse(getattr(info, "st_file_attributes", 0) & 0x400)
        os.utime(path, ns=ns)

    def _catalog_row(self, root_id, relative_path):
        with self.store._connect() as db:
            row = db.execute(
                "SELECT * FROM files WHERE root_id=? AND relative_path=?",
                (root_id, relative_path),
            ).fetchone()
        return dict(row) if row else None

    def _quarantine_empty(self, name="empty.txt"):
        root_id = self._add_root({name: b""})
        file_id = self.service.get_empty_files(root_id)["empty_files"][0]["id"]
        result = self.service.delete_empty_file(file_id, confirm=True)
        self.assertTrue(result["deleted"])
        self.assertTrue(result["quarantined"])
        return root_id, file_id, result["action_id"]

    def _quarantine_duplicate(self):
        payload = b"original payload"
        root_id = self._add_root({"original.bin": payload, "peer.bin": payload})
        file_id = self._catalog_row(root_id, "original.bin")["id"]
        result = self.service.delete_duplicate_file(file_id, confirm=True)
        return root_id, file_id, result["action_id"]

    @contextmanager
    def _wide_file_identities(self):
        real_stat, real_fstat = os.stat, os.fstat

        def wide(info):
            values = {key: getattr(info, key) for key in dir(info) if key.startswith("st_")}
            values["st_dev"] += 2**80
            values["st_ino"] += 2**96
            return SimpleNamespace(**values)

        with patch.object(os, "stat", side_effect=lambda *args, **kwargs: wide(real_stat(*args, **kwargs))), \
                patch.object(os, "fstat", side_effect=lambda descriptor: wide(real_fstat(descriptor))):
            yield

    def _interrupt_quarantine_before_move(self):
        payload = b"original payload"
        root_id = self._add_root({"original.bin": payload, "peer.bin": payload})
        file_id = self._catalog_row(root_id, "original.bin")["id"]
        update_action = self.store.update_file_action

        def interrupt_after_prepare(action_id, updates):
            update_action(action_id, updates)
            if updates.get("status") == "prepared":
                raise RuntimeError("interrupted after durable prepare")

        with patch.object(self.store, "update_file_action", side_effect=interrupt_after_prepare):
            with self.assertRaisesRegex(RuntimeError, "after durable prepare"):
                self.service.delete_duplicate_file(file_id, confirm=True)
        action = self.store.get_file_actions()[0]
        self.assertEqual(action["status"], "prepared")
        self.assertEqual(action["quarantine_state"], "prepared")
        self.assertTrue(action["expected_sha256"])
        self.assertFalse(self._quarantine_path(action["id"]).exists())
        self.assertEqual((self.root_path / "original.bin").read_bytes(), payload)
        return root_id, file_id, action["id"]

    def _start_server(self):
        config = ConfigStore(Path(self.temp.name) / "config")
        events = EventStore(Path(self.temp.name) / "events.db")
        collector = Collector(DemoProbe(), config, events)
        self.server = DashboardServer(0, collector, config, events)
        self.server.file_service = self.service
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop_server)

    def _stop_server(self):
        if self.server is None:
            return
        self.server.shutdown()
        self.thread.join(timeout=3)
        self.server.server_close()
        self.server = None

    def _request(self, method, path, body=None, *, origin=True, token=True):
        headers = {"Host": f"127.0.0.1:{self.server.server_port}"}
        if token is True:
            headers["X-Kshetrajna-Token"] = self.server.token
        elif token is not False:
            headers["X-Kshetrajna-Token"] = token
        payload = None
        if method == "POST":
            if origin is True:
                headers["Origin"] = f"http://127.0.0.1:{self.server.server_port}"
            elif origin is not False:
                headers["Origin"] = origin
            headers["Content-Type"] = "application/json"
            payload = json.dumps(body)
        with closing(http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)) as connection:
            connection.request(method, path, payload, headers)
            response = connection.getresponse()
            raw = response.read()
        return response.status, json.loads(raw)

    def test_empty_file_quarantine_restore_round_trip_refreshes_catalog(self):
        root_id, file_id, action_id = self._quarantine_empty()
        original = self.root_path / "empty.txt"
        quarantine = self._quarantine_path(action_id)

        self.assertFalse(original.exists())
        self.assertTrue(quarantine.exists())
        self.assertEqual(quarantine.stat().st_size, 0)
        self.assertIsNone(self.store.get_file(file_id))
        self.assertEqual(self.store.get_root(root_id)["file_count"], 0)

        items = self.service.get_quarantine_items(root_id)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["action_id"], action_id)
        self.assertEqual(item["original_relative_path"], "empty.txt")
        self.assertEqual(item["size_bytes"], 0)
        self.assertEqual(item["quarantine_state"], "quarantined")
        self.assertNotIn("root_path", item)
        self.assertNotIn("path", item)
        self.assertNotIn("excerpt", item)
        self.assertNotIn("content", item)

        restored = self.service.restore_quarantined_file(action_id, confirm=True)
        self.assertEqual(restored["status"], "restored")
        self.assertTrue(original.exists())
        self.assertEqual(original.read_bytes(), b"")
        self.assertEqual(self.store.get_file(file_id)["relative_path"], "empty.txt")
        self.assertEqual(self.store.get_root(root_id)["file_count"], 1)
        self.assertEqual(self.service.get_quarantine_items(root_id)[0]["quarantine_state"], "restored")
        self.assertFalse(quarantine.exists())
        self.assertEqual(
            [candidate["id"] for candidate in self.service.get_empty_files(root_id)["empty_files"]],
            [file_id],
        )

    def test_duplicate_quarantine_keeps_a_verified_peer(self):
        root_id = self._add_root({"first.bin": b"same payload", "second.bin": b"same payload"})
        group = self.service.get_duplicate_groups(root_id)["groups"][0]
        victim, survivor = group["files"]

        result = self.service.delete_duplicate_file(victim["id"], confirm=True)
        self.assertTrue(result["deleted"])
        self.assertTrue(result["quarantined"])
        self.assertFalse((self.root_path / victim["relative_path"]).exists())
        self.assertEqual((self.root_path / survivor["relative_path"]).read_bytes(), b"same payload")
        self.assertIsNone(self.store.get_file(victim["id"]))
        self.assertIsNotNone(self.store.get_file(survivor["id"]))
        self.assertEqual(self.service.get_duplicate_groups(root_id)["total_groups"], 0)

        action = self.store.get_file_action(action_id=result["action_id"])
        self.assertEqual(json.loads(action["expected_peer_ids"]), [survivor["id"]])
        self.assertEqual(self.service.get_quarantine_items(root_id)[0]["action_id"], result["action_id"])

    def test_duplicate_quarantine_rechecks_peer_content_before_mutating(self):
        root_id = self._add_root({"first.bin": b"same-size", "second.bin": b"same-size"})
        group = self.service.get_duplicate_groups(root_id)["groups"][0]
        victim, peer = group["files"]
        peer_path = self.root_path / peer["relative_path"]
        peer_path.write_bytes(b"different")

        with self.assertRaisesRegex(ValueError, "No duplicate"):
            self.service.delete_duplicate_file(victim["id"], confirm=True)
        self.assertTrue((self.root_path / victim["relative_path"]).exists())
        self.assertEqual(peer_path.read_bytes(), b"different")
        self.assertEqual(self.service.get_quarantine_items(root_id), [])

    def test_restore_requires_literal_true_confirmation(self):
        root_id, file_id, action_id = self._quarantine_empty()
        original = self.root_path / "empty.txt"
        quarantine = self._quarantine_path(action_id)

        for confirmation in (False, None, 1, "true"):
            with self.subTest(confirm=confirmation):
                with self.assertRaisesRegex(ValueError, "confirm=true"):
                    self.service.restore_quarantined_file(action_id, confirm=confirmation)
                self.assertFalse(original.exists())
                self.assertTrue(quarantine.exists())
                self.assertIsNone(self.store.get_file(file_id))

        self.assertEqual(self.service.restore_quarantined_file(action_id, confirm=True)["status"], "restored")

    def test_quarantine_list_requires_dashboard_token(self):
        root_id, _, action_id = self._quarantine_empty()
        self._start_server()

        status, _ = self._request("GET", f"/api/files/quarantine?root_id={root_id}", token=False)
        self.assertEqual(status, 403)
        status, _ = self._request("GET", "/api/files/quarantine", token="wrong-token")
        self.assertEqual(status, 403)
        status, body = self._request(
            "GET", f"/api/files/quarantine?root_id={root_id}", token=True
        )
        self.assertEqual(status, 200)
        self.assertEqual([item["action_id"] for item in body["items"]], [action_id])

    def test_http_restore_requires_origin_token_and_valid_action_id(self):
        _, _, action_id = self._quarantine_empty()
        self._start_server()
        path = "/api/files/quarantine/restore"

        status, _ = self._request(
            "POST", path, {"action_id": action_id, "confirm": True}, origin=False, token=True
        )
        self.assertEqual(status, 403)
        status, _ = self._request(
            "POST", path, {"action_id": action_id, "confirm": True}, origin=True, token="wrong-token"
        )
        self.assertEqual(status, 403)

        for malformed in ("", "../escape", "a/b", 7, True, "é", "x" * 201):
            with self.subTest(action_id=malformed):
                status, body = self._request(
                    "POST", path, {"action_id": malformed, "confirm": True}
                )
                self.assertEqual(status, 400)
                self.assertIn("action_id", body["error"])

        status, body = self._request("POST", path, {"action_id": action_id, "confirm": True})
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "restored")

    def test_restore_collision_does_not_overwrite_original_path(self):
        _, _, action_id = self._quarantine_empty()
        original = self.root_path / "empty.txt"
        quarantine = self._quarantine_path(action_id)
        original.write_bytes(b"user-created content")

        with self.assertRaisesRegex(ValueError, "occupied"):
            self.service.restore_quarantined_file(action_id, confirm=True)
        self.assertEqual(original.read_bytes(), b"user-created content")
        self.assertTrue(quarantine.exists())
        self.assertEqual(self.store.get_file_action(action_id=action_id)["quarantine_state"], "quarantined")

    def test_quarantine_fallback_does_not_overwrite_racing_destination(self):
        source = self.root_path / "source.txt"
        destination = self.root_path / "destination.txt"
        source.write_bytes(b"source")
        expected = source.stat(follow_symlinks=False)

        def unsupported_link(*args, **kwargs):
            destination.write_bytes(b"racing destination")
            raise OSError(errno.EOPNOTSUPP, "hard links unavailable")

        with patch.object(os, "link", side_effect=unsupported_link):
            with self.assertRaisesRegex(ValueError, "Destination exists"):
                self.service.action_manager._move_without_overwrite(
                    self.root_path, source.name, source, destination, expected
                )
        self.assertEqual(source.read_bytes(), b"source")
        self.assertEqual(destination.read_bytes(), b"racing destination")

    def test_quarantine_fallback_copies_then_unlinks_verified_source(self):
        source = self.root_path / "source.txt"
        destination = self.root_path / "destination.txt"
        source.write_bytes(b"source")
        expected = source.stat(follow_symlinks=False)

        with patch.object(os, "link", side_effect=OSError(errno.EXDEV, "different filesystem")):
            moved = self.service.action_manager._move_without_overwrite(
                self.root_path, source.name, source, destination, expected
            )
        self.assertFalse(source.exists())
        self.assertEqual(destination.read_bytes(), b"source")
        actual = destination.stat(follow_symlinks=False)
        self.assertEqual((moved.st_dev, moved.st_ino, moved.st_size, moved.st_mtime_ns),
                         (actual.st_dev, actual.st_ino, actual.st_size, actual.st_mtime_ns))
        self.assertNotEqual((moved.st_dev, moved.st_ino), (expected.st_dev, expected.st_ino))

    def test_copy_fallback_does_not_require_path_based_timestamp_support(self):
        source = self.root_path / "source.bin"
        destination = self.root_path / "destination.bin"
        source.write_bytes(b"portable verified copy")
        self._set_fixture_times(source, (1_600_000_000_000_000_000, 1_600_000_000_000_000_000))
        expected = source.stat(follow_symlinks=False)
        real_utime = os.utime

        def descriptor_only_utime(path, *args, **kwargs):
            if not isinstance(path, int):
                raise NotImplementedError("path-based timestamps unavailable")
            return real_utime(path, *args, **kwargs)

        with patch.object(os, "link", side_effect=OSError(errno.EXDEV, "different filesystem")), \
                patch.object(os, "utime", side_effect=descriptor_only_utime):
            moved = self.service.action_manager._move_without_overwrite(
                self.root_path, source.name, source, destination, expected)

        self.assertFalse(source.exists())
        self.assertEqual(destination.read_bytes(), b"portable verified copy")
        self.assertEqual(destination.stat().st_mtime_ns, expected.st_mtime_ns)
        self.assertEqual((moved.st_dev, moved.st_ino),
                         (destination.stat().st_dev, destination.stat().st_ino))

    def test_copy_fallback_quarantine_restore_round_trip_preserves_bytes_and_catalog(self):
        payload = b"verified copy payload\x00" * 4000
        root_id = self._add_root({"original.bin": payload, "peer.bin": payload})
        original = self.root_path / "original.bin"
        before = original.stat(follow_symlinks=False)
        file_id = self._catalog_row(root_id, original.name)["id"]

        with patch.object(os, "link", side_effect=OSError(errno.EXDEV, "different filesystem")):
            result = self.service.delete_duplicate_file(file_id, confirm=True)
            action_id = result["action_id"]
            quarantine = self._quarantine_path(action_id)
            self.assertTrue(result["quarantined"])
            self.assertFalse(original.exists())
            self.assertEqual(quarantine.read_bytes(), payload)
            self.assertIsNone(self.store.get_file(file_id))
            self.assertEqual(self.store.get_root(root_id)["file_count"], 1)
            self.assertEqual(self.store.get_root(root_id)["total_bytes"], len(payload))
            restored = self.service.restore_quarantined_file(action_id, confirm=True)

        self.assertEqual(restored["status"], "restored")
        self.assertEqual(original.read_bytes(), payload)
        self.assertEqual(original.stat().st_mtime_ns, before.st_mtime_ns)
        self.assertFalse(quarantine.exists())
        row = self.store.get_file(file_id)
        self.assertEqual((row["root_id"], row["relative_path"], row["size_bytes"]),
                         (root_id, original.name, len(payload)))
        self.assertEqual(self.store.get_root(root_id)["file_count"], 2)
        self.assertEqual(self.store.get_root(root_id)["total_bytes"], 2 * len(payload))
        action = self.store.get_file_action(action_id=action_id)
        self.assertEqual((action["status"], action["quarantine_state"]), ("restored", "restored"))
        self.assertIsNone(action["error"])
        self.assertFalse(self.store.has_unresolved_file_actions(root_id))

    def test_copy_fallback_restore_verifies_destination_content_before_source_unlink(self):
        payload = b"original payload"
        root_id = self._add_root({"original.bin": payload, "peer.bin": payload})
        file_id = self._catalog_row(root_id, "original.bin")["id"]
        action_id = self.service.delete_duplicate_file(file_id, confirm=True)["action_id"]
        quarantine = self._quarantine_path(action_id)
        original = self.root_path / "original.bin"
        original_hash = self.service.action_manager._hash_regular_file
        info = quarantine.stat(follow_symlinks=False)

        def tamper_before_hash(path, *args, **kwargs):
            if Path(path) == original:
                original.write_bytes(b"modified payload")
                self._set_fixture_times(original, (info.st_atime_ns, info.st_mtime_ns))
            return original_hash(path, *args, **kwargs)

        with patch.object(os, "link", side_effect=OSError(errno.EOPNOTSUPP, "no hard links")), \
                patch.object(self.service.action_manager, "_hash_regular_file", side_effect=tamper_before_hash):
            with self.assertRaisesRegex(ValueError, "content.*changed"):
                self.service.restore_quarantined_file(action_id, confirm=True)

        self.assertTrue(quarantine.exists(), "verified quarantine source must survive a corrupt copy")
        self.assertEqual(quarantine.read_bytes(), payload)
        self.assertFalse(original.exists())
        self.assertIsNone(self.store.get_file(file_id))
        self.assertEqual(self.store.get_root(root_id)["file_count"], 1)
        self.assertEqual(self.store.get_root(root_id)["total_bytes"], len(payload))
        action = self.store.get_file_action(action_id=action_id)
        self.assertEqual((action["status"], action["quarantine_state"]),
                         ("restore_failed", "restore_failed"))
        self.assertTrue(self.store.has_unresolved_file_actions(root_id))

    def test_copy_fallback_restore_preserves_source_when_destination_is_replaced(self):
        root_id, file_id, action_id = self._quarantine_empty()
        quarantine = self._quarantine_path(action_id)
        original = self.root_path / "empty.txt"
        replacement = self.root_path / "replacement.txt"
        replacement.write_bytes(b"")
        original_hash = self.service.action_manager._hash_regular_file
        info = quarantine.stat(follow_symlinks=False)
        replacement_identity = replacement.stat(follow_symlinks=False)

        def replace_before_hash(path, *args, **kwargs):
            if Path(path) == original:
                os.replace(replacement, original)
                self._set_fixture_times(original, (info.st_atime_ns, info.st_mtime_ns))
            return original_hash(path, *args, **kwargs)

        with patch.object(os, "link", side_effect=OSError(errno.EXDEV, "different filesystem")), \
                patch.object(self.service.action_manager, "_hash_regular_file", side_effect=replace_before_hash):
            with self.assertRaisesRegex(ValueError, "identity changed"):
                self.service.restore_quarantined_file(action_id, confirm=True)

        self.assertTrue(quarantine.exists())
        self.assertEqual(quarantine.read_bytes(), b"")
        self.assertTrue(original.exists(), "a racing replacement must not be removed as our failed copy")
        self.assertEqual((original.stat().st_dev, original.stat().st_ino),
                         (replacement_identity.st_dev, replacement_identity.st_ino))
        self.assertIsNone(self.store.get_file(file_id))
        self.assertEqual(self.store.get_root(root_id)["file_count"], 0)
        self.assertEqual(self.store.get_file_action(action_id=action_id)["quarantine_state"], "restore_failed")

    def test_copy_fallback_restore_does_not_overwrite_racing_original(self):
        root_id, file_id, action_id = self._quarantine_empty()
        quarantine = self._quarantine_path(action_id)
        original = self.root_path / "empty.txt"

        def unsupported_link(*args, **kwargs):
            original.write_bytes(b"racing original")
            raise OSError(errno.EOPNOTSUPP, "no hard links")

        with patch.object(os, "link", side_effect=unsupported_link):
            with self.assertRaisesRegex(ValueError, "Destination exists"):
                self.service.restore_quarantined_file(action_id, confirm=True)

        self.assertEqual(original.read_bytes(), b"racing original")
        self.assertEqual(quarantine.read_bytes(), b"")
        self.assertIsNone(self.store.get_file(file_id))
        self.assertEqual(self.store.get_root(root_id)["file_count"], 0)
        self.assertTrue(self.store.has_unresolved_file_actions(root_id))

    def test_quarantine_fallback_rejects_mtime_failure_before_source_unlink(self):
        original = self.root_path / "fallback.txt"
        original.write_bytes(b"")
        self._set_fixture_times(original, (1_600_000_000_000_000_000, 1_600_000_000_000_000_000))
        root_id = self._add_root({})
        file_id = self.service.get_empty_files(root_id)["empty_files"][0]["id"]

        with patch.object(os, "link", side_effect=OSError(errno.EXDEV, "different filesystem")), \
                patch.object(self.service.action_manager, "_set_copy_timestamps"):
            with self.assertRaisesRegex(ValueError, "Quarantine identity changed"):
                self.service.delete_empty_file(file_id, confirm=True)

        self.assertTrue(original.exists())
        item = self.service.get_quarantine_items(root_id)[0]
        self.assertEqual(item["quarantine_state"], "dismissed")
        self.assertFalse(self._quarantine_path(item["action_id"]).exists())

    def test_copy_timestamp_failure_retains_source_catalog_and_totals(self):
        root_id = self._add_root({"fallback.txt": b""})
        file_id = self.service.get_empty_files(root_id)["empty_files"][0]["id"]
        before = self.store.get_file(file_id)
        with patch.object(os, "link", side_effect=OSError(errno.EXDEV, "different filesystem")), \
                patch.object(self.service.action_manager, "_set_copy_timestamps",
                             side_effect=OSError(errno.EACCES, "timestamp refused")):
            with self.assertRaisesRegex(ValueError, "Operation unavailable"):
                self.service.delete_empty_file(file_id, confirm=True)

        self.assertEqual((self.root_path / "fallback.txt").read_bytes(), b"")
        self.assertEqual(self.store.get_file(file_id), before)
        self.assertEqual(self.store.get_root(root_id)["file_count"], 1)
        self.assertEqual(self.store.get_root(root_id)["total_bytes"], 0)
        item = self.service.get_quarantine_items(root_id)[0]
        self.assertEqual(item["quarantine_state"], "dismissed")
        self.assertFalse(self._quarantine_path(item["action_id"]).exists())

    def test_quarantine_conflict_resolves_verified_original_without_data_loss(self):
        root_id, file_id, action_id = self._quarantine_empty()
        original = self.root_path / "empty.txt"
        quarantine = self._quarantine_path(action_id)
        os.link(quarantine, original)
        action = self.store.get_file_action(action_id=action_id)
        info = original.stat(follow_symlinks=False)
        with self.store._connect() as db:
            db.execute(
                "INSERT INTO files (id, root_id, relative_path, name, extension, size_bytes, modified_at, "
                "created_at, is_directory, mime_guess, excerpt, scan_pass, indexed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, '', '', 1, ?)",
                 (file_id, root_id, "empty.txt", "empty.txt", "txt", info.st_size,
                 action["original_modified_at"], action["created_at"], action["created_at"]),
            )
        self.store.update_file_action(
            action_id, {"status": "quarantine_conflict", "quarantine_state": "quarantine_conflict"}
        )

        result = self.service.restore_quarantined_file(action_id, confirm=True)

        self.assertEqual(result["status"], "restored")
        self.assertTrue(original.exists())
        self.assertFalse(quarantine.exists())
        self.assertIsNotNone(self.store.get_file(file_id))
        self.assertEqual(self.store.get_root(root_id)["file_count"], 1)

    def test_quarantine_conflict_tamper_is_rejected_before_duplicate_unlink(self):
        root_id = self._add_root({"original.txt": b"ORIGINAL!", "peer.txt": b"ORIGINAL!"})
        group = self.service.get_duplicate_groups(root_id)["groups"][0]
        victim = next(member for member in group["files"] if member["name"] == "original.txt")
        result = self.service.delete_duplicate_file(victim["id"], confirm=True)
        file_id = victim["id"]
        action_id = result["action_id"]
        original = self.root_path / "original.txt"
        quarantine = self._quarantine_path(action_id)
        original.write_bytes(b"ORIGINAL!")
        action = self.store.get_file_action(action_id=action_id)
        info = original.stat(follow_symlinks=False)
        self.store.update_file_action(action_id, {
            "expected_size": info.st_size,
            "expected_mtime_ns": info.st_mtime_ns,
            "expected_dev": info.st_dev,
            "expected_ino": info.st_ino,
            "status": "quarantine_conflict",
            "quarantine_state": "quarantine_conflict",
        })
        with self.store._connect() as db:
            db.execute(
                "INSERT INTO files (id, root_id, relative_path, name, extension, size_bytes, modified_at, "
                "created_at, is_directory, mime_guess, excerpt, scan_pass, indexed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, '', '', 1, ?)",
                 (file_id, root_id, "original.txt", "original.txt", "txt", info.st_size,
                 action["original_modified_at"], action["created_at"], action["created_at"]),
            )
            inserted = db.execute("SELECT id FROM files WHERE root_id=? AND relative_path=?", (root_id, "original.txt")).fetchone()
        self.assertEqual(inserted["id"], action["file_id"], (inserted, action))
        original_unlink = self.service.action_manager._unlink_verified
        tamper_called = []

        def tamper_then_unlink(*args, **kwargs):
            tamper_called.append(True)
            quarantine_stat = quarantine.stat(follow_symlinks=False)
            quarantine.write_bytes(b"CHANGED!!")
            self._set_fixture_times(quarantine, (quarantine_stat.st_atime_ns, quarantine_stat.st_mtime_ns))
            return original_unlink(*args, **kwargs)

        with patch.object(self.service.action_manager, "_unlink_verified", side_effect=tamper_then_unlink):
            with self.assertRaises(ValueError) as raised:
                self.service.restore_quarantined_file(action_id, confirm=True)
        self.assertTrue(original.exists())
        self.assertTrue(tamper_called, raised.exception)
        self.assertEqual(quarantine.read_bytes(), b"CHANGED!!")
        self.assertEqual(self.store.get_file_action(action_id=action_id)["quarantine_state"], "quarantine_conflict")

    def test_restore_tamper_does_not_move_changed_quarantine_content(self):
        root_id = self._add_root({"tampered.txt": b"original", "peer.txt": b"original"})
        group = self.service.get_duplicate_groups(root_id)["groups"][0]
        victim = next(member for member in group["files"] if member["name"] == "tampered.txt")
        result = self.service.delete_duplicate_file(victim["id"], confirm=True)
        quarantine = self._quarantine_path(result["action_id"])
        quarantine.write_bytes(b"changed!")
        original = self.root_path / "tampered.txt"

        with self.assertRaisesRegex(ValueError, "changed"):
            self.service.restore_quarantined_file(result["action_id"], confirm=True)
        self.assertFalse(original.exists())
        self.assertEqual(quarantine.read_bytes(), b"changed!")
        self.assertIsNone(self.store.get_file(victim["id"]))

    def test_restore_missing_quarantine_does_not_create_original(self):
        _, file_id, action_id = self._quarantine_empty()
        quarantine = self._quarantine_path(action_id)
        original = self.root_path / "empty.txt"
        quarantine.unlink()

        with self.assertRaisesRegex(ValueError, "missing"):
            self.service.restore_quarantined_file(action_id, confirm=True)
        self.assertFalse(original.exists())
        self.assertIsNone(self.store.get_file(file_id))
        self.assertEqual(self.store.get_file_action(action_id=action_id)["quarantine_state"], "quarantined")

    def test_quarantine_contents_are_not_indexed_or_cleanup_candidates(self):
        root_id, _, action_id = self._quarantine_empty()
        quarantine = self._quarantine_path(action_id)
        self.service.rescan_root(root_id)
        self._wait_scan(root_id)

        with self.store._connect() as db:
            indexed = db.execute(
                "SELECT relative_path FROM files WHERE root_id=? AND lower(relative_path) LIKE ?",
                (root_id, f"{QUARANTINE_DIR_NAME.lower()}/%"),
            ).fetchall()
        self.assertEqual(indexed, [])
        self.assertTrue(quarantine.exists())
        self.assertEqual(self.service.get_empty_files(root_id)["empty_files"], [])
        self.assertEqual(self.service.get_duplicate_groups(root_id)["groups"], [])
        self.assertEqual(self.store.search_files(action_id, root_id), [])

    def test_quarantine_does_not_consume_scan_limit(self):
        root_id, _, action_id = self._quarantine_empty()
        quarantine = self._quarantine_path(action_id)
        for index in range(8):
            quarantine.parent.joinpath(f"extra-{index}.bin").write_bytes(b"recovery")
        (self.root_path / "ordinary-a.txt").write_text("a", encoding="utf-8")
        (self.root_path / "ordinary-b.txt").write_text("b", encoding="utf-8")
        with self.store._connect() as db:
            db.execute("UPDATE roots SET scan_limit=2 WHERE id=?", (root_id,))
        self.service.rescan_root(root_id)
        self._wait_scan(root_id)

        self.assertEqual(self.store.get_root(root_id)["status"], "ready")
        with self.store._connect() as db:
            paths = {row[0] for row in db.execute(
                "SELECT relative_path FROM files WHERE root_id=?", (root_id,)
            )}
        self.assertTrue({"ordinary-a.txt", "ordinary-b.txt"}.issubset(paths))
        self.assertFalse(any(path.casefold().startswith(QUARANTINE_DIR_NAME) for path in paths))

    def test_root_and_catalog_deletion_are_blocked_until_quarantine_resolves(self):
        root_id, _, action_id = self._quarantine_empty()

        with self.assertRaisesRegex(ValueError, "Resolve file action"):
            self.service.remove_root(root_id)
        self.assertEqual(self.store.get_root(root_id)["status"], "ready")
        with self.assertRaisesRegex(ValueError, "Resolve file action"):
            self.service.delete_all(confirm=True)
        self.assertIsNotNone(self.store.get_root(root_id))
        self.assertTrue(self._quarantine_path(action_id).exists())

        self.assertEqual(self.service.restore_quarantined_file(action_id, confirm=True)["status"], "restored")
        self.service.remove_root(root_id)
        self.assertIsNone(self.store.get_root(root_id))

    def test_restart_reconciles_interrupted_quarantine_then_restores(self):
        root_id, file_id, action_id = self._quarantine_empty()
        self.store.update_file_action(
            action_id, {"status": "prepared", "quarantine_state": "prepared"}
        )

        restarted_store = FileStore(self.db_path)
        restarted = FileService(restarted_store)
        action = restarted_store.get_file_action(action_id=action_id)
        self.assertEqual(action["status"], "quarantined")
        self.assertEqual(action["quarantine_state"], "quarantined")
        self.assertIsNone(restarted_store.get_file(file_id))
        refreshed_root = restarted_store.get_root(root_id)
        self.assertEqual(refreshed_root["file_count"], 0)
        self.assertEqual(refreshed_root["total_bytes"], 0)
        self.assertEqual(restarted.get_quarantine_items(root_id)[0]["action_id"], action_id)
        self.assertEqual(restarted.restore_quarantined_file(action_id, confirm=True)["status"], "restored")
        self.assertTrue((self.root_path / "empty.txt").exists())
        self.assertIsNotNone(restarted_store.get_file(file_id))

    def test_restart_dismisses_verified_prepared_noop_and_unblocks_root_removal(self):
        root_id, file_id, action_id = self._interrupt_quarantine_before_move()
        before_row = self.store.get_file(file_id)
        before_root = self.store.get_root(root_id)
        restarted_store = FileStore(self.db_path)
        restarted = FileService(restarted_store)

        action = restarted_store.get_file_action(action_id=action_id)
        self.assertEqual((action["status"], action["quarantine_state"]), ("dismissed", "dismissed"))
        self.assertEqual((self.root_path / "original.bin").read_bytes(), b"original payload")
        self.assertEqual((self.root_path / "peer.bin").read_bytes(), b"original payload")
        self.assertFalse(self._quarantine_path(action_id).exists())
        self.assertEqual(restarted_store.get_file(file_id), before_row)
        self.assertEqual(restarted_store.get_root(root_id)["file_count"], before_root["file_count"])
        self.assertEqual(restarted_store.get_root(root_id)["total_bytes"], before_root["total_bytes"])
        self.assertFalse(restarted_store.has_unresolved_file_actions())
        restarted.action_manager.reconcile_actions()
        self.assertEqual(restarted_store.get_file_action(action_id=action_id)["status"], "dismissed")
        restarted.remove_root(root_id)
        self.assertIsNone(restarted_store.get_root(root_id))
        self.assertEqual((self.root_path / "original.bin").read_bytes(), b"original payload")

    def test_restart_prepared_noop_unblocks_catalog_deletion_without_deleting_bytes(self):
        _, _, action_id = self._interrupt_quarantine_before_move()
        restarted_store = FileStore(self.db_path)
        restarted = FileService(restarted_store)

        restarted.delete_all(confirm=True)

        self.assertEqual(restarted_store.get_roots(), [])
        self.assertIsNone(restarted_store.get_file_action(action_id=action_id))
        self.assertEqual((self.root_path / "original.bin").read_bytes(), b"original payload")
        self.assertEqual((self.root_path / "peer.bin").read_bytes(), b"original payload")

    def test_restart_prepared_changed_original_remains_blocked(self):
        root_id, file_id, action_id = self._interrupt_quarantine_before_move()
        original = self.root_path / "original.bin"
        before = original.stat(follow_symlinks=False)
        before_row = self.store.get_file(file_id)
        original.write_bytes(b"modified payload")
        self._set_fixture_times(original, (before.st_atime_ns, before.st_mtime_ns))
        restarted_store = FileStore(self.db_path)
        restarted = FileService(restarted_store)

        action = restarted_store.get_file_action(action_id=action_id)
        self.assertEqual((action["status"], action["quarantine_state"]),
                         ("quarantine_conflict", "quarantine_conflict"))
        self.assertTrue(restarted_store.has_unresolved_file_actions(root_id))
        with self.assertRaisesRegex(ValueError, "Resolve file action"):
            restarted.remove_root(root_id)
        with self.assertRaisesRegex(ValueError, "Resolve file action"):
            restarted.delete_all(confirm=True)
        with self.assertRaises(ValueError):
            restarted.restore_quarantined_file(action_id, confirm=True)
        self.assertEqual(original.read_bytes(), b"modified payload")
        self.assertFalse(self._quarantine_path(action_id).exists())
        self.assertEqual(restarted_store.get_file(file_id), before_row)
        self.assertEqual(restarted_store.get_root(root_id)["file_count"], 2)
        self.assertEqual(restarted_store.get_root(root_id)["total_bytes"], 2 * len(b"original payload"))

    def test_restart_prepared_without_digest_fails_closed(self):
        root_id, file_id, action_id = self._interrupt_quarantine_before_move()
        self.store.update_file_action(action_id, {"expected_sha256": None})
        restarted_store = FileStore(self.db_path)
        restarted = FileService(restarted_store)

        action = restarted_store.get_file_action(action_id=action_id)
        self.assertEqual((action["status"], action["quarantine_state"]),
                         ("quarantine_conflict", "quarantine_conflict"))
        self.assertTrue(restarted_store.has_unresolved_file_actions(root_id))
        with self.assertRaisesRegex(ValueError, "Resolve file action"):
            restarted.remove_root(root_id)
        with self.assertRaisesRegex(ValueError, "Resolve file action"):
            restarted.delete_all(confirm=True)
        self.assertEqual((self.root_path / "original.bin").read_bytes(), b"original payload")
        self.assertIsNotNone(restarted_store.get_file(file_id))

    def test_restart_prepared_replaced_original_with_same_bytes_remains_blocked(self):
        root_id, file_id, action_id = self._interrupt_quarantine_before_move()
        original = self.root_path / "original.bin"
        before = original.stat(follow_symlinks=False)
        replacement = self.root_path / "replacement.bin"
        replacement.write_bytes(b"original payload")
        self._set_fixture_times(replacement, (before.st_atime_ns, before.st_mtime_ns))
        os.replace(replacement, original)
        self.assertNotEqual(original.stat().st_ino, before.st_ino)
        restarted_store = FileStore(self.db_path)
        restarted = FileService(restarted_store)

        action = restarted_store.get_file_action(action_id=action_id)
        self.assertEqual(action["quarantine_state"], "quarantine_conflict")
        self.assertTrue(restarted_store.has_unresolved_file_actions(root_id))
        with self.assertRaisesRegex(ValueError, "Resolve file action"):
            restarted.remove_root(root_id)
        with self.assertRaisesRegex(ValueError, "Resolve file action"):
            restarted.delete_all(confirm=True)
        self.assertEqual(original.read_bytes(), b"original payload")
        self.assertIsNotNone(restarted_store.get_file(file_id))

    def test_restart_prepared_requires_complete_identity_and_catalog_binding(self):
        root_id, file_id, action_id = self._interrupt_quarantine_before_move()
        original_action = self.store.get_file_action(action_id=action_id)
        peer_id = self._catalog_row(root_id, "peer.bin")["id"]
        for changed in ({"expected_size": None}, {"expected_mtime_ns": None},
                        {"expected_dev": None}, {"expected_ino": None}, {"file_id": peer_id}):
            with self.subTest(changed=changed):
                updates = {key: original_action[key] for key in (
                    "expected_size", "expected_mtime_ns", "expected_dev", "expected_ino", "file_id")}
                updates.update(status="prepared", quarantine_state="prepared")
                updates.update(changed)
                self.store.update_file_action(action_id, updates)
                restarted_store = FileStore(self.db_path)
                restarted = FileService(restarted_store)

                self.assertEqual(restarted_store.get_file_action(action_id=action_id)["quarantine_state"],
                                 "quarantine_conflict")
                with self.assertRaisesRegex(ValueError, "Resolve file action"):
                    restarted.remove_root(root_id)
                with self.assertRaisesRegex(ValueError, "Resolve file action"):
                    restarted.delete_all(confirm=True)
                self.assertEqual((self.root_path / "original.bin").read_bytes(), b"original payload")
                self.assertIsNotNone(restarted_store.get_file(file_id))

    def test_restart_marks_interrupted_restore_retryable(self):
        root_id, _, action_id = self._quarantine_empty()
        self.store.update_file_action(
            action_id, {"status": "restore_prepared", "quarantine_state": "restore_prepared"}
        )

        restarted_store = FileStore(self.db_path)
        restarted = FileService(restarted_store)
        action = restarted_store.get_file_action(action_id=action_id)
        self.assertEqual(action["status"], "restore_failed")
        self.assertEqual(action["quarantine_state"], "restore_failed")
        self.assertEqual(restarted.restore_quarantined_file(action_id, confirm=True)["status"], "restored")

    def test_quarantine_persists_wide_file_identities_losslessly(self):
        root_id = self._add_root({"empty.txt": b""})
        file_id = self._catalog_row(root_id, "empty.txt")["id"]

        with self._wide_file_identities():
            action_id = self.service.delete_empty_file(file_id, confirm=True)["action_id"]
            quarantine = self._quarantine_path(action_id)
            info = quarantine.stat(follow_symlinks=False)
            action = self.store.get_file_action(action_id=action_id)
            self.assertEqual((action["quarantine_dev"], action["quarantine_ino"]),
                             (info.st_dev, info.st_ino))
            with self.store._connect() as db:
                encoded = db.execute(
                    "SELECT quarantine_dev, quarantine_ino FROM file_actions WHERE id=?", (action_id,)
                ).fetchone()
            self.assertEqual(tuple(encoded), (f"bigint:{info.st_dev}", f"bigint:{info.st_ino}"))
            self.assertIsNone(self.store.get_file(file_id))
            self.assertEqual(self.store.get_root(root_id)["file_count"], 0)
            self.assertEqual(self.service.restore_quarantined_file(action_id, confirm=True)["status"], "restored")

        self.assertEqual((self.root_path / "empty.txt").read_bytes(), b"")
        self.assertIsNotNone(self.store.get_file(file_id))
        self.assertEqual(self.store.get_root(root_id)["file_count"], 1)

    def test_restart_quarantine_persists_wide_identities_in_both_reconcile_branches(self):
        root_id, file_id, action_id = self._quarantine_empty()
        info = self._quarantine_path(action_id).stat(follow_symlinks=False)
        expected_dev, expected_ino = info.st_dev + 2**80, info.st_ino + 2**96
        self.store.update_file_action(action_id, {
            "quarantine_dev": expected_dev, "quarantine_ino": expected_ino,
        })

        with self._wide_file_identities():
            for state in ("prepared", "proposed"):
                with self.subTest(state=state):
                    self.store.update_file_action(action_id, {"status": state, "quarantine_state": state})
                    restarted_store = FileStore(self.db_path)
                    FileService(restarted_store)
                    action = restarted_store.get_file_action(action_id=action_id)
                    self.assertEqual((action["status"], action["quarantine_state"]),
                                     ("quarantined", "quarantined"))
                    self.assertEqual((action["quarantine_dev"], action["quarantine_ino"]),
                                     (expected_dev, expected_ino))
                    with restarted_store._connect() as db:
                        encoded = db.execute(
                            "SELECT quarantine_dev, quarantine_ino FROM file_actions WHERE id=?", (action_id,)
                        ).fetchone()
                    self.assertEqual(tuple(encoded), (f"bigint:{expected_dev}", f"bigint:{expected_ino}"))
                    self.assertIsNone(restarted_store.get_file(file_id))
                    self.assertEqual(restarted_store.get_root(root_id)["file_count"], 0)
                    self.assertEqual(restarted_store.get_root(root_id)["total_bytes"], 0)

    def test_restore_allocates_fresh_catalog_id_when_scan_reuses_original_id(self):
        root_id, original_id, action_id = self._quarantine_empty()
        (self.root_path / "unrelated.txt").write_bytes(b"unrelated content")
        self.service.rescan_root(root_id)
        self._wait_scan(root_id)
        unrelated = self._catalog_row(root_id, "unrelated.txt")
        self.assertEqual(unrelated["id"], original_id, "fixture must reproduce SQLite primary-key reuse")

        result = self.service.restore_quarantined_file(action_id, confirm=True)

        self.assertEqual(result["status"], "restored")
        restored = self._catalog_row(root_id, "empty.txt")
        self.assertNotEqual(restored["id"], original_id)
        self.assertEqual(self.store.get_file_action(action_id=action_id)["file_id"], restored["id"])
        self.assertEqual(self._catalog_row(root_id, "unrelated.txt"), unrelated)
        self.assertEqual((self.root_path / "empty.txt").read_bytes(), b"")
        self.assertEqual((self.root_path / "unrelated.txt").read_bytes(), b"unrelated content")
        self.assertFalse(self._quarantine_path(action_id).exists())
        self.assertEqual(self.store.get_root(root_id)["file_count"], 2)
        self.assertEqual(self.store.get_root(root_id)["total_bytes"], len(b"unrelated content"))
        restarted_store = FileStore(self.db_path)
        FileService(restarted_store)
        self.assertEqual(restarted_store.get_file_action(action_id=action_id)["file_id"], restored["id"])
        self.assertEqual(restarted_store.get_file(original_id)["relative_path"], "unrelated.txt")

    def test_restore_catalog_insert_and_action_id_rebind_roll_back_together(self):
        root_id, original_id, action_id = self._quarantine_empty()
        (self.root_path / "unrelated.txt").write_bytes(b"unrelated content")
        self.service.rescan_root(root_id)
        self._wait_scan(root_id)
        unrelated = self._catalog_row(root_id, "unrelated.txt")
        self.assertEqual(unrelated["id"], original_id)
        original = self.root_path / "empty.txt"
        original.write_bytes(b"")
        action = self.store.get_file_action(action_id=action_id)
        with self.store._connect() as db:
            db.execute(
                "CREATE TRIGGER reject_rebind BEFORE UPDATE OF file_id ON file_actions "
                "WHEN NEW.file_id != OLD.file_id BEGIN SELECT RAISE(ABORT, 'reject rebind'); END"
            )

        with self.assertRaisesRegex(sqlite3.IntegrityError, "reject rebind"):
            self.service.action_manager._restore_catalog_location(
                action, root_id, original, original.stat(follow_symlinks=False))

        self.assertIsNone(self._catalog_row(root_id, "empty.txt"))
        self.assertEqual(self.store.get_file_action(action_id=action_id)["file_id"], original_id)
        self.assertEqual(self._catalog_row(root_id, "unrelated.txt"), unrelated)
        self.assertEqual(original.read_bytes(), b"")
        self.assertEqual(self._quarantine_path(action_id).read_bytes(), b"")

    def test_restore_still_blocks_an_indexed_original_path_even_when_id_was_reused(self):
        root_id, original_id, action_id = self._quarantine_empty()
        original = self.root_path / "empty.txt"
        original.write_bytes(b"unrelated indexed file")
        self.service.rescan_root(root_id)
        self._wait_scan(root_id)
        collision = self._catalog_row(root_id, "empty.txt")
        self.assertEqual(collision["id"], original_id)
        original.unlink()

        with self.assertRaisesRegex(ValueError, "already indexed"):
            self.service.restore_quarantined_file(action_id, confirm=True)

        self.assertFalse(original.exists())
        self.assertEqual(self._catalog_row(root_id, "empty.txt"), collision)
        self.assertEqual(self._quarantine_path(action_id).read_bytes(), b"")
        self.assertEqual(self.store.get_file_action(action_id=action_id)["quarantine_state"], "quarantined")

    def test_restart_completes_interrupted_restore_with_both_hardlinks_present(self):
        root_id, file_id, action_id = self._quarantine_duplicate()
        original = self.root_path / "original.bin"
        quarantine = self._quarantine_path(action_id)
        self.store.update_file_action(action_id, {"status": "restore_prepared", "quarantine_state": "restore_prepared"})
        os.link(quarantine, original)
        self.assertIsNone(self.store.get_file(file_id))

        restarted_store = FileStore(self.db_path)
        restarted = FileService(restarted_store)

        action = restarted_store.get_file_action(action_id=action_id)
        self.assertEqual((action["status"], action["quarantine_state"]), ("restored", "restored"))
        self.assertEqual(original.read_bytes(), b"original payload")
        self.assertFalse(quarantine.exists())
        self.assertEqual(restarted_store.get_file(action["file_id"])["relative_path"], "original.bin")
        self.assertEqual(restarted_store.get_root(root_id)["file_count"], 2)
        self.assertEqual(restarted_store.get_root(root_id)["total_bytes"], 2 * len(b"original payload"))
        self.assertFalse(restarted_store.has_unresolved_file_actions(root_id))
        restarted.action_manager.reconcile_actions()
        self.assertEqual(restarted_store.get_file_action(action_id=action_id)["status"], "restored")

    def test_restart_interrupted_restore_does_not_accept_unrelated_same_content_original(self):
        root_id, file_id, action_id = self._quarantine_duplicate()
        original = self.root_path / "original.bin"
        quarantine = self._quarantine_path(action_id)
        info = quarantine.stat(follow_symlinks=False)
        original.write_bytes(b"original payload")
        self._set_fixture_times(original, (info.st_atime_ns, info.st_mtime_ns))
        self.store.update_file_action(action_id, {"status": "restore_prepared", "quarantine_state": "restore_prepared"})

        restarted_store = FileStore(self.db_path)
        restarted = FileService(restarted_store)

        self.assertEqual(restarted_store.get_file_action(action_id=action_id)["quarantine_state"], "restore_failed")
        with self.assertRaises(ValueError):
            restarted.restore_quarantined_file(action_id, confirm=True)
        self.assertEqual(original.read_bytes(), b"original payload")
        self.assertEqual(quarantine.read_bytes(), b"original payload")
        self.assertIsNone(restarted_store.get_file(file_id))
        self.assertEqual(restarted_store.get_root(root_id)["file_count"], 1)
        self.assertTrue(restarted_store.has_unresolved_file_actions(root_id))

    def test_restart_linked_restore_retries_unlink_and_preserves_reused_id_row(self):
        root_id, original_id, action_id = self._quarantine_empty()
        (self.root_path / "unrelated.txt").write_bytes(b"unrelated content")
        self.service.rescan_root(root_id)
        self._wait_scan(root_id)
        unrelated = self._catalog_row(root_id, "unrelated.txt")
        self.assertEqual(unrelated["id"], original_id)
        original = self.root_path / "empty.txt"
        quarantine = self._quarantine_path(action_id)
        self.store.update_file_action(action_id, {"status": "restore_prepared", "quarantine_state": "restore_prepared"})
        os.link(quarantine, original)
        restarted_store = FileStore(self.db_path)
        with patch("kshetrajna.file_actions.FileActionManager._unlink_verified",
                   side_effect=PermissionError(errno.EACCES, "transient recovery unlink failure")):
            restarted = FileService(restarted_store)

        action = restarted_store.get_file_action(action_id=action_id)
        self.assertEqual(action["quarantine_state"], "restore_failed")
        self.assertNotEqual(action["file_id"], original_id)
        self.assertEqual(self._catalog_row(root_id, "empty.txt")["id"], action["file_id"])
        self.assertEqual(self._catalog_row(root_id, "unrelated.txt"), unrelated)
        self.assertTrue(original.exists())
        self.assertTrue(quarantine.exists())
        self.assertEqual(restarted_store.get_root(root_id)["file_count"], 2)

        self.assertEqual(restarted.restore_quarantined_file(action_id, confirm=True)["status"], "restored")

        self.assertFalse(quarantine.exists())
        self.assertEqual(original.read_bytes(), b"")
        self.assertEqual(self._catalog_row(root_id, "unrelated.txt"), unrelated)
        self.assertEqual(restarted_store.get_root(root_id)["file_count"], 2)
        self.assertEqual(restarted_store.get_root(root_id)["total_bytes"], len(b"unrelated content"))
        self.assertFalse(restarted_store.has_unresolved_file_actions(root_id))

    def test_restart_linked_restore_rejects_shared_inode_tamper_before_catalog_insert(self):
        root_id, file_id, action_id = self._quarantine_duplicate()
        original = self.root_path / "original.bin"
        quarantine = self._quarantine_path(action_id)
        self.store.update_file_action(action_id, {"status": "restore_prepared", "quarantine_state": "restore_prepared"})
        os.link(quarantine, original)
        before = original.stat(follow_symlinks=False)
        recorded_ctime = self.store.get_file_action(action_id=action_id)["quarantine_ctime_ns"]
        real_hash = self.service.action_manager._hash_regular_file

        def tamper_after_original_hash(path, *args, **kwargs):
            digest = real_hash(path, *args, **kwargs)
            if Path(path) == original:
                original.write_bytes(b"modified payload")
                # Make the post-hash metadata race visible even on filesystems
                # whose ctime clock has not ticked since hard-link creation.
                self._set_fixture_times(original, (before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))
            return digest

        with patch("kshetrajna.file_actions.FileActionManager._hash_regular_file",
                   side_effect=tamper_after_original_hash):
            restarted_store = FileStore(self.db_path)
            restarted = FileService(restarted_store)

        action = restarted_store.get_file_action(action_id=action_id)
        self.assertEqual(action["quarantine_state"], "restore_failed")
        self.assertEqual(action["quarantine_ctime_ns"], recorded_ctime)
        self.assertIsNone(restarted_store.get_file(file_id))
        self.assertEqual(restarted_store.get_root(root_id)["file_count"], 1)
        self.assertEqual(original.read_bytes(), b"modified payload")
        self.assertEqual(quarantine.read_bytes(), b"modified payload")
        with self.assertRaises(ValueError):
            restarted.restore_quarantined_file(action_id, confirm=True)

    def test_restore_retry_after_transient_unlink_failure_revalidates_own_ctime_change(self):
        root_id, file_id, action_id = self._quarantine_duplicate()
        original = self.root_path / "original.bin"
        quarantine = self._quarantine_path(action_id)

        with patch.object(self.service.action_manager, "_unlink_verified",
                          side_effect=PermissionError(errno.EACCES, "transient unlink failure")):
            with self.assertRaisesRegex(ValueError, "Operation unavailable"):
                self.service.restore_quarantined_file(action_id, confirm=True)
        self.assertFalse(original.exists())
        self.assertEqual(quarantine.read_bytes(), b"original payload")
        self.assertIsNone(self.store.get_file(file_id))
        self.assertEqual(self.store.get_file_action(action_id=action_id)["quarantine_state"], "restore_failed")
        self.assertEqual(self.store.get_file_action(action_id=action_id)["quarantine_ctime_ns"],
                         quarantine.stat(follow_symlinks=False).st_ctime_ns)

        result = self.service.restore_quarantined_file(action_id, confirm=True)

        self.assertEqual(result["status"], "restored")
        self.assertEqual(original.read_bytes(), b"original payload")
        self.assertFalse(quarantine.exists())
        self.assertIsNotNone(self.store.get_file(file_id))
        self.assertEqual(self.store.get_root(root_id)["file_count"], 2)
        self.assertEqual(self.store.get_root(root_id)["total_bytes"], 2 * len(b"original payload"))

    def test_failed_restore_does_not_rebaseline_ctime_of_tampered_quarantine(self):
        root_id, file_id, action_id = self._quarantine_duplicate()
        original = self.root_path / "original.bin"
        quarantine = self._quarantine_path(action_id)
        before = quarantine.stat(follow_symlinks=False)
        recorded_ctime = self.store.get_file_action(action_id=action_id)["quarantine_ctime_ns"]

        def tamper_then_fail(*args, **kwargs):
            quarantine.write_bytes(b"modified payload")
            self._set_fixture_times(quarantine, (before.st_atime_ns, before.st_mtime_ns))
            raise PermissionError(errno.EACCES, "unlink refused after tamper")

        with patch.object(self.service.action_manager, "_unlink_verified", side_effect=tamper_then_fail):
            with self.assertRaisesRegex(ValueError, "Operation unavailable"):
                self.service.restore_quarantined_file(action_id, confirm=True)

        self.assertFalse(original.exists())
        self.assertEqual(quarantine.read_bytes(), b"modified payload")
        self.assertEqual(self.store.get_file_action(action_id=action_id)["quarantine_ctime_ns"], recorded_ctime)
        with self.assertRaisesRegex(ValueError, "changed"):
            self.service.restore_quarantined_file(action_id, confirm=True)
        self.assertIsNone(self.store.get_file(file_id))
        self.assertEqual(self.store.get_root(root_id)["file_count"], 1)
        self.assertTrue(self.store.has_unresolved_file_actions(root_id))


if __name__ == "__main__":
    unittest.main()
