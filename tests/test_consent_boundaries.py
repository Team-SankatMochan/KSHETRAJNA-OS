"""Consent is enforced by services before storage, device or provider work."""

import http.client
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch

from kshetrajna.chat import ChatAssistant
from kshetrajna.collector import Collector
from kshetrajna.config import ConfigStore, Settings
from kshetrajna.demo import DemoProbe, seed_demo
from kshetrajna.file_service import FileService
from kshetrajna.file_storage import FileStore
from kshetrajna.jarvis import JarvisCommandBridge, JarvisHttpClient, serve_jsonl
from kshetrajna.local_ai import OllamaClient
from kshetrajna.server import DashboardServer
from kshetrajna.service import AssistantService
from kshetrajna.storage import EventStore


INVALID_CONFIRM = (None, False, 0, 1, 1.0, "true", "false", [], {})


class ConsentBoundaries(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.config = ConfigStore(self.directory)
        self.store = EventStore(self.directory / "events.db")
        self.probe = DemoProbe()
        self.collector = Collector(self.probe, self.config, self.store)
        self.service = AssistantService(self.collector, self.config, self.store)
        self.files = FileService(FileStore(self.directory / "files.db"))

    def test_all_public_actuators_require_literal_confirmation_before_lookup(self):
        boundaries = (
            (self.service.approve, ("priority:42:123",)),
            (self.service.undo, ("decision",)),
            (self.service.delete_history, ()),
            (self.collector.delete_history, ()),
            (self.service.maintenance.execute, ("plan",)),
            (self.files.apply_action, ("proposal",)),
            (self.files.undo_action, ("action",)),
            (self.files.apply_sort_plan, ("plan",)),
            (self.files.undo_sort_plan, ("plan",)),
            (self.files.delete_all, ()),
            (ChatAssistant(self.service, client=Mock()).unload_model, ("model",)),
        )
        with patch.object(self.store, "_connect", side_effect=AssertionError("storage reached")), patch.object(
                self.files.store, "_connect", side_effect=AssertionError("file storage reached")):
            for method, args in boundaries:
                for value in INVALID_CONFIRM:
                    with self.subTest(method=method.__qualname__, confirm=value):
                        with self.assertRaisesRegex(ValueError, "confirm|confirmation"):
                            method(*args, confirm=value)

    def test_invalid_opaque_ids_rejected_before_sqlite(self):
        methods = (self.files.apply_action, self.files.undo_action,
                   self.files.dismiss_proposal, self.files._sort_plan)
        invalid = (None, True, 42, [], {}, "", "a/b", "a\0b", "é", "x" * 201)
        with patch.object(self.files.store, "_connect", side_effect=AssertionError("SQLite reached")):
            for method in methods:
                for value in invalid:
                    with self.subTest(method=method.__name__, value=value):
                        kwargs = {"confirm": True} if method.__name__ in ("apply_action", "undo_action") else {}
                        with self.assertRaises(ValueError):
                            method(value, **kwargs)

    def test_live_collection_blocks_persisted_enabled_without_consent_and_revocation(self):
        self.config.update({"enabled": True})
        with patch.object(self.probe, "sample", wraps=self.probe.sample) as sample:
            self.assertIsNone(self.collector.collect_once())
            sample.assert_not_called()
            self.collector.update_settings({"device_consent": True, "enabled": True})
            self.assertIsNotNone(self.collector.collect_once())
            self.collector.update_settings({"device_consent": False})
            self.assertIsNone(self.collector.collect_once())
            self.assertEqual(sample.call_count, 1)
            self.assertFalse(self.config.load().device_consent)
            self.assertFalse(self.config.load().enabled)

    def test_live_enable_requires_device_consent_and_demo_is_explicitly_exempt(self):
        with self.assertRaisesRegex(ValueError, "consent"):
            self.collector.update_settings({"enabled": True})
        self.assertFalse(self.config.load().enabled)
        demo = Collector(self.probe, self.config, self.store, demo=True)
        demo.update_settings({"enabled": True})
        self.assertIsNotNone(demo.collect_once())
        self.assertFalse(self.config.load().device_consent)

    def test_new_roots_require_service_consent_before_filesystem_access(self):
        with patch.object(self.files, "_validate_root_path", side_effect=AssertionError("path inspected")):
            for value in INVALID_CONFIRM:
                with self.subTest(consent=value), self.assertRaisesRegex(ValueError, "consent"):
                    self.files.add_root(str(self.directory), "Root", consent=value)

    def test_jarvis_jsonl_does_not_manufacture_root_consent(self):
        bridge = JarvisCommandBridge(self.files)
        output = StringIO()
        requests = [{"command": "sort_preview", "path": str(self.directory), **extra}
                    for extra in ({}, {"consent": False}, {"consent": "true"}, {"consent": 1})]
        with patch.object(self.files, "add_root", side_effect=AssertionError("root approved")):
            serve_jsonl(bridge, StringIO("\n".join(map(json.dumps, requests))), output)
        self.assertTrue(all(not json.loads(line)["ok"] for line in output.getvalue().splitlines()))
        self.assertEqual(self.files.store.get_roots(), [])

    def test_permanent_delete_is_separately_disabled_and_rechecked_at_apply(self):
        self.assertFalse(getattr(Settings(), "allow_permanent_delete", True))
        root = self.directory / "approved"
        root.mkdir()
        source = root / "doc.txt"
        source.write_text("keep me")
        root_id = self.files.store.add_root(str(root), "Approved")
        self.files.store.update_root_status(root_id, "ready")
        with self.files.store._connect() as db:
            db.execute("INSERT INTO files (id,root_id,relative_path,name,indexed_at) VALUES (1,?,'doc.txt','doc.txt','now')", (root_id,))
        with self.assertRaisesRegex(ValueError, "permanent"):
            self.files.create_proposal(1, "DELETE", "doc.txt")
        self.config.update({"allow_permanent_delete": True})
        proposal = self.files.create_proposal(1, "DELETE", "doc.txt")
        self.config.update({"allow_permanent_delete": False})
        with self.assertRaisesRegex(ValueError, "permanent"):
            self.files.apply_action(proposal["proposal_id"], confirm=True)
        self.assertEqual(source.read_text(), "keep me")

    def test_local_ai_revocation_invalidates_queued_job_even_after_reenable(self):
        model = Mock()
        model.models.return_value = [{"name": "local:test"}]
        model.stream.return_value = iter(["should not transmit"])
        chat = ChatAssistant(self.service, client=model)
        self.config.update({"allow_local_ai": True})
        with patch("kshetrajna.chat.threading.Thread"):
            job = chat.start("Private queued message")
        self.config.update({"allow_local_ai": False})
        self.config.update({"allow_local_ai": True})
        chat._generate(job["id"])
        model.stream.assert_not_called()
        self.assertEqual(chat.job(job["id"], job["session_id"])["status"], "cancelled")

    def test_local_ai_revocation_during_model_discovery_blocks_message_transmission(self):
        model = Mock()
        def models():
            self.config.update({"allow_local_ai": False})
            return [{"name": "local:test"}]
        model.models.side_effect = models
        chat = ChatAssistant(self.service, client=model)
        self.config.update({"allow_local_ai": True})
        with patch("kshetrajna.chat.threading.Thread"):
            job = chat.start("Private queued message")
        chat._generate(job["id"])
        model.stream.assert_not_called()
        self.assertEqual(chat.job(job["id"], job["session_id"])["status"], "cancelled")

    def test_enabled_local_ai_is_an_explicit_opt_in_without_an_extra_settings_flag(self):
        model = Mock(tool_planning=False)
        model.models.return_value = [{"name": "local:test"}]
        model.stream.return_value = iter(["Approved reply"])
        chat = ChatAssistant(self.service, client=model)
        self.config.update({"allow_local_ai": True})
        with patch("kshetrajna.chat.threading.Thread"):
            job = chat.start("Hello")
        chat._generate(job["id"])
        self.assertEqual(chat.job(job["id"], job["session_id"])["reply"], "Approved reply")
        self.assertEqual(model.stream.call_count, 1)
        with self.assertRaisesRegex(ValueError, "confirm"):
            chat.unload_model("local:test")
        chat.unload_model("local:test", confirm=True)
        model.unload.assert_called_once_with("local:test")

    def test_maintenance_confirmation_does_not_consume_preview_and_revocation_invalidates_it(self):
        self.config.update({"allow_maintenance": True})
        driver = Mock()
        driver.execute.return_value = {"status": "launched", "message": "Requested"}
        maintenance = self.service.maintenance
        maintenance.driver = driver
        plan = maintenance.preview("storage_settings")
        for value in INVALID_CONFIRM:
            with self.assertRaises(ValueError):
                maintenance.execute(plan["id"], confirm=value)
        driver.execute.assert_not_called()
        self.assertIn(plan["id"], maintenance.plans)
        self.config.update({"allow_maintenance": False})
        self.config.update({"allow_maintenance": True})
        with self.assertRaisesRegex(ValueError, "consent changed"):
            maintenance.execute(plan["id"], confirm=True)
        fresh = maintenance.preview("storage_settings")
        self.assertEqual(maintenance.execute(fresh["id"], confirm=True)["status"], "launched")
        driver.execute.assert_called_once()
        with self.assertRaises(ValueError):
            maintenance.execute(fresh["id"], confirm=True)

    def test_public_errors_do_not_include_private_os_details(self):
        private = "private-device-path-do-not-expose"
        with patch.object(self.files.action_manager, "apply_action", side_effect=OSError(private)):
            with self.assertRaises(ValueError) as failure:
                self.files.apply_action("unknown-but-valid-id", confirm=True)
        self.assertNotIn(private, str(failure.exception))
        def wrapped_error(*args):
            try:
                raise OSError(private)
            except OSError as error:
                raise ValueError(f"Mutation failed: {error}")
        with patch.object(self.files.action_manager, "apply_action", side_effect=wrapped_error):
            with self.assertRaises(ValueError) as failure:
                self.files.apply_action("unknown-but-valid-id", confirm=True)
        self.assertNotIn(private, str(failure.exception))
        output = StringIO()
        with patch.object(self.files, "get_empty_files", side_effect=OSError(private)):
            serve_jsonl(JarvisCommandBridge(self.files), StringIO('{"command":"empty_preview"}'), output)
        self.assertNotIn(private, output.getvalue())
        self.assertFalse(json.loads(output.getvalue())["ok"])

    def test_permanent_delete_executes_only_after_explicit_capability_and_confirmation(self):
        root = self.directory / "approved"
        root.mkdir()
        source = root / "doc.txt"
        source.write_text("disposable fixture")
        root_id = self.files.store.add_root(str(root), "Approved")
        self.files.store.update_root_status(root_id, "ready")
        with self.files.store._connect() as db:
            db.execute("INSERT INTO files (id,root_id,relative_path,name,indexed_at) VALUES (1,?,'doc.txt','doc.txt','now')", (root_id,))
        self.config.update({"allow_permanent_delete": True})
        proposal = self.files.create_proposal(1, "DELETE", "doc.txt")
        for value in INVALID_CONFIRM:
            with self.assertRaises(ValueError):
                self.files.apply_action(proposal["proposal_id"], confirm=value)
        self.assertTrue(source.exists())
        self.assertEqual(self.files.apply_action(proposal["proposal_id"], confirm=True)["status"], "deleted")
        self.assertFalse(source.exists())
        self.assertEqual(self.files.get_action_journal()[0]["status"], "deleted")

    def test_jarvis_remote_root_consent_and_apply_forwarding(self):
        with patch.object(JarvisHttpClient, "_load_token", return_value="session"):
            client = JarvisHttpClient("http://127.0.0.1:8765")
        client._get = Mock(return_value={"roots": []})
        client._post = Mock(return_value={})
        for extra in ({}, {"consent": False}):
            with self.assertRaisesRegex(ValueError, "consent"):
                client.execute("sort_preview", path=str(self.directory), **extra)
        client._post.assert_not_called()
        client._get.side_effect = [
            {"roots": []}, {"roots": [{"id": 7, "status": "ready"}]},
        ]
        client._post.side_effect = [{"id": 7}, {"id": "preview"}]
        client.execute("sort_preview", path=str(self.directory), consent=True)
        self.assertIs(client._post.call_args_list[0].args[1]["consent"], True)
        client._post.side_effect = None
        for command in ("sort_apply", "sort_undo"):
            client.execute(command, plan_id="reviewed-plan", confirm=True)
            self.assertIs(client._post.call_args.args[1]["confirm"], True)

    def test_authenticated_jarvis_ignores_proxies_and_refuses_redirects(self):
        received, redirect_root = [], []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                received.append((self.path, self.headers.get("X-Kshetrajna-Token")))
                if self.path == "/" and not redirect_root:
                    payload = b'<meta name="csrf-token" content="private-session">'
                    self.send_response(200)
                else:
                    payload = b""
                    self.send_response(302)
                    self.send_header("Location", "/stolen")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), thread.join(), server.server_close()))
        with patch.dict("os.environ", {"http_proxy": "http://127.0.0.1:1", "HTTP_PROXY": "http://127.0.0.1:1",
                                       "no_proxy": "", "NO_PROXY": ""}):
            client = JarvisHttpClient(f"http://127.0.0.1:{server.server_port}")
            with self.assertRaisesRegex(ValueError, "Dashboard request failed"):
                client.execute("empty_preview")
            redirect_root.append(True)
            with self.assertRaisesRegex(ValueError, "loopback dashboard"):
                JarvisHttpClient(f"http://127.0.0.1:{server.server_port}")
        self.assertEqual(received, [("/", None), ("/api/files/empty", "private-session"), ("/", None)])

    def test_ollama_cancelled_request_never_validates_or_transmits(self):
        client, cancel = OllamaClient(), threading.Event()
        cancel.set()
        with patch.object(client, "validate_model", side_effect=AssertionError("provider contacted")):
            self.assertEqual(list(client.stream("local:test", [], cancel)), [])

    def test_ollama_rechecks_cancel_immediately_before_chat_request(self):
        client, cancel = OllamaClient(), threading.Event()
        connection = Mock()
        def connected(*args, **kwargs):
            cancel.set()
            return connection
        with patch.object(client, "validate_model", return_value={}), patch(
                "kshetrajna.local_ai.http.client.HTTPConnection", side_effect=connected):
            self.assertEqual(list(client.stream("local:test", [], cancel)), [])
        connection.request.assert_not_called()


