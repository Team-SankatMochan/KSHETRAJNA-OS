# Reviewed file cleanup

Kshetrajna's cleanup surface is intentionally separate from Sort AI. Sort AI
organizes files; cleanup acts only on files that the user has reviewed in the
current approved scan scope. A confirmed live cleanup does not immediately
purge the file: it moves it into the protected `.kshetrajna-quarantine`
directory under the approved root and records an `action_id`.

This is the **0.5.0 source flow at `25a0de7`** in **Recover Space**. Basic/Expert
navigation is implemented, with recovery in both modes. Cleanup/restore still
use domain-authoritative unversioned routes: their canonical v1 kinds are
deferred even though `actions.db` exists. See [action coverage](ACTIONS.md) and
the [final gates](FINAL_RELEASE.md).

## What is (and is not) recovered

Quarantine keeps the content, normally on the original volume. **Moving a file
into quarantine does not free capacity.** A zero-byte file offers zero
content-byte savings. Duplicate totals describe potential duplicate storage if
a copy were eventually purged, not space recovered by reviewed cleanup. The
legacy `wasted_bytes` / `total_wasted_bytes` response fields must be interpreted
that way; no measured free-space gain is established by them.

There is no final-purge command in this reviewed flow. The final product must
separate candidate bytes, retained quarantine bytes, verified actions and
measured free-space changes, with concurrent-workload caveats. Windows Disk
Cleanup/Storage tool launches are separate and do not verify completion.

## Dashboard flow

1. Approve and scan one or more locations in **Organize Files**.
2. Open **Recover Space** and choose one ready/truncated scope or all approved
   locations.
3. Choose **Find empty files** or **Find duplicates**.
4. Review the exact paths and evidence. Empty candidates are exactly zero-byte
   regular files. Duplicate groups require matching live size and SHA-256 bytes
   for eligible regular files up to 100 MiB; matching names alone never
   qualify.
5. Choose **Review quarantine**, confirm in the in-app dialog, and wait for the
   result. A successful live cleanup returns `deleted:true`,
   `quarantined:true`, and an `action_id`. The original path is removed from
   the catalog and filesystem, while the reviewed file remains in protected
   quarantine until it is restored.
6. Open **History & Recovery**, review the quarantine record, and restore only the
   exact action you intend to recover. Restoration moves the file back to its
   original path after identity/content checks and refreshes the catalog.

Project-aware boundary: when an indexed folder contains a project marker such
as `package.json`, `pyproject.toml`, `requirements.txt`, `go.mod`,
`Cargo.toml`, `Makefile`, or `.gitignore`, that folder and all descendants are
kept together. Empty or duplicate-looking files inside that project tree are
not cleanup candidates. General-purpose files outside detected project trees
remain eligible when they pass the other safety checks.

The last copy displayed in a duplicate group is marked **KEEP**. The backend
rebuilds the group immediately before deletion and refuses a stale request if
no verified peer remains.

The quarantine directory is structural and protected: its contents are not
indexed, searched, or returned as cleanup candidates. The current milestone has
no permanent final-purge API or CLI command. `deleted:true` therefore means
that the original path was removed and the file was quarantined, not that its
content was irreversibly purged. The **Delete all file data** catalog action is
also not a purge operation; unresolved file-action/quarantine records block
catalog deletion and approved-root removal until they are resolved.

This contract applies to the current reviewed cleanup endpoints in this document. The
legacy generic file-action API (`/api/files/actions/propose` with
`kind:"DELETE"`) remains a separate, explicitly confirmed permanent-delete
capability for compatibility with existing file-action callers. It is not used
by the empty-file/duplicate cleanup UI or CLI.

Permanent deletion now requires the separate, default-off
`allow_permanent_delete` **danger permission** in the service; the UI shows it
only in Expert. Switching views never grants that permission. Its proposal is
generation-bound and cannot survive permission off/on or restart as an approval.
It is never normal cleanup. Permanent temporary-file maintenance is distinct from
file quarantine and has no automatic undo; see [architecture](ARCHITECTURE.md).

## Command-line bridge

The same reviewed flow is available through the loopback/Jarvis bridge. Preview
first, then pass the returned file ID to the explicitly confirmed quarantine
command (its legacy name still says `delete`):

```powershell
$env:PYTHONPATH = 'src'
# Use an existing approved scope; omit --root-id to inspect all approved scopes
python -m kshetrajna.cli cleanup empty --url http://127.0.0.1:8765 --root-id 3
python -m kshetrajna.cli cleanup duplicates --url http://127.0.0.1:8765 --root-id 3

# The reviewed delete moves the file into protected quarantine and returns ACTION_ID
python -m kshetrajna.cli cleanup_empty_delete --url http://127.0.0.1:8765 `
  --file-id FILE_ID --confirm
