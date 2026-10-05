---
tracking: none
slug: m2-4-local
type: build
status: planned
critique_rounds: 2
review_rounds: 2
governance_grant: none
---

# 2.4: the local chat bridge

Task 2.4 of [valor-rebuild.md](valor-rebuild.md), milestone 2. Tom's
request: run the Valor system on his own Mac, which has no Telegram
session and no login to Valor's mailbox, and chat with Valor there. This
task builds the smallest bridge for that, `bridges/local/`, to the bridge
port ([m2-1-port.md](m2-1-port.md), owned by
[bridges/telegram.md](../bridges/telegram.md)), and names what else this
Mac needs to run the kernel end to end with it.

The interface is one page served by the bridge on `127.0.0.1` only
(`VALOR_LOCAL_PORT`, default 8711). Tom opens it with
`python -m bridges.local open`. The page shows his messages, Valor's
notices, and released sends, oldest first; clicking a message of Valor's
makes the next message a reply to it, which is how Telegram binds replies.

## Stakes

`critique_rounds: 2`, `review_rounds: 2`. The task adds a channel to
`core/intake.py`'s sender verification (who counts as Tom) and to the
kernel's declarations (`DECLARED`, `LIMITS`, `owned`), so a mistake lets
something other than Tom start work or approve a send. The token and the
`Host` test below are this channel's authentication, the part MTProto
plays for Telegram; they add no check, gate, or review step to the
pipeline, so no governance grant is asked.

## Done, as evidence

Against the test database and real local services (`VALOR_TEST_DB=
valor_rebuild_test_24builder`, ports 6430 to 6439):

- A message posted to the running bridge's HTTP server with the token
  records one `message.received` on the `local` stream, `verified: true`,
  and `intake.bind` starts a task from it under project `valor`.
- A held `local.send_message` gets an effect notice on the page; a reply
  `approve` to that notice, posted through the page's endpoint, writes
  `approval.granted` with `via: "local"`, then `release.requested` with
  owner `local`; the bridge's outbox performs it and the page's log
  shows the sent text, once.
- A reply `stop` to any notice of a task stops it; a near miss (`Approve!`)
  steers and owes a notice that comes back to the local chat.
- Under the real `sandbox-exec` turn profile, a connect to the bridge's
  port on `127.0.0.1` is refused and the token file cannot be read.
- The suite and both ruff checks are green.

On this Mac, by Tom, once merged (the manual run): the kernel and the
bridge run under launchd on the real ledger; Tom types a request on the
page, a task starts, a notice comes back; a task's held
`local.send_message` is approved from the page and its text appears. The
record goes in this file.

## Threat model

- **The turn** runs as Tom's user under the turn profile. On loopback it
  reaches only the gateway, its own services, and 8000 to 8009
  ([harnesses.md](../harnesses.md), Loopback connects), so it cannot reach
  the bridge's port; the kernel key directory, where the token lives, is
  denied entirely. The turn controls the text and target of a send it
  requests and nothing on the page: the page renders that text with
  `textContent` under a `script-src 'self'` policy, so a send's text never
  runs. The kernel never reads anything a turn owns here.
- **Why not a terminal chat.** A turn can write to Tom's terminal devices
  (`/dev/ttys*` are his, mode 620, and no profile denies them), so a
  terminal approval surface can be overprinted with a forged notice. A
  page in the browser cannot be written by a turn.
- **Other local processes.** Another macOS user reaches `127.0.0.1` but
  not the token. A web page Tom visits can send requests to `127.0.0.1`
  but cannot send the `X-Valor-Token` header without a CORS preflight
  the bridge never answers, and a DNS-rebound name fails the `Host`
  test (`127.0.0.1:<port>` or `localhost:<port>` only). A process of
  Tom's own user outside the sandbox can read the key directory, as it
  can read the Telegram session and `pgpass`: the accepted opening of
  one macOS user (Tom, 2026-10-01, sandbox-openings.md).
- **What verifies Tom.** A record on the `local` stream is written only by
  the bridge, after the request's token matched (`secrets.compare_digest`)
  the mode-600 file `local-token` in the kernel key directory, and only a
  holder of the kernel's database credential can write the ledger at
  all. So `intake.VERIFY["local"]` is true, and every verified local
  record is from the operator: the chat has one member.

## What is built

### `core/` (the port's side)

- `core/intake.py`: `VERIFY["local"]` true; `_from_operator` is the
  `verified` flag for `local`; `owned("local")` is `["local"]` when
  `operator_channel` is `local`, and `owned("telegram")` lists
  `operator_chat` only when `operator_channel` is `telegram`. The exact
  words `approve` and `stop` bind on `telegram` and `local` replies. A
  binding notice goes back to the message's own chat on `telegram` and
  `local`, and to the operator's channel and chat for email (today it
  names `telegram` outright).
- `core/bridge.py`: `LIMITS["local"]` with no text or file limit, and
  `DECLARED["local.send_message"]`: `act`, owner `local`, usage
  "target `local`, payload `{"text": "..."}`; sent once Tom approves",
  refused when the target is not an owned local chat, the text is not a
  non-empty string, or the payload names files (the page shows text
  only).
- `core/settings.py`: `local_port` (`VALOR_LOCAL_PORT`, 8711, outside the
  dev ports and the Postgres and Redis spans) and `local_tokenfile`,
  derived from `pg_passfile` beside the other keys.

### `bridges/local/`

