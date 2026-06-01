---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-06-01
tracking: https://github.com/tomcounsell/ai/issues/1535
last_comment_id:
---

# SDLC Pipeline Portability: 7 Generic Robustness Defects

## Problem

Running the `/sdlc` pipeline end-to-end against a non-`ai` repo (the Cyndra
Bun/TS monorepo at `~/src/cyndra`) over three issues surfaced a cluster of
robustness defects in the SDLC router + tooling. Each issue required manual
nursing to advance: the pipeline twice hard-blocked itself on the G4
oscillation guard, and the terminal merge stage had no skill at all. None are
repo-specific — they are portability gaps that bite any repo that isn't
`~/src/ai`.

The through-line is **portability**: the engine assumes it runs in `~/src/ai`
with the full Python substrate present and the issue number written as `#N`.
The moment those assumptions break (any other repo, a URL-only plan, an
out-of-band PR, an absent validator), failures are *silent* and compound into
pipeline lock-ups that the G4 guard then makes worse by latching.

**Current behavior:**
- `find_plan_path()` searches `~/src/ai/docs/plans` when `SDLC_TARGET_REPO` is
  unset, so plan lookups in any other repo silently find nothing →
  `revision_applied` reads `false` forever → router re-proposes `/do-plan` →
  G4 latches the pipeline closed.
- A plan that references its issue only by tracking URL (`issues/145`) is
  invisible to the resolver (it greps for literal `#145`).
- Row-4c re-proposes `/do-build` on a completed+approved+clean PR.
- `_meta.pr_number` is neither set for out-of-band PRs nor settable.
- `same_stage_dispatch_count` (G4) has no reset path and latches closed after a
  transient mis-read, even once the underlying cause is corrected.
- There is no `/do-merge` skill anywhere in the general library — the merge gate
  had to be performed by hand.
- Forked sub-skills silently lag state and degrade to "skill-only mode" with no
  loud signal when the orchestration substrate is absent.

**Desired outcome:**
The generic pipeline runs unattended in any repo. A missing env var degrades to
"correct" instead of "silently wrong"; the router never routes a finished PR
back to build; G4 self-clears on real state transitions and has an operator
escape hatch; the merge stage has a real deterministic skill; and absent
substrate produces a loud, visible degraded-mode marker instead of silent lag.

## Freshness Check

**Baseline commit:** `8cc68d3f0d33df08013bb2dbf644f87296d9e4d6`
**Issue filed at:** 2026-06-01T06:30:36Z
**Disposition:** Unchanged

**File:line references re-verified (all against baseline):**
- `tools/_sdlc_utils.py:115-119` — defaults to `__file__`-based `~/src/ai` plans dir when `SDLC_TARGET_REPO` unset — still holds.
- `tools/_sdlc_utils.py:124,133` — `needle = f"#{issue_number}"`, substring `in text` match — still holds (and substring match also false-positives `#1455` for `145`).
- `agent/sdlc_router.py:651-658` — row-4c predicate `_rule_critique_ready_with_concerns_revision_applied` lacks the `pr_number` guard row-4a has at line 635 — still holds.
- `tools/sdlc_meta_set.py:50-53` — `_KEY_REGISTRY` whitelists only `plan_revising`, `plan_hash_at_build_start` — still holds.
- `tools/sdlc_stage_query.py:269-275,286` — `_compute_meta` resolves `pr_number` via `session.pr_number` or `_lookup_pr_number` (issue-search only); `compute_same_stage_count` called with no `current_snapshot` — still holds.
- `agent/sdlc_router.py:377-395,1076-1131` — `guard_g4_oscillation` blocks at `>=3`; `compute_same_stage_count` walks history snapshots only — still holds.
- `tools/sdlc_stage_marker.py:96-150` — `write_marker()` returns `{}` and `main()` exits 0 on every failure — still holds.
- No `/do-merge` skill at `.claude/skills-global/do-merge/`, `.claude/commands/do-merge.md`, or `~/.claude/skills/do-merge/` — confirmed absent. `.claude/hooks/validators/validate_merge_guard.py:53-60` expects `data/merge_authorized_{pr}` — still holds.

**Cited sibling issues/PRs re-checked:** Cyndra #139/#140/#144/#145/#146 are on the Cyndra repo (out of scope here, referenced as repro evidence only).

**Commits on main since issue was filed (touching referenced files):** None. `git log --since=<createdAt>` over all five core files returns empty.

**Active plans in `docs/plans/` overlapping this area:** None. The most recent SDLC-router plans (`worktree-parallel-sdlc.md`, completed) do not touch these resolution/routing paths.

**Notes:** Issue filed today; baseline is current HEAD. No drift.

## Prior Art

The SDLC router and its guards have evolved across several merged PRs. This
plan extends that lineage rather than reworking it.

