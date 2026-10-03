---
tracking: none
slug: m1-4u-caps
type: bug
status: planned
critique_rounds: 2
review_rounds: 2
---

# 1.4u Invented caps out of the merged code

A bug fix in milestone 1 of [valor-rebuild.md](valor-rebuild.md). Tom's
standing rule: a limit, timeout, refusal, or stop needs a source (Tom,
`valor-rebuild.md`, or a doc the plan cites) or a function (a security read
of turn-owned state, the metering rule, a protocol fact, or a scheduling
fact). Spending is metered only (mission.md, How "metered spending" is
read). This task takes out the limits in `core/`, `tools/`, and
`harnesses/` that have neither, sets the ones whose function is real to
the fact that gives them their number, and ends without a number the
waits those limits end: a call no client waits for is cut, provisioning
stops when `start` is interrupted, and two timeouts report what happened.
It adds no check, gate, hook, or review step. The idle bound is a stop
that routes to Tom and is not in the guards ledger; it is Tom's question
below, and this task leaves it as it is.

Stakes 2: the kernel's gateway, its judgement fan-out, and provisioning.
Line numbers are at `b3f9011c7`.

## Done, as evidence

| Done item | Evidence |
|---|---|
| No case limit on calibration | `test_calibrate_loads_any_number_of_cases`: a 51-case file loads whole; `test_calibrate_refuses_too_many_cases` is gone |
| The fallback leg sends no `max_tokens`; its input limit is its endpoint's context | `test_open_weight_sends_no_output_limit`, `test_open_weight_input_limit_is_its_endpoints_context`; the too-large tests size their inputs from the settings |
| An open-weight call with no reported usage is charged the most its endpoint can produce | `test_open_weight_worst_case_is_its_endpoints_largest_answer` |
| A governance fan-out runs every hunk at once on one Postgres connection | `test_governance_fan_out_runs_past_eight_at_once`, `test_a_300_hunk_fan_out_opens_one_connection` |
| The kernel's open-file limit is raised to its hard limit, or OPEN_MAX, and never lowered | `test_start_raises_the_open_file_limit_and_never_lowers_it` |
| A 429 is `rate_limited`; its endpoint is not asked again before its `Retry-After`, and nothing waits | `test_a_429_holds_its_endpoint_and_the_next_call_goes_to_the_fallback`, `test_a_429_without_retry_after_holds_nothing`, `test_retry_after_as_an_http_date_is_honoured`, `test_a_retry_after_of_non_ascii_digits_holds_nothing` |
| A workspace turn's output per call is Claude Code's own default unless the project spec names one | `test_workspace_turn_sets_no_output_limit_by_default`, `test_a_spec_output_limit_reaches_the_turn` |
| The gateway sets no read or connect timeout of its own | `test_the_gateway_sets_no_upstream_timeout_of_its_own` |
| A call no client waits for is cut and charged | `test_a_turn_that_exits_cuts_its_silent_calls`, `test_a_client_that_disconnects_cuts_its_call` |
| A call that never reached the provider, or was cut before it answered, says so | `test_an_unreachable_upstream_is_a_502_naming_it`, `test_an_upstream_that_closes_before_answering_is_a_502_naming_it` |
| Provisioning's git calls and setup commands run with no limit, and an interrupted `start` kills them | `test_provisioning_runs_past_the_git_timeout`, `test_interrupting_start_kills_provisioning_git`, `test_interrupting_start_kills_a_setup_command`; `setup_timeout_s` is gone |
| A perform's git calls still end at `git_timeout_s` and the deadline | the existing `core/git.py` deadline tests, unchanged |
| A service program that does not finish is a `Refused` naming it, in provisioning and in a run | `test_a_hung_service_program_is_refused_by_name`, `test_a_run_whose_services_hang_returns_the_reason` |
| A Keychain token is used until its stated expiry | `test_a_login_expiring_in_seconds_is_still_used`; the expired case keeps its test |
| A role name may use Postgres's whole identifier length | `test_a_63_character_role_is_accepted_and_created`, `test_a_64_character_role_is_refused` |
| Suite green, ruff clean | `VALOR_TEST_DB=valor_rebuild_test_14u .venv/bin/python -m pytest -q tests`; `uvx ruff check .`, `uvx ruff format --check .` |

## Threat model

