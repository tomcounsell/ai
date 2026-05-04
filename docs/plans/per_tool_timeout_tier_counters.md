---
status: Planning
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-05-04
tracking: https://github.com/tomcounsell/ai/issues/1270
last_comment_id:
---

# Per-Tool Timeout Enforcement (Per-Tier Counters)

## Problem

A session can call a tool whose PreToolUse hook fires (so `current_tool_name` and `last_tool_use_at` are set) but whose PostToolUse hook never fires because the tool itself is wedged. Because Tier 1 sub-check A in `_has_progress` ([agent/session_health.py:553-558](agent/session_health.py)) treats `last_tool_use_at` within `SDK_PROGRESS_FRESHNESS_WINDOW` (1800s / 30 min) as evidence of progress, the session keeps "passing" the liveness check for up to 30 minutes while making no real progress. Common manifestations:

- A `Bash` shell command that hangs (network curl with no timeout, ssh that never returns).
- An MCP tool whose remote server stalled (Gmail / Calendar / pencil API).
- An internal tool (`Read` / `Glob` / `ToolSearch`) hung on a slow filesystem or a giant fan-out.

Operators get no per-tier visibility today — the existing `<project>:session-health:recoveries:{no_progress|worker_dead}` Redis counter ([session_health.py:863](agent/session_health.py)) tells them the session was killed but not which class of tool wedged.

**Current behavior:**
A session calling a tool that never returns is reprieved for up to 30 min by Tier 1 sub-check A (because `last_tool_use_at` is fresh from the PreToolUse fire). Eventually it falls through to recovery via `worker_dead` or via Tier 2's reprieve-cap escalation, but the operator has no signal that "the tool wedged" vs. "the worker crashed."

**Desired outcome:**
On each liveness tick, sessions whose `current_tool_name` is non-null and whose `last_tool_use_at` exceeds the **tier-specific** budget (30s internal / 2min MCP / 5min default) are detected as tool-wedged. The session is recovered via the existing recovery branch, a per-tier cumulative counter is bumped on the `AgentSession` row, and a project-scoped Redis counter (`{project_key}:session-health:tool_timeouts:{internal|mcp|default}`) is incremented so dashboards can show tier diversity.

## Definitions

| Term | Definition | Reference |
|------|-----------|-----------|
| Tool-wedge timeout | A session with `current_tool_name != None` and `last_tool_use_at` older than the tier budget | New |
| Internal tool | Lightweight built-in tools that should never take >30s. Set: `{ToolSearch, Read, Glob, Grep, Edit, Write, NotebookEdit}` | New constant in `agent/session_health.py` |
| MCP tool | Tool exposed by a Model Context Protocol server. Detected by `mcp__` name prefix | Existing convention (`agent/sdk_client.py`) |
| Default tool | Everything else (e.g. `Bash`, `Task`, `Skill`, `WebFetch`) | New |
| Per-tier counter | Cumulative `IntField` on `AgentSession` recording how often each timeout tier fired | New |
| Project-tier counter | Redis `INCR` keyed `{project_key}:session-health:tool_timeouts:{tier}` | New, mirrors existing `recoveries:{kind}` |

## Freshness Check

**Baseline commit:** `5055b527c9fbe7710d7bb5dbe9a44132565e9fa6`
**Issue filed at:** `2026-05-04T09:19:00Z` (today)
**Disposition:** Unchanged

