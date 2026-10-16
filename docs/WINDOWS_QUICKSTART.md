# Run Kshetrajna on your own PC

The public website links to Windows downloads. Each visitor runs their own
local dashboard, with observations and reviewed actions on their own device.

**Choose the version deliberately:** the downloads below are historical
**0.4.3 unsigned beta archives**. Current source is the **0.5.0 baseline** and
adds reviewed quarantine/recovery, health readings, Basic/Expert navigation and
a narrow [canonical action API](ACTIONS.md); the old packages do not include
those changes. [Final gates](FINAL_RELEASE.md) remain, not a released 1.0.0. Use the source
instructions below for current behavior.

## Historical beta launch — Python included

1. Download [Intel/AMD x64](https://github.com/Team-SankatMochan/KSHETRAJNA-OS/releases/download/v0.4.3/Kshetrajna-Windows-x64.zip)
   or [Snapdragon/ARM64](https://github.com/Team-SankatMochan/KSHETRAJNA-OS/releases/download/v0.4.3/Kshetrajna-Windows-arm64.zip).
   Choose the architecture shown in Windows Settings → System → About.
2. In File Explorer choose **Extract All**, then open the **Kshetrajna** folder.
   Running scripts directly inside the archive will not work.
3. Open **Launch.cmd**. The browser opens your local dashboard. Python is
   bundled; no Git, account, pip packages or administrator launch is required.
4. Optionally open **Install.cmd** for a verified per-user installation and
   desktop shortcut. Existing versions and local history are preserved.
5. Open **Privacy**, enable collection, and review the local telemetry consent.
   Enable maintenance and/or reviewed app controls separately when desired.
   Enabling a control executes nothing.
6. Open **Chat** for app/memory reviews and maintenance, or **Sort AI** for
   approved-folder indexing and sorting previews. Actual modifications require
   review and approval (including reviewed sorting batches). Eligible file moves support undo; closing
   apps, working-set trims and permanent deletion have no exact undo.
7. Use **Quit Kshetrajna** in Privacy to stop the background app and attempt
   restoration of active reversible priority trials. Closing a browser tab
   leaves the app running.

These are unsigned beta archives with setup scripts, not signed MSI/EXE
installers. Download from the [project release](https://github.com/Team-SankatMochan/KSHETRAJNA-OS/releases/tag/v0.4.3);
matching checksum files are provided. See [packaging details](WINDOWS_BUNDLES.md).
Checksums detect corruption but do not replace signing or establish trust in an
arbitrary archive. Do not elevate the whole application or expose its port to
other computers to work around an error.

## Optional AI

Install [Ollama](https://ollama.com/download/windows) and explicitly download
a local model suited to the device. In Chat, select the installed model and
choose **Enable local AI**. No API key is needed. Cloud models are rejected.
PC tools work without a model using deterministic replies. Neither Ollama nor
a model is bundled or downloaded automatically. ARM64 core checks do not
qualify an external model runtime's inference performance on Snapdragon.
See [local assistant details](LOCAL_ASSISTANT.md).

## Return from the hosted page

Click **Open my local dashboard** after launching. `127.0.0.1` refers to the
visitor's own device. The link opens a separate local tab. The hosted site
does not fetch local data, copy approval tokens or proxy PC commands.

The default address is `http://127.0.0.1:8765/#chat`. If another port is used,
open the address shown by **Console.cmd**.

## Troubleshooting

- **Startup fails:** open **Console.cmd** to see the error. Extract the whole
  archive again if files are missing; do not mix architectures or versions.
- **Address unreachable:** launch the app on the same PC where you click the
  link. A phone or another computer's loopback address refers to that device.
- **Port busy:** run `Console.cmd --port 8767` and use the displayed address.
- **No model:** PC tools still work. Install/select a model separately for
  general AI conversation.
- **No app candidates:** enable collection and allow multiple fresh samples.
  Low CPU alone never authorizes termination.
- **Stop:** use Quit in Privacy, or Ctrl+C for a Console.cmd launch. Review
  any unresolved recovery record on the next launch.

## Update and remove

Quit the app, download a new release and extract it into a new folder. Run
its Install.cmd if desired. Installed versions use separate immutable folders
under `%LOCALAPPDATA%\Programs\Kshetrajna`; setup does not overwrite old ones.
Preferences/history remain under `%LOCALAPPDATA%\Kshetrajna`.

After quitting, remove an old extracted/installed version and its shortcut
manually. Use the dashboard's deletion controls for history after resolving
pending recovery records. Removing program files does not delete history or
original files in approved folders. Automatic updates are not implemented.

## Alternative: run from source

Install [Python 3.13](https://www.python.org/downloads/release/python-31316/)
for your architecture, then [download source from main](https://github.com/Team-SankatMochan/KSHETRAJNA-OS/archive/refs/heads/main.zip),
extract it and open **start_here.cmd**. The launcher selects installed Python
3.13, opens the app and can start an already installed Ollama runtime. Keep
the launch window open. No Git installation is required for a source download.
The separate Python ARM64 installer is marked experimental by upstream.

Use **3.13.16** for reproducible checks; the launcher selects the installed 3.13
series and does not enforce that patch or automatically install Python. `run.cmd`
uses `python` on PATH, so check `python --version` and architecture if multiple
interpreters are installed. `run_demo.cmd` gives an isolated synthetic session;
it cannot modify Windows or personal files. `run_tests.cmd` runs offline tests.

### Current source safety and privacy

- Approve each file location before scanning. Current scanning persists metadata
  **and text/Office/PDF excerpts**; metadata-only default and separate content
  consent are planned, not present. See [privacy](PRIVACY.md).
- Reviewed empty/duplicate cleanup moves content into protected quarantine with
  an action ID for restore. **Bytes remain stored; this is not capacity
  recovered.** Do not manually delete quarantine or recovery databases. The
  reviewed flow has no final purge; legacy generic permanent DELETE and
  temporary-file maintenance are different irreversible operations.
- A working-set trim does not release committed allocations or guarantee a
  speedup; standby memory is already reusable. No unsupported standby purge or
  automatic process termination is offered. A close request is not confirmed
  exit, and a Windows-tool launch is not completed cleanup.
- Current source opens in Basic by default. Expert reveals advanced controls
  without granting permission; History & Recovery is available in both. Service
  execution requires literal confirmation, and a new CLI/JSONL root requires
  explicit consent. A permission off/on cycle invalidates relevant old plans.
  Permanent DELETE additionally needs its default-off service danger permission.
  Local AI remains optional and cannot approve file access or arbitrary execution.
- System Health and Investigate Memory separate physical RAM, nullable commit/
  cache/OS low-memory readings and fixed system-volume disk capacity. Unknown or
  stale is not zero. Delayed action measurements are still pending; a current
  health reading is not proof that an action helped.
- Delete recorded history clears telemetry/derived history and chats, not the
  canonical action ledger. Explicit canonical erasure is separate and preserves
  unresolved recovery and audit/replay tombstones. See [privacy](PRIVACY.md).
- If reporting a fault, inspect a minimized copy first: exception logs may
  contain paths. There is no blanket redaction guarantee. See
  [crash reporting](CRASH_REPORTING.md).

## Snapdragon qualification

The historical **0.4.3** 162-test source suite and exact packaged-app checks
passed on native Windows x64 and ARM64 CI. Those checks qualify those artifacts,
not current quarantine/final changes or Snapdragon battery, NPU or model speed.
The coordinator reports **453 combined pinned-Linux tests: 450 passed, three
skipped**. Last native evidence remains 276 passed on x64 CPython 3.13.3 (not the
pin); there is no newer native qualification. See [validation](VALIDATION.md).
Current pinned-runtime native x64/ARM64 packages, signed installation, real-device
measurements, accessibility and ordinary-user validation remain final gates.
Prepared release tools include MSI Upgrade-row audits and lifecycle automation;
their reported 26-pass fixture suite is not native signing/installation evidence.
Current browser/NVDA, ARM64 and large benchmarks have not run. Signing access,
hardware and participants are blockers until available, not passed checks.
For device measurements use [the Snapdragon guide](SNAPDRAGON.md). In a bundle, run
`runtime\python.exe -B -m kshetrajna.diagnostics --live --require-native-arm64 --output device-check.json`.
The diagnostic is read-only and reports failures explicitly.
