---
status: Ready
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-06-22
tracking: https://github.com/tomcounsell/ai/issues/1761
last_comment_id: 4770352989
revision_applied: true
---

# SDLC Cross-Repo Plan Resolution Fix (PLAN↔CRITIQUE never converges to BUILD)

## Problem

During two `/do-sdlc` runs in the **cuttlefish** repo (issues #547, #550, each in its
own worktree), the supervisor never converged from PLAN/CRITIQUE to BUILD on its own.
Both subagents had to manually drive BUILD against a plan already marked build-ready,
with zero code-correctness blockers. Each stuck run burned repeated PLAN→CRITIQUE→PLAN
dispatch cycles and required manual override — defeating the point of autonomous `/do-sdlc`.

**Current behavior:**
The router cannot read `revision_applied: true` from a target-repo plan because the plan
file is resolved in the **wrong repo**. `find_plan_path` and `_resolve_target_repo`
(`tools/_sdlc_utils.py`) run inside `sdlc-tool`, which forces cwd to `~/src/ai` via
`uv run --directory $AI_REPO_ROOT`. When `SDLC_TARGET_REPO` is unset — which is the case
for **every local `/do-sdlc` run** (only `agent/sdk_client.py:1590` exports it, on the
bridge/worker path) — resolution falls through to `_git_toplevel()` → `~/src/ai`, so a
target-repo issue resolves to:
- **None** (no ai-repo plan references the issue → #547), or
- the **wrong plan** via a bare `#{issue}` textual fallback (a plan that merely *mentions*
  PR #550, whose `tracking:` is #534 and has no `revision_applied` → #550).

Either way `_parse_revision_applied` returns `False`, so router row 4c
(`_rule_critique_ready_with_concerns_revision_applied`, `agent/sdlc_router.py:676`) and the
G7 lock self-heal are **unreachable**. The pipeline cycles rows 2b→4b→2b forever. The
notes-only re-stale (frontmatter-inclusive plan hash busting G5) keeps the cycle fed but is
not the binding cause.

**Desired outcome:**
A target-repo issue's plan resolves to the *correct* plan in the *correct* repo even when
`sdlc-tool` forces cwd to `~/src/ai`. `revision_applied: true` is then read correctly, row 4c
fires, and a with-concerns plan converges to BUILD in a single revision pass — unattended.

## Freshness Check

**Baseline commit:** `e1ad6e7e2aa55e0d2ff50d1e051a4e8e1012c1fa`
**Issue filed at:** 2026-06-22T14:49:17Z (confirmed-root-cause comment 2026-06-22T16:05:57Z on `e1ad6e7e`)
**Disposition:** Unchanged

**File:line references re-verified (all still hold on `e1ad6e7e`):**
- `tools/_sdlc_utils.py:330` `find_plan_path` — confirmed. Already prefers `tracking:` match
  (lines 373–391) but still keeps a bare `#N` textual `fallback`; plans-dir resolution
  (lines 352–360) is `SDLC_TARGET_REPO` → `_git_toplevel()` → `__file__` fallback.
- `tools/_sdlc_utils.py:51` `_resolve_target_repo` — confirmed. `GH_REPO` → `SDLC_TARGET_REPO`-as-cwd → `_git_toplevel()`.
- `tools/sdlc_stage_query.py:294` `_parse_revision_applied`, `:384` calls `find_plan_path` — confirmed.
- `agent/sdk_client.py:1590` is the **only** site exporting `SDLC_TARGET_REPO` — confirmed via grep.
- `tools/sdlc_verdict.py:94` `compute_plan_hash` — confirmed frontmatter-inclusive (hashes full bytes after CRLF normalization).
- `tools/sdlc_next_skill.py:98-104` computes `context["current_plan_hash"]` via `compute_plan_hash` — confirmed (this is the G5 input).
- `agent/sdlc_router.py:374-423` G5, `:649-696` rows 4b/4c — confirmed.
- `.claude/skills-global/do-plan-critique/SKILL.md:211` bare `from tools.sdlc_verdict import compute_plan_hash` — confirmed.
- Bare `cd ~/src/ai` / `python -m tools.X`: `do-build/SKILL.md:490`, `do-build/PR_AND_CLEANUP.md:107,127`,
  `do-plan/SKILL.md:132,154`, `do-docs/SKILL.md:144,150`, `do-patch/SKILL.md:207`,
  `do-pr-review/sub-skills/post-review.md:224-226` — confirmed.

**Cited sibling issues/PRs re-checked:** lineage commits (`3e1e3dae`, `6e943ea9`, `5bc6243a`,
`8218c5af`, `627e3cf0`) are all merged. None changed the plan-resolution path that is the binding cause.

**Commits on main since issue was filed (touching referenced files):** none —
`git log --since="2026-06-22T14:49:17Z"` on `tools/_sdlc_utils.py tools/sdlc_verdict.py
agent/sdlc_router.py tools/sdlc_stage_query.py` returns empty. Issue was filed against current HEAD.

**Active plans in `docs/plans/` overlapping this area:** none touching `find_plan_path` / `_resolve_target_repo`.

**Notes:** `find_plan_path` already prefers a `tracking:` frontmatter match (recent fix) — the
remaining hole is (a) the wrong *plans directory* when `SDLC_TARGET_REPO` is unset, and (b) the
bare-`#N` textual `fallback` returning a foreign plan. Both must be closed.

## Prior Art

Long lineage of router dead-end fixes — none addressed cross-repo plan resolution:

- **`3e1e3dae`** Fix SDLC router dead-end: CRITIQUE in_progress with empty verdict (#1668) — router state, not path resolution.
- **`6e943ea9`** Fix SDLC router stale-critique dead-end (#1639) — staleness logic, not path.
- **`5bc6243a`** verdict normalization + plan-existence gate + stale-verdict supersession (#1638/#1640/#1641) — verdict handling, not path.
- **`8218c5af`** Guard rule 4b against re-firing once a PR exists (#1554) — adds the `pr_number` guard on row 4b; relevant but downstream of the path bug.
- **`627e3cf0`** row 8c REVIEW empty-verdict re-dispatch (#1755) — REVIEW stage.
- **`docs/features/sdlc-tool-resolver.md`** (#1671/#1672) — introduced the `sdlc-tool` wrapper + `_resolve_target_repo` ladder precisely to fix cwd-dependent `tools/` resolution in foreign repos. **This plan completes that work**: the wrapper forces cwd to `~/src/ai`, but plan/repo resolution inside it still needs `SDLC_TARGET_REPO` to point at the target repo, and local `/do-sdlc` never sets it.
- **`docs/features/sdlc-pipeline-portability.md`** — "git-root plan resolution + tracking-URL match" shipped the `tracking:`-preferred match in `find_plan_path`. The bare-`#N` fallback survived and is one of the two remaining holes.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| #1671/#1672 (`sdlc-tool` resolver) | Forced `sdlc-tool` cwd to `~/src/ai` so the correct `tools/` package loads | Forcing cwd to `~/src/ai` is exactly what makes `_git_toplevel()` return the wrong repo for plan/repo resolution; it relies on `SDLC_TARGET_REPO` being exported, which local `/do-sdlc` never does |
| pipeline-portability `tracking:`-match | `find_plan_path` prefers a `tracking:` frontmatter match over a bare textual reference | Only disambiguates *within one plans directory*; it does not fix the wrong plans directory, and the bare-`#N` `fallback` still returns a foreign plan when no `tracking:` match exists |
| Router row 2b/4b/4c lineage | Patched staleness re-dispatch and `pr_number` guards | All operate on `meta.revision_applied`, which is silently `False` because the plan file was resolved in the wrong repo — they were never reachable |

**Root cause pattern:** every prior fix treated this as a *router state-machine* problem.
The binding cause is one layer below the router: the plan file is resolved against the wrong
repo, so the router's inputs (`revision_applied`, plan hash) are wrong before any rule runs.
`SDLC_TARGET_REPO` propagation from local `/do-sdlc` is the missing link.

## Data Flow

1. **Entry point**: Local `/do-sdlc` supervision loop runs `sdlc-tool next-skill --issue-number {N}` in a target-repo cwd (e.g. `~/src/cuttlefish` worktree).
2. **`sdlc-tool` wrapper** (`scripts/sdlc-tool:92`): `exec uv run --directory "$AI_REPO_ROOT" python -m tools.sdlc_next_skill` — **cwd is now forced to `~/src/ai`**; child process inherits the parent env (so `SDLC_TARGET_REPO` would propagate *if it were set*).
3. **`tools.sdlc_next_skill`** (`:98-104`): calls `find_plan_path(N)` → `compute_plan_hash(plan)` → sets `context["current_plan_hash"]` for G5.
4. **`find_plan_path`** (`tools/_sdlc_utils.py:352-391`): plans-dir = `SDLC_TARGET_REPO` (unset) → `_git_toplevel()` = `~/src/ai` → walks `~/src/ai/docs/plans`. Returns None (#547) or a foreign `#550`-mentioning plan via `fallback`.
5. **`_compute_meta`** (`tools/sdlc_stage_query.py:384-385`): `_parse_revision_applied(wrong_or_none_plan)` → `False`.
6. **Router** (`agent/sdlc_router.py`): `meta.revision_applied=False` → row 4c unreachable → rows 2b→4b→2b loop. G5 sees frontmatter-inclusive hash differ after the revision write → cache busts → re-stale.
7. **Output**: `next-skill` keeps returning `/do-plan` then `/do-plan-critique`, never `/do-build`.

The fix injects `SDLC_TARGET_REPO` at step 1 (export from `/do-sdlc`) and hardens steps 4 and 6
so resolution refuses to silently land on the ai repo / a foreign plan, and the revision write
no longer busts the G5 cache.

## Architectural Impact

- **New dependencies**: none.
- **Interface changes**: `find_plan_path` gains stricter resolution semantics (cross-repo bare-`#N` fallback rejected); a new sibling `compute_plan_body_hash` hashes the plan with **only the `revision_applied:` frontmatter key stripped** (all other frontmatter — and the entire body — is still hashed) for the G5 staleness input. `record_verdict`'s `_compute_artifact_hash` and `tools.sdlc_next_skill`'s `current_plan_hash` must use the **same** `revision_applied`-stripped hash so cached and current hashes are comparable. This narrow strip (vs. excluding all frontmatter) preserves the existing G5 contract that *other* frontmatter edits (status, type, tracking) bust the cache.
- **Coupling**: decreases — local `/do-sdlc` and the bridge/worker path converge on the same `SDLC_TARGET_REPO` contract instead of only the latter setting it.
- **Data ownership**: unchanged. Plans still owned by their tracking issue; this fixes *which* plan the router reads.
- **Reversibility**: high — env-var export and resolver-guard changes are small and isolated; the hash change is the only one with a stored-state interaction (see Risk 2).

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer (war-room critique)

**Interactions:**
- PM check-ins: 1-2 (confirm hash-migration approach and skill-portability scope)
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies. All resolution runs against the
local filesystem and `gh`/`git` CLIs already required by the SDLC pipeline.

## Solution

### Key Elements

- **`SDLC_TARGET_REPO` export from local `/do-sdlc`**: the supervision loop resolves the target
  repo's filesystem path once and exports it so every `sdlc-tool` subprocess (which forces cwd to
  `~/src/ai`) can resolve the *target* repo's plans, not the ai repo's. This is the primary fix —
  it makes resolution correct by default rather than relying on a heuristic.
- **Defensive resolver guards**: `find_plan_path` rejects the cross-repo bare-`#N` textual fallback
  (return None rather than a foreign plan), and `_resolve_target_repo` / `find_plan_path` refuse to
  silently degrade to the ai repo for a target-repo issue. Defense-in-depth so a missing env var
  fails *loud/None* rather than *silently wrong*.
- **Repo-portable global SDLC skills**: replace bare `from tools.X` / `python -m tools.X` /
  `cd ~/src/ai` invocations in the global SDLC skills with the cwd-independent `sdlc-tool` path or
  an `AI_REPO_ROOT`-anchored invocation, so they load the canonical ai-repo `tools/` even when run
  inside a target repo that ships its own `tools/`.
- **`revision_applied`-stripped plan hash for staleness**: the G5 staleness input hashes the plan
  with **only the `revision_applied:` frontmatter line removed** (the rest of the frontmatter and the
  full body are still hashed), so a `revision_applied: true` write does not bust the cache and
  re-stale a clean verdict — while any *other* edit (body prose, status, tracking) still busts it as
  before. Robustness layer that closes the "notes-only re-stale" feeder without regressing G5's
  sensitivity to real plan changes.

### Flow

`/do-sdlc` resolves target repo path → exports `SDLC_TARGET_REPO` → loop calls `sdlc-tool next-skill`
→ `find_plan_path` resolves the **correct** target-repo plan → `_parse_revision_applied` reads
`revision_applied: true` → router row 4c fires → `/do-build` dispatched → pipeline converges unattended.

### Technical Approach

- **Layer 1 (primary) — `SDLC_TARGET_REPO` propagation:**
  - In `.claude/skills-global/do-sdlc/SKILL.md` Step 2, alongside `SDLC_REPO` (the slug), resolve and
    export the target repo's **filesystem path** as `SDLC_TARGET_REPO` (e.g. `git rev-parse --show-toplevel`
    in the supervision cwd) for the lifetime of the loop. The slug (`SDLC_REPO`/`GH_REPO`) drives `gh`;
    the path (`SDLC_TARGET_REPO`) drives plan-dir resolution. Document that the two are distinct.
  - Verify the export reaches `sdlc-tool` subprocesses: `sdlc-tool` uses `exec`, so the child inherits
    the env — no wrapper change needed, but add a regression test asserting `SDLC_TARGET_REPO` is honored
    end-to-end when set and cwd is forced to `~/src/ai`.
- **Layer 1 (primary) — resolver hardening (`tools/_sdlc_utils.py`):**
  - `find_plan_path`: when the plans dir resolved via `_git_toplevel()` is the **ai repo** but the issue
    is a target-repo issue (heuristic: `SDLC_TARGET_REPO` unset *and* no `tracking:` match found), do not
    return the bare-`#N` `fallback` — return None. A None result is a *recoverable* signal (router can
    surface "plan not found"); a foreign plan is a *silent corruption*. Keep the `tracking:`-match path as
    the authoritative happy path.
  - Confirm the precedence in both `find_plan_path` and `_resolve_target_repo` is `SDLC_TARGET_REPO` first,
    then git-toplevel, then `__file__` fallback — and that the git-toplevel rung never wins for a
    target-repo issue when `SDLC_TARGET_REPO` is set.
- **Layer 2 (secondary) — skill portability:**
  - `do-plan-critique/SKILL.md:211`: replace `python -c "from tools.sdlc_verdict import compute_plan_hash; ..."`
    with an `AI_REPO_ROOT`-anchored invocation (mirror `scripts/sdlc-tool`'s `uv run --directory "$AI_REPO_ROOT"`)
    or a new `sdlc-tool` subcommand if a plan-hash CLI is warranted. Prefer the smallest change that loads the
    canonical ai-repo module.
  - Audit and convert the other bare invocations: `do-build/SKILL.md:490`, `do-build/PR_AND_CLEANUP.md:107,127`,
    `do-plan/SKILL.md:132,154`, `do-docs/SKILL.md:144,150`, `do-patch/SKILL.md:207`,
    `do-pr-review/sub-skills/post-review.md:224-226`. For each, decide: (a) genuinely needs the ai-repo `tools/`
    → anchor to `AI_REPO_ROOT`; or (b) operates on the *target* repo's plan file → keep cwd-relative but ensure
    the path is absolute. Document the disposition per call in the PR.
- **Layer 3 (robustness) — `revision_applied`-stripped plan hash:**
  - Add `compute_plan_body_hash` in `tools/sdlc_verdict.py` that removes **only the single
    `revision_applied:` frontmatter line** (the leading `---...---` block is parsed, the
    `revision_applied:` key is dropped, the frontmatter is re-serialized, and the body is left intact),
    then hashes with the same CRLF normalization as `compute_plan_hash`. This is the narrow scope from
    critique CONCERN 4 — **not** stripping all frontmatter. Every other frontmatter key (`status`,
    `type`, `tracking`, `last_comment_id`) and the entire body still contribute to the hash, so any real
    plan edit still busts G5's cache exactly as today. Implementation note: strip the line robustly
    (match `^revision_applied:` after the opening `---`, tolerate leading/trailing whitespace and
    `true`/`false` values) so a present-but-`false` and an absent key produce the **same** hash — the
    only thing that must not bust the cache is the `/do-plan` revision write flipping the key.
  - Both the **writer** (`_compute_artifact_hash` at `:120-133`, used by `record_verdict`) and the
    **reader** (`tools/sdlc_next_skill.py:98-104` setting `context["current_plan_hash"]`) must switch to
    `compute_plan_body_hash` together so cached `artifact_hash` and `current_plan_hash` remain comparable.
  - **Update `compute_plan_hash`'s module docstring (BLOCKER 2):** the docstring at
    `tools/sdlc_verdict.py:65-71` currently declares frontmatter edits (incl. `revision_applied: true`)
    "are meaningful plan changes that MUST bust the cache." That contract is now scoped: with
    `compute_plan_body_hash` driving G5, the **only** frontmatter key that does NOT bust the cache is
    `revision_applied:`; all other frontmatter still busts it. Update the docstring (and the
    "Artifact hash semantics" block) to state this precisely, and note that `compute_plan_hash` itself
    is retained for any callers that genuinely want the full-bytes hash. Add a task in Step 1.
  - **Migration / self-heal (BLOCKER 1):** with the narrow strip, a stored frontmatter-inclusive
    `artifact_hash` only mismatches the new `revision_applied`-stripped `current_plan_hash` for plans
    whose stored hash was computed *before* this deploy. For an issue that re-runs CRITIQUE or BUILD,
    `record_verdict` re-writes the hash and self-heals. **But an already-build-ready in-flight issue
    (cached READY-TO-BUILD verdict, no pending CRITIQUE/BUILD re-run) never re-fires the writer**, so
    its old full-bytes hash would mismatch indefinitely and G5 would keep cache-missing. To close this:
    inside `guard_g5_artifact_hash_cache` (`agent/sdlc_router.py:374`), when `cached_hash != current_hash`,
    before returning None (no-match) **transparently re-compare against the cached hash recomputed under
    the new scheme**: if the *stored plan file* hashed with the new `compute_plan_body_hash` equals
    `current_hash` AND the only difference from the stored `artifact_hash` is the `revision_applied` line,
    treat it as a match and **rewrite the stored `artifact_hash` to the new value via `record_verdict`'s
    single-writer path** (never a raw write). This makes the migration self-heal for build-ready
    in-flight issues on their first router pass, not only on a CRITIQUE/BUILD re-run. Document the
    transparent-rewrite path; no separate backfill script (the rewrite is the migration). A
    `sdlc-tool migrate-hashes` subcommand is the explicit fallback if the in-guard rewrite proves too
    coupled — but the in-guard transparent rewrite is preferred (smaller surface, no operator step).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `find_plan_path` swallows per-file read errors (`tools/_sdlc_utils.py:383-384,389-390`) — add a test that a
      malformed/unreadable plan file does not crash resolution and does not silently return a foreign plan.
- [ ] `_resolve_target_repo` catches `gh repo view` failures and returns None (`:79-88`) — assert the None path is
      reached (and logged) when `gh` fails, and that callers degrade safely.
- [ ] `compute_plan_body_hash` returns None on read failure (mirror `compute_plan_hash:107-112`) — assert G5 treats
      a None hash as "no cache" (cache miss), never a false match.
- [ ] `compute_plan_body_hash` on a plan with malformed/unterminated frontmatter (no closing `---`) — assert it
      degrades to hashing the file unchanged (or returns None) rather than crashing or stripping body lines.
- [ ] `guard_g5_artifact_hash_cache` transparent-rewrite path: a stored full-bytes `artifact_hash` whose only
      difference from the current plan is the `revision_applied:` line is treated as a match and rewritten via
      `record_verdict` — assert the second router pass is a clean cache hit (no re-dispatch).

### Empty/Invalid Input Handling
- [ ] `find_plan_path(0)` / `find_plan_path(None)` returns None (existing guard `:349-350`) — keep covered.
- [ ] Plan file with no frontmatter (`compute_plan_body_hash`): hashes the whole file unchanged — add a test.
- [ ] Plan file with `revision_applied: false` vs the same plan with the key **absent**: `compute_plan_body_hash`
      yields the **same** hash (the strip tolerates present-false and absent equivalently) — add a test.
- [ ] Plan file with frontmatter but no `revision_applied` key: `_parse_revision_applied` returns False — keep covered.
- [ ] `SDLC_TARGET_REPO` set to a non-existent path: `find_plan_path` returns None (plans_dir `.is_dir()` is False) rather than falling back to the ai repo — add a test.

### Error State Rendering
- [ ] When `find_plan_path` returns None for a target-repo issue (was a foreign plan before), the router surfaces a
      clear "plan not found / re-run /do-plan" signal rather than silently routing to critique forever — assert the
      router's blocked/dispatch reason is informative.

## Test Impact

- [ ] `tests/integration/test_sdlc_cross_repo_resolution.py` — UPDATE/EXTEND: add cases for (a) `SDLC_TARGET_REPO`
      set → correct target-repo plan; (b) unset + only a bare-`#N` mention in the ai repo → returns None, not the
      foreign plan; (c) unset + non-existent `SDLC_TARGET_REPO` → None.
- [ ] `tests/unit/test_sdlc_utils.py` — UPDATE: add `find_plan_path` cross-repo-fallback-rejection and
      precedence-order assertions; assert git-toplevel never overrides a set `SDLC_TARGET_REPO`.
- [ ] `tests/unit/test_sdlc_verdict.py` — UPDATE: add `compute_plan_body_hash` cases (only `revision_applied:`
      line stripped — other frontmatter keys still affect the hash; no-frontmatter passthrough; CRLF normalization;
      read-failure → None; present-`false` ≡ absent key); assert a `revision_applied: true`-only frontmatter edit
      yields an **unchanged** hash, while a `status:`/body edit yields a **changed** hash. Also assert the
      `compute_plan_hash` docstring no longer claims `revision_applied` busts the cache (BLOCKER 2 — parity/doc check
      if one exists, else covered by the behavioral test).
- [ ] `tests/unit/test_sdlc_env_vars.py` — UPDATE: assert `SDLC_TARGET_REPO` propagation contract from local `/do-sdlc`.
- [ ] `tests/unit/test_sdlc_next_skill.py` — UPDATE: assert `context["current_plan_hash"]` uses the body-only hash and
      that a frontmatter-only edit no longer busts G5 (cache hit → routes to build).
- [ ] `tests/unit/test_sdlc_router.py` / `test_sdlc_router_decision.py` — UPDATE: add the end-to-end convergence case —
      with-concerns verdict + `revision_applied: true` read from the correct plan → row 4c → `/do-build` in one pass.
      Also add the G5 transparent-rewrite migration case: cached full-bytes `artifact_hash` (pre-deploy) for a
      build-ready issue + current `revision_applied`-stripped hash → guard recognizes the `revision_applied`-only delta,
      rewrites the stored hash, and returns the cached READY-TO-BUILD dispatch (no spurious re-critique).
- [ ] `tests/unit/test_sdlc_skill_md_parity.py` — UPDATE if it asserts specific skill-invocation strings that change
      when bare `tools.X` calls are converted.

## Rabbit Holes

- **Re-architecting critique nit/concern classification.** The "with-concerns / nit-minting" critique behavior is a
  contributing irritant, not the binding cause. A clean with-concerns verdict must converge in one revision pass once
  the path bug is fixed. Do NOT touch `do-plan-critique` classification logic in this plan.
- **A general repo-resolution framework.** Resist building an abstraction over every cwd/env/git permutation. Fix the
  two concrete holes (unset `SDLC_TARGET_REPO` from local `/do-sdlc`; bare-`#N` cross-repo fallback) and stop.
- **Writing a standalone backfill script** for stored `artifact_hash` values. The in-guard transparent rewrite
  (Layer 3) self-heals build-ready in-flight issues on their first router pass, and CRITIQUE/BUILD re-runs self-heal
  the rest via `record_verdict`. A separate migration script is the explicit fallback only if the in-guard rewrite
  proves too coupled — do not build it pre-emptively.
- **Rewriting `sdlc-tool`'s `uv run --directory` behavior.** Forcing cwd to `~/src/ai` is correct and load-bearing for
  `tools/` resolution; the fix is to feed it the right `SDLC_TARGET_REPO`, not to change the wrapper's cwd discipline.

## Risks

### Risk 1: Converting bare `tools.X` calls breaks a skill that genuinely operates on the target repo's plan
**Impact:** A skill that should edit the *target* repo's plan file gets redirected to the ai repo's `tools/` and writes
to the wrong path, or vice versa.
**Mitigation:** Per-call disposition audit (Technical Approach, Layer 2): classify each invocation as "needs ai-repo
`tools/` logic" (anchor to `AI_REPO_ROOT`) vs "operates on target-repo plan file" (keep cwd-relative, ensure absolute
path). The `tools.X` *module code* always loads from the ai repo; only the *file argument* may be target-repo-relative.
Cover with the cross-repo integration test.

### Risk 2: `revision_applied`-stripped hash migration leaves build-ready in-flight issues stuck
**Impact:** A stored full-bytes `artifact_hash` (pre-deploy) mismatches the new `revision_applied`-stripped
`current_plan_hash`. For an issue that re-runs CRITIQUE/BUILD this self-heals via `record_verdict`. But an
**already-build-ready in-flight issue** (cached READY-TO-BUILD, no pending re-run) never re-fires the writer, so the
mismatch — and the G5 cache miss — would persist indefinitely (the original BLOCKER 1).
**Mitigation:** The Layer-3 in-guard transparent rewrite closes this: when `cached_hash != current_hash`,
`guard_g5_artifact_hash_cache` recomputes the stored plan under the new scheme and, if the only delta is the
`revision_applied` line, treats it as a match and rewrites the stored hash via `record_verdict`'s single-writer path.
This self-heals build-ready in-flight issues on their first router pass, not only on a CRITIQUE/BUILD re-run. The
narrow strip (only `revision_applied`, not all frontmatter) also keeps the mismatch surface minimal. Add a router test
asserting the build-ready migration case routes straight to build with no spurious re-critique. No backfill script.

### Risk 3: `SDLC_TARGET_REPO` export collides with the bridge/worker path
**Impact:** Double-setting or conflicting values between `agent/sdk_client.py:1590` and the new `/do-sdlc` export.
**Mitigation:** The two paths are mutually exclusive (bridge/worker sessions vs local `/do-sdlc` runs). The contract is
identical (absolute filesystem path to the target repo root). Add a test asserting both producers emit the same shape;
document the single contract in `docs/features/sdlc-tool-resolver.md`.

## Race Conditions

No race conditions identified. All resolution is synchronous, single-threaded, and filesystem/subprocess-based. The
G5 hash compare reads a committed plan file; the `/do-sdlc` loop is serial (one `next-skill` call at a time). The only
ordering concern — `revision_applied` write must land before the next `next-skill` read — is already enforced by the
serial supervision loop and the plan being committed+pushed in `/do-plan` Phase 4 before the loop advances.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG] Nothing deferred to a separate issue — every layer of the confirmed root cause (path resolution,
  skill portability, hash robustness) is in scope for this plan.

Nothing deferred — every relevant item (all three fix layers from the confirmed RCA) is in scope for this plan.

## Update System

The global SDLC skills (`.claude/skills-global/do-*`) are hardlinked to `~/.claude/skills/` on every machine by
`/update` (`scripts/update/hardlinks.py::sync_claude_dirs`). Editing the existing skill files in place is sufficient —
no new skill directory, so no `RENAMED_REMOVALS` entry is needed. `scripts/sdlc-tool` is also synced via the same path;
if a new `sdlc-tool` subcommand is added for plan-hashing, append it to `ALLOWED_SUBCOMMANDS` (already covered by the
existing sync). No new dependencies or config files. No migration steps for existing installations beyond the
self-healing hash re-dispatch (Risk 2). The `SDLC_TARGET_REPO` contract is an env var set at runtime — nothing to
install or propagate.

## Agent Integration

No new agent-facing capability. This is an internal SDLC-pipeline correctness fix. The agent already reaches this code
through the existing `sdlc-tool` CLI (invoked via Bash by `/do-sdlc` and the granite PTY sessions) and through
`agent/sdk_client.py` on the bridge/worker path. No new MCP server, no `.mcp.json` change, no new `[project.scripts]`
entry. The integration surface that matters is the `SDLC_TARGET_REPO` env contract between `/do-sdlc` and `sdlc-tool`,
covered by the cross-repo integration test (`tests/integration/test_sdlc_cross_repo_resolution.py`).

## Documentation

### Feature Documentation
- [ ] Update `docs/features/sdlc-tool-resolver.md` — document the `SDLC_TARGET_REPO` (path) vs `GH_REPO`/`SDLC_REPO`
      (slug) distinction, that local `/do-sdlc` now exports the path, and the resolver's refusal to fall back to a
      foreign plan for a target-repo issue.
- [ ] Update `docs/features/sdlc-pipeline-portability.md` — note the bare-`#N` cross-repo fallback rejection and the
      `revision_applied`-stripped plan hash for staleness (only the `revision_applied:` key is excluded; all other
      frontmatter still busts G5), plus the in-guard transparent-rewrite migration for build-ready in-flight issues.
      Close the "notes-only re-stale" loop it previously described.
- [ ] No new `docs/features/README.md` index row — both target docs already have rows; update their summaries in place.

### Inline Documentation
- [ ] Docstrings on `find_plan_path` (cross-repo fallback rejection rule) and `compute_plan_body_hash` (only the
      `revision_applied:` key is stripped; everything else is hashed).
- [ ] **Update `compute_plan_hash`'s module docstring + "Artifact hash semantics" block (`tools/sdlc_verdict.py:65-71`)**
      so it no longer claims `revision_applied: true` MUST bust the cache — state that G5 now uses
      `compute_plan_body_hash`, which excludes only `revision_applied:` (BLOCKER 2).
- [ ] Comment in `guard_g5_artifact_hash_cache` explaining the transparent-rewrite migration path.
- [ ] Comment in `/do-sdlc` Step 2 explaining the `SDLC_TARGET_REPO` (path) vs `SDLC_REPO` (slug) split.

## Success Criteria

- [ ] With `SDLC_TARGET_REPO` set to a target repo, `find_plan_path(N)` resolves the *target-repo* plan (not the ai repo).
- [ ] With `SDLC_TARGET_REPO` unset and only a bare-`#N` mention in the ai repo, `find_plan_path(N)` returns None (not the foreign plan).
- [ ] Local `/do-sdlc` Step 2 exports `SDLC_TARGET_REPO` as the target repo's filesystem path; verified end-to-end through a `sdlc-tool` subprocess (cwd forced to `~/src/ai`).
- [ ] A `revision_applied: true`-only frontmatter edit produces an **unchanged** `compute_plan_body_hash` → G5 cache hit → routes to build (no re-stale); a `status:`/body edit still **changes** it (G5 sensitivity preserved).
- [ ] A build-ready in-flight issue with a pre-deploy full-bytes `artifact_hash` self-heals on its first router pass via the in-guard transparent rewrite — routes straight to build, no spurious re-critique (BLOCKER 1).
- [ ] End-to-end: with-concerns critique verdict + `revision_applied: true` on the correct plan → router row 4c → `/do-build` in a single revision pass (the originally-stuck convergence case).
- [ ] All converted global SDLC skills load the canonical ai-repo `tools/` when run inside a target repo shipping its own `tools/` (no bare `from tools.X` / `python -m tools.X` / `cd ~/src/ai` in the SDLC skill set).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

The lead agent orchestrates; it never builds directly.

### Team Members

- **Builder (resolver-and-env)**
  - Name: `resolver-builder`
  - Role: Layer 1 — `SDLC_TARGET_REPO` export in `/do-sdlc`, resolver hardening in `tools/_sdlc_utils.py`
  - Agent Type: builder
  - Resume: true

- **Builder (skill-portability)**
  - Name: `skill-builder`
  - Role: Layer 2 — convert bare `tools.X` / `cd ~/src/ai` invocations in global SDLC skills
  - Agent Type: builder
  - Resume: true

- **Builder (hash-robustness)**
  - Name: `hash-builder`
  - Role: Layer 3 — body-only plan hash in `tools/sdlc_verdict.py`, switch writer + reader together
  - Agent Type: builder
  - Resume: true

- **Validator (sdlc)**
  - Name: `sdlc-validator`
  - Role: Verify all success criteria, run the cross-repo integration + router convergence tests
  - Agent Type: validator
  - Resume: true

- **Documentarian (sdlc-docs)**
  - Name: `sdlc-documentarian`
  - Role: Update `sdlc-tool-resolver.md` and `sdlc-pipeline-portability.md`
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. `revision_applied`-stripped plan hash + G5 migration
- **Task ID**: build-hash
- **Depends On**: none
- **Validates**: tests/unit/test_sdlc_verdict.py, tests/unit/test_sdlc_next_skill.py, tests/unit/test_sdlc_router.py
- **Assigned To**: hash-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `compute_plan_body_hash` in `tools/sdlc_verdict.py` that removes **only the `revision_applied:` frontmatter line** (not all frontmatter), re-serializes, then hashes with CRLF normalization. Tolerate present-`false` ≡ absent. Handle malformed/unterminated frontmatter gracefully.
- Switch `_compute_artifact_hash` (writer) and `tools/sdlc_next_skill.py` `current_plan_hash` (reader) to `compute_plan_body_hash` together.
- **Update `compute_plan_hash`'s module docstring + "Artifact hash semantics" block (`:65-71`)** so it no longer claims `revision_applied` busts the cache (BLOCKER 2); document that G5 now uses `compute_plan_body_hash`.
- **Add the in-guard transparent-rewrite migration** to `guard_g5_artifact_hash_cache` (`agent/sdlc_router.py:374`): on `cached_hash != current_hash`, if the stored plan hashed under the new scheme matches `current_hash` and the only delta is the `revision_applied` line, treat as a match and rewrite the stored `artifact_hash` via `record_verdict`'s single-writer path (BLOCKER 1). Add a code comment.
- Add tests: only `revision_applied:` stripped (other keys still affect hash); no-frontmatter passthrough; CRLF normalization; read-failure → None; present-`false` ≡ absent; `revision_applied`-only edit → unchanged hash; `status:`/body edit → changed hash; router transparent-rewrite migration → cache hit routes to build.

### 2. Resolver hardening + SDLC_TARGET_REPO export
- **Task ID**: build-resolver
- **Depends On**: none
- **Validates**: tests/unit/test_sdlc_utils.py, tests/integration/test_sdlc_cross_repo_resolution.py, tests/unit/test_sdlc_env_vars.py
- **Assigned To**: resolver-builder
- **Agent Type**: builder
- **Parallel**: true
- In `find_plan_path` (`tools/_sdlc_utils.py`): reject the cross-repo bare-`#N` fallback for a target-repo issue (return None, not a foreign plan); keep the `tracking:`-match happy path; assert precedence `SDLC_TARGET_REPO` → git-toplevel → `__file__`.
- In `.claude/skills-global/do-sdlc/SKILL.md` Step 2: resolve and export `SDLC_TARGET_REPO` (target repo filesystem path) for the supervision loop, alongside the existing `SDLC_REPO` slug; comment the path-vs-slug distinction.
- Add integration test asserting end-to-end resolution honors `SDLC_TARGET_REPO` when cwd is forced to `~/src/ai`.

### 3. Skill portability conversion
- **Task ID**: build-skills
- **Depends On**: none
- **Validates**: tests/unit/test_sdlc_skill_md_parity.py
- **Assigned To**: skill-builder
- **Agent Type**: builder
- **Parallel**: true
- Convert `do-plan-critique/SKILL.md:211` to an `AI_REPO_ROOT`-anchored `compute_plan_hash` invocation.
- Audit + convert the other bare invocations (`do-build:490`, `do-build/PR_AND_CLEANUP.md:107,127`, `do-plan:132,154`, `do-docs:144,150`, `do-patch:207`, `do-pr-review/post-review.md:224-226`); record per-call disposition (ai-repo `tools/` vs target-repo plan file) in the PR.

### 4. Validate
- **Task ID**: validate-sdlc
- **Depends On**: build-hash, build-resolver, build-skills
- **Assigned To**: sdlc-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all Verification commands and the cross-repo + router convergence tests.
- Confirm the end-to-end convergence success criterion (with-concerns + revision_applied → /do-build in one pass).
- Report pass/fail.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-sdlc
- **Assigned To**: sdlc-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/sdlc-tool-resolver.md` and `docs/features/sdlc-pipeline-portability.md` per the Documentation section.

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: sdlc-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all Verification commands; verify every success criterion (including docs) met.
- Generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_sdlc_utils.py tests/unit/test_sdlc_verdict.py tests/unit/test_sdlc_next_skill.py tests/integration/test_sdlc_cross_repo_resolution.py -q` | exit code 0 |
| Lint clean | `python -m ruff check tools/_sdlc_utils.py tools/sdlc_verdict.py tools/sdlc_next_skill.py` | exit code 0 |
| Format clean | `python -m ruff format --check tools/_sdlc_utils.py tools/sdlc_verdict.py tools/sdlc_next_skill.py` | exit code 0 |
| Body hash helper exists | `grep -c "def compute_plan_body_hash" tools/sdlc_verdict.py` | output > 0 |
| Hash strips only revision_applied | `grep -c "revision_applied" tools/sdlc_verdict.py` | output > 0 |
| G5 transparent-rewrite migration present | `grep -c "compute_plan_body_hash\|revision_applied" agent/sdlc_router.py` | output > 0 |
| No bare `from tools.sdlc_verdict` in critique skill | `grep -c "from tools.sdlc_verdict import" .claude/skills-global/do-plan-critique/SKILL.md` | match count == 0 |
| No bare `cd ~/src/ai` in build skill | `grep -c "cd ~/src/ai" .claude/skills-global/do-build/SKILL.md` | match count == 0 |
| `/do-sdlc` exports SDLC_TARGET_REPO | `grep -c "SDLC_TARGET_REPO" .claude/skills-global/do-sdlc/SKILL.md` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Revision pass addressing NEEDS REVISION (2026-06-22). -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER 1 | doctrine | "Self-heals in one cycle" is false for already-build-ready in-flight issues — `record_verdict` never re-fires for them, so a cached full-bytes `artifact_hash` mismatches the new hash indefinitely. | Layer 3 (Tech Approach) + Step 1 + Risk 2 + Success Criteria | Added in-guard transparent rewrite in `guard_g5_artifact_hash_cache`: on hash mismatch, recompute stored plan under new scheme; if delta is only the `revision_applied` line, rewrite stored hash via `record_verdict` and treat as match. `sdlc-tool migrate-hashes` named as explicit fallback. |
| BLOCKER 2 | doctrine | `compute_plan_hash` docstring (`:65-71`) declares frontmatter edits incl. `revision_applied` MUST bust the cache — Layer 3 reverses this. | Layer 3 + Step 1 + Inline Docs | Added explicit task to update the module docstring + "Artifact hash semantics" block to scope the contract: only `revision_applied:` is excluded (via `compute_plan_body_hash`); all other frontmatter still busts. |
| CONCERN 4 | doctrine | Scope the hash helper to strip ONLY `revision_applied:` rather than all frontmatter — dissolves Blocker 2, narrows Blocker 1, removes G5 regression risk. | Layer 3 (redesigned) + Architectural Impact + Key Elements + all hash references | `compute_plan_body_hash` now strips only the single `revision_applied:` line; other frontmatter and the full body still hash. Tests assert `status:`/body edits still bust the cache. |
| CONCERN (skill blast radius) | doctrine | Secondary skill-portability fix touches 6+ global SDLC skill files in one PR. | Open Question 2 + Rabbit Holes (no general framework) | Kept bundled per the binding-cause-cohesion argument; per-call disposition audit required (Risk 1). Confirmed acceptable as one PR. |
| CONCERN (fallback strictness) | doctrine | When `find_plan_path` returns None (was a foreign plan), router UX must be informative, not a silent re-dispatch loop. | Error State Rendering test + Open Question 3 | Plan asserts the router surfaces a clear "plan not found / re-run /do-plan" reason; covered by a failure-path test. |
| NIT | doctrine | Verification "Body-only hash exists" grep was loose (`def ...\|frontmatter`) and mislabeled "body-only". | Verification table | Replaced with precise greps: `def compute_plan_body_hash`, `revision_applied` presence, and the G5 migration marker; relabeled away from "body-only". |

---

## Resolved Questions (settled during critique revision)

1. **Hash scope** — RESOLVED (critique CONCERN 4): scope the helper to strip **only the `revision_applied:`
   frontmatter key** (`compute_plan_body_hash`), not all frontmatter. This dissolves the docstring-contract conflict
   (BLOCKER 2), narrows the migration surface (BLOCKER 1), and removes the G5 regression risk that excluding all
   frontmatter would introduce. The router-signal alternative was not chosen — the narrow hash strip is the
   smallest robust change and keeps G5 sensitive to every real plan edit.
2. **Skill-portability blast radius** — RESOLVED: keep all conversions in this one PR. The portability fix is
   cohesive with the binding cause (both are cross-repo `tools/` resolution); splitting it risks a fast-follow that
   never ships. Each converted call carries a per-call disposition note in the PR (Risk 1).
3. **Fallback rejection strictness** — RESOLVED: when `find_plan_path` returns None for a target-repo issue, the
   router surfaces an **informative** "plan not found / re-run /do-plan" dispatch reason (not a silent re-dispatch
   loop). Covered by the Error State Rendering failure-path test.
