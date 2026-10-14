# Sort AI 0.4.1

## Use it

1. In File Intelligence, find Downloads or another known folder and choose
   **Approve and scan**. Paths come from Windows' known-folder API, including
   redirected locations. OneDrive environment locations are also discovered.
2. Watch the scan status and indexed count. Errors and skipped entries are
   shown beside the folder. Choose the scanned folder in Sort AI.
3. Choose **Auto-sort clear matches**. It builds a fresh preview and asks once
   to approve the batch. Clear loose files move into their proposed folders;
   folder creation is included. **Preview sort** lets you inspect first.
4. Only ambiguous files/conflicts stay in the advice queue. Use **Preview &
   decide**, approve specific reviewed moves, or choose **Keep these here**.
   Keep decisions persist until the indexed size or modification time changes.
5. **Undo eligible changes** restores the batch without overwriting changed
   paths. **Recent sort batches** lets you inspect/undo an earlier batch after
   generating another preview. Individual moves remain in the file-action journal.

## How destinations are chosen

The same local evidence engine supplies file insights and sorting. It uses
file type, filename, approved bounded excerpts and sibling/project context.
Clear loose images go to Pictures, videos to Videos, audio to Audio, archives
to Archives, presentations to Presentations, spreadsheets to Data/Spreadsheets,
and installer-labelled packages to Installers. Documents retain stronger
subject-based destinations when evidence supports them.

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

The 50,000-file limit remains explicit. A truncated scan can sort the files
indexed in its latest pass, with a partial-scan notice; old unseen catalog rows
are excluded. No scan or mutation runs without folder approval. This release
does not watch folders continuously or sort future downloads in the background:
rescan and start another batch when needed.

File moves retain path, identity, overwrite and journal checks. Rescans are
queued without discarding a new request during worker shutdown. UI refreshes
do not replace an active plan or collapse unchanged review details.

## Windows API references

- [SHGetKnownFolderPath](https://learn.microsoft.com/en-us/windows/win32/api/shlobj_core/nf-shlobj_core-shgetknownfolderpath)
- [Known folder identifiers](https://learn.microsoft.com/en-us/windows/win32/shell/knownfolderid)
