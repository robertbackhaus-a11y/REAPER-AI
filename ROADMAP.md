# REAPER-AI — Development Roadmap

**Baseline:** v1.0.0 · 23 October 2026 · Development direction after v1.0.0

REAPER-AI is a local single-user coding agent built on FastAPI, LangGraph, Ollama, SQLite, the existing tool dispatcher, and a vanilla HTML/CSS/JavaScript web interface. This roadmap distinguishes released, evidence-backed capability from planned work. Release targets are planning states, not delivery commitments.

## 1. Release overview

| Version | Focus | Status |
| --- | --- | --- |
| v0.8.0 | Native MCP integration | Released |
| v0.9.0 | Web editor and file navigation | Released |
| v0.9.1 | Sentinel evidence and context | Released |
| v0.10.0 | Autonomous coding quality | Released |
| v0.11.0 | Cross-platform and workflow UI | Released |
| v1.0.0 | Stable autonomous coding release | Released (current release) |

## 2. Released state (v1.0.0)

The following work is implemented and released. Completion means that the described code path and its focused evidence were recorded; it does not imply universal reliability for every model or task.

- **S01 — Decision-trace analysis and failure classification:** decision traces distinguish observed context, model decisions, tool outcomes, phase/strategy changes, and available thinking data.
- **S02 — Relevance-based source selection:** source excerpts are individually relevance-ranked; historical source coverage remains distinct from the source ranges actually visible to the next model decision.
- **S03 — Evidence-aware working state:** observed tool facts, model proposals, open questions, evidence references, checkpoint persistence, and finalization diagnostics are kept distinct. A successful read is evidence of that read, not automatic proof of a semantic conclusion.
- **Native MCP integration:** v0.8.0 provides native MCP discovery and execution through the existing dispatcher for stdio and Streamable HTTP servers.
- **Project workflow:** project management, project-scoped chat history and durable memory, a repository index, and the integrated web editor are available.
- **Model comparison:** Qwen 3.8 Q5, Qwen 3.6 35B-A3B, and Devstral Small 2 were evaluated with recorded model-specific outcomes. Qwen 3.8 Q5 remains the reference model because it showed the strongest observed autonomous analysis and functional coding result in this agent configuration.
- **Autonomous coding quality (v0.10.0):** C01–C05 accepted with overall grade 2 (section 3).
- **Cross-platform supervisor (v0.11.0, P01/P02):** Windows Job Object and POSIX process-group supervision; Windows and Linux practically confirmed, macOS accepted on the shared POSIX implementation.
- **Workflow UI (v0.11.0, P04–P08):** read-only Git Diff tab, MCP server management with global configuration and per-project activation, agent status and run summary, tool-to-code navigation, and the distinction between general chats and coding projects (section 4).

### Known limitations

- The factual interpretation of observed source remains substantially dependent on the selected LLM.
- Structured S03 assessments and question updates are optional decision fields and were not emitted reliably by every tested model; they were diagnostics, not acceptance criteria for the model comparisons.
- `completed` alone does not demonstrate functional task fulfillment; observed changes and independent verification remain authoritative.
- Repeated tool calls and verification loops can still occur in untested model/task combinations. C02 removes one demonstrated cause by preserving shell exit code, stdout, and stderr in the next decision context.
- Terminal usage totals for a recovered run currently cover its resumed process; retained decision traces and event logs provide the complete cross-process measurement.
- P02 is accepted: Windows and Linux (Debian 13 under WSL2) are practically confirmed. macOS is accepted on the shared POSIX implementation but has not been practically tested.
- A hard crash of the API process during a running run has not been practically tested; P02 demonstrated recovery after cancellation and an API restart.
- v1.0.0 configures the Ollama endpoint through `REAPER_OLLAMA_URL`, keeps terminal SSE replay and live streaming on one authoritative sequence/status path, and uses Linux-compatible unit-test paths.

