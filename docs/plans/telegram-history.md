# Chat history: a turn reads and searches Telegram and Slack

**Ask.** Tom, valor team chat, 2026-10-17: "i want you to have a telegram
tool that you can use to search chat history in case I say something that
sounds like i might be talking about a previous message." Then, same chat,
2026-10-17 01:00 UTC, while the plan was in critique (relayed into this
task as a steer): "in an older version of your codebase, we had a cli tool
called valor-telegram and another for valor-email. if that pattern is
appropriate you can adopt it. we'd also want slack as well. all accounts
are real human, btw". Slack is in scope on that word (rule 2).

**Premise.** A chat session is seeded only from the ledger's
`message.received` rows (`core/intake.py` `_chat_start`), so whatever was
said before the bridge recorded a chat is invisible. The Telethon session
and the Slack tokens sit in the kernel key directory, which the turn
sandbox denies (`docs/harnesses.md`, The turn sandbox), and each running
bridge holds its connection open. Loopback is open to turns. So each CLI
asks its running bridge over loopback, and the bridge reads with the client
it already has. The old `valor-telegram read` (tag `old-system`,
`tools/valor_telegram.py`) opened its own Telethon client; that is the part
not carried over.

## What is built

1. **Reading in the wires.** `Wire.history` (Telegram) gains
   `search: str | None = None`, passed to `client.get_messages(..., search=)`,
   Telegram's server-side search. Gap fill's calls are unchanged. The Slack
   wire's `history` gains `latest` (with `inclusive=False`, so strictly
   older) and a new `search(query, *, count)` calls `search.messages` with
   the user token. `search.messages` needs the user scope `search:read`,
   which the manifest (`bridges/slack/__main__.py`, `docs/bridges/slack.md`)
   lacks: it is added, and `docs/rollout.md` gains the step to reinstall the
   app so the token carries it. Until then a Slack search answers 503 with
   Slack's `missing_scope` sentence; history works without it.
2. **A read endpoint in each bridge.** `GET /history?chat=&q=&limit=&before=`
   on `127.0.0.1:<port>`, an aiohttp app (already used by `bridges/local`)
   started in the bridge's `run` beside its other tasks. The port is a
   constructor parameter; `__main__` passes `settings.telegram_read_port`
   (`VALOR_TELEGRAM_READ_PORT`, 8712) or `settings.slack_read_port`
   (`VALOR_SLACK_READ_PORT`, 8713); tests pass 0 and read the bound port, so
   no test touches a live bridge. `limit` defaults to 50, at most 100.
   `before` pages back (a Telegram id; a Slack ts, passed as `latest`); on
   Slack it does not apply with `q`, since `search.messages` pages by page
   number, and the CLI says so. Slack search queries
   `<q> in:<#channel>` and keeps matches in that channel. The answer is JSON
   newest first, each message with `id`, `sent_at`, `sender_id`,
   `sender_name`, `out`, `reply_to` (Telegram reply id, Slack `thread_ts`),
   `text` and media kind and name. A wire error answers 503 with its
   sentence. A bind failure fails the bridge's start like any other start
   error: no handling for a failure that has not happened.
3. **`valor-telegram read` and `valor-slack read`**: one stdlib Python
   script, `tools/valor_read.py`, installed into the shared `bin/` under
   both names (`WORKSPACE_TOOLS` becomes name to source); the name picks the
   channel and port. `valor-telegram read [CHAT] [--search TEXT] [--limit N]
   [--before ID] [--json]` prints oldest first, one line per message, through
   the same shape as `intake.chat_line`. No `send`: sends stay broker effects.
4. **Every turn has it, live sessions included.** `_turn_for`
   (`core/__main__.py`) installs the workspace tools into
   `Layout(Path(b.mirror).parent).root.parent / "bin"` before each turn, the
   path `_turn_for` already derives TMPDIR from, so a session provisioned
   before the merge gets them on its next turn. A Brief with no mirror is
   left alone. `_install_tools` writes each copy to a temp file from
   `tempfile.mkstemp` in `bin/` (today `.{name}.{pid}.tmp`, which two turns
   starting at once in one kernel share, failing the second `replace`). It also sets `VALOR_CHAT` in the turn's env from
   the Brief: `brief.chat` as `<channel>:<id>`, else the project spec's
   first chat. With no `CHAT` argument the CLI uses `VALOR_CHAT` when its
   channel matches, else exits with a sentence asking for the chat.
5. **Docs that tell sessions.** `skills/sdlc/converse.md` (rendered every
   turn, so live sessions get it): when a message seems to point at
   something earlier you were not shown, search the chat's history with
   `valor-telegram read --search` / `valor-slack read --search` before
   answering or asking. One line in `skills/sdlc/channel.md` for tasks.
   `docs/bridges/telegram.md` and `slack.md` document the endpoint;
   `tools/README.md` lists the CLIs as `read`; `docs/harnesses.md` names
   them next to `look`.

**Effect class.** `read`: no broker effect, no approval, no mark-read. No
token, access check or chat allowlist is added; each would be governance
with no incident behind it.

## Out of scope

Email: Valor's mailbox is already read and searched in every turn through
`gws gmail` and the `yudame:email` skill, so a `valor-email` would be a
second mechanism (rule 4). Ingesting fetched history into the ledger;
changing what `_chat_start` seeds; media bytes; listing chats.

## Tests

- Telegram endpoint over the emulator (route and client both take
  `search`): latest N newest first with sender, date, id, reply-to; `q`
  keeps only matches; `before` pages strictly older; `limit` 500 clamps to
  100; an empty chat answers `[]`; a disconnected wire answers 503 with
  its sentence; media kind; Valor's own messages carry `out`; gap fill's
  history call sends no search key.
- Slack endpoint over a fake wire (no Slack test double exists): history
  shape with `thread_ts` as `reply_to`; search builds `in:<#C…>` and drops
  matches from other channels; a DM (`D…`) search; `before` passes
  `latest` with `inclusive=False`; a `missing_scope` error is 503 with its
  sentence; the manifest's user scopes include `search:read`.
- CLI against a stub server on a free port: line format, oldest first,
  `VALOR_CHAT` default, a `slack:` `VALOR_CHAT` for `valor-telegram`
  exits with a sentence, a bridge not answering prints that plainly.
- `_turn_for`: installs both tool names into the `bin/` derived from
  `b.mirror` for an already-provisioned workspace; a Brief with no mirror
  runs its turn with no install and no `VALOR_CHAT`; `VALOR_CHAT` is the
  Brief's chat, else the spec's first chat, unset when the spec has none.
- Two `_install_tools` calls running at once in one process both succeed
  and leave whole, executable files.
- Full suite under 5 minutes.

## Done

After merge and the bridge restarts (and, for Slack search, the app
reinstall with `search:read`), Tom refers in the valor team chat to a
message from before the session began, and the session answers from the
history it fetched. Recorded with the date (rule 5).

**Governance.** None added. **Debt.** None added.
