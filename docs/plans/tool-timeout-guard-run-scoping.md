---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-11
tracking: https://github.com/tomcounsell/ai/issues/2002
last_comment_id: none
revision_applied: true
---

# Tool-timeout guard run-scoping (epoch-scope current_tool_name/last_tool_use_at)

## Problem

`agent/session_health.py`'s per-tool timeout sub-loop can fire a spurious "tool
timeout" recovery on a resumed session that has not actually done anything wrong
in its new run.

**Current behavior:**
`_check_tool_timeout()` reads `current_tool_name` and `last_tool_use_at` off a
`running` session row and decides the session is wedged inside a single tool
call. It never compares `last_tool_use_at` to the current run's start anchor.
These two fields are only cleared on one requeue path — the
`reason_kind == "tool_timeout"` branch (`agent/session_health.py:2630-2632`).
Every other requeue path (worker-startup recovery, and the
`no_progress`/`worker_dead` branches of `_apply_recovery_transition`) leaves
both fields set. So a session that crashes or is recovered mid-tool-call via any
of those other paths, then resumes, still carries the prior run's
`current_tool_name`/`last_tool_use_at`. On the first 30s tick after resume —
before the new run takes its first turn — `_check_tool_timeout()` reads that
stale pair and concludes the new run is wedged in a tool it never started.

