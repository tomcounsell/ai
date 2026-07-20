# Config Timeout Catalog

Centralized, `.env`-overridable catalog for timing/timeout/TTL knobs (issue #1968).
Before this change, ~350 inline `timeout=`/`ex=` literals were scattered across
subprocess calls, HTTP clients, SMTP, Redis, and Popoto TTLs. The same semantic
knob drifted to different numbers in different files (a "1h lock" TTL re-spelled
as `3600` in three places; a subprocess timeout copy-pasted as `5`, `10`, and
`30` with no shared constant) and nothing was tunable without a code edit.

## `TimeoutSettings` (env prefix `TIMEOUTS__`)

One general system-timing config group in `config/settings.py` — not a rigid
taxonomy of exclusive sub-categories. Every field is bounded (`ge`/`le`) so an
invalid `.env` override raises `ValidationError` at import instead of silently
misbehaving, and carries an inline docstring naming its call sites and why its
default was chosen.

| Field | Default | Bounds | Env override | Covers |
|-------|---------|--------|---------------|--------|
| `git_subprocess_s` | 60.0s | 1–300s | `TIMEOUTS__GIT_SUBPROCESS_S` | git/gh CLI subprocess calls (rev-parse, status, add, commit, push, worktree, revert, `gh issue create`, etc.) across `agent/branch_manager.py`, `agent/worktree_manager.py`, `agent/session_logs.py`, `agent/completion.py`, `agent/session_revival.py`, `monitoring/*_watchdog.py`, `monitoring/crash_tracker.py`, `reflections/docs_auditor.py`. `gh` CLI calls are folded into this same field rather than a separate `gh_cli_s` — identical single request/response subprocess usage. |
| `subprocess_default_s` | 300.0s | 1–1800s | `TIMEOUTS__SUBPROCESS_DEFAULT_S` | Generic/other subprocess calls that are NOT git/gh-specific (grep, pgrep, `launchctl kickstart`, `ruff check`/`ruff format --check`, `pytest tests/unit/`, etc.). |
| `http_request_s` | 30.0s | 1–300s | `TIMEOUTS__HTTP_REQUEST_S` | General-purpose HTTP client calls (`requests.get`/`.post`/`.put`) that are not the Anthropic SDK, e.g. `reflections/sentry_triage.py`'s Sentry API calls. |
| `smtp_s` | 30.0s | 1–120s | `TIMEOUTS__SMTP_S` | `smtplib.SMTP(host, port, timeout=...)` connections in `bridge/email_relay.py`, `bridge/email_dead_letter.py`, `bridge/email_bridge.py`. |
| `redis_socket_s` | 5.0s | 1–60s | `TIMEOUTS__REDIS_SOCKET_S` | Redis client `socket_timeout`/`socket_connect_timeout` on short-lived request-response connections (`config/redis_bootstrap.py`, `agent/agent_session_queue.py`'s probe connection). Does NOT apply to the dedicated `socket_timeout=None` long-lived pub/sub `listen()` connection, which is intentionally unbounded. |
| `anthropic_sdk_s` | 30.0s | 1–300s | `TIMEOUTS__ANTHROPIC_SDK_S` | Inner SDK-level timeout for the Anthropic API call (issue #1925 double-timeout pattern). Paired with `anthropic_hard_s`. |
| `anthropic_hard_s` | 35.0s | 1–300s | `TIMEOUTS__ANTHROPIC_HARD_S` | Outer `asyncio.wait_for(...)` hard cap around the whole Anthropic call. Fires even when the inner SDK timer never gets a socket event (e.g. a half-open TCP connection). |
| `agent_session_retain_ttl_s` | 2592000s (30d) | 1–2592000s | `TIMEOUTS__AGENT_SESSION_RETAIN_TTL_S` | `models/agent_session.py`'s `retain_for_resume` BUILD-session backstop (`Meta.ttl`). |
| `last_processed_ttl_s` | 2592000s (30d) | 1–2592000s | `TIMEOUTS__LAST_PROCESSED_TTL_S` | `models/last_processed.py`'s per-chat read cursor (`Meta.ttl`). |

### The double-timeout pattern (`anthropic_sdk_s` / `anthropic_hard_s`)

`agent/llm/wrapper.py` and `agent/memory_extraction.py` use a deliberate
two-timer pattern (inner SDK-level `timeout` + outer `asyncio.wait_for` hard
cap, hotfix #1055). Both timers are promoted as a paired field set and the
two-timer structure is preserved — they are never collapsed into one value.
Letting the SDK/httpx layer raise its own typed timeout error first (before
the outer hard cap fires) produces cleaner logs; the outer cap exists to
guard against a half-open connection that never trips the inner timer.

### Session-lifecycle TTLs may be month-scale

`AgentSession` and session-used Popoto objects may run up to 30 days
(`2592000s`) on current main — longer than the short dedup/claim-lock TTLs
elsewhere in the system (see the `bridge_msg_claim_ttl_seconds` "GRAIN OF
SALT" comment on `FeatureSettings`: a long TTL on a *claim lock* is a defect
because it orphans a claim key on mid-window process death, but a session
record living 30 days is fine). Both TTL fields default to their site's
current value, so wiring them in was a value-source change with zero
behavior change.

## Runtime-dependent caps: large-but-finite, never removed

For a subprocess/HTTP call whose correct timeout genuinely depends on how
the process is running (workload, machine, interactive vs. headless), the
migration maps it to a large-but-finite `settings.timeouts.*` ceiling
(week-scale is sanctioned) rather than an invented short number. The cap is
never dropped: the worker is the sole serial session-execution engine, so an
uncapped `subprocess.run`/`Popen` (git/gh credential prompt,
`.git/index.lock` contention, a stalled fetch/push) would wedge the worker
indefinitely. A grep gate (`git grep -nP 'subprocess\.run\((?![^)]*timeout=)'`
over the migrated dirs) enforces that no worker-critical subprocess call
loses its `timeout=`. This gate is single-line/`-P`-only — it does not catch
`Popen(`, multi-line call openings, or an explicit `timeout=None`; those
sites are eyeballed manually during migration validation. A durable AST-based
check is future work, not covered by this gate.

## Non-session TTLs: named constants, not settings fields

Short dedup/lock TTLs (`ex=3600` "1h lock", `ex=120` "2-min dedup") stay
named module-level constants rather than promoted `settings` fields, reused
at every site that shares them:

- `_INTERRUPTED_SENT_DEDUP_TTL_SECONDS` (120s) — `agent/messenger.py`,
  `agent/session_completion.py`, `agent/session_health.py`
- `HOUR_DEDUP_LOCK_TTL_SECONDS` (3600s) — `agent/session_health.py`
- `_CIRCUIT_FLAG_TTL_SECONDS` (3600s) — `agent/circuit_health_gate.py`
- `_OUTBOX_TTL` — `agent/session_completion.py`, `pm_briefings/__init__.py`,
  `pm_briefings/delivery.py` (mirrors the existing `OUTBOX_TTL` convention in
  `agent/output_handler.py`)

- `ISSUE_LOCK_TTL_SECONDS` (default **1800s**, env-overridable via the
  `ISSUE_LOCK_TTL_SECONDS` env var, not `TIMEOUTS__*`) —
  `models/session_lifecycle.py`. The per-issue SDLC lease TTL, raised from
  300s by #2026 (WS1): a blocked `claude -p` supervisor makes zero `sdlc-tool`
  writes mid-stage, so the default is sized above the observed p99 stage wall
  time (6–25 min) instead of relying on a renewal heartbeat that has no
  executor. **Provisional/tunable.** The TTL is only the crash backstop — the
  supervisor explicitly releases the lease (`release_issue_lock`,
  compare-and-delete in `finalize_session`) on run completion and graceful
  failure, so the happy path frees immediately; a genuinely dead owner is
  reclaimed within ≤ TTL by the `orphaned_lock` self-heal.

`bridge/dedup.py`'s `_LAST_EVENT_TTL_SECONDS` (2592000s) is a freeform
observability TTL, not a Popoto model TTL, and is left as its existing named
constant at builder discretion.

Two fast-fail single-timer Anthropic SDK calls — `bridge/read_the_room.py`'s
`RTR_SDK_TIMEOUT` and `agent/session_completion.py`'s
`_COMPLETION_NOVELTY_JUDGE_TIMEOUT_S` — are deliberately left as local
constants rather than `anthropic_sdk_s`. Both are load-bearing real-time UX
gates structurally akin to a watchdog fast-fail cap, not part of the #1925
backend double-timeout pair.

## Per-tool wedge tiers vs. a Bash call's declared timeout (issue #2145)

The worker's per-tool wedge tiers (`TOOL_TIMEOUT_INTERNAL_SEC`=30,
`TOOL_TIMEOUT_MCP_SEC`=120, `TOOL_TIMEOUT_DEFAULT_SEC`=300 — raw-env knobs in
`agent/session_health.py`, predating this catalog) interact with the Bash
tool's own `timeout` parameter (milliseconds, harness cap 600000):

- The PreToolUse hooks persist the declared value (seconds) as
  `AgentSession.current_tool_timeout_s`, riding the same save as
  `current_tool_name`/`last_tool_use_at`.
- `_check_tool_timeout` uses
  `max(tier_budget, min(declared, TOOL_TIMEOUT_DECLARED_MAX_SEC) + TOOL_TIMEOUT_DECLARED_GRACE_SEC)`
  as the effective wedge budget. A call inside its own declared budget is
  never wedge-killed at the flat tier default; the cap
  (`TOOL_TIMEOUT_DECLARED_MAX_SEC`, default 600) guarantees an absurd
  declared value cannot disable wedge detection, and the grace
  (`TOOL_TIMEOUT_DECLARED_GRACE_SEC`, default 60) covers PostToolUse hook
  latency.
- All four `TOOL_TIMEOUT_*` knobs follow the raw-env convention (not
  `TIMEOUTS__*`); promoting them into `TimeoutSettings` remains a separate
  cleanup per the criterion below.

## Update-restart drain knobs (issue #2141)

Raw-env knobs (consumed by `scripts/remote-update.sh` — a bash consumer, so
`TIMEOUTS__*`/`TimeoutSettings` does not apply — and by
`scripts/update/drain.py` / `worker/shutdown_cleanup.py` as their defaults):

| Knob | Default | Used by |
|------|---------|---------|
| `UPDATE_WORKER_DRAIN_TIMEOUT_S` | 300 | Total window the update flow waits for running sessions to drain before DEFERRING the worker restart to the next cycle. Consumed by `remote-update.sh` AND `scripts/update/service.py::install_worker` (#2161). |
| `UPDATE_WORKER_DRAIN_POLL_S` | 10 | Poll interval of the drain probe. |
| `WORKER_SHUTDOWN_GRACE_S` | 3 | Worker SIGTERM shutdown: bounded active-task wait before abandoning in-flight turns and terminating `claude -p` harness children (`worker/shutdown_cleanup.py`). Sized to launchd's real kill grace. |

See `docs/features/bridge-worker-architecture.md` § "Update restart
semantics for in-flight sessions" for the full decision flow.

## Promote-vs-name-locally criterion

Promote a literal to a `settings.timeouts.*` field if it is:
- duplicated across two or more modules,
- plausibly tuned per-machine, or
- a session-lifecycle TTL.

Otherwise (a logic-coupled short one-off, e.g. a `time.sleep(0.1)` poll
interval local to one function that no one would ever tune) leave it as a
named local constant. The goal is eliminating *duplicated/undiscoverable*
knobs, not achieving zero integer literals.

## Normalization

Where multiple call sites disagreed on a value for the same semantic knob
(e.g. subprocess timeouts spread across `5`/`10`/`30`), the migrated field
defaults to the **longest** pre-existing literal in that category (Decision
#1). A longer timeout only delays failure detection on the hang path — it
never breaks a call that used to succeed with a shorter one. This is a
deliberate, documented normalization, not a "no behavior change" migration;
re-tuning any value beyond this one-time normalization is explicitly out of
scope for this change (see the plan's No-Gos) and left to a follow-up where
a human justifies each adjustment.

## Regression guard

`.claude/hooks/validators/validate_no_inline_timeout.py` flags new inline
`timeout=<int>` literals in `subprocess`/`requests` calls at commit time, so
the cleanup does not silently grow back. `tests/unit/test_validate_no_inline_timeout.py`
proves it fires on a violating fixture and passes on a compliant one. The
guard provides an allowlist mechanism for genuinely local one-offs that
don't belong in `settings`.

## Catalog audit

Alongside the literal migration, `config/settings.py` had its own
accumulated cruft resolved:

- Zero-usage fields (including `secret_key`) were deleted after confirming
  no reflective access (`getattr(settings`, `model_dump`, `.dict(`) and no
  direct env-key read (`os.environ.get(...)`/`os.getenv(...)`) anywhere in
  the codebase.
- The duplicated `data_dir` definition was resolved in favor of a single
  surviving owner, `PathSettings.data_dir` (the value derived in
  `model_post_init`); `create_directories` was rewired to read the survivor.
- `ServerSettings.port`'s stale default was fixed/removed to match reality.
- `.env.example` was regenerated to document every `TIMEOUTS__*` override
  key with a comment above each `KEY=` line, per the existing completeness
  check convention.

## Adding a new timing/TTL knob

1. Decide promote-vs-name-locally using the criterion above.
2. If promoting: add a field to `TimeoutSettings` (or a new group, if it
   doesn't fit the general timing bucket) with `ge`/`le` bounds and a
   description naming its call sites, matching the existing commenting
   style.
3. Add the corresponding `TIMEOUTS__<FIELD>` key to `.env.example` with a
   comment above it.
4. If a worker/bridge service reads the key at runtime under
   `VALOR_LAUNCHD=1`, add it to the plist env-injection path in
   `scripts/update/` — launchd-managed processes skip the `.env` file read
   and rely on pre-injected vars.
