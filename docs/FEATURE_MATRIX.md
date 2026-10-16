# Feature and final-release matrix

**Product:** local Windows PC health and file intelligence, following Observe →
Diagnose → Explain → Approve → Act → Verify → Measure → Recover. **Baseline:
0.5.0.** The full destination is defined by the [release contract](FINAL_RELEASE.md);
1.0.0 is reserved until its gates pass. “Present” below means source implementation,
not production certification or completion of a final-release phase.

## Implemented baseline

| Area | Present behavior | Evidence and limitation |
|---|---|---|
| System observation / health | Opt-in CPU/physical RAM, nullable commit/cache/OS low-memory and fixed system-volume disk counters; read-only diagnoses, age/freshness; process/foreground/idle samples | Optional failures and old rows remain unavailable, not zero; stale/unknown is not a current diagnosis; cache overlaps available RAM; native qualification of new counters is pending |
| History and patterns | Pause/retention/deletion, stable visits, ordered routines, recency-aware next-app predictions and replay | Synthetic-history tests; scores are not calibrated probabilities; system-history retention does not expire file excerpts/logs |
| Priority trials | One reviewed eligible background PID, Normal → Below normal, durable intent, expiry/undo/startup restoration | Controlled drivers; restoration is target-process only, not descendant rollback; native user-app mutation is not qualified by fixture passes |
| Workspace boards | Save/activate dashboard app boards and show contextual files | Not desktop window arrangement or application launching |
| Synthetic demo | Scenarios, simulated approvals, reports and file fixtures | No personal-file/process mutation; synthetic readings are not benchmarks |
| File catalog and preview | Approved roots, conventional-folder discovery, bounded/cancellable scans, search and text/code/Office/PDF/image previews | Separate `files.db`. Scans currently **persist text/Office/PDF excerpts**; metadata-only default is not delivered. Text/Office: 4,096-byte bound; PDF: 4,096 **characters**, not 4 KiB. No OCR/semantic vision claim |
| Sort AI | Deterministic clear-match batches, advice queue, remembered keep decisions, project context, partial results and eligible batch undo | Fixture/API tests and historical disposable-file browser journeys; at most 500 automatic moves; nested collections protected; sorting never deletes. See [Sort AI](SORT_AI.md) |
| Reviewed cleanup | Empty-file and exact SHA-256 duplicate preview, quarantine and exact action-ID restore | Ready/truncated roots; structural/project/sensitive/link exclusions, 100 MiB hash limit, survivor revalidation; bytes remain stored, **not capacity recovered**. No reviewed final purge. See [cleanup](CLEANUP.md) |
| Filesystem recovery | Identity/content evidence, no-overwrite, protected quarantine, catalog/root deletion guards | Regression fixtures and source checks; complete Windows concurrent reparse/race and cross-volume qualification remain open |
| Windows maintenance | Fixed Storage/Disk Cleanup/Store/temp tools, bounded temporary-file cleanup, eligible one-process working-set trim | Separate opt-in and reviewed UI; tool launch is not completion. Temp deletion is permanent; trim has no exact undo, does not release commit, and may cause refaults |
| App controls | Reviewed close request or typed-name force termination of one verified allowlisted background PID | Controlled-driver tests; no automatic closure/termination, process-tree action or undo; request close is not proof of exit |
| Local assistant | Optional installed Ollama model, streaming/stop/new chat, review-only tools, model-unload request | Historical x64 local reply and fixture tests; bounded in-memory chat, no arbitrary execution/cloud provider/model pull; unload is not measured reclaimed bytes |
| Canonical action ledger / API | `actions.db`; v1 proposal/consent/execute, safe persisted review DTO, expiry/replay and permission-generation invalidation | **Only** memory trim, process close/terminate and four fixed external tools wired; other kinds deferred. `canonical_authority_complete:false`; legacy routes remain domain-authoritative, not cross-DB atomic. See [action contract](ACTIONS.md) |
| Local service / bridge | Loopback Host/Origin/token checks, strict service confirmations, explicit root consent, URL-mode CLI/JSONL bridge and offline directory lock | `--path`/`--known-folder` require `--consent` to approve/scan a new root; no remote credentials/provider; generation-bound irreversible plans fail after permission changes |
| Basic/Expert shell | Full destination navigation, Basic default, advanced-control visibility, dialog cancel/focus handling, History & Recovery in both modes | Mode grants no permissions; permanent DELETE also needs its separate service danger opt-in. Source/fixture coverage is not browser/NVDA qualification |
| Privacy / diagnostics | Separate system/file/canonical deletion scopes, guarded selected canonical payload erasure, local logs/reports, no automatic crash upload | Recovery/audit tombstones remain; no application DB encryption or comprehensive path-redaction/secure-erasure guarantee. Metadata-only/content migration pending. See [privacy](PRIVACY.md) and [crash reporting](CRASH_REPORTING.md) |
| Distribution | Source launchers, wheel/Docker configuration, unsigned Windows archive/setup builder; prepared final-release identity/evidence gates, MSI builder/audits and lifecycle tooling | Reported isolated release-tool suite: 26 passed including MSI Upgrade-row audit logic, **not native installation**. Historical **0.4.3** package checks do not qualify current source or new signed artifacts |