**Desired outcome:**
`_check_tool_timeout()` only treats `current_tool_name`/`last_tool_use_at` as
describing the current run when `last_tool_use_at >= (started_at or created_at)`.
When the pair is stale (older than the run's start anchor), the tick skips the
tool-timeout evaluation instead of firing on stale data. Legacy rows with no
anchor at all preserve today's always-evaluate behavior — the exact fallback
#1979 chose for `_delivery_belongs_to_current_run`.

## Freshness Check

**Baseline commit:** `711b26f2db2cd1de741c42c772fa42d0f3bdac73`
**Issue filed at:** 2026-07-10T06:37:29Z
**Disposition:** Minor drift

**File:line references re-verified (against baseline):**
- `agent/session_health.py:4074` (issue: unguarded read) — drifted. The
  unguarded first read is now the `_check_tool_timeout(entry)` call at
  **`4098`** (with the fresh re-read `recheck` at `4120`), inside
  `_agent_session_tool_timeout_check` (`4019`). Claim holds.
- `agent/session_health.py:2621-2623` (issue: only clearing branch) — drifted to
  **`2630-2632`** (`entry.current_tool_name = None` / `entry.last_tool_use_at =
  None` under `reason_kind == "tool_timeout"`), with the OOM/requeue field lists
  at `2641-2642` and `2659-2660`. Claim holds.
- `_check_tool_timeout` — now at **`458-483`**; pure function, no epoch
  comparison. Bug confirmed present.
- `_delivery_belongs_to_current_run` (precedent) — present at **`298-311`**.
- `_recover_interrupted_agent_sessions_startup` at `552`;
  `_apply_recovery_transition` at `2129` — both leave the two fields untouched
  on their requeue paths. Confirmed.
- `_ts()` helper at `285-295` — reusable for the anchor comparison.

**Cited sibling issues/PRs re-checked:**
- #1614 — closed; established the sticky-field freshness-gate pattern
  (`NO_OUTPUT_BUDGET_SECONDS`) and asked future work to repeat the audit.
- #1979 — closed 2026-07-10; fixed by **PR #2006** (merged 2026-07-10T07:41),
  which added `_delivery_belongs_to_current_run`. This landed AFTER #2002 was
  filed, so the exact precedent helper now exists on main — good.

**Commits on main since issue was filed (touching `agent/session_health.py`):**
- `ffed9ba0` "Resilience hygiene sweep" (#2004/#2011) — irrelevant to the root
  cause; only shifted line numbers (accounts for the drift above). It did not
  add any epoch guard to `_check_tool_timeout`.

**Active plans in `docs/plans/` overlapping this area:** none.

**Notes:** Root cause unchanged; only line numbers moved. Corrected pointers are
carried into Technical Approach below.

## Prior Art

- **Issue #1614 / its PR**: Fixed the first instance of this sticky-field class
  (`turn_count`, `log_path`, `claude_session_uuid`) by gating reads behind a
  freshness window. Succeeded. Established the "re-audit this file whenever a
  related fix touches it" convention that surfaced #2002.
- **Issue #1979 / PR #2006**: Fixed the same class for `response_delivered_at`
  via a per-run epoch comparison (`_delivery_belongs_to_current_run`) against
  `started_at`/`created_at`, with a no-anchor legacy fallback. Merged and
  present on main. This is the direct template for #2002 — same file, same
  shape, same fallback choice.
- **Issue #1762 / its PR**: Added the `reason_kind == "tool_timeout"` clearing
  branch (`2630-2632`) that resets `current_tool_name`/`last_tool_use_at` — the
  single path that clears these fields today. It fixed clearing on the
  tool-timeout requeue only; it did not cover the other requeue paths, which is
  precisely the gap #2002 closes at read time.

No prior attempt tried to epoch-scope the tool-timeout fields specifically, so
there is no failed fix to analyze.

## Data Flow

1. **PreToolUse hook** stamps `current_tool_name` + `last_tool_use_at` on the
   `AgentSession` row when a tool call begins; **PostToolUse** clears
   `current_tool_name` when it returns.
2. **Crash / recovery mid-tool-call** via a non-tool-timeout path
   (`_recover_interrupted_agent_sessions_startup`, or `no_progress`/`worker_dead`
   in `_apply_recovery_transition`) requeues the row `running -> pending`
   **without** clearing the two fields — they stay describing the prior run.
3. **Resume**: worker picks the session back up, transitions `pending ->
   running`, and stamps a fresh `started_at` for the new run. This re-stamp is
   the load-bearing premise of the whole fix and is verified in code: both
   pickup paths run `chosen.started_at = datetime.now(tz=UTC)`
   (`agent/session_pickup.py:463` and `:611`) immediately before the
   `pending -> running` `transition_status`, so a resumed run's `started_at` is
   always newer than a stale prior-run `last_tool_use_at`. The stale
   `current_tool_name`/`last_tool_use_at` are still present.
4. **30s tool-timeout tick** (`_agent_session_tool_timeout_check`) reads the
   stale pair via `_check_tool_timeout(entry)` at `4098` **before the new run
   has taken its first turn**, sees an old `last_tool_use_at` far past budget,
   and returns `(tier, reason)` → a spurious recovery + steering message.
5. **Fix inserts an epoch gate** at step 4: if `last_tool_use_at < (started_at or
   created_at)`, the pair is stale → `_check_tool_timeout` returns `None`, the
   tick skips this session, and no false recovery fires. Once the resumed run
   makes a real tool call, PreToolUse re-stamps `last_tool_use_at` fresh (>=
   anchor) and legitimate tool-timeout detection resumes normally.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

One-file change mirroring an already-merged precedent (#1979). The cost is
alignment and review, not coding time.

## Prerequisites

No prerequisites — this work has no external dependencies. It is a pure-Python
change to one module plus unit tests, runnable with the repo's existing venv.

## Solution

### Key Elements

- **Epoch gate in `_check_tool_timeout`**: teach the pure function to treat
  `current_tool_name`/`last_tool_use_at` as valid only when the timestamp falls
  at or after the run's start anchor.
- **No-anchor legacy fallback**: rows with neither `started_at` nor `created_at`
  keep today's always-evaluate behavior (byte-for-byte the choice
  `_delivery_belongs_to_current_run` made — `if anchor is None: return True`).
- **No new fields, no schema change, no new clearing paths**: the gate lives at
  the read site, so every requeue path is covered at once without touching each
  transition branch.

### Flow

Tool-timeout tick reads a running session → `_check_tool_timeout(entry)` →
**epoch gate**: `last_tool_use_at >= (started_at or created_at)`? → if stale,
return `None` (skip, no recovery) → if fresh (or no anchor), evaluate tier
budget as today → wedged past budget returns `(tier, reason)` → recovery fires.

### Technical Approach

- Add the epoch comparison inside `_check_tool_timeout` (`agent/session_health.py:458`),
  after the existing `last_at` type check and before the budget comparison. Reuse
  the `_ts()` helper and the same anchor expression as the precedent:
  `anchor = _ts(started_at) or _ts(created_at)`; if `anchor is not None and
  _ts(last_tool_use_at) < anchor`, `return None`. Keeping it inline in the pure
  function means both call sites (`4098` initial read and `4120` fresh re-read)
  are covered automatically, and the change stays side-effect-free.
- Optionally factor a tiny named helper `_tool_use_belongs_to_current_run(entry)`
  paralleling `_delivery_belongs_to_current_run` for symmetry/readability. Either
  shape is acceptable; the inline version is the smallest diff. Pick one; do not
  ship both.
- Do NOT touch the requeue/clearing branches (`2630-2632`, `2641-2642`,
  `2659-2660`). The read-site gate subsumes them for the false-positive concern;
  the existing clearing branch stays as-is (it is still correct on its own path).
- Keep the fallback identical to #1979: missing/garbage timestamp or missing
  anchor must not silently start firing or silently stop firing beyond today's
  behavior. Boundary `last_tool_use_at == anchor` counts as current-run (fire if
  over budget), matching `_delivery_belongs_to_current_run`'s `>=`.
- **Stale-skip breadcrumb (keep the pure function pure).** The stale path returns
  the same bare `None` as "no tool in flight" and "under budget", so a health
  check that goes dark on a `started_at` regression would leave no trace. Do NOT
  add logging inside `_check_tool_timeout` (it must stay side-effect-free). At the
  caller near `agent/session_health.py:4098`, when `check is None` AND
  `entry.current_tool_name` is set AND the pair is stale
  (`anchor = _ts(started_at) or _ts(created_at)` is not `None` and
  `_ts(last_tool_use_at) < anchor`), emit exactly one
  `logger.debug("[session-health] tool-timeout stale-skip for %s (last_tool_use_at %s < anchor %s)", ...)`.
  Reuse the same `_ts()` anchor expression as the gate so the caller and the pure
  function cannot drift. This is a debug breadcrumb only — it changes no control
  flow (the `continue` after `check is None` still fires).

## Failure Path Test Strategy

### Exception Handling Coverage
- `_check_tool_timeout` is a pure function with no `except` blocks; the new code
  is a comparison, not an I/O call. No exception handlers in scope of the change.
- The caller `_agent_session_tool_timeout_check` already wraps per-session work
  in try/except and re-reads on a fresh query; the change adds no new failure
  surface there. State "No exception handlers in scope" for the modified function.

### Empty/Invalid Input Handling
- [ ] Test `last_tool_use_at` present but `started_at`/`created_at` both `None`
  (no-anchor legacy row) → evaluation proceeds exactly as today (fires over
  budget).
- [ ] Test garbage/non-datetime anchor values → `_ts()` returns `None` → treated
  as no-anchor legacy → proceeds (never crashes).
- [ ] `last_tool_use_at is None` and `current_tool_name` empty cases already
  return `None`; confirm still `None` after the change (regression guard).

### Error State Rendering
- No user-visible output. The observable effect is the absence of a spurious
  recovery/steering event; asserted via `_check_tool_timeout` returning `None`
  on a stale-pair entry and via the sub-loop not calling
  `_apply_recovery_transition` for that entry.

## Test Impact

- [ ] `tests/unit/test_session_health_tool_timeout.py` — UPDATE (additive only):
  add epoch-scoping cases. The existing `_entry()` builder (line 118) creates a
  `SimpleNamespace` with **no** `started_at`/`created_at`, so under the
  "no-anchor ⇒ evaluate" fallback every existing firing/non-firing test stays
  green unmodified. Add a sibling builder (e.g. `_entry_anchored(...)`) that sets
  `started_at`/`created_at`, and add cases: (a) stale `last_tool_use_at` before
  anchor over budget → `None` (the bug); (b) fresh `last_tool_use_at` at/after
  anchor over budget → fires; (c) boundary equal timestamps → fires; (d)
  no-anchor over budget → fires (legacy preserved); (e) **production-shape
  resume case** mirroring `agent/session_pickup.py:463`/`:611`: `started_at =
  datetime.now(tz=UTC)`, `last_tool_use_at` = a prior-run timestamp far past
  budget → `_check_tool_timeout` returns `None` (proves the fix is not a no-op
  under the exact state a real resume produces).
- [ ] `tests/integration/test_session_health_tool_timeout.py` — UPDATE (additive,
  optional): add one end-to-end case proving the sub-loop does NOT call
  `_apply_recovery_transition` for a resumed session whose stale
  `last_tool_use_at` predates a fresh `started_at`, mirroring
  `test_delivery_guard_resume_epoch.py::TestApplyRecoveryTransitionDeliveryGuard`.
- Template reference (not modified): `tests/unit/test_delivery_guard_resume_epoch.py`
  is the shape to copy for the new pure-helper cases.

No existing test cases require DELETE or REPLACE — the change is additive and the
no-anchor fallback preserves all current assertions.

## Rabbit Holes

- **Re-plumbing the requeue/clearing branches** to clear
  `current_tool_name`/`last_tool_use_at` on every path. Tempting for
  "completeness," but it multiplies the diff across three transition branches and
  is unnecessary — the read-site epoch gate already neutralizes stale reads
  everywhere at once. Stay at the read site.
- **Generalizing an epoch-scoping abstraction** across all sticky fields
  (delivery + tool-timeout + future). Out of scope; match the existing per-field
  helper style rather than inventing a framework.
- **Touching tier budgets or the 30s loop cadence.** The bug is staleness, not
  tuning. Leave `TOOL_TIMEOUT_*` constants and `TOOL_TIMEOUT_LOOP_INTERVAL`
  alone.

## Risks

### Risk 1: Legacy rows lack a start anchor and behavior silently changes
**Impact:** If the fallback were "no anchor ⇒ skip," genuine wedges on
older/anchor-less rows would stop being detected (a silent regression in the
opposite direction).
**Mitigation:** Match #1979 exactly — no anchor ⇒ evaluate (fire). Explicit unit
case (d) locks this in. `started_at`/`created_at` are populated for all
currently-created rows, so the fallback is a narrow legacy safety net, not the
common path.

### Risk 2: Clock skew / naive-vs-aware datetime comparison
**Impact:** A naive `last_tool_use_at` or anchor could raise on comparison or
mis-order.
**Mitigation:** Route both operands through `_ts()` (already normalizes naive →
UTC and datetime/float → float timestamp), exactly as
`_delivery_belongs_to_current_run` does. Add a unit case with a naive
`last_tool_use_at` (the file already tests naive handling at
`test_check_tool_timeout_handles_naive_datetime`).

## Race Conditions

### Race 1: PostToolUse fires between the stale read and the transition
**Location:** `agent/session_health.py:4098-4128`
**Trigger:** The resumed run legitimately starts (and finishes) a tool call in
the same tick window the sub-loop is evaluating.
**Data prerequisite:** `last_tool_use_at` must reflect the current run before the
budget verdict is trusted.
**State prerequisite:** The row read at transition time must be the freshest.
**Mitigation:** Already handled by the existing re-read (`fresh =
AgentSession.get_by_id(...)`) and `recheck = _check_tool_timeout(fresh)` at
`4108-4128`. The new epoch gate lives inside `_check_tool_timeout`, so the
fresh re-read gets the same epoch protection for free — a run that re-stamps
`last_tool_use_at >= anchor` between reads now passes the gate and is correctly
treated as current-run. No new race introduced; the fix strictly reduces
false positives.

## No-Gos (Out of Scope)

Nothing deferred — every relevant item is in scope for this plan. The change is a
single read-site epoch gate plus additive tests; there is no external, ordered,
destructive, or separately-tracked follow-up.

## Update System

No update system changes required — this is a pure-internal change to one
worker module. No new dependencies, no config files, no Popoto schema change (no
new fields), so `scripts/update/run.py` and `scripts/update/migrations.py` are
untouched.

## Agent Integration

No agent integration required — this is a worker-internal health-check change. It
touches neither the Telegram bridge, MCP servers (`mcp_servers/` + `.mcp.json`),
nor any CLI entry point. The behavior is exercised entirely by the worker's
background tool-timeout sub-loop.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/session-lifecycle.md` (or the closest existing
  session-health/recovery doc) with a one-line note that the tool-timeout guard
  is epoch-scoped against the run start anchor, alongside the delivery-guard
  note from #1979. If no single canonical doc covers the tool-timeout sub-loop,
  add the note where #1979's delivery-guard epoch scoping is documented so the
  two sticky-field fixes are described together.

### Inline Documentation
- [ ] Docstring update on `_check_tool_timeout` noting the epoch-scoping guard
  and the no-anchor legacy fallback (mirror the wording style of
  `_delivery_belongs_to_current_run`'s docstring).
- [ ] Inline comment at the gate referencing #2002 and the #1979 precedent.

No new feature doc is warranted — this is a bug fix that extends an existing,
already-documented pattern.

## Success Criteria

- [ ] `_check_tool_timeout` returns `None` for an entry whose `last_tool_use_at`
  predates its `started_at`/`created_at` anchor even when over budget (the bug),
  including the production-shape resume case (`started_at = now`,
  `last_tool_use_at` = prior-run over budget) mirroring
  `agent/session_pickup.py:463`/`:611`.
- [ ] `_check_tool_timeout` still fires for a fresh (>= anchor) over-budget entry
  and for a no-anchor over-budget entry (legacy preserved).
- [ ] Boundary case `last_tool_use_at == anchor` fires (current-run, `>=`).
- [ ] All pre-existing `test_session_health_tool_timeout.py` cases pass unchanged.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).

## Team Orchestration

### Team Members

- **Builder (session-health)**
  - Name: sh-builder
  - Role: Add the epoch gate to `_check_tool_timeout`, the caller-side stale-skip
    debug breadcrumb (~`4098`), and the additive unit (and optional integration)
    tests.
  - Agent Type: builder
  - Domain: async/concurrency (worker health loop). Paste the async/state-freshness
    rules from `DOMAIN_FRAMING.md` into the assignment.
  - Resume: true

- **Validator (session-health)**
  - Name: sh-validator
  - Role: Verify the gate matches the #1979 fallback semantics and all criteria hold.
  - Agent Type: validator
  - Resume: true

### Step by Step Tasks

### 1. Implement epoch gate + tests
- **Task ID**: build-epoch-gate
- **Depends On**: none
- **Validates**: `tests/unit/test_session_health_tool_timeout.py`,
  `tests/integration/test_session_health_tool_timeout.py`
- **Informed By**: precedent `_delivery_belongs_to_current_run`
  (`agent/session_health.py:298-311`) and
  `tests/unit/test_delivery_guard_resume_epoch.py`
- **Assigned To**: sh-builder
- **Agent Type**: builder
- **Parallel**: false
- Add the epoch comparison inside `_check_tool_timeout` (reuse `_ts()`; anchor =
  `_ts(started_at) or _ts(created_at)`; stale ⇒ `return None`; no-anchor ⇒ proceed).
- Add unit cases (a)-(e) from Test Impact (including the production-shape resume
  case) plus a naive-datetime anchored case.
- Add the optional integration case asserting no `_apply_recovery_transition`
  call for a stale-pair resumed session.
- Add the stale-skip `logger.debug` breadcrumb at the caller (~`4098`), gated on
  `check is None` AND `current_tool_name` set AND stale pair — reusing the same
  `_ts()` anchor expression as the gate. Keep `_check_tool_timeout` side-effect-free.
- Update the `_check_tool_timeout` docstring and add the #2002/#1979 inline comment.

### 2. Validate
- **Task ID**: validate-epoch-gate
- **Depends On**: build-epoch-gate
- **Assigned To**: sh-validator
- **Agent Type**: validator
- **Parallel**: false
- Confirm the four Success Criteria bullets on `_check_tool_timeout` behavior.
- Run `pytest tests/unit/test_session_health_tool_timeout.py
  tests/unit/test_delivery_guard_resume_epoch.py -q` and the integration test.
- Confirm no requeue/clearing branch was modified (`git diff` shows only the
  read-site function + tests + docstring/doc note).
- Report pass/fail.

### 3. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-epoch-gate
- **Assigned To**: sh-builder (documentarian pass)
- **Agent Type**: documentarian
- **Parallel**: false
- Add the epoch-scoping note next to the #1979 delivery-guard note in the
  session-health/lifecycle doc.

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: sh-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the full Verification table below.
- Confirm all Success Criteria (including docs) are met.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests pass | `pytest tests/unit/test_session_health_tool_timeout.py -q` | exit code 0 |
| Precedent tests still pass | `pytest tests/unit/test_delivery_guard_resume_epoch.py -q` | exit code 0 |
| Epoch gate present (behavioral) | `pytest tests/unit/test_session_health_tool_timeout.py -q -k "stale or anchor"` | exit code 0 |
| Epoch gate present (delta) | `git diff main -- agent/session_health.py \| grep -c "^+.*started_at"` | output > 0 (new gate/comparison line added; file already has 30 `started_at` on main, so a whole-file count would be blind) |
| Read-site anchor reuse (delta) | `git diff main -- agent/session_health.py \| grep -c "^+.*_ts("` | output > 0 |
| Format clean | `python -m ruff format --check agent/session_health.py` | exit code 0 |
| Lint clean | `python -m ruff check agent/session_health.py` | exit code 0 |
| Clearing branch untouched | `git diff main -- agent/session_health.py \| grep -c "current_tool_name = None"` | match count == 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room, FULL depth). Verdict: READY TO BUILD (with concerns). -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Risk & Robustness (Skeptic) | Data Flow step 3's load-bearing premise (resume re-stamps a fresh `started_at`) is stated without a source pointer. Verified TRUE during critique — the fix is NOT a no-op. | Cite the re-stamp source in Data Flow; add a production-shape unit case | Re-stamp confirmed at `agent/session_pickup.py:463` and `:611` — both pickup paths run `chosen.started_at = datetime.now(tz=UTC)` immediately before the pending→running `transition_status`, so a resumed run's `started_at` is always newer than a stale prior-run `last_tool_use_at`. Add a unit case mirroring production: `started_at`=now, `last_tool_use_at`=prior-run over budget → `_check_tool_timeout` returns `None`. |
| CONCERN | Risk & Robustness (Operator) | The stale-suppression path collapses into the same bare `None` as "no tool in flight" / "under budget" (`session_health.py:458-483`), so a health check that goes dark on a `started_at` regression leaves no breadcrumb. | Add a debug log at the caller (not in the pure function) on the stale-skip path | Keep `_check_tool_timeout` pure. At the caller near `session_health.py:4098`, when `check is None` AND `current_tool_name` is set AND `_ts(last_tool_use_at) < (_ts(started_at) or _ts(created_at))`, emit one `logger.debug(...)`. Reuse the same `_ts()` anchor expression to avoid drift. |
| CONCERN | Scope & Value + History & Consistency (agreement) | Verification row "Epoch gate present" (`grep -n "started_at" ... \| grep -i "tool"`) false-fails: no single code line carries both `started_at` and `tool` in a correct inline gate (anchor line has `started_at` not `tool`; comparison line has `tool` not `started_at`). Passes only if a comment incidentally holds both. | Replace the grep with a delta- or behavior-sensitive check | Use `grep -c "started_at" agent/session_health.py` expecting `>1` (precedent supplies one occurrence on main, the gate adds a second) — survives the inline-vs-named-helper choice in Open Question 1. Or gate behaviorally: `pytest tests/unit/test_session_health_tool_timeout.py -q -k "stale or anchor"`. |
| NIT | History & Consistency | Verification row "Read-site anchor reuse" (`grep -c "_ts(" ...` expecting `>1`) already passes on baseline — `_delivery_belongs_to_current_run` alone has three `_ts(` calls — so it verifies nothing about the new gate. | Make it delta-sensitive | `git diff main -- agent/session_health.py \| grep -c "^+.*_ts("` expecting `>0`. |
| NIT | Scope & Value | Both Open Questions are already resolved in the plan body (Q1 at Technical Approach: inline = smallest diff; Q2 at Documentation: co-locate with #1979 note), yet sit under "Open Questions" reading as unresolved. | Restate each as "Resolved: ..." or delete the section | — |

### Revision Applied (2026-07-11)

All five findings addressed:
- **Skeptic CONCERN** — Data Flow step 3 now cites the re-stamp source
  (`agent/session_pickup.py:463`/`:611`, verified in code); Test Impact and
  Success Criteria add production-shape resume unit case (e).
- **Operator CONCERN** — Technical Approach + build task add a caller-side
  (`~4098`) `logger.debug` stale-skip breadcrumb, gated on `check is None` AND
  `current_tool_name` set AND stale pair, reusing the gate's `_ts()` anchor
  expression; `_check_tool_timeout` stays pure.
- **Scope+History CONCERN** — the unmatchable `grep -n "started_at" ... | grep -i "tool"`
  row is replaced by a behavioral pytest (`-k "stale or anchor"`) and a genuinely
  diff-sensitive `git diff main ... grep -c "^+.*started_at"` (the whole-file
  count is blind: main already has 30 `started_at` occurrences).
- **History NIT** — "Read-site anchor reuse" row is now delta-sensitive
  (`git diff main ... grep -c "^+.*_ts("`).
- **Scope NIT** — resolved Open Questions section deleted. Resolutions live in the
  plan body (Q1: inline gate = smallest diff, Technical Approach; Q2: co-locate the
  epoch-scoping note with the #1979 delivery-guard note, Documentation).
