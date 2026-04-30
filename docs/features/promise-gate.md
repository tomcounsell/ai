# Promise Gate

**Issue:** [#1219](https://github.com/tomcounsell/ai/issues/1219)
**Plan:** [docs/plans/sdlc-1219.md](../plans/sdlc-1219.md)
**Status:** Active

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

## Where it sits

The gate runs at every send-path call site that writes to the Redis
outbox:

| Send path | Gate state before #1219 | Gate state after #1219 |
|---|---|---|
| Worker drafted (nudge loop, `bridge/message_drafter.classify_output`) | Gated (LLM + heuristic) | Gated (drafter delegation, no double-charge) |
| `tools/send_telegram.py` | Bypassed | **Gated** |
| `tools/send_message.py` (telegram or email) | Bypassed | **Gated** |
| `tools/valor_telegram.py send` | Bypassed | **Gated** |
| `tools/valor_email.py cmd_send` | Bypassed | **Gated** |

The gate is implemented in [`bridge/promise_gate.py`](../../bridge/promise_gate.py).
Each CLI tool calls `cli_check_or_exit(text, transport, session_id)`
immediately before its Redis `rpush`. The drafter's `classify_output`
calls `evaluate_promise(text, transport="drafter", classifier_verdict=result)`
to delegate the existing classification result without paying a second
Haiku call.

## Architectural posture

### LLM-first, regex fail-closed-only

The primary judgment layer is a Haiku call with a strengthened
few-shot prompt that names a *forward-deferral* class. A regex
backstop is the **fail-closed-only** last line that fires solely on
the heuristic-fallback branch (no API key / SDK exception / parse
failure). The heuristic does NOT override an LLM `ALLOW`.

This split honors both the issue's stated mandate (*"widen what the
LLM drafter judges and keep heuristics as the unambiguous fail-closed
last line"*) and the user-memory record `feedback_llm_drafter_over_regex`
(*"strengthen the LLM classifier prompt before adding heuristic regex
patterns to anti-pattern gates"*).

### Why the heuristic is fail-closed (vs. RTR's fail-open)

`bridge/read_the_room.py` is heavily cited as the architectural
precedent for this gate's async/sync contract, env-var enable
pattern, and SDK timeout. But RTR is **fail-open** (any error returns
`action="send"`), and this gate's heuristic branch is **fail-closed**
(regex match without evidence returns BLOCK). The two postures look
contradictory; they are not — they are intentionally inverted because
the cost of false-positive is inverted between the two gates.

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
in the template would defeat the gate on the first BLOCK. This was
the cycle-2 Blocker B-NEW-2 finding that retired the cycle-1 design's
`--no-promise-gate` per-call flag entirely. There is no per-call
bypass — operators rephrase blocked messages just like the agent
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
block rate). Adding a per-message bypass was rejected on review — see
"Why the template never names the kill switch" above.

## Telemetry — two channels with documented asymmetry

### Audit JSONL (universal)

Every gate call writes one entry to `logs/classification_audit.jsonl`
via the `_write_promise_audit` helper. The entry shape:

```json
{
  "ts": "2026-04-30T12:00:00+00:00",
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
| `promise_gate_drafter_delegation` | Verdict derived from drafter's `ClassificationResult` |
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

This preserves the §Race Conditions stateless-judgment claim: the
gate makes **no** AgentSession state-driven decision; the existence
check on the explicit input is for telemetry routing only.

### `session_id` provenance per CLI

The four CLI paths produce `session_id` values with different
semantics:

| CLI | session_id source | session_events emission |
|-----|-------------------|-------------------------|
| `tools/send_telegram.py` | reads real `VALOR_SESSION_ID` from worker harness env | fires when invoked from a worker subprocess |
| `tools/send_message.py` | accepts whatever its caller passes (typically real `VALOR_SESSION_ID`) | fires conditional on lookup |
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
leak httpx connections under cancellation (PR #1055 invariant).

`RTR_SDK_TIMEOUT` is **imported** from `bridge.read_the_room` rather
than redefined locally — both gates share the same SDK invariant
from PR #1055; copying the literal value would risk drift.

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

## Tests

* [`tests/unit/test_promise_gate.py`](../../tests/unit/test_promise_gate.py) — main test module. Covers
  empty-input, kill switch, classifier_verdict short-circuit, all five
  forward-deferral phrases (LLM-mocked + heuristic + scheduled-delivery
  override + B2 substantive-content rule), behavioral-change regression,
  recovery template anti-leak, and `cli_check_or_exit`
  exception-swallow semantics.
* [`tests/unit/test_promise_gate_audit.py`](../../tests/unit/test_promise_gate_audit.py) — covers the
  forked `_write_promise_audit` helper (cycle-2 C-NEW-2).
* [`tests/unit/test_promise_gate_session_events.py`](../../tests/unit/test_promise_gate_session_events.py) — covers
  conditional session_events emission with real and synthetic
  session_ids (cycle-2 C-NEW-1, C-NEW-4).
* `tests/unit/test_send_telegram.py`, `test_send_message.py`,
  `test_valor_telegram.py`, `test_valor_email.py` — each adds a
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
`bridge/message_drafter.py::CLASSIFIER_SYSTEM_PROMPT` (drafter path)
and `bridge/promise_gate.py::PROMISE_GATE_SYSTEM_PROMPT` (CLI Haiku
path). If telemetry shows a class of false-positives the LLM cannot
catch from text alone, the prompt is the right knob to turn.
