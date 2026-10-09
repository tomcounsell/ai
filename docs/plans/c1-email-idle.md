---
tracking: none
slug: c1-email-idle
type: build
status: planned
critique_rounds: 1
review_rounds: 1
governance_grant: none
---

# The email watch notices a dead path and reconnects

Track C1 of `rebuild-finish-prompt.md`. A bug fix: it changes the code that
holds the IMAP connection and adds no check, gate, hook, round, or review
step, so no `governance_grant` is needed.

## Incident

The 2026-10-05 window (`~/src/valor-build-notes/window-driver.md`, "Email
watch") logged `TimeoutError` errno 60 three times in about two hours, each
ending in `imap.idle` at `conn.idle(duration=IDLE_REISSUE_S)`. Mail that
landed in those stretches waited.

## Cause

Two things, both in the code.

1. **A silent path is not seen until the 29-minute re-issue.** While
   idling, the client sends nothing. If the path drops the connection with
   no reset (a NAT or Wi-Fi mapping expiring), the server's `EXISTS` never
   arrives and nothing on the client side changes. The first bytes the
   client sends are the `DONE` at the re-issue; they go unanswered, and
   the read of the tagged reply blocks until the kernel's TCP gives up
   (errno 60). `smtp.imap_connect` sets no socket timeout and no
   `SO_KEEPALIVE`, so no read of the connection ever has a bound of the
   bridge's own.
2. **The failure is then not acted on.** `watch` logs it and waits for
   `retry`, which only the bridge's `tick` (an outbox wake) sets. Until a
   wake comes, no new connection is made, so the mail the dead path hid
   stays unread even though a fresh connection's first `poll` would find it.

## Fix

In `bridges/email/smtp.py` and `bridges/email/imap.py`:

- **A read bound.** `imap_connect` passes `timeout=` to the connection, so
  every read on it, including the reply to `DONE`, raises `TimeoutError`
  (an `OSError`, already handled) instead of waiting on the kernel. The
  value is `imap.IDLE_REISSUE_S`, 29 minutes. Source: RFC 3501 section 5.4
  lets a server log a client out after 30 minutes of inactivity, and RFC
  2177 has the client act within 29; a server that has said nothing to a
  command for that long has already been entitled to drop the session, so
  waiting longer buys nothing. Idling is unaffected: `imaplib`'s idle
  waits on its own `duration`, and the read bound applies to the commands
  and to the reply after `DONE`. The constant is one name used in both
  places.
- **Reconnect on the failure itself.** When a connection that had finished
  at least one `poll` fails, `watch` makes one new connection at once,
  without waiting for `retry`; the fresh `poll` reads what the dead path
  hid. A connection that fails before finishing a poll (the network is
  down, the credentials are refused, the server is unreachable) waits for
  `retry` as now. This adds no timer and no retry count: the failure of a
  working connection is the event, and the existing tick is the only
  repeat. A path that dies again after a good poll gets another immediate
  reconnect, which is again an event, not a schedule.
- **No `SO_KEEPALIVE`.** RFC 1122 section 4.2.3.6 makes keepalive off by
  default with a default idle of at least two hours, so no sourced value
  would shorten detection below the re-issue. The `DONE` at the re-issue is
  the probe RFC 2177 provides. Left out; if the 29-minute window proves too
  long, a shorter probe interval is a question for Tom (cost: more
  `DONE`/`IDLE` round trips against the server), assumed: keep 29 minutes.

`docs/bridges/email.md` "The watch" says the connection has "no socket
timeout"; it is rewritten to state the read bound and the immediate
reconnect, in status-quo wording.

## Done

- [ ] A connection from `smtp.imap_connect` has its socket timeout set to
  `IDLE_REISSUE_S`; a test reads `conn.sock.gettimeout()`.
- [ ] An IDLE whose server stops answering raises within the bound instead
  of blocking (test below, bound shrunk by monkeypatch).
- [ ] After a failure of a connection that had polled, `watch` connects
  again with `retry` unset, receives mail that landed during the dead
  stretch, and logs one line per failure.
- [ ] After a failure of a connection that never polled, `watch` waits for
  `retry` (no spin).
- [ ] `docs/bridges/email.md` states the bound and the reconnect; no other
  doc still says "no socket timeout".
- [ ] Suite, `uvx ruff check .`, `uvx ruff format --check .` clean.

## Threat model

- Inbound mail stays data; nothing here changes parsing or intake.
- The read bound changes no authority and opens no new connection target;
  `Ends` still registers every socket, so a stop still ends a blocked read.
- Immediate reconnect could become a hot loop if a server accepts, polls
  once, and drops every time. Each such cycle completes a real `poll`, so
  it is bounded by the server's own behavior and logs one line per failure;
  a server that fails before a poll is not retried without a tick. No cap
  is added: the case is not seen, and the cost of one login per drop is
  the server's rate to refuse (a refusal is a pre-poll failure and waits).
- A stall mid-`FETCH` of a very large message: the bound is per read, not
  per command, so a slow but moving transfer is not cut.

## Tests

In `tests/test_email_imap.py` and `tests/test_email_kernel.py`, against
the existing Dovecot behind `tests/mailserver.py`:

1. `imap_connect` socket timeout equals `IDLE_REISSUE_S`.
2. **The stand-in that stops answering mid-IDLE** (the non-obvious case):
   `Terminator` in `tests/mailserver.py` gets a `stall()` that, once set,
   keeps the TCP connection open but forwards nothing in either direction
   (no reset, as a dropped NAT mapping behaves). With `IDLE_REISSUE_S`
   shrunk to a few seconds, `imap.idle(conn)` is started, `stall()` is set
   once `idling` is seen, and the call is expected to raise `OSError`
   after the shrunk bound (the `DONE` goes out, no reply ever returns), not
   hang. Without the read bound this test hangs, which is the red.
3. **The watch end to end**: a kernel test starts `email.watches()`,
   waits for `idling`, stalls the stand-in, delivers a message through
   Dovecot directly, and does not call `tick`. With the bound shrunk, the
   watch fails, reconnects on its own through a fresh `Terminator`
   connection, and `received` shows the message. Red before the fix: it
   waits for a tick forever.
4. A watch whose first connection never polls (the login refused) is not
   retried without `tick`: reuse `test_a_watch_that_cannot_connect_connects_again_on_the_next_tick`
   and assert no second login before the tick.

Overlapping tests are folded into existing ones where they cover the same
path; no test is kept for the removed "no timeout" wording.

## Left out

- Keepalive tuning and a faster probe cadence (see Fix).
- Timestamps on the bridge's log lines (the finding noted their absence);
  a separate small change if wanted.

## Questions for Tom

None that block. Assumed: 29 minutes is the right bound and probe cadence.
