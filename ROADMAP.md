# REAPER-AI — Development Roadmap

**Status:** Implementation in progress — M0–M3 completed with documented limitations; M4 next  
**Architecture baseline:** [ARCHITECTURE.md](ARCHITECTURE.md) (approved)  
**Target:** Windows 11, local single-user autonomous coding agent  
**Technology decisions:** Python, embedded LangGraph, Ollama, FastAPI, SSE, custom HTML/CSS/vanilla JavaScript UI  
**Language:** English for repository artifacts; user interaction in the user's preferred language.

## 1. Goal and delivery policy

Build an agent that reliably **inspects, implements, verifies, corrects, and reports** software-engineering work. Observable repository state and executed checks take precedence over model claims. A chat interface exposes a bounded execution engine; it does not itself orchestrate coding work.

This is a new implementation. The former REAPER-AI runner, Main/Worker architecture, JSONL session design, prompts, and earlier A01–A06 backlog are **not** migration requirements. Reuse old code only if independently justified.

### Non-negotiable rules

- Local, **single-user** system. No enterprise services, accounts, multi-tenancy, containers, hosted dependencies, or second orchestration framework.
- LangGraph is a normal Python dependency; **do not replicate its orchestration infrastructure**.
- Ollama provides local model inference. The user chooses installed models.
- Keep application source, local application data, and target project repositories separate.
- **Never** automatically commit, push, tag, publish, deploy, or perform destructive repository actions.
- Protect existing dirty files and untracked files. A model-generated statement is never an execution result.
- Prefer small modules and focused changes. No test-count targets, unnecessary abstractions, or full regression after every package.
- Every work package closes with working code, a focused verification, and relevant documentation updated. No automatic Git operations.
- Implement in the sequence below. Avoid building later-phase infrastructure in advance.

## 2. Milestones at a glance

| Milestone | Outcome | Main deliverable |
|---|---|---|
| M0 — Foundation | Implemented working baseline; original individual acceptance items not all audited | Installable minimal project skeleton |
| M1 — Engine proof | Implemented; coding E2E evidence recorded | CLI-only inspect → edit → verify flow |
| M2 — Reliability | Qualified acceptance; verified coding function and synthetic process-tree timeout; limitations below | Evidence, repair, safety, process containment |
| M3 — Local application | **Accepted (A01–A03); qualifications below** | FastAPI, projects, models, structured events |
| M4 — User interface | Usable lightweight local application | Vanilla JS chat and run-progress UI |
| M5 — Persistence | Usable conversations and run history | Local store, result retrieval, safe recovery |
| M6 — Release candidate | Verified end-to-end REAPER-AI | Documentation, installation, real-world acceptance |

The initial deliverable is **one dependable agent**. Multi-agent delegation and long-term memory are **not prerequisites**.

## 3. Work packages

### M0 — Foundation

**F01 — Project skeleton and development environment**

- Create Python package layout, `pyproject.toml`, dependency locking, basic CLI entry point and configuration loader.
- Add LangGraph and the chosen Ollama client as explicit dependencies; never vendor LangGraph.
- Keep configuration for model, Ollama URL, project root, limits and data directory minimal.
- Keep `ARCHITECTURE.md` as design authority. No speculative endpoints or frontend framework.

**Done when:** clean environment installs, package imports, CLI starts, and a local Ollama health/model-list check works or fails clearly.

**F02 — Resolve engine-critical design choices**

- Validate **direct Ollama API vs. adapter** with actual model tool-call responses; choose one.
- Select graph execution isolation: local child process or in-process **with proven full-tree cancellation**. Prefer the simplest mechanism that satisfies Windows behavior.
- Define exact file-edit preconditions, workspace constraints, and evidence formats.
- Define minimal task-aware verification gates (`coding`, `analysis`, `investigation`, `other`).
- Record the decisions in `ARCHITECTURE.md` before locking implementation details.

**Done when:** a short decision record documents each choice and the failure modes it addresses; none are left to ad hoc model prompts.

### M1 — LangGraph execution prototype (CLI only)

**E01 — Typed execution state and graph**

