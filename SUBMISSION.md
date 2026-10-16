# Kshetrajna — submission guide

## Start here

Double-click **run_demo.cmd**. Keep the terminal open. The dashboard opens at http://127.0.0.1:8766. Use **run.cmd** for consent-based live Windows observations instead.

The demo is deliberately labeled synthetic. It contains fifteen minutes of recurring app activity so judges can see learned counts immediately. A scenario change resets only the temporary demo session. No demo action modifies Windows.

## Project description

Kshetrajna is a privacy-first adaptive workload assistant for Windows. It observes local application activity and resource pressure, builds an explainable picture of the current workflow, and proposes changes the user can inspect and approve. Reversible priority trials have a visible journal and a route back; separate Windows maintenance tools are explicitly labeled when they have no automatic undo. Reviewed File Intelligence cleanup moves confirmed files into protected quarantine and exposes a list/restore path by exact action ID; this milestone has no permanent final-purge API. The prototype connects telemetry, local behavioral statistics, recommendations, a workspace board, consent, controlled CPU-priority trials, outcome measurement, file indexing, and file-action recovery in one offline application. It is designed around Windows on ARM compatibility using native Win32 APIs and Python without third-party runtime dependencies.

## A 90-second demonstration

1. **0:00–0:15 — Introduce the problem.** “Windows sees processes and CPU load. Kshetrajna adds a local view of how I use those apps, so resource decisions can reflect my workflow.”
2. **0:15–0:30 — Show the evidence.** Point to Development context, app importance, recurring groups, and the next-app prediction. Explain that context labels are rules and the personal patterns are observed counts. The visible numbers are synthetic for this demo.
3. **0:30–0:45 — Review a proposal.** Select Review simulated trial. Read the target, reason, tradeoff, and duration. Approve it. The decision journal now shows a simulated active trial.
4. **0:45–1:00 — Adapt the workspace board.** Name a workspace, choose apps, save it, and activate it. The app board changes. This is dashboard adaptation; it does not move desktop windows.
5. **1:00–1:15 — Close the loop.** Select Undo now, or let the 60-second trial finish. Show the journal and give Helpful or Not helpful feedback. With at least three post-approval samples, the journal shows observed metric deltas without claiming causation.
6. **1:15–1:30 — Show a new context.** Select Meeting or Creative from Demo scenario. The context and app priorities update. Finish with the privacy controls, the reviewed Sort AI panel, and Download session report.

## What makes the prototype useful

The contribution is the complete interaction: evidence → recommendation → explicit approval → bounded trial → observation → rollback → feedback, alongside approved-folder indexing → deterministic file suggestions → editable batch preview → conflict-aware mutation → undo/recovery. Reviewed cleanup adds a second guarded path: preview → explicit confirmation → protected quarantine → quarantine list → restore. Each individual mechanism already has precedents; the submission demonstrates how they can work together as a local, user-controlled Windows layer.

## Judge questions

**Is this an operating system?** It is an application layer above Windows. The OS remains responsible for scheduling and memory management.

**Where is the AI?** The first context engine is explainable weighted rules. Personalization comes from recency/frequency scores, recurring app groups, transition counts, and persisted negative feedback. It does not contain an LLM or trained deep model.

**Does it improve performance?** No causal benchmark is claimed. The prototype records observations around a trial. Controlled A/B experiments and a latency metric are needed before making a performance claim.

**What can it change?** With live opt-in and approval, it can temporarily lower one eligible background process from Normal to Below normal priority. With a separate maintenance opt-in, it can launch selected Windows cleanup tools, review/delete bounded old `%TEMP%` candidates, or request a one-process working-set trim; those actions may be irreversible and are not presented as performance guarantees. Reviewed File Intelligence cleanup is different: after preview and confirmation, the original file is moved into protected `.kshetrajna-quarantine` and the result returns `deleted:true`, `quarantined:true`, and an `action_id`. Use the quarantine list and `POST /api/files/quarantine/restore` with `{action_id,confirm:true}` to recover it. No final-purge API is exposed. The default mode observes only. Workspace boards change the dashboard UI. File Intelligence requires explicit folder-access consent before scanning. Sort AI explains deterministic folder/file ideas in plain language and requires separate confirmation before creating a folder or moving files; conflicts are blocked before mutation.

**How does rollback work?** The original state is journaled before mutation, then restored on undo/expiry, pause, or graceful shutdown. Startup retries pending records after a crash. It never overwrites a priority class changed by another actor. Descendants are outside the selected-process rollback scope.

For reviewed file cleanup, restoration is a separate recovery action: call
`GET /api/files/quarantine` with the dashboard token, review the returned
`action_id`, then call `POST /api/files/quarantine/restore` with the exact ID and
literal `confirm:true` plus the normal same-origin request headers. Unresolved
quarantine records block catalog/root deletion until resolved. This is restore,
not permanent final purge.

**What about Snapdragon X?** The code uses standard Win32 APIs and has no third-party native wheels. The supplied project brief reports a user-confirmed 61-test run on an HP Snapdragon X ARM64 laptop. The current quarantine validation is Linux x86_64, so that ARM64 claim is retained as a user-provided validation statement rather than independently reproduced here; native diagnostics and real priority actuation still require device qualification.

## Submission contents

- Source and launch scripts in this repository.
- README.md: quickstart, implemented phases, and limitations.
- docs/ARCHITECTURE.md: technology choices and contracts.
- PHASE1.md: the earlier observe-only foundation design, retained as historical context.
- tests/: executable regression tests; current results and platform limitations are recorded in docs/VALIDATION.md.
- .github/workflows/tests.yml: CI configuration for Windows and Linux unit/demo tests, with a conditional native ARM64 job when repository visibility and runner availability permit it.

The current integration is an experimental Git snapshot for later device
testing. Submit a release only after completing the device checks. Historical
pitch and description files are retained under `submission/`; they describe
the earlier prototype rather than this integration's qualification status.
