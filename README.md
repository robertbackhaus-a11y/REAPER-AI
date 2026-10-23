# REAPER-AI

<p align="center">
  <img src="docs/assets/reaper-ai-v0.9.0-ui-preview.png"
       alt="REAPER-AI Web Interface"
       width="80%">
</p>

**Local, single-user AI coding agent.** REAPER-AI turns engineering requests into observable work: it iteratively inspects files, makes changes, runs checks, and reports the evidence actually produced. A model assertion is never treated as proof of success.

> **Version 1.0.0:** REAPER-AI combines checkpointed autonomous coding with evidence-aware source selection, multi-file changes, self-correction, and recovery. It retains hash-bound targeted edits, repository indexing, scoped durable memory, native MCP discovery and execution, project management for coding and general chats, a guarded browser editor, Git Diff, run status and tool-to-code navigation. Windows and Linux are practically confirmed; macOS uses the same POSIX supervisor implementation but has not been practically tested. This release also adds a configurable Ollama endpoint, corrected terminal SSE replay, and Linux-compatible test paths. The independent C01–C05 coding acceptances are recorded in the [roadmap](ROADMAP.md).

The current version retains the v0.6.0 interface modernization and the D01 completion work: checkpointed source facts from every successful read, context prioritization for current targeted ranges, explicit reconciliation of satisfied read-only verification points, and visible no-information-gain evidence for identical reads. S02/S03 keep observed tool evidence separate from model proposals: an evidence-linked assessment can be supported, refuted, or inconclusive, while a successful read alone never closes a semantic question. Still-open questions and their evidence needs are prioritized with relevant source excerpts and remain explicit at finalization. The browser retains the same HTML/CSS/vanilla-JavaScript stack: projects and chats can be permanently deleted with their owned run history while project/global memory remains scoped correctly, and chat results now use the full available content width. The activity panel displays only recorded run events, decision traces, and available Ollama/log data; it does not invent progress or thinking. Live events, Decision Trace, and Ollama Thinking / runtime log can remain open together in independent, bounded scroll areas so long diagnostic data does not cover adjacent panels.

## Requirements and installation

Requirements:

