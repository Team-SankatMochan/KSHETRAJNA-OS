# Local action API and consent boundary

This describes the **0.5.0 implementation at `25a0de7`**, not a final-release
qualification. `actions.db` and the routes below exist. The rollout is narrow:
`GET /api/v1/capabilities` reports `canonical_authority_complete: false` and
`legacy_routes: "domain_authoritative_compatibility"`. Existing UI, chat and
file/CLI routes still use domain services and journals; they are not silently
converted into canonical actions. See [architecture](ARCHITECTURE.md).

## Wired versus deferred

| Canonical kind | Proposal selector | Current permission / scope |
|---|---|---|
| `system.memory_trim` | `pid` | Maintenance opt-in plus collection/device/process/foreground consent; one eligible background process |
| `system.process_close` | `pid` | App-control opt-in plus collection/device/process/foreground consent; one eligible allowlisted background process |
| `system.process_terminate` | `pid` | Same as close, with exact executable-name confirmation at execution; no undo |
| `system.external_tool` | `tool` | Maintenance opt-in; only `disk_cleanup`, `storage_settings`, `open_temp`, `store_cache` |

Registered but **deferred in v1**: `file.move`, `file.rename`, `file.quarantine`,
`file.restore`, `file.delete_permanent`, `system.priority_trial`,
`system.temp_cleanup`, and `system.model_unload`. Their vocabulary/visibility
metadata is not an executor. Proposal attempts return `capability_deferred`
(409). Some have existing unversioned domain workflows; deferred does not mean
that quarantine restore, priority undo or other existing recovery disappeared.
Canonical temporary-file cleanup awaits separate danger review. No arbitrary
executable, path, shell command, remote provider or model download is accepted.

## Local transport

The service binds `127.0.0.1`. Every v1 request needs an accepted local Host and
the current page-session `X-Kshetrajna-Token`. POSTs also require an allowed
`Origin` (`http://127.0.0.1:PORT` or `http://localhost:PORT`); GET may omit Origin,
but a supplied Origin must match. There is no remote-account, cookie-login or
API-key provider flow. The session token is a credential: never put it in a URL,
document or shared network capture. Same-user software is not sandboxed by this
boundary.

POST bodies must be JSON objects, with one positive `Content-Length` of at most
4,096 bytes and `Content-Type: application/json`; transfer encoding, duplicate
keys, non-finite JSON constants and unexpected operation fields are rejected.
Public errors are bounded codes such as `confirmation_required`, `expired`,
`revoked`, `plan_unavailable`, or `operation_unavailable`, not raw tracebacks.
This v1 boundary is not a blanket redaction claim for legacy APIs or local logs.

## Proposal → informed consent → execute

| Method / route | Body or result |
|---|---|
| `GET /api/v1/capabilities` | Wired/deferred capability metadata and rollout limitations |
| `POST /api/v1/actions` | `{"kind":"system.memory_trim","pid":1234}` or another wired selector; 201 with `action` |
| `GET /api/v1/actions/ACTION_ID` | `action`, including the persisted display-only `review` |
| `POST /api/v1/actions/ACTION_ID/consent` | `{"confirm":true,"evidence_digest":"RETURNED_DIGEST"}` |
| `POST /api/v1/actions/ACTION_ID/execute` | `{"confirm":true,"evidence_digest":"RETURNED_DIGEST","idempotency_key":"NEW_UNIQUE_KEY"}`; termination also needs `typed_name` |
| `GET /api/v1/actions?limit=50` | Redacted history page with `items` and `next_cursor` |
| `GET /api/v1/actions/ACTION_ID/events?limit=50` | Transition metadata, not raw private evidence/results |
| `POST /api/v1/actions/erase-history` | `{"confirm":true,"ids":["ACTION_ID"]}`; selected canonical-history redaction only |

Read the returned review before consenting. `review.available: true` exposes
allowlisted title/effect/undo copy, process basename/PID/creation token or a fixed
tool, bounded proposal observations, freshness/expiry and caveats. Force
termination returns `typed_confirmation.field: "typed_name"` and `exact_value`;
send that **exact** value, not a guessed name. Review is derived from persisted
proposal evidence, not current telemetry or a changed live plan. It never
returns arbitrary private paths, raw plans/results or permission generations.
History list/event responses are narrower than proposal/detail responses.

Erased, legacy, deferred or malformed review evidence yields
`review: {"available": false}`. Do not manufacture a review or approve from the
remaining metadata. Review is not executable reconstruction: consent/execute
also require the original unexpired in-memory domain plan and matching evidence.

Consent and execution each require literal JSON `true`, not `1`, a string or a missing value.
Proposals expire after 60 seconds. Approval binds the persisted evidence digest,
mode and service-managed permission generations; execution claims one unique
idempotency key before calling the domain executor. Used/expired/revoked or
mismatched approvals do not authorize a second execution. An idempotency key is
a replay guard, not a promise that retrying returns a cached success.

`config.json` keeps service-managed `_consent_versions`; clients cannot update
that private field through settings. Changing a relevant permission invalidates
pending canonical proposals/approvals, including disable/re-enable cycles.
Existing maintenance/process and permanent-delete domain plans also bind their
relevant permission generations. Fresh domain identity, foreground/session and
eligibility checks remain authoritative at execution; a ledger claim alone is
not proof that OS preflight passed.

## Outcomes, restart and recovery

Intent/consent/events are durable in `actions.db`; system outcomes/recovery stay
in `events.db`, file outcomes/recovery in `files.db`. These are **not one
cross-database atomic transaction**. The current adapter records `succeeded`
only for demo simulation, a completed memory trim or a confirmed termination.
Close requests, external-tool launches and unconfirmed/failing/interrupted
domain outcomes remain `unknown`; success is not inferred from a launch.
Detailed domain outcomes are not exposed as raw JSON in v1.

Startup revokes unused canonical approvals and fences interrupted claims as
`unknown` or, for a recoverable policy, `recovery_required`; it never rebuilds a
plan or replays an irreversible action. Domain journals decide actual recovery.
Recovery discovery is not limited by display-history pagination. Basic and
Expert both expose necessary recovery; disabling permission for new work does
not itself disable undo/restore safety checks. The four currently wired kinds
have no exact undo. Delayed measurement scheduling remains pending; a recorded
working-set change is not commit released, lasting savings or a proven speedup.

## Deletion has separate scopes

`POST /api/delete` with `confirm:true` restores active priority trials where
possible, clears telemetry/derived history and chat, revokes pending canonical
approvals, and returns `scope: "telemetry_history"` plus
`canonical_history: "retained_use_explicit_erasure"`. It does **not** erase
`actions.db` history or the file catalog.

Canonical `erase-history` takes 1–100 distinct IDs. It redacts eligible resolved
payloads but retains action rows, approval metadata, replay hashes and event
tombstones. Pending/in-flight/unknown/recovery-required work and conservative
recovery evidence are preserved with reasons. SQLite secure-delete and a
best-effort WAL truncation are not secure-media or backup erasure;
`storage_cleanup: "pending"` reports a pinned WAL. This API never clears domain
recovery journals. See [privacy](PRIVACY.md) and [crash recovery](CRASH_REPORTING.md).

The [final contract](FINAL_RELEASE.md) still requires remaining adapters,
privacy migration, delayed outcomes and native/artifact/browser/user evidence.
Presence of this API does not complete phases 2–11 or qualify 1.0.0.
