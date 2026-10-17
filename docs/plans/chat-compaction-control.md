# A chat session controls its own compaction

**Ask.** Tom, valor team chat, 2026-10-17 02:00-02:08 UTC: "you control your
own compaction ... reminders starting at 150, then every 100 until 550, then
600 and then force compaction after 650". This builds on the merged Hermes
compaction (146317905, a688cb5c4) and keeps its summary rules
(`skills/sdlc/compact.md` through the PreCompact hook) unchanged.

**Premise, checked.** Claude Code 2.1.302's `/compact` command is
`supportsNonInteractive: true` and takes optional instructions (read from the
binary), so `claude -p --resume <session> -- /compact` compacts a session in
place. The PreCompact hook prints `VALOR_COMPACT` whatever the trigger. Every
model call a turn makes is a `gateway.charged` row in the task's ledger with
`turn_id`, `model` and `usage`. The last call a turn makes on its own model is
the main agent's final answer, so input + cache read + cache creation +
output of that call is the session's context size when the turn ended. Claude
Code compacts at 80% of `CLAUDE_CODE_AUTO_COMPACT_WINDOW` less 20,000 (the
merged measurement), so a window of 832,500 fires at 650,000. The chat model,
`claude-opus-5-5`, has a 1M window, so it is not clamped.

## What is built

1. **The numbers, in one place.** `WINDOWS` moves from
   `harnesses/claude_code.py` to `core/settings.py`, beside `SEATS`, with
   `REMIND = (150k, 250k, 350k, 450k, 550k, 600k)`, `FORCE = 650k`, and
   `compaction(model) -> (reminders, force) | None`. For a window under 1M,
   every number is scaled by window/1M (a 200k window: reminders 30k to
   120k, force 130k). A model `WINDOWS` does not list gets None: no reminders
   and Claude Code's default compaction, as now. `compact_at` goes.
2. **The force at 650k.** `workspace_turn` sets
   `CLAUDE_CODE_AUTO_COMPACT_WINDOW` to `force * 100 // 80 + 20_000` (832,500)
   with `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=80`, for a chat session only.
3. **The request.** A new text signal, `.valor/compact.md`
   (`core/signals.py` `TEXT_SIGNALS`), counted only in a chat session
   (`_stage_verdict` stops listing it as meaning nothing there). When a chat
   turn finishes with it, and neither stopped nor moved the task (so
   `stop.md` beside it wins), `session.run` runs one more turn in the same
   session before returning `listening`: prompt `/compact`, then the file's
   text as extra instructions, merged with the hook's; an empty file
   (`(empty compact.md)`) gives a bare `/compact`. It is
   `runs.run_turn(..., compaction=True)`: `turn.started` carries
   `compaction: true`, and no `turn.collected` is written for it. Every
   reader of `turn.started` treats it alike: `machine.fold` moves neither
   `steer_since` nor `entry_finished` for it (messages that arrived
   meanwhile reach the next turn; the session id still moves);
   `session._working_turn_finished` skips it; `core/serve.py`
   `LAST_WORKING_ENDED` excludes it, so a kernel restart never collects it
   and never displaces the chat turn's `last_collected` (effects report,
   errors). A failed compaction ends the run `failed`, as a failed turn does.
4. **The reminder.** `next_prompt` for a chat session adds `# Your context`
   when the last working turn's size has passed a reminder number that the
   working turn before it had not passed: "Your context is about N tokens.
   You may compact (`.valor/compact.md`); at F it is compacted for you."
   One reminder per number, so 600k reminds once and nothing more until the
   force; crossing two numbers at once gives one. None when a compaction
   turn finished after the last working turn. A turn's size is its last
   `gateway.charged` row on the session's model that is `complete` and not
   `unsent`: input + cache read + cache creation + output. Rows are in close
   order; that last row is the main agent's final answer, because a turn's
   subagents run in the foreground (background tasks are off,
   `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS`), so every subagent call closes
   before the main agent's next call starts, and Claude Code's side calls
   (titles, suggestions) run on another model.
5. **converse.md** says what `compact.md` does, and to compact at a natural
   break (a topic change, a quiet chat) once reminded, not to wait for the
   force; its "When the conversation is compacted" paragraph says 650k.
   `docs/harnesses.md` (Compaction), `docs/bridges/telegram.md`, the
   `core/session.py` and `core/signals.py` docstrings follow.

**Out of scope.** Task sessions (Claude Code's default); Pi and Codex (they
run no chat session); the summary rules; idle-time compaction by the
kernel.

**Governance.** None. A new signal and a prompt note add capability; nothing
is refused, blocked or checked.

## Tests

- `compaction`: the 1M window gives Tom's numbers; 200k gives them scaled;
  an unlisted model gives None.
- `workspace_turn`: a chat session gets 832,500 and 80; a task turn neither.
- The reminder: sizes 140k then 160k remind at 160k; 160k then 240k do not;
  240k then 400k remind once; 590k then 640k remind; 600k then 640k do not;
  a compaction turn after the last working turn gives none; the size skips
  another model's rows, an earlier row of the turn, and a later row that is
  incomplete or unsent; an unlisted model never reminds.
- `session.run` with a fake turn: a chat turn leaving `compact.md` is
  followed by a `/compact` turn on the same session with the file's text;
  an empty file gives a bare `/compact`; a message steered during the chat
  turn is in the next prompt, with the chat turn's effects report; without
  the file no extra turn; `stop.md` beside it ends the session with no
  compaction; a task turn's `compact.md` runs nothing.
- `machine.fold`: a compaction turn moves neither `steer_since` nor
  `entry_finished`, and keeps the session. `serve.recollect` with a
  compaction turn last collects nothing and leaves the chat turn's
  `turn.collected` the last one.
- Live, at build: a scratch Claude Code session with the plugin and a
  Telegram `VALOR_COMPACT`, filled past 20k, is resumed with `/compact`; its
  transcript shows a manual compact boundary, the summary has the
  compact.md parts, and the next resumed prompt still knows an open ask
  from before.

**Done when** (Tom's bar, after merge): in the valor team chat, the session
is reminded, chooses to compact, and keeps working with Tom.
