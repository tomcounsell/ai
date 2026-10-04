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
- **A send stopped before its end of data line is `failed`** (patch round
  11; it reverses the earlier default that such a send stays in flight).
  The server accepts a message only on that line (RFC 5321 4.1.1.4), so the
  send is certainly not sent. Left in flight, it was looked up in Sent Mail
  on every wake for ever and, on the first miss, Tom was told it "may or
  may not have gone", which is false. A stop after the line is still in
  doubt and stays in flight.
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
| 11. Size arithmetic | Inbound caps dropped; `max_file_bytes` is Gmail's cited 25 MB encoded limit, compared with the encoded size; no timer on any write; an end before the end of data line is a definite refusal, after it `Unknown` with `__context__` named |
| 12. Poison message | Logged, left unseen, the search continues; test added |
| 13. Recipient claim | Threat model reworded: Tom's tap on the full card is the control |
| 14. Premise slips | Both SMTP sources named; `read_key` names the owning command; imports per port decision 30 |
| 15. Limits | No timer on any SMTP or IMAP command and no floor rate; IMAP waits in IDLE |

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
  a failed write such as EPIPE) is definite: `perform` raises
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

The bridge runs sends and lookups in `stop.in_thread`, a worker thread a
stop ends by shutting its connection down.

**Every limit has a source.** No timeout scales with the message and no
floor rate is assumed. No SMTP or IMAP command has a timer: each waits
until the server answers or a stop ends it (patch round 3, below). After a
250 the send is done, so `QUIT` is sent and its reply not awaited. The
watch waits in IDLE (RFC 2177) and issues it again
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
guard's firing are in the DMARC commit. The bridge-level UIDVALIDITY and
`email_since` cases, and the password's absence from a ledger row and a log
line, are not built either (only the UIDVALIDITY read and the exception
text are tested).

## Patch round 1

From the review, test, and docs checks:

| Finding | Handled |
|---|---|
| Lookup scope | A definite failure raises `broker.Failed` (added by 2.3 in `core/broker.py`): `failed`, no lookup. Only a send in doubt reads Sent Mail |
| Garbled replies | Code -1, another code, or a line too long after the end of data line is `Unknown` |
| Not built list | Corrected above |
| Dead plist names | `VALOR_SMTP_TIMEOUT_S` and `VALOR_SMTP_FLOOR_BYTES_PER_S` removed, with `VALOR_EMAIL_POLL_S` and `VALOR_IMAP_TIMEOUT_S` |
| C1, polling | IMAP IDLE, re-issued at 29 minutes (RFC 2177, RFC 3501 section 5.4); `email_poll_s` removed |
| C2, IMAP timeout | Removed; the IDLE re-issue is the read bound while idling |
| C3, SMTP timeouts | No command has a timer; `QUIT` after a 250 is not awaited |
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

Merging with 1.4d needs `Outbox.reconcile`'s `settle` argument, and the crash tests'
`reconcile_after_s=0` dropped. `broker.Failed` is a 2.3 change to `core/broker.py` (added in ffb85c0fb), carried at merge beside the
`core/bridge.py` additions below.

## Patch round 2 (Tom's feedback of 2026-10-03)

Scope: findings 1, 3, 4 and 5, and finding 2 as sends and Sent Mail
lookups each run as their own task. The branch holds no DMARC commit
(aec2bff7f is parked as `m2-3-dmarc-parked`); the docs say email starts,
answers, and steers nothing.

| Finding | Handled | Test |
|---|---|---|
| 1, mail during a search | `imap.idle` reads the `EXISTS` already received on the connection and returns True without idling; `select_inbox` drops the count SELECT reports | `test_mail_announced_during_another_command_is_found_before_idling` |
| 2, a hung server | `EmailBridge.run` starts each `Release` and each dangling effect's Sent Mail lookup as a task of its own, on its own database connection (`Outbox.perform(item, conn)`, `Outbox.settle`, `Outbox.dangling`; a bridge's `reconcile(outbox)` replaces the outbox's serial one at start and on each wake). One task per effect at a time. | `..._never_greets_does_not_hold_the_next_send`, `..._lookup_that_never_answers_does_not_hold_the_outbox` |
| 3, in-doubt sends | `notice.requested` (`send_in_doubt`, `about_key` `send-in-doubt:<effect>`, once) when a perform ends `Unknown`, and on the first wake a dangling send no task is performing is not found | `test_a_send_in_doubt_tells_tom_once_at_once`, `..._cut_off_before_its_greeting_tells_tom_on_the_next_wake` |
| 4, settle test | `test_settle_after_function` filters on its own effect id | itself |
| 5, docs | `docs/bridges/email.md`: an in-doubt send settles on the next wake, with the notice; the watch searches again before idling; a hung server holds only its own effect | |

