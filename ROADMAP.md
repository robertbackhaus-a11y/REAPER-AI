# REAPER-AI — Development Roadmap

**Baseline:** v0.9.1 · 21 October 2026 · Development direction through v1.0.0

REAPER-AI is a local single-user coding agent built on FastAPI, LangGraph, Ollama, SQLite, the existing tool dispatcher, and a vanilla HTML/CSS/JavaScript web interface. This roadmap distinguishes released, evidence-backed capability from planned work. Release targets are planning states, not delivery commitments.

## 1. Release overview

| Version | Focus | Status |
| --- | --- | --- |
| v0.8.0 | Native MCP integration | Released |
| v0.9.0 | Web editor and file navigation | Released |
| v0.9.1 | Sentinel evidence and context | Released |
| v0.10.0 | Autonomous coding quality | In progress — C01/C02/C03 complete |
| v0.11.0 | Cross-platform and workflow UI | Planned |
| v1.0.0 | Stable autonomous coding agent | Target |

## 2. v0.9.1 — Completed development state

The following work is implemented and released. Completion means that the described code path and its focused evidence were recorded; it does not imply universal reliability for every model or task.

- **S01 — Decision-trace analysis and failure classification:** decision traces distinguish observed context, model decisions, tool outcomes, phase/strategy changes, and available thinking data.
- **S02 — Relevance-based source selection:** source excerpts are individually relevance-ranked; historical source coverage remains distinct from the source ranges actually visible to the next model decision.
- **S03 — Evidence-aware working state:** observed tool facts, model proposals, open questions, evidence references, checkpoint persistence, and finalization diagnostics are kept distinct. A successful read is evidence of that read, not automatic proof of a semantic conclusion.
- **Native MCP integration:** v0.8.0 provides native MCP discovery and execution through the existing dispatcher for stdio and Streamable HTTP servers.
- **Project workflow:** project management, project-scoped chat history and durable memory, a repository index, and the integrated web editor are available.
- **Model comparison:** Qwen 3.8 Q5, Qwen 3.6 35B-A3B, and Devstral Small 2 were evaluated with recorded model-specific outcomes. Qwen 3.8 Q5 remains the reference model because it showed the strongest observed autonomous analysis and functional coding result in this agent configuration.

### Known limitations

- The factual interpretation of observed source remains substantially dependent on the selected LLM.
- Structured S03 assessments and question updates are optional decision fields and were not emitted reliably by every tested model; they were diagnostics, not acceptance criteria for the model comparisons.
- `completed` alone does not demonstrate functional task fulfillment; observed changes and independent verification remain authoritative.
- Repeated tool calls and verification loops can still occur in untested model/task combinations. C02 removes one demonstrated cause by preserving shell exit code, stdout, and stderr in the next decision context.
- Terminal usage totals for a recovered run currently cover its resumed process; retained decision traces and event logs provide the complete cross-process measurement.
- The autonomous process supervisor is currently Windows-specific. Linux and macOS installation/API-start commands are documented, but autonomous run start, recovery, and cancellation require a POSIX supervisor before they can be supported.
- Terminal SSE replay can expose a minor status/event ordering inconsistency after a run ends; persisted terminal status and results remain authoritative.

**D01** remains a diagnostic benchmark and model-comparison exercise, not the primary product target. Its accepted Qwen 3.8 run demonstrated a viable read-only workflow, while the externally reviewed report also showed that a completed technical analysis can contain factual errors.

## 3. v0.10.0 — Autonomous Coding Quality

**Goal:** Achieve repeatably reliable autonomous coding tasks with Qwen 3.8 Q5 using the existing Sentinel architecture.

### C01 — Multi-file coding

- Change existing projects across multiple related files.
- Respect interfaces and dependencies.
- Preserve existing behavior unless the task explicitly changes it.

**Completed.** In `183ee8ae8303`, Qwen 3.8 Q5 autonomously implemented `validate` across related configuration, validation, and CLI modules; it preserved the existing `show` output, updated the fixture README, corrected a BOM-related negative-test probe, and passed independent functional checks. The run completed with 20 model calls, 19 tool calls, 202.4 seconds, and no identical repeated action. **C01: passed, grade 2.** This is a successful acceptance case, not a general repeatability claim.

### C02 — Verification and completion

- Base success reports on observed file changes and actual test or functional results.
- Recognize successfully completed work and finish without unnecessary further verification.
- Analyze the known refund benchmark's 18 unnecessary verification repetitions.
- Do not add blanket tool limits or approval mechanisms.

**Completed.** The refund benchmark showed that a successful `unittest -v` result written to stderr was reduced to `shell succeeded` in the next prompt. C02 preserves the shell exit code and bounded stdout/stderr in the existing observation path. In `183ee8ae8303`, this information was practically carried through the end-to-end workflow and the run completed without identical verification repetitions. This confirms the corrected information flow for one run; it does not establish universal completion behavior.

### C03 — Error correction and recovery

- Diagnose and correct implementation errors autonomously.
- Re-run failed checks after a correction.
- Continue interrupted coding runs through the existing checkpoint mechanism.
- Avoid repeating already completed actions without a concrete reason.

**Completed.** In `8f7670dc9ebf`, Sentinel diagnosed and patched a refund-calculation defect, then was deliberately cancelled before completion. The valid `agent_observe → agent_decide` checkpoint retained the changed file and outstanding checks across a REAPER restart. Recovery continued the same run without rereading the implementation or reapplying the patch. Sentinel recovered from an unavailable `pytest` module by using `unittest`, passed both fixture tests, verified `books: 15.00` through the CLI, updated the fixture README, and finished `completed`. Combined trace data records 17 model calls, 16 tool calls, and 147.7 seconds including the interruption/restart. One failed pre-patch `pytest` command was repeated once; no sustained tool loop occurred. **C03: passed, grade 2.** One successful controlled recovery does not establish universal recovery reliability.

