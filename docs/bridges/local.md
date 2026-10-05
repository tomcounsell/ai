# Local chat bridge

The local chat bridge serves one page on `127.0.0.1` for chatting with
Valor on a Mac that has no Telegram session for Valor and no login to its
mailbox. It conforms to the bridge port defined in
[telegram.md](telegram.md#the-bridge-port): intake, performers, and the
outbox. This doc covers what is particular to the local chat.

**Status.** Built in `bridges/local/`. `python -m bridges.local run` runs
it under `bridge.serve`; `open` opens the page; `--plist` prints its
launchd job.

## What it serves

| Mechanism | Serves |
|---|---|
| Tom's messages recorded and bound like Telegram messages | Mission item 1: work arrives the way Tom sends it, on a Mac with no other channel |
| Every `local.send_message` an `act` effect, approved by reply on the page | Constraint "Bounded authority, metered spending" |
| The ledger is the platform: a send is shown once it has an outcome | Constraint "Reliable stop, recovery, and correction": nothing is lost or shown twice |

## The page

`python -m bridges.local open` reads the token and opens
`http://127.0.0.1:<local_port>/#<token>` (`VALOR_LOCAL_PORT`, default
8711) with `/usr/bin/open`. The token travels in the URL fragment, which
never reaches the server or its log. With no token file, `open` writes
nothing and says to start the bridge first.

The page shows Tom's messages, Valor's notices, and released sends, oldest
first. It polls `GET /log` every two seconds and adds each row whose
`event_id` it has not shown, in event id order. It renders every text with
`textContent`, so a send's text never runs. Clicking one of Valor's rows
makes the next message a reply to it: the page posts that row's
`message_id` as `reply_to`. A send keeps its text in the box and retries
with the same `id` until the bridge answers. On a `401`, or opened with no
token, the page stops and says to reopen it with
`python -m bridges.local open`.

## Routes

| Route | What it does |
|---|---|
| `GET /` | The page, with `Content-Security-Policy: frame-ancestors 'none'`, so no other page can frame it |
| `GET /chat.js` | The page's script |
| `GET /log` | The whole local chat, oldest first |
| `POST /send` | One message from Tom: `{id, text, reply_to}` |

`/log` and `/send` take the token in the `X-Valor-Token` header and answer
`401` without it; a token in the query string is not read. Nothing answers
`OPTIONS`, so a page on another origin cannot send the header. Each request
opens its own database connection and closes it.

Each `/log` row carries `event_id` (for deduplication), `message_id`,
`from` (`tom` or `valor`), `text`, and `reply_to`. Tom's rows are his
`message.received` rows on the `local` stream, with the page's `id` as
`message_id`. Valor's rows are `notice.sent` rows sent on `local`, with the
notice id as `message_id` and the text of its `notice.requested`, and
`effect.outcome` rows sent on `local`, with the effect id as `message_id`
and the text of its `effect.held`. `/log` sends the whole chat each poll:
event ids are identity values that commit out of order, so a cursor would
skip a row.

## Receiving

A posted message becomes one `Inbound`: `channel` and `chat_id` `local`,
`chat_kind` `dm`, `message_id` the page's random `id` (so a retried post
records once), `sender_id` `local`, `sender_name` `Tom`, `sent_at` the
server's clock in UTC, and `reply_to` as posted. Every local record is
verified and from the operator: the bridge writes one only after the
request's token matched (`secrets.compare_digest`) the token file, and the
chat has one member. The kernel binds it as it binds a Telegram message:
the exact words `approve` and `stop` bind on a reply, a near miss steers
and owes a notice, and a binding notice goes back in reply in the local
chat.

## Sending

| Action type | Class | Target | Payload |
|---|---|---|---|
| `local.send_message` | `act` | `local` | `text` |

It is refused when the target is not the local chat this machine owns
(`local`, while `operator_channel` is `local`), when the text is not a
string, and when the payload names files: the page shows text only. There is no length limit.

`perform` writes nothing outside the ledger and returns
`{"sent": [{"channel": "local", "chat_id": "local", "message_id":
<effect_id>}]}`; the outcome row is what shows it on the page. `lookup`
returns the same, since the ledger is the platform: a crash between the
intent and the outcome reconciles `done`, and the text is shown once. A
notice is marked sent with its notice id as the message id.

## Settings and files

- `operator_channel` `local` makes the local chat the operator's; then
  `operator_chat` is `local`, and the Telegram operator chat is not owned.
- `local_port` (`VALOR_LOCAL_PORT`, 8711) sits outside `DEV_PORTS` (8000
  to 8009) and the Postgres and Redis spans, so a turn cannot connect to it.
- `local-token` in the kernel key directory, mode 600, made by `run` when
  it is missing and never rewritten. It is written to a temporary file
  beside it and hard-linked into place, so the name holds a whole token or
  nothing.

## Threat model

- **The turn** reaches only the gateway, its own services, and 8000 to
  8009 on loopback ([harnesses.md](../harnesses.md)), so it cannot reach
  the bridge's port; that deny alone keeps a turn off this channel. The
  kernel key directory is denied to every turn, but the token is also in
  the browser's history.
- **The browser profiles are an opening of this Mac.** The profiles under
  `~/Library/Application Support` are neither read- nor write-denied to a
  turn. A turn can read the token there, and can leave an extension, a
  pref, or a startup page that Tom's browser loads on its next start and
  that reaches the port with the token. It can read the profiles' cookies
  and logins too ([sandbox-openings.md](../sandbox-openings.md)). No deny
  is added; Tom's answer is pending in
  [m2-4-local.md](../plans/m2-4-local.md), and accepting it for the proof
  of concept is assumed.
- **Framing.** A page a turn serves on 8000 to 8009 cannot frame the chat
  page: `GET /` sends `frame-ancestors 'none'`.
- **Why not a terminal chat.** A turn can write to Tom's terminal devices
  (`/dev/ttys*`), so a terminal approval surface can be overprinted with a
  forged notice at once. The browser route needs a browser restart and has
  to get past the browser's own protections.
- **Other local processes and web pages** are what the token is for.
  Another macOS user reaches `127.0.0.1` but reads neither the key
  directory nor Tom's browser history. A web page Tom visits cannot send
  the token header without a CORS preflight the bridge never answers. A
  process of Tom's own user outside the sandbox can read the key directory:
  the accepted opening of one macOS user
  ([sandbox-openings.md](../sandbox-openings.md)).

## Gaps

- Files in either direction; more than one local chat; reaching the page
  from another device; browser notifications; paging long history;
  markdown rendering.
- A merge approved on the page fails with the GitHub credential named when
  this Mac holds none.
- An approved `email.send` waits for an email bridge this Mac does not
  run; nothing is sent and the page shows no outcome.
- The local and Telegram channels cannot both be the operator channel.

## The implementation

| Module | What it holds |
|---|---|
| `__init__.py` | `LocalBridge`: perform, lookup, the outbox loop, the HTTP server, the token |
| `__main__.py` | `run`, `open`, `--plist` |
| `chat.html`, `chat.js` | The page |
