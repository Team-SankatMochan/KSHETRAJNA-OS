import http.client
import json
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from kshetrajna.chat import ChatAssistant, identify_intent
from kshetrajna.collector import Collector
from kshetrajna.config import ConfigStore, Settings
from kshetrajna.demo import DemoProbe
from kshetrajna.local_ai import OllamaClient, LocalAIError
from kshetrajna.models import ProcessSample, Snapshot
from kshetrajna.process_control import ProcessControlService, WindowsProcessDriver
from kshetrajna.server import DashboardServer
from kshetrajna.service import AssistantService
from kshetrajna.storage import EventStore


class FakeModel:
    def __init__(self):
        self.calls = []
        self.unloaded = []
    def models(self):
        return [{"name": "local:test", "size_bytes": 123}]
    def stream(self, model, messages, cancel):
        self.calls.append((model, messages))
        yield "A local "
        yield "reply."
    def unload(self, model):
        self.unloaded.append(model)
        return {"status": "completed", "message": "Unloaded."}


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = ConfigStore(Path(self.temp.name))
        self.config.update({"device_consent": True, "enabled": True, "track_foreground": True})
        self.store = EventStore(Path(self.temp.name) / "events.db")
        self.collector = Collector(DemoProbe(), self.config, self.store)
        self.service = AssistantService(self.collector, self.config, self.store, demo=False)
        self.model = FakeModel()
        self.chat = ChatAssistant(self.service, client=self.model)
        self.now = datetime.now(timezone.utc)
        for seconds in (40, 20, 0):
            self.store.record(Snapshot(
                (self.now - timedelta(seconds=seconds)).isoformat(), 22, 16*1024**3, 6*1024**3,
                900, "foreground.exe", 1,
                [ProcessSample(42, "chrome.exe", 0.1, 500*1024**2, 123),
                 ProcessSample(77, "lsass.exe", 0, 600*1024**2, 456)]), [])
        self.config.update({"sample_interval_seconds": 10})

    def wait_job(self, job):
        for _ in range(100):
            current = self.chat.job(job["id"], job["session_id"])
            if current["status"] != "generating":
                return current
            time.sleep(0.01)
        self.fail("Reply did not finish")

    def test_model_requires_consent_but_tools_remain_available(self):
        job = self.chat.start("free up ram")
        self.assertEqual(job["status"], "complete")
        self.assertFalse(self.model.calls)
        self.assertIn("Available RAM", job["reply"])
        self.assertTrue(any(a["type"] == "maintenance" for a in job["actions"]))
        self.assertEqual(self.store.decisions(), [])

    def test_real_conversation_provider_uses_history_and_local_context(self):
        self.config.update({"allow_local_ai": True})
        first = self.wait_job(self.chat.start("Explain recursion"))
        self.assertEqual(first["reply"], "A local reply.")
        second = self.wait_job(self.chat.start("Explain it more simply", first["session_id"]))
        self.assertEqual(second["provider"], "Ollama / local:test")
        self.assertEqual(len(self.model.calls), 2)
        history = self.model.calls[-1][1]
        self.assertTrue(any(m["content"] == "Explain recursion" for m in history))
        self.assertTrue(any(m["content"] == "A local reply." for m in history))

    def test_clear_erases_history_jobs_and_blocks_old_session(self):
        job = self.chat.start("Hello")
        self.chat.clear(job["session_id"])
        self.assertFalse(self.chat.sessions)
        self.assertFalse(self.chat.jobs)
        with self.assertRaises(ValueError):
            self.chat.start("Hello", job["session_id"])

    def test_collection_off_does_not_share_retained_snapshot(self):
        self.config.update({"enabled": False, "allow_local_ai": True})
        result = self.wait_job(self.chat.start("Review ram"))
        self.assertEqual(result["actions"], [])
        prompt = self.model.calls[0][1][0]["content"]
        self.assertIn('"snapshot": null', prompt)
        self.assertNotIn('"pid": 42', prompt)

    def test_idle_review_requires_evidence_and_never_executes(self):
        job = self.chat.start("kill all idle processes")
        self.assertEqual([a.get("pid") for a in job["actions"]], [42])
        self.assertIn("Low CPU does not establish", job["reply"])
        self.assertEqual(self.store.decisions(), [])

    def test_intent_mapping_and_message_validation(self):
        self.assertEqual(identify_intent("clear microsoft store cache"), "store_cache")
        self.assertEqual(identify_intent("clean temporary files"), "temp_cleanup")
        self.assertEqual(identify_intent("kill pid 42"), "process_review")
        self.assertEqual(identify_intent("Why is the sky blue?"), "conversation")
        self.assertEqual(identify_intent("Explain what RAM does"), "conversation")
        with self.assertRaises(ValueError): self.chat.start("x"*2001)
        with self.assertRaises(ValueError): Settings.from_dict({"allow_local_ai": "true"})

    def test_model_error_is_explicit_and_keeps_tool_plan(self):
        self.config.update({"allow_local_ai": True})
        with patch.object(self.model, "stream", side_effect=LocalAIError("Controlled model failure")):
            job = self.wait_job(self.chat.start("review ram"))
        self.assertEqual(job["status"], "failed")
        self.assertIn("Controlled model failure", job["error"])
        self.assertTrue(job["actions"])

    def test_cancelled_model_output_is_not_saved(self):
        started, released = threading.Event(), threading.Event()
        def waiting(model, messages, cancel):
            started.set()
            released.wait(2)
            yield "Late output"
        self.config.update({"allow_local_ai": True})
        with patch.object(self.model, "stream", side_effect=waiting):
            job = self.chat.start("Hello")
            self.assertTrue(started.wait(1))
            self.chat.cancel(job["id"], job["session_id"])
            released.set()
            time.sleep(.05)
        self.assertEqual(self.chat.job(job["id"], job["session_id"])["status"], "cancelled")
        self.assertEqual(len(self.chat.sessions[job["session_id"]]["messages"]), 1)

    def test_process_control_is_opt_in_and_journaled_before_execution(self):
        controls = self.chat.processes
        with self.assertRaises(ValueError): controls.preview(42, "close")
        self.config.update({"allow_process_control": True})
        calls = []
        def execute(plan):
            self.assertEqual(self.store.decisions()[0]["status"], "process_requested")
            calls.append(plan)
            return {"status": "close_requested", "message": "Requested"}
        controls.driver = SimpleNamespace(execute=execute)
        plan = controls.preview(42, "close")
        with self.assertRaises(ValueError): controls.execute(plan["id"])
        result = controls.execute(plan["id"], confirm=True)
        self.assertEqual(result["status"], "close_requested")
        self.assertEqual(len(calls), 1)
        with self.assertRaises(ValueError): controls.execute(plan["id"], confirm=True)

    def test_force_needs_exact_name_and_foreground_revalidation(self):
        self.config.update({"allow_process_control": True})
        controls = self.chat.processes
        plan = controls.preview(42, "terminate")
        with self.assertRaises(ValueError): controls.execute(plan["id"], confirm=True, typed_name="chrome")
        latest = self.store.latest()
        self.store.record(Snapshot(datetime.now(timezone.utc).isoformat(), 0, 16*1024**3, 6*1024**3,
                                   42, "chrome.exe", 1, [ProcessSample(42, "chrome.exe", 0, 500*1024**2, 123)]), [])
        with self.assertRaises(ValueError): controls.execute(plan["id"], confirm=True, typed_name="chrome.exe")
        self.assertEqual(self.store.decisions(), [])

    def test_protected_pid_reuse_expiry_and_no_demo_mutations(self):
        self.config.update({"allow_process_control": True})
        controls = self.chat.processes
        with self.assertRaises(ValueError): controls.preview(77, "terminate")
        plan = controls.preview(42, "terminate")
        controls.clock = lambda: time.time()+61
        with self.assertRaises(ValueError): controls.execute(plan["id"], confirm=True, typed_name="chrome.exe")
        controls.clock = time.time
        demo = ProcessControlService(self.collector, self.config, self.store, demo=True,
                                    driver=SimpleNamespace(execute=lambda p: self.fail("Demo driver executed")))
        plan = demo.preview(42, "terminate")
        self.assertEqual(demo.execute(plan["id"], confirm=True, typed_name="chrome.exe")["status"], "simulated")
        controls_plan = controls.preview(42, "close")
        self.store.record(Snapshot(datetime.now(timezone.utc).isoformat(), 0, 16*1024**3, 6*1024**3,
                                   900, "foreground.exe", 1, [ProcessSample(42, "chrome.exe", 0, 500*1024**2, 999)]), [])
        with self.assertRaises(ValueError): controls.execute(controls_plan["id"], confirm=True)

    def test_ollama_refuses_cloud_models(self):
        client = OllamaClient()
        with patch.object(client, "_json", return_value={"models":[{"name":"model:cloud"}, {"name":"local:test"}]}):
            self.assertEqual([m["name"] for m in client.models()], ["local:test"])
        with patch.object(client, "models", return_value=[{"name":"local:test"}]), patch.object(client, "_json", return_value={"remote_host":"https://cloud.example"}):
            with self.assertRaises(LocalAIError): client.validate_model("local:test")

    def test_native_driver_refuses_protected_name_before_any_win32_call(self):
        driver = WindowsProcessDriver.__new__(WindowsProcessDriver)
        with self.assertRaises(ValueError): driver._verified({"name":"lsass.exe","pid":77,"created_ticks":123}, force=True)

    def test_model_tool_call_only_prepares_review_cards(self):
        self.config.update({"allow_local_ai": True})
        self.model.tool_planning = True
        calls = []
        def stream(model, messages, cancel, *, tool_callback=None):
            calls.append(messages)
            if tool_callback:
                tool_callback({"function":{"name":"review_pc", "arguments":{"intent":"memory_review"}}})
            else:
                yield "Review the memory cards before approval."
        with patch.object(self.model, "stream", side_effect=stream):
            job = self.wait_job(self.chat.start("This laptop keeps lagging. Can you investigate?"))
        self.assertEqual(len(calls), 2)
        self.assertTrue(job["actions"])
        self.assertEqual(calls[-1][-1]["role"], "tool")
        self.assertEqual(self.store.decisions(), [])

    def test_model_cannot_invent_executable_tools_or_pids(self):
        self.config.update({"allow_local_ai": True})
        self.model.tool_planning = True
        def stream(model, messages, cancel, *, tool_callback=None):
            tool_callback({"function":{"name":"terminate_process", "arguments":{"pid":77}}})
            yield "Done"
        with patch.object(self.model, "stream", side_effect=stream):
            job = self.wait_job(self.chat.start("Hello"))
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["actions"], [])
        self.assertEqual(self.store.decisions(), [])


    def test_verified_action_outcome_enters_conversation_history(self):
        self.config.update({"allow_local_ai": True})
        job = self.wait_job(self.chat.start("Hello"))
        self.chat.record_outcome(job["session_id"], {"title":"Close app", "status":"close_requested",
            "result":{"message":"Save prompt may remain open"}, "undo":"No undo"})
        self.wait_job(self.chat.start("Did the app exit?", job["session_id"]))
        history = self.model.calls[-1][1]
        self.assertTrue(any("close_requested" in m["content"] for m in history))
        with self.assertRaises(ValueError): self.chat.validate_session("invalid")


