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
`harnesses/` that have neither, derives the ones whose function is real
from the fact that gives them their number, and makes two timeout paths
report what happened. It adds no check, gate, hook, or review step. One
item, the idle bound, is a stop that routes to Tom and is not in the guards
ledger; it is Tom's question below.

Stakes 2: the kernel's gateway, its judgement fan-out, and provisioning.
Line numbers are at `b3f9011c7`.

## Done, as evidence

| Done item | Evidence |
|---|---|
| No case limit on calibration | `test_calibrate_loads_any_number_of_cases`: a 51-case file loads whole; `test_calibrate_refuses_too_many_cases` is gone |
| The fallback leg's input limit is its endpoint's documented context less its output | `test_open_weight_input_limit_is_its_context_less_its_output`; the too-large tests size their inputs from the setting |
| The governance fan-out runs as many calls at once as the machine cluster has free connections | `test_governance_fan_out_runs_past_eight_at_once`, `test_governance_fan_out_stays_within_free_connections` |
| A 429 is recorded as `rate_limited`, and its `Retry-After` is honoured before that endpoint is asked again | `test_a_429_with_retry_after_holds_the_next_call_to_that_endpoint`, `test_a_429_without_retry_after_is_rate_limited_and_the_fallback_is_asked` |
| A workspace turn's output per call is Claude Code's own default unless the project spec names one | `test_workspace_turn_sets_no_output_limit_by_default`, `test_a_spec_output_limit_reaches_the_turn` |
| The gateway sets no read or connect timeout of its own | `test_the_gateway_sets_no_upstream_timeout_of_its_own`; `test_a_slow_upstream_answer_reaches_the_turn` |
| A call that never reached the provider says so | `test_an_unreachable_upstream_is_a_502_naming_it`: the body says the provider could not be reached, the charge is 0 with `unsent: true` |
| A call cut before the provider answered says so | `test_an_upstream_that_closes_before_answering_is_a_502_naming_it` |
| Provisioning's fetch, clone, and the clone's cleanup run under the provisioning limit, not the perform limit | `test_provisioning_runs_past_the_git_timeout`: with `VALOR_GIT_TIMEOUT_S` far below what the fetch takes, provisioning succeeds; `test_a_perform_still_ends_at_the_git_timeout` (existing deadline tests, unchanged) |
| The provisioning limit is Claude Code's documented longest foreground command | `test_setup_timeout_is_the_turns_longest_command`: default 600 s; `test_a_setup_command_over_the_limit_records_timeout` (existing, unchanged) |
| A service program that does not finish is a `Refused` naming it, in provisioning and in a run | `test_a_hung_service_program_is_refused_by_name`; `test_a_run_whose_services_hang_returns_the_reason` |
| A Keychain token is used until its stated expiry | `test_a_login_expiring_in_seconds_is_still_used`; the expired case keeps its test |
| A role name may use Postgres's whole identifier length | `test_a_63_character_role_is_accepted_and_created`, `test_a_64_character_role_is_refused` |
| The idle bound is ledgered (on Tom's answer) | `test_migrate_seeds_the_granted_guards_once` lists five guards; the idle guard's payload has its incident, mission item, grant date, and expiry ninety days out |
| Suite green, ruff clean | `VALOR_TEST_DB=valor_rebuild_test_14u .venv/bin/python -m pytest -q tests`; `uvx ruff check .`, `uvx ruff format --check .` |

## Threat model

The turn controls the diff it leaves (how many hunks, how large each is),
the request bodies its Claude Code sends through the gateway (`max_tokens`
included), how slowly it reads a response, and every file in its
workspace. It does not control the project spec (Tom's), the provider's
answers, the Keychain, or the origin repository a task is provisioned
from. The kernel must never let the size of a turn's diff exhaust the
machine cluster's connections, never charge under the invoice, and never
report a cut or unreachable call as anything but what it was. Nothing in
this task reads a turn-owned file.

## The items

`Replacement` is one of: unbounded; derived from a protocol fact (cited);
a timeout matching the client's own (cited); kept with its function
written beside it.

| # | Where | What it does now | Replacement | Why |
|---|---|---|---|---|
| 1 | `core/settings.py:216`, `core/workspace.py:1251-1253` | `verdict_max_bytes`, 256 KiB | not in this task | 1.4s removes it (m1-4s-signal-reads.md); both touch `workspace.py`, so it stays there |
| 2 | `core/judgement_sites.py:377`, `:391-392` | `MAX_CASES = 50`: `load_cases` raises on more | unbounded | Its pair was the calibration spending cap, which is gone. `calibrate` asks the cases one after another (`:434-437`), so the count holds no slot, no connection, and nothing turn-owned |
| 3 | `core/settings.py:168` | `open_weight_max_input_tokens` = 100,000 | derived: `OPEN_WEIGHT_CONTEXT` (131,072) less `open_weight_max_tokens` (400) = 130,672 estimated tokens | The pinned endpoint is `qwen/qwen3-235b-a22b-2507` at `parasail/fp8`; OpenRouter's endpoint listing (`/api/v1/models/qwen/qwen3-235b-a22b-2507/endpoints`, checked 2026-10-03) gives that endpoint `context_length` 131072. A call whose input and output pass the context is one the provider refuses, so the skip is a protocol fact. 100,000 was set to bound a call's cost, which is not a function |
| 4 | `core/settings.py:171`, `core/judgement_sites.py:283`, `:302` | `judgement_concurrency` = 8 governance calls at once | derived: the machine cluster's free client connections, read once at the start of the fan-out | See "The fan-out" below. Neither provider documents a rate limit as a number: TypeSafe's API page says only to back off on 429 or 529; OpenRouter's limits page names no limit for paid models and says to honour `Retry-After`. So rate limits are not a protocol fact for a number, and 429 is handled as the providers document it |
| 5 | `harnesses/claude_code.py:113`, `:146`, `:152`; docstrings `:16-17`, `:134-135` | sets `CLAUDE_CODE_MAX_OUTPUT_TOKENS` = 32000 on every workspace turn | unbounded by Valor: the variable is set only when the project spec names `max_output_tokens` | Claude Code documents its own per-model default and cap (code.claude.com/docs/en/env-vars, `CLAUDE_CODE_MAX_OUTPUT_TOKENS`). The gateway charges from each body's `max_tokens` (`core/gateway.py:358`), so metering stays never under the invoice with any value. The spec key is Tom's per-project choice, so it is kept. `turn()` (`:51`, 1024) builds test turns only and is left |
| 6 | `core/gateway.py:272` | `sock_read=300`: an upstream silent for 300 s is cut, and the turn sees 502 "upstream failed" | unbounded: `sock_read=None` | Claude Code's own client timeout is `API_TIMEOUT_MS`, documented default 600000 (code.claude.com/docs/en/env-vars). The gateway cutting at 300 s ends calls the client is still waiting for. The stop path (`revoke`, `:296-302`) cuts a task's calls at once |
| 7 | `core/workspace.py:509-517`, `:524`, `:636`, `:644-645`, through `core/git.py:157` | `git_timeout_s` (120 s, the perform limit) bounds provisioning's origin fetch, `ls-remote`, clone, and the clone's `reflog expire` and `gc` | kept with its function: these calls run under `setup_timeout_s` | The function is a scheduling fact: provisioning holds `provision:<task>` (`core/__main__.py:268`) in a worker thread (`:285`) that nothing stop-aware watches, and git runs in its own session (`core/git.py:166`), so an interrupt of `start` does not reach it. 120 s belongs to performs (reconcile waits twice it); a first fetch of a large repository is not a perform |
| 8 | `core/gateway.py:156` | a Keychain token expiring within 30 s is refused as expired | unbounded: compared with `time.time()` | The provider checks the token when the request arrives; a token that expires in flight gets a 401, which the gateway already answers by rereading the Keychain once (`:101-120`) and passing the 401 on |
| 9 | `core/workspace.py:157` | role names `[a-z_][a-z0-9_]{0,40}` | derived: `{0,62}` | Postgres identifiers are at most `NAMEDATALEN - 1` = 63 bytes (Postgres docs, Lexical Structure, Identifiers); a longer name is truncated, so a spec role and its created role would differ. The character set stays: names go into `pgpass` lines and SQL |
| 10 | `core/settings.py:205`, `core/workspace.py:728-735` | `setup_timeout_s` = 1200 | kept with its function, value derived: 600 s | Function: the scheduling fact in row 7. Value: a setup command runs in the turn's sandbox and environment (`turn_environment`), and the turn's own Claude Code runs one foreground command for at most `BASH_MAX_TIMEOUT_MS`, documented default 600000 (code.claude.com/docs/en/env-vars); background commands are off (`CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`). A setup the turn could not rerun itself is no longer than the turn's longest command. The comment beside the setting says this |
| 11 | `core/gateway.py:272`, `:411-412`, `:424` | `sock_connect=10`; a connect failure or timeout is `unsent` and the turn sees 502 "upstream failed" | unbounded, and an honest outcome | The client's own `API_TIMEOUT_MS` bounds the wait, and the operating system ends a connect that gets no answer. The 502's message names what happened (below). Bug fix, no guard |
| 12 | `core/workspace.py:766-775`, `core/router.py:115-118` | a service program past its 120 s raises `TimeoutExpired`; `_Services.up` catches only `Refused`, so a run ends in a traceback | an honest outcome | `_service_run` raises `Refused("<program> did not finish in 120 s")`; `stop_services` (`:926`) catches `Refused` in place of `TimeoutExpired`. Provisioning's message names the program. The 120 s, `pg_ctl -t 60` (pg_ctl's own default), and the Redis poll keep their function (a hung start holds `provision:<task>` or the run lock) and are not changed here. Bug fix, no guard |
| 13 | `core/settings.py:227`, `core/session.py:100-102` | `idle_turns` = 2: two turns in a row with no signal end the run "so Tom can look" | ledgered as a seeded guard, on Tom's answer | See "The idle bound" below |

### The fan-out (row 4)

Each governance judgement opens its own connection to the machine cluster
for each step (`judgement.py`: `_open`, the charge in `_call`, `_record`).
A diff of a few hundred hunks gathered at once opens a few hundred, and
Postgres refuses connections past `max_connections` with "too many
clients" (Postgres docs, Connections and Authentication). So the bound is
real and its number is a protocol fact, read live:

```sql
SELECT current_setting('max_connections')::int
     - current_setting('superuser_reserved_connections')::int
     - current_setting('reserved_connections')::int
     - (SELECT count(*) FROM pg_stat_activity WHERE backend_type = 'client backend')
```

`judgement_sites.governance` reads it once on the connection it already
opens (`:279`), and the semaphore takes the larger of that and 1.
`settings.judgement_concurrency` goes. A connection another process takes
between the read and the calls can still meet "too many clients"; that is
the honest Postgres error, and the review's run fails and reruns as any
failed run does.

**429.** `judgement.post` (`core/judgement.py:592-609`) returns
`LegError("rate_limited", "none", status=429)` on a 429, with the
`Retry-After` value in seconds when the header is present (delta seconds
or an HTTP date, RFC 9110 section 10.2.3). It also records, per endpoint
URL, the time before which no call to that endpoint is sent, and every
later `post` to that URL waits until then. This is what both providers'
docs say to do; it adds no number. The hunk whose call got the 429 goes to
its fallback leg, as every failed leg does (judgement-layer.md: the
fallback is the retry). A 429 is billed nothing, the same as every other
error status (`tools/jev.py:78`, `tools/open_weight.py:110`). Without a
`Retry-After`, the reason is still `rate_limited`, and the next run asks
again, as it does for any failure (`UNANSWERED_RUNS`, sourced in
m1-3-judgement.md). A 429 is never recorded as a generic `http_status`.

### The idle bound (row 13)

It is a guard: it ends a run and hands the task to Tom, which is a
checkpoint that holds work (core/guards.py's own definition). Its source
is docs/sdlc-state-machine.md (the `idle` exit) and `valor-rebuild.md`
(absorbs `IDLE_TURNS`); its incident is pso-a's bare replay, where 2 of 3
turns ended idle waiting on killed background tests (rebuild-baseline.md,
Caveats). It is not one of the four checkpoints the 2026-10-01 pipeline
decision names as granted (sdlc-state-machine.md, The pipeline Tom set),
and it is not in the guards ledger. CLAUDE.md requires every guard ledgered
with its incident, mission item, and a ninety-day expiry, and adding
governance takes Tom's tap. So: question 1.

With a yes, `core/guards.py` gains a fifth seeded entry:

- `guard_id`: `machine.GUARD_IDLE = "run.idle"`;
- `name`: "the idle bound: two turns in a row with no stage signal end the run for Tom";
- `incident`: the pso-a line above;
- `mission_items`: `[6]` (a task stuck on turns that produce nothing is a
  failure Tom hears about with its evidence, not one that runs on unseen);
- `source`: sdlc-state-machine.md, build, Exit evidence; valor-rebuild.md, 1.1 Absorbs;
- `granted_at`: the date of Tom's answer, and `expires` ninety days later,
  so `seeded_payload` takes each seed's own grant date (the four keep
  2026-10-01 and 2026-12-30);
- `provenance.via`: "Tom's answer to m1-4u-caps.md question 1, seeded by migrate".

Its firings are the consecutive `idle` turn verdicts the ledger already
holds; nothing new is written when it fires. Deleting expired guards is
milestone 4's routine, as for the other four.

With a no, an unledgered guard cannot stay: `idle_turns` and the `idle`
return go, and a run continues through turns without a signal until one
signals, fails, or Tom stops the task. The state machine doc's `idle` exit
goes with them.

## The code, by file

- `core/settings.py`: `OPEN_WEIGHT_CONTEXT = 131_072` beside
  `OPEN_WEIGHT_PIN`, with the endpoint listing cited;
  `open_weight_max_input_tokens` computed from it and
  `open_weight_max_tokens` (still printed by `python -m core settings`);
  `judgement_concurrency` removed; `setup_timeout_s` default `600` with the
  derivation as its comment; the cap comment on the legs rewritten to the
  facts.
- `core/judgement_sites.py`: `MAX_CASES` and its check removed; the
  semaphore sized from the free-connection read.
- `core/judgement.py`: `post` returns `rate_limited` on 429, parses
  `Retry-After`, and waits on the per-endpoint time; the reason joins the
  reasons table (`:137`).
- `core/gateway.py`: `ClientTimeout(total=None)`; `_metered` keeps the
  exception that ended a call, and when no response was started returns
  502 with "the gateway could not reach the provider (<exception type>)"
  for `unsent`, or "the provider's connection ended before it answered
  (<exception type>)" otherwise (types only: aiohttp's text can name
  headers); `_forward` the same; the Keychain comparison has no margin.
