"""Bounded, local sequence learning. Scores describe evidence, not calibrated odds."""

from collections import Counter, defaultdict
from datetime import datetime, timezone
from itertools import combinations
from math import exp, log


SESSION_GAP = 90
MIN_DWELL = 4
WINDOW_SECONDS = 300
HALF_LIFE = 4 * 3600
MIN_SUPPORT = 3
MAX_TRANSITIONS = 4000


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (TypeError, ValueError, OverflowError):
        return None


def sessions_from(observations, idle_threshold):
    """Collapse samples into visits; unknown activity, idle and gaps break chains.

    A visit needs measured dwell. A brief/unfinished visit breaks sequence learning
    instead of inventing a direct switch across the omitted application.
    """
    rows = {}
    for row in observations:
        at = timestamp(row.get("observed_at"))
        if at is not None:
            rows[at] = row
    ordered = sorted(rows.items())
    sessions, session, visit, previous_at = [], [], None, None

    def finish_visit():
        nonlocal visit, session
        if visit:
            if visit["seconds"] >= MIN_DWELL:
                session.append(visit)
            elif session:
                sessions.append(session)
                session = []
        visit = None

    def finish_session():
        nonlocal session
        finish_visit()
        if session:
            sessions.append(session)
        session = []

    for index, (at, row) in enumerate(ordered):
        app, idle = row.get("foreground_app"), row.get("idle_seconds")
        active = isinstance(app, str) and bool(app) and isinstance(idle, (int, float)) and 0 <= idle < idle_threshold
        if not active or (previous_at is not None and at - previous_at > SESSION_GAP):
            finish_session()
        previous_at = at
        if not active:
            continue
        key = app.casefold()
        if visit and visit["key"] != key:
            finish_visit()
        if visit is None:
            visit = {"key": key, "app": app, "start": at, "end": at, "seconds": 0.0}
        next_at = ordered[index + 1][0] if index + 1 < len(ordered) else at
        # No assumed dwell during pauses, sleep or after the last sample.
        elapsed = next_at - at if 0 <= next_at - at <= SESSION_GAP else 0
        visit["seconds"] += elapsed
        visit["end"] = at + elapsed
    finish_session()
    return sessions