Also: `tests/mailserver.py` can leave the first N SMTP connections
ungreeted (`Behavior.silent`). The handoff doc's guard count reads four
again, and `m2-1-port.md` no longer says 2.3 adds a mail check.

## Patch round 3 (the lead's decisions on the round 2 report)

| Item | Handled | Test |
|---|---|---|
| No timers at all | `config.SMTPTimeouts` and `Config.smtp_timeouts` are gone. `smtp.perform` sets no socket timeout, sends the body in one `sendall`, and reads each reply until the server answers. RFC 5321 4.5.3.2 asks a client for per-command timeouts; Tom decided none is set ("No timer; stop ends it") | `test_a_connection_closed_before_the_final_reply_is_in_doubt_and_the_lookup_finds_it`, `test_a_9_mb_attachment_sends_through_a_held_read_rate` |
| A stop ends a blocked command | `bridges/email/stop.py`: `Ends` keeps a duplicate descriptor of each socket a call opens (`_SMTP._get_socket`, `_IMAP._create_socket`, so the greeting and the TLS handshake are covered), and `Ends.call` runs a blocking function in a worker thread; a cancel shuts the sockets down, which returns the blocked read, and waits for the thread to return before it re-raises. `EmailBridge.perform` and `lookup` run through `stop.in_thread`; `imap.watch` runs connect, each IMAP step and IDLE through `Ends.call` and closes the connection only after the thread has returned | `test_a_stop_ends_a_send_whose_server_never_answers_at` (EHLO, STARTTLS, the TLS handshake, AUTH, MAIL, RCPT, DATA, the body, the final reply), `..._never_greets`, `test_a_stop_ends_an_imap_server_that_never_greets` (lookup and watch), `test_a_stop_ends_the_watch_idling` |
| The teardown stall | Closing the connection from the stopping side while the IDLE thread waited left that thread in its read: the TLS layer waits on the descriptor with the IDLE deadline, and a closed descriptor never wakes it. The shutdown comes first and the close after the thread returns, so `test_a_file_swapped_after_approval_fails_the_send_and_is_never_sent` passes alone | that test, run alone |
| Plan under 600 lines | "Tech debt absorbed" moved from `m2-3-email.md` into this record (below); the plan's timer text now says no timers | |

Each stop test runs the coroutine under `asyncio.run`, which waits for its
worker threads when it closes the loop, so a thread still blocked holds the
test up; the test then checks that no live thread has a frame in
`bridges/email`. `tests/mailserver.py` gains `Behavior.mute_at` (the session
goes quiet at a command, the TLS handshake, the body, or the final reply) and
`final_reply = None` (close with no reply); `release_silent` is gone.

`core/bridge.py` additions (2.1's builder owns that file's port): the optional
`Bridge.reconcile(outbox)` hook (`serve` calls it in place of the outbox's own
serial one at start and on each wake), `Outbox.dangling()`, `Outbox.settle(effect_id, conn=None)`,
and `Outbox.perform(item, conn=None)`, which performs on the given database
connection. The merge with 2.1 takes these as additions to its port.

## Patch round 4 (the lead's decision on the round 3 review and test)

| Finding | Change | Test |
|---|---|---|
| A cancel of a queued call waited forever, and 14 hung calls filled the shared pool | `Ends.call` runs each call on a thread of its own and sets an asyncio future from it; there is no queue, so a hung server holds only its own thread and a cancelled call always has a started thread to end | `test_every_call_has_a_thread_of_its_own_and_a_queued_one_cannot_exist` (41 hung calls, the last cancelled with the rest) |
| Code and docs disagreed on a stop before the end of data line | A stop writes no outcome at any point and the effect stays in flight, as after a kill; the docs, the plan and the `smtp.py` docstring say so. Tom ("a stop ends a hung one"): his task stop reaches a performing send, and SIGTERM runs `Ends` before the process exits | below |
| Tom's task stop did not reach a performing send | `EmailBridge.until_stopped` listens on `valor_stop` (and reads the `task.stopped` row once listening) for the effect's task and cancels the perform or the Sent Mail lookup, which shuts the connection down. A stopped call writes no outcome and no notice | `test_tom_stopping_the_task_ends_a_send_blocked_on_its_server` |
| SIGTERM killed the process with `Ends` not run | `bridges.email.serve` turns SIGTERM into a cancel of the run; the cancel reaches every blocked call, and the process exits with status 1 | `test_sigterm_ends_a_send_blocked_on_its_server_and_the_bridge_exits` (child process, signalled by its PID) |
| Gaps in the mute tests | `mute_at` gains `EHLO2` (the EHLO after STARTTLS); `QUIT` is muted as well | `test_a_stop_ends_a_send_whose_server_never_answers_at[EHLO2]`, `test_a_send_whose_server_mutes_the_quit_is_done_and_returns` |
| Stale plan text | The crash row, the grant, the Stakes line and the DMARC rows say parked or none; `broker.Failed` is named as a 2.3 change to `core/broker.py` | |

