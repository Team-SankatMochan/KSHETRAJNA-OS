# Sort AI and reviewed file organization

Sort AI is the implemented deterministic organization component of the **0.5.0
baseline**, in the **Organize Files** destination. Basic/Expert navigation and a
narrow canonical action ledger exist at `25a0de7`, but Sort AI remains
domain-authoritative: file kinds are deferred in v1 and
`canonical_authority_complete` is false. See [action coverage](ACTIONS.md) and
[remaining final work](FINAL_RELEASE.md), not a claim that 1.0.0 is ready.
Historical 0.4.3 bundles include an earlier organizer/watcher, not current
quarantine or final privacy/consent changes.

**Current privacy boundary:** approving a scan permits persistence of metadata
and allowed text/Office/PDF excerpts in `files.db`; metadata-only indexing and
separate content consent are not the current default. Text/Office output is
bounded to 4,096 UTF-8 bytes; PDF output to 4,096 characters, not guaranteed
4 KiB. See [privacy and retention](PRIVACY.md).

## Use it

1. In **Organize Files**, find Downloads, Desktop, or another known folder and
   choose **Approve and scan**. Paths come from Windows' known-folder API,
   including redirected locations. OneDrive environment locations are also
   discovered.
2. For a laptop-wide organizing pass, choose **Choose locations · scan
   together**, select the personal/data folders you want, and approve once.
   File Intelligence never silently scans the whole machine: each location is
   shown before indexing, and system/app-data folders, credentials, links, and
   cloud-only entries remain excluded from traversal/inspection as applicable.
   Sensitive-file metadata may still be cataloged without an excerpt; exclusion
   lists are not a guarantee that every secret is recognized.
3. Watch the scan status and indexed count. Errors, skipped entries, and
   partial scans are shown beside each folder. Choose one ready folder in Sort
   AI, or choose **All approved locations** to compare the selected sources and
   all approved destinations together.
4. Choose **Auto-sort clear matches**. It builds a fresh preview and asks once
   to approve the batch. Clear loose files move into their proposed folders;
   folder creation is included. When an approved Desktop exists, clear loose
   files from an approved Downloads root use that Desktop as the destination
   (with category folders such as `Pictures` or `Documents`); they no longer
   get cleared out only inside Downloads. **Preview sort** lets you inspect
   first.
5. Only ambiguous files/conflicts stay in the advice queue. Use **Preview &
   decide**, approve specific reviewed moves, or choose **Keep these here**.
   Keep decisions persist until the indexed size or modification time changes.
6. **Undo eligible changes** restores the batch without overwriting changed
   paths. **Recent sort batches** lets you inspect/undo an earlier batch after
   generating another preview. Individual moves remain in the file-action journal.

## How destinations are chosen

The same local evidence engine supplies file insights and sorting. It uses
file type, filename, approved bounded excerpts and sibling/project context.
Clear loose images go to Pictures, videos to Videos, audio to Audio, archives
to Archives, presentations to Presentations, spreadsheets to Data/Spreadsheets,
and installer-labelled packages to Installers. Documents retain stronger
subject-based destinations when evidence supports them. Existing matching
folders in any approved location are preferred, and a saved project scope (for
example `Aarthika` plus aliases) outranks generic category routing. Repeated
project-name groups can also reuse one unique existing project folder without
requiring a manual rule first.

This is deterministic local classification, not image understanding or a
generative semantic model. Extension-based categories describe format evidence,
not the truth of a file's content. Unknown binaries, generic names, incomplete
downloads, numbered/possibly duplicate files, conflicting destinations, and
multiple subject matches require advice. Nothing is deleted by sorting.

## Bounds and scanning

Automatic batches contain at most 500 unblocked loose-file moves. Existing
nested collections and projects stay together. Scanning skips links/cloud-only
entries, protected folders and generated dependency/cache folders. Skipped
counts are displayed. Cloud-only files must be made available locally first;
links/junctions remain unsupported. Missing/unreadable roots show an error.

An individually approved root scans up to 50,000 files; a location selected in
the multi-location approval dialog gets a 200,000-file limit. A truncated scan
can sort the files indexed in its latest pass, with a partial-scan notice for
each affected source; old unseen catalog rows are excluded. No scan or
mutation runs without folder approval.

## Watch new downloads

In **File library**, select **Watch new files** on an approved Downloads folder.
The preference is saved per location and can be paused. While the app is open,
it checks immediate files about every two seconds, waits at least four seconds
for an unchanged size and modification time, and then refreshes the index and
creates a new Sort AI preview. Partial browser downloads (`.crdownload`, `.part`,
`.partial`, `.tmp`, `.download`, `.opdownload`), sensitive files and links are
ignored. A stable size is a best-effort completion signal, not a browser event.

The folder card shows the result and a **Review new arrivals** button. Old
unexecuted previews expire when new arrivals trigger a rescan. Applied batches
remain undoable. Watching never approves or executes a move, and stops when
Kshetrajna quits. It resumes next launch for enabled locations. Subdirectories
are indexed during the rescan; watch detection itself examines at most 20,000
immediate entries per location, with an explicit limit message.

Nested collections stay together by default. Turn on **Include nested files in
review** only when you want possible misplaced files inside subfolders surfaced;
these moves are advice-only, never part of the automatic batch. Project marker
folders and generated dependencies remain protected.

File moves retain path, identity, overwrite and journal checks. Rescans are
queued without discarding a new request during worker shutdown. UI refreshes
do not replace an active plan or collapse unchanged review details.

## Integration and final gates

Use the running loopback service for CLI/assistant integration, with roots
approved in the UI and selected by ID. New roots selected with `--path` or
`--known-folder` require explicit `--consent` (JSONL: `"consent":true`); preview
arguments alone cannot approve them. Approval still includes excerpt scanning.
Sort apply/idea/batch/keep/organize and undo now require literal `confirm:true`
in the service, not only the bridge. Domain plans, exact selections,
destinations, expiry, identity and no-overwrite checks remain authoritative;
canonical file adapters are still pending. Expert view never grants root access
or approves moves; necessary recovery is available in both modes.

The optional local model cannot authorize scanning or sorting, and filenames,
excerpts and model replies are untrusted input. No arbitrary shell execution,
remote provider, model download or NPU dependency is introduced by Sort AI.
Cleanup remains [a separate quarantine flow](CLEANUP.md): retained bytes are not
capacity recovered, and sorting does not purge anything.

Current fixture coverage and historical browser/package checks are recorded in
[validation](VALIDATION.md); they do not complete Windows reparse-race, exact
candidate x64/ARM64 package, final privacy, accessible UI, hardware/performance or
human-user qualification. See [final gates](FINAL_RELEASE.md).

## Windows API references

- [SHGetKnownFolderPath](https://learn.microsoft.com/en-us/windows/win32/api/shlobj_core/nf-shlobj_core-shgetknownfolderpath)
- [Known folder identifiers](https://learn.microsoft.com/en-us/windows/win32/shell/knownfolderid)