- `core/git.py`: `_git` and `trusted` take `limit: float | None`, default
  `settings.git_timeout_s`; a caller's `deadline` still lowers it.
- `core/workspace.py`: provisioning's `fetch`, `ls-remote`, `clone`,
  `reflog expire`, and `gc` pass `limit=settings.setup_timeout_s`; role
  regex `{0,62}`; `_service_run` raises `Refused` on timeout;
  `stop_services` catches `Refused`.
- `harnesses/claude_code.py`: `workspace_turn`'s `max_output_tokens`
  defaults to `None`, and the variable is set only when it is given; both
  docstrings say what it does.
- `core/guards.py`, `core/machine.py`: the idle seed and `GUARD_IDLE`, on
  Tom's yes.

The diff stays mechanical: one hunk per item, no renames, no moved code,
so rebases onto 1.4b, 1.4s, 3a, and 3b stay small.

## Tests

New, each of a removed cap's unbounded or honest behavior:

1. `test_calibrate_loads_any_number_of_cases`: `load_cases` on 51 cases
   returns 51.
2. `test_open_weight_input_limit_is_its_context_less_its_output`: the
   setting equals `OPEN_WEIGHT_CONTEXT - open_weight_max_tokens`.
3. `test_governance_fan_out_runs_past_eight_at_once`: twelve hunks, a port
   that blocks until all twelve are in flight, then answers; the fan-out
   finishes (with a semaphore of 8 it would hang until the test's
   timeout).
