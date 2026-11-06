# Sentry catch-up, 2026-11-06

Tom's ask (valor team chat, 2026-11-06): catch up on Sentry issues and handle them.

## What is open (org `yudame`, read 2026-11-06)

| Issue | Events | Last seen | Tracked by | Call |
|---|---|---|---|---|
| VALOR-JV CancelledError in `wire._call` | 13 | 11-05 | #3678 | fix |
| VALOR-KS TimeoutError ends the Telegram bridge | 4 | 11-04 | #3681 | fix |
| VALOR-KZ FileNotFoundError: a turn in a reclaimed workspace | 5 (Captain, Cowboy) | 11-05 | #3682 | fix |
| VALOR-3J another project's suite reports into `valor` | 1214 | 11-04 | #3677 | environment fix |

The other 28 open issues are in two client projects: no fix here, triage only. Popoto has none.

## Fixes: one commit each, each with its test (hard rule 9)

1. **JV.** Move the `CancelledError` mapping from `TelegramWire.download` into `_call`: a cancel that is not the current task's (`not current_task().cancelling()`) raises `NotConnected`. `download`'s own copy goes.
   Tests: a Telethon-side cancel of the awaited request gives `NotConnected` for `history`/`get`/`mark_read` (through `_call`) and for `download`. A cancel of the calling task still raises `CancelledError`.
2. **KS.** `TelegramWire.disconnected()` waits for `client.disconnected` to finish without re-raising its error (`asyncio.wait`), so `_keep_connected` reconnects.
   Tests: a future failed with `TimeoutError` or `OSError`, and one set to `None`, all make `disconnected()` return. A cancel of the waiter propagates. The bridge's `_keep_connected` reconnects after an errored drop: emulator or fake wire, no live network.
3. **KZ.** Today the kernel reclaims a merged or ended task's workspace, but feedback reopens the task to `patch` with the stale `workspace` still in its brief. So every step spawns the harness in a missing directory. The fix: a task whose latest workspace row is `workspace.removed` has no workspace.
   - `serve._ready` and `serve._provision` read that as "provision", so the existing provisioning job makes a new workspace from the target branch's head. That head holds a merged task's work.
   - The fold drops `session` at `workspace.removed`, since the session's transcript lived in the removed directory. The next turn starts a new session with the instruction and the feedback.
   - Builder's call: put the rule in `tasks.brief` or in one helper that serve uses. Choose after reading the brief's callers, and change nothing else's behaviour.
   Tests: a merged task and an ended task, each reclaimed then given feedback, get a `workspace.provisioned` row, and the next turn's cwd exists and resumes no session. A task never reclaimed keeps resuming its session. A re-provisioned task that finishes again is in `RECLAIMABLE` again.
   After rollout, the two parked tasks this left on the Captain and the Cowboy get re-provisioned and run their feedback turns. That is the intended behaviour; the delivery names them.
4. **3J.** `SENTRY_DSN`, the `valor` project's DSN, sits in launchd's user environment on the Macs (`launchctl getenv`). No plist or repo code sets it, and nothing in Valor reads it: `core/sentry.py` reads `settings.sentry_dsn_file`. Every task inherits it, so client suites that init Sentry from `SENTRY_DSN` report into `valor`.
   Fix at the source: on each Mac, spend at most 20 min looking for whatever sets it and remove `SENTRY_DSN` there. Then run `launchctl unsetenv SENTRY_DSN`, so the restart from the rollout drops it.
   No code change and no new drop name. If the variable comes back after a reboot, Sentry reopens 3J and the delivery says the setter was not found.

## Then

- Full suite (under 5 min), merge, then `core update --wait` on each Mac running the bridge and the kernel (the Cowboy, the Captain, the Bald, the Pirate). Run it over ssh in each kernel job's launchctl environment; on the Cowboy use `ssh localhost` with `PATH=/opt/homebrew/bin`. Check that each Mac's kernel and Telegram bridge run the merged head.
- Resolve JV, KS, KZ and 3J in Sentry, each naming its commit. Close #3678, #3681, #3682 and #3677 the same way.
- Client projects: in Sentry, resolve the stale issues (no events in 30 days) and ignore the noise with a reason. Noise means errors from hand-run `manage.py shell` sessions, a migration re-run, and the graceful-shutdown log line. For each live defect, write the cause, a proposed fix, and value/effort to `.valor/` (git-ignored) for the report, never to the repo.

## Out of scope

Changing a client repository. The nightly Sentry routine files issues but cannot start fix tasks, which is why these four piled up; that is a product note for the delivery, not a change here. No new guard, check or drop list.

## Stakes and loops

Stakes: three small kernel and bridge fixes that are reversible by revert. The worst case is a reopened task provisioning wrongly, which the tests cover. Critique 0, review 0: each is a bug fix (hard rule 9).