## Patch round 5 (the lead's decision on the round 4 review and test)

| Finding | Change | Test |
|---|---|---|
| A stop listener around the whole settle cancelled the Sent Mail lookup of an already stopped task on every wake: a send the server took never settled | `EmailBridge.until_stopped` wraps only the server call: the lookup in `EmailBridge.lookup`, ended only by a stop notification that arrives while it runs (no read of an earlier `task.stopped`). A stopped lookup raises `Stopped`, a `broker.Unknown`, so `reconcile` concludes nothing and the bridge writes no notice for it | `test_a_send_the_server_took_after_tom_stopped_the_task_settles_from_sent_mail` |
| A stop cancelled `broker.release` partway, so a release for a stopped task was never recorded refused | The race is inside `EmailBridge.perform`, around the SMTP call, after the broker's fence and intent. The broker writes `effect.refused` as before. A send ended there raises `Stopped`: no outcome, and `release` skips the `send_in_doubt` notice for it | `test_a_release_for_a_task_stopped_after_approval_is_recorded_refused` |
| Docs said little of what Tom sees after his own stop | `docs/bridges/email.md` "Stop and recovery": nothing at the moment, the send stays in flight, a later lookup settles it silently or, on a miss, sends one `send_in_doubt` notice | |
| Un-awaited `Outbox.settle` coroutine warning | `until_stopped` takes a callable, so no coroutine exists before it is used | |
| Watch connect socket leak | `imap._open` keeps the connection in a list the watch closes in its `finally`, even when a stop drops the call's result | |
| `test_a_stop_ends_an_imap_server_that_never_greets` failed once under load | `ended` waits for the worker thread to be gone, up to 30 s, rather than reading it at a fixed moment | the same test |
| 13 test dovecots left running by killed runs | `Dovecot` runs in the foreground under a shell holding the read end of a pipe from the test process; when the process ends, however it ends, the shell stops Dovecot | `test_dovecot_does_not_outlive_a_pytest_process_that_was_killed` |

## Patch round 6 (the lead's decision on the round 5 review)

| Finding | Change | Test |
|---|---|---|
| A failed stop listener (its connection dropping) was counted as Tom's stop: the send was cut off, no notice was written, and the outcome said the task was stopped | `EmailBridge.until_stopped` counts a stop only when the listener returns normally; if it raised, the call is awaited to its end. The listener is not re-established, so no later stop reaches that send; SIGTERM still ends it. No retry count and no timer | `test_a_dropped_stop_listener_does_not_end_a_healthy_send` (the listener raises mid-send; the send is `done`, with no notice) |

## Patch round 7 (the lead's decision on the round 6 review and test)

| Finding | Change | Test |
|---|---|---|
| After the stop listener dropped, Tom's stop no longer ended a hung send or a hung Sent Mail lookup | `EmailBridge.until_stopped` does what the outbox does for its own listener: when the listener fails it closes it, waits for the next outbox wake (`tick`, through `woken`), listens on a fresh connection and restarts `stop_heard` with `check=True`, so the durable `task.stopped` row catches a stop that landed in the gap. A connect failure waits for the next wake. No timer, no retry count. Send and lookup share the helper. A lookup for a task stopped before a drop is ended by the restart's check; it has no outcome and runs again on the next wake | `test_tom_stopping_a_hung_send_after_the_stop_listener_dropped_ends_it`, `test_..._a_hung_lookup_...`, the `in_the_gap` case of the first test (a stop that lands while the listener is down, caught by the durable row) |

## Patch round 8 (rebase onto the merged resident kernel and port)

