# Agents and Workflows Reference

Guidance for designing multi-step LLM systems — when to use a predetermined workflow, when to let the model decide, and which of the standard patterns fits your problem. For tool-calling mechanics, see [`TOOL_USE.md`](TOOL_USE.md). For prompt-level techniques inside each step, see [`PROMPT_ENGINEERING.md`](PROMPT_ENGINEERING.md).

---

## Workflows vs agents

The two extremes of multi-step LLM design:

- **Workflow** — a predefined sequence of LLM calls. You know the steps in advance; your code orchestrates them. Think "LLM-as-component."
- **Agent** — the model chooses what to do next, picking from a toolset. You don't know the sequence in advance; the agent decides based on intermediate results. Think "LLM-as-orchestrator."

The decision rule: **use a workflow when you know the exact steps; use an agent when you don't**. If you can write the pseudocode of the multi-step process without knowing the input, it's a workflow. If the sequence depends on what the model discovers mid-process, it's an agent.

Workflows are more reliable, easier to test, and easier to explain. Agents are more flexible but less predictable, harder to evaluate, and fail in more creative ways. Start with workflows. Reach for agents only when the flexibility is genuinely necessary — not when it's novel or fun.

---

## Workflow patterns

Four standard patterns cover most cases. They nest and combine.

### Prompt chaining

Break a complex task into sequential steps, each a focused LLM call.

```
topic → search_trends() → Claude picks best topic → Claude researches → Claude writes script → post
```

Use when:

- A single prompt has too many constraints and the model drops some ("write a script, professional tone, no emojis, no mention of AI, under 400 words"). Splitting "write a draft" from "enforce constraints on the draft" gives each call fewer things to juggle.
- The steps are genuinely sequential — each depends on the previous output.

The smallest useful chain: one prompt produces output, a second prompt critiques or reformats it. Even this lightweight chain outperforms a single maximally-constrained prompt when constraints are many.

### Parallelization

Break a task into independent subtasks, run them concurrently, aggregate.

```
material evaluation:
  → evaluate_metal()     \
  → evaluate_polymer()    → aggregate → final recommendation
  → evaluate_ceramic()    /
  → evaluate_composite() /
```

Use when:

- The subtasks are genuinely independent (no ordering, no shared state).
- You want focused evaluation per subtask instead of one prompt juggling everything.
- Latency matters — parallel calls return in the time of the slowest, not the sum.

Each subtask can be prompt-engineered, eval'd, and improved separately. The aggregator is usually a final LLM call that compares results.

### Routing

Classify the input, then dispatch to a specialized pipeline.

```
topic → Claude classifies (educational / entertainment / news) → category-specific pipeline → generate
```

Use when:

- Different input types demand different handling, and a single prompt trying to handle all types produces blurry output.
- You can enumerate the categories in advance.

The routing call is cheap — a classification, often with Haiku. The downstream pipelines do the substantive work. Treat the router as a small, fast LLM call with strict output (categorical JSON, enforced with prefill+stop) so its output is directly dispatchable.

### Evaluator-optimizer

A producer makes output. An evaluator judges it. If rejected, loop back to the producer with feedback. Repeat until accepted or a turn cap hits.

```
generate image → CAD model → render → compare render to image
  if match: done
  if mismatch: feedback → generate again
```

Use when:

- Output quality can be checked automatically (a model can compare two images, validate syntax, run tests).
- First-pass output is usually imperfect but iteration is cheap.

The loop cap is essential. Without it, a broken producer and a strict evaluator spin forever. Cap at 3–5 iterations and treat hitting the cap as failure — log the history for debugging, don't silently return the last (still-bad) attempt.

---

## Agent design rules

When flexibility is worth the unpredictability, two design rules apply.

### Prefer abstract tools over specialized ones

Give the agent a small set of flexible tools, not a large set of narrow ones. Claude Code ships with `bash`, `read_file`, `write_file`, `web_fetch` — generic primitives that combine for arbitrary tasks. A coding assistant with `refactor_tool`, `install_dependencies`, `run_tests`, `lint_code` as separate tools is more brittle: each tool has a hidden assumption about when it's the right one, and the model picks wrong.

Abstract tools compose. `get_current_datetime` + `add_duration` + `set_reminder` handle "remind me in three days," "remind me next Thursday," "remind me a week after my next appointment." Specialized tools like `remind_in_three_days` only handle the exact scenarios you anticipated.

### Build environment inspection into the loop

Agents can't predict the exact effect of their actions. After every side-effecting tool call, the agent needs a way to observe the new state:

- Computer Use takes a screenshot after every click or keypress.
- A file-editing agent reads the file before editing and re-reads after, not trusting that the edit did what it expected.
- A video-generation agent uses ffmpeg to extract frames from its output and inspects them rather than trusting the render matched the script.

Without inspection, the agent plans its next step against an imagined state. Real environments diverge from imagination constantly.

---

## Why workflows usually win

Agents are more fun to build. Workflows are more likely to ship.

- Workflows are easier to eval — each step is a prompt, each prompt has a dataset.
- Workflows fail in predictable places — when something breaks, you know which step to debug.
- Workflows compose cleanly — nested chaining, routing-inside-parallelization, parallelization-inside-chaining are all straightforward.
- Agents expose emergent failure modes — the model takes a weird sequence of tool calls you didn't anticipate, and the eval dataset doesn't cover it.

Users want the system to work. An ambitious-but-flaky agent loses to a boring-but-reliable workflow every time. Choose the boring option unless flexibility is genuinely the feature.

---

## Combining patterns

Real systems stack patterns. An automated production-debugging system is a scheduled workflow that invokes an agent inside one of its steps:

- Scheduled job runs daily (cron, not an agent).
- Workflow step 1: fetch logs (deterministic).
- Workflow step 2: dedupe errors (one LLM call).
- Workflow step 3: for each error, invoke a coding agent to propose a fix (agent, because the fix path is unknown).
- Workflow step 4: open a PR with the fixes (deterministic).

The outer scaffold is a workflow because the steps are known. The inner fix-proposal is an agent because the problem space is open-ended. Layering like this gets the reliability of workflows where you can have it and the flexibility of agents where you can't.
