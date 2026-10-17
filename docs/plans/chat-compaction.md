# Chat sessions compact the way Hermes does

**Ask.** Tom, valor team chat, 2026-10-17: "ideally we try to compact
anytime between 150k and 600k tokens", then "for slack and telegram we
wouldn't need to keep the 'opening exchange' but for email you certainly
would ... let's go with Hermes' simpler solution".

**Premise, checked.** Chat sessions run on the `frontier` seat, Claude Code
with `claude-opus-5-5` (`core/settings.py` SEATS, `core/intake.py`), so Pi
and Codex run no chat session and are untouched. Claude Code 2.1.302 (the
binary, read directly) auto-compacts at its compact window less 13,000
tokens; the window is `min(model window, CLAUDE_CODE_AUTO_COMPACT_WINDOW)`,
the env var outranking every settings file. A `PreCompact` hook's stdout
becomes the compaction's custom instructions (`newCustomInstructions`).
Auto-compaction summarizes the whole conversation: it keeps no message
verbatim beside the summary. Hermes source (`hermes_cli/config_defaults.py`,
`agent/context_compressor.py`): threshold 0.50 of the window, a
`threshold_tokens` cap that only lowers it, `protect_first_n` 3,
`protect_last_n` 20, a `## User Messages (verbatim, newest first)` section,
and a re-compaction that carries the previous summary forward.

## What is built

1. **The threshold.** `harnesses/claude_code.py` gains
   `compact_at(window) = min(max(window // 2, 150_000), 600_000)`. For a chat
   session's turn, `workspace_turn` sets `CLAUDE_CODE_AUTO_COMPACT_WINDOW` to
   `compact_at(window) + 13_000`, so Claude Code compacts at the threshold.
   The window is a figure beside the seat's model, `claude-opus-5-5`:
   1,000,000 (Claude Code's calls reached 947,155 input tokens before a
   compaction, `docs/harnesses.md`), giving 500,000. Task turns keep
   Claude Code's default.
2. **The instructions.** A new `skills/sdlc/compact.md` holds the
   compaction instructions, and is the only place they live. They say:
   - Quote every human message in the chat verbatim, newest first, with
     when, who and message id, carrying forward the quotes from an earlier
     summary.
   - End with the last 20 messages of the chat verbatim, both the humans'
     and Valor's sends, as the recent tail.
   - Keep converse.md's list: who is in the chat and what each cares about,
     unanswered asks and who made them, each task started (id, purpose,
     asker, state), and promises made.
   - When updating an earlier summary, keep what is still true and drop
     what has been settled.
   - In an email thread only, quote the opening message verbatim. Telegram,
     Slack and the local chat do not keep it.

   The kernel renders this text per channel into `VALOR_COMPACT` for a chat
   session's turn. Valor's plugin `hooks.json` gains a `PreCompact` hook,
   `/bin/sh -c 'printf %s "$VALOR_COMPACT"'`, which prints it. With the
   variable unset (every task turn) it prints nothing and changes nothing.
   The channel reaches `workspace_turn` through the harness dict, set in
   `core/__main__.py` `_turn_for` beside `_reading_env`.
3. **converse.md.** Its "What to keep" paragraph becomes two sentences.
   The session compacts at about 500k tokens into a summary that quotes the
   chat's human messages and its recent tail. Anything older is in the chat
   (`valor-telegram read`, `valor-slack read`) and in the record.
4. **Docs.** `docs/harnesses.md` ("Compaction, never replacement") and
   `docs/bridges/telegram.md` (chat session paragraph) are updated to the
   threshold, the hook and the retrieval paths, and `core/session.py`'s
   docstring line too. The docs also record the difference from Hermes:
   the tail is quoted inside the summary rather than spliced verbatim,
   because Claude Code's auto-compaction has no kept tail. Old tool output
   needs no clearing, because the whole middle is replaced.

**Why no summarizer of our own.** Claude Code provides the trigger and the
instructions. The only gap is that the tail is copied by the summarizer
rather than kept byte for byte. A summarizer that splices a verbatim tail
would be a second compaction mechanism under rule 4, and nothing yet
shows that the quoted tail is not good enough.

**Out of scope.** Pi and Codex (they run no chat session); task turns'
compaction; a store for compacted messages (the ledger and the read tools
cover it); topic-aware or idle-time compaction.

**Governance.** None. The PreCompact hook supplies text and refuses
nothing. No check, gate or limit is added.

## Tests

- `compact_at`: window 200k gives 150k (floor), 1M gives 500k, 1.5M gives
  600k (ceiling), 300k gives 150k, 1.2M gives exactly 600k.
- `workspace_turn` for a chat session sets `CLAUDE_CODE_AUTO_COMPACT_WINDOW`
  to 513000 and `VALOR_COMPACT`. A task turn sets neither.
- The rendered instructions: Telegram, Slack and local carry no opening
  clause, email does, and every channel carries the verbatim-quotes, tail
  and keep-list clauses.
- The hook, run as Claude Code runs it (as `test_orchestration.py` runs
  `subagent_start.py`), prints `VALOR_COMPACT` exactly, and prints nothing
  when it is unset.
- Live, at build: a scratch Claude Code session with
  `CLAUDE_CODE_AUTO_COMPACT_WINDOW=100000` and the plugin is driven past the
  window. Its transcript shows the compaction fired near 87k, and the
  summary carries the quoted messages and the tail.

**Done when** (Tom's bar, after merge): the valor team chat's session
compacts at about 500k, which its transcript's compact boundary shows, and
then keeps answering Tom correctly about asks and tasks from before the
compaction.
