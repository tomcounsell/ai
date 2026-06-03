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
- **Interface changes:** Add a new function `find_or_ensure_session(session_id, issue_number)` to `_sdlc_utils.py`. `find_session()` keeps its current read-only semantics (no side effects) so existing read-only callers are unaffected. State-touching callers switch to `find_or_ensure_session`.
- **Coupling:** Reduces coupling — collapses four divergent resolvers down to the shared `_sdlc_utils` resolver, and centralizes the ensure decision in one place.
- **Data ownership:** Unchanged. `stage_states` still lives on the PM AgentSession; auto-ensure only guarantees the record exists before a write lands on it.
- **Reversibility:** High. The new function is additive; reverting means switching callers back and deleting `find_or_ensure_session`.

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1 (confirm the resolver-boundary direction vs. inlining ensure in `find_session`)
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies. It modifies internal Python resolvers and reuses an existing idempotent ensure path.

## Solution

### Key Elements

- **`find_or_ensure_session(session_id, issue_number)`** (new, in `tools/_sdlc_utils.py`): resolves an existing PM session exactly as `find_session()` does today; if none is found AND an ensure is appropriate (valid `issue_number >= 1`, or a session-id env var is present), calls `ensure_session()` to create/dedup one, then re-resolves and returns it. Auto-creating on a *read* like `stage-query` is an intended, documented side effect.
- **Read-only `find_session()`** (unchanged): keeps pure-lookup semantics. Reserved for callers that genuinely want "does a session exist?" without creating one.
- **Caller convergence:** every state-touching subcommand resolves through `find_or_ensure_session` instead of its bespoke local resolver. The four divergent resolvers (`sdlc_meta_set._find_session`, `sdlc_stage_marker._find_session`, `sdlc_stage_query._find_session_by_id/_by_issue`, and `sdlc_verdict`'s `find_session` alias) collapse onto the shared one.

### Flow

Direct `sdlc-tool verdict record --stage CRITIQUE --issue-number 1558` → subcommand calls `find_or_ensure_session(None, 1558)` → no env session, no existing PM session for #1558 → `ensure_session(1558)` creates `sdlc-local-1558` (or dedups onto a live bridge PM session) → re-resolve returns the PM session → `update_stage_states()` writes the verdict → **persisted**. Subsequent `verdict get` / `next-skill` read the same record.

### Technical Approach

- **Guards inside `find_or_ensure_session` (mirror the existing `ensure_session` contract so we don't re-derive them):**
  - Resolve `session_id` arg → `VALOR_SESSION_ID` → `AGENT_SESSION_ID` first (same order as `find_session`). If that resolves a live PM session, return it without ensuring.
  - Only attempt ensure when `issue_number is not None and issue_number >= 1`, OR a session-id env var is set. When neither holds, do NOT create — return `None` (e.g. a bare `sdlc-tool stage-query` with no `--issue-number` and no env still no-ops, which is correct: there is no issue context to attach state to). This is the "fail-by-returning-None loudly enough" answer to the issue's open question — we do not silently fabricate a sessionless session.
  - `ensure_session()` already enforces idempotency, PM-type gating, terminal-status gating, and bridge dedup (#1147). Auto-ensure inherits all of it for free — do NOT reimplement.
- **Where the ensure lives:** a thin sibling function `find_or_ensure_session` rather than a side-effect baked into `find_session`. Rationale: keeping the side-effecting variant explicit and separately named makes the "this read can create a session" behavior obvious at every call site, satisfying the issue's "it should be obvious in the code" requirement. `find_session` stays pure.
- **`revision_applied`:** no change needed. It is flipped by recording a CRITIQUE verdict via `verdict record`. Once `verdict record` resolves through `find_or_ensure_session`, the write lands on a real session and `revision_applied` persists correctly. Auto-ensure + a working `verdict record` is sufficient; no new code path for `revision_applied`.
- **SKILL.md Step 1.5:** the explicit `session-ensure` call becomes redundant once auto-ensure is in the resolver, but it is harmless (idempotent) and serves as documentation. Leave the call in place; update the surrounding prose to note that auto-ensure now also guarantees a session for non-`/sdlc` callers, so the explicit step is belt-and-suspenders rather than the sole guarantee.
- **Failure semantics:** preserve the existing "never crash the calling skill" contract. `ensure_session()` returns `{}` on any failure (e.g. `ProjectKeyResolutionError`); `find_or_ensure_session` treats an empty/failed ensure as "no session" and returns `None`, so the subcommand degrades to today's no-op behavior rather than raising. The improvement is that the *common* path (valid issue_number, resolvable project) now succeeds.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `ensure_session()` already wraps its body in `try/except` returning `{}`; `find_or_ensure_session` must wrap its ensure call so an ensure failure yields `None`, not a raise. Add a test that monkeypatches `ensure_session` to raise and asserts `find_or_ensure_session` returns `None` (no propagation).
- [ ] `find_session`'s existing `except Exception` blocks (lines 70, 93, 99) remain; no new bare `except: pass` introduced.

### Empty/Invalid Input Handling
- [ ] `find_or_ensure_session(None, None)` with no env vars → returns `None`, no session created (test asserts `AgentSession` count unchanged).
- [ ] `find_or_ensure_session(None, 0)` and `(None, -1)` → no ensure attempted (mirrors `ensure_session` and `find_session_by_issue` guards), returns `None`.
- [ ] `find_or_ensure_session(None, 1558)` with no existing session → creates `sdlc-local-1558`, returns it.

### Error State Rendering
- [ ] Subcommand-level: `sdlc-tool verdict record` against a resolvable issue persists and the JSON output reflects the recorded verdict (not `{}`). `verdict get` round-trips it.
- [ ] When ensure genuinely cannot resolve a project_key, the subcommand still prints valid JSON (`{}` or empty defaults) and exits 0 — no traceback to the caller.

## Test Impact

- [ ] `tests/unit/test_sdlc_utils.py` — UPDATE: add a `TestFindOrEnsureSession` class covering the guards (None/0/-1 → no create; valid issue → create; env session → return without ensure; ensure-raises → None). Existing `find_session_by_issue` tests are unaffected (additive).
- [ ] `tests/unit/test_sdlc_session_ensure.py` — UPDATE (if needed): confirm `find_or_ensure_session` delegates to `ensure_session` and does not duplicate its guard logic. Likely no change to existing tests; add cross-reference assertions only if convenient.
- [ ] `tests/unit/test_sdlc_meta_set.py` — UPDATE: `sdlc_meta_set` now resolves through the shared resolver. Update any test that asserts a sessionless `meta-set` no-ops to instead assert it now persists when an issue_number is supplied (the behavior change this issue mandates). Keep the genuinely-sessionless (no issue, no env) no-op test.
- [ ] `tests/unit/test_sdlc_stage_marker.py` — UPDATE: same convergence; add a test that a sessionless-but-issue-numbered `stage-marker` now persists the marker.
- [ ] `tests/unit/test_sdlc_stage_query.py` — UPDATE: add a test that `query_enriched(issue_number=N)` with no pre-existing session now auto-creates and returns a session-backed payload (was empty defaults). Keep the no-issue/no-env empty-defaults test.
- [ ] `tests/unit/test_sdlc_verdict.py` — UPDATE: add a sessionless-but-issue-numbered `verdict record` → `verdict get` round-trip test proving the write persists.
- [ ] Add (NEW): `tests/integration/test_sdlc_sessionless_e2e.py` — drive the actual `sdlc-tool` subcommands (subprocess) for a throwaway issue number in a clean env (no `VALOR_SESSION_ID`), asserting record→get→next-skill reflects persisted state. Clean up the `sdlc-local-{N}` session in teardown via Popoto (`AgentSession.query.filter(...).delete()`), per Manual Testing Hygiene.

## Rabbit Holes

- **Refactoring `AgentSession`'s shape.** The issue explicitly says the schema is expected to shift before/around this work; do NOT lock the fix to the current field layout or attempt a schema migration. Fix the behavior at the resolver boundary only.
- **Reading plan frontmatter in the router.** Tempting to make `next-skill` consult `revision_applied: true` on disk as a fallback. Out of scope — the router's contract is "state lives on the session." Auto-ensure makes the session the single source of truth; do not add a second source.
- **Collapsing `find_session` and `find_or_ensure_session` into one function with a `create=` flag.** A boolean trap that hides the side effect. Keep two explicitly-named functions.
- **Garbage-collecting `sdlc-local-{N}` sessions created by reads.** The existing `--kill-orphans` path in `sdlc_session_ensure.py` already reaps zombie `sdlc-local-*` PM sessions. Do not add new cleanup machinery.

## Risks

### Risk 1: Read operations now have a write side effect (session creation)
**Impact:** A bare `stage-query` could create a session unexpectedly, polluting the session list.
**Mitigation:** The guard only creates when `issue_number >= 1` or a session-id env is present. A `stage-query` with a real issue number *should* have a session — that's the whole point. The function name `find_or_ensure_session` makes the side effect explicit at every call site. Existing `--kill-orphans` reaps any zombies.

### Risk 2: Duplicate session creation inside bridge/worker sessions
**Impact:** Auto-ensure could spawn a `sdlc-local-{N}` duplicate alongside the live bridge PM session.
**Mitigation:** `ensure_session()` already short-circuits on `VALOR_SESSION_ID`/`AGENT_SESSION_ID` to the live bridge PM session (#1147/#1151). Auto-ensure calls `ensure_session()` and inherits this dedup verbatim. The new integration test runs in a clean env to prove the local-create path; an additional unit test asserts the env-set path returns the env session without creating.

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
- [ ] Update `docs/features/sdlc-tool-resolver.md` to document `find_or_ensure_session`: the resolver-boundary auto-ensure, the create-guards (issue_number >= 1 or env session), the read-with-side-effect semantics, and that it supersedes the per-skill `session-ensure` requirement for non-`/sdlc` callers.
- [ ] If `docs/features/README.md` indexes the resolver doc, confirm the entry still accurate (no new file).

### External Documentation Site
No external documentation site for this repo — no action.

### Inline Documentation
- [ ] Docstring on `find_or_ensure_session` stating the auto-create side effect, the guards, and that it reuses `ensure_session`'s dedup.
- [ ] Update the `.claude/skills-global/sdlc/SKILL.md` Step 1.5 prose to note auto-ensure now covers non-`/sdlc` callers (the explicit call is belt-and-suspenders).

## Success Criteria

- [ ] In a clean env (no `VALOR_SESSION_ID`/`AGENT_SESSION_ID`), `sdlc-tool verdict record --stage CRITIQUE --verdict "READY TO BUILD" --issue-number {N}` followed by `sdlc-tool verdict get --stage CRITIQUE --issue-number {N}` round-trips the verdict (no longer `{}`).
- [ ] `sdlc-tool stage-query --issue-number {N}` against a fresh issue auto-creates the PM session and returns a session-backed payload.
- [ ] `sdlc-tool next-skill --issue-number {N}` reflects recorded state instead of the phantom "cannot build" default after a verdict is recorded.
- [ ] A bare `sdlc-tool stage-query` with no `--issue-number` and no env var still no-ops (returns empty defaults, exits 0) — no fabricated session.
- [ ] The four divergent resolvers (`sdlc_meta_set`, `sdlc_stage_marker`, `sdlc_stage_query`, `sdlc_verdict`) all resolve through the shared `find_or_ensure_session` for state-touching operations.
- [ ] No duplicate `sdlc-local-{N}` session is created when run inside a bridge/worker session with `VALOR_SESSION_ID` set (dedup preserved).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] `grep` confirms each state-touching subcommand references `find_or_ensure_session`.

## Team Orchestration

### Team Members

- **Builder (resolver)**
  - Name: resolver-builder
  - Role: Add `find_or_ensure_session` to `_sdlc_utils.py`; converge the four callers onto it; update SKILL.md prose.
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
- Add `find_or_ensure_session(session_id=None, issue_number=None)` to `tools/_sdlc_utils.py`: resolve env/arg/issue exactly as `find_session`; if a live PM session is found, return it; otherwise, when `issue_number >= 1` or a session-id env var is set, lazily import and call `tools.sdlc_session_ensure.ensure_session(issue_number, ...)`, then re-resolve and return; wrap the ensure in try/except so failures return `None`.
- Keep `find_session` read-only and unchanged.
- Docstring stating the create-side-effect, guards, and dedup reuse.

### 2. Converge callers onto the shared resolver
- **Task ID**: build-callers
- **Depends On**: build-resolver
- **Validates**: tests/unit/test_sdlc_meta_set.py, tests/unit/test_sdlc_stage_marker.py, tests/unit/test_sdlc_stage_query.py, tests/unit/test_sdlc_verdict.py
- **Assigned To**: resolver-builder
- **Agent Type**: builder
- **Parallel**: false
- Replace `sdlc_meta_set._find_session` body (or call site) so state writes resolve through `find_or_ensure_session`.
- Replace `sdlc_stage_marker._find_session` similarly.
- Route `sdlc_stage_query.query_stage_states` / `query_enriched` through `find_or_ensure_session` (keep the no-issue/no-env empty-defaults path).
- Route `sdlc_verdict` record/get session resolution through `find_or_ensure_session`.
- Delete the now-dead local `_find_session` helpers where fully superseded (NO LEGACY CODE TOLERANCE).

### 3. Update tests
- **Task ID**: build-tests
- **Depends On**: build-callers
- **Validates**: tests/unit/test_sdlc_utils.py, tests/integration/test_sdlc_sessionless_e2e.py (create)
- **Assigned To**: resolver-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `TestFindOrEnsureSession` to test_sdlc_utils.py (guards, env-session-without-create, ensure-raises→None, valid-issue→create).
- Update the per-subcommand unit tests per the Test Impact section (flip issue-numbered sessionless cases to assert persistence; keep genuinely-sessionless no-op).
- Create `tests/integration/test_sdlc_sessionless_e2e.py` driving the real `sdlc-tool` subcommands via subprocess in a clean env; teardown deletes the `sdlc-local-{N}` session via Popoto.

### 4. Update SKILL.md prose
- **Task ID**: build-skill-prose
- **Depends On**: build-resolver
- **Assigned To**: resolver-builder
- **Agent Type**: builder
- **Parallel**: true
- Update `.claude/skills-global/sdlc/SKILL.md` Step 1.5 prose to note auto-ensure now guarantees a session for non-`/sdlc` callers; keep the explicit `session-ensure` call as belt-and-suspenders.

### 5. Validation
- **Task ID**: validate-all
- **Depends On**: build-callers, build-tests, build-skill-prose
- **Assigned To**: resolver-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all unit + the new integration test.
- Grep-confirm each state-touching subcommand references `find_or_ensure_session`.
- Confirm the bare `stage-query` (no issue, no env) still no-ops, and the env-set path does not create a duplicate.
- Report pass/fail.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Resolver unit tests | `pytest tests/unit/test_sdlc_utils.py -q` | exit code 0 |
| Subcommand unit tests | `pytest tests/unit/test_sdlc_meta_set.py tests/unit/test_sdlc_stage_marker.py tests/unit/test_sdlc_stage_query.py tests/unit/test_sdlc_verdict.py tests/unit/test_sdlc_session_ensure.py -q` | exit code 0 |
| Sessionless e2e | `pytest tests/integration/test_sdlc_sessionless_e2e.py -q` | exit code 0 |
| Callers reference shared resolver | `grep -l find_or_ensure_session tools/sdlc_meta_set.py tools/sdlc_stage_marker.py tools/sdlc_stage_query.py tools/sdlc_verdict.py` | output contains all four |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Function placement** — the plan adds a separately-named `find_or_ensure_session` rather than baking the side effect into `find_session`, to keep the create-behavior explicit at call sites. Confirm this is preferred over inlining an `ensure=True` default into `find_session`.
2. **SKILL.md Step 1.5** — keep the explicit `session-ensure` call as harmless belt-and-suspenders (plan's choice), or remove it now that the resolver guarantees the session? Removing reduces duplication but loses the documentation-of-intent at the entry point.
