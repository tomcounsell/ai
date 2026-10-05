---
tracking: none
slug: m2-4-local
type: build
status: built
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
something other than Tom start work or approve a send. The token below is
this channel's authentication, the part MTProto plays for Telegram; it
adds no check, gate, or review step to the pipeline, so no governance
grant is asked.

## Done, as evidence

Against the test database and real local services (`VALOR_TEST_DB=
valor_rebuild_test_24builder`, ports 6530 to 6539):

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
  port on `127.0.0.1` is denied (the existing probe, with `local_port`
  added).
- The suite and both ruff checks are green.

On this Mac, by Tom, once merged (the manual run): the kernel and the
bridge run under launchd on a fresh `valor_rebuild` (the older ledger
backed up and renamed `valor_rebuild_pre24`); Tom
types a request on the page, a task starts, a notice comes back; a task's
held `local.send_message` is approved from the page and its text appears.
The record goes in this file.

## Threat model

- **The turn** runs as Tom's user under the turn profile. On loopback it
  reaches only the gateway, its own services, and 8000 to 8009
  ([harnesses.md](../harnesses.md), Loopback connects), so it cannot reach
  the bridge's port. That deny alone is what keeps a turn off this
  channel. The token does not: the kernel key directory is denied to
  every turn, but the token is also in the browser's history (the URL
  fragment), and the browser profiles under `~/Library/Application
  Support` are neither read- nor write-denied (`core/workspace.py`), so a
  turn may read the token there. Directly, holding it gets a turn nothing
  while the port is out of reach. The turn controls the text and target
  of a send it requests and nothing else on the page; the page renders
  that text with `textContent`, so a send's text never runs. The kernel
  never reads anything a turn owns here.
- **The browser profiles.** A turn can also write a profile: leave an
  extension, a pref, or a startup page that Tom's unsandboxed browser
  loads on its next start, and from there reach the port with the token
  it read. It can read the profiles' cookies and logins too. This Mac is
  Tom's own, so that is his browsing data, not Valor's. The plan adds no
  deny for it; it is an opening of this Mac, recorded in
  `docs/bridges/local.md`, and Question 2 asks Tom.
- **Framing.** A page a turn serves on 8000 to 8009, which Tom opens to
  see its work, could frame the chat page with its token. `GET /` sends
  `Content-Security-Policy: frame-ancestors 'none'`, so the browser never
  renders the chat page inside another page.
- **Why not a terminal chat.** A turn can write to Tom's terminal devices
  (`/dev/ttys*` are his, mode 620, and no profile denies them), so a
  terminal approval surface can be overprinted with a forged notice at
  once, needing nothing loaded later. The browser route above needs a
  browser restart and has to get past the browser's own protections, so
  the page is still the better surface.
- **Other local processes and web pages.** This is what the token is for.
  Another macOS user reaches `127.0.0.1` but reads neither the key
  directory nor Tom's browser history. A web page Tom visits can send
  requests to `127.0.0.1` but cannot send the `X-Valor-Token` header
  without a CORS preflight the bridge never answers; a DNS-rebound name
  is the same case, since its requests carry no token either. A process
  of Tom's own user outside the sandbox can read the key directory, as it
  can read the Telegram session and `pgpass`: the accepted opening of one
  macOS user (Tom, 2026-10-01, sandbox-openings.md).
- **What verifies Tom.** A record on the `local` stream is written only by
  the bridge, after the request's token matched (`secrets.compare_digest`)
  the mode-600 file `local-token` in the kernel key directory, and only a
  holder of the kernel's database credential can write the ledger at
  all. So `intake.VERIFY["local"]` is true, and every verified local
  record is from the operator: the chat has one member.

## What is built

### `core/` (the port's side)

