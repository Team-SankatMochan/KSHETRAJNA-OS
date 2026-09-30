# Validation record

Date: 30 September 2026. This records checks actually performed on the submission prototype.

## Environment

- Windows 11, build 26200, AMD64.
- CPython 3.13.3; no third-party runtime packages.
- Local browser dashboard served at 127.0.0.1:8766 in isolated synthetic demo mode.

## Results

| Check | Result |
|---|---|
| Python unittest suite | 35 tests passed, including pattern/prediction, privacy and device diagnostic checks |
| JavaScript syntax | Both dashboard scripts passed Node syntax checks |
| HTTP integration | Assets, security headers, Host/Origin/token validation, request validation, approval, undo and report export passed |
| Privacy and persistence | Consent, retention, deletion, migration and instance lock tested |
| Action lifecycle | Journal before apply, stale proposal rejection, single active trial, expiry, restart recovery and failed restoration tested |
| Native driver logic | Controlled doubles verified allowed priority changes, preservation of external changes and PID reuse rejection |
| Windows observation | Live read-only CPU, physical RAM, process and idle samples returned on this machine |
| Browser interaction | Simulated approval, saved workspace activation, manual rollback and outcome display verified |
| Browser console | No errors observed during the interaction check |
| Workflow prediction | Sequence disambiguation, idle/gap boundaries, habit drift, ambiguity abstention and chronological replay verified with synthetic histories |
| Repository whitespace | git diff --check passed |

The browser trial displayed before/after observations with an explicit noncausal caveat. Synthetic readings do not demonstrate a performance improvement.

## Development-device baseline (x64, not Snapdragon)

The device checker passed on CPython 3.13.3, native Windows x64, with the expected Win32 structure sizes. Five timed runs of the fixed 12,000-snapshot synthetic pattern workload gave a median of **32.153 ms** and p95 of **36.321 ms**. Six real read-only telemetry samples gave a median collection time of **11.270 ms**, p95 **13.007 ms**, and diagnostic-process sampled working set **29.65 MiB**. CPU use during the one-second diagnostic sampling window was **0.772% of one core**. These short measurements are workload-dependent and do not represent Snapdragon results or normal dashboard overhead.

Foreground was unavailable in this noninteractive diagnostic runner (0/6 samples); foreground tracking was separately observed working in the user's live desktop dashboard. Device checks must be repeated in an interactive session on the Snapdragon computer. See [device setup and interpretation](SNAPDRAGON.md).

## Reproduce

1. Run `run_tests.cmd` from the repository.
2. Run `run_demo.cmd` and follow `SUBMISSION.md` for the demo flow.
3. Run `run.cmd` for real Windows observations; explicitly enable collection in Privacy controls. Live priority trials remain off unless separately enabled and individually approved.

## Qualification still required

- Native ARM64/Snapdragon hardware has not been tested.
- Real process priority changes were not executed against user applications during validation; native behavior was tested with controlled doubles.
- CI configuration is included, but a remote CI run has not been observed.
- No controlled benchmark, battery measurement, production security audit or installer qualification has been completed.
- Workspace adaptation currently changes the dashboard board. It does not arrange desktop windows.
- Recovery restores the selected process. Descendant inheritance and recovery while the app remains crashed need further engineering.

This is an end-to-end hackathon prototype covering the project phases, with these limits stated explicitly; it is not a production operating system.
