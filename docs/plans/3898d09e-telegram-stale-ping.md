# Telegram bridge: a ping lost with a dropped connection drops every later one

Tom's ask (Slack DM, 2026-11-14 15:45 UTC): his message in a Telegram group got no 👀 he could see.
The record shows the message reached Valor at 15:43:03 UTC, about 110 s after he sent it at 15:41:13;
👀 and 🤔 were then placed in the same second. The question is the delay.

## What happened (this Mac's `telegram.log`, local time UTC+7; UTC in brackets)

- 18:17 (11:17): the bridge started on the merged head, which carries 577143e9c (#3683's fix).
- 22:37:44 (15:37:44): a keepalive ping went unanswered, so Telethon dropped the connection. 22:39–22:42: the network on this Mac was really out: two `[Errno 60] Operation timed out` drops, each followed by the bridge's 60 s wait before the next connect.
- 22:41:13 (15:41:13): Tom sent his message, while the connection was dead.
- 22:42:57: reconnected; the gap fill read the message at 22:43:03, which is when the ledger records it. Intake added no delay.
- From 22:43:57 on, the connection is dropped exactly 60 s after every connect ("Closing current connection to begin reconnect", with no network error), and still is at 22:47:58. With the gap fill failing on half of the drops, the bridge is off for about half of each two-minute cycle. One reaction failed "not connected" at 22:45:01.
- The same cycle ran 2026-11-09 21:22–22:47, 2026-11-10 17:00–19:15 and 2026-11-12 22:00–23:28. Each time it stopped only when the bridge was restarted.

**Cause.** Telethon 1.45.0's sender keeps the id of the last ping in `_ping`, and clears it only when the matching pong arrives (`mtprotosender.py:758`). Each keepalive tick (every 60 s) sends a ping when `_ping` is None, and otherwise treats the connection as dead and drops it (`_keepalive_ping`, `mtprotosender.py:453`). `TelethonWire.connect()` reuses the client. `client.disconnect()` throws away the pending ping, so the old pong never comes and nothing resets `_ping`. So once a ping is lost with a connection, the first keepalive tick on every new connection drops it. #3683's plan blamed the 60 s drops on stale keepalive loops; that fix stopped those loops but left this.

So the 110 s for this message came from a real network outage. The defect is that the bridge never recovers from one: it keeps every Telegram message waiting up to two minutes until it is restarted.

## Fix (one commit with its test, hard rule 9)

In `TelethonWire.connect()` (`bridges/telegram/wire.py`), after `await self.client.disconnect()`, set `self.client._sender._ping = None`. Add a comment saying why. Telethon has no public call for this; the attribute is private, so the comment names the Telethon version and the line. Nothing else changes: no new check, guard or filter.

## Tests (`tests/test_telegram_wire.py`, the real `TelegramClient` from `_real`, no network)

- A reconnect after a drop that lost a ping: set `client._sender._ping` to an id, stand in `client.connect`, then call `w.connect()`. Afterwards `_ping` is None, and the next `_keepalive_ping` sends a ping instead of starting a reconnect. Stand in the sender's `send` to record the call, and `_start_reconnect` to fail the test.
- The first connect on a fresh client (`_ping` already None) still connects, with the same assertion.
- The existing wire and bridge suites pass. Then the full suite.

## Then

- After the merge, the Mac that carries the chat (this one) runs its update, which restarts the bridge. Check its log: no once-a-minute "begin reconnect" after the restart, and none after the next real drop.
- Add this case to #3683 (done in this stage) and close it once the fix is merged.

## Out of scope

- The network outage itself (22:39–22:42 local).
- The "Future exception was never retrieved" `TimeoutError` from `wire.py:335`, logged when a gap-fill read dies with the connection. It is noise in the log; it has not been reported as a defect.
- Telethon's own `auto_reconnect` path.

## Stakes and loops

Stakes: a one-line change to how the Telegram bridge reconnects, reversible by revert. While the bug stands, every Telegram message after a network blip waits up to two minutes. Critique 0, review 0: a bug fix (hard rule 9).
