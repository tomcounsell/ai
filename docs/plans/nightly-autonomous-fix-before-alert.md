---
status: Ready
type: feature
appetite: Large
owner: Valor Engels
created: 2026-07-27
tracking: https://github.com/tomcounsell/ai/issues/2334
last_comment_id: 5086978978
revision_applied: true
revision_applied_at: 2026-09-02T06:01:21Z
---

# Nightly Regression: Attempt an Autonomous Fix Before Paging a Human

## Problem

The nightly regression detector (`scripts/nightly_regression_tests.py`) runs the
unit suite each night. On a newly-confirmed serial failure it does two things in
the same run: dispatches an **investigate-only** Eng session (`maybe_dispatch_triage_session`,
mandate: "Do NOT attempt an auto-hotfix") **and** pages a human up front via
`send_telegram(...)`. The human is the *first responder* — they wake up to a raw
"tests are red" ping carrying the full cognitive load of "what broke and what do I
do", even when the regression is a mechanical test carry-forward Valor could have
patched before anyone woke up.

The worked example that proves the point: **#2399 → PR #2402**. 11 newly-confirmed
nightly failures, all of which turned out to be **test-stale** (source contracts
moved in merged PRs, the tests weren't carried forward). A human triaged and fixed
them by hand. An autonomous fixer should have handled the mechanical carry-forward
and paged nobody.

**Current behavior:** Detection → dispatch investigate-only session **and** page a
human immediately.

**Desired outcome:** Detection → silent log → Valor **classifies** each failure and
attempts a *bounded, review-gated* autonomous fix **only** when every failure is
test-stale with high confidence and within a hard blast-radius cap → on success a
**low-urgency, non-paging notification** tells a human a fix PR is waiting for review
(the happy path is not fully silent — a human must know a PR exists) → a human is
**paged** (high-urgency, wakes someone) **only** as the *escalation of last resort*:
when any failure is code-regressed, when classification is uncertain, when a cap is
exceeded, or (fail-safe) when the fix session dies/hangs. The last-resort page carries
the stuck point (what broke, what was tried, why blocked, the specific decision needed),
not a raw test dump.

Two notification tiers, kept distinct throughout the plan: a **page** is the
high-urgency escalation that wakes a human (reserved for genuine blockers); a
**notify** is a low-urgency FYI ("a fix PR exists, review when convenient"). Only
genuine blockers page.

**The one gate that matters:** an autonomous fixer must NEVER make a failing test pass
by weakening or deleting its assertions. If the code genuinely regressed, editing the
test to match is a catastrophic silent regression. Failing toward paging is always
safe; a silent wrong fix is not.

## Freshness Check

<!-- Round 2 re-verification, 2026-09-02. The original 2026-07-27 check against
     66a433bd9 is superseded: nine commits reshaped the detector between then and
     now. Only the current baseline is recorded, per the no-historical-artifacts
     rule. -->

**Baseline commit:** `3b6eb651b` (`git rev-parse origin/main`, 2026-09-02)
**Issue filed at:** 2026-07-24T06:45:58Z · **Plan first written:** 2026-07-27 (baseline `66a433bd9`)
**Disposition:** Minor drift — **the premise is intact, the substrate moved substantially.**

The defect this plan exists to fix is unchanged: `main()` still pages a human up front
on the `elif new_failures:` branch (`send_telegram(msg, dry_run=args.dry_run)`, ~L1197-1207),
and the dispatched session is still investigate-only (`"...auto-hotfix — this is an
investigation-and-file-an-issue task only"`, L846 in `_build_triage_prompt`; the seed
prompt repeats it at L1127). Nothing else has taken over the concern, and no merged PR
fixes it. **Proceed.**

What *did* move is the machinery the plan's dedup and dispatch design was written
against. `scripts/nightly_regression_tests.py` grew from ~700 to **1366 lines** across
nine commits (`3264ce7ff`, `e48199979`, `c8d06886f`, `f57a10b4a`, `3578882c7`,
`af8cd6aac`, `5a37cef05`, `1831b6883`, `b8e08ece4`). Every line reference below is
re-resolved against `3b6eb651b`.

**Re-verified — claims that still hold:**

| Claim | Current location | Status |
|---|---|---|
| Up-front unconditional page on newly-confirmed failures | `main()`, `elif new_failures:` → `send_telegram(...)` ~L1197-1207 | **Holds** — the defect |
| Investigate-only mandate ("Do NOT attempt an auto-hotfix") | `_build_triage_prompt` L846; seed prompt L1127 | **Holds** |
| `maybe_dispatch_triage_session()` is the dispatch seam | L856-988 (gained `prompt` / `slug_suffix` / `dry_run` kwargs) | **Holds**, signature widened |
| `reconfirm_serial()` serial gate → `confirmed_failing` | L532 | **Holds** |
| `send_telegram(msg, dry_run=False)` has **no** urgency parameter | L609 | **Holds** — the `silent=` addition is still required and still non-vacuous |
| `baseline-verifier` hardcodes `main` as the baseline ref | `.claude/agents/baseline-verifier.md` L54 (`BASELINE_COMMIT=$(git rev-parse main)`), L56 (`git worktree add "$BASELINE_DIR" main --detach`) | **Holds** — `--detach` added; still two literal `main` tokens to parameterize |
| `main` branch protection is **ABSENT** | `gh api repos/tomcounsell/ai/branches/main/protection` → `404 Not Found` | **Holds, re-verified 2026-09-02** — the sole structural never-merge leg still does not exist |
| #2405 (Cowork parity / intra-day watchdog follow-up) open | `gh issue view 2405` → OPEN | **Holds** |

**Re-verified — claims that DRIFTED and forced design changes (all propagated below):**

1. **`dispatched_hash` no longer exists.** #2559 / PR #2581 (`3264ce7ff`) replaced
   sha256-of-the-failing-set dedup with **per-node** dedup. The live mechanism is now
   `dispatched_nodes` (a node-ID set in `data/nightly_tests_last_run.json`) plus three
   pure helpers: `prior_dispatched(prev)` (L669), `compute_dispatch_set(prev, confirmed_failing)`
   (L728), `carry_dispatched_nodes(prev, confirmed_failing, just_dispatched)` (L754).
   **Consequence:** the plan's `fix_attempts: {failing_set_hash: count}` and
   `dispatched_sessions: {failing_set_hash: agent_session_id}` maps were keyed on a hash
   that no longer has a producer. Both are **re-keyed per node ID** — see Technical
   Approach → Dedup. The round-4 concerns those maps resolved (orphaned session pointer,
   canonical attempt-count naming) survive intact under the new key; only the key changed.

2. **`compute_new_failures` and `compute_dispatch_set` are now different sets, deliberately.**
   `compute_new_failures` (L654) drives the *alert*; `compute_dispatch_set` (L728) asks the
   different question of *what has never been filed*. The decision gate must be explicit
   about which set it consumes — it consumes `new_failures` (that is the population this
   feature promises to fix before paging), while attempt-count dedup rides on the per-node
   `dispatched_nodes` model. Stated in Technical Approach.

3. **`head_commit` is ALREADY persisted.** `current["head_commit"] = _get_head_commit()`
   at L1092 (`_get_head_commit()` L1344). Step 1's first bullet is **already done on main**;
   it degrades to a verify-and-consume step. The plan no longer claims to add it.

4. **`current["dispatched_session_id"]` (scalar) already exists in state** (L1090, carried
   from `prev`). The plan's round-4 replacement of a scalar session pointer with a keyed map
   is therefore now a *change to live code*, not a greenfield choice. Named in Interface changes.

5. **Three new mechanisms the plan must not break, none of which existed at plan time:**
   - **Seed / re-baseline runs** (`is_first_run`, `is_reseed` L1026, `is_seed_run` L1027,
     `seeded_nodes()` L683, `seed_size`, `min_expected_collected`). A seed run declares a
     known-failing population rather than discovering a regression. **The autonomous fixer
     must never dispatch on a seed run** — added as a hard gate condition.
   - **`MAX_DISPATCH_NODES = 10`** (L185) truncation of the dispatch set, with the remainder
     retried next run. The fixer's caps must compose with this rather than duplicate it.
   - **`validate_run_integrity()` → `integrity_warnings`** (L1047) and the `_fatal()`
     never-write-state-on-an-untrusted-run invariant, plus the `DRY_RUN_SESSION_ID` truthy
     sentinel so `--dry-run` exercises the success path without spawning a session.
     **An integrity-warned run or a `--dry-run` must never dispatch a fixer** — added as
     gate conditions.

**Overlapping active plans:** `nightly-serial-reconfirm.md` (the #2180 serial gate — a
dependency, not a conflict). The #2559/#2823 detector work that caused the drift above has
already landed; no in-flight lane owns `scripts/nightly_regression_tests.py`.

## Prior Art

- **#2192 / PR #2195** — added `maybe_dispatch_triage_session` (investigate-and-file-an-issue) + the run lock + LLM summarizer. **Deliberately deferred auto-hotfix as a No-Go** (unreviewed merge to main was the concern). This plan does NOT reverse that No-Go — it introduces a *narrower, review-gated* bounded fix (propose-a-PR, never merge), which preserves the original concern (a human still reviews before anything lands on main).
- **#2399 / PR #2402** — the worked example. 11 test-stale failures across 3 groups. Two groups were mechanical carry-forward; **Group 2 hid a genuine design fork** (self-heal intended vs. a safety hole) that required a human reading #2144's intent to resolve. **Lesson baked into scoping:** "classifies as test-stale" is *necessary but not sufficient* for autonomy — a test-stale carry-forward that turns on a contract-direction judgment must still escalate.
- **#410** — Autoexperiment (autonomous prompt-optimization loop). Prior art for a bounded autonomous loop with caps; informs the guardrail-constant design (max attempts, dedupe).
- **#2405** — follow-up filed by this plan for the Cowork-parity migration (open question 5). Still OPEN as of 2026-09-02.
- **#2559 / PR #2581** — replaced set-hash triage dedup with per-node dedup (`dispatched_nodes`, `compute_dispatch_set`, `carry_dispatched_nodes`) to end the #2429/#2430/#2462 duplicate-issue churn. **Landed after this plan was first written and retired the `dispatched_hash` this plan originally extended** — the dedup design is re-keyed per node to match (Technical Approach → Dedup). Its lesson also applies directly here: a second, parallel keying scheme in the same state file is how the churn started.
- **#2823 (collection-aware baseline, `e48199979`…`b8e08ece4`)** — added seed/re-baseline runs, the umbrella-issue seed path, `MAX_DISPATCH_NODES` truncation, and `validate_run_integrity`. These are the four run shapes on which an autonomous fixer must refuse to act; folded into the decision gate as hard disqualifiers.

## Data Flow

1. **Entry point**: launchd fires `python scripts/nightly_regression_tests.py`. `load_env_or_die()`, run lock, `run_tests()`, `reconfirm_serial()` → `confirmed_failing` set, `new_failures` = newly-confirmed vs `prev["failing_tests"]`. (Unchanged.)
2. **Classification** *(new)*: for the `new_failures` set, run the adapted baseline-verifier comparing current HEAD against `prev["head_commit"]` (the last-known-good SHA — by definition these tests passed there, since they are *newly* confirmed). Produces `{regressions, pre_existing, inconclusive}` buckets keyed to the nightly context (see Technical Approach for the semantic mapping).
3. **Decision gate** *(new)*: eligible-for-autonomy iff every `new_failure` lands in the "test-stale-candidate" bucket (passed at baseline, no `regressions`, no `inconclusive`) AND count ≤ `NIGHTLY_FIX_MAX_FAILURES` AND per-node attempt counts are under cap. **Four hard disqualifiers added in the 2026-09-02 refresh, from mechanisms that landed after the plan was first written:** the run is a seed/re-baseline run (`is_seed_run`), the run carried `integrity_warnings`, the run is `--dry-run`, or the dispatch set was truncated by `MAX_DISPATCH_NODES`. Any of these → escalate path, never autonomy: a seed run is a *declaration* of known-failing state rather than a discovery, an integrity-warned run's `confirmed_failing` set is not trustworthy enough to edit tests against, `--dry-run` must never spawn a session, and a truncated run is by definition looking at an incomplete picture. Otherwise → escalate path.
4. **Autonomous fix path** *(new)*: dispatch a **narrowly-scoped** fixer session — NOT an auto-continuing full-SDLC eng session (an eng session drives itself to `/do-merge`). Its mandate is exactly: make test-file-only edits on a branch, run the targeted tests, open a **DRAFT** PR (`gh pr create --draft`), write the structured hand-back, and STOP. The mandate never names "run the SDLC pipeline" and never names `/do-merge`, and explicitly forbids `gh pr ready`/`gh pr merge`/push-to-`main`. The `--draft` flag is a deterrent/detective signal, NOT a structural in-session block — under hardcoded `bypassPermissions` the session's bash could still self-merge; the load-bearing structural never-merge guarantee is **server-side branch protection on `main`**, external to the session (see Technical Approach → Enforcement design). Bounded: test-file-only edits, cite the source-contract-moving commit. No up-front page.
5. **Structured hand-back** *(new)*: the dispatched session persists its terminal hand-back **on its own `AgentSession` record via the Popoto ORM** (nullable `nightly_fix_handback` JSON field — not a separate file), so the watchdog (which already reads the session via the ORM) has a single, atomic source of truth: `{disposition, pr_url, what_broke, classification, tried, blocked_reason, decision_requested, written_at}`.
6. **Test-file-only mechanical guard** *(new)*: before the watchdog honors a `fixed-silent` disposition, it fetches the PR's changed paths (`gh pr diff --name-only`) and **rejects the fix mechanically if any path is outside `tests/`** — a source-file edit voids `fixed-silent` and converts it to an escalation page. This does not rely on the session honoring the prompt.
7. **Escalation / fail-safe watchdog** *(new)*: a **page** fires (a) immediately when the decision gate routes to escalate (any regression/inconclusive/cap-exceeded), with content from the classification; (b) when a prior dispatch's hand-back reports `blocked-escalate`; (c) when a `fixed-silent` PR fails the test-file-only mechanical guard; or (d) fail-safe — the session is terminal-failed/killed/abandoned, OR hung past `NIGHTLY_FIX_HANDBACK_TIMEOUT_HOURS` with no hand-back. Silence must never swallow a red suite.
8. **Output**: on the happy path, a PR for human review **plus a low-urgency notify** (not a page); otherwise a last-resort **page** carrying the stuck point.

## Architectural Impact

- **New dependencies**: none external. Reuses `baseline-verifier` (parameterized), `tools.valor_session create`, the existing local-JSON state file, and `send_telegram`.
- **Interface changes**: `baseline-verifier` gains a documented `baseline_ref` **Input field** (default `main` via a `${baseline_ref:-main}` shell default in the prompt body, preserving every existing `/do-test` caller). `AgentSession` gains a nullable `nightly_fix_handback` JSON field (the structured hand-back contract). `data/nightly_tests_last_run.json` **already carries `head_commit`** (landed on main, L1092 — consumed, not added) and gains two **per-node-keyed** maps: `fix_attempts: {node_id: count}` and `fix_sessions: {node_id: agent_session_id}`. Both are keyed by pytest node ID, matching the live per-node dedup model (`dispatched_nodes` / `compute_dispatch_set`) that #2559 put in place of the retired `dispatched_hash`; they are pruned together by exactly the rule `carry_dispatched_nodes` already uses — a node that stops appearing in `confirmed_failing` drops out of both. The keyed (rather than scalar) shape is what stops a second dispatch from orphaning the first fixer's session pointer (round-4 concern 1); note the *existing* scalar `current["dispatched_session_id"]` (L1090) stays as-is for the unchanged triage path — the fixer path uses `fix_sessions` and does not overload it.
- **Coupling**: the nightly runner already reads Redis for the fixer's session status in the watchdog; persisting the hand-back on that same `AgentSession` record (rather than a parallel JSON file) keeps one source of truth and removes a file-write race. Local-JSON stays the store for detection/dedup state only. The fixer session runs a **narrowly-mandated** make-test-edits → run-tests → open-DRAFT-PR → hand-back → STOP flow (NOT a self-driving pipeline) — no new orchestration substrate and, critically, **no new permission subsystem**.
- **Data ownership**: nightly detection/dedup state stays local JSON; the fixer session is a normal `AgentSession` owned by the worker, and its hand-back lives on that record via the Popoto ORM (`session.save()` — never raw-Redis writes).
- **Reversibility**: high — gated behind `NIGHTLY_FIX_MODE` (`off` | `shadow` | `active`). `off` restores the exact current detect-and-page behavior; `shadow` (the first-deploy default) runs classification + the decision gate and logs the decision it *would* have made, but still pages as today and dispatches nothing.

## Appetite

**Size:** Large

**Team:** Solo dev, PM, code reviewer

**Interactions:**
- PM check-ins: 2-3 (the classification-semantics adaptation and the escalation/fail-safe design are safety-critical and warrant alignment)
- Review rounds: 2+ (this ships a system that edits tests autonomously — the test-stale gate and the never-weaken-assertions invariant must be reviewed hard)

Safety-critical: a wrong autonomous fix is strictly worse than the current alert. The appetite is Large because of the review/alignment overhead the safety surface demands, not because the code volume is large.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Worker running (dispatched fixer sessions need an executor) | `./scripts/valor-service.sh worker-status` | The fixer is an `AgentSession` the worker executes |
| `baseline-verifier` subagent present | `test -f .claude/agents/baseline-verifier.md` | Classification building block |
| `valor-telegram` reachable | `test -x .venv/bin/valor-telegram` | Escalation alert channel |
| Review-required branch protection on `main` with `enforce_admins` (**prerequisite for `active` mode only**) | `gh api repos/tomcounsell/ai/branches/main/protection` | The ONLY structural never-merge backstop. **Verified ABSENT at plan time (404).** Must satisfy BOTH `required_pull_request_reviews.required_approving_review_count >= 1` AND `enforce_admins.enabled == true` before `NIGHTLY_FIX_MODE=active` — without `enforce_admins`, GitHub exempts the operator's admin-scoped token from required review and it could merge a zero-approval PR. The runner's `active`-mode preflight fails toward paging until both legs hold. `shadow`/`off` do not require it. |

No new secrets or external services. (Branch protection is a GitHub repo setting, not a new service.)

## Solution

### Key Elements

- **Silent detection log**: remove the up-front unconditional `send_telegram` on the `new_failures` branch; replace with a log line at detection. The page becomes escalation-driven only.
- **Shadow-first rollout**: `NIGHTLY_FIX_MODE` ships defaulting to `shadow` — classify, run the decision gate, log the decision, but page-as-today and dispatch nothing. Flip to `active` only after real shadow logs confirm the gate's judgment. De-risks the blast radius of an autonomous test-editor.
- **Test-stale-vs-code-regressed classifier**: reuse `baseline-verifier`, pointed at the last-known-good commit, as the *necessary precondition* for autonomy; the dispatched session performs the final test-stale determination via git archaeology under a test-file-only constraint.
- **Decision gate**: a pure function mapping the classification + caps to one of `autonomous-fix | escalate`.
- **Narrowly-mandated fixer (not a pipeline)**: the fixer is dispatched as a narrowly-scoped session, NOT an auto-continuing full-SDLC eng session. Its mandate is exactly: make test-file-only edits on a branch, run the targeted tests, open a **DRAFT** PR, write the structured hand-back, STOP. The mandate never names "run the pipeline" and never names `/do-merge`.
- **Branch protection on `main` = the real structural never-merge guarantee (external to the session)**: the ONLY mechanism that mechanically prevents self-merge under hardcoded `bypassPermissions`. Currently ABSENT (verified 404 at plan time); enabling review-required protection is a hard, code-enforced prerequisite for `active` mode. The `gh pr create --draft` flag and the forbid-`gh pr ready`/`gh pr merge` mandate are **deterrents**, not structural blocks — an unrestricted in-session bash could flip a draft ready and merge. This replaces round 2's incorrect claim that the draft flag was itself load-bearing.
- **Fail-closed test-file-only watchdog guard**: the watchdog runs `gh pr diff --name-only` **outside** the fixer session; on ANY error, non-zero exit, empty/unparseable output, any path outside `tests/`, OR a changed-file count exceeding `NIGHTLY_FIX_MAX_CHANGED_FILES` (round-4 concern 2 — the cap is wired here), it **VOIDS the PR (closes it) and PAGES a human**. Only a successful command whose every path is under `tests/` and whose changed-file count is within the cap downgrades to the low-urgency notify. It never fails open.
- **Structured hand-back protocol**: persisted on the `AgentSession` record via the ORM (nullable `nightly_fix_handback` field), consumed by the escalation watchdog.
- **Escalation + fail-safe watchdog**: pages on `blocked-escalate`, on the escalate decision, on a test-file-only guard failure/error, or when the session dies/hangs past a bounded timeout with no hand-back. On the happy path it emits a **low-urgency notify** (not a page).
- **Guardrail constants**: five named, env-overridable caps read via **raw `os.environ.get` at nightly-script module scope** (max failures, max changed files, max attempts, hand-back timeout, and the `NIGHTLY_FIX_MODE` gate), each with a provisional/tunable comment. Deliberately NOT promoted to `config/settings.py` — see Technical Approach for the rationale.

### Flow

Nightly run detects newly-confirmed failures → classify (baseline-verifier vs last-green SHA) →
**decision gate** (in `shadow` mode: log the decision below, then page-as-today and dispatch nothing; in `active` mode, execute it):
- *all test-stale-candidate & within caps* → dispatch narrowly-mandated fixer session (silent) → session edits tests, opens a **DRAFT** PR, persists `fixed-silent` hand-back on its `AgentSession` record → watchdog runs the **fail-closed** test-file-only guard on the PR diff → **guard passes (command succeeded, every path under `tests/`, changed-file count `<= NIGHTLY_FIX_MAX_CHANGED_FILES`): low-urgency notify, draft PR waits for a human to mark ready + review; guard fails (error / non-zero / empty / non-`tests/` path / count over cap): void the PR + page**.
- *any regression / inconclusive / cap exceeded* → page human now with the classification stuck-point.
- *session self-determines a code regression or judgment call mid-fix* → persists `blocked-escalate` hand-back → watchdog pages with what-was-tried + decision-requested.
- *session dies/hangs past timeout* → fail-safe watchdog pages ("autonomous fix died, suite still red").

### Technical Approach

**Reusing baseline-verifier in the no-branch nightly context (core design decision).**
`baseline-verifier` is built for PR review: it compares failing tests on a *feature branch* against `main`. Nightly has no feature branch — the suite runs on `main` HEAD, and the failure IS on `main`. The correct "known-good" reference is therefore **not** `main` but the **last-known-good commit**: since a *newly-confirmed* failure by definition was absent from the prior run's confirmed-failing set, the prior run's HEAD SHA is a commit where that test passed. So:
- Persist `head_commit = git rev-parse HEAD` in `data/nightly_tests_last_run.json` each run. **The nightly baseline is this persisted last-green-nightly SHA, NOT bare `main`** — a regression that landed on `main` since the last green nightly would be masked if we diffed against `main`, because it is already present there.
- Parameterize `baseline-verifier` with a documented **`baseline_ref` Input field**. Concretely, `.claude/agents/baseline-verifier.md` currently hardcodes the baseline at its Step 2 (`BASELINE_COMMIT=$(git rev-parse main)` and `git worktree add "$BASELINE_DIR" main`, ~lines 54-56). Replace both literal `main` tokens with a shell parameter carrying a **`${baseline_ref:-main}` default**, and add a `baseline_ref` entry to the agent's Input section documenting it. The default preserves every existing `/do-test` caller (they pass nothing → `main`); the nightly dispatch passes `baseline_ref=prev["head_commit"]`.
- Interpretation in the nightly context:
  - **PASSED at last-green, FAILS at HEAD** → newly-broken since last-green. A source-contract-moving commit *or* a code regression landed in `last_green..HEAD`. This is the set eligible for the *test-stale determination*.
  - **FAILED at last-green too** (baseline-verifier `pre_existing`) → not caused by recent change; do not auto-touch → escalate/leave.
  - **`inconclusive`** (errored/not-found at baseline) → escalate; never guess.

**The final test-stale-vs-code-regressed determination is the dispatched session's job, not a mechanical verdict.** baseline-verifier tells us "newly broken since last-green" — it does NOT by itself prove test-stale (a genuine bug also passes-then-fails). #2399 Group 2 proved a mechanical bucket is insufficient. Therefore the dispatched session:
1. Reads `git log last_green..HEAD -- <source-of-failing-test>` to find the governing commit.
2. Is **hard-constrained to edit only files under `tests/`** — if a correct fix would require touching source, that is a code regression → escalate, do not proceed.
3. Must cite the specific contract-moving commit that legitimizes each test change (the evidence #2402 produced by hand).
4. Escalates on ANY of: a suspected code regression, a contract-*direction* judgment call (Group-2 shape), inability to cite a legitimizing commit, or a cap exceeded.
5. Executes a **narrow mandate, not a pipeline**: make the test-file-only edits on a branch, run the targeted tests, open a **DRAFT** PR, write the hand-back, STOP. The mandate **never names "run the SDLC pipeline" and never names `/do-merge`.** (Concretely: `/do-build`-style test edits → run the targeted tests to confirm they pass without a NEW regression → `gh pr create --draft`. It is dispatched as a narrowly-scoped session, not an auto-continuing eng session that would drive itself onward to Merge.)

**Enforcement design — layered defense; the ONE structural guarantee lives OUTSIDE the session (BLOCKER resolution, round 3).**
Round 1 proposed a "structural never-merge via a `nightly-fixer` permission profile." **That mechanism has no implementation seam and is deleted from this plan.** Verified in the codebase: `tools.valor_session create` exposes no permission/settings flag; `AgentSession` has no permission field; every `claude-cli` spawn is hardcoded to `--permission-mode bypassPermissions` (`agent/session_runner/harness/claude.py` L154-168); and `PermissionRequest` hooks do NOT fire under `claude -p` (`agent/session_runner/hook_edge.py` L188-190). A deny-list has nowhere to attach, and bypass mode would not evaluate deny rules anyway. `config/permission_profiles/nightly_fixer.json` was fiction.

**Honest statement of the in-session limitation (round 3, the surviving BLOCKER).** The fixer is a `--role eng` `AgentSession` spawned with hardcoded `--permission-mode bypassPermissions` = unrestricted bash. **Nothing in-session can mechanically stop it from running `gh pr ready <url>` then `gh pr merge <url>`.** Round 2 wrongly called `gh pr create --draft` "the load-bearing structural never-auto-merge guarantee" — a draft PR is only unmergeable until a `gh pr ready` that this same session's bash is free to issue. The draft flag is therefore a **deterrent and a detective signal, NOT a structural in-session block.** We do not claim in-session structural prevention, because under bypass mode none exists. This plan states that plainly and puts the real guarantee outside the session.

**The real structural guarantee is server-side and external to the session: GitHub branch protection on `main`.** Branch protection requiring a PR review approval from someone *other than the author* makes self-merge impossible regardless of the fixer's bash access — it is enforced by GitHub, cannot be overridden from within the session, and is therefore the single load-bearing never-merge mechanism. **Current state, verified at plan time via `gh api repos/tomcounsell/ai/branches/main/protection`: `404 Not Found` — `main` is NOT protected (or protection is unreadable by the operator token). The load-bearing backstop does not exist today.** Consequently the builder MUST, as an explicit task: (a) re-verify current protection with `gh api repos/tomcounsell/ai/branches/main/protection` and `gh repo view`, and document the finding in the feature doc; (b) if review-required protection is absent, treat **enabling review-required branch protection on `main` (require a PR review approval, dismiss stale approvals, disallow author self-approval) as a hard prerequisite for ever setting `NIGHTLY_FIX_MODE=active`** — and enforce it *in code* (a runtime preflight, below), not just in prose.

The never-auto-merge posture is a layered defense whose ONLY structural leg lives outside the session:

1. **Server-side branch protection on `main` — STRUCTURAL, external, load-bearing.** The one mechanism that actually prevents self-merge. Currently ABSENT (404 above). Gated in code as a prerequisite for `active` mode (runtime preflight below).
2. **Shadow-first default — blast-radius bound.** `NIGHTLY_FIX_MODE` defaults to `shadow`, which dispatches nothing; `active` is a deliberate, documented opt-in gated on #1.
3. **Narrow mandate + anti-criteria deterrent — in-session, deterrent only.** The mandate explicitly FORBIDS `gh pr ready`, `gh pr merge`, and push-to-`main`; the PR is opened with `gh pr create --draft`. Anti-criteria assert the dispatch prompt and runner code never emit `gh pr ready`/`gh pr merge` and DO emit `--draft`. Because bash is unrestricted this reduces the chance a well-behaved session strays; it is NOT a structural block.
4. **Extended fail-closed watchdog — merge/draft-state detective layer.** Beyond the diff-path guard, the watchdog runs `gh pr view <pr> --json isDraft,state,mergedAt`. If the PR is not draft, or is merged/closed unexpectedly, or the command errors/returns empty → **page** (and `gh pr close <pr>` if still open). **Honest scope: DETECTIVE — it fires on the next nightly preamble (~24h later), catches-and-pages, and does NOT stop a same-session merge. #1 is what prevents that.**

The enforcement is this stack, with branch protection (#1) the only structural leg; everything in-session (#3) is deterrence and everything in the watchdog (#4) is next-night detection.

**Runtime `active`-mode branch-protection preflight (enforces the shadow→active flip, round-3 concern).** Because "flip `NIGHTLY_FIX_MODE=active` only after branch protection is enabled" is unenforceable as prose, the runner enforces it: when `NIGHTLY_FIX_MODE == "active"`, before dispatching any fixer the runner calls `gh api repos/{owner}/{repo}/branches/main/protection` and confirms review-required protection is present. `active` mode requires **BOTH** legs of the same boolean short-circuit to hold: `data["required_pull_request_reviews"]["required_approving_review_count"] >= 1` **AND** `data["enforce_admins"]["enabled"] is True`. The `enforce_admins` leg is load-bearing, not decorative: GitHub **exempts repo admins from required-review enforcement unless `enforce_admins.enabled == true`** (the default is off), and the fixer's `gh` runs under the operator's admin-scoped token — so without this leg an admin token could `gh pr merge` a zero-approval PR straight to `main`, defeating the sole structural never-merge backstop. On 404 / error / missing-review-requirement / `enforce_admins` absent-or-false, the runner **refuses to dispatch, logs the reason, and falls back to the escalate-and-page path** (fail toward paging) — `active` mode cannot dispatch an autonomous fixer into an unprotected (or admin-exempt) `main`. This makes the prerequisite a code gate, not a hope.

**Decision gate** (`decide_fix_or_escalate(classification, new_failures, caps, run_flags) -> "autonomous-fix" | "escalate"`): pure, unit-testable, no I/O. Autonomy iff **all** of:

```
not run_flags.is_seed_run              # a re-baseline declares state, it does not discover a regression
and not run_flags.integrity_warnings   # an untrusted confirmed set must not be edited against
and not run_flags.dry_run              # --dry-run never spawns a fixer
and not run_flags.dispatch_truncated   # MAX_DISPATCH_NODES cut the picture short
and classification.regressions == []
and classification.inconclusive == []
and set(new_failures) <= set(test_stale_candidates)
and len(new_failures) <= NIGHTLY_FIX_MAX_FAILURES
and all(fix_attempts.get(n, 0) < NIGHTLY_FIX_MAX_ATTEMPTS for n in new_failures)
```

The gate consumes **`new_failures`** (from `compute_new_failures`, L654) — that is the population this feature promises to fix before paging. It is deliberately *not* `compute_dispatch_set`'s output, which answers the different question of what has never been filed; the two sets diverged in #2559 and conflating them would let a standing, already-filed failure enter the autonomous-fix path. Attempt-count dedup nonetheless rides on the per-node model, hence the `all(...)` over node IDs rather than a single set-hash lookup. baseline-verifier returns discrete buckets (no numeric score), so "high confidence" is bucket membership, not a threshold float. In `shadow` mode the verdict is computed and logged but not acted on.

**Structured hand-back — persisted on the `AgentSession` record via the ORM (reconsidered per critique).** The fixer is *already* a normal `AgentSession` the worker owns, and the watchdog *already* reads that record via `AgentSession.query`. So rather than duplicate a parallel file-based store (with its own atomic-write dance and read/write race), the hand-back lives on the session itself: add a nullable `nightly_fix_handback` JSON field to `AgentSession`. The fixer writes it at terminal through a thin, documented helper CLI (`python -m tools.nightly_fix_handback write ...`) that loads the session by id and calls `session.save()` — **ORM only, never raw Redis**. Schema: `{disposition: "fixed-silent"|"blocked-escalate"|"still-working", pr_url, what_broke, classification, tried, blocked_reason, decision_requested, written_at}`. This is an additive nullable field: per `_heal_descriptor_pollution` (issues #1099/#1172) existing records read it as `None` with no backcompat code; it is still registered as a no-op idempotent entry in `scripts/update/migrations.py` per the repo's Popoto-schema-change convention.

**Escalation watchdog + fail-safe.** At the start of each nightly run, before running tests, inspect each *prior* dispatch (if any) by iterating `fix_sessions` and loading each `agent_session_id` — read its session status **and its `nightly_fix_handback` field** via `AgentSession.query` (ORM — never raw Redis). The keyed `fix_sessions` map (round-4 concern 1) is what lets the watchdog resolve exactly which record belongs to each still-red node, even across multiple concurrent dispatches. Distinct session ids are de-duplicated before loading, since one fixer session covers several nodes.
- `blocked-escalate` → **page** with the hand-back's stuck-point content; prune those nodes' entries from `fix_attempts` and `fix_sessions`.
- session terminal `failed`/`killed`/`abandoned` with no `fixed-silent` hand-back → fail-safe **page**.
- session non-terminal but older than `NIGHTLY_FIX_HANDBACK_TIMEOUT_HOURS` → fail-safe **page** ("fix attempt hung, suite still red").
- `fixed-silent` with a `pr_url` → run **two fail-closed mechanical guards**, both against the exact `pr_url` persisted in the hand-back (never a PR discovered by branch/search — see the new-vs-preexisting note below):
  - **Diff-path guard**: `gh pr diff --name-only <pr_url>`. Downgrades to a low-urgency notify **only** when the command exits zero AND returns non-empty, parseable output AND every changed path is under `tests/` AND the changed-file **count is `<= NIGHTLY_FIX_MAX_CHANGED_FILES`** (round-4 concern 2 — this is where the previously-declared-but-unwired cap earns its keep: a test-file-only diff that nonetheless rewrites an implausibly large number of files is not a mechanical carry-forward and must escalate). On ANY of {non-zero exit, error/exception, empty or unparseable output, any path outside `tests/`, changed-file count `> NIGHTLY_FIX_MAX_CHANGED_FILES`} → treat the fix as void.
  - **Merge/draft-state guard (round-3, detective)**: `gh pr view <pr_url> --json isDraft,state,mergedAt`. The fix stays eligible for notify **only** when the PR is still a draft AND `state == "OPEN"` AND `mergedAt` is null. On ANY of {`isDraft == false`, `state != "OPEN"`, `mergedAt` non-null, command error/empty} → treat the fix as void. This catches the honest-limitation case where an in-session bash issued `gh pr ready`/`gh pr merge` despite the mandate — it is **detective (next-night), not preventive**; branch protection (#1) is what actually prevents the merge.
  - On ANY guard voiding the fix → **close the PR (`gh pr close <pr_url>`, if still open) and page** ("autonomous fix could not be verified test-file-only-and-still-draft — review required"). Neither guard ever fails open. Both run in the nightly runner's watchdog preamble and do not trust the session's self-report.

**New-vs-preexisting-PR resolution (round-3 concern).** The watchdog identifies the fixer's PR by exactly one means: the `pr_url` written into the hand-back on the fixer's own `AgentSession` record. It never enumerates open PRs, never matches by head branch, and never inspects any PR the fixer did not create. A hand-back with a `fixed-silent` disposition but a null/absent/unparseable `pr_url` is treated as invalid → fail-safe **page** (a claimed silent fix with no PR to guard is a contradiction, never a notify). This removes the earlier ambiguity between "the fixer opened a new draft PR" and "the watchdog inspects a PR" — there is one PR, named by the hand-back, or there is a page.

**Verifiable low-urgency notify (distinct from a page).** `send_telegram(msg, dry_run=...)` currently has no urgency parameter, so a bare-string "notify" would be functionally identical to a page and would satisfy the old prose anti-criterion vacuously. The fix: add a `silent: bool = False` parameter to the `send_telegram` wrapper, forwarded to `valor-telegram send --silent` (Telegram `disable_notification`). The happy-path notify calls `send_telegram(msg, silent=True)`; every page keeps the default `silent=False`. The anti-criterion asserts the call signature — `grep -c "send_telegram(.*silent=True"` ≥ 1 — not a prose substring, so it can only pass if a genuinely distinct low-urgency call exists.

**Worst-case fail-safe latency (critique correction).** The watchdog is a *preamble to the next nightly run*, so `NIGHTLY_FIX_HANDBACK_TIMEOUT_HOURS` does not bound *detection* latency — it only decides whether a still-running dispatch is judged "hung" when the preamble next fires. The true worst-case latency for surfacing a hung/dead fixer is therefore **~one nightly cadence (~24h)**, not the 6h timeout. This is **accepted for the first cut**: (a) `shadow` mode is the default, so no real dispatch happens until we opt in; (b) a hung fixer produces no merge (structurally impossible) and leaves the suite in exactly the red state a human would have seen under today's detect-and-page anyway — the feature cannot make latency *worse* than the status quo, only the happy path faster. If ~24h proves unacceptable after `active` rollout, a dedicated intra-day launchd watchdog is filed as follow-up **#2405**; it is deliberately out of scope here to avoid a second scheduled job before the core gate is proven.

**Dedup — per-node keying, matching the live model (rebased 2026-09-02).** The plan originally extended `dispatched_hash`, a sha256-of-the-sorted-failing-set key. **That mechanism no longer exists**: #2559 / PR #2581 replaced set-hash dedup with **per-node** dedup, and the live substrate is `dispatched_nodes` plus `prior_dispatched()` / `compute_dispatch_set()` / `carry_dispatched_nodes()`. Re-keying to match is not cosmetic — a set-hash key has no producer left, and inventing a second, parallel keying scheme in the same state file would reintroduce exactly the two-sources-of-truth shape #2559 removed.

Persist two maps in `data/nightly_tests_last_run.json`, both keyed by **pytest node ID**:
- `fix_attempts: {node_id: count}` — how many times an autonomous fix has been attempted for that node. **This is the ONE name and the ONE semantics for attempt tracking; there is no scalar `fix_attempt_count`** (round-4 concern 3, preserved).
- `fix_sessions: {node_id: agent_session_id}` — which `AgentSession` each still-red node was handed to, so the watchdog can resolve exactly the right record. Keyed rather than scalar because a scalar pointer is orphaned the moment a second dispatch happens (round-4 concern 1, preserved). It is a *separate* key from the pre-existing scalar `dispatched_session_id` (L1090), which continues to serve the unchanged triage path untouched.

Reset semantics, expressed in terms of the live helpers:
- **A node entering the failing set starts at 0.** No hash to invalidate; a node absent from both maps is simply unattempted.
- **`fix_attempts[node]` increments once per dispatch attempt**, at dispatch time before the session runs, so a crash mid-attempt still counts (fail toward escalate, never infinite-retry).
- **Going green prunes the node from BOTH maps**, by the same rule `carry_dispatched_nodes` already applies to `dispatched_nodes`: keep a key only while its node is still in `confirmed_failing`. The maps therefore hold only currently-red nodes, cannot grow unbounded, and a node that later re-regresses starts fresh with no stale session pointer. Reusing the existing carry rule (rather than a bespoke one) keeps all three maps pruning in lockstep.
- **Cap-exceeded escalates, does not silently drop.** When any node in `new_failures` has reached `NIGHTLY_FIX_MAX_ATTEMPTS`, the run escalates (pages) instead of re-dispatching.
- **Composes with, does not duplicate, `MAX_DISPATCH_NODES`.** That cap (L185) governs triage dispatch volume; `NIGHTLY_FIX_MAX_FAILURES` governs autonomy eligibility. A truncated run is a hard disqualifier for autonomy (see the decision gate), so the two caps never have to be reconciled numerically.

**In-process, not Cowork, for the first cut** (open question 5): reuse the existing `maybe_dispatch_triage_session` machinery (rename → `maybe_dispatch_fix_session` with the new mandate). Cowork parity is filed as follow-up **#2405**.

**Guardrail constants — raw `os.environ.get` at module scope, NOT `config/settings.py` (critique decision).** All five knobs (`NIGHTLY_FIX_MODE`, `NIGHTLY_FIX_MAX_FAILURES`, `NIGHTLY_FIX_MAX_CHANGED_FILES`, `NIGHTLY_FIX_MAX_ATTEMPTS`, `NIGHTLY_FIX_HANDBACK_TIMEOUT_HOURS`) are declared as module-level constants in `scripts/nightly_regression_tests.py`, each read via `os.environ.get(...)` with an in-code default and a one-line provisional/tunable grain-of-salt comment. This is a deliberate, stated choice, not an oversight: `config/settings.py`'s `TimeoutSettings` is the home for cross-cutting timeout/retry/TTL values consumed across the bridge/worker/agent runtime (per `docs/features/config-timeout-catalog.md`'s promote-vs-name-locally criterion). These five are single-consumer knobs of one standalone launchd script (`nightly_regression_tests.py` imports nothing from the runtime config surface today), so naming them locally keeps them next to their only reader and avoids polluting the global settings namespace. If a second consumer ever appears, promote them then. The anti-criterion greps for `os.environ.get("NIGHTLY_FIX` to assert no bare magic numbers survive in the new paths.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The existing `except Exception` swallow in `maybe_dispatch_*` (dispatch failure → "no dispatch") is preserved; add a test asserting a failed dispatch logs a warning AND leaves the `fix_attempts`/`fix_sessions` entries for those node IDs untouched so a retry is possible.
- [ ] baseline-verifier invocation failure (worktree add fails, timeout) → classified `inconclusive` → **must route to escalate**, not to autonomous-fix. Test the fail-toward-paging path explicitly.
- [ ] `nightly_fix_handback` field absent/null/malformed on the session record when the watchdog reads it → treated as "no hand-back" → fail-safe page (never silently assume success). Test with a `None` field and a non-dict/invalid payload.
- [ ] `fixed-silent` hand-back whose PR diff touches a non-`tests/` path → the mechanical guard voids the fix (closes the PR) → **page** (not a notify). Test by stubbing `gh pr diff --name-only` to return a `src/` path.
- [ ] **Fail-closed diff-path guard**: stub `gh pr diff --name-only` to (a) exit non-zero, (b) raise/throw, (c) return empty/whitespace output, and (d) return an all-`tests/` diff whose file count `> NIGHTLY_FIX_MAX_CHANGED_FILES` (round-4 concern 2) — each case must void the fix → **page**, never a notify. Asserted alongside the non-`tests/` case above.
- [ ] **Fail-closed merge/draft-state guard (round-3)**: stub `gh pr view --json isDraft,state,mergedAt` to return (a) `isDraft=false`, (b) `state="MERGED"`/non-null `mergedAt`, (c) `state="CLOSED"`, and (d) command error/empty — each must void the fix → close (if open) + **page**, never a notify. This is the detective backstop for a strayed in-session `gh pr ready`/`gh pr merge`.
- [ ] **`fixed-silent` hand-back with null/absent/unparseable `pr_url`** → invalid → fail-safe page (a claimed silent fix with no PR to guard is never a notify). Test.
- [ ] **Active-mode branch-protection preflight**: with `NIGHTLY_FIX_MODE=active`, stub `gh api .../branches/main/protection` to return (a) 404, (b) error, (c) a body with no `required_pull_request_reviews` / `required_approving_review_count < 1`, (d) **`enforce_admins.enabled == false` or absent even when `required_approving_review_count >= 1`** (round-4 admin-exempt case) — each must refuse dispatch and route to escalate-and-page (`valor_session create` NOT called; `send_telegram` called). Only when BOTH `required_approving_review_count >= 1` AND `enforce_admins.enabled == true` does dispatch proceed. Test.

### Empty/Invalid Input Handling
- [ ] `new_failures == []` → no classification, no dispatch, no page (clean-run path unchanged). Test.
- [ ] `prev["head_commit"]` absent (first run after deploy, or state migrated from an older schema without the field) → cannot establish a last-green baseline → escalate (do not attempt an unbaselined fix). Test.
- [ ] Empty/whitespace `disposition` in a hand-back → treated as invalid → fail-safe page. Test.

### Error State Rendering
- [ ] Escalation Telegram content is asserted to contain the stuck-point fields (what broke, tried, decision requested) — not a raw node-ID dump — on the `blocked-escalate` path.
- [ ] Fail-safe page fires (asserted via `send_telegram` call capture) when the session is terminal-failed with no hand-back.
- [ ] `fixed-silent` + guard-pass emits a **low-urgency notify** via `send_telegram(msg, silent=True)` — a distinct call signature from the high-urgency page (`silent=False`), asserted via call capture on the `silent` kwarg (not a prose substring) — the happy path is not fully silent.

### Run-Shape Disqualifiers (added in the 2026-09-02 freshness rebase)
- [ ] **Seed / re-baseline run** (`is_seed_run` true — first run, or `prev["collection"] != COLLECTION_PATHS`): even in `active` mode with an all-test-stale classification, NO fixer is dispatched; the run takes its existing seed path. A baseline declares known-failing state; autonomously "fixing" an absorbed population would rewrite tests wholesale. Asserted: `valor_session create` not called on the fixer path.
- [ ] **Integrity-warned run** (`validate_run_integrity` returned non-empty `integrity_warnings`, e.g. the shallow-shrink warning): no dispatch → escalate. A `confirmed_failing` set the detector itself distrusts must never be edited against. Asserted.
- [ ] **`--dry-run`**: no fixer dispatched and no PR opened, mirroring the existing `DRY_RUN_SESSION_ID` short-circuit contract — the one command an operator reaches for to preview a run must stay previewable. Asserted.
- [ ] **Truncated dispatch** (`len(dispatch_nodes) > MAX_DISPATCH_NODES`): no autonomous fix on a run whose picture is knowingly incomplete → escalate. Asserted.

### Mode Gating
- [ ] `NIGHTLY_FIX_MODE=shadow` (the default): the decision gate runs and logs its verdict, but NO fixer session is dispatched AND the up-front page still fires as today. Asserted: `valor_session create` not called, `send_telegram` called.
- [ ] `NIGHTLY_FIX_MODE=off`: exact current detect-and-page behavior; no classification, no gate. Asserted.
- [ ] `NIGHTLY_FIX_MODE=active`: full dispatch path exercised.

## Test Impact

- [ ] `tests/unit/test_nightly_regression_tests.py` — UPDATE: the `new_failures` branch no longer pages up front in `off`/`active` mode; assertions expecting an immediate `send_telegram` on new failures must move to the escalate/fail-safe paths (or be pinned to `shadow`, which still pages as today). If `maybe_dispatch_triage_session` is renamed, UPDATE its tests to the new name + bounded-fix prompt. **Do not disturb** the per-node dedup tests (`compute_dispatch_set` / `carry_dispatched_nodes` / `prior_dispatched` / `seeded_nodes`), the seed/re-baseline tests, or the run-integrity tests — this feature layers around them and must leave their behavior byte-identical.
- [ ] Any test asserting the dispatch prompt contains "Do NOT attempt an auto-hotfix" — REPLACE: the mandate names only make-test-edits/run-tests/open-DRAFT-PR/hand-back/STOP (never `/do-merge`, never "run the pipeline"), and the never-auto-merge posture is asserted via the draft-PR flag (`gh pr create --draft`) plus the fail-closed watchdog guard.
- [ ] `tests/unit/` baseline-verifier tests (if any assert the hardcoded `main` ref) — UPDATE: `baseline_ref` Input field with `${baseline_ref:-main}` default; assert the default still resolves to `main` for existing callers.
- [ ] `tests/unit/` AgentSession model/schema tests — UPDATE/ADD: assert the new nullable `nightly_fix_handback` field round-trips via the ORM and reads `None` on legacy records.
- [ ] If no direct nightly-runner unit tests exist yet, the build ADDS them (decision-gate, watchdog, hand-back parsing, mechanical guard, mode gating) — see Step by Step Tasks.

Justification for anything not affected: the base detector mechanics (`run_tests`, `reconfirm_serial`, run lock, TTFT gate, `load_env_or_die`) are untouched — this feature layers a classification/decision/escalation stage around the existing `new_failures` branch.

## Rabbit Holes

- **Building a general-purpose "is this test stale?" classifier.** Do not. The mechanical precondition (baseline-verifier vs last-green) plus the test-file-only session constraint plus escalate-on-doubt is the whole design. Trying to fully mechanize the test-stale judgment reproduces the #2399 Group-2 trap.
- **Reworking baseline-verifier's internals.** Only add an optional baseline-ref parameter; do not touch its junitxml classification or worktree lifecycle.
- **A bespoke session-orchestration/steering substrate for the fixer.** It's a normal `AgentSession` with a narrow mandate (make test edits → run tests → open a DRAFT PR → hand-back → STOP). Do not invent a new runner; do not dispatch it as an auto-continuing eng session that would drive itself through Merge.
- **A new permission subsystem for the fixer.** Do NOT build one. Round 1's `nightly-fixer` permission profile had no seam (bypass-mode spawns, no session permission field). The structural never-merge guarantee is server-side branch protection on `main` (external to the session); the draft flag + forbid-ready/merge mandate + fail-closed watchdog are deterrent/detective layers. Do not re-introduce a permission-deny-list spike, and do not claim any in-session mechanism structurally prevents merge — under `bypassPermissions` none can.
- **Cowork migration now.** Deferred to #2405. Resisting it here is the point.
- **Tuning the guardrail numbers to perfection.** Ship provisional env-overridable constants with grain-of-salt comments; tune from real nightly data later.

## Risks

### Risk 1: The fixer weakens a test to make a genuine regression pass (the catastrophic case)
**Impact:** A real bug is masked by a test edit and silently merged after a rubber-stamp review.
**Mitigation:** Layered defense whose only structural leg is external to the session — (a) **server-side branch protection on `main`** requiring a non-author review approval (STRUCTURAL, load-bearing; GitHub-enforced, cannot be overridden by the session's bash; currently ABSENT per the 404, so enabling it is a code-enforced prerequisite for `active` mode via the runtime preflight); (b) **shadow-first default** so nothing dispatches until a deliberate opt-in; (c) the **narrow mandate** (make test edits → run tests → open draft PR → STOP) that never names `/do-merge`/"run the pipeline" and **explicitly forbids `gh pr ready`/`gh pr merge`/push-to-`main`** — a deterrent, since bash is unrestricted; (d) the fixer opens a **DRAFT** PR with `gh pr create --draft` — a deterrent + detective marker, NOT a structural block (an in-session bash could flip it ready and merge; honest); (e) the watchdog's **fail-closed** diff-path guard AND the round-3 **merge/draft-state guard** (`gh pr view --json isDraft,state,mergedAt`) that detect a strayed merge next-night and page (detective, not preventive); (f) escalate on any inconclusive/regression bucket; (g) the fix branch re-runs the targeted tests to confirm no NEW regression. This is a stack of layers, with (a) the only structural guarantee — NOT a single permission gate, and NOT an in-session structural claim.

### Risk 2: baseline-verifier's branch-vs-main model is misapplied to nightly
**Impact:** Wrong classification → either false autonomy (dangerous) or false escalation (noisy).
**Mitigation:** The last-green-SHA mapping is the explicit design; on any missing/absent baseline the gate fails toward escalate. Covered by the empty-input tests. Flagged as Open Question 1 for critique.

### Risk 3: Silence swallows a regression (session dies, no page ever fires)
**Impact:** A red suite goes unnoticed — worse than today.
**Mitigation:** The fail-safe watchdog pages on terminal-failure/hang. Note (per critique): because the watchdog is a next-run preamble, worst-case *detection* latency is ~one nightly cadence (~24h), not the 6h timeout — accepted for the first cut (shadow default; a hung fixer cannot merge and leaves the suite exactly as red as today's status quo). Intra-day watchdog filed as #2405 if needed. Explicitly tested.

### Risk 4: Re-dispatch storm / cost blowup
**Impact:** The same failing set spawns a fresh fixer session every night, burning tokens.
**Mitigation:** the per-node `fix_attempts: {node_id: count}` map rides alongside the live `dispatched_nodes` dedup; past `NIGHTLY_FIX_MAX_ATTEMPTS` the node escalates instead of re-dispatching.

## Race Conditions

### Race 1: Watchdog reads a hand-back the fixer session is still writing
**Location:** `AgentSession.nightly_fix_handback` field write (fixer session, via `session.save()`) vs read (next nightly preamble, via `AgentSession.query`).
**Trigger:** A long-running fixer session still executing when the next nightly fires.
**Data prerequisite:** The hand-back must be complete before the watchdog treats it as authoritative.
**State prerequisite:** A null/partial hand-back must not read as success.
**Mitigation:** Moving the hand-back onto the `AgentSession` record (from the earlier file design) **removes the partial-read hazard entirely** — a Popoto record write is a single atomic hash write; a reader sees either the prior value or the fully-written new one, never a torn payload. The watchdog treats null/absent/malformed as "no hand-back" → fail-safe (never as success), and cross-checks the session's terminal status so a still-running (`updated_at` fresh, non-terminal) session is never mistaken for done. The nightly run lock still prevents two nightly runs overlapping.

### Race 2: Two nightly invocations overlap
**Location:** `main()` entry.
**Mitigation:** Unchanged — the existing `_acquire_run_lock()` flock already serializes nightly runs; the loser is a no-op.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2405] Migrating the nightly fixer dispatch to a Claude Cowork routine (parity with #2209). First cut stays in-process per open question 5.
- [SEPARATE-SLUG #2405] Any cross-run learning / auto-tuning of the guardrail constants from historical nightly data — the constants ship provisional and env-overridable; adaptive tuning is out of scope.
- [SEPARATE-SLUG #2405] A dedicated intra-day launchd watchdog to shrink the ~24h fail-safe detection latency — only warranted if the next-run-preamble latency proves too slow after `active` rollout; the first cut relies on the nightly-cadence preamble.

The invariants "never auto-merge to main" and "never edit source (non-test) files to make a failing test pass" are NOT deferrals — they are permanent safety boundaries of this feature, enforced by a **layered defense whose only structural leg is external to the session**: **server-side branch protection on `main`** (the load-bearing mechanism; GitHub-enforced, currently ABSENT and therefore a code-enforced `active`-mode prerequisite), plus in-session **deterrents** (narrow mandate that never names `/do-merge` and forbids `gh pr ready`/`gh pr merge`; `gh pr create --draft`) and **detective** watchdog guards (fail-closed diff-path guard + fail-closed merge/draft-state guard). We do NOT claim any in-session mechanism structurally prevents merge — under hardcoded `bypassPermissions` none can. All layers are asserted as Verification anti-criteria below.

## Update System

- New env-overridable constants (`NIGHTLY_FIX_MODE`, `NIGHTLY_FIX_MAX_FAILURES`, `NIGHTLY_FIX_MAX_CHANGED_FILES`, `NIGHTLY_FIX_MAX_ATTEMPTS`, `NIGHTLY_FIX_HANDBACK_TIMEOUT_HOURS`) have safe in-code defaults (`NIGHTLY_FIX_MODE` defaults to `shadow`), so no `.env` propagation is required for the feature to run. Document them in `.env.example` (commented) for discoverability only, noting the `off`/`shadow`/`active` rollout path. **Env-completeness nit (round-3): the `.env.example` completeness check requires a comment line immediately above each `KEY=` line** (per CLAUDE.md → "Adding a new secret"), so each of the five keys ships as a two-line block — a `#`-comment describing the knob + its default + (for `NIGHTLY_FIX_MODE`) the note that `active` requires branch protection on `main`, followed by the commented `# NIGHTLY_FIX_...=` placeholder. Add these blocks so the completeness check passes; a bare `KEY=` with no preceding comment would fail it.
- **Popoto schema change**: the additive nullable `AgentSession.nightly_fix_handback` field is registered as an idempotent no-op entry in `scripts/update/migrations.py`'s `MIGRATIONS` dict per the repo convention. No data backfill runs (`_heal_descriptor_pollution` reads legacy records as `None`); the entry exists to satisfy the schema-change convention and record the field's introduction.
- No new launchd job: the escalation/fail-safe watchdog runs as a preamble inside the existing nightly launchd invocation, so `scripts/install_nightly_tests.sh` needs no change. (A dedicated intra-day watchdog, if the ~24h fail-safe latency proves too slow after `active` rollout, is deferred to #2405.)
- No new config files or permission subsystem — the never-auto-merge guarantee comes from `gh pr create --draft` (GitHub-native) plus the fail-closed watchdog, so there is nothing to propagate. (Round 1's `config/permission_profiles/nightly_fixer.json` is removed from the design — it had no implementation seam.)
- No `data/` directory or gitignore entry is added (the hand-back moved onto the ORM record; the earlier file-based store is dropped).
- No `/update` skill changes required — this is internal to an already-deployed scheduled job.

## Agent Integration

- The dispatched fixer is an Eng `AgentSession` created via the existing `python -m tools.valor_session create --role eng --slug ... --json --message ...` path — already agent-reachable — with a **narrow mandate carried in its dispatch message** (make test edits → run tests → open a DRAFT PR → hand-back → STOP). No new session-create flag, no permission profile, no settings seam is required (round 1's assumption that one existed was false: `tools.valor_session create` has no permission flag, `AgentSession` no permission field, spawns are hardcoded `bypassPermissions`). The never-auto-merge guarantee lives entirely in the mandate + the `gh pr create --draft` call the fixer makes + the fail-closed watchdog — none of which needs a new capability.
- New helper CLI `python -m tools.nightly_fix_handback` (write/read) so the fixer session has a documented, testable way to emit its structured hand-back. It loads the target `AgentSession` and persists the hand-back via `session.save()` (ORM only). Register it in `pyproject.toml [project.scripts]` if a console entry point is warranted (e.g. `valor-nightly-fix-handback`), otherwise `python -m` invocation is sufficient — decide at build time.
- The nightly runner itself is a script, not a bridge tool; no bridge import changes.
- Integration test: assert (a) the dispatched session's mandate names make-test-edits/run-tests/open-DRAFT-PR/hand-back/STOP, does NOT name `/do-merge` or "run the pipeline", explicitly forbids `gh pr ready`/`gh pr merge`/push-to-`main`, and includes `gh pr create --draft`; (b) a `fixed-silent` hand-back with a `tests/`-only PR diff AND a still-draft/OPEN/unmerged PR (both guards pass) emits a low-urgency notify (`send_telegram(..., silent=True)`) and no page; (c) a `fixed-silent` hand-back whose PR diff touches a non-`tests/` path — OR whose diff/merge-state guard command errors/returns empty — OR whose PR is not-draft/merged/closed — is voided → the PR is closed → page; (d) a `blocked-escalate` hand-back fires a page; (e) in `active` mode with branch protection absent (preflight 404), no fixer is dispatched and a page fires.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/nightly-alert-triage.md` — the mandate flipped from investigate-only to bounded-fix-before-alert; document the classification gate, the never-weaken-assertions invariant, the **layered enforcement and its honest limits** (server-side branch protection on `main` as the ONLY structural never-merge leg; in-session `bypassPermissions` cannot mechanically block `gh pr ready`/`gh pr merge`; draft flag + forbid-ready/merge mandate are deterrents; fail-closed diff-path + merge/draft-state watchdog guards are detective/next-night; shadow-first default — explicitly NOT a permission gate and NOT an in-session structural claim), the branch-protection verification finding + the `active`-mode runtime preflight, the ORM-based hand-back protocol, the notify-vs-page tiering (`send_telegram(silent=True)`), the shadow→active rollout, and the guardrail constants.
- [ ] Cross-link from `docs/features/nightly-regression-tests.md` (base detector) and note the new stage in the flow.
- [ ] Add/confirm entry in `docs/features/README.md` index.

### Inline Documentation
- [ ] Each guardrail constant carries a grain-of-salt/provisional comment and its env-override name.
- [ ] Docstring the decision-gate function, the hand-back schema, and the baseline-ref parameterization of `baseline-verifier`.

## Success Criteria

- [ ] In `active` mode, on a run where every newly-confirmed failure is test-stale-candidate and within caps, **no up-front Telegram page** fires; a narrowly-mandated fixer session is dispatched and (on success) a **draft** PR exists plus a low-urgency notify — verified end-to-end in an integration test with a seeded state file.
- [ ] The default `NIGHTLY_FIX_MODE=shadow` computes+logs the gate verdict but dispatches nothing and still pages as today; `off` reproduces exact current behavior. Both asserted.
- [ ] On any `regression`/`inconclusive` classification, on cap-exceeded, or on a missing last-green baseline, the run **escalates** (pages) and does NOT dispatch an autonomous fix.
- [ ] The fixer opens a **DRAFT** PR (grep-verifiable: `gh pr create --draft` present in the dispatch instruction / fixer path); its mandate names only make-test-edits/run-tests/open-draft-PR/hand-back/STOP (grep-verifiable: no `do-merge`/`do_merge` in the fixer path) and explicitly forbids `gh pr ready`/`gh pr merge`/push-to-`main` (grep-verifiable: `grep -Ec "gh pr ready|gh pr merge"` == 0 in the runner; integration test asserts the dispatch prompt forbids them). The structural never-merge guarantee is **server-side branch protection on `main`** (external), NOT the draft state and NOT a permission profile (none exists); the draft flag is a deterrent/detective marker only.
- [ ] Branch-protection state on `main` is verified and documented (`gh api repos/tomcounsell/ai/branches/main/protection`); in `active` mode a runtime preflight refuses to dispatch (and pages) unless protection satisfies BOTH `required_pull_request_reviews.required_approving_review_count >= 1` AND `enforce_admins.enabled == true` (the round-4 blocker: admin tokens are review-exempt without `enforce_admins`; the fixer's `gh` runs under the operator's admin-scoped token) (grep-verifiable: `branches/main/protection` AND `enforce_admins` present in the runner; test asserts active-mode-without-either-leg → escalate, no dispatch).
- [ ] The watchdog runs a fail-closed **merge/draft-state guard** (`gh pr view <pr> --json isDraft,state,mergedAt`): a not-draft / merged / closed / errored PR voids the fix → close (if open) + page (asserted, including the merged and command-error cases).
- [ ] The watchdog's **fail-closed** mechanical test-file-only guard voids a `fixed-silent` PR (closes it) and pages on any non-`tests/` path, non-zero exit, error, empty output, or changed-file count exceeding `NIGHTLY_FIX_MAX_CHANGED_FILES` (asserted, including the error/empty and count-over-cap cases). `NIGHTLY_FIX_MAX_CHANGED_FILES` is wired into this guard (round-4 concern 2 — no longer a dead constant).
- [ ] `blocked-escalate` hand-back → page with stuck-point content; `fixed-silent` + `tests/`-only PR (guard command succeeds) → low-urgency notify via `send_telegram(silent=True)` (no page); session-dead/hung-past-timeout → fail-safe page. All asserted via call-signature capture.
- [ ] A seed/re-baseline run, an integrity-warned run, a `--dry-run`, or a `MAX_DISPATCH_NODES`-truncated run **never** dispatches an autonomous fixer, even in `active` mode with a clean all-test-stale classification (asserted for each of the four).
- [ ] The existing per-node dedup, seed/umbrella, and run-integrity behavior is unchanged: `tests/unit/test_nightly_regression_tests.py` passes without modification to any `compute_dispatch_set` / `carry_dispatched_nodes` / `seeded_nodes` / `validate_run_integrity` assertion.
- [ ] Every guardrail number is a named env-overridable constant read via `os.environ.get` with a provisional comment (grep-verifiable: no bare magic numbers in the new code paths).
- [ ] No raw-Redis operations on Popoto-managed keys in the new code (session status AND hand-back read/written via the ORM).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

The lead orchestrates; it never builds directly.

### Team Members

- **Builder (classifier + decision gate)**
  - Name: `gate-builder`
  - Role: baseline-ref parameterization of baseline-verifier; the pure `decide_fix_or_escalate` function; persist `head_commit`; classification wiring in `main()`.
  - Agent Type: builder
  - Domain: async/subprocess + git-archaeology framing (see DOMAIN_FRAMING.md)
  - Resume: true

- **Builder (dispatch + hand-back + watchdog)**
  - Name: `escalation-builder`
  - Role: rewrite dispatch mandate (bounded-fix); `tools.nightly_fix_handback` CLI; escalation/fail-safe watchdog preamble; per-node `fix_attempts` + `fix_sessions` dedup maps; guardrail constants.
  - Agent Type: builder
  - Domain: Redis/Popoto (ORM-only session reads) + conversational-UX (escalation message content)
  - Resume: true

- **Test engineer**
  - Name: `fix-gate-tester`
  - Role: unit tests for the decision gate + watchdog + hand-back parsing; integration test for the silent-fix and escalate paths with seeded state.
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: `safety-validator`
  - Role: verify the never-weaken-assertions surface — the branch-protection verification + `active`-mode runtime preflight (the ONLY structural never-merge leg), the deterrent layer (`gh pr create --draft` present; `gh pr ready`/`gh pr merge`/`do-merge` absent in the fixer path + dispatch prompt), the **fail-closed** diff-path guard AND the round-3 **merge/draft-state guard** (void+close+page on non-`tests/` diffs, non-draft/merged/closed PR, or guard-command error/empty output), escalate-on-doubt paths, the verifiable notify-vs-page tiering (`send_telegram(silent=True)`), shadow-mode default, fail-safe page, anti-criteria green. Confirm the plan makes NO in-session structural-prevention claim.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `nightly-doc`
  - Role: update the feature docs and index.
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Persist last-green baseline + parameterize baseline-verifier
- **Task ID**: build-baseline-ref
- **Depends On**: none
- **Validates**: `tests/unit/` new decision-gate/classification tests; existing baseline-verifier tests still pass with default `main`
- **Assigned To**: gate-builder
- **Agent Type**: builder
- **Parallel**: true
- **`head_commit` is already persisted on main** (`current["head_commit"] = _get_head_commit()`, L1092; helper at L1344). Do NOT re-add it. This step *consumes* `prev["head_commit"]` as the last-green baseline ref and verifies it is written on every non-fatal path.
- Add a documented `baseline_ref` Input field to `.claude/agents/baseline-verifier.md` with a `${baseline_ref:-main}` shell default replacing the two hardcoded `main` tokens at Step 2 (`BASELINE_COMMIT=$(git rev-parse main)` L54; `git worktree add "$BASELINE_DIR" main --detach` L56) — do not break `/do-test` callers.
- Handle absent `prev["head_commit"]` → route to escalate (no unbaselined fix). Note this is the real first-run/`_fatal()`-refused-to-write-state case, not a hypothetical.

### 2. Decision gate + classification wiring
- **Task ID**: build-decision-gate
- **Depends On**: build-baseline-ref
- **Validates**: `tests/unit/test_nightly_decision_gate.py` (create)
- **Assigned To**: gate-builder
- **Agent Type**: builder
- **Parallel**: false
- Pure `decide_fix_or_escalate(classification, new_failures, caps)` returning `autonomous-fix | escalate`.
- Wire classification (adapted baseline-verifier) into `main()`'s `new_failures` branch; remove the up-front unconditional page, replace with a detection log.

### 3. Narrow-mandate draft-PR dispatch + ORM hand-back
- **Task ID**: build-dispatch-handback
- **Depends On**: none
- **Validates**: `tests/unit/test_nightly_fix_dispatch.py`, `tests/unit/test_nightly_fix_handback.py` (create)
- **Assigned To**: escalation-builder
- **Agent Type**: builder
- **Parallel**: true
- Rewrite the dispatch prompt so the mandate names ONLY: make test-file-only edits → run the targeted tests → open a **DRAFT** PR (`gh pr create --draft`) → write the hand-back → STOP (cite the contract-moving commit). The mandate must **explicitly forbid `gh pr ready`, `gh pr merge`, and push-to-`main`** (deterrent — honest that in-session bash is unrestricted under `bypassPermissions`). It is dispatched as a narrowly-scoped session, NOT an auto-continuing eng session — never "run the SDLC pipeline," never `/do-merge`. Ensure the runner code itself never emits `gh pr ready`/`gh pr merge` (anti-criterion greps count 0). Rename `maybe_dispatch_triage_session` → `maybe_dispatch_fix_session`. **No permission profile / settings seam** — round 1's `config/permission_profiles/nightly_fixer.json` is deleted from the design (no implementation seam exists).
- Add the nullable `AgentSession.nightly_fix_handback` field + a no-op idempotent `MIGRATIONS` entry in `scripts/update/migrations.py`.
- Add `tools/nightly_fix_handback.py` (loads the session, persists via `session.save()`; strict read treats null/absent/malformed as no-hand-back).

### 4. Escalation + fail-safe watchdog + mechanical guard + guardrail constants + dedup
- **Task ID**: build-watchdog
- **Depends On**: build-dispatch-handback
- **Validates**: `tests/unit/test_nightly_escalation_watchdog.py` (create)
- **Assigned To**: escalation-builder
- **Agent Type**: builder
- **Parallel**: false
- Watchdog preamble in `main()`: resolve each prior dispatch by iterating the per-node `fix_sessions: {node_id: agent_session_id}` map (de-duplicating session ids first) and loading each session; read its status + `nightly_fix_handback` (ORM); page on `blocked-escalate`, terminal-fail-with-no-handback, or hang-past-timeout. Identify the fixer PR **only** by the `pr_url` in the hand-back — never enumerate/search PRs; a `fixed-silent` disposition with a null/absent/unparseable `pr_url` → fail-safe page.
- Add the **fail-closed diff-path guard**: on `fixed-silent`, run `gh pr diff --name-only <pr_url>`; keep eligible for notify ONLY on zero-exit + non-empty + all-`tests/` + changed-file count `<= NIGHTLY_FIX_MAX_CHANGED_FILES` (round-4 concern 2 — wire the cap here); on ANY {non-zero exit, error, empty/unparseable output, non-`tests/` path, count over cap} → void.
- Add the **fail-closed merge/draft-state guard (round-3)**: run `gh pr view <pr_url> --json isDraft,state,mergedAt`; keep eligible for notify ONLY when still draft AND `state == "OPEN"` AND `mergedAt` null; on ANY {not-draft, non-OPEN, merged, command error/empty} → void. Detective (next-night), catches a strayed `gh pr ready`/`gh pr merge`.
- On ANY guard voiding the fix → `gh pr close <pr_url>` (if still open) + page; only both-guards-pass downgrades to notify.
- **Verify + document current branch-protection state (round-3, prerequisite)**: run `gh api repos/tomcounsell/ai/branches/main/protection` and `gh repo view`; record the finding in `docs/features/nightly-alert-triage.md`. At plan time this returned `404 Not Found` (main NOT protected). If still absent, the feature doc must state that enabling protection on `main` is a hard prerequisite for `NIGHTLY_FIX_MODE=active`, and that protection must satisfy BOTH `required_pull_request_reviews.required_approving_review_count >= 1` **AND** `enforce_admins.enabled == true` — the `enforce_admins` leg is mandatory because GitHub exempts repo admins (the operator token's scope) from required review otherwise, which would let an admin-token `gh pr merge` land a zero-approval PR.
- **Active-mode branch-protection runtime preflight (round-3, hardened round-4)**: when `NIGHTLY_FIX_MODE == "active"`, before dispatching call `gh api repos/{owner}/{repo}/branches/main/protection` and require BOTH legs of the same boolean short-circuit: `required_pull_request_reviews.required_approving_review_count >= 1` **AND** `enforce_admins.enabled is True` (the round-4 blocker: without `enforce_admins` the operator's admin-scoped token is exempt from required review and could merge a zero-approval PR). On 404/error/missing-review/`enforce_admins` absent-or-false → refuse to dispatch, log the reason, route to escalate-and-page (fail toward paging). This makes the shadow→active prerequisite a code gate.
- Add `silent: bool = False` to the `send_telegram` wrapper (forwarded to `valor-telegram send --silent`); the happy-path notify uses `send_telegram(msg, silent=True)`, pages keep `silent=False`.
- Five named env-overridable guardrail constants (module-scope `os.environ.get`, provisional comments), incl. `NIGHTLY_FIX_MODE` (off/shadow/active, default shadow); gate the dispatch on mode. Add two **per-node-keyed** state maps alongside the live `dispatched_nodes` model (`dispatched_hash` no longer exists — #2559 retired it): **`fix_attempts: {node_id: count}`** (the sole canonical attempt-tracking name — no scalar `fix_attempt_count`) AND **`fix_sessions: {node_id: agent_session_id}`** (round-4 concern 1 — the watchdog resolves the fixer's `AgentSession` per node, so a second dispatch can't orphan the first pointer; keep it separate from the pre-existing scalar `dispatched_session_id` at L1090, which the triage path still owns). Increment `fix_attempts[node]` per dispatch attempt and record the session id in `fix_sessions[node]`; **prune from BOTH maps any node no longer in `confirmed_failing`, reusing `carry_dispatched_nodes`'s existing keep-while-still-failing rule** so all three maps prune in lockstep and none grows unbounded; at `NIGHTLY_FIX_MAX_ATTEMPTS` escalate (page) instead of re-dispatching.
- **Gate the fixer off the four run-shape disqualifiers** that landed after this plan was first written: `is_seed_run` (L1027), non-empty `integrity_warnings` (L1047), `args.dry_run`, and a dispatch set truncated by `MAX_DISPATCH_NODES` (L185). Each routes to escalate, never to autonomy.

### 5. Tests (unit + integration)
- **Task ID**: build-tests
- **Depends On**: build-decision-gate, build-watchdog
- **Assigned To**: fix-gate-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Unit: decision gate (all branches, **including the four run-shape disqualifiers: seed/re-baseline, integrity-warned, `--dry-run`, `MAX_DISPATCH_NODES`-truncated → escalate, no dispatch**), watchdog (all page paths + notify path), hand-back parsing (valid/null/malformed, incl. null `pr_url` → fail-safe page), **fail-closed diff-path guard** (all-tests diff within `NIGHTLY_FIX_MAX_CHANGED_FILES` → eligible; non-tests diff, non-zero exit, raised exception, empty output, or count-over-cap → page+close), **fail-closed merge/draft-state guard** (still-draft+OPEN+unmerged → eligible; not-draft, MERGED/non-null mergedAt, CLOSED, error/empty → page+close), **active-mode branch-protection preflight** (404/error/no-review-requirement/`enforce_admins`-false-or-absent → refuse+escalate+page; only both-review-count≥1-AND-`enforce_admins.enabled==true` → dispatch), notify signature (`send_telegram(silent=True)` on happy path vs `silent=False` on pages), mode gating (off/shadow/active), dedup keyed-map reset semantics (changed set → fresh key; pruned when green; cap-reached → escalate), empty-input and missing-baseline paths.
- Integration: seeded `data/nightly_tests_last_run.json` → active-mode (with branch-protection preflight stubbed present) silent-fix path (no page, `silent=True` notify, dispatch with a narrow mandate that includes `gh pr create --draft`, forbids `gh pr ready`/`gh pr merge`/push-to-`main`, and excludes `/do-merge`) and escalate path (page, no dispatch); active-mode-without-protection (refuse dispatch + page); shadow-mode (page + no dispatch).

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: build-tests
- **Assigned To**: nightly-doc
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/nightly-alert-triage.md` + cross-links + index.

### 7. Final validation
- **Task ID**: validate-all
- **Depends On**: build-tests, document-feature
- **Assigned To**: safety-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all Verification commands; confirm the never-weaken-assertions anti-criteria are green; confirm all success criteria.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/ -x -q -k nightly` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Draft-PR deterrent flag present (anti-criterion — deterrent, not structural) | `grep -c "gh pr create --draft" scripts/nightly_regression_tests.py` | output > 0 |
| Fixer mandate excludes `/do-merge`, dispatched narrow (anti-criterion — integration test, NOT a grep of a comment) | `pytest tests/unit/test_nightly_fix_dispatch.py -q` (asserts the built dispatch message contains `gh pr create --draft` and does NOT contain `do-merge`/"run the pipeline") | exit code 0 |
| No auto-merge anywhere in the fixer path (anti-criterion) | `grep -c "do-merge\|do_merge" scripts/nightly_regression_tests.py` | match count == 0 |
| Fixer path never emits `gh pr ready`/`gh pr merge` (deterrent anti-criterion — code AND dispatch prompt) | `grep -Ec "gh pr ready\|gh pr merge" scripts/nightly_regression_tests.py` | match count == 0 |
| Dispatch mandate forbids ready/merge/push-to-main (anti-criterion — integration test) | `pytest tests/unit/test_nightly_fix_dispatch.py -q` (asserts the built dispatch message contains `gh pr create --draft`, explicitly forbids `gh pr ready`/`gh pr merge`/push-to-`main`, and excludes `do-merge`/"run the pipeline") | exit code 0 |
| No resurrected permission-profile fiction (anti-criterion) | `test ! -e config/permission_profiles/nightly_fixer.json && echo ok` | prints `ok` |
| Fail-closed test-file-only diff guard present (anti-criterion) | `grep -c "pr diff --name-only" scripts/nightly_regression_tests.py` | output > 0 |
| `NIGHTLY_FIX_MAX_CHANGED_FILES` is wired into the diff-path guard, not dead (round-4 concern 2) | `grep -c "NIGHTLY_FIX_MAX_CHANGED_FILES" scripts/nightly_regression_tests.py` | output ≥ 2 (declaration + guard use) |
| Fail-closed merge/draft-state watchdog guard present (round-3 detective anti-criterion) | `grep -c "pr view" scripts/nightly_regression_tests.py` (the `gh pr view <pr> --json isDraft,state,mergedAt` guard) | output > 0 |
| Active-mode branch-protection runtime preflight present (round-3 shadow→active gate) | `grep -c "branches/main/protection" scripts/nightly_regression_tests.py` | output > 0 |
| Preflight requires `enforce_admins` (round-4 blocker — admin tokens are review-exempt without it) | `grep -c "enforce_admins" scripts/nightly_regression_tests.py` | output > 0 |
| No raw-Redis on Popoto keys in new code (anti-criterion) | `grep -Ec "\.hgetall\(\|\.hget\(\|r\.delete\(\|r\.srem\(\|r\.sadd\(" scripts/nightly_regression_tests.py tools/nightly_fix_handback.py` | match count == 0 |
| Up-front page removed from the `new_failures` branch (behavioral anti-criterion) | `pytest tests/unit/test_nightly_decision_gate.py -q -k "shadow or off or new_failures"` — the mode-gating + detection tests assert that on a `new_failures` run in `off`/`active` mode NO up-front page fires from the detection branch (only escalate/fail-safe paths page); `shadow` still pages as today. Behavioral assertion via `send_telegram` call capture, no bespoke AST script. | exit code 0 |
| Run-shape disqualifiers gate the fixer (seed / integrity-warned / dry-run / truncated) | `pytest tests/unit/test_nightly_decision_gate.py -q -k "seed or integrity or dry_run or truncated"` | exit code 0 |
| Pre-existing detector behavior untouched (per-node dedup, seed umbrella, run integrity) | `pytest tests/unit/test_nightly_regression_tests.py -q` | exit code 0 |
| Retired `dispatched_hash` not resurrected (the 2026-09-02 rebase re-keyed dedup per node) | `grep -c "dispatched_hash\|failing_set_hash" scripts/nightly_regression_tests.py` | match count == 0 |
| Detection log sentinel present | `grep -c "nightly-fix.*detection" scripts/nightly_regression_tests.py` | output > 0 |
| Guardrail constants are env-overridable (module-scope) | `grep -c "os.environ.get(\"NIGHTLY_FIX" scripts/nightly_regression_tests.py` | output ≥ 5 |
| Low-urgency notify is a distinct call signature, not a bare string (anti-criterion) | `grep -c "send_telegram(.*silent=True" scripts/nightly_regression_tests.py` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room), round 2 — 2026-07-27. Verdict: NEEDS REVISION (1 blocker). Round-3, round-4, and round-5 rows appended below. Round 5 (2026-09-02, FULL depth, baseline b87fb26de): NEEDS REVISION — 4 blockers, 2 concerns, 2 nits. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness + History & Consistency + Scope & Value (all 3 agreed) | The round-1 "structural never-merge" resolution has no implementation seam. `tools.valor_session create` exposes no `--permission`/`--settings`/profile flag; `AgentSession` has no settings/permission field; and every `claude-cli` spawn is hardcoded to `--permission-mode bypassPermissions` (`agent/session_runner/harness/claude.py` L154-168) while `PermissionRequest` hooks "do not fire under `claude -p`" (`agent/session_runner/hook_edge.py` L188-190). A deny-list has nowhere to attach and, even attached, bypass mode does not evaluate deny rules. | **RESOLVED (round 2)** | Permission-profile mechanism **deleted** from the plan (Technical Approach → "Enforcement design"; `config/permission_profiles/nightly_fixer.json` removed; a new anti-criterion asserts the file does not exist). Replaced with a buildable defense-in-depth substrate needing NO permission subsystem: (1) **narrow mandate** (make-test-edits → run-tests → open-DRAFT-PR → hand-back → STOP; not an auto-continuing eng session); (2) **`gh pr create --draft`** — GitHub structurally refuses to merge a draft until a human marks it ready-for-review (the load-bearing never-auto-merge mechanism); (3) fail-closed watchdog guard; (4) no-auto-merge + shadow-first. No spike needed. |
| CONCERN | Risk & Robustness | The `gh pr diff --name-only` mechanical guard's failure path is unspecified: a PR-closed/auth-expired/network error returning an empty or errored path list could vacuously satisfy "every changed path under `tests/`" and silently notify instead of paging. | **RESOLVED (round 2)** | Guard made **fail-closed** (Technical Approach → watchdog; Solution key element; Step 4): downgrade to notify ONLY on zero-exit + non-empty + all-`tests/`; ANY {non-zero exit, error, empty/unparseable output, non-`tests/` path} → close PR + page. Failure-Path tests added for the non-zero, raised-exception, and empty-output cases. |
| CONCERN | Scope & Value | The "notify vs. page" tiering — the mechanism that keeps the happy path "not fully silent" — is verified only by `grep -c "notify\|low.urgency\|fyi"`, but the sole alert primitive `send_telegram(msg, dry_run=...)` has no urgency parameter, so the check passes on a bare string literal even if the call is functionally identical to a page. | **RESOLVED (round 2)** | Added `silent: bool = False` to the `send_telegram` wrapper (forwarded to `valor-telegram send --silent` / `disable_notification`); happy-path notify uses `send_telegram(msg, silent=True)`, pages keep `silent=False`. Anti-criterion changed to `grep -c "send_telegram(.*silent=True"` (call signature, not a prose substring); tests assert the `silent` kwarg via call capture. |
| CONCERN | History & Consistency | The plan is internally inconsistent about how settled the permission mechanism is: Resolved Decision #2 + Success Criteria treat structural denial as an accomplished, grep-verifiable fact, while Agent Integration hedges the wiring is an undetermined "build-time detail" to verify in an integration test written after the fact. | **RESOLVED (round 2)** | Resolved Decision #2 rewritten to a single honest enforcement decision (draft-PR + defense-in-depth, no permission profile); Agent Integration's "build-time detail" hedge removed (no session-create flag is needed). The `grep -c "nightly_fixer"` anti-criterion replaced with a pytest integration-test assertion (`test_nightly_fix_dispatch.py`) that the dispatch message contains `gh pr create --draft` and excludes `/do-merge`. |
| BLOCKER | Risk & Robustness (round 3) | Round 2's "draft-PR structural no-auto-merge" guarantee is only prompt-strength. The fixer is a `--role eng` `AgentSession` spawned with hardcoded `--permission-mode bypassPermissions` = unrestricted bash (`agent/session_runner/harness/claude.py` L154-168); nothing stops it running `gh pr ready <url>` then `gh pr merge <url>`. The diff-path watchdog inspects only paths, not merge/draft state, and runs on the next nightly (~24h later, after any merge already landed). | **RESOLVED (round 3)** | Honest limitation stated plainly (Technical Approach → Enforcement design): under `bypassPermissions` an in-session bash cannot be mechanically blocked from `gh pr ready`/`gh pr merge`; no in-session structural-prevention claim is made. The real structural backstop moved OUTSIDE the session: **server-side branch protection on `main`** (require non-author review approval) — verified ABSENT at plan time (404), so enabling it is a hard prerequisite for `active` mode, enforced by an `active`-mode runtime preflight (`gh api .../branches/main/protection` → refuse+escalate on 404/error/missing-review). Shadow-first default bounds blast radius. Watchdog extended with a fail-closed **merge/draft-state guard** (`gh pr view --json isDraft,state,mergedAt` → void+close+page on not-draft/merged/closed/error) — honestly labeled DETECTIVE (next-night), not preventive. Mandate + anti-criteria now forbid `gh pr ready`/`gh pr merge`/push-to-`main` (deterrent) and assert `--draft`. |
| CONCERN | Risk & Robustness (round 3) | `fix_attempt_count` dedup reset semantics were unspecified — when does a set's attempt count reset, and does the state grow unbounded? | **RESOLVED (round 3)** | Replaced scalar with a keyed `fix_attempts` map (Technical Approach → Dedup): increment per dispatch attempt; **prune keys no longer red** (bounded, and a later regression starts fresh); at `NIGHTLY_FIX_MAX_ATTEMPTS` escalate instead of re-dispatch. *(Re-keyed from failing-set hash to node ID in the 2026-09-02 freshness rebase — the hash producer no longer exists; the reset semantics carried over unchanged.)* |
| CONCERN | Scope & Value (round 3) | The bespoke AST verify script `scripts/_verify_no_page_in_new_failures_branch.py` is redundant with the mode-gating behavioral tests. | **RESOLVED (round 3)** | Removed the AST script + its Verification row; the up-front-page-removed anti-criterion is now the behavioral mode-gating test (`send_telegram` call capture on the `new_failures` run). |
| CONCERN | History & Consistency (round 3) | The shadow→active flip prerequisite (branch protection) was prose-only and unenforceable. | **RESOLVED (round 3)** | Added an in-code `active`-mode runtime preflight that verifies review-required branch protection before any dispatch and fails toward paging when absent — the prerequisite is now a code gate, not a hope. Tested. |
| CONCERN | Risk & Robustness (round 3) | The watchdog was ambiguous about which PR it inspects (new fixer PR vs a pre-existing PR). | **RESOLVED (round 3)** | Watchdog identifies the fixer PR **only** by the `pr_url` in the hand-back; never enumerates/searches PRs; a `fixed-silent` with null/absent/unparseable `pr_url` → fail-safe page (Technical Approach → new-vs-preexisting note). |
| NIT | History & Consistency (round 3) | `.env.example` completeness check requires a comment line above each `KEY=`; the five new constants would fail it as bare keys. | **RESOLVED (round 3)** | Update System now specifies each of the five keys ships as a two-line block (comment describing knob + default, then commented placeholder), with `NIGHTLY_FIX_MODE`'s comment noting `active` requires branch protection. |
| BLOCKER | Risk & Robustness (round 4) | The branch-protection preflight/verify gated only on `required_pull_request_reviews.required_approving_review_count >= 1`. GitHub exempts repo admins from required-review enforcement unless `enforce_admins.enabled == true` (default off), and the fixer's `gh` runs under the operator's admin-scoped token — so `gh pr merge` could land a zero-approval PR, defeating the sole structural never-merge leg. | **RESOLVED (round 4)** | Added `enforce_admins.enabled is True` to the SAME boolean short-circuit in all three sites: the `active`-mode runtime preflight (Technical Approach + Step 4), the manual builder verify step (Step 4), and the Success Criteria row. `active` mode now requires **BOTH** `required_approving_review_count >= 1` AND `enforce_admins.enabled == true`; the preflight fails toward paging otherwise. Prerequisites table, failure-path test (new `enforce_admins`-false case), unit-test list, validator scope, and a new `grep -c "enforce_admins"` Verification row updated to match. |
| CONCERN | Risk & Robustness (round 4) | `dispatched_session_id` was a scalar but `fix_attempts` became a keyed map — a second dispatch (different failing set) would orphan the first session pointer, leaving the watchdog unable to resolve the earlier fixer's `AgentSession`. | **RESOLVED (round 4)** | Replaced the scalar with a keyed `fix_sessions: {node_id: agent_session_id}` map (Interface changes, Dedup section, watchdog, Step 4), sharing the `fix_attempts` key and pruned together when a node goes green. The watchdog iterates it to load exactly the record each still-red node was dispatched to. *(Named `dispatched_sessions` and hash-keyed in round 4; renamed + re-keyed in the 2026-09-02 rebase to avoid colliding with the live scalar `dispatched_session_id`.)* |
| CONCERN | Scope & Value (round 4) | `NIGHTLY_FIX_MAX_CHANGED_FILES` was declared but never wired into any guard (a dead constant). | **RESOLVED (round 4)** | **Decision: WIRE it** into the fail-closed diff-path guard — a `fixed-silent` PR whose changed-file count exceeds `NIGHTLY_FIX_MAX_CHANGED_FILES` (even if all-`tests/`) is voided → close + page. Updated the guard (Technical Approach, Solution key element, flow, Step 4), the failure-path + unit tests (new count-over-cap case), a Success Criteria row, and a `grep -c` Verification row asserting ≥2 uses (declaration + guard). |
| CONCERN | History & Consistency (round 4) | `fix_attempt_count` (scalar, increment-before-run) vs `fix_attempts` (keyed map) was a naming/semantics contradiction introduced in round 3. | **RESOLVED (round 4)** | Reconciled to ONE name + ONE semantics: **`fix_attempts` (the keyed map) is the sole canonical name; the scalar `fix_attempt_count` is fully retired.** The gate reads `all(fix_attempts.get(n, 0) < NIGHTLY_FIX_MAX_ATTEMPTS for n in new_failures)`; per-node count is `fix_attempts[node_id]`, incremented once per dispatch attempt. All references (Interface changes, gate, Risk 4, team role, failure-path test, Dedup section) updated. |
| BLOCKER | Risk \& Robustness (round 5) | The notify-vs-page tier rests on forwarding `--silent` to `valor-telegram send`, but that flag does not exist — `valor-telegram send --help` lists no `--silent` and `disable_notification` has zero hits in `tools/valor_telegram.py` or `bridge/`. Step 4 tasks only the `send_telegram` wrapper, never the `send` subparser (L1367-1440) or the Telethon `send_message` path (`bridge/telegram_relay.py` L468/L488). | pending | `send_telegram` (`scripts/nightly_regression_tests.py` L609-637) runs `subprocess.run(..., capture_output=True)` and never checks `.returncode`, logging `"Telegram sent"` unconditionally at L633 — an unrecognized `--silent` makes argparse exit 2, so the happy-path notify is **silently dropped** while the log claims success, and the `grep -c "send_telegram(.*silent=True"` anti-criterion still passes. Add `send_parser.add_argument("--silent", action="store_true")` near L1373, forward `disable_notification=True` at `bridge/telegram_relay.py` L468, and guard: `proc = subprocess.run(...); if proc.returncode != 0: log(f"WARNING: telegram send failed rc={proc.returncode}")`. |
| BLOCKER | Risk \& Robustness (round 5) | Data Flow step 2 assumes `scripts/nightly_regression_tests.py` can invoke `baseline-verifier`, but that subagent is reachable only via the Claude Task tool — sole caller `.claude/skills-global/do-test/baseline-verification.md` L81 (`subagent_type: "baseline-verifier"`). The nightly runner is a plain launchd Python script with no Task tool; no `.py` caller exists anywhere. The classification stage gating every other decision has no invocation seam. | pending | Reuse the existing session seam (`python -m tools.valor_session create`, invoked at L913) to spawn a classifier session, but note this makes classification **asynchronous** — the runner cannot block on a session result inside one nightly invocation, turning Data Flow steps 2-4 into a two-night handshake. Also `${baseline_ref:-main}` cannot work: a Task-tool dispatch passes a prose prompt, not env vars, so the shell default in `.claude/agents/baseline-verifier.md` Step 2 always resolves to literal `main`, silently reinstating the masking the plan calls out. Interpolate the SHA as a literal (`BASELINE_COMMIT=<sha>`) into the prompt text, not as a shell parameter expansion. |
| BLOCKER | History \& Consistency (round 5) | `NIGHTLY_FIX_MODE=off` carries two incompatible semantics. Reversibility, Mode Gating, and Success Criteria all say `off` reproduces the exact current detect-and-page behavior; Test Impact and the Verification "up-front page removed" row say the `new_failures` branch "no longer pages up front in `off`/`active` mode". Current behavior *does* page up front (L1197-1207), so the two readings are mutually exclusive — on the designated break-glass rollback. | pending | Load-bearing because `off` is the emergency revert: under the Test Impact reading, setting `off` after an incident leaves a red suite with **no alert at all** (no detection page, no watchdog since nothing was dispatched) — strictly worse than the status quo it exists to restore. Pin one branch: `if NIGHTLY_FIX_MODE in ("off", "shadow"): send_telegram(msg, dry_run=args.dry_run)` on the `elif new_failures:` arm; change the Verification selector from `-k "shadow or off or new_failures"` to assert `send_telegram` **is** called for `off` and `shadow` and **not** for `active`. |
| BLOCKER | History \& Consistency (round 5) | The `enforce_admins`-plus-required-review branch protection prerequisite is repo-wide, and its collateral is unexamined: it blocks the direct-to-`main` pushes this repo depends on (CLAUDE.md "Plans and md docs commit directly on main"; `.githooks/pre-push`; this critique skill's own finalize commit on `main`), and since the fixer's `gh` runs under the operator's token the operator is the PR author and cannot approve it — making the fix PR unmergeable and breaking `/do-merge` for every PR in the repo. | pending | Verified 2026-09-02: `gh api repos/tomcounsell/ai/branches/main/protection` → 404, so the consequences are untested here. `required_approving_review_count >= 1` + `enforce_admins.enabled = true` makes `git push origin main` fail for `docs/plans/` commits and `gh pr merge` fail with "reviews required" for every autonomous merge. The existing `SDLC_AGENT_GH_TOKEN` bot identity is the only candidate second approver — name it explicitly, or state that `active` mode is not adoptable on this repo and the prerequisite is unsatisfiable as written. |
| CONCERN | Risk \& Robustness (round 5) | The watchdog enumerates three fail-safe states (terminal failed/killed/abandoned, non-terminal-past-timeout, null/malformed hand-back) but never the case where the `AgentSession` record is **absent** when loaded by id. `AgentSession` carries `Meta.ttl` (30 days, `models/agent_session.py` L651) and rows are swept by `agent/session_health.py`, so a `fix_sessions[node]` pointer can resolve to nothing — falling through every page branch, leaving the suite red and nobody paged. | pending | The load is `AgentSession.query.filter(...)` returning an empty list, not `None`. Guard: `sessions = AgentSession.query.filter(id=sid); if not sessions: page("fixer session record missing — cannot verify outcome"); continue` — placed **before** the status/`updated_at` inspection so a missing record cannot be misread as a fresh non-terminal session. Prune the node's `fix_attempts`/`fix_sessions` entries after paging so the next run does not re-page a dead pointer. |
| CONCERN | Scope \& Value (round 5) | The plan's own motivating case cannot pass its own gate. #2399 had **11** newly-confirmed failures, but the gate hard-disqualifies any run truncated by `MAX_DISPATCH_NODES = 10` (L185). Eleven all-new failures → dispatch set 11 > 10 → truncated → escalate, never autonomy. The single scenario justifying the feature would still have paged a human. | pending | The truncation disqualifier and `NIGHTLY_FIX_MAX_FAILURES=15` are incompatible: the effective ceiling is `min(15, 10)` = 10, so every value 11..15 is dead configuration. Either set the `NIGHTLY_FIX_MAX_FAILURES` default to 10 and delete the truncation clause as redundant, or make the gate consume `new_failures` truncation state independently of `run_flags.dispatch_truncated` — `compute_dispatch_set` (L728) answers "what has never been filed", a different question from `compute_new_failures` (L654), a distinction the plan makes elsewhere but does not carry into the cap arithmetic. |
| NIT | Scope \& Value (round 5) | The shadow→active flip depends on reviewing shadow logs, but no task, success criterion, or verification row makes the shadow verdict discoverable — the only log anti-criterion greps for `nightly-fix.*detection`, not the gate verdict. | pending | Give the shadow verdict a stable greppable prefix (`nightly-fix shadow-verdict: autonomous-fix\|escalate reason=...`) and add one Verification row. |
| NIT | History \& Consistency (round 5) | Freshness Check records baseline `3b6eb651b`; `origin/main` is now `b87fb26de`, six commits ahead. None touch `scripts/nightly_regression_tests.py` or `.claude/agents/baseline-verifier.md` and every line reference still resolves, so the drift is cosmetic. | pending | Bump the recorded baseline SHA on the next revision pass. |

---

## Resolved Decisions (critique round 1)

These were the plan's original Open Questions; the critique (1 BLOCKER + 6 CONCERNs) resolved them. Recorded here so the next critique round sees the decisions rather than re-litigating them.

1. **baseline-verifier semantic adaptation — RESOLVED (owner-confirmed in the design-constraints comment).** Known-good ref maps to the *last-green nightly SHA* (`prev["head_commit"]`), not `main`, because nightly runs on main and the failure IS on main — diffing against `main` would mask a regression that already landed there. Implemented via a documented `baseline_ref` Input field with a `${baseline_ref:-main}` default (Technical Approach), preserving every `/do-test` caller. No nightly-specific classifier.
2. **Never-auto-merge enforcement — RESOLVED (BLOCKER, round 3; round 2's answer was corrected).** The round-1 "structural permission profile" had no implementation seam (no session-create permission flag, no `AgentSession` permission field, spawns hardcoded `bypassPermissions`, `PermissionRequest` hooks do not fire under `claude -p`) and is **deleted**. Round 2 then wrongly called `gh pr create --draft` "the load-bearing structural guarantee" — but under `bypassPermissions` the fixer's unrestricted bash can issue `gh pr ready` then `gh pr merge`, so the draft flag is a deterrent, not a structural block. **Honest round-3 decision:** the ONLY structural never-merge guarantee is **server-side branch protection on `main`** (external to the session, GitHub-enforced, cannot be overridden by in-session bash) — currently ABSENT (verified 404), so enabling review-required protection is a hard prerequisite for `active` mode, enforced by an `active`-mode runtime preflight. Everything in-session (narrow mandate that forbids `gh pr ready`/`gh pr merge`, `gh pr create --draft`) is a **deterrent**; the fail-closed diff-path guard and the new fail-closed **merge/draft-state guard** are **detective** (next-night). No in-session structural-prevention claim is made. See Technical Approach → "Enforcement design."
3. **Guardrail constants home — RESOLVED (CONCERN).** Five knobs as module-scope `os.environ.get` reads with provisional comments, deliberately NOT promoted to `config/settings.py` (single-consumer script; rationale in Technical Approach). Provisional defaults: `NIGHTLY_FIX_MAX_FAILURES=15`, `NIGHTLY_FIX_MAX_CHANGED_FILES=10`, `NIGHTLY_FIX_MAX_ATTEMPTS=1`, `NIGHTLY_FIX_HANDBACK_TIMEOUT_HOURS=6`, `NIGHTLY_FIX_MODE=shadow`.
4. **Shadow-first rollout — RESOLVED (CONCERN).** `NIGHTLY_FIX_MODE` ships defaulting to `shadow` (classify + log the gate verdict, page-as-today, dispatch nothing); flip to `active` after observing shadow logs. `off` restores exact current behavior.
5. **Happy-path notification — RESOLVED (CONCERN; sharpened round 2).** The `fixed-silent` path emits a low-urgency notify (not a page) so a human knows a fix draft PR is waiting; only genuine blockers page. Made verifiable: `send_telegram` gains `silent: bool = False` (→ `valor-telegram send --silent`); the notify calls `send_telegram(msg, silent=True)`, pages keep the default. Anti-criterion asserts the call signature, not a prose substring.
6. **Test-file-only mechanical guard — RESOLVED (CONCERN; made fail-closed round 2).** The watchdog runs `gh pr diff --name-only` OUTSIDE the fixer session and is **fail-closed**: it downgrades to a notify only on zero-exit + non-empty + all-`tests/`; ANY {non-zero exit, error, empty/unparseable output, non-`tests/` path} voids the fix (closes the PR) and pages. Independent of the prompt; never fails open.
7. **Hand-back storage — RESOLVED (CONCERN).** Persisted on the `AgentSession` record via the ORM (nullable `nightly_fix_handback` field) rather than a duplicate file store, eliminating Race 1's torn-read hazard.

No open questions remain — ready for critique (round 3 findings absorbed: honest `bypassPermissions` limitation, branch-protection structural backstop + builder-verify + `active`-mode preflight, shadow-first gating, merge/draft-state watchdog guard, mandate/anti-criteria deterrent, dedup reset semantics, AST script removed, env-completeness; **round 4 findings absorbed: `enforce_admins.enabled == true` added to the preflight/verify/success-criteria boolean short-circuit so an admin-scoped token can't merge a zero-approval PR; keyed `dispatched_sessions` map so a second dispatch can't orphan the first session pointer; `NIGHTLY_FIX_MAX_CHANGED_FILES` wired into the diff-path guard; `fix_attempts` reconciled to the sole canonical name/semantics, scalar `fix_attempt_count` retired**).

**2026-09-02 freshness rebase (baseline `3b6eb651b`).** The premise is unchanged and re-verified — the up-front page and the investigate-only mandate are both still live — but the detector gained ~660 lines across nine commits, so the plan was rebased onto the current substrate: dedup re-keyed from the retired `dispatched_hash` to per-node `fix_attempts` / `fix_sessions` matching #2559's live model; `head_commit` recognized as already persisted (Step 1 degraded to consume-and-verify); the decision gate given four hard run-shape disqualifiers (seed/re-baseline, integrity-warned, `--dry-run`, `MAX_DISPATCH_NODES`-truncated) from the #2823 collection-aware baseline; all file:line references re-resolved; and a non-regression criterion added so the existing per-node dedup, seed-umbrella, and run-integrity behavior stays byte-identical. Branch protection on `main` re-checked and **still absent (404)**, so the `active`-mode preflight remains load-bearing exactly as designed.
