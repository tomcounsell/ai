---
tracking: none
slug: c1-email-idle
type: build
status: building
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

## Critique round 1

Verdict: revise (`~/src/valor-build-notes/critic-c1-r1.md`). The rounds are
spent, so the findings ride into the build, and the lead decided:

- Drop the 29-minute socket read bound. The kernel's TCP ends the read
  with errno 60 before it could fire, RFC 3501 section 5.4 is about server
  patience, and `imap_connect` also serves the Sent Mail lookup, which
  has no timer by design.
- The failed watch already reconnects on the next tick (`serve_tick_s`,
  60 s); the defect is that a dead path goes unnoticed until the DONE.
- Detect the dead path with a sourced signal, chosen by measurement.
- Immediate reconnect only for transport errors after IDLE was entered.
- Test 4 corrected to what the existing test does.

## Cause

While idling, the client sends nothing. A path that drops the connection
with no reset (a NAT or Wi-Fi mapping expiring, the network changing) is
silent to the client: no `EXISTS` ever arrives and nothing on the client
side changes. The first bytes the client sends are the `DONE` at the
29-minute re-issue; they go unanswered, and the kernel's TCP ends the
read with errno 60. Mail that landed in that stretch waits until then.
The failed watch reconnects on the next tick (`serve_tick_s`, 60 s), so
after the failure is noticed the wait is short; the defect is the time
before it is noticed. The window ties each drop to a change of network
reachability (powerd's summaries lack the `NetAcc` flag around them).

## Measurement

2026-10-09, a read-only session (`EXAMINE`, no message touched) against
Valor's mailbox, opened apart from the bridge with the bridge's own key
read, IDLE for 660 s, every untagged line logged. Result: the server
advertises IDLE, accepted it, and sent **no untagged line at all in 11
minutes**. At the `DONE` the read hit EOF (`IMAP4.abort: socket error:
EOF`), so the connection had ended by then without the client being told.
So Gmail gives no keepalive cadence to measure silence against, and the
dead-path signal is the operating system's network-change event.

## Fix

- **End the connection when the network changes.** `imap.network_changes`
  runs `route -n monitor` and sets an event on each `RTM_IFINFO`,
  `RTM_NEWADDR` or `RTM_DELADDR` line. Source: `net/route.h` defines
  these as "iface going up/down etc." and "address being added to /
  removed from iface"; the other messages the monitor prints (a failed
  lookup, a route cached for one connection) are not a change of path and
  are ignored. `watch` takes the event; when it is set, the connection is
  ended through `Ends` (the existing stop path), the call blocked in IDLE
  returns, and the watch reconnects at once: the new connection's first
  `poll` finds what the old path hid. No timer is added. If the monitor
  cannot start or ends, one line is logged and the re-issue remains the
  only probe.
- **Reconnect at once on a transport failure after IDLE was entered.**
  `idle` takes `on_enter`, called when the server accepts IDLE. A failure
  that is an `OSError` or an `IMAP4.abort`, on a connection that entered
  IDLE, reconnects without waiting for the tick. A server without IDLE
  (`IMAP4.error`), `NO` "idle denied", an immediate `BYE`, and any failure
  before IDLE was entered wait for the tick as before, so no loop of
  login, `SELECT`, `SEARCH` runs faster than the existing wake.
- **No socket timeout, no `SO_KEEPALIVE`.** RFC 1122 section 4.2.3.6
  defaults keepalive off with an idle of at least two hours; no sourced
  value shortens detection. `smtp.imap_connect` is unchanged.
- **What stays.** A path that dies with no interface change on this Mac
  is still found at the 29-minute re-issue, so mail can wait that long.
  RFC 2177's 29 minutes is a ceiling on the re-issue interval; a shorter
  one is legal but its cost (more round trips to Gmail) against its
  benefit is Tom's, and none is chosen.

`docs/bridges/email.md` "The watch" states the measurement, the monitor,
and the two immediate reconnects.

## Done

- [x] Measurement recorded above.
- [x] `network_changes` sets its event for the three interface messages
  and for nothing else; a monitor that cannot start ends quietly.
- [x] A stand-in that stops forwarding with no reset (`Terminator.stall`)
  during IDLE, then a network change: the watch replaces the connection and
  receives mail delivered in the dead stretch with no tick. Red without the
  event.
- [x] A connection reset after IDLE began (`Terminator.cut`) is replaced
  without a tick and the mail is received.
- [x] Each of OSError and abort after IDLE reconnects at once; OSError
  before IDLE, no IDLE support, `idle denied`, and an early `BYE` do not.
- [x] `docs/bridges/email.md` updated; suite and ruff clean.

## Threat model

- Inbound mail stays data; parsing and intake are unchanged.
- The monitor is `route -n monitor`, a fixed command with no input from
  mail; it needs no privilege and reads only the routing socket.
- A flapping network reconnects once per interface event. Each is a real
  event, not a schedule; a reconnect that fails waits for the tick.
- The monitor process is the bridge's child and is killed by its handle
  when the watch ends.

## Tests

`tests/test_email_imap.py`: `idle` reports entry; `network_changes` on a
fake command (interface line sets the event, chatter does not, a missing
command ends quietly). `tests/test_email_kernel.py`: the stalled path with
a network change, the cut connection, and six parametrized failure kinds
(which reconnect before the tick and which wait). `tests/mailserver.py`:
`Terminator.stall` and `Terminator.cut`. The existing test that a watch
which cannot connect connects again on the next tick stays and covers the
tick path.

## Left out

- Keepalive tuning and a shorter probe cadence (see Fix).
- Timestamps on the bridge's log lines (the finding noted their absence).

## Questions for Tom

None that block. Assumed: the re-issue at 29 minutes stays the probe when
no network change is seen.
