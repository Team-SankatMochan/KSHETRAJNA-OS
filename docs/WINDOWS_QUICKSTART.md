# Run Kshetrajna on your own PC

The Vercel page is the public entry point. Every Windows user can download
the application and run their own loopback dashboard. Real observations and
reviewed actions operate on that user's device, with local consent.

## First launch

1. Install [Python 3.13](https://www.python.org/downloads/release/python-31316/).
   Choose Windows 64-bit for Intel/AMD or ARM64 for Snapdragon. The official
   Python 3.13 ARM64 installer is marked experimental. Do not select 32-bit.
   The launcher selects an installed 3.13 even when a newer Python is default.
2. [Download the current app source](https://github.com/Team-SankatMochan/KSHETRAJNA-OS/archive/refs/heads/experimental/sort-ai-jarvis.zip).
   In File Explorer choose **Extract All**, then open the extracted folder.
   Running scripts directly inside an archive will not work.
3. Double-click **start_here.cmd**. It checks Python, starts the local app, and
   opens its browser dashboard. It starts an already installed Ollama runtime
   when available. It does not install Python, packages or models automatically.
4. Keep the launch window open. Open **Privacy**, enable collection, and review
   the requested consent. Enable Windows maintenance and/or reviewed app controls
   separately when you want those actions. Enabling controls executes nothing.
5. Open **Chat** for memory/app review and maintenance, or **Sort AI** for
   approved folder indexing, sorting previews and supported file-move undo.
   Every actual modification requires review and approval.

This is a source bundle with a Python prerequisite. It is not a signed,
standalone executable installer. No Git installation or account is needed
to download this public repository. No administrator launch is required.

## Optional AI

Install [Ollama](https://ollama.com/download/windows) and explicitly download
a local model suited to the device. In Kshetrajna Chat, select that installed
model and choose **Enable local AI**. No API key is needed. The app rejects
cloud models. PC tools also work without a model, using deterministic replies.
Windows ARM64 core compatibility does not qualify an Ollama build or model's
inference performance; verify the selected runtime separately on Snapdragon.
See [local assistant details](LOCAL_ASSISTANT.md).

## Return from the hosted page

Click **Open my local dashboard** after launching. `127.0.0.1` refers to the
visitor's own device; it never points to the project owner's laptop. The link
opens a separate local browser tab. The hosted site does not fetch local data,
copy approval tokens, proxy commands, or expose the agent over the internet.
The local dashboard retains its same-origin and approval protections.

The default address is `http://127.0.0.1:8765/#chat`. If the app selected another
port or you supplied one, use the address shown in its launch window.

## Troubleshooting

- **Python missing:** install 3.13 for the correct architecture, then rerun
  `start_here.cmd`. The launcher uses `py -3.13` first and a verified 3.13
  `python` command as fallback. The Python install manager's automatic installs
  are disabled for this launch process.
- **Address cannot be reached:** the local app must be running on the same PC
  where you clicked the link. Check its window for startup errors. On another
  phone or PC, the link points to that other device.
- **Port busy:** run `start_here.cmd --port 8767` and use the displayed address.
- **No model:** local tools still work. Install a model separately and select
  it in Chat if you want general AI conversation.
- **No app candidates:** enable collection, allow time for multiple fresh
  samples, and ensure the relevant tracking controls are enabled. Low CPU
  alone never authorizes termination.
- **Stop:** Ctrl+C in the launch window allows graceful shutdown and restoration
  of any active reversible priority trial. Closing apps, trimming working sets
  and permanent file deletion have no exact undo.

## Update and remove

Stop the app, download a fresh bundle, extract it into a new folder, and run
the new `start_here.cmd`. Do not overwrite a running copy. Local preferences
and history live under `%LOCALAPPDATA%\Kshetrajna`, separately from source.
Remove an old source folder after stopping it. Use the dashboard's deletion
controls for history, handling unresolved recovery records first. Removing
source does not delete history or original files in approved folders.

## Snapdragon checks

Run `check_device.cmd --require-native-arm64` on the Snapdragon device before
claiming native runtime or performance results. See [ARM64 setup](SNAPDRAGON.md).
The latest code compatibility run passed Windows x64, Windows ARM64 and Linux;
it does not measure battery savings, NPU use or model speed on your laptop.
