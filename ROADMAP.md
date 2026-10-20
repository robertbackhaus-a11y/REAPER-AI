# REAPER-AI — Development Roadmap

**Current release:** v0.6.0
**Next planned release:** targeted acceptance and evidence-backed fixes
**Scope:** local, single-user autonomous coding agent

## 1. Project goal and architecture principles

REAPER-AI turns an engineering request into observable local work: Sentinel inspects relevant repository facts, makes focused changes, runs checks, and reports the evidence actually produced. A model assertion is never proof by itself.

- Keep one local stack: FastAPI, LangGraph, Ollama, SQLite, HTML/CSS/vanilla JavaScript, and SSE.
- Prefer existing components over a second agent, state store, service, frontend, or persistence layer.
- Keep Git optional and never commit, push, tag, release, or elevate permissions automatically.
- Preserve explicit checkpoint recovery, file-hash conflict detection, process timeouts, and observable tool results.
- Document only implemented behaviour and observed acceptance evidence.

## 2. Current state — v0.6.0

| Area | State |
| --- | --- |
| M0 — Foundation | Implemented |
| M1 — Engine | Implemented |
| M2 — Reliability | Implemented with documented limitations |
| M3 — FastAPI | Accepted |
| M4 — Web UI | v0.6.0 GUI modernization implemented |
| M5 — Persistence | Implemented |
| M6 — Autonomous Agent | First autonomous end-to-end coding acceptance passed |
| Variante B — Multi-project | Steps 1–5 implemented |

v0.6.0 includes the v0.5.0 project-scoped repository index, hybrid checkpointed working memory, durable project memory, explicit checkpoint recovery, index-guided Sentinel navigation, P0 hash-bound `apply_patch`, and P1 working-memory consolidation, plus the completed GUI modernization.

## 3. Completed milestones M0–M6

| Milestone | Completed scope |
| --- | --- |
| M0 — Foundation | Python package, local workspace conventions, development baseline |
| M1 — Engine | LangGraph-based execution and Ollama-backed structured decisions |
| M2 — Reliability | File/Git/process adapters, explicit errors, bounded execution, source and write conflict evidence |
| M3 — FastAPI | Local API, run control, SSE events, supervisor integration, persisted run access |
| M4 — Web UI | Functional browser UI with projects, conversations, run status, events, recovery, and diagnostics |
| M5 — Persistence | SQLite conversations, messages, runs, events, stage results, checkpoints/recovery, and scoped durable memory |
| M6 — Autonomous Agent | Iterative `Prepare → Decide → Execute → Observe → Finalize` graph, dispatcher, decision traces, evidence-based completion, and recovery |

### Variante B — multi-project implementation

| Step | Completed scope |
| --- | --- |
| 1 — Project core | Stable project IDs, project workspaces, per-project model settings, project-scoped chats/runs/memory |
| 2 — Repository index | Incremental project-scoped file metadata, Python AST symbols/imports, Git branch/HEAD metadata, index queries |
| 3 — Hybrid run working memory | Checkpointed current task/phase, confirmed facts, proposed decisions, information gaps, source and verification evidence |
| 4 — Sentinel integration | Repository map and index tools guide targeted source access without loading the whole repository |
| 5 — Multi-project UI | Project management, project-scoped conversations/memory, index status/search, and existing live-run UI integration |

Historic BUG-003 through BUG-019 work is consolidated in these completed capabilities: unrestricted project inspection, source-capture metadata, context-visible coverage, structured decision traces, robust UTF-8 event handling, source-aware working memory, targeted edits, and evidence-based final status. Detailed incident logs remain in Git history and persisted run records rather than being repeated here.

## 4. Demonstrated Sentinel capabilities and remaining acceptance

### Successful autonomous coding acceptance

Run `f852b6ce174e` is the current end-to-end acceptance record:

- resumed the same valid checkpoint and reached `completed` with exit code `0`;
- used hash-protected `apply_patch` to change `src/reaper_ai/persistence.py` and `src/reaper_ai/api.py`;
- detected and corrected its own failed verification script;
- used the REAPER Venv FastAPI `TestClient` for functional verification;
- received HTTP 200 with real project repository-index data from `GET /projects/{project_id}/index/top-python-files`.

This demonstrates one complete autonomous coding workflow. It is not a claim that every coding task, repository, model response, or external command is reliably handled.

### D01 — autonomous-agent acceptance (accepted 2026-10-20)

Existing focused tests cover tool dispatch, failed-check correction, checkpoint recovery, persistence, project isolation, UTF-8 events, process timeout reporting, and explicit recovery availability. The following acceptance evidence is intentionally kept factual:

