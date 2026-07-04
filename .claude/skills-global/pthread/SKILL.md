---
name: pthread
description: "Spawn parallel agents for independent tasks that can run concurrently. Triggered by 'parallelize', 'run these in parallel', or when independent subtasks are detected."
context: fork
---

# P-Thread — Parallel Execution Pattern

Goal: when a task decomposes into independent subtasks, run them as concurrent subagents and deliver one aggregated result. Never report partial results; never parallelize dependent work.

## Decide

Parallelize when subtasks share no data dependencies and no writes to the same files: independent searches, reviews of separate modules, competing hypotheses, or multiple perspectives on the same question (fusion — same goal, different lenses, compare outputs). If B needs A's output, A and B are one sequential thread. If two subtasks would write to the same working tree, serialize them or give each its own isolation (e.g. a git worktree).

## Execute

The mechanism is the Agent tool (Task tool in some harnesses):

- Issue all Agent calls **in a single message** — batched calls run concurrently; calls spread across messages serialize.
- Each prompt must be self-contained. The subagent sees none of your conversation: include the goal, relevant paths and context, success criteria, and the exact shape of the answer you need back.
- Pick the narrowest agent type that fits (read-only explorer for searches; general-purpose for multi-step work).
- Wait for ALL agents to complete before synthesizing. If one fails or returns thin results, follow up with it (SendMessage where available) or spawn a replacement — do not silently drop its slice.

## Aggregate

Subagent final messages return to you, not the user. Merge and deduplicate findings, resolve disagreements between agents explicitly rather than averaging them, and if the merged picture reveals a gap, spawn a follow-up agent before reporting. Deliver one coherent result.