def workflow_groups(sessions):
    """Mine pairs/triples in session-anchored windows, resistant to extra apps."""
    evidence = defaultdict(list)
    names = {v["key"]: v["app"] for session in sessions for v in session}
    windows = 0
    for session_id, session in enumerate(sessions):
        buckets = defaultdict(lambda: defaultdict(float))
        origin = session[0]["start"]
        for visit in session:
            start, end = visit["start"], visit["end"]
            while start < end:
                bucket = int((start - origin) // WINDOW_SECONDS)
                stop = min(end, origin + (bucket + 1) * WINDOW_SECONDS)
                buckets[bucket][visit["key"]] += stop - start
                start = stop
        for bucket, dwell in sorted(buckets.items()):
            # Bound combinations and exclude incidental focus flashes.
            apps = sorted(k for k, _ in sorted(dwell.items(), key=lambda x: (-x[1], x[0]))[:8] if dwell[k] >= MIN_DWELL)
            if len(apps) < 2:
                continue
            windows += 1
            for size in (2, 3):
                for group in combinations(apps, size):
                    evidence[group].append((session_id, origin + bucket * WINDOW_SECONDS))
    candidates = [(apps, seen) for apps, seen in evidence.items() if len(seen) >= 2]
    candidates.sort(key=lambda item: (-len(item[1]), -len(item[0]), item[0]))
    selected = []
    for apps, seen in candidates:
        if any(set(apps) < set(other) and len(seen) == len(other_seen) for other, other_seen in selected):
            continue
        selected.append((apps, seen))
        if len(selected) == 4:
            break
    return [{"apps": [names[k] for k in apps], "occurrences": len(seen),
             "name": "Recurring workspace", "sessions": len({s for s, _ in seen}),
             "support_percent": round(100 * len(seen) / max(1, windows)),
             "evidence": f"{len(seen)} of {windows} active five-minute windows across {len({s for s, _ in seen})} sessions"}
            for apps, seen in selected]


def transitions_from(sessions):
    result = []
    for session in sessions:
        for i in range(1, len(session)):
            result.append({"previous": session[i - 2]["key"] if i >= 2 else None,
                           "current": session[i - 1]["key"], "next": session[i]["key"],
                           # Learn a transition only when the target's dwell is observed.
                           "at": session[i]["end"], "session_start": session[0]["start"]})
    return result[-MAX_TRANSITIONS:]


class SequenceModel:
    """First/second-order counts with exponential decay and sparse-data shrinkage."""

    def __init__(self):
        self.first = defaultdict(dict)
        self.second = defaultdict(dict)

    def add(self, row):
        for table, key in ((self.first, row["current"]),
                           (self.second, (row["previous"], row["current"]))):
            if key is None or (isinstance(key, tuple) and key[0] is None):
                continue
            entry = table[key].setdefault(row["next"], {"weight": 0, "at": row["at"], "count": 0})
            entry["weight"] *= exp(-log(2) * max(0, row["at"] - entry["at"]) / HALF_LIFE)
            entry["weight"] += 1
            entry["at"] = row["at"]
            entry["count"] += 1

    def predict(self, current, previous, now, *, first_only=False):
        choices, basis = self.first.get(current, {}), "current app"
        sequence = self.second.get((previous, current), {})
        if not first_only and sum(e["count"] for e in sequence.values()) >= MIN_SUPPORT:
            choices, basis = sequence, "last two apps"
        weighted = [(app, e["weight"] * exp(-log(2) * max(0, now - e["at"]) / HALF_LIFE), e["count"])
                    for app, e in choices.items()]
        weighted.sort(key=lambda x: (-x[1], x[0]))
        total = sum(w for _, w, _ in weighted)
        if not weighted or weighted[0][2] < MIN_SUPPORT or total < 2.5:
            return None, "Learning: at least three supported switches and recent evidence are needed."
        winner, weight, count = weighted[0]
        share = weight / total
        runner = weighted[1][1] / total if len(weighted) > 1 else 0
        if share < 0.55 or share - runner < 0.15:
            return None, "Several next apps are similarly likely; no clear prediction yet."
        confidence = min(99, round(100 * weight / (total + 2)))
        return {"key": winner, "confidence": confidence, "basis": basis,
                "support": count, "total": sum(c for _, _, c in weighted),
                "alternatives": [{"key": app, "score": round(100 * w / (total + 2))}
                                 for app, w, _ in weighted[1:3]]}, ""


def evaluate(transitions):
    """Walk forward: predict before learning each switch. No random split/leakage."""
    model = SequenceModel()
    trials = predicted = correct = baseline_predicted = baseline_correct = 0
    start = max(0, len(transitions) - 200)
    for index, row in enumerate(transitions):
        guess, _ = model.predict(row["current"], row["previous"], row["at"])
        baseline, _ = model.predict(row["current"], None, row["at"], first_only=True)
        if index >= start:
            trials += 1
            if guess:
                predicted += 1
                correct += guess["key"] == row["next"]
            if baseline:
                baseline_predicted += 1
                baseline_correct += baseline["key"] == row["next"]
        model.add(row)
    return model, {"evaluated": trials, "predicted": predicted, "correct": correct,
                   "accuracy_percent": round(100 * correct / predicted) if predicted else None,
                   "coverage_percent": round(100 * predicted / trials) if trials else 0,
                   "baseline_predicted": baseline_predicted, "baseline_correct": baseline_correct,
                   "baseline_accuracy_percent": round(100 * baseline_correct / baseline_predicted) if baseline_predicted else None,
                   "method": "Chronological replay: predict each switch using only earlier completed visits."}


def learn_patterns(observations, latest, idle_threshold=60):
    sessions = sessions_from(observations, idle_threshold)
    transitions = transitions_from(sessions)
    model, quality = evaluate(transitions)
    names = {v["key"]: v["app"] for session in sessions for v in session}
    counts, sequence_sessions = Counter(), defaultdict(set)
    for session_id, session in enumerate(sessions):
        for i in range(len(session) - 2):
            path = tuple(v["key"] for v in session[i:i + 3])
            counts[path] += 1
            sequence_sessions[path].add(session_id)
    sequences = [{"apps": [names[k] for k in path], "occurrences": count,
                  "sessions": len(sequence_sessions[path])}
                 for path, count in sorted(counts.items(), key=lambda x: (-x[1], x[0]))[:4] if count >= 3]
    now = timestamp(latest.get("observed_at")) if latest else None
    current = (latest.get("foreground_app") or "").casefold() if latest else ""
    prediction, reason = None, "No active foreground app is available."
    idle = latest.get("idle_seconds") if latest else None
    if current and now is not None and idle is not None and idle < idle_threshold:
        tail = sessions[-1] if sessions else []
        if tail and tail[-1]["key"] == current and tail[-1]["end"] == now:
            previous = tail[-2]["key"] if len(tail) > 1 else None
            prediction, reason = model.predict(current, previous, now)
            if prediction:
                prediction["app"] = names[prediction.pop("key")]
                for alternative in prediction["alternatives"]:
                    alternative["app"] = names[alternative.pop("key")]
                prediction["evidence"] = (f'{prediction["support"]} of {prediction["total"]} matching switches; '
                                          f'based on {prediction["basis"]}. Recent observations count more. '
                                          'Score is smoothed evidence, not a calibrated probability.')
        else:
            reason = "Waiting for a stable foreground visit (at least four measured seconds)."
    return {"workflows": workflow_groups(sessions), "sequences": sequences,
            "prediction": prediction, "prediction_reason": reason, "prediction_quality": quality,
            "switches": len(transitions), "session_count": len(sessions),
            "history_limit": 12000, "transition_limit": MAX_TRANSITIONS}
