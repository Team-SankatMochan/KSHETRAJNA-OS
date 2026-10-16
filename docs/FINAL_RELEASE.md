# Final-release execution contract

Status: implementation program, not a release certification. Baseline: `4d86a96`
(0.5.0). Target: 1.0.0 only after the gates below pass. Keep the runtime version
unchanged until the final candidate is actually qualified.

## Product

Kshetrajna is a local Windows PC-health and file-intelligence assistant:

**Observe → Diagnose → Explain → Approve → Act → Verify → Measure → Recover.**

Primary destinations: System Health, Recover Space, Investigate Memory, Organize
Files, History & Recovery, Privacy, and the optional Local Assistant. Basic mode
is the default. Expert mode changes visibility, not authorization or consent.

## Accepted decisions

- Add `actions.db` for the canonical action/consent/event ledger. Preserve the
  privacy separation of `events.db` and `files.db`; never imply cross-database
  writes are one crash-atomic transaction.
- Preserve existing domain executors and recovery semantics. Adapters migrate
  incrementally; no universal executor may invent rollback for irreversible work.
- Keep 0.5.0 as the implementation baseline; reserve 1.0.0 for final qualification.
- No undocumented standby-list purge, blanket RAM cleaner, registry cleaner,
  automatic process termination, or silent file mutation.
- Retain legacy permanent deletion only as a separately enabled Expert danger
  capability. Normal reviewed cleanup is quarantine, not permanent deletion.
- One local service is the mutation authority. HTTP, dashboard, CLI and assistant
  share validation and consent. Compatibility aliases cannot manufacture consent.
- No new remote provider, automatic model download, telemetry upload, global
  installation, signing identity, or account change is authorized by this plan.

## Action contract

Canonical kinds include `file.move`, `file.rename`, `file.quarantine`,
`file.restore`, `file.delete_permanent`, `system.priority_trial`,
`system.memory_trim`, `system.process_close`, `system.process_terminate`,
`system.temp_cleanup`, `system.external_tool`, and `system.model_unload`.

Each action records an opaque ID, version, kind/capability, mode, exact target,
reviewed evidence digest, expiry, risk, explicit consent, idempotency key,
preflight, execution/verification/measurement outcomes, and recovery policy.
Persist intent before mutation. Expired/used/mismatched approvals fail closed.
An uncertain result is `recovery_required` or `unknown`, never automatic success
or a reason to replay an irreversible action.

Events are append-only through the application interface. This is an audit
trail, not tamper-proof storage against the same Windows user. Queries paginate;
recovery must never use a display-history limit.

Canonical HTTP resources live under `/api/v1`. Existing routes retain their
meaning and IDs during migration, but receive explicit-confirmation hardening.
Every actuator enforces consent in its service, not just in browser dialogs.
Use stable public errors, never raw private OS exception payloads.

## Truthful outcomes

- Quarantine retains bytes, normally on the original volume. It does **not** free
  capacity. Report potential duplicate storage separately from measured free
  space; zero-byte files offer zero content-byte savings.
- Windows standby pages are already reusable memory. System-cache counters
  overlap other figures; do not add cache to available RAM or call it waste.
- Process working-set trim may cause refaults/latency and has no exact undo.
  It does not release committed allocations or prove a speedup.
- A close request is not confirmed process exit. A tool launch is not verified
  completion. A model-unload request is not measured bytes reclaimed.
- Before/after deltas are observations, not causal proof. Include freshness,
  collection gaps, concurrent workload caveats, delayed readings and unavailable
  measurements. Never fabricate zero or an improvement for missing counters.

## Privacy and safety

Telemetry, file-root access, content inspection, maintenance, app controls, and
local AI require separate consent. Revoking a capability stops new work and
invalidates pending approvals; necessary recovery remains available.

Metadata-only indexing is the final default. Content inspection is explicitly
opt-in; existing excerpt data needs a visible migration/erasure policy. Logs,
exports, database sidecars and backups are part of retention/deletion design.
The optional model receives only whitelisted context; model text, filenames and
document contents are untrusted and never authorize actions.

The main application runs without elevation. Any future privileged operation
requires a separate signed narrow broker and a new review; never elevate the
whole web service. Loopback/token/origin controls are not a sandbox against local
malware or a claim of Windows-account isolation.

Approved roots, reparse rejection, source/destination identity, no-overwrite,
bounded reads, duplicate-survivor checks, and crash recovery remain mandatory.
Native filesystem operations require Windows-specific race qualification.

