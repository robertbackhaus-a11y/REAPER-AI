# REAPER-AI

**Local, single-user AI coding agent — greenfield project, under development.**

REAPER-AI aims to turn software-engineering requests into **observable, verifiable work**: inspect the relevant code, make focused changes, run appropriate checks, correct failures where practical, and report what actually happened. Model claims are not treated as evidence of successful execution.

> **Status:** Architecture agreed; implementation is planned. This repository is not yet a working coding agent. Installation and usage instructions will be added when the corresponding functionality exists.

## Architecture

| Component | Choice |
|---|---|
| Agent workflow | [LangGraph](https://github.com/langchain-ai/langgraph), embedded Python library |
| Local inference | [Ollama](https://ollama.com/) |
| Application/API | Python and FastAPI |
| Web interface | Custom HTML, CSS and vanilla JavaScript |
| Repository operations | Git, file tools and PowerShell |
| Long-term knowledge | Optional; evaluated later, separate from execution state |

The initial goal is **one dependable coding workflow** before additional agents, delegation or advanced memory are introduced. A controlled execution state governs analysis, implementation, verification and bounded correction. The repository, Git state and real command outputs are the source of truth.

## Principles

- **Local and practical:** one user, no cloud platform or enterprise infrastructure.
- **Small changes:** inspect only what is relevant; preserve existing work, including uncommitted and untracked files.
- **Evidence-based completion:** report actual file changes and checks, including limitations and failures.
- **User-controlled Git:** no automatic commit, push, release or deployment.
- **Focused testing:** relevant checks, not test-count targets or large suites for small changes.
- **Minimal dependencies:** use LangGraph rather than building another orchestration framework.

## Project documents

- [ARCHITECTURE.md](ARCHITECTURE.md) — approved target architecture and boundaries.
- [ROADMAP.md](ROADMAP.md) — planned development milestones and acceptance criteria.
- [CONTRIBUTING.md](CONTRIBUTING.md) — contribution guidelines.
- [SECURITY.md](SECURITY.md) — reporting security issues and local-execution considerations.

## Development status

The first milestone establishes the Python project; the next demonstrates an actual **CLI-only LangGraph coding flow** with Ollama and targeted verification. FastAPI, the browser interface and persistence follow once the engine works. See the roadmap for the full sequence.

## License

[MIT](LICENSE).
