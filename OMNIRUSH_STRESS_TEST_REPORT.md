# OmniRush Sandbox Stress-Test Report

**Repository:** Kshetrajna OS
**Date:** 2026-10-07
**Result:** **PASS WITH ISSUES**

## Executive Summary

The sandbox successfully exercised the repository's temporary filesystem, Python, SQLite, localhost HTTP, synthetic demo, file-indexing, Git, subprocess, and error-handling workflows.

The main issue found was a highly reproducible file-scan completion race. It was fixed during the follow-up remediation: terminal scan status is now published only after the internal scan gate and aggregate stats are finalized. The original race is retained below for historical context.

The active environment is Linux/WSL2 with CPython 3.11.2. The project declares CPython `>=3.13,<3.14` and requires Windows for live Win32 telemetry, so native Windows telemetry, ctypes ABI behavior, process priority changes, and ARM64 execution were not exercised here.

No real process priorities, Windows settings, services, registry values, user directories, secrets, or persistent application history were touched.

## Environment

| Item | Observed value |
|---|---|
| OS | Linux under WSL2 |
| Kernel | `6.6.87.2-microsoft-standard-WSL2` |
| Architecture | x86_64, 64-bit pointers |
| Python | CPython 3.11.2 at `/usr/bin/python` |
| Project requirement | Python `>=3.13,<3.14` |
| Working directory | `/mnt/c/Users/parra/OneDrive/Desktop/KshetRajna-OS` |
| PATH | Present; value not printed |
| Docker indicators | None observed (`DOCKER_HOST`, `container`, Kubernetes indicator absent) |
| Branch | `experimental/sort-ai-jarvis` |
| HEAD | `d91e553 Experimental push: Sort AI, Jarvis integration and cleanup` |

## Repository Baseline

### Project and framework

- Python `src/`-layout package and local Windows dashboard application.
- No external application framework.
- Backend uses `http.server.ThreadingHTTPServer`.
- Storage uses standard-library SQLite.
- Windows integration uses `ctypes` and Win32 APIs.
- Frontend uses plain HTML, CSS, JavaScript, and SVG.
- Runtime dependency list is empty.

### Packaging and commands

- Build backend: `setuptools.build_meta`.
- Documented installer: `python -m pip install -e .`.
- No npm/Bun project, `package.json`, JavaScript lockfile, or frontend build tool.
- Console entry points:

```text
kshetrajna        -> kshetrajna.__main__:main
kshetrajna-sort   -> kshetrajna.cli:main
kshetrajna-jarvis -> kshetrajna.jarvis:main
```

- Launchers:

```text
run.cmd          live application
run_demo.cmd     synthetic demo
run_tests.cmd    unittest discovery
check_device.cmd Windows/device diagnostics
```

- Documented test command:

```text
PYTHONPATH=src python -m unittest discover -s tests -v
```

### Source/data flow

```text
WindowsProbe or DemoProbe
        -> Collector
        -> events.db
        -> intelligence.py / patterns.py
        -> service.py
        -> server.py loopback API
        -> HTML/CSS/JS dashboard
```

File Intelligence follows a separate path:

```text
Approved root
        -> file_scanner.py
        -> files.db
        -> file_insights.py / search / sort_ai.py
        -> file_service.py / file_actions.py
        -> dashboard or Jarvis bridge
```

### Important modules

- `telemetry.py`: read-only Win32 CPU, memory, process, foreground, and idle probes.
- `collector.py`: background sampling, activity transitions, retention, and recovery ticks.
- `storage.py`: `events.db`, migrations, snapshots, decisions, workspaces, and retention.
- `intelligence.py` and `patterns.py`: explainable context, rankings, recurring workflows, and predictions.
- `service.py`: recommendations, approval, evaluation, feedback, workspaces, maintenance, and rollback.
- `actions.py`: simulated and native process-priority drivers.
- `server.py`: static dashboard and authenticated loopback JSON API.
- `file_scanner.py`: bounded traversal, text extraction, sensitive-file handling, and link filtering.
- `file_storage.py`: `files.db` schema and queries.
- `file_service.py`: scans, search, Sort AI plans, file actions, recovery, and undo.
- `file_actions.py`: identity-checked file rename/move proposals and rollback.
- `file_insights.py`: deterministic metadata/category insights.
- `sort_ai.py`: deterministic, reviewable organization suggestions.
- `jarvis.py`: named loopback/JSONL commands with explicit confirmation.
- `diagnostics.py`: platform and Win32 ABI diagnostics.

