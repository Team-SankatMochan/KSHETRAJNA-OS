# Windows on Snapdragon: setup and qualification

## Compatibility assessment

Target: **Windows 11 on ARM64, native CPython 3.13.x ARM64** (standard GIL build). This is a Windows application, not an Android/Snapdragon phone application.

| Component | Assessment | Evidence / remaining check |
|---|---|---|
| Context, workflow patterns, prediction | Architecture-independent Python | Unit tests and fixed synthetic benchmark |
| SQLite, local HTTP, dashboard | Standard library and static assets | API/storage tests; included in ARM64 Python |
| CPU/RAM/process/activity telemetry | Uses desktop Win32 APIs, no x64-only extensions | Explicit function signatures and pointer-sized HANDLE/SIZE_T; validate on your device |
| Windows structures | Expected LLP64 layouts on x64/ARM64 | Device check asserts FILETIME 8, LASTINPUTINFO 8, MEMORYSTATUSEX 64, PROCESS_MEMORY_COUNTERS 72 bytes |
| Native priority trials | Opt-in, user-approved, off by default | Logic tested with doubles; real ARM64 actuation not independently qualified |
| Qualcomm NPU | Not used | No NPU/TOPS score or acceleration claim |

The 139-test source suite and the self-contained ARM64 package passed on a native Windows ARM64 CI runner, including read-only telemetry diagnostics. The supplied submission brief separately reports an earlier user-confirmed 61-test run on an HP Snapdragon X laptop. Generic ARM64 CI does not reproduce that device or measure its model speed, battery or NPU. **Snapdragon hardware performance needs on-device measurements.** x64 Python can run through Windows emulation; its results are not native ARM64 results. The checker uses IsWow64Process2 to distinguish these cases.

## Download the native package

Download the [v0.4.1 ARM64 archive](https://github.com/Team-SankatMochan/KSHETRAJNA-OS/releases/download/v0.4.1/Kshetrajna-Windows-arm64.zip), extract it and open **Launch.cmd**. Native ARM64 Python is included. Optional **Install.cmd** creates a verified per-user copy and shortcut. See [Windows quick start](WINDOWS_QUICKSTART.md).

From the extracted Kshetrajna folder, run the read-only device check:

```powershell
.\runtime\python.exe -B -m kshetrajna.diagnostics --live --require-native-arm64 --output device-check.json
```

## Pull and run on the Snapdragon computer

1. Install Git and **Python 3.13.x ARM64** from the official Python Windows downloads. The 3.13 ARM64 installer is labelled experimental. Add the chosen interpreter to PATH; do not accidentally select an existing x64 Python or 3.14 runtime.
2. If the checkout requires authentication, use a GitHub account with repository access.
3. In PowerShell:

~~~powershell
git clone https://github.com/Team-SankatMochan/KSHETRAJNA-OS.git
cd KSHETRAJNA-OS
# For an existing checkout instead: git pull --ff-only
python --version
.\run_tests.cmd
.\check_device.cmd --require-native-arm64
.\run.cmd
~~~

No pip/npm install or administrator access is needed for the source launch. If you installed several Pythons, activate an ARM64 3.13 virtual environment first so `python` selects it.

The device check takes six read-only telemetry samples and runs a fixed 12,000-sample prediction benchmark. The current repository suite contains 139 tests; the submission materials refer to an earlier 61-test device run. The check does not enable continuous collection, modify priorities or load saved history. The source launcher writes `reports/device-check.json`, which Git ignores. A nonzero exit means a required check failed; the JSON identifies it. Do not describe an emulated result as native.

The live app opens http://127.0.0.1:8765/. On a fresh computer, choose **Enable collection**. History belongs to that Windows user at `%LOCALAPPDATA%\Kshetrajna`, independently of the repo and this development computer. Keep priority trials off for initial qualification.

## Scores to compare

There is no invented aggregate compatibility or Snapdragon score. Record these measured metrics under similar power mode, AC/battery state and background workload:

- **Pattern latency:** median/p95 milliseconds over five runs after warm-up, with the same fixed synthetic history.
- **Collector latency:** median/p95 milliseconds per real Windows sample.
- **Diagnostic process CPU:** CPU seconds and percent of one core during one-second sampling, excluding warm-up. This is not the normal five-second dashboard's overhead.
- **Sampled working set:** diagnostic process memory after the synthetic benchmark; it includes the Python runtime and allocator retention, not only the live collector.
- **Prediction accuracy and coverage:** synthetic replay in the report is a functional check only. Use the live dashboard after repeated personal workflows for history-based results. These are not calibrated confidence or evidence of performance gains.

After running, inspect the JSON and share it for comparison if desired. It contains environment versions and aggregate diagnostics, with no app names, PIDs, paths, titles, URLs or user history. Reports are never uploaded automatically.

## Manual device acceptance

Use two or three everyday apps for several minutes. Confirm foreground names change, CPU/RAM values update, idle status responds, Pause stops new samples, and Delete recorded history removes derived patterns. Compare system readings with Task Manager allowing for sampling and normalization differences. Protected processes can be omitted. Do not expect predictions immediately: they require repeated stable visits. Closing the terminal ends the source-launched collector; the browser alone is only the dashboard.

## Hosting

**Pull and run locally is the intended deployment.** The application already has a browser interface backed by a local Windows collector. A publicly hosted page cannot call the user's Win32 APIs or inspect arbitrary processes. Cloud hosting could offer a landing page or a separate synthetic demo, but real telemetry would still require a local agent and additional authentication/privacy design. Do not expose this prototype HTTP service beyond loopback.

The current [source CI](https://github.com/Team-SankatMochan/KSHETRAJNA-OS/actions/runs/38474805570)
passed Windows x64, Windows ARM64 and Linux. The exact released packages passed
[native x64/ARM64 package CI](https://github.com/Team-SankatMochan/KSHETRAJNA-OS/actions/runs/38474805694).
Device-specific performance and external local inference remain separate checks.

## References

- [Official Python Windows downloads](https://www.python.org/downloads/windows/)
- [Python 3.13 Windows runtime documentation](https://docs.python.org/3.13/using/windows.html)
- [Microsoft: Windows on Arm](https://learn.microsoft.com/en-us/windows/arm/overview)
- [Microsoft: emulation on Arm](https://learn.microsoft.com/en-us/windows/arm/apps-on-arm-x86-emulation)
- [Microsoft: IsWow64Process2 architecture detection](https://learn.microsoft.com/en-us/windows/win32/api/wow64apiset/nf-wow64apiset-iswow64process2)
- [GitHub: standard ARM runners for public repositories](https://github.blog/changelog/2025-08-07-arm64-hosted-runners-for-public-repositories-are-now-generally-available/)