class ChatHttpTests(unittest.TestCase):
    setUp = ChatTests.setUp
    def test_shutdown_requires_origin_token_and_explicit_confirmation(self):
        server = DashboardServer(0, self.collector, self.config, self.store, demo=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        port = server.server_port
        def shutdown(body, token=server.token, origin=None):
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            connection.request("POST", "/api/shutdown", json.dumps(body),
                {"Content-Type":"application/json", "Origin": origin or f"http://127.0.0.1:{port}",
                 "X-Kshetrajna-Token": token})
            response = connection.getresponse()
            response.read()
            result = response.status
            connection.close()
            return result
        self.assertEqual(shutdown({"confirm":True}, token="wrong"), 403)
        self.assertEqual(shutdown({"confirm":True}, origin="https://untrusted.example"), 403)
        self.assertEqual(shutdown({}), 400)
        self.assertEqual(shutdown({"confirm":"yes"}), 400)
        self.assertFalse(server.shutdown_requested)
        self.assertEqual(shutdown({"confirm":True}), 200)
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertTrue(server.shutdown_requested)

    def test_http_chat_guards_assets_and_approval_boundary(self):
        server = DashboardServer(0, self.collector, self.config, self.store, demo=True)
        server.chat.client = self.model
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        port = server.server_port
        def call(path, body, *, authorized=True):
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            headers={"Content-Type":"application/json", "Origin":f"http://127.0.0.1:{port}"}
            if authorized: headers["X-Kshetrajna-Token"] = server.token
            connection.request("POST", path, json.dumps(body), headers)
            response = connection.getresponse()
            data = json.loads(response.read())
            status = response.status
            connection.close()
            return status, data
        self.assertEqual(call("/api/chat/message", {"message":"hello"}, authorized=False)[0], 403)
        status, data = call("/api/chat/message", {"message":"free ram"})
        self.assertEqual(status, 200)
        self.assertTrue(data["actions"])
        self.assertEqual(self.store.decisions(), [])
        self.assertEqual(call("/api/chat/job", {"id":data["id"], "session_id":"wrong"})[0], 400)
        self.assertEqual(call("/api/processes/execute", {"id":"missing","confirm":True})[0], 400)
        self.assertEqual(call("/api/chat/clear", {"session_id":data["session_id"]})[0], 200)
        connection=http.client.HTTPConnection("127.0.0.1",port)
        connection.request("GET","/chat.js")
        response=connection.getresponse()
        self.assertEqual(response.status,200)
        self.assertIn(b"reviewChatAction",response.read())
        connection.close()