## Phase gates and execution ownership

| Phase | Output | Exit gate |
|---|---|---|
| 0 | Stable quarantine/scanner/CLI baseline | Regression suites plus combined Linux/native x64 source checks; committed and pushed |
| 1 | This final contract | Decisions, risk, recovery, terminology and ownership frozen; committed and pushed |
| 2 | Action ledger and adapters | Durable intent/events, consent binding, replay rejection, migration/restart tests |
| 3 | Approval/privacy boundary | Every actuator rejects missing/false/non-boolean consent; adapters agree |
| 4 | Health observation | Physical/commit/cache/low-memory truth round-trips; native ABI and failure tests |
| 5 | Expert actions and outcomes | One verified target, explicit consent, honest immediate/delayed results |
| 6 | Storage recovery | No-overwrite, catalog reconciliation, metadata privacy, safe candidate review |
| 7 | File Organization | Fresh evidence, clear/advice/keep flows, exact approved batch and undo |
| 8 | Basic/Expert UI | Accessible navigation, dialogs, focus, no permission changes from mode switch |
| 9 | Assistant/CLI integration | Proposal-only model, one service authority, no implicit root approval |
| 10 | Reproducible distribution | Tests, Docker run, signed x64/ARM64 installation/upgrade/uninstall, provenance |
| 11 | Final qualification | Exact artifacts, real devices, browser/a11y/performance and user validation |

Implementation can be prepared in parallel after phase 1; integration and phase
promotion are sequential. The coordinator owns shared interfaces, final checks,
Git commits and one push per completed phase. Workers own bounded paths/worktrees
and must not commit or push. A rejected gate is fixed before promotion, not hidden
by marking a later phase complete. Each commit records its actual verification.

Use the installed specialized pool where applicable:

- data engineer: ledger/migrations, catalog and model parsing;
- frontend designer: existing HTML/CSS/JS and real interactions;
- test engineer: regression, integration and failure injection;
- researcher: supported Windows APIs and build/dependency decisions;
- contract auditor: routes, persistence and caller compatibility;
- security auditor: consent, authorization, redaction and package trust;
- accessibility auditor: responsive, motion, keyboard and screen-reader review;
- reviewer: connected correctness and regressions;
- docs engineer: verified contracts, runbooks and resumable handoff;
- general: bounded native/system or integration implementation.

Flutter roles/skills are not used for this Python/vanilla-JavaScript application.
Applicable workflows are orchestration, parallel execution, graph discovery,
feature/refactor, search-first, API design, security review, design/accessibility,
TDD/systematic debugging, build/test/performance, verification and handoff. Load
them when their task starts rather than adding a new stack to match a skill.

## Required evidence

- Offline `make test` semantics: pinned CPython 3.13.16, standard-library unittest.
- Linux may skip documented Windows-only checks; native Windows must run them.
- Native x64 and ARM64 use the exact candidate revision and packaged runtime.
- Browser checks: Edge/Chromium, keyboard, NVDA/Narrator, reduced motion, 320px,
  text scaling/zoom, dialog cancel/focus/error and polling-focus retention.
- Crash injection: intent, destination creation/verification, source removal,
  catalog projection, finalization and recovery. Never use personal files/apps.
- Benchmarks: declared reference hardware, idle CPU/RSS/IO, 10k/50k/200k-file
  fixtures, cancellation, delayed memory effects and ARM64 battery/thermal data.
- Packaging: clean inputs, hash-pinned tools/runtime, repeatable unsigned payloads,
  artifact-content scan, signed installer, final signature verification, SBOM and
  provenance. Timestamped signed binaries need not be byte-identical.
- Users: at least ten ordinary Windows users including keyboard users and native
  ARM64 when supported; zero accidental destructive approvals/data loss; record
  task completion, comprehension, repeated use and rejection/undo rates.

Signing credentials, protected release environments, unavailable native devices,
Docker engines and human participants are real prerequisites. Missing access
means **blocked/not run**, never a pass. Do not tag 1.0.0, create a final release,
or claim 10/10 qualification until all applicable gates have evidence.

## Current checkpoint

Phase 0 was committed and pushed as `4d86a96`. Linux CPython 3.13.16: 276 run,
274 passed, 2 Windows-only skips. Native Windows x64 CPython 3.13.3: 276 passed,
no skips. Windows's patch is not the reproducible pin. See `VALIDATION.md`.
Phases 2–11 are pending; this contract is not evidence that they are implemented.
