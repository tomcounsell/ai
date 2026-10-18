# Mid-turn steering: a message for running work lands after the current step

Task b39f3a21e875. Tom, valor team chat, 2026-10-18: "Wait for the current
step, but insert at the next opportunity. Same goes for orchestration and the
steering of subagents and kernels at all times." Readback accepted: any message
for running work lands after the current tool call, never cutting it off;
everywhere work runs, subagents included; a message that misses the turn opens
the next one, as today; stop still stops at once.

## What the code and the harnesses do (checked by experiment, Claude Code 2.1.302)

- Today a `message.steered` row waits for the next working turn (`core/machine.py`
  `steering`/`steer_since`; `core/session.py` `_steering`, `chat_prompt`).
- **A PostToolUse hook** printing `additionalContext` reached the main agent and a
  running foreground subagent alike, right after the step that was running when
  the message was written, before the next model call. Both followed a correction
  ("do not run echo two; run echo three"). It needs `hookEventName` to match, so
  `PostToolUseFailure` (a failed step) is registered too. Valor's plugin already
  runs hooks in every subagent (`skills/valor/hooks/hooks.json`, SubagentStart).
- **stream-json stdin** (`--input-format stream-json`) also takes a message
  mid-turn, but only into the main agent: a message written while a subagent ran
  waited for its hand-back, and `parent_tool_use_id` does not route it inside.
  SendMessage reaches only background agents, which turns disable.
- **Pi 1.0.0** (`docs/rpc.md`): `--mode rpc` takes `prompt`, then `steer`
  commands, "delivered after the current assistant turn finishes executing its
  tool calls"; `agent_settled` ends it. No hook surface with `--no-extensions`.
- **Codex** `exec` takes nothing once started: it keeps the next-turn path.
- **A defect in the same window:** `session.run` renders the prompt, then
  `run_turn` waits for a slot and writes `turn.started`. A steer recorded in
  between is in no prompt, and the turn's finish moves `steer_since` past it.

## What will be built

1. **Claude Code: the hook is its one mid-turn path** (not stdin), since only it
   reaches subagents; the argv prompt, `/compact` turns and `parse` are unchanged.
   A working or chat turn (not fresh, not compaction) with a `tmpdir` gets
   `VALOR_STEER=<tmpdir>/steer`. The kernel writes each message there as one file
   (write then rename), rendered as the prompt renders it (`_steering`;
   `intake.chat_line` for a chat), opening "Valor steering." A plugin hook
   (`hooks/steer.py`, on PostToolUse and PostToolUseFailure) prints every message
   this agent (its `agent_id`, or the main agent) has not yet taken, appending a
   receipt line `{id, agent}` before printing. The Brief's channel says what a
   "Valor steering." note is: Tom's (or the named writer's) word.
2. **Pi**: `--mode rpc`, the prompt as a `prompt` command, each message a `steer`
   command, stdin closed at `agent_settled`, events read to EOF; the receipt is
   the user message Pi records. The builder confirms the receipt shape against
   the gateway first; without one, Pi keeps the next-turn path and the delivery
   says so. `parse` reads the RPC stream: the last `agent_end`'s result, usage
   summed over every run, never double-counted.
3. **The runner** (`core/runs.py`) has one notify loop on its listener, `LISTEN`
   on `valor_stop` and `valor_events`, dispatching by channel: stop to the stop
   path, a `message.steered` row of this task to the harness's `deliver`. On start
   it delivers every steered row after the prompt's window. At the end it reads the
   receipts and writes `message.delivered` `{steered_id, turn_id}` (quiet) for each
   message the main agent took, in the `turn.ended` transaction.
4. **One delivery, never two.** `turn.started` records `read_upto`, the last row
   its prompt read (from `next_prompt`), and `steer_since` uses it when present
   (fresh, advisor, compaction turns: as today). The fold drops a delivered row
   from `steering`, `chat_prompt`, `chat_waiting` and `join` only when its turn
   ended done without error: that turn's session, which the next working turn
   resumes, holds the message. An errored, failed or stopped turn keeps all its
   steering for the next turn, as today. A message only a subagent took is not
   delivered: the main agent gets it at its next step or next turn.
5. **Reactions** (`on_turn`): a message delivered into a turn is read by it.
6. **Docs:** architecture.md Steering; harnesses.md (each path, Codex fallback);
   `skills/sdlc/converse.md` and `tools/chat_tasks.py` usage (a steer lands at the
   task's next step).

Answers and feedback need no path of their own: a question ends its turn and
feedback reopens ended work, so neither has a running turn to reach.

## Out of scope

- Fresh sessions (critique, review, test, docs) are blind by design; Codex turns
  and a chat turn's text-only reply get the next turn. No governance is added.

## Stakes

Every turn passes through the changed runner and the plugin's hooks; a fault
stops all work, kernel-wide, until rolled back.

## Tests

- Runner (scripted harness, `tests/scripted.py`): a steer during a step is in the
  inbox, its receipt becomes `message.delivered`, the next prompt omits it; a
  steer after the last step opens the next turn; a steer recorded in the slot wait
  is delivered at start; none for fresh or compaction turns or another task;
  **stop kills mid-step while steer rows are flowing**.
- Hook: each agent takes each message once; two agents both take it; the failure
  event's name; no inbox, no output.
- Fold: `read_upto` sets the window; none leaves it as today; delivered drops only
  from a clean turn; an errored turn keeps it; subagent-only receipt keeps it.
- Chat: a `child.reported` mid-turn still wakes the next turn; delivered skipped.
- Pi: a steer at `agent_end` is delivered exactly once, this turn or next; `parse`
  with two runs takes the last result and sums usage once. Merge join; reactions.
- Live: a real turn through the gateway takes a steer mid-step; done is Tom
  correcting a task mid-build in the chat.