The turn controls the diff it leaves (how many hunks, how large each is),
the request bodies its Claude Code sends through the gateway (`max_tokens`
included), when it drops a connection or exits, and every file in its
workspace. It does not control the project spec (Tom's), the provider's
answers and headers, the Keychain, or the origin repository a task is
provisioned from. The kernel must never let the size of a turn's diff
exhaust the machine cluster's connections or its own file descriptors,
never wait forever on a call no client is waiting for, never charge under
the invoice, and never report a cut or unreachable call as anything but
what it was. Nothing in this task reads a turn-owned file.

## The items

`Replacement` is one of: unbounded; derived from a protocol fact (cited);
a timeout matching the client's own (cited); kept with its function
written beside it.

| # | Where | What it does now | Replacement | Why |
|---|---|---|---|---|
| 1 | `core/settings.py:216`, `core/workspace.py:1251-1253` | `verdict_max_bytes`, 256 KiB | not in this task | 1.4s removes it (m1-4s-signal-reads.md); both touch `workspace.py`, so it stays there |
| 2 | `core/judgement_sites.py:377`, `:391-392` | `MAX_CASES = 50`: `load_cases` raises on more | unbounded | `calibrate` asks the cases one after another (`:434-437`), so the count holds no slot, no connection, and nothing turn-owned |
| 3 | `core/settings.py:168-169`, `tools/open_weight.py:51-52`, `:93` | input limit 100,000 estimated tokens; every call sends `max_tokens` 400 | derived: no `max_tokens` sent; input limit `OPEN_WEIGHT_CONTEXT` = 131,072; most output `OPEN_WEIGHT_MAX_COMPLETION` = 117,964 | See "The fallback leg" below |
| 4 | `core/settings.py:171`, `core/judgement_sites.py:283`, `:302` | `judgement_concurrency` = 8 governance calls at once | unbounded, on one shared connection | See "The fan-out" below |
| 5 | `harnesses/claude_code.py:113`, `:146`, `:152`; docstrings `:16-17`, `:134-135` | sets `CLAUDE_CODE_MAX_OUTPUT_TOKENS` = 32000 on every workspace turn | unbounded by Valor: set only when the project spec names `max_output_tokens` | Claude Code documents its own per-model default and cap (code.claude.com/docs/en/env-vars, `CLAUDE_CODE_MAX_OUTPUT_TOKENS`). The gateway charges from each body's `max_tokens` (`core/gateway.py:351`), so metering stays never under the invoice with any value. The spec key is Tom's per-project choice and stays. `turn()` (`:51`, 1024) builds test turns only and is left |
| 6 | `core/gateway.py:272` | `sock_read=300`: an upstream silent for 300 s is cut, and the turn sees 502 "upstream failed" | unbounded: `sock_read=None`, and a call is cut when no client waits for it | See "The gateway" below |
| 7 | `core/settings.py:205`; `core/workspace.py:509-517`, `:524`, `:636`, `:644-645`, `:728-735`; through `core/git.py:157` | provisioning's origin fetch, `ls-remote`, clone, and the clone's cleanup end at `git_timeout_s` (120 s, the perform limit); each setup command ends at `setup_timeout_s` (1200 s) | unbounded, and interruptible: `setup_timeout_s` is removed | See "Provisioning" below |
| 8 | `core/gateway.py:156` | a Keychain token expiring within 30 s is refused as expired | unbounded: compared with `time.time()` | The provider checks the token when the request arrives; a token that expires in flight gets a 401, which the gateway already answers by rereading the Keychain once (`:101-120`) and passing the 401 on |
| 9 | `core/workspace.py:157` | role names `[a-z_][a-z0-9_]{0,40}` | derived: `{0,62}` | Postgres identifiers are at most `NAMEDATALEN - 1` = 63 bytes (Postgres docs, Lexical Structure, Identifiers); a longer name is truncated, so a spec role and its created role would differ. The character set stays: names go into `pgpass` lines and SQL |
| 10 | `core/gateway.py:272`, `:411-412`, `:424` | `sock_connect=10`; a connect failure or timeout is `unsent`, and the turn sees 502 "upstream failed" | unbounded, and an honest outcome | The operating system ends a connect that gets no answer (on macOS about 75 s, the TCP connection-establishment timer). The 502 names what happened (below). Bug fix, no guard |
| 11 | `core/workspace.py:766-775`, `core/router.py:115-118` | a service program past its 120 s raises `TimeoutExpired`; `_Services.up` catches only `Refused`, so a run ends in a traceback | an honest outcome | `_service_run` raises `Refused("<program> did not finish in 120 s")`; `stop_services` (`:926`) catches `Refused` in place of `TimeoutExpired`. The 120 s, `pg_ctl -t 60` (pg_ctl's own default), and the Redis poll keep their function (a hung start holds `provision:<task>` or the run lock) and are not changed here. Bug fix, no guard |
| 12 | `core/settings.py:227`, `core/session.py:100-102` | `idle_turns` = 2: two turns in a row with no signal end the run for Tom | not changed by this task | Tom's question 1. Whichever way he answers, a one-line follow-up does it |

### The fallback leg (row 3)

The pinned endpoint is `qwen/qwen3-235b-a22b-2507` at `parasail/fp8`.
OpenRouter's endpoint listing for the model
(`https://openrouter.ai/api/v1/models/qwen/qwen3-235b-a22b-2507/endpoints`,
checked 2026-10-03) gives that endpoint `context_length` 131072 and
`max_completion_tokens` 117964. Both go into `core/settings.py` beside
`OPEN_WEIGHT_PIN`, with the URL and the date checked, as
`OPEN_WEIGHT_CONTEXT` and `OPEN_WEIGHT_MAX_COMPLETION`.

- **No `max_tokens`.** The 400 was set from "the schema's answer is under
  100 tokens" and measured at most 219 over 35 single-question calls
  (`tools/open_weight.py:98-101`). `BREADTH` asks three questions, and the
  schema's `notes` restates each rubric, so nothing measured covers it. An
  answer cut at 400 reads as `malformed` and fails the leg. The body sends
  no `max_tokens`, and the endpoint's own completion limit applies.
  `open_weight_max_tokens` is removed.
- **Input limit.** `open_weight_max_input_tokens` becomes
  `OPEN_WEIGHT_CONTEXT`. An input the endpoint cannot hold is one the
  provider refuses, so skipping it is a protocol fact. The skip keeps its
  existing `input_too_large` record.
- **Metering.** The leg's `max_output_tokens` (the figure
  `judgement_worst_case` charges, `core/judgement.py:348-350`) becomes
  `OPEN_WEIGHT_MAX_COMPLETION`, the most the endpoint will produce for any
  call, so a call with no reported usage is charged at or over what the
  host can bill. The `judgement.started` row's `max_tokens` field carries
  the same figure. The leg's `estimate` docstring is rewritten to the
  facts.

### The fan-out (row 4)

Each governance judgement opens a Postgres connection for each step and
closes it before the next (`core/judgement.py`: `_open` `:332`, `_call`
`:405`, `_unused` `:415`, `_record` `:465`), and holds none during the
provider call. Every step takes the task's advisory lock
(`core/spending.py:127-129`), so one task's steps already run one at a
time in Postgres. A gather of a few hundred hunks opens a few hundred
connections that queue on that lock, and the first burst can pass
`max_connections` ("too many clients", Postgres docs, Connections and
Authentication).

- `governance` opens one connection and passes it to `judge` as
  `shared=judgement.Shared(conn)`, which pairs the connection with an
  `asyncio.Lock`. Each step's database block, when given it, uses the
  shared connection under the lock in place of `db.connect`; with none it
  connects as now, so the other sites and calibration are unchanged. The
  lock covers the database blocks only, never a leg's `ask`, so the
  steps serialize exactly as the advisory lock already serializes them and
  the provider calls all run at once.
- Every hunk is gathered at once, with `return_exceptions=True`, inside
  the connection's block. A stop that one hunk meets (`TaskStopped` from
  its `_open`) never cuts another hunk short: every hunk runs to its end,
  so each opened call is charged, and only then is the first failure
  raised. `settings.judgement_concurrency` and the semaphore are removed.
