# Plan Critique: Unify Loops, Schedules, Routines, and Reflections

**Plan**: `docs/plans/unify-recurring-tasks-into-reflections.md`
**Issue**: #1273
**Critics**: Skeptic, Operator, Archaeologist, Adversary, Simplifier, User
**Findings**: 12 total (3 blockers, 6 concerns, 3 nits)

## Blockers

### B1. `ReflectionRun` model name was deliberately deleted; reusing it courts the same fate

- **Severity**: BLOCKER
- **Critics**: Archaeologist, Skeptic
- **Location**: Q1 (Architectural Impact, Solution), Task 1, Success Criteria proof for `models/reflection_run.py`
- **Finding**: `models/reflections.py` is a re-export shim whose docstring states verbatim: "ReflectionRun has been removed (issue #748)." Commit `fa5c89a3` deleted both the monolith and `ReflectionRun` as Phase C of the previous unification (PR #967). The plan's "Prior Art" section names #967 as a successful precedent but does not acknowledge that a model with this exact name existed, was tied to the same recurring-task substrate, and was deleted by name eight commits later. Recreating a Popoto model named `ReflectionRun` in `models/reflection_run.py` is not just symbolic — it puts new code on the same import path the prior cleanup explicitly cleared, and any test or doc fixture that still references the old class will silently bind to the new one with a different schema. The schemas are unrelated (the deleted one tracked daily ReflectionRunner state; the proposed one tracks per-execution history) which makes the collision more dangerous, not less, because anyone consulting the original deletion's rationale will assume the name is reserved.
- **Suggestion**: Rename the new model to something that does not collide with a deliberately-cleared identifier. Candidates: `ReflectionExecution`, `ReflectionEvent`, `ReflectionLog`, `ReflectionAttempt`. Update Q1, Task 1, Test Impact, Success Criteria, Verification table, and the migration script accordingly. In the Prior Art section, explicitly cite the #748/#967 deletion of the prior `ReflectionRun` and explain why the new name is different.
- **Implementation Note**: The shim at `models/reflections.py` currently exports `__all__ = ["ReflectionIgnore", "PRReviewAudit"]`. Adding back `ReflectionRun` to that list (or to a sibling file) re-introduces a name the prior cleanup explicitly removed; tests at `tests/integration/test_reflections_redis.py` lines 3-4 and `tests/unit/test_pr_review_audit.py` lines 6-7 contain comments like "ReflectionRun model no longer exists" that will become incorrect-but-passing when the new class is introduced under the old name. Pick a non-colliding model name; do the rename in one pass; grep `ReflectionRun` repo-wide before the rename PR to confirm zero hits in non-historical contexts.

### B2. `mark_completed()` API surface changes silently — every existing callsite breaks the dashboard-fast-read invariant

- **Severity**: BLOCKER
- **Critics**: Skeptic, Adversary
- **Location**: Q1 ("Reflection.last_run_summary stays embedded"), Task 1 ("Preserve existing mark_started, mark_completed, mark_skipped API surface where shape allows; refactor where shape changes"), Data Flow step 8
- **Finding**: The plan removes embedded `run_history` from `Reflection` (Q1 cycle-3 ripple) and adds `last_run_summary` for fast dashboard reads, but `Reflection.mark_completed()` at `models/reflection.py:85-128` currently writes the full run record into `run_history` *as part of its contract*. The plan's "preserve existing API surface where shape allows" is a hand-wave: the shape *cannot* be preserved when the destination field is removed. The plan does not specify (a) what `mark_completed` writes when `run_history` is gone, (b) whether it now also writes a `ReflectionRun` row in the same call (which doubles the Redis write count per tick from 1 to 2 — a 2× regression on the hot path), (c) what `last_run_summary` actually looks like (a dict of which fields? — Q1 says "{ran_at, status, duration, error}" but the current `mark_completed` payload also includes `projects`, `timestamp`, plus fields the plan adds (`cost_usd`, `tokens_input`, `tokens_output`)). The single-line claim "preserve where shape allows; refactor where shape changes" defers the load-bearing decision out of the plan and into the build phase, which is exactly the pattern critique cycles 1 and 2 already flagged elsewhere.
- **Suggestion**: Specify `mark_completed`'s new contract concretely in Q1 or Task 1. State which fields land on `last_run_summary` (a single dict on `Reflection`), which fields land on `ReflectionRun` (the per-run row), and whether `mark_completed` makes one Popoto save or two. Add a Race Condition entry covering the case where the `Reflection.save()` succeeds but the `ReflectionRun.create()` fails — the dashboard summary diverges from the per-run history.
- **Implementation Note**: Current shape — `Reflection.mark_completed(duration, error=None, projects=None)` does ONE save and writes a run record into `run_history` capped at 200. New shape needs: (1) `last_run_summary = {"ran_at": time.time(), "status": "success"|"error", "duration_ms": int, "error": str|None, "cost_usd": float}` (one dict, atomic with the parent save), (2) `ReflectionRun.create(reflection_name=..., timestamp=..., status=..., duration_ms=..., cost_usd=..., tokens_input=..., tokens_output=..., error=..., projects=...)` (separate save). The two writes are not atomic at the Redis level; if the second fails the dashboard summary contradicts the run history. Either swallow the inconsistency (document it) or use a sidecar "pending_run_persist" set and a small reconciler — the latter contradicts feedback_prevention_over_cleanup so document-and-accept is preferred. Also: `total_cost_usd`/`total_input_tokens`/`total_output_tokens` are the actual `AgentSession` field names (verified at `models/agent_session.py:369-375`), NOT `cost_usd`/`tokens_input`/`tokens_output` as the plan's Q8 implementation guard claims — fix the field-name mapping in Task 1 and the migration script before build, otherwise the cost fields will silently land as zero on every agent-type reflection run.

### B3. `pending_clear` sidecar is not bounded — a machine that misses several `/update` runs accumulates orphan entries forever, and there is no worker-startup drain

- **Severity**: BLOCKER
- **Critics**: Operator, Adversary
- **Location**: Q3 phase 2, Task 8, Task 12, Race 1
- **Finding**: The plan's coexistence-with-running-reflections design uses a Popoto-managed sidecar set `reflections:migration:pending_clear` populated during the `/update` migration, then drained on the *next* `/update` run. The flagged-by-plan-maker question (focus area #3) is correct in suspecting this surface. Specifically: (a) **the sidecar set has no TTL specified.** Popoto-managed sets without `Meta.ttl` live forever; if the second `/update` fails before draining, or a reflection enters a permanent zombie "running" state (not unheard-of with our existing stuck-detection bug — see `agent/reflection_scheduler.py:483` which sets `last_status="error"` not "running"-cleared), the sidecar grows monotonically. (b) **There is no worker-startup hook to drain the sidecar.** A machine could go weeks between `/update` runs (the typical case for a developer machine that's powered off) and accumulate names referencing reflections that have long since completed. The migration is "fully reentrant" only because the same code re-runs on the next `/update` — but `/update` is a human-driven trigger, and the worker is the process that actually owns reflection state. (c) **The drain logic re-fetches each Reflection by name, but `last_status` is the worker's view, not the migration's view.** If `/update` runs while the worker has crashed mid-run and the stuck-detection has not yet flipped `last_status` from "running" → "error" (the existing 2× interval logic), the migration sees "running" and skips the clear. On the next `/update`, the stuck-detection has fired and `last_status="error"`, so the clear succeeds — but during the gap, `run_history` is *both* on the Reflection record and on the new `ReflectionRun` rows. The dashboard read (Q1 cycle-3 ripple) reads from `ReflectionRun` only, so this is "merely" a Redis-bytes leak, not a correctness bug — but it is a leak that is silent.
- **Suggestion**: Add three concrete guards: (1) `reflections:migration:pending_clear` sidecar gets `Meta.ttl = 86400 * 14` (14 days). After two weeks of failed drains, the entries expire and the next `/update` re-discovers any still-pending reflections from scratch. (2) Add a worker-startup hook in `worker/__main__.py` that drains the sidecar before the first scheduler tick. The worker is the right owner — it knows the current `last_status` of every reflection. The `/update` drain becomes a belt-and-suspenders double-check, not the only mechanism. (3) Add a Race Condition section entry covering "stuck-running detection has not yet fired when migration scans" — document that the leaked `run_history` bytes self-heal on the next migration, and assert tests cover this path explicitly.
- **Implementation Note**: Sidecar model lives in `models/reflection.py` (or new sibling file); the Popoto pattern is `class MigrationPendingClear(Model): name = KeyField(); class Meta: ttl = 86400 * 14`. Worker startup hook goes between `register_worker_pid()` and the scheduler's `start()` call in `worker/__main__.py` — search for the existing "Hourly agent-session-cleanup reflection" handoff pattern for a precedent. The drain should NOT abort startup on failure (memory MCP precedent: `mcp_memory_result.ok` failures are non-fatal warnings); a partial drain is strictly better than no drain. Do not add a "force clear running entries older than N hours" rule — that races with the existing 2× interval stuck detection at `agent/reflection_scheduler.py:475-487` and would create a write-write conflict.

