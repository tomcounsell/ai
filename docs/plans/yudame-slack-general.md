# Yudame Slack #general as a team chat

Tom, 2026-10-23 10:46 UTC: "what would it take to add that as a comms
route for teamwork on general yudame business things".

## What is built

1. **The chat, under `valor` on the Cowboy.** `projects/valor.toml` lists
   `slack:T0B6E8JPE3C/C0B67T44KEF` in `chats` and `mentions`. Its `rooms`
   entry follows the Telegram Yudame team chat's: the company's team chat
   in Slack, for general Yudame business; answer whoever mentions you; no
   engineering or SDLC work starts here; web search and filing GitHub
   issues are fine; engineering requests go to the Eng group of the
   project they are about, else Tom.

   *Why valor, not yudame.* A chat is received only by a bridge on the
   machine its spec names. `yudame.toml` names the Pirate, which runs no
   new kernel and holds no Slack tokens; the one Slack bridge with the
   Yudame tokens (`com.valor.kernel.slack`) runs on the Cowboy. Moving
   `yudame.toml` to the Cowboy would also move its Telegram group here
   (and yuda.me's repo tasks) before the Pirate's rollout, which no one
   asked for. `valor.toml` is the Cowboy's spec, its client is `yudame`,
   and Tom's Slack DM, his business chat, already lives under it. One
   line moves the chat to `yudame.toml` when the Pirate takes over Slack.
   No second Slack connection is added.

2. **Slack's mention wakes it.** Slack stores a mention as
   `<@U0B6C6J735L>` (#general's own history shows Tom's tag that way),
   and `intake.MENTION` matches only the word "valor", so a tag would be
   `heard` and never wake the session. The fix is in the Slack bridge,
   which alone knows its own user id (`wire.user_id`): `inbound.build`
   writes Valor's own mention in the record's `text` (and the thread's)
   as Slack's labelled form, `<@U0B6C6J735L|valor>`. `MENTION` then
   matches it as written, unchanged, and the session reading the message
   sees that the mention is of itself. In `core/` only `MENTION`'s comment changes; send
   lookup never reads inbound text. `docs/bridges/slack.md`'s record
   table says so.

3. **Live.** After the merge, update this Mac and restart the Slack
   bridge (the `update` skill), since the inbound change is code in a
   resident process; `owns` reads the specs on every call. Then confirm
   in #general: a message naming Valor gets a reply, and one that does
   not gets none. The first gap fill of a chat with no recorded messages
   starts at its newest, so Tom's 10:45 tag is not replayed; Tom's next
   mention is the live test. `docs/bridges/slack.md` "Status" gains the
   connected channel.

## Out of scope

#all-yudame (C0B5YQEFG3Z); moving the Yudame project to the Cowboy; a
second workspace; any hard lock on engineering in the chat (no incident;
the room description carries the rule, as for every team chat).

## Tests

- `tests/test_kernel.py`, the real-specs test: `slack:T0B6E8JPE3C/C0B67T44KEF`
  is in valor's `chats` and `mentions`, and its room says "No engineering
  or SDLC work starts here".
- Slack inbound tests: a message containing `<@own>` comes out as
  `<@own|valor>`; another user's mention `<@U0OTHER>` is left alone; a
  message with no mention is unchanged; an already labelled
  `<@own|Valor>` is not labelled twice; a thread entry's text is labelled
  the same way.
- Intake: in a Slack mentions-only chat, a message with the labelled
  mention wakes the session (`message.steered`), one without is
  `message.heard`, a thread reply to Valor's post wakes it.

## Governance

None added: a chat listed in an existing spec field and a text fix.
