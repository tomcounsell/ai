# Slack dev channels, one per project, and Slack in every update

Tom, valor team chat, 2026-10-25 10:57 UTC: "On slack, can you create
private dev channels for each project? named like x-dev. They are
equivalent to Eng: x telegram groups". 11:01 UTC: "Make it so the update
script that runs on all machines makes sure it's Slack comms is working,
the same way it ensures Telegram is connected".

**Done when** Tom posts in one of the new channels and Valor answers there
as in that project's Eng group; and an update on each Mac reports Slack's
state alongside Telegram's.

## Found

- The Pirate, Captain and Bald run the new kernel and Telegram bridge at
  `86f139f7f`, no Slack bridge. Each holds a copy of the Cowboy's
  `slack-keys` (rollout step 5 copies it), so starting a Slack bridge there
  today would run the Cowboy's app token twice.
- `intake.owned("slack")` adds `VALOR_OPERATOR_SLACK_IDS` on every machine:
  a Slack bridge on any Mac with that set owns Tom's Slack DM. Those Macs
  omit the variable, so Tom in their chats is not `tom` at bind
  (`_from_operator`, in the kernel). Tom's Telegram DM is owned only
  through `valor.toml`'s `chats`.
- The update keeps installed jobs on the launcher (`permissions.ensure`)
  and restarts stale bridges (`serve.restart_bridges`). It does not install
  a missing bridge or report whether any bridge is connected; the
  dashboard's Services row (`rollout.services`, from each bridge's lock
  connection) is the only connected state there is, and it is not the
  socket's: `core.bridge.serve` opens the lock connection before the
  bridge connects, and a Slack connect that fails is retried forever.
- The manifest has no `groups:write`, needed to create a private channel,
  invite Tom, and set its purpose.

## Receiving: one Slack app per Mac

Each Mac that answers a project with a Slack channel runs its own Slack
bridge on its own internal app in the Yudame workspace, installed to
Valor's account: the Cowboy's (exists), and new ones for the Pirate,
Captain and Bald. Every app gets Valor's user events; each bridge receives
only its own Mac's specs' chats, by the ownership rule that exists. No
code is new for receiving, no token runs twice, and each project's turns
stay on its client's Mac. The alternative, the Cowboy receiving for every
Mac, needs a forwarding path between kernels that does not exist (a second
mechanism). Cost: three more apps, against the free plan's ten (counted
first, item 1).

## Build

