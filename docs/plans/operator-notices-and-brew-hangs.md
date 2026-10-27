# Every Mac's notices reach Tom; nightly brew runs end in minutes

Task 2bb12eb511b2. Two faults found installing Brew Status (8b86d911c550).

## 1. Update-failure notices from the Pirate, the Captain and the Bald

**Premise, corrected.** Only the Cowboy sets an operator chat
(`VALOR_OPERATOR_CHANNEL=slack`, `VALOR_OPERATOR_CHAT=T0B6E8JPE3C/U0B78H17XQ8`,
Tom's Slack DM, in its kernel, Slack and Telegram plists). The other three
resolve to `telegram`/`None`, so every rollout notice there is
`notice.undeliverable`. The Bald's of 2026-10-26 03:02 is one, and the Pirate
and the Captain each have one too. It is per-machine config, but copying
it as-is is unsafe: `intake.owned()` adds the operator chat to what a
bridge receives, so the Bald's Slack bridge would also take Tom's DM. That
DM is placed on the Cowboy by `projects/valor.toml`, and the intake
docstring says Tom's DMs are owned "only through a spec". Separately, the
**Pirate has no Slack bridge running**. Its `com.valor.kernel.slack` has
been launchd-disabled since 2026-10-16. `permissions._reload` bootstraps
without `launchctl enable` ("Bootstrap failed: 5", kernel.log), and
`_slack` returns early once the plist exists, so no later update retries.

**Code (with tests):**
- `intake.owned`: the operator chat is added only when it is not one of
  Tom's DMs (`operator_telegram_id`, `operator_slack_ids`). Those stay
  owned only through a spec, as documented.
- `intake.operator_session`: None when this machine does not own the
  operator chat. A non-owning Mac's root-task notices then go straight
  to the chat as kernel text, instead of opening a second Valor session
  in Tom's DM that never hears his replies.
- `permissions._reload`: `launchctl enable` before bootstrap (rollout.md
  step 9 already does this by hand). `_slack`: a plist that exists but
  whose job is not loaded is loaded.

**Per machine (build, over ssh):** on the Pirate, the Captain and the
Bald, add the Cowboy's two operator variables to the kernel, Slack and
Telegram plists. Then reload the kernel and the bridges, and enable and
load the Pirate's Slack job. Order: the kernel plists get the variables first. The bridges get
them only once the merged `owned()` is on that Mac, so no bridge takes
Tom's DM in between. The only interim exposure is a non-owning kernel
opening an operator session (fixed by the merge); the update routine
brings the merge to all three.

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
and kickstart one run. It must end in minutes. The setup step keeps them
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
- `test_kernel.py`: with `operator_channel="slack"` and
  `operator_chat=TOM_SLACK` on the Pirate, the DM is not in `owned("slack")`.
  It is on the Cowboy (spec). The same holds for Telegram with
  `operator_telegram_id`. A Telegram group operator chat is still owned.
- `operator_session` is None on a Mac that does not own the operator chat,
  and opens a session on the owner. A rollout notice there is requested
  to the operator chat with no `notice.undeliverable`.
- `test_permissions.py`: `_reload` runs `launchctl enable` before
  bootstrap. `_slack` loads an existing plist whose job is not loaded, and
  leaves a loaded one alone.
- Live: on each of the three Macs, `core settings` in the kernel's env
  shows the operator chat. The Slack log shows "connected". One notice
  goes to Valor's own Slack DM through each Mac's bridge (same send path,
  without messaging Tom). `notice.sent` is recorded.
- Live: the kickstarted brew runs on the Captain and the Bald end in
  minutes. Tonight's 04:00 runs on all four Macs end, and `brew-status`
  on each shows them. On the Cowboy and the Pirate the ollama-app line
  reads UPGRADE or no update, not TIMEOUT.
