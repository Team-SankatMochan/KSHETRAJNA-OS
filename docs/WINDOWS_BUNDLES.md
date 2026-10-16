# Self-contained Windows distribution

Historical Kshetrajna 0.4 releases introduced Windows x64 and ARM64 bundles. A bundle contains
the application, its dashboard assets and the official CPython 3.13.16
embeddable runtime for that architecture. Users do not need to install Python,
Git, pip packages or an AI model to use the PC tools.

**Current source baseline: 0.5.0; final 1.0.0 is reserved until qualification.**
The published 0.4.3 archives predate the quarantine milestone and planned final
PC-health/file-intelligence surface. These are unsigned beta archives,
not signed MSI/EXE installers. Python's upstream runtime license is retained.
General AI conversation additionally requires a separately installed compatible
Ollama runtime and a local model; neither is downloaded by the application.

The [v0.4.3 beta release](https://github.com/Team-SankatMochan/KSHETRAJNA-OS/releases/tag/v0.4.3)
contains the exact archives tested in [native x64/ARM64 CI](https://github.com/Team-SankatMochan/KSHETRAJNA-OS/actions/runs/38739508788).
That historical record includes both jobs, the 162-test source suite and
packaged-app checks. It is not native qualification of later source. The
[current validation record](VALIDATION.md) belongs to the coordinator, who
reports a combined pinned Linux CPython 3.13.16 run (453 run, 450 passed, three
skipped). Last native evidence remains 276 passed on x64 CPython 3.13.3, not a new
ARM64/package or pinned Windows patch check.
This guide's build/smoke commands describe the configured
path, not tests newly run in this documentation lane.

## User experience

1. Download the correct Windows architecture and choose Extract All.
2. Open `Launch.cmd` to start the local dashboard without a persistent console.
3. Or run `Install.cmd` to create a verified copy under the current user's
   `%LOCALAPPDATA%\Programs\Kshetrajna`. Setup asks for consent and can create
   a desktop shortcut. Administrator privileges are not required.
4. Enable collection and action controls in the local Privacy panel. Every
   PC/file modification follows the version's review flow. Current source adds
   strict service confirmation and root consent, private permission-generation
   invalidation and Basic/Expert visibility; do not assume these later changes
   are present in the historical downloaded archive.
5. Use **Quit Kshetrajna** in Privacy to stop the app and attempt restoration
   of active reversible priority trials. Closing a browser tab leaves it running.
6. If startup fails, open `Console.cmd` to inspect the error. It also accepts
   `--port 8767`, `--demo` and `--no-browser`.

Preferences/history remain separately in `%LOCALAPPDATA%\Kshetrajna`.
New installs use a version/architecture/commit-specific directory; existing
versions and unrelated desktop shortcuts are preserved. Removing program files
does not remove history or original approved-folder files. Quit before removing
an extracted/installed version and its shortcut. Use the app's data-deletion
controls after resolving any pending recovery records to remove history.

## Why this packaging technology

The app's runtime dependency list is empty. Bundling the official CPython
embeddable distribution avoids a third-party freezer/bootloader and additional
native-wheel dependencies, while preserving native ARM64 Win32 calls. Python's
`._pth` configuration contains only the bundled standard library, runtime and
application source directory. It does not import global site packages or rely
on a machine's default Python. The packaged launcher disables bytecode writes.

The build downloads only fixed HTTPS URLs from python.org. Their SHA-256 values
are pinned from the [official release inventory](https://www.python.org/downloads/release/python-31316/).
The ZIP extractor rejects traversal, duplicate names and symbolic links and has
member/size bounds. Application packaging includes only selected source/static
file types. Personal data, logs, databases, caches and model files are excluded.

`bundle.json` records the version, source commit, architecture, upstream runtime
origin/hash and every shipped file's SHA-256. Installation verifies source and
copied files, rejects unsafe paths/links/junctions, stages a fresh directory,
then renames it into an immutable installation location. Unknown or damaged
existing installs are preserved for review. Checksums detect corruption and do
not replace code signing or establish authenticity of an arbitrary download.
Use the project's GitHub release and its accompanying checksum files.

## Reproduce a clean build

Commit source changes first, then:

```powershell
python scripts/build_windows_bundle.py --architecture x64
python scripts/build_windows_bundle.py --architecture arm64
```

Output: `dist/Kshetrajna-Windows-x64.zip`, `dist/Kshetrajna-Windows-arm64.zip`
and one `.sha256` file per archive. Build output/runtime cache are ignored by
Git. The `--allow-dirty` switch is only for local inspection; never publish a
bundle whose manifest reports dirty source.

To test a package on a machine matching its architecture:

```powershell
Expand-Archive dist/Kshetrajna-Windows-x64.zip build/package-smoke
python scripts/smoke_windows_bundle.py --bundle build/package-smoke/Kshetrajna --architecture x64
```

The packaged interpreter verifies integrity, imports SQLite/app code, performs
read-only Windows diagnostics, starts an isolated live instance with all
collection/action opt-ins disabled, starts a separate synthetic demo, checks
static assets and shutdown approval guards, and confirms graceful exits.
No personal PC action is executed. ARM64 testing additionally requires native
ARM64 diagnostics to pass. A generic ARM64 CI machine is compatibility evidence,
not a Snapdragon benchmark or NPU/model-inference qualification.

## Prepared final-release framework

The prepared release lane adds `.github/workflows/final-release.yml` and
`scripts/release_gate.py`, MSI builder/audits, reproducibility/content scans,
native source/packaged checks and standard-user lifecycle tooling. These are
frameworks, not a qualified MSI or authorization to sign/publish. They were
inspected read-only for this guide; use commands only after tooling integration.
The reported isolated release-tool suite is **26 passed**, including MSI
Upgrade-row audit fixtures, **not a native installer run**.

From a clean integrated candidate on native Windows, with CPython 3.13.16 and
the matching archive already built, the MSI content commands are:

```powershell
# Review WiX's upstream EULA and satisfy its applicable terms before opting in.
$env:KSHETRAJNA_WIX_EULA_ACCEPTED = 'yes'
$version = python -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])"
# --work must not exist. Use a distinct work/output path for each architecture.
python scripts/build_msi.py --archive dist/Kshetrajna-Windows-x64.zip `
  --output "dist/Kshetrajna-$version-x64.msi" --work build/msi-x64
python scripts/audit_msi.py --msi "dist/Kshetrajna-$version-x64.msi" `
  --archive dist/Kshetrajna-Windows-x64.zip
```

The audit checks allowed MSI tables/properties/registry/removal scope, Upgrade
rows/platform/language and exact embedded payload bytes against the archive.
It needs Windows Installer APIs and is distinct from Authenticode and real
installation. ARM64 must use its own native runner, archive and paths. No audit,
MSI compilation or EULA acceptance was executed by this documentation change.

The final workflow re-audits signed bytes, verifies Authenticode with protected
hash-pinned signing tools, and requires native standard-user install/upgrade/
uninstall using an independently supplied signed, lower-version predecessor.
`check_msi_upgrade.py` checks UpgradeCode family, distinct ProductCodes, lower
predecessor version and actual current-user registration at clean/predecessor/
upgraded/uninstalled phases. Fixture passes are not evidence of those native
states. Final manifest assembly checks exact MSI hashes and same-commit
source/packaged/lifecycle evidence plus SPDX inventory; provenance is separately
CI-issued, not established by a self-asserted JSON field.

`release_gate.py candidate` is intended for the exact qualified version-tag CI
context, not an ordinary **0.5.0** checkout: it rejects pre-1.0 versions, dirty
source or mismatched tag/project/runtime/commit identity. Signing, qualification
and publishing each require independently protected environments. Do not change
version, create tags or invoke publication to work around a failed gate. See
[deployment](DEPLOYMENT.md) for prepared offline verification/build commands.

## Qualification still required

The existing `.github/workflows/windows-bundles.yml` runs the source suite, builds and tests
the actual x64 and ARM64 packages on matching Windows runners, then retains
archives/checksums as CI artifacts. Both architecture jobs must pass before
publishing those exact artifacts in a GitHub release. Hosted web links are
updated only after the release assets exist. No credentials or device histories
are included in a release.

That workflow path alone is not final-release certification. The
[final contract](FINAL_RELEASE.md) also requires clean/hash-pinned inputs and
tools, repeatable unsigned payloads, artifact-content scanning, final signature
verification, signed x64/ARM64 installation/upgrade/uninstall, SBOM and
provenance. Timestamped signed binaries need not be byte-identical. Signing
credentials and protected release infrastructure are prerequisites, not present
permissions or passed gates; these instructions do not authorize a release.

Before 1.0.0, qualify the exact candidate revision **and packaged CPython
3.13.16 runtime** on native x64 and ARM64. Include Windows pathname/reparse and
crash/restart fixtures, Docker build/run, browser/keyboard/screen-reader tests,
declared-hardware performance/cancellation, Snapdragon battery/thermal data and
at least ten ordinary Windows users including keyboard users and native ARM64
when supported. Unavailable hardware, credentials or people mean **blocked/not
run**, never pass. Do not infer current qualification from the historical CI URL.

Normal launch stays unelevated. A future privileged operation would need a
separate signed narrow broker and a new review, not elevation of the web service.
Basic/Expert navigation, strict consent and the narrow canonical ledger are
implemented in current source, not retroactively added to old archives. Full
adapter coverage, metadata-only/content migration and delayed measurements
remain pending; `canonical_authority_complete` is false. Quarantine
retains file bytes (not capacity recovered); program uninstall does not purge
recovery content or logs. See [cleanup](CLEANUP.md), [privacy](PRIVACY.md) and
[crash reporting](CRASH_REPORTING.md).