- **File descriptors.** Each provider call opens its own aiohttp session
  and socket (`judgement.py:600`). Under launchd the soft open-file limit
  is 256 (`launchctl limit maxfiles`: `256 unlimited` on this machine),
  and 2.1 runs the kernel under launchd, so a few hundred hunks at once
  fail with EMFILE. `core/__main__.py`'s `main` raises the soft
  `RLIMIT_NOFILE` before anything else: it tries the hard limit first,
  and if that is refused, `min(OPEN_MAX, hard)`, the value setrlimit(2)'s
  COMPATIBILITY section names, with `OPEN_MAX` 10240
  (`sys/syslimits.h:100`). It never sets a value at or below the current
  soft limit: a terminal's soft limit on this machine is 1,048,576, above
  `OPEN_MAX`, and Darwin 25 accepts an unlimited soft limit. Those
  numbers are the operating system's, a scheduling fact, not Valor's.

**429.** `judgement.post` (`core/judgement.py:592-609`) returns
`LegError("rate_limited", "none", status=429)` on a 429, and the reason
joins the reasons table (`:137`). When the answer carries `Retry-After`
(delta seconds in ASCII digits, or an HTTP date, RFC 9110 section
10.2.3), `post` records,
for that endpoint URL, the time before which it is not asked again. A
later `post` to that URL inside the hold is not sent and does not wait: it
returns `LegError("rate_limited", "none")` at once, billed nothing, and
the hunk goes to its fallback leg as every failed leg does
(judgement-layer.md: the fallback is the retry). Neither provider
documents a rate limit as a number: TypeSafe's API page says to back off
on 429 or 529, OpenRouter's limits page names no limit for paid models
and says to honour `Retry-After`. Not asking inside the hold is that
documented behaviour, and it adds no number. A 429 without a readable
`Retry-After` holds nothing; the next run asks again, as it does for any failure
(`UNANSWERED_RUNS`, sourced in m1-3-judgement.md). A 429 is never recorded
as a generic `http_status`.

