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

### D01 — autonomous-agent acceptance (partially complete)

Existing focused tests already cover tool dispatch, failed-check correction, checkpoint recovery, persistence, project isolation, UTF-8 events, process timeout reporting, and explicit recovery availability. The following are targeted remaining acceptance cases, not unproven defects:

- read-only analysis that completes without modifying code;
- behaviour with a repository already modified before a run starts;
- rejection of unsupported or insufficient success claims;
- unproductive-loop detection and controlled terminal behaviour;
- cancellation of a process tree including child processes;
- browser representation of actual engine events, cancellation, recovery, and terminal status.

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
