"""Closed action vocabulary, not executors or an authorization mechanism.

Visibility never grants permission. Adapters must recheck opt-ins, exact target
identity and domain preflight at execution. ``approved_roots`` denotes existing
per-root grants, not a new global setting. ``allow_permanent_delete`` is a
reserved, default-denied danger opt-in; this module does not enable it. Recovery
remains subject to domain safety checks, not a revoked grant for new work.
"""

from dataclasses import asdict, dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class Capability:
    kind: str
    risk: str
    visibility: str
    optin_setting: str | None
    recovery: str

    def as_dict(self) -> dict:
        return asdict(self)


CAPABILITIES = MappingProxyType({capability.kind: capability for capability in (
    Capability("file.move", "moderate", "basic", "approved_roots", "domain_journal"),
    Capability("file.rename", "moderate", "basic", "approved_roots", "domain_journal"),
    Capability("file.quarantine", "moderate", "basic", "approved_roots", "domain_journal"),
    Capability("file.restore", "moderate", "basic", None, "domain_journal"),
    Capability("file.delete_permanent", "danger", "expert", "allow_permanent_delete", "none"),
    Capability("system.priority_trial", "moderate", "expert", "allow_priority_changes", "restore_priority"),
    Capability("system.memory_trim", "high", "expert", "allow_maintenance", "none"),
    Capability("system.process_close", "high", "expert", "allow_process_control", "none"),
    Capability("system.process_terminate", "danger", "expert", "allow_process_control", "none"),
    Capability("system.temp_cleanup", "high", "expert", "allow_maintenance", "none"),
    Capability("system.external_tool", "high", "expert", "allow_maintenance", "none"),
    Capability("system.model_unload", "moderate", "basic", "allow_local_ai", "none"),
)})


def get_capability(kind: str) -> Capability:
    if type(kind) is not str or kind not in CAPABILITIES:
        raise ValueError("Unknown action capability")
    return CAPABILITIES[kind]