### The gateway (rows 6 and 10)

`sock_read=300` today is the only thing that ends a call whose upstream
goes silent. The gateway runs each metered call as its own task behind
`asyncio.shield` with `handler_cancellation=False` (`gateway.py:265`,
`:328-332`), so it does not see the client give up (Claude Code's
`API_TIMEOUT_MS`, documented default 600000, code.claude.com/docs/en/env-vars).
On a normal turn exit `core/runs.py:124-126` calls `retire`, which does not
cut calls, then `drain` (`:129`) waits for every call to be charged. With
no read timeout, a silent upstream would hold `drain`, the run lock, and
the slot forever, after the stop listener is already gone. The fix ends a
call when no client waits for it, with no number:

- **The turn exits.** A new `Gateway.cut(task_id)` cancels the task's
  `upstream_calls`, the loop `revoke` already runs; `revoke` calls it
  after adding to `revoked`. `core/runs.py`'s normal-exit branch calls
  `gateway.retire(task_id)` then `gateway.cut(task_id)` before `drain`.
  A turn's calls belong to its process; once it has exited and its token
  is retired, no client is left.
- **The client disconnects.** `handler_cancellation=True`. When the
  downstream connection closes, aiohttp cancels `handle`; the `shield`
  keeps the metered call from being cancelled with it, and `handle`
  catches the cancellation, awaits the call so its charge lands, and
  re-raises. `_forward` is cancelled with `handle`, which closes its
  upstream request.
- **Only a registered call is cancelled.** A cancel that lands while a
  call is opening its ledger row or writing its charge would lose the
  charge. So `cut`, `revoke`, and a leaving client cancel a call only
  while it is in `upstream_calls`: it joins that set after its
  `gateway.opened` row and leaves it before `_close`. A client that
  leaves before then marks the call `abandoned`. Right after joining,
  `_metered` checks that mark and that its token's grant is still the one
  it was issued; if either fails, the call is not sent, is charged 0
  (`unsent`), and answers 403. A turn whose token was retired while one of
  its calls opened is therefore never waited on.
- **The charge.** Unchanged: `_metered`'s `CancelledError` branch marks
  the call `cut` and `_close` charges by the existing rules (started:
  every allowed output token; not started: the worst case). Its comment
  says the cancel is a stop or a cut.
- **No timeouts.** `ClientTimeout(total=None)`: no connect or read limit.
- **Honest 502s.** `_metered` keeps the exception that ended a call. With
  no response started it returns 502 with "the gateway could not reach
  the provider (<exception type>)" when `unsent`, and "the provider's
  connection ended before it answered (<exception type>)" otherwise.
  `_forward` does the same. Only the exception's type goes in the
  message; aiohttp's text can carry headers.
- **A 401 rereads the login where it is seen.** With
  `handler_cancellation=True` a client that has read a whole 401 can
  leave before `handle` resumes, so the login cache is invalidated in
  `_metered` and `_forward` as soon as the status arrives, not after
  `handle`'s await. The decision is one method, `Gateway._answered`.

### Provisioning (row 7)