- `core/intake.py`:
  - `VERIFY["local"]` true; `_from_operator` is the `verified` flag for
    `local`.
  - `owned("local")` is `["local"]` when `operator_channel` is `local`
    (keyed on the channel, not the chat), and `owned("telegram")` lists
    `operator_chat` only when `operator_channel` is `telegram`.
  - One flag in `_bind` and `_notice`, `replies = channel in ("telegram",
    "local")`, the channels where Tom replies to a message. On those, the
    exact words `approve` and `stop` bind, and a binding notice goes back
    in reply (`reply_to` the message's id) in the message's own channel
    and chat. On email, a binding notice goes to the operator's channel
    and chat (today `_notice` names `telegram` outright), and the near
    miss text names the operator channel as Tom sees it: "by reply in
    Telegram" or "by reply on the local chat page".
- `core/bridge.py`: `LIMITS["local"]` with no text or file limit, and
  `DECLARED["local.send_message"]`: `act`, owner `local`, usage
  "target `local`, payload `{"text": "..."}`; sent once Tom approves",
  refused when the target is not an owned local chat, the text is not a
  string, or the payload names files (the page shows text
  only).
- `core/settings.py`: `local_port` (`VALOR_LOCAL_PORT`, 8711), with a
  comment that it stays outside `DEV_PORTS` (8000 to 8009, which a turn
  reaches) and the Postgres and Redis spans; `local_tokenfile`, derived
  from `pg_passfile` beside the other keys; and `operator_chat` stays a
  field whose default factory returns `local` when
  `VALOR_OPERATOR_CHANNEL` is `local`, and otherwise `VALOR_OPERATOR_CHAT`
  or None, so the chat has one name. Tests that set fields with
  `configure` set both: `configure(operator_channel="local",
  operator_chat="local")`.

### `bridges/local/`

- `__init__.py`, the `Bridge`: `channel = "local"`. `run` makes the token
  file (mode 600) when it is missing, starts an `aiohttp.web` server on
  `127.0.0.1:local_port`, and consumes the outbox. A `NoticeDue` is
  marked sent with `message_id` = its `notice_id`; a `Release` is
  performed through `Outbox.perform`, and `perform` returns
  `{"sent": [{"channel": "local", "chat_id": "local", "message_id":
  <effect_id>}]}`. Being shown on the page is having a `notice.sent` or
  `effect.outcome` row, so both are ledger writes only. The ledger is the
  platform, so `lookup` returns the same `sent` as `perform`: a crash
  between intent and outcome reconciles `done`, and the approved text is
  shown once. `tick` does nothing.
- Each HTTP request opens its own database connection and closes it (as
  `conn()` in `bridges/telegram/kernel.py`); no handler touches the
  outbox's connections.
- Routes: `GET /` (static, no secret, with `Content-Security-Policy:
  frame-ancestors 'none'`) and `GET /chat.js`; `GET /log` (the whole
  local chat each poll, oldest first: local `message.received`, and
  `notice.sent` and `effect.outcome` rows sent on `local`, with their
  text from `notice.requested` and `effect.held`); `POST /send` (`{id,
  text, reply_to}`). Each `/log` row carries `event_id` (for
  deduplication), `message_id` (the page's id for Tom's messages, the
  `notice_id` or the `effect_id` for Valor's), `from` (`tom` or
  `valor`), `text`, and `reply_to`. `/log` and `/send` check the token
  header; nothing answers `OPTIONS`.
- A posted message becomes an `Inbound` with `channel` `local`, `chat_id`
  `local`, `chat_kind` `dm`, `message_id` the page's own random `id` (so
  a retried post records once), `sender_id` `local`, `sender_name` `Tom`,
  `sent_at` from the server's clock in UTC, and `reply_to` as posted.
- `__main__.py`: `run`; `open` (reads the token file and opens
  `http://127.0.0.1:<port>/#<token>` with `/usr/bin/open`; the fragment
  never reaches the server or its log; with no token file it says to
  start the bridge first); and `--plist`, the Telegram bridge's `plist()`
  pattern (label `com.valor.kernel.local`, `KeepAlive`, `RunAtLoad`, log
  in `log_dir`) with `PLIST_ENV` `VALOR_PGHOST`, `VALOR_PGPORT`,
  `VALOR_DB`, `VALOR_PG_PASSFILE`, `VALOR_MACHINE`,
  `VALOR_DEFAULT_MACHINE`, `VALOR_PROJECTS`, `VALOR_OPERATOR_CHANNEL`,
  `VALOR_LOCAL_PORT`, `VALOR_SERVE_TICK_S`.
