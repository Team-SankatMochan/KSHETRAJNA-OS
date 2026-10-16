# Kshetrajna

> **The intelligence that knows the field.**

A local Windows **PC-health and file-intelligence assistant** built around
**Observe → Diagnose → Explain → Approve → Act → Verify → Measure → Recover**.
It combines system observation, explainable recommendations, reviewed file
organization and recovery, and optional local conversation. The complete product
destination, safety boundaries and release gates are defined in the
[final-release contract](docs/FINAL_RELEASE.md).

**Status: 0.5.0 implementation baseline, not a final release.** Version 1.0.0 is
reserved until the contract's qualification gates pass. Planned capabilities
below are not claims about the current executable.

## Product destination and current boundary

The source navigation is **System Health, Recover Space, Investigate Memory,
Organize Files, History & Recovery, Privacy**, and an optional **Local Assistant**.
Basic is the default view; Expert reveals advanced controls without granting
permission. Recovery remains available in both. Each capability still needs its
own opt-in and each action its own approval. Navigation, dialog/focus handling
and visibility controls are implemented, but current browser/NVDA qualification
has not run.

At integrated revision `25a0de7`, `actions.db` and `/api/v1` proposal → consent →
execute are implemented for **memory trim, process close, process termination
and four fixed external tools only**. The [action API contract](docs/ACTIONS.md)
documents persisted safe review data, strict confirmation, expiry/replay and
permission-generation invalidation. Other canonical capabilities are deferred.
Legacy routes remain domain-authoritative; capabilities report
`canonical_authority_complete: false`. Existing journals/executors remain the
recovery authority, not a promise of universal undo. See the
[feature matrix](docs/FEATURE_MATRIX.md) for implemented versus planned scope.

**Sort AI update:** Automatic batches handle clear loose-file matches, with a
separate advice queue and remembered keep decisions. Approved Downloads files
can route into an approved Desktop, while existing folders and named project
scopes are reused across the selected locations. Downloads discovery uses
Windows' actual folder location. Opt-in download watching refreshes the index and Sort AI preview after new files settle. Empty-file and exact-duplicate cleanup is a separate reviewed action surface. See [Sort AI setup and behavior](docs/SORT_AI.md) and [cleanup integration](docs/CLEANUP.md).

**Coordinator-reported combined Linux check:** pinned CPython 3.13.16 ran **453
tests: 450 passed, three skipped**. The last native Windows baseline remains
`4d86a96`: x64 CPython 3.13.3 ran 276 with no skips; no newer native qualification
is recorded. That Windows patch is not the reproducible pin. These source checks
do not qualify a current package, ARM64 device or Docker image. Prepared release
tooling also has a reported 26-pass isolated suite including MSI Upgrade-row
audit logic, not a native installer lifecycle pass. This docs lane did not rerun
either suite; the coordinator owns [qualification evidence](docs/VALIDATION.md).
The published 0.4.3 beta record is historical and does not qualify these later
source changes. Historical pitch assets are retained under
[`submission/`](submission/README.md).

## Run locally

### Self-contained Windows bundles