### C04 — Existing changes

- Work with a dirty working tree.
- Preserve unrelated user changes.
- Make changes traceable and task-scoped.

### C05 — Coding acceptance

Evaluate three representative coding tasks:

1. Diagnose and correct a functional defect.
2. Implement a feature across two or three existing modules.
3. Demonstrate correction and recovery after an interrupted run.

Each task requires an independent functional check. Assess autonomy, code quality, correctness, error handling, and efficiency separately. The target is grade 2 or better. Do not add REAPER-specific rules constructed solely to pass these tasks.

## 4. v0.11.0 — Cross-platform and workflow UI

**Goal:** Operate REAPER on Windows, Linux, and macOS, and complete the existing web interface for day-to-day coding use.

### P01 — Cross-platform supervisor

- Retain the existing Windows Job Object supervisor.
- Use native POSIX process groups and signal handling on Linux and macOS.
- Support run start, timeout, cancellation, and process-tree termination.
- Preserve checkpoint and recovery behavior.
- Do not add a separate supervisor platform or elaborate abstraction layer.

### P02 — Cross-platform acceptance

Practically verify on Windows, Linux, and macOS:

- Installation.
- API start.
- Autonomous coding run.
- Cancellation of a running run.
- Timeout and process termination.
- Recovery after interruption.

Document only platforms that have actually been tested as supported.

### P03 — Workflow and efficiency

- Investigate and, if confirmed, correct the terminal SSE replay inconsistency.
- Optimize context and token efficiency only where measured runs show a benefit.
- Consider CLI/IDE integrations and skills only when a concrete workflow need is demonstrated.

### P04 — Visual diff and code review

**Goal:** Make Sentinel's observed code changes directly reviewable in the web interface.

- List changed files for each agent run.
- Show line-by-line before/after differences.
- Visually distinguish additions and removals.
- Switch between a diff and the integrated code editor.
- Reuse existing Git, hash, and change-evidence data.
- Support projects without Git where sufficient observed change data exists.
- Show only observed file changes, never model-claimed changes without evidence.

**Acceptance:** After a coding run, a user can identify changed files, compare changes, and open affected files in the editor. No manual approval workflow is required.

### P05 — MCP server and tool-management UI

**Goal:** Manage the v0.8.0 MCP integration through the existing web interface.

- Add, edit, and remove MCP servers.
- Support stdio and Streamable HTTP.
- Show connection status and concrete errors.
- List discovered tools with names, descriptions, and input parameters.
- Manually run connection checks and tool discovery.
- Continue using `mcp.json` as the configuration source.
- Integrate controls into the existing project settings or settings UI.

**Acceptance:** A user can configure, test, and inspect an MCP server and its discovered tools without manually editing JSON. No additional database, second MCP orchestrator, or separate permission system is planned.

### P06 — Agent status and run summary

**Goal:** Make autonomous work understandable without reviewing complete decision traces continuously.

- Show the current phase and subtask.
- Show the current or most recently completed tool action.
- Summarize observed changes, checks, and errors.
- After a run, show actual changed files, successful/failed checks, and open points.
- Reuse existing events, tool facts, and run data.
- Keep decision traces available as a technical detail view.

**Acceptance:** During and after a run, users can see actual progress and observed outcome at a glance. No additional model-assessment or status system is planned.

### P07 — Tool-to-code navigation

**Goal:** Connect chat, agent activity, diff, and editor views.

- Make file paths and line references in tool results clickable.
- Open the relevant file in the integrated editor.
- Navigate to the referenced line where possible.
- Open the associated diff from activity-panel change references.
- Reuse the existing file tree and editor.

**Acceptance:** Selecting a relevant file reference in agent activity reaches the corresponding code position or change. No new editor library or frontend framework is planned.

## 5. v1.0.0 — Stable Autonomous Coding Agent

v1.0.0 is released only on demonstrated reliability, not on feature count.

| Area | Acceptance target |
| --- | --- |
| Autonomous planning and coding execution | Grade 2 or better |
| Multi-file implementation | Reliably demonstrated |
| Functional verification | Independently observed |
| Error diagnosis and correction | Reliably demonstrated |
| Checkpoint recovery | Successfully demonstrated |
| Tool loops | No systematic unproductive loops in acceptance runs |
| Existing user changes | Preserved |
| Cross-platform operation | Windows, Linux, and macOS practically accepted |
| Existing functions | MCP, projects, persistence, memory, and editor retained |
| Workflow UI | MCP management, change review, and understandable run outcomes available |
| Installation | Reproducible |
| Documentation | Matches the implemented system |

Technical repository reviews remain a supporting capability. D01 remains a diagnostic and model-comparison benchmark rather than an independent v1.0 blocking criterion.

## 6. Binding development principles

REAPER remains a lean local single-user coding agent.

- No enterprise architecture.
- No role, tenant, or approval systems.
- No additional agents or orchestration layers without a concrete need.
- No redundant databases or services.
- No custom shell, file-type, or workspace whitelists.
- No artificial tool-call or repair limits.
- No model-specific special mechanisms.
- Qwen 3.8 Q5 remains the reference model.
- Reuse existing architecture and components.
- Retain HTML, CSS, and vanilla JavaScript for the frontend.
- No unnecessary frontend frameworks.
- Prefer small, reviewable changes.
- Keep tests proportional to the actual change.
- Update documentation when relevant behavior changes.
