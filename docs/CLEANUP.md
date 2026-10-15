# Reviewed file cleanup

Kshetrajna's cleanup surface is intentionally separate from Sort AI. Sort AI
organizes files; cleanup can permanently remove only files that the user has
reviewed in the current approved scan scope.

## Dashboard flow

1. Approve and scan one or more locations in **File Intelligence**.
2. Open **Disk Cleanup** and choose one ready/truncated scope or all approved
   locations.
3. Choose **Find empty files** or **Find duplicates**.
4. Review the exact paths and evidence. Empty candidates are exactly zero-byte
   regular files. Duplicate groups require matching live size and SHA-256 bytes
   for eligible regular files up to 100 MiB; matching names alone never
   qualify.
5. Choose **Review deletion**, confirm in the in-app dialog, and wait for the
   result. A deletion is permanent; the action journal records the outcome but
   Kshetrajna does not promise undo for cleanup deletion.

Project-aware boundary: when an indexed folder contains a project marker such
as `package.json`, `pyproject.toml`, `requirements.txt`, `go.mod`,
`Cargo.toml`, `Makefile`, or `.gitignore`, that folder and all descendants are
kept together. Empty or duplicate-looking files inside that project tree are
not cleanup candidates. General-purpose files outside detected project trees
remain eligible when they pass the other safety checks.

The last copy displayed in a duplicate group is marked **KEEP**. The backend
rebuilds the group immediately before deletion and refuses a stale request if
no verified peer remains.

## Command-line bridge

The same reviewed flow is available through the loopback/Jarvis bridge. Preview
first, then pass the returned file ID to an explicit deletion command:

```powershell
# Use an existing approved scope; omit --root-id to inspect all approved scopes
python -m kshetrajna.cli cleanup empty --url http://127.0.0.1:8765 --root-id 3
python -m kshetrajna.cli cleanup duplicates --url http://127.0.0.1:8765 --root-id 3

# Deletion is a separate command and always requires explicit confirmation
python -m kshetrajna.cli cleanup_empty_delete --url http://127.0.0.1:8765 `
  --file-id FILE_ID --confirm
python -m kshetrajna.cli cleanup_duplicate_delete --url http://127.0.0.1:8765 `
  --file-id FILE_ID --confirm
```

`empty_preview` and `duplicate_preview` are also accepted directly. The
`--path` and `--known-folder` options may be used for a preview; an explicit
path is approved and scanned before it is inspected. The JSONL bridge accepts
the same command names and remains loopback-only.

## Local API integration

All private GET requests need the page session's `X-Kshetrajna-Token` header.
All mutations also need the matching same-origin `Origin` header. The browser
already supplies both through the shared request helper.

```text
GET  /api/files/empty[?root_id=ID]
GET  /api/files/duplicates[?root_id=ID]
POST /api/files/empty/delete       {"file_id": ID, "confirm": true}
POST /api/files/duplicates/delete  {"file_id": ID, "confirm": true}
```

The duplicate response includes the SHA-256 digest, group size/count, wasted
bytes, `scan_partial`, the number of catalog rows inspected, and the per-file
hash limit. A partial result must be presented as partial rather than as proof
that no more duplicates exist. Cleanup only uses current rows from the latest
scan pass and only ready/truncated roots.

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
              -> FileActionManager journal -> identity/content recheck -> delete
              -> catalog update + action journal refresh
```

Run from the repository root with Python 3.13, keep `.python-version`, the
lockfiles, `Dockerfile`, and `.env.example` in the submission, and exclude
`node_modules/` and local databases. The offline verification command is:

```powershell
$env:PYTHONPATH = 'src'
python -m unittest discover -s tests -v
```
