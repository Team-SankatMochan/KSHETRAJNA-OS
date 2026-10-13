# Validation record

## Update: 14 October 2026 — Vercel demo

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
  post-action device/inode values; the corrected commit must pass a fresh run.

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

## Qualification still required

- Native ARM64/Snapdragon diagnostics are not independently reproduced in this checkout; the submission materials contain a user-confirmed report of the earlier 61-test suite on an ARM64 laptop, but no committed device-check artifact.
- Real process priority changes were not executed against user applications during validation; native behavior was tested with controlled doubles.
- CI configuration is included, but a remote CI run has not been observed.
- No controlled benchmark, battery measurement, production security audit or installer qualification has been completed.
- Workspace adaptation currently changes the dashboard board. It does not arrange desktop windows or launch applications.
- Sort AI uses deterministic path, sibling, metadata, and bounded local text heuristics for purpose/action explanations and cautious review prompts; it is not semantic model inference and cannot guarantee truth from a 4 KiB preview. No model download, NPU runtime, or accelerated provider is included.
- No signed installer or packaged x64/ARM64 artifact has been qualified.
- The runner does not have `pip`, so a wheel build could not be executed here; source launch remains the verified deployment path.
- Recovery restores the selected process. Descendant inheritance and recovery while the app remains crashed need further engineering.

This is an end-to-end hackathon prototype covering the project phases, with these limits stated explicitly; it is not a production operating system.
