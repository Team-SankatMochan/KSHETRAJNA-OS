"""Regression tests for the reviewed cleanup and sort command-line boundary."""

import json
import socket
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.client import IncompleteRead
from io import BytesIO, StringIO
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch
from urllib.error import HTTPError, URLError

from kshetrajna import cli, jarvis
from kshetrajna.collector import Collector
from kshetrajna.config import ConfigStore
from kshetrajna.demo import DemoProbe
from kshetrajna.file_service import FileService
from kshetrajna.file_storage import FileStore
from kshetrajna.server import DashboardServer
from kshetrajna.storage import EventStore


def run_main(arguments, *, wrapper=False, stdin=""):
    output, errors = StringIO(), StringIO()
    with redirect_stdout(output), redirect_stderr(errors), patch.object(
            jarvis.sys, "stdin", StringIO(stdin)):
        try:
            if wrapper:
                with patch.object(cli.sys, "argv", ["kshetrajna-sort", *arguments]):
                    status = cli.main()
            else:
                status = jarvis.main(arguments)
        except SystemExit as error:
            status = error.code
    return status, output.getvalue(), errors.getvalue()


class CliParserTests(unittest.TestCase):
    def test_help_and_bare_invocations_do_not_initialize_a_bridge(self):
        with patch.object(jarvis, "_build_local_bridge") as local, patch.object(
                jarvis, "JarvisHttpClient") as remote:
            for wrapper, arguments in (
                    (False, []), (False, ["--help"]),
                    (True, []), (True, ["sort"]), (True, ["cleanup"]),
                    (True, ["--help"]), (True, ["cleanup", "--help"]),
                    (True, ["sort_apply", "--url", "http://localhost:1", "--help"])):
                with self.subTest(wrapper=wrapper, arguments=arguments):
                    status, output, errors = run_main(arguments, wrapper=wrapper)
                    self.assertEqual(status, 0)
                    self.assertIn("usage:", output)
                    self.assertIn("cleanup", output)
                    self.assertIn("permanent", output.lower())
                    self.assertNotIn("--size-min", output)
                    self.assertEqual(errors, "")
            local.assert_not_called()
            remote.assert_not_called()

    def test_every_bridge_cleanup_alias_is_accepted_by_the_parser(self):
        bridge = Mock()
        bridge.execute.return_value = {"reviewed": True}
        groups = (
            jarvis.JarvisCommandBridge._CLEANUP_EMPTY_PREVIEW,
            jarvis.JarvisCommandBridge._CLEANUP_DUPLICATE_PREVIEW,
            jarvis.JarvisCommandBridge._CLEANUP_EMPTY_DELETE,
            jarvis.JarvisCommandBridge._CLEANUP_DUPLICATE_DELETE,
        )
        with patch.object(jarvis, "_build_local_bridge", return_value=(bridge, None)):
            for index, commands in enumerate(groups):
                for command in sorted(commands):
                    arguments = [command]
                    if index >= 2:
                        arguments += ["--file-id", "7", "--confirm"]
                    with self.subTest(command=command):
                        status, output, errors = run_main(arguments)
                        self.assertEqual(status, 0, errors)
                        self.assertEqual(json.loads(output)["command"], command)
                        self.assertEqual(bridge.execute.call_args.args, (command,))

    def test_wrapper_shorthands_defaults_and_options_before_command(self):
        bridge = Mock()
        bridge.execute.return_value = {}
        cases = (
            (["cleanup", "empty"], "empty_preview"),
            (["cleanup", "empties"], "empty_preview"),
            (["cleanup", "duplicate"], "duplicate_preview"),
            (["cleanup", "duplicates"], "duplicate_preview"),
            (["cleanup", "--root-id", "7"], "empty_preview"),
            (["sort", "preview", "--root-id", "7"], "sort_preview"),
            (["sort", "apply", "--plan-id", "reviewed-plan", "--confirm"], "sort_apply"),
            (["sort", "undo", "--plan-id", "reviewed-plan", "--confirm"], "sort_undo"),
            (["--root-id", "7"], "sort_preview"),
            (["--root-id", "7", "duplicate_preview"], "duplicate_preview"),
        )
        with patch.object(jarvis, "_build_local_bridge", return_value=(bridge, None)):
            for arguments, command in cases:
                with self.subTest(arguments=arguments):
                    status, output, errors = run_main(arguments, wrapper=True)
                    self.assertEqual(status, 0, errors)
                    self.assertEqual(json.loads(output)["command"], command)

    def test_invalid_ids_conflicting_scopes_and_removed_option_never_initialize(self):
        cases = [
            ["empty_preview", "--path", "somewhere", "--known-folder", "downloads"],
            ["empty_preview", "--root-id", "1", "--path", "somewhere"],
            ["duplicate_preview", "--size-min", "4096"],
            ["empty_delete", "--file-id", "1"],
            ["duplicate_delete", "--confirm"],
        ]
        for flag, command in (("--root-id", "empty_preview"), ("--file-id", "empty_delete")):
            for value in ("0", "-1", "1.5", "True", "not-an-id", str(2**63)):
                cases.append([command, flag, value, "--confirm"])
        with patch.object(jarvis, "_build_local_bridge") as local, patch.object(
                jarvis, "JarvisHttpClient") as remote:
            for arguments in cases:
                with self.subTest(arguments=arguments):
                    status, output, errors = run_main([*arguments, "--url", "http://localhost:1"])
                    self.assertEqual(status, 2)
                    self.assertTrue(output or errors)
            local.assert_not_called()
            remote.assert_not_called()

    def test_client_construction_errors_are_safe_json_with_exit_two(self):
        secret = "must-not-be-printed"
        urls = (
            "", "not-a-url", "https://example.invalid", "http://[broken",
            "http://127.0.0.1:invalid", "http://localhost:65536",
            "http://localhost:0", "http://localhost:",
            f"http://user:{secret}@localhost", f"http://localhost/?token={secret}",
        )
        with patch.object(jarvis, "urlopen", side_effect=AssertionError("Invalid URL reached transport")):
            for url in urls:
                with self.subTest(url=url):
                    status, output, errors = run_main(["empty_preview", "--url", url])
                    self.assertEqual(status, 2)
                    result = json.loads(output)
                    self.assertFalse(result["ok"])
                    self.assertIsInstance(result["error"], str)
                    self.assertNotIn(secret, output + errors)
                    self.assertNotIn("Traceback", output + errors)

    def test_unreachable_loopback_is_reported_without_traceback(self):
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            url = f"http://127.0.0.1:{reserved.getsockname()[1]}"
            status, output, errors = run_main(["empty_preview", "--url", url])
        self.assertEqual(status, 2)
        self.assertFalse(json.loads(output)["ok"])
        self.assertIn("dashboard", json.loads(output)["error"].lower())
        self.assertEqual(errors, "")


