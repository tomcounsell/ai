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
  connection) is the only connected state there is.
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
mechanism). Cost: three more apps (a free workspace allows ten).

## Build

1. **Manifest**: add `groups:write`. Reinstall the Cowboy's app; create
   "Valor Pirate", "Valor Captain", "Valor Bald" from the manifest at
   api.slack.com as Valor (browser), Socket Mode on, installed to Valor. If
   any install needs a workspace admin's approval, stop and write
   `.valor/question.md` for Tom with exactly what to click.
2. **Tokens per Mac**: `keys` reads `SLACK_APP_TOKEN_<MACHINE>` and
   `SLACK_USER_TOKEN_<MACHINE>` from the shared vault, `<MACHINE>` being
   `settings.machine` upper-cased with non-alphanumerics as `_`
   (`VALOR_THE_PIRATE`). The Cowboy's pair is renamed to its names. Each
   Mac's `slack-keys` is rewritten with its own pair now (the copies
   replaced; before the merge, through `VALOR_VAULT_ENV` naming a
   temporary file with that pair, then removed). Rollout step 5 stops
   copying `slack-keys`; step 9 creates the Mac's app and runs `keys`.
3. **Tom's Slack DM** is listed in `valor.toml` (`slack:T0B6E8JPE3C/<his
   id>`, from the Cowboy's job), and `owned()` stops adding the operator
   ids. `VALOR_OPERATOR_SLACK_IDS` is then added to the other Macs' kernel,
   Telegram and email jobs (reloaded), as the docs already say, so Tom is
   `tom` there.
4. **Channels**: Valor creates, with the Cowboy's token in a one-off
   script not committed, private `valor-dev`, `popoto-dev`,
   `cuttlefish-dev`, `psyoptimal-dev`, `cyndra-dev`, `satsol-dev`,
   `mastery-dev`, `gato-os-dev`, `mondayflowers-dev`, `counsell-home-dev`,
   `yudame-dev`; invites Tom; sets each purpose to its room text. Each is
   added to its spec's `chats`, not to `mentions`, with `rooms` equal to
   its Eng group's; `yudame-dev`'s (no Eng group) is "Engineering on the
   yuda.me site." Left out: `skills` and `work-vault` (no Eng group, not in
   the Brief's list). `satsol-dev` stays unanswered until SATSOL has a Mac.
5. **The update installs Slack**: the setup check (`permissions.ensure`)
   writes and bootstraps `com.valor.kernel.slack` when this Mac owns a
   Slack chat, its `slack-keys` holds a workspace, and the job is missing:
   the plist from `python -m bridges.slack --plist` in the kernel's
   environment. No keys: reported as `slack: no keys`, nothing started.
   It never starts a bridge on another Mac's token: each Mac's file holds
   only its own pair (item 2).
6. **The update reports bridges**: `rollout.ended` gains `bridges`, each
   installed bridge's `rollout.services` state, read once the restarted
   ones reconnect (at most 30 s). The room reply and `core update --wait`
   print it: "telegram connected since 03:00:12; slack not connected".
   The overnight routine's row carries the same.
7. **Docs**: `docs/bridges/slack.md` (status, the account, tokens,
   receiving, one app per Mac), `docs/rollout.md` steps 5 and 9,
   `core/permissions.py` and `core/rollout.py` docstrings,
   `projects/README.md` where it names ownership.

Sequencing: the other Macs' Slack jobs are not started before the merge
(old `owned()` would take Tom's DM); after it, their next update installs
them (item 5). The Cowboy's update after the merge does the same check.

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
  missing, and never reads another machine's pair.
- The setup check installs the Slack job when owned and keyed; not when the
  plist exists, no Slack chat is owned, or no keys (reported).
- `rollout.ended` carries each bridge's state; a kicked bridge back within
  the wait is connected, one not back is not; the reply and `--wait` text.
- The manifest holds `groups:write`. Existing tests on Slack ownership via
  operator ids (`tests/test_kernel.py`) move to the spec listing.
- Live: the Cowboy's bridge receives `valor-dev` and `popoto-dev`; Tom
  posts in one and Valor answers; after each Mac's next update, its row
  shows `telegram` and `slack` connected.

critique_rounds 2, review_rounds 1: it changes the kernel's update path
on every Mac, Tom's DM ownership, and live tokens.