### Storage model

`events.db` contains snapshots, process samples, activity events, decisions, and workspaces. It uses retention pruning, secure deletion settings, and a migration for `created_ticks`.

`files.db` contains approved roots, files, scan state, file actions, Sort AI plans, and Sort AI suggestions. It uses SQLite WAL mode.

### HTTP and security boundaries

- Server binds to `127.0.0.1`.
- Host headers are checked.
- Mutating requests require a matching local Origin and server-generated session token.
- Request bodies are capped at 4096 bytes and transfer encoding is rejected.
- File roots are explicitly approved.
- File traversal rejects traversal paths, UNC paths, protected roots, symlinks, and reparse points.
- Text excerpts are bounded to 4096 bytes.
- Sensitive filenames and sensitive directories are not excerpted.
- Process actions use an allowlist, PID creation identity, session checks, background checks, and a narrow Normal-to-Below-Normal priority change.
- Demo mode uses simulated actions and temporary storage.

### CI

`.github/workflows/tests.yml` runs the standard-library `unittest` suite on Windows and Ubuntu with Python 3.13. Windows also runs live diagnostics. A public-repository Windows ARM64 job is configured conditionally.

## Test and Workflow Results

### Filesystem

The requested `.omnirush-sandbox-test/` tree was created inside the repository with root, nested, and deep files. Contents were read back, one file was renamed, one was copied, and the complete tree was deleted. Final absence was verified.

**Result: PASS.**

### Full test suite

The documented command was run with bytecode generation disabled:

```text
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v
```

Observed runs:

| Run | Result | Wall time |
|---|---|---:|
| Baseline 1 | 80 total: 77 passed, 1 error, 2 skipped | 25.36 s |
| Baseline 2 | 80 total: 78 passed, 2 skipped | 25.97 s |
| Modified tree | 81 total: 78 passed, 1 error, 2 skipped | 27.02 s |
| Final reverted tree | 80 total: 77 passed, 1 error, 2 skipped | 29.28 s |

No warnings were reported. The two skips were the Windows ABI and Windows reparse-point checks.

### Demo

Command:

```text
PYTHONPATH=src python -m kshetrajna --demo --no-browser --port 18766
```

Verified:

- Dashboard process starts.
- Port becomes reachable.
- `GET /` returns 200 and dashboard HTML.
- Static assets return successfully.
- `/api/state` returns synthetic demo data.
- `/api/report` returns the expected product/version/mode JSON.
- Demo scenario changes to `Meeting`.
- Synthetic file roots, file statistics, search, and insights load.
- Missing authentication returns 403.
- Python subprocess SIGINT shutdown returns exit code 0.
- No demo process or test port remains after cleanup.

Measured demo startup ranged from approximately 1.21 to 2.04 seconds.

### Live-server path

The live command was attempted safely without enabling collection:

```text
PYTHONPATH=src python -m kshetrajna --no-browser --port 18768
```

It exited with code 2:

```text
Windows is required for telemetry
```

This is the repository's explicit platform guard. No live telemetry or process adaptation was attempted.

Executing `run.cmd` through Linux Bash produced the expected Windows-batch mismatch (`@echo` and `setlocal` errors, literal `%*`, and exit code 2). The launcher is intended for Windows `cmd.exe`.

### HTTP boundary

Against a synthetic demo server:

| Operation | Result |
|---|---:|
| Normal `GET /` | 200 |
| Unexpected Host | 403 |
| Unknown route | 404 |
| POST without authentication | 403 |
| Invalid JSON | 400 |
| Oversized body | 413 |
| Malformed file ID | 400 |
| Unsupported PUT | 501 |
| Raw malformed HTTP | 400 response body |
| Graceful shutdown | Exit code 0 |

