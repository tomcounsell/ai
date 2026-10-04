# 2.1 The resident kernel: the record

The critique and patch rounds of [m2-1-resident-kernel.md](m2-1-resident-kernel.md),
in order.

## Critique round 1 (of 2): revise

Each finding of critique-2-1-r1.md, and how this revision handles it.

- F1, performers per process: Performers per task from the Brief, in
  `step` and per effect in recover; 1.4d's signatures and async `refuse`;
  `declared_performers()` in every task.
- F2, signals lost after `turn.ended`: recover re-collects through
  `read_turn_file`; test added.
- F3, live calls charged: `holder` on `gateway.opened`; only free holders
  are charged; test added.
- F4, schema not re-runnable: `IF NOT EXISTS`, `OR REPLACE`, drop then
  create the trigger; `test_migrate_twice`.
- F5, nested `sent`: one GIN index over `COALESCE` of both paths; test.
- F6, refused releases yielded forever: yielded only with no intent,
  outcome, or `effect.refused`; the bridge release appends it once;
  `release.requested` on the task stream.
- F7, approval then release in `bind`: `bind` writes the approval and
  `release.requested` together; `schedule` releases kernel ones; merge
  self-restart and approve-after-stop covered.
- F8, propose-class reconcile: `reconcile` reads the intent row; test.
- F9, sweep stops kept services: `services:<task>` lock; sweep needs both
  locks free; 1.4b noted.
- F10, steering spent by fresh turns: only working-session turns spend
  it; test during checks.
- F11, binding: the port's decisions 10 to 14.
- F12, context identity: `kernel_commit` and `offered` on `turn.started`;
  the claim restated; fresh-process test.
- F13, the slot: `core/slot.py`, per turn, FIFO, shared with `core run`
  and the checks; 4.3 adds its sort key and preemption.
- F14, the port: [m2-1-port.md](m2-1-port.md), written to the lead's
  decisions.
- F15, the operator group: settings, `valor.toml`, rollout step 2, and Q1
  narrowed to identities.
- F16: `Spec` fields, the plist `PATH`, plists printed for Tom (decision
  31), the caffeinate limit, a connection per concurrent intake call,
  notices deduplicated across kernels.
