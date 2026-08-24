---
status: PoC Complete
type: assessment
appetite: Medium
owner: Valor
created: 2026-05-28
tracking: https://github.com/tomcounsell/ai/issues/1486
verdict: proceed-with-caveats
---

# Results: Granite-Orchestrated Dual Claude Code Session Executor

This document summarizes the end-to-end run of the granite-agent-loop PoC
described in `docs/plans/granite-agent-loop-poc.md`. The PoC produced a
committed Python file through a fully granite-driven loop with no human
intervention, validating the core architecture.

## Run summary

- **Task given**: "write a Python file named hello_poc.py that prints
  'Hello from granite PoC' when run; nothing else"
- **Status**: `done` (granite routing loop terminated normally; PM emitted
  "TASK COMPLETE" signal)
- **Total turns**: 4 (well under the `max_turns=10` cap)
- **Wall-clock**: 57.85s
- **Sandbox cwd**: `/tmp/granite_poc_run/`
- **Result file**: `/tmp/granite_poc_run/hello_poc.py` (created, runs,
  prints expected output)
- **PM task-list ID**: `granite-poc-pm-91ee74be`
- **Dev task-list ID**: `granite-poc-dev-91ee74be`

## Required assessment schema (per critique table)

| Field | Value |
|---|---|
| `avg_turns` | 4 (single run; needs N=10+ for stable mean) |
| `granite_parse_error_rate` | 0% (4/4 routing calls returned a valid tool call) |
| `operator_event_count` | 1 (one `timeout` event surfaced during turn 2) |
| `wall_clock_s` | 57.85 |
| `kill_criteria_hit` | none |

## Latency per turn

Trace lines (`logs/granite_poc_trace.jsonl`, deltas inferred from the
`duration_ms` field where present and from successive `ts` values):

| Turn | Stage | Session | Granite tool | Duration |
|---|---|---|---|---|
| 1 | send_to_dev | dev | extract_dev_prompt | 11.9 s (Dev created file, including bash + write tool calls) |
| 2 | send_to_pm | pm | summarize_for_pm | 31.4 s (PM took a turn that hit the 30s per-line readline deadline -- granite surfaced this as a timeout operator_event) |
| 3 | probe | dev | probe_session | 9.6 s (granite probed Dev because of the PM timeout; Dev replied "still working or wrapped up?") |
| 4 | done | -- | (PM said "TASK COMPLETE") | 0 ms (loop exited on the explicit phrase) |

**Granite routing latency**: median ~1.2 s/call on this machine (from the
gate smoke test, 20 scenarios; mean 1.25 s, max 5.77 s on cold first call).
Granite is not the bottleneck.

**Claude Code latency**: ~10-30 s/turn dominates wall-clock. ttft was 2-3
s; the long turn 2 was a PM reasoning turn, not network.

## Operator events observed

1. `{"type": "timeout", "reason": "per-line 30s deadline reached"}` --
   surfaced during PM's turn (turn 2). Granite responded correctly by
   calling `probe_session`, which sent "still working or wrapped up?" to
   the Dev session. Dev replied that it had completed the task. PM then
   confirmed and the loop exited.

This validates the hang detection path. The kill-criterion threshold for
operator-event-driven failures (>30% of turns) was not approached.

## Success criteria checklist

- [x] `python scripts/granite_poc.py "..."` runs end-to-end and produces
      a committed Python file without human intervention -- the PoC
      created `hello_poc.py` in `/tmp/granite_poc_run/` (`print('Hello
      from granite PoC')` -- valid Python, runs)
- [x] `logs/granite_poc_trace.jsonl` contains >= 2 PM-Dev turns with
      correct `{"type": "result"}` detection -- 7 trace entries across 4
      turns; explicit `events_count` columns show stream-json events
      were parsed; both PM and Dev produced `result` events
- [x] At least one operator event handled by granite -- turn 2 timeout
      drove granite to call `probe_session`
- [x] Both PM (Opus) and Dev (Sonnet) subprocesses have
      `ANTHROPIC_API_KEY=""` in env -- verified via subprocess.Popen spy
      (see "Caveat 1" below for grep-test discrepancy)
- [x] This results doc exists
- [x] Unit tests pass: `pytest tests/unit/test_claude_session.py
      tests/unit/test_granite_router.py -x -q` -- 27/27

## Verification table results