- **PR #1044** (issue #1040): "router oscillation guards G1-G5" — introduced the guard stack and `same_stage_dispatch_count`/`_sdlc_dispatches` machinery this plan fixes (D5).
- **PR #1050** (issue #1043): "G6 terminal-merge-ready guard" — the fast-path that *sometimes* masks D3; D3 is the case where G6's preconditions (CI/merge-state/DOCS) are not all met but the PR is still finished enough that routing back to build is wrong.
- **PR #1240** (issue #1216): "consolidate SDLC routing to a single source of truth" — established `agent/sdlc_router.py` + `sdlc-tool next-skill` as canonical, and the SKILL.md parity test this plan must keep green.
- **PR #1409** (issue #1393): "multi-dev fan-out and DAG stage dispatch" — added `/do-merge` to `_SKILL_TO_STAGE` and the dispatch rules, but no skill was ever authored to back it (D6).

No prior attempt addressed plan-path portability, the G4 reset path, or the
missing merge skill. These are net-new fixes, not retries.

## Research

No relevant external findings — proceeding with codebase context. All seven
defects are internal to this repo's SDLC tooling; no external libraries, APIs,
or ecosystem patterns are involved.

## Data Flow

The router decision flows through three layers; each defect lives at a specific
hop:

1. **Entry point**: PM session invokes `sdlc-tool next-skill --issue-number N`
   (`tools/sdlc_next_skill.py`).
2. **State read**: `query_enriched()` (`tools/sdlc_stage_query.py`) loads the PM
   session's `stage_states`, then `_compute_meta()` derives `_meta`:
   - `pr_number` ← `session.pr_number` or `_lookup_pr_number(issue)` **[D4 read path]**
   - `revision_applied` ← `_find_plan_path(issue)` → `_parse_revision_applied()` **[D1/D2 — wrong/empty path makes this always false]**
   - `same_stage_dispatch_count` ← `compute_same_stage_count(raw_states)` **[D5 — no live-snapshot comparison]**
3. **Decision**: `decide_next_dispatch(stages, meta, context)` (`agent/sdlc_router.py`)
   evaluates guards G1–G7 then `DISPATCH_RULES` rows in order **[D3 — row-4c fires too early; D5 — G4 reads latched count]**.
4. **Output**: a `Dispatch`/`Blocked`/`MultiDispatch` JSON the PM acts on. The
   MERGE-stage dispatch (`/do-merge`) currently resolves to no skill **[D6]**.
   Out-of-band, the forked sub-skills write stage markers back via
   `tools/sdlc_stage_marker.py` **[D7 — silent no-op on failure]**.

## Architectural Impact

- **New dependencies**: none (no new libraries/services). One new skill
  directory under `.claude/skills-global/do-merge/`.
- **Interface changes**:
  - `find_plan_path()` gains a cwd-git-root resolution branch (same signature).
  - `sdlc_meta_set` `_KEY_REGISTRY` gains `pr_number` (additive).
  - `compute_same_stage_count()` gains reset-on-divergence semantics (same
    signature; behavior change scoped to the `current_snapshot` path).
  - `sdlc-tool dispatch` gains a `reset` subcommand (additive).
  - `sdlc_stage_marker` `main()` gains a non-zero exit on genuine writeback
    failure (behavior change for a load-bearing tool — see Risk 1).
- **Coupling**: unchanged. Fixes stay within the existing module boundaries.
- **Data ownership**: unchanged. `_pr_number` becomes a writable `stage_states`
  meta key alongside the existing `_plan_revising` / `_plan_hash_at_build_start`.
- **Reversibility**: high. Each defect is an independent, revertable change.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1-2 (one design confirmation on D1 precedence + D7 loud-failure semantics)
- Review rounds: 1

Seven small, independent fixes plus one new skill doc. The coding is bounded;
the risk is in the two behavior changes (G4 reset, stage-marker loud failure)
which need careful test coverage, not large surface area.

## Prerequisites

No prerequisites — this work has no external dependencies. All changes are
within SDLC tooling already present in the repo.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `gh` CLI authenticated | `gh auth status` | `/do-merge` skill and `_lookup_pr_number` shell out to `gh` |

Run all checks: `python scripts/check_prerequisites.py docs/plans/sdlc_1535_pipeline_portability.md`

## Solution

### Key Elements

- **Plan resolver (D1, D2)**: `find_plan_path()` derives the plans dir from the
  cwd git toplevel and matches both `#<n>` and `issues/<n>` references with a
  bounded regex.
- **Router precedence (D3)**: row-4c (and 4b) defer to downstream PR-stage rows
  once a PR exists or BUILD has completed.
- **PR-number recovery (D4)**: `pr_number` becomes settable via `meta-set` and,
  failing that, `_lookup_pr_number` resolves the open PR by branch head.
- **G4 reset (D5)**: the oscillation count resets when live state has moved past
  the last recorded dispatch snapshot, plus an explicit `dispatch reset`
  operator command.
- **Merge skill (D6)**: a portable `/do-merge` skill in the general library that
  performs the verify-then-merge gate and satisfies the merge-guard hook.
- **Loud degradation (D7)**: `sdlc_stage_marker` fails loudly on genuine
  writeback failure and emits a degraded-mode marker when the substrate is
  genuinely absent; `do-build` / `do-pr-review` gain the degraded-mode probe
  that `do-docs` already has.

### Flow

