# REAPER-AI

**Local, single-user AI coding agent.** REAPER-AI turns engineering requests into observable work: it iteratively inspects files, makes changes, runs checks, and reports the evidence actually produced. A model assertion is never treated as proof of success.

> **Version 0.5.0:** M5/P01–P03 local history, explicit recovery, scoped durable memory, the Variante-B multi-project core, repository index, hybrid per-run working memory, and index-guided Sentinel navigation are implemented, alongside the completed M6/P01–P06 iterative tool-agent work. P0 adds hash-bound targeted edits; P1 consolidates run working memory.

## Requirements and installation

Windows 11, Python 3.12+, and Ollama with at least one installed model are required. Git is optional.

```powershell
git clone https://github.com/robertbackhaus-a11y/REAPER-AI.git
cd REAPER-AI
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
ollama pull qwen3.6:35b
.\.venv\Scripts\python.exe -m uvicorn reaper_ai.api:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000/>. For development, install with `pip install -e .` instead.

## Ollama configuration

REAPER-AI uses the local Ollama API at `http://127.0.0.1:11434`. Start Ollama before opening the application and install a model with `ollama pull <model>`. The browser lists installed models and starts each run with the selected model. Direct engine runs use `REAPER_MODEL` when set, otherwise the built-in default `qwen3.6:35b`. Sentinel requests JSON decisions with Ollama thinking disabled (`think: false`), because the tested Qwen setup returned more reliable structured decisions that way.

Choose any existing absolute project directory, an installed Ollama model, and a bounded timeout in the browser. Git is an optional inspection tool, not a run precondition. The workspace supplies initial project context; tool paths may also be absolute and are subject to the started user's operating-system permissions. REAPER-AI never elevates permissions. It does not automatically commit, push, tag, publish, or release.

## Iterative agent and tools (M6)

`src/reaper_ai/agent.py` provides the serializable state and central dispatcher for Sentinel's single iterative LangGraph path. Its checkpointed state includes a compact factual progress snapshot: most recent changed paths, recent tool checks with outcomes, open verification points, and recent tool/model errors. It uses file/Git adapters in `tools.py` and system adapters in `system_tools.py`; it does not introduce another tool implementation, service, or database.

Dispatchable actions are file listing/searching, UTF-8 file and line-range reads, text search, guarded whole-file writes, exact guarded text replacements, Git status/diff, Python-installation discovery, and shell commands. `apply_patch(path, expected_text, replacement, expected_hash)` changes one uniquely occurring source fragment without transferring or rewriting the rest of the file. Every result has `ok`, exit code where applicable, stdout/stderr or structured data, duration, change evidence, and a precise error. Full tool results remain persisted, while the planner receives bounded observation excerpts. Ordinary project scans omit Git object storage but `.git` remains explicitly addressable.

Commands and tools execute with the started user's ordinary operating-system permissions—there is no automatic elevation, shell allow-list, command blacklist, risk class, or approval workflow. Timeouts and supervisor cancellation remain active. Existing text files must be read first and supplied with their returned SHA-256 hash; a changed source causes a conflict instead of an overwrite. `apply_patch` also requires that its exact original text occurs once, otherwise it leaves the file unchanged.

The active graph is `Prepare → Decide → Execute → Observe → Decide … → Finalize`. Ollama returns a JSON action or final-report decision; action decisions state why work remains and may name open verification points. Its compact system contract directs the planner to use known evidence first, end analysis when it is sufficient to implement, make minimal changes for development work, verify proportionately, and change strategy after errors or ineffective actions. A write proves only that the write succeeded; an independent successful checking action must name the exact open point in `verification_for` to clear it. A final decision is `completed` only when its evidence satisfies the goal; it can instead report the terminal outcome `partial` with a concrete, observed blocker. The engine prioritizes checkpointed factual progress ahead of historical observations, dispatches deterministically, and stores the normalized result as an observation. A failed tool call, malformed JSON, or invalid decision becomes the next decision's evidence rather than ending the run. Every decision also emits a persisted Decision Trace with the exact prompt hash/size, the presented facts, decisions, gaps, visible source ranges, selected action, and later tool outcome/information gain. When Ollama supplies a separate `message.thinking` field, it is emitted separately as `model_thinking`; absent data is recorded as unavailable and is never invented. Completion requires at least one successful tool result; a final model statement remains an assessment, not independent verification. The former static coding graph has been removed; historical static checkpoints remain in SQLite for record retention but are rejected safely because they have no executable graph.

Successful file and line-range reads are retained once as hash- and position-bound sources in checkpointed progress. Captured reads state whether they are truncated, their original and captured lengths, and the actual retained line span; empty line ranges are returned as actionable errors. Compact tool-confirmed facts such as Python declarations, signatures, and imports reference those sources but do not copy their text. Historical observations retain read metadata, not a second source-text copy. The prompt derives persisted coverage from the retained sources and visible coverage from the excerpts actually sent to Ollama. This retains evidence without forbidding a later reread when the agent needs a different range or detects changed source.

## Local history

M5/P01 uses Python's built-in SQLite support; no server, ORM, or background service is required. By default, the database is stored at `%LOCALAPPDATA%\REAPER-AI\reaper-ai.sqlite3`. Set `REAPER_DATA_DIR` to choose another **directory**; `reaper-ai.sqlite3` is created within it. Application data stays outside both this source checkout and target workspaces. SQLite files are ignored by Git.

