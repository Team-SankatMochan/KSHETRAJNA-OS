"""Deterministic synthetic workloads, isolated from Windows and real history."""

import math
from datetime import datetime, timedelta, timezone

from .models import ProcessSample, Snapshot

SCENARIOS = {
    "Development": ("Code.exe", "chrome.exe", "WindowsTerminal.exe"),
    "Meeting": ("ms-teams.exe", "chrome.exe", "notion.exe"),
    "Creative": ("blender.exe", "chrome.exe", "figma.exe"),
    "Gaming": ("cs2.exe", "discord.exe", "steamwebhelper.exe"),
}


class DemoProbe:
    def __init__(self, scenario="Development"):
        self.scenario = scenario
        self.index = 0

    def reset(self):
        pass

    def sample(self, *, track_processes=True, track_foreground=True, max_processes=12, at=None):
        apps = SCENARIOS[self.scenario]
        self.index += 1
        # Stable three-app routine; at the fastest demo cadence each supporting
        # app remains foreground for six seconds, above the dwell filter.
        phase = self.index % 20
        active = 0 if phase < 14 else 1 if phase < 17 else 2
        # CPU is deliberately high to demonstrate a proposal. Approval does not
        # alter these figures: this is scenario data, never a claimed benchmark.
        cpu = round(82 + 5 * math.sin(self.index / 5), 2)
        processes = [ProcessSample(90001 + i, app, [19.5, 24.2, 3.1][i],
                                   [1900, 3100, 480][i] * 1024**2, 70000 + i)
                     for i, app in enumerate(apps)]
        return Snapshot((at or datetime.now(timezone.utc)).isoformat(), cpu, 16 * 1024**3,
                        int(2.1 * 1024**3), 90001 + active if track_foreground else None,
                        apps[active] if track_foreground else None, 2.0 if track_foreground else None,
                        processes[:max_processes] if track_processes else [])


def seed_demo(store, probe):
    now = datetime.now(timezone.utc)
    previous = None
    for i in range(180):
        sample = probe.sample(at=now - timedelta(seconds=(180 - i) * 5))
        events = []
        if sample.foreground_app != previous:
            events.append(("foreground_changed", sample.foreground_pid, sample.foreground_app))
        store.record(sample, events)
        previous = sample.foreground_app
