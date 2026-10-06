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
| Sort AI folder creation | Off per plan | Category folders are created only when the user explicitly enables the plan option |

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
  destinations, selections, plan status, and per-file action references.

Deleting File Intelligence data never deletes original files. It is refused
while file-action records still need recovery; resolve or dismiss those
records first. System history deletion restores active priority trials first
and retains unresolved restoration records.

SQLite is not application-encrypted. Windows profile permissions and the
loopback boundary are the protections currently provided. No claim of full
database encryption is made.
