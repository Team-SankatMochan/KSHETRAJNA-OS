# Kshetrajna

> **The intelligence that knows the field.**

A local Windows workload and file assistant that observes app activity, indexes local files, recognizes workflows, explains resource recommendations, asks for consent, and records outcomes and rollback. This is the end-to-end hackathon prototype, version 0.5.0.

**Sort AI update:** Automatic batches handle clear loose-file matches, with a
separate advice queue and remembered keep decisions. Approved Downloads files
can route into an approved Desktop, while existing folders and named project
scopes are reused across the selected locations. Downloads discovery uses
Windows' actual folder location. Opt-in download watching refreshes the index and Sort AI preview after new files settle. Empty-file and exact-duplicate cleanup is a separate reviewed action surface. See [Sort AI setup and behavior](docs/SORT_AI.md) and [cleanup integration](docs/CLEANUP.md).

**Current qualification:** The reviewed-cleanup and quarantine milestone has a
local Linux x86_64 source check under CPython 3.13.16. Native Windows package,
device, and Docker checks are not claimed for this milestone.
The published 0.4.3 beta record is historical and does not qualify these later
source changes. Historical pitch assets are retained under
[`submission/`](submission/README.md).

## Run the submission now

### Self-contained Windows bundles

Version 0.4 adds native x64 and ARM64 packages containing Python and the app.
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
The development machine already had `llama3.1:8b`; other devices need their own
compatible runtime and model. For bundles, start Ollama and open **Launch.cmd**.
Chat supports normal conversation, current system inspection, memory review,
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

## What the prototype delivers

| Phase | Implemented behavior |
|---|---|
| 1 · Observe | Windows CPU/RAM, process working sets and CPU share, foreground app, idle transitions, local SQLite history, privacy controls. |
| 2 · Understand | Weighted context rules, app importance from foreground/frequency/recency, recurring app groups, next-app transition counts. |
| 3 · Recommend | Evidence and tradeoffs for eligible CPU-priority trials, memory advice, and workspace suggestions. |
| 4 · Adapt with consent | Review/approve/dismiss, optional 60-second live Normal → Below normal priority trial, saved workspace boards within the dashboard. |
| 5 · Evaluate and reverse | Durable decision journal, manual and timed undo, pause/shutdown restoration, startup recovery, observed before/after deltas, feedback that suppresses unwanted suggestions. |
| 6 · Demonstrate and verify | Four synthetic scenarios, JSON report download, launch scripts, regression tests, Windows/Linux CI definition, submission guide. |
| 7 · File Intelligence | Local file indexing, bounded excerpts, one-click previews for text/code, Office files, PDFs, and images, automatic purpose/action decisions, keyword search, content-aware review signals, metadata insights, contextual file suggestions, separate `files.db`, a reviewed Sort AI panel, and explicit cleanup previews for zero-byte files and exact SHA-256 duplicates. Explicit folder-access consent precedes scanning. Conversational category ideas combine path, related-file, filename, and content evidence to organize study material, project evidence, code, data, media, logs, and generated caches without silently moving or deleting files. Cleanup never removes protected markers, sensitive files, directories, links, or the last duplicate survivor; a confirmed live cleanup moves the reviewed file into the protected `.kshetrajna-quarantine` directory and returns `deleted:true`, `quarantined:true`, and an `action_id` for recovery. |
| 8 · Windows tools | Reviewed Storage settings, Disk Cleanup, Store cache, temporary-folder inspection, bounded temporary-file preview/cleanup, and one-process working-set trim. Each operation has its own scope and undo limitation. |

## Important boundaries

- Workflow context and Sort AI use local rules and retained observations. Optional conversation uses an installed Ollama LLM with a bounded review-only tool interface. Semantic file embeddings and NPU inference are not implemented.
- Workspace adaptation changes this dashboard's app board. It does not reposition desktop windows or modify taskbars.
- Live CPU trials are experimental and individually approved. They affect a single allowlisted, background process at Normal priority in the current session. PID and process creation time are checked before acting.
- The selected process is restored to Normal on undo or expiry (checked approximately every second), when collection is paused, when live trials are disabled, on graceful shutdown, or during startup recovery. Failed restoration remains visible and keeps its recovery record.
- If the app is forcibly killed, a priority change can persist until restart recovery or until the target exits. Newly spawned children can inherit the selected process's priority; they are outside this prototype's rollback scope.
- No application is terminated automatically, no power plan is changed, and no elevated or real-time priority is used. Separately enabled app controls can request graceful closure or individually confirmed force termination for one verified, allowlisted background PID. Closing/termination can lose unsaved work and has no undo. An explicitly enabled Windows maintenance action can request `EmptyWorkingSet` for one eligible background process or delete reviewed, old files in the fixed `%TEMP%` location; working-set trim has no exact undo and temporary-file deletion is permanent. Reviewed File Intelligence cleanup is different: live cleanup moves the file into protected quarantine, and the action can be restored with its exact action ID.
- Before/after system metrics are observations, not proof that a trial caused an improvement. Synthetic demo metrics are never benchmarks.

