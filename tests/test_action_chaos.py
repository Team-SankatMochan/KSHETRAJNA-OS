"""Actual process death during real disposable FileService quarantine operations.

Instrumentation observes real calls and then os._exit()s. It never substitutes a
successful move, journal write, catalog transaction, or reconciliation result.
"""

import contextlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
from scripts.benchmark_core import run_process, wait_for_scan
from kshetrajna.file_service import FileService
from kshetrajna.file_storage import FileStore, QUARANTINE_DIR_NAME

PAYLOAD = b"Disposable crash fixture: duplicate payload.\n"
CRASH_CODES = {"prepared": 81, "moved": 82, "catalog_transaction": 83, "catalog_complete": 84}
WAIT = 60


def crash_child(base, boundary):
    """Internal subprocess entry; the parent owns and removes this fixture."""
    store = FileStore(base / "catalog.db")
    service = FileService(store)
    root_id = service.add_root(str(base / "root"), "Disposable crash fixture")["id"]
    if wait_for_scan(service, root_id, WAIT)["status"] != "ready":
        raise RuntimeError("fixture scan failed")
    matches = service.search("victim.bin", root_id)["results"]
    if len(matches) != 1:
        raise RuntimeError("fixture victim not indexed")
    code = CRASH_CODES[boundary]
    if boundary == "prepared":
        update = store.update_file_action

        def observed_update(action_id, updates):
            update(action_id, updates)
            if updates.get("status") == "prepared":
                os._exit(code)

        store.update_file_action = observed_update
    elif boundary == "moved":
        move = service.action_manager._move_without_overwrite

        def observed_move(*args, **kwargs):
            move(*args, **kwargs)
            os._exit(code)

        service.action_manager._move_without_overwrite = observed_move
    elif boundary == "catalog_transaction":
        connect = store._connect

        @contextlib.contextmanager
        def observed_connect():
            with connect() as database:
                def trace(statement):
                    # UPDATE file_actions has executed, but the shared transaction
                    # has not committed. SQLite must roll it back after process death.
                    if statement.lstrip().upper().startswith("DELETE FROM FILES WHERE ID="):
                        os._exit(code)

                database.set_trace_callback(trace)
                yield database

        store._connect = observed_connect
    elif boundary == "catalog_complete":
        refresh = store.refresh_root_totals

        def observed_refresh(*args, **kwargs):
            refresh(*args, **kwargs)
            # Journal/catalog transaction AND aggregate refresh have really committed.
            os._exit(code)

        store.refresh_root_totals = observed_refresh
    service.delete_duplicate_file(matches[0]["id"], confirm=True)
    raise RuntimeError("crash boundary was not reached")


class ActionCrashTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="kshetrajna-chaos-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "root"
        self.root.mkdir()
        (self.root / "victim.bin").write_bytes(PAYLOAD)
        (self.root / "survivor.bin").write_bytes(PAYLOAD)

    def crash(self, boundary):
        result = run_process(
            [sys.executable, "-B", str(Path(__file__).resolve()), "--crash-child", boundary, str(self.base)],
            timeout=WAIT,
        )
        self.assertEqual(result.returncode, CRASH_CODES[boundary], "child did not die at the requested boundary")
        store = FileStore(self.base / "catalog.db")
        actions = store.get_file_actions()
        self.assertEqual(len(actions), 1)
        action = actions[0]
        self.assertTrue(action["quarantine_requested"])
        self.assertEqual(action["source_relative"], "victim.bin")
        with store._connect() as database:
            self.assertEqual(database.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        return store, action

    def assert_survivor(self):
        self.assertEqual((self.root / "survivor.bin").read_bytes(), PAYLOAD)

    def check_boundary(self, boundary):
        store, before = self.crash(boundary)
        action_id, root_id, file_id = before["id"], before["root_id"], before["file_id"]
        target = self.root / QUARANTINE_DIR_NAME / action_id
        completed = boundary == "catalog_complete"
        unmoved = boundary == "prepared"
        self.assertEqual(before["status"], "quarantined" if completed else "prepared")
        self.assertEqual(before["quarantine_state"], "quarantined" if completed else "prepared")
        self.assertEqual((self.root / "victim.bin").exists(), unmoved)
        self.assertEqual(target.exists(), not unmoved)
        self.assertEqual(store.get_file(file_id) is None, completed)
        self.assertEqual(store.get_root(root_id)["file_count"], 1 if completed else 2)
        self.assert_survivor()

        # This is a new live service, not demo mode; startup executes real recovery.
        service = FileService(FileStore(self.base / "catalog.db"))
        after = service.store.get_file_action(action_id=action_id)
        expected = "dismissed" if unmoved else "quarantined"
        self.assertEqual(after["status"], expected)
        self.assertEqual(after["quarantine_state"], expected)
        self.assertEqual(service.store.get_file(file_id) is None, not unmoved)
        self.assertEqual(service.store.get_root(root_id)["file_count"], 2 if unmoved else 1)
        self.assertEqual(service.store.get_root(root_id)["total_bytes"], len(PAYLOAD) * (2 if unmoved else 1))
        self.assert_survivor()
        second = FileService(FileStore(self.base / "catalog.db"))
        again = second.store.get_file_action(action_id=action_id)
        self.assertEqual(again["status"], expected, "second startup was not idempotent")
        self.assertEqual(again["quarantine_state"], expected)
        if unmoved:
            self.assertEqual((self.root / "victim.bin").read_bytes(), PAYLOAD)
            self.assertFalse(target.exists())
            return
        self.assertEqual(target.read_bytes(), PAYLOAD)
        self.assertFalse((self.root / "victim.bin").exists())
        restored = second.restore_quarantined_file(action_id, confirm=True)
        self.assertEqual(restored["status"], "restored")
        self.assertEqual((self.root / "victim.bin").read_bytes(), PAYLOAD)
        self.assertFalse(target.exists())
        self.assertEqual(second.store.get_file(file_id)["relative_path"], "victim.bin")
        self.assertEqual(second.store.get_root(root_id)["file_count"], 2)
        self.assertEqual(second.store.get_root(root_id)["total_bytes"], len(PAYLOAD) * 2)
        self.assert_survivor()

    def test_crash_after_durable_prepare_preserves_original(self):
        self.check_boundary("prepared")

    def test_crash_after_real_move_recovers_quarantine_and_restore(self):
        self.check_boundary("moved")

    def test_crash_inside_catalog_transaction_rolls_back_then_recovers(self):
        self.check_boundary("catalog_transaction")

    def test_crash_after_catalog_and_totals_is_idempotent(self):
        self.check_boundary("catalog_complete")

    def test_restart_does_not_overwrite_occupied_original(self):
        store, before = self.crash("moved")
        occupied = b"Synthetic replacement must not be overwritten."
        (self.root / "victim.bin").write_bytes(occupied)
        target = self.root / QUARANTINE_DIR_NAME / before["id"]
        service = FileService(store)
        action = store.get_file_action(action_id=before["id"])
        self.assertEqual(action["status"], "quarantine_conflict")
        self.assertEqual((self.root / "victim.bin").read_bytes(), occupied)
        self.assertEqual(target.read_bytes(), PAYLOAD)
        with self.assertRaises(ValueError):
            service.restore_quarantined_file(before["id"], confirm=True)
        self.assertEqual((self.root / "victim.bin").read_bytes(), occupied)
        self.assertEqual(target.read_bytes(), PAYLOAD)
        self.assert_survivor()

    def test_restart_rejects_tampered_quarantine_content(self):
        store, before = self.crash("moved")
        target = self.root / QUARANTINE_DIR_NAME / before["id"]
        tampered = b"X" * len(PAYLOAD)
        target.write_bytes(tampered)
        service = FileService(store)
        self.assertEqual(store.get_file_action(action_id=before["id"])["status"], "quarantine_conflict")
        with self.assertRaises(ValueError):
            service.restore_quarantined_file(before["id"], confirm=True)
        self.assertFalse((self.root / "victim.bin").exists())
        self.assertEqual(target.read_bytes(), tampered)
        self.assert_survivor()


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--crash-child":
        crash_child(Path(sys.argv[3]), sys.argv[2])
    else:
        unittest.main()
