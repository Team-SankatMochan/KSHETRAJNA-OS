# Workflow patterns and predictions

The model learns from retained foreground executable names, timestamps and idle duration. It reads no window titles, documents, URLs, keys or cloud services. Everything is derived in memory; deleting the source history also removes learned patterns.

## Sessions and recurring workflows

- Analyze up to 12,000 recent snapshots, within the configured retention period.
- Collapse consecutive samples of the same app, case-insensitively, into visits. More frequent sampling does not create more switches.
- Require four measured seconds of foreground dwell. Brief visits break the sequence; omitting a popup never fabricates a direct transition across it.
- Idle/unknown foreground samples and gaps over 90 seconds break sessions. No transitions span those boundaries.
- Split activity into nonoverlapping five-minute windows anchored to each session. Mine recurring pairs and triples rather than requiring an identical set in every window. An occasional extra app therefore does not hide an established pair.
- Require two supporting windows; report the number of windows and sessions. A repeated window is not presented as a separate session. Limit each window to its eight most-used apps and display four groups.
- Show ordered three-app routines after three occurrences. These may overlap, so they are evidence counts rather than independent trials.

## Next-app prediction

Learn first-order transitions (current app to next) and second-order transitions (previous app plus current app to next). Prefer the latter after three matching observations; otherwise use the current-app model. Retain up to 4,000 completed transitions.

Recent evidence decays exponentially with a four-hour half-life. A winning app needs at least three supporting transitions and at least 2.5 total decayed evidence units. Abstain when the leading share is below 55%, or its lead over the runner-up is below 15 percentage points. Also abstain for an unestablished current visit, idle state, disabled foreground collection, paused collection or a sample older than 90 seconds.

The displayed 0–100 evidence score is `100 * winner_weight / (total_weight + 2)`, capped at 99. The extra denominator shrinks scores when evidence is sparse. This is a heuristic score, **not a calibrated probability**. Show the model basis, raw support, and up to two alternatives. Raw counts and decayed scores measure different things and need not have the same proportions.

## Measuring behavior

Chronologically replay completed transitions, predicting before learning each answer. Report results for the last 200 transitions: correct/offered predictions, accuracy among offered predictions, and coverage among all evaluated switches. Show a current-app-only baseline alongside the sequence model. Initial learning and ambiguous cases lower coverage rather than being counted as correct guesses.

The replay is a diagnostic on retained history, not a controlled real-world benchmark or a claim of calibrated confidence. Synthetic demo history is deliberately repetitive. Real workflows may be ambiguous, new habits take time to establish, app names cannot distinguish browser tasks, and sampling misses activity between observations. This model predicts the next stable app switch, not when that switch will occur. It never launches or modifies an application.

## Validation

Tests cover sequence disambiguation, sampling-rate consistency, case normalization, idle and missing-data boundaries, focus flashes, noisy co-occurrence, tied predictions, cold starts, habit drift, stale history, chronological replay and privacy/deletion behavior. A local synthetic 12,000-sample analysis took about 31 ms on the development machine; this is a single development measurement, not an ARM or production performance guarantee.