- F17: no governance added (the DMARC check is 2.3's, round 2 E).
- F18: Q2 decided by default (decision 15).
- F19: limits cited as protocol facts with split and request-time refusal
  (decision 15b); near-miss notices (15a); the start rule widened to
  `valor` for unlisted chats.

## Critique round 2 (of 2): revise

Each finding of critique-2-1-r2.md and how it is built in.

- A: the intent carries the action; reconcile reads it, falling back to
  `effect.held`; 1.4d builds to the shape; test added.
- B: `slot.held` is reentrant within a process; test added.
- C: a binding that raises binds `none` and owes a notice; `approve`
  reads the effect first (port item 39); test added.
- D: a refused kernel release appends `effect.refused` once and owes a
  notice (item 40); test added.
- E: the DMARC check is 2.3's, under its grant (item 11a); email is
  `verified: false` until it lands.
- F: a provision job off the loop; a failure owes a notice; test added.
- G: `settle_after_s` is a number or a function (item 37).
- H, I: the limits live in `core/bridge.py`; Telegram counts UTF-16
  units; email's limit is the whole message through a size function
  (item 15c).
- J: `Bridge.tick()` (item 38).
- K: every MTProto record is verified; operator status is decided at
  bind; chat ids are marked strings in records and in `sent`.
- L: a steer no runner reads owes a notice.
- M: `recollect` reads `handled/` and what remains in `.valor/`, and
  rebuilds `state` and `finished` from the turn rows; test added.
- N: one services handle per task in `serve`; the reap runs when it
  first starts them; `core run` refuses a task the kernel holds.
- Low 1: an absent `machine` is `settings.default_machine`. Low 2: the
  start rule cites 2.3's rollout. Low 3: 4.1 dropped from the schema
  row. Low 4: 4.3 is told the slot is `core/slot.py`. Low 5: `offered`
  is recorded after narrowing. Low 6: the port says how a lookup finds
  the intent's `at`. Low 7: `step` takes a Performers factory. Low 8:
  the cc is decided by default. Low 9: an email near-approve notice says
  approvals come by Telegram.

## Patch round 1

Review round 1 said `changes`, the test check `gaps`. Retries ride the
`serve_tick_s` wake or a row on the task's stream.

1, 3. A failed `services:<task>` claim, a release that raises other than
   a refusal, or services that cannot open park the task and free the
   harness slot until its next row or the next tick. Tests.
2. `slot.held` unlocks before closing its session.
4. `message.bound` is each binding's first row; a second binder writes
   nothing. Test with two binders.
5. `core/README.md` brought to the code.
6. A notice with no chat writes `notice.undeliverable`. Test.
7. Files alone from Tom start a task listing them. Test.
8. The outbox relistens after a drop; an `is_error` turn leaves the
   steering; one task's error is logged and the rest go on. Tests.
   Telegram `verified` is true for every record (Telegram attests the
   sender id); bind checks `sender_id == operator_telegram_id`. The
   port said so; m2-2-telegram.md's row 11 is corrected.
9. Tests: `recorded`, `claimed`, one bridge per channel and machine,
   `core run` refusing while `services:<task>` is held.
10. `intake.lowest(channel, chat)`, the smallest integer id recorded for
    the chat or None, so 2.2's gap fill of a chat with no seen entry
    stops there by membership (D32). Test with `highest`.
11. The plan and the port describe reconcile as waiting on the
    effect's performing lock, then reading the remote; no age.
12. The rounds live in this file, linked from the plan, so each file
    stays under 600 lines.

## Checks after patch round 1, at e4dc6cfb0 (review round 2 of 2)

- Test: `gaps`. 564 passed, 7 skipped; two failures from other suites
  running at once, each passing alone. Ruff clean. Probes show parked
  tasks neither block others nor loop, `slot.held` releases on a raised
  error and a killed backend, and notices dedupe. Finding: `intake.lowest`
  and `intake.highest` raise `NumericValueOutOfRange` on an all-digit id
  longer than 19 digits; Telegram ids fit.
- Review: `changes`; governance boolean no; no invented caps; the six
  threat model items hold. Findings, both reproduced by probes:
  1. A task stopped between steps keeps its services. `Kernel.active()`
     skips stopped tasks, so `settle` never runs for them; Postgres and
     Redis stay up and `services:<task>` stays held until the kernel
     exits, so `workspace.sweep` cannot stop them either.
  2. A row written during a step can be lost. A step that ends without
     moving marks every row up to then as seen, including rows another
     writer added while it ran, so Tom steering mid-turn before the turn
     fails leaves nothing to step the task again. The tick clears only
     parked tasks.
  3. Docs: `docs/data.md`'s session locks omit `services:<task>`.
  Item 11 of patch round 1 holds in the plan and port only: the code still
  settles on `reconcile_after_s`, which 1.4d deletes.
- Docs: `updated`, 83bfcc026 on `m2-1-docs2`.

## Delivery: delivered-not-passed

Review rounds are spent. Recommendation: accept one more patch that
settles a stopped task (services down, lock released) when the stop is
read, marks as seen only the rows the step read, makes the id match in
`lowest` and `highest` fit a bigint, and adds `services:<task>` to
`docs/data.md`, each with a test; rerun the three checks; merge if they
pass. 2.2, 2.3 and 4.1 build on this task.

## Patch round 2

Scope: the Delivery's recommendation (Tom's feedback of 2026-10-03).
Rebased onto the rebuild branch at ca620a91f (3a, 3c and 4.2 merged).

