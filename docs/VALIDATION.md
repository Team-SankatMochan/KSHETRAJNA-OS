# Validation record

## Update: 15 October 2026 — organizer and download watching 0.4.3

- The exact staged source passed all **162 tests** on Windows x64 in
  41.149 seconds. It excludes unrelated empty-file/duplicate-deletion work in
  the working folder. No permanent-deletion feature was added by this update.
- Project and destination fixtures cover existing folders across approved
  locations, Downloads routing, multiple matching folders, preserved code trees,
  source/destination identity, root removal, partial scans and crash recovery.
- Watcher fixtures verify completion debouncing, changing/partial downloads,
  pause, demo isolation, expired approvals and automatic preview generation.
  Watching never moves a file or inherits approval from an older preview.
- An isolated live dashboard with synthetic system readings and disposable
  files moved five clear matches (including three Aarthika project files) and
  restored all five with zero failures. Personal files were not sorted.
- Folder watching checks immediate entries every two seconds and waits at
  least four seconds of stable size/mtime. It is a polling implementation with
  a 20,000-entry detection bound; nested arrivals need a manual rescan.

## Update: 14 October 2026 — Sort AI 0.4.1

- Local Windows x64 full suite: 138 tests passed in 112.679 seconds before
  adding the batch-limit regression. The final Sort AI group passed 25 tests
  in 10.277 seconds; the file-insights group passed 16 tests after preserving
  subject evidence. The final source suite contains 139 tests.
- Regression fixtures cover common-download batch moves, uncertain/duplicate
  retention, project preservation, exact review selection, persistent keep
  decisions, changed-source rejection, latest-pass partial scans, queued rescan
  races, redirected Downloads/OneDrive discovery, batch bounds and HTTP approval.
- Windows known-folder discovery was checked read-only on this x64 machine.
  Its actual Downloads and redirected Desktop/Documents locations were found.
- An isolated live browser fixture scanned seven files, sorted three clear
  matches while retaining four ambiguous files, and restored the three moves.
  The final in-app approval/history controls were exercised; two keep decisions
  reduced the advice queue from four to two. No personal files were sorted.
