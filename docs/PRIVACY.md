# Privacy and stored-data inventory

This inventory describes **0.5.0 source at `25a0de7`**, with planned changes identified
separately. Kshetrajna's live service binds to `127.0.0.1`, has no analytics or
automatic telemetry/crash upload, and does not require a cloud account. Optional
local AI contacts a separately installed loopback Ollama runtime. A program
running as the same Windows user can access that user's service and files;
loopback/token/origin checks are not a sandbox or Windows-account isolation.

## Separate current controls

| Control | Default | Data and behavior |
|---|---|---|
| Collection | Off on first live launch | First acknowledgement is saved in `config.json`; CPU/physical RAM, nullable commit/cache/OS low-memory and system-volume disk readings, bounded process samples, executable basenames, PID/creation tokens, foreground/idle and activity events |
| Process / foreground samples | On after collection consent | CPU/working sets and foreground basename, not window titles, URLs, keystrokes, clipboard, screenshots, command lines or document contents; protected/disappearing processes can be omitted |
| Live priority trials | Off | Reviewed one-process Normal → Below normal trial, identity checks, expiry and target-process recovery |
| Windows maintenance | Off | Fixed operations and reviewed temporary-file cleanup; this is not consent to file indexing |
| Reviewed app controls | Off | Separate app-control opt-in; a close request or typed-name force termination still needs review and approval |
| Permanent deletion | Off | Separate `allow_permanent_delete` danger permission; UI controls also need Expert visibility, which never grants permission |
| File Intelligence roots | Empty | UI asks permission for each selected root; scanning includes metadata **and allowed content excerpts** in this baseline |
| File watching | Opt-in per approved location | Can rescan an approved Downloads root and refresh previews; does not execute moves; independent of system collection pause |
| Sort AI actions / folder creation | Reviewed approval | Exact selected idea/batch can move files and create folders; previews alone do not authorize a move |
| Local AI | Off | Separate opt-in plus an installed compatible local model; PC tools can work without it |

There is no administrator/UAC requirement for normal launch. Application consent
does not grant Windows access to protected resources. System telemetry consent
does not approve file roots. New roots require literal `consent:true` at the
service boundary. CLI `--path`/`--known-folder` requires `--consent` (JSONL:
`"consent":true`); preview arguments alone no longer approve access. Explicitly
approving that scan still includes excerpts. Use `--root-id` for an existing
approved root. Service apply/undo/cleanup/restore and history/catalog deletion
require literal `confirm:true`; browser dialogs are not the only check.

Permission generations are service-managed in `config.json` and reject stale
irreversible plans after relevant consent changes, including off/on cycles.
They are not a writable public settings field. Narrow canonical approvals also
get revoked when their relevant permissions change. Necessary domain recovery
remains available with its identity/no-overwrite checks, not permission for new
work. See the [action contract](ACTIONS.md) for exact coverage and limitations.

## Stored locations and content

Live data defaults to `%LOCALAPPDATA%\Kshetrajna` (or a chosen `--data-dir`):

| Store | Current contents / boundary |
|---|---|
| `config.json` | Validated collection, retention, action and local-model preferences plus private service-managed `_consent_versions` |
| `events.db` | System snapshots, activity, app transitions, decisions, workspaces and priority recovery journal; system telemetry stores executable basenames, not full executable paths |
| `files.db` | Approved **absolute root paths**, labels, relative file names/paths, sizes/times, scan state, **persisted excerpts**, file-action identities/outcomes, Sort AI plans/reasons/selections and recovery records |
| `actions.db` | Canonical intent, private target/evidence (including process identity, observations and permission bindings), consent/claim metadata, outcomes and transition events for the narrow rollout; not governed by telemetry deletion/retention |
| `kshetrajna.log`, `.1`, `.2` | Operational messages and exception tracebacks; rotation is configured at 500,000 bytes with two backups, not the system-history retention period |
| `.kshetrajna-quarantine` under an approved root | The actual reviewed file bytes, plus recovery evidence in the file journal; excluded from indexing and cleanup previews |
| User-saved reports / copies / backups | Remain wherever saved; app deletion controls do not erase external copies |

`actions.db` is implemented, but only four system kinds are wired through v1.
Legacy routes remain domain-authoritative and
`canonical_authority_complete` is false. Public action responses project
allowlisted metadata; proposal/detail add a validated **persisted review DTO**,
not arbitrary raw paths, plans/results or permission generations. The review is
historical evidence, not current eligibility or an executable plan. Separate
system/file privacy domains remain; cross-database writes are not crash-atomic.

### Extraction is not metadata-only today

`file_service.py` extracts and stores allowed text, Office and PDF excerpts while
scanning, including reuse of unchanged stored excerpts. The current root approval
does **not** provide a separate metadata-only/content-inspection switch.

- Plain text: first **4,096 bytes** read, UTF-8 decoded and control-character
  filtered. This is a byte bound, not 4,096 characters.
- DOCX/PPTX/XLSX: selected XML text parts only; output at most **4,096 UTF-8
  bytes**, with a separate 64 KiB XML-read budget. No macro execution, external
  link resolution or embedded-object rendering is claimed.
