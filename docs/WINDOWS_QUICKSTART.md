# Run Kshetrajna on your own PC

The public website links to Windows downloads. Each visitor runs their own
local dashboard, with observations and reviewed actions on their own device.

## First launch — Python included

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
   individual review and approval. Eligible file moves support undo; closing
   apps, working-set trims and permanent deletion have no exact undo.
7. Use **Quit Kshetrajna** in Privacy to stop the background app and attempt
   restoration of active reversible priority trials. Closing a browser tab
   leaves the app running.

These are unsigned beta archives with setup scripts, not signed MSI/EXE
installers. Download from the [project release](https://github.com/Team-SankatMochan/KSHETRAJNA-OS/releases/tag/v0.4.3);
matching checksum files are provided. See [packaging details](WINDOWS_BUNDLES.md).

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

## Snapdragon qualification

The 162-test source suite and exact packaged-app checks passed on native
Windows x64 and ARM64 CI. This establishes compatibility of those tested
packages, not a Snapdragon battery, NPU or model-speed benchmark. For device
measurements use [the Snapdragon guide](SNAPDRAGON.md). In a bundle, run
`runtime\python.exe -B -m kshetrajna.diagnostics --live --require-native-arm64 --output device-check.json`.
The diagnostic is read-only and reports failures explicitly.
