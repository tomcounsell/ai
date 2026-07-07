---
status: Planning
type: bug
appetite: Large
owner: Valor Engels
created: 2026-07-07
tracking: https://github.com/valorengels/ai/issues/1915
last_comment_id:
---

# SDLC Fork: Synchronous Workers + Single-Owner Worktree/Branch Identity

## Problem

A supervised 6-issue parallel SDLC batch (#1898 #1899 #1901 #1902 #1904 #1905) shipped every pipeline, but 4 of 6 needed manual supervisor recovery because the renovated `context: fork` dispatch path has three interlocking defects.

**Current behavior:**

1. **Phantom-wait (Defect 1).** Stage skills declare `context: fork` — a single, isolated, non-resumable subagent turn. do-build spawns builders in the background (`run_in_background: [true if Parallel: true]`, `do-build/WORKFLOW.md:72`) then instructs itself to poll/monitor/resume them for up to 15 min (`WORKFLOW.md:100-109`); do-sdlc dispatches each stage via the Agent tool and **omits the `run_in_background` flag entirely** (`do-sdlc/SKILL.md:93-94`), inheriting the tool's background default. A fork that ends its turn cannot be re-entered when a background child finishes — so builds were left uncommitted, branches unpushed, and PRs never created (#1904, #1901, #1902, #1898).

2. **Shared slug worktree, no lane seam (Defect 2).** `worktree_manager.py:883-884` templates BOTH the worktree dir (`.worktrees/{slug}`) and branch (`session/{slug}`) off one `slug`; there is no override for either. do-build interpolates that single slug into every builder prompt (`WORKFLOW.md:49-54`), so parallel builders share one git index — one builder's files were swept into another's commit, work orphaned, commits corrupted (#1904, #1899, #1898). Supervisor `.worktrees/sdlc-{N}` lane instructions are silently dropped because nothing reads them.

3. **Duplicate PRs (Defect 3).** A fork branch and a supervisor branch produced two byte-identical PRs per issue (#1908/#1910, #1911/#1912). do-build's `gh pr create --head session/{slug}` (`PR_AND_CLEANUP.md:59`) has **no** pre-create existence check; the pipeline's only dedup probe is search-based (`gh pr list --search "#{issue}"`, `sdlc/SKILL.md:91`), which lags GitHub's search index. No `gh pr list --head` (live-ref) lookup exists anywhere.

**Desired outcome:**

- A fork NEVER ends its turn with a live background child. Workers run synchronously (or the fork blocks-and-joins in-turn before committing/pushing/PR-ing).
- Within one issue, parallel build tasks are serialized in the slug worktree, eliminating shared-index corruption. No builder ever writes concurrently with another into the same index.
- **Slug identity always wins:** each issue's fork exclusively owns `.worktrees/{slug}` + `session/{slug}`. Supervisors stop allocating lanes. This converges fork + supervisor onto one branch per plan, which structurally collapses duplicate PRs (GitHub allows one open PR per head branch).
- A live-ref `gh pr list --head` guard makes PR reuse deterministic instead of racing the search index.

## Freshness Check

**Baseline commit:** `1b4f17957`
**Issue filed at:** 2026-07-06T04:15:17Z
**Disposition:** Minor drift

**File:line references re-verified (all still accurate at plan time):**
- `.claude/skills-global/do-build/WORKFLOW.md:72` — `run_in_background: [true if Parallel: true]` — still present (verified verbatim).
- `.claude/skills-global/do-build/WORKFLOW.md:100-109` — poll/monitor/resume-on-15-min-silence block — still present.
- `.claude/skills-global/do-build/WORKFLOW.md:49-54` — single-`{slug}` builder prompt template — still present.
- `.claude/skills-global/do-build/PR_AND_CLEANUP.md:59` — `gh pr create --head session/{slug}` with no preceding dedup — still present.
- `.claude/skills-global/do-sdlc/SKILL.md:93-94` — Agent-tool stage dispatch omitting `run_in_background` — still present.
- `.claude/skills/sdlc/SKILL.md:91` — search-based `gh pr list --search "#{issue}"` probe — still present.
- `agent/worktree_manager.py:883-884` — `.worktrees/{slug}` + `session/{slug}` derivation — still present.
- `agent/agent_session_queue.py:420-449` — `resolve_branch_for_stage` returning `session/{slug}` — still present (file was reworked by #1930 but this function is intact).

**Commits on main since issue was filed (touching referenced files):**
- `e8351e4ca` — "Granite PTY teardown: headless `claude -p` session runner cutover (#1930)" — added `agent/session_runner/` and reworked `agent_session_queue.py` (+117 lines). **Did not touch** the `context: fork` stage skills, `worktree_manager.py`, or the PR-create sites. `resolve_branch_for_stage` still derives `session/{slug}`. All three defects remain live. The fork resumption model the issue targets (`context: fork` = one non-resumable turn) is unchanged.

**Cited sibling issues/PRs re-checked:**
- #1871 — OPEN — "SDLC router G5 fast-path dispatches /do-build while plan_revising=true" — explicitly a separate follow-up (out of scope here).
- #1908 (MERGED) / #1910 (CLOSED) — the duplicate PR pair produced for #1904's build. Confirms Defect 3 shipped real duplicates.
- #1911 (CLOSED) / #1912 (MERGED) — the duplicate PR pair for #1899's build. Same.

**Active plans in `docs/plans/` overlapping this area:** `docs/plans/completed/sdlc-fork-issue-number-divert.md` (completed — established the "fork loses session context" blind spot) and `docs/plans/completed/critique_await_all_critics_barrier.md` (completed — fixed the identical background-then-await bug in do-plan-critique via a filesystem roster barrier). Both are prior art, not live conflicts. No live plan touches do-build/do-sdlc dispatch.

**Notes:** Minor drift only — #1930 renamed nothing the issue references and left every cited site intact. The critique-barrier precedent (`critique_await_all_critics_barrier.md`) is the proven template for the Defect 1 fix: do-plan-critique already replaced background+await with an in-turn barrier; do-build never received the equivalent fix.

## Prior Art

- **`docs/plans/completed/critique_await_all_critics_barrier.md`** — do-plan-critique had the exact same bug: it spawned critics with `run_in_background: true` and could return before they finished, dropping late findings. Fixed with a filesystem roster barrier that blocks the driver in-turn until every critic writes its artifact. **This is the direct template for the do-build fix** — the pattern (spawn → block-and-join in the same turn → only then proceed) is proven in this codebase.
- **`docs/plans/completed/sdlc-fork-issue-number-divert.md`** — established and documented the "fork loses session context" failure class (`:17`, `:103`). Confirms the root model: a `context: fork` skill is a single non-resumable turn.
- **do-test `parallel-dispatch.md:56-76`** — the correct in-turn join barrier already in production: spawn N background suites, then block up to a timeout collecting every output, with a direct-execution fallback. A working reference implementation of "background for parallelism, but never end the turn with live children."
- `gh issue list --state closed --search "fork worktree background"` and `gh pr list --state merged --search "fork background worktree slug"` returned no other prior attempts at *this* worktree/dispatch problem — the phantom-wait + shared-worktree defects have not been fixed before.

## Research

No external research needed — this is a purely internal change to skill-orchestration prose and a Python enforcement test. No external libraries, APIs, or ecosystem patterns are involved. `gh` CLI behavior (one open PR per head branch; `--head` queries live refs vs `--search` hits the lagging index) is established from the codebase recon and standard `gh` semantics, not new research.

## Why Previous Fixes Failed

The identical background-then-await bug was already fixed once — in do-plan-critique (`critique_await_all_critics_barrier.md`) — but the fix was applied **only to that one skill**, not generalized to the whole `context: fork` family.

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| `critique_await_all_critics_barrier.md` | Replaced background+await with an in-turn filesystem barrier in do-plan-critique | Point fix — never propagated to do-build or do-sdlc, which have the same "fork ends turn with live children" pattern |
| `sdlc-fork-issue-number-divert.md` | Documented the "fork loses context" blind spot | Documentation only — did not add a structural guard preventing forks from backgrounding then exiting |

**Root cause pattern:** The `context: fork` execution model (single non-resumable turn) has no enforced invariant that a fork must reach terminal state within its own turn. Each skill is trusted to background responsibly, and each is fixed individually only after it burns a pipeline. The durable fix is a **generalized invariant + an enforcement test** that scans every fork stage skill, not another point patch.

## Data Flow

1. **Entry point:** PM session (headless `claude -p` turn) routes work and invokes `/sdlc`, which dispatches ONE stage skill as a `context: fork` subagent (or `/do-sdlc` supervises a full run locally).
2. **do-build fork:** derives `{slug}` from the plan filename → creates `.worktrees/{slug}` on `session/{slug}` (`worktree_manager` / `git worktree add`) → deploys builder Task subagents pointed at that worktree.
3. **Current failure — Defect 1:** builders spawned `run_in_background: true`; fork proceeds to Step 4 monitor loop; model interprets "poll/resume for 15 min" as resumable and ends the turn → downstream commit/push/PR steps (`SKILL.md:130-131`, `PR_AND_CLEANUP.md:52-89`) never run.
4. **Current failure — Defect 2:** multiple parallel builders write into the one `.worktrees/{slug}` git index concurrently → interleaved staging, cross-swept commits.
5. **Current failure — Defect 3:** fork opens PR on `session/{slug}`; a supervisor's separate branch opens a second PR; the only guard (`sdlc/SKILL.md:91` search probe) missed it due to index lag.
6. **Output (fixed):** builders run serially in `.worktrees/{slug}`; fork joins all children in-turn; a `gh pr list --head session/{slug}` check reuses any existing PR; one branch → one PR; the fork reaches terminal state (committed, pushed, PR open/reused) before returning.

## Architectural Impact

- **New dependencies:** None.
- **Interface changes:** None to Python signatures. `worktree_manager.create_worktree` / `get_or_create_worktree` and `resolve_branch_for_stage` are **left unchanged** — the "slug identity always wins" decision uses the existing slug→worktree→branch derivation as the single source of truth rather than adding an override seam.
- **Coupling:** Decreases. Removes the implicit (broken) coupling between forks and a nonexistent cross-turn resumption path. Removes the supervisor's dropped-on-the-floor lane-assignment coupling by declaring slug the sole identity.
- **Data ownership:** Clarified — each issue's fork exclusively owns `.worktrees/{slug}` + `session/{slug}`. No shared ownership across sessions.
- **Behavioral change:** Within-issue build parallelism is traded for correctness (builders serialize in the slug worktree). Build latency may rise for large multi-task plans; acceptable given the batch showed parallel builders corrupting each other.
- **Reversibility:** High — all changes are skill-markdown prose + one additive test. Revert is a git revert of the skill edits.

## Appetite

**Size:** Large

**Team:** Solo dev, PM, code reviewer

**Interactions:**
- PM check-ins: 2-3 (confirm serialize-vs-per-builder-worktree decision; confirm slug-wins over lane seam)
- Review rounds: 2+ (skill-orchestration behavior change touches the whole fork family)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `gh` authenticated | `gh auth status` | PR-guard instructions and prior-art checks rely on `gh` |
| Repo clean on `main` | `git rev-parse --abbrev-ref HEAD` | Skill edits + test land via normal build branch off main |

No external secrets or services required.

## Solution

### Key Elements

- **Fork turn-boundary invariant:** A documented, test-enforced rule — "A `context: fork` skill NEVER ends its turn with a live background child. Either dispatch workers synchronously (`run_in_background: false`) or block-and-join every background child in-turn before proceeding to any commit/push/PR/cleanup step."
- **do-build synchronous/serial builders:** Parallel build tasks run one at a time in the slug worktree (`run_in_background: false`). `Parallel: true` in a plan becomes an ordering hint, not concurrency. The Step 4 "Health Monitoring for Background Agents" block (with cross-turn resume language) is replaced by an in-turn join/verify.
- **do-sdlc explicit foreground dispatch:** §3c passes `run_in_background: false` explicitly instead of relying on the tool default.
- **Slug identity always wins:** do-sdlc / sdlc skill docs declare that each issue's fork owns `.worktrees/{slug}` + `session/{slug}`; supervisors must NOT allocate `.worktrees/sdlc-{N}` lanes. No worktree/branch override seam is added.
- **Live-ref PR dedup guard:** do-build runs `gh pr list --head session/{slug} --state open` immediately before `gh pr create`; if a PR exists, it reuses that PR number and skips create.
- **Enforcement test:** a unit test that scans every `context: fork` stage skill for the forbidden background-then-exit pattern and asserts do-build's PR step carries the `--head` guard.

### Flow

Plan ready → `/sdlc` dispatches do-build fork → fork creates `.worktrees/{slug}` on `session/{slug}` → deploys builders **serially** (`run_in_background: false`), joining each before the next → after all builders return, fork verifies commits exist → `gh pr list --head session/{slug}` (reuse if present, else `gh pr create`) → fork returns having reached terminal state (committed, pushed, PR open/reused). Supervisor never allocates a lane; the next issue's fork owns its own `.worktrees/{its-slug}`.

### Technical Approach

- **Defect 1 — do-build (`WORKFLOW.md`, `SKILL.md`, `PR_AND_CLEANUP.md`):**
  - Change the builder spawn template (`WORKFLOW.md:44-74`) so `run_in_background: false` (drop `[true if Parallel: true]`). Builders execute one at a time; the Task call blocks until each returns.
  - Replace the Step 4 "Health Monitoring for Background Agents" block (`WORKFLOW.md:94-115`) — which frames a 15-min poll-and-resume loop — with an in-turn verification step (each builder's self-check output is already in-hand because dispatch is synchronous). Keep the safety-commit-on-failure behavior.
  - Add the fork turn-boundary invariant as a bold rule near the top of `do-build/SKILL.md`.
- **Defect 1 — do-sdlc (`SKILL.md:93-94`):** add explicit `run_in_background: false` to the Agent-tool stage dispatch. Add the same invariant note to the supervision-loop preamble.
- **Defect 2 — slug-wins decision (`do-sdlc/SKILL.md`, `sdlc/SKILL.md`):** add a "Worktree & branch ownership" note declaring slug the sole identity; supervisors do not pre-allocate `.worktrees/sdlc-{N}` lanes. No changes to `worktree_manager.py` or `resolve_branch_for_stage` — the existing derivation IS the invariant. do-build's serial builders (Defect 1 fix) already remove within-issue index contention.
- **Defect 3 — live-ref PR guard (`do-build/PR_AND_CLEANUP.md:52-59`):** before `gh pr create --head session/{slug}`, insert:
  ```bash
  EXISTING_PR=$(gh pr list --head session/{slug} --state open --json number -q '.[0].number')
  if [ -n "$EXISTING_PR" ]; then echo "Reusing PR #$EXISTING_PR"; else gh pr create --head session/{slug} ...; fi
  ```
  This queries live refs (no search-index lag). Structural backstop: with slug-wins, fork + supervisor converge on one `session/{slug}` head, and GitHub itself permits only one open PR per head branch.
- **Enforcement test (`tests/unit/test_sdlc_fork_no_background.py`):** parse the fork stage skill files and assert (a) do-build's builder spawn does not contain the `run_in_background: [true if Parallel:` pattern and does contain `run_in_background: false`; (b) do-build's PR step contains `gh pr list --head` ahead of `gh pr create`; (c) do-sdlc's stage dispatch contains `run_in_background: false`.

## Failure Path Test Strategy

### Exception Handling Coverage
- The touched Python surface is one new test file; it introduces no `except Exception: pass` blocks. The skill edits are markdown prose (no runtime exception paths). State: **No exception handlers in scope** beyond the new test's own file-read guards (which assert-fail loudly if a skill file is missing, never swallow).

### Empty/Invalid Input Handling
- The enforcement test must fail loudly (not skip) if a scanned skill file is missing or empty — an empty/missing skill file must be treated as a test failure, not a silent pass. Add an explicit assertion that each scanned path exists and is non-empty before pattern-matching.

### Error State Rendering
- The user-visible failure mode being fixed IS a silent-loop / stranded-turn class (fork ends without committing). The enforcement test is the guard that the fix stays in place; its failure message must name the offending skill file and the forbidden pattern so a future regression is diagnosable at a glance.

## Test Impact

- [ ] `tests/unit/test_sdlc_router_decision.py` — UPDATE (verify-only): confirm no assertion depends on the removed background-dispatch phrasing; router decision logic is unchanged by this plan, so this is a read-through, likely no edit.
- [ ] `tests/unit/test_worktree_manager.py` — no change expected: `worktree_manager.py` is intentionally left untouched (slug-wins reuses existing derivation). Listed to confirm the builder does NOT accidentally modify worktree signatures.
- [ ] `tests/unit/test_agent_session_queue.py` — no change expected: `resolve_branch_for_stage` is left untouched. Listed as a guard against accidental branch-derivation changes.

No existing test asserts the do-build/do-sdlc orchestration prose today, so the new `tests/unit/test_sdlc_fork_no_background.py` is additive and does not replace prior coverage.

## Rabbit Holes

- **Adding a worktree/branch override seam** (threading `worktree_name`/`branch_name` through `create_worktree`, `get_or_create_worktree`, `resolve_branch_for_stage`, and do-build). Tempting to "do it properly," but it re-introduces the lane-allocation complexity the slug-wins decision deliberately removes, and every seam is a new corruption surface. Slug-wins is simpler and closes all three defects. Only pursue the seam if PM explicitly wants true per-builder parallelism (Open Question 1).
- **Per-builder worktrees with branch-merge-back.** Real parallelism means N worktrees + N branches + a merge/rebase step back onto `session/{slug}` — a whole coordination protocol. Out of scope; serialize instead.
- **Rewriting the whole fork execution model / making forks resumable.** The headless session runner (#1930) is fresh; do not re-architect resumption. The invariant (no live children at turn end) works within the current model.
- **Fixing the 5 "also observed" batch follow-ups** (TEST marker, plan-artifacts-on-main, do-test WARN regex, meta-set `revision_applied`, recon-gate-on-reflection-issues). Each is a separate concern; see No-Gos.

## Risks

### Risk 1: Serializing builders slows large multi-task plans
**Impact:** A plan with many independent build tasks that previously ran in parallel now runs them one at a time, increasing wall-clock build time.
**Mitigation:** Correctness over speed — the batch proved parallel builders corrupt the shared index. If latency becomes a real problem, the per-builder-worktree path (Open Question 1) is the escape hatch, filed as its own issue. Most plans have few genuinely-parallel build tasks.

### Risk 2: Prose invariants are advisory; the model may still background
**Impact:** A skill instruction saying "never background" can be ignored by the executing model under load.
**Mitigation:** The enforcement test (`test_sdlc_fork_no_background.py`) makes the invariant structural at the *skill-file* level (the forbidden pattern cannot be committed), and slug-wins + GitHub's one-PR-per-head rule make Defect 3 structural at the *platform* level. The prose is backed by two mechanical guards.

### Risk 3: `gh pr list --head` guard races a truly-simultaneous second creator
**Impact:** Two forks calling `gh pr list --head` at the same microsecond both see "no PR" and both call `gh pr create`.
**Mitigation:** GitHub rejects the second `gh pr create` for an already-open head branch ("a pull request already exists for session/{slug}") — the create itself is the atomic guard; the `--head` pre-check just makes the common case graceful. With slug-wins there is only ever one head per plan, so the second creator's create fails cleanly rather than duplicating.

## Race Conditions

### Race 1: Concurrent builders mutating the shared slug git index
**Location:** `.claude/skills-global/do-build/WORKFLOW.md:44-115` (parallel builder dispatch into `.worktrees/{slug}`)
**Trigger:** Two `Parallel: true` build tasks both `git add`/`git commit` in `.worktrees/{slug}` within the same window.
**Data prerequisite:** Each builder's file edits must be staged and committed atomically without another builder's edits interleaving.
**State prerequisite:** The git index of `.worktrees/{slug}` must have exactly one writer at a time.
**Mitigation:** Serialize builders (`run_in_background: false`) — one builder runs to completion (including its commit) before the next starts. Single writer at all times; the race is eliminated by construction.

### Race 2: Fork vs supervisor both creating a PR for the same issue
**Location:** `.claude/skills-global/do-build/PR_AND_CLEANUP.md:52-59`
**Trigger:** Two dispatch paths reach `gh pr create` for the same issue near-simultaneously.
**Data prerequisite:** At most one open PR should exist per issue's build branch.
**State prerequisite:** Both creators must resolve to the same head branch so GitHub's one-PR-per-head rule applies.
**Mitigation:** Slug-wins converges both on `session/{slug}` (same head) + `gh pr list --head` pre-check + GitHub's atomic one-open-PR-per-head enforcement on `gh pr create`.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1871] SDLC router G5 fast-path dispatching `/do-build` while `plan_revising=true` — already filed and reproduced separately; not part of this dispatch fix.
- The five "also observed" batch follow-ups are distinct concerns, each warranting its own issue rather than being bundled here:
  - [SEPARATE-SLUG #1935] TEST-stage marker staying `pending` when tests run inline in BUILD/REVIEW (G5/G6 fast-path) — the nearest tracked item covering headless-runner turn classification; confirm-or-file during build.
  - [EXTERNAL] Plan artifacts committed to origin/main before the feature PR merges — this is the repo's documented commit-on-main rule for plans (`docs/sdlc/do-plan.md`), a deliberate convention, not a bug to fix here; raise separately if the convention itself is to change.
  - [EXTERNAL] do-test swallow-gate regex missing `log("WARN: ...")` / uppercase `WARNING:` — a do-test regex change, unrelated to fork dispatch; file its own issue.
  - [EXTERNAL] `meta-set` rejecting `revision_applied` (frontmatter-derived by design) — documentation/tooling friction in the revision path; file its own issue.
  - [EXTERNAL] `validate_issue_recon.py` blocking reflection-auto-filed issues lacking `## Recon Summary` — a recon-gate UX change (it bit this very issue; recon was backfilled manually); file its own issue.
- [EXTERNAL] Adding a per-builder-worktree parallelism seam (Open Question 1) — only if PM chooses true parallelism over serialization; would be its own plan.

## Update System

The stage skills live in `.claude/skills-global/do-build/`, `.claude/skills-global/do-sdlc/`, and `.claude/skills/sdlc/`. The `-global` dirs are hardlinked to `~/.claude/skills/` on every machine by `/update` (`scripts/update/hardlinks.py::sync_claude_dirs`). Editing the canonical repo copies is sufficient — the next `/update` re-hardlinks them; **no new sync wiring, no `RENAMED_REMOVALS` entry, and no migration are required** (no files are renamed or moved between `skills/` and `skills-global/`). `.claude/skills/sdlc/` is project-only and not synced — editing it affects this repo only, which is correct. No `scripts/update/run.py` or `migrations.py` changes needed.

## Agent Integration

No agent integration required — this changes SDLC skill-orchestration prose and adds one Python unit test. No new CLI entry point (`pyproject.toml [project.scripts]`), no `.mcp.json` / `mcp_servers/` surface, and no `bridge/telegram_bridge.py` import. The skills are already reachable by the PM/dev session via the existing `/sdlc`, `/do-build`, and `/do-sdlc` invocation paths; this plan only changes their internal behavior.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/sdlc-fork-turn-boundary.md` documenting the fork turn-boundary invariant ("a `context: fork` skill never ends its turn with a live background child"), the serialize-builders-in-slug-worktree rule, the slug-identity-always-wins ownership rule, and the live-ref PR dedup guard. Cross-link the two prior-art plans.
- [ ] Add an entry to `docs/features/README.md` index table.
- [ ] Update `docs/features/headless-session-runner.md` and/or `docs/features/eng-session-architecture.md` with a pointer to the new invariant doc (these describe the fork/dev-subagent execution model).

### Inline Documentation
- [ ] The enforcement test (`tests/unit/test_sdlc_fork_no_background.py`) carries a module docstring explaining which invariant each assertion guards and why (so a future failure is self-explanatory).

## Success Criteria

- [ ] do-build spawns builders with `run_in_background: false` (serial); the `[true if Parallel: true]` pattern is gone from `WORKFLOW.md`.
- [ ] do-build's Step 4 no longer instructs cross-turn "resume on 15-min silence"; it verifies builders in-turn.
- [ ] do-sdlc §3c stage dispatch passes `run_in_background: false` explicitly.
- [ ] do-build's PR step runs `gh pr list --head session/{slug}` before `gh pr create` and reuses an existing PR.
- [ ] do-sdlc / sdlc docs declare slug-identity-always-wins ownership of `.worktrees/{slug}` + `session/{slug}`; no lane allocation.
- [ ] `worktree_manager.py` and `resolve_branch_for_stage` are unchanged (confirmed by test/diff).
- [ ] `tests/unit/test_sdlc_fork_no_background.py` passes and fails loudly if any fork skill reintroduces the background-then-exit pattern.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

The lead agent orchestrates via Task tools and never builds directly. Per this plan's own invariant, builders are dispatched **serially** (`run_in_background: false`); the lead joins each before dispatching the next.

### Team Members

- **Builder (skill-orchestration)**
  - Name: `fork-skill-builder`
  - Role: Edit do-build / do-sdlc / sdlc skill files for Defects 1, 2, 3 (synchronous dispatch, slug-wins note, PR `--head` guard, invariant preamble)
  - Agent Type: builder
  - Domain: async (paste the concurrency/turn-boundary rules from `DOMAIN_FRAMING.md`)
  - Resume: true

- **Builder (enforcement-test)**
  - Name: `guard-test-builder`
  - Role: Write `tests/unit/test_sdlc_fork_no_background.py` scanning the fork skills for forbidden/required patterns; assert missing/empty skill files fail loudly
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

### 1. Edit do-build for synchronous builders + PR guard
- **Task ID**: build-do-build-skill
- **Depends On**: none
- **Validates**: tests/unit/test_sdlc_fork_no_background.py (create)
- **Assigned To**: fork-skill-builder
- **Agent Type**: builder
- **Domain**: async
- **Parallel**: false
- In `.claude/skills-global/do-build/WORKFLOW.md`, change the builder spawn template (lines ~44-74) to `run_in_background: false`; remove `[true if Parallel: true]`.
- Replace the Step 4 "Health Monitoring for Background Agents" block (lines ~94-115) with an in-turn verification of each builder's self-check output; retain the safety-commit-on-failure step.
- In `.claude/skills-global/do-build/PR_AND_CLEANUP.md` (Step 7, lines ~52-59), insert the `gh pr list --head session/{slug} --state open` reuse guard before `gh pr create`.
- Add the fork turn-boundary invariant (bold rule) near the top of `.claude/skills-global/do-build/SKILL.md`.

### 2. Edit do-sdlc + sdlc for explicit foreground + slug-wins
- **Task ID**: build-supervisor-skills
- **Depends On**: none
- **Validates**: tests/unit/test_sdlc_fork_no_background.py (create)
- **Assigned To**: fork-skill-builder
- **Agent Type**: builder
- **Domain**: async
- **Parallel**: false
- In `.claude/skills-global/do-sdlc/SKILL.md` §3c (lines ~93-94), add explicit `run_in_background: false` to the Agent-tool stage dispatch; add the turn-boundary invariant to the supervision-loop preamble.
- Add a "Worktree & branch ownership" note to `.claude/skills-global/do-sdlc/SKILL.md` and `.claude/skills/sdlc/SKILL.md`: slug identity always wins; each issue's fork owns `.worktrees/{slug}` + `session/{slug}`; supervisors do NOT allocate `.worktrees/sdlc-{N}` lanes.
- Do NOT modify `agent/worktree_manager.py` or `agent/agent_session_queue.py::resolve_branch_for_stage`.

### 3. Write enforcement test
- **Task ID**: build-guard-test
- **Depends On**: build-do-build-skill, build-supervisor-skills
- **Validates**: tests/unit/test_sdlc_fork_no_background.py
- **Assigned To**: guard-test-builder
- **Agent Type**: test-engineer
- **Parallel**: false
- Create `tests/unit/test_sdlc_fork_no_background.py`: assert each scanned skill file exists and is non-empty (fail loudly otherwise); assert do-build has no `run_in_background: [true if Parallel:` and does have `run_in_background: false`; assert `gh pr list --head` precedes `gh pr create` in do-build; assert do-sdlc stage dispatch has `run_in_background: false`.
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
| do-build serializes builders | `grep -c 'true if Parallel: true' .claude/skills-global/do-build/WORKFLOW.md` | match count == 0 |
| do-build has PR head-guard | `grep -c 'gh pr list --head' .claude/skills-global/do-build/PR_AND_CLEANUP.md` | output > 0 |
| do-sdlc explicit foreground | `grep -c 'run_in_background: false' .claude/skills-global/do-sdlc/SKILL.md` | output > 0 |
| worktree_manager untouched | `git diff --name-only main -- agent/worktree_manager.py` | output does not contain worktree_manager.py |
| branch derivation untouched | `git diff --name-only main -- agent/agent_session_queue.py` | output does not contain agent_session_queue.py |
| Feature doc exists | `test -f docs/features/sdlc-fork-turn-boundary.md && echo ok` | output contains ok |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Serialize vs per-builder worktrees.** This plan recommends **serializing** within-issue parallel builders in the slug worktree (simplest, closes Defects 1 and 2 together, no `worktree_manager` changes). The alternative — per-builder worktrees + branch merge-back — preserves true parallelism at the cost of a coordination protocol and a worktree/branch override seam. Confirm serialization is acceptable, or do you want the parallelism path (which would become its own larger plan)?
2. **Slug-wins vs adding a lane seam.** This plan adopts "slug identity always wins; supervisors stop allocating lanes" (issue's option c) and adds NO override seam. This structurally collapses duplicate PRs. Confirm you don't want a supervisor-assignable worktree/branch seam instead (issue's option a) — that path re-introduces the coupling this decision removes.
3. **Should the enforcement test also cover the other fork skills** (do-plan-critique, do-pr-review, do-merge, pthread) for the background-then-exit pattern, or scope it to the three skills implicated in the batch (do-build, do-sdlc, sdlc) for now? do-plan-critique already has its barrier; extending the scan is cheap insurance but broadens the test's blast radius.
