# Self-contained Windows distribution

Kshetrajna 0.4 adds small Windows x64 and ARM64 bundles. A bundle contains
the application, its dashboard assets and the official CPython 3.13.16
embeddable runtime for that architecture. Users do not need to install Python,
Git, pip packages or an AI model to use the PC tools.

The application is still a hackathon prototype. These are unsigned archives,
not signed MSI/EXE installers. Python's upstream runtime license is retained.
General AI conversation additionally requires a separately installed compatible
Ollama runtime and a local model; neither is downloaded by the application.

The [v0.4.1 beta release](https://github.com/Team-SankatMochan/KSHETRAJNA-OS/releases/tag/v0.4.1)
contains the exact archives tested in [native x64/ARM64 CI](https://github.com/Team-SankatMochan/KSHETRAJNA-OS/actions/runs/38524710443).
Both jobs passed, including the 139-test source suite and packaged-app checks.

## User experience

1. Download the correct Windows architecture and choose Extract All.
2. Open `Launch.cmd` to start the local dashboard without a persistent console.
3. Or run `Install.cmd` to create a verified copy under the current user's
   `%LOCALAPPDATA%\Programs\Kshetrajna`. Setup asks for consent and can create
   a desktop shortcut. Administrator privileges are not required.
4. Enable collection and action controls in the local Privacy panel. Every
   PC/file modification still requires review and approval.
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

## Release gate

`.github/workflows/windows-bundles.yml` runs the source suite, builds and tests
the actual x64 and ARM64 packages on matching Windows runners, then retains
archives/checksums as CI artifacts. Both architecture jobs must pass before
publishing those exact artifacts in a GitHub release. Hosted web links are
updated only after the release assets exist. No credentials or device histories
are included in a release.