## Planned final destination and gates

These are **remaining phase gates**, not a declaration that all work is absent
or that phases 2–11 are complete. Source classifications above use `25a0de7`;
release/verification tooling was inspected separately in the prepared lanes.

| Final surface / phase | Required output | Exit evidence still needed |
|---|---|---|
| Action foundation (2–3) | Extend the implemented narrow ledger/consent adapter to deferred kinds without weakening existing domain recovery | Integrated migration/restart/failure/reconciliation qualification; three databases never claimed atomic; source tests do not qualify native execution |
| System Health / Investigate Memory (4–5) | Qualify the implemented nullable health model and one-target actions; add delayed outcome scheduling/presentation | Native ABI/failure evidence, refault/latency caveats and delayed measurements; no unsupported standby purge or blanket RAM cleaner |
| Recover Space (6) | Finish candidate/retained/measured-free-space reporting and canonical adapters while preserving domain recovery | Native no-overwrite/crash/reparse-race evidence, metadata-only default and excerpt migration/erasure policy; separate danger permission already gates permanent DELETE |
| Organize Files (7) | Fresh evidence, clear/advice/keep flows, exact approved batches and undo | End-to-end selection/expiry/conflict/cancel/recovery checks across adapters |
| Basic/Expert UI (8) | Qualify the implemented navigation/visibility/review shell in a real browser | Keyboard, NVDA/screen-reader, reduced-motion, 320px, zoom, dialog cancel/focus/error and polling-focus checks not run for the current candidate |
| Assistant / CLI (9) | One local service authority, proposal-only optional AI and whitelisted context | Adapter agreement and strict service consent; untrusted text never authorizes execution |
| Distribution (10) | Run the prepared fail-closed release framework on exact clean artifacts | Exact packaged runtime/revision tests, Docker run, real MSI install/upgrade/uninstall, integrity/content scan, signatures, SBOM/provenance; signing infrastructure is a blocker until available |
| Qualification (11) | Exact-artifact hardware, performance, accessibility and user validation | Declared reference devices, 10k/50k/200k fixtures, idle overhead/cancellation, ARM64 battery/thermal; at least ten ordinary Windows users including keyboard users and native ARM64 when supported; zero accidental destructive approvals/data loss |

The coordinator reports a new combined pinned Linux CPython 3.13.16 run:
**453 run, 450 passed, three skipped**. Last native evidence remains the
`4d86a96` baseline: 276 passed on x64 CPython 3.13.3 (not the pin); there is no
newer native qualification. These suites were not rerun in this docs lane.
The coordinator owns [actual qualification evidence](VALIDATION.md).
Pinned Windows x64, native ARM64, Docker, signing, protected release access,
hardware and human participants remain prerequisites; unavailable means
**blocked/not run**, never passed. No semantic embeddings, NPU acceleration,
automatic process termination, registry cleaner or general shell-execution
capability is delivered or implied by the final contract.