PM runs `/sdlc` in any repo → `next-skill` reads correct plan dir (cwd git
root) → `revision_applied` reads true → router routes forward (not back to
plan) → on a finished PR, routes to `/do-merge` (real skill) → merge gate runs
→ pipeline reaches Done unattended. If the substrate is absent, every stage
emits a visible "degraded — state not persisted" marker instead of silently
lagging.

### Technical Approach

**D1 — `find_plan_path()` repo-root resolution** (`tools/_sdlc_utils.py:105-137`)

New resolution order for the plans directory:
1. `SDLC_TARGET_REPO` env var, if set (explicit override wins — preserves
   backward compatibility for callers that already export it).
2. Else `git rev-parse --show-toplevel` of the cwd (via `subprocess`, 5s
   timeout, `cwd=Path.cwd()`); use `<toplevel>/docs/plans`.
3. Else the existing `__file__`-relative `~/src/ai/docs/plans` fallback.

Wrap each step so a failure (not a git repo, `git` missing) falls through to the
next. This satisfies acceptance #1 (no env var → cwd git root) while keeping the
env var as an intentional override.

> **Design decision (resolved, critique OQ1):** Ordering is **env var → cwd git
> root → ~/src/ai**. The issue text reads "derive from git toplevel (falling
> back to the env var, then ~/src/ai)", which would order git-root above the env
> var; this plan inverts that so an explicit `SDLC_TARGET_REPO` still wins as an
> override (its original purpose) while the no-env-var default becomes the cwd
> git root. Both orderings satisfy acceptance criterion #1; env-var-wins is the
> critique-endorsed choice because it preserves existing override semantics.

**D2 — tracking-URL plan match** (`tools/_sdlc_utils.py:124-134`)

Replace the substring `needle in text` check with a compiled regex that matches
either `#<n>` or `issues/<n>` with a trailing boundary so `145` does not match
`1455`:
```python
ref_re = re.compile(rf"(?:#|issues/){issue_number}(?![0-9])")
```
This covers the bare `#145`, the full URL `https://github.com/org/repo/issues/145`,
and the `issues/145` path form, and fixes the pre-existing substring
false-positive.

**D3 — row-4c precedence** (`agent/sdlc_router.py:641-658`)

Add the PR/BUILD-completion guard that row-4a already has to the two
concern-path predicates:
- `_rule_critique_ready_with_concerns_no_revision` (4b)
- `_rule_critique_ready_with_concerns_revision_applied` (4c)

Each returns `False` when `meta.get("pr_number")` is set **or**
`stage_states.get("BUILD") == STATUS_COMPLETED`. Once a PR exists or the build
is done, downstream rows (7 review, 8 patch, 9 docs, 10 merge) own routing. This
is the issue's "evaluate downstream stage completion / PR-clean state before the
revision-applied→build rule" implemented at the predicate layer (cleaner than
reordering the table, which the parity test cross-checks).

**D4 — `pr_number` set + branch-head recovery**
(`tools/sdlc_meta_set.py:50-83`, `tools/sdlc_stage_query.py:180-217,269-275`)

Two complementary changes:
1. Whitelist `pr_number` in `_KEY_REGISTRY` → `("_pr_number", int)`; add an
   `int` branch to `_coerce_value` (reject non-positive / non-numeric → return
   `{}`). Then `_compute_meta` reads `_pr_number` from `raw_states` as a
   resolution source: `session.pr_number` → `raw_states["_pr_number"]` →
   `_lookup_pr_number(...)`.
2. Extend `_lookup_pr_number` to fall back to a branch-head search when the
   issue-number search returns nothing. **(Critique BLOCKER fix)** The canonical
   SDLC branch is `session/{slug}` (`worktree_manager.py:687,820,1182`), NOT
   `session/sdlc-{issue_number}` (which does not exist — hardcoding it would be
   dead code). Resolve the slug from the PM session
   (`getattr(session, "slug", None)`); if present, search
   `gh pr list --head session/{slug} --state open --json number`. Constrain to
   `--state open`. As an optional secondary fallback when no slug is available,
   search `gh pr list --search "in:title,body #{issue_number}" --state open
   --json number`. This recovers out-of-band PRs whose body never referenced the
   issue. The issue-number search remains the primary path; branch-head is a
   fallback only when it returns nothing.

**D5 — G4 reset on real transition + explicit reset**
(`agent/sdlc_router.py:1076-1131,377-395`; `tools/sdlc_stage_query.py:280-289`;
`tools/sdlc_dispatch.py`)

1. `compute_same_stage_count(stage_states, current_snapshot=None)`:
   **(Critique CONCERN — scope precisely.)** The history walk already resets on
   recorded-snapshot divergence (`sdlc_router.py:1112-1122`) and the count is
   recomputed fresh every call — **leave lines 1112-1122 untouched; do NOT
   rewrite the walk loop.** The ONLY new behavior edits the
   `if current_snapshot is not None:` block (lines 1124-1128): when
   `current_snapshot` is provided and its canonical form **differs** from the
   most-recent history snapshot, the impending streak increment is broken —
   `return (0, skill)`. On match, keep the existing `count += 1`.
   `dispatch reset` (item 3) remains the escape hatch for the genuinely-latched
   recorded-history case.