1. A stopped task settles. `Kernel.schedule` settles every task whose
   services the kernel holds that is no longer active and has no job:
   its services stop and `services:<task>` is released on the wake that
   reads the stop. Test: `test_a_stop_between_steps_settles_the_task`
   (a provisioned Redis, planned, then stopped between steps: the Redis
   is gone and the lock is free).
2. A step marks as seen only the rows it read and the rows it wrote.
   `ledger.WRITTEN`, a contextvar the kernel sets around a step, collects
   the ids `ledger.append` writes in it; after a step that did not move,
   seen advances over the step's own rows and stops at the first row
   another writer added while it ran, so that row steps the task again.
   The gateway's rows (`gateway.opened`, `gateway.refused`,
   `gateway.charged`) join the notice rows as rows that wake no step,
   since the gateway writes them outside the step's context. Test:
   `test_a_row_written_during_a_step_steps_it_again` (a gateway row
   during a step steps nothing; a row added during the next step steps
   the task once more, and its own rows do not).
3. `intake.highest` and `intake.lowest` count an all-digit id only when
   it fits a bigint, compared as numeric first, so a longer id neither
   raises nor counts. Test:
   `test_highest_and_lowest_skip_ids_past_a_bigint`.
4. `docs/data.md` names `services:<task>` among the session locks;
   `docs/harnesses.md` says the kernel keeps services up between steps
   and stops them on waiting, merge, or a stop; `docs/architecture.md`
   says a row written during a step steps the task again.
5. From the rebase: `recover` sums an interrupted turn's charges with
   3a's `spending.turn_spent` (numeric), not a bigint sum of its own;
   `turn.started` carries 4.2's persona digest and size beside
   `kernel_commit` and `offered`; `signals.collect` and `recollect` both
   read 3c's screens after the signal files.
6. Question 1 (the operator) is under "Decided by default".
7. From 2.2's review: the fold's `question.asked` branch set
   `return_to` before reading `question_id`, so a row without one was
   ignored yet changed the fold. It now reads the id first. The
   Hypothesis example (a `question.asked` with an empty payload in
   `plan`) is an `@example` on
   `test_every_prefix_folds_to_exactly_one_state`.
8. A collect after a kill reads the screens 3c had already filed.
   `signals.filed_screens` inspects `.valor/handled/<turn>/screens/` in
   place, with the same descriptor checks as `read_screens` (both now
   share `_open_dirs` and `_inspect`); `recollect` records those and the
   ones still in `.valor/screens/`, once per name. `docs/browser.md`
   says so. Test: `test_recollect_reads_the_screens_a_killed_collect_had_filed`
   (a filed screen, a filed link refused, an unfiled one moved; a second
   recollect gives the same list).

Each new test fails on the code before this round. Suite: 763 passed,
11 skipped (`valor_rebuild_test_21build`, ports 6440-6449). Ruff check and
format check clean.

## Patch round 3

Scope: the blocking finding and the low findings of the review after
patch round 2, and the tester's gaps, as the lead decided.

1. An OpenAI-route gateway call carries `holder: run:<task>`, as the
   Anthropic route's does, so `recover` charges one a killed kernel left
   open. Test: `test_a_call_a_killed_kernel_left_open_is_charged_by_recover`
   (the call opened and never charged; `recover` charges its estimate and
   `tasks.audit` is empty).
2. `Kernel.settle` closes `services:<task>` even when stopping the
   services raises. Test:
   `test_settle_releases_the_lock_when_stopping_the_services_fails`.
3. `recollect` reads what is still in `.valor/` first, moving it aside,
   then what `handled/<turn>/` holds: a file in both places is read as the
   one filed last, which is the copy kept, as screens are. Test:
   `test_recollect_reads_the_text_signal_it_keeps`.