The database stores conversations, user and factual terminal-result messages, run metadata/statuses/results, and bounded structured engine events with corresponding stage-result entries. The browser's conversation selector restores saved conversations and terminal results after an API restart. `GET /conversations`, `GET /conversations/{id}`, and `GET /runs/{run_id}` expose the same records for local tools.

## Projects

One SQLite database can now hold multiple local projects. A project has a stable ID, name, absolute workspace, optional description and supplemental Sentinel instructions, plus an Ollama model snapshot (`temperature`, `top_p`, `top_k`, and `num_ctx`). Projects may intentionally share a workspace while keeping their conversations, runs, and durable knowledge separate. New project runs always use the project workspace and persist their model/prompt configuration with the run, including recovery.

`GET/POST /projects`, `GET/PATCH/DELETE /projects/{project_id}`, `GET /projects/{project_id}/conversations`, and `GET/POST /projects/{project_id}/memory` provide the local API. Projects with historical chats, runs, or knowledge cannot be deleted; no records are silently cascaded away. Existing workspace-bound history is migrated once to generated legacy projects where unambiguous; ambiguous conversations remain accessible as legacy history. The browser sidebar selects, creates, edits, and deletes eligible projects, filters chats and project knowledge, and retains legacy chats as a separate selection.

Each project also has an on-demand SQLite repository index. It stores file metadata (relative path, hash, language, size, line count, modification time), Python AST symbols, imports, and optional Git branch/HEAD—not source copies. Scans skip typical metadata, dependency, cache, and generated directories; an unchanged hash avoids a repeat Python parse, while added, changed, or removed files update the project-local index. `POST/GET /projects/{project_id}/index`, `GET /projects/{project_id}/index/files`, `.../symbols`, `.../imports`, and `.../top-python-files` refresh and query it. The final endpoint returns at most five project-scoped Python files with indexed symbols, ordered by symbol count and then path. The run API refreshes a project's index before it starts and after its supervisor ends. The project sidebar shows index status, allows a manual refresh, and performs simple file/symbol searches. Sentinel receives a compact repository map once per run and can query files, symbols, per-file symbols, imports, and index status before requesting targeted source ranges.

Each run also carries a compact, checkpointed working memory: current phase/subtask, confirmed tool facts, model-proposed decisions, and explicit information gaps. Sources, actual changes, and verification state remain in their existing dedicated progress fields. One common observation path records semantic evidence from reads, searches, repository-index queries, file changes, and checks; kinds remain distinct and model notes remain `proposed`. The next decision receives current task, selected source excerpts, confirmed facts, decisions, gaps, and verification before a small metadata-only observation history. Successful evidence advances the recorded phase from orientation through targeted research, implementation, and verification, and a focused read does not demote an already concrete implementation task. The memory is restored on recovery, stays separate from project durable memory, and does not contain another complete source copy.

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
- `GET/POST /conversations` and `GET /conversations/{conversation_id}`
- `GET /memory` and `POST /memory` for explicit durable user decisions
- `POST /runs` (optional `conversation_id`), `GET /runs/{run_id}`, `GET /runs/{run_id}/log`, `GET /runs/{run_id}/events`, `GET /runs/{run_id}/diff`, `POST /runs/{run_id}/cancel`, `GET /runs/{run_id}/recovery`, and `POST /runs/{run_id}/resume`

Starting a run without `conversation_id` creates a new conversation; supplying one appends the user request and resulting factual terminal summary to it. Persistence failures return a clear `503` at request time. If saving history fails only after a child run finishes, the observed engine outcome is still returned and the live response contains `storage_error`; the engine itself is not coupled to SQLite.

## Verification

Run the focused persistence checks with:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

These tests cover history, durable-memory scope/source/restart behavior, checkpoint persistence/integrity, stale-run detection, explicit recovery availability, targeted large-file and conflict handling, and iterative agent behavior (read/search/range/write, external absolute paths, tool-failure correction, checkpointed decision recovery, Git, Python discovery, and shell output/error/timeout).

## Known limitations

- A completed run records observed tool evidence; it does not establish semantic or functional correctness unless the agent ran a suitable check.
- Recovery is explicit. REAPER-AI never restarts an interrupted run automatically, and it rejects missing, invalid, incompatible, or legacy static-pipeline checkpoints.
- Tool execution uses the started user's permissions. There is no automatic privilege elevation, commit, push, tag, release, or deployment.
- The v0.5.0 end-to-end acceptance run `f852b6ce174e` autonomously changed two source files with `apply_patch`, recovered from a cancelled checkpoint, corrected a failed verification script, and verified `GET /projects/{project_id}/index/top-python-files` through FastAPI `TestClient` against real index data. This is one successful coding workflow, not a claim that every coding task is reliably solved.
- The current workspace Git diff is an inspection result, not an immutable per-run diff snapshot.

## Project documents

- [ARCHITECTURE.md](ARCHITECTURE.md) — architecture and persistence boundaries.
- [ROADMAP.md](ROADMAP.md) — milestones and acceptance criteria.
- [CONTRIBUTING.md](CONTRIBUTING.md) — contribution guidelines.
- [SECURITY.md](SECURITY.md) — local-execution considerations.

## License

[MIT](LICENSE).
