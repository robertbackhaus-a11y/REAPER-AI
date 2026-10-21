# REAPER-AI — Development Roadmap

**Baseline:** v0.9.1 · 21 October 2026 · Development direction through v1.0

REAPER-AI has completed M0–M6, multi-project support, checkpoint recovery, persistent memory, the web interface, and native MCP integration. The next work therefore concentrates on making Sentinel a substantially more reliable autonomous coding agent instead of expanding the feature set.

## 1. Release overview

| Release | Status | Focus |
| --- | --- | --- |
| v0.8.0 — Native MCP | Released | Existing functional baseline |
| v0.9.0 — Browser editor | Released | Project file navigation and hash-guarded browser editing |
| v0.9.1 — Sentinel evidence handling | Released | S02/S03 source selection, tool evidence, model assessments, and open questions |
| v0.10.0 — Coding Quality | Planned | Multi-file implementation, verification, recovery |
| v0.11.0 — Workflow & Efficiency | Conditional | Only improvements justified by measured coding tasks |
| v1.0.0 — Stable Autonomous Agent | Target | Reproducible, reliable daily use |

These versions are proposed milestones, not release commitments. In particular, v0.11.0 may be unnecessary if earlier work already meets the acceptance criteria.

## 2. Prioritized development matrix

| Priority | Work package | Objective | Source of requirement |
| --- | --- | --- | --- |
| P0 | Decision quality | Reduce unsupported technical claims | D01 Qwen3.8 |
| P0 | Progress awareness | Recognize satisfied information gaps and change strategy | Devstral D01 |
| P0 | Context selection | Keep multiple relevant functions visible | Token analysis |
| P1 | Multi-file coding | Reliably change related modules | Agent comparison |
| P1 | Verification and correction | Detect and repair real implementation errors | Coding acceptance |
| P1 | Recovery in practice | Continue interrupted coding work correctly | Existing checkpoints |
| P2 | Context efficiency | Reduce tokens without reducing quality | Token experiments |
| P2 | Skills | Reuse task-specific instructions only if needed | Goose / Pi comparison |
| P3 | Developer integrations | Improve CLI or IDE workflow only where useful | Competitive analysis |

Capability gaps take precedence over convenience features.

## 3. v0.9.1 — Sentinel Reliability

**Goal:** Improve Sentinel decision quality without adding another agent, model-specific workarounds, or additional orchestration.

| ID | Task | Expected result |
| --- | --- | --- |
| S01 | Decision-trace analysis | Completed: distinguish missing context, incorrect reasoning, and insufficient investigation |
| S02 | Source-context selection | Completed: prioritize individually relevant source ranges without changing checkpoint or source-coverage formats |
| S03 | Progress and information gaps | Completed: retain observed tool facts separately from model assessments, evidence needs, and open questions |
| S04 | Evidence-based findings | Separate verified defects, plausible risks, and intended design decisions |
| S05 | D01 acceptance | Demonstrate measurable improvement without regression |

S02 does not distribute the context budget blindly: source ranges remain relevance-ranked and non-overlapping. S03 does not authorize arbitrary read limits or forced strategy changes; it records evidence needs and preserves the distinction between observed tool facts and model interpretations. The current model comparisons show that these mechanisms improve traceability but do not guarantee that every model will emit structured assessments, avoid verification repeats, or reach a correct completion.

### Acceptance criteria

- Technical execution: grade 2 or better.
- Factual analysis: target grade 2 and demonstrably better than the current grade-4 baseline.
- No loss of autonomous operation.
- No newly introduced repeated-tool loops.
- No significant increase in model calls without a demonstrated quality benefit.
- Repeat the relevant evaluation in more than one run before declaring improvement.

Retain only changes supported by decision-trace evidence.

## 4. v0.10.0 — Autonomous Coding Quality

**Goal:** Move from successful individual coding demonstrations to dependable everyday coding tasks.

| ID | Task | Expected result |
| --- | --- | --- |
| C01 | Multi-file changes | Consistent implementation across related modules |
| C02 | Change verification | Verify actual behavior rather than accepting model claims |
| C03 | Failed implementation recovery | Correct errors through the existing tool and checkpoint mechanisms |
| C04 | Existing changes | Preserve unrelated working-tree modifications |
| C05 | Coding acceptance | Three representative real coding tasks |

Suggested acceptance tasks:

1. Correct a small functional bug and demonstrate the fix.
2. Implement a feature affecting two or three existing modules.
3. Refactor a small component without changing externally observable behavior.

Each task must use the normal REAPER toolchain and Qwen3.8-Q5, without a special-case prompt designed solely to pass the acceptance. A successful autonomous coding run exists in the v0.8.0 baseline; this milestone is about repeatability, not a new execution mechanism.

## 5. v0.11.0 — Workflow and Efficiency

This optional release exists only if practical use reveals worthwhile improvements.

- Improve prompt/context efficiency using measured real runs.
- Consider reusable skills only when repeated tasks justify them.
- Improve CLI or IDE workflows where the web UI is inconvenient.
- Resolve the known terminal SSE replay-order inconsistency.
- Evaluate model changes only with the established D01 and coding-quality tests.

No automatic multi-model routing, RAG database, additional agent hierarchy, or elaborate plugin management is planned.

## 6. v1.0.0 — Stable Autonomous Coding Agent

The version number represents a demonstrated level of reliability, not a collection of features.

| Area | Acceptance target |
| --- | --- |
| Autonomous planning and execution | Grade ≤ 2 |
| Technical repository analysis | Grade ≤ 2 |
| Coding changes and verification | Grade ≤ 2 |
| Error correction and recovery | Grade ≤ 2 |
| Local model execution | Stable with the selected reference model |
| MCP | Existing v0.8.0 functionality preserved |
| Persistence and project isolation | Existing functionality preserved |
| Installation | Reproducible Git-based installation |
| Documentation | Matches the released implementation |

The grade targets are evaluation goals, not guarantees that every possible task will succeed.

## 7. Out of scope

REAPER remains a lightweight local single-user coding agent. The following are not roadmap objectives without a concrete demonstrated need:

- Multi-agent platforms and delegation frameworks.
- Enterprise security, roles, or tenant management.
- Separate MCP orchestration.
- Additional databases or services.
- Custom tool and file-type restrictions.
- Large regression suites unrelated to observed failures.
- Cloud dependency or automatic model routing.
- Replacing the existing frontend stack.

## 8. Recommended immediate action

Start with **S01 — decision-trace analysis**. Before changing `engine.py`, use completed model evaluations to establish which failures arise from context visibility, which from model reasoning, and which from insufficient investigation.

A small read-only analysis should produce:

1. The five most consequential incorrect technical claims or failed decisions.
2. Their source evidence and relevant decision-trace entries.
3. A classification of each failure.
4. At most three proposed corrections, ordered by likely value and implementation effort.

S01–S03 are implemented. The next code change, if justified by a measured failure, is S04 or a targeted correction of the observed verification-output retention issue. This keeps the next milestone focused on making the existing Sentinel more reliable rather than making REAPER more complicated.