`git_timeout_s` is the perform limit (reconcile waits twice it); a first
fetch of a large repository is not a perform, and 1200 s for a setup
command has no source. What is real is the reason a hang hurts:
provisioning runs in a worker thread under `provision:<task>`
(`core/__main__.py:268`, `:285`), git runs in its own session
(`core/git.py:166`), so an interrupt of `start` reaches neither, and
`asyncio.run` then waits on the thread at shutdown. That is a reason to
make provisioning interruptible, not a number. So:

- `setup_timeout_s` is removed, with `VALOR_SETUP_TIMEOUT_S`.
- `git` gains `Interruptible`, held in a context variable that
  `git.interruptible()` sets for a block; `asyncio.to_thread` copies the
  context, so the provisioning thread runs under it. Under a watch, `_git`
  sets no time limit of its own (a caller's `deadline` still applies);
  outside one it keeps `git_timeout_s`, so performs are unchanged.
- `git.start` starts a process in its own process group and, under a
  watch, records the group. One lock spans the interrupted flag, the
  `Popen`, and the record, so no process starts unseen; once the flag is
  set, `start` raises `git.Interrupted`. `interrupt()` sets the flag,
  sends TERM to every recorded group, waits `reap_grace_s` (the existing
  setting; git removes its lock files on TERM), then sends KILL to the
  groups still recorded. A git call that ran under an interrupted watch
  raises `git.Interrupted`.
- The clone's temporary ref in the shared cache is removed in a `finally`
  under `git.uninterrupted()`, so an interrupt mid-clone leaves no ref.
- `_setup` starts each command through `git.start`, with its output to a
  temporary file the kernel holds open, not a pipe, and `wait()`s on the
  process, with no timeout. The result keeps the last `SETUP_TAIL` (1500)
  characters, read from the last 4 x `SETUP_TAIL` bytes, since a UTF-8
  character is at most 4 bytes. A child the command leaves holding its output
  cannot hold the step open; `runs.reap(mark)` then sweeps it, as now. An
  interrupted provision ends in `Refused("provisioning failed:
  interrupted")`.
- `core/__main__.py`'s `_provision` runs `workspace.provision` in a thread
  under a watch. Ctrl-C, SIGTERM, and SIGHUP of the kernel (the last two
  through `loop.add_signal_handler`) cancel it; on that cancel it calls
  `interrupt()` and awaits the thread before it re-raises, so
  `provision:<task>` is held until the cleanup is done. The handlers stay
  in place through that wait: a second signal cancels the wait, and the
  wait absorbs it (`uncancel`) and goes on until the thread is done.
- 2.1's provision job ("the task stays stoppable", m2-1-resident-kernel.md)
  hooks the task's stop into the same `interrupt()`.
- 1.4c already races its build against the stop and interrupt alone
  (m1-4c-verifier.md:171, :549), and its VM setup also writes its output
  to files, waits on the process, and reaps the group.
- 1.4d's leftover-file sweep relies on every git call that carries a
  credential ending at `git_timeout_s` (m1-4d-credential.md:126-129).
  Provisioning's git calls carry no credential, so that holds.

## The code, by file

- `core/settings.py`: `OPEN_WEIGHT_CONTEXT` and
  `OPEN_WEIGHT_MAX_COMPLETION` with their source; `open_weight_max_tokens`,
  `judgement_concurrency`, and `setup_timeout_s` removed;
  `open_weight_max_input_tokens` set from the context; the comment on the
  legs rewritten to the facts.
- `core/judgement.py`: `Shared` and `_connect`; `judge` takes `shared`;
  `post` returns `rate_limited`, parses `Retry-After`, and refuses inside
  a hold.
- `core/judgement_sites.py`: `MAX_CASES` removed; `governance` shares one
  connection and gathers every hunk with `return_exceptions=True`.
- `tools/open_weight.py`: no `max_tokens` in the body; the leg's input and
  output figures from the endpoint's.
- `core/__main__.py`: the open-file limit raised in `main`; `_provision`
  under an `Interruptible`, with SIGTERM and SIGHUP handled.
- `core/gateway.py`: `cut`; `abandoned`; `handler_cancellation=True` with
  the cancellation handled in `handle`; the registration check in
  `_metered`; `ClientTimeout(total=None)`; the honest 502s; the Keychain
  comparison with no margin.
- `core/runs.py`: `gateway.cut` before `drain` on a normal exit.
- `core/git.py`: `Interruptible`, `Interrupted`, `interruptible()`,
  `uninterrupted()`, `watch()`, `start()`; `_git` with no limit under a
  watch.
- `core/workspace.py`: the clone's ref removed under `uninterrupted()`;
  setup commands through `git.start`, output to a file, waited on, with no
  timeout; role regex
  `{0,62}`; `_service_run` raises `Refused` on timeout; `stop_services`
  catches `Refused`.
- `harnesses/claude_code.py`: `workspace_turn`'s `max_output_tokens`
  defaults to `None`, and the variable is set only when it is given; both
  docstrings say what it does.

One hunk per item where it can be, no renames, no moved code, so rebases
onto 1.4b, 1.4s, 3a, and 3b stay small.

## Tests

New, each of a removed cap's unbounded or honest behavior:

1. `test_calibrate_loads_any_number_of_cases`: `load_cases` on 51 cases
   returns 51.
2. `test_open_weight_sends_no_output_limit`: the body has no `max_tokens`.
3. `test_open_weight_input_limit_is_its_endpoints_context`: the leg's
   input limit is `OPEN_WEIGHT_CONTEXT`.
4. `test_open_weight_worst_case_is_its_endpoints_largest_answer`: a local
   upstream that answers 200 with no usage; the charge is
   `judgement_worst_case(estimate, OPEN_WEIGHT_MAX_COMPLETION, price)`.
5. `test_governance_fan_out_runs_past_eight_at_once`: twelve hunks, a port
   that blocks until all twelve are in flight, then answers; the fan-out
   finishes.
6. `test_a_300_hunk_fan_out_opens_one_connection`: `db.connect` counted
   across a 300-hunk governance run with stub legs: the run's own
   connection only; every judgement row is written.
7. `test_start_raises_the_open_file_limit_and_never_lowers_it`: run in a
   subprocess, from a lowered soft limit and from the one it has; after
   `_raise_open_files` the soft limit is at least what it was, and is the
   hard limit, `min(OPEN_MAX, hard)`, or the limit it already had.
8. `test_a_429_holds_its_endpoint_and_the_next_call_goes_to_the_fallback`:
   the local upstream answers 429 with `Retry-After: 60`; the attempt's
   reason is `rate_limited`; the next judgement within the hold sends
   nothing to that endpoint, returns at once, and its fallback is asked; a
   call to the other endpoint is sent.
9. `test_a_429_without_retry_after_holds_nothing`.
10. `test_retry_after_as_an_http_date_is_honoured`.
11. `test_workspace_turn_sets_no_output_limit_by_default` and
    `test_a_spec_output_limit_reaches_the_turn`: the built command's
    environment, without and with `harness["max_output_tokens"]`.
12. `test_the_gateway_sets_no_upstream_timeout_of_its_own`: the session's
    `ClientTimeout` has no total, connect, or read limit.
13. `test_a_turn_that_exits_cuts_its_silent_calls`: a local upstream that
    accepts a metered request and never answers; a turn whose child holds
    one call through the gateway while the turn exits; the router's run
    returns, `turn.ended` is written, the run lock is free, the call is
    charged its worst case, and `drain` is empty.
14. `test_a_client_that_disconnects_cuts_its_call`: the same silent
    upstream; the test's client closes its connection; the call is
    cancelled and charged once, and the upstream sees its request closed.
    `test_a_started_stream_whose_client_leaves_is_charged_once`: a stream
    that has started, then the client leaves; one `gateway.charged` per
    opened call, every allowed output token.
15. `test_an_unreachable_upstream_is_a_502_naming_it`: the gateway pointed
    at a closed port in 6561-6569; 502, the message says the provider
    could not be reached, `unsent: true`, charged 0.
16. `test_an_upstream_that_closes_before_answering_is_a_502_naming_it`.
17. `test_provisioning_runs_past_the_git_timeout`: with `git_timeout_s`
    patched to 0.001 s, provisioning outside a watch is refused; under
    `git.interruptible()` every provisioning git call runs with no limit,
    except the ref removal, which keeps `git_timeout_s`.
18. `test_interrupting_start_kills_provisioning_git`: a trusted git call
    whose alias sleeps, in a thread under a watch; `interrupt()` ends it
    with `git.Interrupted`, its process is gone, and a later `start` is
    refused. `test_a_call_marked_uninterrupted_runs_after_an_interrupt`.
19. `test_interrupting_start_kills_a_setup_command`: a setup command that
    sleeps beside a child of its own; after `interrupt()` neither process
    is left, and provisioning is refused "interrupted".
    `test_a_setup_command_whose_child_holds_its_output_still_ends`: a
    command that leaves a child holding its output returns, its tail is
    kept, and the child is reaped.
20. `test_a_hung_service_program_is_refused_by_name`: `_service_run` on
    `/bin/sleep` with a short timeout raises `Refused` naming `sleep`.
21. `test_a_run_whose_services_hang_returns_the_reason`: `_Services.up`
    returns the reason; the run's status line carries it.
22. `test_a_login_expiring_in_seconds_is_still_used`: `expiresAt` ten
    seconds out is served.
23. `test_a_63_character_role_is_accepted_and_created`: provisioning with
    it, then a login as that role; `test_a_64_character_role_is_refused`.
24. `test_a_call_revoked_while_it_opens_is_not_sent_and_is_charged_nothing`
    and `test_a_call_whose_client_leaves_while_it_opens_is_not_sent_and_is_charged_nothing`:
    the open held inside its ledger write; the upstream sees nothing; one
    charge of 0, `unsent`.
25. `test_a_revoke_while_a_call_is_being_charged_leaves_one_charge`: the
    charge held inside its write while the task is revoked; one charge row,
    the full one.
26. `test_a_stop_between_two_hunks_opens_leaves_every_opened_call_charged`:
    a stop queued behind the second hunk's open; governance raises
    `TaskStopped`, and every `gateway.opened` row has its `gateway.charged`.
27. `test_a_term_or_hangup_of_the_kernel_interrupts_provisioning`: SIGTERM
    and SIGHUP during `_provision` reach the thread's watch and cancel the
    start.
28. `test_a_second_signal_waits_for_the_provisioning_cleanup`: SIGTERM,
    then SIGHUP while the thread is still cleaning up; `_provision` is not
    done until the thread is, so `provision:<task>` stays held.
29. `test_a_retry_after_of_non_ascii_digits_holds_nothing`: `"²"` and
    Arabic-Indic digits read as no `Retry-After`; the 429 is
    `rate_limited`, goes to the fallback, and the next call asks again.
30. `test_a_401_whose_client_leaves_at_once_still_rereads_the_credential`:
    the upstream answers 401 with a stream that never ends, the client
    reads the status and leaves, and the login is read again.

Changed, since they enshrine a cap:

- `tests/test_judgement_sites.py:534-536` `test_calibrate_refuses_too_many_cases`: removed.
- `tests/test_judgement.py:303-316`: the oversize inputs are sized from
  `settings.jev_max_input_tokens` and `settings.open_weight_max_input_tokens`
  times `bytes_per_token`, plus one, so they follow the settings.
- Any test reading `open_weight_max_tokens`, `judgement_concurrency`, or
  `setup_timeout_s`: reads the replacement or is removed with it.
- `test_revoke_cuts_a_call_still_waiting_on_the_provider_and_still_charges_it`
  also asserts one charge row.

Non-obvious cases covered above: a turn whose call is silent after the
stop listener is gone (13); a `Retry-After` on one endpoint never holds
the other (8); an HTTP-date `Retry-After` (10); an unlimited hard
open-file limit (7); a setup command's own children after an interrupt
(19); a token that expires in flight still gets the gateway's existing 401
reread (`test_an_expired_login_is_read_from_the_keychain_at_most_once_a_minute`,
unchanged); a perform's git calls keep 120 s and the deadline (existing
tests, unchanged).