- Implement a compact state: immutable objective/project root, task kind, findings, plan, phase, budgets, attempts, tool evidence, result.
- Nodes: prepare, inspect/plan, execute, verify, conditional repair, finalize.
- Route read-only tasks around editing; permit justified no-op and evidence-based early failure.
- The graph—not the model—owns progress counters, transitions and terminal status.

**Done when:** CLI demonstrations show deterministic routing for a simple coding request and a read-only analysis request, without a free-running generic tool loop.

**E02 — Focused repository and Git tools**

- Implement bounded `list_files`, `search_code`, `read_file`, `git_status`, `git_diff`.
- Bind every invocation to an explicit absolute project root; return structured data with source location and truncation flags.
- Capture a baseline including existing tracked changes and untracked paths.

**Done when:** the engine can inspect a repository without broad recursive dumps and can distinguish pre-existing changes from work performed during a run.

**E03 — Safe editing and command tools**

- Provide a focused `apply_edit` with file/version preconditions and an explicit error on conflicts.
- Provide bounded `run_command` (PowerShell/child process), working directory, timeout, exit code, capped output, cancellation support.
- Prevent implicit edits outside the authorized workspace; disallow automatic Git publication and destructive actions.

**Done when:** a CLI-run coding task creates a real, attributable change without overwriting pre-existing user work; failures are returned as data, not hidden in logs.

**E04 — First real coding task**

- Wire the selected Ollama model into graph planning and execution.
- Produce a small, scoped implementation followed by actual verification and a structured result.
- Use a disposable sample repository, not REAPER-AI itself, for initial destructive/edge-case trials.

**Gate M1:** a real local model completes a representative change; another request performs analysis with no edit. The output contains observable evidence, not merely a success summary. **Do not proceed to API/UI until this gate passes.**

### M2 — Execution reliability

**R01 — Progress control and context efficiency**

- Bound model calls, tool calls, wall time, inspection without progress, output volume and repair attempts.
- Keep concise source-linked findings in graph state, separate from disposable LLM history.
- Report token usage per call and per run; avoid repeatedly retransmitting long tool output.
- On insufficient progress, request a concrete next step or terminate with a truthful reason.

**Done when:** a deliberately nonproductive inspection task terminates within budget and preserves diagnostic evidence; no 40-step blind loop is required.

**R02 — Verification and result contract**

- Implement `RunResult`: `completed | partial | failed | cancelled | timed_out`, changed files, diff summary, executed checks, evidence references, unresolved issues and usage.
- Verification decisions use observed file/Git state and actual check exit codes; model assessment is explicitly distinct.
- Support justified no-change outcomes and failures without pretending that syntax checks prove functional correctness.

**Done when:** a model's false `success` claim cannot mark a no-op coding request completed; a genuine successful read-only task can complete.

**R03 — Bounded repair flow**

- Feed concrete check failures into a limited execute → verify retry.
- Preserve the original objective and avoid unrelated refactoring.
- Return `partial`/`failed` when budget expires or remaining risks are unresolved.

**Done when:** one test failure can trigger a focused correction, and repeated failure terminates predictably.

**R04 — Process control and interruption**

- Make cancellation terminate the execution process and its spawned commands on Windows.
- Handle hung tools, unavailable Ollama, invalid tool calls, context exhaustion and unexpected child exits.
- Preserve evidence from interrupted runs; never replay potentially non-idempotent commands automatically.

**Gate M2:** real and synthetic targeted trials cover successful edit, false success, failure/repair, read-only run, pre-existing dirty changes, timeout and cancellation.

<!-- M2 IMPLEMENTATION STATUS (2026-10-18) -->
#### Implementation checkpoint — 2026-10-18