4. `test_governance_fan_out_stays_within_free_connections`: with the
   free-connection read patched to 3, at most 3 are in flight; with it
   patched to 0, one is.
5. `test_free_connections_reads_the_cluster`: on the test cluster the read
   is positive and at most `max_connections`.
6. `test_a_429_with_retry_after_holds_the_next_call_to_that_endpoint`: the
   local upstream answers 429 with `Retry-After: 1`; the attempt's reason
   is `rate_limited`; the next call to that endpoint is sent at least one
   second after the 429, and a call to the other endpoint is not held.
7. `test_a_429_without_retry_after_is_rate_limited_and_the_fallback_is_asked`.
8. `test_retry_after_as_an_http_date_is_honoured`.
9. `test_workspace_turn_sets_no_output_limit_by_default` and
   `test_a_spec_output_limit_reaches_the_turn`: the built command's
   environment, without and with `harness["max_output_tokens"]`.
10. `test_the_gateway_sets_no_upstream_timeout_of_its_own`: the session's
    `ClientTimeout` has no total, connect, or read limit.
11. `test_a_slow_upstream_answer_reaches_the_turn`: a non-streaming
    upstream that answers after a pause the test can afford is passed
    through and charged from its usage.
12. `test_an_unreachable_upstream_is_a_502_naming_it`: the gateway pointed
    at a closed port in 6561-6569; 502, the message says the provider
    could not be reached, `unsent: true`, charged 0.
