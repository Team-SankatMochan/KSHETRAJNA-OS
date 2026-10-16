# Architecture and decisions

## Data flow

~~~mermaid
flowchart LR
  W[Win32 read APIs] --> C[Consent-gated collector]
  D[Synthetic demo probe] --> C
  C --> S[(Local SQLite events.db)]
  S --> I[Explainable context and patterns]
  I --> R[Recommendations]
  R --> U[User review]
  U --> J[Durable recovery journal]
  J --> A[Priority driver]
  A --> E[Observed before/after metrics]
  E --> F[Feedback and undo]
  F --> S
  I --> B[Saved dashboard workspace board]
  
   FS[File Scanner] --> DB2[(Local SQLite files.db)]
   DB2 --> FI[File Insights, Search & Content Review]
   FI --> SA[Sort AI rule provider]
   SA --> FP[Editable batch preview]
   FP --> FJ[File action journal]
   FJ --> DB
   FI --> DB[Dashboard]
~~~

## Technology decisions

| Choice | Reason | Tradeoff |
|---|---|---|
| CPython 3.13 | Available on Windows x64 and ARM64; readable, fast to iterate, standard SQLite and FFI | Native ARM64 installer is experimental; Python runtime must be installed |
| Win32 via ctypes | Direct read APIs and a narrow priority actuator without native wheel dependencies | ABI declarations require care; protected processes may be inaccessible |
| SQLite and explicit transactions | Durable local history, journal-before-write, simple schema upgrade | Per-user plaintext database; no encrypted-at-rest guarantee |
| Separate files.db (WAL mode) | Isolates file scanning from recovery journal; supports concurrent read/write | Two databases to manage |
| Standard-library HTTP server | Zero dependencies and a single local process | Loopback prototype only; not a production Internet server |
| Plain HTML/CSS/JS/SVG | Offline assets, no npm install, accessible semantic controls | DOM updates need care to preserve input and focus |
| Rule/context + counts | Each result has inspectable evidence; no cloud or model download | App-name categories miss unknown or ambiguous workflows |
| Separate native/simulated drivers | The demo can exercise lifecycle logic without touching Windows | A passing demo does not validate hardware-specific mutation behavior |
| OS file lock | Prevents two CLI instances sharing one live journal | Locking scope is the selected local data directory |
| Deterministic local content review | Uses the approved bounded text preview to describe source, data, notes, logs, drafts, placeholders and generated artifacts without a model download | It is heuristic evidence, not semantic certainty; binary/media content is metadata-only and uncertain items require review |
| Deterministic Sort AI provider | Gives explainable category suggestions without a model download or unverified ARM64 runtime | Content profiles improve explanations, but uncertain/conflicting items still require review |
| Loopback Jarvis bridge | Lets an assistant reuse the reviewed dashboard workflow through named JSON/HTTP commands | Loopback only; apply and undo require explicit confirmation; no arbitrary command execution |

## Observation contract

System CPU is a delta of GetSystemTimes counters across processors in the caller's processor group. Process CPU is normalized to the same total capacity. The first sample has no prior delta and reports zero. Memory is physical available/total RAM and process working set; these values do not measure reclaimable memory.

App identity is a PID plus the process creation FILETIME. Only the executable basename is persisted; the full image path is discarded after the native query. Foreground activity uses the current interactive session and last-input age, never input content.

Analysis uses up to 12,000 retained snapshots. Context uses the latest 24 samples; ranking durations remain capped at 60 seconds per interval. Pattern learning collapses stable visits, breaks on idle/unknown activity or gaps over 90 seconds, and mines recurring pairs/triples and ordered routines. A first/second-order sequence model uses recency decay, sparse-evidence shrinkage and ambiguity abstention. Chronological replay reports accuracy and coverage against a first-order baseline. Scores are not calibrated probabilities. See [patterns and predictions](PATTERNS.md) for thresholds, privacy behavior and limitations.

## Action contract

Only a fresh server-generated proposal can be approved. The browser sends its opaque ID, never an arbitrary PID or priority value. Both live opt-in and collection must be enabled. One unresolved trial is permitted at a time.

The driver restricts targets to an application allowlist, the current session, a matching PID/creation token and basename, a background app, and current Normal priority. It can only lower to Below normal and restore to Normal. A durable prepared record is written before the native mutation. This record survives a crash between native execution and the active-state update.

