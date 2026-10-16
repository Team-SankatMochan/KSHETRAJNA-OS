# Privacy and stored-data inventory

Kshetrajna is local-first. The service binds to `127.0.0.1`, does not upload
telemetry, and has no analytics or cloud dependency. A program running as the
same Windows user can still access the user's local service and files; this is
not an adversarial same-user security boundary.

## Separate controls

| Control | Default | Data and behavior |
|---|---|---|
| Collection | Off on first live launch | The first enable acknowledgement is persisted once in `config.json`; CPU, physical memory, bounded process samples, executable basename, PID plus creation token, foreground basename, idle duration, and activity events |
| Process samples | On after collection consent | Top process CPU/working-set values; protected or disappearing processes are skipped and reported through health state |
| Foreground tracking | On after collection consent | Foreground executable basename and app/idle transitions; no titles, URLs, keystrokes, clipboard, screenshots, or document content |
| Live priority trials | Off | One allowlisted background process, Normal → Below normal, fresh identity and telemetry checks, bounded expiry and restore |
| Windows maintenance | Off | Fixed Windows actions only; each action gets a separate preview and approval |
| File Intelligence roots | Empty | Only explicitly approved directories are scanned. Network roots, system paths, links and reparse points are rejected |
| Sort AI folder creation | Off until confirmation | Category folders are created only for a conversational organization idea the user explicitly approves |

There is no administrator/UAC permission requirement in the source launcher.
The one-time device-consent acknowledgement is an application preference, not
a claim that Windows grants the process access to every protected resource.
Protected processes and inaccessible folders are skipped or reported. File
roots remain separately approved because a single telemetry consent must not
authorize reading document metadata or excerpts.

## Stored locations

Live system data is under `%LOCALAPPDATA%\Kshetrajna`:

- `config.json`: validated preferences.
- `events.db`: observations, activity, decisions, workspaces, and recovery
  journal. Existing schemas migrate the process creation-token column.
- `kshetrajna.log` and rotated logs: operational messages without excerpts or
  full file paths.

File Intelligence uses `files.db` in the same directory:

- `roots`: approved absolute roots, labels, scan status and aggregate sizes.
- `files`: relative names, extensions, sizes, timestamps, and at most 4 KiB
  excerpts from listed text types. Sensitive names, extensions, and folders
  are cataloged without excerpts.
- `file_actions`: reviewed rename/move proposals, identity snapshots, outcomes
  and undo status.
- `sort_plans` / `sort_suggestions`: deterministic reasons, proposed relative
  destinations, selections, plan status, per-file action references, content
  summaries, purpose/action decisions, confidence, evidence, and cautious
  review reasons derived from path, related-file context, names, and bounded
  excerpts. A decision may identify study material, project evidence, source,
  data, media, logs, or generated-looking files; it is not a delete command and
  does not claim certainty.
- A review preview can temporarily read a bounded text excerpt. DOCX/PPTX/XLSX
  previews read only selected XML text parts and never execute macros. Media and
  other binaries are not opened or launched by the dashboard; decisions use
  metadata, path, filename, and related-file evidence.
- Approved raster images (PNG, JPEG, GIF, WebP, BMP) may be loaded into the
  dashboard preview after bounded signature/dimension and identity checks. The
  dashboard displays the pixels locally; it does not upload them or claim
  semantic image recognition.
- Approved PDFs may be loaded into a sandboxed same-origin document viewer and
  have a bounded text preview extracted locally. PDF bytes are size-bounded,
  signature-checked, and never uploaded.
- Reviewed File Intelligence cleanup moves a confirmed empty/duplicate file into
  the protected `.kshetrajna-quarantine` directory and removes it from the
  active catalog. The action journal stores the exact recovery record; the
  quarantine list can restore the file after identity/content checks. The
  separate generic file-action DELETE capability remains explicitly confirmed
  and permanent.

Deleting File Intelligence data never deletes original files. It is refused
while file-action records still need recovery; resolve or dismiss those
records first. System history deletion restores active priority trials first
and retains unresolved restoration records.

SQLite is not application-encrypted. Windows profile permissions and the
loopback boundary are the protections currently provided. No claim of full
database encryption is made.

## Local assistant

With separate local-AI consent, messages and a fresh collected telemetry
summary go to the installed Ollama server at `127.0.0.1:11434`. File contents,
excerpts, paths and index listings are excluded. Cloud-labelled/remote models
are rejected. New prompts omit a PC telemetry summary when collection is paused;
earlier chat messages remain in the conversation until cleared.
Chat histories/jobs stay in bounded application memory and are cleared by new
chat, system-history deletion, expiry, or application shutdown. Ollama has its
own runtime/log policy; clearing Kshetrajna does not claim to erase its memory.
Approved process-control outcomes are stored in the existing event journal,
including executable basename, PID and creation token. Termination is never
inferred from an idle classification or a model reply.