| Criterion | Result | Evidence |
| --- | --- | --- |
| Autonomous coding | PASS | Historical run `f852b6ce174e`: two guarded source changes, recovery, failed-check correction, and FastAPI `TestClient` verification against real index data. |
| Read-only analysis | PASS (grade 4, accepted) | Run `d01-1792496155` completed without writes after 17 tool actions, did not repeat a successful range, and produced a line-cited report. External review classified the report's technical quality as sufficient, while retaining its known limitations below. |
| Error correction | PASS | `f852b6ce174e`; the focused `test_agent_keeps_evidence_through_failed_check_correction_and_resume` also preserves the failure/correction/check evidence path. |
| Unsupported success claim | PASS | The focused agent-dispatcher test retains a failed check and reaches `completed` only after a later successful verification; model prose alone is excluded from persisted factual context. |
| Existing changes protected | PASS | Run `33ea6c56dd7a` in a separate Git repository created only `agent_output.txt`; the pre-existing uncommitted `protected-notes.txt` remained byte-identical and `README.md` was unchanged. |
| Unproductive loop | PASS (fresh targeted run) | `d01-1792496155` completed after progressive, non-identical source ranges. It recovered from two out-of-range reads by changing files/ranges; it did not repeat a successful range. Historic failed runs remain retained as regression evidence. |
| Cancellation/process tree | PASS (bounded evidence) | Existing M2 R04 synthetic timeout evidence verified parent/child-process termination; the API cancellation of `218b6ffa761b` reached `cancelled` and emitted a terminal event. |
| GUI events/status | PASS | The browser rendered the persisted live timeline for `218b6ffa761b`, including tool events, decision traces, terminal cancellation status, and its explicit recovery control. |

D01 is **accepted**. The assessment combines technical execution (grade 2), factual-result quality (grade 4), and an overall grade of 3. Run `d01-1792496155` reached `completed` / exit code 0 in 128.9 seconds with 18 model calls, 17 tool calls, no file changes, a valid checkpoint, strategy changes after two invalid range requests, and no repeated successful range read. The report was externally reviewed: bounded check/error history is a deliberate context trade-off; an open verification after a failed check is correct; the claimed missing Git timeouts was false; active-run process liveness has a small residual risk mitigated by `_watch()`; and replay has a possible small terminal-stream inconsistency. These are retained as limitations rather than blockers for this acceptance.

### REAPER-AI — development, test, and acceptance history

This history records evidence that remains available in project documents, source, Git history, focused tests, or persisted run data. Historic runs whose logs are no longer retained are identified by their recorded symptoms rather than reconstructed as new evidence.

- Earlier D01 runs showed repeated reads of `engine.py:220–450` and later `engine.py:320–549`. The retained diagnosis attributed this to successful range fragments that produced no source fact when they could not be parsed independently, and to prompt selection that could demote a current truncated range.
- The corrective path retains every successful read as a hash- and range-bound observed source fact, even for a fragment inside a declaration; independently parseable excerpts add declarations, signatures, and imports. An identical later read is recorded as no new confirmed knowledge. This state is checkpointed.
- Range selection now scores individual excerpts and does not treat a truncated, targeted range as a weak full-file capture. Persisted source coverage and `visible_source_coverage` remain separate, so the decision trace shows only what was actually sent to the model.
- A final decision may explicitly reconcile a satisfied read-only proof with `open_verifications: []`; it does not implicitly remove unrelated verification requirements. This fixed the former stale-proof closure path while preserving failed-check evidence.
- The fresh D01 integration run `d01-1792496155` demonstrated progressive source navigation, autonomous recovery from invalid requested line ranges, a regular terminal state, and no writes. Its report was reviewed as sufficient rather than perfect; the detailed assessment above records the one false timeout claim and the remaining API/replay observations.
- Focused dispatcher, persistence, checkpoint/recovery, project-isolation, UTF-8, supervisor timeout/cancellation, source-coverage, and agent-decision tests were run during the corresponding changes. Python syntax and `git diff --check` were also used as targeted technical checks. No claim is made for historical raw logs that have since been removed with application history.

### D02 — published baseline

v0.5.0 is the published autonomous-coding baseline. v0.6.0 adds the GUI modernization while preserving that evidence. Follow-up work must preserve it and add only targeted acceptance for an observed gap.

## 5. Technical decisions

### Agent, state, and source evidence

LangGraph remains the embedded iterative graph. The dispatcher deterministically executes model-selected tools; SQLite persists conversations, runs, events, checkpoints, index data, and durable memory. The run state keeps confirmed facts, model-proposed decisions, information gaps, sources, changes, and verification as distinct evidence kinds.

Historical source coverage records what was captured from a file. Visible source coverage records only excerpts that were actually sent in the current model prompt. The distinction prevents a context-truncated excerpt from being represented as a complete read. Source-linked facts are invalidated after a changed hash.

### Editing and verification