class ConsentHttpTests(unittest.TestCase):
    setUp = ConsentBoundaries.setUp

    def test_routes_forward_confirmation_to_the_service(self):
        self.config.update({"enabled": True})
        seed_demo(self.store, self.probe)
        server = DashboardServer(0, self.collector, self.config, self.store, demo=True)
        server.file_service = self.files
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), thread.join(), server.server_close()))
        routes = (
            ("/api/delete", {}),
            ("/api/actions/approve", {"id": "priority:42:123"}),
            ("/api/actions/undo", {"id": "decision"}),
            ("/api/maintenance/execute", {"id": "plan"}),
            ("/api/files/actions/apply", {"proposal_id": "proposal"}),
            ("/api/files/actions/undo", {"action_id": "action"}),
            ("/api/files/sort/apply", {"plan_id": "plan"}),
            ("/api/files/sort/undo", {"plan_id": "plan"}),
            ("/api/files/delete-all", {}),
            ("/api/chat/unload", {"model": "model"}),
        )
        for path, payload in routes:
            for extra in ({}, *({"confirm": value} for value in INVALID_CONFIRM)):
                with self.subTest(path=path, extra=extra):
                    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
                    connection.request("POST", path, json.dumps({**payload, **extra}), {
                        "Origin": f"http://127.0.0.1:{server.server_port}",
                        "X-Kshetrajna-Token": server.token,
                    })
                    response = connection.getresponse()
                    data = json.loads(response.read())
                    connection.close()
                    self.assertEqual(response.status, 400, data)
                    self.assertRegex(data["error"], "confirm|confirmation")