- **M0 working baseline:** Python virtual environment/package, embedded LangGraph import and local Ollama connection established. Do not imply every original F01/F02 completion criterion was separately checked.
- **M1 working baseline:** CLI engine can inspect/select, propose/apply changes in a disposable Git repository and report syntax/Git evidence. The first two-file exercise updated `calculator.py` and created `service.py`.
- **M2 implemented:** R01 call/time budgets and usage, R02 structured outcomes/check evidence, R03 bounded repair logic, R04 Windows Job Object supervisor with timeout backstop. This is an implementation inventory, not proof that every branch works.
- **Verified:** `multiply(6, 7) == 42` and `multiply_numbers(6, 7) == 42` with live Python imports (`FUNCTIONAL: OK`). R04 synthetic three-second timeout terminated parent and child (`R04: PASS`, exit 2). Dirty working-tree protection yielded an explicit failure instead of overwriting modified files.
- **Installed, not verified:** Run provenance (`run_id`, Git HEAD baseline, pre/post Git status) was patched into the engine; no subsequent run has demonstrated those fields.
- **Not fully covered:** False-success rejection, all repair/failure variants, real user cancellation, interruption diagnostics, resumed runs and complete original M2 gate scenario matrix. They remain acceptance concerns for M3/M6 where relevant. **Do not rerun the successful coding exercise just to increase test counts.**
- **Next:** M3 A01 — a minimal local FastAPI layer around the already implemented engine/supervisor. Add project and model discovery/events in A02/A03; no frontend framework, auto-commit or new orchestration stack.

### M3 — Local API and project binding

**A01 — FastAPI application and run management**

- Serve one local API and the future static UI; start, inspect, cancel and retrieve runs.
- Keep graph execution isolated from HTTP request lifetime and provide reliable cancellation.
- Use bounded concurrency suitable for one local user, initially one modifying run at a time.

**Done when:** an API request starts a real engine run and receives a stable run identifier and structured terminal result.

**A02 — Ollama models and projects**

- List **installed Ollama models dynamically**; no hardcoded model names.
- Select and validate a project/repository root before running; expose the active workspace clearly.
- Keep user choices and run settings explicit, with safe defaults and local configuration.

**Done when:** a newly downloaded Ollama model appears without changing REAPER-AI code, and the selected project root controls every tool.

**A03 — Structured run events and SSE**

- Emit `run_started`, `phase_changed`, `model_activity`, `tool_started`, `tool_completed`, `files_changed`, `verification_completed`, `run_completed`.
- Include run identifiers and bounded evidence references; do not require parsing stderr to determine status.
- Preserve terminal delivery even if verbose logs are dropped or the browser reconnects.

**Gate M3:** the API can start/cancel work and report actual progress, results and errors independently of a browser.

<!-- M3 ACCEPTANCE CHECKPOINT (2026-10-18) -->
#### M3 implementation and acceptance checkpoint — 2026-10-18

- **A01 — Accepted for current scope:** FastAPI exposes health, run start/status/log and cancellation. A real local Ollama coding request completed through the API (`exit_code: 0`) and generated a genuine Git diff. The API cancellation endpoint returned `cancelling` followed by `cancelled`; Git status was unchanged.
- **A02 — Accepted for current scope:** Installed models discovered dynamically (`qwen3.6:35b`, `qwen3.8-q5:latest`, `qwen3:8b`); workspace validation passed against a temporary Git repository; run-specific model choice reached the engine.
- **A03 — Accepted for current scope:** SSE returned 13 structured events ending in `run_completed`, with phase/model/file/verification information. Final evidence was syntax/Git **structural** verification, not a functional runtime test.
- **Resolve before M4:** Public API `run_id` and engine provenance `run_id` are different values. Normalize the externally exposed identifier/event payload without losing internal correlation.
- **Deferred/limitations:** Run registry and logs are in memory (M5 persistence pending); exceptional process/cancel paths not comprehensively covered; API cancellation verified status transition but not descendant termination in that same trial (full process-tree timeout proven separately at M2). Do not upgrade structural evidence into a functional-correctness claim.
- **Next:** M4/U01 — serve a small HTML/CSS/vanilla JS interface with project/model choice, task submission and current run status. U02 adds event/diff/error visibility. No framework, second service, or automatic Git publication.

### M4 — Lightweight interface

**U01 — Custom chat and project/model controls**

- Plain HTML, CSS, vanilla JavaScript served by FastAPI; no Chainlit, React, npm or separate frontend server.
- Show sessions, user requests, active project/workspace, installed model selection, and current run status.

**Done when:** a local user can submit a coding task without the CLI and can see which repository will be modified.

**U02 — Execution transparency**

