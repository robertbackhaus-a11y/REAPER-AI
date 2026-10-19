## Installation (Windows)

Requirements: Windows 11, Python 3.12+, Git and Ollama.

```powershell
git clone https://github.com/robertbackhaus-a11y/REAPER-AI.git
cd REAPER-AI
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
ollama pull qwen3.6:35b
.\.venv\Scripts\python.exe -m uvicorn reaper_ai.api:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000/ in your browser.

For development, use `pip install -e .` instead of `pip install .` to install the project in editable mode.

REAPER-AI runs locally and does not commit or push changes automatically.

# REAPER-AI — Architecture

**Version:** 0.5.0
**Status:** M0–M4, M5/P01–P03, M6/P01–P06, P0 targeted edits, and P1 working-memory consolidation are implemented. One checkpoint-recovered end-to-end Sentinel coding acceptance is recorded; broader task reliability remains an explicit limitation.
**Scope:** Local, single-user autonomous coding agent  
**Language:** English for repository documentation; user interaction follows the user's language.

## 1. Objective

REAPER-AI is a locally operated engineering agent that can understand a software task, inspect a repository, propose and apply focused changes, execute appropriate checks, correct failures, and report verifiable results. A conversation is its interface; a controlled execution workflow is its core.

The former REAPER-AI v0.x implementation is **not** an architectural baseline. This document describes a new system, not a migration or a retrofit. Experience from the previous implementation informs constraints, but does not require code reuse.

### Design principles

- **Evidence over claims.** A model's statement is not proof that a file changed or a check passed.
- **Deterministic control, model-driven engineering.** Python determines allowed transitions, limits, and evidence collection; the model performs technical reasoning and proposes actions.
- **Local and single-user.** No accounts, tenants, role management, cloud orchestration, or enterprise platform layers.
- **Focused, budgeted work.** Focused repository inspection and targeted verification use wall-time and prompt/output budgets without fixed tool, file, or repair-attempt caps.
- **Explicit failure.** A task that cannot be completed must finish with a precise explanation rather than a fabricated success.
- **Minimal dependencies and clear boundaries.** Prefer mature libraries where they replace real infrastructure work; do not recreate an agent framework.
- **No implicit publishing.** No automatic commits, pushes, tags, releases, deployments, or destructive repository operations.

## 2. Architecture decisions

| Area | Decision | Rationale |
|---|---|---|
| Workflow orchestration | **LangGraph (Python library)** | Explicit state, nodes, routing, and checkpoint-safe iteration; no homegrown free-running tool loop |
| Local inference | **Ollama** | Local model hosting, selectable models, tool-capable API |
| Core application | **Python** | Direct integration of graphs, tools, Git, subprocesses |
| User interface | **Custom lightweight browser UI (HTML/CSS/JavaScript)** | Full control, minimal dependencies; Chainlit is not used |
| API/server | **FastAPI, embedded local HTTP/SSE service** | Serve the custom UI and expose sessions, runs, projects, cancellation, and structured events |
| Long-term knowledge | **Scoped SQLite durable memory** | Explicit user decisions and observed completed-run evidence; external Basic Memory deferred |
| Source-of-truth | **Repository and tool results** | Current files, Git state, exit codes, and test results outrank memory |
| Conversation/run history | **SQLite via Python stdlib** | One local file, no ORM or server; records conversations, runs, results, and bounded structured events |
| Persistent execution | **SQLite stage checkpoints with explicit recovery** | Continue only after user action from the next verified stage; no automatic replay |
| Iterative-agent execution | **Serializable agent state plus deterministic dispatcher** | One LangGraph loop reuses current adapters and checkpoints |
| Git safety | **Never commit/push automatically** | Changes remain reviewable and under user control |

LangGraph is already installed in the local development environment and is used **as an embedded Python library**, not as a hosted LangGraph server/platform. The custom UI is served locally through FastAPI; **Chainlit is excluded**. No LangSmith or other cloud dependency is required.

## 3. Logical system

```text
                        User
                         |
           Lightweight browser interface
               (HTML / CSS / JS)
                         |
                Local FastAPI / SSE
           (sessions, models, projects,
             run control, live events)
                         |
              REAPER Execution Engine
                 (LangGraph state)
                         |
       +-----------------+-----------------+
       |                 |                 |
   Task analysis     Implementation    Verification
       |                 |                 |
       +---------- Execution policy ------+
                         |
             Structured tool adapters
          +--------------+--------------+
          |              |              |
      Repository      PowerShell      Git / checks
        files         subprocess       and diffs
                         |
               Local project workspace

      Ollama: model inference for graph nodes
      SQLite durable memory: scoped, verified knowledge only
      Local run store: conversation metadata, events, results, checkpoints