4. `intake.highest` and `intake.lowest` compare an id with the bigint
   maximum as text (length, then digits) and cast only one that fits, so
   an id of any length is skipped and never overflows the cast. The
   comparison stays in SQL, so paging reads one aggregate, not every id
   of the chat. Test: `test_highest_and_lowest_skip_ids_past_a_bigint`
   with an id of 131073 digits.
5. A `question.asked` whose `question_id` is not a string is malformed
   and ignored, as one without an id is. Test:
   `test_a_question_id_that_is_not_a_string_is_malformed`.
6. Tests: `test_tick_called` waits for the bridge's tick event, not a
   fixed sleep; `test_a_stop_between_steps_settles_the_task` takes its
   ports from `VALOR_TEST_PORTS` when set.
7. L2 (a row committing with an id below one already seen) is a
   follow-up, under "Decided by default".

Each new test fails on the code before this round. Suite: 771 passed,
11 skipped (`valor_rebuild_test_21build`, ports 6440-6449). Ruff check and
format check clean.

## Patch round 4

Scope: the blocking finding of the review after patch round 3, as the
lead decided.

1. `serve.serve` builds its gateway with `openai_credential=OpenAIKey()`,
   as `core run` does, so an OpenAI-route call under the resident kernel
   goes upstream with the installed key, not the turn's own. `token()` is
   None when the key file is missing, so startup is unchanged without it.
   Test: `test_the_kernel_gateway_sends_the_installed_openai_key` (runs
   `serve.serve` with no gateway passed, a key file and a local upstream;
   the upstream sees the installed key on `/v1/responses`).

