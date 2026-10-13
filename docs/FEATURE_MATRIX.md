# Feature and phase matrix

This matrix describes the implementation in this checkout. “Verified” means
covered by automated tests or a read-only local check. Windows-only behavior is
marked separately when this Linux development session cannot execute it.

| Phase | Capability | Status | Evidence / boundary |
|---|---|---|---|
| 1 | Consent-gated CPU, physical memory, process working set/CPU, foreground and idle collection | Verified in controlled tests; native read-only checks require Windows | `telemetry.py`, `collector.py`, `tests/test_phase1.py`, `tests/test_diagnostics.py` |
| 1 | Pause, resume, stop, retention, deletion, health and structured local logging | Verified | `storage.py`, `config.py`, `collector.py` |
| 2 | Stable visits, idle/gap session boundaries, recurring groups and ordered routines | Verified with synthetic histories | `patterns.py`, `tests/test_patterns.py` |
| 2 | Context-sensitive next-app predictions, abstention, replay accuracy/coverage | Verified with chronological replay tests | Evidence scores are not calibrated probabilities |
| 3 | Evidence-based recommendations, dismissal, feedback and observed before/after evaluation | Verified | Observations do not prove causation |
| 4 | Reviewed priority trial, journal-before-write, identity checks, expiry, pause/shutdown/startup restore | Controlled-driver verified; live actuation requires Windows | Descendant priority inheritance remains outside rollback scope |
| 5 | Saved and activated dashboard app boards; contextual files | Verified | This does not arrange desktop windows, launch apps, or promise display/DPI restoration |
| 6 | Isolated synthetic demo scenarios, simulated approvals, reports and file fixtures | Verified | Demo does not scan personal folders, create processes, or mutate real files |
| 7 | Approved roots, automatic conventional-folder path discovery, bounded/cancellable incremental indexing, sensitive exclusions, keyword search, one-click text/code/Office/PDF/image previews, automatic purpose/action decisions, content profiles and contextual files | Verified | Discovery checks directory existence only; a user approval still starts each scan. Separate `files.db`; previews, document/image loading, and decisions are bounded and opt-in; no semantic image-vision claim |
| 7 | Sort AI preview, subject/project-aware explanations, cautious review flags, editable destinations, selected batch apply, partial outcomes, reviewed move/delete actions and undo for eligible moves | Verified with temporary fixtures and HTTP preview | Deterministic local evidence engine; no semantic model is claimed; delete is permanent and requires explicit confirmation |
| 7 | Reparse/traversal defense, identity checks, duplicate avoidance, recovery journal | Verified with regression fixtures; Windows reparse smoke test requires Windows | File catalog deletion blocks while file recovery records are unresolved |
| 8 | Storage settings, Disk Cleanup, Store cache, temp-folder opening | Windows implementation present; controlled launch requires Windows | Fixed verified system paths; no shell API |
| 8 | Bounded temporary-file preview and reviewed cleanup | Fixture-verified; real deletion requires explicit Windows approval | Old files only; recent, sensitive, changed, symlink/reparse and failed items are skipped |
| 8 | Working-set trim for an individually approved background process | Controlled eligibility logic; native operation requires Windows | No exact undo or speedup claim |
| 9 | Local model conversation, streaming/cancellation, review-only tool planning and approved app controls | Implemented; Windows x64 fixture and local-model verification | Existing Ollama model, no API key/cloud calls; separate consent; closure/termination has no undo |
| 9 | Semantic file embeddings / Snapdragon NPU provider | Not implemented | Core rule engine remains functional; external model runtime has its own ARM64 compatibility requirements |
| 10 | Loopback service, Host/Origin/token checks, body bounds, static asset allowlist, local audit and migration | Verified | SQLite files are not application-encrypted |
| 11 | Source launchers and ARM64/x64 setup path | Source launchers verified; installer/artifact qualification pending | Native ARM64 Python is the documented Snapdragon path |
| Integration | Local Jarvis package, loopback HTTP client, JSON-lines bridge, preview/apply/undo commands | Verified with controlled HTTP and fixture tests | Apply/undo require explicit confirmation; no arbitrary shell or remote URL support |

## Acceptance journeys

Journeys 1–10 and 12–13 are represented by the source, dashboard paths, and
tests in this repository. Journey 11 currently means saving and activating a
dashboard board, not arranging native desktop windows. Journey 14 is a
documented source-launch path rather than a signed installer. Native ARM64
qualification must be rerun on the HP Snapdragon laptop because this checkout
cannot reproduce that hardware session.