```

These are **logical responsibilities**, not separate network services or separate LLM agents. One local Python application may contain them all. The executor may run in a child process for cancellation and containment; that does not imply a distributed system.

<!-- M2 IMPLEMENTATION STATUS (2026-10-18) -->
### Implementation checkpoint — M0 to M2 (2026-10-18)

- **M0:** Local Python package with embedded LangGraph and Ollama connectivity established; CLI entry point available. This does not certify every originally proposed F01/F02 deliverable.
- **M1:** CLI coding path in `src/reaper_ai/engine.py` with focused repository/file/Git tools in `tools.py`. A disposable-repository, two-file coding task completed with recorded syntax/Git evidence.
- **M2:** Bounded model calls/wall time, structured `RUN_RESULT`, limited correction handling and a separate Windows supervisor in `src/reaper_ai/supervisor.py` are implemented. The tested coding result (`multiply` and `multiply_numbers`) passed a real Python import/call assertion: `FUNCTIONAL: OK`.
- **R04 evidence:** Synthetic timeout after three seconds returned supervisor exit 2 and verified termination of both parent and child processes (`R04: PASS`). This proves the tested timeout path, **not** every cancellation or interruption case.
- **Run provenance:** historical before/after Git snapshot code was removed when the static pipeline was retired. Run identity, events, and attributable file hashes remain in the API/SQLite records and tool observations.
- **Unverified/deferred:** Exhaustive R01–R03 edge-case behavior, general functional correctness, resumed runs, user-driven cancellation, and all planned API/UI/persistence features. Existing dirty files remain protected; no automatic adoption/rollback of pre-existing changes is implied.
- **Observed result vocabulary:** The current engine has emitted `failed` and more specific verification levels/statuses; it must not be described as fully implementing the idealized `RunResult` contract below until checked against actual code. The architecture below remains the target where not specifically evidenced.

## 4. Core execution contract

A **run** is one bounded attempt to satisfy one user request against a specific project/workspace. Conversation turns and runs are distinct: a chat can contain several runs, but one run has its own immutable initial objective and execution state.

### Input

- User request, including explicit constraints.
- Project identity and **absolute repository/workspace path**.
- Selected local model and bounded inference parameters.
- Optional relevant conversation context, structured as prior user decisions and verified run results.
- Execution policy (permissions, time limits, iteration limits, protected paths).

### Output

The engine returns a structured `RunResult` independently of model-generated prose:

```text
RunResult
  status: completed | partial | failed | cancelled | timed_out
  objective: string
  summary: string
  changed_files: list[path]
  diff_summary: string
  checks: list[{name, command, exit_code, outcome, output_excerpt}]
  evidence: list[reference]
  unresolved: list[string]
  usage: {model_calls, input_tokens, output_tokens, elapsed_seconds}
```

`completed` means the requested action was performed and the defined verification gates passed. It does **not** promise formal correctness. `partial` is explicit when some deliverables are missing. `failed` and other terminal statuses may not be described as success in subsequent conversation context.

For a **read-only** request, no code change is required; evidence is the inspected source/output. For a coding request, observable changes and appropriate checks are required unless the correct conclusion is that no change is needed, which must be explicitly evidenced.

## 5. LangGraph execution model

### Graph state

Use a compact typed state, not the entire conversation transcript as the primary workspace:

```text
ExecutionState
  run_id
  objective
  task_kind: coding | analysis | investigation | other
  project_root
  constraints
  phase
  findings: bounded list of sourced observations
  plan: small list of concrete next actions
  files_in_scope: list[path]
  baseline: repository-state fingerprint
  change_evidence: actual Git/file observations
  checks: recorded tool execution results
  errors: bounded list
  attempts: counters per phase
  messages: bounded LLM interaction context
  result: RunResult (when terminal)