## Concerns

### C1. 90-day uniform TTL on `ReflectionRun` is defensible-but-wrong; per-reflection TTL keyed off frequency is the right shape and costs almost nothing to add

- **Severity**: CONCERN
- **Critics**: Skeptic, Simplifier
- **Location**: Q1 implementation guard, Risk 3, Success Criteria proof for TTL
- **Finding**: The plan flags this in focus area #2 and is right to. 90 days × 32 reflections × varying frequencies produces an asymmetric distribution: `circuit-health-gate` at 60s generates 1,440 rows/day = 129,600 rows over 90 days for *one* reflection, while `daily-log-review` at 86400s generates ~90 rows over the same window. The high-frequency reflections produce 1,400× more data than the daily ones, and that data is also 1,400× less interesting per row (60-second circuit-state checks are observability noise after a week, not a quarter). 90 days is too short for the daily/weekly reflections (a quarterly post-mortem cannot reconstruct a "what happened during the holiday slowdown" investigation) and 1,000× too long for the per-minute reflections. The plan defends 90 days as "defensible but not rigorously chosen" — that is the right framing, and the right answer is per-reflection TTL.
- **Suggestion**: Replace the uniform `Meta.ttl = 86400 * 90` on `ReflectionRun` with a per-row TTL set at create time, computed as `max(7d, ceil(N * interval_seconds))` where N is a small multiplier (suggest 200, matching the current `_RUN_HISTORY_CAP`). For `every: 60s`, that's 200 × 60 = 12,000 seconds = 3.3 hours retained — too short. For `every: 1d`, that's 200 days. Hmm, this needs a per-frequency tier instead: `<= 5min interval → 7d retention; <= 1h → 30d; > 1h → 90d`. This is one helper function and one extra arg on `ReflectionRun.create()`. The dashboard-side post-mortem use case is preserved for the long-frequency reflections; the 60s-tick noise self-cleans within a week. Net Redis growth drops by ~80% versus uniform 90d.
- **Implementation Note**: The Popoto `Meta.ttl` is class-level — to set per-row TTL you must call Redis directly via the Popoto-exposed key API, which contradicts "no raw Redis on Popoto-managed keys." Two options: (a) override `ReflectionRun.create()` with a method that sets TTL via the model's `redis_db.expire(self._key, ttl_seconds)` — this is technically through Popoto's surface even if the EXPIRE call is direct; verify with the team that `instance.redis_db.expire()` is a sanctioned path. (b) Split into three concrete subclasses (`ReflectionRunHigh`, `ReflectionRunMedium`, `ReflectionRunLow`) each with its own `Meta.ttl`, dispatched by interval at create time. Option (b) is more code but stays purely declarative and avoids the "is this raw Redis or not" question. The migration script's backfill must use the same dispatcher so historical entries land in the right bucket.

