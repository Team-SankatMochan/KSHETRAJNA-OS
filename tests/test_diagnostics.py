import ctypes
import json
import sys
import unittest
from unittest.mock import patch

from kshetrajna.diagnostics import abi_checks, architecture, run_report, summary


class DeviceTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == 'win32', 'Windows ABI')
    def test_windows_structures_have_expected_layouts(self):
        self.assertEqual(abi_checks()['status'], 'passed')
        device = architecture()
        self.assertEqual(device['pointer_bits'], ctypes.sizeof(ctypes.c_void_p) * 8)
        self.assertIn(device['native_machine'], ('x64', 'ARM64', 'x86'))

    def test_emulated_python_does_not_pass_native_arm_check(self):
        device = {'pointer_bits': 64, 'native_machine': 'ARM64', 'process_machine': 'x64', 'emulated': True}
        with patch('kshetrajna.diagnostics.architecture', return_value=device), \
             patch('kshetrajna.diagnostics.pattern_benchmark', return_value={}):
            report = run_report(require_native_arm64=True)
        self.assertFalse(report['passed'])
        self.assertFalse(report['checks']['native_arm64'])

    def test_native_arm_is_recognized_without_claiming_live_test(self):
        device = {'pointer_bits': 64, 'native_machine': 'ARM64', 'process_machine': 'ARM64', 'emulated': False}
        with patch('kshetrajna.diagnostics.architecture', return_value=device), \
             patch('kshetrajna.diagnostics.pattern_benchmark', return_value={}):
            report = run_report(require_native_arm64=True)
        self.assertTrue(report['checks']['native_arm64'])
        self.assertEqual(report['live_telemetry']['status'], 'not_run')

    def test_telemetry_failure_is_not_reported_as_pass(self):
        with patch('kshetrajna.diagnostics.live_check', side_effect=OSError('private detail')), \
             patch('kshetrajna.diagnostics.pattern_benchmark', return_value={}):
            report = run_report(live=True)
        self.assertFalse(report['passed'])
        self.assertEqual(report['live_telemetry']['status'], 'failed')
        self.assertNotIn('private detail', json.dumps(report))

    def test_timing_summary_uses_nearest_rank_p95(self):
        result = summary(list(range(1, 21)))
        self.assertEqual(result, {'median_ms': 10.5, 'p95_ms': 19, 'max_ms': 20})