## What is left out

- Item 1, `verdict_max_bytes`: 1.4s.
- `idle_turns`: Tom's question 1; not touched here.
- The limits the audit found sourced or functional at their number: the
  mirror fetch's file and footprint limits, the judgement leg timeouts,
  `UNANSWERED_RUNS`, `git_timeout_s` for performs, `reconcile_after_s`, the
  round counts, `jev_max_input_tokens`, `bytes_per_token`, `reap_grace_s`,
  `backup_keep`, the port ranges, the Keychain read rate and its 10 s
  `security` kill, `client_max_size`, the effect ceiling, and the
  unpriced-model and stopped-task refusals.
- Deriving `FUNCTION_HUNK_MAX_BYTES` from Jev's input limit: it refuses
  nothing and has its function.
- Truncated text in rows (stderr and setup tails, ledger display): they
  refuse and route nothing.
- `_service_run`'s 120 s and the Redis poll's values: functional; only
  their outcome is fixed here.
- `scripts/`: 1.5 moves every replay script into `tests/emulator/` and
  edits them (m1-5-emulator.md), so this task does not touch them. Their
  verdicts, relayed to builder-1-5:
  - `replay.py` `MAX_RUNS` 16 and `MAX_FAILED_RUNS` 2: invented; 1.5
    removes both.
  - `role_play_tom.py` `max_feedback` 2: sourced (the demonstration's two
    feedback rounds, docs/emulator.md, mission.md); 1.5 keeps it.
  - `judge_replay.py` `DIFF_LIMIT` 70,000, `OUTPUT_TAIL` 4,000,
    `VERIFY_TIMEOUT` 1,800 and `role_play_tom.py` `DIFF_LIMIT` 80,000:
    kept, function a measurement fact: the baseline's scores
    (rebuild-baseline.md) were produced under them, and the gate compares
    against those scores. Each carries that sentence beside it.
  - `replay.py` `proc.wait(timeout=10)` after terminating the local
    upstream: functional, but its expiry is a traceback; 1.5's edit
    catches it.

