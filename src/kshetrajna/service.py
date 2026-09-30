"""The observe → explain → approve → evaluate → undo application workflow."""

import logging
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone

from .actions import SimulatedPriorityDriver, WindowsPriorityDriver
from .demo import SCENARIOS, seed_demo
from .intelligence import analyze, recommend
from .maintenance import MaintenanceService


class AssistantService:
    def __init__(self, collector, config, store, *, demo=False, driver=None, file_service=None):
        self.collector, self.config, self.store = collector, config, store
        self.file_service = file_service
        self.demo = demo
        self.driver = driver or (SimulatedPriorityDriver() if demo else None)
        self.active_workspace = None
        self._clock = time.time
        self.maintenance = MaintenanceService(collector, config, store, demo=demo)

    def _driver(self):
        if self.driver is None:
            self.driver = WindowsPriorityDriver()
        return self.driver

    def _model(self):
        settings = self.config.load()
        latest = self.store.latest()
        model = analyze(self.store.observations(12000), latest, [],
                        settings.idle_threshold_seconds)
        if not settings.enabled or not settings.track_foreground:
            model["prediction"] = None
            model["prediction_reason"] = "Predictions are paused while foreground collection is disabled."
        elif latest and self._clock() - datetime.fromisoformat(latest["observed_at"]).timestamp() > 90:
            model["prediction"] = None
            model["prediction_reason"] = "Waiting for a fresh foreground observation."
        return latest, model

    def state(self):
        with self.collector._gate:
            latest, model = self._model()
            decisions = self.store.decisions()
            suppressed = {d.get("suggestion_id") for d in decisions
                          if d["status"] == "dismissed" or d.get("feedback") == "not_helpful"}
            active = self.store.pending_decisions()
            proposals = [p for p in recommend(latest, model) if p["id"] not in suppressed]
            
            contextual_files = []
            if self.file_service and getattr(self.file_service, "store", None):
                from .context_files import surface_contextual_files
                active_ws_dict = None
                if self.active_workspace:
                    for ws in self.store.workspaces():
                        if ws["name"] == self.active_workspace:
                            active_ws_dict = ws
                            break
                            
                all_files = self.file_service.store.get_all_files()
                insights = self.file_service.get_insights()
                contextual_files = surface_contextual_files(
                    model.get("context", "General"),
                    model.get("rankings", []),
                    active_ws_dict,
                    model.get("workflows", []),
                    all_files,
                    insights
                )

            return {"mode": "demo" if self.demo else "live", "settings": asdict(self.config.load()),
                    "latest": latest, "history": self.store.history(), "activity": self.store.activity(),
                    "intelligence": model, "recommendations": proposals, "decisions": decisions,
                    "maintenance": self.maintenance.offers(),
                    "active_actions": active, "workspaces": self.store.workspaces(),
                    "active_workspace": self.active_workspace,
                    "contextual_files": contextual_files,
                    "health": {"last_error": self.collector.last_error,
                               "last_sample_at": self.collector.last_sample_at}}

    def approve(self, suggestion_id):
        with self.collector._gate:
            settings = self.config.load()
            if not settings.enabled:
                raise ValueError("Enable collection before approving a trial")
            if not self.demo and not settings.allow_priority_changes:
                raise ValueError("Enable live priority trials in Privacy controls first")
            if self.store.pending_decisions():
                raise ValueError("Undo or finish the active trial before starting another")
            latest, model = self._model()
            if not latest or self._clock() - datetime.fromisoformat(latest["observed_at"]).timestamp() > 30:
                raise ValueError("The sample is stale; wait for a fresh observation")
            proposals = self.state()["recommendations"]
            proposal = next((p for p in proposals if p["id"] == suggestion_id and p["kind"] == "priority"), None)
            if not proposal:
                raise ValueError("This proposal is no longer available")
            plan = self._driver().prepare(proposal["target"])
            decision = {"id": uuid.uuid4().hex, "suggestion_id": suggestion_id,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "expires_at": self._clock() + 60, "status": "prepared",
                        "mode": "demo" if self.demo else "live", "plan": plan,
                        "reason": proposal["reason"], "before": self.store.history(6),
                        "feedback": None}
            # The recovery journal is committed BEFORE any Windows write.
            self.store.save_decision(decision)
            try:
                self._driver().apply(plan)
                decision["status"] = "active"
                self.store.save_decision(decision)
            except Exception:
                self._restore(decision)
                raise
            return decision

    def _restore(self, decision):
        try:
            decision["status"] = self._driver().restore(decision["plan"])
            decision.pop("error", None)
        except Exception:
            decision["status"] = "restore_failed"
            decision["error"] = "Restore could not complete. Retry Undo or restart Kshetrajna."
            logging.getLogger(__name__).exception("Priority restore failed")
        if decision["status"] != "restore_failed":
            after = [row for row in self.store.history(90) if row["observed_at"] > decision["created_at"]]
            before = decision["before"]
            decision["evaluation"] = {"samples_before": len(before), "samples_after": len(after),
                                      "conclusion": "Insufficient samples" if len(after) < 3 else
                                      "Observed change only; other workload changes can explain it."}
            if len(after) >= 3 and before:
                average = lambda rows, key: sum(r[key] for r in rows) / len(rows)
                decision["evaluation"]["cpu_delta_points"] = round(average(after, "cpu_percent") - average(before, "cpu_percent"), 2)
                decision["evaluation"]["available_memory_delta_mb"] = round(
                    (average(after, "memory_available_bytes") - average(before, "memory_available_bytes")) / 1024**2, 1)
            decision["finished_at"] = datetime.now(timezone.utc).isoformat()
        self.store.save_decision(decision)
        return decision

    def undo(self, decision_id):
        with self.collector._gate:
            decision = next((d for d in self.store.pending_decisions() if d["id"] == decision_id), None)
            if not decision:
                raise ValueError("No active trial with that ID")
            return self._restore(decision)

    def restore_all(self):
        with self.collector._gate:
            for decision in self.store.pending_decisions():
                self._restore(decision)

    def tick(self):
        with self.collector._gate:
            settings = self.config.load()
            latest = self.store.latest()
            for decision in self.store.pending_decisions():
                regained_focus = latest and latest.get("foreground_pid") == decision["plan"]["pid"]
                if (self._clock() >= decision["expires_at"] or regained_focus or
                        not settings.enabled or (not self.demo and not settings.allow_priority_changes)):
                    self._restore(decision)

    def dismiss(self, suggestion_id):
        with self.collector._gate:
            if not any(p["id"] == suggestion_id for p in self.state()["recommendations"]):
                raise ValueError("Unknown recommendation")
            self.store.save_decision({"id": uuid.uuid4().hex, "suggestion_id": suggestion_id,
                                     "created_at": datetime.now(timezone.utc).isoformat(), "status": "dismissed",
                                     "mode": "demo" if self.demo else "live"})

    def feedback(self, decision_id, value):
        if value not in ("helpful", "not_helpful"):
            raise ValueError("Invalid feedback")
        with self.collector._gate:
            decision = next((d for d in self.store.decisions() if d["id"] == decision_id), None)
            if not decision or "plan" not in decision:
                raise ValueError("Unknown trial")
            decision["feedback"] = value
            self.store.save_decision(decision)

    def save_workspace(self, name, apps):
        with self.collector._gate:
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 50:
                raise ValueError("Workspace name must be 1–50 characters")
            known = {r["app"] for r in self._model()[1]["rankings"]}
            if not isinstance(apps, list) or not 1 <= len(apps) <= 8 or any(not isinstance(a, str) or a not in known for a in apps):
                raise ValueError("Choose 1–8 apps from the observed app list")
            self.store.save_workspace(name.strip(), list(dict.fromkeys(apps)))

    def activate_workspace(self, name):
        with self.collector._gate:
            if name is not None and not any(w["name"] == name for w in self.store.workspaces()):
                raise ValueError("Unknown workspace")
            self.active_workspace = name

    def change_scenario(self, scenario):
        if not self.demo or scenario not in SCENARIOS:
            raise ValueError("Scenario selection is only available in synthetic demo mode")
        with self.collector._gate:
            self.restore_all()
            self.store.clear()
            self.maintenance.plans.clear()
            self.active_workspace = None
            self.collector.probe.scenario = scenario
            self.collector.probe.index = 0
            seed_demo(self.store, self.collector.probe)

    def delete_history(self):
        with self.collector._gate:
            self.restore_all()
            if self.store.pending_decisions():
                raise ValueError("A restore is pending. Its recovery record must be kept until restoration completes.")
            self.collector.delete_history()
            self.maintenance.plans.clear()
            self.active_workspace = None

    def report(self):
        state = self.state()
        return {"product": "Kshetrajna", "version": "0.2.0", "mode": state["mode"],
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "intelligence": state["intelligence"], "decisions": state["decisions"],
                "workspaces": state["workspaces"],
                "measurement_note": "Synthetic demo data is not a performance benchmark. Live before/after changes do not establish causation.",
                "privacy": "Local only. No titles, URLs, keystrokes, screenshots, or document content."}
