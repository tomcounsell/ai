# The broker's performers

Part of [tech-stack.md](tech-stack.md), section 8.

A performer is a Python class with an `action_type`, a fixed
`effect_class`, and the coroutines `perform` and `lookup` (by idempotency
key). It may define `async refuse(conn, action)`, checked at request and
again at release. It runs in the kernel process, outside the turn's
sandbox. Git runs in a
worker thread, so the gateway's streams never wait on a push. The composition
root builds a `broker.Performers` for each task from its Brief and passes it
to every broker call, so one task's performer never acts for another.

A bridge's send types (`telegram.send_message`, `email.send`, `slack.send_message`) are declared in
`core/bridge.py`; the kernel sizes each file a send names in the task's
workspace without reading it and refuses an impossible send at request time.
The bridge's `perform` reads each file once and refuses one whose sha256
differs. Its `lookup` returns nothing only when the platform can no longer
record the send, and raises `Unknown` while it still might.
Status: **in use**.

- **`push_branch`** (`act`, `tools/push_branch.py`): pushes one commit to
  one branch of the workspace's origin over git, never with `--force`,
  tags, submodules, or a signature. Git is found on `PATH` (`VALOR_GIT`
  overrides) and run by absolute path (`core/binaries.py`), with a system-only PATH, no `DYLD_*` or
  global config, git in its own process group, run until it exits and killed whole when its caller is
  stopped or interrupted, its output in files the kernel holds so a call
  ends when git exits, with replace
  refs, grafts, and the commit-graph ignored and `core.hooksPath` off (`core/git.py`). `lookup`
  answers present (the branch holds the commit, at its tip or below),
  absent, or unknown; `broker.reconcile` uses it to settle a dangling merge
  intent: the intent row holds the whole action, so reconcile rebuilds what to look up from it. It pushes to the task's `push_url` (else the origin URL recorded
  at start), never carries the credential, and refuses the task's target branch.
- **`merge`** (`act`, `tools/push_branch.py`): the kernel's push of a
  passed candidate onto the target branch, released only when the merge
  predicate holds; no turn is offered it. It is built from the task's Brief
  and refuses a payload naming any other URL or branch: the URL and target
  branch the kernel recorded at start from the project spec. A local
  origin needs no token. A remote is pushed from the kernel mirror with the GitHub token, which only a task the
  kernel provisioned has. Each git call against the remote gets a config
  file of its own holding one `Authorization` header scoped to that URL,
  removed when git exits, with redirects and proxies pinned off in the file
  and on the command line. GitHub's refusal of the token fails the merge
  saying to rotate it ([machine.md](machine.md), Keychain). A merge may
  land on the remote's default branch: landing work there is Valor's own
  call, and the ledger's record of the merge is what makes it safe
  (docs/business.md, constraint "The record is the safety mechanism"). A
  merge to a branch that deploys to production is the harder-to-reverse
  case, which gets the longer argument (docs/business.md, Business item 4);
  the performer treats it like any other branch.
- **`WorkspaceWrite`** (`propose`) and **`OutboxAppend`** (`act`)
  (`tests/performers.py`), for the tests only: a file in the workspace, and
  a local outbox that stands where a bridge's send will.

## The accounts each business gives Valor

Valor's accounts are its own: a full Google Workspace seat
(valor@yuda.me), its own memberships on every platform it uses, and its own
logged-in browser on each machine ([browser.md](browser.md)). Valor holds
none of Tom's accounts and never acts as Tom. Its acts through its accounts
are fully free: a post, a send, a portal submit, or a client-agent launch
needs no tap from Tom, and each is reported. No spend ceiling applies;
turns run `claude -p` on Valor's Claude subscription. Posting uses the
craft skills de-slop and authenticity-pass, which shape the post and gate
nothing (`skills/README.md`).

Valor acts through its own accounts on the client's Mac, and those
accounts are the boundary of what it can do there (docs/business.md,
Business item 8). Every channel that can act records what it did; an
account Valor can act through without a record is a gap (docs/business.md,
constraint "The record is the safety mechanism"). The rows below are this
install's, from the code. Each of the four Macs gets its own copy of this
table as its client's accounts are granted; the per-Mac lists are not
known yet.

