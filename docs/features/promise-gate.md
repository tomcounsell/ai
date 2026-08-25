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

## Where it sits

The gate runs at every send-path call site that writes to the Redis
outbox:

| Send path | Gate state |
|---|---|
| Worker path (nudge loop, `bridge/message_drafter.draft_message`) | Gated via `_evaluate_drafter_promise` in the drafter on **both** length regimes — the short-output (<200 char) early return and the full composed path; `needs_self_draft=True` triggers self-draft steering instead of delivery. Every decision audits with `source="promise_gate_drafter"` |
| Terminal flush (`agent/session_health.flush_deferred_self_draft_sync` + the async email fallback) | **Gated** via `_gate_terminal_promise`: a promise-flagged deferred draft is substituted with an honest fallback (never suppressed, never delivered verbatim); audits with `source="terminal_flush"` |
| `tools/send_message.py` (telegram or email) | **Gated** |
| `tools/valor_telegram.py send` | **Gated** |
| `tools/valor_email.py cmd_send` | **Gated** |

The gate is implemented in [`bridge/promise_gate.py`](../../bridge/promise_gate.py).
Each CLI tool calls `cli_check_or_exit(text, transport, session_id)`
immediately before its Redis `rpush`. The drafter calls `_evaluate_drafter_promise`
(a shared helper in `bridge/message_drafter.py` that runs
`bridge.promise_gate._evaluate_promise_heuristic` on the exact text about to
ship — the verbatim `raw_response` on the short path, the narration-stripped
text on the full path — honors `PROMISE_GATE_ENABLED`, and writes a
`source="promise_gate_drafter"` audit entry) as part of the pass-through
validation flow — no Haiku call, no double-charge.
`evaluate_promise` still accepts an optional `classifier_verdict` parameter
(kept for backward compatibility) but the drafter does not populate it.

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
original text rather than swallowing the reply.

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

Measured directly against the live LLM layer. The discriminator is **the
presence of a forward-looking clause, not the presence of evidence.**
Adding a file count, a commit hash, or a bare `#102` does not rescue "still
running", "is on it", or "I'll report back"; only a URL-shaped
autonomous-delivery reference does.

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
  true, with no forward clause, or (b) cite a full PR URL. Callers that
  legitimately need to defer record the promise on the bound Job
  instead (see the advisory flow above). `.claude/commands/roles/prime-pm-role.md`
  teaches this to the PM.
- **The two layers genuinely disagree, and that is by design.**
  "Still working on this." is blocked 8/8 by the LLM but *allowed* by
  the heuristic, because no regex covers it. The heuristic is a narrow
  fail-closed backstop for the phrases it does match, never a
  reimplementation of the LLM's judgment. Do not write a test that
  asserts LLM-equivalent behavior from `_evaluate_promise_heuristic`,
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
  "source": "promise_gate_llm"
}
```

The `source` discriminator takes one of:

| Source | When |
|--------|------|
| `promise_gate_llm` | LLM Haiku call returned a parseable verdict |
| `promise_gate_heuristic` | LLM unavailable / parse failure → fell through to regex |
| `promise_gate_timeout` | LLM SDK 3-second timeout fired |
| `promise_gate_disabled` | Kill switch was on |
| `promise_gate_drafter_delegation` | Verdict derived from a pre-computed `classifier_verdict` (backward-compat path; the drafter does not populate this) |
| `promise_gate_drafter` | Drafter-path decision (`_evaluate_drafter_promise`, both length regimes); on the kill-switch it records `action="allow" / reason="gate_disabled"` under this same source |
| `terminal_flush` | Terminal-flush decision (`_gate_terminal_promise` in `agent/session_health.py`); a block means the honest fallback was substituted |
| `promise_gate_cli_exception` | `cli_check_or_exit` swallowed an unexpected raise (fail-open) |

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
pattern: `async with semaphore_slot(): async with
anthropic.AsyncAnthropic(timeout=RTR_SDK_TIMEOUT) as client:`.
Coroutine-level timeouts (`asyncio.wait_for`) are forbidden — they
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
  kill-switch contract.
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