### SQLite

Validated using temporary databases:

- Schema initialization passed.
- Legacy `created_ticks` migration passed.
- Snapshot/process record and read passed.
- Invalid transaction rolled back correctly.
- File database initialized correctly.
- WAL mode was active.
- Temporary database cleanup passed.

### File Intelligence

A harmless temporary corpus included text, Markdown, JSON, Python, empty, nested, Unicode, spaced, sensitive, oversized, permission-restricted, and symlink cases.

Results:

- Scan reached `ready`.
- 11 files were indexed.
- Unicode and spaced names worked.
- Nested files worked.
- Symlink target was not indexed.
- Excerpts were capped at 4096 characters.
- Sensitive and oversized file excerpts were empty.
- Permission-restricted file excerpt was empty.
- Search worked.
- SQL-injection-shaped search input returned zero results without damaging the database.
- Insight generation worked.
- Traversal, nonexistent, file-as-root, and UNC roots were rejected.
- Root removal removed the index entry.
- Temporary corpus was deleted completely.

### Git

The initial ordinary Git status was clean. A temporary `.omnirush_git_test.txt` file containing `OMNIRUSH_GIT_SANDBOX_TEST` appeared as an untracked file, was verified, deleted, and the original status was restored.

Final Git state:

- No ordinary staged or unstaged changes.
- Branch unchanged.
- HEAD unchanged.
- Pre-existing ignored `reports/development-device.json` remains ignored.

### Process management and error handling

A harmless signal-aware long-running Python process stayed alive while another command executed, captured stdout/stderr, terminated cleanly, and returned exit code 0.

Intentional failures behaved normally:

- Nonexistent command: exit 127.
- Python exception: exit 1.
- Invalid file path: exit 1.
- Session remained responsive afterward.

### Network and persistence

Outbound test:

- DNS for `example.com`: succeeded.
- HTTPS request: succeeded with HTTP 200.
- Only 128 response bytes were read.

Persistence test:

- A temporary repository file survived inspection by a fresh independent Python process with identical contents.
- It was then deleted.
- A new OmniRush conversation/session was not created, so that specific boundary remains untested.

## Performance Measurements

| Measurement | Result |
|---|---:|
| Fresh `python -c pass` | 14.40–16.41 ms |
| Fresh imports of main/server/file service | 341.71–371.65 ms |
| Read 76 tracked files / 1.41 MB | 433.25 ms |
| Demo startup | 1.21–2.04 s |
| Localhost state median | 3.90 ms |
| Localhost state P95 | 4.24 ms |
| Localhost state maximum | 15.10 ms |

No non-sandboxed comparison was available. OmniRush tool-orchestration latency was not treated as a repository benchmark.

## Bugs and Findings

### File scan completion race

**Reproduction:**

```text
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 \
python -m unittest -q \
tests.test_sort_ai.SortAiTests.test_catalog_deletion_keeps_unresolved_action_recoverable
```

**Expected:** Once the root reports a non-scanning state, file actions can begin.
**Actual:** The database reports `ready`, but `_active_scans` still contains the root, so `create_proposal()` raises:

```text
ValueError: Wait for the current scan to finish before changing files.
```

The focused test passed 2/10 repetitions and failed 8/10. The full suite failed repeatedly on the same test.

The likely ordering issue is in `file_service.py`: root status is updated before the `finally` block removes the root from `_active_scans`. This is a repository race, with timing likely amplified by the current WSL environment.

**Severity:** Medium.
**Reproducibility:** High in this environment.

### Windows launcher under Linux

`run.cmd` is a Windows batch file and cannot be executed by Bash. This is an environment/platform mismatch, not evidence that the Windows launcher fails under Windows.

### Detached shell SIGINT

A Bash background launch inherited SIGINT-ignore behavior and did not stop when sent `kill -INT`. A Python subprocess harness received SIGINT correctly and shut the application down with exit code 0. This is shell/job-control behavior rather than a demonstrated application defect.

## Reversible Workflow Test

