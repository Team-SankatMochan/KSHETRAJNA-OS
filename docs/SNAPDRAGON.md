# Windows on Snapdragon: setup and qualification

## Compatibility assessment

Target: **Windows 11 on ARM64, native CPython 3.13.x ARM64** (standard GIL build). This is a Windows application, not an Android/Snapdragon phone application.

| Component | Assessment | Evidence / remaining check |
|---|---|---|
| Context, workflow patterns, prediction | Architecture-independent Python | Unit tests and fixed synthetic benchmark |
| SQLite, local HTTP, dashboard | Standard library and static assets | API/storage tests; included in ARM64 Python |
| CPU/RAM/process/activity telemetry | Uses desktop Win32 APIs, no x64-only extensions | Explicit function signatures and pointer-sized HANDLE/SIZE_T; validate on your device |
| Windows structures | Expected LLP64 layouts on x64/ARM64 | Device check asserts FILETIME 8, LASTINPUTINFO 8, MEMORYSTATUSEX 64, PROCESS_MEMORY_COUNTERS 72 bytes |
| Native priority trials | Opt-in, user-approved, off by default | Logic tested with doubles; real ARM64 actuation not qualified |
| Qualcomm NPU | Not used | No NPU/TOPS score or acceleration claim |

The design is ARM64-compatible, but the development machine is x64. **Snapdragon hardware performance is unmeasured until you run the device check.** x64 Python can run through Windows 11 emulation; its results are not native ARM64 results. The checker uses IsWow64Process2 to distinguish these cases.

## Pull and run on the Snapdragon computer

1. Install Git and **Python 3.13.x ARM64** from the official Python Windows downloads. The 3.13 ARM64 installer is labelled experimental. Add the chosen interpreter to PATH; do not accidentally select an existing x64 Python or 3.14 runtime.
2. Authenticate GitHub using an account with access to this private repository.
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

The device check takes six read-only telemetry samples and runs a fixed 12,000-sample prediction benchmark. It does not enable continuous collection, modify process priorities or load your recorded history. It writes `reports/device-check.json`, which Git ignores. A nonzero exit means a required check failed; the JSON identifies it. Do not describe an emulated result as native.

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

CI includes Windows x64 and Linux tests. Standard Windows ARM64 CI is conditional on public repository visibility; it is skipped while this repo is private. No visibility, paid runner or device enrollment is changed automatically.

## References

- [Official Python Windows downloads](https://www.python.org/downloads/windows/)
- [Python 3.13 Windows runtime documentation](https://docs.python.org/3.13/using/windows.html)
- [Microsoft: Windows on Arm](https://learn.microsoft.com/en-us/windows/arm/overview)
- [Microsoft: emulation on Arm](https://learn.microsoft.com/en-us/windows/arm/apps-on-arm-x86-emulation)
- [Microsoft: IsWow64Process2 architecture detection](https://learn.microsoft.com/en-us/windows/win32/api/wow64apiset/nf-wow64apiset-iswow64process2)
- [GitHub: standard ARM runners for public repositories](https://github.blog/changelog/2025-08-07-arm64-hosted-runners-for-public-repositories-are-now-generally-available/)
