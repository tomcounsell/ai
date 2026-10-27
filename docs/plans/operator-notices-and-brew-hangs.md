# Every Mac's notices reach Tom; nightly brew runs end in minutes

Task 2bb12eb511b2. Two faults found installing Brew Status (8b86d911c550).

## 1. Update-failure notices from the Pirate, the Captain and the Bald

**Premise, corrected.** Only the Cowboy sets an operator chat
(`VALOR_OPERATOR_CHANNEL=slack`, `VALOR_OPERATOR_CHAT=T0B6E8JPE3C/U0B78H17XQ8`,
Tom's Slack DM, in its kernel, Slack and Telegram plists). The other three
resolve to `telegram`/`None`, so every rollout notice there is
`notice.undeliverable`. The Bald's of 2026-10-26 03:02 is one, and the Pirate
and the Captain each have one too. Nothing in `docs/rollout.md` names the
two variables, which is how three Macs were set up without them. The
notice is written by the kernel (`serve._rollout_text`) with the channel and
chat in the row, and a bridge sends any due notice on its channel with no
ownership check (`core/bridge.py` `due`), so only the kernel job needs
them. The bridges' jobs are left alone, so no bridge's `intake.owned`
puts Tom's DM on a second Mac (the kernel's does, which only its
`operator_session` reads). A rollout notice is one of `notices.TOMS`, so
it skips `operator_session` and is written straight to the operator chat:
the variables alone deliver it. Separately, the
**Pirate has no Slack bridge running**, so it could not send a Slack notice.
Its `com.valor.kernel.slack` has
been launchd-disabled since 2026-10-16. `permissions._reload` bootstraps
without `launchctl enable` ("Bootstrap failed: 5", kernel.log), and
`_slack` returns early once the plist exists, so no later update retries.

**Code (with tests):**
- `intake.operator_session`, for the notices of root tasks that are no
  session (a CLI or routine task's `question`, `ended`, `report`): when
  the operator chat is one of Tom's DMs (`operator_telegram_id`,
  `operator_slack_ids`), it is None unless a spec placed on this machine
  lists that chat (`_project_chats`), as the intake docstring already
  says of his DMs. The Cowboy's `projects/valor.toml` lists both; no spec
  on the other three does. With None those notices go to the DM as kernel
  text instead of opening a session there whose replies that Mac's bridge
  never receives. A group or local operator chat is unchanged.
- `permissions.slack`: a plist that exists but whose job is not loaded
  (`launchctl print` fails) is enabled (`launchctl enable`) and loaded;
  the enable is only on the Slack job, so `_reload` never re-enables a
  job rollout.md disabled.
- `docs/rollout.md` step 9: the kernel job carries
  `VALOR_OPERATOR_CHANNEL=slack` and `VALOR_OPERATOR_CHAT=<Tom's Slack DM>`
  on every Mac (`serve.PLIST_ENV` keeps them across rewrites).

**Per machine (build, over ssh):** on the Pirate, the Captain and the
Bald, add the two variables to `com.valor.kernel.plist` only, and reload
the kernel. The Pirate's Slack job is enabled and loaded by hand the
same way; its next update would do it once the merge lands.

## 2. Nightly Homebrew runs (`com.valor.brew-nightly`)

**Cause.** The Captain and the Bald still run pre-vault scripts (Oct 13,
Sep 8) as bare `/bin/bash`, not through Valor.app. Their copies predate the
self-update step, so the vault never reached them. A bare job touching
`~/Desktop/Valor` or `/Applications` raises a privacy prompt that waits for
a click. Tonight's Bald run has been blocked in `open()` on the Brewfile
since 04:00 (sampled). The Captain's Oct 27 `brew bundle` sat 04:00→14:58.
The Bald's Oct 23 run sat in the ollama-app upgrade until Oct 26 10:43,
most likely an App Management prompt (unconfirmed).

**Fix.** On the Captain and the Bald: stop the hung run, then install the vault
script and vault plist (with Valor.app's `valor-run`), bootout and bootstrap,
and kickstart one run. It must end in minutes. Valor.app's Desktop
access is confirmed: on both Macs the kernel, run through the same
launcher, copied the Slack keys from `~/Desktop/Valor/.env` at its last
update (`slack-keys` of 2026-10-27, `setup: fixed com.valor.kernel.slack`).
App Management is not confirmed. If the kickstarted run has not ended
after 15 minutes, the build samples it and its children to find the
prompt; a grant only a person at that Mac can click goes to Tom as a
blocker, named, and that Mac stays open in the delivery. The setup step keeps them
current from then on. `SETUP.md` in the vault names Valor.app instead of the
retired BrewNightly.app, and says an old copy needs this one-time install.
**No per-step timeout:** it would turn a missing grant into a silent skip.
With the launcher, nothing waits on a prompt.

**Ollama.** Ollama.app rejects the AppleScript quit (`-10004` privilege
violation, tried on the Cowboy), so every run times out and skips it.
SIGTERM quits it and its server in 1 s, and `open -a` brings it back
(tried). `quit_app_gracefully`: when the quit is refused with
`-10004`, send SIGTERM to the app's own process, then wait as now. Apps
that accept the event, or whose Automation is not granted (`-1743`),
are unchanged, so iTerm is never signalled.

The script and `SETUP.md` live in the vault (`~/Desktop/Valor/brew`), not
this repo: they reach the Cowboy and the Pirate at their next 04:00
without a merge.

## Out of scope
The Captain's 1Password and iTerm quit timeouts (30 s each, no hang).
Why `com.valor.kernel.slack` was disabled on Oct 16.

## Tests
- `test_kernel.py`, with the real `owned()` and the repo's specs: with
  the operator chat Tom's Slack DM, `operator_session` is None on the
  Pirate and opens a session on the Cowboy; with a Telegram group
  operator chat no spec lists, it still opens one. A root task's `ended`
  notice on the Pirate is requested to the DM with no
  `notice.undeliverable`.
- `test_permissions.py`: `slack` enables and loads an existing plist whose
  job is not loaded, and runs no launchctl for a loaded one.
- Live: on each of the three Macs, `core settings` in the kernel's env
  shows the operator chat, and the Slack bridge's `owned("slack")` (its
  own job's env) still lacks Tom's DM. The Pirate's Slack log shows "connected". One notice
  goes to Valor's own Slack DM through each Mac's bridge (same send path,
  without messaging Tom). `notice.sent` is recorded.
- Live: the kickstarted brew runs on the Captain and the Bald end in
  minutes. Tonight's 04:00 runs on all four Macs end, and `brew-status`
  on each shows them. On the Cowboy and the Pirate the ollama-app line
  reads UPGRADE or no update, not TIMEOUT.