- `chat.html` and `chat.js`: a list and a text box; polls `/log` every
  two seconds and adds rows whose event id it has not shown; renders with
  `textContent` only. Clicking a row of Valor's posts that row's
  `message_id` as the next send's `reply_to`. A send keeps its text in
  the box and retries with the same `id` until the bridge answers. On a
  401 the page stops polling and sending and says to reopen it with
  `python -m bridges.local open`; opened without a token in the
  fragment, it says the same.

### Docs

`docs/bridges/local.md` (status quo, the port as this bridge uses it,
the threat model above, the browser profiles as an opening of this
Mac), `bridges/README.md` (entry points, `local/`),
`docs/bridges/telegram.md` (the port's `channel` field and declared-type
table gain `local`), the binding table in `core/intake.py`'s docstring.

## Running it on this Mac

What the plan found missing, and the order of the manual run:

1. `pgpass` and `judgement-keys` are in `~/.config/valor-kernel`; no
   `claude-token`, so the gateway uses the `claude` login from the
   Keychain.
2. Shell settings beside `PGPASSFILE`: `VALOR_OPERATOR_CHANNEL=local`;
   `VALOR_DB` unset.
3. The rollout step, so the kernel resumes nothing from this Mac's older
   ledger: `python -m core backup`; then, as the owner with nothing
   connected, `ALTER DATABASE valor_rebuild RENAME TO
   valor_rebuild_pre24` (kept, since the ledger is never emptied); then
   `python -m core migrate`, which creates a fresh `valor_rebuild` under
   the existing `pgpass` lines and scram rules. The renamed ledger falls
   under the cluster's `trust` rules, which on this one-user Mac matches
   the accepted opening of one macOS user.
4. `python -m core serve --plist` and `python -m bridges.local --plist`,
   each loaded with `launchctl`; no kernel job is loaded here today. The
   bridge makes its token on start.
5. `python -m bridges.local open`.

## Tech debt absorbed

- `owned("telegram")` lists the operator chat whatever the operator
  channel is.
- A binding notice for an email names `telegram`, so on a machine whose
  operator channel is another it goes nowhere; the near miss notice for
  an email says approvals come by Telegram on every machine.
- `bridges/README.md` lists only the Telegram entry point.

## Left out

Files in either direction; more than one local chat; reaching the page
from the phone or the LAN; browser notifications; paging long history
(the whole chat is sent each poll); markdown rendering; merges released
from this Mac (no GitHub credential here; a merge approved on the page
fails with the credential named); an approved `email.send`, which waits
for an email bridge this Mac does not run, so nothing is sent and the
page shows no outcome; running the local and Telegram channels
as operator channel at once.

## Tests

`tests/test_local_bridge.py`, over the real bridge server on a port of
6530 to 6539 and the test database:

- Posted message to task, end to end through `intake.bind`, with the
  `Inbound` fields above.
- The held send: notice shown, `approve` reply, release, outcome, shown
  once in `/log`. The `reply_to` posted is taken from the notice's
  `/log` row (`message_id`), not from the ledger.
- `stop` reply stops; `Approve!` steers and its notice is on `local`, in
  reply to the message. `reply_to` again comes from `/log`.
- No token, a wrong token, and a token in a query string instead of the
  header: 401, nothing recorded. `OPTIONS /send` carries no
  `Access-Control-Allow-*`; `GET /` carries `frame-ancestors 'none'`.
- The same page `id` posted twice records once.
- A forced crash between intent and outcome: on restart the reconcile
  writes `done` with the same `sent`, and `/log` shows the text once.
