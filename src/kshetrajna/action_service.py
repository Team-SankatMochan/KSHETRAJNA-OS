"""Narrow canonical adapter; domain services still own OS preflight and recovery.

Startup requires exclusive mutation ownership (the launcher's InstanceLock).
Plans deliberately remain process-local: persisted evidence is not an executable
plan after restart. actions.db and events.db are NOT a crash-atomic transaction.
"""

import math
from datetime import datetime, timezone

from .action_store import digest_evidence, public_action
from .capabilities import CAPABILITIES
from .config import CONSENT_PERMISSIONS, TELEMETRY_PERMISSIONS
from .intelligence import PRIORITY_ALLOWLIST
from .maintenance import TOOLS
from .process_control import APP_ALLOWLIST, UNDO


EXTERNAL_TOOLS = ("disk_cleanup", "storage_settings", "open_temp", "store_cache")
WIRED_KINDS = ("system.memory_trim", "system.process_close", "system.process_terminate",
               "system.external_tool")
_ADAPTER = "maintenance_process_v2"
_MEMORY_COPY = {
    "effect": "One-time EmptyWorkingSet for this background process only. Its pages may be reloaded immediately, causing page faults or slower responses. This does not clear all system RAM.",
    "undo": "No automatic undo. Windows reloads pages when needed; the original resident set cannot be restored exactly.",
}
_PROCESS_EFFECTS = {
    "close": "Ask this app's windows to close normally. Save prompts may appear.",
    "terminate": "Force termination can lose unsaved work and interrupt useful background tasks.",
}


class ActionApiError(ValueError):
    def __init__(self, code, status=400):
        self.code, self.status = code, status
        super().__init__(code)


def fields(body, required, optional=()):
    if type(body) is not dict or set(body) - set(required) - set(optional) or set(required) - set(body):
        raise ActionApiError("invalid_request")


def confirmed(body):
    if body.get("confirm") is not True:
        raise ActionApiError("confirmation_required")


