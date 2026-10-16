"""Audited revocation regressions: synthetic provider, process and file targets."""

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from kshetrajna.chat import ChatAssistant
from kshetrajna.collector import Collector
from kshetrajna.config import ConfigStore
from kshetrajna.demo import DemoProbe
from kshetrajna.file_service import FileService
from kshetrajna.file_storage import FileStore
from kshetrajna.jarvis import JarvisCommandBridge, JarvisHttpClient
from kshetrajna.local_ai import OllamaClient
from kshetrajna.models import ProcessSample, Snapshot
from kshetrajna.process_control import ProcessControlService
from kshetrajna.service import AssistantService
from kshetrajna.storage import EventStore


TELEMETRY_KEYS = ("enabled", "device_consent", "track_processes", "track_foreground")


class RecordingProvider:
    tool_planning = True

    def __init__(self, after_plan=None):
        self.calls = []
        self.after_plan = after_plan

    def models(self):
        return [{"name": "synthetic:test"}]

    def stream(self, model, messages, cancel, *, tool_callback=None):
        if cancel.is_set():
            return
        self.calls.append(json.loads(json.dumps(messages)))
        if tool_callback:
            tool_callback({"function": {"name": "review_pc", "arguments": {"intent": "memory_review"}}})
        yield "synthetic-private-telemetry-reply"
        if tool_callback and self.after_plan:
            self.after_plan()


class ConsentRevalidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.config = ConfigStore(self.directory)
        self.config.update({**dict.fromkeys(TELEMETRY_KEYS, True), "allow_local_ai": True,
                            "allow_process_control": True})
        self.store = EventStore(self.directory / "events.db")
        self.store.record(Snapshot(datetime.now(timezone.utc).isoformat(), 12, 16 * 1024**3, 8 * 1024**3,
            90002, "synthetic-foreground.exe", 0,
            [ProcessSample(90001, "chrome.exe", 0, 500 * 1024**2, 123)]), [])
        self.collector = Collector(DemoProbe(), self.config, self.store)
        self.service = AssistantService(self.collector, self.config, self.store)

    def toggle(self, key, reenable=True):
        self.config.update({key: False})
        if reenable:
            self.config.update({key: True})

    def test_permission_versions_survive_other_store_and_reload(self):
        keys = (*TELEMETRY_KEYS, "allow_local_ai", "allow_maintenance", "allow_process_control",
                "allow_permanent_delete")
        for key in keys:
            with self.subTest(key=key):
                self.config.update({key: True})
                reader = ConfigStore(self.directory)
                before = reader.consent_version(key)
                self.toggle(key)
                self.assertNotEqual(reader.consent_version(key), before)
                self.assertEqual(ConfigStore(self.directory).consent_version(key), reader.consent_version(key))

    def test_queued_ai_telemetry_is_invalidated_for_every_permission_transition(self):
        for key in TELEMETRY_KEYS:
            for reenable in (False, True):
                with self.subTest(key=key, reenable=reenable):
                    self.config.update(dict.fromkeys(TELEMETRY_KEYS, True))
                    provider = RecordingProvider()
                    chat = ChatAssistant(self.service, client=provider)
                    with patch("kshetrajna.chat.threading.Thread"):
                        job = chat.start("Review memory")
                    self.assertIsNotNone(chat.jobs[job["id"]]["tools"]["snapshot"])
                    self.toggle(key, reenable)
                    chat._generate(job["id"])
                    self.assertEqual(provider.calls, [], "pre-revocation telemetry reached the provider")
                    self.assertEqual(chat.job(job["id"], job["session_id"])["status"], "cancelled")

    def test_public_settings_patch_cannot_reset_private_permission_versions(self):
        before = self.config.consent_version("allow_process_control")
        with self.assertRaisesRegex(ValueError, "Unknown setting"):
            self.collector.update_settings({"_consent_versions": {"allow_process_control": 0}})
        self.assertEqual(self.config.consent_version("allow_process_control"), before)

    def test_ai_checks_telemetry_generation_before_tool_followup(self):
        for key in TELEMETRY_KEYS:
            with self.subTest(key=key):
                self.config.update(dict.fromkeys(TELEMETRY_KEYS, True))
                provider = RecordingProvider(after_plan=lambda: self.toggle(key))
                chat = ChatAssistant(self.service, client=provider)
                with patch("kshetrajna.chat.threading.Thread"):
                    job = chat.start("Investigate this laptop")
                chat._generate(job["id"])
                self.assertEqual(len(provider.calls), 1, "stale tool context was retransmitted")
                self.assertEqual(chat.job(job["id"], job["session_id"])["status"], "cancelled")

    def test_new_ai_job_does_not_retransmit_history_from_old_telemetry_permissions(self):
        provider = RecordingProvider()
        provider.tool_planning = False
        chat = ChatAssistant(self.service, client=provider)
        with patch("kshetrajna.chat.threading.Thread"):
            first = chat.start("Review memory")
        chat._generate(first["id"])
        self.toggle("track_processes", reenable=False)
        with patch("kshetrajna.chat.threading.Thread"):
            second = chat.start("Explain more", first["session_id"])
        chat._generate(second["id"])
        self.assertEqual(len(provider.calls), 2)
        self.assertNotIn("synthetic-private-telemetry-reply", json.dumps(provider.calls[-1]))
        self.assertNotIn('"name": "chrome.exe"', json.dumps(provider.calls[-1]))

    def test_ai_revocation_reenable_also_discards_old_session_context(self):
        provider = RecordingProvider()
        provider.tool_planning = False
        chat = ChatAssistant(self.service, client=provider)
        with patch("kshetrajna.chat.threading.Thread"):
            first = chat.start("Review memory")
        chat._generate(first["id"])
        self.toggle("allow_local_ai")
        with patch("kshetrajna.chat.threading.Thread"):
            second = chat.start("Hello again", first["session_id"])
        chat._generate(second["id"])
        self.assertEqual(len(provider.calls), 2)
        self.assertNotIn("synthetic-private-telemetry-reply", json.dumps(provider.calls[-1]))

    def test_real_ollama_client_rechecks_telemetry_versions_after_model_validation(self):
        client = OllamaClient()
        chat = ChatAssistant(self.service, client=client)
        def validate(model):
            self.toggle("device_consent")
            return {}
        with patch.object(client, "models", return_value=[{"name": "synthetic:test"}]), patch.object(
                client, "validate_model", side_effect=validate), patch(
                "kshetrajna.local_ai.http.client.HTTPConnection", side_effect=AssertionError("provider transmitted")):
            with patch("kshetrajna.chat.threading.Thread"):
                job = chat.start("Review memory")
            chat._generate(job["id"])
        self.assertEqual(chat.job(job["id"], job["session_id"])["status"], "cancelled")

    def test_memory_maintenance_preview_is_bound_to_telemetry_permission_versions(self):
        self.config.update({"allow_maintenance": True})
        maintenance = self.service.maintenance
        maintenance.driver = Mock()
        plan = maintenance.preview("memory:90001:123")
        self.toggle("track_foreground")
        with self.assertRaisesRegex(ValueError, "consent changed"):
            maintenance.execute(plan["id"], confirm=True)
        maintenance.driver.execute.assert_not_called()
        self.assertEqual(self.store.decisions(), [])

    def test_live_process_candidates_reject_persisted_invalid_consent_but_demo_is_exempt(self):
        self.config.update({"enabled": True, "device_consent": False})
        driver = Mock()
        controls = ProcessControlService(self.collector, self.config, self.store, driver=driver)
        self.assertEqual(controls.candidates(), [])
        with self.assertRaises(ValueError):
            controls.preview(90001, "close")
        driver.execute.assert_not_called()
        demo = ProcessControlService(self.collector, self.config, self.store, demo=True, driver=driver)
        plan = demo.preview(90001, "close")
        self.assertEqual(demo.execute(plan["id"], confirm=True)["status"], "simulated")
        driver.execute.assert_not_called()

    def test_pending_process_plan_rejects_revocation_and_reenable_of_every_required_permission(self):
        for key in (*TELEMETRY_KEYS, "allow_process_control"):
            with self.subTest(key=key):
                self.config.update({**dict.fromkeys(TELEMETRY_KEYS, True), "allow_process_control": True})
                driver = Mock()
                controls = ProcessControlService(self.collector, self.config, self.store, driver=driver)
                plan = controls.preview(90001, "close")
                self.toggle(key)
                with self.assertRaisesRegex(ValueError, "consent changed|fresh preview"):
                    controls.execute(plan["id"], confirm=True)
                driver.execute.assert_not_called()
                self.assertEqual(self.store.decisions(), [])

    def test_fresh_process_review_after_revocation_can_use_fake_driver(self):
        self.toggle("allow_process_control")
        driver = Mock()
        driver.execute.return_value = {"status": "close_requested", "message": "Synthetic request only"}
        controls = ProcessControlService(self.collector, self.config, self.store, driver=driver)
        plan = controls.preview(90001, "close")
        self.assertEqual(controls.execute(plan["id"], confirm=True)["status"], "close_requested")
        driver.execute.assert_called_once()

    def test_permanent_delete_proposal_does_not_survive_off_on_or_service_restart(self):
        files = FileService(FileStore(self.directory / "files.db"))
        root = self.directory / "synthetic-root"
        root.mkdir()
        target = root / "disposable.txt"
        target.write_text("synthetic fixture")
        root_id = files.store.add_root(str(root), "Synthetic")
        files.store.update_root_status(root_id, "ready")
        with files.store._connect() as db:
            db.execute("INSERT INTO files (id,root_id,relative_path,name,indexed_at) VALUES (1,?,'disposable.txt','disposable.txt','now')", (root_id,))
        self.config.update({"allow_permanent_delete": True})
        proposal = files.create_proposal(1, "DELETE", "disposable.txt")
        self.toggle("allow_permanent_delete")
        with patch.object(files.action_manager, "apply_action") as execute:
            with self.assertRaisesRegex(ValueError, "consent changed|fresh review"):
                files.apply_action(proposal["proposal_id"], confirm=True)
            execute.assert_not_called()
        fresh = files.create_proposal(1, "DELETE", "disposable.txt")
        restarted = FileService(files.store)
        with patch.object(restarted.action_manager, "apply_action") as execute:
            with self.assertRaisesRegex(ValueError, "consent changed|fresh review"):
                restarted.apply_action(fresh["proposal_id"], confirm=True)
            execute.assert_not_called()
        self.assertEqual(target.read_text(), "synthetic fixture")