The new test fails on the code before this round (the upstream saw the
turn's own authorization). Suite: 772 passed, 11 skipped
(`valor_rebuild_test_21build`, ports 6440-6449). Ruff check and format
check clean.

## Patch round 5

Scope: the blocking finding and two of the notes of the review after
patch round 4, as the lead decided.

1. `runs.kernel_commit()` is no longer cached: it reads the checkout's
   HEAD (`runs.CHECKOUT`) on every turn, so under `serve`, after the
   checkout moves, `turn.started` records the commit its persona and stage
   files were read from. Test:
   `test_turn_started_records_the_commit_its_persona_was_read_from` (one
   process, a checkout committed between two turns; each row's commit is
   the HEAD of its turn and the persona digests follow).
2. `test_services_survive_between_steps` takes its ports from
   `VALOR_TEST_PORTS`, through the helper `own_ports` it now shares with
   `test_a_stop_between_steps_settles_the_task`.
3. `docs/machine.md` says `serve` builds the judgement port at start, so
   a rotated key takes a restart, and that with a key missing `serve`
   exits and launchd's `KeepAlive` restarts it, writing the refusal to
   `kernel.log` each time. `docs/data.md` says when `kernel_commit` is
   read.
4. L1 (the plist's overrides) and the earlier late-commit note are
   follow-ups, under "Decided by default".

The new test fails with the cache restored. Suite: 773 passed, 11
skipped (`valor_rebuild_test_21build`, ports 6440-6449). Ruff check and
format check clean.

## Rebase onto 2418d02c8

2.1 (squashed from m2-1-docs6 at 94bd1a7ac, whose checks passed) rebased
onto the rebuild branch at 2418d02c8 (1.4b, 1.4i, 1.4s, 1.4u, 1.4v, 1.4w,
3b, 1.4d, and 1.5 merged). The tip is the status quo; 2.1's changes sit on
top of it, through its mechanisms.

Conflicts and resolutions:

1. `core/broker.py`: rebuilt from the tip (explicit `Performers` passed
   positionally, async performers, the effect's lock file
   `core/performing.py`), with 2.1's request id, the key ending in the
   effect id, declared bridge performers (`release.requested`,
   `released`), the refused release's notice, `dangling`, and `Unknown`
   laid on it. The tip's lock file settles a dangling intent; no age is
   read.
2. `broker.CURRENT` is dropped. The composition root's `_performers`
   joins the tip's (the merge with URL, branch, and GitHub credential)
   with every declared send, and `router.run`, `router.step`,
   `serve.recover`, and the kernel's release take the factory
   (`router.PerformersFactory`) and build a task's Performers from its
   Brief. `tests/scripted.route` passes the factory; `performers_of` is
   gone. The kill test's hanging runner passes `ctx.performers`, so its
   Brief offers what the scripted one does.
3. `core/router.py`: the tip's interruptible `_Services.up` (no time
   limit; a stop or a cancel interrupts it) with 2.1's `claim`, `close`,
   and `refresh`, and `run` as `step` repeated. `refresh` reads the Brief
   again only while the handle knows no workspace, so a handle built with
   its layout (the tip's interrupt tests) keeps it.
4. `core/runs.py`, `core/tasks.py`, `core/session.py`: the tip's
   `offered` argument carries what `CURRENT` carried; the turn slot and
   `kernel_commit` are kept.
5. `core/signals.py`: the tip's descriptor walk. `recollect` is rebuilt
   on it (`open_turn_dir`, `read_turn_file`, `open_plain_file`); a
   screen that is a link reads as the tip words it.
6. `core/settings.py`: the tip's fields (no `reconcile_after_s`,
   `git_timeout_s`, `idle_turns`, `setup_timeout_s`) with 2.1's machine,
   operator, inbound, and `serve_tick_s` fields.
7. Docs: the tip's text with 2.1's additions. `docs/sandbox.md` takes the
   tip's two wording changes to the section it holds;
   `docs/sandbox-openings.md` points at it.

Dropped, since the tip removed what they rested on: `Declared.settle_after_s`
and `settle()`, email's settle at `reconcile_after_s`, the sentence on it in
`docs/bridges/email.md`, and the plan's settle bullet and test. A bridge send
whose perform ended with no outcome is read from the server on the next
reconcile; one the server does not hold is `failed`
(`test_a_killed_send_the_server_never_got_reconciles_failed`,
`test_unknown_leaves_intent`). Nothing new limits, waits, or guards.

Suite: 1211 passed, 21 skipped (`valor_rebuild_test_21build`, ports
6430-6439). Ruff check and format check clean.

## Patch round 6

Scope: the two required findings of the review of the rebased candidate
(e68a6b820, docs at 24a495320) and the port's lookup contract, as the
lead decided.

1. R1. The kernel no longer reads a file a send names. `Declared` carries
   the task's workspace (`declared_performers(brief.workspace)` in
   `_performers`); its `refuse` runs the type's `check`, then sizes each
   file there through 1.4s's `open_plain_file`: every component opened
   relative to a descriptor with `O_NOFOLLOW`, the file opened
   nonblocking, its size from `fstat`, never a byte read, in a worker
   thread. A path that is missing, a link (at any component), a FIFO, a
   file with a second link, relative, or outside the workspace gets one
   answer, "not a regular file in the task's workspace", so a refusal says
   nothing about a path outside. A task with no workspace sends no file.
   The sha256 comparison is gone from the kernel; the bridge's `perform`
   reads each file once and refuses a mismatch (the port, unchanged). A
   bridge's `Bound` runs `check` alone. `message_bytes` takes the files'
   sizes, so email's size function needs no read either. Tests:
   `test_files_refused_alike_and_never_read` (a link to `/etc/hosts`, a
   linked directory, a missing path, a path outside, an absent path
   outside, `..`, a FIFO, a hard link, and a relative path: one reason,
   with `open` and `os.read` refused for the block),
   `test_oversize_file_refused_at_request` (a sparse file one byte over
   2000 MiB refused and one at the limit held, unread);
   `test_file_hash_mismatch` is gone.
2. R2. `checks.test_runner` and `fresh.docs_runner` hold the turn slot
   (`slot.held(task_id, dsn)`) around the whole check, as the plan's
   Design says; the docs check's inner turn takes it again as a no-op.
   Test: `test_a_check_holds_the_slot` (both checks wait while a turn
   holds the slot and hold it while they run); it fails without the
   change.
