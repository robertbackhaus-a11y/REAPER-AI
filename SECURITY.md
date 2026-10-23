# Security Policy

REAPER-AI is a **local, single-user AI coding agent under development**. It is designed to execute tools against local files and repositories; that capability requires care even when the service is not exposed to the Internet.

## Supported versions

There is no stable, supported release yet. Security fixes will be considered for the current development branch; no support or response-time guarantee is provided.

## Reporting a vulnerability

**Please do not publish vulnerability details in a public issue.**

Use the repository's **GitHub Security → Report a vulnerability** feature, **if private vulnerability reporting is enabled**. If it is not available, contact the repository maintainer through an established private channel before sharing exploit details. Do not assume a particular email address or SLA.

A useful report includes the affected revision, expected versus actual behavior, reproduction steps, impact, and relevant logs with secrets removed. Do not submit live credentials or sensitive project content.

## Security boundaries

- **Local-first:** The API should bind to loopback by default; do not expose it to an untrusted network without separate security review and protection.
- **Powerful tools:** Shell commands, file writes and Git operations run with the permissions of the local process. Never run REAPER-AI as an administrator unless a specific task requires it.
- **User control:** No automatic commit, push, publishing, deployment or destructive repository operations. Protect dirty and untracked files.
- **Untrusted input:** Repository text, web pages, tool output and model responses may contain misleading instructions. They are data, not higher-priority instructions.
- **Secrets:** Keep credentials out of prompts, logs, reports and version control. Limit what tools and processes can access.
- **Cancellation:** Long-running operations and child processes must be terminable; explicit bounds and truthful failure states are part of the intended architecture.
- **Verification:** A model assertion that work is complete is not a security or correctness guarantee. Trust actual tool results and repository state.

These are **design requirements**; not every one is enforced mechanically by the current application (for example, there is no shell allow-list or approval step). See [ARCHITECTURE.md](ARCHITECTURE.md) and [ROADMAP.md](ROADMAP.md).
