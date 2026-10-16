import json
import unittest
from datetime import datetime, timedelta, timezone

from kshetrajna.health import summarize_health
from kshetrajna.intelligence import analyze
from kshetrajna.models import Snapshot


NOW = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)
GIB = 1024**3


def reading(**changes):
    result = {
        "observed_at": NOW.isoformat(), "cpu_percent": 10,
        "memory_total_bytes": 16 * GIB, "memory_available_bytes": 8 * GIB,
        "memory_commit_total_bytes": 10 * GIB, "memory_commit_limit_bytes": 32 * GIB,
        "memory_system_cache_bytes": 6 * GIB, "memory_low_signal": False,
        "disk_total_bytes": 500 * GIB, "disk_free_bytes": 100 * GIB,
    }
    return {**result, **changes}


class HealthTests(unittest.TestCase):
    def test_cpu_pressure_does_not_diagnose_memory_pressure(self):
        result = summarize_health(reading(cpu_percent=95), now=NOW)
        self.assertEqual(result["cpu"]["status"], "high")
        self.assertEqual(result["physical"]["status"], "normal")
        self.assertEqual(result["commit"]["status"], "normal")
        self.assertEqual(result["os_low_memory"]["status"], "normal")
        self.assertEqual(result["memory_pressure"], "normal")

    def test_commit_and_os_signals_are_independent_of_physical_use(self):
        result = summarize_health(reading(memory_commit_total_bytes=31 * GIB), now=NOW)
        self.assertEqual(result["commit"]["status"], "high")
        self.assertEqual(result["physical"]["status"], "normal")
        self.assertEqual(result["cpu"]["status"], "normal")
        self.assertEqual(result["memory_pressure"], "high")
        result = summarize_health(reading(memory_low_signal=True), now=NOW)
        self.assertEqual(result["os_low_memory"]["signal"], True)
        self.assertEqual(result["memory_pressure"], "high")

    def test_physical_pressure_and_cache_overlap_are_not_added(self):
        result = summarize_health(reading(memory_available_bytes=GIB), now=NOW)
        self.assertEqual(result["physical"]["used_percent"], 93.75)
        self.assertEqual(result["physical"]["available_bytes"], GIB)
        self.assertEqual(result["physical"]["total_bytes"], 16 * GIB)
        self.assertEqual(result["system_cache"]["bytes"], 6 * GIB)
        self.assertTrue(result["system_cache"]["overlaps_available"])
        self.assertEqual(result["memory_pressure"], "high")
        self.assertNotIn("speedup", json.dumps(result).lower())
        self.assertIn("standby", result["system_cache"]["note"].lower())

    def test_missing_counters_are_unknown_not_zero_or_healthy(self):
        result = summarize_health(reading(memory_commit_total_bytes=None,
                                          memory_commit_limit_bytes=None,
                                          memory_system_cache_bytes=None,
                                          memory_low_signal=None), now=NOW)
        self.assertIsNone(result["commit"]["used_percent"])
        self.assertIsNone(result["system_cache"]["bytes"])
        self.assertIsNone(result["os_low_memory"]["signal"])
        self.assertEqual(result["commit"]["status"], "unknown")
        self.assertEqual(result["memory_pressure"], "unknown")

    def test_missing_stale_invalid_and_future_timestamps_cannot_diagnose(self):
        samples = [None, reading(observed_at=None), reading(observed_at="invalid"),
                   reading(observed_at=NOW.replace(tzinfo=None).isoformat()),
                   reading(observed_at=(NOW + timedelta(minutes=2)).isoformat()),
                   reading(observed_at=(NOW - timedelta(seconds=31)).isoformat())]
        for sample in samples:
            with self.subTest(sample=sample):
                result = summarize_health(sample, now=NOW)
                self.assertNotEqual(result["freshness"], "fresh")
                for key in ("cpu", "physical", "commit", "os_low_memory", "disk"):
                    self.assertEqual(result[key]["status"], "unknown")
                self.assertEqual(result["memory_pressure"], "unknown")
        stale = summarize_health(samples[-1], now=NOW)
        self.assertEqual(stale["freshness"], "stale")
        self.assertEqual(stale["age_seconds"], 31)
        self.assertEqual(stale["physical"]["available_bytes"], 8 * GIB)

    def test_invalid_values_are_not_diagnosed_as_real_counters(self):
        result = summarize_health(reading(cpu_percent=float("nan"),
                                          memory_available_bytes=17 * GIB,
                                          memory_commit_limit_bytes=0,
                                          memory_low_signal=1,
                                          disk_free_bytes=-1), now=NOW)
        for key in ("cpu", "physical", "commit", "os_low_memory", "disk"):
            self.assertEqual(result[key]["status"], "unknown")
        json.dumps(result, allow_nan=False)

    def test_zero_is_valid_only_when_actually_observed(self):
        result = summarize_health(reading(memory_available_bytes=0,
                                          memory_commit_total_bytes=0,
                                          memory_system_cache_bytes=0,
                                          disk_free_bytes=0), now=NOW)
        self.assertEqual(result["physical"]["status"], "high")
        self.assertEqual(result["commit"]["used_percent"], 0)
        self.assertEqual(result["system_cache"]["bytes"], 0)
        self.assertIs(result["os_low_memory"]["signal"], False)
        self.assertEqual(result["disk"]["status"], "high")

    def test_snapshot_input_and_freshness_boundary_are_supported_without_mutation(self):
        sample = Snapshot(NOW.isoformat(), 10, 16 * GIB, 8 * GIB, None, None, None)
        before = sample.to_dict()
        result = summarize_health(sample, now=NOW + timedelta(seconds=30))
        self.assertEqual(result["freshness"], "fresh")
        self.assertEqual(result["observed_at"], NOW.isoformat())
        self.assertEqual(result["evaluated_at"], (NOW + timedelta(seconds=30)).isoformat())
        self.assertEqual(sample.to_dict(), before)

    def test_intelligence_retains_legacy_keys_and_exposes_separate_health(self):
        latest = reading(cpu_percent=95)
        model = analyze([], latest, [])
        self.assertEqual(model["pressure"], "High")
        self.assertIn("health", model)
        self.assertIn("physical", model["health"])
        self.assertIn("prediction_quality", model)
        empty = analyze([], None, [])
        self.assertEqual(empty["pressure"], "Unknown")
        self.assertEqual(empty["health"]["freshness"], "unknown")


if __name__ == "__main__":
    unittest.main()