### C2. "First-party MCP, leave harness skills invisible forever" trades user observability for an aesthetic — bridge-side passive logging is cheap and answers the question the plan claims it cannot

- **Severity**: CONCERN
- **Critics**: User, Operator
- **Location**: Q4, Open Questions, focus area #1 from plan-maker
- **Finding**: The plan's focus area #1 asks the right question. The decision (B+C: wrap with first-party MCP, leave harness skills alone) is correct in not *shadowing* the harness skills, but the framing "harness scheduling stays invisible to dashboard/memory/analytics forever" overstates the constraint. The bridge is an I/O-only process that already inspects every Telegram message and Claude Code session boundary — it can passively observe when a session uses `/loop` or `/schedule` (the harness emits `ScheduleWakeup` and `create_scheduled_task` tool calls in the session transcript) and write a lightweight Redis row capturing `(session_id, tool_name, schedule_text, timestamp)` for dashboard consumption. This is not "shadowing" — the harness keeps owning the scheduling, our system owns the observation log. The cost is one passive listener and a small Popoto model with TTL=30d. The benefit is the question "what recurring AI work is configured on this machine" (the plan's stated outcome #1) is *actually* answerable — without it, dashboard.json gives a partial answer that quietly drops harness-scheduled work.
- **Suggestion**: Add a fourth option (D) to Q4: "Wrap with first-party MCP AND passively log harness use bridge-side." Adopt B+C+D. Add a small Task 9b: "Bridge-side passive observation: capture `ScheduleWakeup` / `create_scheduled_task` tool-call events from session transcripts, write `HarnessSchedule` Popoto rows with TTL=30d, surface a 'Harness scheduling (read-only)' panel on the dashboard." This is genuinely separate from the migration scope; it can ship independently. If the team rejects it for scope discipline, *keep the rejection in the plan* under "Out of Scope" with the rationale "we accept that `/loop` and `/schedule` use is invisible to our dashboard; the user's mental model is 'first-party for durable, harness for ephemeral.'"
- **Implementation Note**: The hook lives in `bridge/telegram_bridge.py` where the bridge already inspects Claude Code session events; the existing pattern for "passive observation of session activity" is the nudge loop's tool-call inspection. The Popoto model is `class HarnessSchedule(Model): session_id, tool_name, schedule_repr, observed_at; Meta.ttl = 86400 * 30`. The bridge writes; the worker and dashboard read. No reverse routing needed — this is an observation log, not a control plane. If the dashboard panel is in scope, `dashboard.json` gains a `harness_schedules` key with the last N entries; if not, the rows are still queryable via `valor-telegram` debug commands.