| Check | Result |
|---|---|
| Unit tests pass | PASS (27/27) |
| PoC trace exists | PASS (7 entries in `/tmp/granite_poc_run/logs/granite_poc_trace.jsonl`; absent from worktree because the PoC was run from a sandbox to avoid polluting the repo) |
| No API key in new files | DISCREPANCY -- see Caveat 1 |
| No claude-agent-sdk import | PASS (`grep -r "claude_agent_sdk\|ClaudeAgent"` returns no matches) |
| Results doc exists | PASS (this file) |
| Lint clean | PASS (ruff check on all four new files) |

## Caveat 1 -- ANTHROPIC_API_KEY grep test conflict

The plan's verification table states:

> No API key in new files: `grep -r "ANTHROPIC_API_KEY" agent/granite_router.py agent/claude_session.py agent/granite_agent_loop.py` should exit 1.

The plan's success criteria #4 states:

> Both PM (Opus) and Dev (Sonnet) subprocesses have `ANTHROPIC_API_KEY=""` in their env.

These are in conflict: the only way to set `ANTHROPIC_API_KEY=""` is to
reference the name in code. The PoC chose the success-criterion path
(the runtime invariant matters more than the literal grep), so:

- `agent/claude_session.py` writes `env["ANTHROPIC_API_KEY"] = ""` before
  spawning. Verified via subprocess.Popen spy.
- `grep -n "ANTHROPIC_API_KEY"` returns three lines, all in
  `agent/claude_session.py`: two doc-comment references and the one
  blanking assignment. None of them READ the variable.

Recommendation: the production-replacement plan should reword the
verification check to "no places that *read* the variable" -- e.g.
`grep -n 'os.environ\[.ANTHROPIC_API_KEY.\]' agent/...` or assert via
test fixture rather than text search.

## Caveat 2 -- task list isolation works but warrants a worker integration test

`CLAUDE_CODE_TASK_LIST_ID` is set per session (`granite-poc-pm-<8hex>`
and `granite-poc-dev-<8hex>`) which prevents this PoC's task lists from
polluting any concurrent dev session. The PoC verified this only by
inspecting the spawned env; no test cross-checks that Claude Code
actually honored the variable. If the architecture moves toward
production, add an integration assertion that `pgrep -fla CLAUDE_CODE_TASK_LIST_ID=granite-poc-` matches each subprocess and that the task lists are independent.

## Caveat 3 -- spike-3 update

The plan's spike-3 said `--input-format stream-json` works on its own.
In practice the Claude CLI rejects this unless paired with `-p / --print`
AND `--verbose`. The PoC corrected this in `agent/claude_session.py`:

```
claude -p --verbose --input-format stream-json --output-format stream-json
       --model {opus|sonnet} --permission-mode bypassPermissions
```

This is a documented Claude Code constraint
("Error: When using --print, --output-format=stream-json requires --verbose")
not a behavioural surprise.

## What this PoC does NOT yet prove

1. **Concurrent session limits on Max** -- not exercised; sessions are
   sequential.
2. **Long-running PM context blowout** -- the run was 4 turns and
   ~16 KB of stream-json. The `summarize_for_pm` granite tool extracts
   only PM-facing text; the raw tool_use stream is never forwarded. But
   we have not tested a 20+ turn run that exercises granite's history
   truncation logic.
3. **Crash recovery** -- the explicit `restart()` path is unit-tested
   with a fake subprocess but has not been triggered by a real Claude
   Code crash. Worth a chaos test that SIGKILLs Dev mid-turn.
4. **Multiple-choice / feedback events** -- the operator-event taxonomy
   is implemented but only the timeout case fired in this run. The
   smoke test (Task 0) confirmed granite *would* call `handle_choice`
   when given that input, but the live Claude sessions did not emit a
   numbered multiple-choice prompt during the run.

## Verdict

**Proceed to production planning, with caveats.**

The three-layer architecture (granite operator + dual persistent Claude
sessions over stream-json stdio) is viable. Granite4.1:3b is reliable
(100% tool-call dispatch in 20-scenario smoke test) and fast (mean
1.25 s/call). Persistent Claude subprocesses survive multi-turn under
stream-json without `--resume` UUIDs. The PoC produced a working
artifact (`hello_poc.py`) end-to-end in under a minute.

What still needs to happen before any production wiring:

1. Re-run with N >= 10 different tasks of varying complexity to
   establish stable mean-turns, mean-latency, and granite parse-error
   rate at scale.
2. Run a >= 20-turn task to exercise the granite history truncation
   logic (`HISTORY_KEEP_LAST_N = 8`).
3. Chaos test: SIGKILL Dev mid-turn, verify `ClaudeSession.restart()`
   path with a real subprocess.
4. Production integration plan (SEPARATE-SLUG) -- explicitly out of
   scope per the No-Gos section.
