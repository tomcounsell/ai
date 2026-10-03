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
| 5. No later reconcile | Port decisions 22 and 23; reconcile settles a send from Sent Mail once no process performs it, and a miss leaves it in flight; Gmail's filing delay is measured in the window |
| 6. Sender filter and `email_since` | Filter built from `owns` and `owned`; contradictions fixed (other addresses not received; forgeries tested from owned addresses); `xtom@` test; cost under Left out |
| 7. Guard row | Incident restated; `mission_items` `[6]`; `source` added; per-guard `note` and `via` overrides; `GUARD_DMARC` in `guards.py`; expiry note in the window |
| 8. DMARC on intra-domain mail | Tom's pre-window step 1; DKIM key in step 2 |
| 9. Which backend to terminate | `valor-email-perform`; invariant asserted; exactly-one shown by the after-DATA hook |
| 10. `LIST (SPECIAL-USE)` on Gmail | Plain `LIST`; `X-GM-RAW` when `X-GM-EXT-1`; the window records which works |
| 11. Size arithmetic | Inbound caps dropped; `max_file_bytes` is Gmail's cited 25 MB encoded limit, compared with the encoded size; a timer per send call; a timeout before the end of data line is a definite refusal, after it `Unknown` with `__context__` named |
| 12. Poison message | Logged, left unseen, the search continues; test added |
| 13. Recipient claim | Threat model reworded: Tom's tap on the full card is the control |
| 14. Premise slips | Both SMTP sources named; `read_key` names the owning command; imports per port decision 30 |
| 15. Limits | RFC 5321's per-command timeouts and no floor rate; IMAP waits in IDLE with no socket timeout |

## Build

The build reads two of the lead's decisions, and every limit it sets has
a source.

