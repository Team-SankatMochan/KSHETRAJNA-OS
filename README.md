# Kshetrajna

> **The intelligence that knows the field.**

A local Windows workload assistant that observes app activity, recognizes workflows, explains resource recommendations, asks for consent, and records outcomes and rollback. This is the end-to-end hackathon prototype, version 0.2.

## Run the submission now

Install **Python 3.13**, open this folder, and double-click **run_demo.cmd**. The browser opens at [127.0.0.1:8766](http://127.0.0.1:8766/). No packages, API keys, cloud services, or build step are required.

The demo uses clearly labeled synthetic data in a temporary database. Its approval and rollback actions are simulated and cannot modify Windows. Closing the process removes its temporary session. Download the session report before closing if you want to keep it.

For actual Windows observations, run **run.cmd**, then choose **Enable collection**. Live collection and priority changes each default to off. Use Ctrl+C in the terminal for a graceful stop. Both launchers accept **--no-browser** and **--port 8767**.

Read [SUBMISSION.md](SUBMISSION.md) for a 90-second demo script and copy-ready project description. [Architecture and decisions](docs/ARCHITECTURE.md) explains the implementation. The original [project vision](docs/VISION.md) is preserved.

## What the prototype delivers

| Phase | Implemented behavior |
|---|---|
| 1 · Observe | Windows CPU/RAM, process working sets and CPU share, foreground app, idle transitions, local SQLite history, privacy controls. |
| 2 · Understand | Weighted context rules, app importance from foreground/frequency/recency, recurring app groups, next-app transition counts. |
| 3 · Recommend | Evidence and tradeoffs for eligible CPU-priority trials, memory advice, and workspace suggestions. |
| 4 · Adapt with consent | Review/approve/dismiss, optional 60-second live Normal → Below normal priority trial, saved workspace boards within the dashboard. |
| 5 · Evaluate and reverse | Durable decision journal, manual and timed undo, pause/shutdown restoration, startup recovery, observed before/after deltas, feedback that suppresses unwanted suggestions. |
| 6 · Demonstrate and verify | Four synthetic scenarios, JSON report download, launch scripts, regression tests, Windows/Linux CI definition, submission guide. |

## Important boundaries

- Context detection uses explicit app-name rules. Personal patterns use counts from recent retained observations. There is no trained LLM, NPU inference, or claim to understand document content.
- Workspace adaptation changes this dashboard's app board. It does not reposition desktop windows or modify taskbars.
- Live CPU trials are experimental and individually approved. They affect a single allowlisted, background process at Normal priority in the current session. PID and process creation time are checked before acting.
- The selected process is restored to Normal on undo or expiry (checked approximately every second), when collection is paused, when live trials are disabled, on graceful shutdown, or during startup recovery. Failed restoration remains visible and keeps its recovery record.
- If the app is forcibly killed, a priority change can persist until restart recovery or until the target exits. Newly spawned children can inherit the selected process's priority; they are outside this prototype's rollback scope.
- No application is killed, no working set is forcibly trimmed, no power plan is changed, and no elevated or real-time priority is used.
- Before/after system metrics are observations, not proof that a trial caused an improvement. Synthetic demo metrics are never benchmarks.

## Privacy and storage

Live data is stored under **%LOCALAPPDATA%\Kshetrajna**. Records contain UTC timestamps, executable basenames, PIDs and creation tokens, CPU/RAM measurements, app transitions, idle duration, workspace names, and decisions. Window titles, URLs, documents, command lines, keystrokes, clipboard content, screenshots, and full executable paths are not stored.

Default retention is seven days, selectable from one to thirty. Pausing stops new observation; prior history remains until retention or deletion. Deletion restores active trials first, then removes observations, derived patterns, decisions, and saved workspaces. Unresolved recovery records cannot be erased through the dashboard. Diagnostic logs and preferences remain.

SQLite is not application-encrypted. The boundary is the current Windows user profile and a loopback-only HTTP service. Host checks, matching Origin, per-session tokens, strict local asset loading, and request size limits protect dashboard mutations from ordinary cross-site requests. Programs already running as the same user are not isolated from this prototype.

## Technology choices

**Python 3.13 + standard library**, **ctypes/Win32**, **SQLite**, and **HTML/CSS/JavaScript/SVG**. There are zero runtime package dependencies. This keeps the project offline-capable and avoids architecture-specific third-party wheels. A small local HTTP server hosts the dashboard; it is not designed for public deployment.

On Snapdragon X, use a native ARM64 Python build. The Windows API bindings use pointer-sized handles and counters. The official Python 3.13 ARM64 installer is labeled experimental, and native Snapdragon X device validation is still required. Development and smoke testing here used Windows x64.

## Tests

Testing on Snapdragon? Follow [Windows ARM64 setup and device checks](docs/SNAPDRAGON.md). Run `check_device.cmd --require-native-arm64` there to generate local compatibility and timing results. The application runs locally; cloud hosting is not required.

Workflow learning now recognizes ordered routines, tolerates incidental extra apps, and uses session-aware next-app predictions with recency weighting and historical replay. See [patterns and predictions](docs/PATTERNS.md).

See [the validation record](docs/VALIDATION.md) for checks performed and remaining hardware qualification.

Run **run_tests.cmd**, or from PowerShell:

~~~powershell
$env:PYTHONPATH = 'src'
python -m unittest discover -s tests -v
~~~

Tests cover consent, schema migration, retention/deletion, model evidence, stale proposals, one active trial, journal-before-action ordering, expiry, restart recovery, failed restoration, feedback, workspaces, and the HTTP workflow. Native priority calls are isolated behind a driver and tested with controlled doubles; the demo never changes user processes.

## Source map

~~~text
src/kshetrajna/
  telemetry.py       Windows observation
  intelligence.py    context, importance, patterns, proposals
  service.py         approval, lifecycle, feedback, reports
  actions.py         simulated and native priority drivers
  collector.py       sampling and recovery tick
  storage.py         SQLite history and durable journal
  config.py          validated privacy settings
  instance.py        one live process per data directory
  demo.py            isolated synthetic scenarios
  server.py          loopback HTTP boundary
  web/               dashboard and workspace board
tests/               behavior and API regression tests
docs/                architecture and original vision
~~~

## Next engineering milestones

Native ARM64 hardware qualification, controlled performance experiments, production packaging and signing, encrypted storage, stronger process ownership checks, descendant-aware recovery, real desktop workspace integration, and a validated learned context model remain future work.

MIT license. See [LICENSE](LICENSE).
