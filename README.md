# REAPER-AI

**Local, single-user AI coding agent.** REAPER-AI turns bounded engineering requests into observable work: it inspects a selected Git repository, makes focused changes, runs checks, and reports the evidence actually produced. A model assertion is never treated as proof of success.

> **Status:** Development work after the v0.2.0 FastAPI/UI baseline adds M5/P01 history, M5/P02 explicit checkpoint recovery, M5/P03 scoped durable memory, and M6/P01 system tool integration. The optional external Basic Memory integration is deferred.

## Requirements and installation

Windows 11, Python 3.12+, Git, and Ollama with at least one installed model are required.

```powershell
git clone https://github.com/robertbackhaus-a11y/REAPER-AI.git
cd REAPER-AI
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
ollama pull qwen3.6:35b
.\.venv\Scripts\python.exe -m uvicorn reaper_ai.api:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000/>. For development, install with `pip install -e .` instead.

Choose an absolute Git repository root, an installed Ollama model, and a bounded timeout in the browser. REAPER-AI does not automatically commit, push, tag, publish, deploy, or delete repository data.

Each run inventories all regular tracked project files without a file-size or file-count cutoff. Metadata marks files as UTF-8 text or binary; binary files stay visible for analysis but cannot be selected for text editing. The model receives a relevance-ranked manifest for selection, while explicitly named tracked paths remain selectable. Selection, source analysis, and edit proposal are separate stages: selected UTF-8 files are retained for guarded writes, but the proposal sees only targeted functions, classes, or line/offset sections. `REAPER_MODEL_CONTEXT_CHARS` bounds that prompt (36,000 UTF-8 bytes by default; clamped to 8,000–96,000). The analysis stage first reserves a fitting section for every selected file, then adds the most relevant sections. A run may make up to 12 precise, non-overlapping text edits. Python and TOML are syntax-checked before writing; workspace, path, clean-Git-state, exact-source, and file-specific-diff checks remain in force.

## System Tools

M6/P01 integrates local system tools into the execution engine. `src/reaper_ai/system_tools.py` provides:

- `run_shell_command`: Executes controlled shell commands (PowerShell on Windows, `/bin/sh` elsewhere) with timeout, environment override, and capture of stdout, stderr, and exit codes.
- `read_global_file` / `write_global_file`: Access files outside the Git workspace with safety checks.
- `find_python_executables` / `create_python_env`: Manage Python environments.

These tools are available to the engine for verification and auxiliary tasks, allowing Sentinel to execute system-level operations rather than just generating code.

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

Recovery starts LangGraph at the checkpoint's next stage, so completed stages are not rerun. Before source analysis, a proposal, apply, repair, or verify continuation, the engine checks the checkpoint format/digest, workspace, original source contents, creation paths, and (for verification) expected written contents. A missing, corrupt, incompatible, or workspace-inconsistent checkpoint is reported as unavailable or fails safely after the explicit request; it is never guessed or replayed automatically. This is controlled continuation, not a general pause/resume service.

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

These tests cover history, durable-memory scope/source/restart behavior, checkpoint persistence/integrity, stale-run detection, explicit recovery availability, continuation from the next stage without rerunning the completed proposal stage, and targeted multi-file editing when the selected source exceeds the model-context budget.

## Project documents

- [ARCHITECTURE.md](ARCHITECTURE.md) — architecture and persistence boundaries.
- [ROADMAP.md](ROADMAP.md) — milestones and acceptance criteria.
- [CONTRIBUTING.md](CONTRIBUTING.md) — contribution guidelines.
- [SECURITY.md](SECURITY.md) — local-execution considerations.

## License

[MIT](LICENSE).
