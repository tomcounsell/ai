# Telegram bridge: a reconnect leaves the dropped connection's loops running

Tom's ask (valor team chat, 2026-11-09): he is still getting Sentry email for Valor. The four new issues
are VALOR-MJ, MK, MN and MP. All come from the Captain's Telegram bridge, release 52a101ca2, between
2026-11-08 14:17 and 22:30 UTC.

## What happened (the Captain's `telegram.log`; times UTC)

- 14:14: a real network fault (`InvalidBufferError`, then timeouts). The sender dropped on its own, and the bridge reconnected through `TelethonWire.connect()`.
- `TelegramClient.connect()` (Telethon 1.45.0, the latest release) starts a new `_update_loop` and a new `_keepalive_loop` and overwrites their handles. Only `client.disconnect()` cancels them, and after a drop the wire never calls it. So every reconnect left the old pair running.
- Stale keepalive loops ping the new connection too. The second ping before a pong calls `_start_reconnect(None)`, so each new connection died about 60 s after it opened. That gave roughly one reconnect a minute for eight hours, and every reconnect added another pair of loops.
- The stale update loops share one `MessageBox`. One loop ends a difference that another is applying, which raises MJ (`apply_difference`) and MK (`end_get_diff`). Telethon logs these as "Fatal error handling updates" at ERROR. The failing loop then calls `client.disconnect()` while the bridge is reconnecting, which sets the sender's connection to `None` under its own send and receive loops: MN and MP.
- 22:30: that race left the bridge waiting on a connection that never came back. Nothing more was logged until the 04:21 rollout restarted it. That is why nothing has recurred since. The rollout did not fix anything.

**Messages.** None were missed. The Captain's ledger has no inbound Telegram message between 06:26 UTC on Nov 8 and 06:30 UTC on Nov 9, in any chat it owns. The gap fill after the 04:21 restart completed and found nothing to take. The Captain was deaf for six hours, though, and a message sent in that window would have waited for the restart.

## Fix (one commit with its test, hard rule 9)

`TelethonWire.connect()` calls `await self.client.disconnect()` before `self.client.connect()`. That is Telethon's own teardown: it cancels the previous connection's update and keepalive loops, saves the update state, and closes the session file, which Telethon reopens on next use. On a fresh client it does nothing, which I checked. One line plus a sentence in the module docstring. Nothing else changes.

There is no Sentry filter. These errors came from the bug, not from Telethon logging something it recovers from. `core/sentry.py` stays as it is.

## Tests (`tests/test_telegram_wire.py`, a real `TelegramClient` on a temp session, no network)

- After a drop, `connect()` cancels the stand-in update and keepalive tasks left by the dropped connection before `client.connect` runs. Exactly one pair is running afterwards.
- A connect that fails after Telethon started its loops, such as `is_user_authorized` timing out, gets its loops cancelled by the next `connect()`.
- The first connect on a fresh client still connects and reads Premium once.
- The existing drop tests (`disconnected()` returns on a drop with or without an error) still pass.

## Then

- Run the full suite and merge. Then run `core update --wait` on the Cowboy, the Captain, the Bald and the Pirate, over ssh in each kernel job's launchctl environment. On the Cowboy use `ssh localhost` with `PATH=/opt/homebrew/bin`, detached.
- Check that each Telegram bridge runs the merged head. Check the Captain's log for no `Fatal error handling updates` and no once-a-minute reconnects. Check Sentry for no new MJ, MK, MN or MP events.
- Resolve the four issues in Sentry with a note naming the commit.

## Out of scope

- The Nov 6 `sqlite3.OperationalError` on the session file: an older one-off.
- Telethon's ERROR line "Automatic reconnection failed 0 time(s)", which it logs on every drop: it opened no Sentry issue.
- No new check, guard or filter.

## Stakes and loops

Stakes: a one-line change to how the Telegram bridge reconnects, reversible by revert. A mistake would show up as the bridge failing to reconnect, which the tests and the rollout check cover. Critique 0, review 0: it is a bug fix (hard rule 9).