2. `_compute_meta` builds the live snapshot
   (`build_stage_snapshot(raw_states, {"pr_number": pr_number})`) and passes it
   into `compute_same_stage_count`, so a stage/verdict correction recorded since
   the last dispatch resets the count → G4 stops firing.
3. Add `sdlc-tool dispatch reset --issue-number N` (new `_cli_reset` in
   `tools/sdlc_dispatch.py`) that clears `_sdlc_dispatches` via
   `update_stage_states`, as the explicit operator escape hatch the issue asks
   for. Document it in the G4 block reason string.

**D6 — `/do-merge` skill** (`.claude/skills-global/do-merge/SKILL.md`, new)

Author a portable skill that performs the deterministic merge gate:
1. Verify PR state via `gh pr view {pr}`: `state == OPEN`, `mergeable`, CI green
   (`statusCheckRollup` all SUCCESS), `mergeStateStatus == CLEAN`.
2. Verify REVIEW `APPROVED` (read `sdlc-tool verdict get --stage REVIEW`).
3. Verify body has `Closes #` linking the tracking issue.
4. Create the authorization file `data/merge_authorized_{pr}` (required by
   `validate_merge_guard.py:53-60`), squash-merge via `gh pr merge {pr}
   --squash`, then delete the auth file.
5. Reference repo-specific addenda already present at `docs/sdlc/do-merge.md`
   (ruff gates, plan migration, worktree cleanup) the same way other skills do.
   Update `docs/sdlc/do-merge.md:2` to point at the new skill instead of the
   retired `.claude/commands/do-merge.md`.

Auto-deploys: `scripts/update/hardlinks.py::_sync_skills` discovers any
`skills-global/*/SKILL.md` directory and hardlinks it to `~/.claude/skills/`
with no registration step.

**D7 — loud degradation** (`tools/sdlc_stage_marker.py:96-202`;
`.claude/skills-global/do-build/SKILL.md`, `do-pr-review/SKILL.md`)

1. `sdlc_stage_marker`: **(Critique CONCERN — tri-state, deterministic.)**
   Distinguish three cases, not two:
   - **ABSENT** (`ImportError` importing `models.agent_session`, or
     `redis.ConnectionError` / Redis unreachable): emit
     `{"status": "degraded", "stage": ..., "reason":
     "state not persisted — substrate absent"}` and **exit 0**. This is the
     loud, visible degraded-mode marker.
   - **PRESENT_NO_SESSION** (substrate imports and Redis reachable, but
     `_find_session` returns `None` — `sdlc_stage_marker.py:119-120`): emit the
     same degraded marker and **exit 0** (OQ2 resolved toward *quiet* — a
     session-less local/non-`ai` run is expected, not an error).
   - **PRESENT_WRITE_FAILED** (session resolved, but `start_stage` /
     `complete_stage` rejects or raises): print a clear stderr diagnostic and
     **exit non-zero** (mirror `sdlc_dispatch.py:173-186`), so a genuine
     writeback failure is loud rather than a silent no-op.
   - Keep the idempotent already-completed path
     (`sdlc_stage_marker.py:138-140`) returning **exit 0**.
2. `do-build` / `do-pr-review` SKILL.md: add the substrate-probe step that
   `do-docs` already documents (`status: "disabled"` → skill-only mode), so a
   forked sub-skill announces "running in degraded mode (state not persisted)"
   at the top of its run instead of silently lagging.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `tools/_sdlc_utils.py` `find_plan_path` — the new `git rev-parse`
  subprocess is wrapped; add a test asserting it falls through to the
  `__file__` fallback (not a crash) when `git` errors / cwd is not a repo.
- [ ] `tools/sdlc_stage_query.py` `_lookup_pr_number` — branch-head `gh`
  failure must return `None` (existing `except` returns `None`); add a test.
- [ ] `tools/sdlc_stage_marker.py` — the broadened `except` must now
  distinguish degraded (exit 0 + `status: degraded`) from genuine failure
  (exit non-zero + stderr); test both observable behaviors, not `pass`.

### Empty/Invalid Input Handling
- [ ] `find_plan_path` regex: test `issue_number` that is a prefix of a longer
  number in the file (`145` must not match `#1455`).
- [ ] `sdlc_meta_set` `pr_number`: empty string, `0`, negative, and
  non-numeric → return `{}` / exit 2 (invalid arg), never write garbage.
- [ ] `compute_same_stage_count`: empty/malformed `_sdlc_dispatches` → `(0, None)`.

### Error State Rendering
- [ ] `sdlc_stage_marker` degraded marker and loud-failure message must reach
  stdout/stderr respectively (the PM/operator sees them) — assert on captured
  output, not just exit code.
- [ ] `/do-merge` skill: a failed gate (not mergeable / CI red / not approved)
  must surface a clear reason and NOT create the auth file or call merge.

## Test Impact

- [ ] `tests/unit/test_sdlc_utils.py` — UPDATE: add cases for cwd-git-root
  resolution (no env var), `SDLC_TARGET_REPO` override precedence, git-failure
  fallback, and `issues/<n>` / URL matching + the `#1455`-vs-`145` boundary.
  Existing `find_session_*` tests are unaffected.
