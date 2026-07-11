# HarnessAdapter Seam

**Status:** Shipped (Tasks 2.2-2.3 of issue #2000, Phase 2 of `harness-cross-compat.md` / #1996)

## Overview

Before this seam, the headless session runner (`agent/session_runner/`)
drove `claude -p` via a bare async function
(`get_response_via_harness`) welded into `agent/sdk_client.py`: argv
assembly, env construction, and stream-json parsing all lived inline, with
no seam to swap the subprocess or normalize its output.
`agent.session_runner.harness` is the new package behind which all
claude-specific knowledge lives; the runner and role driver consume only
`TurnRequest` / `TurnResult` / `TurnEvent` — never claude-specific argv or
stream-json shapes directly.

This same PR also deleted the fully-dead Claude Agent SDK path
(`ValorAgent`, `get_agent_response_sdk`, the `_active_clients` registry,
and `worker/idle_sweeper.py`) that had been co-resident with the live
harness path, kept alive only by a top-level dependency and a scatter of
test scaffolding. That deletion completes the **harness half** of #1925
(remove `claude_code_sdk`); #1925 now shrinks to the PydanticAI /
non-harness-LLM-call lane.

This PR also lands Task 2.3: PM turn routing driven by `--json-schema`
structured output, with the prefix-regex classifier demoted to a
telemetered fallback, and the `file_paths` (#1802) delivery slot — see
"Schema Routing" below.

## Module Map (`agent/session_runner/harness/`)

| Module | Role |
|--------|------|
| `base.py` | The `HarnessAdapter` protocol plus the normalized `TurnRequest` / `TurnResult` / `TurnEvent` dataclasses. |
| `claude.py` | `ClaudeHarnessAdapter` — the (today, only) concrete adapter for the `claude -p` CLI. Owns argv/env assembly, stream-json parsing, the stale-UUID and image-dimension retry fallbacks, and turn-input/health helpers, extracted byte-identically from the pre-extraction `agent/sdk_client.py` free functions. |
| `events.py` | The fixed normalized `TurnEvent` type vocabulary, aligned with codex's `ThreadEvent` naming (`session.started`, `turn.spawned`, `item.stdout`, `turn.exited`, `turn.completed`). Deliberately minimal — see Rabbit Holes below. |

`agent/sdk_client.py` re-exports the harness module's public names for its
remaining (non-runner) callers, so the extraction is behavior-preserving;
new call sites should import directly from `agent.session_runner.harness`.

## `TurnResult` (delivers program item T2.4)

```python
@dataclass
class TurnResult:
    resume_handle: str | None = None
    final_text: str = ""
    structured_output: dict[str, Any] | None = None
    events: list[TurnEvent] = field(default_factory=list)
    usage: dict[str, Any] | None = None
    cost_usd: float | None = None
    returncode: int | None = None
    result_event_fired: bool | None = None
    exit_reason: ExitReason | None = None
```

`exit_reason` reuses #2004's `ExitReason` StrEnum — there is no parallel
taxonomy. `structured_output` carries the claude CLI's schema-validated
`StructuredOutput` tool-call result (see "Schema Routing" below), present
only when a schema was requested and the CLI's own validation succeeded.

## Normalized `TurnEvent`s

`HarnessAdapter.run_turn(request, *, on_event=...)` fires `on_event`
synchronously, in-line with the subprocess's own output, so the caller can
persist the resume handle the instant it is known:

| Event | When | Payload |
|-------|------|---------|
| `session.started` | The harness reports a new (or reused) session id — claude's `system/init` event. MUST be the first handle-bearing event per turn (Race 1). | `{"handle": str \| None, "raw": dict}` |
| `turn.spawned` | The subprocess pid is known. | `{"pid": int}` |
| `item.stdout` | Once per non-empty stdout line (liveness heartbeat). | none |
| `turn.exited` | The subprocess has exited (before result parsing/retries resolve). | none |
| `turn.completed` | Exactly once, at the end of `run_turn()` — the turn's usage/cost/exit-shape summary. | `{"usage": dict \| None, "cost_usd": float \| None, "returncode": int \| None, "result_event_fired": bool \| None}` |

Only what the runner consumes for liveness/telemetry is modeled — every
claude stream-json event type is deliberately NOT normalized here (see
plan #2000 Rabbit Holes, "Building a universal event superset").

## Resume-Handle Contract (Race 1)

The adapter MUST emit `session.started{handle}` as its first event; the
runner persists the resume handle on receipt — preserving the
persist-at-init contract through the seam (crash auto-resume, #1917).

## Resume-Id Stability (Task 2.1 empirical finding)

A live two-turn probe against claude 2.1.207 (`docs/plans/harness-adapter-seam.md`
Spike Results) confirmed that plain `--resume` **reuses** the session id
rather than forking it (`--fork-session` is the only path that forks).
`HeadlessRoleDriver._handle_init` (`role_driver.py`) applies this finding
to exactly one of its two independent responsibilities:

1. **`self._transcript_path`** is retargeted **unconditionally** on every
   init event — a preempted/killed turn's *partial* transcript must be
   the resume target, never the stale pre-turn uuid. Untouched by the
   resume-id finding.
2. **`self._claude_session_id`** is still adopted from the observed id
   every turn, but a mismatch against the previously-expected id is now
   **assert-and-alarm**: an error-level log (auto-captured to Sentry via
   `LoggingIntegration`) keyed to the session's persisted `claude_version`,
   rather than silently forking machinery built for behavior the CLI no
   longer exhibits.

## Dead SDK Path Deletion

Removed wholesale, no legacy tolerance:

- `ValorAgent` class and `get_agent_response_sdk` (the persistent
  `ClaudeSDKClient` substrate — the only production instantiator of
  `ValorAgent` was itself dead, with no live caller).
- The `_active_clients` module-level registry and its accessors
  (`get_active_client`, `get_all_active_sessions`).
- `worker/idle_sweeper.py` and its `worker/__main__.py` supervision
  wiring — the sweeper existed to tear down persistent SDK clients before
  a ~48h idle death; the `claude -p` subprocess-per-turn harness has no
  such client, so it was obsolete once the SDK path went.
- The `claude_agent_sdk` import from `agent/sdk_client.py` (confirmed
  zero references remain there; `agent/sdk_client.py` shrank from 3,999
  to ~1,560 lines).
- `agent/health_check.py::_handle_steering`'s dead `if client:` SDK arm —
  **the `else` Redis re-push body was kept** as the unconditional
  steering-delivery path (see below).
- `agent/__init__.py` exports shrunk accordingly.

### `claude-agent-sdk` dependency: kept, not dropped

A build-time scope correction to the original plan: `claude_agent_sdk`
(the installed package) has genuine live consumers **outside** the deleted
path — `agent/health_check.py` and `agent/hooks/*.py` import SDK
hook-config types (`HookContext`, `HookMatcher`, `AgentDefinition`,
`PostToolUseHookInput`, etc.) unrelated to the persistent-client
substrate. The `claude-agent-sdk==0.2.116` dependency in `pyproject.toml`
**stays**; only the import inside `agent/sdk_client.py` was removed.

### Steering fallback survives (critique blocker, addressed)

`agent/health_check.py::_handle_steering` is the **sole**
steering-injection/delivery path for every CLI-harness (production)
session — not a liveness check. Deleting the whole function (as an
under-specified read of "delete the dead SDK path" might suggest) would
have removed live production steering. Only the dead `if client:` arm
(the `get_active_client(session_id)` call and its now-removed import) was
pruned; the `else` body — re-pushing every non-abort message to the Redis
steering list (`agent.steering`) for the worker's turn-boundary drain — is
now the function's unconditional body. Regression-tested by
`tests/integration/test_steering.py::TestWatchdogSteering::test_watchdog_repushes_message_to_redis_list`.

## Schema Routing (Task 2.3)

**The PM turn schema** (`agent/session_runner/router.py::PM_TURN_JSON_SCHEMA`):

```python
{
    "type": "object",
    "properties": {
        "route": {"type": "string", "enum": ["user", "complete", "continue"]},
        "message": {"type": "string"},
        "file_paths": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["route", "message"],
}
```

Every top-level role turn requests this schema via `TurnRequest.json_schema`,
which the adapter passes through to `--json-schema`. Per Task 2.1's empirical
finding, the CLI injects a synthetic `StructuredOutput` tool; on success the
validated object lands on the terminal `result` event's `structured_output`
key, which the adapter surfaces on `TurnResult.structured_output` via a new
`on_structured_output` callback (mirroring the existing `on_usage` pattern).

**Routing precedence** (`SessionRunner._classify_turn`, `runner.py`):

1. `router.validate_structured_route(outcome.structured_output)` — builds a
   `ClassificationResult` directly from the schema-validated object. No text
   parsing.
2. **Fallback**: `router.classify_pm_prefix(text)` — the pre-schema
   `[/user]`/`[/complete]` prefix-regex parse, consulted only when
   `structured_output` is absent or fails validation (the CLI's own
   schema-validation-failure signal — see Task 2.1's Spike Results). Emits
   `schema_routing_fallback` session-event telemetry plus paired analytics
   counters (`session_runner.pm_turn_routed`, `.schema_routing_fallback`).
3. **Compliance nudge** — the existing final backstop, unchanged.

`.claude/commands/roles/prime-pm-role.md` and `prime-teammate-role.md` teach
the schema contract (`route: user|complete|continue`, `file_paths`) — the
`[/user]`/`[/complete]`/`[/dev]` prefix-token teaching is fully retired from
both prime docs. `router.py`'s regex classifier (`classify_pm_prefix`,
`PREFIX_TOKEN_RE`) stays in the codebase as the runtime fallback; only the
*teaching* of it to the PM/Teammate personas is gone.

**Fallback-rate alert** (`monitoring/schema_routing_alert.py`):
`check_schema_routing_fallback_rate()` queries the
`session_runner.pm_turn_routed` / `.schema_routing_fallback` analytics
counters over a rolling 1h window and returns a WARNING `Alert` when the
fallback rate exceeds 5% (a healthy schema path is ~0%). Wired into
`monitoring/alerts.py`'s `AlertManager.check_all()` alongside
`ResourceMonitor.check_thresholds()`.

**`file_paths` delivery (#1802):** `adapter.py`'s `on_user_payload` /
`on_complete_payload` / `_deliver_sync` / `_enqueue_to_outbox` carry an
optional `file_paths` list end-to-end onto the real `send_cb` delivery call,
gated by a capability probe (`_send_cb_accepts_file_paths`) so handlers
without a `file_paths` parameter (e.g. `EmailOutputHandler.send`) are never
broken by an unexpected kwarg. Test coverage
(`tests/unit/session_runner/test_schema_routing.py`) asserts `file_paths`
reaches the **real delivery call** — not just that the router parsed the
slot — including the outbox-fallback-on-delivery-failure path.

## See Also

- [Headless Session Runner](headless-session-runner.md) — the turn loop and role driver that consume this seam
- [Session Lifecycle](session-lifecycle.md) — the resume-handle field generalization
- [Harness Abstraction](harness-abstraction.md) — the original harness/PTY split this seam builds on
- `docs/plans/harness-adapter-seam.md` — the plan (Task 2.1 probes, Task 2.2/2.3 split decision)
