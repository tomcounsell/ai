---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-06-03
tracking: https://github.com/tomcounsell/ai/issues/1558
last_comment_id:
revision_applied: true
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

There is no error. Reads look like "nothing has happened yet" and writes appear to succeed but persist nothing.

**Precise diagnosis (corrected per critique — the original framing was wrong):** the router *does* read plan frontmatter. `sdlc_stage_query._compute_meta()` (`tools/sdlc_stage_query.py:290-293`) calls `_find_plan_path(issue_number)` → `_parse_revision_applied(plan_path)` and reads `revision_applied: true` straight off the plan file. **But that read is gated behind session existence:** `query_enriched` short-circuits to `_default_meta()` (which hardcodes `revision_applied: False`) at `sdlc_stage_query.py:385-386` whenever `session is None`, so `_compute_meta` never runs sessionless. The on-disk `revision_applied: true` is therefore invisible *only because the read path is skipped*, producing a phantom "cannot build" loop. The fix is to make a session exist (via auto-ensure on writes) so the already-correct `_compute_meta` frontmatter read actually executes — **not** to add a new frontmatter read. `_parse_revision_applied` / `_compute_meta` must stay untouched.

**Desired outcome:**

It is impossible to silently drop an SDLC state *write* sessionless. Any entry point that *writes* pipeline state ensures a PM session exists first, so the write has a home regardless of how the pipeline is driven. Reads stay side-effect-free (see Technical Approach — writes-only ensure scoping). The same `sdlc-tool` command sequence above, run in a direct Claude Code session, creates the PM session on the first *write* (`verdict record`), persists the verdict, and the subsequent `verdict get` / `next-skill` reads find that session and reflect the recorded state.

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
- **Interface changes:** Add an `ensure: bool = False` parameter to the existing `find_session(session_id, issue_number)` in `_sdlc_utils.py`. Default `ensure=False` preserves today's pure read-only semantics, so existing read callers are unaffected with zero code change. Only the three **write** callers pass `ensure=True` explicitly — the `True` at the call site is what makes the create-side-effect visible.
- **Coupling:** Reduces coupling on the write paths — the three write resolvers (`sdlc_meta_set`, `sdlc_stage_marker`, `sdlc_verdict._cli_record`) collapse onto the shared `_sdlc_utils.find_session`, centralizing the ensure decision. `sdlc_stage_query`'s read resolvers are deliberately left as-is (de-scoped, see No-Gos) — a known, documented residual duplication, not an oversight.
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

- **`find_session(session_id, issue_number, ensure=False)`** (extended, in `tools/_sdlc_utils.py`): with `ensure=False` (default) it behaves exactly as today — pure lookup, returns the session or `None`. With `ensure=True`, when no existing PM session is found AND an ensure is appropriate (valid `issue_number >= 1`, or a session-id env var is present), it calls `ensure_session()` to create/dedup one, then re-resolves and returns it. Only **write** call sites pass `ensure=True`.
- **Default-off behavior:** `ensure=False` keeps pure-lookup semantics for every read caller. Reads never create a session.
- **Write-path convergence (per resolved Concern 2 — `ensure=True` on writes only):** the three *write* subcommands resolve through `find_session(..., ensure=True)`:
  - `sdlc_meta_set.write_meta` (was local `_find_session`) → delete local helper, import + call shared `find_session(..., ensure=True)`.
  - `sdlc_stage_marker.write_marker` (was local `_find_session`) → delete local helper, import + call shared `find_session(..., ensure=True)`.
  - `sdlc_verdict._cli_record` (already aliases the shared `find_session`) → pass `ensure=True` at this one call site.
- **Read paths stay pure (`ensure=False`):**
  - `sdlc_verdict._cli_get` keeps its existing `find_session(...)` call with no `ensure` — same shared alias, default-off.
  - `sdlc_stage_query` (`query_stage_states` / `query_enriched`, and therefore `sdlc_next_skill`) is **left entirely unchanged**. It is read-only, correctly returns `_default_meta()` when no session exists, and finds the session once a write has ensured it. Converging its `_find_session_by_id`/`_find_session_by_issue` helpers would break ~20 existing test patch sites (`test_sdlc_stage_query.py`) for **zero** behavior change — explicitly de-scoped (see No-Gos).