- [ ] `tests/unit/test_sdlc_router.py` / `test_sdlc_router_decision.py` —
  UPDATE: add D3 cases (4b/4c return False when `pr_number` set or
  `BUILD == completed`; a finished PR routes to review/docs/merge, never build).
- [ ] `tests/unit/test_sdlc_router_oscillation.py` — UPDATE: add D5 cases
  (count resets to 0 when live snapshot diverges from last history snapshot;
  G4 does not fire after a stage correction).
- [ ] `tests/unit/test_sdlc_meta_set.py` — UPDATE: `pr_number` accepted and
  coerced to int; invalid values rejected with exit 2.
- [ ] `tests/unit/test_sdlc_stage_query.py` — UPDATE: `_compute_meta` resolves
  `pr_number` from `_pr_number`; `same_stage_dispatch_count` reflects the
  live-snapshot reset; add branch-head `_lookup_pr_number` fallback case.
- [ ] `tests/unit/test_sdlc_stage_marker.py` — UPDATE: degraded-mode marker
  (exit 0 + `status: degraded`) vs loud failure (exit non-zero) — the existing
  "always exit 0" assertions must be REPLACED with the two-class contract.
- [ ] `tests/unit/test_sdlc_skill_md_parity.py` — UPDATE: confirms the guard
  table / dispatch rows still parse after D3 predicate edits; add `/do-merge`
  to any skill-existence assertion if present. No row removals.
- [ ] `tests/unit/test_do_merge_baseline.py`, `test_do_merge_review_filter.py` —
  REVIEW (likely UPDATE): these exercise merge-gate logic; verify they still
  pass against the new skill's gate and extend if they assumed a command path.
- [ ] New: `tests/integration/test_do_merge_skill.sh` or unit coverage for the
  `/do-merge` gate (auth-file create/cleanup, refuse-on-failed-gate). REPLACE
  any reliance on a hand-run merge.

## Rabbit Holes

- **Reordering `DISPATCH_RULES`** to fix D3. The parity test cross-checks row
  docstrings/ordering against SKILL.md; fix D3 at the *predicate* layer instead.
- **Generalizing the substrate probe into a shared framework** for D7. Three
  skills need a documented probe step; copy the `do-docs` pattern, don't build
  an abstraction.
- **Auto-resolving merge conflicts in `/do-merge`**. The gate verifies
  `mergeable`/`CLEAN` and stops; conflict resolution is explicitly out of scope.
- **Refactoring `compute_same_stage_count`'s snapshot machinery**. The reset is
  a narrow conditional on the existing `current_snapshot` path; resist rewriting
  `build_stage_snapshot`.
- **Making `pr_number` a first-class `AgentSession` field**. The meta-key path
  (`_pr_number` in `stage_states`) matches the existing `_plan_revising`
  pattern; a schema field is a bigger change than the defect warrants.

## Risks

### Risk 1: `sdlc_stage_marker` loud-failure breaks bridge-initiated sessions
**Impact:** `write_marker` currently always exits 0; many skills call it as a
fire-and-forget belt-and-suspenders backup. Making it exit non-zero on failure
could surface errors in bridge sessions where the hook is the *primary* marker
path and the CLI call is redundant.
**Mitigation:** Only exit non-zero when the substrate is **present** and the
write genuinely fails. When the substrate is absent (the non-ai-repo case this
issue targets), exit 0 with a degraded marker. Keep the idempotent
already-completed path returning success. Add explicit tests for the
substrate-present-success, substrate-present-fail, and substrate-absent paths.

### Risk 2: D1 precedence change surprises callers exporting `SDLC_TARGET_REPO`
**Impact:** If any caller relied on the env var being ignored, behavior shifts.
**Mitigation:** Env var keeps top precedence (override semantics preserved), so
existing exporters are unaffected; only the no-env-var default changes. Flagged
as Open Question 1 for explicit PM confirmation.

### Risk 3: branch-head PR lookup resolves the wrong PR
**Impact:** `gh pr list --head session/sdlc-{n}` could match a stale/closed PR.
**Mitigation:** Constrain to `--state open` and the issue-specific branch name
`session/sdlc-{issue_number}` (the canonical SDLC branch). The issue-number
search remains the primary path; branch-head is a fallback only when it returns
nothing.

## Race Conditions

### Race 1: concurrent `stage_states` writes (dispatch reset vs marker writeback)
**Location:** `tools/sdlc_dispatch.py` (new `reset`), `tools/sdlc_stage_marker.py`,
`tools/sdlc_meta_set.py` — all write `stage_states` on the same PM session.
**Trigger:** An operator runs `dispatch reset` while a forked sub-skill writes a
stage marker.
**Data prerequisite:** Both must read-modify-write the same `stage_states` dict.
**State prerequisite:** Neither write may clobber the other's keys.
**Mitigation:** All three already route through
`tools.stage_states_helpers.update_stage_states`, the optimistic-retry safe
writer. The new `reset` and `pr_number` writes MUST use the same helper (not a
direct `session.save()`), preserving the existing cross-process write contract.

## No-Gos (Out of Scope)