- Python 3.12 or newer.
- [Ollama](https://ollama.com/) with an installed compatible model. The documented reference model is `qwen3.8-q5:latest`.
- Git only when cloning the repository; an extracted source archive does not require Git.

### Windows (PowerShell)

```powershell
git clone https://github.com/robertbackhaus-a11y/REAPER-AI.git
cd REAPER-AI
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
ollama pull qwen3.8-q5:latest
$env:REAPER_MODEL = "qwen3.8-q5:latest"
.\.venv\Scripts\python.exe -m uvicorn reaper_ai.api:app --host 127.0.0.1 --port 8000
```

### Linux and macOS (POSIX shell)

```sh
git clone https://github.com/robertbackhaus-a11y/REAPER-AI.git
cd REAPER-AI
python3 -m venv .venv  # Python 3.12 or later
. .venv/bin/activate
python -m pip install .
ollama pull qwen3.8-q5:latest
REAPER_MODEL=qwen3.8-q5:latest python -m uvicorn reaper_ai.api:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000/>. For development, replace `python -m pip install .` with `python -m pip install -e .` after activating the virtual environment.

P02 cross-platform acceptance is complete: Windows and Linux are practically confirmed, and macOS is accepted on the shared POSIX implementation. The supervisor retains its Windows Job Object path and starts the engine in a dedicated POSIX session elsewhere; timeout and cancellation terminate that engine process group, including shell commands it launched. P02 verified installation, API start, a completed coding run, cancellation, timeout cleanup, and checkpoint resume after an API restart on Debian 13 under WSL2 with Python 3.13; see the [roadmap](ROADMAP.md) for the recorded results. macOS itself has not been practically tested. On Linux and macOS the default database location is `~/.local/share/REAPER-AI/reaper-ai.sqlite3`. Under WSL2 with mirrored networking, a Windows Ollama server is reachable at the default Ollama address without a Linux installation.

## Ollama configuration

REAPER-AI uses the local Ollama API at `http://127.0.0.1:11434` by default. Set `REAPER_OLLAMA_URL` (for example `http://192.168.1.20:11434`) before starting the API to use another Ollama server (PowerShell: `$env:REAPER_OLLAMA_URL = "http://192.168.1.20:11434"`; POSIX shell: `export REAPER_OLLAMA_URL=http://192.168.1.20:11434`). The API's model list and the engine's model calls both use it; a trailing slash is ignored. Start Ollama before opening the application and install a model with `ollama pull <model>`. The browser lists installed models and starts each run with the selected model. Set `REAPER_MODEL=qwen3.8-q5:latest` for documented direct-engine use; if it is unset, the current engine fallback remains `qwen3.6:35b`. Sentinel requests JSON decisions with `think: false`, `format: "json"`, `stream: false`, `temperature: 0.0`, `top_p: 0.9`, `top_k: 40`, and `num_ctx: 16384`; the tested Qwen setup returned more reliable structured decisions that way.

Choose any existing absolute project directory, an installed Ollama model, and a bounded timeout in the browser. Git is an optional inspection tool, not a run precondition. The workspace supplies initial project context; tool paths may also be absolute and are subject to the started user's operating-system permissions. REAPER-AI never elevates permissions. It does not automatically commit, push, tag, publish, or release.

### Model choice

The current D01 comparison retains `qwen3.8-q5:latest` as Sentinel's primary model: it produced the strongest autonomous result among the three tested local models, although its factual-analysis quality still needs improvement. `qwen3.6:35b` was faster but too shallow, while Devstral Small 2 timed out in an unproductive index loop. The measured configurations, run evidence, limitations, and full evaluation are documented in [MODELCHOICE.md](MODELCHOICE.md).

## Iterative agent and tools (M6)

`src/reaper_ai/agent.py` provides the serializable state and central dispatcher for Sentinel's single iterative LangGraph path. Its checkpointed state includes a compact factual progress snapshot: most recent changed paths, recent tool checks with outcomes, open verification points, and recent tool/model errors. It uses file/Git adapters in `tools.py` and system adapters in `system_tools.py`; it does not introduce another tool implementation, service, or database.

Dispatchable actions are file listing/searching, UTF-8 file and line-range reads, text search, guarded whole-file writes, exact guarded text replacements, Git status/diff, Python-installation discovery, and shell commands. `apply_patch(path, expected_text, replacement, expected_hash)` changes one uniquely occurring source fragment without transferring or rewriting the rest of the file. Every result has `ok`, exit code where applicable, stdout/stderr or structured data, duration, change evidence, and a precise error. Full tool results remain persisted, while the planner receives bounded observation excerpts. Ordinary project scans omit Git object storage but `.git` remains explicitly addressable.

### MCP tools

REAPER-AI can also discover tools from configured Model Context Protocol (MCP) servers through the official Python MCP SDK. Put an optional `mcp.json` beside the application database (normally `%LOCALAPPDATA%\REAPER-AI\mcp.json`), or set `REAPER_MCP_CONFIG` to a different JSON file. A run discovers the servers it may use (see project activation below) once during preparation; unavailable MCP servers are reported in its decision context but do not block local tools. Discovered tools are qualified as `mcp:<server>:<tool>`, with their description and input schema available to Sentinel. Each selected MCP action reconnects through the SDK, calls the tool, and becomes an ordinary observed result with no automatic semantic verification.

**Optional local SearXNG bridge.** `python -m reaper_ai.searxng_mcp` exposes one stdio MCP tool, `web_search(query, limit=5)`, against `http://127.0.0.1/searxng` by default. Set `SEARXNG_URL` for another operator-provided JSON endpoint. It returns only SearXNG result titles, URLs, and descriptions; it does not fetch result pages. Register it as a normal global stdio MCP server and select it for the project that should use it.

```json
{
  "mcpServers": {
    "example-local": {
      "command": "uvx",
      "args": ["example-mcp-server"]
    },
    "example-http": {
      "url": "http://127.0.0.1:8001/mcp"
    }
  }
}
```

`command` starts a local stdio server; `url` selects Streamable HTTP. An optional positive `timeout` (seconds), `env`, and `cwd` may be supplied for a stdio server. No server is bundled or started by REAPER-AI automatically.

**Managing MCP servers in the web UI (P05, v0.11.0).** Settings → *MCP-Server (global)* lists every server in `mcp.json` with its name, connection type (stdio/HTTP), configuration, and enabled state. Servers can be added, edited (including renaming), enabled/disabled, and removed there; *Testen* connects once and shows either the server's tools with name, description, and top-level input parameters (name, type, required) or the concrete connection error. Every change is written directly to the same `mcp.json`, which stays the only source of server definitions; a disabled server is stored as `"disabled": true` on its entry. In the project section of the same dialog, *MCP-Server für dieses Projekt* selects which global servers the project's agents may use; the project stores only these server IDs. A project run discovers and calls only tools of servers that are both selected for the project and globally enabled (a new or never-configured project selects none); this applies equally to coding projects and general chat projects. Legacy chats without a project keep their previous access to all globally enabled servers. Changes apply from the next run: a running run keeps the server definitions it discovered at its start. Renaming or removing a global server updates the project selections accordingly. The API endpoints are `GET /mcp/servers`, `PUT|PATCH|DELETE /mcp/servers/{id}`, and `POST /mcp/servers/{id}/test`; projects accept `mcp_servers` on create and update.

Commands and tools execute with the started user's ordinary operating-system permissions—there is no automatic elevation, shell allow-list, command blacklist, risk class, or approval workflow. Timeouts and supervisor cancellation remain active. Existing text files must be read first and supplied with their returned SHA-256 hash; a changed source causes a conflict instead of an overwrite. `apply_patch` also requires that its exact original text occurs once, otherwise it leaves the file unchanged.

The active graph is `Prepare → Decide → Execute → Observe → Decide … → Finalize`. Ollama returns a JSON action or final-report decision; action decisions state why work remains and may name open verification points. Its compact system contract directs the planner to use known evidence first, end analysis when it is sufficient to implement, make minimal changes for development work, verify proportionately, and change strategy after errors or ineffective actions. A write proves only that the write succeeded; an independent successful checking action must name the exact open point in `verification_for` to clear it. A final decision is `completed` only when its evidence satisfies the goal; it can instead report the terminal outcome `partial` with a concrete, observed blocker. A read-only final decision can explicitly reconcile a satisfied analysis proof with `open_verifications: []`; it does not clear unrelated proof points automatically. The engine prioritizes checkpointed factual progress ahead of historical observations, dispatches deterministically, and stores the normalized result as an observation. A failed tool call, malformed JSON, or invalid decision becomes the next decision's evidence rather than ending the run. Every decision also emits a persisted Decision Trace with the exact prompt hash/size, the presented facts, decisions, gaps, visible source ranges, selected action, accepted model assessments/question updates, and later tool outcome/information gain. An open model question that reaches a final response without an evidence-linked update remains explicitly `inconclusive` in the terminal result rather than being inferred from final prose. When Ollama supplies a separate `message.thinking` field, it is emitted separately as `model_thinking`; absent data is recorded as unavailable and is never invented. Completion requires at least one successful tool result; a final model statement remains an assessment, not independent verification. The former static coding graph has been removed; historical static checkpoints remain in SQLite for record retention but are rejected safely because they have no executable graph.

Successful file and line-range reads are retained once as hash- and position-bound sources in checkpointed progress. Captured reads state whether they are truncated, their original and captured lengths, and the actual retained line span; empty line ranges are returned as actionable errors. Every successful source excerpt also creates a compact observed tool fact for its actual path, hash, and captured range; Python declarations, signatures, and imports add detail when the excerpt is independently parseable. Historical observations retain read metadata, not a second source-text copy. The prompt derives persisted coverage from the retained sources and visible coverage from the excerpts actually sent to Ollama. This retains evidence without forbidding a later reread when the agent needs a different range or detects changed source.

## Local history

M5/P01 uses Python's built-in SQLite support; no server, ORM, or background service is required. By default, the database is stored at `%LOCALAPPDATA%\REAPER-AI\reaper-ai.sqlite3`. Set `REAPER_DATA_DIR` to choose another **directory**; `reaper-ai.sqlite3` is created within it. Application data stays outside both this source checkout and target workspaces. SQLite files are ignored by Git.

The database stores conversations, user and factual terminal-result messages, run metadata/statuses/results, and bounded structured engine events with corresponding stage-result entries. The browser's conversation selector restores saved conversations and terminal results after an API restart. `GET /conversations`, `GET /conversations/{id}`, and `GET /runs/{run_id}` expose the same records for local tools.

Each conversation tab in the top bar has two icons. **Hide** (`eye-off`) only removes the tab from the bar for the current browser session; the conversation, its history, and stored results stay untouched, and hidden conversations can be reopened from the *Ausgeblendet* selector next to the **＋** button (a page reload shows all tabs again). **Delete** (`trash-2`) asks for confirmation and then permanently removes that conversation with its messages, runs, events, and checkpoints through `DELETE /conversations/{id}`, stopping its active run first; the deletion survives restarts. Both actions always target the tab they belong to; when the active conversation is hidden or deleted, the next open tab is activated, or a new draft if none is left.

## Projects

One SQLite database can now hold multiple local projects. A project has a stable ID, name, absolute workspace, optional description and supplemental Sentinel instructions, plus an Ollama model snapshot (`temperature`, `top_p`, `top_k`, and `num_ctx`). Projects may intentionally share a workspace while keeping their conversations, runs, and durable knowledge separate. New project runs always use the project workspace and persist their model/prompt configuration with the run, including recovery.

**General chats and coding projects (P08, v0.11.0).** Every project has a type that is fixed when it is created (`kind`: `coding` or `chat`). A **coding project** owns an absolute workspace and keeps the complete autonomous coding workflow: repository index, file tree, editor, Git diff, project knowledge, checkpoints, Sentinel, and its P05 MCP server selection. A **general chat project** has no workspace and no Git repository; it is meant for model conversation, research, and MCP use and also keeps its own MCP selection, knowledge, and chat history. **Legacy-Chats** (conversations without a project) are general chats as well: they are listed unchanged, need no workspace, and use all enabled MCP servers as before; they are never converted into projects automatically. All existing projects had a workspace and are therefore coding projects; the upgrade only adds the `projects.kind` column with the default `coding`. General chats run through the same Sentinel engine without restrictions on its tools; because the engine needs a working directory, they use the scratch directory `chat-workspace` in the application data directory, never a user project. The sidebar groups the navigation into *Allgemeine Chats* (Legacy-Chats and chat projects) and *Coding-Projekte*; *Neues Projekt* in the settings asks for the type and shows the workspace field only for coding projects. Chat tabs, hiding a tab, and permanently deleting a chat work the same way in both contexts. The Editor and Diff tabs, the file tree, and the repository index are enabled only in coding projects; workspace endpoints (`/projects/{id}/index…`, `/files/content`, `/changes…`) answer `409` for a chat project. `POST /projects` accepts `kind` (default `coding`; `workspace` is required only for it), and `GET /conversations?legacy=true` lists only conversations without a project.

`GET/POST /projects`, `GET/PATCH/DELETE /projects/{project_id}`, `GET /projects/{project_id}/conversations`, and `GET/POST /projects/{project_id}/memory` provide the local API. Deleting an inactive project removes its project-scoped conversations, messages, runs, events, checkpoints, durable memory, and repository index in one SQLite transaction; workspace files and legacy records without a project assignment are never touched. A project with an active run cannot be deleted. Existing workspace-bound history is migrated once to generated legacy projects where unambiguous; ambiguous conversations remain accessible as legacy history. The browser sidebar lists projects and marks the active selection; project editing, workspace/model options, project knowledge, and index controls are grouped under the top-right settings control. Project conversations are shown as closeable, non-destructive tabs above the chat, while legacy chats remain reachable through their own project selection. Closing a tab only hides it. `DELETE /conversations/{conversation_id}` permanently removes that chat's messages, runs, events, checkpoints, and chat-sourced memory; `DELETE /conversations` removes every saved chat while retaining project/global memory. The browser requests cancellation of a matching active run before either deletion.

**Sequential task list.** A coding project has a compact *Aufgaben* list in the sidebar: add, edit, delete, and reorder coding assignments, then **Starten**, **Anhalten**, or **Fortsetzen**. Sentinel works through the open tasks one after another; each task is an ordinary run in its own conversation, so its chat and run history stay linked (select the task title to open them). A task is `open`, `running`, `completed`, or `failed`; completed tasks are never run again. When a task's run ends, the API marks it and starts the next open task; any other outcome than `completed` marks the task failed and halts the list. *Anhalten* starts no further task after the running one (the existing *Abbrechen* stops a run immediately). *Fortsetzen* resumes a failed task from its checkpoint through the existing recovery when the run allows it, otherwise it runs the task again in the same conversation, and then continues the list. Tasks and the list state are stored in the existing SQLite database (`project_tasks`, `projects.task_queue_state`); after an API restart a task that was running is marked failed and the list stays halted until it is resumed. Only one run executes at a time; nothing is committed or pushed. API: `GET/POST /projects/{id}/tasks`, `PATCH/DELETE /projects/{id}/tasks/{task_id}`, `POST /projects/{id}/tasks/order`, and `POST /projects/{id}/tasks/start|pause|resume`.

Each project also has an on-demand SQLite repository index. It stores file metadata (relative path, hash, language, size, line count, modification time), Python AST symbols, imports, and optional Git branch/HEAD—not source copies. Scans skip typical metadata, dependency, cache, and generated directories; an unchanged hash avoids a repeat Python parse, while added, changed, or removed files update the project-local index. `POST/GET /projects/{project_id}/index`, `GET /projects/{project_id}/index/files`, `.../symbols`, `.../imports`, and `.../top-python-files` refresh and query it. The final endpoint returns at most five project-scoped Python files with indexed symbols, ordered by symbol count and then path. The run API refreshes a project's index before it starts and after its supervisor ends. Index status, refresh, and simple file/symbol searches are available in the settings control. Sentinel receives a compact repository map once per run and can query files, symbols, per-file symbols, imports, and index status before requesting targeted source ranges.

The sidebar also renders the selected project's indexed files as an expandable tree. Selecting a text file opens it in the separate editor view, which supports Python, JavaScript, JSON, HTML, CSS, Markdown, and plain text using a small built-in highlighter rather than a frontend framework. Multiple opened files become editor tabs. `GET /projects/{project_id}/files/content` returns UTF-8 content and its SHA-256; `PUT` requires the displayed hash, so a changed file produces a conflict instead of an overwrite. Binary or undecodable files have no text preview, and a truncated large-file view cannot be saved.

A third workspace tab, **Diff**, beside Chat and Editor, shows read-only Git comparisons for the selected project. A compact selector above the diff offers three modes: **Working Tree** compares the current working tree with `HEAD` (tracked changes by default; a switch adds untracked files), **Commit** compares a selected local commit with its first parent, and **Release** compares a selected local tag with `HEAD`, optionally including uncommitted tracked working-tree changes (for example `v0.10.0` against the current development state). The left column lists modified, added, deleted, renamed, conflicted, and untracked files with their M/A/D/R/? marker; each entry shows the file name prominently with its directory below and the full path as a tooltip. Selecting a file shows its colored unified diff on the right, with added, removed, context, hunk, and header lines distinguished. The divider between list and diff can be dragged (or moved with the arrow keys) between 220 px and 70 % of the panel; the width is kept in browser storage. The current comparison refreshes when the tab opens, on its refresh button (which also reloads commits and tags), and after `tool_completed`, `files_changed`, and `run_completed` events while the tab is visible; a historical comparison stays selected and the selected file is kept when it is still listed. Git is the only source: `GET /projects/{project_id}/changes/refs` lists up to 200 local commits touching the workspace and all local tags, `GET /projects/{project_id}/changes?mode=worktree|commit|release&ref=…&untracked=…&worktree=…` lists a comparison's files, and `GET /projects/{project_id}/changes/diff?path=…` (same parameters) returns one file's diff (at most 200,000 bytes, then marked truncated). Only paths listed by the same comparison are accepted, refs are resolved by Git and option-like refs are rejected, and Git runs with `--no-optional-locks`; nothing is staged, checked out, reset, committed, or written. A workspace inside a larger repository lists only changes below the workspace, with repository-relative paths.

Each run also carries a compact, checkpointed working memory: current phase/subtask, observed tool facts, model proposals, and explicit information gaps. Sources, actual changes, and verification state remain in their existing dedicated progress fields. One common observation path records tool evidence from reads, searches, repository-index queries, file changes, and checks; kinds remain distinct and model notes remain `proposed`. An open question names the exact evidence it needs. A model assessment or question update may cite an observed path/hash/range or an observed tool as supported, refuted, answered, or inconclusive, but it remains a model interpretation: a successful read or tool call proves only its recorded result and does not close a question automatically. Tool facts establish only their recorded outcome; a model hypothesis is not promoted automatically, and functional claims still require an appropriate observed check. The next decision receives its current task and still-open questions with their evidence needs before selected source excerpts, tool facts, model proposals, remaining gaps, verification, and a small metadata-only observation history. Open-question terms and already cited source ranges also inform excerpt selection, so generic historical gaps do not displace evidence for the current subtask. Successful evidence advances the recorded phase from orientation through targeted research, implementation, and verification, and a focused read does not demote an already concrete implementation task. The memory is restored on recovery, stays separate from project durable memory, and does not contain another complete source copy.

Raw supervisor logs remain temporary diagnostics and are not retained as a durable log archive. Stored events are capped at 32 KB each. A follow-up run in the selected conversation receives only a bounded set of prior user requests and recorded, observed run results; it never replays assistant/model prose and re-checks repository facts.

## Durable memory

P03 extends the same SQLite file with small, scoped durable-memory records that survive conversations and API restarts. New project records are scoped by stable project ID; legacy records retain their previous workspace scope. The implementation deliberately does **not** add Basic Memory, embeddings, or a vector database.

Only two kinds of knowledge are retained:

- An explicit **user-confirmed decision** submitted through the browser's **Projektwissen / Merken** action or `POST /memory`.
- One **observed result** for a completed run: a mechanical summary of its persisted verification level, changed files, and number of checks.

Assistant/model prose, ordinary chat messages, checkpoint state, and unsuccessful or unconfirmed completion claims are never automatically saved as verified knowledge. Memory is fetched only as a run begins, scoped to the selected project (or workspace for legacy runs), and is marked advisory: repository facts must still be checked. `GET/POST /memory` retain legacy compatibility; project routes provide isolated project knowledge.

## Checkpoint recovery

After each completed, nonterminal engine stage, REAPER-AI replaces a versioned SQLite checkpoint containing the full execution state and that stage's result. On API startup, stale `running`/`cancelling` runs are marked `interrupted`; nothing restarts by itself. The browser shows a recovery status and an explicit **Ab … fortsetzen** button for an interrupted, cancelled, or timed-out run with a valid checkpoint. The same operation is available via `GET /runs/{run_id}/recovery` and `POST /runs/{run_id}/resume`.

Recovery starts LangGraph at the checkpoint's next stage, so completed actions are not rerun. The agent saves the same SQLite checkpoint after `agent_prepare`, `agent_decide`, `agent_execute`, and `agent_observe`; a resumed agent starts at the stored safe stage with its plan, factual progress, observations, and pending action intact. A missing, corrupt, incompatible, workspace-inconsistent, or historical static-pipeline checkpoint is reported as unavailable or fails safely after the explicit request; it is never guessed or replayed automatically.

## API

The local API exposes:

- `GET /health`, `GET /models`, and `POST /projects/validate`
- `GET/POST/DELETE /conversations`, `GET /conversations/{conversation_id}`, and `DELETE /conversations/{conversation_id}`
- `GET /memory` and `POST /memory` for explicit durable user decisions
- `GET /projects/{project_id}/changes/refs`, `GET /projects/{project_id}/changes`, and `GET /projects/{project_id}/changes/diff?path=…` (read-only Git comparisons: working tree, commit, release tag)
- `POST /runs` (optional `conversation_id`), `GET /runs/{run_id}`, `GET /runs/{run_id}/log`, `GET /runs/{run_id}/events`, `GET /runs/{run_id}/diff`, `POST /runs/{run_id}/cancel`, `GET /runs/{run_id}/recovery`, and `POST /runs/{run_id}/resume`

Starting a run without `conversation_id` creates a new conversation; supplying one appends the user request and resulting factual terminal summary to it. Persistence failures return a clear `503` at request time. If saving history fails only after a child run finishes, the observed engine outcome is still returned and the live response contains `storage_error`; the engine itself is not coupled to SQLite.

**Agent status and run summary (P06, v0.11.0).** The right panel *Agentenaktivität* shows the displayed run's status, current activity (for example *Datei lesen*, *Code ändern*, *Tests ausführen*), the last completed tool action with its result, the runtime since start, and the model and tool calls counted so far. It is fed by the existing run event stream (SSE), always belongs to the selected chat, and stays correct after a browser reload and when switching between chats; a run finishing in another chat does not take over the visible panel. After the run, a copyable *Run-Zusammenfassung* lists status and result, recorded changed files, recognized test commands with their results, errors and open points, runtime, and model/tool calls. Only recorded data is shown; anything missing appears as *nicht erfasst*. Live events, Decision Trace, and Ollama Thinking / runtime log remain available below it.

**Tool-to-code navigation (P07, v0.11.0).** In coding projects, file paths in *Live-Ereignisse*, *Decision Trace*, and *Run-Zusammenfassung* are links: selecting one opens the file in the *Editor* tab, jumps to the referenced line, and highlights the affected lines. The *Diff* tab offers *Im Editor öffnen* and clickable diff lines. Only paths inside the selected project's workspace are linked; deleted files show a clear message, and navigation never changes or saves a file.

## Verification

Run the focused persistence checks with:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

On Linux and macOS, run `python -m unittest discover -s tests -v` inside the activated virtual environment of a Git checkout. The suite is Linux-compatible: shell-tool tests use PowerShell commands on Windows and `/bin/sh` commands elsewhere; the web UI helper tests (`test_code_links.py`, `test_run_status.py`) need Node.js and are skipped without it.

These tests cover history, durable-memory scope/source/restart behavior, checkpoint persistence/integrity, stale-run detection, explicit recovery availability, targeted large-file and conflict handling, and iterative agent behavior (read/search/range/write, external absolute paths, tool-failure correction, checkpointed decision recovery, Git, Python discovery, and shell output/error/timeout).

## Known limitations

- A completed run records observed tool evidence; it does not establish semantic or functional correctness unless the agent ran a suitable check.
- Recovery is explicit. REAPER-AI never restarts an interrupted run automatically, and it rejects missing, invalid, incompatible, or legacy static-pipeline checkpoints.
- Tool execution uses the started user's permissions. There is no automatic privilege elevation, commit, push, tag, release, or deployment.
- The v0.5.0 end-to-end acceptance run `f852b6ce174e` autonomously changed two source files with `apply_patch`, recovered from a cancelled checkpoint, corrected a failed verification script, and verified `GET /projects/{project_id}/index/top-python-files` through FastAPI `TestClient` against real index data. This is one successful coding workflow, not a claim that every coding task is reliably solved.
- The current workspace Git diff is an inspection result, not an immutable per-run diff snapshot.
- The Diff tab requires a coding project whose workspace is in a Git repository; general chats (Legacy-Chats and chat projects) and non-Git workspaces show no diff.
- A project's type cannot be changed after creation, and existing conversations cannot be moved between projects. General chats share one scratch working directory (`chat-workspace` in the application data directory); files a chat run creates there are not removed when the chat is deleted. Binary files show only Git's binary notice, the change list is capped at 2,000 files, and the commit selector shows the latest 200 commits. A Release comparison that includes uncommitted changes covers tracked files only, and a file renamed and edited there may appear as a deletion plus an addition. A missed event (for example after an SSE interruption) is caught up only when the tab is reopened or refreshed.
- Model-supplied assessments and question updates remain optional decision fields. Qwen3.8 can complete a correct analysis without emitting them. Shell observations now retain their exit code and bounded stdout/stderr in the next decision context, but this removes one demonstrated information loss; it does not guarantee that every model or task avoids verification repeats. See [MODELCHOICE.md](MODELCHOICE.md) for measured model behavior.
- macOS is accepted on the shared POSIX implementation but has not been practically tested. A hard crash of the API process during a running run has not been practically tested; recovery was demonstrated after cancellation and an API restart.
- A recovered run's terminal usage values currently describe its resumed supervisor process. Cross-process totals remain available in the retained decision traces and event logs; this is a reporting limitation, not a loss of checkpoint state.

## Development, test, and acceptance history

D01 autonomous-agent acceptance was accepted on 2026-10-20 with an overall grade of 3 (technical execution: 2; factual result quality: 4). The fresh read-only run `d01-1792496155` completed without source changes, recovered from invalid range requests, and did not repeat a successful range read. Its line-cited report was externally reviewed; one timeout claim was false and is recorded as a limitation rather than evidence of a new defect. The detailed, evidence-scoped history of earlier read loops, source-fact/context corrections, checkpointed verification reconciliation, focused tests, and remaining limitations is maintained in [ROADMAP.md](ROADMAP.md#reaper-ai--development-test-and-acceptance-history).

C01/C02 development acceptance was recorded with multi-file run `183ee8ae8303`: Qwen 3.8 Q5 made 20 model calls and 19 tool calls in 202.4 seconds, with no identical repeated action. It independently implemented and functionally checked `validate` across the fixture's related modules, preserved the existing `show` output, corrected its own BOM-related negative-test probe, and updated the README. The C01 acceptance is grade 2. C02 was practically confirmed in the same run: the next decision received the successful shell check's exit code, stdout, and stderr rather than only a generic success label. This is one evidence-backed run, not proof of general repeatability.

C03 development acceptance was recorded with interrupted-and-resumed run `8f7670dc9ebf`. Sentinel diagnosed a refund calculation defect, applied a hash-bound patch to `totals.py`, and was then cancelled before completion. Its valid checkpoint resumed the same run at `agent_decide` after a REAPER restart, retaining the patch and outstanding verification work. Sentinel selected `unittest` after its `pytest` attempt exposed a missing dependency, passed both fixture tests, verified the CLI output (`books: 15.00`), updated the fixture README, and completed. The combined retained traces show 17 model calls, 16 tool calls, 104,346 input tokens, 5,053 output tokens, and 147.7 seconds including interruption/restart. One failed pre-patch `pytest` call was repeated once; no sustained loop occurred. **C03: passed, grade 2.** This remains one controlled acceptance case, not proof of universal recovery reliability.

C04 dirty-working-tree acceptance was recorded with `7211e4ac8a5a`. Qwen 3.8 Q5 added a JSON CLI output and documentation while retaining a user edit in the shared CLI file, an unrelated edited file, and an untracked user file. Independent checks passed all 3 tests, confirmed unchanged default text output, and verified the JSON result. The run completed with 16 model calls, 15 tool calls, 102,787 input tokens, 6,031 output tokens, and 148.6 seconds, without identical unproductive tool repeats. **C04: passed, grade 2.** Its final diagnostics still left some already evidenced questions as `inconclusive`; this did not affect the independently verified outcome.

C05 is the aggregate v0.10.0 autonomous-coding acceptance: C01–C04 provide independently checked evidence for multi-file implementation, complete verification context, checkpoint recovery, and preservation of a dirty working tree. **C05: passed, overall grade 2.** This makes the feature scope ready for normal v0.10.0 release preparation, not a claim that every model, repository, or task will behave identically.

## Project documents

- [ARCHITECTURE.md](ARCHITECTURE.md) — architecture and persistence boundaries.
- [ROADMAP.md](ROADMAP.md) — milestones, acceptance criteria, and release history.
- [MODELCHOICE.md](MODELCHOICE.md) — model evaluation and reference-model decision.
- [CONTRIBUTING.md](CONTRIBUTING.md) — contribution guidelines.
- [SECURITY.md](SECURITY.md) — local-execution considerations.

## License

[MIT](LICENSE), Copyright (c) 2026 Robert Backhaus. Python dependencies are installed separately under their own licenses (MIT or BSD-3-Clause for the direct dependencies); none are bundled with REAPER-AI.