### C3. The cost-from-AgentSession field-name mapping is hand-waved and will land all-zero unless verified

- **Severity**: CONCERN
- **Critics**: Skeptic
- **Location**: Q8 implementation guard, Task 1, Task 13 cost-accounting test bullet, Test Impact
- **Finding**: Q8 says "When the executor finishes an agent-type reflection, it reads the spawned `AgentSession.cost_usd` / `tokens_input` / `tokens_output` and writes them onto the `ReflectionRun` row." Verified against `models/agent_session.py:369-375`: the actual field names are `total_cost_usd`, `total_input_tokens`, `total_output_tokens`. None of `cost_usd`, `tokens_input`, `tokens_output` are field names on `AgentSession` today. The plan's other mentions of these names (Architectural Impact bullet, Task 1 bullet, Test Impact "cost-accounting" bullet) carry the same wrong names. If a builder writes the obvious `run.cost_usd = session.cost_usd` line, every agent-type reflection will record `cost_usd = AttributeError → 0` and Q8 silently degrades to "we tracked zeros for a quarter."
- **Suggestion**: Globally replace `tokens_input`/`tokens_output`/`cost_usd` with `total_input_tokens`/`total_output_tokens`/`total_cost_usd` in the plan wherever the source is `AgentSession`. The destination fields on `ReflectionRun` can keep simpler names (`cost_usd`, `tokens_input`, `tokens_output`) but the read-side must use the correct AgentSession names. Add an explicit field-mapping table to Q8 with two columns (Reflection-side / AgentSession-side) so the renaming is unmistakable.
- **Implementation Note**: At `models/agent_session.py:369-375` the fields are `total_input_tokens = IntField(default=0)`, `total_output_tokens = IntField(default=0)`, `total_cost_usd = FloatField(default=0.0)`. The mapping is `ReflectionRun.cost_usd = session.total_cost_usd`, `ReflectionRun.tokens_input = session.total_input_tokens`, `ReflectionRun.tokens_output = session.total_output_tokens`. The read happens after the AgentSession completes — if the session is killed mid-run those fields are still set (the SDK callback writes them on partial completion per the `total_cost_usd` docstring at line 372-374), so the guard is "only read after `session.status` ∈ {completed, killed, failed}." Test should assert all three field-name pairs explicitly using `getattr(session, "total_cost_usd")`-style calls so a future field rename on AgentSession would fail the test loudly.