class BridgeValidationTests(unittest.TestCase):
    def setUp(self):
        self.service = Mock()
        self.local = jarvis.JarvisCommandBridge(self.service)
        with patch.object(jarvis.JarvisHttpClient, "_load_token", return_value="test-session-token"):
            self.remote = jarvis.JarvisHttpClient("http://localhost:8765")
        self.remote._get = Mock(return_value={})
        self.remote._post = Mock(return_value={})

    def test_cleanup_deletion_requires_typed_id_and_literal_true_before_dispatch(self):
        commands = (jarvis.JarvisCommandBridge._CLEANUP_EMPTY_DELETE
                    | jarvis.JarvisCommandBridge._CLEANUP_DUPLICATE_DELETE)
        invalid = [dict(file_id=value, confirm=True) for value in (None, True, False, "1", 1.0, 0, -1, 2**63)]
        invalid += [dict(file_id=1, confirm=value) for value in (None, False, "true", "false", 1, 1.0, [])]
        for bridge in (self.local, self.remote):
            for command in sorted(commands):
                for arguments in invalid:
                    with self.subTest(bridge=type(bridge).__name__, command=command, arguments=arguments):
                        with self.assertRaises(ValueError):
                            bridge.execute(command, **arguments)
        self.assertEqual(self.service.mock_calls, [])
        self.remote._get.assert_not_called()
        self.remote._post.assert_not_called()

    def test_invalid_scopes_and_size_min_are_rejected_before_read_or_approval(self):
        invalid = [{"root_id": value} for value in (True, False, "1", 1.0, 0, -1, 2**63)]
        invalid += [
            {"path": "folder", "known_folder": "downloads"},
            {"root_id": 1, "path": "folder"},
            {"known_folder": 1}, {"path": 1}, {"size_min": 4096},
        ]
        for bridge in (self.local, self.remote):
            for command in ("empty_preview", "duplicate_preview", "sort_preview"):
                for arguments in invalid:
                    with self.subTest(bridge=type(bridge).__name__, command=command, arguments=arguments):
                        with self.assertRaises(ValueError):
                            bridge.execute(command, **arguments)
        self.assertEqual(self.service.mock_calls, [])
        self.remote._get.assert_not_called()
        self.remote._post.assert_not_called()

    def test_duplicate_http_preview_uses_only_supported_query_parameters(self):
        self.remote.execute("duplicates_preview", root_id=7)
        self.remote._get.assert_called_once_with("/api/files/duplicates?root_id=7")
        self.remote._get.reset_mock()
        self.remote.execute("cleanup_duplicates")
        self.remote._get.assert_called_once_with("/api/files/duplicates")