| Account | Mac | What Valor does through it | Through the broker | How it reaches the ledger |
|---|---|---|---|---|
| GitHub, `valorengels`, by `GITHUB_PUSH_TOKEN` in `github-keys` | this install | `merge` of a passed candidate onto the Brief's URL and branch | yes | `effect.intent` then `effect.outcome`; a `report` notice to Tom |
| GitHub, `valorengels`, by `GITHUB_PUSH_TOKEN` in `github-keys` | this install | read: the `conditions` routine fetches each business's repository, private ones included, to read `decisions/conditions.toml` ([routines.md](routines.md), Watched conditions) | no; a read under the routine's `propose` ceiling | one `repository.fetched` row per repository, naming the key, never its value |
| Telegram, Valor's user account, by `telegram-keys` and `telegram.session` | this install | `telegram.send_message`; receiving, which marks messages read | sends yes; receive no | a send is `effect.held`, `release.requested`, and the bridge's outcome; each message taken is `message.received`; marking it read has no row |
| Gmail, Valor's mailbox, by `mail-keys` | this install | `email.send` over SMTP; receiving over IMAP, which sets `\Seen` | sends yes; receive no | as Telegram's; setting `\Seen` has no row |
| Slack, Valor's user account, by `slack-keys` | this install | `slack.send_message`; receiving over the Socket Mode connection | sends yes; receive no | as Telegram's, less the read mark: a send is `effect.held`, `release.requested`, and the bridge's outcome; each message taken is `message.received` |
| Anthropic, by `claude-token` or Claude Code's login | this install | frontier turns' model calls | no; the gateway | `gateway.opened` and `gateway.charged` per call; spend on `turn.ended` |
| OpenAI, by `openai-key` | this install | model calls on the OpenAI route | no; the gateway | `gateway.opened` and `gateway.charged` per call |
| TypeSafe (Jev) and OpenRouter, by `judgement-keys` | this install | judgement calls from the kernel | no; the gateway | `gateway.opened` and `gateway.charged` with `route: judgement`; `judgement.answered` or `judgement.failed` |
| Claude Code's login in the login keychain, read by a turn through `security` | this install | a model call around the gateway | no | none beyond the turn's own transcript. **Gap**, accepted by Tom on 2026-10-01 ([architecture.md](architecture.md), Limits) |
| Valor's own `gh` login, git credential helper, SSH agent, and `sentry-cli` auth | this install | whatever the account allows, from a turn: pushes to the task's origin, pull requests, issue and Sentry reads | no | none beyond the turn's own transcript. **Gap** |
| Google Meet, valor@yuda.me, by the call profile `~/Library/Application Support/Valor/meet-chrome` ([bridges/meet.md](bridges/meet.md)) | each Mac where Tom signed the profile in | `meet.say` and `meet.send_message` in a call a group chat asked for, performed by the call's own process; joining and hearing the call | says and posts yes; joining and hearing no | a say or post as Telegram's send, released by the call's process; what it heard is `call.heard` and `call.participant`, its start and end `call.started` and `call.ended` |
| Google Workspace, valor@yuda.me, by `gws`'s refresh token in `~/.config/gws/credentials.json` ([machine.md](machine.md)) | this install | whatever the account allows, from a turn: Gmail reads, drafts and sends, Calendar, Drive, Docs, Sheets, Slides, Tasks; and `meet.join`'s new Meet (`gws meet spaces create`, `tools/meet.py`) | a turn's use no; `meet.join` yes | a turn's use none beyond its own transcript. **Gap**; `meet.join` is `effect.intent` then `effect.outcome` |
| Mercury, Yudame's company account, by the API token in 1Password (`m-valor`, item `Mercury API Token`), read through `op` at use time ([machine.md](machine.md), Keychain) | the Cowboy, where the `valor` project is placed and whose IP the token's allowlist names | `mercury-invoicing`: customers and invoices created with `DontSend`, invoices, customers, PDFs and account transactions read; the notice and reminders it prepares leave as `email.send` | no; the notice and reminders yes | the sends as Gmail's; the Mercury calls none beyond the turn's own transcript. **Gap** |
| Any other account logged in on the Mac, in the login keychain or a browser session | this install | whatever the account allows, from a turn | no | none beyond the turn's own transcript. **Gap** |
| Stripe | per business, not yet granted | | | |
| Google Analytics | per business, not yet granted | | | |

A business account granted to Valor is recorded only when its actions
pass a performer or the gateway. Granted as a login on the Mac and
nothing more, it joins the second gap row.
