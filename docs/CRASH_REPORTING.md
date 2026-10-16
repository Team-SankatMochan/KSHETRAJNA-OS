# Crash reporting, diagnostics and recovery

This is the **0.5.0 baseline** runbook, not a claim that a new crash-reporting
service or final crash qualification is implemented. Kshetrajna has local
operational logs, user-requested reports, a narrow canonical action ledger and
existing domain recovery journals (source inspected at `25a0de7`).
It has **no automatic crash upload or hosted crash-reporting integration**.
The final product's recovery/privacy gates are in [the release contract](FINAL_RELEASE.md).

## Current evidence surfaces

| Surface | What exists | Privacy / interpretation limit |
|---|---|---|
| Local log | `%LOCALAPPDATA%\Kshetrajna\kshetrajna.log` (or selected data directory), rotating at 500,000 bytes with two backups | Operational messages plus exception tracebacks; not governed by seven-day system-history retention; not comprehensively redacted |
| Console | `Console.cmd` in a bundle, or the source launch window | Startup/OS exceptions may include private paths or environment details |
| Dashboard report | User-requested system intelligence/decision/workspace JSON | Can contain app basenames, activity, labels and decisions; not a guaranteed anonymized support bundle; synthetic metrics are not benchmarks |
| Device diagnostic | `check_device.cmd` from source, or packaged `kshetrajna.diagnostics` | Read-only compatibility/timing evidence for that runtime/device, not proof of safe native mutation or a performance gain; review before sharing |
| Recovery journals | System decisions in `events.db`; file actions, plans and quarantine records in `files.db` | Needed to reconcile interrupted work; private databases are not support attachments by default and are not application-encrypted |
| Canonical action ledger | `actions.db` intent/evidence/consent/claim/events for the narrow rollout; safe persisted review through proposal/detail | Not a universal execution authority, not cross-database crash atomicity and not erased by telemetry deletion; raw private payloads are not support attachments |

HTTP request access logging is suppressed. That does **not** mean every other
message is path-free: scan, telemetry and priority-restore failures can log full
exception tracebacks. There is no blanket full-path exclusion or automatic
secret-redaction guarantee. Filesystem/OS error text and user labels can be
sensitive. Logs, exports, SQLite sidecars, copied databases and backups all need
separate retention review; see [the privacy inventory](PRIVACY.md).

## When something fails

1. Stop issuing new approvals. Record the time, app/source version, whether the
   session was live or synthetic, runtime/architecture and the operation you
   requested. Do not retry an irreversible operation just because its result
   was not displayed.
2. If the service still responds, use **Quit Kshetrajna** in Privacy or Ctrl+C
   for a console/source launch. Closing the browser alone does not stop it.
   Graceful shutdown attempts restoration of active priority trials; it is not
   a guarantee that every restore succeeds.
3. Reopen the **same version and data directory** to inspect recovery. Startup
   attempts priority restoration and file-domain reconciliation. The canonical
   adapter revokes unused approvals and fences interrupted claims without
   rebuilding plans or replaying actions. Review pending/unknown/recovery-required
   or failed records rather than deleting them to make the error disappear.
4. For reviewed file quarantine, keep the approved root and protected
   `.kshetrajna-quarantine` directory intact. List recovery records and restore
   by exact action ID through [the cleanup flow](CLEANUP.md). Identity/content
   changes or an occupied destination can block restoration; never overwrite a
   changed path or manually purge quarantine to bypass a guard.
5. If unresolved, preserve the original private state locally and reproduce only
   with disposable files/apps in an isolated directory. Do not copy a live SQLite
   database without accounting for its WAL/SHM state. Do not run a second service
   against the same directory or use elevation to force access.

After a forced kill, a selected process's lowered priority can persist until
restart recovery or process exit. Recovery does not cover inherited child
priorities. File move/restore recovery can be refused when evidence changed.
Working-set trim, temporary-file deletion, generic permanent DELETE and process
termination have no exact undo. A close request may leave an app running with a
save prompt; a tool launch does not establish completion. Model unload is a
request, not measured reclaimed memory. None of these should be replayed
automatically after an uncertain outcome.

Quarantine retains bytes, normally on the original volume. Recovering the file
is not reclaiming capacity, and catalog deletion is not secure erasure. Deletion
guards intentionally keep unresolved recovery evidence. See [architecture](ARCHITECTURE.md)
for current domain contracts and limits.

## Share a minimized report manually

There is no one-click, verified-redacted crash bundle in this baseline. Before
sharing through a support channel you trust:

