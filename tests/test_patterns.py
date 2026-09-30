import unittest
from datetime import datetime, timedelta, timezone

from kshetrajna.patterns import (learn_patterns, sessions_from, transitions_from,
                                SequenceModel, evaluate, workflow_groups)


START = datetime(2026, 9, 30, 9, tzinfo=timezone.utc)


def activity(apps, *, start=START, step=5, dwell=10):
    rows = []
    seconds = 0
    for app in apps:
        for offset in range(0, dwell, step):
            rows.append({"observed_at": (start + timedelta(seconds=seconds + offset)).isoformat(),
                         "foreground_app": app, "idle_seconds": 0})
        seconds += dwell
    if apps:
        rows.append({"observed_at": (start + timedelta(seconds=seconds)).isoformat(),
                     "foreground_app": apps[-1], "idle_seconds": 0})
    return rows


class PatternTests(unittest.TestCase):
    def test_order_disambiguates_shared_browser(self):
        cycle = ["Editor.exe", "Browser.exe", "Terminal.exe", "Meeting.exe", "Browser.exe", "Notes.exe"]
        for prefix, expected in ((["Editor.exe", "Browser.exe"], "Terminal.exe"),
                                 (["Meeting.exe", "Browser.exe"], "Notes.exe")):
            rows = activity(cycle * 10 + prefix)
            learned = learn_patterns(rows, rows[-1])
            self.assertEqual(learned["prediction"]["app"], expected)
            self.assertEqual(learned["prediction"]["basis"], "last two apps")
            self.assertLess(learned["prediction"]["confidence"], 100)
            self.assertTrue(learned["sequences"])

    def test_idle_missing_and_long_gaps_never_create_switches(self):
        a = activity(["A.exe"])
        for middle in ({"foreground_app": "A.exe", "idle_seconds": 80},
                       {"foreground_app": None, "idle_seconds": None}):
            separator = {"observed_at": (START + timedelta(seconds=15)).isoformat(), **middle}
            rows = a + [separator] + activity(["B.exe"], start=START + timedelta(seconds=20))
            self.assertEqual(transitions_from(sessions_from(rows, 60)), [])
        rows = a + activity(["B.exe"], start=START + timedelta(hours=1))
        self.assertEqual(transitions_from(sessions_from(rows, 60)), [])

    def test_brief_focus_flash_does_not_invent_a_bridge(self):
        rows = activity(["A.exe"])
        rows += [{"observed_at": (START + timedelta(seconds=12)).isoformat(),
                  "foreground_app": "Popup.exe", "idle_seconds": 0}]
        rows += activity(["B.exe"], start=START + timedelta(seconds=13))
        self.assertEqual(transitions_from(sessions_from(rows, 60)), [])

    def test_repeated_sampling_and_case_changes_are_not_switches(self):
        rows = activity(["Code.exe", "code.EXE", "CODE.exe"])
        self.assertEqual(transitions_from(sessions_from(rows, 60)), [])
        self.assertIsNone(learn_patterns(rows, rows[-1])["prediction"])

    def test_prediction_is_stable_across_sampling_rates(self):
        outputs = []
        for step in (2, 6):
            rows = activity(["A", "B", "C"] * 8 + ["A"], dwell=12, step=step)
            learned = learn_patterns(rows, rows[-1])
            outputs.append((learned["prediction"]["app"], learned["switches"]))
        self.assertEqual(outputs[0], outputs[1])

    def test_extra_app_does_not_hide_recurring_pair(self):
        rows = activity(["A", "B"] * 7 + ["Extra"] + ["A", "B"] * 14)
        groups = workflow_groups(sessions_from(rows, 60))
        self.assertTrue(any(set(g["apps"]) == {"A", "B"} and g["occurrences"] >= 2 for g in groups))

    def test_tied_options_abstain(self):
        model = SequenceModel()
        for i in range(12):
            model.add({"previous": None, "current": "A", "next": "B" if i % 2 else "C", "at": i})
        prediction, reason = model.predict("A", None, 12)
        self.assertIsNone(prediction)
        self.assertIn("similarly", reason)

    def test_recent_changed_habit_outweighs_old_repetition(self):
        model = SequenceModel()
        for i in range(20):
            model.add({"previous": None, "current": "A", "next": "Old", "at": i})
        for i in range(6):
            model.add({"previous": None, "current": "A", "next": "New", "at": 172800 + i})
        guess, _ = model.predict("A", None, 172806)
        self.assertEqual(guess["key"], "New")
        self.assertIsNone(model.predict("A", None, 172806 + 7 * 86400)[0])

    def test_cold_start_unstable_and_idle_abstain(self):
        rows = activity(["A", "B", "A"])
        self.assertIsNone(learn_patterns(rows, rows[-1])["prediction"])
        rows = activity(["A", "B"] * 10)
        latest = {**rows[-1], "idle_seconds": 90}
        self.assertIsNone(learn_patterns(rows, latest)["prediction"])
        rows.append({"observed_at": (START + timedelta(seconds=205)).isoformat(),
                     "foreground_app": "A", "idle_seconds": 0})
        learned = learn_patterns(rows, rows[-1])
        self.assertIsNone(learned["prediction"])
        self.assertIn("stable", learned["prediction_reason"])

    def test_replay_does_not_learn_the_answer_before_predicting(self):
        rows = [{"previous": None, "current": "A", "next": "B", "at": i} for i in range(4)]
        _, quality = evaluate(rows)
        self.assertEqual(quality["evaluated"], 4)
        self.assertEqual(quality["predicted"], 1)
        self.assertEqual(quality["correct"], 1)
        self.assertEqual(quality["coverage_percent"], 25)
        _, cold = evaluate(rows[:3])
        self.assertIsNone(cold["accuracy_percent"])

    def test_replay_improves_coverage_on_ambiguous_first_order_history(self):
        rows = activity(["Editor", "Browser", "Terminal", "Meeting", "Browser", "Notes"] * 20)
        quality = learn_patterns(rows, rows[-1])["prediction_quality"]
        self.assertGreater(quality["predicted"], quality["baseline_predicted"])
        self.assertGreaterEqual(quality["accuracy_percent"], 90)

    def test_sorting_duplicate_samples_and_bad_timestamps(self):
        rows = activity(["A", "B"] * 6)
        expected = learn_patterns(rows, rows[-1])
        noisy = list(reversed(rows)) + [rows[2], {"observed_at": "bad"}]
        actual = learn_patterns(noisy, rows[-1])
        self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