### C4. Q5's `output_sink:` introduces a cross-cutting delivery contract for benefits the existing system already provides

- **Severity**: CONCERN
- **Critics**: Simplifier
- **Location**: Q5, Task 5, `agent/reflection_output.py`
- **Finding**: The `system-health-digest` reflection at `~/Desktop/Valor/reflections.yaml` is currently `execution_type: agent` with a `command:` prompt that says "send the daily sustainability digest" — the agent itself, given the prompt, picks up the existing Telegram tools and delivers the message. The Q5 design replaces this with declarative `output_sink: telegram:Dev: Valor` and a separate `agent/reflection_output.py::deliver()` helper that *also* calls into the Telegram outbox. There are now two delivery paths (the agent's own tool calls AND the post-completion sink handler), which is a strict regression on the "agent's prompt does the right thing" pattern. For function-type reflections the sink helper makes sense, but for agent-type reflections it duplicates intent that already lives in the prompt. The proof: `system-health-digest` currently delivers to Telegram successfully without an `output_sink:` declaration; Q5 adds the field as if it were a missing capability. It's not — it's a redundant capability for agent-type reflections.
- **Suggestion**: Restrict `output_sink:` to `execution_type: function` reflections only. For agent-type, the agent's prompt continues to drive the delivery surface (which it already does correctly today). Document this explicitly in Q5 as "agent-type reflections do not use `output_sink`; their prompt declares the delivery surface in natural language and the agent uses the existing Telegram/Memory tools." This drops a sink handler, drops the duplicate-delivery-path race condition, and drops half of `agent/reflection_output.py`.
- **Implementation Note**: At parse time in `agent/reflection_scheduler.py::_parse_yaml_entry()` (the function around line 158), reject `output_sink:` when `execution_type: agent` is set. The validator emits a clear ValueError mentioning "for agent-type reflections, the prompt declares delivery." The four-handler `agent/reflection_output.py` collapses to three (`log_only`, `dashboard_only`, `memory:<importance>`); `telegram:<chat>` becomes function-type-only. Existing agent-type reflections do not get an `output_sink:` field added during migration. The Q5 table in the plan is updated to flag the constraint inline.

### C5. Task 8 still cites stale line numbers for `ui/data/reflections.py` despite Q1's cycle-3 fix updating them

- **Severity**: CONCERN
- **Critics**: Adversary, Skeptic (consistency)
- **Location**: Task 9 implementation steps (lines 805-807 of plan)
- **Finding**: Q1's cycle-3 ripple section (lines 195-207 of plan) correctly cites `ui/data/reflections.py` lines 138, 264, 282, 310, 322 for `state.run_history` references — verified against the actual file. But Task 9's implementation bullets (lines 805-807) still cite the stale rev3 line numbers (129, 239-277, 280-306). The Freshness Check at the top of the plan calls out the drift explicitly and updates Q1's numbers; Task 9 was missed in the same edit pass. A builder following Task 9's steps will land patches at the wrong lines.
- **Suggestion**: Sync Task 9's line citations with Q1's cycle-3 ripple section: update line 805 to `_build_entry()` line 138; update line 806 to `get_run_history(name, page)` lines 264 and 282; update line 807 to `get_run_detail(name, run_index)` lines 310 and 322. After the edit, grep the plan for "line 129" / "lines 239-277" / "lines 280-306" — none should remain.
- **Implementation Note**: This is a one-pass mechanical edit. Verify with `grep -nE "line (129|239|280)" docs/plans/unify-recurring-tasks-into-reflections.md` returning zero hits after the fix. The Q1 numbers (138, 264, 282, 310, 322) are verified against the current file at the moment of this critique; if the file drifts again before build, the freshness check at build kickoff catches it.

### C6. "Existing PR-#1187-era stale-running clear logic preserved" is wrong — the existing logic flips to `error`, not "running"-clear

- **Severity**: CONCERN
- **Critics**: Adversary
- **Location**: Task 4 ("On worker restart: scan stale `last_status="running"` and force-clear (preserve PR-#1187-era logic)"), Race 2 mitigation
- **Finding**: At `agent/reflection_scheduler.py:475-487`, the existing stuck-detection sets `state.last_status = "error"` with `last_error = "Reset: appeared stuck (exceeded 2x interval)"` — it does NOT clear `running` to a neutral state, it converts it to a failure. The plan's Task 4 says "force-clear" suggesting a state transition like `running → idle`, but the production code transitions `running → error` and that record now counts toward the new `failure_count_consecutive` and could trip the 5-failure auto-pause threshold. A worker that crashes 5 times in 24h with reflections mid-flight will auto-pause every reflection that was in flight at crash time — even though the underlying reflection logic is fine, only the worker process died. This is the failure-loop-detector pattern the system already has; the new failure tracking will fire on top of it.
- **Suggestion**: Either (a) describe the existing transition correctly and explicitly exempt "stuck-detected" errors from the consecutive-failure counter, or (b) introduce a new transition `running → skipped` (with `last_error = "stale running cleared on worker restart"`) that does NOT increment `failure_count_consecutive`. Option (b) is cleaner; option (a) requires the counter to know its source. Race 2's mitigation already says "force-marked error with last_error..." so the Task 4 wording inherits the same wrongness. Pick a path and propagate.
- **Implementation Note**: The current code at `agent/reflection_scheduler.py:484-486` is `state.last_status = "error"; state.last_error = "Reset: appeared stuck (exceeded 2x interval)"; state.save()`. With option (b), introduce a new `state.mark_skipped(reason="stale running cleared on worker restart")` call site and wire `mark_skipped` to NOT increment the counter (it already doesn't — verified at `models/reflection.py:131`). With option (a), in the new failure-tracking logic, sniff `last_error.startswith("Reset:")` or `last_error.startswith("stale")` and skip the counter increment for those. Option (b) is one new caller and zero string-matching logic; prefer it. Whichever wins, add a unit test asserting "5 consecutive worker-restart-induced stale clears do NOT pause the reflection."

## Nits

### N1. Plan claims "33 reflections" but registry has 32

- **Severity**: NIT
- **Critics**: Skeptic
- **Location**: focus area #2 ("33 reflections × varying frequencies × 90d")
- **Finding**: `grep -c "^- name:" ~/Desktop/Valor/reflections.yaml` returns 32. The plan-maker's focus area uses 33 informally. Cosmetic.
- **Suggestion**: Update to 32, or use "~30" — the math doesn't depend on the exact count.

### N2. Reflection-yaml comment about ReflectionRunner is stale and will confuse the migration

- **Severity**: NIT
- **Critics**: Archaeologist
- **Location**: `~/Desktop/Valor/reflections.yaml` (the `redis-index-cleanup` entry)
- **Finding**: The yaml entry for `redis-index-cleanup` carries the inline comment "Also wired as step_popoto_index_cleanup in ReflectionRunner (scripts/reflections.py)". `scripts/reflections.py` was deleted in PR #967 (issue #748) and `ReflectionRunner` no longer exists. A reader of the migrated yaml encountering this comment will assume the monolith is still relevant. This is not a blocker for the unification migration but is a cosmetic mess the plan should sweep.
- **Suggestion**: Add to the migration script's YAML rewrite phase: strip stale `# Also wired as ...` and `# ReflectionRunner` references from comment lines while preserving structure. Or add to Task 14 (Documentation) a sweep of the yaml for stale references.

### N3. "Bonus" Q9 (bridge watchdog exception) adds zero value to this plan

- **Severity**: NIT
- **Critics**: Simplifier
- **Location**: Q9
- **Finding**: Q9 confirms that the bridge watchdog stays external. This is already documented in `docs/features/reflections.md` and the yaml itself. The plan re-asserts the constraint without changing it. The section adds reading time and gives the impression of nine architectural decisions when there are eight.
- **Suggestion**: Either delete Q9 or fold it into a one-line mention under "## No-Gos" (it's already there). The plan currently has both, doubling up.

## Structural Check Results

| Check | Status | Detail |
|-------|--------|--------|
| Required sections | PASS | All four (Documentation, Update System, Agent Integration, Test Impact) present and non-empty |
| Task numbering | PASS | Tasks 1-15 sequential, no gaps |
| Dependencies valid | PASS | All `Depends On` references point to valid earlier tasks |
| File paths exist | PARTIAL | 4 cited test files do not yet exist (`tests/unit/test_reflection_runner.py`, `tests/unit/test_agent_session_scheduler.py`, `tests/integration/test_reflections_yaml.py`, `tests/integration/test_dashboard_reflections.py`) — likely intentional new files; `ui/routes/reflections.py` cited but missing — concerning; `config/reflections.yaml` cited but missing (it's a symlink, command output may have been read-time race) |
| Prerequisites met | FAIL | Issue #1292 (cited as hard precondition) is OPEN — verified via `gh issue view 1292 --json state` returning "OPEN". Per the plan's own sequencing rule (lines 64-81), build kickoff is blocked. |
| Cross-references | PASS | Every Success Criterion maps to a task; No-Gos do not appear as planned work; Rabbit Holes match No-Gos in spirit |

The `ui/routes/reflections.py` reference at line 808 ("Caller signatures stay the same; `ui/routes/reflections.py` is unchanged") cites a path that does not exist on the worktree branch. Likely the actual route file is named differently (e.g., `ui/routes/reflection_routes.py` or `ui/app.py`) — confirm before build. The plan's invariant "callers unchanged" cannot be verified if the caller cannot be found.

## Verdict

**NEEDS REVISION** — 3 BLOCKERS must be resolved before build:

1. **B1** (model name collision): rename `ReflectionRun` to a non-colliding identifier, document the prior #748 deletion in Prior Art
2. **B2** (`mark_completed` API contract): specify the new contract (which fields land where, one save or two, atomicity story)
3. **B3** (sidecar TTL + worker drain): add `Meta.ttl = 14d` to the pending_clear sidecar, add worker-startup drain hook, add Race Condition entry covering the stuck-detection-not-yet-fired window

The 6 CONCERNs (C1-C6) are not blocking but each carries a concrete Implementation Note that should be embedded in the plan during the revision pass — particularly C3 (cost field-name mapping) and C5 (Task 9 stale line numbers), which would silently corrupt build output if left unaddressed.

The 3 NITs are cosmetic and can be folded in opportunistically.

The plan is well-researched and the cycle-3 refresh has clearly addressed prior-cycle blockers — the new blockers surface from deeper inspection (model-name archaeology, contract specificity, sidecar lifecycle), not from regressions.

**Precondition for any build kickoff**: Issue #1292 must close (or its Step A items must merge), per the plan's own sequencing rule. As of this critique, #1292 is OPEN.

---

## Success Criteria

Not applicable — this is a critique report, not a plan. Success criteria are defined in the parent plan at `docs/plans/unify-recurring-tasks-into-reflections.md`. This document evaluates that plan; it does not propose implementation work of its own.

## Update System

Not applicable — this is a critique report, not a plan. The parent plan's `## Update System` section at `docs/plans/unify-recurring-tasks-into-reflections.md` covers `/update` skill changes. No update-system changes follow from this critique directly; the parent plan must address the BLOCKERs in its next revision cycle.

## Agent Integration

Not applicable — this is a critique report, not a plan. The parent plan's `## Agent Integration` section covers MCP server registration and CLI surfaces. No agent integration follows from this critique; the BLOCKERs surface contract gaps in the parent plan that the next revision cycle must close.

## Test Impact

No existing tests affected — this is a critique report, not a plan. It evaluates the parent plan at `docs/plans/unify-recurring-tasks-into-reflections.md` and produces no code or test changes of its own. The parent plan's `## Test Impact` section already enumerates the affected tests; the BLOCKERs in this critique recommend additions to that list (specifically: a worker-startup sidecar drain test for B3, and a field-name mapping test for C3) which the next revision cycle of the parent plan should incorporate.

## Documentation

No documentation changes needed — this is a critique report itself, archived under `docs/plans/critiques/` for historical record. The parent plan's `## Documentation` section enumerates the doc updates required when the unification ships; this critique does not introduce new doc requirements beyond what the parent plan already commits to.