python -m kshetrajna.cli cleanup_duplicate_delete --url http://127.0.0.1:8765 `
  --file-id FILE_ID --confirm

# Review quarantine after the cleanup result
python -m kshetrajna.cli quarantine list --url http://127.0.0.1:8765 --root-id 3

# Restore one exact action; this is recovery, not final purge
python -m kshetrajna.cli quarantine restore --url http://127.0.0.1:8765 `
  --action-id ACTION_ID --confirm
```

`empty_preview` and `duplicate_preview` are also accepted directly. Prefer
approving/scanning a root in the dashboard and supplying `--root-id`. A new root
selected with `--path` or `--known-folder` now requires explicit `--consent`
(JSONL: `"consent":true`) before approval and scanning, including excerpts.
Without it a preview does not approve new access. The JSONL bridge accepts
the same command names and remains loopback-only. Quarantine listing accepts an
optional `--root-id`; restore accepts only an exact `--action-id` and literal
`--confirm`.

## Local API integration

These are existing unversioned domain routes, not wired v1 cleanup resources.
All private GET requests need the page session's `X-Kshetrajna-Token` header.
All mutations also need the matching same-origin `Origin` header. The browser
already supplies both through the shared request helper.
The service itself requires literal `confirm:true` for quarantine and restore;
truthy strings/numbers and missing confirmation are not accepted.

```text
GET  /api/files/empty[?root_id=ID]
GET  /api/files/duplicates[?root_id=ID]
GET  /api/files/quarantine[?root_id=ID]
POST /api/files/empty/delete       {"file_id": ID, "confirm": true}
POST /api/files/duplicates/delete  {"file_id": ID, "confirm": true}
POST /api/files/quarantine/restore {"action_id": "ACTION_ID", "confirm": true}
```

The cleanup delete endpoints retain their existing names, but a successful live
response includes `deleted:true`, `quarantined:true`, and `action_id`; it moves
the file into `.kshetrajna-quarantine`. Quarantine listing returns recovery
metadata without file contents. Private GETs require the page session's
`X-Kshetrajna-Token`; POSTs also require the matching same-origin `Origin`.

The duplicate response includes the SHA-256 digest, group size/count, potential
duplicate-content bytes (`wasted_bytes`), `scan_partial`, the number of catalog rows inspected, and the per-file
hash limit. A partial result must be presented as partial rather than as proof
that no more duplicates exist. Cleanup only uses current rows from the latest
scan pass and only ready/truncated roots.

## Recovery and release limits

Keep the original approved root, protected quarantine directory and file-action
journal together. Restore by exact action ID; occupied or changed destinations
must not be overwritten. Do not manually purge quarantine or delete `files.db`
to bypass a blocked recovery record. Catalog deletion is not secure erasure of
SQLite sidecars, logs, exports or backups; see [privacy](PRIVACY.md).

The coordinator reports **453 combined pinned-Linux tests: 450 passed, three
skipped**; the last native baseline remains 276 passed on x64 CPython 3.13.3.
This docs lane did not rerun them; [validation](VALIDATION.md) belongs to the
coordinator. There is no newer native qualification of the pinned Windows
runtime, new packages, ARM64, concurrent reparse races or cross-volume/device
matrix. The old 0.4.3 archives predate quarantine.
Crash-injection boundaries, exact packaged x64/ARM64 tests, signing, user/device
and accessibility checks remain [final gates](FINAL_RELEASE.md), not passes.
See [crash recovery/reporting](CRASH_REPORTING.md) before sharing diagnostics.

## Buildathon integration

For Frontier Buildathon 2026, this project fits **Track 04 — Systems,
Infrastructure & Cybersecurity** best: it is a local Windows system/file
assistant with explicit consent, path safety, bounded inspection, and audited
destructive operations. The broader Sort AI automation also supports Track 01
(**Agents & Automation**), but the submission accepts one selected track, so
use Track 04 as the primary lane unless the dashboard asks for a different
project framing.

Keep this feature in the existing File Intelligence flow rather than calling
`os.remove` from the UI or from a new script. The integration boundary is:

```text
approved scan -> current catalog row -> cleanup preview -> explicit confirm
              -> FileActionManager journal -> identity/content recheck
               -> move into protected quarantine -> catalog/action refresh
               -> quarantine list -> explicit restore (optional)
```

The generic file-action DELETE path is outside this flow and remains permanent.

Run from the repository root with Python 3.13.16, keep `.python-version`, the
lockfiles, `Dockerfile`, and `.env.example` in the submission, and exclude
`node_modules/` and local databases. The offline verification command is:

```powershell
$env:PYTHONPATH = 'src'
python -m unittest discover -s tests -v
```
