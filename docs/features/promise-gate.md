# Promise Gate

## What it does

The promise gate is the centralised honesty gate for every agent-to-user
delivery path. It rejects messages that contain *empty forward-deferral
promises* — phrases like *"I'll come back with X"*, *"will follow up"*,
*"stay tuned"*, *"more soon"*, *"I'll report back"* — that the agent
cannot keep, because the agent's session is already ending by the time
the message reaches the user.

Two honest message shapes only:

1. **"I did X"** with concrete evidence (file path, commit hash, queued
   session ID, memory write, service restart).
2. **"I didn't do X because Y"** with explicit reason.

Forward-deferrals are forbidden unless the deferral itself names a
verifiable autonomous-delivery mechanism (queued session ID, scheduled
cron, scheduled agent — surfaced as a `session_id`, `schedule_id`, or
PR URL).

### Fulfilling a forward promise (the check-in primitive)

Detection alone is not enough — a session that legitimately cannot finish
its work this turn needs a way to *create* the scheduled-delivery
mechanism the gate looks for. That is the
[check-in primitive](checkin-primitive.md):

```bash
python -m tools.agent_session_scheduler checkin \
  --prompt "<what to do when it fires>" --in 30m
```

It schedules a one-shot future Eng session (arbitrary prompt, delivered to
the originating chat, at T) and returns a `schedule_id=<hex>` that matches
`_SCHEDULED_DELIVERY_PATTERNS`. Cite that token in the message and the gate
ALLOWs the forward promise. The recovery template names this primitive as
option `(c)`.

## Where it sits — path coverage table

The gate runs at every send-path call site that writes to the Redis
outbox. Every route is gated, but not every route pays for an LLM call —
the judgment layer used is a deliberate per-route choice, not an oversight:

| Route | Entry point | Judgment layer | Audit source(s) | Why this layer |
|---|---|---|---|---|
| Drafter short path (raw output < 200 chars, no SDLC session, no artifacts, no `?`, no fenced code) | `draft_message`'s early return → `_evaluate_drafter_promise(..., use_llm=False)` | Heuristic only (regex `_evaluate_promise_heuristic`) — **zero LLM calls, test-enforced** | `promise_gate_drafter` | Bounds per-message latency on brief replies; short replies are the highest-risk population for empty promises (#2421), so the gate has to be free to run on every one of them |
| Drafter main path (everything else) | `draft_message`'s main return → `_evaluate_drafter_promise(..., use_llm=True)` | LLM-primary (`_evaluate_promise_llm_or_heuristic`), regex fail-closed-only fallback | `promise_gate_drafter_llm` / `promise_gate_drafter_heuristic` / `promise_gate_drafter_timeout` | The composed, longer reply is where forward-deferral prose actually lives (the Incident A class); real callers are `agent/output_handler.py` and `bridge/email_bridge.py`, both defaulting `use_llm=True` |
| Stop hook (`agent/hooks/stop.py`) | Explicit `draft_message(..., use_llm=False)` | Heuristic only, forced regardless of message length | `promise_gate_drafter` (same sources as the short path) | Runs inline on the Stop hook's 10-second harness-wall critical path; an inline LLM round-trip there repeats the documented 126/131 SIGKILL incident (`docs/features/memory-hook-performance.md`) — the fix was "detach, don't bound," not "add a timeout around a Haiku call" |
| Poll questions (`TelegramRelayOutputHandler.send_poll`) | `validate_poll_question` → `_evaluate_promise_heuristic` directly | Heuristic only | none (surfaced as a non-blocking `Violation(rule="poll_question_promise")`, not a full gate call) | Poll questions reach Telegram without ever calling `draft_message`, so they would otherwise ship with zero honesty checking; a poll question is a structured artifact, not a prose reply worth an LLM round-trip. Residual (no LLM backstop) tracked in #3094 |
| Terminal flush (`agent/session_health.flush_deferred_self_draft_sync` + the async email fallback) | `agent/session_health._gate_terminal_promise` | Heuristic only — **the one known-uncovered route**: it never reaches the LLM layer | `terminal_flush` | No live agent exists at flush time to consume an LLM-derived revise-or-override advisory; there is nobody left to self-draft a rewrite, so the heuristic backstop is what actually ships the substitution. Residual (no LLM backstop) tracked in #3094 |
| CLI senders — `tools/send_message.py`, `tools/valor_telegram.py send`, `tools/valor_email.py cmd_send` | `cli_check_or_exit` → `evaluate_promise` (sync wrapper over `evaluate_promise_async`) | LLM-primary, regex fail-closed-only fallback | `promise_gate_llm` / `promise_gate_heuristic` / `promise_gate_timeout` / `promise_gate_disabled` / `promise_gate_cli_exception` | Same LLM-first contract as the drafter main path, reached through the sync CLI wrapper instead of an `await` |

