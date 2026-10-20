# REAPER-AI — Model Selection and D01 Evaluation

**Date:** 20 October 2026
**Baseline:** REAPER-AI v0.7.0 (`876fab1`)
**Decision:** Retain `qwen3.8-q5:latest` as Sentinel's primary model.

## 1. Purpose

REAPER-AI is a local, autonomous, single-user coding agent. Selecting its underlying model requires more than comparing code-generation benchmarks. Sentinel must investigate an unfamiliar repository, use tools reliably, maintain its working state across multiple decisions, recognize when information has already been obtained, and produce technically defensible conclusions.

The D01 read-only repository-analysis task was used to compare three locally available Ollama models under the REAPER-AI v0.7.0 agent architecture:

- `qwen3.8-q5:latest`
- `qwen3.6:35b` (35B-A3B)
- `devstral-small-2:24b-instruct-2512-q4_K_M`

These results describe the models **as operated through this specific version of REAPER-AI**. They are not general-purpose model rankings or statistically representative benchmark results.

## 2. Evaluation Criteria

The evaluation considered three areas:

**Technical execution:** autonomous task progression, structured tool selection and execution, recovery from invalid decisions or tool calls, avoidance of unproductive loops, and reaching a terminal result without intervention.

**Technical accuracy:** breadth of repository inspection, correctness of claims about actual implementation, recognition of existing safeguards, and distinction between demonstrated defects, possible risks, and deliberate design decisions.

**Efficiency:** model calls, tool calls, input/output tokens, and elapsed runtime. Lower resource use is beneficial only when an equivalent amount of useful work and adequate quality are delivered.

Grades use the project's five-point scale: **1 = very good; 5 = inadequate**. REAPER-AI's target for core capabilities is **grade 2 or better**.

## 3. D01 Results

| Metric | Qwen3.8-Q5 | Qwen3.6-35B-A3B | Devstral Small 2 24B |
|---|---:|---:|---:|
| Run ID | `d01-1792496155` | `a0051419af3d` | `829bfe76066b` |
| Model calls | 18 | 5 | 120 |
| Tool calls | 17 | 3 | 119 |
| Input tokens | 152,249 | 30,746 | 1,555,309 |
| Output tokens | 4,673 | 2,258 | 19,539 |
| Runtime | 128.9 s | 25.0 s | 600.2 s |
| Terminal status | `completed` | `completed` | `timed_out` |
| Exit code | 0 | 0 | 1 |
| File changes | 0 | 0 | 0 |
| Technical execution grade | **2** | **3** | **5** |
| Technical accuracy grade | **4** | **5** | **5**¹ |
| Efficiency grade | **3** | **1** | **5** |
| Overall grade | **3** | **4** | **5** |

¹ Devstral produced no final technical analysis; the grade reflects failure to deliver an assessable result, not a claim-by-claim review of a report.

The Qwen3.6 and Devstral evaluations were conducted on an isolated v0.7.0 baseline, excluding uncommitted MCP integration changes. No repository files were modified by the agents. Devstral's measured configuration was `temperature=0.15`, `top_p=0.9`, `top_k=40`, `num_ctx=16384`, `think=false`. The configurations are therefore not necessarily identical in all inference parameters.

## 4. Findings by Model

### 4.1 Qwen3.8-Q5 — Current Reference

Qwen3.8 completed D01 autonomously with 18 model calls and 17 tool calls. It examined a wider portion of the repository, recovered from invalid read ranges, avoided repeatedly issuing successful identical reads, and produced a final report.

Its main weakness was **technical accuracy**, graded 4. The report included unsupported or incorrect conclusions about the code, including a claim that Git-related subprocess operations lacked timeouts even though the implementation included them. A successful agent run therefore did not imply a reliable technical assessment.

**Assessment:** Best overall result among the three tested models, but below the project's target for technical accuracy.

### 4.2 Qwen3.6-35B-A3B — Fast, but Insufficiently Thorough

Qwen3.6 completed after only five model calls and three tool calls. It recovered independently from an invalid model decision, but inspected only `src/reaper_ai/tools.py`. A repeated read of lines 276–385 was a verification repeat, not an ongoing read loop.

Its five reported findings did not withstand a satisfactory code-level review:

1. **`git_info` allegedly returned misleading partial success:** Incorrect; an error in either Git command leads to `ok: false`.
2. **`_git_ignored_directories` allegedly suppressed errors:** Partially true but overstated; on failure it conservatively treats no directories as ignored, potentially scanning more files rather than silently excluding them.
3. **`write_file` / `apply_patch` allegedly had a non-atomic window:** A possible theoretical risk, not a demonstrated defect in the intended local single-user environment.
4. **`_workspace_target` allegedly permitted an unintended workspace escape:** Incorrect characterization; the workspace is deliberately project context, not a filesystem sandbox.
5. **`search_text` / `os.walk` allegedly caused symlink cycles:** Incorrect; `os.walk` does not follow directory symlinks by default, and files are closed through context managers.