- `/log` returns rows whose event ids commit out of order: every row is
  there.
- `local.send_message` refused for target `telegram-chat`, empty text,
  a non-string text, and any `files`.
- A send whose text is `<img src=x onerror=...>` comes back from `/log`
  as the same string.
- `owned`: with operator channel `local`, `owns("local", "local")`,
  `operator_chat` is `local`, and not `owns("telegram", <operator_chat>)`;
  with `telegram`, the reverse. The `operator_chat` derivation is tested
  by building `Settings()` under a monkeypatched environment, as
  `tests/test_settings.py` does.
- An email binding notice and its near miss text go to and name the
  operator channel.
- `run` makes the token file mode 600 and never rewrites one that exists;
  `open` with no token file writes nothing.

The sandbox side reuses `tests/test_demo_sandbox.py`:
`test_a_task_turn_reaches_its_own_services_and_clone_and_nothing_else`
gains `settings.local_port` in its port probe, expected `denied`; the
token file sits in the kernel key directory, whose deny
`test_a_task_turn_cannot_reach_the_kernels_credential_data_or_dumps` and
`test_by_default_a_task_profile_denies_the_kernel_paths_its_settings_name`
already prove.

## Questions for Tom

1. Should this Mac hold the GitHub push credential so merges work from
   here? Assumed: no, not for this proof of concept.
2. On this Mac a turn can read and write your browser profiles, cookies
   and logins included. Accept that for the proof of concept, or deny
   them to turns? Assumed: accept for the proof of concept; no deny is
   added.

## Decided by default

- The page over a terminal: a turn can write Tom's terminals and cannot
  reach a loopback port outside its allowlist.
- The kernel and the bridge run under launchd for the manual run, and may
  be unloaded after it.
- The proof of concept runs on a fresh `valor_rebuild`: during rollout
  the older ledger is backed up, renamed `valor_rebuild_pre24`, and
  `migrate` makes a new one under the existing credentials, so the older
  demo and replay tasks on this Mac stay out of the resident kernel.
- One local chat with the fixed id `local`, which the usage line names, so
  a turn knows the target without seeing settings; the operator chat is
  derived from it, never set apart.
- Outbound message ids are notice and effect ids, so a reply binds by
  structure and a replayed `sent` is idempotent; Tom never types an id.
- `approve` and `stop` bind on local replies exactly as on Telegram; a
  bare `approve` that replies to nothing starts a task, as on Telegram.
- `lookup` returns what `perform` returns, since the ledger is the
  platform: an approved send survives a crash and is shown once.
- `/log` sends the whole chat each poll: event ids commit out of order,
  so a cursor would skip rows.
- The token is passed in the URL fragment, so it is in the browser's
  history; a turn may read it there, and reaches the port with it only
  through the browser profile (Question 2).
- Port 8711, a setting.

## Build record

Built on `m2-4-local` from `d0e9d7e9c`, with round 2's findings taken as
the lead decided.

- `core/settings.py`: `local_port` (8711), `local_tokenfile` (a property
  beside the other keys), and `operator_chat` a field whose default
  factory returns `local` when `VALOR_OPERATOR_CHANNEL` is `local`.
- `core/intake.py`: `VERIFY["local"]`, `REPLIES = ("telegram", "local")`
  in `_bind` and `_notice`, `owned` keyed on `operator_channel`, the near
  miss for an email naming "by reply in Telegram" or "by reply on the
  local chat page", and the docstring's binding table.
- `core/bridge.py`: `LIMITS["local"]` (none) and
  `DECLARED["local.send_message"]` with `_refuse_local`.
- `bridges/local/`: `LocalBridge` (perform and lookup return the same
  `sent`; notices marked sent with their notice id), an `aiohttp` server
  on `127.0.0.1` with one connection per request, `/log` rows carrying
  `event_id`, `message_id`, `from`, `text`, `reply_to`; `GET /` with
  `frame-ancestors 'none'`; `run`, `open`, `--plist`; `chat.html` and
  `chat.js` (no JavaScript runtime on this Mac, so the script was not
  run; no test runs it, as planned).
