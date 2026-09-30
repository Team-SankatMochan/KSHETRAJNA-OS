# Kshetrajna — submission guide

## Start here

Double-click **run_demo.cmd**. Keep the terminal open. The dashboard opens at http://127.0.0.1:8766. Use **run.cmd** for consent-based live Windows observations instead.

The demo is deliberately labeled synthetic. It contains fifteen minutes of recurring app activity so judges can see learned counts immediately. A scenario change resets only the temporary demo session. No demo action modifies Windows.

## Project description

Kshetrajna is a privacy-first adaptive workload assistant for Windows. It observes local application activity and resource pressure, builds an explainable picture of the current workflow, and proposes changes the user can inspect and approve. Every supported adaptation has a visible journal and a route back. The prototype connects telemetry, local behavioral statistics, recommendations, a workspace board, consent, controlled CPU-priority trials, outcome measurement, and rollback in one offline application. It is designed around Windows on ARM compatibility using native Win32 APIs and Python without third-party runtime dependencies.

## A 90-second demonstration

1. **0:00–0:15 — Introduce the problem.** “Windows sees processes and CPU load. Kshetrajna adds a local view of how I use those apps, so resource decisions can reflect my workflow.”
2. **0:15–0:30 — Show the evidence.** Point to Development context, app importance, recurring groups, and the next-app prediction. Explain that context labels are rules and the personal patterns are observed counts. The visible numbers are synthetic for this demo.
3. **0:30–0:45 — Review a proposal.** Select Review simulated trial. Read the target, reason, tradeoff, and duration. Approve it. The decision journal now shows a simulated active trial.
4. **0:45–1:00 — Adapt the workspace board.** Name a workspace, choose apps, save it, and activate it. The app board changes. This is dashboard adaptation; it does not move desktop windows.
5. **1:00–1:15 — Close the loop.** Select Undo now, or let the 60-second trial finish. Show the journal and give Helpful or Not helpful feedback. With at least three post-approval samples, the journal shows observed metric deltas without claiming causation.
6. **1:15–1:30 — Show a new context.** Select Meeting or Creative from Demo scenario. The context and app priorities update. Finish with the privacy controls and Download session report.

## What makes the prototype useful

The contribution is the complete interaction: evidence → recommendation → explicit approval → bounded trial → observation → rollback → feedback. Each individual mechanism already has precedents; the submission demonstrates how they can work together as a local, user-controlled Windows layer.

## Judge questions

**Is this an operating system?** It is an application layer above Windows. The OS remains responsible for scheduling and memory management.

**Where is the AI?** The first context engine is explainable weighted rules. Personalization comes from recency/frequency scores, recurring app groups, transition counts, and persisted negative feedback. It does not contain an LLM or trained deep model.

**Does it improve performance?** No causal benchmark is claimed. The prototype records observations around a trial. Controlled A/B experiments and a latency metric are needed before making a performance claim.

**What can it change?** With live opt-in and approval, it can temporarily lower one eligible background process from Normal to Below normal priority. The default mode observes only. Workspace boards change the dashboard UI.

**How does rollback work?** The original state is journaled before mutation, then restored on undo/expiry, pause, or graceful shutdown. Startup retries pending records after a crash. It never overwrites a priority class changed by another actor. Descendants are outside the selected-process rollback scope.

**What about Snapdragon X?** The code uses standard Win32 APIs and has no third-party native wheels. A native ARM64 Python runtime is available, but ARM64 hardware qualification has not been performed here.

## Submission contents

- Source and launch scripts in this repository.
- README.md: quickstart, implemented phases, and limitations.
- docs/ARCHITECTURE.md: technology choices and contracts.
- PHASE1.md: the earlier observe-only foundation design, retained as historical context.
- tests/: executable regression tests.
- .github/workflows/tests.yml: CI configuration for Windows and Linux unit/demo tests.

Submit the repository using the hackathon's required channel. No deployment, GitHub push, or external submission has been performed by this build.