### Flow

Direct `sdlc-tool verdict record --stage CRITIQUE --issue-number 1558` → subcommand calls `find_session(None, 1558, ensure=True)` → no env session, no existing PM session for #1558 → `ensure_session(1558)` creates `sdlc-local-1558` (or dedups onto a live bridge PM session) → re-resolve returns the PM session → `update_stage_states()` writes the verdict → **persisted**. Subsequent `verdict get` / `next-skill` read the same record.

### Technical Approach

- **Guards inside the `ensure=True` branch of `find_session` (mirror the existing `ensure_session` contract so we don't re-derive them):**
  - Resolve `session_id` arg → `VALOR_SESSION_ID` → `AGENT_SESSION_ID` first (the existing order). If that resolves a live PM session, return it without ensuring — this happens before the `ensure` branch is even reached, since it is the existing lookup path.
  - Only attempt ensure when `issue_number is not None and issue_number >= 1`, OR a session-id env var is set. When neither holds, do NOT create — return `None` (e.g. a bare `sdlc-tool meta-set` with no `--issue-number` and no env still no-ops, which is correct: there is no issue context to attach state to). This is the "fail-by-returning-None loudly enough" answer to the issue's open question — we do not silently fabricate a sessionless session.
  - `ensure_session()` already enforces idempotency, PM-type gating, terminal-status gating, and bridge dedup (#1147). The `ensure=True` branch inherits all of it for free — do NOT reimplement.
- **Where the ensure lives:** an `ensure: bool = False` parameter on the existing `find_session`, not a separate function (per resolved Open Question 1). Default-off means the only callers that can trigger creation are the three *write* sites that opt in with `ensure=True` — and that explicit `True` argument at the call site is what makes the create-side-effect visible. Mitigates the boolean-trap concern via (a) safe default, (b) docstring on the param, (c) the side effect being opt-in and grep-able (`grep -rn 'ensure=True' tools/`).
- **`revision_applied`:** no change needed, and **do NOT touch `_parse_revision_applied` or `_compute_meta`** (per Concern 1 — they already read frontmatter correctly). The flag is flipped by recording a CRITIQUE verdict via `verdict record`. Once `verdict record` resolves through `find_session(..., ensure=True)`, the write lands on a real session; the *next* `stage-query`/`next-skill` read finds that session, so `query_enriched` reaches `_compute_meta` (no longer short-circuiting to `_default_meta()`), which then reads `revision_applied: true` off the plan frontmatter exactly as it does today. The whole `revision_applied` chain works the moment a session exists — the only change required is making the write create that session.
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

**Symbol-deletion blast radius (Concern 3):** deleting the local `_find_session` helpers in `sdlc_meta_set` and `sdlc_stage_marker` breaks every `patch("...._find_session")` that targets them by name with `AttributeError`. The "patch where it's looked up" rule applies: callers do `from tools._sdlc_utils import find_session` (or keep an alias), so the patch target becomes the name **in the caller's module namespace** (`tools.sdlc_meta_set.find_session`), not `tools._sdlc_utils.find_session`. Every site below is enumerated with its repoint. **`sdlc_stage_query` is NOT changed**, so all ~20 `test_sdlc_stage_query.py` patch sites (`_find_session_by_id`/`_find_session_by_issue`) remain valid and need no edits.

- [ ] `tests/unit/test_sdlc_utils.py` — UPDATE: add a `TestFindSessionEnsure` class covering the `ensure=True` guards (None/0/-1 → no create; valid issue → create; env session → return without ensure; ensure-raises → None) AND a `ensure=False` default case (valid issue, no session → returns `None`, no create — proves default-off). Existing `find_session` / `find_session_by_issue` tests are unaffected (the new param defaults to the old behavior).
- [ ] `tests/unit/test_sdlc_session_ensure.py` — UPDATE (if needed): confirm the `ensure=True` branch delegates to `ensure_session` and does not duplicate its guard logic. Likely no change to existing tests; add cross-reference assertions only if convenient.
- [ ] `tests/unit/test_sdlc_meta_set.py` — UPDATE: 8 patch sites at lines 40, 64, 89, 110, 123, 148, 155, 168 patch `tools.sdlc_meta_set._find_session`. REPOINT each to `tools.sdlc_meta_set.find_session` (the imported name in meta_set's namespace). Additionally, update the line-110 sessionless test: it asserts `meta-set` no-ops with `return_value=None`; with `ensure=True` now passed, either keep it as a "ensure couldn't resolve → None → no-op" case (mock `find_session` → None) or add a sibling test asserting an issue-numbered `meta-set` persists. Keep the genuinely-sessionless (no issue, no env) no-op behavior covered.
- [ ] `tests/integration/test_sdlc_pipeline_lock.py` — UPDATE: 3 patch sites at lines 145, 179, 200 patch `tools.sdlc_meta_set._find_session`. REPOINT each to `tools.sdlc_meta_set.find_session`.
- [ ] `tests/unit/test_sdlc_stage_marker.py` — DELETE/REPLACE: the `TestFindSession` class (≈ lines 22-93) tests the local `_find_session` resolver directly as the unit under test. That helper is deleted (superseded by `test_sdlc_utils.py`'s shared-resolver tests) → DELETE this class. The `test_passes_issue_number_to_find_session` test (line 124) and any `write_marker` test that patches the resolver must REPOINT to `tools.sdlc_stage_marker.find_session`. ADD a test that a sessionless-but-issue-numbered `stage-marker` now persists the marker.
- [ ] `tests/unit/test_sdlc_verdict.py` — UPDATE: add a sessionless-but-issue-numbered `verdict record` → `verdict get` round-trip test proving the write persists. `sdlc_verdict` keeps its `_find_session` alias (so `test_sdlc_tool_wrapper.py:164` `sdlc_verdict._find_session` patch survives) — `_cli_record` now calls `_find_session(..., ensure=True)`, `_cli_get` unchanged.
- [ ] `tests/unit/test_sdlc_stage_query.py` — NO CHANGE: `sdlc_stage_query` is unchanged, so its ~20 patch sites stay valid. (Listed explicitly so a reviewer doesn't expect edits here.)
- [ ] Add (NEW): `tests/integration/test_sdlc_sessionless_e2e.py` — see the dedicated note below (subprocess-boundary round-trip).

### Integration test: subprocess-boundary round-trip (Concern 4)

The test MUST prove persistence survives **process boundaries** (the real bug), not in-process Popoto object reuse:
- [ ] Each step (`verdict record`, `verdict get`, `next-skill`) is a **distinct** `subprocess.run(["sdlc-tool", ...], capture_output=True, text=True, env={**os.environ, "VALOR_SESSION_ID": "", "AGENT_SESSION_ID": ""})` — the empty env vars force the clean-env local-create path.
- [ ] Assert on **parsed stdout JSON** from a separate `get` subprocess: `json.loads(get_proc.stdout)["verdict"]` matches what `record` wrote. Do NOT assert against an in-process `AgentSession.query` result.
- [ ] Use a high throwaway issue number (e.g. `999001+`) unlikely to collide with a real PM session.
- [ ] Teardown in a `finally`: `AgentSession.query.filter(session_id=f"sdlc-local-{N}").delete()` via Popoto, per Manual Testing Hygiene.

## Rabbit Holes

- **Refactoring `AgentSession`'s shape.** The issue explicitly says the schema is expected to shift before/around this work; do NOT lock the fix to the current field layout or attempt a schema migration. Fix the behavior at the resolver boundary only.
- **Touching `_parse_revision_applied` / `_compute_meta` in `sdlc_stage_query.py`.** These ALREADY read `revision_applied` off the plan frontmatter correctly (lines 290-293). The original plan framing said the router never reads frontmatter — that was wrong (see Problem → Precise diagnosis). The frontmatter read is merely *gated behind session existence*; once a write ensures the session, the existing read fires. Do NOT add a second frontmatter read, a `next-skill` disk fallback, or modify these functions.
- **Converging `sdlc_stage_query`'s read resolvers.** Tempting (DRY) to fold `_find_session_by_id`/`_find_session_by_issue` into the shared `find_session`. De-scoped per Concern 2 (writes-only): stage_query is read-only, returns correct defaults sessionless, and finds the session once a write creates it. Converging it changes no behavior but breaks ~20 test patch sites. Leave it alone.
- **Adding `ensure=True` defaults or auto-detecting "should I create?".** The flag defaults to `False` and is only flipped on by the three *write* call sites, by hand. Do NOT make `ensure` default to `True`, and do NOT add heuristics that decide to create based on subcommand name or call context — the opt-in `ensure=True` argument is the entire contract. (Resolved Open Question 1 chose the inline flag over a separate function; the guard against the boolean-trap risk is the safe default + explicit opt-in, not a second function.)
- **Garbage-collecting `sdlc-local-{N}` sessions created by reads.** The existing `--kill-orphans` path in `sdlc_session_ensure.py` already reaps zombie `sdlc-local-*` PM sessions. Do not add new cleanup machinery.

## Risks

### Risk 1: A write subcommand creates a session as a side effect
**Impact:** `verdict record` / `meta-set` / `stage-marker` now create a PM session when none exists, potentially polluting the session list.
**Mitigation:** This is the intended fix — a write must have a home. Creation is gated: only when `issue_number >= 1` or a session-id env is present, and only at the three write sites that explicitly pass `ensure=True`. **Reads (`stage-query`, `next-skill`, `verdict get`) do NOT create sessions** (resolved Concern 2 — writes-only), so the constant diagnostic read traffic has zero session-creation side effect. Existing `--kill-orphans` reaps any zombies.

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
- **Converging `sdlc_stage_query`'s read resolvers** (`_find_session_by_id` / `_find_session_by_issue`) onto the shared `find_session`. Deliberately de-scoped (resolved Concern 2): stage_query is read-only and needs no behavior change for this fix; converging it would churn ~20 test patch sites for zero benefit. It keeps its own resolvers. Convergence remains available as future cleanup if a reader path ever needs ensure semantics.
- **`ensure=True` on any read path.** Reads stay pure `ensure=False`. Only the three write sites opt in.
- Otherwise nothing deferred — the write-path resolvers, callers, tests, docs, and the SKILL.md prose update are all in scope.

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

- [ ] In a clean env (no `VALOR_SESSION_ID`/`AGENT_SESSION_ID`), `sdlc-tool verdict record --stage CRITIQUE --verdict "READY TO BUILD" --issue-number {N}` followed by `sdlc-tool verdict get --stage CRITIQUE --issue-number {N}` round-trips the verdict (no longer `{}`). The `record` write creates the session; the `get` read finds it.
- [ ] After a `verdict record`, `sdlc-tool stage-query --issue-number {N}` returns the session-backed payload (the write created the session; the read finds it).
- [ ] `sdlc-tool next-skill --issue-number {N}` reflects recorded state instead of the phantom "cannot build" default after a verdict is recorded.
- [ ] A bare `sdlc-tool stage-query` with no `--issue-number` and no env var still no-ops (returns empty defaults, exits 0) — no fabricated session. (Reads never create — this holds with or without a prior write.)
- [ ] The three **write** resolvers (`sdlc_meta_set.write_meta`, `sdlc_stage_marker.write_marker`, `sdlc_verdict._cli_record`) resolve through the shared `find_session(..., ensure=True)`. Read paths (`sdlc_verdict._cli_get`, `sdlc_stage_query`) stay `ensure=False` / unchanged.
- [ ] No duplicate `sdlc-local-{N}` session is created when run inside a bridge/worker session with `VALOR_SESSION_ID` set (dedup preserved).
- [ ] `_parse_revision_applied` / `_compute_meta` in `sdlc_stage_query.py` are unmodified (Concern 1 — the frontmatter read was already correct).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] `grep -rn 'ensure=True' tools/` confirms exactly the three write call sites pass it (and `_sdlc_utils.find_session`'s signature defines it).

## Team Orchestration

### Team Members

- **Builder (resolver)**
  - Name: resolver-builder
  - Role: Add the `ensure` flag to `find_session` in `_sdlc_utils.py`; converge the three write callers onto `find_session(..., ensure=True)` (leave stage_query reads untouched); update SKILL.md prose + cleanup marker.
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

### 2. Converge the three WRITE callers onto the shared resolver
- **Task ID**: build-callers
- **Depends On**: build-resolver
- **Validates**: tests/unit/test_sdlc_meta_set.py, tests/unit/test_sdlc_stage_marker.py, tests/unit/test_sdlc_verdict.py, tests/integration/test_sdlc_pipeline_lock.py
- **Assigned To**: resolver-builder
- **Agent Type**: builder
- **Parallel**: false
- `tools/sdlc_meta_set.py`: delete local `_find_session` (lines 86-127), `from tools._sdlc_utils import find_session`, change `write_meta`'s call (line 157) to `find_session(session_id, issue_number=issue_number, ensure=True)`.
- `tools/sdlc_stage_marker.py`: delete local `_find_session` (lines 51-93), `from tools._sdlc_utils import find_session`, change `write_marker`'s call (line 118) to `find_session(session_id, issue_number=issue_number, ensure=True)`.
- `tools/sdlc_verdict.py`: keep the existing `from tools._sdlc_utils import find_session as _find_session` alias (line 85). In `_cli_record` (line 316) add `ensure=True`. Leave `_cli_get` (line 346) unchanged (`ensure=False`).
- **Do NOT touch `tools/sdlc_stage_query.py`** — it is read-only and de-scoped (No-Gos). Its `_find_session_by_id`/`_find_session_by_issue` helpers, `query_enriched`, `_compute_meta`, and `_parse_revision_applied` all stay as-is.
- NO LEGACY CODE TOLERANCE: the two deleted local `_find_session` helpers must leave no commented-out remnant.

### 3. Update tests
- **Task ID**: build-tests
- **Depends On**: build-callers
- **Validates**: tests/unit/test_sdlc_utils.py, tests/integration/test_sdlc_sessionless_e2e.py (create)
- **Assigned To**: resolver-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `TestFindSessionEnsure` to test_sdlc_utils.py (guards, env-session-without-create, ensure-raises→None, valid-issue+ensure=True→create, valid-issue+ensure=False→None).
- Repoint the broken patch sites per the Test Impact section: `test_sdlc_meta_set.py` (8 sites → `tools.sdlc_meta_set.find_session`), `test_sdlc_pipeline_lock.py` (3 sites → `tools.sdlc_meta_set.find_session`), `test_sdlc_stage_marker.py` (DELETE the `TestFindSession` class; repoint `write_marker` resolver patches to `tools.sdlc_stage_marker.find_session`).
- Add the issue-numbered persistence tests: `meta-set`, `stage-marker`, and `verdict record`→`get` round-trips. Keep genuinely-sessionless (no issue, no env) no-op coverage.
- Leave `test_sdlc_stage_query.py` untouched (stage_query unchanged).
- Create `tests/integration/test_sdlc_sessionless_e2e.py` per the Concern-4 note: distinct `subprocess.run(["sdlc-tool", ...])` calls with `VALOR_SESSION_ID`/`AGENT_SESSION_ID` forced empty, assert on parsed stdout JSON across process boundaries, teardown deletes `sdlc-local-{N}` via Popoto in a `finally`.

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
- Grep-confirm `ensure=True` appears at exactly the three write call sites (meta_set, stage_marker, verdict._cli_record) and nowhere in stage_query.
- Confirm `git diff` shows `tools/sdlc_stage_query.py` unchanged (Concern 1 + Concern 2 de-scope).
- Confirm the bare `stage-query` (no issue, no env) still no-ops, reads never create a session, and the env-set path does not create a duplicate.
- Report pass/fail.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Resolver unit tests | `pytest tests/unit/test_sdlc_utils.py -q` | exit code 0 |
| Subcommand unit tests | `pytest tests/unit/test_sdlc_meta_set.py tests/unit/test_sdlc_stage_marker.py tests/unit/test_sdlc_stage_query.py tests/unit/test_sdlc_verdict.py tests/unit/test_sdlc_session_ensure.py -q` | exit code 0 |
| Pipeline-lock integration test | `pytest tests/integration/test_sdlc_pipeline_lock.py -q` | exit code 0 |
| Sessionless e2e | `pytest tests/integration/test_sdlc_sessionless_e2e.py -q` | exit code 0 |
| Write callers pass ensure=True | `grep -l 'ensure=True' tools/sdlc_meta_set.py tools/sdlc_stage_marker.py tools/sdlc_verdict.py` | output contains all three |
| stage_query left unchanged | `git diff --quiet tools/sdlc_stage_query.py && echo unchanged` | prints `unchanged` |
| SKILL.md cleanup marker present | `grep -rn 'REDUNDANT-AFTER-#1558' .claude/skills-global/sdlc/SKILL.md` | one match |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |

## Critique Results

Critique run 2026-06-04 — verdict **READY TO BUILD (with concerns)**: 0 blockers, 4 concerns, 1 nit. All four concerns embedded in this revision.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Consistency Auditor, Skeptic, Archaeologist | Problem statement falsely claims "the router never reads plan frontmatter" — `_compute_meta` (sdlc_stage_query.py:290-293) DOES read `revision_applied`; it's just session-gated. | Problem → "Precise diagnosis"; `revision_applied` Technical Approach; Rabbit Holes; Success Criteria | Builder must NOT touch `_parse_revision_applied`/`_compute_meta`; the only change is making a write create the session so the existing read fires. |
| CONCERN | Adversary, Operator, Simplifier | Auto-ensure on `next-skill`/`stage-query` makes read-only commands create sessions. | Solution → write-path convergence; Technical Approach; Risk 1; No-Gos — **DECISION: `ensure=True` on writes only** (user-confirmed 2026-06-04) | Only `write_meta`/`write_marker`/`_cli_record` pass `ensure=True`. Reads stay pure. stage_query de-scoped entirely. |
| CONCERN | Operator, Skeptic | Deleting local `_find_session` helpers breaks ~15 test patch sites by name, not the handful listed. | Test Impact → enumerated patch sites + "patch where it's looked up" repoints | Writes-only scope means stage_query's ~20 patch sites are NOT touched; only meta_set (8) + pipeline_lock (3) repoint, stage_marker `TestFindSession` class DELETE. |
| CONCERN | Skeptic | Integration test must round-trip through Redis across process boundaries, not in-process Popoto. | Test Impact → "Integration test: subprocess-boundary round-trip"; Task 3 | Distinct `subprocess.run` per step, empty `VALOR_SESSION_ID`/`AGENT_SESSION_ID`, assert on parsed stdout JSON, teardown via Popoto in `finally`. |
| NIT | Simplifier | SKILL.md belt-and-suspenders `session-ensure` call is dead-on-arrival. | Accepted as-is (resolved Open Question 2) — kept + tagged `REDUNDANT-AFTER-#1558` for findable cleanup | No change required. |

---

## Open Questions

_All resolved by PM check-in 2026-06-04. Decisions folded into the plan above._

1. **Function placement** — ✅ RESOLVED: inline an `ensure: bool = False` parameter into the existing `find_session` rather than a separate `find_or_ensure_session`. Boolean-trap risk mitigated by the safe default (`ensure=False`), an explicit opt-in `ensure=True` at each of the three write call sites (grep-able), and a docstring on the param. Rabbit Holes updated to forbid `ensure=True` defaults / context-heuristics instead.
2. **SKILL.md Step 1.5** — ✅ RESOLVED: keep the explicit `session-ensure` call (play-it-safe), but mark it findable with an inline `<!-- REDUNDANT-AFTER-#1558: ... -->` comment so it can be tidied once the resolver auto-ensure is proven. A verification-table check asserts the marker is present.