- Render current phase, useful model/tool activity, verification results, changed files, readable diffs, errors and final status.
- Support cancellation and clear failed/partial outcomes; never label a model's unverified claim as success.
- Keep interface responsive when output is large and when SSE reconnects.

**Gate M4:** the browser can drive the M2 acceptance scenarios and display consistent engine-produced outcomes.

### M5 — Local persistence and context

**P01 — Conversation and run records**

- Choose the smallest suitable local storage mechanism only after real API/UI requirements are known.
- Save conversations separately from structured run results and bounded diagnostic events.
- Feed subsequent turns only relevant user decisions and verified prior results—not raw model success claims.

**Done when:** restarting the app preserves completed conversations/results without misleading follow-up context.

**P02 — Optional checkpoints and safe recovery**

- Decide whether real pause/resume is necessary; if yes, use LangGraph-compatible checkpointing rather than a second custom system.
- Interrupted runs must remain visibly interrupted and cannot silently re-execute modifying commands.

**Done when:** restart/recovery semantics are documented and tested. If resume is not needed, explicitly omit it from the first release.

**P03 — Optional durable memory (decision gate)**

- Evaluate whether Basic Memory gives a measurable benefit beyond conversation/run records.
- If adopted, retrieve only on demand and write only verified durable decisions or lessons.
- Do not put ephemeral graph state or unverified completion claims in long-term memory.

**Done when:** there is a documented keep/defer decision. Basic Memory integration is not required to ship the initial agent.

### M6 — Final acceptance and first release

**D01 — Representative end-to-end acceptance**

Exercise real local tasks and record evidence for:

1. Focused coding task with actual attributable diff and relevant checks.
2. Read-only repository analysis without edits.
3. Failed check followed by bounded correction or honest failure.
4. Model invents success without changing files: rejected.
5. Pre-existing tracked/untracked user modifications preserved.
6. Nonproductive inspection: bounded termination and token diagnostics.
7. Cancellation ends the entire command tree and allows clean next run.
8. Browser status/changed files/check results reflect real engine events.

Use focused automated checks and a small manual acceptance set; add permanent tests only where a genuine regression risk warrants them. Do not impose a giant test suite.

**D02 — Packaging, operations and documentation**

- Verify installation on a clean Windows Python environment and local Ollama connectivity.
- Document setup, configuration, project selection, safety policy, troubleshooting and actual supported features.
- Finalize architecture documentation to match shipped behavior, then create/update the project README.
- Release, tag, push and publication happen only on explicit user instruction.

**Gate M6:** REAPER-AI is usable from a clean installation and passes the evidence-based scenarios above. Outstanding limitations are documented; no unsupported feature claims.

## 4. Deferred scope — not part of the initial release

- Delegated/specialist workers and concurrent editing.
- Multi-model routing as an optimization.
- Advanced durable memory or automated knowledge extraction.
- Autonomous scheduling, deployment/release automation, cloud services, remote agents.
- Interactive graph/workflow editor and heavy frontend framework.

Promote an item only after a real use case justifies it, with an explicit new work package. Do not create placeholder infrastructure now.

## 5. Working agreement

- Complete **one work package at a time**; inspect the current implementation before making targeted changes.
- Verify with the smallest meaningful check and report **what actually ran**, what changed and what remains uncertain.
- Do not alter unrelated modules. Update `ARCHITECTURE.md` if an architectural decision changes.
- Keep documentation consistent, but **write the README after this roadmap is approved**, as agreed.
- Never perform Git commit/push/release automatically.

**Next step:** M4/U01 — minimal HTML/CSS/vanilla JS UI served by FastAPI; preserve evidence labels and align API/engine run identity. No automatic Git commit or push.

## M4 acceptance record

**M4/U01 — Lightweight interface:** Completed for the tested coding workflow.

**M4/U02 — Execution transparency:** Completed for the tested coding workflow.

Browser acceptance demonstrated workspace validation, model selection, coding execution, SSE timeline, structural verification, terminal status and Git diff rendering.

Known limitations are documented in ARCHITECTURE.md. Full functional correctness, browser reconnection and recovery have not been established.

**Next milestone:** M5 — Local persistence and context.

**Release preparation:** v0.2.0 (M3 + M4), subject to Git review and explicit commit/tag instructions.