- Docs: `docs/bridges/local.md` (new, with the browser profile opening),
  `docs/bridges/telegram.md` and `docs/bridges/email.md` (the operator
  channel is Telegram or the local page), `docs/architecture.md`,
  `bridges/README.md`.
- Tests: `tests/test_local_bridge.py`, 18 tests, every one the plan
  names, each over the bridge run through `serve` on a port of 6530 to
  6539; `reply_to` comes from `/log`. The sandbox probe in
  `tests/test_demo_sandbox.py` gains `settings.local_port`, `denied`.
- Suite (`VALOR_TEST_DB=valor_rebuild_test_24builder`,
  `VALOR_TEST_PORTS=6530-6539`): 1448 passed, 55 skipped, 2 failed, 62
  errors. The 62 errors and one failure are the mail tests (dovecot is
  not installed on this Mac); the other failure is the Pi test (no
  `/opt/homebrew/bin/node`). All three fail the same way at `d0e9d7e9c`.
  Both ruff checks pass.
- Rollout is not performed: the backup, the rename to
  `valor_rebuild_pre24`, `migrate`, the two plists, and the manual run
  stay for Tom's Mac.

## Patch round 1

Review round 1 (of 2) asked for changes; the test check passed with one
note. Each was taken:

1. `_refuse_local` refused empty text with no source or function behind
   it: the page shows an empty row. Dropped, with its test case; the
   text must be a string.
2. The browser profile opening was recorded only in
   `docs/bridges/local.md`. `docs/sandbox-openings.md` now names it: turns
   can read and write the browser profiles, cookies and logins included,
   on every machine. `local.md` links it. No deny.
3. `ensure_token` created the file and then wrote it, so a crash between
   the two left an empty token that refused every request until the file
   was deleted by hand. The token is now written and synced to a
   temporary file beside it, then hard-linked into place, so an existing
   token is still never rewritten and the name holds a whole token or
   nothing. A new test crashes the link and finds no token file, then a
   start that makes one.
4. Test check note: the local tests fixed their ports. They now take
   their span from `VALOR_TEST_PORTS` when set (`tests/ports.py`),
   keeping 6530 to 6539 and 6540 to 6549 as defaults.

5. A full run found the page test counting every `approve` in the
   session's test ledger, which the bridge and edge tests also post to;
   it now counts only the replies to its own notice.

Suite (`VALOR_TEST_DB=valor_rebuild_test_24builder`,
`VALOR_TEST_PORTS=6530-6539`): 1464 passed, 55 skipped, 3 failed, 62
errors. The 62 errors and the mailserver failure are the mail tests
(dovecot is not installed on this Mac), one failure is the Pi test (no
node), and the third was the page test above; after its fix, the local,
edge, page, sandbox, and intake tests run together: 71 passed. Both ruff
checks pass.

## Critique rounds

### Round 1 (of 2): revise

Findings, in brief, and what changed:

1. `lookup` returning None lost an approved send on a crash
   (`core/broker.py` writes `failed` when lookup finds nothing).
   `lookup` returns the same `sent` as `perform`; crash test expects
   `done`, shown once.
2. A `/log?after=N` cursor skips rows, since identity ids commit out of
   order. `/log` returns the whole chat; the page dedupes by event id; a
   test covers it.
3. The HTTP handlers' connection was unnamed. One connection per request,
   as in `bridges/telegram/kernel.py`.
4. This Mac's ledger holds older demo and replay tasks a resident kernel
   would resume. The run uses a fresh ledger (named in round 2).
5. The threat model claimed the token stops turns; a turn may read it in
   browser history. The model says the loopback deny alone stops turns
   and the token stops other users and web pages. No new deny.
6. The `Host` check and the page source test had no incident behind them.
   Both cut, with the `Content-Security-Policy` header; the `textContent`
   round trip stays.