```

The model can propose findings, a plan, and tool requests; the **engine owns counters, tool results, file-change evidence, permissions, and terminal status**. Do not ask the model to fabricate engine-owned fields.

### M6/P01 agent foundation (implemented)

`reaper_ai.agent.AgentState` is a compact, JSON-serializable state for every new run. It holds the plan/current step, next action, metadata-only observations, confirmed facts, model decisions/information gaps, factual progress (including retained sources), verification state, and tool-call accounting. Immutable task and workspace context remain in the enclosing LangGraph state; file hashes remain with individual guarded write actions. The existing SQLite checkpoint already stores the complete engine state, so no second persistence store or checkpoint mechanism is introduced. Older agent checkpoints are normalized when first used and remain readable.

`dispatch_tool` is the single execution boundary. A request has a tool name, structured arguments, and a context containing workspace and optional working directory. Its result always has `ok`, `exit_code` where applicable, stdout/stderr or structured data, `duration_ms`, `changes`, and a precise `error`. Output is bounded before it can become an observation. It does not elevate privileges; the operating system is the authority for every action.

The active execution graph is:

```text
START → agent_prepare → agent_decide → agent_execute → agent_observe
                              ↑                              │
                              └──────── next decision ────────┘
agent_decide → agent_finalize → END
```

`agent_decide` obtains a constrained JSON action or final-report decision from Ollama. The engine supplies project context; `agent_execute` calls `dispatch_tool`; and `agent_observe` appends the normalized result to state. The system contract requires a short `reason`, treats known findings and observed results as the primary basis for the next useful action, moves implementation work from sufficient inspection to a minimal change and proportionate check, and requires a changed strategy after errors or ineffective actions. `plan`, `open_verifications`, and `verification_for` are optional, accepted decision fields; the last two are applied only through the existing progress logic. A write is evidence of the write, while the independently successful checking action must carry the exact matching `verification_for` item to clear its proof requirement. A final decision can declare only `completed` (the goal has observed support) or `partial` (an observed concrete blocker/incomplete result); the engine copies that outcome into the terminal run status. Tool errors and malformed or invalid model decisions become observations for the next decision with the available error message. A final report is accepted only after at least one successful observed tool result and no explicitly open verification point. The model's final prose remains an assessment separate from the `observed_tools` verification label.

The agent has a wall-time budget, compact planner observations, and a LangGraph recursion guard derived from wall time; it has no arbitrary model-call, tool-call, file, or repair cap. Source text stays once in retained hash-bound sources; historical read observations retain metadata only. The next decision receives factual progress, selected source excerpts, confirmed facts, and compact observation metadata. A `decision_trace` event records the actual serialized prompt's hash/size plus the exact presented factual, decision, gap, verification, and source-range sections; it later receives a same-ID outcome event with tool result, information gain, repeat marker, and phase/strategy transition. Ollama is called with `think: false` and `format: "json"`; `model_thinking` therefore records a missing separate thinking field rather than inventing one. This avoids Qwen responses that put all output in thinking while leaving the required JSON content empty. Both are ordinary bounded execution events, so SQLite replay after restart and the right-panel diagnostic view use the existing event path. It checkpoints after prepare, decision, execution, and observation through the existing SQLite store. Recovery recognizes `agent_*` stages and starts at their stored next safe node; already-observed tools are not rerun. The API accepts any existing absolute project directory; Git is optional, and SSE carries the existing phase/model/tool/checkpoint events.

### On-demand file access and edit scope

The agent requests file listing/searching and focused text or line-range reads through `tools.py`; there is no separate inventory, selection, proposal, or apply pipeline. Paths can be workspace-relative or absolute, subject to the started user's operating-system permissions. Ordinary project scans skip Git object storage so source files are not crowded out; `.git` and all other hidden paths remain available when explicitly targeted. Text tools distinguish binary content from UTF-8 text, so binary files can be listed but are not silently treated as editable text.

The planner prompt is bounded by `REAPER_MODEL_CONTEXT_CHARS` (default 36,000 UTF-8 bytes, clamped to 8,000–96,000) and retains only compact recent observations. Existing-file writes require the SHA-256 value returned by a preceding read; `tools.py` compares that value immediately before writing and returns a conflict instead of overwriting an intervening change. The write result records before/after hashes as change evidence. Git can supply additional inspection evidence but is not a precondition.

### Progress and budgets

- Bound wall-clock execution time, model prompt size, and captured output; there is no fixed model-call, tool-call, file, or repair-attempt cap.
- Track meaningful progress separately from tool-call count: scoped findings, a concrete implementation action, actual file changes, and recorded checks.
- Preserve source-linked findings across model-context compaction. Re-reading remains available when a different range or changed version is relevant.
- A failed verification remains an observed error; it never silently converts into success.

## 6. Model roles and delegation

**Begin with one capable engineering model and deterministic graph nodes.** Do not assume the old Main/Worker split is necessary.

Optional specialist workers may later be introduced when a task is genuinely separable, e.g. repository search, independent diagnostics, or a narrowly scoped implementation. Every delegated task must have:

- An explicit objective, allowed paths/tools, and bounded resource budget.
- Its own context rather than access to all chat history.
- Structured results, evidence and failure status—not only a narrative summary.
- A clear ownership rule for file modifications; avoid concurrent editing of the same working tree without isolation.

Workers are a capability to add after the single-agent execution path works, not an initial requirement. Different model sizes are an optimization, not an architecture principle.

## 7. Tool layer

Tools are narrow Python adapters returning **structured results**. A shell remains available for operations that cannot be expressed as a focused tool, but should not be the sole file-inspection or Git interface.

### Current dispatcher tools

| Tool | Responsibility | Evidence |
|---|---|---|
| `list_files` / `search_text` / `read_file` / `read_file_range` | Focused discovery and bounded text excerpts | Paths, line ranges, source hash |
| `write_file` | Whole-text write with read-hash precondition | Changed path, before/after hashes |
| `apply_patch` | One exact text replacement in an existing UTF-8 file | Changed path, before/after hashes; no write unless the original text occurs once |
| `git_status` / `git_diff` | Optional repository inspection | Machine-observed repository state |
| `shell` | PowerShell or child process execution with cwd/timeout/output cap | Command, exit code, captured output |
| `find_python` | Python launcher discovery | Resolvable executable paths |

The dispatcher exposes `list_files`, `read_file`, `read_file_range`, `search_text`, `write_file`, `apply_patch`, `git_status`, `git_diff`, `find_python`, and `shell` through the iterative graph. A workspace is initial project context, not a filesystem boundary: file paths may be absolute, and the shell's working directory can be explicitly supplied. Git actions are observational and do not require a repository.

There is no shell allow-list, command blacklist, risk classification, or approval dispatcher. Tools run under the started user's ordinary operating-system permissions, without automatic elevation. A text write to an existing file requires the SHA-256 returned by a prior read; a changed source returns a conflict and is not overwritten. For a focused existing-file edit, `apply_patch` also requires the exact original text to occur exactly once before it writes.

Tools must validate their working directory and report failures explicitly. An output-truncation flag is never interpreted as a complete source read. Tool implementations—not the LLM—record actual invocation data.

### Safety and change policy

- Treat the workspace as normal project context, not a filesystem sandbox. Absolute file paths are permitted and remain subject to the started user's operating-system permissions.
- A dirty Git state is not a run precondition. Existing-file writes use their just-read hash, and their before/after hashes make the individual agent change attributable.
- Default to no Git commit, push, tag, release, deployment, package installation, or destructive deletion.
- Make cancellation terminate the entire process tree, including shell children; use suitable Windows process containment where required.
- A dedicated project working directory is distinct from persistent application data.

## 8. Verification and truthful completion

Verification is based on agent-selected tool evidence, not a model self-assessment.

For coding tasks, record every observed write, Git inspection, and check command. Success requires at least one successful tool result, while functional verification still requires the agent to choose an appropriate check. A successful syntax check is not equivalent to satisfying functional correctness.

The iterative execution state also retains a small factual progress snapshot separate from the full observation history: the latest attributable change, recent check outcomes, explicitly open verification points, and recent errors. It is checkpointed with the run and precedes compact historical observations in the next planner prompt. A model action can tie itself to exact open verification points; only a successful observed result removes those points. This gives the model stable evidence for deciding whether to correct, verify, or return its `final` decision; it does not automatically declare a task complete or prohibit a repeat action when new evidence warrants one.

Successful `read_file` and `read_file_range` observations also produce separate source findings with path, line range, source hash, observed text, and source/capture truncation metadata. Empty ranges are errors, and a captured range records its actual final line rather than merely its requested endpoint. The engine derives compact observed facts from successfully captured Python declarations, signatures, and imports; an incomplete range falls back only to verbatim declaration/import lines. These facts retain the same path/hash/range, are checkpointed in working memory, and are presented before model-proposed findings and call history. Complete read line intervals are consolidated by path and source hash; overlapping source is emitted once while retaining non-overlapping portions. The prompt separately describes persisted coverage and the smaller set currently visible under its budget, so a context excerpt is not represented as a complete source read. Re-reading remains permitted when new evidence warrants it.

The engine must distinguish:

- **Observed:** file exists, tracked/untracked changes, command returned code 0, test output recorded.
- **Inferred:** model assessment that a change meets a requirement.
- **Unknown:** behavior not tested or evidence unavailable.

Where semantic correctness cannot be established automatically, report that limitation in `unresolved`. Do not declare `Risks: none` merely because the model wrote it.

## 9. Conversation, execution, and memory

Three separate scopes:

| Scope | Contains | Authority |
|---|---|---|
| Conversation | User requests, preferences, decisions, short verified result summaries | User intent and interaction history |
| Execution state | Current plan, progress, tools, changes, checks, graph checkpoints | Authority for this run's actual progress |
| Long-term memory | User-confirmed decisions and observed, completed-run evidence | Advisory; may be stale |

### Variante B / multi-project core, index, and browser controls (steps 1–2 and 5)

The same local SQLite file now has a `projects` table with a UUID `project_id`, name, absolute workspace, description, supplemental system prompt, and Ollama configuration (`model`, `temperature`, `top_p`, `top_k`, `num_ctx`). Conversations, runs, and durable-memory rows carry an optional `project_id`; project identity, rather than workspace text, separates projects that intentionally use the same directory. A run snapshots the selected project's model options and supplemental prompt before the supervisor starts, so later project edits do not alter an active run or recovery.

Schema changes are additive and tracked by a small SQLite schema marker. On the first upgrade, the store creates one legacy project for each unambiguous historical workspace, assigns runs and workspace-scoped memory, and assigns a conversation only if its workspace and all linked run workspaces agree. Ambiguous or workspace-less conversations remain legacy/unassigned; run IDs and checkpoints are untouched. Empty projects may be deleted, but a project with historical conversations, runs, or memory is rejected rather than cascading data deletion.

The same database also keeps a project-local repository index. `project_index_files` stores only path, SHA-256, language, size, line count, and modification time; `project_index_symbols` and `project_index_imports` record Python `ast` output and simple local-module links. Source text remains in the workspace and `read_file_range` remains the only source-retrieval path. Typical metadata, virtual environments, dependency trees, caches, and generated-output directories are excluded from ordinary indexing. The index uses content hashes to avoid reparsing unchanged files, removes vanished paths, records Git branch/HEAD when available, and is refreshed on project-run start/end and API index queries. Two projects sharing one workspace retain separate index rows keyed by `project_id`.

The index adds neither a second state store nor new LangGraph nodes. The existing vanilla-JavaScript browser sidebar selects or creates a project, edits its stored configuration, filters its chats and project memory, and exposes index status, refresh, and simple file/symbol search. Legacy/unassigned chats remain accessible through a separate selector state. Project-specific prompt text supplements the fixed Sentinel technical contract; it cannot replace tool definitions or execution rules.

### Variante B / hybrid run working memory (step 3)

The existing checkpointed `AgentState` contains a small per-run work record: `phase` (`explore`, `plan`, `implement`, or `verify`), `current_task`, confirmed `facts`, model `decisions`, and `information_gaps`. Sources remain in progress with path, hash, range, and text; changes and verification remain in their existing progress fields. Existing checkpoints receive this compact shape when resumed.

Structured model decisions may add a current task, decisions, and information gaps; these stay `proposed`, even when a model phrases them as facts. A single engine observation path turns successful or failed tools into compact confirmed facts: read-derived declaration/signature/import facts, search locations, repository-index metadata, file changes, checks, and concrete information gaps. Their kinds remain distinct, while full source remains only in the hash- and range-bound source list. Source-linked facts carry path and SHA-256; a write invalidates affected sources, facts, and gaps before its changed-file fact is recorded. The prompt orders current task and phase, selected source excerpts, confirmed facts and decisions, information gaps, verification, then compact tool metadata. It selects a few task-relevant contiguous source ranges, so overlapping reads do not divide one affected function into many prompt fragments. A no-match search is retired once later source evidence supersedes it. The recorded phase changes only after successful tool evidence (`explore` for structural orientation, `plan` for focused research, `implement` after a write, and `verify` after a relevant check); a focused read retains `implement` when a concrete implementation decision is already pending. This is per-run state, is included in the existing checkpoint, and adds no database or new graph path.

### Variante B / index-guided Sentinel navigation (step 4)

At `agent_prepare`, a project run reads a compact repository map from the persisted index exactly once and stores it in the checkpointed agent state. It contains index status, task-ranked file metadata, task-matched Python symbols, and imports of the selected files; it never contains full source. The dispatcher adds project-scoped `index_status`, `index_files`, `index_symbols`, `index_file_symbols`, and `index_imports` actions. These remain metadata/navigation operations; `read_file_range` retrieves actual source. The agent contract directs exploration to use this map and focused index queries, planning to identify affected symbols/dependencies, implementation to write once evidence is sufficient, and verification to run actual checks. A successful write refreshes the same project index so later navigation sees current hashes. The project ID is passed to the supervisor and restored for recovery, while legacy workspace-only runs simply have no index map.

P03 keeps long-term memory in the existing SQLite file, rather than adding Basic Memory, embeddings, or a vector store. The repository has no existing RAG stack and no measured retrieval need that warrants those dependencies. Revisit this only if scoped lookup no longer satisfies a concrete user need.

Memory retrieval is **on demand**, when a run starts, scoped to its stable project ID; legacy runs retain workspace-scoped lookup. Repository and environment facts must be rechecked when correctness depends on their current state. Memory writes are deliberately narrow:

- An explicit browser/API action can store a `user_decision` with `user_confirmed` verification.
- A `completed` run can store one `verified_result` with `observed` verification. Its content is a mechanical summary of the saved verification level, changed-file evidence, and check count.
- Model/assistant prose, ordinary messages, transient graph state, checkpoints, and unsuccessful or partial completion claims are never auto-promoted to durable memory.

Each memory row records its optional project ID/workspace and source conversation/run IDs, kind, verification label, and creation time. The API offers legacy-compatible `GET/POST /memory` and project-scoped memory routes; the browser uses the selected project's route and retains the workspace-based action for legacy history. Memory stays advisory and source facts outrank it.

Conversation history should not be reconstructed by concatenating model-generated success claims. Store status and evidence references separately, and supply only the relevant context to new runs.

<!-- M3 ACCEPTANCE CHECKPOINT (2026-10-18) -->
### M3 implementation and acceptance checkpoint — 2026-10-18

- **A01 (FastAPI/run control):** Local FastAPI starts successfully. `POST /runs` and `GET /runs/{run_id}` were exercised using the existing Windows supervisor and LangGraph engine; the API returned a terminal `completed` result and process exit code 0. A separate request to `POST /runs/{run_id}/cancel` transitioned from `cancelling` to `cancelled`; the observed Git working-tree status remained unchanged. The API cancellation exercise did **not** independently verify termination of each descendant; that was separately verified by the M2 R04 synthetic timeout probe.
- **A02 (models/projects):** `GET /models` returned the Ollama-installed models `qwen3.6:35b`, `qwen3.8-q5:latest`, and `qwen3:8b`. `POST /projects/validate` confirmed a temporary local Git repository. An API-triggered task used the requested `qwen3.6:35b` model.
- **A03 (events/SSE):** `GET /runs/{run_id}/events` returned 13 structured SSE events, including `run_started`, `phase_changed`, `model_activity`, `files_changed`, `verification_completed`, and `run_completed`. A real Git diff added `subtract(a, b)` to the sample repository; verification was **structural** (Python syntax + Git evidence), **not** a runtime functional test of that specific change.
- **Identity caveat:** The public API run ID and engine-generated provenance `run_id` differ. The event stream carries the public `api_run_id` alongside engine metadata; agree on one canonical externally visible ID and retain an internal ID only when useful before M4.
- **Recovery/retention caveat:** A01 retains run details in memory; completed history does not survive an API restart. Persistent storage and recovery are M5 scope, not delivered in M3.
- **Acceptance boundaries:** The observed API cancel flow completed quickly, but the remaining exceptional cancellation/restart paths and all prospective engine verification scenarios have **not** been exhaustively tested. The architecture sections below describe targets where not already implemented.

## 10. UI, API, and streaming

### Selected frontend: lightweight custom browser UI

Build and maintain a small **local HTML/CSS/JavaScript interface** served by FastAPI. No Chainlit, separate frontend server, SPA framework, npm toolchain, or hosted service is required. The interface must support:

- Local single-user operation, no mandatory hosted services.
- Chat with session history and selectable Ollama model.
- Project/repository selection and visible active workspace.
- Streaming of current phase, tool activity, verification results, and final status.
- Cancellation that actually stops child processes.
- Readable diffs and evidence links.
- Low maintenance overhead and straightforward installation.

The UI must consume **structured engine events**, not parse human-readable stderr messages:

```text
run_started
phase_changed
model_activity
 tool_started / tool_completed