- `__init__.py`, the `Bridge`: `channel = "local"`. `run` starts an
  `aiohttp.web` server on `127.0.0.1:local_port` and consumes the outbox.
  A `NoticeDue` is marked sent with `message_id` = its `notice_id`; a
  `Release` is performed through `Outbox.perform`, and `perform` returns
  `sent` with `message_id` = the effect id. Being shown on the page is
  having a `notice.sent` or `effect.outcome` row, so both are ledger
  writes only. `lookup` returns None: a dangling intent was never shown,
  so the broker records it `failed` and nothing shows twice. `tick` does
  nothing.
- Routes: `GET /` and `GET /chat.js` (static, no secret); `GET /log?after=N`
  (the chat's rows after event id N: local `message.received`, and
  `notice.sent` and `effect.outcome` rows sent on `local`, with their
  text from `notice.requested` and `effect.held`); `POST /send`
  (`{id, text, reply_to}`; `id` is the page's own random id, so a retried
  post records once). Every route checks `Host`; `/log` and `/send` check
  the token header; nothing answers `OPTIONS`.
- `__main__.py`: `run`, `open` (makes the token file, mode 600, when
  missing, then opens `http://127.0.0.1:<port>/#<token>` with
  `/usr/bin/open`; the fragment never reaches the server or its log), and
  `--plist` (label `com.valor.kernel.local`, `KeepAlive`, the PG settings,
  `VALOR_MACHINE`, `VALOR_OPERATOR_CHANNEL`, `VALOR_LOCAL_PORT`).
- `chat.html` and `chat.js`: a list and a text box; polls `/log` every
  two seconds; renders with `textContent` only.

### Docs

`docs/bridges/local.md` (status quo, the port as this bridge uses it,
the threat model above), `bridges/README.md` (entry points, `local/`),
`docs/bridges/telegram.md` (the port's `channel` field and declared-type
table gain `local`), the binding table in `core/intake.py`'s docstring.

## Running it on this Mac

What the plan found missing, and the order of the manual run:

1. The kernel database is migrated here (1.1 rollout), and `pgpass` and
   `judgement-keys` are in `~/.config/valor-kernel`; no `claude-token`,
   so the gateway uses the `claude` login from the Keychain.
2. Shell settings: `VALOR_OPERATOR_CHANNEL=local`, `VALOR_OPERATOR_CHAT=local`
   beside `PGPASSFILE`.
3. `python -m core serve --plist` and `python -m bridges.local --plist`,
   each loaded with `launchctl`; no kernel job is loaded here today.
4. `python -m bridges.local open`.

## Tech debt absorbed

- `owned("telegram")` lists the operator chat whatever the operator
  channel is.
- A binding notice for an email names `telegram`, so on a machine whose
  operator channel is another it goes nowhere.
- `bridges/README.md` lists only the Telegram entry point.

## Left out

Files in either direction; more than one local chat; reaching the page
from the phone or the LAN; browser notifications; paging long history;
markdown rendering; merges released from this Mac (no GitHub credential
here; a merge approved on the page fails with the credential named);
running the local and Telegram channels as operator channel at once.

## Tests

`tests/test_local_bridge.py`, over the real bridge server on a port of
6430 to 6439 and the test database:

- Posted message to task, end to end through `intake.bind`.
- The held send: notice shown, `approve` reply, release, outcome, shown
  once in `/log`.
- `stop` reply stops; `Approve!` steers and its notice is on `local`.
- No token, a wrong token, and a token in a query string instead of the
  header: 401, nothing recorded. A right token with `Host: evil.test`:
  403. `OPTIONS /send` carries no `Access-Control-Allow-*`.
- The same page `id` posted twice records once.
- A forced crash between intent and outcome: on restart the reconcile
  writes `failed`, and `/log` never shows the text.
- `local.send_message` refused for target `telegram-chat`, empty text,
  a non-string text, and any `files`.
- A send whose text is `<img src=x onerror=...>` comes back from `/log`
  as the same string; the page's script holds no `innerHTML`, and `/`
  carries the `Content-Security-Policy` header.
- `owned`: with operator channel `local`, `owns("local", "local")` and
  not `owns("telegram", <operator_chat>)`; with `telegram`, the reverse.
- An email binding notice goes to the operator channel.
- `open` makes the token file mode 600 and never rewrites one that exists.
- Under real `sandbox-exec` with an expanded turn profile (as
  `tests/test_demo_sandbox.py` does): a connect to the bridge port is
  refused and a read of the token file fails.

## Questions for Tom

1. A page on `127.0.0.1` rather than a terminal chat? Assumed: the page,
   for the reason in the threat model.
2. Run the kernel and the bridge under launchd on this Mac's real ledger,
   or only by hand during the manual run? Assumed: launchd, both jobs,
   unloaded again after the run if you prefer.
3. Should this Mac hold the GitHub push credential so merges work from
   here? Assumed: no, not for this proof of concept.

## Decided by default

- The page over a terminal: a turn can write Tom's terminals and cannot
  reach a loopback port outside its allowlist.
- One local chat with the fixed id `local`, which the usage line names, so
  a turn knows the target without seeing settings.
- Outbound message ids are notice and effect ids, so a reply binds by
  structure and a replayed `sent` is idempotent; Tom never types an id.
- `approve` and `stop` bind on local replies exactly as on Telegram; a
  bare `approve` that replies to nothing starts a task, as on Telegram.
- `lookup` returns None (a send not recorded was never shown), so a crash
  in that window ends `failed`, never shown twice.
- The token is passed in the URL fragment, so it is in the browser's
  history: readable only by Tom's user, which already reads the key
  directory.
- Port 8711, a setting.