7. The new sandbox test duplicated `tests/test_demo_sandbox.py`. Its
   existing probe gains `local_port`; the key directory tests are cited;
   a settings comment keeps the port outside `DEV_PORTS`.
8. The token was made by `open`, after the bridge started. `run` makes
   it; `open` only reads it.
9. Intake was underspecified for local notices and the near miss text.
   One `replies` flag covers both.
10. The local message fields were unspecified. Listed, with the page's
    retry of unsent text under the same id.
11. The chat had two names. `operator_chat` derives from
    `operator_channel` `local`.
12. Two questions were implementation calls. Moved to Decided by default.
13. `--plist` kept as the Telegram pattern, with its environment listed
    (`VALOR_OPERATOR_CHAT` is not needed, per 11).

Test ports moved to 6530 to 6539, outside the Redis span.

### Round 2 (of 2): revise

Both rounds are spent, so these findings ride into the build, as the
lead decided:

1. A fresh ledger under a new name has no `pgpass` line and no scram
   rule, so `migrate` fails at secure-login and the database sits on
   `trust`.
   Taken: rollout runs `python -m core backup`, renames the old ledger
   `valor_rebuild_pre24`, and migrates a fresh `valor_rebuild` with
   `VALOR_DB` unset, under the existing credentials. The builder does
   not perform the rollout.
2. `/log` did not name the id a reply carries. Taken: each row carries
   `message_id`, the page replies with it, and the held-send and `stop`
   tests take `reply_to` from `/log`.
3. How `operator_chat` derives was left open. Taken: the default factory
   derives it and it stays a field; tests set both fields.
4. "A page cannot be written by a turn" was false: the browser profiles
   are open to turns. Taken: the threat model says so, the opening is
   recorded in `docs/bridges/local.md`, and Question 2 asks Tom, with
   "accept for the proof of concept" assumed. No deny.
5. A turn's page on 8000 to 8009 could frame the chat page. Taken: `GET /`
   sends `Content-Security-Policy: frame-ancestors 'none'`, a property of
   the page, asserted beside the `OPTIONS` test.
6. An approved `email.send` goes nowhere on this Mac. Taken: one line
   under Left out.
7. Nits: the email near miss names the channel as Tom sees it; on a 401
   the page stops polling and says to reopen it with
   `python -m bridges.local open`.

## Checks

Round 1, on 55d9937f8:

- **Test:** pass. The base (d0e9d7e9c) and the head fail the same tests: 2 failures and 62 errors, all because dovecot or node is not installed on this Mac. The head adds 18 passes. The check added 17 tests (c23afcad5): the page driven in headless Chrome over the DevTools protocol, the `/send` 400s, the `/log` filter, the plist, and the loopback binding.
- **Review:** changes, three findings: an invented empty-text refusal, the browser-profile opening missing from sandbox-openings.md, and a token write that was not atomic. Governance: no.
- **Docs:** updated (6855fab0a).

Round 2, after patch round 1, on db14d203d:

- **Test:** pass. Two full runs gave the same result: 1465 passed, 55 skipped, and the same dovecot and node failures. All 35 local tests pass.
- **Review:** pass, round 2 of 2. Governance: no. It noted one wording point, left as is: a send with no `text` field shows as an empty row.
- **Docs:** no_change.

The lead checked the governance paragraph against CLAUDE.md byte for byte in bridges/README.md and core/README.md. The diff adds no "cori" and no em or en dashes.

## Merged

The lead's decision: merge. Every check passed on db14d203d. Valor-cori-rebuild was fast-forwarded to it.

Rollout on Tom's Mac, decided by default:

- The Mac has no external disk, so `core backup` (which needs a different device) cannot run. The rename itself keeps the old ledger intact as `valor_rebuild_pre24`, and a `pg_dump -Fc` of it goes to `~/valor-backups/`.
- No Telegram or mailbox login is needed. The gateway uses the Keychain `claude` login (about eight hours unless Tom's own sessions refresh it).