## Ordering and rebases

1.4u merges after 1.4b and 1.4s; whichever of 1.4u, 3a (`core/gateway.py`)
and 3b (`harnesses/claude_code.py`) lands later rebases. 1.4b touches
`core/settings.py` and `core/workspace.py`; 1.4s touches
`core/workspace.py` and `core/fresh.py`. 1.4c's two `setup_timeout_s`
lines change as described under Provisioning.

## Docs this changes

docs/judgement-layer.md (the fallback's input and output figures at :150,
the 429 hold beside "One call is one attempt" at :153, "at most 50 cases"
at :286), docs/harnesses.md (:112, :324-327), docs/tech-stack.md
(:238-240: the output variable is not part of metering), core/README.md
(:53), and wherever docs name `setup_timeout_s` or the gateway's
timeouts. The docs check writes these.

## Questions for Tom

1. **Grant the idle bound as a guard, or remove it?** Two turns in a row
   with no question, plan, or delivery end a run and hand the task to you.
   That is a stop that routes to you, and it is not in the guards ledger,
   so under the governance paragraph it stays only with your grant
   (ledgered with its incident, mission item 6, and a ninety-day expiry).
   Its incident is pso-a's bare replay, where 2 of 3 turns ended idle
   waiting on background tests that were killed (rebuild-baseline.md,
   Caveats). That cause is already fixed: background tasks are disabled in
   every turn (`CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`,
   `harnesses/claude_code.py:154-157`), and the rerun had no idle turns.
   So the guard has an incident whose mechanism is gone, and it may well
   reach its expiry without firing. Granting keeps a backstop for a stage
   whose turns produce nothing; removing means such a stage runs on until
   it signals, fails, or you stop it. This is your call, and the plan
   takes no side. This task leaves the bound as merged code has it; after
   your answer, a one-line follow-up seeds it or removes it.