13. `test_an_upstream_that_closes_before_answering_is_a_502_naming_it`.
14. `test_provisioning_runs_past_the_git_timeout`: with `git_timeout_s`
    patched to a value shorter than a local repository's clone takes,
    provisioning that repository succeeds, and the limit `_git` was given
    for each provisioning call is `setup_timeout_s`.
15. `test_setup_timeout_is_the_turns_longest_command`: default 600.
16. `test_a_hung_service_program_is_refused_by_name`: `_service_run` on
    `/bin/sleep` with a short timeout raises `Refused` naming `sleep`.
17. `test_a_run_whose_services_hang_returns_the_reason`: `_Services.up`
    returns the reason; the run's status line carries it.
18. `test_a_login_expiring_in_seconds_is_still_used`: `expiresAt` ten
    seconds out is served.
19. `test_a_63_character_role_is_accepted_and_created`: provisioning with
    it, then a login as that role; `test_a_64_character_role_is_refused`.

Changed, since they enshrine a cap:

- `tests/test_judgement_sites.py:534-536` `test_calibrate_refuses_too_many_cases`: removed.
- `tests/test_judgement.py:303-316`: the oversize inputs are sized from
  `settings.jev_max_input_tokens` and `settings.open_weight_max_input_tokens`
  times `bytes_per_token`, plus one, so they follow the settings.
