# Mid-turn steering: a message for running work lands after the current step

Task b39f3a21e875. Tom, valor team chat, 2026-10-18: "Wait for the current
step, but insert at the next opportunity. Same goes for orchestration and the
steering of subagents and kernels at all times." Readback accepted ("Sounds
good"): any message for running work lands after the current tool call, never
cutting it off; everywhere work runs; a message that misses the turn opens the
next one, as today; stop still stops at once.

## What the code and the harnesses do (checked)

- Today a `message.steered` row waits for the next working turn
  (`core/machine.py` `steering`, `steer_since`; `core/session.py` `_steering`,
  `chat_prompt`). Turns run with stdin closed (`core/runs.py` `_feed`).
- **Claude Code 2.1.302, by experiment** (`claude -p --input-format stream-json
  --output-format stream-json --verbose --replay-user-messages`, stdin open):
  a user record written during a 6-second Bash call was taken in right after
  that call's result, before the next model call, in the same turn (one
  `result`). The echo of a consumed record keeps the `uuid` the writer gave it:
  a delivery receipt. A record written while the final answer streamed was run
  as a follow-up in the same process (a second `result`) before it exited.
- **Subagents, by experiment:** a record written while a foreground subagent
  worked landed in the parent right after the subagent handed back (its whole
  run is the parent's current step). Setting `parent_tool_use_id` does not
  route it to the subagent. SendMessage reaches only background agents, which
  turns disable (`CLAUDE_CODE_DISABLE_BACKGROUND_TASKS`). Nothing Claude Code
  offers puts a message into a running foreground subagent.
- **Pi 1.0.0** (`docs/rpc.md`, `rpc-commands.md`): `--mode rpc` takes a
  `prompt` command and `steer` commands on stdin; a steer "is delivered after
  the current assistant turn finishes executing its tool calls, before the next
  LLM call". `agent_settled` means Pi will do nothing more; `queue_update` and
  the user `message_end` show what was taken in.
- **Codex** (`codex exec`): no input once started (`app-server` is
  experimental). It keeps the next-turn path.
- **A defect in the same window:** `session.run` renders the prompt, then
  `run_turn` waits for a turn slot and writes `turn.started`. A steer recorded
  in between is in no prompt, and the turn's finish sets `steer_since` past it:
  it is never shown. Slot waits make the window minutes long.

## What will be built

1. **The harness port** (`core/runs.py`): `TurnCommand.steer`, optional, a
   small dataclass the harness fills: the opening record (the prompt),
   `encode(key, text) -> bytes`, `delivered(record) -> key | None`, and
   `settled(record) -> bool`. With it, the runner keeps stdin open, reads
   stdout as JSON lines (still copied whole to the stdout file), and closes
   stdin at the first settled record.
2. **Delivery in the runner.** A working-session or chat turn (not fresh, not
   compaction) also `LISTEN`s on `valor_events` on the listener it has. On
   start, and on each `message.steered` row for its task, it writes every
   steered row after the prompt's window not yet delivered, rendered as the
   prompt renders it (`session._steering` for a task; `intake.chat_line`, with
   the `message.heard` lines before it, for a chat). On a receipt it appends
   `message.delivered` `{steered_id, turn_id}` (quiet; the steered row keeps
   its provenance). After stdin closes it writes nothing; what is left is the
   next turn's, as today. Stop is unchanged.
3. **One delivery, never two.** The fold keeps `delivered` ids; `steering`,
   `chat_prompt` and `chat_waiting` skip them, and `join`'s "steering waits
   unread" counts only undelivered rows. `turn.started` records `read_upto`,
   the last row id its prompt read (from `next_prompt`), and `steer_since`
   uses it, which closes the slot-wait gap for every harness (Codex included).
4. **Claude Code** (`harnesses/claude_code.py`): stream-json in and out with
   `--replay-user-messages`; the prompt is the first stdin record, not argv;
   each message's `uuid` is its key; `parse` reads the last `result` line.
5. **Pi** (`harnesses/pi.py`): `--mode rpc`; `prompt`, then `steer` commands;
   receipt from the user message Pi records; settled at `agent_settled`;
   `parse` reads the same events. The builder confirms the receipt by a run
   against the gateway first; if Pi gives none it can match, Pi keeps the
   next-turn path and the delivery says so.
6. **Reactions** (`core/reactions.py` `on_turn`): a message delivered into a
   turn counts as read by it, so its activity emoji goes on that message.
7. **Docs:** `docs/architecture.md` Steering; `docs/harnesses.md` (each
   harness's mid-turn path, the Codex fallback, the subagent limit);
   `skills/sdlc/converse.md` (a chat message may arrive mid-turn; a
   `task.steer` lands at the task's next step); `tools/chat_tasks.py` usage.

Answers and feedback need no path of their own: a question ends its turn and
feedback reopens ended work, so neither has a running turn to reach; a chat's
word to a running task is a steer (`TaskSteer`), which this delivers.

## Out of scope, named in the delivery

- **Running subagents.** A message reaches the turn's main agent after the
  subagent's hand-back, the next step the main agent has. Reaching inside a
  running subagent would need a second injection path (a PostToolUse hook);
  not built, per one mechanism per concern.
- Fresh sessions (critique, review, test, docs) are blind by design and get
  no steering; the next working turn does, as today. Codex: next turn.
- No governance is added: no check, gate, or validator.

## Stakes

Every turn passes through the changed runner and both harness invocations; a
fault stops all work, kernel-wide, until rolled back.

## Tests

- Runner, with a scripted stream-json harness (`tests/scripted.py`): a steer
  during a step is written after it, `message.delivered` lands, the next prompt
  omits it; a steer after settle opens the next turn; a steer for a fresh or
  compaction turn is not written; stop still kills mid-step; a steer recorded
  during the slot wait is delivered at start; another task's steer is ignored.
- Fold: `steering` skips delivered rows; `read_upto` sets the window; an
  errored turn keeps undelivered steering and drops delivered.
- Chat: `chat_prompt` and `chat_waiting` skip delivered; a heard line before a
  delivered steer travels with it; no extra turn is woken.
- Merge join: delivered steering no longer holds a merge.
- Harnesses: argv and record shapes; `parse` of a recorded stream-json and Pi
  RPC run (two `result`s: the last wins).
- Reactions: activity lands on a message delivered mid-turn.
- Live, before done: a real `claude` turn through the gateway takes a steer
  mid-step; the done run is Tom correcting a task mid-build in the chat.
