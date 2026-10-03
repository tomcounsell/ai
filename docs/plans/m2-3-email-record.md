# 2.3 The email bridge: the record

What is decided by default in [m2-3-email.md](m2-3-email.md), its
critique round, and its build, in order.

## Decided by default

- **One process of its own** under launchd: an IMAP or SMTP stall never
  holds the kernel.
- **Only owned senders are read**, through `owns`: no Mac marks seen mail
  another Mac's bridge waits for. Receive scope, not a gate.
- **`email_since`** keeps the unseen backlog from arriving as new, and
  marks the boundary with `main`'s bridge.
- **No UID cursor**: a UIDVALIDITY change or a gap replays nothing.
- **The mail credential in the kernel key directory**, per machine.md.
- **Dovecot and a hand-written SMTP server**: UID and UIDVALIDITY behavior is under
  test, and Dovecot runs as the agent's user with no root.

## Critique round 1 (of 1): revise, rounds spent

| Finding | Handled |
|---|---|
| 1. Port does not match 2.1 | "Port used" written to the lead's port decisions: `core/bridge.py`; `verified` set by `core/intake.py` from raw headers; `chat_id` the thread root, ownership and start on `sender_id`; `thread` dicts; `inbound_dir`; `operator_email`; `sent` in results; binding on `(channel, chat_id, message_id)` |
| 2. No file store; check-then-read race | Store dropped; `perform` reads, hashes, compares, and sends the same bytes; swap test added |
| 3. UID cursor replays old mail | Cursor dropped; `UNSEEN SINCE` per owned sender only; webmail-opened mail under Left out |
| 4. In-doubt sends written `failed` | The performer raises `broker.Unknown` once the body has started; delayed-250 test; the broker side is port decision 24 |
| 5. No later reconcile | Port decisions 22 and 23; reconcile settles a send from Sent Mail once no process performs it; Gmail's filing delay is measured in the window |
| 6. Sender filter and `email_since` | Filter built from `owns` and `owned`; contradictions fixed (other addresses not received; forgeries tested from owned addresses); `xtom@` test; cost under Left out |
| 7. Guard row | Incident restated; `mission_items` `[6]`; `source` added; per-guard `note` and `via` overrides; `GUARD_DMARC` in `guards.py`; expiry note in the window |
| 8. DMARC on intra-domain mail | Tom's pre-window step 1; DKIM key in step 2 |
| 9. Which backend to terminate | `valor-email-perform`; invariant asserted; exactly-one shown by the after-DATA hook |
| 10. `LIST (SPECIAL-USE)` on Gmail | Plain `LIST`; `X-GM-RAW` when `X-GM-EXT-1`; the window records which works |
| 11. Size arithmetic | Inbound caps dropped; `max_file_bytes` is Gmail's cited 25 MB encoded limit, compared with the encoded size; a timer per send call; a timeout before the end of data line is a definite refusal, after it `Unknown` with `__context__` named |
| 12. Poison message | Logged, left unseen, poll continues; test added |
| 13. Recipient claim | Threat model reworded: Tom's tap on the full card is the control |
| 14. Premise slips | Both SMTP sources named; `read_key` names the owning command; imports per port decision 30 |
| 15. Limits | RFC 5321's per-command timeouts and no floor rate; the IMAP socket timeout is main's value |

## Build

The build reads two of the lead's decisions, and every limit it sets has
a source.

**Reconcile has no age rule.** Task 1.4d (`core/performing.py`) holds a
file lock per effect for as long as any process or thread performs it;
reconcile runs once that lock is free, reads the target, and settles
`done` or `failed` at once. For email the target is Sent Mail, read by
Message-ID. Gmail copies a message sent through SMTP into Sent Mail
("Choose your IMAP email client settings for Gmail", Gmail Help,
https://support.google.com/mail/answer/78892); no document gives the time
that filing takes, so the bridge waits for none and keeps Sent Mail out
of the common path:

- The 250 reply settles `done` in `perform` (RFC 5321 section 4.1.1.4:
  the server accepts the message with that reply, or refuses it).
- Any end before the end of data line has gone in full is a definite
  refusal, `failed`: the server has accepted nothing (4.1.1.4).
- Only a send whose end of data line went with no reply read, or whose
  process died after it, reads Sent Mail. A receiver is to answer that
  line at once (RFC 5321 section 4.5.3.2.6; RFC 1047), and the duplicate
  this window can cause is the one section 6.1 describes. If Gmail files
  the message after the reconcile reads, a delivered message is recorded
  `failed`; nothing resends it, so no copy is doubled. The window
  measures the filing time.

The base this build stands on does not hold 1.4d's performing module.
`email.send` declares no settle time, so on this base the broker's
`reconcile_after_s` applies until 1.4d removes it, and the crash tests set
it to 0. The bridge runs sends in its module's `in_thread`, which is
`asyncio.to_thread` here and becomes 1.4d's `performing.in_thread` on the
combined base, so the worker thread holds the effect's lock while it
runs.

**Every timeout has a source.** No timeout scales with the message and
no floor rate is assumed. The SMTP timeouts are the
minimums RFC 5321 section 4.5.3.2 gives a client
(`bridges/email/config.py`, `SMTPTimeouts`): the 220 greeting 300 s
(4.5.3.2.1), `MAIL` and `RCPT` 300 s (4.5.3.2.2, 4.5.3.2.3), `DATA`'s 354
120 s (4.5.3.2.4), each send call of the body 180 s (4.5.3.2.5), the
final 250 600 s (4.5.3.2.6). EHLO, STARTTLS, AUTH, and QUIT have no value
in that section and take the 300 s of the other single-reply commands. A
send call carries at most one TLS record, 16,384 bytes (RFC 8446 section
5.1), so the per-call timer applies to each. `email_poll_s` and
`imap_timeout_s`, 30 s each, are `main`'s `IMAP_POLL_INTERVAL` and
`IMAP_SOCKET_TIMEOUT` (`bridge/email_bridge.py`, lines 47 and 51). The
size limit is Gmail's 25 MB ("Gmail sending limits in Google Workspace",
Admin Help).

**Found in the build.** A failed IMAP login left its socket open; the
connection is closed when login raises, so the poll does not hold one
socket per interval.

**The DMARC check** (`intake.dmarc_verified`, its hook in `receive`, the
`email.dmarc` guard seed, and their tests) is its own commit, which
merges with Tom's grant under open question 17.

**Not built here.** The upload case that terminates the
`valor-email-perform` backend, the steer binding of Tom's reply, the
stopped task's held send, the file swap at the bridge level, the guard's
firing read from the row, and `tests/test_live_email.py`.