## Decided by default

- The gateway cuts a call once its turn has exited or its client has
  disconnected, rather than keeping a read timeout.
- `setup_timeout_s` is removed and provisioning is made interruptible,
  rather than keeping a number with no source.
- The governance fan-out shares one connection, and the kernel's
  open-file limit is raised to the hard limit, rather than bounding how
  many judgements run at once.
- The fallback leg sends no `max_tokens`, and its worst case is the
  endpoint's largest answer.
- A held endpoint answers `rate_limited` at once and its fallback is
  asked; nothing waits on `Retry-After`.
- The project spec's `max_output_tokens` key is kept as Tom's per-project
  choice.

## Patch round 1 (review round 1 of 2)

On top of the docs commit `936c377ae`. Every finding resolved:

- **F1.** `test_calibrate_loads_any_number_of_cases` exists: a 51-case
  file loads whole (test 1).
- **F2.** A second SIGTERM or SIGHUP during `_provision`'s cleanup no
  longer ends the wait: the handlers stay (without them the second signal
  would end the kernel with the cleanup half done), and the wait absorbs
  each further cancel until the interrupt and the thread are both done
  (test 28).
- **Nit.** `_setup`'s tail bytes are 4 x `SETUP_TAIL`, said beside it.
- **T1.** `retry_after` reads only ASCII digits as delta seconds; anything
  else is no hold, and the 429 stays `rate_limited` (test 29).
- The Done table's open-file row names its test as it is,
  `test_start_raises_the_open_file_limit_and_never_lowers_it`; every
  other test the plan names exists, apart from the removed
  `test_calibrate_refuses_too_many_cases`.
- **T2.** The early-leave 401 is a test (30); with the invalidate moved
  back after `handle`'s await it fails.
