---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-06-03
tracking: https://github.com/tomcounsell/ai/issues/1558
last_comment_id:
---

# sdlc-tool state subcommands silently no-op when invoked outside /sdlc

## Problem

SDLC pipeline state (which stages are done, critique/review verdicts, routing meta like `revision_applied`) lives on a **PM AgentSession's `stage_states`** field in Redis — not in the plan file or git. The `sdlc-tool` CLI family (`stage-query`, `verdict record/get`, `meta-set`, `stage-marker`, `next-skill`) reads and writes that state. In a direct Claude Code CLI session there is no PM AgentSession record unless something creates one.

The `session-ensure` step that *creates* a local PM session is wired only into `/sdlc` Step 1.5 (`.claude/skills-global/sdlc/SKILL.md`). Any path that doesn't pass through `/sdlc` first — a direct `sdlc-tool` call, or an individual `/do-*` skill — bypasses the ensure. The subcommands then resolve their session via a resolver that returns `None`, so **every read returns empty defaults and every write is a silent no-op**.

**Current behavior:**

In a direct Claude Code session, with the plan committed to `main` and `revision_applied: true` in its frontmatter:

```
sdlc-tool stage-query  --issue-number 1546   → {"stages": {}, "_meta": {…all null/false…}}
sdlc-tool verdict record --stage CRITIQUE --verdict "READY TO BUILD" --issue-number 1546 → {}   (did not persist)
sdlc-tool verdict get    --stage CRITIQUE --issue-number 1546 → {}   (still empty)
sdlc-tool next-skill     --issue-number 1546 → {"skill":"/do-plan","reason":"Cannot build without a plan"}
```

There is no error. Reads look like "nothing has happened yet" and writes appear to succeed but persist nothing. The router never reads plan frontmatter — only `session.stage_states` — so the on-disk `revision_applied: true` is invisible, producing a phantom "cannot build" loop.

**Desired outcome:**

It is impossible to touch SDLC state sessionless. Any entry point that reads or writes pipeline state ensures a PM session exists first, so state has a home regardless of how the pipeline is driven. The same `sdlc-tool` command sequence above, run in a direct Claude Code session, creates the PM session on first touch, persists the verdict, and `next-skill` reflects the recorded state.

## Freshness Check

**Baseline commit:** `e13b57cac3f5f174510005287a9bd87f50628244`
**Issue filed at:** 2026-06-03T09:44:37Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `tools/_sdlc_utils.py:75` — `find_session()` resolves `VALOR_SESSION_ID`→`AGENT_SESSION_ID`→`find_session_by_issue(issue_number)` and returns `None` on miss with no auto-ensure. **Still holds.**
- `tools/sdlc_session_ensure.py::ensure_session()` — idempotent, bridge-dedup ensure path with env short-circuit, PM-type gate, terminal-status gate, idempotent create-by-id, `issue_number < 1` guard. **Still holds — this is the reuse target.**
- `.claude/skills-global/sdlc/SKILL.md` Step 1.5 — wires `session-ensure` only into the `/sdlc` entry point. **Still holds.**
- Resolution is duplicated across callers (not centralized): `sdlc_verdict.py:85` aliases the shared `_sdlc_utils.find_session`; `sdlc_meta_set.py:86` and `sdlc_stage_marker.py:51` define their own local `_find_session`; `sdlc_stage_query.py:53/74` has `_find_session_by_id`/`_find_session_by_issue`. **Four divergent resolvers — confirmed at plan time.**

**Cited sibling issues/PRs re-checked:**
- #941 / PR #951 — CLOSED/MERGED 2026-04-14. Fixed the same silent-no-op symptom but only for the `/sdlc` entry point. This issue is the surviving gap.
- #1147 / PR #1151 — CLOSED/MERGED 2026-04-24. Added the bridge-dedup ensure path to reuse so no duplicate sessions are created inside worker/bridge sessions.

**Commits on main since issue was filed (touching referenced files):** None. Issue filed today.