- `tests/test_pipeline.py:866-887` `test_migrate_seeds_the_granted_guards_once`:
  five guards, and the idle guard's own grant date and expiry (on Tom's yes).

Non-obvious cases covered above: a connection taken between the free read
and the calls (the honest Postgres error, test 4's zero case shows the
floor of one); a `Retry-After` on one endpoint never holds the other (6);
an HTTP-date `Retry-After` (8); a token that expires in flight still gets
the gateway's existing 401 reread (`test_an_expired_login_is_read_from_the_keychain_at_most_once_a_minute`,
unchanged); a perform's git calls keep 120 s and the deadline
(existing `core/git.py` deadline tests, unchanged).

## What is left out

- Item 1, `verdict_max_bytes`: 1.4s.
- The limits the audit found sourced or functional and fits its number:
  the mirror fetch's file and footprint limits, the judgement leg
  timeouts, `UNANSWERED_RUNS`, `git_timeout_s` for performs,
  `reconcile_after_s`, the round counts, `jev_max_input_tokens`,
  `open_weight_max_tokens`, `bytes_per_token`, `reap_grace_s`,
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
  verdicts, for 1.5's builder:
  - `replay.py` `MAX_RUNS` 16 and `MAX_FAILED_RUNS` 2: invented; 1.5
    removes both.
  - `role_play_tom.py` `max_feedback` 2: sourced (the demonstration's two
    feedback rounds, docs/emulator.md, mission.md); 1.5 keeps it.
  - `judge_replay.py` `DIFF_LIMIT` 70,000, `OUTPUT_TAIL` 4,000,
    `VERIFY_TIMEOUT` 1,800 and `role_play_tom.py` `DIFF_LIMIT` 80,000:
    kept, function a measurement fact: the baseline's scores
    (rebuild-baseline.md) were produced under them, and the gate compares
    against those scores; 1.5 keeps them and records truncation. Each
    should carry that sentence beside it, which 1.5's edit can add.
  - `replay.py` `proc.wait(timeout=10)` after terminating the local
    upstream: functional (scheduling), but its expiry is a traceback; 1.5's
    edit can catch it.