class JarvisPreFilesystemTests(unittest.TestCase):
    def setUp(self):
        self.files = Mock()
        self.files.store.get_roots.return_value = []
        self.local = JarvisCommandBridge(self.files)
        with patch.object(JarvisHttpClient, "_load_token", return_value="synthetic-session"):
            self.remote = JarvisHttpClient("http://127.0.0.1:8765")
        self.remote._get = Mock(return_value={"roots": []})
        self.remote._post = Mock()

    def test_new_path_without_consent_is_not_resolved_or_statted(self):
        with patch.object(Path, "resolve", side_effect=AssertionError("path resolved")), patch.object(
                Path, "stat", side_effect=AssertionError("path statted")):
            for bridge in (self.local, self.remote):
                for consent in ({}, {"consent": False}):
                    with self.subTest(bridge=type(bridge).__name__, consent=consent):
                        with self.assertRaisesRegex(ValueError, "consent"):
                            bridge.execute("sort_preview", path="/synthetic-unapproved-root", **consent)
        self.files.add_root.assert_not_called()
        self.remote._post.assert_not_called()

    def test_network_device_uri_and_traversal_forms_are_rejected_before_filesystem_access(self):
        invalid = (r"\\server\share", "//server/share", r"/\server\share", r"\/server/share",
                   r"\\?\UNC\server\share", r"\\.\C:\private",
                   r"\??\C:\private", "file://server/share", "C:relative", "C:/", "C:/Windows",
                   "/synthetic/../private", "/synthetic/./private", "/synthetic/\0private")
        with patch.object(Path, "resolve", side_effect=AssertionError("path resolved")), patch.object(
                Path, "stat", side_effect=AssertionError("path statted")):
            for bridge in (self.local, self.remote):
                for value in invalid:
                    with self.subTest(bridge=type(bridge).__name__, path=value), self.assertRaises(ValueError):
                        bridge.execute("sort_preview", path=value, consent=True)
        self.files.add_root.assert_not_called()
        self.remote._post.assert_not_called()

    def test_existing_approved_paths_match_lexically_without_probing_symlink_aliases(self):
        root = {"id": 7, "path": "/synthetic/approved", "status": "ready"}
        self.files.store.get_roots.return_value = [root]
        self.files.store.get_root.return_value = root
        self.remote._get.return_value = {"roots": [root]}
        with patch.object(Path, "resolve", side_effect=AssertionError("path resolved")), patch.object(
                Path, "stat", side_effect=AssertionError("path statted")):
            self.local.execute("sort_preview", path="/synthetic/approved/")
            self.remote.execute("sort_preview", path="/synthetic/approved/")
        self.files.generate_sort_plan.assert_called_once_with(7, False)
        self.files.add_root.assert_not_called()
        self.remote._post.assert_called_once_with("/api/files/sort/preview", {"root_id": 7, "create_folders": False})

    def test_known_folder_without_consent_does_not_run_discovery(self):
        with patch("kshetrajna.jarvis.discover_known_folders", side_effect=AssertionError("folder probed")):
            for bridge in (self.local, self.remote):
                with self.subTest(bridge=type(bridge).__name__), self.assertRaisesRegex(ValueError, "consent"):
                    bridge.execute("sort_preview", known_folder="downloads")
        self.remote._get.assert_not_called()

    def test_windows_approved_path_matches_case_and_slashes_without_filesystem_access(self):
        root = {"id": 7, "path": r"C:\Synthetic\Approved", "status": "ready"}
        self.files.store.get_roots.return_value = [root]
        self.files.store.get_root.return_value = root
        self.remote._get.return_value = {"roots": [root]}
        with patch.object(Path, "resolve", side_effect=AssertionError("path resolved")), patch.object(
                Path, "stat", side_effect=AssertionError("path statted")):
            self.local.execute("sort_preview", path="c:/synthetic/approved/")
            self.remote.execute("sort_preview", path="c:/synthetic/approved/")
        self.files.generate_sort_plan.assert_called_once_with(7, False)
        self.files.add_root.assert_not_called()

    def test_approved_path_alias_needs_consent_instead_of_being_resolved(self):
        root = {"id": 7, "path": "/synthetic/approved", "status": "ready"}
        self.files.store.get_roots.return_value = [root]
        self.remote._get.return_value = {"roots": [root]}
        with patch.object(Path, "resolve", side_effect=AssertionError("alias resolved")), patch.object(
                Path, "stat", side_effect=AssertionError("alias statted")):
            for bridge in (self.local, self.remote):
                with self.subTest(bridge=type(bridge).__name__), self.assertRaisesRegex(ValueError, "consent"):
                    bridge.execute("sort_preview", path="/synthetic/alias-to-approved")
        self.files.add_root.assert_not_called()
        self.remote._post.assert_not_called()
