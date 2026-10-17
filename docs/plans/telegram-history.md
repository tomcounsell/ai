# Telegram history: a turn reads and searches a chat

**Ask.** Tom, valor team chat, 2026-10-17: "i want you to have a telegram
tool that you can use to search chat history in case I say something that
sounds like i might be talking about a previous message." A chat session is
seeded only from the ledger's `message.received` rows (`core/intake.py`
`_chat_start`), so anything said before the bridge recorded the chat is
invisible.

**Premise.** The Telethon session lives in the kernel key directory, which
the turn sandbox denies (`docs/harnesses.md`, The turn sandbox), and the
running bridge holds it open. So a turn cannot, and must not, open a second
client. Loopback is open to turns. The tool therefore asks the running
bridge, over loopback, to read with the `Wire` it already has.

## What is built

1. **`Wire.history` gains `search: str | None = None`.** `TelethonWire`
   passes it to `client.get_messages(chat_id, limit=, offset_id=, search=)`
   (Telegram's server-side search). One method, not a second one; gap fill
   calls it unchanged. The test emulator's `history` filters by substring.
2. **The bridge serves `GET /history`** on `127.0.0.1:settings.telegram_history_port`
   (`VALOR_TELEGRAM_HISTORY_PORT`, default 8712), an aiohttp app (already a
   dependency, as in `bridges/local`) started in `TelegramBridge.run` beside
   `_consume` and `_keep_connected`. Query: `chat` (id, required), `q`
   (text), `limit` (default 50, at most 100, Telegram's page), `before`
   (message id, to page back). It answers JSON, newest first: per message
   `id`, `date` (UTC ISO), `sender_id`, `sender_name`, `out`, `reply_to`,
   `text`, and `media` kind and name. A `WireError` answers 503 with its
   sentence (not connected, flood wait N s, refused: ...). A bind failure
   on start is logged and the bridge runs on without the endpoint; it
   never stops receiving or sending.
3. **`tools/telegram-history`**, a workspace tool installed into every
   turn's `bin/` like `look` (`core/workspace.py` `WORKSPACE_TOOLS`):
   stdlib Python, `telegram-history [CHAT] [--search TEXT] [--limit N]
   [--before ID]`. It prints one line per message, oldest first, in the
   shape of `intake.chat_line` (`[when] who (message N, replying to M):
   text`). With no `CHAT` it uses `$VALOR_CHAT`.
4. **`VALOR_CHAT` in the turn environment** (`workspace.harness_env`'s
   caller in the turn path): the session's own chat id when the Brief has a
   Telegram `chat`, else the project's first Telegram chat from its spec's
   `chats`. Unset when the project has none; the tool then asks for a chat.
5. **Docs that tell sessions.** `skills/sdlc/converse.md`: when a message
   seems to point at something earlier that you were not shown, run
   `telegram-history --search ...` (or with no search for the latest)
   before answering or asking. `_chat_start`'s first line names the tool
   and the chat id. `skills/sdlc/channel.md` gets one line so tasks know it
   too. `docs/bridges/telegram.md` documents the endpoint (Receiving, a
   "Reading history for a turn" section); `tools/README.md` lists the tool
   as `read`; `docs/harnesses.md` names it next to `look`.

**Effect class.** `read`. It is not a broker effect, needs no approval, and
writes nothing to Telegram (no mark-read). No access check, token, or chat
allowlist is added: any limit would be governance with no incident behind
it. The endpoint binds loopback only, the same exposure every other
loopback service on the Mac has.

## Out of scope

Slack and email history; ingesting fetched history into the ledger; changing
what `_chat_start` seeds; a memory or index of chats; reading media bytes.

## Tests

- `tests/test_telegram_history.py` over the emulator (`tests/telegram_emulator.py`):
  latest N newest-first with sender, date, id, reply-to; `q` returns only
  matching messages; `before` pages strictly older ids; `limit` above 100
  is clamped; a chat with no messages answers an empty list; a
  disconnected wire answers 503 with the sentence; a message with media
  carries its kind; Valor's own messages carry `out`.
- Gap fill still passes unchanged (`tests/test_telegram_inbound.py`,
  `test_telegram_pipeline.py`), showing `history` without `search` is as
  before.
- The CLI against a stub server on a free port: the line format, oldest
  first, `$VALOR_CHAT` default, no chat and no `$VALOR_CHAT` exits non-zero
  with a plain sentence, bridge not running prints "the Telegram bridge is
  not answering on 127.0.0.1:PORT".
- `VALOR_CHAT`: a session Brief with a Telegram chat sets it to that chat;
  a task on a project with chats sets the first Telegram one; a project
  with none leaves it unset; a Slack session falls back to the project's
  Telegram chat.
- Full suite under 5 minutes.

## Done

Tom, in the valor team chat after merge and the rollout's bridge restart,
says something that refers to a message from before the session began, and
the session answers it from the history it fetched. Recorded with the date.
Suites are evidence, not the finish (rule 5).

**Governance.** None added: no check, gate, hook, validator, approval step
or allowlist. **Debt.** None added.