**File:line references re-verified:**
- `agent/session_health.py:1541` — `_agent_session_health_loop` — confirmed at line 1541.
- `agent/session_health.py:553-558` — Tier 1 sub-check A treats `last_tool_use_at` within `SDK_PROGRESS_FRESHNESS_WINDOW` as progress — confirmed.
- `agent/session_health.py:863` — `<project>:session-health:recoveries:{kind}` Redis counter — confirmed at line 863.
- `agent/session_health.py:145` — `AGENT_SESSION_HEALTH_CHECK_INTERVAL = 300` — confirmed; loop runs every 5 minutes, NOT every 30s as the issue's first paragraph implies. Issue body acknowledges this in Solution Sketch question #7.
- `agent/hooks/liveness_writers.py:81` — `record_tool_boundary` — confirmed at line 81; called from both PreToolUse and PostToolUse hooks.
- `models/agent_session.py:359, 364` — `current_tool_name` and `last_tool_use_at` fields — confirmed (added in issue #1172 Pillar A).

**Cited sibling issues/PRs re-checked:**
- #1226 — closed 2026-05-01 by PR #1243 (per-turn SDK signals promoted to Tier 1) — the very pattern this issue extends.
- #1172 — closed 2026-04-29 by PR #1177 — wall-clock cap retired; `current_tool_name` / `last_tool_use_at` introduced.
- #1036 — closed 2026-04-18 — Tier 1 / Tier 2 detector originally introduced.
- #1099 — referenced for compaction reprieve gate; still relevant pattern.
- #1218 — orphan subprocess SIGKILL escalation; confirmed pattern for cross-tick state.

**Commits on main since issue was filed (touching referenced files):**
- None (issue filed today; baseline commit is HEAD).

**Active plans in `docs/plans/` overlapping this area:** None. Existing plans like `pubsub-notify-listener-socket-timeout.md`, `tools_audit_remediation.md`, `pm-telegram-tool.md` touch unrelated layers.

**Notes:** Issue body recon stands. The cited file:line pointers are accurate. The 30s vs 300s cadence question in Solution Sketch #7 is the central architectural decision this plan must resolve.

## Prior Art

Search: `gh issue list --state closed --search "tool timeout liveness per-tier"` and `gh pr list --state merged --search "tool timeout liveness"`.

- **#1226 / PR #1243** (merged 2026-05-01): "Promote per-turn SDK signals to Tier 1 to detect hung sessions." Introduced `last_tool_use_at` as a Tier 1 freshness input — the exact field this issue must REVERSE-direction-check against (a stale `last_tool_use_at` while `current_tool_name` is non-null = the wedge condition).
- **#1172 / PR #1177** (merged 2026-04-27): "PM session liveness — see progress or stay graceful." Retired wall-clock per-session cap; introduced `current_tool_name` / `last_tool_use_at` as in-flight visibility fields. **This is the Pillar A foundation the new check builds on.**
- **#1218** (closed): Orphan subprocess SIGKILL escalation. Established the **two-tick state across health ticks** pattern (`_pending_sigkill: set[int]` snapshot/clear/drain). The plan reuses this pattern shape — though for tool timeouts a cross-tick set is not strictly required because the wedge is detected by field-comparison alone (the field state IS the cross-tick signal).
- **#1099 Mode 3**: Compaction reprieve gate. Pattern for adding a new gate to the existing loop.
- **#1046**: Promote `last_stdout_at` to Tier 1 — superseded by #1172's evidence-only model. Cited in the issue Recon as a cautionary tale.

No prior art attempted per-tool wall-clock enforcement in this codebase. Fazm's `acp-bridge/src/index.ts` (referenced in issue) is the external pattern source — adapted to liveness-loop polling rather than `setTimeout`.

## Research

The work is internal to this codebase — no new external libraries, services, or APIs. Skipping WebSearch (purely internal change to existing `agent/session_health.py` plus `models/agent_session.py` schema additions). External pattern reference (Fazm) is already linked in the issue body.

## Spike Results

No spikes required. Every architectural decision the plan must answer is resolvable by codebase inspection (which I performed in Freshness Check) plus a tradeoff call. Specifically:

- The "in-flight registry" question (issue #1) is answerable by reading `models/agent_session.py` (only single-slot `current_tool_name` exists — no `tool_use_id` ↔ `started_at` map).
- The cadence question (#7) is answerable by reading `agent/session_health.py:145` (current 300s).
- The synthetic-completion question (#4) is answerable by reading the existing recovery branch (single-line `running → pending` transition).

## Data Flow

```
PreToolUse hook fires
  → liveness_writers.record_tool_boundary(tool_name=X, clear=False)
  → AgentSession.current_tool_name = X, last_tool_use_at = now
  → SDK subprocess executes the tool
  → [WEDGE: tool never returns; PostToolUse never fires]

Liveness sub-loop tick (every 30s; new dedicated loop)
  → for each running AgentSession with current_tool_name != None:
    → tier = _classify_tool_tier(current_tool_name)
    → budget = TIER_BUDGETS[tier]
    → age = now - last_tool_use_at
    → if age > budget:
      → increment AgentSession.tool_timeout_count_{tier}
      → INCR project_key:session-health:tool_timeouts:{tier}
      → fall through to existing recovery branch (running → pending)
        - reuses same path as current "no_progress" recovery
        - reason string: "tool-wedge: {tool_name} ({tier} tier) older than {budget}s"
```

## Architectural Impact

- **New dependencies**: None. All work uses existing `psutil`, Popoto, Redis primitives.
- **Interface changes**:
  - 3 new `IntField`s on `AgentSession`: `tool_timeout_count_internal`, `tool_timeout_count_mcp`, `tool_timeout_count_default` (default=0, backcompat-safe — Popoto handles missing fields gracefully).
  - 1 new env var: `TOOL_TIMEOUT_TIERS_DISABLED=1` (kill switch, parallel to `DISABLE_PROGRESS_KILL`).
  - 3 new env-tunable budgets: `TOOL_TIMEOUT_INTERNAL_SEC` (30), `TOOL_TIMEOUT_MCP_SEC` (120), `TOOL_TIMEOUT_DEFAULT_SEC` (300).
- **Coupling**: Increases coupling between `agent/session_health.py` and the in-flight visibility fields owned by `agent/hooks/liveness_writers.py`. Both already exist; the new check is a *reader* of fields the writers populate. No bidirectional coupling.
- **Data ownership**: New counters owned by `agent/session_health.py` (the only writer). The `current_tool_name` / `last_tool_use_at` fields stay owned by `liveness_writers.py`. Fields are read-only from the new sub-check's perspective.
- **Reversibility**: High. The new sub-loop can be killed by `TOOL_TIMEOUT_TIERS_DISABLED=1`; the new fields are nullable/default-0 and harmless if the loop is removed entirely.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM check-in for tier-budget defaults and synthetic-vs-hard-recovery decision.

**Interactions:**
- PM check-ins: 1-2 (tier-budget validation; recovery semantics)
- Review rounds: 1 (code review on `session_health.py` changes — high-traffic file)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `popoto` available | `python -c "import popoto"` | AgentSession schema additions |
| `psutil` available | `python -c "import psutil"` | Reused for existing Tier 2 (no new dependency) |
| Redis reachable | `python -c "from popoto.redis_db import POPOTO_REDIS_DB; POPOTO_REDIS_DB.ping()"` | Project-tier counters |

Run all checks: `python scripts/check_prerequisites.py docs/plans/per_tool_timeout_tier_counters.md`

## Solution

### Key Elements

- **`_classify_tool_tier(tool_name: str) -> Literal["internal", "mcp", "default"]`** — pure function in `agent/session_health.py`. `mcp__` prefix → `"mcp"`; in `_INTERNAL_TOOL_NAMES` constant → `"internal"`; everything else → `"default"`.
- **`_check_tool_timeout(entry: AgentSession) -> tuple[str, str] | None`** — pure function. Returns `(tier, reason)` if `current_tool_name` is non-null AND `last_tool_use_at` is older than the tier budget; `None` otherwise. No side effects.
- **Three `IntField` counters on `AgentSession`** — `tool_timeout_count_{internal,mcp,default}`, default=0.
- **Dedicated 30s liveness sub-loop** — `_agent_session_tool_timeout_loop()`, scheduled alongside `_agent_session_health_loop()` in the worker startup. Reuses the existing `running` session iterator. Does NOT reduce `AGENT_SESSION_HEALTH_CHECK_INTERVAL` (avoids load impact on the existing 5-min loop's other checks).
- **Recovery via existing branch** — when a tool timeout is detected, the sub-loop sets a `tool_timeout_pending: bool` attribute on the session entry in-memory, then calls into the existing `_agent_session_health_check` recovery path with a custom reason string. (Implementation detail: the simplest concrete plumbing is to pass a precomputed `reason` and `_reason_kind="tool_timeout"` into the same `should_recover=True` branch.)
- **Project-tier Redis counter** — `INCR {project_key}:session-health:tool_timeouts:{tier}`, identical pattern to the existing `recoveries:{kind}` counter at line 863.
- **Kill switch** — `TOOL_TIMEOUT_TIERS_DISABLED=1` short-circuits the sub-loop entirely (parity with `DISABLE_PROGRESS_KILL`).

### Flow

Worker startup → schedule `_agent_session_health_loop` (300s) AND `_agent_session_tool_timeout_loop` (30s) in parallel → each sub-loop scans `AgentSession.query.filter(status="running")` → tool-timeout sub-loop applies `_check_tool_timeout` to every row → on hit: bump per-tier `IntField`, INCR project Redis counter, transition `running → pending` via existing recovery branch with reason `"tool-wedge: {tool_name} ({tier}) older than {budget}s"`.

### Technical Approach

- **Synthetic completion vs hard recovery — defer to hard recovery only (v1).** The Fazm "synthetic completed notification" pattern requires plumbing into the harness's tool_result-injection path, which would couple this work to `agent/sdk_client.py` and the `claude -p` subprocess protocol. Hard recovery (existing `running → pending` transition) is a one-line addition. **v1 = hard recovery for all tiers; synthetic-completion is explicitly deferred to a separate issue.** Operator-visible signal is the per-tier counter; the cost is one re-queue per wedge, which is acceptable given how rare wedges are.
- **Single-slot `current_tool_name` is sufficient (v1).** Adding a `tool_use_id`-keyed in-flight registry would require schema changes on top of the schema changes already in this plan, and the issue's stated detection problem is solvable with the single slot. The single-slot limitation (Tool A wedged, Tool B fired before A returned, A is invisible) is **documented in the feature doc** but not addressed in v1.
- **Internal tool set is hard-coded.** `_INTERNAL_TOOL_NAMES = frozenset({"ToolSearch", "Read", "Glob", "Grep", "Edit", "Write", "NotebookEdit"})`. Drift risk is real but small — adding a tool to this set is a one-line edit. **Not env-overridable in v1.**
- **Cadence: dedicated 30s sub-loop.** The 5-min cadence of the existing health loop would defeat the 30s internal budget (a 30s wedge could fire as late as 5min30s after start). Solution = parallel sub-loop with its own 30s sleep. The existing 300s checks (heartbeat, psutil, OOM defer) stay unchanged.
- **Counter design: cumulative `IntField`s only.** Structured `tool_timeout_history: list[dict]` is rejected — cap/rotation overhead exceeds the dashboard value. Counters survive Popoto field-backcompat heal generically and answer the dashboard question ("which tier dominates?") directly.
- **No nudge-loop or steering-message changes.** The recovered session restarts from `pending` (it does NOT see a "your tool wedged" steering message). Documented in feature doc as a v2 enhancement.
- **Race window between detection and recovery.** A tool's PostToolUse could fire **between** the sub-loop's read of `current_tool_name`/`last_tool_use_at` and the recovery transition. Mitigation: re-read both fields immediately before transition; if `current_tool_name` is now `None` OR `last_tool_use_at` is fresher than the budget, abort the recovery (treat as no-op tick). This costs one extra `AgentSession.query` round-trip per recovery but is the only correct way to avoid killing a session whose tool just completed.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] No new `except Exception: pass` blocks introduced. The Redis `INCR` and Popoto `save()` calls are wrapped in `try/except` matching the existing pattern at [session_health.py:860-868](agent/session_health.py) (debug-log on failure, never block recovery).
- [ ] Test: monkeypatch the Redis client to raise `ConnectionError` on `incr` — verify the recovery path still completes and the `IntField` counter still bumps.

### Empty/Invalid Input Handling
- [ ] `_classify_tool_tier(tool_name=None)` and `_classify_tool_tier(tool_name="")` must return `"default"` (not raise). Test both.
- [ ] `_check_tool_timeout` with `current_tool_name=None` MUST return `None` (no-op path). Test.
- [ ] `_check_tool_timeout` with `last_tool_use_at=None` (legacy session pre-Pillar A) MUST return `None`. Test.

### Error State Rendering
- [ ] If the session's project_key is missing or `None`, the project Redis counter increment is skipped silently with a debug log — but the per-tier `IntField` and the recovery itself MUST still proceed. Test.

## Test Impact

- [ ] `tests/unit/test_session_health.py` — UPDATE: add a new test class `TestToolTimeoutSubLoop` covering tier classification, age-vs-budget boundary cases, counter increments, and the no-current-tool no-op path. Existing tests for `_has_progress` and `_tier2_reprieve_signal` should not regress (tool-timeout sub-loop is parallel to them, not a replacement).
- [ ] `tests/unit/test_liveness_writers.py` — UPDATE (minor): add a regression test asserting that `record_tool_boundary(clear=True)` resets `current_tool_name=None` so the new sub-loop's "current_tool_name is None" no-op path is correct.
- [ ] `tests/integration/test_session_health_recovery.py` (if exists; check) — REPLACE or UPDATE: add an end-to-end case where a fake AgentSession has `current_tool_name="Bash"` and `last_tool_use_at` set 6 minutes ago — assert the session is recovered with reason starting `"tool-wedge: Bash (default)"`.

If `tests/integration/test_session_health_recovery.py` does not exist, this row becomes "CREATE: new integration test for the sub-loop's end-to-end behavior."

## Rabbit Holes

- **Building a `tool_use_id`-keyed in-flight registry.** Tempting because Fazm has one. Avoid in v1 — the single-slot approximation answers the dashboard question and the issue's stated detection problem. Document the limitation; defer to a follow-up issue if real wedges expose it.
- **Synthetic `tool_result` injection.** Plumbing this into the harness subprocess protocol couples the work to `agent/sdk_client.py` and the SDK message format. Avoid in v1 — hard recovery is a one-line change.
- **Per-tool-regex override map (`config/tool_timeouts.yaml`).** Configurability beyond per-tier env vars is over-scope for v1. Three env vars (`TOOL_TIMEOUT_INTERNAL_SEC`, `TOOL_TIMEOUT_MCP_SEC`, `TOOL_TIMEOUT_DEFAULT_SEC`) are sufficient.
- **Reducing `AGENT_SESSION_HEALTH_CHECK_INTERVAL` to 30s globally.** Avoid — would re-run psutil scans, OOM-defer logic, and orphan-subprocess reaps every 30s, ~10x the load. Dedicated parallel sub-loop is the correct knife-cut.
- **Adding `tool_timeout_history: list[dict]`.** Rejected. Counters are sufficient for the dashboard question. Per-event history is post-hoc forensics that the existing JSONL log already covers.

## Risks

### Risk 1: False-positive timeout on a legitimately long Bash command
**Impact:** A user runs `npm install` (5+ min legitimately) and the sub-loop kills it at 5 min.
**Mitigation:** The 5min `default` tier matches Fazm's chosen default. The env override `TOOL_TIMEOUT_DEFAULT_SEC` is exposed for users who run consistently long Bash commands. Internal tool set is conservative — only includes tools that should never legitimately exceed 30s.

### Risk 2: Race between PostToolUse fire and sub-loop recovery
**Impact:** PostToolUse fires at t=29.9s (just under the 30s budget); sub-loop tick at t=30.0s reads stale `current_tool_name="Read"` and stale `last_tool_use_at`; sub-loop recovers a session that's already proceeding to the next turn.
**Mitigation:** Documented in Technical Approach — the sub-loop must re-read `current_tool_name` and `last_tool_use_at` immediately before the recovery transition. If either has advanced (current_tool_name is None OR last_tool_use_at is newer), abort the recovery for this tick.

### Risk 3: Multi-recovery thrash (a tool that wedges, gets recovered, re-runs, wedges again)
**Impact:** Same tool wedges every retry; per-tier counter increments forever; project Redis counter grows unboundedly.
**Mitigation:** Existing `MAX_RECOVERY_ATTEMPTS=2` cap fires (the recovery transitions `running → pending`, which bumps `recovery_attempts`; at attempt 2 the session finalizes as `failed`). The per-tier counter is bounded by `MAX_RECOVERY_ATTEMPTS` × N sessions per project. Acceptable.

## Race Conditions

### Race 1: PostToolUse fires while sub-loop is computing recovery
**Location:** `agent/session_health.py:_agent_session_tool_timeout_loop` (new function), interleaving with `agent/hooks/post_tool_use.py::post_tool_use_hook` writing via `liveness_writers.record_tool_boundary(clear=True)`.
**Trigger:** Sub-loop reads stale `current_tool_name="Bash"`/`last_tool_use_at=t-301s` at t=0; PostToolUse fires at t=+0.05s setting `current_tool_name=None`/`last_tool_use_at=t+0.05s`; sub-loop attempts recovery at t=+0.10s.
**Data prerequisite:** `current_tool_name` and `last_tool_use_at` fields populated by Pillar A writers.
**State prerequisite:** Session is in `status="running"`.
**Mitigation:** Re-read both fields immediately before the recovery transition (idempotent re-query). If `current_tool_name is None` OR `last_tool_use_at` is newer than the budget allows, abort the recovery and treat as a no-op tick. Documented in Technical Approach.

### Race 2: Sub-loop and main health loop simultaneously try to recover the same session
**Location:** `agent/session_health.py:_agent_session_health_loop` (300s) AND `_agent_session_tool_timeout_loop` (30s, new) reading the same `running` row.
**Trigger:** A session is wedged in both ways (worker dead AND tool-wedge condition). Both loops decide to recover.
**Data prerequisite:** Session row in `running` state.
**State prerequisite:** Both loops scheduled and active.
**Mitigation:** The recovery branch already uses Popoto's `finalize_session(...)` and `running → pending` transition with `StatusConflictError` handling at [session_health.py:874-890](agent/session_health.py). The CAS-style status transition guarantees only one loop wins; the loser sees `StatusConflictError` and logs at INFO. Both loops increment their own counters before the transition attempt — the loser's counter increment is benign (the session DID exhibit both conditions; the counter accurately reflects that). The project-tier Redis counter is incremented by both losing and winning loops, which slightly over-counts in the rare double-wedge case. Acceptable.

## No-Gos (Out of Scope)

- **Synthetic `tool_result` injection** — deferred to a separate issue. v1 does hard recovery only.
- **Per-`tool_use_id` in-flight registry** — single-slot approximation only in v1.
- **Per-tool YAML override map** — only the three per-tier env vars in v1.
- **Steering-message integration** — recovered sessions restart from `pending` without a "your tool wedged" steering note. Deferred.
- **Reducing the global health-check interval** — sub-loop runs at 30s; main loop stays at 300s.
- **Counter rotation / time-window decay** — counters are cumulative for the lifetime of the session row. Project Redis counters are cumulative forever (matches `recoveries:{kind}` precedent).

## Update System

No update-system changes required. The new fields on `AgentSession` are nullable / default-0 (Popoto handles forward-compat). The new env vars (`TOOL_TIMEOUT_INTERNAL_SEC`, `TOOL_TIMEOUT_MCP_SEC`, `TOOL_TIMEOUT_DEFAULT_SEC`, `TOOL_TIMEOUT_TIERS_DISABLED`) have safe defaults baked into `agent/session_health.py` — machines without these in `~/Desktop/Valor/.env` get the documented defaults. Add the four env vars (with comments) to `.env.example` so the env-completeness check in `scripts/update/env_sync.py` doesn't flag them as missing.

## Agent Integration

No agent integration required. The new sub-loop is an internal worker behavior — it does not surface a CLI, MCP tool, or bridge import. The agent (Claude Code) does not need to know the sub-loop exists; sessions whose tools wedge are simply re-queued and the agent gets a fresh turn. The dashboard (`ui/app.py`) MAY surface the new `tool_timeout_count_*` fields on the session JSON, but that's a documentation enhancement, not a wiring change — the dashboard already reflects every `AgentSession` field via `_session_to_json()`.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/session-health-check.md` — add a new section "Per-Tool Timeout Sub-Loop (Tier 1.5)" describing the new sub-loop, the three tiers, the budgets, the env overrides, the kill switch, and the limitation (single-slot `current_tool_name`).
- [ ] Update `docs/features/agent-session-health-monitor.md` — add a brief note linking to the new sub-loop section in `session-health-check.md`. Cross-link from the "Detection" subsection.
- [ ] Update `docs/features/README.md` index table — add a row for the new sub-loop if not already covered by the existing session-health-check entry.

### External Documentation Site
No external docs site is in use for this section of the repo.

### Inline Documentation
- [ ] Code comments on `_classify_tool_tier`, `_check_tool_timeout`, and `_agent_session_tool_timeout_loop` covering: the tier semantics, the env override names, why the loop is parallel to the main loop (not folded in), and the re-read-before-transition race mitigation.
- [ ] Update the docstring on `_has_progress` in `session_health.py` to cross-reference the new sub-loop ("This Tier 1 freshness check uses `last_tool_use_at` as a positive progress signal; the parallel `_agent_session_tool_timeout_loop` uses the same field in the OPPOSITE direction — staleness while `current_tool_name` is non-null is the wedge condition.").
- [ ] Add docstring on the three new `IntField`s in `models/agent_session.py` describing tier semantics and writer.

## Success Criteria

- [ ] `AgentSession.tool_timeout_count_internal`, `_mcp`, `_default` fields exist and default to 0.
- [ ] `_classify_tool_tier` returns `"internal"` for {`ToolSearch`, `Read`, `Glob`, `Grep`, `Edit`, `Write`, `NotebookEdit`}, `"mcp"` for any name starting with `mcp__`, `"default"` otherwise.
- [ ] `_check_tool_timeout` correctly identifies tool-wedge condition at tier-budget boundaries (29s = no, 31s = yes for internal; 119s/121s for mcp; 299s/301s for default).
- [ ] `_agent_session_tool_timeout_loop` runs every 30s, scans running sessions, recovers wedged ones, increments per-tier counters.
- [ ] Project Redis counter `{project_key}:session-health:tool_timeouts:{tier}` increments on recovery.
- [ ] Recovery uses the existing `running → pending` branch with reason starting `"tool-wedge: ..."`.
- [ ] `TOOL_TIMEOUT_TIERS_DISABLED=1` short-circuits the sub-loop entirely.
- [ ] Re-read race-mitigation: a session whose `current_tool_name` becomes `None` between detection and transition is NOT recovered.
- [ ] Existing Tier 1 / Tier 2 detector is unmodified and not regressed (test coverage in `tests/unit/test_session_health.py` still passes).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead NEVER builds directly - they deploy team members and coordinate.

### Team Members

- **Builder (schema)**
  - Name: schema-builder
  - Role: Add three `IntField`s to `AgentSession` with docstrings; verify Popoto backcompat-heal handles missing fields.
  - Agent Type: builder
  - Resume: true

- **Builder (sub-loop)**
  - Name: subloop-builder
  - Role: Add `_classify_tool_tier`, `_check_tool_timeout`, `_agent_session_tool_timeout_loop` to `agent/session_health.py`; wire into worker startup.
  - Agent Type: builder
  - Resume: true

- **Test Engineer (unit)**
  - Name: unit-tester
  - Role: Add `TestToolTimeoutSubLoop` to `tests/unit/test_session_health.py` covering classification, boundary cases, counter increments, no-op paths, race-window mitigation, and kill switch.
  - Agent Type: test-engineer
  - Resume: true

- **Test Engineer (integration)**
  - Name: integration-tester
  - Role: Add or extend `tests/integration/test_session_health_recovery.py` with an end-to-end wedged-tool case.
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Update `docs/features/session-health-check.md` and cross-link from `docs/features/agent-session-health-monitor.md`.
  - Agent Type: documentarian
  - Resume: true

- **Validator**
  - Name: lead-validator
  - Role: Run all unit + integration tests, verify success criteria, confirm no regressions in existing health-check tests.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add per-tier counter fields to AgentSession
- **Task ID**: build-schema
- **Depends On**: none
- **Validates**: `tests/unit/test_agent_session.py` (existing schema tests pass), `python -c "from models.agent_session import AgentSession; AgentSession(); print('ok')"` (smoke)
- **Informed By**: Freshness Check confirmed `models/agent_session.py:359, 364` for adjacent in-flight visibility fields.
- **Assigned To**: schema-builder
- **Agent Type**: builder
- **Parallel**: true
- Add three `IntField(default=0)` fields: `tool_timeout_count_internal`, `tool_timeout_count_mcp`, `tool_timeout_count_default`.
- Add docstrings explaining tier semantics and writer (the new sub-loop in `session_health.py`).
- Place in the same field block as `current_tool_name` / `last_tool_use_at` (the Pillar A block).

### 2. Add tier-classification and timeout-check helpers
- **Task ID**: build-helpers
- **Depends On**: none
- **Validates**: New unit tests added in step 4.
- **Assigned To**: subloop-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `_INTERNAL_TOOL_NAMES = frozenset({"ToolSearch", "Read", "Glob", "Grep", "Edit", "Write", "NotebookEdit"})` constant near the top of `agent/session_health.py`.
- Add `TOOL_TIMEOUT_INTERNAL_SEC = int(os.environ.get("TOOL_TIMEOUT_INTERNAL_SEC", 30))`, `TOOL_TIMEOUT_MCP_SEC = int(os.environ.get("TOOL_TIMEOUT_MCP_SEC", 120))`, `TOOL_TIMEOUT_DEFAULT_SEC = int(os.environ.get("TOOL_TIMEOUT_DEFAULT_SEC", 300))`.
- Add `_classify_tool_tier(tool_name: str | None) -> Literal["internal", "mcp", "default"]` — pure function, handles None/empty as `"default"`.
- Add `_check_tool_timeout(entry: AgentSession) -> tuple[str, str] | None` — returns `(tier, reason)` if wedged, else None.

### 3. Add the dedicated 30s sub-loop and recovery wiring
- **Task ID**: build-subloop
- **Depends On**: build-schema, build-helpers
- **Validates**: New unit tests added in step 4; integration test added in step 5.
- **Assigned To**: subloop-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `_agent_session_tool_timeout_loop()` async function in `agent/session_health.py` modeled on `_agent_session_health_loop` but with `await asyncio.sleep(30)`.
- Inside the loop, iterate `AgentSession.query.filter(status="running")` and apply `_check_tool_timeout`.
- On hit:
  - Re-read `current_tool_name` and `last_tool_use_at` from a fresh query (race mitigation).
  - If still wedged: bump `entry.tool_timeout_count_{tier}` via partial save (`update_fields=[...]`).
  - INCR `{project_key}:session-health:tool_timeouts:{tier}` Redis counter (try/except with debug log, never block).
  - Call into the existing recovery transition path with `reason=f"tool-wedge: {tool_name} ({tier} tier) older than {budget}s"` and `_reason_kind="tool_timeout"`.
- Add `TOOL_TIMEOUT_TIERS_DISABLED` env-var short-circuit at the top of each tick.
- Wire the loop into worker startup at the same site where `_agent_session_health_loop` is scheduled (find via `grep -n "_agent_session_health_loop" agent/session_health.py worker/__main__.py agent/agent_session_queue.py`).

### 4. Add unit tests
- **Task ID**: build-unit-tests
- **Depends On**: build-schema, build-helpers
- **Validates**: `pytest tests/unit/test_session_health.py -v`
- **Assigned To**: unit-tester
- **Agent Type**: test-engineer
- **Parallel**: true (with build-subloop, since the helpers exist)
- Add `TestToolTimeoutSubLoop` class with cases:
  - Tier classification: `mcp__foo` → mcp; `Read` → internal; `Bash` → default; `None`/`""` → default.
  - Boundary ages: 29s/31s for internal, 119s/121s for mcp, 299s/301s for default.
  - No-op paths: `current_tool_name=None`, `last_tool_use_at=None`.
  - Counter increments: assert `tool_timeout_count_internal` bumps from 0 → 1 on internal-tier hit.
  - Project Redis counter: assert `INCR` was called with expected key.
  - Race mitigation: monkeypatch the second-read query to return a fresh `last_tool_use_at` — assert recovery is aborted.
  - Kill switch: set `TOOL_TIMEOUT_TIERS_DISABLED=1`, run one tick, assert no recoveries happened.

### 5. Add integration test
- **Task ID**: build-integration-test
- **Depends On**: build-subloop
- **Validates**: `pytest tests/integration/test_session_health_recovery.py -v` (or the new file path).
- **Assigned To**: integration-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Create or extend `tests/integration/test_session_health_recovery.py`:
  - Set up a fake `AgentSession` with `status="running"`, `current_tool_name="Bash"`, `last_tool_use_at = now - 301s`.
  - Run one tick of `_agent_session_tool_timeout_loop` (extracted via a `_run_one_tick` test seam, or by direct call to the inner check function).
  - Assert: session row transitions to `pending`, `tool_timeout_count_default` is 1, project Redis counter `tool_timeouts:default` is 1, recovery reason starts with `"tool-wedge: Bash (default)"`.

### 6. Validate (parallel test runs)
- **Task ID**: validate-tests
- **Depends On**: build-unit-tests, build-integration-test
- **Assigned To**: lead-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_session_health.py tests/unit/test_liveness_writers.py tests/unit/test_agent_session.py -v`.
- Run `pytest tests/integration/test_session_health_recovery.py -v`.
- Run `python -m ruff format --check . && python -m ruff check .` (lint scope: changed files).
- Confirm no regressions in existing Tier 1 / Tier 2 tests.

### 7. Update documentation
- **Task ID**: document-feature
- **Depends On**: build-subloop
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: true (with validate-tests)
- Update `docs/features/session-health-check.md` with the new "Per-Tool Timeout Sub-Loop" section.
- Update `docs/features/agent-session-health-monitor.md` with a cross-link.
- Update `docs/features/README.md` index table if not already covered.
- Verify `.env.example` has the four new env-var entries with comments.

### 8. Final validation
- **Task ID**: validate-all
- **Depends On**: validate-tests, document-feature
- **Assigned To**: lead-validator
- **Agent Type**: validator
- **Parallel**: false
- Confirm every Success Criterion checkbox.
- Run `pytest tests/ -x -q` (full suite smoke).
- Run `python -m ruff format --check . && python -m ruff check .`.
- Generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests pass | `pytest tests/unit/test_session_health.py tests/unit/test_liveness_writers.py tests/unit/test_agent_session.py -x -q` | exit code 0 |
| Integration test passes | `pytest tests/integration/test_session_health_recovery.py -x -q` | exit code 0 |
| Full test suite smoke | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Schema fields present | `python -c "from models.agent_session import AgentSession; a = AgentSession(); assert hasattr(a, 'tool_timeout_count_internal') and hasattr(a, 'tool_timeout_count_mcp') and hasattr(a, 'tool_timeout_count_default')"` | exit code 0 |
| Helper present | `python -c "from agent.session_health import _classify_tool_tier; assert _classify_tool_tier('mcp__foo') == 'mcp' and _classify_tool_tier('Read') == 'internal' and _classify_tool_tier('Bash') == 'default'"` | exit code 0 |
| Sub-loop scheduled | `grep -n "_agent_session_tool_timeout_loop" agent/session_health.py worker/__main__.py agent/agent_session_queue.py` | output > 1 |
| Env vars in example | `grep -E "TOOL_TIMEOUT_(INTERNAL_SEC\|MCP_SEC\|DEFAULT_SEC\|TIERS_DISABLED)" .env.example` | output contains all 4 |

## Critique Results

<!-- Populated by /do-plan-critique. -->

---

## Open Questions

1. Are the tier defaults right? (30s internal / 2min MCP / 5min default — taken from issue's Solution Sketch + Fazm reference, but adjusted internal from 10s to 30s to give the 30s sub-loop one tick of headroom.)
2. Hard recovery for all tiers in v1 vs. synthetic-completion for any tier? Plan currently picks hard recovery for all; want to confirm before build.
3. Should the internal tool set include `Bash` for short shell commands? Plan currently treats `Bash` as `default`. Could argue some `Bash` invocations (e.g., `ls`, `cat`, `pwd`) should be internal-tier — but classifying by *invocation arguments* is over-scope. Keep `Bash` in `default` for v1.
