# Ask-me in chat: shaping with Tom one question at a time

**Ask.** Tom, valor team chat, 2026-10-17: chat sessions need ask-me's
"sophistication here while we shape requirements before you commit to
building"; after a batch of overlapping questions, "only ask one at a time.
Start with the greatest loadbearing question work down from there"; where it
applies: "2. Live chats, and also at your discretion. Sometimes I'll
explicitly tell you to 'ask me'". His earlier shaping rules from the same
chat live only in one session's memory (`shape-before-build.md`).

**Premise, checked.** `skills/sdlc/converse.md` item 2 says "Work: start a
task" and nothing about shaping. The persona's "How to ask" (source
`persona/conduct.md`, rendered into every turn by `core/persona.py`
`TEXTS`; documented in `docs/persona.md`) says "One batch". The ask-me skill
(`yudame/skills` `plugins/yudame-dev/skills/ask-me/SKILL.md`, installed in
every turn as `yudame-dev:ask-me`) lists `AskUserQuestion` in
`allowed-tools` and says "with 2-4 options ... labeled `(Recommended)`" as
pop-up options; a chat session has no pop-up, only its sends. Valor's
account has MAINTAIN on `yudame/skills`.

## What is built

1. **ask-me works without a pop-up** (PR on `yudame/skills`, from Valor's
   account). One short paragraph under "Asking well": where the harness has
   no question tool (a chat, a headless session), ask in the conversation as
   one message: the question, the assumption, the options numbered with the
   recommendation first, and that a free answer is welcome; the reply is
   the answer. Nothing else in the skill changes. Merged by Valor after
   its CI passes; then `claude plugin marketplace update yudame` and
   `claude plugin update yudame-dev@yudame` on this Mac so turns load it.
2. **`converse.md`: shaping.** Item 2 becomes "an idea being shaped, or
   work". While an idea is still being talked through, shape it privately
   and start nothing. Once shaped, check whether anything only Tom (or the
   asker) could clarify would change what gets built: if not, start the
   task; if so, ask following the ask-me skill (name it; do not copy it),
   one question per message, most load-bearing first, until unblocked, then
   a short readback ending "Anything I missed? If not, I'll go ahead." The
   next message moving on is go. Never ask anyone to say go or approve a
   plan; implementation is Valor's. When Tom says "ask me", interview him
   the same way. Outside a live exchange, one at a time is the session's
   call. About eight lines.
3. **Persona "How to ask"** (`persona/conduct.md` and `docs/persona.md` in
   the same words for the rule): item 1 reads that one batch is the rule
   for a task asking while Tom is away (each round costs him a return);
   a live chat asks one at a time, as its stage says. The rationale
   sentence in `docs/persona.md` stays.
4. **Memory note.** `shape-before-build.md` belongs to the valor chat
   session (task f7f216cbef82's state), not this workspace; the delivery
   tells that session converse.md now carries the rules, so it trims its
   note to a pointer itself (one source, rule 4).

**Out of scope.** The wake-after-question timer (tomcounsell/ai#3654);
any change to how tasks ask (`.valor/question.md`); a chat-specific copy of
ask-me; checking a session's questions for compliance.

**Governance.** None added. The changes are instructions; nothing refuses,
gates or verifies.

## Tests

- `tests/test_kernel.py`
  `test_a_chat_session_gets_the_converse_channel_and_only_stop_counts`:
  the chat prompt names the ask-me skill and carries "Anything I missed? If
  not, I'll go ahead." (documented behavior of the stage).
- A persona render test: the rendered persona carries both rules (a task
  away batches; a live chat asks one at a time).
- Full suite under 5 minutes.
- ask-me PR: the repo's own checks pass; read the rendered SKILL.md.
- Not testable here: the done bar. Tom raises an idea in a real chat,
  Valor shapes it one question at a time, and Tom finds it works; recorded
  with the date when he does.
