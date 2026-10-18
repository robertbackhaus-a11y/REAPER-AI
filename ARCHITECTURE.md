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

**Status:** M0–M4 and M5/P01–P03 implemented with documented acceptance limits; the optional external Basic Memory integration is deferred.
**Scope:** Local, single-user autonomous coding agent  
**Language:** English for repository documentation; user interaction follows the user's language.

## 1. Objective

REAPER-AI is a locally operated engineering agent that can understand a software task, inspect a repository, propose and apply focused changes, execute appropriate checks, correct failures, and report verifiable results. A conversation is its interface; a controlled execution workflow is its core.

The former REAPER-AI v0.x implementation is **not** an architectural baseline. This document describes a new system, not a migration or a retrofit. Experience from the previous implementation informs constraints, but does not require code reuse.

### Design principles

- **Evidence over claims.** A model's statement is not proof that a file changed or a check passed.
- **Deterministic control, model-driven engineering.** Python determines allowed transitions, limits, and evidence collection; the model performs technical reasoning and proposes actions.
- **Local and single-user.** No accounts, tenants, role management, cloud orchestration, or enterprise platform layers.
- **Small, bounded work.** Focused repository inspection, limited execution attempts, and targeted verification rather than repeated full scans.
- **Explicit failure.** A task that cannot be completed must finish with a precise explanation rather than a fabricated success.
- **Minimal dependencies and clear boundaries.** Prefer mature libraries where they replace real infrastructure work; do not recreate an agent framework.
- **No implicit publishing.** No automatic commits, pushes, tags, releases, deployments, or destructive repository operations.

## 2. Architecture decisions

| Area | Decision | Rationale |
|---|---|---|
| Workflow orchestration | **LangGraph (Python library)** | Explicit state, nodes, routing, bounded retries; no homegrown free-running tool loop |
| Local inference | **Ollama** | Local model hosting, selectable models, tool-capable API |
| Core application | **Python** | Direct integration of graphs, tools, Git, subprocesses |
| User interface | **Custom lightweight browser UI (HTML/CSS/JavaScript)** | Full control, minimal dependencies; Chainlit is not used |
| API/server | **FastAPI, embedded local HTTP/SSE service** | Serve the custom UI and expose sessions, runs, projects, cancellation, and structured events |
| Long-term knowledge | **Scoped SQLite durable memory** | Explicit user decisions and observed completed-run evidence; external Basic Memory deferred |
| Source-of-truth | **Repository and tool results** | Current files, Git state, exit codes, and test results outrank memory |
| Conversation/run history | **SQLite via Python stdlib** | One local file, no ORM or server; records conversations, runs, results, and bounded structured events |
| Persistent execution | **SQLite stage checkpoints with explicit recovery** | Continue only after user action from the next verified stage; no automatic replay |
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
- **Run provenance:** A patch adding `run_id`, Git baseline and before/after status metadata was installed. Its emitted fields and semantics **have not been runtime-verified**.
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

### Proposed nodes

1. **Classify / prepare:** Identify task type, bind project root, capture repository baseline, apply safety constraints.
2. **Inspect / plan:** Read the minimum relevant material and produce a concrete next action. A simple task can bypass extensive planning.
3. **Execute:** Apply a focused edit or run a relevant technical operation through tools.
4. **Verify:** Collect actual diff/check evidence, run task-appropriate checks, and compare outcomes with the objective.
5. **Repair (conditional):** Feed concrete verification failures back into focused execution, with a small retry budget.
6. **Finalize:** Produce `RunResult` from state and evidence; persist a concise, accurately labeled result.

Routing is conditional rather than a rigid mandatory chain:

```text
START -> prepare -> inspect/plan
                       |
           +-----------+-------------+
           |                         |
      read-only task              coding task
           |                         |
      verify sources              execute
           |                         |
           |                       verify
           |                         |
           |             failed and retry budget?
           |                  yes -> repair -> verify
           |                  no  -> finalize
           +------------------------> finalize -> END
```

A task may finish early if it is already satisfied or cannot be safely performed. The graph must not force meaningless file edits.

### Repository inventory and edit scope

Inventory records every regular tracked project file as compact path, byte-size, and text/binary metadata; it does not exclude files because they are large, numerous, or written in a language the engine does not recognize. Binary files are visible to the model for analysis, but deterministic validation prevents treating them as UTF-8 text.