files_changed
verification_completed
run_completed
```

Event payloads include `run_id`, timestamps, status, and applicable evidence identifiers. FastAPI exposes a small HTTP API for commands and SSE for one-way live run events. The browser renders these events without scraping logs. Engine logic remains independent of the frontend.

## 11. Persistence and recovery

M5/P01 uses a small SQLite database through `reaper_ai.persistence.Store`. The default path is `%LOCALAPPDATA%\REAPER-AI\reaper-ai.sqlite3`; `REAPER_DATA_DIR` can select a different parent directory. It is application data, never a target workspace or repository artifact.

The schema keeps conversations and messages separate from run records. A run links to its selected conversation and stores workspace, model, task, timeout, status, start/finish metadata, exit code, and structured terminal result. At run completion the API records machine-readable engine events (each limited to 32 KB) and corresponding stage-result rows. It also writes a compact factual terminal message based on status/check/file evidence, rather than model success prose. The UI and local API can load this history after restart.

SQLite failures before a run starts reject the request clearly; a post-run history-write failure is exposed on the live run as `storage_error` without replacing the observed engine outcome. Raw supervisor logs are transient and are not promoted to an unbounded database archive.

For a follow-up in the selected conversation, the API supplies a bounded context made only from prior user requests and structured, observed run metadata; stored assistant/model prose is excluded and repository facts must still be rechecked.

P02 extends the same SQLite store with one versioned, digest-protected checkpoint per run. The engine writes it only after a stage returns a consistent state and records its stage result alongside the full state, including the compact agent-progress snapshot. The checkpoint states the next LangGraph node, allowing a recovery to begin there rather than replaying completed stages. On startup, stale `running` or `cancelling` database rows become visibly `interrupted`; they remain idle until the user explicitly calls the recovery API or presses the UI control.

Recovery accepts only `interrupted`, `cancelled`, or `timed_out` runs with a valid checkpoint. It validates format, integrity digest, workspace identity, and the saved source/write preconditions before invoking the saved next stage. A missing, corrupt, incompatible, or inconsistent checkpoint is surfaced as non-recoverable, never silently substituted or replayed. P02 is intentionally not general checkpointing infrastructure or automatic process restoration. P03 durable memory remains separate from checkpoint data and never uses it as knowledge.

## 12. Operational boundaries

- Windows 11 is the primary target.
- Single local user and local Ollama models; no cloud LLM dependency.
- Project repositories can be outside the application directory.
- Clearly distinguish **application installation**, **application data**, and **user project workspace**.
- Installation and dependency versions should be reproducible; avoid copying LangGraph source into REAPER-AI.
- The application must still handle Ollama unavailability, tool timeout, cancellation, full model context, and malformed model tool calls as explicit failures.

## 13. Scope boundaries

**Included in the initial architecture:** one reliable execution path for coding and analysis, structured state and evidence, Git/file/command tools, bounded verification/repair, local Ollama, a minimal operator interface, and truthful results.

**Not initially required:** parallel agents, autonomous background schedules, multi-user access, enterprise permissions, remote workers, distributed execution, plugin marketplace, cloud observability, vector infrastructure, or an elaborate project management system.

Potential future capabilities are evaluated only after the core can complete representative coding tasks reliably.

## 14. Open architecture decisions

The following items are intentionally **not settled** by this document:

1. **Inference adapter:** direct Ollama client or LangChain-compatible adapter, based on reliable tool-call behavior with the selected models.
2. **Execution isolation:** CLI engine runs under a managed Windows child-process supervisor; synthetic timeout and full process-tree termination have been verified. API-driven cancellation and abnormal-exit paths remain to be integrated and checked in M3.
4. **File editing implementation:** patch-based edit tool and precondition strategy to prevent accidental overwrites.
5. **Verification policy:** task-specific minimum checks and success criteria, without a heavyweight universal test pipeline.
6. **Memory (resolved for initial release):** Basic Memory is deferred. Scoped SQLite memory is sufficient for explicit user decisions and observed completed-run evidence; reconsider only with a measured need for richer retrieval.

These decisions must be resolved before production implementation; they do not block agreement on the execution architecture.

## 15. Acceptance criteria for the architecture

The architecture is viable only if a prototype can demonstrate all of the following with real local models and repositories:

1. A focused coding request produces a real, attributable diff and task-appropriate verification.
2. A model cannot claim implementation success without observable evidence.
3. Nonproductive inspection is bounded and ends with a meaningful status, not uncontrolled token consumption.
4. Read-only analysis completes correctly without forced edits.
5. A failed check triggers a bounded correction attempt or an explicit failure.
6. Existing user modifications are protected and never silently counted as new work.
7. Cancellation stops the executing process tree; subsequent runs start cleanly.
8. User-visible progress reflects engine events, not speculative model statements.

---

**Architectural direction:** Build REAPER-AI from scratch around embedded **LangGraph + local Ollama + evidence-producing tools**. The custom lightweight UI, local FastAPI/SSE boundary, and single-file SQLite persistence are selected and implemented. Do not recreate the v0.x free-running stage runner.

## Implementation status — M4, M5/P01–P03, and M6/P01–P06

The lightweight browser UI is implemented with HTML, CSS and vanilla JavaScript, served locally by FastAPI.

Verified in a real browser coding run:
- Git workspace validation and Ollama model selection.
- Coding task execution through the API and LangGraph engine.
- Structured SSE progress events and terminal run status.
- Display of structural verification results and current Git diff.

Limitations:
- Structural verification does not establish functional correctness.
- The displayed Git diff reflects the current workspace, not an immutable run snapshot.
- Browser cancellation and reconnection have not been separately verified.
- Persistent sessions, completed run history, explicit stage-checkpoint recovery, and scoped durable memory are implemented in SQLite (M5/P01–P03).
- Run-result presentation and public run-ID consistency require refinement.

M5/P01–P03 verification is covered by focused storage/API tests for conversation/message/run association, persisted stage results/events, checkpoint integrity, stale-run detection, restart from the checkpoint's next stage, and durable-memory scope/source/restart behavior. M6 has focused dispatcher/agent tests for file list/read/range/search/write, a failed PowerShell content check followed by guarded correction and successful recheck, recovery from the saved next decision without re-running tools, absolute-path writing, Git status, Python discovery, and PowerShell output/nonzero/timeout handling. A live `qwen3:8b` read-only run searched `def dispatch_tool`, read `src/reaper_ai/agent.py` lines 152–180, and completed with two successful observed tool results and no Git change. This does not claim general process restoration or semantic fact retrieval.