**D01** remains a diagnostic benchmark and model-comparison exercise, not the primary product target. Its accepted Qwen 3.8 run demonstrated a viable read-only workflow, while the externally reviewed report also showed that a completed technical analysis can contain factual errors.

## 3. v0.10.0 — Autonomous Coding Quality Acceptance

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

**Completed.** In `7211e4ac8a5a`, Qwen 3.8 Q5 added a JSON-output option to a small CLI while working in a deliberately dirty Git fixture. It retained a user edit in the jointly modified CLI file, left an unrelated user edit and an untracked user note SHA-256-identical, preserved the existing text output, added a JSON test, and updated the fixture README. Independent checks passed all 3 tests and both output modes. The run completed with 16 model calls, 15 tool calls, 102,787 input tokens, 6,031 output tokens, and 148.6 seconds, with no identical unproductive tool repetition. **C04: passed, grade 2.** Some already evidenced questions remained terminally `inconclusive` in the diagnostic output; this did not affect the observed task outcome. One successful dirty-tree run does not establish universal preservation reliability.

### C05 — Coding acceptance

Evaluate three representative coding tasks:

1. Diagnose and correct a functional defect.
2. Implement a feature across two or three existing modules.
3. Demonstrate correction and recovery after an interrupted run.

Each task requires an independent functional check. Assess autonomy, code quality, correctness, error handling, and efficiency separately. The target is grade 2 or better. Do not add REAPER-specific rules constructed solely to pass these tasks.

**Completed.** C05 consolidates the independently checked C01–C04 evidence rather than adding a new benchmark. `183ee8ae8303` demonstrated multi-file feature work and clean completion; C02 retained successful shell exit code, stdout, and stderr in the following decision context; `8f7670dc9ebf` demonstrated post-patch checkpoint recovery; and `7211e4ac8a5a` demonstrated task-scoped changes while preserving dirty working-tree content. Every recorded acceptance run completed with exit code 0 and each C01, C03, and C04 functional acceptance received grade 2; C02 was practically confirmed in C01.2. **C05: passed, overall grade 2.** The v0.10.0 autonomous-coding scope is therefore complete. These are finite, independently checked examples, not a claim of universal model or task reliability.

## 4. v0.11.0 — Cross-platform and workflow UI (released)

**Goal:** Operate REAPER on Windows, Linux, and macOS, and complete the existing web interface for day-to-day coding use.

### P01 — Cross-platform supervisor

- Retain the existing Windows Job Object supervisor.
- Use native POSIX process groups and signal handling on Linux and macOS.
- Support run start, timeout, cancellation, and process-tree termination.
- Preserve checkpoint and recovery behavior.
- Do not add a separate supervisor platform or elaborate abstraction layer.

**Implemented; accepted through P02.** Windows retains its Job Object implementation. On POSIX, the existing supervisor starts the engine in a dedicated session and terminates that process group on timeout or API cancellation. The existing start and recovery API contracts are shared across both paths. A Windows regression suite and an actual Job Object timeout probe cover the retained path; the POSIX normal-exit, cancellation, and timeout branches have focused tests. P02 has practically exercised the POSIX path on Linux; macOS uses the same path but remains practically unexercised.

### P02 — Cross-platform acceptance

Practically verify on Windows, Linux, and macOS:

- Installation.
- API start.
- Autonomous coding run.
- Cancellation of a running run.
- Timeout and process termination.
- Recovery after interruption.

Document only platforms that have actually been tested as supported.

**PASS (adjusted acceptance decision): Windows and Linux practically confirmed; macOS accepted on the shared POSIX implementation, not practically tested.** Windows is accepted through the retained Job Object regression and the prior Windows acceptance runs. Linux was tested on Debian GNU/Linux 13 (trixie) under WSL2 (kernel 6.18.33.2-microsoft-standard-WSL2, x86_64) with Python 3.13.5, using a fresh source tree of `998d1c6`, a fresh virtual environment, `python -m pip install .`, and the documented `uvicorn` start. The existing Windows Ollama 0.40.4 server was reached over the WSL2 mirrored network at `127.0.0.1:11434`; no Linux Ollama was installed. With `qwen3.8-q5:latest` and small Git fixtures:

