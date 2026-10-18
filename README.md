# REAPER-AI

**Local, single-user AI coding agent.** REAPER-AI turns engineering requests into observable work: it iteratively inspects files, makes changes, runs checks, and reports the evidence actually produced. A model assertion is never treated as proof of success.

> **Status:** Development work after the v0.2.0 FastAPI/UI baseline adds M5/P01 history, M5/P02 explicit checkpoint recovery, M5/P03 scoped durable memory, and an iterative M6 tool agent. The optional external Basic Memory integration is deferred.

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

Choose any existing absolute project directory, an installed Ollama model, and a bounded timeout in the browser. Git is an optional inspection tool, not a run precondition. The workspace supplies initial project context; tool paths may also be absolute and are subject to the started user's operating-system permissions. REAPER-AI never elevates permissions. It does not automatically commit, push, tag, publish, or release.

## Iterative agent and tools (M6)

`src/reaper_ai/agent.py` provides the serializable state and central dispatcher for Sentinel's single iterative LangGraph path. It uses file/Git adapters in `tools.py` and system adapters in `system_tools.py`; it does not introduce another tool implementation, service, or database.

Dispatchable actions are file listing/searching, UTF-8 file and line-range reads, text search, guarded text writes, Git status/diff, Python-installation discovery, and shell commands. Every result has `ok`, exit code where applicable, stdout/stderr or structured data, duration, change evidence, and a precise error. Captured command output remains bounded so observations fit the model context.

Commands and tools execute with the started user's ordinary operating-system permissions—there is no automatic elevation, shell allow-list, command blacklist, risk class, or approval workflow. Timeouts and supervisor cancellation remain active. Existing text files must be read first and written with their returned SHA-256 hash; a changed source causes a conflict instead of an overwrite.

The active graph is `Prepare → Decide → Execute → Observe → Decide … → Finalize`. Ollama returns a JSON action or final-report decision; the engine supplies project context, dispatches deterministically, and stores the normalized result as an observation. A failed tool call becomes the next decision's evidence rather than ending the run. Completion requires at least one successful tool result; a final model statement remains an assessment, not independent verification. The prior static coding graph is retained only for historical code/checkpoint compatibility and is no longer an executable API or CLI mode.

## Local history

M5/P01 uses Python's built-in SQLite support; no server, ORM, or background service is required. By default, the database is stored at `%LOCALAPPDATA%\REAPER-AI\reaper-ai.sqlite3`. Set `REAPER_DATA_DIR` to choose another **directory**; `reaper-ai.sqlite3` is created within it. Application data stays outside both this source checkout and target workspaces. SQLite files are ignored by Git.

The database stores conversations, user and factual terminal-result messages, run metadata/statuses/results, and bounded structured engine events with corresponding stage-result entries. The browser's conversation selector restores saved conversations and terminal results after an API restart. `GET /conversations`, `GET /conversations/{id}`, and `GET /runs/{run_id}` expose the same records for local tools.

Raw supervisor logs remain temporary diagnostics and are not retained as a durable log archive. Stored events are capped at 32 KB each. A follow-up run in the selected conversation receives only a bounded set of prior user requests and recorded, observed run results; it never replays assistant/model prose and re-checks repository facts.

## Durable memory

P03 extends the same SQLite file with small, scoped durable-memory records that survive conversations and API restarts. The implementation deliberately does **not** add Basic Memory, embeddings, or a vector database: this repository has no existing RAG stack and there is no measured need beyond workspace-scoped SQLite lookup.

Only two kinds of knowledge are retained:

- An explicit **user-confirmed decision** submitted through the browser's **Projektwissen / Merken** action or `POST /memory`.
- One **observed result** for a completed run: a mechanical summary of its persisted verification level, changed files, and number of checks.

Assistant/model prose, ordinary chat messages, checkpoint state, and unsuccessful or unconfirmed completion claims are never automatically saved as verified knowledge. Memory is fetched only as a run begins, for the selected workspace plus deliberately global user decisions, and is marked advisory: repository facts must still be checked. `GET /memory?workspace=...` lists the applicable stored records; `POST /memory` accepts an explicit `content` and optional `workspace`.

## Checkpoint recovery

After each completed, nonterminal engine stage, REAPER-AI replaces a versioned SQLite checkpoint containing the full execution state and that stage's result. On API startup, stale `running`/`cancelling` runs are marked `interrupted`; nothing restarts by itself. The browser shows a recovery status and an explicit **Ab … fortsetzen** button for an interrupted, cancelled, or timed-out run with a valid checkpoint. The same operation is available via `GET /runs/{run_id}/recovery` and `POST /runs/{run_id}/resume`.

Recovery starts LangGraph at the checkpoint's next stage, so completed actions are not rerun. The agent saves the same SQLite checkpoint after `agent_prepare`, `agent_decide`, `agent_execute`, and `agent_observe`; a resumed agent starts at the stored safe stage with its plan, observations, and pending action intact. A missing, corrupt, incompatible, or workspace-inconsistent checkpoint is reported as unavailable or fails safely after the explicit request; it is never guessed or replayed automatically. Historical static-pipeline checkpoints remain stored but are not executable by the active iterative agent.

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

## Project documents

- [ARCHITECTURE.md](ARCHITECTURE.md) — architecture and persistence boundaries.
- [ROADMAP.md](ROADMAP.md) — milestones and acceptance criteria.
- [CONTRIBUTING.md](CONTRIBUTING.md) — contribution guidelines.
- [SECURITY.md](SECURITY.md) — local-execution considerations.

## License

[MIT](LICENSE).