Expiry is checked about once per second, independently of sampling frequency. Pausing or disabling the live-trial preference triggers restoration. A fresh sample showing the target in the foreground also triggers restoration. Manual Undo, graceful shutdown, and startup recovery all use the same restore path.

If the target exited or the PID was reused, restoration is marked target_exited. If another actor changed its priority to something else, restoration is marked superseded and leaves that external value alone. If restoration fails, its recovery record remains and history deletion is blocked until it is resolved.

Rollback applies to the selected process. Children created while its priority is lowered can inherit that class; restoring descendants is not implemented. The prototype must therefore be described as an experimental target-process trial, not an unconditional whole-application rollback guarantee.

## Maintenance action contract

Windows maintenance is a separate, explicitly opt-in surface (`allow_maintenance`) from the reversible priority-trial surface. Each action is previewed for 60 seconds and recorded in the decision journal before execution. The fixed actions are: opening Windows Disk Cleanup, opening Storage settings, opening the current temporary folder, resetting Microsoft Store cache, and requesting `EmptyWorkingSet` for one eligible background process. No shell command or arbitrary executable is accepted.

The first four actions delegate to Windows interfaces and do not report completion or reclaim amounts. Working-set trim records before/after resident bytes when available, may cause page faults or slower responses, and has no exact automatic undo. None of these actions is a causal performance claim. A maintenance failure or interrupted request remains recorded as an outcome and is never replayed automatically.

## Privacy and trust boundaries

- Live and synthetic demo storage are separate. Demo storage is temporary; demo never instantiates the native action driver or accesses the filesystem for file intelligence.
- The service binds only 127.0.0.1. Exact Host checks reject ordinary DNS rebinding. Mutations require matching Origin and a random page-session token.
- API bodies are bounded to 4096 bytes; transfer encoding is rejected. The UI uses textContent for app names and user workspace labels.
- SQLite schema migration adds process identity tokens to existing Phase 1 databases. Old rows have token zero and cannot create actionable native proposals.
- Retention runs during both enabled and paused collection. Pending recovery records are exempt so deletion cannot discard the path to restoration.
- A local program running as the same user can access the user's files/service. This is not an adversarial multi-user security boundary.
- Conventional folder discovery checks only known directory paths and never enumerates contents until the user approves a root. The first live telemetry acknowledgement is persisted in `config.json`; file-root approvals remain separate.
- System Intelligence telemetry and File Intelligence have separate databases and deletion controls. System telemetry excludes document names/content; File Intelligence intentionally stores approved root paths, relative file names, metadata, and bounded excerpts from allowed text files until the user deletes that catalog.

## File Intelligence contract