- Browser syntax and repository whitespace checks passed.
- Commit `093d60d` passed all 139 source tests on Windows x64, native Windows
  ARM64 and Linux in [run 38524710465](https://github.com/Team-SankatMochan/KSHETRAJNA-OS/actions/runs/38524710465).
  Both native packages passed their actual bundled-interpreter/app checks in
  [run 38524710443](https://github.com/Team-SankatMochan/KSHETRAJNA-OS/actions/runs/38524710443).
- The exact native CI artifacts were published in the
  [v0.4.1 beta release](https://github.com/Team-SankatMochan/KSHETRAJNA-OS/releases/tag/v0.4.1),
  with x64/ARM64 archives and their checksum files. The x64 archive was
  installed under the current user's Programs directory with a new
  version-specific Desktop shortcut; existing versions and history remain.
- A separate optional local archive audit did not start because automatic
  permission review timed out twice. The user requested publication and
  installation without further testing. No additional smoke tests or
  personal-file operations were performed after that instruction.

## Update: 14 October 2026 — self-contained distribution

- Windows x64 source suite: **129 tests passed** in 46.369 seconds.
- A local inspection build using the hash-pinned official CPython 3.13.16 x64
  runtime passed bundle-integrity checks, packaged SQLite/app import, read-only
  Windows diagnostics, isolated live startup with all collection/action opt-ins
  disabled, synthetic demo startup, asset loading, shutdown approval guards,
  and graceful exits. This inspection build was not published.
- New installation fixtures verify corruption/traversal/link rejection,
  post-copy integrity, preserved history, repeat installs and retained versions.
- New file-action checks reject undo/recovery after the target is replaced with
  a different file of identical size and timestamp. Recovery also revalidates
  approved paths and normalizes catalog extensions.
- Commit `b0fa11d` passed all three source jobs in
  [run 38474805570](https://github.com/Team-SankatMochan/KSHETRAJNA-OS/actions/runs/38474805570):
  Windows x64, native Windows ARM64 and Linux.
- Both native Windows package jobs passed in
  [run 38474805694](https://github.com/Team-SankatMochan/KSHETRAJNA-OS/actions/runs/38474805694),
  including the 129-test suite and actual bundled-interpreter diagnostics,
  isolated live/demo launch, asset loading and approved graceful shutdown.
- The exact tested clean-source archives were published in the
  [v0.4.0 beta release](https://github.com/Team-SankatMochan/KSHETRAJNA-OS/releases/tag/v0.4.0).
  A post-build audit verified every file against its manifest, source commit,
  archive checksum and exclusion of local databases/configuration/logs.
  Archive SHA-256: x64 `dad190d3ce244a3c4743ea437d8925d3fc9ba4a675c479dcf2699b872ab82b53`;
  ARM64 `773e76cee0f965ab7256996a6c4866ef152b4fe89a5a8b7ab7aa62d496860a09`.
- The published x64 artifact was installed under the current user's Programs
  directory, verified again, and given a version-specific Desktop shortcut.
  Its installed interpreter passed read-only diagnostics and isolated live/demo
  dashboard checks. The shortcut's windowless entry point separately passed
  isolated live launch and approved Quit, with collection disabled throughout.
  Existing personal history was not modified; test instances were stopped.
- Updated public onboarding with both existing release links was checked in
  the browser and visually inspected, including expanded optional-AI guidance.

## Update: 14 October 2026 — Vercel demo

- Public local-PC onboarding now includes source download, Python architecture
  guidance, optional local-model setup, troubleshooting and a local dashboard
  link. The source-download URL returned HTTP 200. The setup section and its
  expanded AI guidance were checked in the browser and visually inspected.
- `start_here.cmd --help` passed on Windows x64. A launch with `--demo
  --no-browser --port 8802` served the real local dashboard successfully in
  synthetic mode. No personal PC action or permanent installation was performed.

- Added an isolated static `web-demo` and root Vercel configuration. No cloud
  model, Windows control endpoint or personal telemetry is part of that site.
- Node syntax validation and repository whitespace checks passed.
- Browser checks passed for memory action review/approval and the updated
  synthetic reading/journal, and file sorting followed by reviewed undo.
- The desktop layout was visually inspected. Hosted data and replies are
  explicitly labeled synthetic/scripted; no real PC operation was performed.
- These checks previewed the static site locally. A real Vercel deployment
  has not been made. See [deployment settings](VERCEL.md).
- GitHub run `38468264761` passed Linux and Windows ARM64. Windows x64 exposed
  the same wide-integer issue in the post-action file-identity fields. The
  lossless storage codec and round-trip regression now cover both expected and
  post-action device/inode values.
- Corrected code commit `2a0441b` passed all three jobs in
  [GitHub run 38469748864](https://github.com/Team-SankatMochan/KSHETRAJNA-OS/actions/runs/38469748864):
  Windows x64, Windows ARM64 (including the native-ARM64 diagnostic step),
  and Linux. This is hosted-runner compatibility evidence, not a Snapdragon
  performance benchmark or a validation of local model inference on that device.

## Update: 13 October 2026 — local assistant

- Windows x64, CPython 3.13.3: **119 tests passed** in the final full-suite run
  (38.928 seconds). The final chat-focused checks also passed after guardrail
  and interface refinements. Fixtures and controlled drivers isolate mutations.
- New coverage includes model consent/history/cancellation, review-only model
  tool calls, invalid executable-tool rejection, action outcome context, app
  controls, typed force confirmation, expiry and PID/foreground revalidation.
- A real reply from the already-installed `llama3.1:8b` model was observed in the
  browser. No API key or new model download was used. Test PC readings/actions
  remained synthetic during browser checks.
- Windows user-SID binding was checked read-only. No personal application was
  closed, force-terminated, or memory-trimmed during validation.
- The Windows file-identity SQLite overflow now has a lossless large-integer
  representation and regression test. The path test compares resolved Windows
  paths rather than raw short/long path spelling. Remote CI has not been rerun
  on these local changes.
- JavaScript syntax checks and launcher help smoke passed. The provider's
  external runtime and this integration still need Snapdragon device checks;
  native ARM64 inference or NPU acceleration is not claimed.

The following 6 October record describes the earlier integration snapshot.

Date: 6 October 2026. This records checks actually performed in the current development checkout.

## Environment

- Linux x86_64 development runner for this session; Windows-only native calls were not executed here.
- CPython 3.14.4; no third-party runtime packages.
- Local loopback APIs and isolated temporary-folder fixtures.

## Results

| Check | Result |
|---|---|
| Python unittest suite | 101 tests total: 99 passed, 2 platform-specific skips in the current Linux verification run; includes pattern/prediction, privacy, file-action hardening, scan completion ordering, explicit file-root consent, conversational folder approval, bounded UTF-8, Office/PDF/image extraction, PDF signature/page/text/identity/path checks, automatic purpose/action decisions, project/subject evidence, Sort AI lifecycle, temporary maintenance scope, automatic path discovery, persisted consent, Jarvis bridge validation, malformed file API, and device diagnostic checks |
| Jarvis bridge | Local in-process dispatch, loopback token reuse, JSON-lines validation, explicit apply/undo confirmation, and synthetic command smoke passed |
| JavaScript syntax | All four dashboard scripts passed Node syntax checks |
| HTTP integration | Assets, security headers, Host/Origin/token validation, request validation, approval, undo, report export, and Sort AI plan preview passed |
| Demo launch smoke | `PYTHONUNBUFFERED=1 PYTHONPATH=src python3 -m kshetrajna --demo --no-browser --port 8800` started the loopback server and reported synthetic mode; it was stopped by the controlled timeout |
| Privacy and persistence | Consent, retention, deletion, migration and instance lock tested |
| Action lifecycle | Journal before apply, stale proposal rejection, single active trial, expiry, restart recovery and failed restoration tested |
| Native driver logic | Controlled doubles verified allowed priority changes, preservation of external changes and PID reuse rejection |
| Windows observation | Not executed in this Linux integration session; earlier Windows x64 results are historical |
| Browser interaction | Simulated approval, saved workspace activation, manual rollback and outcome display are covered by existing browser-path code; Sort AI DOM syntax was checked, while a full browser automation run was not available in this runner |
| Browser console | Not checked in this session; dashboard scripts passed syntax checks |
| Workflow prediction | Sequence disambiguation, idle/gap boundaries, habit drift, ambiguity abstention and chronological replay verified with synthetic histories |
| File-action hardening | Root file/symlink rejection, scan-limit truncation, post-mutation race handling, malformed numeric API IDs, catalog-deletion protection, reviewed permanent-delete journaling, Sort AI conflict/undo fixtures, automatic purpose/action decisions, content-aware review signals, and byte-bounded UTF-8/Office/PDF/image extraction passed focused regression tests |
| Repository whitespace | `git diff --check` passed for the experimental snapshot |

The journal displays before/after observations with an explicit noncausal
caveat. Synthetic readings do not demonstrate a performance improvement.

## Development-device baseline (not reproduced in this session)

The committed historical record reports a Windows 11 x64 CPython 3.13.3 device check and short synthetic/read-only measurements. Those measurements were not rerun after this integration pass and are not presented as current results. The supplied project brief separately reports that the earlier 61-test suite passed on a user-confirmed Snapdragon ARM64 laptop; no ARM64 diagnostic JSON is included in this checkout, so that device claim is not independently reproduced by this validation record.

The historical Windows diagnostic runner lacked interactive foreground
readings. Device checks must be repeated in an interactive session on the
Snapdragon computer. See [device setup and interpretation](SNAPDRAGON.md).

## Reproduce

1. Run `run_tests.cmd` from the repository.
2. Run `run_demo.cmd` and follow `SUBMISSION.md` for the demo flow.
3. Run `run.cmd` for real Windows observations; explicitly enable collection in Privacy controls. Live priority trials remain off unless separately enabled and individually approved.

## Current qualification still required

- Native ARM64 CI diagnostics passed; specific Snapdragon device measurements and external model inference still need on-device checks. The historical 61-test user report is separate evidence.
- Real process priority changes were not executed against user applications during validation; native behavior was tested with controlled doubles.
- Remote source and native package CI passed as linked above. No controlled device performance benchmark, battery measurement or production security audit has been completed.
- Workspace adaptation currently changes the dashboard board. It does not arrange desktop windows or launch applications.
- Sort AI uses deterministic path, sibling, metadata, and bounded local text heuristics for purpose/action explanations and cautious review prompts; it is not semantic model inference and cannot guarantee truth from a 4 KiB preview. No model download, NPU runtime, or accelerated provider is included.
- The unsigned self-contained x64/ARM64 artifacts passed native package checks. Signed MSI/EXE installers, auto-updates and model/NPU qualification are not implemented.
- Recovery restores the selected process. Descendant inheritance and recovery while the app remains crashed need further engineering.

This is an end-to-end hackathon prototype covering the project phases, with these limits stated explicitly; it is not a production operating system.