A temporary improvement was made to `file_insights.py`: case-insensitive extension stripping for display titles, with a focused regression test for `REPORT.PY`.

- Focused regression test passed.
- Modified full suite still exposed the scan race.
- Diff was inspected.
- Source and test changes were reverted completely.
- Baseline focused file-excerpt test passed after revert.
- The temporary improvement was reverted before the scan-race fix was implemented.

## Security Boundary Assessment

Observed harmless paths were broadly accessible:

- Repository directory listing: allowed.
- Repository parent listing: allowed.
- `/tmp/omnirush` files: allowed.
- Home metadata: allowed.
- Mounted project path under `/mnt/c`: allowed.

No secrets, credentials, tokens, API keys, cookies, or personal file contents were read. No harmless tested path returned a permission denial.

## Developer Experience

### Strong points

- Standard-library architecture is easy to exercise in isolation.
- Synthetic demo is safe and functional.
- Local HTTP behavior is observable without a browser.
- SQLite and file-indexing boundaries are testable with temporary fixtures.
- Git changes are easy to inspect and revert.
- Long-running Python processes are manageable through subprocesses.

### Fragile points

- The project-required Python 3.13 runtime is not present.
- Windows-specific code cannot be validated in this Linux session.
- Windows batch launchers are not portable to the active shell.
- The scan-state synchronization gap found during the stress test has been fixed.
- Native ARM64 and ctypes behavior remain untested here.
- The sandbox exposes more filesystem reachability than a strict repository-only model.

## Most Serious Finding

The scan-completion race was the most serious finding. It caused an 80% failure rate in the focused test and repeated full-suite failures before remediation. The application now clears its active-scan gate and updates scan aggregates before publishing terminal status.

## Remediation Follow-Up

The repository race was fixed in `src/kshetrajna/file_service.py`.

Changes:

- Terminal scan state is finalized under the service lock.
- The active-scan gate is cleared before the terminal status is published.
- Aggregate root statistics are updated before the terminal status is published.
- Added `test_ready_scan_clears_active_scan_gate` in `tests/test_sort_ai.py`.

Verification after the fix:

- New regression test: **10/10 passed**.
- Formerly flaky action test: **10/10 passed**.
- Full suite after scan-race fix: **81 total: 79 passed, 2 expected Windows/platform skips**.
- Full suite after case-insensitive insight-title fix: **82 total: 80 passed, 2 expected Windows/platform skips**.
- Full suite after explicit file consent and conversational sorting changes: **84 total: 82 passed, 2 expected Windows/platform skips**.
- Conversational sorting HTTP endpoint regression: passed; full suite now **85 total: 83 passed, 2 expected Windows/platform skips**.
- Full-suite test time: **16.646 seconds**.
- No test errors or warnings were reported.
- Three additional full-suite repetitions: **3/3 passed**, each with exit code 0.
- AST syntax scan: **41 Python files parsed successfully**.

The remaining Windows/WSL runtime limitations and detached-shell signal behavior are environment observations, not fixed by changing application behavior.

### Case-insensitive file insight titles

`file_insights.py` now strips a file extension without depending on the filename's letter casing. For example, `REPORT.PY` with extension metadata `py` now displays as `Report` instead of `Report.Py`. A focused regression test was added and the full suite passed 3/3 consecutive runs after the change.

### File consent and conversational sorting

The dashboard now opens an explicit file-access dialog before approving a root. The browser sends `consent: true`, and the HTTP API rejects root additions without that field. Scanning still does not mutate files.

Sort AI now groups its deterministic ideas by destination folder and asks one plain-language question per idea. The user can approve creating a folder and moving its files, or moving files into an existing folder, one idea at a time. A confirmed idea cannot silently apply other pending ideas. Existing file conflicts remain blocked.

## Final Cleanup

- No `.omnirush*` files remain in the repository.
- No temporary files remain in `/tmp/omnirush`.
- No Kshetrajna application process remains running.
- All test ports are closed.
- The scan-race fix, case-insensitive insight-title fix, their regression tests, and documentation updates remain as the current uncommitted repository changes.
- No commit, checkout, reset, pull, or push was performed.
