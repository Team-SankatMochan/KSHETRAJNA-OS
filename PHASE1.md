# Kshetrajna

**Historical Phase 1 design.** The current submission adds intelligence, consent-based trials, evaluation, and workspace boards. See README.md and SUBMISSION.md for current behavior and limitations.

> **The intelligence that knows the field.**

Kshetrajna is a privacy-first adaptive intelligence layer for Windows. Phase 1 is its **observe-only foundation**: it shows local CPU and memory pressure, running app resource use, foreground app changes, and idle transitions. It does not change process priority, trim memory, modify power policy, move windows, or learn a model yet.

Collection is **off on first launch**. The dashboard gives the user an explicit enable/pause control, separate process and foreground tracking controls, a retention choice, and a delete-history action. All data stays in the current Windows user's local profile.

## Run on Windows

1. Install **Python 3.13** for your machine. On Snapdragon X, choose the **Windows ARM64** installer. The official 3.13 ARM64 installer is labeled experimental, so validate on the exact device before presenting it as production-ready.
2. From this directory, run `run.cmd`. It uses the `python` executable on your path and opens `http://127.0.0.1:8765/`.
3. Review the privacy controls, then choose **Enable collection**. Close the terminal or press Ctrl+C to stop the app.

To use another port or avoid opening a browser, run `run.cmd --port 8766 --no-browser`. No administrator rights, external services, API keys, package installs, or build step are required.

### Tests

From this directory in PowerShell:

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

The tests cover first-launch consent, settings validation, collection and activity events, retention and deletion, and the local dashboard's write protection. They are written for Windows x64 and ARM64 with Python 3.13; ARM64 execution still needs a device check.

## Phase 1 architecture

```text
Win32 read-only APIs
  ├─ system CPU + physical RAM
  ├─ process IDs, image basenames, CPU time + working set
  └─ foreground PID + last-input age
              │
              ▼
        collector loop ────── config.json (consent and limits)
              │
              ▼
     SQLite event store (events.db)
              │
              ▼
  loopback HTTP API + static dashboard
       127.0.0.1 only
```

The collector samples at a user-selected interval (default **5 seconds**) only while enabled. A lock serializes sampling, settings changes, and deletion, so pausing cannot leave a pending write. Each cycle prunes records older than the selected retention period (default **7 days**, maximum **30**), including while collection is paused. The dashboard polls local state every 5 seconds. The collector and dashboard share one process and stop together.

### Locked technology choices

| Layer | Choice | Why |
|---|---|---|
| Runtime | CPython **3.13.x**, 64-bit, native ARM64 on Snapdragon X | One readable codebase; the official 3.13 release provides a Windows ARM64 installer. Version range is fixed in `pyproject.toml`. |
| Windows telemetry | `ctypes` calls to documented **Win32** APIs | Read-only, works without drivers or admin rights, and avoids third-party native wheels that may vary by architecture. |
| Storage | Standard-library **SQLite 3** | Embedded, transactional, local, queryable, and easy to delete or inspect. No database service. |
| Backend | Standard-library `http.server`, bound to **127.0.0.1** | Enough for a single-user Phase 1 dashboard without another runtime or network dependency. |
| Frontend | Plain HTML, CSS, JavaScript, and SVG | Local static assets; no CDN, build tool, tracking, or framework bundle. |
| Configuration | Validated JSON in `%LOCALAPPDATA%\Kshetrajna` | Explicit preferences, atomic writes, easy user inspection. |
| Logs | Standard-library rotating file log | Bounded operational diagnostics, separate from telemetry. |
| Tests | Standard-library `unittest` and fakes | Tests the privacy and storage boundaries without extra packages. |
| Deployment | Source plus `run.cmd` | Runs on x64 or ARM64 with the matching Python interpreter; packaging can be addressed after device validation. |

No ML framework, NPU runtime, privileged service, installer, or system actuator is part of Phase 1. Later phases can consume the stored observations, but any proposed change must have an explanation, explicit approval, measured outcome, and rollback path before an actuator is added.

### What is stored

| Record | Fields | Notes |
|---|---|---|
| System sample | UTC time, CPU %, total and available physical RAM | CPU is measured between samples; the first reading is zero because there is no earlier baseline. |
| Process sample | PID, executable **basename**, CPU share %, working-set bytes | Up to 12 processes by default, configurable internally up to 30. Protected or inaccessible processes are skipped. CPU share is normalized to total system processor capacity. |
| Foreground sample | PID, executable basename, seconds since last input | No window titles. Last-input time is session-scoped. |
| Activity event | foreground changed, became idle, became active | Idle threshold defaults to 60 seconds. It records transitions, not input contents. |

Kshetrajna never reads or stores URLs, document names, window titles, command lines, clipboard contents, screenshots, keyboard events, or the full executable path. The app name itself can reveal information, so foreground and process tracking can each be disabled. The dashboard displays past history after pausing until the user deletes it or retention expires.

Data is in `%LOCALAPPDATA%\Kshetrajna\events.db`; preferences are in `config.json`; operational errors go to `kshetrajna.log` (rotated at 500 KB with two backups). The log does not intentionally include sampled app names or metrics. **Delete recorded history** removes the event and sample tables and compacts the database; it does not reset preferences or erase diagnostic logs. SQLite data is protected by the Windows user profile, not encrypted by this prototype. Full-disk encryption is recommended if local-at-rest protection is required.

The dashboard accepts connections only on the loopback interface. It rejects unexpected Host headers; settings and deletion additionally require the page's session token and a matching local Origin. Static assets make no external requests. Another program running as the same Windows user can still access local files or the loopback service, so this is a local privacy boundary, not a defense against a compromised user account.

### Source map

```text
src/kshetrajna/
  telemetry.py    read-only Win32 probes
  collector.py    sampling loop and activity transitions
  storage.py      SQLite schema, queries, retention, deletion
  config.py       validated opt-in settings
  server.py       loopback dashboard and API
  web/            local dashboard assets
tests/            privacy and lifecycle tests
```

### Scope and known limits

- The dashboard shows a **sampled approximation**, not Task Manager's exact accounting. Short process bursts between samples can be missed. Working set is resident process memory, not reclaimable memory or a promise of available RAM.
- Unprivileged access means some protected or system processes will be absent. Foreground tracking observes the current interactive session; it does not inspect other user sessions.
- Windows `GetSystemTimes` reports the calling processor group on systems with more than 64 logical processors. Snapdragon X devices are below that limit, but the implementation is not a whole-machine profiler for larger multi-group systems.
- This prototype has been exercised on Windows x64. **Native ARM64 execution on a Snapdragon X device remains a device-validation gate**, including testing the ARM64 Python installer and Win32 calls.
- There is no automatic startup, background service, export, cloud sync, model training, or system modification.

## Sources behind the stack

- [Python 3.13 release, including ARM64 installer](https://www.python.org/downloads/release/python-3130/)
- [Python Windows installation and ARM64 package notes](https://docs.python.org/3.13/using/windows.html)
- [Microsoft's Windows on Arm support overview](https://learn.microsoft.com/en-us/windows/arm/overview)
- [Microsoft's `GetSystemTimes` contract and processor-group caveat](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-getsystemtimes)
- [Microsoft's process memory API](https://learn.microsoft.com/en-us/windows/win32/api/psapi/nf-psapi-getprocessmemoryinfo)
- [Microsoft's process enumeration guidance](https://learn.microsoft.com/en-us/windows/win32/psapi/enumerating-all-processes)
- [Microsoft's session-scoped last-input API](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getlastinputinfo)

## License

MIT. See `LICENSE`.