- [EXTERNAL] Cyndra-repo-specific customizations (modeling
  "merge ≠ deployed-to-customer-Mac" and the Notion Todo `Done` sync) — these
  live in a different repository (`~/src/cyndra`) that this agent's `~/src/ai`
  pipeline does not own. The issue footer explicitly scopes them to the Cyndra
  repo; this repo's issue #1535 covers only the seven generic defects.
- Nothing else deferred — every relevant generic-pipeline item (D1–D7) is in
  scope for this plan.

## Update System

The new `/do-merge` skill auto-propagates: `scripts/update/hardlinks.py`
(`_sync_skills`, invoked by `/update` on every machine) discovers any directory
under `.claude/skills-global/` containing a `SKILL.md` and hardlinks it to
`~/.claude/skills/`. No edit to `hardlinks.py`, the update script, or the update
skill is required — adding `.claude/skills-global/do-merge/SKILL.md` is
sufficient.

- `RENAMED_REMOVALS` already retires the old `commands/do-merge.md`
  (`hardlinks.py:50`), so there is no stale command file to collide with the new
  skill — no addition needed there.
- The Python tool changes (`_sdlc_utils`, `sdlc_router`, `sdlc_stage_query`,
  `sdlc_meta_set`, `sdlc_stage_marker`, `sdlc_dispatch`) ship via the normal
  `git pull` in `scripts/remote-update.sh`; `sdlc-tool` is already hardlinked to
  `~/.local/bin` via `USER_BIN_SCRIPTS`, so the new `dispatch reset` subcommand
  is available everywhere after `/update` with no extra step.

## Agent Integration

The agent reaches this functionality through the existing `sdlc-tool` CLI entry
point (already in `pyproject.toml [project.scripts]` / hardlinked to
`~/.local/bin`) and the skill library — both surfaces the PM session already
uses.

- No new CLI entry point in `pyproject.toml` is required; `dispatch reset` is a
  new subcommand of the existing `sdlc-tool dispatch`.
- No bridge (`bridge/telegram_bridge.py`) change is required — these are
  pipeline-internal tools invoked by the PM session via Bash, not new
  user-facing capabilities.
- `/do-merge` becomes invocable by the PM session as soon as the skill exists
  and `/update` runs; the router already dispatches `/do-merge` (it is in
  `_SKILL_TO_STAGE` and `DISPATCH_RULES`) — D6 supplies the missing skill body.
- Integration coverage: a shell/integration test that runs `sdlc-tool next-skill`
  end-to-end in a temp non-`ai` git repo (no `SDLC_TARGET_REPO`) and asserts the
  plan resolves and the router routes forward — this is the cross-repo smoke
  test that would have caught D1/D2/D3 originally.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/sdlc-pipeline-portability.md` describing the
  seven fixes and the "runs unattended in any repo" contract, cross-linking the
  router (`agent/sdlc_router.py`) and the resolver (`tools/_sdlc_utils.py`).
- [ ] Add an entry to `docs/features/README.md` index table.
- [ ] Update `docs/sdlc/do-merge.md:2` to reference the new
  `.claude/skills-global/do-merge/SKILL.md` instead of the retired command, and
  confirm its addenda (ruff gates, plan migration, cleanup) compose with the new
  skill.

### External Documentation Site
- [ ] Not applicable — this repo has no Sphinx/MkDocs site.

### Inline Documentation
- [ ] Update the module docstrings of `tools/_sdlc_utils.py` (resolution order),
  `tools/sdlc_meta_set.py` (new `pr_number` key), and
  `tools/sdlc_stage_marker.py` (degraded vs loud-failure contract).
- [ ] Update `agent/sdlc_router.py` `guard_g4_oscillation` docstring to mention
  the reset path and the `dispatch reset` escape hatch.
- [ ] Update `.claude/skills-global/sdlc/SKILL.md` Step 3.5/Step 4 only if the
  guard/row semantics description drifts (keep the parity test green).

## Success Criteria

- [ ] `find_plan_path()` resolves the plan from the cwd git root with no
  `SDLC_TARGET_REPO` set (D1).
- [ ] Plan resolution succeeds when the plan references the issue only by
  tracking URL (`issues/<n>`), and `#1455` does not match issue `145` (D2).
- [ ] `next-skill` routes a completed+approved+clean PR forward
  (REVIEW/DOCS/MERGE), never back to `/do-build` (D3).
- [ ] An out-of-band PR routes to REVIEW/MERGE without manual `/sdlc PR <n>`:
  `pr_number` is settable via `meta-set` and resolvable by branch head (D4).
- [ ] `same_stage_dispatch_count` resets on a real stage transition and an
  explicit `sdlc-tool dispatch reset` exists; G4 does not latch after a
  correction (D5).
- [ ] A `/do-merge` skill exists in `.claude/skills-global/` and performs the
  deterministic merge gate (verify → authorize → squash-merge → cleanup) (D6).
- [ ] Forked sub-skills emit a clear degraded-mode marker and
  `sdlc_stage_marker` never silently no-ops a stage writeback (D7).
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).
- [ ] grep confirms `.claude/skills-global/do-merge/SKILL.md` exists and creates
  `data/merge_authorized_` before calling `gh pr merge`.

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools.
The lead NEVER builds directly — they deploy team members and coordinate. The
seven defects group into three independent build streams that can run in
parallel, each followed by a validator.

