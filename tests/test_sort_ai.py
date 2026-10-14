import tempfile
import time
import unittest
import http.client
import json
import threading
from contextlib import closing
from pathlib import Path
from unittest.mock import patch, Mock

from kshetrajna.collector import Collector
from kshetrajna.config import ConfigStore
from kshetrajna.demo import DemoProbe
from kshetrajna.file_service import FileService
from kshetrajna.file_storage import FileStore
from kshetrajna.jarvis import JarvisCommandBridge, JarvisHttpClient, serve_jsonl
from kshetrajna.known_folders import discover_known_folders
from kshetrajna.server import DashboardServer
from kshetrajna.storage import EventStore


class SortAiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root_path = Path(self.temp.name) / "approved"
        self.root_path.mkdir()
        (self.root_path / "main.py").write_text("print('hello')", encoding="utf-8")
        (self.root_path / "notes.txt").write_text("meeting notes", encoding="utf-8")
        self.store = FileStore(Path(self.temp.name) / "files.db")
        self.service = FileService(self.store)
        self.root_id = self.service.add_root(str(self.root_path), "Approved")["id"]
        for _ in range(100):
            if self.store.get_root(self.root_id)["status"] != "scanning":
                break
            time.sleep(0.01)

    def tearDown(self):
        if self.store.get_roots():
            self.service.delete_all()
        self.temp.cleanup()

    def test_ready_scan_clears_active_scan_gate(self):
        self.assertNotIn(self.root_id, self.service._active_scans)

    def rescan_wait(self):
        self.service.rescan_root(self.root_id)
        deadline = time.monotonic() + 10
        while self.store.get_root(self.root_id)['status'] == 'scanning':
            self.assertLess(time.monotonic(), deadline, 'Scan never finished')
            time.sleep(.01)

    def test_auto_batch_sorts_common_downloads_and_leaves_uncertain_files(self):
        payloads = {'beach.jpg': b'image', 'holiday.jpg': b'original image', 'recording.mp4': b'video',
                    'archive.zip': b'archive', 'BrowserSetup.exe': b'installer',
                    'holiday (1).jpg': b'possible duplicate', 'unknown.bin': b'unknown',
                    'untitled.txt': b'valuable writing', 'pending.crdownload': b'partial'}
        for name, data in payloads.items():
            (self.root_path / name).write_bytes(data)
        project = self.root_path / 'my-project'
        project.mkdir()
        (project / 'pyproject.toml').write_text('[project]\nname="my-project"')
        (project / 'paper.pdf').write_bytes(b'project reference')
        self.rescan_wait()
        plan = self.service.generate_sort_plan(self.root_id, create_folders=True)
        clear = {s['source_relative'] for s in plan['suggestions'] if s['automation_eligible']}
        self.assertTrue({'beach.jpg', 'recording.mp4', 'archive.zip', 'BrowserSetup.exe'} <= clear)
        self.assertFalse({'holiday (1).jpg', 'unknown.bin', 'untitled.txt', 'pending.crdownload'} & clear)
        self.assertFalse(any(s['source_relative'].startswith('my-project/') for s in plan['suggestions']))
        with self.assertRaisesRegex(ValueError, 'confirm=true'):
            self.service.apply_confident_sort(plan['id'])
        result = self.service.apply_confident_sort(plan['id'], confirm=True)
        for directory, name in [('Pictures','beach.jpg'), ('Videos','recording.mp4'),
                                ('Archives','archive.zip'), ('Installers','BrowserSetup.exe')]:
            self.assertEqual((self.root_path / directory / name).read_bytes(), payloads[name])
        for name in ('holiday (1).jpg', 'unknown.bin', 'untitled.txt', 'pending.crdownload'):
            self.assertEqual((self.root_path / name).read_bytes(), payloads[name])
        self.assertTrue((project / 'paper.pdf').exists())
        self.assertEqual(self.service.undo_sort_plan(plan['id'])['status'], 'rolled_back')
        for name, data in payloads.items():
            self.assertEqual((self.root_path / name).read_bytes(), data)
        self.assertFalse((self.root_path / 'Pictures').exists())

    def test_exact_review_selection_does_not_move_other_files_in_same_folder(self):
        (self.root_path / 'second.txt').write_text('second file')
        self.rescan_wait()
        plan = self.service.generate_sort_plan(self.root_id, create_folders=True)
        selected = next(s for s in plan['suggestions'] if s['source_relative'] == 'notes.txt')
        result = self.service.approve_sort_idea(plan['id'], suggestion_ids=[selected['id']], confirm=True)
        self.assertEqual(len(result['applied']), 1)
        self.assertTrue((self.root_path / 'second.txt').exists())
        self.assertEqual(self.service.undo_sort_plan(plan['id'])['status'], 'rolled_back')

    def test_keep_decision_persists_until_the_indexed_file_changes(self):
        plan = self.service.generate_sort_plan(self.root_id, create_folders=True)
        notes = next(s for s in plan['suggestions'] if s['source_relative'] == 'notes.txt')
        with self.assertRaisesRegex(ValueError, 'confirm=true'):
            self.service.keep_sort_files(plan['id'], [notes['id']])
        self.service.keep_sort_files(plan['id'], [notes['id']], confirm=True)
        self.rescan_wait()
        plan = self.service.generate_sort_plan(self.root_id, create_folders=True)
        self.assertNotIn('notes.txt', [s['source_relative'] for s in plan['suggestions']])
        (self.root_path/'notes.txt').write_text('changed contents with a different size')
        self.rescan_wait()
        plan = self.service.generate_sort_plan(self.root_id, create_folders=True)
        self.assertIn('notes.txt', [s['source_relative'] for s in plan['suggestions']])

    def test_auto_batch_bounds_selection_and_excludes_review_items(self):
        plan = self.service.generate_sort_plan(self.root_id, create_folders=True)
        seed = plan['suggestions'][0]
        virtual = {**plan, 'suggestions': [{**seed, 'id':str(index), 'source_relative':f'{index}.txt',
                   'destination_relative':f'Documents/{index}.txt', 'review_required':index == 0}
                   for index in range(503)]}
        with patch.object(self.service, '_sort_plan', return_value=virtual), patch.object(
                self.service, '_clear_sort_selection'), patch.object(self.service, 'update_sort_plan') as update, patch.object(
                self.service, 'apply_sort_plan', return_value={'status':'partial'}):
            self.service.apply_confident_sort(plan['id'], confirm=True)
        selected = update.call_args.args[1]
        self.assertEqual(len(selected), 500)
        self.assertNotIn('0', [item['id'] for item in selected])

    def test_truncated_scan_sorts_only_latest_indexed_subset(self):
        for name in ('00.txt', '01.txt'):
            (self.root_path / name).write_text('complete download')
        self.rescan_wait()
        with patch('kshetrajna.file_scanner.MAX_FILES', 2):
            self.rescan_wait()
        self.assertEqual(self.store.get_root(self.root_id)['status'], 'truncated')
        plan = self.service.generate_sort_plan(self.root_id, create_folders=True)
        self.assertTrue(plan['scan_partial'])
        self.assertEqual({s['source_relative'] for s in plan['suggestions']}, {'00.txt', '01.txt'})
        self.assertEqual(len(self.service.apply_confident_sort(plan['id'], confirm=True)['applied']), 2)
        self.assertTrue((self.root_path / 'main.py').exists())
        self.assertEqual(self.service.undo_sort_plan(plan['id'])['status'], 'rolled_back')

    def test_worker_does_not_erase_immediately_requested_rescan(self):
        if self.service._scan_thread:
            self.service._scan_thread.join(timeout=5)
        self.service._scan_thread = Mock(is_alive=Mock(return_value=True))
        self.service._active_scans.add(self.root_id)
        calls = []
        def scan(root_id):
            calls.append(root_id)
            self.service._finish_scan(root_id)
            if len(calls) == 1:
                self.service.rescan_root(root_id)
        with patch.object(self.service, '_run_scan', side_effect=scan):
            self.service._scan_worker()
        self.assertEqual(calls, [self.root_id, self.root_id])
        self.assertIsNone(self.service._scan_thread)

    def test_generated_folders_are_skipped_and_explained(self):
        generated = self.root_path / 'node_modules'
        generated.mkdir()
        (generated / 'dependency.js').write_text('const x = 1')
        self.rescan_wait()
        self.assertFalse(any(s['relative_path'].startswith('node_modules/') for s in self.store.get_sortable_files(self.root_id)))
        self.assertIn('protected/generated folders', self.store.get_root(self.root_id)['error'])

    def test_renamed_source_is_not_retargeted_by_an_old_plan(self):
        plan = self.service.generate_sort_plan(self.root_id, create_folders=True)
        file = self.store.search_files('main.py')[0]
        action = self.service.create_proposal(file['id'], 'RENAME', 'changed.py')
        self.service.apply_action(action['proposal_id'])
        result = self.service.apply_confident_sort(plan['id'], confirm=True)
        failed = next(s for s in result['failed'] if 'source moved' in s['error'])
        self.assertIsNotNone(failed)
        self.assertTrue((self.root_path / 'changed.py').exists())
        self.service.undo_sort_plan(plan['id'])
        self.service.undo_action(action['id'])

    def test_preview_edit_apply_and_reverse_batch(self):
        plan = self.service.generate_sort_plan(self.root_id, create_folders=True)
        self.assertEqual(plan["status"], "preview")
        self.assertEqual(len(plan["suggestions"]), 2)
        self.assertTrue(all(s["selected"] for s in plan["suggestions"]))
        self.assertTrue(all(s["content_summary"] for s in plan["suggestions"]))
        self.assertTrue(all("supports placing it" in s["reason"] for s in plan["suggestions"]))

        first = plan["suggestions"][0]
        plan = self.service.update_sort_plan(plan["id"], [{
            "id": first["id"],
            "selected": False,
            "destination_relative": first["destination_relative"],
        }])
        result = self.service.apply_sort_plan(plan["id"])
        self.assertEqual(result["status"], "applied")
        self.assertEqual(len(result["applied"]), 1)
        self.assertEqual(len(result["failed"]), 0)
        self.assertEqual(len(list(self.root_path.rglob("*.txt"))), 1)

        undone = self.service.undo_sort_plan(plan["id"])
        self.assertEqual(undone["status"], "rolled_back")
        self.assertTrue((self.root_path / "notes.txt").exists())
        self.assertFalse((self.root_path / "Documents").exists())

    def test_conversational_folder_approval_applies_only_one_idea(self):
        plan = self.service.generate_sort_plan(self.root_id, create_folders=True)
        code = next(s for s in plan["suggestions"] if s["destination_folder"] == "Code")
        result = self.service.approve_sort_idea(plan["id"], folder="Code", confirm=True)
        self.assertEqual(result["status"], "partial")
        self.assertTrue((self.root_path / "Code" / "main.py").exists())
        self.assertTrue((self.root_path / "notes.txt").exists())
        self.assertEqual(result["plan"]["suggestions"][0]["destination_folder"], code["destination_folder"])
        self.assertEqual(self.service.undo_sort_plan(plan["id"])["status"], "rolled_back")
        self.assertTrue((self.root_path / "main.py").exists())
        self.assertFalse((self.root_path / "Code").exists())

    def test_automatic_content_decision_can_apply_its_safe_folder(self):
        file_id = self.store.search_files("main.py")[0]["id"]
        result = self.service.organize_file(file_id, confirm=True)
        self.assertIn(result["status"], ("applied", "partial"))
        self.assertTrue((self.root_path / "Code" / "main.py").exists())
        self.service.undo_sort_plan(result["plan"]["id"])
        self.assertTrue((self.root_path / "main.py").exists())

    def test_conflicts_are_visible_and_not_selected(self):
        (self.root_path / "Documents").mkdir()
        (self.root_path / "Documents" / "notes.txt").write_text("existing", encoding="utf-8")
        plan = self.service.generate_sort_plan(self.root_id, create_folders=True)
        notes = next(s for s in plan["suggestions"] if s["source_relative"] == "notes.txt")
        self.assertFalse(notes["selected"])
        self.assertEqual(notes["status"], "blocked")
        self.assertIn("already exists", notes["reason"])

    def test_catalog_deletion_keeps_unresolved_action_recoverable(self):
        file_id = self.store.search_files("main.py")[0]["id"]
        action = self.service.create_proposal(file_id, "RENAME", "renamed.py")
        with self.assertRaisesRegex(ValueError, "recovery records"):
            self.service.delete_all()
        with self.assertRaisesRegex(ValueError, "recovery records"):
            self.service.remove_root(self.root_id)
        self.service.dismiss_proposal(action["proposal_id"])
        self.service.delete_all()
        self.assertEqual(self.store.get_roots(), [])

    def test_demo_batch_never_creates_or_moves_fixture_files(self):
        demo_service = FileService(self.store, demo=True)
        plan = demo_service.generate_sort_plan(self.root_id, create_folders=True)
        result = demo_service.apply_sort_plan(plan["id"])
        self.assertEqual(result["status"], "applied")
        self.assertTrue((self.root_path / "main.py").exists())
        self.assertFalse((self.root_path / "Code").exists())
        self.assertEqual(demo_service.undo_sort_plan(plan["id"])["status"], "rolled_back")

    def test_batch_reports_partial_completion_without_overwrite(self):
        plan = self.service.generate_sort_plan(self.root_id, create_folders=True)
        edits = [{"id": suggestion["id"], "selected": True,
                  "destination_relative": "Documents/same.txt"}
                 for suggestion in plan["suggestions"]]
        plan = self.service.update_sort_plan(plan["id"], edits)
        result = self.service.apply_sort_plan(plan["id"])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(result["applied"]), 1)
        self.assertEqual(len(result["failed"]), 1)
        self.assertEqual(self.service.undo_sort_plan(plan["id"])["status"], "rolled_back")
        self.assertTrue((self.root_path / "main.py").exists())
        self.assertTrue((self.root_path / "notes.txt").exists())

    def test_jarvis_bridge_requires_explicit_apply_confirmation(self):
        bridge = JarvisCommandBridge(self.service)
        plan = bridge.execute("sort_preview", root_id=self.root_id, create_folders=True)
        with self.assertRaisesRegex(ValueError, "confirm=true"):
            bridge.execute("sort_apply", plan_id=plan["id"])
        result = bridge.execute("sort_apply", plan_id=plan["id"], confirm=True)
        self.assertEqual(result["status"], "applied")
        self.assertEqual(bridge.execute("sort_undo", plan_id=plan["id"], confirm=True)["status"], "rolled_back")

    def test_jarvis_jsonl_rejects_unknown_commands(self):
        from io import StringIO
        output = StringIO()
        serve_jsonl(JarvisCommandBridge(self.service), StringIO('{"command":"nope"}\n'), output)
        response = json.loads(output.getvalue())
        self.assertFalse(response["ok"])
        self.assertIn("Unknown Jarvis command", response["error"])

    def test_known_folder_discovery_does_not_enumerate_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory)
            (profile / "Downloads").mkdir()
            (profile / "Downloads" / "private.txt").write_text("not read", encoding="utf-8")
            with patch.dict("os.environ", {"USERPROFILE": directory}, clear=True), patch(
                    'kshetrajna.known_folders._windows_known_folders', return_value={}), patch(
                    'os.scandir', side_effect=AssertionError('Discovery must not enumerate files')):
                folders = discover_known_folders()
            self.assertEqual([(folder["id"], folder["path"]) for folder in folders],
                             [("downloads", str(profile / "Downloads"))])

    def test_redirected_downloads_and_commercial_onedrive_are_discovered(self):
        profile = Path(self.temp.name) / 'profile'
        downloads = Path(self.temp.name) / 'Redirected Downloads'
        cloud = Path(self.temp.name) / 'OneDrive - College'
        downloads.mkdir()
        (cloud / 'Desktop').mkdir(parents=True)
        with patch.dict('os.environ', {'USERPROFILE':str(profile), 'OneDriveCommercial':str(cloud)}, clear=True), patch(
                'kshetrajna.known_folders._windows_known_folders', return_value={'Downloads':str(downloads), 'Desktop':str(cloud/'Desktop')}):
            folders = discover_known_folders()
        self.assertIn({'id':'downloads', 'label':'Downloads', 'path':str(downloads)}, folders)
        self.assertEqual(sum(f['path'] == str(cloud/'Desktop') for f in folders), 1)


class SortAiHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name) / "approved"
        root.mkdir()
        (root / "sample.py").write_text("print('ok')", encoding="utf-8")
        self.files = FileService(FileStore(Path(self.temp.name) / "files.db"))
        self.root_id = self.files.add_root(str(root), "Approved")["id"]
        for _ in range(100):
            if self.files.store.get_root(self.root_id)["status"] != "scanning":
                break
            time.sleep(0.01)
        config = ConfigStore(Path(self.temp.name))
        config.update({"enabled": True})
        events = EventStore(Path(self.temp.name) / "events.db")
        collector = Collector(DemoProbe(), config, events)
        self.server = DashboardServer(0, collector, config, events, demo=True)
        self.server.file_service = self.files
        self.server.service.file_service = self.files
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        if self.files.store.get_roots():
            self.files.delete_all()
        self.temp.cleanup()

    def call(self, method, path, body=None):
        port = self.server.server_port
        with closing(http.client.HTTPConnection("127.0.0.1", port, timeout=3)) as connection:
            connection.request(method, path, json.dumps(body) if body is not None else None, {
                "Origin": f"http://127.0.0.1:{port}",
                "X-Kshetrajna-Token": self.server.token,
                "Content-Type": "application/json",
            })
            response = connection.getresponse()
            return response.status, json.loads(response.read())

    def test_preview_endpoint_returns_reviewable_plan(self):
        status, plan = self.call("POST", "/api/files/sort/preview", {"root_id": self.root_id, "create_folders": True})
        self.assertEqual(status, 200)
        self.assertEqual(plan["status"], "preview")
        self.assertTrue(plan["suggestions"])
        status, detail = self.call("GET", "/api/files/sort/plan/" + plan["id"])
        self.assertEqual(status, 200)
        self.assertEqual(detail["id"], plan["id"])
        status, _ = self.call("POST", "/api/files/sort/update", {"plan_id": plan["id"], "suggestions": "bad"})
        self.assertEqual(status, 400)

    def test_auto_endpoint_requires_confirmation_and_preserves_undo(self):
        _, plan = self.call('POST', '/api/files/sort/preview', {'root_id':self.root_id, 'create_folders':True})
        status, _ = self.call('POST', '/api/files/sort/auto', {'plan_id':plan['id']})
        self.assertEqual(status, 400)
        status, result = self.call('POST', '/api/files/sort/auto', {'plan_id':plan['id'], 'confirm':True})
        self.assertEqual(status, 200)
        self.assertEqual(len(result['applied']), 1)
        status, result = self.call('POST', '/api/files/sort/undo', {'plan_id':plan['id']})
        self.assertEqual(status, 200)
        self.assertEqual(result['status'], 'rolled_back')

    def test_root_add_requires_explicit_consent(self):
        status, body = self.call("POST", "/api/files/roots/add", {"path": "C:\\Users\\Demo\\Desktop", "label": "Desktop"})
        self.assertEqual(status, 400)
        self.assertIn("explicit consent", body["error"])

    def test_conversational_approval_endpoint_applies_one_idea(self):
        status, plan = self.call("POST", "/api/files/sort/preview", {"root_id": self.root_id, "create_folders": True})
        self.assertEqual(status, 200)
        folder = plan["suggestions"][0]["destination_folder"]
        status, result = self.call("POST", "/api/files/sort/approve-idea", {
            "plan_id": plan["id"], "folder": folder, "confirm": True,
        })
        self.assertEqual(status, 200, result)
        self.assertEqual(result["status"], "applied")
        status, undone = self.call("POST", "/api/files/sort/undo", {"plan_id": plan["id"]})
        self.assertEqual(status, 200, undone)
        self.assertEqual(undone["status"], "rolled_back")

    def test_jarvis_http_client_reuses_dashboard_token(self):
        bridge = JarvisHttpClient(f"http://127.0.0.1:{self.server.server_port}")
        result = bridge.execute("sort_preview", root_id=self.root_id, create_folders=True)
        self.assertEqual(result["status"], "preview")


if __name__ == "__main__":
    unittest.main()