| Finding | Change | Test |
|---|---|---|
| 2.3 sat on an older 2.1 | 2.3's diff was applied as one squashed change onto the merged head, not commit by commit: a commit-by-commit rebase conflicted in eleven files on its first commit and again after. Conflicts resolve toward the merged code: the port in `core/bridge.py` and `docs/bridges/`, the broker's `performers` argument first, `Outbox.settle` and `perform` through `broker.reconcile` and `broker.release`. The email size function takes the file sizes the kernel measures and never reads a file: base64 at 57 raw bytes per 78 encoded, computed exactly. | `test_mail.py` exactness cases at 0, 1, 56, 57, 58, 3000 and 123457 bytes |
| The record named a test that does not exist for the stop that lands in the gap | Round 7 names the `in_the_gap` case of `test_tom_stopping_a_hung_send_after_the_stop_listener_dropped_ends_it` | |
| The woke task was cancelled only on the path that reached the line after the wait | `until_stopped` cancels it in a `finally`. No timer is added to any email command; a stop ends a hung one | |

## Patch round 9 (rebase onto the merged Telegram bridge)

| Finding | Change | Test |
|---|---|---|
| 2.3 sat on a 2.1 without the merged 2.2 | Rebased onto the merged head. Two doc conflicts, `machine.md` and `tech-stack.md`, resolved by keeping Telegram's merged rows and layering email's. The fake bridges module is `tests/bridges.py` with `--import-mode=importlib` in `pyproject.toml`, which is how the `bridges` package is not shadowed; the 2.3 suite passes under it | the full suite |
| `read_key` took a whole `python ...` command | Kept: the email bridge's only key command is `python -m bridges.email keys`, not a `python -m core` subcommand, and this plan (the key directory section) has `credentials.read_key` name the owning command in its errors. The email bridge is its only caller of that form | `test_a_missing_mail_key_names_the_bridge_command_that_writes_it` |

## Patch round 11 (the lead's decisions on the blind review)

| Finding | Change | Test |
|---|---|---|
| 1. The kernel never had `email_address`: reply-all copied Valor to itself and the size measured was not the size sent | `VALOR_EMAIL_ADDRESS` is in the kernel's `PLIST_ENV`; the rollout sets it in the kernel's environment | `test_the_kernel_job_knows_valors_address_when_it_fills_a_reply_all` runs `reply_all` and the size in the plist's own environment |
| 2. A stop before the end of data line left the send in flight for ever, with a false notice | `Ends` keeps what its thread raised; `EmailBridge.perform` re-raises a `broker.Failed` from the thread when a stop ended it, so the effect is `failed`, with no lookup and no notice. See Decided by default | `test_tom_stopping_the_task_ends_a_send_blocked_on_its_server` and the hung-send case, now `failed` with no notice |
| 3. A `.eml` attachment was base64 `message/rfc822` with bare LFs | A `message/*` guess goes as `application/octet-stream`: the file's own bytes, base64, CRLF, measured exactly. Sending it as `message/rfc822` in 7bit or 8bit would rewrite the file's line ends and its size could not be known without reading it | `test_an_eml_attachment_goes_out_as_wire_valid_mime_that_measures_exactly` |
| 4. The watch's database connection was never replaced | `imap.watch` takes a `connect` function: a dropped connection (`poll` raises `OperationalError` for it) is replaced on the next wake (`retry`), and the last one is closed when the watch ends. No new backoff | `test_a_watch_whose_database_connection_dropped_gets_a_new_one_on_the_next_tick` |
| `persist` cut the extension to 16 characters | Removed, no source. One filesystem fact remains: an extension that would make the staged file's name longer than 255 bytes is dropped, since the write would fail and the message could never be received | `test_an_extension_is_kept_whole_unless_the_filesystem_cannot_name_the_file` |
| Docs | Rollout (no Tom step, window steps as the build is), email.md (nothing binds, the stop outcomes, `send_in_doubt`, imports, `.eml`), architecture.md and machine.md (Telegram is built), the port doc (`Bridge.reconcile`, `Outbox.dangling`, `Outbox.settle`, `Outbox.perform(item, conn)`, `broker.Failed`), this plan's tests and files (parked and unbuilt cases marked) | |

## Tech debt absorbed (moved from the plan)

- #3601: the SMTP timeout bounds the whole upload on `main`, so large
  attachments fail; here no write has a timer. The other
  three items in #3601 go with the code not carried.
- #3124 and #2160: moot; sends are held for Tom, steering is in `core/`.
- On `main`: unverified certificates, `_extract_body` raising on an
  unknown charset, the HTML regex, dropped empty-body and no-`From`
  mail, and `\Seen` set before the fetch; each is fixed above.
- Docs placing the bridges' secrets in the Keychain: they go in the
  kernel key directory, for the reason machine.md gives.