0. **The copied tokens go first**, before any app is made: the Pirate's,
   Captain's and Bald's `slack-keys` (the Cowboy's pair) are deleted, and
   rollout step 5 stops copying it. From then on no Mac but the Cowboy
   holds the Cowboy's app token, whatever happens to items 1 and 2.
1. **Manifest and apps**: add `groups:write`. First count the Yudame
   workspace's installed apps and integrations at its app management page
   (free plan: ten); with fewer than three slots free, stop and ask Tom
   which to remove. Reinstall the Cowboy's app; create "Valor Pirate",
   "Valor Captain", "Valor Bald" from the manifest at api.slack.com as
   Valor (browser), Socket Mode on, installed to Valor. If any install
   needs a workspace admin's approval, stop and write `.valor/question.md`
   for Tom with exactly what to click. A Mac whose app is not made has no
   `slack-keys` (item 0), so its update reports `slack: no keys`.
2. **Tokens per Mac**: `keys` reads `SLACK_APP_TOKEN_<MACHINE>` and
   `SLACK_USER_TOKEN_<MACHINE>` from the shared vault, `<MACHINE>` being
   `settings.machine` upper-cased with non-alphanumerics as `_`
   (`VALOR_THE_PIRATE`), and never the unsuffixed names. The Cowboy's pair
   is renamed to its names. Each Mac runs `keys` once its app exists (on
   the Mac, through `VALOR_VAULT_ENV` naming a temporary file with that
   pair until the merge, then removed). Live check: each Mac's app token's
   sha256 differs from every other Mac's, compared over ssh without
   printing either.
3. **Tom's Slack DM** is listed in `valor.toml` (`slack:T0B6E8JPE3C/<his
   id>`, from the Cowboy's job), and `owned()` stops adding the operator
   ids. `VALOR_OPERATOR_SLACK_IDS` is added to the other Macs' kernel,
   Telegram and email jobs (reloaded) before any Slack job there starts, so
   Tom is `tom` there and item 5 copies it.
4. **Channels**: Valor creates, with the Cowboy's token in a one-off
   script not committed, private `valor-dev`, `popoto-dev`,
   `cuttlefish-dev`, `psyoptimal-dev`, `cyndra-dev`, `satsol-dev`,
   `mastery-dev`, `gato-os-dev`, `mondayflowers-dev`, `counsell-home-dev`,
   `yudame-dev`; invites Tom; sets each purpose to its room text. Each is
   added to its spec's `chats`, not to `mentions`, with `rooms` equal to
   its Eng group's; `yudame-dev`'s (no Eng group) is "Engineering on the
   yuda.me site." Each Mac's own app's user joins its channels (Valor is
   one user, so it is already a member). Left out: `skills` and
   `work-vault` (no Eng group, not in the Brief's list). `satsol-dev`
   stays unanswered until SATSOL has a Mac.
5. **The update installs Slack**: the setup check (`permissions.ensure`)
   writes and bootstraps `com.valor.kernel.slack` when this Mac owns a
   Slack chat, its `slack-keys` holds a workspace, and the job is missing.
   The plist is `bridges.slack`'s, its environment the `PLIST_ENV`
   variables taken from the Telegram job's plist on this Mac (it carries
   the machine, database, inbound and operator settings), else from the
   kernel's own environment. No keys: reported as `slack: no keys`,
   nothing started.
6. **Connected means the socket is up.** Today a bridge's lock connection
   exists before it connects, so a Slack bridge on a revoked token or an
   uninstalled app would show connected. Each bridge's tick (Telegram's
   and Slack's) reports `wire.connected()` to the port, which names its
   listener connection `valor-<channel>-up@<epoch of the first up>` while
   up and `valor-<channel>-listen` while down. `rollout.services` reports
   `since` from that name and None without it; the dashboard's Services
   row follows. `rollout.ended` gains `bridges`, each installed bridge's
   state, read once the restarted ones have had one tick (at most 90 s).
   The room reply and `core update --wait` print it: "telegram connected
   since 03:00:12; slack not connected (no keys)". The overnight
   routine's row carries the same.
7. **Docs**: `docs/bridges/slack.md` (status, the account, tokens,
   receiving, one app per Mac, connected state), `docs/bridges/telegram.md`
   (the port's connected state), `docs/rollout.md` steps 5 and 9,
   `core/permissions.py`, `core/rollout.py` and `core/bridge.py`
   docstrings, `projects/README.md` where it names ownership.

Sequencing: item 0, then 3's job settings, then 1, 2 and 4. The other
Macs' Slack jobs start at their first update after the merge (before it,
the old `owned()` would take Tom's DM). The Cowboy's update after the
merge does the same check.

Not governance: nothing here refuses or blocks; items 5 and 6 are Tom's ask.

## Out of scope

The email bridge's equal ownership of `VALOR_OPERATOR_EMAIL`; a Slack
channel for team chats; inviting anyone but Tom; installing other bridges.

## Tests

- Each of the eleven specs lists one `slack:T0B6E8JPE3C/C…` dev channel,
  not in `mentions`, its room equal to the Eng group's; `skills` and
  `work-vault` list none.
- `owned("slack")` with operator ids set lists only spec chats; Tom's DM is
  owned on the Cowboy and not on the Pirate; Tom is `tom` at bind in a
  Pirate dev channel; another person there is named and heard.
- `keys` reads this machine's suffixed names, refuses with them named when
  missing, and never reads another machine's pair or the unsuffixed one.
- The setup check installs the Slack job when owned and keyed, and the
  written plist's `EnvironmentVariables` equal the Telegram job's for
  every `PLIST_ENV` name it has (operator ids, inbound, tick included);
  not installed when the plist exists, no Slack chat is owned, or no keys
  (reported).
- A Slack bridge whose `wire.connect` keeps failing holds its lock and is
  reported not connected; once connected it is reported connected with
  its since; after a disconnect, not connected. The same for Telegram.
  `restart_bridges` still finds each bridge's stamp.
- `rollout.ended` carries each bridge's state; a kicked bridge up within
  the wait is connected, one not up is not; the reply and `--wait` text.
- The manifest holds `groups:write`. Existing tests on Slack ownership via
  operator ids (`tests/test_kernel.py`) move to the spec listing.
- Live: no other Mac's app token equals the Cowboy's (item 2); the
  Cowboy's bridge receives `valor-dev` and `popoto-dev`; Tom posts in one
  and Valor answers; after each Mac's next update, its row shows
  `telegram` and `slack` connected.

critique_rounds 2, review_rounds 1: it changes the kernel's update path
on every Mac, Tom's DM ownership, and live tokens.