Qwen3.6 consumed substantially fewer tokens because it performed far less investigation, not because it demonstrably achieved the same result more efficiently.

**Assessment:** Operationally quick, but too shallow and inaccurate for technical repository analysis under D01.

### 4.3 Devstral Small 2 — Failure to Advance Beyond Discovery

Devstral Small 2 was selected for evaluation because of its agentic software-engineering focus. In the actual REAPER-AI v0.7.0 run, however, it did not complete D01.

The initial `index_files(language="python")` call already returned 12 Python files. That list, the observations identifying repeated calls as providing no new information, and a subsequently read excerpt of `engine.py` were visible in the agent decision context.

Despite this, Devstral invoked `index_files` **117 times**; **115** of those results were marked as providing no new information. An outstanding working-state item, “List of Python files in the repository,” remained unresolved. Rather than recognizing that the existing result satisfied this item and moving on to source analysis, the model continued querying the index.

The run reached the timeout at 600.2 seconds, after 120 model calls and 119 tool calls. It read only `engine.py:1–203`, made one `list_files` call, and produced no final technical report.

The decision traces supported a specific diagnosis:

- The initial tool returned the requested file list correctly.
- The relevant observations and repeated-call warnings were available in the model context.
- The repeated actions produced no meaningful additional evidence.
- The model failed to change strategy or recognize the outstanding task as satisfied.

**Assessment:** Failed autonomous task management in this particular agent configuration. The evidence does not establish that Devstral is generally unsuitable for coding agents or incompatible with other orchestration approaches.

## 5. What the Comparison Actually Demonstrates

The evaluation shows that **coding proficiency and autonomous agent proficiency are different capabilities**.

REAPER-AI requires a combination of:

1. **Reliable structured tool calling:** select valid actions, supply valid arguments, and process actual outcomes.
2. **Autonomous multi-step execution:** progress from discovery to inspection, verification, and completion.
3. **Accurate working-state management:** distinguish outstanding questions from facts already established.
4. **Recognition of information gain:** stop repeating an action when the answer is already available and select the next useful step.
5. **Technically defensible code analysis:** verify claims against implementation and distinguish observed defects from hypothetical risks or design choices.

Generic coding benchmarks do not directly measure this entire combination. In particular, a model can be fast and use few tokens because it stops prematurely; another can be trained for agentic coding yet still fail to manage progress in a given host application's decision protocol.

The Devstral trace points to a **strategy-transition failure**, not a defective `index_files` tool or demonstrated loss of context. Qwen3.6 illustrates a different failure mode: premature completion with insufficient investigation. Qwen3.8 is more effective at task progression but remains vulnerable to incorrect technical claims.

## 6. Architectural Implications

The model comparisons do **not** justify adding model-specific workarounds to Sentinel.

In particular:

- Do not rewrite the system prompt solely to accommodate Devstral's repeated indexing behavior.
- Do not introduce special-case logic that treats one model's repetitions differently.
- Do not build a second orchestration layer, separate agent, or elaborate state-validation framework to compensate for an unproven need.
- Do not equate fewer tokens, fewer tool calls, or shorter runtime with better performance unless the resulting work is comparable in quality.

The existing decision traces already provide a basis for investigating whether errors arise from missing evidence, a premature research strategy, or incorrect interpretation of visible code. Any future changes to context allocation or prompt wording should be driven by measured recurring failures rather than benchmark expectations or speculative complexity.

## 7. Decision and Next Steps

**Decision: Keep `qwen3.8-q5:latest` as Sentinel's primary local model.**

Of the evaluated models, it achieved the best balance of autonomous execution, breadth of investigation, and completion reliability. Its technical-accuracy grade of 4 remains a known limitation and does not meet REAPER-AI's long-term target of grade 2.

For future model evaluations:

- Prefer candidates with demonstrated agentic tool use **and** repository-level code reasoning, not coding benchmarks alone.
- Run the same real REAPER task on an isolated, documented baseline.
- Judge claims against the source code rather than treating completion or a plausible final report as proof.
- Compare tokens and time only alongside actual task coverage and technical accuracy.
- Reject model-specific architectural changes unless they solve a measured, general problem.

**Bottom line:** REAPER-AI needs a reliable autonomous software-investigation model, not merely a good code generator. Qwen3.8-Q5 is the current reference because it progresses through the task more dependably than the alternatives tested—not because its analysis is already sufficiently accurate.
