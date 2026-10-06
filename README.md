# Kshetrajna

> **The intelligence that knows the field.**

A local Windows workload and file assistant that observes app activity, indexes local files, recognizes workflows, explains resource recommendations, asks for consent, and records outcomes and rollback. This is the end-to-end hackathon prototype, version 0.3.

**Experimental integration snapshot:** Sort AI and the Jarvis bridge are staged
for device testing. This is not a release-qualified build. Historical pitch
assets are retained once under [`submission/`](submission/README.md).

## Run the submission now

Install **Python 3.13**, open this folder, and double-click **run_demo.cmd**. The browser opens at [127.0.0.1:8766](http://127.0.0.1:8766/). No packages, API keys, cloud services, or build step are required.

The demo uses clearly labeled synthetic data in a temporary database. Its approval and rollback actions are simulated and cannot modify Windows. Closing the process removes its temporary session. Download the session report before closing if you want to keep it.

For actual Windows observations, run **run.cmd**, then choose **Enable collection**. Live collection and priority changes each default to off. Use Ctrl+C in the terminal for a graceful stop. Both launchers accept **--no-browser** and **--port 8767**.

The first live enable action asks for local telemetry consent once and persists it in `%LOCALAPPDATA%\Kshetrajna\config.json`; later launches do not ask again. Conventional Desktop, Downloads, Documents, and OneDrive paths are fetched locally without enumerating their contents. Selecting **Approve and scan** is still required for each folder.

Read [SUBMISSION.md](SUBMISSION.md) for a 90-second demo script and copy-ready project description. [Architecture and decisions](docs/ARCHITECTURE.md), the [feature matrix](docs/FEATURE_MATRIX.md), [privacy inventory](docs/PRIVACY.md), [deployment guide](docs/DEPLOYMENT.md), and [demo script](docs/DEMO.md) explain the verified implementation. The original [project vision](docs/VISION.md) is preserved.

## What the prototype delivers

| Phase | Implemented behavior |
|---|---|
| 1 · Observe | Windows CPU/RAM, process working sets and CPU share, foreground app, idle transitions, local SQLite history, privacy controls. |
| 2 · Understand | Weighted context rules, app importance from foreground/frequency/recency, recurring app groups, next-app transition counts. |
| 3 · Recommend | Evidence and tradeoffs for eligible CPU-priority trials, memory advice, and workspace suggestions. |
| 4 · Adapt with consent | Review/approve/dismiss, optional 60-second live Normal → Below normal priority trial, saved workspace boards within the dashboard. |
| 5 · Evaluate and reverse | Durable decision journal, manual and timed undo, pause/shutdown restoration, startup recovery, observed before/after deltas, feedback that suppresses unwanted suggestions. |
| 6 · Demonstrate and verify | Four synthetic scenarios, JSON report download, launch scripts, regression tests, Windows/Linux CI definition, submission guide. |
| 7 · File Intelligence | Local file indexing, bounded excerpts, keyword search, metadata insights, contextual file suggestions, separate `files.db`, and a reviewed Sort AI panel. Deterministic category suggestions support editable batch previews, explicit empty-folder creation, conflict-aware partial completion, durable per-file actions, and reverse-order undo. |
| 8 · Windows tools | Reviewed Storage settings, Disk Cleanup, Store cache, temporary-folder inspection, bounded temporary-file preview/cleanup, and one-process working-set trim. Each operation has its own scope and undo limitation. |

## Important boundaries

- Context detection uses explicit app-name rules. Personal patterns use counts from recent retained observations. There is no trained LLM, NPU inference, or claim to understand document content.
- Workspace adaptation changes this dashboard's app board. It does not reposition desktop windows or modify taskbars.
- Live CPU trials are experimental and individually approved. They affect a single allowlisted, background process at Normal priority in the current session. PID and process creation time are checked before acting.
- The selected process is restored to Normal on undo or expiry (checked approximately every second), when collection is paused, when live trials are disabled, on graceful shutdown, or during startup recovery. Failed restoration remains visible and keeps its recovery record.
- If the app is forcibly killed, a priority change can persist until restart recovery or until the target exits. Newly spawned children can inherit the selected process's priority; they are outside this prototype's rollback scope.
- No application is killed, no power plan is changed, and no elevated or real-time priority is used. An explicitly enabled Windows maintenance action can request `EmptyWorkingSet` for one eligible background process or delete only reviewed, old files in the fixed `%TEMP%` location; working-set trim has no exact undo and temporary-file deletion is permanent.
- Before/after system metrics are observations, not proof that a trial caused an improvement. Synthetic demo metrics are never benchmarks.

## Privacy and storage

System Intelligence data is stored under **%LOCALAPPDATA%\Kshetrajna**. Its records contain UTC timestamps, executable basenames, PIDs and creation tokens, CPU/RAM measurements, app transitions, idle duration, workspace names, and decisions. System telemetry does not store window titles, URLs, document names, command lines, keystrokes, clipboard content, screenshots, or full executable paths. File Intelligence is a separate opt-in catalog: it stores approved root paths, relative file names/paths, metadata, bounded excerpts from allowed text files, and reviewed file-action journals; use its separate **Delete all file data** control to remove the catalog after resolving any pending file recovery records.

Default retention is seven days, selectable from one to thirty. Pausing stops new observation; prior history remains until retention or deletion. Deletion restores active trials first, then removes observations, derived patterns, decisions, and saved workspaces. Unresolved recovery records cannot be erased through the dashboard. Diagnostic logs and preferences remain.

SQLite is not application-encrypted. The boundary is the current Windows user profile and a loopback-only HTTP service. Host checks, matching Origin, per-session tokens, strict local asset loading, and request size limits protect dashboard mutations from ordinary cross-site requests. Programs already running as the same user are not isolated from this prototype.

## Technology choices

**Python 3.13 + standard library**, **ctypes/Win32**, **SQLite**, and **HTML/CSS/JavaScript/SVG**. There are zero runtime package dependencies. This keeps the project offline-capable and avoids architecture-specific third-party wheels. A small local HTTP server hosts the dashboard; it is not designed for public deployment.

On Snapdragon X, use a native ARM64 Python build. The Windows API bindings use pointer-sized handles and counters. The official Python 3.13 ARM64 installer is labeled experimental. The supplied submission brief reports a user-confirmed 61-test ARM64 run; the current integration checks were run on Linux x86_64, and the earlier Windows x64 validation is historical. No ARM64 diagnostic report is committed here. Follow [the device checks](docs/SNAPDRAGON.md) before making native performance or actuation claims.

## Tests

Testing on Snapdragon? Follow [Windows ARM64 setup and device checks](docs/SNAPDRAGON.md). Run `check_device.cmd --require-native-arm64` there to generate local compatibility and timing results. The application runs locally; cloud hosting is not required.

Workflow learning now recognizes ordered routines, tolerates incidental extra apps, and uses session-aware next-app predictions with recency weighting and historical replay. See [patterns and predictions](docs/PATTERNS.md).

See [the validation record](docs/VALIDATION.md) for checks performed and remaining hardware qualification.

## Jarvis and command integration

The project is also a Python package with a narrow, local-only Jarvis bridge.
It exposes named Sort AI commands rather than arbitrary shell execution. Start
the dashboard, then use:

```powershell
# Discover existing conventional folders without scanning them
python -m kshetrajna.jarvis --url http://127.0.0.1:8765 discover_folders

# Equivalent sorting command; this prints a reviewable JSON plan
python -m kshetrajna.cli sort --url http://127.0.0.1:8765 `
  --known-folder downloads --create-folders

# Create a reviewed plan from an approved known folder
python -m kshetrajna.jarvis --url http://127.0.0.1:8765 sort_preview `
  --known-folder downloads --create-folders

# Use the returned plan ID only after reviewing the preview
python -m kshetrajna.jarvis --url http://127.0.0.1:8765 sort_apply `
  --plan-id PLAN_ID --confirm

# Reverse eligible changes
python -m kshetrajna.jarvis --url http://127.0.0.1:8765 sort_undo `
  --plan-id PLAN_ID --confirm
```

For a subprocess protocol, run `python -m kshetrajna.jarvis --url http://127.0.0.1:8765 --jsonl` and send one bounded JSON object per line, such as `{"command":"discover_folders"}`. For in-process integration, import `JarvisHttpClient` or `JarvisCommandBridge` from `kshetrajna.jarvis`. The bridge accepts loopback URLs only and requires explicit `confirm: true` for apply and undo.

Run **run_tests.cmd**, or from PowerShell:

~~~powershell
$env:PYTHONPATH = 'src'
python -m unittest discover -s tests -v
~~~

Tests cover consent, schema migration, retention/deletion, model evidence, stale proposals, one active trial, journal-before-action ordering, expiry, restart recovery, failed restoration, feedback, workspaces, file scanning/actions, Sort AI batch preview/apply/undo, catalog deletion protection, temporary-file scope, automatic path discovery, persisted consent, Jarvis bridge validation, malformed file API inputs, and the HTTP workflow. Native priority calls are isolated behind a driver and tested with controlled doubles; file and maintenance mutations use temporary fixtures; the demo never changes user processes. The local suite contains 80 tests: 78 passed and 2 platform-specific checks were skipped on CPython 3.14.4/Linux x86_64. See [the validation record](docs/VALIDATION.md).

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
  file_storage.py    SQLite file catalog (files.db)
  file_scanner.py    bounded filesystem walker
  file_insights.py   deterministic file metadata
  file_service.py    file API, Sort AI orchestration, and recovery
  file_demo.py       synthetic file data
  sort_ai.py         deterministic, explainable organization suggestions
  known_folders.py   non-scanning conventional-folder discovery
  jarvis.py          loopback/JSONL Jarvis command bridge
  cli.py             kshetrajna-sort convenience entry point
  web/               dashboard, workspace board, file inbox, and file-action journal
tests/               behavior and API regression tests
docs/                architecture and original vision
~~~

## Next engineering milestones

A committed native ARM64 diagnostic record, controlled performance experiments, production packaging and signing, encrypted storage, stronger process ownership checks, descendant-aware recovery, real desktop window placement/launch integration, and a validated learned context model remain future work. Sort AI is now integrated as a deterministic local provider; a semantic model provider is intentionally not claimed. See the [feature matrix](docs/FEATURE_MATRIX.md) for exact phase boundaries.

MIT license. See [LICENSE](LICENSE).
