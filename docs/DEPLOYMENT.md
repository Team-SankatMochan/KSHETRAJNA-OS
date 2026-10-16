# Local deployment and packaging

The current implementation baseline is **0.5.0**. The final destination is the
local PC-health/file-intelligence product in [the release contract](FINAL_RELEASE.md),
not an Internet service. Integrated source `25a0de7` includes the narrow
[action ledger/API](ACTIONS.md), health model and Basic/Expert shell. Prepared
release/verification tools described below are separate work awaiting integration
and qualification. Version 1.0.0, a signed installer and final hardware/user
qualification are not delivered by these instructions.

For the separately hosted interactive web demo, see [Vercel deployment](VERCEL.md).
The committed root `vercel.json` serves `web-demo`; live Windows functions remain local.

## Self-contained Windows app

[Download the historical 0.4.3 Windows beta](https://github.com/Team-SankatMochan/KSHETRAJNA-OS/releases/tag/v0.4.3).
Choose x64 for Intel/AMD or ARM64 for Snapdragon, extract, and open `Launch.cmd`.
Python is included. `Install.cmd` offers a verified per-user copy and optional
desktop shortcut. Use Quit Kshetrajna in Privacy for graceful shutdown.
See [the quick start](WINDOWS_QUICKSTART.md) and [package details](WINDOWS_BUNDLES.md).
These archives predate 0.5.0 reviewed quarantine and do not implement the planned
final product. To inspect the current baseline, use the source path below.

## Source launch

Prerequisite: CPython 3.13.16 on Windows. The project has no third-party runtime
packages. From the repository directory:

```powershell
.\run_demo.cmd
.\run.cmd
.\run.cmd --no-browser --port 8767
```

The first command uses temporary synthetic `events.db` and `files.db` data.
The second uses `%LOCALAPPDATA%\Kshetrajna`, starts with collection disabled on
first launch (later launches retain settings), and requires live consent in the
dashboard. Live and demo modes each bind only to loopback; neither is a second
remote control plane. Keep the terminal open and press Ctrl+C for graceful restoration and
shutdown.

PowerShell equivalents are:

```powershell
$env:PYTHONPATH = 'src'
python -m kshetrajna --demo
python -m kshetrajna
```

## Jarvis connection

No package install is needed when running from source with `PYTHONPATH=src`.
If an installed entry point is wanted, use a **project-local** environment and
the pinned build tools (download required once):

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-build.lock
.\.venv\Scripts\python -m pip install --no-build-isolation --no-deps -e .
```

From source, connect a Jarvis process to the already-running loopback dashboard.
Approve/scan the root in the UI first, then substitute its root ID below. An
installed environment may use its own Python instead of `PYTHONPATH`:

```powershell
$env:PYTHONPATH = 'src'
python -m kshetrajna.jarvis --url http://127.0.0.1:8765 discover_folders
python -m kshetrajna.jarvis --url http://127.0.0.1:8765 sort_preview --root-id 3 --create-folders
python -m kshetrajna.jarvis --url http://127.0.0.1:8765 sort_apply --plan-id PLAN_ID --confirm
python -m kshetrajna.jarvis --url http://127.0.0.1:8765 sort_undo --plan-id PLAN_ID --confirm

# Preview, quarantine the reviewed file, list the recovery record, then restore it
python -m kshetrajna.cli cleanup empty --url http://127.0.0.1:8765 --root-id 3
python -m kshetrajna.cli cleanup_empty_delete --url http://127.0.0.1:8765 `
  --file-id FILE_ID --confirm
python -m kshetrajna.cli quarantine list --url http://127.0.0.1:8765 --root-id 3
python -m kshetrajna.cli quarantine restore --url http://127.0.0.1:8765 `
  --action-id ACTION_ID --confirm
```

`kshetrajna-sort` is installed as a convenience entry point. Bare invocation
prints help without opening storage or networking. Sort scope options default to
`sort_preview`; `python -m kshetrajna.cli sort --url ...` is the equivalent
source command. Explicit `--jsonl` mode is available for assistant subprocesses.
The HTTP bridge discovers the dashboard token from the local page, validates that
the target is loopback, and never exposes arbitrary commands. Creating a
preview stores a plan. New roots selected by `--path` or `--known-folder` require
explicit `--consent` (JSONL: `"consent":true`); preview arguments alone do not
approve root access. That approval still includes persisted excerpts; prefer
`--root-id` for existing roots. Apply, undo and cleanup quarantine/restore require
literal confirmation in both bridge and service. A successful live cleanup returns
`deleted:true`, `quarantined:true`, and an `action_id`; it is not a permanent
final purge. The current bridge exposes no final-purge command. The legacy
generic file-action DELETE endpoint remains a separate permanent-delete API.
See [reviewed cleanup](CLEANUP.md).

Use `--url` to share the running dashboard service. Offline bridge mode instead
takes the same directory lock and refuses to run concurrently on its data. The
service remains local and consent-validating. Existing routes remain
domain-authoritative compatibility paths; the narrow v1 adapter does not replace
them and reports `canonical_authority_complete:false`. No assistant-specific or
CLI-specific consent bypass is authorized. Loopback URLs cannot contain user
credentials/query data; local session tokens must never be published.

## Offline tests, wheel and Linux demo

Use CPython 3.13.16 as pinned in `.python-version`. The standard-library test
suite requires no dependencies or network:

```sh
PIP_NO_INDEX=1 make test
PYTHONPATH=src python3 -m kshetrajna --demo --no-browser --port 8766
```

On Windows use `run_tests.cmd`. For a wheel, install `requirements-build.lock`
in a project-local virtual environment, then run
`make build PYTHON=.venv/bin/python` on Linux. No global install is required.

The configured Docker path builds/tests the wheel and runs an unprivileged,
loopback-only synthetic demo:

```sh
docker build -t kshetrajna:local .
docker run --rm --network host kshetrajna:local
```

Open `http://127.0.0.1:8766` on the Linux host. `-p` alone does not expose a
container-loopback listener. Docker Desktop requires host networking enabled;
otherwise use the source demo. Docker provides no live Win32 telemetry. These
commands describe configuration, not a Docker build/run passed in this docs lane.

## Snapdragon X / Windows ARM64

1. Install the official native ARM64 Python 3.13.16 distribution where available.
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

Historical public version 0.4.3 contains self-contained x64 and ARM64 archives with the
official hash-pinned CPython 3.13.16 runtime. Those release artifacts and their
historical package checks are recorded in [the validation history](VALIDATION.md);
they do not qualify later source changes. The historical `4d86a96` baseline had
Linux CPython 3.13.16 (276 run, 274 passed, two Windows-only skips) and native x64
CPython 3.13.3 (276 passed, no skips), not pinned-patch package evidence.
The coordinator now reports a combined **453-test pinned Linux CPython 3.13.16
run: 450 passed, three skipped**. The native result above remains the last
baseline; no newer native qualification is recorded. Current native package/
ARM64 device checks, Docker build/run and a new release are not claimed here.
The public archives/setup scripts remain unsigned, with no automatic-update
service; prepared MSI/signing tooling is not a shipped signed installer.

### Prepared verification and release framework — future commands

These commands were checked against the prepared tooling interfaces, **not run
in this documentation lane**. Use them from the integrated checkout only after
the release/verification lanes are merged, with CPython **3.13.16** and matching
project/runtime version. Do not run old branch source and label it a combined
candidate check. The coordinator owns qualification evidence in
[VALIDATION.md](VALIDATION.md).

```sh
# Offline source checks; optional Node.js must exist for --js required.
python3 -B scripts/verify_source.py --js required
# Same offline suite through the prepared pinned-runtime Makefile.
PIP_NO_INDEX=1 make test
# Release-tool fixture tests and static configuration checks, not installation.
PYTHONPATH=src python3 -B -m unittest discover -s tests -p test_release_tools.py -v
python3 -B scripts/validate_release_config.py
# Opt-in disposable source benchmarks; without --run, reports not_run.
python3 -B scripts/benchmark_core.py
python3 -B scripts/benchmark_core.py --run --counts 10000 50000 200000 --repeats 3 --timeout 900
```

The source verifier compiles Python in memory, runs unittest with bounded
execution and reports JS syntax, not a browser/accessibility/package/native
test. Its runtime check is 3.13-series; the prepared Makefile/release tools enforce
the exact patch. Benchmark timings are disposable source-fixture observations,
not target-hardware throughput, idle overhead, ARM64 battery/thermal or delayed
action measurements. The 10k/50k/200k runs have **not** been performed for this
candidate.

For prepared wheel build/smoke/reproducibility, install hash-pinned tools once
into a project-local environment (network required for that installation):

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes --only-binary=:all: --no-deps -r requirements-build.lock
make smoke-wheel PYTHON=.venv/bin/python
make reproducible PYTHON=.venv/bin/python
make smoke-docker PYTHON=.venv/bin/python
```

Docker still requires a working daemon/host networking. The prepared
`final-release.yml` adds exact clean tag/version identity, independently protected
signing/qualification/publication environments, same-commit source/packaged
native tests, content scans, unsigned reproducibility, MSI table/payload/upgrade
audits, Authenticode, real standard-user install/upgrade/uninstall and final
manifest/SBOM/provenance checks. It deliberately rejects **0.5.0** as a final
release; do not bump/tag merely to satisfy the tool. See
[Windows bundle/release commands](WINDOWS_BUNDLES.md) for the native tooling.

The reported **26-pass isolated release-tool suite**, including MSI Upgrade-row
auditing, validates fixture logic only. No native MSI install, upgrade, uninstall
or signing was run. Actual credentials, signed predecessor packages, protected
environments and disposable native workers remain prerequisites.

### Required before 1.0.0

Complete deferred canonical adapters, metadata-only/content migration,
comprehensive privacy redaction and delayed outcomes; qualify the implemented
ledger/consent/health/Basic–Expert shell under the [contract](FINAL_RELEASE.md).
Then qualify exact clean-source
artifacts with the pinned runtime on native x64 and ARM64. Release evidence must
include Windows race/crash recovery, Docker, browser/a11y and declared-hardware
performance/cancellation tests, signed installation/upgrade/uninstall, final
signature verification, content scan, SBOM and provenance. Unsigned payloads
should be reproducible; timestamped signatures need not be byte-identical.

Signing credentials, protected release environments, native devices and at least
ten ordinary Windows users (including keyboard users and native ARM64 when
supported) are blockers until available. Do not substitute old CI, hashes or
synthetic data for those checks, and do not promote the version until gates pass.

## Uninstall and data removal

Resolve recovery and use the dashboard's scoped deletion controls before
stopping the process and removing the repository/shortcut. Removing the app does not
automatically delete `%LOCALAPPDATA%\Kshetrajna`; restore or otherwise resolve
quarantine records first. Unresolved file-action records block catalog and
approved-root deletion. There is no final-purge API in this milestone, and
catalog deletion never removes original approved-folder files.

Do not manually delete quarantine or its journal to force removal. Quarantine
retains the actual bytes, not recovered space. Logs/preferences, SQLite sidecars
and external backups/exports have separate retention limits; see
[privacy](PRIVACY.md) and [crash reporting](CRASH_REPORTING.md) before copying or
sharing diagnostics. Normal launch does not require elevation; do not expose or
elevate the whole service to work around permissions.