## Ordering and rebases

1.4u merges after 1.4b and 1.4s; whichever of 1.4u, 3a (`core/gateway.py`)
and 3b (`harnesses/claude_code.py`) lands later rebases. 1.4b touches
`core/settings.py` and `core/workspace.py`; 1.4s touches
`core/workspace.py` and `core/fresh.py`. 1.4c plans `setup_timeout_s` for
image builds (m1-4c-verifier.md:170, :221); after this task its value is
600 s with the derivation above, and 1.4c's plan should say whether that
fits an image build. 1.4d's leftover-file sweep relies on every git call
that carries a credential ending at `git_timeout_s` (m1-4d-credential.md:126-129);
provisioning's fetch carries no credential (private fetching is left out
of 1.4d), so that holds.

## Docs this changes

docs/judgement-layer.md (the fallback's input limit at :150, "One call is
one attempt" at :153 gains the 429 sentence, "at most 50 cases" at :286),
docs/harnesses.md (:112, :324-327), docs/tech-stack.md (:238-240: the
output variable is not part of metering), core/README.md (:53), and, on
Tom's yes, docs/sdlc-state-machine.md's `idle` exit naming its guard
record. The docs check writes these.

## Questions for Tom

1. **The idle bound is a stop that hands a task to you, and it is not in
   the guards ledger. Ledger it?** Two turns in a row ending with no
   question, plan, or delivery end the run so you can look. Its incident is
   pso-a's bare replay (2 of 3 turns idle); it would be seeded like the
   other four, with mission item 6 and an expiry ninety days from your
   answer. Recommendation: yes. Without it, a stage whose turns produce
   nothing keeps running unseen until you stop it. Assumed answer for the
   build: yes, seeded with your answer's date. If you say no, the bound and
   the `idle` exit are removed instead.

## Decided by default

- `setup_timeout_s` becomes 600 s, derived from Claude Code's documented
  longest foreground command, not kept at 1200 (which has no source).
- Provisioning's git calls run under `setup_timeout_s` rather than without
  a limit, since an interrupt of `start` does not reach git and nothing
  stop-aware watches the provisioning thread.
- The governance fan-out reads free connections live rather than going
  unbounded, since an unbounded fan-out fails on a large diff with "too
  many clients".
- The project spec's `max_output_tokens` key is kept as Tom's per-project
  choice.