- A normal run (`a4353ec566d8`) fixed a one-line defect, ran the test, and finished `completed` with exit code 0 in 4 model calls and 19.2 seconds; an independent `unittest` run passed.
- API cancellation (`c022987b9109`) during an engine-launched `/bin/sh -c "sleep 90 …"` produced `cancelled`, supervisor exit code 130, and `ENGINE_CANCELLED`; the engine group (engine, `sh`, `sleep`) was empty afterwards.
- A 20-second timeout (`99be4b5e0db6`) during the same shell sleep produced `timed_out`, exit code 2, and `ENGINE_TIMEOUT`; the engine group was again empty.
- A run (`b487e3951342`) was cancelled after its patch, during its slow verification command. After an API restart, the persisted `cancelled` status and valid `agent_decide → agent_execute` checkpoint were reported, and explicit resume continued the same run: it re-executed only the interrupted verification, did not reread or repatch the file, and finished `completed` with exit code 0 in 1 resumed model call.

After each case, no supervisor, engine, or engine-launched shell process remained. No product correction was required. Of the 27 repository unit tests, 23 pass on Linux; the 4 failures are Windows-specific test assumptions (PowerShell commands, and patching `os.name` to `nt`, which makes `pathlib` construct Windows paths), not observed product behavior. No macOS runtime was available. By adjusted acceptance decision, macOS is accepted on the basis of the shared POSIX process-group implementation practically confirmed on Linux; this is not a practical macOS test, and macOS-specific deviations remain possible. A hard crash of the API process during a running run was not practically tested. P02 is accepted (PASS) with these limitations.

**Follow-up in v1.0.0 (section 5):** the four Windows-specific test assumptions were corrected. Shell-tool tests choose PowerShell or `/bin/sh` commands by platform, and the two API tests patch the supervisor platform check instead of `os.name`. The suite now passes on Windows and on Debian 13 under WSL2 (Python 3.13); there, the Node.js-based web UI helper tests are skipped because Node.js is not installed.

### P03 — Workflow and efficiency

- Investigate and, if confirmed, correct the terminal SSE replay inconsistency.
- Optimize context and token efficiency only where measured runs show a benefit.
- Consider CLI/IDE integrations and skills only when a concrete workflow need is demonstrated.