- PDF: signature-checked input at most **32 MiB**, decompression budget **2 MiB**,
  final excerpt at most **4,096 characters**. Despite its internal constant's
  name, this is **not a guaranteed 4 KiB UTF-8 output bound**. Extraction is
  partial/heuristic, not full PDF understanding or OCR.
- Known sensitive names/types receive no excerpt; sensitive, generated and
  protected directory lists skip traversal. Metadata may still identify a
  sensitive file outside skipped directories. Lists are not a guarantee that
  every secret is recognized.
- Raster images may be displayed locally after signature, size, dimension and
  identity checks. PDFs may be streamed into a sandboxed document viewer. The
  dashboard does not launch those documents externally; rendering is not a
  semantic image-vision capability. Other unsupported binaries use metadata.

Purpose/action evidence and review warnings can be derived from excerpts,
filenames and related-file context and persisted with plans. They are guesses
for review, not authorization to move/delete anything.

### Logs and reports need review before sharing

HTTP access logging is suppressed, but scan, telemetry and restore failures can
log exception tracebacks. Those may contain file paths or other private OS
details. **There is no blanket guarantee that logs exclude full paths or all
sensitive strings.** No comprehensive automatic redaction/export-erasure claim
is made. Read [crash reporting and recovery](CRASH_REPORTING.md); share only a
reviewed, minimized copy, never a raw catalog, token or private file by default.

## Pause, retention and deletion

- System collection pause stops new observations; it does not delete retained
  data, pause separately enabled file watching, or erase earlier chat messages.
- System history defaults to seven days (selectable one to thirty). This is not
  automatic expiry for `files.db` excerpts, quarantine content or log rotations.
- System-history deletion attempts restoration of active priority trials before
  removing observations/derived history. Failed recovery records are retained;
  deletion cannot discard the only restoration path. Chat state is also cleared.
- `/api/delete` returns `scope:"telemetry_history"` and
  `canonical_history:"retained_use_explicit_erasure"`. It revokes pending
  canonical approvals, **not canonical history**. Selected canonical payload
  erasure uses `/api/v1/actions/erase-history` with literal confirmation and exact
  IDs; unresolved/recoverable work is preserved with reasons. Action rows,
  approval metadata, replay hashes and event tombstones remain. Best-effort WAL
  truncation may report `storage_cleanup:"pending"`; this is not backup/media
  erasure and never clears domain recovery journals.
- File catalog/root deletion removes catalog data, not original approved-folder
  files. Unresolved file actions/quarantine block deletion or root removal;
  restore/reconcile them first. Do not delete databases manually to bypass this.
- Quarantine retains bytes, normally on the same volume: it is recoverability,
  **not recovered capacity**. The reviewed cleanup UI/CLI has no final purge.
  Legacy generic DELETE is separately permanent and now service-gated by the
  default-off danger opt-in; its UI is Expert-only. Temporary-file maintenance
  deletion is also permanent and still has no canonical v1 adapter.
- Preferences and diagnostic logs remain after history/catalog deletion. SQLite
  deletion is not secure erasure: WAL/SHM sidecars, database free pages, exports,
  backups and runtime-owned logs need separate retention/erasure design. Removing
  program files likewise does not erase user data.

SQLite is not application-encrypted. Windows profile permissions and the local
service boundary are current protections, not encryption-at-rest guarantees.

## Local assistant

After separate local-AI consent, the conversation and an available fresh system
summary go to `127.0.0.1:11434`. The application does not automatically attach
file excerpts, paths or index listings. **Anything the user types/pastes into
chat is part of the prompt**, including private content they choose to supply.
Cloud-labelled/remote models are rejected; there is no arbitrary provider URL,
automatic model pull or shell-execution interface.

Collection pause omits a new PC summary; previous conversation content remains
until cleared. Bounded chats/jobs stay in application memory, not a chat database,
and clear on new chat, expiry, system-history deletion or shutdown. This is not
forensic erasure of OS paging/browser/runtime memory. Ollama's own logs and
retention are separate. Approved process outcomes are persisted in the system
journal with executable basename, PID and creation token. A model reply, file
name or document instruction never authorizes execution.

## Remaining final privacy gates

Separate telemetry/root/maintenance/app-control/local-AI permissions, strict
confirmations, private generation invalidation and Basic/Expert visibility are
implemented. **Separate content-inspection consent and metadata-only default
are not**: old excerpts still need a visible migration/erasure policy. Stable
v1 error codes and bounded review projection do not establish comprehensive
redaction/retention across legacy errors, logs, exports, sidecars and backups.
The prepared release framework's allowlisted diagnostic exporter is a narrow
summary tool, not whole-service redaction. See [crash reporting](CRASH_REPORTING.md).

No whole-service elevation is authorized. Native, browser/accessibility,
artifact and user qualification still need evidence under the
[release contract](FINAL_RELEASE.md); the coordinator owns
[actual check results](VALIDATION.md). This inventory does not complete phases
2–11 or promote 1.0.0.