`write_file` remains available for new files or full replacement. `apply_patch(path, expected_text, replacement, expected_hash)` is the focused existing-file edit path: it requires a matching hash and exactly one original-text occurrence, and returns before/after hashes. Tool results, not model prose, provide change and verification evidence.

### Ollama structured-decision configuration

The active Sentinel decision request uses:

```python
"think": False,
"format": "json",
"stream": False,
"temperature": 0.0,
"top_p": 0.9,
"top_k": 40,
"num_ctx": 16384,
```

The Qwen A/B measurement for the same reconstructed decision situation observed 54.14 seconds with thinking enabled and 4.99 seconds with thinking disabled. With `think: true`, several production decisions produced long separate reasoning while `message.content` was empty; `think: false` returned valid JSON and allowed the acceptance run to finish. This setting applies to Sentinel's structured decision path only, not to reasoning use in unrelated LLM applications.

## 6. Release history

| Release | Status | Notes |
| --- | --- | --- |
| v0.4.0 | Historical pre-release | Earlier M5/M6 and Variante-B baseline; tag retained |
| v0.5.0 | Current published milestone | P0/P1, `think: false`, repository-index endpoint, copyright notices, and checkpoint-recovered autonomous coding acceptance |
| v0.6.0 | Current published milestone | GUI modernization: project tabs, settings entry point, compact chat/result presentation, activity dashboard, and bounded independent diagnostic scrolling |

Historical tags remain immutable from this roadmap forward. Repository publication status and an installed local application are separate states.

## 7. v0.6.0 — GUI Modernization

**Goal:** provide a compact, technically oriented Sentinel development interface without changing the backend stack.

The existing FastAPI, HTML, CSS, vanilla JavaScript, SSE, and SQLite stack remains in place. No React/Vue migration, npm frontend, second web server, or separate state layer is planned.

### G01 — project navigation and chat tabs — completed

- The left sidebar lists only existing projects and clearly marks the active project.
- Chats of the selected project appear as tabs in a top tab bar; `+` creates a new chat.
- Tabs can switch and close without deleting persisted conversations.
- Project changes show project-scoped tabs. Existing SQLite conversations remain the source of truth.
- Runs continue through tab or project changes.
- Existing project, conversation, run, recovery, index, and memory routes remain unchanged; tabs are a vanilla-JavaScript view over persisted conversations.
- The confirmed dark blue/cyan GUI presentation, transparent Wolf/Kanji logo, compact chat/code/diff styling, and visual integration of the existing activity panel are implemented without backend changes.

### G02 — live agent activity — completed

- The right panel becomes a compact dark developer dashboard.
- Show real Sentinel phase, explore/plan/implement/verify state, active/completed tools, durations, file changes, errors, correction attempts, verification evidence, and terminal status.
- Reuse existing SSE and persisted engine events; do not invent activity or progress percentages.
- With `think: false`, do not reserve permanent empty thinking output.

### G03 — settings entry point — completed

- A top-right settings control consolidates existing project creation/editing/removal, workspace, description, project prompt, model selection, existing model parameters, index status/refresh/search, and actual general settings.
- Reuse current API routes and SQLite records. Offer only settings that work.

### G04 — chat and results — completed

- Improve reading space and distinguish user messages, Sentinel answers, tool results, changes, diffs, errors, and observed verification.
- Retain copy controls, cancellation, and recovery.
- Keep diffs and technical details collapsible instead of permanently crowding the chat.

### G05 — visual usability — completed

- Keep dark mode, compact developer-dashboard styling, consistent spacing/type/status symbols, and useful responsive layout.
- Avoid design-system dependencies and decorative animation.
- Retain a sensible collapsible activity panel where the existing layout supports it.

### G06 — targeted GUI acceptance — completed

1. Select and switch projects.
2. Create and edit a project from settings.
3. Open, switch, and close multiple chat tabs without deleting conversations.
4. Keep an active run alive while changing tabs/projects.
5. Show actual SSE tool events, changes, verification, errors, cancel, and recovery state.
6. Reload the browser and restore persisted conversations.

### Definition of done

- The new GUI structure works with the existing backend.
- Project-scoped chat tabs and settings-based project management work.
- Live activity presents actual events, changes, and verification evidence.
- Cancellation and recovery remain usable.
- README, architecture, and roadmap describe the implemented state.
- The local installation is checked after implementation.

## 8. Open work after v0.6.0

Do not invent feature milestones in advance. Continue with the remaining D01 acceptance cases and concrete limitations observed during v0.6.0 use. Prioritize evidence-backed fixes over new platform layers.

## 9. Deliberately deferred functionality

- Multi-agent delegation
- Automatic multi-model routing
- Embeddings, vector databases, or RAG without a measured need
- Cloud services
- Automated releases or deployments
- Enterprise roles, tenants, and permission systems