3. The port: `lookup` returns None only when the platform can no longer
   record the send, and raises `broker.Unknown` while it still might.
   `broker.reconcile` and `Bound` say so; the code already wrote nothing
   on `Unknown`. `docs/plans/m2-2-telegram.md` no longer names a settle
   time: a send killed before its send record is settled `failed` by the
   first reconcile once its lock is free.

Docs: `docs/bridges/telegram.md` and `docs/bridges/email.md` say where a
file must be and who reads it. Nothing new limits, waits, or guards.

Suite: 1213 passed, 21 skipped (`valor_rebuild_test_21p6`, ports 6430-6439).
Ruff check and format check clean.

## Patch round 7

Scope: G1 of the test of round 6 and L1 of its review, as the lead
decided.

1. G1. A send whose `files` is not a list, or holds an entry that is not
   an object, is refused at request with the one answer every unsized
   file gets, "not a regular file in the task's workspace", and the
   refusal is ledgered, so the turn is collected. `_size_refusal` takes a
   non-list `files` as its one entry and a non-object entry as its own
   path. Test: `test_malformed_files_refused_at_request` (`["x"]`,
   `[null]`, `5`, `{"a": 1}`, `[[1]]` through `broker.request` on the test
   database, each refused with one `effect.refused` on the task); it fails
   without the change.
2. L1. The usage strings of `telegram.send_message` and `email.send` say
   each file path is absolute and inside the turn's workspace.

Nothing new limits, waits, or guards. A NUL in a send's payload fails in
Postgres when the request is ledgered; that is the ledger's and not this
round's.

Suite: 1218 passed, 21 skipped (`valor_rebuild_test_2_1p7`, ports
6430-6439). Ruff check and format check clean.

## Patch round 8

Scope: R1 of the review of round 7 and its recommendation on the reason,
as the lead decided.

1. R1. `_size_refusal` no longer coerces. `files` absent or None is no
   files; otherwise it must be a list whose every entry is an object with
   a string `path` and a string `sha256`, the port's shape and what the
   bridge's `perform` reads. Anything else is refused, so a bare object,
   a bare string, a list of strings, an entry without a string sha256,
   and `0`, `""`, `{}` no longer reach Tom.
2. A shape refusal has its own reason, "files must be a list of {path,
   sha256} objects", and does not echo the value. A well-formed entry
   whose path is missing, a link, or outside the workspace keeps the one
   file answer, "not a regular file in the task's workspace". Both are
   protocol facts; neither reads a file.
3. Test: `test_malformed_files_refused_at_request` runs through
   `broker.request` on the test database with round 7's five shapes, the
   three accepted ones of R1, entries missing or mistyping `path` or
   `sha256`, `0`, `""`, `{}`, and two well-formed lists naming an absent
   file; each is refused with its reason and one `effect.refused` on the
   task. `test_well_formed_files_pass_to_sizing` holds None, `[]`, and a
   well-formed list of a workspace file for Tom. The shape cases fail
   without the change.
4. `docs/bridges/telegram.md` and `email.md` state the shape and its
   refusal.

Nothing new limits, waits, or guards.

Suite: 1232 passed, 21 skipped (`valor_rebuild_test_2_1p8`, ports
6460-6469). Ruff check and format check clean.

## Patch round 9

Scope: a turn is never left uncollected because of a NUL character in an
action, as the lead decided (the gap of the test of round 8, and the NUL
the test of round 6 noted).

1. A path holding a NUL character names no file, and `os.open` raised
   `ValueError` on it out of `open_plain_file`, so `broker.request` raised
   for a send naming one. `workspace._parts` now takes such a path as not a
   plain relative path, so every descriptor walk answers it with a reason,
   and a send's file gets the one file answer, "not a regular file in the
   task's workspace". Test: `test_a_nul_in_a_file_path_gets_the_file_answer`
   (a NUL in the file name and in a directory component).