- Start with version/source revision, Windows/runtime version and native versus
  emulated architecture; a short reproduction using synthetic/disposable data;
  expected versus actual result; time and public error/status; and whether an
  unresolved recovery record remains.
- Copy only the relevant small log/error fragment and **inspect the copy**.
  Remove usernames, home/root paths, document names/content, project/workspace
  labels, machine identifiers, URLs/credentials and session tokens. Keep any
  original evidence private. This manual review is not a redaction certification.
- Do not attach `config.json`, `events.db`, `files.db`, `actions.db`, database sidecars,
  quarantine contents, original documents, chat prompts or a complete browser
  network capture by default. They may contain sensitive data. Share an opaque
  action ID only when necessary; no token is needed in a public report.
- State checks actually run, including skipped/failed/blocked ones. A historical
  0.4.3 package check does not qualify 0.5.0 source or a later candidate. A model
  response, before/after delta or low CPU is not causal proof of improvement.

Kshetrajna does not erase copies you send, external backups, browser/OS memory,
or Ollama-owned logs. Choose whether to share; no report is uploaded by these
instructions. No accounts, remote providers or signing credentials are required
for local diagnosis.

## Canonical recovery boundary and remaining gates

The [canonical ledger and v1 resources](ACTIONS.md) are implemented for memory
trim, process close/terminate and four fixed external tools only; other kinds
are deferred and `canonical_authority_complete` remains false. Existing
unversioned routes retain domain authority. Public proposal/detail review is
derived from validated persisted evidence, not reconstructed from current
telemetry or used to rebuild an executable plan. Erased/legacy/malformed evidence
is unavailable. Raw private plans, paths, result payloads and permission
generations are not returned in that review DTO.

Strict confirmation, evidence/expiry/replay checks and service-managed permission
generations protect the narrow flow. Revoking then re-enabling relevant consent
does not revive old approvals. Domain recovery remains available with safety
checks in Basic and Expert; switching view is not permission. Cross-database
writes are not one crash-atomic transaction, and append-only application events
are not tamper-proof against the same Windows user.

Telemetry deletion (`/api/delete`) clears its own scope and chat, revokes pending
approvals and retains canonical history. Explicit selected-ID canonical
`erase-history` redacts only eligible resolved payloads; unresolved/recoverable
work, approval metadata and audit/replay tombstones remain. WAL cleanup can be
pending. Neither endpoint erases all domains, backups or media securely.

Required failure injection covers intent persistence, destination creation and
verification, source removal, catalog projection, finalization and recovery,
using disposable fixtures only. Test migration/restart, uncertain outcomes,
replay/expiry/used approval rejection, no-overwrite, concurrent path changes and
recovery discovery independent of paginated display history. Uncertain results
must remain unknown/recovery-required, never automatic success or a reason to
replay irreversible work.

Final privacy work includes comprehensive legacy/log/export redaction,
retention/deletion across sidecars/backups, and metadata-only indexing/separate
content consent with a visible policy for previously persisted excerpts. v1
stable error codes and bounded display projections are not blanket log redaction.
Delayed measurements remain pending; nullable freshness-aware health readings
are not causal verification. Model/document text never authorizes execution.

### Prepared minimized diagnostic exporter

The prepared release tooling includes `scripts/export_diagnostics.py`, inspected
read-only, not run here. After tooling integration, use an existing explicit
device-check JSON (not a database or raw log), and an output path that does not
exist in an existing directory:

```sh
python3 -B scripts/export_diagnostics.py --input reports/device-check.json --output reports/support-summary.json
```

It accepts at most 1 MB input and allowlists architecture/pointer/emulation/
pinned-runtime facts, selected boolean checks, ABI/telemetry status and finite
aggregate collection timings. It does not copy arbitrary report text or upload
anything. Review the resulting summary before sharing. It does **not** redact
logs/catalogs/chat, erase source data or establish release qualification.

The coordinator reports 453 combined pinned-Linux tests (450 passed, three
skipped); last native evidence remains the 276-test x64 CPython 3.13.3 baseline.
The prepared release framework's 26-pass isolated suite includes MSI Upgrade-row
audit fixtures, not native installation. No suite was rerun by this docs lane.

Final native pinned-runtime x64/ARM64 and exact-package qualification, signed
MSI install/upgrade/uninstall, Docker, browser/NVDA, large-fixture benchmarks and at
least ten ordinary Windows users remain gates. Signing infrastructure, devices
and participants are prerequisites: missing access is **blocked/not run**, never
passed. See [recorded validation](VALIDATION.md), not this runbook, for tests
actually performed. Do not promote 1.0.0 on documentation alone.
