# Release tooling (not a release certification)

The application stays at **0.5.0** and CPython **3.13.16**. `final-release.yml`
rejects the current baseline. Version/tag promotion belongs to the coordinator
after `docs/FINAL_RELEASE.md` qualification. None of these files supplies a
signing identity, creates an account, changes machine configuration, or certifies
native tests that have not run.

## Local Linux checks

Use a project-local CPython 3.13.16 environment:

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install --require-hashes --only-binary=:all: --no-deps -r requirements-build.lock
make test PYTHON=.venv/bin/python
make smoke-wheel PYTHON=.venv/bin/python
make reproducible PYTHON=.venv/bin/python
make smoke-docker PYTHON=.venv/bin/python
```

`make test` is offline, uses only unittest/standard library, and rejects a runtime
other than CPython 3.13.16. Install `make` with the host's normal setup; Docker's
build runs the equivalent unittest command directly. Wheel output directories
must not already contain another wheel. To inspect an existing wheel, use
`python scripts/smoke_runtime.py --wheel-dir dist` without rebuilding it.

The Docker smoke runs the **installed runtime image**, non-root, read-only,
without external networking, with a temporary `/tmp`. It checks real assets,
token/origin/confirmation rejection and successful HTTP quit/exit. It fails if
Docker is unavailable; a successful image build alone is insufficient.

Unsigned payload checks (native interpreter execution is a separate gate):

```sh
python scripts/build_windows_bundle.py --architecture x64
python scripts/check_reproducible.py --kind windows --architecture x64 --artifact dist/Kshetrajna-Windows-x64.zip
python scripts/release_tools.py scan dist/Kshetrajna-Windows-x64.zip
python scripts/release_tools.py inventory dist/Kshetrajna-Windows-x64.zip --output reports/x64.spdx.json
```

Repeat for `arm64`. Clean Git inputs are required for release bundles.
`--allow-dirty` is explicitly local inspection only. `SOURCE_DATE_EPOCH` defaults
to the candidate commit timestamp; timestamps, ordering and permissions are
fixed. The wheel check uses two fresh source staging directories. Reproducibility
means repeated **unsigned wheel/ZIP payloads** under the same pinned toolchain,
not byte-identical timestamped signed MSI containers or cross-toolchain results.

`scripts/toolchain-sources.json`, `toolchain.lock.json`, and
`runtime-components.json` record authoritative sources, exact hashes, and license
evidence. WiX **7.0.0** and .NET **8.0.32** are downloaded and extracted under the
explicit work directory. No `dotnet tool install`, MSI tool installation, or
global configuration is used. WiX binaries require its upstream OSMF EULA;
`KSHETRAJNA_WIX_EULA_ACCEPTED=yes` must only be supplied by an operator who has
reviewed and met those terms.

## Windows packaging and lifecycle

Compile only on a Windows build worker, with pinned Python and a fresh work dir:

```powershell
python scripts/build_msi.py --archive dist/Kshetrajna-Windows-x64.zip --output dist/Kshetrajna-0.5.0-x64.msi --work build/msi-x64
python scripts/audit_msi.py --msi dist/Kshetrajna-0.5.0-x64.msi --archive dist/Kshetrajna-Windows-x64.zip
```

The MSI is per-user under `%LOCALAPPDATA%/Programs/Kshetrajna/<architecture>`.
The installer owns only program files, narrow HKCU component keys, and empty
directory cleanup. History remains separately under `%LOCALAPPDATA%/Kshetrajna`.
Use `Launch.cmd` in the installation folder to open the application. There is no
custom elevated action, startup task, global PATH edit, or bundled privileged
broker. `audit_msi.py` opens MSI tables read-only and expands the embedded cabinet
without installing, matching every file to the scanned ZIP.

**Never run `test-lifecycle.ps1` on a personal PC.** It refuses an elevated token
and requires an explicitly approved disposable CI VM. It installs a verified
signed predecessor, upgrades to the exact candidate, smokes the installed runtime,
uninstalls, and checks history preservation. Its JSON report is bound to the
candidate's MSI SHA-256. A real signed predecessor with a lower version and the
same architecture/upgrade family is a prerequisite; none is fabricated here.

## External final-release prerequisites

The workflow requires existing protected environments `release-signing`,
`release-qualification`, and `release-publish`. The REST gate checks independent
required reviewers (`prevent_self_review`) and an explicit `v*` **tag** policy.
Reviewers must review the remaining real-device/browser/user evidence in
`docs/FINAL_RELEASE.md` before approving final publication. GitHub administrative
bypass policy and environment access remain operator responsibilities; the REST
environment schema does not expose an admin-bypass setting.

Required protected environment variables:

| Environment | Inputs |
|---|---|
| Signing | `WIX_EULA_ACCEPTED=yes`, `AUTHENTICODE_THUMBPRINT`, `SIGNTOOL_PATH`, `SIGNTOOL_SHA256`, `AUTHENTICODE_TIMESTAMP_URL` (HTTPS) |
| Qualification | `AUTHENTICODE_THUMBPRINT`, `SIGNTOOL_PATH`, `SIGNTOOL_SHA256`, `DISPOSABLE_VM_APPROVAL=approved`, `PREVIOUS_X64_MSI_PATH`, `PREVIOUS_X64_MSI_SHA256`, `PREVIOUS_ARM64_MSI_PATH`, `PREVIOUS_ARM64_MSI_SHA256` |

No actual values, certificate, secret, or environment configuration is included.
SignTool must already be provisioned from a trusted Windows SDK and its exact
binary hash approved. The certificate/private key stays pre-provisioned in the
signing worker's current-user store; scripts neither import nor export it.

Required native runner labels:

- Hosted `windows-2025` x64 and `windows-11-arm` for source and packaged-interpreter
  suites (zero skips), live diagnostics, and packaged HTTP smoke at the same SHA.
- Disposable self-hosted Windows `x64`/`arm64` workers labelled
  `kshetrajna-signing-ephemeral`, with the existing signing identity.
- Clean **standard-user** disposable Windows workers labelled
  `kshetrajna-standard-user-ephemeral` for install/upgrade/uninstall.

Final publication requires both architectures, Linux tests/wheel/Docker checks,
matching version/tag/revision, exact artifacts, trusted timestamped Authenticode,
cabinet byte auditing, lifecycle evidence, and actual CI-issued attestations.
Only the two signed MSIs, their known-component SPDX inventories and evidence,
the release manifest and checksums are final assets. Unsigned ZIPs remain clearly
labelled CI inspection artifacts. The publish step verifies provenance's source
commit/tag/workflow, uploads a draft, re-downloads and hashes every asset, then
publishes. Failure leaves the gate blocked (or a draft for operator review).

## Inventory, provenance and local diagnostics

SPDX 2.3 inventories hash real archive files and identify known bundled components
with their own license expressions. Unknown licensing/transitive coverage remains
`NOASSERTION`; build-only setuptools/wheel/WiX/.NET are not falsely listed as app
runtime dependencies. Inventory is not a complete legal/vulnerability audit and
is not an attestation. The final workflow's pinned GitHub provenance action must
actually issue and verify attestations; local scripts never manufacture them.

An optional **local-only** export takes an existing explicitly chosen diagnostic
JSON and copies only enumerated status, architecture, boolean checks, and bounded
numeric timings. It drops arbitrary text, OS-version strings, paths, logs, names,
tokens, and unknown keys; it never uploads or reads application databases:

```sh
python scripts/export_diagnostics.py --input reports/device.json --output reports/support-summary.json
python scripts/validate_release_config.py --download-actionlint
```

The optional validator downloads hash-pinned actionlint 1.7.7 under `.tools`.
`--powershell <executable>` performs AST parsing only and does not run installer or
signing scripts. Workflow linting and AST parsing do not replace native Windows,
Docker, Authenticode, or lifecycle execution.