2. Postgres jsonb cannot store a NUL character. Every row an effect gets
   (`effect.refused`, `effect.held`, `effect.intent`) holds the payload
   whole, and `turn.collected` holds each request, so a request holding
   one failed when it was ledgered, whatever its type, and the turn was
   not collected; a send whose path holds one failed there too, after
   item 1's refusal. Rewriting the payload would break the rule that a row
   holds what its digest names and what Tom approves, so the request is
   answered where the turn's files are read: `signals._request` records a
   request holding a NUL anywhere (type, target, payload, keys included)
   as unreadable, "it holds a NUL character, which the ledger's JSON
   (Postgres jsonb) cannot store", without the request, as it records one
   that is not JSON. It never reaches the broker; the turn is collected
   and its next prompt names the file and the reason. Test:
   `test_a_nul_in_a_request_is_answered_and_the_turn_collected` on the
   test database, through `signals.collect` and `session.record` (which
   calls `broker.request`): a send with a NUL in a file path, an email
   with one in its subject, a request with one deep in its payload, and
   one in a target, each answered; a clean send beside them is held. It
   fails with `UntranslatableCharacter` without the change.
3. `docs/harnesses.md` says such a request is recorded with an error.

`broker.request` called directly with a payload holding a NUL still fails
at the ledger: its callers are the turn's requests, read by `signals`,
and the kernel's own merge. Nothing new limits, waits, or guards.

Suite: 1234 passed, 21 skipped (`valor_rebuild_test_2_1p9`, ports 6430-6439).
Ruff check and format check clean.

## Patch round 10

Scope: a turn is never left uncollected because of a request Postgres jsonb
cannot store, as the lead decided (R1 of the review of round 9).

1. `json.loads` accepts three things jsonb refuses: a `\u0000` escape (a
   NUL character), an unpaired surrogate escape such as `\ud800`, and
   `NaN`, `Infinity`, or a number too large for a float, which becomes
   infinite and is written back as `Infinity`. Each failed at
   `ledger.append`, so the turn was not collected. `signals._request` now
   records a request holding any of them anywhere (type, target, payload,
   keys included) as unreadable, "it holds a NUL character" (or "an
   unpaired surrogate", or "a NaN or infinite number"), "which the
   ledger's JSON (Postgres jsonb) cannot store". A surrogate pair is one
   character to jsonb and is stored. Checked against jsonb on this
   machine's Postgres 18: `"\u0000"`, `"\ud800"`, `"\udc00"`,
   `"\ude00\ud83d"`, `NaN` and `Infinity` are refused; `1e400` as text
   and a paired emoji are stored.
2. Test: `test_a_request_jsonb_cannot_store_is_answered_and_the_turn_collected`
   (round 9's NUL test, renamed and extended) on the test database,
   through `signals.collect` and `session.record`: round 9's four NUL
   requests, a lone high surrogate in a subject, a lone low surrogate as a
   key, a swapped pair, a NaN, a negative and a positive infinity, `1e999`, and a lone surrogate
   in an action type, a target, a nested value and a send's file path, each
   answered with its reason; a clean send holding a paired emoji is held
   with the emoji intact. Without the change it fails with
   `InvalidTextRepresentation` (the surrogate), and with the number check
   alone removed, on the `NaN` token.
3. The `effects` row in `docs/harnesses.md` and the signals module
   docstring name all three.

A NUL in `question.md`, `no_question.md`, `done.md` or `plan.json`, and an
unpaired surrogate or non-finite number in `plan.json`, are a later
follow-up. Nothing new limits, waits, or guards.

Suite: 1234 passed, 21 skipped (`valor_rebuild_test_2_1p10`, ports 6470-6479).
Ruff check and format check clean.