## Privacy and storage

System Intelligence data is stored under **%LOCALAPPDATA%\Kshetrajna**. Its records contain UTC timestamps, executable basenames, PIDs and creation tokens, CPU/RAM measurements, app transitions, idle duration, workspace names, and decisions. System telemetry does not store window titles, URLs, document names, command lines, keystrokes, clipboard content, screenshots, or full executable paths. File Intelligence is a separate opt-in catalog: it stores approved root paths, relative file names/paths, metadata, bounded excerpts from allowed text files, and reviewed file-action journals. Live cleanup moves reviewed files into the protected `.kshetrajna-quarantine` directory under their approved root; quarantine content is not indexed or offered as a cleanup candidate.

Default retention is seven days, selectable from one to thirty. Pausing stops new observation; prior history remains until retention or deletion. Deletion restores active trials first, then removes observations, derived patterns, decisions, and saved workspaces. File Intelligence catalog/root deletion is blocked while unresolved file-action or quarantine records remain; restore those records first. Diagnostic logs and preferences remain. The reviewed cleanup bridge has no permanent final-purge API or CLI command; the legacy generic file-action DELETE endpoint remains a separate explicit permanent-delete capability.

SQLite is not application-encrypted. The boundary is the current Windows user profile and a loopback-only HTTP service. Host checks, matching Origin, per-session tokens, strict local asset loading, and request size limits protect dashboard mutations from ordinary cross-site requests. Programs already running as the same user are not isolated from this prototype.

## Technology choices

**Python 3.13 + standard library**, **ctypes/Win32**, **SQLite**, and **HTML/CSS/JavaScript/SVG**. There are zero runtime package dependencies. This keeps the project offline-capable and avoids architecture-specific third-party wheels. A small local HTTP server hosts the dashboard; it is not designed for public deployment.

On Snapdragon X, use the ARM64 bundle or native ARM64 Python for source launch. The Windows API bindings use pointer-sized handles and counters. Published 0.4.3 Windows x64/ARM64 package qualification is recorded in [the validation history](docs/VALIDATION.md); it does not qualify later source changes. Current cleanup changes still require native Windows CI and device checks before release. Generic ARM64 runner compatibility is not a Snapdragon model-speed or battery benchmark. Follow [the device checks](docs/SNAPDRAGON.md) for your laptop.

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

# Preview reviewed cleanup candidates (project trees stay protected)
python -m kshetrajna.cli cleanup empty --url http://127.0.0.1:8765 --root-id 3
python -m kshetrajna.cli cleanup duplicates --url http://127.0.0.1:8765 --root-id 3

# Delete only after reviewing the preview and confirming the exact file ID
python -m kshetrajna.cli cleanup_empty_delete --url http://127.0.0.1:8765 `
  --file-id FILE_ID --confirm

# Review the protected quarantine record returned by that deletion
python -m kshetrajna.cli quarantine list --url http://127.0.0.1:8765 --root-id 3

# Restore the exact action; this is not a permanent final purge
python -m kshetrajna.cli quarantine restore --url http://127.0.0.1:8765 `
  --action-id ACTION_ID --confirm
```

For a subprocess protocol, run `python -m kshetrajna.jarvis --url http://127.0.0.1:8765 --jsonl` and send one bounded JSON object per line, such as `{"command":"discover_folders"}`. For in-process integration, import `JarvisHttpClient` or `JarvisCommandBridge` from `kshetrajna.jarvis`. The bridge accepts loopback URLs only and requires explicit `confirm: true` for apply, undo, cleanup quarantine, and quarantine restore. A successful live cleanup response uses `deleted:true` and `quarantined:true`; it removes the original path but does not permanently purge the content. The current bridge exposes restore, not a final-purge command. The legacy generic file-action DELETE route remains a separate permanent-delete API. The corresponding private recovery API is `GET /api/files/quarantine` followed by `POST /api/files/quarantine/restore` with `{"action_id":"ACTION_ID","confirm":true}`.

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

Specific Snapdragon device measurements, controlled performance experiments, signed production installers, encrypted storage, descendant-aware recovery, real desktop window placement/launch integration, and a validated learned context model remain future work. Sort AI is integrated as a deterministic local provider; semantic embeddings and NPU inference are not implemented. See the [feature matrix](docs/FEATURE_MATRIX.md) for exact phase boundaries.

MIT license. See [LICENSE](LICENSE).
