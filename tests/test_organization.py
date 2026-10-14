import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from kshetrajna.file_service import FileService
from kshetrajna.file_storage import FileStore
from kshetrajna.organization import matches


class OrganizationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.source = self.base / 'Downloads'
        self.target = self.base / 'Documents'
        self.source.mkdir()
        self.target.mkdir()
        (self.target / 'Aarthika').mkdir()
        (self.source / 'aarthika-pitch.pptx').write_bytes(b'pitch fixture')
        (self.source / 'aarthika-budget.csv').write_text('amount\n42\n')
        (self.source / 'holiday.jpg').write_bytes(b'image fixture')
        (self.source / 'unclear.bin').write_bytes(b'unknown')
        self.store = FileStore(self.base / 'files.db')
        self.service = FileService(self.store)
        self.src = self.service.add_root(str(self.source), 'Downloads')['id']
        self.dst = self.service.add_root(str(self.target), 'Documents')['id']
        self.wait()

    def wait(self):
        deadline = time.monotonic() + 10
        while self.service._active_scans:
            self.assertLess(time.monotonic(), deadline)
            time.sleep(.01)

    def tearDown(self):
        self.wait()
        self.temp.cleanup()

    def project(self):
        return self.service.save_project_scope('Aarthika', ['SIH finance'], self.dst, 'Aarthika', confirm=True)

    def test_project_detector_finds_existing_empty_folder_and_boundary_match(self):
        plan = self.service.generate_sort_plan(self.src, True)
        group = next(p for p in plan['report']['project_candidates'] if p['name'] == 'Aarthika')
        self.assertEqual(group['count'], 2)
        self.assertEqual(group['destinations'][0]['root_id'], self.dst)
        self.assertTrue(matches('aarthika-pitch.pptx', 'Aarthika'))
        self.assertFalse(matches('aarthika-pitch.pptx', 'art'))

    def test_project_scope_moves_across_roots_and_undo_restores_catalog(self):
        self.project()
        plan = self.service.generate_sort_plan(self.src, True)
        project_files = [s for s in plan['suggestions'] if s['project_name'] == 'Aarthika']
        self.assertEqual(len(project_files), 2)
        self.assertTrue(all(s['destination_root_id'] == self.dst for s in project_files))
        result = self.service.approve_sort_idea(plan['id'], suggestion_ids=[s['id'] for s in project_files], confirm=True)
        self.assertEqual(len(result['applied']), 2)
        for s in project_files:
            self.assertTrue((self.target / s['destination_relative']).exists())
            self.assertEqual(self.store.get_file(s['file_id'])['root_id'], self.dst)
        with self.assertRaisesRegex(ValueError, 'recovery'):
            self.service.remove_root(self.dst)
        self.assertEqual(self.service.undo_sort_plan(plan['id'])['status'], 'rolled_back')
        for s in project_files:
            self.assertTrue((self.source / s['source_relative']).exists())
            self.assertEqual(self.store.get_file(s['file_id'])['root_id'], self.src)

    def test_existing_category_folder_outside_source_is_reused(self):
        (self.target / 'Pictures').mkdir()
        plan = self.service.generate_sort_plan(self.src, True, prefer_existing=True)
        photo = next(s for s in plan['suggestions'] if s['source_relative'] == 'holiday.jpg')
        self.assertEqual(photo['destination_root_id'], self.dst)
        result = self.service.approve_sort_idea(plan['id'], suggestion_id=photo['id'], confirm=True)
        self.assertEqual(len(result['applied']), 1)
        self.assertTrue((self.target / 'Pictures' / 'holiday.jpg').exists())
        self.service.undo_sort_plan(plan['id'])

    def test_multiple_existing_folders_need_a_choice(self):
        (self.target / 'Pictures').mkdir()
        (self.source / 'Pictures').mkdir()
        plan = self.service.generate_sort_plan(self.src, True, prefer_existing=True)
        photo = next(s for s in plan['suggestions'] if s['source_relative'] == 'holiday.jpg')
        self.assertTrue(photo['review_required'])
        self.assertFalse(photo['automation_eligible'])

    def test_cross_root_collision_is_blocked_in_combined_plan(self):
        self.project()
        (self.target / 'aarthika-budget.csv').write_text('different\n123\n')
        self.service.rescan_root(self.dst)
        self.wait()
        plan = self.service.generate_sort_plan(self.src, True, root_ids=[self.src, self.dst])
        items = [s for s in plan['suggestions'] if s['destination_relative'] == 'Aarthika/aarthika-budget.csv']
        self.assertEqual(len(items), 2)
        self.assertTrue(all(s['status'] == 'blocked' for s in items))

    def test_destination_reparse_or_scan_is_rejected_and_source_kept(self):
        self.project()
        plan = self.service.generate_sort_plan(self.src, True)
        selected = next(s for s in plan['suggestions'] if s['project_name'] == 'Aarthika')
        self.store.update_root_status(self.dst, 'scanning')
        with self.assertRaisesRegex(ValueError, 'scanned'):
            self.service.approve_sort_idea(plan['id'], suggestion_id=selected['id'], confirm=True)
        self.assertTrue((self.source / selected['source_relative']).exists())

    def test_pending_project_rule_is_expired_after_remove(self):
        scope = self.project()
        plan = self.service.generate_sort_plan(self.src, True)
        self.service.remove_project_scope(scope['id'])
        self.assertEqual(self.store.get_sort_plan(plan['id'])['status'], 'expired')

    def test_scope_destinations_reject_traversal_and_unapproved_roots(self):
        for folder in ('../elsewhere', 'C:/elsewhere', 'Aarthika/../../escape'):
            with self.assertRaises(ValueError):
                self.service.save_project_scope('Aarthika', [], self.dst, folder, confirm=True)
        with self.assertRaises(ValueError):
            self.service.save_project_scope('Aarthika', [], 999, 'Aarthika', confirm=True)
        with self.assertRaises(ValueError):
            self.service.approve_locations([{'path': str(self.source), 'label': 'Downloads'}])

    def test_report_accounts_for_every_considered_file_and_preserves_project_tree(self):
        project = self.source / 'real-project'
        project.mkdir()
        (project / 'package.json').write_text('{}')
        (project / 'aarthika-ref.pdf').write_bytes(b'reference')
        self.service.rescan_root(self.src)
        self.wait()
        plan = self.service.generate_sort_plan(self.src, True, include_nested=True)
        self.assertEqual(plan['report']['considered'], plan['report']['kept'] + len(plan['suggestions']))
        self.assertEqual(plan['report']['reasons']['project_structure'], 2)
        self.assertFalse(any(s['source_relative'].startswith('real-project/') for s in plan['suggestions']))

    def test_reconcile_prepared_cross_root_rename(self):
        row = next(r for r in self.store.get_sortable_files(self.src) if r['name'] == 'aarthika-budget.csv')
        action = self.service.create_proposal(row['id'], 'MOVE', 'Aarthika/aarthika-budget.csv', self.dst)
        self.store.update_file_action(action['id'], {'status': 'prepared'})
        (self.source / row['relative_path']).rename(self.target / action['destination_relative'])
        self.service.action_manager.reconcile_actions()
        self.assertEqual(self.store.get_file_action(action_id=action['id'])['status'], 'applied')
        self.assertEqual(self.store.get_file(row['id'])['root_id'], self.dst)
        self.service.undo_action(action['id'])
        self.assertTrue((self.source / row['relative_path']).exists())

    def test_scan_many_locations_has_one_explicit_consent_and_does_not_move(self):
        locations = []
        for i in range(7):
            directory = self.base / f'collection-{i}'
            directory.mkdir()
            (directory / 'notes.txt').write_text('fixture')
            locations.append({'path': str(directory), 'label': directory.name})
        result = self.service.approve_locations(locations, consent=True)
        self.assertEqual(len(result['approved']), 7)
        self.assertEqual(result['skipped'], [])
        self.wait()
        self.assertTrue(all((Path(l['path']) / 'notes.txt').exists() for l in locations))
        self.assertTrue(all(self.store.get_root(r['id'])['scan_limit'] == 200000 for r in result['approved']))