class ActionService:
    def __init__(self, ledger, service, processes):
        self.ledger, self.service, self.processes = ledger, service, processes
        self.collector, self.config = service.collector, service.config
        self.mode = "demo" if service.demo else "live"
        # Never replay a crash-interrupted operation or reconstruct old approvals.
        self.ledger.reconcile_interrupted()
        self.ledger.revoke_pending()

    def capabilities(self):
        items = []
        for capability in CAPABILITIES.values():
            item = capability.as_dict()
            item["api_state"] = "wired" if capability.kind in WIRED_KINDS else "deferred"
            if capability.kind == "system.external_tool":
                item["tools"] = list(EXTERNAL_TOOLS)
            if item["api_state"] == "deferred":
                item["reason"] = ("Permanent temporary-file cleanup awaits separate danger review"
                                  if capability.kind == "system.temp_cleanup" else "No canonical adapter in this slice")
            items.append(item)
        return {"items": items, "canonical_authority_complete": False,
                "legacy_routes": "domain_authoritative_compatibility",
                "persistence": "Separate action and domain journals; not cross-database atomic"}

    def _domain(self, kind):
        if kind not in WIRED_KINDS:
            if kind in CAPABILITIES:
                raise ActionApiError("capability_deferred", 409)
            raise ActionApiError("unknown_kind")
        return self.processes if kind.startswith("system.process_") else self.service.maintenance

    @staticmethod
    def _permissions(kind):
        if kind.startswith("system.process_"):
            return ("allow_process_control", *TELEMETRY_PERMISSIONS)
        if kind == "system.memory_trim":
            return ("allow_maintenance", *TELEMETRY_PERMISSIONS)
        return ("allow_maintenance",)

    def _versions(self, kind):
        return self.config.consent_versions(*self._permissions(kind))

    def permission_versions(self):
        return self.config.consent_versions(*CONSENT_PERMISSIONS)

    def permissions_changed(self, previous):
        current = self.permission_versions()
        kinds = [kind for kind in WIRED_KINDS if any(
            previous[key] != current[key] for key in self._permissions(kind))]
        if kinds:
            self.ledger.revoke_pending(kinds)

    def revoke_pending(self):
        self.ledger.revoke_pending(list(WIRED_KINDS))

    def _gate(self, kind):
        settings = self.config.load()
        for key in self._permissions(kind):
            if self.mode == "demo" and key in ("device_consent", "allow_process_control", "allow_maintenance"):
                continue
            if getattr(settings, key) is not True:
                raise ActionApiError("permission_required", 403)

    def propose(self, body):
        kind = body.get("kind")
        if type(kind) is not str:
            raise ActionApiError("invalid_request")
        domain = self._domain(kind)
        selector = "tool" if kind == "system.external_tool" else "pid"
        fields(body, ("kind", selector))
        value = body[selector]
        if selector == "pid" and (type(value) is not int or not 0 < value <= 2**32 - 1):
            raise ActionApiError("invalid_pid")
        if selector == "tool" and (type(value) is not str or value not in EXTERNAL_TOOLS):
            raise ActionApiError("invalid_tool")
        with self.collector._gate, self.config._lock:
            self._gate(kind)
            try:
                if kind.startswith("system.process_"):
                    plan = domain.preview(value, "terminate" if kind.endswith("terminate") else "close")
                else:
                    offer_id = value
                    if kind == "system.memory_trim":
                        offer_id = next((offer["id"] for offer in domain.offers()
                                         if offer["action"] == "trim_memory" and offer["target"]["pid"] == value), None)
                        if offer_id is None:
                            raise ActionApiError("target_unavailable", 409)
                    plan = domain.preview(offer_id)
            except (ValueError, OSError) as error:
                if isinstance(error, ActionApiError):
                    raise
                raise ActionApiError("preview_unavailable", 409) from None
            latest = self.service.store.latest() if selector == "pid" else None
            evidence = {"adapter": _ADAPTER, "plan": plan,
                        "permissions": self._versions(kind), "mode": self.mode,
                        "observed_at": latest["observed_at"] if latest else None}
            target = {"plan_id": plan["id"], "identity": plan.get("target", {"tool": value})}
            try:
                record = self.ledger.create(kind, target, evidence, mode=self.mode, expires_seconds=60)
            except Exception:
                domain.plans.pop(plan["id"], None)
                raise
            return self._reviewed_action(record)

    def _record(self, action_id):
        record = self.ledger.get(action_id)
        if record is None:
            raise ActionApiError("not_found", 404)
        return record

    def detail(self, action_id):
        return self._reviewed_action(self._record(action_id))

    def _reviewed_action(self, record):
        result = public_action(record)
        result["review"] = self._review(record)
        return result

    def _review(self, record):
        """Bounded display DTO, never an executable plan or a generic JSON scrubber.

        Validate the adapter schema and known domain copy before exposing text.
        A changed domain copy/schema needs adapter review, not a fallback to raw
        strings. Historical display never reads current telemetry or live plans.
        """
        unavailable = {"available": False}
        try:
            kind, evidence, target = record["kind"], record["evidence"], record["target"]
            if (record["erased_at"] is not None or kind not in WIRED_KINDS or
                    type(evidence) is not dict or evidence.get("adapter") != _ADAPTER or
                    set(evidence) != {"adapter", "plan", "permissions", "mode", "observed_at"} or
                    type(target) is not dict or record["mode"] not in ("live", "demo") or
                    evidence["mode"] != record["mode"] or digest_evidence(evidence) != record["evidence_digest"]):
                return unavailable
            permissions, plan = evidence["permissions"], evidence["plan"]
            if (type(permissions) is not dict or set(permissions) != set(self._permissions(kind)) or
                    any(type(value) is not int or not 0 <= value < 2**63 for value in permissions.values()) or
                    type(plan) is not dict or type(plan.get("id")) is not str or
                    not 1 <= len(plan["id"]) <= 200 or target.get("plan_id") != plan["id"]):
                return unavailable
            expiry = plan["expires_at"]
            if type(expiry) not in (int, float) or not math.isfinite(expiry) or not 0 < expiry < 253402300799:
                return unavailable
            plan_expires_at = datetime.fromtimestamp(expiry, timezone.utc).isoformat()
            observed_at = evidence["observed_at"]
            process, tool, observations, typed_confirmation = None, None, None, None
            if kind == "system.external_tool":
                tool = plan["action"]
                if (type(tool) is not str or tool not in EXTERNAL_TOOLS or plan.get("offer_id") != tool or
                        target.get("identity") != {"tool": tool} or observed_at is not None):
                    return unavailable
                copy = TOOLS[tool]
                caveats = ["A tool launch is not verified completion or measured space reclaimed. Windows owns its final controls."]
            else:
                identity = plan["target"]
                if type(identity) is not dict or target.get("identity") != identity:
                    return unavailable
                name, pid, ticks = identity["name"], identity["pid"], identity["created_ticks"]
                names = PRIORITY_ALLOWLIST if kind == "system.memory_trim" else APP_ALLOWLIST
                if (type(name) is not str or len(name) > 260 or name.casefold() not in names or
                        type(pid) is not int or not 0 < pid <= 2**32 - 1 or
                        type(ticks) is not int or not 0 < ticks <= 2**64 - 1 or
                        type(observed_at) is not str or len(observed_at) > 40 or
                        datetime.fromisoformat(observed_at).utcoffset() is None):
                    return unavailable
                process = {"name": name, "pid": pid, "created_ticks": ticks}
                cpu, resident = identity.get("cpu_percent"), identity.get("working_set_bytes")
                if ((cpu is not None and (type(cpu) not in (int, float) or not math.isfinite(cpu) or not 0 <= cpu <= 100)) or
                        (resident is not None and (type(resident) is not int or not 0 <= resident <= 2**63 - 1))):
                    return unavailable
                observations = {"cpu_percent": cpu, "working_set_bytes": resident}
                if kind == "system.memory_trim":
                    if plan["action"] != "trim_memory" or plan.get("offer_id") != f"memory:{pid}:{ticks}":
                        return unavailable
                    copy = {"title": f"Trim resident memory: {name} (PID {pid})", **_MEMORY_COPY}
                    caveats = ["Resident working-set bytes are not committed allocations. A trim may cause refaults or latency; it proves no speedup or lasting saving."]
                else:
                    action = "terminate" if kind == "system.process_terminate" else "close"
                    if plan["action"] != action or plan.get("mode") != record["mode"]:
                        return unavailable
                    low_cpu, seconds = identity.get("low_cpu_background"), identity.get("observed_seconds")
                    if type(low_cpu) is not bool or type(seconds) is not int or not 0 <= seconds <= 3600:
                        return unavailable
                    observations.update(low_cpu_background=low_cpu, observed_seconds=seconds)
                    copy = {"title": f"{'Force terminate' if action == 'terminate' else 'Request close'}: {name} (PID {pid})",
                            "effect": _PROCESS_EFFECTS[action], "undo": UNDO}
                    caveats = ["Low CPU or background status does not establish that an app has no useful work.",
                               "Save work first. A close request is not confirmed exit; child processes may remain."]
                    if action == "terminate":
                        typed_confirmation = {"field": "typed_name", "exact_value": name}
            if any(plan.get(field) != copy[field] for field in ("title", "effect", "undo")):
                return unavailable
            caveats.append("This is the recorded proposal snapshot, not current eligibility. The domain executor rechecks identity and eligibility before mutation.")
            if record["mode"] == "demo":
                caveats.append("Synthetic demo: no Windows modifications.")
            return {"available": True, **{field: plan[field] for field in ("title", "effect", "undo")},
                    "process": process, "tool": tool, "observations": observations,
                    "typed_confirmation": typed_confirmation, "caveats": caveats,
                    "freshness": {"observed_at": observed_at, "proposed_at": record["created_at"],
                                  "expires_at": record["expires_at"], "plan_expires_at": plan_expires_at,
                                  "max_observation_age_seconds": 30 if process else None}}
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            return unavailable

    def _bound(self, record, expected_status):
        domain = self._domain(record["kind"])
        if record["status"] != expected_status:
            raise ActionApiError(record["status"] if record["status"] in ("revoked", "expired")
                                 else "invalid_transition", 409)
        if record["mode"] != self.mode or domain.demo != (self.mode == "demo"):
            raise ActionApiError("mode_mismatch", 409)
        if not self._review(record)["available"]:
            raise ActionApiError("plan_unavailable", 409)
        self._gate(record["kind"])
        if record["evidence"].get("permissions") != self._versions(record["kind"]):
            raise ActionApiError("stale_approval", 409)
        plan = domain.plans.get(record["target"]["plan_id"])
        if plan is None or plan["expires_at"] <= domain.clock():
            raise ActionApiError("plan_unavailable", 409)
        evidence = {"adapter": _ADAPTER, "plan": plan,
                    "permissions": self._versions(record["kind"]), "mode": self.mode,
                    "observed_at": record["evidence"]["observed_at"]}
        if digest_evidence(evidence) != record["evidence_digest"]:
            raise ActionApiError("plan_changed", 409)
        return domain, plan

    def consent(self, action_id, body):
        confirmed(body)
        fields(body, ("confirm", "evidence_digest"))
        with self.collector._gate, self.config._lock:
            record = self._record(action_id)
            self._bound(record, "proposed")
            return public_action(self.ledger.consent(action_id, body["evidence_digest"], confirm=body["confirm"]))

    def execute(self, action_id, body):
        confirmed(body)
        fields(body, ("confirm", "evidence_digest", "idempotency_key"), ("typed_name",))
        if "typed_name" in body and (type(body["typed_name"]) is not str or len(body["typed_name"]) > 260):
            raise ActionApiError("invalid_typed_name")
        with self.collector._gate, self.config._lock:
            record = self._record(action_id)
            domain, plan = self._bound(record, "approved")
            if record["kind"] == "system.process_terminate" and body.get("typed_name") != plan["target"]["name"]:
                raise ActionApiError("typed_name_mismatch")
            # This is an adapter binding check, NOT a claim that OS identity passed.
            # The domain executor performs authoritative fresh identity preflight.
            self.ledger.claim(action_id, body["idempotency_key"], body["evidence_digest"],
                              preflight={"passed": True, "scope": "adapter_binding_only",
                                         "domain_identity": "executor_authoritative",
                                         "plan_digest": digest_evidence(plan)})
            try:
                kwargs = {"confirm": body["confirm"]}
                if domain is self.processes:
                    kwargs["typed_name"] = body.get("typed_name", "")
                decision = domain.execute(plan["id"], **kwargs)
                status = decision.get("status")
                succeeded = ((status == "simulated" and self.mode == "demo") or
                             (status == "terminated" and record["kind"] == "system.process_terminate") or
                             (status == "completed" and record["kind"] == "system.memory_trim"))
                outcome = {"domain_decision_id": decision["id"], "domain_status": status,
                           "result": decision.get("result")}
            except Exception:
                # Failure may follow a partial mutation or an unsuccessful journal
                # write. Never infer 'nothing happened', success, or safe replay.
                succeeded, outcome = False, {"code": "domain_outcome_unavailable"}
            finally:
                domain.plans.pop(plan["id"], None)
            return public_action(self.ledger.complete(action_id, "succeeded" if succeeded else "unknown",
                                                      outcome=outcome))

    def erase_history(self, body):
        confirmed(body)
        fields(body, ("confirm", "ids"))
        # Never clear domain journals here or infer that domain recovery finished.
        return self.ledger.erase_history(body["ids"], confirm=body["confirm"])