### Team Members

- **Builder (plan-resolution)**
  - Name: `resolver-builder`
  - Role: D1 + D2 in `tools/_sdlc_utils.py` and resolver tests
  - Agent Type: builder
  - Resume: true

- **Builder (router-routing)**
  - Name: `router-builder`
  - Role: D3 + D4 + D5 in `agent/sdlc_router.py`, `tools/sdlc_stage_query.py`,
    `tools/sdlc_meta_set.py`, `tools/sdlc_dispatch.py`
  - Agent Type: builder
  - Resume: true

- **Builder (portability)**
  - Name: `portability-builder`
  - Role: D6 (new `/do-merge` skill) + D7 (`sdlc_stage_marker` loud failure,
    do-build/do-pr-review degraded probe)
  - Agent Type: builder
  - Resume: true

- **Validator (router)**
  - Name: `router-validator`
  - Role: Verify D3/D4/D5 against the router/oscillation/parity test suites
  - Agent Type: validator
  - Resume: true

- **Validator (full)**
  - Name: `full-validator`
  - Role: Verify all success criteria, run the cross-repo smoke test
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `docs-writer`
  - Role: Feature doc + index + inline docstrings + `docs/sdlc/do-merge.md` update
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

Using Tier 1 core agents (`builder`, `validator`, `documentarian`). No Tier 2
specialists required — this is bounded internal tooling work.

## Step by Step Tasks

### 1. Plan resolution (D1 + D2)
- **Task ID**: build-resolver
- **Depends On**: none
- **Validates**: `tests/unit/test_sdlc_utils.py`
- **Assigned To**: resolver-builder
- **Agent Type**: builder
- **Parallel**: true
- Add env-var → cwd-git-root → `__file__` resolution order to `find_plan_path`.
- Replace substring needle with the `(?:#|issues/){n}(?![0-9])` regex.
- Add unit tests: no-env cwd resolution, env override, git-failure fallback,
  `issues/<n>` match, `#1455`-vs-`145` boundary.

### 2. Router routing logic (D3 + D4 + D5)
- **Task ID**: build-router
- **Depends On**: none
- **Validates**: `tests/unit/test_sdlc_router.py`, `test_sdlc_router_decision.py`,
  `test_sdlc_router_oscillation.py`, `test_sdlc_meta_set.py`,
  `test_sdlc_stage_query.py`, `test_sdlc_skill_md_parity.py`
- **Assigned To**: router-builder
- **Agent Type**: builder
- **Parallel**: true
- D3: 4b/4c predicates return False when `pr_number` set or `BUILD == completed`.
- D4: whitelist `pr_number` (int) in `meta-set`; `_compute_meta` reads
  `_pr_number`; `_lookup_pr_number` branch-head fallback.
- D5: `compute_same_stage_count` resets on live-snapshot divergence;
  `_compute_meta` passes the live snapshot; add `sdlc-tool dispatch reset`.
- All `stage_states` writes go through `update_stage_states`.

### 3. Portability + degradation (D6 + D7)
- **Task ID**: build-portability
- **Depends On**: none
- **Validates**: `tests/unit/test_sdlc_stage_marker.py`,
  `tests/unit/test_do_merge_baseline.py`, new `/do-merge` gate test
- **Assigned To**: portability-builder
- **Agent Type**: builder
- **Parallel**: true
- Author `.claude/skills-global/do-merge/SKILL.md` (verify → auth-file →
  squash-merge → cleanup); update `docs/sdlc/do-merge.md:2` reference.
- `sdlc_stage_marker`: degraded marker (substrate absent, exit 0) vs loud
  failure (substrate present + write fails, exit non-zero + stderr).
- Add substrate-probe degraded-mode step to `do-build` and `do-pr-review`
  SKILL.md (mirror `do-docs`).

### 4. Validate router stream
- **Task ID**: validate-router
- **Depends On**: build-router
- **Assigned To**: router-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the router/oscillation/meta/stage-query/parity suites; confirm D3/D4/D5
  acceptance behaviors; report pass/fail.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: build-resolver, build-router, build-portability
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/sdlc-pipeline-portability.md` + README index entry.
- Update inline docstrings and `docs/sdlc/do-merge.md`.

### 6. Final validation
- **Task ID**: validate-all
- **Depends On**: build-resolver, validate-router, build-portability, document-feature
- **Assigned To**: full-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the full Verification table; run the cross-repo smoke test in a temp
  non-`ai` git repo; verify all seven acceptance criteria + docs; final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/ -x -q -k "sdlc or do_merge"` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| do-merge skill exists | `test -f .claude/skills-global/do-merge/SKILL.md` | exit code 0 |
| do-merge writes auth file | `grep -q 'merge_authorized_' .claude/skills-global/do-merge/SKILL.md` | exit code 0 |
| Plan resolver uses git root | `grep -q 'show-toplevel' tools/_sdlc_utils.py` | exit code 0 |
| URL plan match | `grep -q 'issues/' tools/_sdlc_utils.py` | exit code 0 |
| pr_number whitelisted | `grep -q 'pr_number' tools/sdlc_meta_set.py` | exit code 0 |
| dispatch reset exists | `python -m tools.sdlc_dispatch reset --help` | exit code 0 |

