# Source verification, disposable benchmarks, and crash recovery

These are **source** tools, requiring Python 3.13.x and only the standard library.
They do not install dependencies, fetch network resources, inspect personal
folders, build packages, or certify a Windows bundle. Existing HTTP tests use
disposable **loopback** servers, not external services. Run from the repository root. Use
the repository's pinned interpreter (the `.python-version` version); examples
below use `python3.13`. All artifacts are schema-1 JSON, not telemetry uploads.

## Offline source entry point

```sh
python3.13 -B scripts/verify_source.py
python3.13 -B scripts/verify_source.py --json-output source-verification.json
```

The default performs these independently reported checks:

| Check | Contract | Not evidence for |
| --- | --- | --- |
| Runtime/version | Python 3.13.x; project/build version equals user-visible `__version__` | The contents/version of a built wheel or executable |
| Python syntax | Compile `src/**/*.py`, `scripts/**/*.py`, `tests/**/*.py` in memory; no `.pyc` | Type checking, lint, branch coverage |
| unittest | Discover/run `test*.py`; nonzero exit on failure, error, unexpected success, zero tests, runner failure or timeout | A native platform not actually used |
| Baseline discovery | At least **276** tests, including **42** quarantine and **14** scan-lifecycle tests | Those tests passed: see separate unittest result |
| JavaScript | Separately run `node --check` on first-party JS under `src/kshetrajna/web/` and `web-demo/` | Browser interaction, visual/a11y tests, JS behavior |

`--js auto` (default) reports a **visible skip** if optional Node is absent;
`--js required` makes absence a failure; `--js off` explicitly skips. No Node
installation is attempted. Broken JS or a JS-check timeout is a failure, not a
skip. Source success can coexist with a visible optional JS skip; inspect the
`javascript` object rather than treating `status: passed` as a browser gate.

`--timeout 600` is the default hard unittest-child timeout (configurable 1–3600
seconds). Every JS file has a 15-second check timeout. Child output is captured;
failures retain safe test identifiers/counts, never assertion values, source
snippets, exception messages, or tracebacks in JSON. For local diagnosis rerun
the named test with standard unittest; its normal diagnostics are not a
privacy-filtered report.

Focused runs accept repeatable **filename globs**, not paths:

```sh
python3.13 -B scripts/verify_source.py --pattern test_benchmark_core.py --pattern test_action_chaos.py
PYTHONPATH=src python3.13 -B -m unittest discover -s tests -p test_quarantine.py -v
PYTHONPATH=src python3.13 -B -m unittest discover -s tests -p test_scan_lifecycle.py -v
```

A focused report marks the full-suite baseline `not_applicable`. Patterns are
deduplicated so overlapping selections cannot inflate counts. The 276-test floor
is historical; the new tests are additional, not a replacement for that baseline.
The full combined quality gate remains the coordinator's responsibility. Nothing
here changes Makefile, CI workflows, existing tests, or production behavior.

## Opt-in synthetic benchmarks

Without `--run`, the script returns `status: not_run` and creates **no fixtures**:

```sh
python3.13 -B scripts/benchmark_core.py
python3.13 -B scripts/benchmark_core.py --run --counts 8 --repeats 1 --actions 2 --timeout 30
python3.13 -B scripts/benchmark_core.py --run --counts 10000 50000 200000 --repeats 3 --actions 3 --timeout 900 --json-output benchmark.json
```

The large command is deliberately opt-in; running it is **not** part of unittest
or source verification. No throughput target is asserted. The micro fixture tests
correctness of the measurement path, not large-scale throughput.

Bounds and interpretation:

- `--counts`: up to three distinct counts in **2–200,000**; defaults 10k/50k/200k.
  The count includes one empty action target. Remaining files are short,
  deterministic synthetic text, sharded into at most 1,000 files per directory.
- `--repeats`: 1–10 (default 3); each repetition uses a **fresh child process,
  temporary directory and catalog**. No existing directory can be adopted as a
  fixture, and no user-selected input/root option exists. OS caches are not
  flushed; this is not a disk-cold benchmark. Large defaults create up to
  780,000 files cumulatively over three repetitions, sequentially, not at once.
- `--actions`: 1–20 pairs per repetition (default 3). Real confirmed
  `FileService.delete_empty_file` and `restore_quarantined_file` calls are timed
  separately. Filesystem presence, restored bytes, catalog identity and aggregate
  counts are checked **outside** timing. This measures empty-file actions on the
  same filesystem, not large payloads, duplicate discovery, or cross-volume moves.
- `scan_initial`: real root approval plus full scan into a fresh database; scanner
  limit is 200k through the existing approved-location API. Exact file count and
  `ready` status are required; a truncated scan cannot count as success.
- `search_hit` / `search_miss`: actual service search, one unique filename hit and
  one absent term. Expected result counts are checked; the service's existing
  100-result limit still applies. This is not a content-ranking benchmark.