The full manifest remains in engine state. The selection prompt uses a relevance-ranked compact subset to fit the model context and explicitly permits a user-named tracked path that is not present in that subset. Once selected, UTF-8 content is read in bounded chunks. A run may make at most 12 concrete text operations and may send at most 36 KB of selected existing text to the proposal model; these are operation/context limits, not inventory limits. Git-clean preconditions, workspace path validation, and file-specific diff evidence remain mandatory.

### Progress and budgets

- Bound **model calls**, execution time, tool output size, and repeated nonproductive inspection.
- Track **meaningful progress** separately from tool-call count: scoped findings, a concrete implementation action, actual file changes, and recorded checks.
- Before spending more inference on inspection, request a compact next-action decision; if nothing new emerges, return an evidence-based failure rather than loop.
- Preserve small, source-linked findings across model-context compaction. Re-read a file only for a specific missing fact or changed version.
- Repair attempts must be limited. A verification failure never silently converts into success.

Budgets are configuration values, not hardwired assumptions that every task needs the same number of steps. They will be tuned against real tasks.

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

### Initial tools

| Tool | Responsibility | Evidence |
|---|---|---|
| `list_files` / `search_code` / `read_file` | Focused repository discovery and bounded file excerpts | Paths, line ranges, source versions |
| `apply_edit` | Narrow edits with file/version preconditions | Changed paths and edit outcome |
| `git_status` / `git_diff` | Compare repository state with captured baseline | Machine-observed changes |
| `run_command` | PowerShell or child process execution with cwd/timeout/output cap | Command, exit code, captured output |
| `run_checks` | Targeted syntax/build/test commands | Exact commands, exit codes, results |

Tools must validate their working directory and report failures explicitly. An output-truncation flag is never interpreted as a complete source read. Tool implementations—not the LLM—record actual invocation data.

### Safety and change policy

- Operate only on explicitly selected project/workspace unless the user authorizes another path.
- Preserve pre-existing dirty changes; capture baseline before the run. Do not confuse existing modifications with this run's work.
- Default to no Git commit, push, tag, release, deployment, package installation, or destructive deletion.
- Require explicit approval for irreversible or high-impact actions.
- Make cancellation terminate the entire process tree, including shell children; use suitable Windows process containment where required.
- A dedicated project working directory is distinct from persistent application data.

## 8. Verification and truthful completion

Verification is a separate engine responsibility, not a model self-assessment.

For coding tasks, capture the pre-run baseline and post-run repository state, including relevant untracked files. Record the actual diff and every check executed. Success requires the expected artifact/change (or justified no-op) plus reasonable task-specific verification. A successful syntax check is not equivalent to satisfying functional requirements.

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

P03 keeps long-term memory in the existing SQLite file, rather than adding Basic Memory, embeddings, or a vector store. The repository has no existing RAG stack and no measured retrieval need that warrants those dependencies. Revisit this only if scoped lookup no longer satisfies a concrete user need.

Memory retrieval is **on demand**, when a run starts, scoped to its workspace plus deliberately global user decisions. Repository and environment facts must be rechecked when correctness depends on their current state. Memory writes are deliberately narrow:

- An explicit browser/API action can store a `user_decision` with `user_confirmed` verification.
- A `completed` run can store one `verified_result` with `observed` verification. Its content is a mechanical summary of the saved verification level, changed-file evidence, and check count.
- Model/assistant prose, ordinary messages, transient graph state, checkpoints, and unsuccessful or partial completion claims are never auto-promoted to durable memory.

Each memory row records its optional workspace and source conversation/run IDs, kind, verification label, and creation time. The API offers `GET /memory` and `POST /memory`; the UI sends an explicit project-scoped decision. Memory stays advisory and source facts outrank it.

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

P02 extends the same SQLite store with one versioned, digest-protected checkpoint per run. The engine writes it only after a stage returns a consistent state and records its stage result alongside the full state. The checkpoint states the next LangGraph node, allowing a recovery to begin there rather than replaying completed stages. On startup, stale `running` or `cancelling` database rows become visibly `interrupted`; they remain idle until the user explicitly calls the recovery API or presses the UI control.

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

## Implementation status — M4 and M5/P01–P03

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

M5/P01–P03 verification is covered by focused storage/API tests for conversation/message/run association, persisted stage results/events, checkpoint integrity, stale-run detection, restart from the checkpoint's next stage, and durable-memory scope/source/restart behavior. This does not claim automatic resumption, general process restoration, or semantic fact retrieval.
