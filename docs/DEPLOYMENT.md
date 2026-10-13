# Local deployment and packaging

For the separately hosted interactive web demo, see [Vercel deployment](VERCEL.md).
The committed root `vercel.json` serves `web-demo`; live Windows functions remain local.

## Source launch

Prerequisite: Python 3.13 on Windows. The project has no third-party runtime
packages. From the repository directory:

```powershell
.\run_demo.cmd
.\run.cmd
.\run.cmd --no-browser --port 8767
```

The first command uses temporary synthetic `events.db` and `files.db` data.
The second uses `%LOCALAPPDATA%\Kshetrajna`, starts with collection disabled,
and requires live consent in the dashboard. Both services bind only to
loopback. Keep the terminal open and press Ctrl+C for graceful restoration and
shutdown.

PowerShell equivalents are:

```powershell
$env:PYTHONPATH = 'src'
python -m kshetrajna --demo
python -m kshetrajna
```

## Jarvis connection

Install the source package in a Python environment when `pip` is available:

```powershell
python -m pip install -e .
```

Then connect a Jarvis process to the already-running loopback dashboard:

```powershell
python -m kshetrajna.jarvis --url http://127.0.0.1:8765 discover_folders
python -m kshetrajna.jarvis --url http://127.0.0.1:8765 sort_preview --known-folder downloads --create-folders
python -m kshetrajna.jarvis --url http://127.0.0.1:8765 sort_apply --plan-id PLAN_ID --confirm
python -m kshetrajna.jarvis --url http://127.0.0.1:8765 sort_undo --plan-id PLAN_ID --confirm
```

`kshetrajna-sort` is installed as a convenience entry point and defaults to
`sort_preview`; `python -m kshetrajna.cli sort --url ...` is the equivalent
source command. A JSON-lines mode is available for assistant subprocesses.
The bridge discovers the dashboard token from the local page, validates that
the target is loopback, and never exposes arbitrary commands. Creating a
preview stores a plan; specifying a new folder also approves indexing it.
Original files are changed only by apply or undo, which require explicit
confirmation.

## Snapdragon X / Windows ARM64

1. Install the official native ARM64 Python 3.13 distribution where available.
2. Open the repository in a normal user PowerShell.
3. Run `check_device.cmd --require-native-arm64`.
4. Review `reports\device-check.json` before making native performance or
   actuation claims.
5. Run `run_demo.cmd` first, then `run.cmd` and explicitly enable collection.

The application uses standard-library Python and pointer-sized `ctypes` Win32
declarations, so it avoids third-party native wheels. The NPU is not used and
no accelerated provider is claimed. The owner-reported Snapdragon test result
is retained as a report; it has not been independently reproduced in this
checkout.

## Packaging status

The reproducible packaging path is the source launcher plus the Python
interpreter. No installer, signed executable, or release artifact is claimed
in this checkout. A future installer must preserve per-user data separation,
loopback-only binding, explicit startup consent, clean shutdown, and separate
x64/ARM64 qualification. Missing signing certificates do not change the
source-launch behavior.

## Uninstall and data removal

Stop the process, remove the repository or launcher shortcut, and use the
dashboard deletion controls for stored data. Removing the application does not
automatically delete `%LOCALAPPDATA%\Kshetrajna`; review and delete that folder
only after unresolved recovery records are handled. Original approved-folder
files are never removed by catalog deletion.