The historical **0.4.3 beta** has native x64 and ARM64 packages containing Python
and that older app. These downloads do not include the current 0.5.0 quarantine
milestone or the planned final product.
Download [Intel/AMD x64](https://github.com/Team-SankatMochan/KSHETRAJNA-OS/releases/download/v0.4.3/Kshetrajna-Windows-x64.zip)
or [Snapdragon/ARM64](https://github.com/Team-SankatMochan/KSHETRAJNA-OS/releases/download/v0.4.3/Kshetrajna-Windows-arm64.zip).
Extract a package and open **Launch.cmd**, or use **Install.cmd** for a verified
per-user copy and optional desktop shortcut. Use **Quit Kshetrajna** in Privacy
to stop the background application. See [packaging and verification](docs/WINDOWS_BUNDLES.md).
These are unsigned archives; a compatible local model remains optional and separate.

### Run from source on your own Windows PC

The source option requires Python 3.13.16 (x64 for Intel/AMD, ARM64 for Snapdragon).
Extract the source download and double-click **start_here.cmd**. The launcher selects
an installed Python 3.13 installation and opens your own local dashboard. No Git or account
is required. See [Windows quick start](docs/WINDOWS_QUICKSTART.md).

Enable collection and the desired action controls locally. Ollama plus an
installed local model is optional for AI conversation; real PC tools can run
without it. Only the source distribution requires a separate Python installation.

### Host the interactive demo on Vercel

Import this repository from **main** (or **experimental/sort-ai-jarvis**), choose **Other**,
and leave Root Directory at the repository root. The committed `vercel.json`
serves **web-demo** without an install or build step. No API keys are needed.
See [Vercel setup](docs/VERCEL.md) for the exact settings.

The hosted version uses synthetic data, scripted replies and simulated actions.
Real Windows telemetry, file operations, RAM/process controls and installed local
AI run on the laptop through the launchers below.

### Local chat, without an API key

Run **run_chat.cmd** to start the installed Ollama runtime and the application.
Open **Chat**, select an installed local model, and choose **Enable local AI**.
Choose a compatible runtime and model installed on your device; no particular
model is required or supplied. For bundles, start Ollama and open **Launch.cmd**.
Local Assistant supports normal conversation, current system inspection, memory review,
temporary-file cleanup, Store cache tools, and reviewed app closure. Enable
the corresponding action controls in Privacy, then approve each review card.
Force termination targets one eligible background PID and requires typing its
exact name. Low CPU never triggers automatic termination.

Model replies stream locally. Stop, new chat, model selection and model unload
are available. Conversations remain in application memory, not a chat database.
The provider rejects cloud models. See [local assistant setup and boundaries](docs/LOCAL_ASSISTANT.md).
This is a local-model assistant, not the OpenAI ChatGPT service. The optional
Ollama runtime/model is separate from the dependency-free Python core.

Install **Python 3.13.16**, open this folder, and double-click **run_demo.cmd**. The browser opens at [127.0.0.1:8766](http://127.0.0.1:8766/). No packages, API keys, cloud services, or build step are required.

The demo uses clearly labeled synthetic data in a temporary database. Its approval and rollback actions are simulated and cannot modify Windows. Closing the process removes its temporary session. Download the session report before closing if you want to keep it.

For actual Windows observations, run **run.cmd**, then choose **Enable collection**. Live collection and priority changes each default to off. Use Ctrl+C in the terminal for a graceful stop. Both launchers accept **--no-browser** and **--port 8767**.

The first live enable action asks for local telemetry consent once and persists it in `%LOCALAPPDATA%\Kshetrajna\config.json`; later launches do not ask again. Conventional Desktop, Downloads, Documents, and OneDrive paths are discovered locally without enumerating their contents. Selecting **Approve and scan** opens an explicit file-access permission dialog for each folder; scanning never moves or deletes files. Sort AI previews destinations and sorts clear matches after one batch approval; ambiguous files enter a separate advice queue. Keep-here decisions are remembered, and eligible batches can be undone.

Read [SUBMISSION.md](SUBMISSION.md) for a 90-second demo script and copy-ready project description. [Architecture and decisions](docs/ARCHITECTURE.md), the [UML model](docs/UML.md), [feature matrix](docs/FEATURE_MATRIX.md), [privacy inventory](docs/PRIVACY.md), [deployment guide](docs/DEPLOYMENT.md), and [demo script](docs/DEMO.md) explain the verified implementation. The original [project vision](docs/VISION.md) is preserved.

## Implemented baseline

These are implementation areas, not completion of the final-release phases.

| Phase | Implemented behavior |
|---|---|
| 1 · Observe | Windows CPU/physical RAM, nullable commit/cache/OS low-memory and system-volume disk counters, process working sets/CPU, foreground app, idle transitions, local history and privacy controls. Read-only health keeps freshness and unavailable values explicit. |
| 2 · Understand | Weighted context rules, app importance from foreground/frequency/recency, recurring app groups, next-app transition counts. |
| 3 · Recommend | Evidence and tradeoffs for eligible CPU-priority trials, memory advice, and workspace suggestions. |
| 4 · Adapt with consent | Review/approve/dismiss, optional 60-second live Normal → Below normal priority trial, saved workspace boards within the dashboard. |
| 5 · Evaluate and reverse | Durable decision journal, manual and timed undo, pause/shutdown restoration, startup recovery, observed before/after deltas, feedback that suppresses unwanted suggestions. |
| 6 · Demonstrate and verify | Four synthetic scenarios, JSON report download, launch scripts, regression tests, Windows/Linux CI definition, submission guide. |
| 7 · File Intelligence | Approved-root indexing, persisted bounded text/Office/PDF excerpts, local previews and search, deterministic purpose/action evidence, reviewed Sort AI, and empty-file/exact-duplicate cleanup. Reviewed cleanup moves content into protected quarantine with an action ID for restore; bytes remain stored, so this is not recovered capacity. See [cleanup](docs/CLEANUP.md). |
| 8 · Windows tools | Reviewed Storage settings, Disk Cleanup, Store cache, temporary-folder inspection, bounded temporary-file preview/cleanup, and one-process working-set trim. Each operation has its own scope and undo limitation. |

These historical implementation-area numbers are not the final contract's
phase-completion checklist. The narrow canonical adapter and Basic/Expert shell
are present; full adapter coverage and qualification remain incomplete.

## Important boundaries

- Workflow context and Sort AI use local rules and retained observations. Optional conversation uses an installed Ollama LLM with a bounded review-only tool interface. Semantic file embeddings and NPU inference are not implemented.
- Workspace adaptation changes this dashboard's app board. It does not reposition desktop windows or modify taskbars.
- Live CPU trials are experimental and individually approved. They affect a single allowlisted, background process at Normal priority in the current session. PID and process creation time are checked before acting.
- Restoration to Normal is attempted on undo or expiry (checked approximately every second), when collection is paused, when live trials are disabled, on graceful shutdown, or during startup recovery. Exited/reused targets and external priority changes are handled separately; failed restoration remains visible and keeps its recovery record.
- If the app is forcibly killed, a priority change can persist until restart recovery or until the target exits. Newly spawned children can inherit the selected process's priority; they are outside the current rollback scope.
- No application is terminated automatically, no power plan is changed, and no elevated or real-time priority is used. Separately enabled app controls can request graceful closure or individually confirmed force termination for one verified, allowlisted background PID. Closing/termination can lose unsaved work and has no undo. An explicitly enabled Windows maintenance action can request `EmptyWorkingSet` for one eligible background process or delete reviewed, old files in the fixed `%TEMP%` location; working-set trim has no exact undo and temporary-file deletion is permanent. Reviewed File Intelligence cleanup is different: live cleanup moves the file into protected quarantine, and the action can be restored with its exact action ID.
- Before/after system metrics are observations, not proof that a trial caused an improvement. Synthetic demo metrics are never benchmarks.
- Standby pages are already reusable RAM. Cache counters overlap other memory
  figures; they cannot be added to available RAM or called waste. Working-set
  trim does not release committed allocations or guarantee faster responses.
  There is no unsupported standby purge, blanket RAM cleaner, registry cleaner,
  or automatic process termination.
- A Windows tool launch is not verified cleanup completion, a close request is
  not confirmed exit, and a model-unload request is not measured RAM reclaimed.
  Missing/stale measurements must not be read as zero or an improvement.

## Privacy and storage

System Intelligence data is stored under **%LOCALAPPDATA%\Kshetrajna**. Its records contain UTC timestamps, executable basenames, PIDs and creation tokens, CPU/RAM measurements, app transitions, idle duration, workspace names, and decisions. System telemetry does not store window titles, URLs, document names, command lines, keystrokes, clipboard content, screenshots, or full executable paths. File Intelligence is a separate opt-in catalog: it stores approved root paths, relative file names/paths, metadata, bounded excerpts from allowed text, Office and PDF files, and reviewed file-action journals. **Current root approval includes excerpt extraction and persistence; metadata-only scanning is not the current default.** Plain text and Office output are bounded to 4,096 UTF-8 bytes; PDF output is bounded to 4,096 characters, not a guaranteed 4 KiB. Live cleanup retains content in the protected `.kshetrajna-quarantine` directory under the approved root; it is not indexed or offered as a cleanup candidate.

System-history retention defaults to seven days, selectable from one to thirty;
this is not a file-excerpt retention policy. Pausing collection stops new system
observation, not separately enabled file watching. System-history deletion first
attempts trial restoration; unresolved recovery records are preserved. It revokes
pending canonical approvals but **does not erase canonical history**; explicit
selected-ID erasure is a separate API with retained recovery/audit tombstones.
File catalog/root deletion is blocked by unresolved file-action/quarantine records;
restore them first. Logs and preferences remain. Exception logging can include
paths: there is **no blanket log redaction guarantee**. See the
[privacy inventory](docs/PRIVACY.md) and [crash-reporting limits](docs/CRASH_REPORTING.md).
The cleanup bridge has no final-purge command; legacy generic file-action DELETE
is a separate permanent-delete API, default-denied by the service's
`allow_permanent_delete` danger opt-in and visible in the UI only in Expert.
Expert visibility itself is not authorization.

SQLite is not application-encrypted. The boundary is the current Windows user profile and a loopback-only HTTP service. Host checks, matching Origin, per-session tokens, strict local asset loading, and request size limits protect dashboard mutations from ordinary cross-site requests. Programs already running as the same user are not isolated from this application. Telemetry, root-access, maintenance, app-control and local-AI permissions are separate; content-inspection consent, metadata-only default and a visible migration/erasure policy for existing excerpts remain pending.

## Technology choices

**Python 3.13 + standard library**, **ctypes/Win32**, **SQLite**, and **HTML/CSS/JavaScript/SVG**. There are zero runtime package dependencies. This keeps the project offline-capable and avoids architecture-specific third-party wheels. A small local HTTP server hosts the dashboard; it is not designed for public deployment.

On Snapdragon X, use the ARM64 bundle or native ARM64 Python for source launch. The Windows API bindings use pointer-sized handles and counters. Published 0.4.3 Windows x64/ARM64 package qualification is recorded in [the validation history](docs/VALIDATION.md); it does not qualify later source changes. Current source has the recorded x64 check above, but pinned-runtime native x64/ARM64 package and device qualification remain gates. Generic ARM64 runner compatibility is not a Snapdragon model-speed or battery benchmark. Follow [the device checks](docs/SNAPDRAGON.md) for your laptop.

## Reproducible source build

Use **Python 3.13.16**, pinned in `.python-version`. The application and tests
need no third-party packages, Node.js, npm install, API keys, or `.env` loader.
Keep the regression tests in source control; wheels and Windows bundles contain
only the application, not test fixtures or build tools.

On Linux, run the synthetic demo or offline tests directly from source:

```sh
PYTHONPATH=src python3 -m kshetrajna --demo --no-browser --port 8766
# Open http://127.0.0.1:8766; Ctrl+C stops the isolated demo.
make test
```

For an installable wheel, install the pinned packaging tools in a project-local
virtual environment. Dependency download is needed once; the tests stay offline.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-build.lock
make build PYTHON=.venv/bin/python
.venv/bin/python -m pip install --no-index --no-deps dist/kshetrajna-0.5.0-py3-none-any.whl
.venv/bin/kshetrajna-sort --help
```

The `Dockerfile` runs tests in a build stage and installs only the wheel in a
non-root runtime image. Linux Docker Engine can run the loopback-only demo:

```sh
docker build -t kshetrajna:local .
docker run --rm --network host kshetrajna:local
```

Open `http://127.0.0.1:8766` on that Linux host. Port publishing (`-p`) does not
expose a server bound to container loopback. Docker Desktop needs host networking
explicitly enabled; otherwise run the source demo directly. Containers do not
provide live Windows telemetry. Keep the real application local; only the
synthetic `web-demo/` is suitable for public hosting (see [deployment](docs/DEPLOYMENT.md)).
Docker build/run was unavailable in the current validation environment, so the
commands above describe the configured path rather than a passed local check.

## Tests

Testing on Snapdragon? Follow [Windows ARM64 setup and device checks](docs/SNAPDRAGON.md). Run `check_device.cmd --require-native-arm64` there to generate local compatibility and timing results. The application runs locally; cloud hosting is not required.

Workflow learning now recognizes ordered routines, tolerates incidental extra apps, and uses session-aware next-app predictions with recency weighting and historical replay. See [patterns and predictions](docs/PATTERNS.md).

See [the validation record](docs/VALIDATION.md) for checks performed and remaining hardware qualification.

## Jarvis and command integration

The project is also a Python package with a narrow, local-only Jarvis bridge.
It exposes named Sort AI and reviewed cleanup commands rather than arbitrary
shell execution. Start the dashboard, then use:

```powershell
$env:PYTHONPATH = 'src'
# Discover existing conventional folders without scanning them
python -m kshetrajna.jarvis --url http://127.0.0.1:8765 discover_folders

# Prefer an already approved root; previews below do not grant new root access
python -m kshetrajna.cli sort --url http://127.0.0.1:8765 `
  --root-id 3 --create-folders

# Create a reviewed plan from the same approved root
python -m kshetrajna.jarvis --url http://127.0.0.1:8765 sort_preview `
  --root-id 3 --create-folders

# Use the returned plan ID only after reviewing the preview
python -m kshetrajna.jarvis --url http://127.0.0.1:8765 sort_apply `
  --plan-id PLAN_ID --confirm

# Reverse eligible changes
python -m kshetrajna.jarvis --url http://127.0.0.1:8765 sort_undo `
  --plan-id PLAN_ID --confirm

# Preview reviewed cleanup candidates (project trees stay protected)
python -m kshetrajna.cli cleanup empty --url http://127.0.0.1:8765 --root-id 3
python -m kshetrajna.cli cleanup duplicates --url http://127.0.0.1:8765 --root-id 3

# Quarantine only after reviewing and confirming the exact file ID
python -m kshetrajna.cli cleanup_empty_delete --url http://127.0.0.1:8765 `
  --file-id FILE_ID --confirm

# Review the protected quarantine record returned by that action
python -m kshetrajna.cli quarantine list --url http://127.0.0.1:8765 --root-id 3

# Restore the exact action; this is not a permanent final purge
python -m kshetrajna.cli quarantine restore --url http://127.0.0.1:8765 `
  --action-id ACTION_ID --confirm
```

For a subprocess protocol, run `python -m kshetrajna.jarvis --url http://127.0.0.1:8765 --jsonl` and send one bounded JSON object per line, such as `{"command":"discover_folders"}`. For in-process integration, import `JarvisHttpClient` or `JarvisCommandBridge` from `kshetrajna.jarvis`. The HTTP client accepts loopback URLs only, without URL credentials or query data. Apply, undo, cleanup quarantine and restore require literal `confirm: true` at the service boundary as well as in the bridge. New roots selected by `--path` or `--known-folder` require explicit `--consent` (JSONL: `"consent":true`); without it, previews do not approve a new root. With it, approval and scanning include persisted excerpts. UI approval followed by `--root-id` remains convenient. Legacy routes still use their domain authority rather than the canonical ledger. See [cleanup contracts](docs/CLEANUP.md) for recovery routes and response semantics. Offline bridge mode takes the same data-directory lock and cannot coexist with the dashboard on that directory; use `--url` to share the running service.

Run **run_tests.cmd**, or from PowerShell:

~~~powershell
$env:PYTHONPATH = 'src'
python -m unittest discover -s tests -v
~~~

Tests cover consent, schema migration, retention/deletion, model evidence, stale proposals, one active trial, journal-before-action ordering, expiry, restart recovery, failed restoration, feedback, workspaces, file scanning/actions, bounded UTF-8 extraction, content-aware review signals, Sort AI batch preview/apply/undo, conversational folder approval, empty-file and exact-duplicate cleanup, quarantine listing/restoration and protected-directory behavior, catalog/root deletion protection while recovery records remain, temporary-file scope, automatic path discovery, persisted consent, Jarvis bridge validation, malformed file API inputs, private dashboard GET guards, and the HTTP workflow. Native priority calls are isolated behind a driver and tested with controlled doubles; file and maintenance mutations use temporary fixtures; the demo never changes user processes. See [the validation record](docs/VALIDATION.md) for the current test count and platform limits.

## Source map

~~~text
src/kshetrajna/
  telemetry.py       Windows observation
  intelligence.py    context, importance, patterns, proposals
  service.py         approval, lifecycle, feedback, reports
  action_store.py    actions.db intent, consent, replay guards and audit events
  action_service.py  narrow canonical /api/v1 adapter and persisted review DTO
  capabilities.py    closed wired/deferred action vocabulary
  health.py          pure read-only counter diagnoses and freshness
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

## Final-release gates — not passed by documentation

The [execution contract](docs/FINAL_RELEASE.md) is authoritative. The ledger,
narrow v1 adapter, strict service confirmations, root consent, nullable health
model and Basic/Expert shell are implemented, not proof that phases 2–11 are
fully complete. Remaining work includes canonical adapters for deferred kinds,
metadata-only indexing/content-consent and old-excerpt migration, comprehensive
redaction, delayed measurement, and integrated adapter/UI/recovery qualification.
Prepared release tools include fail-closed final-release identity/evidence gates,
MSI auditing and signed lifecycle automation, but are not a signed release. See
[deployment](docs/DEPLOYMENT.md) for future verification commands.
Final qualification requires the exact candidate and packaged runtime on native
x64 and ARM64, Windows race/crash fixtures, Docker build/run, browser/keyboard/
screen-reader testing, declared-hardware performance and battery measurements,
signed install/upgrade/uninstall, artifact scanning, SBOM and provenance.

Signing credentials/protected release infrastructure, real devices (including
Snapdragon), and at least ten ordinary Windows users including keyboard users
are prerequisites, not assumed passes. Missing access means **blocked/not run**.
Do not tag 1.0.0 until applicable gates have evidence. Historical pitch assets
remain available but are not current qualification. Semantic embeddings, NPU
inference, real desktop placement and encrypted-at-rest storage are not delivered
by the baseline and are not silently added to the frozen release scope.

MIT license. See [LICENSE](LICENSE).