The gate is implemented in [`bridge/promise_gate.py`](../../bridge/promise_gate.py).
Each CLI tool calls `cli_check_or_exit(text, transport, session_id)`
immediately before its Redis `rpush`. The drafter calls the shared
`_evaluate_drafter_promise` helper in `bridge/message_drafter.py`, which
evaluates the exact text about to ship (the verbatim `raw_response` on the
short path, the narration-stripped text on the main path), honors
`PROMISE_GATE_ENABLED`, applies the Job-scoped override check
(`promise_override_active`), and writes the audit entry — see the table
above for which judgment layer runs on which route.
`evaluate_promise`/`evaluate_promise_async` still accept an optional
`classifier_verdict` parameter (kept for backward compatibility) but the
drafter does not populate it.

### Terminal flush

`flush_deferred_self_draft_sync` (and its async email sibling
`_deliver_deferred_self_draft_fallback`) deliver a deferred self-draft when a
session ends before self-draft steering was consumed. At that moment there is
no live agent to self-draft a rewrite, so `needs_self_draft=True` is not an
option. `agent/session_health._gate_terminal_promise` therefore evaluates the
exact text about to ship and, on a block, **substitutes** the honest fallback
`TERMINAL_PROMISE_FALLBACK_MESSAGE` ("I couldn't complete that follow-up
before this session ended — please send the request again if you still need
it."). Suppression is not used, because it reintroduces the swallowed-reply
class. The gate is fail-open for delivery: an evaluation error delivers the
original text rather than swallowing the reply. This route always evaluates
via the regex heuristic — it never reaches the LLM layer the drafter's main
path now uses, because there is no agent left to consume an LLM-derived
revise-or-override advisory. The measurement tool (below) tracks this route's
audit rows separately so a latency or false-positive regression here is
never averaged away by the LLM-covered routes.

## Advisory flow: revise-or-override

On the drafter path the gate is **advisory to the PM**, not merely a block.
A promise-blocked draft carries a revise-or-override suggestion
(`bridge/promise_gate.build_promise_advisory`, strictly **read-only** — a
zero-writes test monkeypatches every Redis write command to explode) that
rides the self-draft steering instruction back to the agent:

- **Revise** — rewrite to claim only delivered work, with evidence.
- **Override** — stand by the obligation by recording an inbound
  expectation on the bound Job (`python -m tools.job_tool expectation-add
  --direction inbound`) and resend. The drafter core
  (`_evaluate_drafter_promise`) downgrades a BLOCK to ALLOW
  (`reason="promise_recorded_override"`, audited) when the session's bound
  Job carries an **open inbound** expectation
  (`bridge.promise_gate.promise_override_active`, resolved through the
  permanent reply index via `bridge.job_router.job_for_session`).
  Discharged expectations do not override, and outbound expectations
  (what a spawned lane owes the PM) never clear the gate.

While the bound Job's goal is still the router's mint placeholder, the same
advisory carries the **goal-authoring nudge** — the second enforcement point
of the PM's goal mandate (the `prime-pm-role` priming is the first).

Expectations are PM-authored and PM-discharged only — no mechanical
trigger ever discharges one. The backstop is the `agent/session_health.py`
sweep's `_check_jobs_at_rest_with_open_expectations`, which surfaces Jobs at
rest with open expectations to the operator log along with the
`metrics:promise_advisories_issued` vs `metrics:expectations_authored`
counters. See [`durability-model.md`](durability-model.md).

## Architectural posture

### LLM-first, regex fail-closed-only

The primary judgment layer is a Haiku call with a strengthened
few-shot prompt that names a *forward-deferral* class. A regex
backstop is the **fail-closed-only** last line that fires solely on
the heuristic-fallback branch (no API key / SDK exception / parse
failure). The heuristic does NOT override an LLM `ALLOW`.

### What the gate actually keys on

The discriminator is **whether the obligation is durably recorded**, not
whether the sentence is grammatically forward-looking. "I'll come back with
X" and "still working on this" are treated identically by the underlying
question the gate asks: *is there a record, findable on the next turn, that
something other than this ending session will deliver on this?* The gate
recognizes exactly three such records:

1. **A Job inbound expectation** — `python -m tools.job_tool
   expectation-add --direction inbound`, resolved through
   `promise_override_active`/`job_for_session`. Job-scoped, not
   per-message: any open inbound expectation on the bound Job clears the
   gate for every outbound on that Job until discharge.
2. **A `schedule_id`** from `python -m tools.agent_session_scheduler
   checkin` — a queued, autonomous-delivery mechanism the
   `_SCHEDULED_DELIVERY_PATTERNS` regex recognizes directly in text.
3. **A PR URL** (`https://github.com/.../pull/N`) — a durable artifact
   anyone can check without the originating session still being alive.

Absent one of these three, both judgment layers treat a forward-looking
claim as unfulfillable regardless of how much substantive content rides
alongside it — a file count, a commit hash, or a bare `#102` issue mention
is not a recorded obligation and does not rescue "still running", "is on
it", or "I'll report back". Only a URL-shaped or session/schedule-shaped
autonomous-delivery reference does, because only those are checkable after
the session ends.

Measured directly against the live LLM layer:

| Phrasing | LLM verdict |
|---|---|
| Present fact, no forward clause ("what read as one config line is 14 files across `tools/` and `config/`") | allow 8/8 |
| Past-tense work plus a bare PR number, no forward clause | allow 8/8 |
| Ongoing clause plus a full `https://github.com/.../pull/N` URL | allow 8/8 |
| Ongoing clause plus a bare `#102` | allow 6/8 (**unreliable**) |
| Real evidence but no artifact yet ("dev is on it; no PR yet") | block 8/8 |
| "Still working on this." | block 8/8 |
| Evidence plus an explicit "I'll report back" tail | block 8/8 |

Two consequences worth knowing before you touch either layer:

- **A sender cannot make a mid-flight report pass by piling on
  evidence.** The supported shapes are (a) state only what is already
  true, with no forward clause, (b) cite a full PR URL, or (c) record the
  obligation on the bound Job or via a scheduled check-in. Callers that
  legitimately need to defer record the promise instead of rephrasing
  around it (see the advisory flow above). `.claude/commands/roles/prime-pm-role.md`
  teaches this to the PM.
- **The two layers genuinely disagree, and that is by design.**
  "Still working on this." is blocked 8/8 by the LLM but *allowed* by
  the heuristic, because no regex covers it. The heuristic is a narrow
  fail-closed backstop for the phrases it does match — never a
  reimplementation of the LLM's judgment, and never asked to generalize
  past the obligation-recorded discriminator above. Do not write a test
  that asserts LLM-equivalent behavior from `_evaluate_promise_heuristic`,
  and do not write one that asserts an LLM verdict for a phrasing that
  measured below 8/8.

### Why the heuristic is fail-closed (vs. RTR's fail-open)

`bridge/read_the_room.py` is the architectural precedent for this gate's
async/sync contract, env-var enable pattern, and SDK timeout. But RTR is
**fail-open** (any error returns `action="send"`), and this gate's heuristic
branch is **fail-closed** (regex match without evidence returns BLOCK). The
two postures are intentionally inverted because the cost of false-positive
is inverted between the two gates.

| Gate | What false-positive means | Cost | Correct posture |
|------|---------------------------|------|-----------------|
| Read-the-Room | Suppress a message that should have been sent | Silent message loss | **Fail-open** |
| Promise gate (judgment branch) | Block a message that's actually honest | Re-emission with the recovery template; sender rephrases and retries | **Fail-closed on heuristic** |
| Promise gate (infrastructure branch) | Block delivery on an asyncio/import/ORM glitch | Silent message loss (identical to RTR's failure mode) | **Fail-open** in `cli_check_or_exit` |

Both gates' postures are coherent: failure modes that produce
silent message loss fail open; failure modes that produce a loud,
recoverable BLOCK fail closed.

## Recovery contract

When the gate blocks, the CLI tool prints the following template to
stderr and exits non-zero:

```
Empty forward-deferral promise blocked by bridge/promise_gate.
The phrase '{quoted offending phrase}' was rejected.

Your session is ending. Do not promise future work. Choose one of:
  (a) Deliver findings now: 'I did X with evidence Y'
  (b) State explicitly that you didn't: 'I didn't do X because Y'

See docs/features/promise-gate.md for the full contract.
```

The agent's loop sees the error, applies one of the two
contractually-acceptable shapes, and re-emits. The second call almost
always passes.

### Why the template never names the kill switch

The recovery template intentionally does **not** mention the kill
switch (`PROMISE_GATE_ENABLED=false`) or any other bypass mechanism.
The agent reads its own stderr to recover; teaching the bypass syntax
in the template would defeat the gate on the first BLOCK. There is no
per-call bypass — operators rephrase blocked messages just like the agent
does. One honesty contract for all senders.

The anti-leak is enforced by tests in
[`tests/unit/test_promise_gate.py::TestRecoveryTemplate`](../../tests/unit/test_promise_gate.py).

## Kill switch (incident-response only)

`PROMISE_GATE_ENABLED=false` disables the gate process-wide. Set it
in `~/Desktop/Valor/.env` or in your shell startup, then restart the
relevant service (bridge, worker). Subsequent gate calls return
ALLOW unconditionally and log `source="promise_gate_disabled"` to
the audit JSONL so the disabled state remains observable.

This is the **only** escape hatch. It is not advertised in the
recovery template, not exposed as a per-call flag, and is intended
solely for incident response (e.g. a regression rolling out a 100%
block rate).

### Env-var contract

Default is **on** (gate enabled). The disable signal must be
explicit:

| `PROMISE_GATE_ENABLED` value | Gate state | Notes |
|------------------------------|------------|-------|
| unset | enabled | default |
| `""` (empty string) | enabled | treated as the default; a stray `PROMISE_GATE_ENABLED=` line in an env file does NOT silently disable the gate |
| whitespace-only (e.g. `"   "`) | enabled | normalized to empty → default |
| `"1"`, `"true"`, `"yes"`, `"on"` (case-insensitive) | enabled | explicit-on |
| `"0"`, `"false"`, `"no"`, `"off"`, or any other non-empty value | disabled | explicit disable signal |

This contract is intentionally stricter than the structurally-similar
`bridge/read_the_room.py:_read_enabled` — RTR's default is `"false"`,
so an empty-string env var matches its default-off state invisibly.
The promise gate's default is `"true"` (default-on safety control),
so empty-string is treated as the default rather than as a disable
signal. Otherwise a stray `PROMISE_GATE_ENABLED=` would silently
disable the gate while telemetry shows `source="promise_gate_disabled"`
on every send.

## Telemetry — two channels with documented asymmetry

### Audit JSONL (universal)

Every gate call writes one entry to `logs/classification_audit.jsonl`
via the `_write_promise_audit` helper. The entry shape:

```json
{
  "ts": "<iso-8601 timestamp>",
  "kind": "promise_gate",
  "text_preview": "I'll come back with X",
  "action": "block",
  "reason": "Forward-deferral without verifiable scheduled-delivery reference",
  "class_": "forward_deferral",
  "transport": "telegram",
  "session_id": "real-session-abc",
  "source": "promise_gate_llm",
  "elapsed_ms": 812.4,
  "queue_wait_ms": 3.1
}
```

`class_` is optional in the verdict tool schema (only `action` and `reason`
are required) — a real Haiku call can return `action="block"` with
`class_=None`. Never assert on `class_` in a test; assert on `action` (and,
where relevant, on `reason`'s text). `elapsed_ms`/`queue_wait_ms` are present
whenever the call actually reached the LLM-attempt code path (any of the
`llm`/`heuristic`/`timeout` suffixes below, on both the CLI and drafter
namespaces); they are omitted on the kill-switch and classifier-delegation
short-circuits, which never call the LLM and have nothing to time.

The `source` discriminator takes one of:

| Source | When |
|--------|------|
| `promise_gate_llm` | CLI path (`evaluate_promise`/`evaluate_promise_async`): LLM Haiku call returned a parseable verdict |
| `promise_gate_heuristic` | CLI path: LLM unavailable / parse failure → fell through to regex |
| `promise_gate_timeout` | CLI path: LLM SDK 3-second timeout, or the bounded semaphore-acquire wait, fired |
| `promise_gate_disabled` | CLI path: kill switch was on |
| `promise_gate_drafter_delegation` | Verdict derived from a pre-computed `classifier_verdict` (backward-compat path; the drafter does not populate this) |
| `promise_gate_drafter` | Drafter short path (`use_llm=False`, both the <200-char early return and the Stop hook's forced-heuristic call) |
| `promise_gate_drafter_disabled` | Drafter path: kill switch was on (any length) — records `action="allow" / reason="gate_disabled"`. Distinct from `promise_gate_drafter` so the disabled state is greppable by source on this route, mirroring `promise_gate_disabled` on the CLI path |
| `promise_gate_drafter_llm` | Drafter main path (`use_llm=True`): LLM Haiku call returned a parseable verdict |
| `promise_gate_drafter_heuristic` | Drafter main path: LLM unavailable / parse failure → fell through to regex |
| `promise_gate_drafter_timeout` | Drafter main path: LLM SDK 3-second timeout, or the bounded semaphore-acquire wait, fired |
| `terminal_flush` | Terminal-flush decision (`_gate_terminal_promise` in `agent/session_health.py`, heuristic-only); a block means the honest fallback was substituted |
| `promise_gate_cli_exception` | `cli_check_or_exit` swallowed an unexpected raise (fail-open) |

Roughly 40 rows written before this instrumentation existed carry no `kind`
field at all (and no `elapsed_ms`/`queue_wait_ms`); every reader of this
JSONL — including the measurement tool below — must tolerate that shape
rather than assuming `kind` is always present.

Empty-input calls (empty / whitespace-only / `None` text) write **no**
audit entry. Every other branch writes one.

### session_events (conditional on real AgentSession)

The gate also emits `promise_gate.blocked`, `promise_gate.disabled`,
and `promise_gate.timeout` session_events via best-effort
`AgentSession.query.get(session_id)` (Popoto ORM, never raw Redis
per CLAUDE.md). On real-session hit, the event is appended to
`session.session_events` and the session is saved. On miss
(synthetic `cli-{epoch}` ID, stale ID, lookup error), session_events
emission is silently skipped — only the audit JSONL fires.

This preserves the stateless-judgment claim: the
gate makes **no** AgentSession state-driven decision; the existence
check on the explicit input is for telemetry routing only.

### `session_id` provenance per CLI

The four CLI paths produce `session_id` values with different
semantics:

| CLI | session_id source | session_events emission |
|-----|-------------------|-------------------------|
| `tools/send_message.py` | reads real `VALOR_SESSION_ID` from worker harness env (or accepts whatever its caller passes) | fires conditional on lookup |
| `tools/valor_telegram.py send` | synthetic `cli-{epoch}` | always skipped (audit-only) |
| `tools/valor_email.py cmd_send` | synthetic `cli-{int(time.time())}-{pid}-{hex8}` | always skipped (audit-only) |

The audit JSONL records the literal `session_id` regardless of
provenance. The dashboard reads from `session_events` for real-session
gate activity; CLI-originated activity on synthetic IDs is observable
only through the audit JSONL today (a JSONL-backed dashboard tile is
documented as a follow-up).

## Latency budget

* p50 < 500ms
* p99 < 3s

The SDK-level 3-second timeout is enforced via the RTR-correct
pattern: `async with semaphore_slot(timeout=RTR_SDK_TIMEOUT): async with
anthropic.AsyncAnthropic(timeout=RTR_SDK_TIMEOUT) as client:`. The
semaphore acquire itself is bounded by the same timeout — a caller that
cannot get a slot within `RTR_SDK_TIMEOUT` raises `TimeoutError` rather
than queuing indefinitely, and that wait is measured as `queue_wait_ms`
on the audit row, separately from the LLM call's own `elapsed_ms`. This is
not a coroutine-level timeout around the API call itself (which stays
forbidden — see below); it only bounds how long a call waits for a
semaphore slot, so it does not reintroduce the #1055 hazard. Coroutine-level
timeouts (`asyncio.wait_for`) around the API call are forbidden — they
leak httpx connections under cancellation.

`RTR_SDK_TIMEOUT` is **imported** from `bridge.read_the_room` rather
than redefined locally — both gates share the same SDK invariant;
copying the literal value would risk drift.

On timeout, the gate falls through to the heuristic (sub-millisecond)
and writes the audit entry with `source="promise_gate_timeout"`.

## Failure modes

| Mode | Posture | Behavior |
|------|---------|----------|
| LLM unavailable (no API key) | Heuristic fallback (fail-closed-only) | Regex returns BLOCK on forward-deferral / behavioral-change without evidence; ALLOW otherwise |
| LLM SDK exception | Heuristic fallback | Same as above |
| LLM SDK parse failure | Heuristic fallback | Same as above |
| LLM SDK 3-second timeout | Heuristic fallback | Same as above; audit `source="promise_gate_timeout"`; `promise_gate.timeout` session_event on real-session |
| Kill switch on | Audit + skip | Audit JSONL written first; ALLOW returned; `promise_gate.disabled` session_event on real-session |
| Audit log write fails | Silent log warning | Gate continues; gate's verdict not affected |
| `cli_check_or_exit` swallows unexpected raise | Fail-open (infrastructure branch) | Logs warning; writes audit `source="promise_gate_cli_exception"`; CLI proceeds to outbox write |
| LLM path reached while an event loop is already running | Heuristic fallthrough | `_run_async_safely` cannot use `asyncio.run` inside a running loop; it **closes** the coroutine and returns `None` (heuristic takes over). Only reachable under a test harness / async caller — production reaches the sync API from a CLI context with no running loop. |

### The `_run_async_safely` running-loop guard

`evaluate_promise` is a sync API; on the CLI Haiku path it runs
`_run_async_safely(_evaluate_promise_async(text))`. `_run_async_safely` calls
`asyncio.run(coro)`, which raises `RuntimeError` if an event loop is already running —
**before** it ever touches the coroutine. Because the `_evaluate_promise_async(text)`
argument was eagerly created, it would be neither awaited nor closed, leaking
`coroutine '_evaluate_promise_async' was never awaited` at GC/teardown. The
running-loop branch therefore calls `coro.close()` before returning `None`.
Behavior is unchanged: in production there is no running loop so
`asyncio.run` really awaits the coroutine; the close-branch is only exercised
under tests.

## Tests

* [`tests/unit/test_promise_gate.py`](../../tests/unit/test_promise_gate.py) — main test module. Covers
  empty-input, kill switch, classifier_verdict short-circuit, all five
  forward-deferral phrases (LLM-mocked + heuristic + scheduled-delivery
  override + B2 substantive-content rule), behavioral-change regression,
  recovery template anti-leak, and `cli_check_or_exit`
  exception-swallow semantics.
* [`tests/unit/test_promise_gate_audit.py`](../../tests/unit/test_promise_gate_audit.py) — covers the
  forked `_write_promise_audit` helper and the drafter-path
  `source="promise_gate_drafter"` audit entries.
* [`tests/unit/test_message_drafter.py`](../../tests/unit/test_message_drafter.py) —
  `TestShortOutputPromiseGate` covers the short-output reachability fix with the
  exact 172-char incident text, the benign false-positive guard, and the
  kill-switch contract. `TestMainPathLLMWiring` proves the short path issues
  zero LLM calls, that the Stop hook's `use_llm=False` call shape issues zero
  LLM calls, and the main path's LLM-exception/timeout fallthrough to the
  heuristic. `TestPollQuestionHeuristicGate` covers the poll-question honesty
  check (`validate_poll_question` → heuristic, non-blocking `Violation`).
* [`tests/unit/test_promise_advisory.py`](../../tests/unit/test_promise_advisory.py) —
  `TestPromiseOverride` covers the Job-scoped `promise_recorded_override`
  path against both the heuristic and a real LLM call (Incident A text),
  plus the discharged-expectation non-override case.
* [`tests/unit/test_deferred_self_draft_completed.py`](../../tests/unit/test_deferred_self_draft_completed.py) —
  `TestTerminalFlushPromiseGate` covers the terminal-flush substitution,
  `source="terminal_flush"` audit, kill switch, and the guard that the
  substitute itself passes the heuristic.
* [`tests/unit/test_promise_gate_session_events.py`](../../tests/unit/test_promise_gate_session_events.py) — covers
  conditional session_events emission with real and synthetic
  session_ids.
* `tests/unit/test_send_message.py`,
  `valor_telegram/test_valor_telegram_rtr.py`, `test_valor_email.py` — each adds a
  `--help` anti-leak test asserting the help output never advertises
  the bypass syntax.
* [`tests/integration/test_promise_gate_real_api.py`](../../tests/integration/test_promise_gate_real_api.py) —
  real Anthropic API calls (skipped without `ANTHROPIC_API_KEY`). Asserts
  `action`/`reason`/audit-record shape only, never `class_` — `class_` is
  optional in the verdict tool schema, so a real Haiku call can legitimately
  return `action="block"` with `class_=None` (#3016). Covers the
  forward-deferral BLOCK case, the honest-completion ALLOW case, and the
  Job-scoped override case: an identical forward-looking message with a
  recorded open inbound expectation on the bound Job passes as
  `promise_recorded_override`.

## Phase-4 measurement tool

[`tools/promise_gate_measurement.py`](../../tools/promise_gate_measurement.py)
samples `logs/classification_audit.jsonl` and an optional file of sampled
`ask_coverage` schema objects (issue #3027's PM-turn schema field — see
[Message Drafter](message-drafter.md)), and reports:

* **Latency percentiles (p50/p95/p99) grouped by audit `source` AND
  `transport`.** A single blended latency number is dominated by whichever
  calling surface sends the most traffic (PM-turn/SDLC scenarios in
  practice) and does not characterize general outbound traffic — the
  per-(source, transport) breakdown is required to see whether, say, the
  terminal-flush route or email transport has a distinct latency profile.
* **Queue-wait percentiles (p50/p95/p99)**, grouped the same way and reported
  alongside the latency bucket for each (source, transport) pair. This is
  `queue_wait_ms` — time spent waiting on the `semaphore_slot` — split out
  from `elapsed_ms` (API round-trip time), which is the number the #3035
  phase-4 decision needs to distinguish "too many callers queued on the
  semaphore" from "the API itself is slow".
* **Contradiction flags** — an `ask_coverage` disposition (`delivered` /
  `blocked` / `declined` / `not_started`) contradicted by its own `evidence`
  text (e.g. `delivered` paired with evidence that reads as a failure/negative
  outcome), or a `delivered` entry with empty evidence that should have been
  invalidated upstream by `_normalize_ask_coverage` but wasn't.

It tolerates the ~40 legacy audit rows written before the `kind` field
existed (treated as `kind="promise_gate"` for grouping purposes) and rows
missing `elapsed_ms`/`queue_wait_ms` (each field excluded from its own
percentile math independently, never treated as zero).

```bash
python -m tools.promise_gate_measurement \
  --audit-log logs/classification_audit.jsonl \
  --ask-coverage-file <path-to-sampled-ask_coverage.jsonl>   # optional
```

Run `python -m tools.promise_gate_measurement --help` for the full flag
list. This tool's output is the **recorded entry criterion** for the
deferred phase-4 decision tracked in #3035 (embargoed until 2026-09-10) —
that issue does not re-derive its own measurement approach; it reads this
tool's report.

## Operations

### Disabling the gate during an outage

```bash
# Add to ~/Desktop/Valor/.env
echo 'PROMISE_GATE_ENABLED=false' >> ~/Desktop/Valor/.env

# Restart bridge + worker
./scripts/valor-service.sh restart
```

Watch the audit JSONL to confirm gate calls are now logging
`source="promise_gate_disabled"`:

```bash
tail -f logs/classification_audit.jsonl | grep promise_gate
```

### Re-enabling after the outage

```bash
sed -i '' '/^PROMISE_GATE_ENABLED=/d' ~/Desktop/Valor/.env
./scripts/valor-service.sh restart
```

### Tuning the LLM prompt

The forward-deferral and behavioral-change few-shot examples live in
`bridge/promise_gate.py::PROMISE_GATE_SYSTEM_PROMPT`. The drafter does not
have its own classifier system prompt — empty-promise detection runs via
`_evaluate_drafter_promise` (a regex/heuristic helper), not a Haiku call.
If telemetry shows a class of false-positives the LLM cannot catch from
text alone, the `PROMISE_GATE_SYSTEM_PROMPT` in `bridge/promise_gate.py`
is the right knob to turn (for the CLI send paths that call `evaluate_promise`).
