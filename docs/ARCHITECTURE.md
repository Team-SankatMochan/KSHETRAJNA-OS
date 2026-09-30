# Architecture and decisions

## Data flow

~~~mermaid
flowchart LR
  W[Win32 read APIs] --> C[Consent-gated collector]
  D[Synthetic demo probe] --> C
  C --> S[(Local SQLite)]
  S --> I[Explainable context and patterns]
  I --> R[Recommendations]
  R --> U[User review]
  U --> J[Durable recovery journal]
  J --> A[Priority driver]
  A --> E[Observed before/after metrics]
  E --> F[Feedback and undo]
  F --> S
  I --> B[Saved dashboard workspace board]
~~~

## Technology decisions

| Choice | Reason | Tradeoff |
|---|---|---|
| CPython 3.13 | Available on Windows x64 and ARM64; readable, fast to iterate, standard SQLite and FFI | Native ARM64 installer is experimental; Python runtime must be installed |
| Win32 via ctypes | Direct read APIs and a narrow priority actuator without native wheel dependencies | ABI declarations require care; protected processes may be inaccessible |
| SQLite and explicit transactions | Durable local history, journal-before-write, simple schema upgrade | Per-user plaintext database; no encrypted-at-rest guarantee |
| Standard-library HTTP server | Zero dependencies and a single local process | Loopback prototype only; not a production Internet server |
| Plain HTML/CSS/JS/SVG | Offline assets, no npm install, accessible semantic controls | DOM updates need care to preserve input and focus |
| Rule/context + counts | Each result has inspectable evidence; no cloud or model download | App-name categories miss unknown or ambiguous workflows |
| Separate native/simulated drivers | The demo can exercise lifecycle logic without touching Windows | A passing demo does not validate hardware-specific mutation behavior |
| OS file lock | Prevents two CLI instances sharing one live journal | Locking scope is the selected local data directory |

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

## Privacy and trust boundaries

- Live and synthetic demo storage are separate. Demo storage is temporary; demo never instantiates the native action driver.
- The service binds only 127.0.0.1. Exact Host checks reject ordinary DNS rebinding. Mutations require matching Origin and a random page-session token.
- API bodies are bounded to 4096 bytes; transfer encoding is rejected. The UI uses textContent for app names and user workspace labels.
- SQLite schema migration adds process identity tokens to existing Phase 1 databases. Old rows have token zero and cannot create actionable native proposals.
- Retention runs during both enabled and paused collection. Pending recovery records are exempt so deletion cannot discard the path to restoration.
- A local program running as the same user can access the user's files/service. This is not an adversarial multi-user security boundary.

## Outcome interpretation

Up to six preceding samples form the baseline. At least three post-approval samples are needed to display a CPU or memory delta. The journal calls this an observed change; unrelated work can account for it. Synthetic values do not respond to approval, so the demo cannot fabricate a performance improvement.

## Platform references

- [Python 3.13 Windows and ARM64 distributions](https://docs.python.org/3.13/using/windows.html)
- [Windows on Arm overview](https://learn.microsoft.com/en-us/windows/arm/overview)
- [GetSystemTimes and processor-group limits](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-getsystemtimes)
- [GetProcessMemoryInfo](https://learn.microsoft.com/en-us/windows/win32/api/psapi/nf-psapi-getprocessmemoryinfo)
- [GetPriorityClass](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-getpriorityclass)
- [SetPriorityClass](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-setpriorityclass)
