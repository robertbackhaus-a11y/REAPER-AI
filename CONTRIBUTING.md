# Contributing to REAPER-AI

REAPER-AI is a small, local, single-user project. Contributions should make it **more reliable or simpler**, not turn it into an enterprise platform.

## Before changing code

1. Read [ARCHITECTURE.md](ARCHITECTURE.md) and [ROADMAP.md](ROADMAP.md).
2. Keep the change within one clearly defined issue or work package.
3. Discuss larger architectural changes before implementing them.
4. Do not assume planned features already exist; [ROADMAP.md](ROADMAP.md) separates released, unreleased, and planned work.

## Implementation rules

- Use Python and embedded LangGraph for the execution engine. Do not introduce a second orchestrator or rebuild LangGraph functionality.
- Keep the local stack lean: Ollama, FastAPI and a custom HTML/CSS/vanilla JavaScript interface. Avoid extra services, containers and frontend build tooling unless clearly justified.
- Make the smallest correct change; avoid unrelated refactoring and unnecessary abstractions.
- Treat repository files, Git status and actual tool results as authoritative. Never substitute model-generated claims for evidence.
- Preserve uncommitted and untracked user work. Do not automatically commit, push, tag, publish or deploy.
- Document changes to behavior, configuration or interfaces in the affected documentation. Keep the README accurate.
- Use English for code and repository documentation; user-facing conversations may follow the user's language.

## Verification: no test marathon

Run **only the focused checks relevant to the change**. Add a permanent regression test when it protects against a concrete failure, not to grow test counts. Broader checks belong to changes with corresponding risk or release validation.

When presenting a change, summarize what changed, which checks were **actually run**, their results and what remains unverified. Don't claim tests passed if they weren't executed.

## Submitting work

Open a focused pull request or issue explaining the problem and proposed solution. Keep changes reviewable and avoid mixing unrelated features. The project owner controls merges, releases and deployment.

## Security

Do not publish secrets, private repository contents or sensitive logs. For vulnerabilities, use the reporting guidance in [SECURITY.md](SECURITY.md), not a public issue containing exploit details.

## License

By contributing, you agree that your contributions are provided under the project's [MIT License](LICENSE).
