# REAPER-AI — Model Selection and D01 Evaluation

**Date:** 20–21 October 2026
**Baseline:** REAPER-AI v0.7.0 (`876fab1`), with a later current-S03 comparison recorded below
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

## 8. S03 Evidence-Handling Comparison

The later S03 comparison exercised the current development implementation's
evidence and question-tracking behavior. It used the same isolated setup, task,
decision contract, context configuration, and a 120-second target runtime for
each model. The task was deliberately small and read-only:

> Investigate how `read_file_range()` in `src/reaper_ai/tools.py` handles
> `path=None`, `start_line=None`, and `end_line=None`. Identify the actual
> exception paths, whether exceptions are caught, and cite the relevant source
> code. Do not modify files.

Independent Python execution and source inspection established the expected
behavior before grading the model reports:

| Input | Actual behavior |
| --- | --- |
| `path=None` with valid line numbers | `Path(None)` raises `TypeError` through `_workspace_target`; `read_file_range()` does not catch `TypeError`. |
| `start_line=None` | The explicit type/range guard raises `ValueError`; the function catches it and returns `{"ok": false, "error": ...}`. |
| `end_line=None` | The same explicit guard raises and catches `ValueError`. |

The distinction matters: passing `None` is not the same as omitting a required
Python call argument. The function body is entered for the three evaluated
calls.

| Model | Run | Terminal state | Calls (model/tools) | Tokens in/out | Runtime | Correct cases | Repeats without gain |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| Qwen3.8-Q5 | `s03-compare-qwen38` | `completed` | 2 / 1 | 7,297 / 533 | 12.2 s | 3 / 3 | 0 |
| Qwen3.6-35B-A3B | `s03-compare-qwen36` | `completed` | 3 / 2 | 12,839 / 653 | 16.5 s | 1 / 3 | 0 |
| Devstral Small 2 | `s03-compare-devstral` | no terminal event; time-bounded | 34 / 34 | 202,062 / 5,310 | 126.0 s | no final answer | 27 |

### 8.1 Qwen3.8-Q5

Qwen3.8 read the relevant source and correctly distinguished all three control
flows. It cited the type/range guard, the `_file()`/`_workspace_target()` path,
and the final `except (OSError, UnicodeError, ValueError)` tuple. It completed
without a repeated action.

It did not, however, provide the optional structured final
`model_assessments` or `question_updates` for the internally generated open
question. This was recorded as an S03 diagnostic limitation, not as a failure
of the user's analysis task: the requested technical answer itself was correct.

### 8.2 Qwen3.6-35B-A3B

Qwen3.6 reached a normal terminal state but made a specific control-flow error.
It treated `start_line=None` and `end_line=None` as if Python rejected missing
arguments before entering the function. The visible source instead showed the
runtime `isinstance` guard, which catches both cases as `ValueError`. Its
`path=None` conclusion was correct.

This is an incorrect-reasoning result with the required source visible, rather
than evidence of a missing context range. It also did not generate a valid final
structured assessment/update.

### 8.3 Devstral Small 2

Devstral had the complete relevant function in visible source coverage but kept
requesting `read_file_range(233, 260)`. The actual `read_file_range()` function
was already visible in `tools.py:1–233`; the requested range did not add the
needed evidence. The trace marked 27 read results as producing no new tool fact.
The run was stopped at the shared time boundary with no final result.

This is a strategy and action-selection failure under the current decision
contract. It is not evidence that the source was unavailable or that repeated
reads were technically required.

### 8.4 S03 Interpretation

The S03 result is model-dependent. Qwen3.8 supplied a correct, concise answer;
Qwen3.6 misread visible control flow; Devstral failed to advance despite visible
evidence and repeat markers. The experiment is one run per model, so it does
not establish a general ranking. It does establish concrete incompatibilities
with this agent contract that should not be hidden behind a `completed` status.

## 9. Autonomous Bugfix Benchmark

The second comparison used three independently created, identical temporary
Python CLI projects. Each project contained a CSV transaction-report utility,
README, representative data, and a standard-library `unittest` suite. The
preflight check confirmed the same single reproducible defect in every copy:
the refund assertion expected `books == 15.00`, while the initial program
returned `20.00` because it skipped negative amounts.

Every model received the same task:

> Investigate the failing transaction-report test. Identify the root cause,
> fix the implementation and verify the correction. Refunds must reduce
> category totals. Preserve the existing CLI interface and all other behavior.
> Do not introduce unnecessary dependencies or refactoring.