**Reconcile has no age rule.** Task 1.4d (`core/performing.py`) holds a
file lock per effect for as long as any process or thread performs it;
reconcile runs once that lock is free and reads the target. For email the
target is Sent Mail, read by Message-ID. Gmail copies a message sent
through SMTP into Sent Mail ("Choose your IMAP email client settings for
Gmail", Gmail Help, https://support.google.com/mail/answer/78892); no
document gives the time that filing takes, so Sent Mail stays out of the
common path and a miss there settles nothing:

- The 250 reply settles `done` in `perform` (RFC 5321 section 4.1.1.4:
  the server accepts the message with that reply, or refuses it).
- Any end before the end of data line has gone in full (a refused step,
  a timeout, a failed write such as EPIPE) is definite: `perform` raises
  `SendRefused`, a `broker.Failed`, and the broker writes `failed` with
  no lookup. A 4xx or 5xx reply to that line is definite too (section
  4.2.1).
- Any other end after that line (no reply, a garbled reply, a line too
  long to read, another code) is `broker.Unknown`. So is the lookup that
  does not find the message, as the port's contract allows
  (`docs/plans/m2-1-port.md`: `Unknown` from the lookup leaves the intent
  in flight, and the outbox's reconcile settles it on a later tick). The
  outbox reconciles on each wake, so a message Gmail files late is found
  on a later wake; one never filed stays in flight, listed by
  `tasks.audit`. No settle time and no `reconcile_after_s` apply, here or
  on 1.4d's base.

The bridge runs sends in its module's `in_thread`, which is
`asyncio.to_thread` here and becomes 1.4d's `performing.in_thread` on the
combined base, so the worker thread holds the effect's lock while it
runs.

**Every limit has a source.** No timeout scales with the message and no
floor rate is assumed. The SMTP timeouts are the minimums RFC 5321
section 4.5.3.2 gives a client (`bridges/email/config.py`,
`SMTPTimeouts`): the 220 greeting 300 s (4.5.3.2.1), `MAIL` and `RCPT`
300 s (4.5.3.2.2, 4.5.3.2.3), `DATA`'s 354 120 s (4.5.3.2.4), each send
call of the body 180 s (4.5.3.2.5), the final reply 600 s (4.5.3.2.6).
EHLO, STARTTLS, and AUTH have no value in that section and wait with no
timer; after a 250 the send is done, so `QUIT` is sent and its reply not
awaited. A send call carries at most one TLS record, 16,384 bytes (RFC
8446 section 5.1), so the per-call timer applies to each. IMAP has no
socket timeout. The watch waits in IDLE (RFC 2177) and issues it again
every 29 minutes (`imap.IDLE_REISSUE_S`), inside the 30 minute
inactivity autologout of RFC 3501 section 5.4; a failed watch connects
again on the outbox's next wake (`Bridge.tick()`). The size limit is
Gmail's 25 MB ("Gmail sending limits in Google Workspace", Admin Help).

**Found in the build.** A failed IMAP login left its socket open; the
connection is closed when login raises.

**The DMARC check** (`intake.dmarc_verified`, its hook in `receive`, the
`email.dmarc` guard seed, its firing row, and their tests) is its own
commit, which merges with Tom's grant under open question 17.

**Not built here.** The upload case that terminates the
`valor-email-perform` backend, the stopped task's held send, and
`tests/test_live_email.py`. The steer binding of Tom's reply and the
guard's firing are in the DMARC commit.

## Patch round 1

From the review, test, and docs checks:

| Finding | Handled |
|---|---|
| Lookup scope | A definite failure raises `broker.Failed` (new in `core/broker.py`): `failed`, no lookup. Only a send in doubt reads Sent Mail |
| Garbled replies | Code -1, another code, or a line too long after the end of data line is `Unknown` |
| Not built list | Corrected above |
| Dead plist names | `VALOR_SMTP_TIMEOUT_S` and `VALOR_SMTP_FLOOR_BYTES_PER_S` removed, with `VALOR_EMAIL_POLL_S` and `VALOR_IMAP_TIMEOUT_S` |
| C1, polling | IMAP IDLE, re-issued at 29 minutes (RFC 2177, RFC 3501 section 5.4); `email_poll_s` removed |
| C2, IMAP timeout | Removed; the IDLE re-issue is the read bound while idling |
| C3, SMTP timeouts | EHLO, STARTTLS, and AUTH wait with no timer; `QUIT` after a 250 is not awaited |
| Filing delay | Not found is `Unknown`; the intent stays in flight until a later wake finds it |
| Mislabelled kill test | Renamed to a send killed before the server took the message; EPIPE before the end of data line tested |
| Guard firing | `guard.fired` on the `guards` stream, in the record's transaction in `intake.receive`; `guards.fired` reads it |

## Checks, round 2 (of 2), at ca86e796e and aec2bff7f

ca86e796e is the bridge and patch round 1 with no DMARC check; aec2bff7f is
the one DMARC commit on top, which is not built into the system until Tom
grants open question 17.

- Docs: updated, 2817e0cda (`m2-1-port.md`: email searches `UNSEEN SINCE`
  after each IDLE wake). The status-quo docs are true without the DMARC
  commit.
- Test: gaps. Base 566, ca86e796e 627, aec2bff7f 650 passed, 7 skipped;
  ruff clean. A definite failure is `failed` with no lookup; an in-doubt
  reply settles `done` from Sent Mail on the next wake; a miss stays in
  flight until the message is filed; new mail arrives by IDLE in under a
  second; `guard.fired` is written only for unverified mail from Tom's
  address. Gaps: no test of a hung EHLO, STARTTLS or AUTH, or of the
  mid-IDLE reconnect.
- Review: changes on ca86e796e; `governance_refused` on aec2bff7f (it adds
  a check and a guard; the grant named is a standing default, not Tom's
  tap, and no spoofed mail has arrived). `broker.Failed` fits the port
  contract. No invented caps.

Findings:

1. Mail that arrives during a search is announced (`EXISTS`) inside that
   search's responses, so the next IDLE does not see it and the mail waits
   up to 29 minutes. Fix: before idling, read the untagged responses
   already received and search again.
2. No timer on EHLO, STARTTLS, AUTH or any IMAP command, no socket timeout
   and no keepalive. Sends and Sent Mail lookups run inside the outbox
   loop, so one server that stays connected and never answers stalls every
   send, the tick and the watch's reconnect, until the process is killed.
   RFC 5321 4.5.3.2 requires per-command timeouts and gives no value for
   these commands.
3. An in-doubt send that is never found stays in flight, one lookup per
   wake, and nothing tells Tom. A send killed before its greeting does the
   same.
4. `test_settle_after_function` settles sends other tests leave in flight;
   it should filter on its own effect id.
5. Docs: an in-doubt send settles on the next wake, not at once.

## Delivery: delivered, not passed

Review rounds are spent. Recommendation to Tom: one more patch.

- Findings 1, 4 and 5 as above.
- Finding 2 without a number: run each send and each Sent Mail lookup as
  its own task off the outbox loop, so a hung server stalls only its own
  effect and a stop still ends it. The per-command timer values RFC 5321
  asks for are Tom's to give, or he accepts the hang ending only on stop.
- Finding 3: a notice to Tom the moment a send is first in doubt, with no
  wait (2.1's notice path).
- The DMARC commit merges only with Tom's tap under open question 17.

Merging with 1.4d needs `in_thread` read as `performing.in_thread`,
`Outbox.reconcile`'s `settle` argument, and the crash tests'
`reconcile_after_s=0` dropped. `broker.Failed` is new in 2.1's
`core/broker.py` and is carried at merge.