**Active plans in `docs/plans/` overlapping this area:** None.

**Notes:** The "observed on #1546" in the issue body is reproduction *context* (the operator was working in a session that mentioned #1546), not a claim about #1546's own scope. #1546 itself is an unrelated PoC issue.

## Prior Art

- **#941 / PR #951**: *"Local /sdlc sessions have no pipeline state tracking -- stage markers silently no-op"* — Added `tools/sdlc_session_ensure.py` and wired a `session-ensure` call into `/sdlc` Step 1.5. Fixed the symptom **only** for the `/sdlc` entry point. Direct `sdlc-tool` and individual `/do-*` callers still bypass it. This issue closes that gap.
- **#1147 / PR #1151**: *"sdlc_session_ensure creates zombie sdlc-local-{N} session when called from a bridge-initiated PM session"* — Added the env-var short-circuit and dedup logic to `ensure_session()` so it returns the live bridge PM session instead of spawning a `sdlc-local-{N}` duplicate. The behavior this plan reuses verbatim — auto-ensure at the resolver boundary inherits this dedup for free because it calls `ensure_session()`.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #951 (#941) | Created `ensure_session()`; wired one `session-ensure` call into `/sdlc` Step 1.5 | Fixed the *caller* (the skill), not the *resolver boundary*. Every subcommand still resolves through a `find_session`-style helper that returns `None` sessionless. Any path not routed through `/sdlc` Step 1.5 — direct `sdlc-tool` calls, individual `/do-*` skills — bypasses the ensure and silently no-ops. |

**Root cause pattern:** The ensure was placed at *one* entry point (the orchestrating skill) rather than at the *shared chokepoint* every subcommand funnels through (the resolver). Fixing it skill-by-skill leaves a combinatorial gap. The durable fix moves the ensure into the resolver so no caller can bypass it.

## Architectural Impact

- **New dependencies:** None new. `_sdlc_utils.py` already imports `models.agent_session`; the auto-ensure path imports `tools.sdlc_session_ensure.ensure_session` lazily (inside the function) to avoid any import-time cycle.
- **Interface changes:** Add an `ensure: bool = False` parameter to the existing `find_session(session_id, issue_number)` in `_sdlc_utils.py`. Default `ensure=False` preserves today's pure read-only semantics, so existing read-only callers are unaffected with zero code change. State-touching callers pass `ensure=True` explicitly — the `True` at the call site is what makes the create-side-effect visible.
- **Coupling:** Reduces coupling — collapses four divergent resolvers down to the shared `_sdlc_utils` resolver, and centralizes the ensure decision in one place.
- **Data ownership:** Unchanged. `stage_states` still lives on the PM AgentSession; auto-ensure only guarantees the record exists before a write lands on it.
- **Reversibility:** High. The parameter is additive with a safe default; reverting means dropping the `ensure=True` arg at the four call sites and removing the branch.

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1 — RESOLVED. Open questions answered: (1) inline an `ensure=True` flag into `find_session` rather than a separate function; (2) keep SKILL.md Step 1.5's `session-ensure` call but tag it findable for later cleanup.
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies. It modifies internal Python resolvers and reuses an existing idempotent ensure path.

## Solution

### Key Elements

- **`find_session(session_id, issue_number, ensure=False)`** (extended, in `tools/_sdlc_utils.py`): with `ensure=False` (default) it behaves exactly as today — pure lookup, returns the session or `None`. With `ensure=True`, when no existing PM session is found AND an ensure is appropriate (valid `issue_number >= 1`, or a session-id env var is present), it calls `ensure_session()` to create/dedup one, then re-resolves and returns it. Auto-creating on a *read* like `stage-query` is an intended, documented side effect — gated behind the explicit `ensure=True` at the call site.
- **Default-off behavior:** `ensure=False` keeps pure-lookup semantics for any caller that genuinely wants "does a session exist?" without creating one. Existing read-only callers need no edits.
- **Caller convergence:** every state-touching subcommand resolves through `find_session(..., ensure=True)` instead of its bespoke local resolver. The four divergent resolvers (`sdlc_meta_set._find_session`, `sdlc_stage_marker._find_session`, `sdlc_stage_query._find_session_by_id/_by_issue`, and `sdlc_verdict`'s `find_session` alias) collapse onto the shared one.

### Flow

Direct `sdlc-tool verdict record --stage CRITIQUE --issue-number 1558` → subcommand calls `find_session(None, 1558, ensure=True)` → no env session, no existing PM session for #1558 → `ensure_session(1558)` creates `sdlc-local-1558` (or dedups onto a live bridge PM session) → re-resolve returns the PM session → `update_stage_states()` writes the verdict → **persisted**. Subsequent `verdict get` / `next-skill` read the same record.

### Technical Approach

- **Guards inside the `ensure=True` branch of `find_session` (mirror the existing `ensure_session` contract so we don't re-derive them):**
  - Resolve `session_id` arg → `VALOR_SESSION_ID` → `AGENT_SESSION_ID` first (the existing order). If that resolves a live PM session, return it without ensuring — this happens before the `ensure` branch is even reached, since it is the existing lookup path.
  - Only attempt ensure when `issue_number is not None and issue_number >= 1`, OR a session-id env var is set. When neither holds, do NOT create — return `None` (e.g. a bare `sdlc-tool stage-query` with no `--issue-number` and no env still no-ops, which is correct: there is no issue context to attach state to). This is the "fail-by-returning-None loudly enough" answer to the issue's open question — we do not silently fabricate a sessionless session.
  - `ensure_session()` already enforces idempotency, PM-type gating, terminal-status gating, and bridge dedup (#1147). The `ensure=True` branch inherits all of it for free — do NOT reimplement.
- **Where the ensure lives:** an `ensure: bool = False` parameter on the existing `find_session`, not a separate function (per resolved Open Question 1). Default-off means the only callers that can trigger creation are the ones that opt in with `ensure=True` — and that explicit `True` argument at the call site is what makes the "this read can create a session" behavior visible. Mitigates the boolean-trap concern via (a) safe default, (b) docstring on the param, (c) the side effect being opt-in and grep-able (`grep -n 'ensure=True'`).
- **`revision_applied`:** no change needed. It is flipped by recording a CRITIQUE verdict via `verdict record`. Once `verdict record` resolves through `find_session(..., ensure=True)`, the write lands on a real session and `revision_applied` persists correctly. Auto-ensure + a working `verdict record` is sufficient; no new code path for `revision_applied`.
- **SKILL.md Step 1.5:** the explicit `session-ensure` call becomes redundant once auto-ensure is in the resolver, but it is harmless (idempotent). Per resolved Open Question 2, KEEP it (play-it-safe), but mark it findable for later cleanup: add an inline `<!-- REDUNDANT-AFTER-#1558: ... -->` comment in SKILL.md right at the call so a future grep (`grep -rn 'REDUNDANT-AFTER-#1558'`) surfaces it for removal once auto-ensure has proven itself in practice. Update the surrounding prose to note that auto-ensure now also guarantees a session for non-`/sdlc` callers, so the explicit step is belt-and-suspenders rather than the sole guarantee.
- **Failure semantics:** preserve the existing "never crash the calling skill" contract. `ensure_session()` returns `{}` on any failure (e.g. `ProjectKeyResolutionError`); the `ensure=True` branch treats an empty/failed ensure as "no session" and returns `None`, so the subcommand degrades to today's no-op behavior rather than raising. The improvement is that the *common* path (valid issue_number, resolvable project) now succeeds.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `ensure_session()` already wraps its body in `try/except` returning `{}`; the `ensure=True` branch must wrap its ensure call so an ensure failure yields `None`, not a raise. Add a test that monkeypatches `ensure_session` to raise and asserts `find_session(None, N, ensure=True)` returns `None` (no propagation).
- [ ] `find_session`'s existing `except Exception` blocks (lines 70, 93, 99) remain; no new bare `except: pass` introduced.

### Empty/Invalid Input Handling
- [ ] `find_session(None, None, ensure=True)` with no env vars → returns `None`, no session created (test asserts `AgentSession` count unchanged).
- [ ] `find_session(None, 0, ensure=True)` and `(None, -1, ensure=True)` → no ensure attempted (mirrors `ensure_session` and `find_session_by_issue` guards), returns `None`.
- [ ] `find_session(None, 1558, ensure=True)` with no existing session → creates `sdlc-local-1558`, returns it.
- [ ] `find_session(None, 1558)` (default `ensure=False`) with no existing session → returns `None`, no session created (proves default-off is preserved).

### Error State Rendering
- [ ] Subcommand-level: `sdlc-tool verdict record` against a resolvable issue persists and the JSON output reflects the recorded verdict (not `{}`). `verdict get` round-trips it.
- [ ] When ensure genuinely cannot resolve a project_key, the subcommand still prints valid JSON (`{}` or empty defaults) and exits 0 — no traceback to the caller.

## Test Impact

- [ ] `tests/unit/test_sdlc_utils.py` — UPDATE: add a `TestFindSessionEnsure` class covering the `ensure=True` guards (None/0/-1 → no create; valid issue → create; env session → return without ensure; ensure-raises → None) AND a `ensure=False` default case (valid issue, no session → returns `None`, no create — proves default-off). Existing `find_session` / `find_session_by_issue` tests are unaffected (the new param defaults to the old behavior).
- [ ] `tests/unit/test_sdlc_session_ensure.py` — UPDATE (if needed): confirm the `ensure=True` branch delegates to `ensure_session` and does not duplicate its guard logic. Likely no change to existing tests; add cross-reference assertions only if convenient.
- [ ] `tests/unit/test_sdlc_meta_set.py` — UPDATE: `sdlc_meta_set` now resolves through the shared resolver. Update any test that asserts a sessionless `meta-set` no-ops to instead assert it now persists when an issue_number is supplied (the behavior change this issue mandates). Keep the genuinely-sessionless (no issue, no env) no-op test.
- [ ] `tests/unit/test_sdlc_stage_marker.py` — UPDATE: same convergence; add a test that a sessionless-but-issue-numbered `stage-marker` now persists the marker.
- [ ] `tests/unit/test_sdlc_stage_query.py` — UPDATE: add a test that `query_enriched(issue_number=N)` with no pre-existing session now auto-creates and returns a session-backed payload (was empty defaults). Keep the no-issue/no-env empty-defaults test.
- [ ] `tests/unit/test_sdlc_verdict.py` — UPDATE: add a sessionless-but-issue-numbered `verdict record` → `verdict get` round-trip test proving the write persists.
- [ ] Add (NEW): `tests/integration/test_sdlc_sessionless_e2e.py` — drive the actual `sdlc-tool` subcommands (subprocess) for a throwaway issue number in a clean env (no `VALOR_SESSION_ID`), asserting record→get→next-skill reflects persisted state. Clean up the `sdlc-local-{N}` session in teardown via Popoto (`AgentSession.query.filter(...).delete()`), per Manual Testing Hygiene.

## Rabbit Holes

- **Refactoring `AgentSession`'s shape.** The issue explicitly says the schema is expected to shift before/around this work; do NOT lock the fix to the current field layout or attempt a schema migration. Fix the behavior at the resolver boundary only.
- **Reading plan frontmatter in the router.** Tempting to make `next-skill` consult `revision_applied: true` on disk as a fallback. Out of scope — the router's contract is "state lives on the session." Auto-ensure makes the session the single source of truth; do not add a second source.
- **Adding `ensure=True` defaults or auto-detecting "should I create?".** The flag defaults to `False` and is only flipped on by the four state-touching call sites, by hand. Do NOT make `ensure` default to `True`, and do NOT add heuristics that decide to create based on subcommand name or call context — the opt-in `ensure=True` argument is the entire contract. (Resolved Open Question 1 chose the inline flag over a separate function; the guard against the boolean-trap risk is the safe default + explicit opt-in, not a second function.)
- **Garbage-collecting `sdlc-local-{N}` sessions created by reads.** The existing `--kill-orphans` path in `sdlc_session_ensure.py` already reaps zombie `sdlc-local-*` PM sessions. Do not add new cleanup machinery.

## Risks

### Risk 1: Read operations now have a write side effect (session creation)
**Impact:** A bare `stage-query` could create a session unexpectedly, polluting the session list.
**Mitigation:** The `ensure=True` branch only creates when `issue_number >= 1` or a session-id env is present, and is only reached when a caller explicitly passes `ensure=True`. A `stage-query` with a real issue number *should* have a session — that's the whole point. The explicit `ensure=True` argument makes the side effect visible and grep-able at every call site. Existing `--kill-orphans` reaps any zombies.

### Risk 2: Duplicate session creation inside bridge/worker sessions
**Impact:** Auto-ensure could spawn a `sdlc-local-{N}` duplicate alongside the live bridge PM session.
**Mitigation:** `ensure_session()` already short-circuits on `VALOR_SESSION_ID`/`AGENT_SESSION_ID` to the live bridge PM session (#1147/#1151). The `ensure=True` branch calls `ensure_session()` and inherits this dedup verbatim. The new integration test runs in a clean env to prove the local-create path; an additional unit test asserts the env-set path returns the env session without creating.

### Risk 3: Behavior change breaks existing tests that assert sessionless no-op
**Impact:** Tests asserting "sessionless write returns `{}`" will now see persistence.
**Mitigation:** Test Impact section enumerates each affected test with a disposition. The genuinely-sessionless (no issue, no env) no-op remains valid and is kept; only the *issue-numbered* sessionless cases flip to persistence.

## Race Conditions

No race conditions identified. `ensure_session()` is idempotent (create-by-deterministic-id `sdlc-local-{N}` with a pre-create existence check) and all operations are synchronous single-process CLI invocations. Two concurrent `sdlc-tool` calls for the same issue could both attempt create, but the deterministic session_id and the idempotent create-by-id check mean the second resolves the first's record rather than duplicating — this is the existing #1147 contract, unchanged.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1546] Any change to the `AgentSession` schema/shape — the issue explicitly defers the schema shift to other work and asks the planner not to lock to the current layout.
- Nothing else deferred — every relevant resolver, caller, test, and the SKILL.md prose update is in scope for this plan.

## Update System

No update system changes required — this is a purely internal Python fix to the `sdlc-tool` resolver path. No new dependencies, config files, or migration steps. The `sdlc-tool` wrapper script and its subcommand list are unchanged; only the Python resolution logic the subcommands call is modified.

## Agent Integration

No new agent integration required — `sdlc-tool` is already exposed to the agent via its installed CLI entry point and is invoked through the Bash tool by the `/sdlc` and `/do-*` skills. This fix changes the *behavior* of existing subcommands (they now persist state when called sessionless-but-issue-numbered), not the surface. The new integration test (`tests/integration/test_sdlc_sessionless_e2e.py`) verifies the agent-facing CLI path end-to-end via subprocess.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/sdlc-tool-resolver.md` to document the `ensure=True` parameter on `find_session`: the resolver-boundary auto-ensure, the create-guards (issue_number >= 1 or env session), the opt-in read-with-side-effect semantics, the safe `ensure=False` default, and that it supersedes the per-skill `session-ensure` requirement for non-`/sdlc` callers.
- [ ] If `docs/features/README.md` indexes the resolver doc, confirm the entry still accurate (no new file).

### External Documentation Site
No external documentation site for this repo — no action.

### Inline Documentation
- [ ] Docstring on `find_session`'s `ensure` parameter stating the opt-in auto-create side effect, the guards, the safe default, and that it reuses `ensure_session`'s dedup.
- [ ] Update the `.claude/skills-global/sdlc/SKILL.md` Step 1.5 prose to note auto-ensure now covers non-`/sdlc` callers (the explicit call is belt-and-suspenders), and add an inline `<!-- REDUNDANT-AFTER-#1558: kept as belt-and-suspenders; remove once resolver auto-ensure is proven -->` comment at the `session-ensure` call so it is grep-findable for later cleanup.

## Success Criteria

- [ ] In a clean env (no `VALOR_SESSION_ID`/`AGENT_SESSION_ID`), `sdlc-tool verdict record --stage CRITIQUE --verdict "READY TO BUILD" --issue-number {N}` followed by `sdlc-tool verdict get --stage CRITIQUE --issue-number {N}` round-trips the verdict (no longer `{}`).
- [ ] `sdlc-tool stage-query --issue-number {N}` against a fresh issue auto-creates the PM session and returns a session-backed payload.
- [ ] `sdlc-tool next-skill --issue-number {N}` reflects recorded state instead of the phantom "cannot build" default after a verdict is recorded.
- [ ] A bare `sdlc-tool stage-query` with no `--issue-number` and no env var still no-ops (returns empty defaults, exits 0) — no fabricated session.
- [ ] The four divergent resolvers (`sdlc_meta_set`, `sdlc_stage_marker`, `sdlc_stage_query`, `sdlc_verdict`) all resolve through the shared `find_session(..., ensure=True)` for state-touching operations.
- [ ] No duplicate `sdlc-local-{N}` session is created when run inside a bridge/worker session with `VALOR_SESSION_ID` set (dedup preserved).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] `grep` confirms each state-touching subcommand calls `find_session(..., ensure=True)`.

## Team Orchestration

### Team Members

- **Builder (resolver)**
  - Name: resolver-builder
  - Role: Add the `ensure=True` flag to `find_session` in `_sdlc_utils.py`; converge the four callers onto it; update SKILL.md prose + cleanup marker.
  - Agent Type: builder
  - Resume: true

- **Validator (resolver)**
  - Name: resolver-validator
  - Role: Verify guards, dedup preservation, caller convergence, and the sessionless round-trip behavior.
  - Agent Type: validator
  - Resume: true

### Available Agent Types

See template list — only `builder`, `validator`, and `documentarian` are needed for this Small fix.

## Step by Step Tasks

### 1. Add the auto-ensuring resolver
- **Task ID**: build-resolver
- **Depends On**: none
- **Validates**: tests/unit/test_sdlc_utils.py
- **Assigned To**: resolver-builder
- **Agent Type**: builder
- **Parallel**: false
- Add an `ensure: bool = False` parameter to `find_session(session_id=None, issue_number=None, ensure=False)` in `tools/_sdlc_utils.py`: existing resolve order unchanged; if a live PM session is found, return it (works for both `ensure` values); otherwise, only when `ensure is True` AND (`issue_number >= 1` or a session-id env var is set), lazily import and call `tools.sdlc_session_ensure.ensure_session(issue_number, ...)`, then re-resolve and return; wrap the ensure in try/except so failures return `None`.
- `ensure=False` (default) path is byte-for-byte today's behavior — no existing caller changes.
- Docstring on the `ensure` param stating the opt-in create-side-effect, guards, safe default, and dedup reuse.

### 2. Converge callers onto the shared resolver
- **Task ID**: build-callers
- **Depends On**: build-resolver
- **Validates**: tests/unit/test_sdlc_meta_set.py, tests/unit/test_sdlc_stage_marker.py, tests/unit/test_sdlc_stage_query.py, tests/unit/test_sdlc_verdict.py
- **Assigned To**: resolver-builder
- **Agent Type**: builder
- **Parallel**: false
- Replace `sdlc_meta_set._find_session` body (or call site) so state writes resolve through `find_session(..., ensure=True)`.
- Replace `sdlc_stage_marker._find_session` similarly.
- Route `sdlc_stage_query.query_stage_states` / `query_enriched` through `find_session(..., ensure=True)` (keep the no-issue/no-env empty-defaults path).
- Route `sdlc_verdict` record/get session resolution through `find_session(..., ensure=True)`.
- Delete the now-dead local `_find_session` helpers where fully superseded (NO LEGACY CODE TOLERANCE).

### 3. Update tests
- **Task ID**: build-tests
- **Depends On**: build-callers
- **Validates**: tests/unit/test_sdlc_utils.py, tests/integration/test_sdlc_sessionless_e2e.py (create)
- **Assigned To**: resolver-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `TestFindSessionEnsure` to test_sdlc_utils.py (guards, env-session-without-create, ensure-raises→None, valid-issue+ensure=True→create, valid-issue+ensure=False→None).
- Update the per-subcommand unit tests per the Test Impact section (flip issue-numbered sessionless cases to assert persistence; keep genuinely-sessionless no-op).
- Create `tests/integration/test_sdlc_sessionless_e2e.py` driving the real `sdlc-tool` subcommands via subprocess in a clean env; teardown deletes the `sdlc-local-{N}` session via Popoto.

### 4. Update SKILL.md prose
- **Task ID**: build-skill-prose
- **Depends On**: build-resolver
- **Assigned To**: resolver-builder
- **Agent Type**: builder
- **Parallel**: true
- Update `.claude/skills-global/sdlc/SKILL.md` Step 1.5 prose to note auto-ensure now guarantees a session for non-`/sdlc` callers; keep the explicit `session-ensure` call as belt-and-suspenders, and add the inline `<!-- REDUNDANT-AFTER-#1558: ... -->` comment at the call so it is grep-findable for later cleanup.

### 5. Validation
- **Task ID**: validate-all
- **Depends On**: build-callers, build-tests, build-skill-prose
- **Assigned To**: resolver-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all unit + the new integration test.
- Grep-confirm each state-touching subcommand calls `find_session(..., ensure=True)`.
- Confirm the bare `stage-query` (no issue, no env) still no-ops, and the env-set path does not create a duplicate.
- Report pass/fail.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Resolver unit tests | `pytest tests/unit/test_sdlc_utils.py -q` | exit code 0 |
| Subcommand unit tests | `pytest tests/unit/test_sdlc_meta_set.py tests/unit/test_sdlc_stage_marker.py tests/unit/test_sdlc_stage_query.py tests/unit/test_sdlc_verdict.py tests/unit/test_sdlc_session_ensure.py -q` | exit code 0 |
| Sessionless e2e | `pytest tests/integration/test_sdlc_sessionless_e2e.py -q` | exit code 0 |
| Callers reference shared resolver | `grep -l 'ensure=True' tools/sdlc_meta_set.py tools/sdlc_stage_marker.py tools/sdlc_stage_query.py tools/sdlc_verdict.py` | output contains all four |
| SKILL.md cleanup marker present | `grep -rn 'REDUNDANT-AFTER-#1558' .claude/skills-global/sdlc/SKILL.md` | one match |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

_All resolved by PM check-in 2026-06-04. Decisions folded into the plan above._

1. **Function placement** — ✅ RESOLVED: inline an `ensure: bool = False` parameter into the existing `find_session` rather than a separate `find_or_ensure_session`. Boolean-trap risk mitigated by the safe default (`ensure=False`), an explicit opt-in `ensure=True` at each of the four call sites (grep-able), and a docstring on the param. Rabbit Holes updated to forbid `ensure=True` defaults / context-heuristics instead.
2. **SKILL.md Step 1.5** — ✅ RESOLVED: keep the explicit `session-ensure` call (play-it-safe), but mark it findable with an inline `<!-- REDUNDANT-AFTER-#1558: ... -->` comment so it can be tidied once the resolver auto-ensure is proven. A verification-table check asserts the marker is present.
