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
   (`core/signals.py` `TEXT_SIGNALS`), counted only in a chat session. When
   a chat turn finishes with it, `session.run` runs one more turn in the same
   session before returning `listening`: prompt `/compact`, followed by the
   file's text if any (extra instructions, merged with the hook's). It is
   `runs.run_turn(..., compaction=True)`, so `turn.started` carries
   `compaction: true`; `machine.fold` then moves neither `steer_since` nor
   `entry_finished` for it, so messages that arrived meanwhile reach the
   next turn. A failed compaction ends the run `failed`, as a failed turn
   does, and the next prompt's failure report names it.
4. **The reminder.** `next_prompt` for a chat session adds `# Your context`
   when the size at the end of the last working turn is past a reminder
   number the turn before it had not reached: "Your context is about N
   tokens. You may compact (`.valor/compact.md`); at F it is compacted for
   you." Sizes come from `gateway.charged` (premise). No reminder when a
   compaction turn came after the last working turn. One reminder per
   number crossed; crossing two at once gives one.
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
  240k then 400k remind once; 600k then 640k remind; a compaction turn after
  the last working turn gives none; another model's calls and an earlier
  call of the turn are not the size; an unlisted model never reminds.
- `session.run` with a fake turn: a chat turn leaving `compact.md` is
  followed by a `/compact` turn on the same session with the file's text; a
  message steered during the chat turn is in the next prompt; without the
  file no extra turn; a task turn's `compact.md` runs nothing.
- `machine.fold`: a compaction turn moves neither `steer_since` nor
  `entry_finished`, and keeps the session.
- Live, at build: a scratch Claude Code session with the plugin and a
  Telegram `VALOR_COMPACT`, filled past 20k, is resumed with `/compact`; its
  transcript shows a manual compact boundary, the summary has the
  compact.md parts, and the next resumed prompt still knows an open ask
  from before.

**Done when** (Tom's bar, after merge): in the valor team chat, the session
is reminded, chooses to compact, and keeps working with Tom.