class CliTransportTests(unittest.TestCase):
    def test_local_initialization_errors_are_also_caught_without_private_details(self):
        with patch.object(jarvis, "_build_local_bridge", side_effect=OSError("private initialization detail")):
            status, output, errors = run_main(["empty_preview"])
        self.assertEqual(status, 2)
        self.assertFalse(json.loads(output)["ok"])
        self.assertNotIn("private initialization detail", output + errors)

    def test_initialization_transport_failures_never_expose_raw_errors(self):
        for error in (URLError("private transport detail"), IncompleteRead(b"private transport detail")):
            with self.subTest(error=type(error).__name__), patch.object(jarvis, "urlopen", side_effect=error):
                status, output, errors = run_main(["empty_preview", "--url", "http://localhost:8765"])
                self.assertEqual(status, 2)
                self.assertFalse(json.loads(output)["ok"])
                self.assertNotIn("private transport detail", output + errors)

    def test_missing_or_unreadable_dashboard_token_is_a_safe_json_error(self):
        for body in (b"<html>private page without a token</html>", b"\xffprivate page"):
            with self.subTest(body=body):
                response = MagicMock()
                response.__enter__.return_value = response
                response.read.return_value = body
                with patch.object(jarvis, "urlopen", return_value=response):
                    status, output, errors = run_main(["empty_preview", "--url", "http://localhost:8765"])
                self.assertEqual(status, 2)
                self.assertFalse(json.loads(output)["ok"])
                self.assertNotIn("private page", output + errors)

    def test_api_errors_keep_actionable_messages_but_redact_the_session_token(self):
        token = "test-session-token"
        body = json.dumps({"error": f"Rescan before deleting; session {token}"}).encode("utf-8")
        failure = HTTPError("http://localhost:8765", 400, "Bad Request", {}, BytesIO(body))
        with patch.object(jarvis.JarvisHttpClient, "_load_token", return_value=token), patch.object(
                jarvis, "urlopen", side_effect=failure):
            status, output, errors = run_main(["empty_delete", "--file-id", "7", "--confirm", "--url", "http://localhost:8765"])
        self.assertEqual(status, 2)
        self.assertIn("Rescan before deleting", json.loads(output)["error"])
        self.assertNotIn(token, output + errors)

    def test_malformed_api_responses_are_safe_json_for_get_and_post(self):
        for command, extra in (("empty_preview", []), ("empty_delete", ["--file-id", "7", "--confirm"])):
            for http_error in (False, True):
                bodies = [b"not JSON private-body", b"[]", b"null"]
                if http_error:
                    bodies.append(b'{"error": {"private-body": 1}}')
                for body in bodies:
                    with self.subTest(command=command, body=body, http_error=http_error):
                        response = MagicMock()
                        response.__enter__.return_value = response
                        response.read.return_value = body
                        failure = HTTPError("http://localhost:8765", 400, "private-body", {}, BytesIO(body))
                        with patch.object(jarvis.JarvisHttpClient, "_load_token", return_value="test-session-token"), patch.object(
                                jarvis, "urlopen", side_effect=failure if http_error else None,
                                return_value=response):
                            status, output, errors = run_main([command, *extra, "--url", "http://localhost:8765"])
                        self.assertEqual(status, 2)
                        self.assertFalse(json.loads(output)["ok"])
                        self.assertNotIn("private-body", output + errors)


class CliLocalServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name) / "data"
        self.data.mkdir()
        self.root = Path(self.temp.name) / "approved"
        self.root.mkdir()
        self.empty = self.root / "empty.txt"
        self.empty.touch()
        (self.root / "one.bin").write_bytes(b"matching ordinary files")
        (self.root / "two.bin").write_bytes(b"matching ordinary files")
        (self.root / "notes.txt").write_text("ordinary meeting notes", encoding="utf-8")
        (self.root / ".env").write_text("PRIVATE_TEST_VALUE=not-for-output", encoding="utf-8")
        self.files = FileService(FileStore(self.data / "files.db"))
        self.addCleanup(self._clear_service)
        self.root_id = self.files.add_root(str(self.root), "Approved")['id']
        deadline = time.monotonic() + 5
        while self.files.store.get_root(self.root_id)["status"] in ("pending", "scanning"):
            self.assertLess(time.monotonic(), deadline, "Temporary scan did not finish")
            time.sleep(0.01)

    def _clear_service(self):
        if self.files._scan_thread:
            self.files._scan_thread.join(timeout=5)
        if self.files.store.get_roots():
            self.files.delete_all()

    def invoke(self, arguments, *, url=None, wrapper=False, stdin=""):
        target = ["--url", url] if url else ["--data-dir", str(self.data)]
        status, output, errors = run_main([*arguments, *target], wrapper=wrapper, stdin=stdin)
        self.assertEqual(errors, "")
        self.assertNotIn("PRIVATE_TEST_VALUE", output)
        self.assertNotIn("not-for-output", output)
        return status, json.loads(output)

    def test_local_previews_persist_and_only_explicit_confirmed_deletes_mutate(self):
        status, preview = self.invoke(["cleanup", "empty", "--root-id", str(self.root_id), "--confirm"], wrapper=True)
        self.assertEqual(status, 0)
        self.assertTrue(self.empty.exists())
        file_id = preview["result"]["empty_files"][0]["id"]
        self.assertEqual(self.invoke(["empty_delete", "--file-id", str(file_id)])[0], 2)
        self.assertTrue(self.empty.exists())
        status, result = self.invoke(["cleanup_empty_delete", "--file-id", str(file_id), "--confirm"])
        self.assertEqual(status, 0)
        self.assertTrue(result["result"]["deleted"])
        self.assertFalse(self.empty.exists())

        status, preview = self.invoke(["duplicates_preview", "--root-id", str(self.root_id)])
        self.assertEqual(status, 0)
        group = preview["result"]["groups"][0]
        victim = group["files"][0]
        status, _ = self.invoke(["duplicates_delete", "--file-id", str(victim["id"]), "--confirm"])
        self.assertEqual(status, 0)
        self.assertFalse((self.root / victim["name"]).exists())
        survivor = group["files"][1]
        self.assertTrue((self.root / survivor["name"]).exists())
        self.assertEqual(self.invoke(["duplicate_delete", "--file-id", str(survivor["id"]), "--confirm"])[0], 2)
        self.assertTrue((self.root / survivor["name"]).exists())
        self.assertEqual(len(self.files.get_action_journal()), 2)

    def test_jsonl_requires_explicit_mode_and_validates_each_command(self):
        file_id = self.files.get_empty_files(self.root_id)["empty_files"][0]["id"]
        commands = [
            {"command": "empty_delete", "file_id": file_id, "confirm": "true"},
            {"command": "empty_delete", "file_id": True, "confirm": True},
            {"command": "duplicate_preview", "size_min": 100},
            {"command": "empty_preview", "root_id": self.root_id},
        ]
        status, output, errors = run_main(
            ["--jsonl", "--data-dir", str(self.data)],
            stdin="invalid JSON\n" + "\n".join(json.dumps(command) for command in commands) + "\n")
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        results = [json.loads(line) for line in output.splitlines()]
        self.assertEqual([result["ok"] for result in results], [False, False, False, False, True])
        self.assertTrue(self.empty.exists())

    def test_sort_preview_apply_and_undo_remain_explicit(self):
        original = {file.name: file.read_bytes() for file in self.root.iterdir()}
        status, preview = self.invoke(["sort", "--root-id", str(self.root_id), "--create-folders"], wrapper=True)
        self.assertEqual(status, 0)
        self.assertEqual({file.name: file.read_bytes() for file in self.root.iterdir()}, original)
        plan_id = preview["result"]["id"]
        self.assertEqual(self.invoke(["sort_apply", "--plan-id", plan_id])[0], 2)
        self.assertEqual(self.invoke(["sort_apply", "--plan-id", plan_id, "--confirm"])[0], 0)
        self.assertEqual(self.invoke(["sort_undo", "--plan-id", plan_id])[0], 2)
        self.assertEqual(self.invoke(["sort_undo", "--plan-id", plan_id, "--confirm"])[0], 0)
        self.assertEqual({file.name: file.read_bytes() for file in self.root.iterdir()}, original)

    def test_real_loopback_cli_reuses_session_and_preserves_surviving_files(self):
        config = ConfigStore(self.data)
        events = EventStore(self.data / "events.db")
        server = DashboardServer(0, Collector(DemoProbe(), config, events), config, events)
        server.file_service = self.files
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}"
            status, preview = self.invoke(["cleanup", "duplicates", "--root-id", str(self.root_id)], url=url, wrapper=True)
            self.assertEqual(status, 0)
            self.assertNotIn(server.token, json.dumps(preview))
            group = preview["result"]["groups"][0]
            victim = group["files"][0]
            self.assertEqual(self.invoke(["duplicate_delete", "--file-id", str(victim["id"])], url=url)[0], 2)
            self.assertTrue((self.root / victim["name"]).exists())
            self.assertEqual(self.invoke(["cleanup_duplicate_delete", "--file-id", str(victim["id"]), "--confirm"], url=url)[0], 0)
            self.assertTrue((self.root / group["files"][1]["name"]).exists())
            self.assertTrue(self.empty.exists())
        finally:
            server.shutdown()
            thread.join(timeout=3)
            server.server_close()


if __name__ == "__main__":
    unittest.main()
