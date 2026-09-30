"""Explainable local models: rules for context, counts for personal patterns."""

from collections import Counter, defaultdict
from datetime import datetime
from hashlib import sha256
from .patterns import learn_patterns


CATEGORIES = {
    "Development": ("code.exe", "devenv.exe", "pycharm64.exe", "idea64.exe", "windowsterminal.exe", "antigravity ide.exe"),
    "Meeting": ("ms-teams.exe", "teams.exe", "zoom.exe", "webex.exe"),
    "Creative": ("photoshop.exe", "figma.exe", "blender.exe", "illustrator.exe", "premiere.exe"),
    "Gaming": ("valorant-win64-shipping.exe", "cs2.exe", "minecraft.exe", "eldenring.exe"),
    "Research": ("chrome.exe", "msedge.exe", "firefox.exe", "notion.exe", "acrord32.exe"),
}
PRIORITY_ALLOWLIST = {"chrome.exe", "msedge.exe", "firefox.exe", "spotify.exe", "discord.exe", "steamwebhelper.exe"}


def category(app: str | None) -> str:
    name = (app or "").casefold()
    return next((label for label, names in CATEGORIES.items() if name in names), "General")


def analyze(observations: list[dict], latest: dict | None, events: list[dict],
            idle_threshold: int = 60) -> dict:
    if not latest:
        return {"context": "Waiting for consent", "confidence": 0, "explanation": "Enable collection or launch the demo.",
                "rankings": [], "workflows": [], "prediction": None, "sample_count": 0, "active_minutes": 0,
                "switches": 0, "pressure": "Unknown", **learn_patterns([], None, idle_threshold)}
    recent = observations[-24:]
    votes = Counter()
    for index, row in enumerate(recent):
        if row.get("foreground_app") and (row.get("idle_seconds") or 0) < idle_threshold:
            votes[category(row["foreground_app"])] += index + 1
    current_category = category(latest.get("foreground_app"))
    if latest.get("foreground_app"):
        votes[current_category] += 2 * max(1, len(recent))
    context = votes.most_common(1)[0][0] if votes else "General"
    confidence = round(100 * votes[context] / sum(votes.values())) if votes else 0
    if latest.get("idle_seconds") is not None and latest["idle_seconds"] >= idle_threshold:
        context, confidence = "Idle", 100
    explanation = ("Input has been idle beyond your threshold." if context == "Idle" else
                   f"Weighted recent foreground app categories suggest {context.lower()}. This score is a rule vote, not a calibrated probability.")
    durations, visits, last_seen = defaultdict(float), Counter(), {}
    active_seconds = 0
    for index, row in enumerate(observations):
        app = row.get("foreground_app")
        if not app or row.get("idle_seconds") is None or row["idle_seconds"] >= idle_threshold:
            continue
        timestamp = datetime.fromisoformat(row["observed_at"]).timestamp()
        next_time = datetime.fromisoformat(observations[index + 1]["observed_at"]).timestamp() if index + 1 < len(observations) else timestamp
        elapsed = max(0, min(60, next_time - timestamp))
        durations[app] += elapsed
        active_seconds += elapsed
        visits[app] += 1
        last_seen[app] = timestamp
    now = datetime.fromisoformat(latest["observed_at"]).timestamp()
    app_metrics = {}
    for process in latest.get("processes", []):
        entry = app_metrics.setdefault(process["name"], {"cpu_percent": 0, "working_set_bytes": 0})
        entry["cpu_percent"] += process["cpu_percent"]
        entry["working_set_bytes"] += process["working_set_bytes"]
    rankings = []
    for app in set(visits) | set(app_metrics):
        foreground = app == latest.get("foreground_app")
        age = max(0, now - last_seen.get(app, now - 3600))
        frequency = visits[app] / max(1, sum(visits.values()))
        score = min(100, round(50 * foreground + 30 * frequency + 20 * max(0, 1 - age / 600)))
        metrics = app_metrics.get(app, {"cpu_percent": 0, "working_set_bytes": 0})
        rankings.append({"app": app, "importance": score, "foreground": foreground,
                         "minutes": round(durations[app] / 60, 1), **metrics,
                         "reason": f"{'Foreground; ' if foreground else ''}{visits[app]} active observations; last seen {round(age)}s ago."})
    rankings.sort(key=lambda item: (-item["importance"], -item["cpu_percent"], item["app"]))
    patterns = learn_patterns(observations, latest, idle_threshold)
    memory = 100 * (1 - latest["memory_available_bytes"] / latest["memory_total_bytes"])
    pressure = "High" if latest["cpu_percent"] >= 75 or memory >= 85 else "Moderate" if latest["cpu_percent"] >= 50 or memory >= 70 else "Low"
    return {"context": context, "confidence": confidence, "explanation": explanation,
            "rankings": rankings, **patterns,
            "sample_count": len(observations), "active_minutes": round(active_seconds / 60, 1),
            "pressure": pressure}


def recommend(latest: dict | None, model: dict) -> list[dict]:
    if not latest:
        return []
    result = []
    for process in latest.get("processes", []):
        if (latest["cpu_percent"] >= 65 and process["cpu_percent"] >= 5
                and process["pid"] != latest.get("foreground_pid")
                and process["name"].casefold() in PRIORITY_ALLOWLIST and process.get("created_ticks")):
            key = f'priority:{process["pid"]}:{process["created_ticks"]}'
            result.append({"id": sha256(key.encode()).hexdigest()[:16], "kind": "priority",
                           "title": f'Try a lower CPU priority for {process["name"]}',
                           "reason": f'System CPU is {latest["cpu_percent"]:.0f}%; this background process uses {process["cpu_percent"]:.1f}% of total capacity.',
                           "effect": "Normal → Below normal for 60 seconds. Background work may finish more slowly. RAM is unchanged.",
                           "target": process, "duration_seconds": 60})
    memory = 100 * (1 - latest["memory_available_bytes"] / latest["memory_total_bytes"])
    if memory >= 85:
        result.append({"id": "memory-review", "kind": "advice", "title": "Review memory-heavy apps",
                       "reason": f"Physical memory use is {memory:.0f}%. Save work before closing any app or browser tabs.",
                       "effect": "Advisory only. Memory is never forcibly reclaimed."})
    if model.get("workflows"):
        result.append({"id": "workspace", "kind": "workspace", "title": "Save a recurring workspace",
                       "reason": "These apps repeatedly appeared together in five-minute activity windows.",
                       "effect": "Create an app board in this dashboard. You control which workspace is active."})
    return result[:4]
