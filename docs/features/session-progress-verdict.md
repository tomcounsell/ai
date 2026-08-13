# Session Progress Verdict (`valor progress`)

Answers one question about an `AgentSession` — *is it still working?* — from signals that
already exist, and answers it **truthfully rather than confidently**.

- **Module:** `tools/session_progress.py`
- **CLI:** `valor progress <SESSION_ID>` (also `python -m tools.valor_session progress --id <SESSION_ID>`)
- **Tests:** `tests/unit/test_session_progress.py`
- **Command reference:** [`docs/tools-reference.md`](../tools-reference.md) — flags, output shape, and the full signal list

## Why this exists

On 2026-08-07 a healthy session was diagnosed as deadlocked by hand, across `ps`, `lsof`, the
CLI transcript, and background-task output files. It was working the whole time and went on to
open a 14-file PR. The hang report ([#2662](https://github.com/tomcounsell/ai/issues/2662)) was
closed invalid.

What makes that incident worth encoding in a tool is that every signal read by hand was
individually misleading:

| Signal | Failure mode |
|--------|--------------|
| instantaneous `%CPU` | 0.0% when sampled between subprocess bursts |
| child-process count | 0 in the gaps between `Bash` calls |
| MCP server idleness | true, but irrelevant when the subagent works through `Bash` |
| parent transcript silence | the *expected* shape of a long synchronous `Agent` call — subagent steps are not written as sidechain entries until the call returns |

The signal that was accurate throughout was
`agent.session_runner.liveness.tool_activity_ts`, which read 0.0s the entire time. Until this
module existed its only consumer was the watchdog's `_session_progress_ts`.

## The truthfulness contract

Four invariants define the module, and each one is a test:

1. **Absence of evidence yields `UNKNOWN`, never a false negative.** A missing marker
   directory, an absent transcript, an unreadable task dir, and a dead pid each read as "no
   signal". None of them manufactures a verdict.
2. **Only positive evidence produces `PROGRESSING`.** Every verdict input is a timestamp
   proving something happened, never the absence of one.
3. **Nothing mutates.** No `save()`, no steering, no kill. Read-only by construction, so any
   agent may run it against any session including one it does not own.
4. **Never raises.** Each collector is independently fail-silent; a broken collector degrades
   that one signal to `None` and the rest still report.

## Verdict vocabulary

| Verdict | Meaning |
|---------|---------|
| `PROGRESSING` | At least one liveness signal is fresher than the window. |
| `NO RECENT ACTIVITY` | Signals exist, but all are older than the window. |
| `UNKNOWN` | No liveness evidence at all, or the session is in a terminal status. |

`NO RECENT ACTIVITY` is deliberately not called "wedged" or "stuck". A long `Bash` call fires
its `PreToolUse` hook once at the *start*, so a session legitimately running a 25-minute test
suite has a stale marker for the whole run and is indistinguishable from a hang by this
evidence. Naming the state after the evidence rather than after a guess is the point.

A terminal status short-circuits to `UNKNOWN` rather than reporting on the final turn's
markers: "progressing" would be wrong, and "no recent activity" would read as an alarm about a
session that simply finished.

## What deliberately does not vote

Three readings are collected or available but excluded from the verdict, each for a distinct
reason:

- **`%CPU` and child-process count are not collected at all.** They are absent from
  `tools/session_progress.py` entirely, and `test_report_never_collects_cpu_or_child_counts`
  keeps them out. These are the two readings that produced the #2662 misdiagnosis; gathering
  them would invite the same inference from the next reader.
- **`pr-link` artifacts** parsed from the transcript are reported, and appended to the verdict
  line as `PR #102 opened 5m ago`, but never vote. A PR proves work *happened*, not that work
  is *happening*.
- **`exec_pid` liveness** is reported and does not vote. A session between turns legitimately
  has a dead `exec_pid`, so letting that force a negative would manufacture the exact false
  "wedged" the module exists to prevent.

The `exec_pid` case has one asymmetry worth stating plainly, because it is the distinction the
whole module rests on: a pid known to be *gone* is positive evidence, while a pid whose state
is *unknowable* is absence of evidence. So `False` surfaces as a contradiction note appended to
the line callers actually read, and `None` adds nothing:

```
PROGRESSING — tool_activity 5m ago (note: exec_pid 41234 is not running)
```

## Future-dated timestamps read as absent

A timestamp more than a minute in the future is treated as **absent**, not as maximally fresh.
Clock skew or a corrupt marker would otherwise clamp to age `0.0` and pin the verdict to
`PROGRESSING` permanently — the most confident possible answer derived from the least
trustworthy possible input. A session whose only timestamps are skewed reads `UNKNOWN`, and the
reason names the offending signals.

## Signals aggregated

All of these already existed; the module adds aggregation and a verdict, not new instrumentation.

| Signal | Source |
|--------|--------|
| `tool_activity` | The runner's `matcher: ""` `PreToolUse` hook, which rewrites `<data_dir>/session_runner_hook_edges/<session_id>/<role>_hook_edges.toolactivity` on every tool call — **including calls made from inside an in-process subagent**, and in foreign repos carrying none of this repo's `.claude/hooks`. The load-bearing input. |
| `task_output` | Newest background-task output mtime under `<tmp>/claude-<uid>/<escaped-cwd>/<uuid>/tasks/*.output`. |
| `transcript` | CLI transcript JSONL mtime. |
| `last_tool_use_at`, `last_turn_at`, `last_stdout_at` | The `AgentSession`'s own liveness timestamps. The first is repo-scoped and is structurally absent for foreign-repo sessions. |

Session resolution goes through `tools.valor_session._find_session`, the same Popoto-ORM
resolver every other verb uses, so this path takes no raw Redis access.

## Window default

`--window` defaults to the watchdog's own `SESSION_PROGRESS_DEADLINE_S`, so the CLI never
disagrees with the running system about what counts as progress. Tighten it only if you intend
to own the interpretation. The value is parsed by a shared `window_arg_type` used by both the
`valor` wrapper and `valor_session`, so a negative or non-finite value (`nan`, `inf`) is
rejected identically at parse time by both front ends.

## See also

- [`docs/tools-reference.md`](../tools-reference.md) — CLI flags and output reference
- [`headless-session-runner.md`](headless-session-runner.md) — the runner that writes the hook-edge marker
- [`agent-session-health-monitor.md`](agent-session-health-monitor.md) — the watchdog that consumes the same signal to act, where this tool only reports
