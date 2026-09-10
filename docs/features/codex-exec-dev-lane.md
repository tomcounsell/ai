# Codex Exec Dev Lane

**Status:** Shipped (issue #2001, Phase 3 of `harness-cross-compat.md`)

An opt-in executor for developer work inside eng sessions. The top-level PM
stays Claude on every session type; on a flagged eng session the PM delegates
implementation turns to an external `codex exec` thread through a
session-scoped MCP tool instead of the in-turn `dev` subagent.

## Selection

`valor-session create --role eng --dev-harness codex` is the only selection
path. Creation validates `session_type=eng` and `dev_harness=codex` before any
side effect (unknown values and non-eng roles refuse with zero queue,
worktree, or resolver effects). The flag is immutable after work begins: the
executor preflights the persisted capability every turn, and
`exec_harness` stays `claude` on every flagged row.

The one supported direction change is the operator one-way downgrade
(`valor-session update-dev-harness` from codex to claude). It clears the flag,
preserves the Codex thread id for forensics, and re-primes the PM for
`Agent(dev)`. A busy lane (held Redis lease) refuses the downgrade with the
flag intact.

## PM Tool Flow

Each flagged eng turn generates a session-local MCP config carrying one stdio
server (`mcp_servers/codex_dev_server.py`, `codex_dev`) plus
`AGENT_SESSION_ID` in its environment. The server is never registered
globally, and unflagged sessions never see the tool. The PM is primed with
`prime-pm-codex-role`, which teaches `codex_dev_run` instead of the `dev`
subagent.

`codex_dev_run(instruction)` runs one Codex turn and returns the developer
report plus a PM-visible attribution line:

```
[dev harness=codex model=0.154.0 turns=2 usage={'input_tokens': 10, ...}]
```

Every call re-reads the persisted session row and refuses unless it is still
an eng session flagged `dev_harness=codex`. Empty instructions, a held lane
lease, an exhausted resume bound, and preflight failures all return typed
`{ok: False, error}` dicts, never protocol errors or tracebacks.

The completion signal comes from the adapter's `--output-schema` final
message (`{"report": str, "complete": bool}`, both required), delivered over
the existing StructuredOutput path.

## Persistence

Four nullable fields on `AgentSession` (all default None, no index rebuild):

| Field | Meaning |
|-------|---------|
| `dev_harness` | Creation-time capability (`codex` or None), immutable |
| `codex_thread_id` | UUID persisted synchronously from the first `session.started` event |
| `codex_version` | CLI version recorded at first persist |
| `codex_turn_count` | Resumed-turn counter, incremented only after a live spawn |
| `dev_lane_fence` | Monotonic fence token; a mismatched token discards the turn result |

Write-or-kill: the `session.started` callback persists thread id, version,
and count before the turn result is parsed. A failed save kills the full
Codex process tree (`kill_codex_tree`) so no orphan thread survives without
a persisted handle. Spawn failures re-save the unchanged count under the
same lease, so failed spawns never burn a resumed turn.

Thread, version, and count survive queue recreation (clone carries the
dev-lane fields; continuation preserves the dev lane while resetting only
the execution-fence fields) and worker restart (resume reuses the persisted
thread id, so create, first turn, resumed turn, steer, preempt, restart, and
completion all share one thread).

A registered idempotent migration confirms the nullable fields read cleanly
on pre-existing rows, using the ORM only and touching no indexes.

## Steering and Concurrency

The PM steers by issuing the next instruction; preemption arrives as a new
call. One Redis dev-lane lease (SET NX EX, TTL 900s, Lua compare-and-delete
release) serializes turns per session: a second concurrent call gets a typed
busy error, never a parallel resume against the same thread. After the turn,
the fence token is re-checked; a lane recreated mid-turn discards the result
with a retryable error instead of resuming a superseded thread.

## Limits

| Setting (`CODEX__*`) | Default | Meaning |
|---|---|---|
| `CODEX__MAX_RESUMED_TURNS` | 10 | Resume bound; exhaustion keeps the thread and errors actionably, no silent rollover |
| `CODEX__TURN_TIMEOUT_S` | 600 | Per-turn wall clock; timeout kills the tree, thread preserved |
| `CODEX__SANDBOX` | `workspace-write` | `danger-full-access` requires an explicit machine setting and never comes from the create flag |
| `CODEX__INSTALL_ENABLED` | false | Opt-in `/update` provisioning of `@openai/codex` at/above floor `0.144.3` |

Approval policy is `never` on first and resumed turns (CLI globals precede
`exec`, so they apply to `exec resume` too). Ephemeral threading is never
requested and the full-auto approval mode is never enabled. The prompt
travels on stdin, never as an argv element; argv is always a list, never a
shell string. The child environment is an explicit allowlist plus
single-use `CODEX_API_KEY`; saved `codex login` is preferred and auth files
are never read.

## Telemetry

One `codex_dev_turn` event per executed turn (plus guard and failure
outcomes) carries harness, model version, turn count, outcome, usage totals,
and wall-clock milliseconds. Usage totals only: never prompts, credentials,
or stderr. The per-session Task 3 JSONL lane file
(`agent/codex_turn_log.py`, exactly `thread_id`, `turn_count`, `outcome`,
`usage`, `wall_clock_ms`) is the schema of record; a write failure returns
`degraded`, logs `codex_turn_log_failed`, and surfaces in the PM report
rather than silently losing evidence. Pre-telemetry probe turns are
backfilled from the lane file with `backfilled: True`.

## Provisioning

`scripts/update/codex_cli.py` installs or upgrades `@openai/codex` only on
machines that set `CODEX__INSTALL_ENABLED=1`, wired as Step 3.95 of the
update run. It never raises: every failure degrades to a `failed` result
logged as a warning. Disabled machines report the installed version and
change nothing.

## Rollback

See [Harness Cross-Compatibility](../infra/harness-cross-compat.md), Rollback
Plan: stop creating flagged sessions, let live ones finish (handles are not
portable to Claude), revert the conditional MCP/prime wiring, leave the
nullable fields and migration records in place.

## Key Files

| File | Purpose |
|------|---------|
| `agent/session_runner/harness/codex.py` | `CodexHarnessAdapter`, preflight, spawn-env allowlist, stderr scrubbing, tree reaper |
| `mcp_servers/codex_dev_server.py` | Session-scoped `codex_dev_run` tool: gating, lease, persistence, accounting, evidence |
| `agent/codex_dev_lease.py` | Redis SET NX EX dev-lane lease with fencing-safe release |
| `agent/codex_turn_log.py` | Task 3 JSONL evidence schema of record |
| `agent/codex_dev_config.py` | Conditional MCP config + typed settings validation |
| `agent/session_telemetry.py` | `record_codex_dev_turn`, `backfill_codex_lane` |
| `scripts/update/codex_cli.py` | Opt-in CLI provisioning |
| `tools/valor_session.py` | `--dev-harness` create path, `update-dev-harness` downgrade |

## See Also

- [HarnessAdapter Seam](harness-adapter.md) — the protocol both adapters implement
- [Headless Session Runner](headless-session-runner.md) — turn loop and conditional lane wiring
- [Eng Session Architecture](eng-session-architecture.md) — where the dev lane fits the session model
- [Harness Cross-Compatibility](../infra/harness-cross-compat.md) — auth, sandbox, update, and rollback rules