- **Opt-in Roots:** Scanning is restricted to user-approved root directories. The dashboard shows an explicit file-access permission dialog before sending the root-add request; the API also requires `consent: true`. System paths, `C:\`, and Kshetrajna's own data directory are rejected. Network (UNC) paths are not supported.
- **Path Security:** The browser sends an absolute path only when the user creates an approved root. After creation, all file APIs use opaque positive integer IDs. Scanner and action logic enforce both lexical and resolved containment using `Path.resolve().is_relative_to()` and reject links/reparse points along the path.
- **Reparse Points:** Symlinks, junctions, volume mount points, and OneDrive cloud placeholders (`FILE_ATTRIBUTE_REPARSE_POINT`) are strictly ignored to prevent traversal and unintended cloud downloads. A scan that reaches the file limit is reported as `truncated`, not `ready`.
- **Bounded Extraction:** Only listed plain-text file extensions have their contents extracted, strictly bounded to 4KB, sanitized of control characters. The bounded preview powers deterministic content profiles and cautious review signals such as empty, placeholder, draft, stale-log, or generated-artifact warnings. Known sensitive filenames (`.env`, `id_rsa`) are cataloged but their contents are never extracted.
- **One-click Review:** A review candidate opens a bounded source/text preview in the dashboard with line numbers for code/configuration. DOCX/PPTX/XLSX previews extract text from safe Office XML parts without executing macros or rendering media. Approved PDFs are streamed into an in-dashboard sandboxed document viewer and also receive bounded text extraction. Approved PNG/JPEG/GIF/WebP/BMP images are streamed into an in-dashboard fit/actual-size viewer after signature, dimension, size, identity, and path checks; no external app is launched. Raster pixels are displayed but are not claimed to have semantic vision recognition.
- **Independence:** Removing File Intelligence data (`files.db`) does not interact with or compromise the System Intelligence recovery journal (`events.db`).
- **Cleanup contract:** Cleanup previews operate only on ready or truncated approved roots. Zero-byte candidates are checked against the live filesystem before display and reviewed cleanup moves them into the protected `.kshetrajna-quarantine` directory rather than deleting them. Duplicate groups first match live file sizes, then verify full SHA-256 content for eligible regular files (bounded to 100 MiB per file); links, reparse points, protected project/structural names, sensitive files, unavailable roots, and stale catalog rows are excluded. A cleanup action requires `confirm: true`, journals identity/content evidence before mutation, revalidates that a duplicate peer survives, and removes the catalog row only after the quarantine move succeeds. List and restore operations keep the recovery record durable; the legacy generic file-action `DELETE` endpoint remains the separate permanent, non-undoable compatibility path.

## Sort AI action contract

Sort AI is a local deterministic provider, not a generative model. It maps
indexed extensions and bounded content profiles/excerpt tags to category folders
and returns a reason plus an evidence score. The dashboard presents grouped,
plain-language ideas such as “I feel these files belong in one folder. Should I
create Code at this Desktop path?” or “I feel this file belongs in the existing
folder named Documents at this path. Should I move it?” Each idea requires a
separate confirmation. A plan expires after five minutes and is stored before
any directory or file mutation. The server validates every component again and
refuses unchanged paths, traversal, links, duplicate destinations, and existing
files.

Every indexed file receives an automatic purpose/action decision with confidence
and evidence. The decision combines path and sibling context with the bounded
preview: it can distinguish study material, audit evidence, project source,
tabular data, media, logs, and regenerable caches. Low-confidence, numbered,
empty, or generated-looking files receive a review action; the system never
deletes or moves a file because a heuristic looked suspicious.

Empty category folders are created only for the conversational idea the user
explicitly confirms. Each selected move creates the existing file-action
proposal first, then applies it with size/time/identity checks. Outcomes can be
applied, partial, failed, or undone; approving one idea leaves other ideas
pending. Undo runs in reverse order and never overwrites a path that became
occupied or changed. A directory created by a plan is removed only if it is
still empty; cleanup is best effort and never recursive deletion. A file can
also receive the separate, explicitly confirmed generic file-action
permanent-delete action; that compatibility path is journaled but is not
undoable by Kshetrajna. Reviewed empty/duplicate cleanup uses quarantine instead.

The plan and suggestion tables are in `files.db`. Startup reconciliation links
an action that completed just before a crash back to its plan. Deleting the
file catalog is refused while proposed, prepared, applied, failed-undo, or
unresolved quarantine records still need recovery.

## Outcome interpretation

Up to six preceding samples form the baseline. At least three post-approval samples are needed to display a CPU or memory delta. The journal calls this an observed change; unrelated work can account for it. Synthetic values do not respond to approval, so the demo cannot fabricate a performance improvement.

## Temporary-file maintenance contract

The maintenance panel uses only the current `%TEMP%` location for its optional
cleanup preview. It bounds the candidate count and total bytes, ignores recent,
empty, sensitive, symlink/reparse, and inaccessible files, and captures size
and modification time before approval. Execution rechecks those values and
reports deleted and skipped items. Deletion is permanent and has no automatic
undo. Opening Explorer, Storage settings, Disk Cleanup, and Store reset are
separate fixed Windows operations and do not claim a reclaimed amount.

## Platform references

- [Python 3.13 Windows and ARM64 distributions](https://docs.python.org/3.13/using/windows.html)
- [Windows on Arm overview](https://learn.microsoft.com/en-us/windows/arm/overview)
- [GetSystemTimes and processor-group limits](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-getsystemtimes)
- [GetProcessMemoryInfo](https://learn.microsoft.com/en-us/windows/win32/api/psapi/nf-psapi-getprocessmemoryinfo)
- [GetPriorityClass](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-getpriorityclass)
- [SetPriorityClass](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-setpriorityclass)