- `scan_cancel_controlled`: a real rescan is observed **after its first real
  catalog flush**. A bounded event barrier makes the cancellation point
  deterministic. Timing starts immediately before the real cancel request and
  includes barrier release and worker shutdown. `cancelled` status and unchanged
  existing catalog count are required. This is instrumentation-assisted active
  scan cancellation, not unconstrained scheduling latency or cancellation of a
  file action (which has no such public API).
- Each metric reports sample count, median, nearest-rank p95, minimum and maximum
  in **seconds**. One sample yields p95 equal to that sample: no confidence or
  significance is implied. Fixture creation/cleanup and post-operation checks
  are excluded from operation timings, but included in the worker timeout.
- Optional RSS uses `resource.getrusage(RUSAGE_SELF).ru_maxrss` on Linux/macOS,
  normalized to bytes. Reports the maximum **worker process lifetime peak** over
  repetitions, not per-operation delta or child-tree RSS. Other platforms
  explicitly report `unavailable`; no replacement measurement is fabricated.
- `--timeout`: hard per-worker wall limit including setup (default 900 seconds,
  1–3600 allowed). Scan, event, thread and subprocess waits are bounded. A
  timed-out/failed worker produces a failed report, not a performance sample.
  Abrupt worker death can leave a synthetic temporary directory on the host;
  this is never a personal root and is not evidence that cleanup ran.

## Actual child-process crash injection

`tests/test_action_chaos.py` uses two small duplicate payloads in a fresh temporary
approved root. A child creates a real store and live `FileService`, performs a
real scan, then invokes confirmed duplicate cleanup. Observation wrappers call
the real methods before abrupt `os._exit` (no normal exception unwinding). SQLite
tracing is used only to select the in-transaction death point. No successful
filesystem mutation or catalog result is mocked.

| Injection point | Persisted state before restart | Required fresh-service behavior |
| --- | --- | --- |
| After durable `prepared` write, before move | Original + catalog intact | Dismiss verified no-op; preserve both payloads |
| After real no-overwrite move, before catalog commit | Quarantine bytes, `prepared` journal, old catalog | Recover quarantine, remove old row, refresh totals; confirmed restore recovers original/catalog |
| During catalog transaction, before catalog DELETE executes | Journal UPDATE uncommitted; real move already finished | SQLite integrity/rollback, then same recover-and-restore contract |
| After catalog transaction **and** aggregate refresh commit, before method returns | Completed quarantine and consistent totals | Idempotent repeated startup, then confirmed restore |

Each child has a 60-second hard deadline and a unique expected crash exit code;
an ordinary exception, missed injection, hang, or successful normal exit fails the
test. Startup is a **new live service** in the parent process, opening the actual
child's database. It is not a mocked recovery or power-loss simulation. Checks
include journal state, original/quarantine presence, exact synthetic bytes,
surviving duplicate, catalog identity, byte/file totals, SQLite integrity,
repeated startup, and restoration. Two extra cases verify an occupied original
is never overwritten and tampered quarantine bytes are not silently accepted.

**Known boundary limitation:** catalog mutation and aggregate refresh are separate
commits in current source. These tests do not certify the narrower crash window
**after** catalog commit but **before** aggregate refresh; completed journal rows
are not selected by startup unresolved-action reconciliation. Do not claim all
possible crash windows, power-loss durability, or automatic aggregate repair for
that window. This tooling lane does not alter production recovery semantics.

## Artifact contract and evidence limits

Both public scripts print one JSON document; `--json-output FILE` additionally
creates a **new** file exclusively (parent must already exist). Existing files
are never overwritten. Exit 0 means the requested source checks passed (with
explicit optional skips), or benchmark was explicitly `not_run`; exit 1 means
verification/measurement failure. Invalid CLI arguments exit 2. No shell, pip,
npm, network service, browser, or installed application is required.

Shared metadata: `schema`, `kind`, `status`, `environment.build_version` from
`pyproject.toml`, `user_visible_version` from source, equality flag, optional Git
revision/dirty flag, Python implementation/version, OS family, architecture and
SQLite version. There is **no hostname, username, executable location, absolute
repository/fixture/report path, environment dump, search text, raw result, excerpt,
file content or unfiltered child diagnostic** in reports. Dirty revisions are
identified as such; a source commit alone does not identify uncommitted changes.
The JavaScript object additionally records the optional Node version when it can
be determined; absence of that version is represented as `null`, not guessed.

| Evidence tier | How to obtain it | Current tooling scope |
| --- | --- | --- |
| Focused source regression | New tooling + chaos tests; adjacent quarantine/lifecycle tests | Required local lane checks |
| Full offline source | Default `verify_source.py` or existing `make test` | Coordinator runs combined gate |
| Optional JS syntax | Reported independently via available Node | No browser behavior claim |
| Large synthetic benchmark | Explicit large command above, retain JSON | Never infer 10k/50k/200k results from micro tests |
| Native Windows / macOS | Run source checks and opt-in benchmark on those actual hosts | Linux results are not native evidence for other OSes |
| Wheel / installed package / bundled runtime | Existing packaging/build/smoke procedures with actual artifacts | `not_run` by these scripts |
| Cross-volume, reparse races, disk-full, power loss | Separate platform/fault matrix | Not certified by disposable same-volume crash tests |