**SSE replay: confirmed and corrected in v1.0.0 (section 5).** Three causes were reproduced with focused API tests. (1) Every supervisor process persisted its events starting at sequence 1 with `INSERT OR REPLACE`, so a resumed run overwrote the first events of the earlier process; after an API restart the replay showed the recovery events first, then stale events of the cancelled process after the recovery's `run_completed`. (2) The live stream ended with the engine's `run_completed` even when the authoritative API status differed (for example `cancelled` when cancellation raced the engine's own completion). (3) A run without an engine terminal event (timeout, cancellation, interruption) received a synthetic terminal event only in the live stream, never in the persisted replay. Now each process appends its events after the stored ones, the API records its own `run_completed` (source `api`) whenever the engine reported none or a different status, a finished run is always replayed from the database, and a running (resumed) run streams the events of its earlier processes before its live log. Live stream and replay therefore use the same sequence numbers and end with the same authoritative status. The browser's run status is reset to running on `run_started`/`recovery_started`, so a recovered run no longer keeps the earlier terminal state. The other P03 items remain open.

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

**Completed (released in v0.11.0).** The Diff tab beside Chat and Editor shows read-only Git comparisons in three modes: Working Tree against `HEAD` (tracked changes by default, untracked files on request), a selected local commit against its first parent, and a selected local release tag against `HEAD`, optionally including uncommitted tracked changes. The file list (file name emphasized, directory below, full path as tooltip, M/A/D/R/? markers) is resizable between 220 px and 70 % with the width kept in the browser; the selected file and historical comparison are preserved across refreshes, including the automatic refresh after agent events. Git is the only source of commits, tags, and changes (`GET /projects/{project_id}/changes/refs`, `.../changes`, `.../changes/diff`); there is no version database and no approval, review, commit, checkout, or reset function. The three modes were practically checked in the browser against a test repository with commits, a rename, a deletion, lightweight and annotated tags, and working-tree/untracked changes; the repository was unchanged afterwards and the repository unit tests pass. The `.active-run` display fix (an inactive run was shown as running) shipped with this package. Deliberately not part of this scope: per-run change lists and projects without Git. Opening a diff file in the editor was added later by P07.

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

**Completed (released in v0.11.0).** The settings dialog has a global *MCP-Server* section (add, edit/rename, enable/disable, remove, connection test showing tool names, descriptions, and input parameters or the concrete error) that reads and writes `mcp.json` directly, and the project section selects which global servers a project uses. Projects store only server IDs; coding and general chat project runs receive tools only from selected, globally enabled servers, while legacy chats without a project keep access to all enabled servers. Changes apply from the next run; a running run keeps its start snapshot, and renaming or removing a server updates project selections. Functionally checked with two stdio and one Streamable-HTTP test server on a copy of the application database: real Sentinel runs in coding projects with two and with one selected server, in a general chat project, and in a legacy chat each saw and called only the expected `mcp:<server>:<tool>` tools; disabling a server during a run did not affect that run; the browser flows (add, test including a failing server, parameter display, rename, remove, project selection) were exercised. All 34 unit tests pass. **P05: completed.** Remaining by design: connection status is the result of a manual test rather than continuous monitoring, and existing projects start with no MCP server selected.

### P06 — Agent status and run summary

**Goal:** Make autonomous work understandable without reviewing complete decision traces continuously.

- Show the current phase and subtask.
- Show the current or most recently completed tool action.
- Summarize observed changes, checks, and errors.
- After a run, show actual changed files, successful/failed checks, and open points.
- Reuse existing events, tool facts, and run data.
- Keep decision traces available as a technical detail view.

**Acceptance:** During and after a run, users can see actual progress and observed outcome at a glance. No additional model-assessment or status system is planned.

**Completed (released in v0.11.0).** The existing right panel (*Agentenaktivität*) shows a compact status block for the displayed run: status (läuft, abgeschlossen, teilweise abgeschlossen, abgebrochen, Timeout, Fehler, unterbrochen), current activity derived from the received events (for example *Datei lesen*, *Code ändern*, *Tests ausführen*, *Änderungen prüfen*, *Modell denkt nach*), the last completed tool action with its result, exit code and duration, the runtime since run start, and the model and tool calls counted so far. After a run, a copyable *Run-Zusammenfassung* lists the status, the Sentinel result text, recorded changed files, test commands found among the recorded shell checks with their results, failed tool calls and the reason for failed or timed-out runs, open points (unresolved questions, a cancel without a stored result), runtime, and model/tool calls. Everything comes from the existing SSE events, `GET /runs/{id}`, and the stored result; missing data is shown as *nicht erfasst*. No backend, telemetry, polling, or status-machine change was made; the helpers live in `static/run_status.js` and are unit-tested through Node. *Live-Ereignisse*, *Decision Trace*, and *Ollama Thinking / Laufzeitlog* remain unchanged. Checked in the browser on a copy of the application database with real Sentinel runs: running display, browser reload during a run (state rebuilt from the replayed event stream), switching projects and chats during a run, cancel, and completed runs with changed files and a passing test. All 41 unit tests pass (7 new). **P06: completed.** Limitations: tests are recognized by command pattern (`pytest`, `unittest`, `npm test`, …) only; counts for a run without a stored result come from the persisted events. The former limitation that a run finishing while another chat was open replaced that chat's panel is fixed in P07.

### P07 — Tool-to-code navigation

**Goal:** Connect chat, agent activity, diff, and editor views.

- Make file paths and line references in tool results clickable.
- Open the relevant file in the integrated editor.
- Navigate to the referenced line where possible.
- Open the associated diff from activity-panel change references.
- Reuse the existing file tree and editor.

**Acceptance:** Selecting a relevant file reference in agent activity reaches the corresponding code position or change. No new editor library or frontend framework is planned.

**Completed (released in v0.11.0).** File references in *Live-Ereignisse* (tool actions with their `path`, including `read_file_range` line ranges), *Decision Trace*, and *Run-Zusammenfassung* (changed files, result text, test commands, errors such as `path:line` or Python tracebacks) are links. Selecting one switches to the *Editor* tab, opens the file through the existing `GET /projects/{id}/files/content`, jumps to the referenced line, and highlights the affected lines (the referenced range, or the `apply_patch` replacement text located in the current file). The *Diff* tab has an *Im Editor öffnen* button and clickable diff lines that open the file at the new-side line with the added lines highlighted; for commit or release-against-`HEAD` comparisons the file opens without line jump because those line numbers need not match the working tree, and deleted files are not offered. Links are created only in coding projects and only for paths inside the selected project's workspace (relative, `./`, `..` within the workspace, or absolute with case-insensitive Windows prefix matching); paths outside it, URLs, and general chats stay plain text. Files missing from the repository index are marked, and a deleted or moved file produces *Datei nicht mehr vorhanden …* instead of an error. Navigation never changes or saves a file: a file with unsaved edits is only switched to, not reloaded (this also applies to the file tree). Path mapping lives in `static/code_links.js` (pure helpers, unit-tested through Node); no backend, framework, or new API was added.

GUI fix from P06: the right panel now always shows the selected chat — its live run while that run is active, otherwise its last recorded run. Events, decision traces, and timelines are cached per run, so a run that ends while another chat is open no longer appears in the visible chat or panel (the activity line only notes that a run in another conversation finished), and switching back to the running chat restores its live timeline without duplicates. Checked in the browser on a copy of the application database with a scratch Git workspace and a fake supervisor emitting real event shapes: links in all three panel sections, line jump and highlight, deleted file message, outside path left as text, Diff button and line clicks (deleted file disabled), run finishing while another chat is open, switching back to a running chat, browser reload during a run in another chat, and no links in general chats. All 54 unit tests pass (13 new). **P07: completed.** Limitations: line numbers from older runs refer to the file state at that time; links in the decision trace carry no line numbers; bare file names without a directory are linked only when they are in the repository index; the activity panel opens files in the editor rather than in the Diff tab.

### P08 — General chats and coding projects

**Goal:** Distinguish general conversations from workspace-bound coding projects in the existing project management, for organization and a fitting interface, without restricting autonomous capabilities.

- Keep existing legacy chats unchanged and usable without a workspace or Git repository; never convert them into coding projects automatically.
- Coding projects keep their workspace, editor, Git diff, project memory, checkpoints, Sentinel, and P05 MCP selection.
- Distinguish both kinds in the navigation, fix the type when a project is created, keep uniform chat tabs including hide and permanent delete, and offer editor and diff only where the context supports them.
- Reuse the existing SQLite store and API; no second project management, database, service layer, roles, or tool restrictions; no rebuild of the coding engine.

**Implemented (released in v0.11.0); not formally accepted.** Projects carry a fixed `kind` (`coding` or `chat`, additive column defaulting to `coding`, so every existing project stays a coding project and no data is converted). General chat projects have no workspace; they, and legacy chats without a workspace, run through the unchanged engine with the scratch directory `chat-workspace` in the application data directory. The sidebar groups *Allgemeine Chats* (Legacy-Chats plus chat projects) and *Coding-Projekte*; the Legacy-Chats view now lists only conversations without a project (`GET /conversations?legacy=true`). Editor, Diff, file tree, and repository index are offered only in coding projects, and their endpoints reject chat projects. Checked on a copy of the real database (one coding project, nine project chats, integrity OK before and after): the existing project appears as a coding project with its chats, files, editor, and diff; a new chat project was created without a workspace and a real general-chat run was started from the browser. All 34 unit tests pass (five new). Open: a project's type cannot be changed later, conversations cannot be moved between projects, and general chats share one scratch working directory.

Two interface fixes ship with this package: after a run completes, is cancelled, or times out, *Senden*/*Run starten* is enabled again and *Abbrechen* disabled; and a still-pending project-list refresh no longer overwrites an open *Neues Projekt* form or its MCP selection. Both were checked in the browser against a copy of the database with a simulated timed-out run and a delayed project list.

## 5. v1.0.0 — Stable Autonomous Coding Agent

v1.0.0 is released only on demonstrated reliability, not on feature count.

### Released changes in v1.0.0

- **Linux-compatible unit tests (P02 follow-up):** shell-tool tests choose PowerShell or `/bin/sh` by platform, and API tests patch the supervisor platform check instead of `os.name`. The suite passes on Windows and on Debian 13 under WSL2; the Node.js-based web UI helper tests are skipped where Node.js is not installed.
- **Configurable Ollama endpoint:** `REAPER_OLLAMA_URL` selects another Ollama server for both the API's model list and the engine's model calls; without it, the endpoint remains `http://127.0.0.1:11434`.
- **Corrected terminal SSE replay (P03):** live stream and database replay use the same sequence numbers and end with the same authoritative status (details in P03).
- **Consistent version metadata:** `reaper_ai.__version__`, `pyproject.toml`, and the FastAPI app version all report 1.0.0.

The autonomous coding logic was not changed. Practical macOS acceptance and a platform-neutral description of the `shell` tool in the engine's decision prompt remain post-release work.

### Acceptance targets

| Area | Acceptance target |
| --- | --- |
| Autonomous planning and coding execution | Grade 2 or better |
| Multi-file implementation | Reliably demonstrated |
| Functional verification | Independently observed |
| Error diagnosis and correction | Reliably demonstrated |
| Checkpoint recovery | Successfully demonstrated |
| Tool loops | No systematic unproductive loops in acceptance runs |
| Existing user changes | Preserved |
| Cross-platform operation | Windows and Linux practically accepted; macOS uses the shared POSIX implementation pending practical acceptance |
| Existing functions | MCP, projects, persistence, memory, and editor retained |
| Workflow UI | MCP management, change review, and understandable run outcomes available |
| Installation | Reproducible |
| Documentation | Matches the implemented system |

Technical repository reviews remain a supporting capability. D01 remains a diagnostic and model-comparison benchmark rather than an independent v1.0 blocking criterion.

### Unreleased — Sequential task list

**Implemented after v1.0.0; not released.** Coding projects get a compact task list in the sidebar (add, edit, delete, reorder; *Starten*, *Anhalten*, *Fortsetzen*). Sentinel processes open tasks one at a time; each task is a normal run in its own linked conversation. The end of a task's run starts the next open task, a non-completed outcome marks the task failed and halts the list, and *Fortsetzen* reuses checkpoint recovery where possible. Completed tasks are never rerun. Tasks persist in the existing SQLite store; no new agent, scheduler, framework, or state system was added, and nothing is committed automatically. Practical check on a scratch Git workspace with `qwen3.8-q5:latest`: two short tasks (add a function, then document it in a new README) ran back to back; the second started automatically about two seconds after the first completed, both ended `completed` in their own conversations, and no commit was created. The existing 45 unit tests pass; no new tests were added.

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