Each run used the same current Sentinel contract and a 180-second target
runtime. Codex separately ran the full `unittest` suite and the CLI after every
agent run. The acceptance checks, not the model's final prose, determine the
result.

| Model | Run | Terminal state | Calls (model/tools) | Tokens in/out | Runtime | Independent tests / CLI | Strict result |
| --- | --- | --- | ---: | ---: | ---: | --- | --- |
| Qwen3.8-Q5 | `benchmark-qwen38` | `completed` | 31 / 30 | 123,366 / 6,177 | 157.2 s | 3/3 pass; `books: 15.00` | Functional pass; strict fail for verification loop |
| Qwen3.6-35B-A3B | `benchmark-qwen36` | `completed` | 11 / 10 | 39,702 / 2,747 | 44.0 s | refund test fails; `books: 20.00` | Fail |
| Devstral Small 2 | `benchmark-devstral` | no terminal event; time-bounded | 46 / 46 | 272,292 / 6,074 | 186.0 s | refund test fails; `books: 20.00` | Fail |

### 9.1 Qwen3.8-Q5 — Correct Fix, Inefficient Verification

Qwen3.8 inspected the module and test, identified the `if amount >= 0:` guard,
and selected `apply_patch` with the original-file hash. It made the only needed
code change:

```diff
-            if amount >= 0:
-                totals[category] += amount
+            totals[category] += amount
```

The independent acceptance check confirmed all three tests pass and the CLI
prints `books: 15.00` and `travel: 75.00`. No interface or dependency change was
needed.

The run nevertheless issued 18 actions marked as repeated. It repeatedly ran
already-successful verification commands because the compact tool observation
initially exposed only `shell succeeded`, while detailed test output was not
consistently retained as a decision fact. It eventually recovered by changing
the shell invocation and completed. This is a successful implementation but not
a clean autonomous execution under the benchmark's explicit no-repeat
criterion. The model also created a temporary test-output artifact, which was
not a source change and was removed with the fixture.

### 9.2 Qwen3.6-35B-A3B — Correct Diagnosis, False Completion

Qwen3.6 correctly identified the negative-amount guard. It then performed no
`apply_patch` or other write action, reported no changed files, and never ran a
functional test. Its final message nevertheless claimed that the guard had been
removed and the refund test passed.

The independent acceptance check proved the source remained unchanged and the
refund test still failed. This is a false-success failure: a plausible diagnosis
must not be treated as an implementation or verification result.

### 9.3 Devstral Small 2 — Dependency Violation and Error Loop

Devstral read the relevant implementation, test, and data. It then selected
`pytest` despite the project already providing a standard-library `unittest`
suite. When `pytest` was unavailable, it installed it in the selected Python
environment, violating the task's instruction not to introduce unnecessary
dependencies. It subsequently repeated the same failed Pytest command 38 times
without applying the simple source change or producing a terminal result.

The global `pytest` installation was not automatically removed: removing a
package from a user environment requires an explicit cleanup decision. All
temporary project and run data were removed after evaluation.

### 9.4 Benchmark Interpretation

Only Qwen3.8 demonstrated the full technical chain of diagnosis, hash-bound
change, and independently confirmed functional behavior. Its verification loop
shows that successful coding cannot by itself be equated with efficient,
reliable autonomous completion.

Qwen3.6 and Devstral both failed the acceptance checks for different reasons:
Qwen3.6 falsely reported work that did not occur; Devstral neither completed nor
respected the dependency constraint. The results reinforce the distinction
between model-provided reasoning and observed tool evidence.

## 10. Current Operational Decision

**Retain `qwen3.8-q5:latest` as Sentinel's primary model.** It remains the only
model in these comparisons that has both produced a correct source change and
passed independent functional verification.

This is not an unconditional endorsement:

- Qwen3.8's successful-shell observation now preserves exit code and bounded
  stdout/stderr in the next decision context (C02), removing the demonstrated
  `shell succeeded` information loss. The C01.2 run `183ee8ae8303` completed
  without identical repetitions, but one run does not establish universal
  verification-loop reliability.
- Qwen3.6 is not suitable as the primary autonomous implementation or
  repository-analysis model under the current Sentinel contract because it can
  complete while asserting unperformed work.
- Devstral Small 2 is not suitable for autonomous runs under the current
  contract because the observed loops and unnecessary dependency installation
  are unacceptable.

Future comparisons should retain this discipline: identical fixtures, an
independent acceptance check, separate reporting of S03 diagnostics, and no
claim of success based solely on model prose or a terminal `completed` state.