## Critique Results

**Verdict: NEEDS REVISION → REVISION APPLIED** — 1 blocker, 3 concerns, 0 nits (critics: Skeptic, Operator, Archaeologist, Adversary, Simplifier, User). Recorded via `sdlc-tool verdict record`. All findings have been folded into the Technical Approach (D1 hedge dropped, D4 branch-head corrected to `session/{slug}`, D5 scoped to the `current_snapshot` block only, D7 tri-state probe specified). The three Open Questions are resolved per the dispositions below. Plan is BUILD-ready.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Adversary, Skeptic | D4 branch-head fallback targets `session/sdlc-{issue_number}`, a branch shape that does not exist. Canonical SDLC branch is `session/{slug}` (`worktree_manager.py:687,820,1182`; this plan's own branch is `session/sdlc_1535_pipeline_portability`). As written, D4's branch-head path is dead code and Success Criterion #4 fails. | Addressed (revision applied) | Resolve slug from PM session (`getattr(session, "slug", None)`) and search `gh pr list --head session/{slug} --state open --json number`. Do NOT hardcode `sdlc-{n}`. Constrain to `--state open`. Optional secondary fallback: `gh pr list --search "in:title,body #{issue_number}"`. |
| CONCERN | Skeptic, Adversary | D5 wording conflates two reset paths. `compute_same_stage_count` already breaks the backward walk on recorded-snapshot divergence (`sdlc_router.py:1112-1122`); the count is recomputed fresh every call and does not persist. The new `current_snapshot` path only affects the impending "+1" turn (lines 1124-1128). Risk: builder rewrites the already-correct walk loop. | Addressed (revision applied) | State precisely: history walk already resets on recorded divergence (leave 1112-1122 untouched). New behavior edits only the `if current_snapshot is not None:` block (1124-1128): on divergence `return (0, skill)`; on match keep `count += 1`. `dispatch reset` remains the escape hatch for the genuinely-latched recorded-history case. |
| CONCERN | Operator | D7 substrate-present-vs-absent boundary is import-defined, but the genuinely ambiguous case is substrate imports + Redis reachable yet `_find_session` returns `None` (`sdlc_stage_marker.py:119-120`) — happens both in a non-`ai` repo (quiet, expected) and as a real wiring bug in `ai` (loud). Open Question 2 defers this; builder cannot implement the boundary deterministically. | Addressed (revision applied) | Define tri-state probe: ABSENT (ImportError/redis.ConnectionError → exit 0 degraded), PRESENT_NO_SESSION (exit 0 degraded — resolve OQ2 toward quiet), PRESENT_WRITE_FAILED (`start_stage`/`complete_stage` raises → exit non-zero + stderr). Keep idempotent already-completed path (138-140) exit 0. |
| CONCERN | Simplifier, User | Three unresolved Open Questions (OQ1 env-var precedence, OQ2 loud-failure threshold, OQ3 D4 scope) block deterministic build. OQ3 in particular means the builder cannot know whether D4 is one change or two. | Addressed (revision applied) | Bake resolutions into Technical Approach prose: OQ1 → env-var-wins (preserves override semantics); OQ2 → quiet on no-session (per D7 concern); OQ3 → ship both, with the branch-head path using the corrected `session/{slug}` form. Remove the "flag for PM" hedge once confirmed. |

### Open-Question dispositions (critique recommendations)
- **OQ1 (D1 precedence):** Env-var-wins ordering (env → cwd git root → `~/src/ai`) is endorsed — preserves `SDLC_TARGET_REPO` override semantics. Already correctly implemented as prose (lines 190-199); just drop the hedge.
- **OQ2 (D7 threshold):** Resolve toward **quiet** — "no session found, substrate present" exits 0 with a degraded marker to avoid noise on legitimately session-less local runs. Loud (exit non-zero) reserved for substrate-present + write-attempt-raises.
- **OQ3 (D4 scope):** Ship **both** — `meta-set` whitelist is the primary path; branch-head is a genuine fallback, but only after the BLOCKER fix (use `session/{slug}`).

---

## Open Questions

All three Open Questions are **resolved** (see the Open-Question dispositions
above). Retained here for traceability:

1. **D1 precedence — RESOLVED (env-var-wins):** Resolution order is
   **env var → cwd git root → ~/src/ai**. An explicit `SDLC_TARGET_REPO` keeps
   top precedence as an override; the no-env-var default is the cwd git root.
   Folded into D1 Technical Approach; hedge dropped.
2. **D7 loud-failure threshold — RESOLVED (quiet on no-session):**
   PRESENT_NO_SESSION exits 0 with a degraded marker (avoids noise on
   legitimately session-less local runs). Loud non-zero is reserved for
   PRESENT_WRITE_FAILED (substrate present + write raises). Folded into D7 as a
   tri-state probe.
3. **D4 scope — RESOLVED (ship both):** The `meta-set` whitelist is the primary
   path; the branch-head `_lookup_pr_number` fallback is a genuine secondary
   recovery, using the corrected `session/{slug}` form (BLOCKER fix). Folded into
   D4 Technical Approach.
