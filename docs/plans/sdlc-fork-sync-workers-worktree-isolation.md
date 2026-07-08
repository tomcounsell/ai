---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-07
tracking: https://github.com/tomcounsell/ai/issues/1915
last_comment_id: 4902211412
revision_applied: true
---

# SDLC Fork: Synchronous Workers + Single-Owner Worktree/Branch Identity

## Problem

A supervised 6-issue parallel SDLC batch (#1898 #1899 #1901 #1902 #1904 #1905) shipped every pipeline, but 4 of 6 needed manual supervisor recovery because the renovated `context: fork` dispatch path had three interlocking defects.

> **Revision note (freshness re-scan against HEAD `68c560040`):** **Defect 1 (phantom-wait) is already fixed on `main` by commit `8542ffb19` (2026-07-07).** That commit set `run_in_background: false` in do-build's builder dispatch, rewrote the dead Step 4 background-polling block, and added Hard Rule 6 + the explicit §3c foreground flag to do-sdlc. This revision therefore **narrows the plan to the two defects still live on `main` — Defect 2 and Defect 3 — plus a regression-guard test that locks 8542ffb19's fix in place.** The Defect-1 code edits originally written into Tasks 1/2 have been removed; a builder following them literally would hit Edit exact-string-match failures against the already-fixed code.

**Current behavior (as of HEAD `68c560040`):**

1. **Phantom-wait (Defect 1) — FIXED on main by `8542ffb19`.** Stage skills declare `context: fork` — a single, isolated, non-resumable subagent turn. Before the fix, do-build spawned builders with `run_in_background: [true if Parallel: true]` then polled/resumed for 15 min, and do-sdlc omitted the flag entirely (inheriting the tool's background default); a fork that ends its turn cannot be re-entered when a background child finishes, so builds were left uncommitted (#1904, #1901, #1902, #1898). `8542ffb19` closed this: builder dispatch is now `run_in_background: false` (`do-build/WORKFLOW.md:72`), the Step 4 polling block is rewritten as an in-turn verification (`WORKFLOW.md:98-104`), and do-sdlc carries Hard Rule 6 (`do-sdlc/SKILL.md:24`) + explicit `run_in_background: false` in §3c (`do-sdlc/SKILL.md:95`). **No remaining code change for Defect 1 — only a regression-guard test (Task 3).**

2. **Shared slug worktree, no lane seam (Defect 2) — STILL LIVE, and now the higher-traffic path.** `worktree_manager.py:883-884` templates BOTH the worktree dir (`.worktrees/{slug}`) and branch (`session/{slug}`) off one `slug`; there is no override. This is architecture-independent code the refactor did not touch, and the 5-day refactor *raised* its relevance: the production `dev` subagent operates in the session's single `.worktrees/{slug}/` and fans builders out into that one index ("one builder per worktree" — `dev.md:27` — with only one slug worktree available), so the shared-index exposure is now a production `dev`-fanout path, not only a local-supervisor concern. On the local-supervisor side, `.worktrees/sdlc-{N}` lane instructions are silently dropped because nothing reads them — the cross-issue lane-drop the batch actually hit (#1904, #1899, #1898). Note: `8542ffb19` made builders foreground but **concurrent** (multiple foreground Task calls in one message run simultaneously), so it did NOT serialize within-issue builders — the shared-index risk for `Parallel: true` tasks is reduced only by do-build's existing convention (mark `Parallel: true` only for tasks that write no shared files).

3. **Duplicate PRs (Defect 3) — STILL LIVE.** A fork branch and a supervisor branch produced two byte-identical PRs per issue (#1908/#1910, #1911/#1912). do-build's `gh pr create --head session/{slug}` (`PR_AND_CLEANUP.md:59`) has **no** pre-create existence check; the pipeline's only dedup probe is search-based (`gh pr list --search "#{issue}"`, `sdlc/SKILL.md:91`), which lags GitHub's search index. No `gh pr list --head` (live-ref) lookup exists anywhere.

**Desired outcome:**

- **Defect 1 stays fixed:** a regression-guard test asserts every `context: fork` skill keeps the no-background-then-exit invariant that `8542ffb19` established.
- **Slug identity always wins (Defect 2):** each issue's fork exclusively owns `.worktrees/{slug}` + `session/{slug}`. Supervisors stop allocating lanes. This converges fork + supervisor onto one branch per plan, which structurally collapses duplicate PRs (GitHub allows one open PR per head branch).
- **Deterministic PR reuse (Defect 3):** a live-ref `gh pr list --head` guard (carrying `--repo $TARGET_GH_REPO` for cross-repo builds) makes PR reuse deterministic instead of racing the search index.

## Freshness Check

**Baseline commit:** `68c560040` (HEAD at revision time)
**Issue filed at:** 2026-07-06T04:15:17Z
**Disposition:** **Major drift** — Defect 1 was fully landed on `main` between the original plan and this revision. Plan rescoped in place (issue not closed: Defects 2 and 3 remain live).

**Re-scan trigger:** Plan critique flagged that commit `8542ffb19` had landed the entire Defect-1 fix while the plan still asserted those sites were unmodified. This section re-verifies every file:line reference against HEAD `68c560040`.

**File:line references re-verified against HEAD:**
- `.claude/skills-global/do-build/WORKFLOW.md:72` — now `run_in_background: false` (was `[true if Parallel: true]`). **Fixed by `8542ffb19`.**
- `.claude/skills-global/do-build/WORKFLOW.md:76` — new bold rule forbidding background dispatch inside the fork. **Added by `8542ffb19`.**
- `.claude/skills-global/do-build/WORKFLOW.md:98-104` — the old 15-min poll/monitor/resume block is gone, replaced by an in-turn "results already in hand" verification. **Rewritten by `8542ffb19`.**
- `.claude/skills-global/do-build/SKILL.md:154` — orchestrator "Run parallel tasks together, always in the foreground" rule. **Added by `8542ffb19`.**
- `.claude/skills-global/do-sdlc/SKILL.md:24` — Hard Rule 6 ("ALWAYS dispatch with `run_in_background: false`"). **Added by `8542ffb19`.**
- `.claude/skills-global/do-sdlc/SKILL.md:95` — §3c now carries explicit `run_in_background: false`. **Fixed by `8542ffb19`.**
- `.claude/skills-global/do-build/PR_AND_CLEANUP.md:57-59` — `gh pr create --head session/{slug}` (with a `# For cross-repo builds, add: --repo $TARGET_GH_REPO` comment at :58) and still **no** preceding `gh pr list --head` dedup guard. **Still live (Defect 3).**
- `.claude/skills/sdlc/SKILL.md:91` — search-based `gh pr list --search "#{issue_number}"` probe (step 2c). **Still live (Defect 3).**
- `agent/worktree_manager.py:883-884` — `.worktrees/{slug}` + `session/{slug}` derivation. **Still present, intentionally left untouched (Defect 2 fixed by prose, not code).**
- `agent/agent_session_queue.py::resolve_branch_for_stage` — returns `session/{slug}`. **Still present, intentionally untouched.**

**Commits on main since issue was filed (touching referenced files):**
- `8542ffb19` — "Fix do-sdlc/do-build fork phantom-wait: force `run_in_background: false`" (2026-07-07) — **landed the complete Defect-1 fix.** Touched `do-build/SKILL.md`, `do-build/WORKFLOW.md`, `do-sdlc/SKILL.md` exactly as this plan originally proposed for Defect 1. Did NOT touch `PR_AND_CLEANUP.md`, `sdlc/SKILL.md`'s dedup probe, `worktree_manager.py`, or add any slug-ownership prose — Defects 2 and 3 are untouched.
- `e8351e4ca` — "Granite PTY teardown: headless `claude -p` session runner cutover (#1930)", plus its 5-day follow-ons (`#1935` zombie liveness, `#1937` interrupt-resume announcement removal, `#1938` subprocess-leak reap) — added `agent/session_runner/` and reworked `agent_session_queue.py`. Did not touch the fork stage skills' *code* or the PR-create sites, so every file:line reference below still holds. **But the refactor changed WHO orchestrates production SDLC, which reframes all three defects (see the architectural-reframe note directly below).**

**Architectural reframe (5-day session-runner refactor — governs how these defects are read):** Production SDLC no longer runs through a `context: fork` orchestrator. The PM session (headless `claude -p`) spawns a **resumable `dev` subagent** (`.claude/agents/dev.md`) that owns and drives the whole pipeline across turns and process restarts. `dev` is continuable by construction — it is NOT a fork, and it never invokes `/do-sdlc`; it calls the leaf `/do-*` skills (`/do-build`, `/do-plan-critique`, `/do-pr-review`, `/do-merge`) directly. This shifts each defect:
- **Defect 1 (phantom-wait):** obsolete at the *orchestrator* level — the resumable `dev` CAN receive the later turn a fork never could, so an orchestrator that "ends its turn waiting on children" is no longer a production failure mode. The residual exposure is narrow: `dev` still calls the leaf `context: fork` skill `/do-build`, which still gets one non-resumable turn and can still phantom-wait on *its own* builders. `8542ffb19` fixed exactly that leaf. Task 3's regression test guards the leaf forks, which is the right and still-live scope.
- **Defect 2 (shared slug worktree):** MORE relevant, not less. The `dev` subagent operates in the session's single `.worktrees/{slug}/` and fans builders out into it ("Fan out … one builder per worktree" — `dev.md:27`, while the session has exactly one slug worktree). So the shared-index exposure is now a first-class production `dev`-fanout path, not just a local-supervisor lane concern. See re-anchored Defect 2 / Risk 1.
- **Defect 3 (duplicate PRs):** root cause largely dissolved in production. The two-orchestrator duplication ("fork branch vs supervisor branch") required two concurrent orchestrators; production now has ONE `dev` per session on one `session/{slug}` head ("never spawn a second dev" — `dev.md` description). The remaining exposure is the local human-supervisor path (where the #1915 batch originated) and two humans/sessions colliding on one issue — a class demonstrably still live (`8351947d`, "Remove duplicate SDLC plan left by supervisor collision on #1938"). The `gh pr list --head` guard still earns its place, now for that narrower human-collision scenario.

**Cited sibling issues/PRs re-checked:**
- #1871 — OPEN — "SDLC router G5 fast-path dispatches /do-build while plan_revising=true" — explicitly a separate follow-up (out of scope here).
- #1908 (MERGED) / #1910 (CLOSED) — the duplicate PR pair produced for #1904's build. Confirms Defect 3 shipped real duplicates.
- #1911 (CLOSED) / #1912 (MERGED) — the duplicate PR pair for #1899's build. Same.

**Active plans in `docs/plans/` overlapping this area:** `docs/plans/completed/sdlc-fork-issue-number-divert.md` (completed — established the "fork loses session context" blind spot) and `docs/plans/completed/critique_await_all_critics_barrier.md` (completed — fixed the identical background-then-await bug in do-plan-critique via a filesystem roster barrier). Both are prior art, not live conflicts. No live plan touches do-build/do-sdlc dispatch.

**Disposition rationale:** Major drift, but the issue is NOT stale — `8542ffb19` fixed one of three defects. The right action per the freshness protocol is rescope-on-revised-premise (not close): drop the already-landed Defect-1 code edits, add a regression-guard test that would have caught a revert of `8542ffb19`, and proceed with the two live defects.

## Prior Art

- **Commit `8542ffb19` — "Fix do-sdlc/do-build fork phantom-wait: force `run_in_background: false`" (2026-07-07).** Landed the entire Defect-1 fix while this plan sat in critique: builder dispatch forced to `run_in_background: false`, the dead Step 4 background-polling block rewritten as in-turn verification, do-build/SKILL.md orchestrator rule updated, and do-sdlc given Hard Rule 6 + explicit §3c foreground flag. **This revision credits that commit and repositions Task 3 to lock its fix in with a regression-guard test rather than re-implement Defect 1 from scratch.** The commit did not touch Defects 2 or 3.
- **`docs/plans/completed/critique_await_all_critics_barrier.md`** — do-plan-critique had the exact same bug: it spawned critics with `run_in_background: true` and could return before they finished, dropping late findings. Fixed with a filesystem roster barrier that blocks the driver in-turn until every critic writes its artifact. Proven precedent for the "spawn → block-and-join in the same turn" pattern that `8542ffb19` applied to do-build/do-sdlc.
- **`docs/plans/completed/sdlc-fork-issue-number-divert.md`** — established and documented the "fork loses session context" failure class (`:17`, `:103`). Confirms the root model: a `context: fork` skill is a single non-resumable turn.
- **do-test `parallel-dispatch.md:56-76`** — the correct in-turn join barrier already in production: spawn N background suites, then block up to a timeout collecting every output, with a direct-execution fallback. A working reference implementation of "background for parallelism, but never end the turn with live children."
- `gh issue list --state closed --search "fork worktree background"` and `gh pr list --state merged --search "fork background worktree slug"` returned no other prior attempts at *this* worktree/dispatch problem — the phantom-wait + shared-worktree defects have not been fixed before.

## Research

No external research needed — this is a purely internal change to skill-orchestration prose and a Python enforcement test. No external libraries, APIs, or ecosystem patterns are involved. `gh` CLI behavior (one open PR per head branch; `--head` queries live refs vs `--search` hits the lagging index) is established from the codebase recon and standard `gh` semantics, not new research.

## Why Previous Fixes Failed

The identical background-then-await bug has now been fixed twice by point patches — first in do-plan-critique (`critique_await_all_critics_barrier.md`), then in do-build/do-sdlc (`8542ffb19`) — each applied **only to the skill that most recently burned a pipeline**, never generalized to the whole `context: fork` family.

| Prior Fix | What It Did | Why It Was Incomplete |
|-----------|-------------|-----------------------|
| `critique_await_all_critics_barrier.md` | Replaced background+await with an in-turn filesystem barrier in do-plan-critique | Point fix — never propagated to do-build or do-sdlc |
| `8542ffb19` | Forced `run_in_background: false` in do-build + do-sdlc; rewrote dead poll loop | Point fix again — covers only the two skills in the #1915 batch; **has no test guarding against a revert**, and left pthread / do-pr-review (also `context: fork`) relying on the tool default with no mechanical guard |
| `sdlc-fork-issue-number-divert.md` | Documented the "fork loses context" blind spot | Documentation only — no structural guard |

**Root cause pattern:** The `context: fork` execution model (single non-resumable turn) has no enforced invariant that a fork must reach terminal state within its own turn. Each skill is trusted to background responsibly, and each is fixed individually — with no regression guard — only after it burns a pipeline. The durable fix is a **generalized invariant + an enforcement test that scans every `context: fork` skill** (not just the two `8542ffb19` touched), which is exactly the regression gap Task 3 closes.

## Data Flow

1. **Entry point (production):** the PM session (headless `claude -p`) spawns a resumable `dev` subagent that drives the pipeline and calls the leaf `/do-*` skills (`/do-build`, etc.) directly — each leaf runs as a `context: fork` subagent turn. **Local-supervisor path:** a human runs `/do-sdlc` (itself `context: fork`) to supervise a full run, or `/sdlc` to dispatch ONE stage. The three defects below live in the leaf fork skills and the shared worktree, so they surface on BOTH paths; the fixes are path-agnostic.
2. **do-build fork:** derives `{slug}` from the plan filename → creates `.worktrees/{slug}` on `session/{slug}` (`worktree_manager` / `git worktree add`) → deploys builder Task subagents pointed at that worktree.
3. **Defect 1 — FIXED on main (`8542ffb19`):** builders now dispatch `run_in_background: false`; their results are in hand when the Task call returns; the fork proceeds to commit/push/PR in the same turn. The regression risk is a future edit reverting this — which Task 3's test guards.
4. **Current failure — Defect 2 (still live):** supervisor `.worktrees/sdlc-{N}` lane instructions are silently dropped (nothing reads them); each issue's builders land in `.worktrees/{slug}`. Concurrent `Parallel: true` foreground builders can still interleave in that shared index — mitigated by do-build's convention (mark `Parallel: true` only for non-shared-file tasks).
5. **Current failure — Defect 3 (still live):** fork opens PR on `session/{slug}`; a supervisor's separate branch opens a second PR; the only guard (`sdlc/SKILL.md:91` search probe) missed it due to index lag.
6. **Output (fixed):** slug-wins prose makes each issue's fork own its own `.worktrees/{slug}` + `session/{slug}` (no supervisor lanes); a `gh pr list --head session/{slug}` check (with `--repo $TARGET_GH_REPO` for cross-repo) reuses any existing PR; one branch → one PR; the fork reaches terminal state (committed, pushed, PR open/reused) before returning — the Defect-1 turn-boundary behavior already landed.

## Architectural Impact

- **Execution model (post 5-day refactor):** Production SDLC runs via the resumable `dev` subagent (PM-spawned, continuable across turns and restarts), not a `context: fork` orchestrator. `dev` calls the leaf `context: fork` skills (`/do-build`, `/do-plan-critique`, `/do-pr-review`, `/do-merge`) directly. The `/do-sdlc` fork orchestrator survives only as the *local-supervisor* entry point. These fixes therefore protect (a) the leaf `/do-build` fork calls made by both `dev` and local supervisors, and (b) the local `/do-sdlc` path — not a production fork orchestrator, which no longer exists. The plan deliberately does NOT re-architect resumption (see Rabbit Holes).
- **New dependencies:** None.
- **Interface changes:** None to Python signatures. `worktree_manager.create_worktree` / `get_or_create_worktree` and `resolve_branch_for_stage` are **left unchanged** — the "slug identity always wins" decision uses the existing slug→worktree→branch derivation as the single source of truth rather than adding an override seam.
- **Coupling:** Decreases. Removes the implicit (broken) coupling between forks and a nonexistent cross-turn resumption path. Removes the supervisor's dropped-on-the-floor lane-assignment coupling by declaring slug the sole identity.
- **Data ownership:** Clarified — each issue's fork exclusively owns `.worktrees/{slug}` + `session/{slug}`. No shared ownership across sessions.
- **Behavioral change:** Defect-1's turn-boundary behavior already shipped (`8542ffb19`) — builders run foreground (concurrent for `Parallel: true`), joined before the turn advances. This plan adds only slug-ownership prose + a PR-dedup guard + a regression test; no further change to build concurrency.
- **Reversibility:** High — all changes are skill-markdown prose + one additive test. Revert is a git revert of the (small) skill edits; `8542ffb19` is independent and stays.

## Appetite

**Size:** Small

**Rationale for downsizing from Large:** The original Large sizing assumed all three defects needed code work. With Defect 1 already landed by `8542ffb19`, the remaining surface is: a ~5-line PR-guard insert (Defect 3), a slug-ownership prose note in two skill files (Defect 2), one additive enforcement test, and one feature doc. Roughly 3 of the original 7 Success Criteria are already satisfied on `main`. No Python signatures change; no `worktree_manager.py` edits. This is a Small.

**Team:** Solo dev, PM, code reviewer

**Interactions:**
- PM check-ins: 1 (confirm slug-wins over lane seam; the serialize-vs-parallel decision is moot — `8542ffb19` already committed the pipeline to concurrent-foreground builders)
- Review rounds: 1-2 (narrow: PR-guard prose + one test + slug-ownership note)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `gh` authenticated | `gh auth status` | PR-guard instructions and prior-art checks rely on `gh` |
| Repo clean on `main` | `git rev-parse --abbrev-ref HEAD` | Skill edits + test land via normal build branch off main |

No external secrets or services required.

## Solution

### Key Elements

- **Fork turn-boundary invariant (already enforced in prose by `8542ffb19`; this plan makes it test-enforced):** "A `context: fork` skill NEVER ends its turn with a live background child. Dispatch workers `run_in_background: false` (or block-and-join every background child in-turn) before proceeding to any commit/push/PR/cleanup step." The prose already lives in do-build/WORKFLOW.md:76, do-build/SKILL.md:154, and do-sdlc Hard Rule 6 — **no re-edit needed.**
- **Slug identity always wins (Defect 2):** do-sdlc / sdlc skill docs declare that each issue's fork owns `.worktrees/{slug}` + `session/{slug}`; supervisors must NOT allocate `.worktrees/sdlc-{N}` lanes. No worktree/branch override seam is added; `worktree_manager.py` and `resolve_branch_for_stage` are untouched.
- **Live-ref PR dedup guard (Defect 3):** do-build runs `gh pr list --head session/{slug} --state open` (with `--repo $TARGET_GH_REPO` for cross-repo builds) immediately before `gh pr create`; if a PR exists, it reuses that PR number and skips create. The sdlc router's search-based probe (`sdlc/SKILL.md:91`) gains a `--head` cross-check note so its assessment doesn't lag the index.
- **Regression-guard test (locks in `8542ffb19` + closes CONCERN 2):** a unit test that scans **every** `context: fork` skill (do-build, do-sdlc, sdlc, pthread, do-pr-review, do-plan-critique, …) for the forbidden background-dispatch pattern, plus specific positive assertions for the two skills the batch implicated.

### Flow

Plan ready → `/sdlc` dispatches do-build fork → fork creates `.worktrees/{slug}` on `session/{slug}` → deploys builders `run_in_background: false` (already landed by `8542ffb19`; concurrent foreground for `Parallel: true` tasks, all joined before the turn advances) → after all builders return, fork verifies commits exist → `gh pr list --head session/{slug}` (reuse if present, else `gh pr create`) → fork returns having reached terminal state (committed, pushed, PR open/reused). Supervisor never allocates a lane; the next issue's fork owns its own `.worktrees/{its-slug}`.

### Technical Approach

- **Defect 1 — NO code change (landed by `8542ffb19`).** The builder spawn, Step 4 rewrite, do-build/SKILL.md orchestrator rule, and do-sdlc Hard Rule 6 + §3c are all in place on `main`. Task 3's test is the only Defect-1 work: it guards against a revert.
- **Defect 2 — slug-wins decision (`do-sdlc/SKILL.md`, `sdlc/SKILL.md`):** add a "Worktree & branch ownership" note declaring slug the sole identity; supervisors do not pre-allocate `.worktrees/sdlc-{N}` lanes. No changes to `worktree_manager.py` or `resolve_branch_for_stage` — the existing derivation IS the invariant. (Note: `8542ffb19` made builders foreground but **concurrent**, so it did not serialize within-issue builders; the shared-index risk for `Parallel: true` tasks is left to do-build's existing "no shared-file writes for parallel tasks" convention — see Risk 1. This plan does not add serialization, matching the concurrent-foreground model `8542ffb19` committed to.)
- **Defect 3 — live-ref PR guard (`do-build/PR_AND_CLEANUP.md:56-59`):** before `gh pr create --head session/{slug}`, insert (mirroring the existing cross-repo `--repo $TARGET_GH_REPO` convention two lines above it):
  ```bash
  # Reuse an existing open PR for this head (live-ref, no search-index lag).
  # Cross-repo builds MUST include --repo $TARGET_GH_REPO on BOTH the list and create.
  EXISTING_PR=$(gh pr list --head session/{slug} --state open --json number -q '.[0].number')  # add: --repo $TARGET_GH_REPO
  if [ -n "$EXISTING_PR" ]; then
    echo "Reusing PR #$EXISTING_PR"
  else
    gh pr create --head session/{slug} ...   # add: --repo $TARGET_GH_REPO
  fi
  ```
  Omitting `--repo` on the `gh pr list --head` guard reopens Defect 3 for cross-repo builds (the guard would query the wrong repo, always see "no PR," and always create a duplicate) — so the `--repo` flag is mandatory on the guard, not optional. Structural backstop: with slug-wins, fork + supervisor converge on one `session/{slug}` head, and GitHub itself permits only one open PR per head branch.
- **Defect 3 (router side) — `sdlc/SKILL.md:91`:** add a `# Cross-check with: gh pr list --head session/{slug} --state open (live refs; --search lags the index)` note beside the existing search probe so the router's PR-existence assessment can corroborate against live refs. (The search probe stays — it's keyed by issue number, which the `--head` query can't replace; the note makes the lag explicit.)
- **Enforcement test (`tests/unit/test_sdlc_fork_no_background.py`) — resolves Open Question 3:** discover **all** `context: fork` skill files (glob `.claude/skills-global/**/SKILL.md` + `.claude/skills-global/do-build/WORKFLOW.md` + `.claude/skills/**/SKILL.md`, filter to those with `context: fork` frontmatter or, for multi-file skills like do-build, the dispatch file). For **every** discovered fork skill assert the file exists, is non-empty, and contains no un-joined background dispatch — specifically no literal `run_in_background: true` and no `run_in_background: [true if Parallel:` template **in a dispatch position** (a prose mention like do-plan-critique's "never `run_in_background: true`" must NOT trip the test — match the `run_in_background:` key inside a Task/Agent call block, or exclude lines where the token is negated/quoted). Then add targeted positive assertions: (a) do-build/WORKFLOW.md contains `run_in_background: false`; (b) do-build/PR_AND_CLEANUP.md contains `gh pr list --head` positioned before `gh pr create`; (c) do-sdlc/SKILL.md contains `run_in_background: false` and Hard Rule 6's text. Failure messages must name the offending file and pattern.

## Failure Path Test Strategy

### Exception Handling Coverage
- The touched Python surface is one new test file; it introduces no `except Exception: pass` blocks. The skill edits are markdown prose (no runtime exception paths). State: **No exception handlers in scope** beyond the new test's own file-read guards (which assert-fail loudly if a skill file is missing, never swallow).

### Empty/Invalid Input Handling
- The enforcement test must fail loudly (not skip) if a scanned skill file is missing or empty — an empty/missing skill file must be treated as a test failure, not a silent pass. Add an explicit assertion that each scanned path exists and is non-empty before pattern-matching.

### Error State Rendering
- The user-visible failure mode being fixed IS a silent-loop / stranded-turn class (fork ends without committing). The enforcement test is the guard that the fix stays in place; its failure message must name the offending skill file and the forbidden pattern so a future regression is diagnosable at a glance.

## Test Impact

- [x] `tests/unit/test_sdlc_router_decision.py` — verify-only: confirm no assertion depends on the sdlc/SKILL.md PR-probe phrasing (this plan only adds a comment beside it); router decision logic is unchanged, so this is a read-through, likely no edit.
- [x] `tests/unit/test_worktree_manager.py` — no change expected: `worktree_manager.py` is intentionally left untouched (slug-wins reuses existing derivation). Listed to confirm the builder does NOT accidentally modify worktree signatures.
- [x] `tests/unit/test_agent_session_queue.py` — no change expected: `resolve_branch_for_stage` is left untouched. Listed as a guard against accidental branch-derivation changes.

No existing test asserts the do-build/do-sdlc orchestration prose today, so the new `tests/unit/test_sdlc_fork_no_background.py` is additive and does not replace prior coverage.

## Rabbit Holes

- **Adding a worktree/branch override seam** (threading `worktree_name`/`branch_name` through `create_worktree`, `get_or_create_worktree`, `resolve_branch_for_stage`, and do-build). Tempting to "do it properly," but it re-introduces the lane-allocation complexity the slug-wins decision deliberately removes, and every seam is a new corruption surface. Slug-wins is simpler. Only pursue the seam if PM explicitly wants true per-builder parallelism (Resolved Question 1 — declined).
- **Per-builder worktrees with branch-merge-back.** Real parallelism means N worktrees + N branches + a merge/rebase step back onto `session/{slug}` — a whole coordination protocol. Out of scope; rely on the concurrent-foreground model `8542ffb19` landed + do-build's disjoint-file convention.
- **Re-editing the already-fixed Defect-1 sites.** `8542ffb19` landed `run_in_background: false` in do-build/WORKFLOW.md, the Step 4 rewrite, do-build/SKILL.md's orchestrator rule, and do-sdlc Hard Rule 6 + §3c. Applying the original plan's Defect-1 edits would fail Edit exact-string-match against fixed code. Only add the regression test (Task 3).
- **Rewriting the whole fork execution model / making forks resumable.** The 5-day headless session-runner refactor (#1930 + #1935/#1937/#1938) already moved production orchestration to the resumable `dev` subagent; do not re-architect resumption on top of that. The leaf-fork invariant (no live children at turn end) works within the current model and is all this plan guards.
- **Fixing the 5 "also observed" batch follow-ups** (TEST marker, plan-artifacts-on-main, do-test WARN regex, meta-set `revision_applied`, recon-gate-on-reflection-issues). Each is a separate concern; see No-Gos.

## Risks

### Risk 1: Concurrent builders (dev fanout or supervisor) can still interleave the shared slug index
**Impact:** The 5-day refactor makes this the higher-traffic exposure. The production `dev` subagent fans builders into the session's single `.worktrees/{slug}/` ("one builder per worktree" while only one slug worktree exists — `dev.md:27,35`); likewise `8542ffb19` made do-build's builders foreground but concurrent (multiple foreground Task calls in one message). In either case two `Parallel: true` builders that both `git add`/`commit` in `.worktrees/{slug}` can interleave staging — the within-issue half of the original Defect 2. This plan does NOT serialize them or add per-builder worktrees (that would fight the just-landed concurrent-foreground decision and re-introduce the lane-allocation complexity slug-wins removes).
**Mitigation:** do-build's existing convention already gates this: `Parallel: true` is only valid for tasks that write no shared files (pthread's own rule: "if two subtasks would write to the same working tree, serialize them or give each isolation"). Slug-wins removes the cross-issue lane-drop that the batch actually hit. If within-issue concurrent-index corruption is later observed in practice — now more likely to surface via the `dev`-fanout path than the retired supervisor-lane path — true per-builder-worktree isolation is the escape hatch, filed as its own plan (see No-Gos), not bundled here. Keeping scope Small.

### Risk 2: Prose invariants are advisory; the model may still background
**Impact:** A skill instruction saying "never background" can be ignored by the executing model under load.
**Mitigation:** The enforcement test (`test_sdlc_fork_no_background.py`) makes the invariant structural at the *skill-file* level (the forbidden pattern cannot be committed), and slug-wins + GitHub's one-PR-per-head rule make Defect 3 structural at the *platform* level. The prose is backed by two mechanical guards.

### Risk 3: `gh pr list --head` guard races a truly-simultaneous second creator
**Impact:** Two forks calling `gh pr list --head` at the same microsecond both see "no PR" and both call `gh pr create`.
**Mitigation:** GitHub rejects the second `gh pr create` for an already-open head branch ("a pull request already exists for session/{slug}") — the create itself is the atomic guard; the `--head` pre-check just makes the common case graceful. With slug-wins there is only ever one head per plan, so the second creator's create fails cleanly rather than duplicating.

## Race Conditions

### Race 1: Concurrent builders mutating the shared slug git index
**Location:** `.claude/skills-global/do-build/WORKFLOW.md:44-104` (concurrent-foreground builder dispatch into `.worktrees/{slug}`)
**Trigger:** Two `Parallel: true` build tasks both `git add`/`git commit` in `.worktrees/{slug}` within the same window.
**Data prerequisite:** Each builder's file edits must be staged and committed atomically without another builder's edits interleaving.
**State prerequisite:** The git index of `.worktrees/{slug}` must have exactly one writer at a time for a given file set.
**Mitigation (partial, by convention — not eliminated by this plan):** `8542ffb19` chose concurrent-foreground, so this race is NOT eliminated by construction. It is bounded by do-build's convention that `Parallel: true` is only set for tasks writing disjoint file sets. Full elimination (per-builder worktrees) is deliberately out of scope (see No-Gos / Risk 1). Slug-wins eliminates the *cross-issue* variant (supervisor lanes no longer contend for a foreign slug worktree).

### Race 2: Fork vs supervisor both creating a PR for the same issue
**Location:** `.claude/skills-global/do-build/PR_AND_CLEANUP.md:52-59`
**Trigger:** Two dispatch paths reach `gh pr create` for the same issue near-simultaneously.
**Data prerequisite:** At most one open PR should exist per issue's build branch.
**State prerequisite:** Both creators must resolve to the same head branch so GitHub's one-PR-per-head rule applies.
**Mitigation:** Slug-wins converges both on `session/{slug}` (same head) + `gh pr list --head` pre-check (carrying `--repo $TARGET_GH_REPO` so cross-repo builds query the correct repo) + GitHub's atomic one-open-PR-per-head enforcement on `gh pr create`. For a cross-repo build, a `--head` guard missing `--repo` would query the origin repo, see no PR, and duplicate — hence `--repo` is mandatory on the guard.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1871] SDLC router G5 fast-path dispatching `/do-build` while `plan_revising=true` — already filed and reproduced separately; not part of this dispatch fix.
- The five "also observed" batch follow-ups are distinct concerns, each warranting its own issue rather than being bundled here:
  - [SEPARATE-SLUG #1935] TEST-stage marker staying `pending` when tests run inline in BUILD/REVIEW (G5/G6 fast-path) — the nearest tracked item covering headless-runner turn classification; confirm-or-file during build.
  - [EXTERNAL] Plan artifacts committed to origin/main before the feature PR merges — this is the repo's documented commit-on-main rule for plans (`docs/sdlc/do-plan.md`), a deliberate convention, not a bug to fix here; raise separately if the convention itself is to change.
  - [EXTERNAL] do-test swallow-gate regex missing `log("WARN: ...")` / uppercase `WARNING:` — a do-test regex change, unrelated to fork dispatch; file its own issue.
  - [EXTERNAL] `meta-set` rejecting `revision_applied` (frontmatter-derived by design) — documentation/tooling friction in the revision path; file its own issue.
  - [EXTERNAL] `validate_issue_recon.py` blocking reflection-auto-filed issues lacking `## Recon Summary` — a recon-gate UX change (it bit this very issue; recon was backfilled manually); file its own issue.
- [EXTERNAL] Adding a per-builder-worktree parallelism seam (Resolved Question 1 — declined) — only if within-issue concurrent-index corruption is later observed in practice; would be its own plan.

## Update System

The stage skills live in `.claude/skills-global/do-build/`, `.claude/skills-global/do-sdlc/`, and `.claude/skills/sdlc/`. The `-global` dirs are hardlinked to `~/.claude/skills/` on every machine by `/update` (`scripts/update/hardlinks.py::sync_claude_dirs`). Editing the canonical repo copies is sufficient — the next `/update` re-hardlinks them; **no new sync wiring, no `RENAMED_REMOVALS` entry, and no migration are required** (no files are renamed or moved between `skills/` and `skills-global/`). `.claude/skills/sdlc/` is project-only and not synced — editing it affects this repo only, which is correct. No `scripts/update/run.py` or `migrations.py` changes needed.

## Agent Integration

No agent integration required — this changes SDLC skill-orchestration prose and adds one Python unit test. No new CLI entry point (`pyproject.toml [project.scripts]`), no `.mcp.json` / `mcp_servers/` surface, and no `bridge/telegram_bridge.py` import. The skills are already reachable by the PM/dev session via the existing `/sdlc`, `/do-build`, and `/do-sdlc` invocation paths; this plan only changes their internal behavior.

## Documentation

### Feature Documentation
- [x] Create `docs/features/sdlc-fork-turn-boundary.md` documenting the fork turn-boundary invariant ("a `context: fork` skill never ends its turn with a live background child" — landed by `8542ffb19` and now guarded by `test_sdlc_fork_no_background.py`), the concurrent-foreground builder model, the slug-identity-always-wins ownership rule, and the live-ref PR dedup guard (incl. the cross-repo `--repo $TARGET_GH_REPO` requirement). Cross-link the two prior-art plans and cite commit `8542ffb19`.
- [x] Add an entry to `docs/features/README.md` index table.
- [x] Update `docs/features/headless-session-runner.md` and/or `docs/features/eng-session-architecture.md` with a pointer to the new invariant doc (these describe the fork/dev-subagent execution model).

### Inline Documentation
- [x] The enforcement test (`tests/unit/test_sdlc_fork_no_background.py`) carries a module docstring explaining which invariant each assertion guards and why (so a future failure is self-explanatory).

## Success Criteria

**Already satisfied on `main` by `8542ffb19` (verified by Task 3's regression test — no new edit):**
- [x] do-build spawns builders with `run_in_background: false`; the `[true if Parallel: true]` pattern is gone from `WORKFLOW.md`.
- [x] do-build's Step 4 no longer instructs cross-turn "resume on 15-min silence"; it verifies builders in-turn.
- [x] do-sdlc §3c stage dispatch passes `run_in_background: false` explicitly (plus Hard Rule 6).

**Delivered by this plan:**
- [x] do-build's PR step runs `gh pr list --head session/{slug}` (with `--repo $TARGET_GH_REPO` for cross-repo) before `gh pr create` and reuses an existing PR.
- [x] `sdlc/SKILL.md`'s search-based PR probe carries a live-ref `--head` cross-check note.
- [x] do-sdlc / sdlc docs declare slug-identity-always-wins ownership of `.worktrees/{slug}` + `session/{slug}`; no lane allocation.
- [x] `worktree_manager.py` and `resolve_branch_for_stage` are unchanged (confirmed by diff).
- [x] `tests/unit/test_sdlc_fork_no_background.py` scans **every** `context: fork` skill (incl. pthread, do-pr-review) for the background-then-exit pattern, plus the do-build PR-guard and do-sdlc positive assertions; fails loudly if any fork skill reintroduces the pattern.
- [x] Tests pass (`/do-test`)
- [x] Documentation updated (`/do-docs`)

## Team Orchestration

The lead agent orchestrates via Task tools and never builds directly. Per the invariant `8542ffb19` already landed, builders are dispatched `run_in_background: false` and joined before the turn advances.

### Team Members

- **Builder (skill-orchestration)**
  - Name: `fork-skill-builder`
  - Role: Edit skill files for Defect 2 (slug-wins ownership note in do-sdlc/SKILL.md + sdlc/SKILL.md) and Defect 3 (PR `--head` reuse guard with `--repo $TARGET_GH_REPO` in do-build/PR_AND_CLEANUP.md; live-ref cross-check note beside sdlc/SKILL.md's search probe). **Do NOT re-edit the Defect-1 sites — they are already fixed by `8542ffb19`.**
  - Agent Type: builder
  - Domain: async (paste the concurrency/turn-boundary rules from `DOMAIN_FRAMING.md`)
  - Resume: true

- **Builder (enforcement-test)**
  - Name: `guard-test-builder`
  - Role: Write `tests/unit/test_sdlc_fork_no_background.py` scanning **every** `context: fork` skill (discovered by frontmatter, incl. pthread + do-pr-review) for the forbidden background-dispatch pattern, plus positive assertions for do-build's PR `--head` guard and do-sdlc's explicit foreground flag; assert missing/empty skill files fail loudly; ensure prose mentions of `run_in_background: true` (e.g. do-plan-critique's negated reference) do NOT false-positive
  - Agent Type: test-engineer
  - Resume: true

- **Validator (dispatch-behavior)**
  - Name: `fork-validator`
  - Role: Verify skill edits match the invariant; confirm `worktree_manager.py` + `resolve_branch_for_stage` untouched; run the new test
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `fork-doc-writer`
  - Role: Create `docs/features/sdlc-fork-turn-boundary.md`, index entry, and cross-links
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

Tier 1 core agents (`builder`, `validator`, `test-engineer`, `documentarian`) cover all tasks. `async` domain framing applies to the skill-orchestration builder.

## Step by Step Tasks

> **Defect 1 has NO task** — it was landed by `8542ffb19`. The original Tasks 1/2 Defect-1 edits are removed; a builder applying them would hit Edit exact-string-match failures against already-fixed code. Task 3 (the regression test) is the only Defect-1 work.

### 1. Edit do-build for the live-ref PR dedup guard (Defect 3)
- **Task ID**: build-do-build-skill
- **Depends On**: none
- **Validates**: tests/unit/test_sdlc_fork_no_background.py (create)
- **Assigned To**: fork-skill-builder
- **Agent Type**: builder
- **Domain**: async
- **Parallel**: false
- In `.claude/skills-global/do-build/PR_AND_CLEANUP.md` (Step 7, lines ~56-59), insert a `gh pr list --head session/{slug} --state open --json number -q '.[0].number'` reuse guard before `gh pr create`. **The guard MUST carry `--repo $TARGET_GH_REPO` for cross-repo builds** (mirroring the existing `# For cross-repo builds, add: --repo $TARGET_GH_REPO` comment at :58) — without it the guard queries the wrong repo and always creates a duplicate. Reuse the existing PR number when one is found; skip create.
- In `.claude/skills/sdlc/SKILL.md` (step 2c, ~line 91), add a comment beside the search-based `gh pr list --search "#{issue_number}"` probe noting a live-ref cross-check (`gh pr list --head session/{slug} --state open`; `--search` lags the index). Do NOT remove the search probe — it is issue-number-keyed and the `--head` query cannot replace it.
- **Do NOT touch** the already-fixed Defect-1 sites: `WORKFLOW.md:72` (`run_in_background: false`), `WORKFLOW.md:76`, `WORKFLOW.md:98-104`, `do-build/SKILL.md:154`.

### 2. Add slug-wins ownership prose (Defect 2)
- **Task ID**: build-supervisor-skills
- **Depends On**: none
- **Validates**: tests/unit/test_sdlc_fork_no_background.py (create)
- **Assigned To**: fork-skill-builder
- **Agent Type**: builder
- **Domain**: async
- **Parallel**: false
- Add a "Worktree & branch ownership" note to `.claude/skills-global/do-sdlc/SKILL.md` and `.claude/skills/sdlc/SKILL.md`: slug identity always wins; each issue's fork owns `.worktrees/{slug}` + `session/{slug}`; supervisors do NOT allocate `.worktrees/sdlc-{N}` lanes.
- Do NOT modify `agent/worktree_manager.py` or `agent/agent_session_queue.py::resolve_branch_for_stage`.
- **Do NOT re-add** do-sdlc's Hard Rule 6 or §3c foreground flag — already present from `8542ffb19`.

### 3. Write regression-guard enforcement test (locks in `8542ffb19`; Defect 1)
- **Task ID**: build-guard-test
- **Depends On**: build-do-build-skill, build-supervisor-skills
- **Validates**: tests/unit/test_sdlc_fork_no_background.py
- **Assigned To**: guard-test-builder
- **Agent Type**: test-engineer
- **Parallel**: false
- Create `tests/unit/test_sdlc_fork_no_background.py`. **Discover every `context: fork` skill** by scanning `.claude/skills-global/**/SKILL.md` and `.claude/skills/**/SKILL.md` for `context: fork` frontmatter (plus do-build's dispatch file `WORKFLOW.md`). For each: assert it exists and is non-empty (fail loudly), and assert no un-joined background dispatch — no literal `run_in_background: true` and no `run_in_background: [true if Parallel:` in a dispatch position. **Exclude negated/quoted prose mentions** (e.g. do-plan-critique's "never `run_in_background: true`") so they don't false-positive.
- Positive assertions: do-build/WORKFLOW.md has `run_in_background: false`; do-build/PR_AND_CLEANUP.md has `gh pr list --head` before `gh pr create`; do-sdlc/SKILL.md has `run_in_background: false` + Hard Rule 6 text.
- This closes CONCERN 2 (pthread, do-pr-review previously unguarded) and resolves Open Question 3.
- Failure messages must name the offending file and pattern.

### 4. Validate dispatch behavior
- **Task ID**: validate-fork-fixes
- **Depends On**: build-guard-test
- **Assigned To**: fork-validator
- **Agent Type**: validator
- **Parallel**: false
- Confirm all skill edits match the invariant and the success criteria.
- Confirm `git diff` shows NO changes to `agent/worktree_manager.py` or `agent/agent_session_queue.py`.
- Run `pytest tests/unit/test_sdlc_fork_no_background.py -q` and report pass/fail.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-fork-fixes
- **Assigned To**: fork-doc-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/sdlc-fork-turn-boundary.md`; add the `docs/features/README.md` index entry; cross-link the two prior-art plans and the headless-session-runner / eng-session-architecture docs.

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: fork-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the full Verification table.
- Confirm every Success Criterion is met, including docs.
- Generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Enforcement test passes | `pytest tests/unit/test_sdlc_fork_no_background.py -q` | exit code 0 |
| Tests pass | `pytest tests/unit/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Defect-1 already landed (no `[true if Parallel:`) | `grep -c 'true if Parallel: true' .claude/skills-global/do-build/WORKFLOW.md` | match count == 0 |
| do-sdlc explicit foreground already landed | `grep -c 'run_in_background: false' .claude/skills-global/do-sdlc/SKILL.md` | output > 0 |
| do-build has PR head-guard | `grep -c 'gh pr list --head' .claude/skills-global/do-build/PR_AND_CLEANUP.md` | output > 0 |
| PR guard is cross-repo safe | `grep -A2 'gh pr list --head' .claude/skills-global/do-build/PR_AND_CLEANUP.md \| grep -c 'TARGET_GH_REPO'` | output > 0 |
| sdlc router has live-ref cross-check note | `grep -c 'gh pr list --head' .claude/skills/sdlc/SKILL.md` | output > 0 |
| Test scans pthread too | `grep -c 'pthread' tests/unit/test_sdlc_fork_no_background.py` | output > 0 (or a glob-discovery assertion covering it) |
| worktree_manager untouched | `git diff --name-only main -- agent/worktree_manager.py` | output does not contain worktree_manager.py |
| branch derivation untouched | `git diff --name-only main -- agent/agent_session_queue.py` | output does not contain agent_session_queue.py |
| Feature doc exists | `test -f docs/features/sdlc-fork-turn-boundary.md && echo ok` | output contains ok |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | freshness | Commit `8542ffb19` already landed the entire Defect-1 fix; plan wrongly asserted the sites were unmodified; Tasks 1/2 would hit Edit exact-string-match failures | Freshness Check re-scanned against HEAD `68c560040`, disposition → **Major drift**; Problem/Data Flow/Solution rescoped; Defect-1 code edits removed from Tasks 1/2; Task 3 repositioned as regression guard | Rescope-on-revised-premise per freshness protocol; issue stays open (Defects 2 & 3 live) |
| CONCERN 1 | correctness | PR `--head` dedup guard omitted `--repo $TARGET_GH_REPO`, reopening Defect 3 for cross-repo builds | Technical Approach + Task 1 + Race 2 + Verification now mandate `--repo $TARGET_GH_REPO` on the `gh pr list --head` guard | Added a Verification grep asserting `TARGET_GH_REPO` appears within 2 lines of `gh pr list --head` |
| CONCERN 2 | coverage | Enforcement test claimed to cover "every context:fork skill" but only guarded do-build/do-sdlc; pthread unguarded; Open Question 3 unresolved | Test rescoped to discover ALL `context: fork` skills by frontmatter (incl. pthread, do-pr-review); Open Question 3 resolved | Prose mentions of `run_in_background: true` excluded to avoid false positives |
| CONCERN 3 | attribution | Commit `8542ffb19` uncredited in Prior Art; Task 3 framed as re-fixing Defect 1 from scratch | Prior Art + Why-Previous-Fixes-Failed now credit `8542ffb19`; Task 3 reframed as "lock in `8542ffb19` with a regression test" | — |
| NIT | sizing | Appetite mis-sized as Large; ~3 of 7 Success Criteria already satisfied on main | Appetite → **Small** with downsizing rationale; 3 criteria marked `[x]` (satisfied by `8542ffb19`) | — |
| FRAMING (owner review) | architecture | Plan read as if a `context: fork` orchestrator drives production; the 5-day session-runner refactor moved production SDLC to the resumable `dev` subagent, which reframes all three defects (Defect 1 obsolete at orchestrator level / live at leaf; Defect 2 now a `dev`-fanout path; Defect 3 root cause dissolved in production, remains for human-collision) | Freshness Check gained an "Architectural reframe" note; Architectural Impact gained an execution-model bullet; Data Flow entry point, Defect 2, and Risk 1 re-anchored on the `dev`-subagent model | No scope change — the three deliverables (regression test, slug-wins prose, live-ref `--head` guard) are unchanged; only the narrative was corrected |

---

## Resolved Questions (settled during revision)

1. **Serialize vs per-builder worktrees — RESOLVED by `8542ffb19`.** The landed fix chose **concurrent foreground** (multiple foreground Task calls in one message, all joined before the turn advances), not serialization. This plan does not fight that decision. The residual within-issue shared-index risk is bounded by do-build's "no shared-file writes for `Parallel: true` tasks" convention (Risk 1); true per-builder-worktree parallelism, if ever needed, is a separate plan (No-Gos).
2. **Slug-wins vs adding a lane seam — RESOLVED: slug-wins.** This plan adopts "slug identity always wins; supervisors stop allocating lanes" (issue's option c) and adds NO override seam — structurally collapsing duplicate PRs via GitHub's one-open-PR-per-head rule. The lane-seam alternative (issue's option a) is explicitly declined; it re-introduces the coupling this decision removes.
3. **Enforcement-test scope — RESOLVED: scan every `context: fork` skill** (closes CONCERN 2). The test discovers fork skills by frontmatter (covering pthread, do-pr-review, do-plan-critique, do-deploy-example, do-presentation, do-design-audit, and the sdlc/do-build dispatch files), not just the three batch-implicated skills. Prose mentions of `run_in_background: true` (e.g. do-plan-critique's negated reference) are excluded so they don't false-positive. This is cheap insurance and the broadened blast radius is the point — the whole fork family gets the guard, not another point patch.
