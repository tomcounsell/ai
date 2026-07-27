---
status: Ready
type: feature
appetite: Large
owner: Valor Engels
created: 2026-07-27
tracking: https://github.com/tomcounsell/ai/issues/2334
last_comment_id: 5086978978
revision_applied: true
revision_applied_at: 2026-07-27T04:32:27Z
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

**Baseline commit:** `66a433bd9` (`git rev-parse HEAD` at plan time)
**Issue filed at:** 2026-07-24T06:45:58Z
**Disposition:** Minor drift

**File:line references re-verified (against current `scripts/nightly_regression_tests.py`):**
- `main()` up-front page on the `elif new_failures:` branch (`send_telegram(...)`, ~L645-666) — **still holds**; this is the "human is first responder" defect.
- `maybe_dispatch_triage_session()` (L437-509), prompt "Do NOT attempt an auto-hotfix" (L473-474), `dispatched_hash` sha256 dedup persisted in `data/nightly_tests_last_run.json` (L463-466, carried forward L628-629) — **still holds**.
- `reconfirm_serial()` serial re-confirmation gate (L212-256), `confirmed_failing` authoritative set — **still holds**.
- `baseline-verifier` subagent (`.claude/agents/baseline-verifier.md`) — confirmed it exists and returns `{regressions, pre_existing, inconclusive}` JSON. **Confirmed drift worth noting:** it hardcodes `main` as the baseline ref (Step 2: `BASELINE_COMMIT=$(git rev-parse main)`; `git worktree add "$BASELINE_DIR" main`). Reuse in the nightly context requires pointing that ref at the last-known-good commit instead — see Technical Approach.

**Cited sibling issues/PRs re-checked:**
- #2192 / PR #2195 — **merged 2026-07-24**; established the triage-dispatch flow this issue reframes. Prerequisite satisfied.
- #2399 / PR #2402 — **merged 2026-07-26**; the worked example (11 test-stale failures). Read in full.
- #2209 — **merged**; sentry-issue-triage migrated to Claude Cowork + reusable Cowork skill. Informs the in-process-vs-Cowork decision (open question 5).
- #2327 — **merged**; `load_env_or_die()` replaced the TCC-blocked bash `source .env`. Already integrated in the current file.

**Commits on main since issue was filed (touching `scripts/nightly_regression_tests.py`):**
- `fb1ad8c57` env-loading in the Python entrypoint (#2327) — integrated, read directly. Irrelevant to the reframe except that new dispatch code inherits the loaded env.
- `1b0a2bced` merge gate simplification (#2378) — irrelevant.
- `fcfbeed0b` triage dispatch (#2195) — this is the flow being reframed.

**Active plans in `docs/plans/` overlapping this area:** `nightly-serial-reconfirm.md` (the #2180 serial-gate base — a dependency this plan builds on, not a conflict). No conflicting active plan.

**Notes:** The prerequisite (#2192/#2195) is merged, so this plan builds on a live flow rather than a hypothetical one.

## Prior Art

- **#2192 / PR #2195** — added `maybe_dispatch_triage_session` (investigate-and-file-an-issue) + the run lock + LLM summarizer. **Deliberately deferred auto-hotfix as a No-Go** (unreviewed merge to main was the concern). This plan does NOT reverse that No-Go — it introduces a *narrower, review-gated* bounded fix (propose-a-PR, never merge), which preserves the original concern (a human still reviews before anything lands on main).
- **#2399 / PR #2402** — the worked example. 11 test-stale failures across 3 groups. Two groups were mechanical carry-forward; **Group 2 hid a genuine design fork** (self-heal intended vs. a safety hole) that required a human reading #2144's intent to resolve. **Lesson baked into scoping:** "classifies as test-stale" is *necessary but not sufficient* for autonomy — a test-stale carry-forward that turns on a contract-direction judgment must still escalate.
- **#410** — Autoexperiment (autonomous prompt-optimization loop). Prior art for a bounded autonomous loop with caps; informs the guardrail-constant design (max attempts, dedupe).
- **#2405** — follow-up filed by this plan for the Cowork-parity migration (open question 5).

## Data Flow

1. **Entry point**: launchd fires `python scripts/nightly_regression_tests.py`. `load_env_or_die()`, run lock, `run_tests()`, `reconfirm_serial()` → `confirmed_failing` set, `new_failures` = newly-confirmed vs `prev["failing_tests"]`. (Unchanged.)
2. **Classification** *(new)*: for the `new_failures` set, run the adapted baseline-verifier comparing current HEAD against `prev["head_commit"]` (the last-known-good SHA — by definition these tests passed there, since they are *newly* confirmed). Produces `{regressions, pre_existing, inconclusive}` buckets keyed to the nightly context (see Technical Approach for the semantic mapping).
3. **Decision gate** *(new)*: eligible-for-autonomy iff every `new_failure` lands in the "test-stale-candidate" bucket (passed at baseline, no `regressions`, no `inconclusive`) AND count ≤ `NIGHTLY_FIX_MAX_FAILURES`. Otherwise → escalate path.
4. **Autonomous fix path** *(new)*: dispatch a **narrowly-scoped** fixer session — NOT an auto-continuing full-SDLC eng session (an eng session drives itself to `/do-merge`). Its mandate is exactly: make test-file-only edits on a branch, run the targeted tests, open a **DRAFT** PR (`gh pr create --draft`), write the structured hand-back, and STOP. The mandate never names "run the SDLC pipeline" and never names `/do-merge`, and explicitly forbids `gh pr ready`/`gh pr merge`/push-to-`main`. The `--draft` flag is a deterrent/detective signal, NOT a structural in-session block — under hardcoded `bypassPermissions` the session's bash could still self-merge; the load-bearing structural never-merge guarantee is **server-side branch protection on `main`**, external to the session (see Technical Approach → Enforcement design). Bounded: test-file-only edits, cite the source-contract-moving commit. No up-front page.
5. **Structured hand-back** *(new)*: the dispatched session persists its terminal hand-back **on its own `AgentSession` record via the Popoto ORM** (nullable `nightly_fix_handback` JSON field — not a separate file), so the watchdog (which already reads the session via the ORM) has a single, atomic source of truth: `{disposition, pr_url, what_broke, classification, tried, blocked_reason, decision_requested, written_at}`.
6. **Test-file-only mechanical guard** *(new)*: before the watchdog honors a `fixed-silent` disposition, it fetches the PR's changed paths (`gh pr diff --name-only`) and **rejects the fix mechanically if any path is outside `tests/`** — a source-file edit voids `fixed-silent` and converts it to an escalation page. This does not rely on the session honoring the prompt.
7. **Escalation / fail-safe watchdog** *(new)*: a **page** fires (a) immediately when the decision gate routes to escalate (any regression/inconclusive/cap-exceeded), with content from the classification; (b) when a prior dispatch's hand-back reports `blocked-escalate`; (c) when a `fixed-silent` PR fails the test-file-only mechanical guard; or (d) fail-safe — the session is terminal-failed/killed/abandoned, OR hung past `NIGHTLY_FIX_HANDBACK_TIMEOUT_HOURS` with no hand-back. Silence must never swallow a red suite.
8. **Output**: on the happy path, a PR for human review **plus a low-urgency notify** (not a page); otherwise a last-resort **page** carrying the stuck point.

## Architectural Impact

- **New dependencies**: none external. Reuses `baseline-verifier` (parameterized), `tools.valor_session create`, the existing local-JSON state file, and `send_telegram`.
- **Interface changes**: `baseline-verifier` gains a documented `baseline_ref` **Input field** (default `main` via a `${baseline_ref:-main}` shell default in the prompt body, preserving every existing `/do-test` caller). `AgentSession` gains a nullable `nightly_fix_handback` JSON field (the structured hand-back contract). `data/nightly_tests_last_run.json` gains `head_commit` and per-dispatch `fix_attempt_count`.
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
| Review-required branch protection on `main` (**prerequisite for `active` mode only**) | `gh api repos/tomcounsell/ai/branches/main/protection` | The ONLY structural never-merge backstop. **Verified ABSENT at plan time (404).** Must be enabled before `NIGHTLY_FIX_MODE=active`; the runner's `active`-mode preflight fails toward paging until it is present. `shadow`/`off` do not require it. |

No new secrets or external services. (Branch protection is a GitHub repo setting, not a new service.)

## Solution

### Key Elements

- **Silent detection log**: remove the up-front unconditional `send_telegram` on the `new_failures` branch; replace with a log line at detection. The page becomes escalation-driven only.
- **Shadow-first rollout**: `NIGHTLY_FIX_MODE` ships defaulting to `shadow` — classify, run the decision gate, log the decision, but page-as-today and dispatch nothing. Flip to `active` only after real shadow logs confirm the gate's judgment. De-risks the blast radius of an autonomous test-editor.
- **Test-stale-vs-code-regressed classifier**: reuse `baseline-verifier`, pointed at the last-known-good commit, as the *necessary precondition* for autonomy; the dispatched session performs the final test-stale determination via git archaeology under a test-file-only constraint.
- **Decision gate**: a pure function mapping the classification + caps to one of `autonomous-fix | escalate`.
- **Narrowly-mandated fixer (not a pipeline)**: the fixer is dispatched as a narrowly-scoped session, NOT an auto-continuing full-SDLC eng session. Its mandate is exactly: make test-file-only edits on a branch, run the targeted tests, open a **DRAFT** PR, write the structured hand-back, STOP. The mandate never names "run the pipeline" and never names `/do-merge`.
- **Branch protection on `main` = the real structural never-merge guarantee (external to the session)**: the ONLY mechanism that mechanically prevents self-merge under hardcoded `bypassPermissions`. Currently ABSENT (verified 404 at plan time); enabling review-required protection is a hard, code-enforced prerequisite for `active` mode. The `gh pr create --draft` flag and the forbid-`gh pr ready`/`gh pr merge` mandate are **deterrents**, not structural blocks — an unrestricted in-session bash could flip a draft ready and merge. This replaces round 2's incorrect claim that the draft flag was itself load-bearing.
- **Fail-closed test-file-only watchdog guard**: the watchdog runs `gh pr diff --name-only` **outside** the fixer session; on ANY error, non-zero exit, empty/unparseable output, OR any path outside `tests/`, it **VOIDS the PR (closes it) and PAGES a human**. Only a successful command whose every path is under `tests/` downgrades to the low-urgency notify. It never fails open.
- **Structured hand-back protocol**: persisted on the `AgentSession` record via the ORM (nullable `nightly_fix_handback` field), consumed by the escalation watchdog.
- **Escalation + fail-safe watchdog**: pages on `blocked-escalate`, on the escalate decision, on a test-file-only guard failure/error, or when the session dies/hangs past a bounded timeout with no hand-back. On the happy path it emits a **low-urgency notify** (not a page).
- **Guardrail constants**: five named, env-overridable caps read via **raw `os.environ.get` at nightly-script module scope** (max failures, max changed files, max attempts, hand-back timeout, and the `NIGHTLY_FIX_MODE` gate), each with a provisional/tunable comment. Deliberately NOT promoted to `config/settings.py` — see Technical Approach for the rationale.

### Flow

Nightly run detects newly-confirmed failures → classify (baseline-verifier vs last-green SHA) →
**decision gate** (in `shadow` mode: log the decision below, then page-as-today and dispatch nothing; in `active` mode, execute it):
- *all test-stale-candidate & within caps* → dispatch narrowly-mandated fixer session (silent) → session edits tests, opens a **DRAFT** PR, persists `fixed-silent` hand-back on its `AgentSession` record → watchdog runs the **fail-closed** test-file-only guard on the PR diff → **guard passes (command succeeded, every path under `tests/`): low-urgency notify, draft PR waits for a human to mark ready + review; guard fails (error / non-zero / empty / non-`tests/` path): void the PR + page**.
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

**Runtime `active`-mode branch-protection preflight (enforces the shadow→active flip, round-3 concern).** Because "flip `NIGHTLY_FIX_MODE=active` only after branch protection is enabled" is unenforceable as prose, the runner enforces it: when `NIGHTLY_FIX_MODE == "active"`, before dispatching any fixer the runner calls `gh api repos/{owner}/{repo}/branches/main/protection` and confirms review-required protection is present (a `required_pull_request_reviews` block with `required_approving_review_count >= 1`). On 404 / error / missing-review-requirement, the runner **refuses to dispatch, logs the reason, and falls back to the escalate-and-page path** (fail toward paging) — `active` mode cannot dispatch an autonomous fixer into an unprotected `main`. This makes the prerequisite a code gate, not a hope.

**Decision gate** (`decide_fix_or_escalate(classification, new_failures, caps) -> "autonomous-fix" | "escalate"`): pure, unit-testable. Autonomy iff `classification.regressions == [] and classification.inconclusive == [] and set(new_failures) ⊆ set(test_stale_candidates) and len(new_failures) <= NIGHTLY_FIX_MAX_FAILURES and fix_attempt_count < NIGHTLY_FIX_MAX_ATTEMPTS`. baseline-verifier returns discrete buckets (no numeric score), so "high confidence" is expressed as bucket membership, not a threshold float. In `shadow` mode the gate's verdict is computed and logged but not acted on.

**Structured hand-back — persisted on the `AgentSession` record via the ORM (reconsidered per critique).** The fixer is *already* a normal `AgentSession` the worker owns, and the watchdog *already* reads that record via `AgentSession.query`. So rather than duplicate a parallel file-based store (with its own atomic-write dance and read/write race), the hand-back lives on the session itself: add a nullable `nightly_fix_handback` JSON field to `AgentSession`. The fixer writes it at terminal through a thin, documented helper CLI (`python -m tools.nightly_fix_handback write ...`) that loads the session by id and calls `session.save()` — **ORM only, never raw Redis**. Schema: `{disposition: "fixed-silent"|"blocked-escalate"|"still-working", pr_url, what_broke, classification, tried, blocked_reason, decision_requested, written_at}`. This is an additive nullable field: per `_heal_descriptor_pollution` (issues #1099/#1172) existing records read it as `None` with no backcompat code; it is still registered as a no-op idempotent entry in `scripts/update/migrations.py` per the repo's Popoto-schema-change convention.

**Escalation watchdog + fail-safe.** At the start of each nightly run, before running tests, inspect the *prior* dispatch (if any): read its session status **and its `nightly_fix_handback` field** via `AgentSession.query` (ORM — never raw Redis).
- `blocked-escalate` → **page** with the hand-back's stuck-point content; clear the pending-dispatch marker.
- session terminal `failed`/`killed`/`abandoned` with no `fixed-silent` hand-back → fail-safe **page**.
- session non-terminal but older than `NIGHTLY_FIX_HANDBACK_TIMEOUT_HOURS` → fail-safe **page** ("fix attempt hung, suite still red").
- `fixed-silent` with a `pr_url` → run **two fail-closed mechanical guards**, both against the exact `pr_url` persisted in the hand-back (never a PR discovered by branch/search — see the new-vs-preexisting note below):
  - **Diff-path guard**: `gh pr diff --name-only <pr_url>`. Downgrades to a low-urgency notify **only** when the command exits zero AND returns non-empty, parseable output AND every changed path is under `tests/`. On ANY of {non-zero exit, error/exception, empty or unparseable output, any path outside `tests/`} → treat the fix as void.
  - **Merge/draft-state guard (round-3, detective)**: `gh pr view <pr_url> --json isDraft,state,mergedAt`. The fix stays eligible for notify **only** when the PR is still a draft AND `state == "OPEN"` AND `mergedAt` is null. On ANY of {`isDraft == false`, `state != "OPEN"`, `mergedAt` non-null, command error/empty} → treat the fix as void. This catches the honest-limitation case where an in-session bash issued `gh pr ready`/`gh pr merge` despite the mandate — it is **detective (next-night), not preventive**; branch protection (#1) is what actually prevents the merge.
  - On ANY guard voiding the fix → **close the PR (`gh pr close <pr_url>`, if still open) and page** ("autonomous fix could not be verified test-file-only-and-still-draft — review required"). Neither guard ever fails open. Both run in the nightly runner's watchdog preamble and do not trust the session's self-report.

**New-vs-preexisting-PR resolution (round-3 concern).** The watchdog identifies the fixer's PR by exactly one means: the `pr_url` written into the hand-back on the fixer's own `AgentSession` record. It never enumerates open PRs, never matches by head branch, and never inspects any PR the fixer did not create. A hand-back with a `fixed-silent` disposition but a null/absent/unparseable `pr_url` is treated as invalid → fail-safe **page** (a claimed silent fix with no PR to guard is a contradiction, never a notify). This removes the earlier ambiguity between "the fixer opened a new draft PR" and "the watchdog inspects a PR" — there is one PR, named by the hand-back, or there is a page.

**Verifiable low-urgency notify (distinct from a page).** `send_telegram(msg, dry_run=...)` currently has no urgency parameter, so a bare-string "notify" would be functionally identical to a page and would satisfy the old prose anti-criterion vacuously. The fix: add a `silent: bool = False` parameter to the `send_telegram` wrapper, forwarded to `valor-telegram send --silent` (Telegram `disable_notification`). The happy-path notify calls `send_telegram(msg, silent=True)`; every page keeps the default `silent=False`. The anti-criterion asserts the call signature — `grep -c "send_telegram(.*silent=True"` ≥ 1 — not a prose substring, so it can only pass if a genuinely distinct low-urgency call exists.

**Worst-case fail-safe latency (critique correction).** The watchdog is a *preamble to the next nightly run*, so `NIGHTLY_FIX_HANDBACK_TIMEOUT_HOURS` does not bound *detection* latency — it only decides whether a still-running dispatch is judged "hung" when the preamble next fires. The true worst-case latency for surfacing a hung/dead fixer is therefore **~one nightly cadence (~24h)**, not the 6h timeout. This is **accepted for the first cut**: (a) `shadow` mode is the default, so no real dispatch happens until we opt in; (b) a hung fixer produces no merge (structurally impossible) and leaves the suite in exactly the red state a human would have seen under today's detect-and-page anyway — the feature cannot make latency *worse* than the status quo, only the happy path faster. If ~24h proves unacceptable after `active` rollout, a dedicated intra-day launchd watchdog is filed as follow-up **#2405**; it is deliberately out of scope here to avoid a second scheduled job before the core gate is proven.

**Dedup extension + reset semantics (round-3 concern).** Extend the existing `dispatched_hash` mechanism to persist a small map `fix_attempts: {failing_set_hash: count}` (not a single scalar), keyed by the sha256 of the *sorted* newly-confirmed failing-test node-id set — so the same failing set is not re-attempted beyond `NIGHTLY_FIX_MAX_ATTEMPTS`, while a *different* set gets its own fresh count. Explicit reset semantics (the round-3 gap):
- **Key = content, so a changed set resets naturally.** If any failing test enters/leaves the set, the hash changes and the new hash starts at count 0 — no stale carry-over.
- **A hash's count increments once per dispatch attempt** (at dispatch time, before the session runs), so a crash mid-attempt still counts (fail toward escalate, never infinite-retry).
- **Success clears the key.** When a failing_set_hash no longer appears in the current run's `new_failures` (the tests went green, i.e. the fix landed or the flake cleared), its entry is **pruned** from `fix_attempts` — the map only holds hashes for currently-red sets, so it cannot grow unbounded and a set that later regresses starts fresh.
- **Cap-exceeded escalates, does not silently drop.** When a hash's count has reached `NIGHTLY_FIX_MAX_ATTEMPTS`, the run escalates (pages) instead of re-dispatching — the set is surfaced to a human rather than retried nightly forever.
This keyed-with-pruning design is what makes "attempt at most N times, then escalate, and reset when the world changes" deterministic rather than an ever-growing scalar.

**In-process, not Cowork, for the first cut** (open question 5): reuse the existing `maybe_dispatch_triage_session` machinery (rename → `maybe_dispatch_fix_session` with the new mandate). Cowork parity is filed as follow-up **#2405**.

**Guardrail constants — raw `os.environ.get` at module scope, NOT `config/settings.py` (critique decision).** All five knobs (`NIGHTLY_FIX_MODE`, `NIGHTLY_FIX_MAX_FAILURES`, `NIGHTLY_FIX_MAX_CHANGED_FILES`, `NIGHTLY_FIX_MAX_ATTEMPTS`, `NIGHTLY_FIX_HANDBACK_TIMEOUT_HOURS`) are declared as module-level constants in `scripts/nightly_regression_tests.py`, each read via `os.environ.get(...)` with an in-code default and a one-line provisional/tunable grain-of-salt comment. This is a deliberate, stated choice, not an oversight: `config/settings.py`'s `TimeoutSettings` is the home for cross-cutting timeout/retry/TTL values consumed across the bridge/worker/agent runtime (per `docs/features/config-timeout-catalog.md`'s promote-vs-name-locally criterion). These five are single-consumer knobs of one standalone launchd script (`nightly_regression_tests.py` imports nothing from the runtime config surface today), so naming them locally keeps them next to their only reader and avoids polluting the global settings namespace. If a second consumer ever appears, promote them then. The anti-criterion greps for `os.environ.get("NIGHTLY_FIX` to assert no bare magic numbers survive in the new paths.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The existing `except Exception` swallow in `maybe_dispatch_*` (dispatch failure → "no dispatch") is preserved; add a test asserting a failed dispatch logs a warning AND leaves `dispatched_hash`/`fix_attempt_count` untouched so a retry is possible.
- [ ] baseline-verifier invocation failure (worktree add fails, timeout) → classified `inconclusive` → **must route to escalate**, not to autonomous-fix. Test the fail-toward-paging path explicitly.
- [ ] `nightly_fix_handback` field absent/null/malformed on the session record when the watchdog reads it → treated as "no hand-back" → fail-safe page (never silently assume success). Test with a `None` field and a non-dict/invalid payload.
- [ ] `fixed-silent` hand-back whose PR diff touches a non-`tests/` path → the mechanical guard voids the fix (closes the PR) → **page** (not a notify). Test by stubbing `gh pr diff --name-only` to return a `src/` path.
- [ ] **Fail-closed diff-path guard**: stub `gh pr diff --name-only` to (a) exit non-zero, (b) raise/throw, and (c) return empty/whitespace output — each case must void the fix → **page**, never a notify. Asserted alongside the non-`tests/` case above.
- [ ] **Fail-closed merge/draft-state guard (round-3)**: stub `gh pr view --json isDraft,state,mergedAt` to return (a) `isDraft=false`, (b) `state="MERGED"`/non-null `mergedAt`, (c) `state="CLOSED"`, and (d) command error/empty — each must void the fix → close (if open) + **page**, never a notify. This is the detective backstop for a strayed in-session `gh pr ready`/`gh pr merge`.
- [ ] **`fixed-silent` hand-back with null/absent/unparseable `pr_url`** → invalid → fail-safe page (a claimed silent fix with no PR to guard is never a notify). Test.
- [ ] **Active-mode branch-protection preflight**: with `NIGHTLY_FIX_MODE=active`, stub `gh api .../branches/main/protection` to return (a) 404, (b) error, (c) a body with no `required_pull_request_reviews` / `required_approving_review_count < 1` — each must refuse dispatch and route to escalate-and-page (`valor_session create` NOT called; `send_telegram` called). With protection present (`required_approving_review_count >= 1`), dispatch proceeds. Test.

### Empty/Invalid Input Handling
- [ ] `new_failures == []` → no classification, no dispatch, no page (clean-run path unchanged). Test.
- [ ] `prev["head_commit"]` absent (first run after deploy, or state migrated from an older schema without the field) → cannot establish a last-green baseline → escalate (do not attempt an unbaselined fix). Test.
- [ ] Empty/whitespace `disposition` in a hand-back → treated as invalid → fail-safe page. Test.

### Error State Rendering
- [ ] Escalation Telegram content is asserted to contain the stuck-point fields (what broke, tried, decision requested) — not a raw node-ID dump — on the `blocked-escalate` path.
- [ ] Fail-safe page fires (asserted via `send_telegram` call capture) when the session is terminal-failed with no hand-back.
- [ ] `fixed-silent` + guard-pass emits a **low-urgency notify** via `send_telegram(msg, silent=True)` — a distinct call signature from the high-urgency page (`silent=False`), asserted via call capture on the `silent` kwarg (not a prose substring) — the happy path is not fully silent.

### Mode Gating
- [ ] `NIGHTLY_FIX_MODE=shadow` (the default): the decision gate runs and logs its verdict, but NO fixer session is dispatched AND the up-front page still fires as today. Asserted: `valor_session create` not called, `send_telegram` called.
- [ ] `NIGHTLY_FIX_MODE=off`: exact current detect-and-page behavior; no classification, no gate. Asserted.
- [ ] `NIGHTLY_FIX_MODE=active`: full dispatch path exercised.

## Test Impact

- [ ] `scripts/nightly_regression_tests.py` tests (search `tests/unit/` for `nightly` / `maybe_dispatch_triage_session` / `dispatched_hash`) — UPDATE: the `new_failures` branch no longer pages up front; assertions that expect an immediate `send_telegram` on new failures must move to the escalate/fail-safe paths. If `maybe_dispatch_triage_session` is renamed, UPDATE its tests to the new name + bounded-fix prompt.
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
**Mitigation:** `fix_attempt_count` extends `dispatched_hash` dedup; past `NIGHTLY_FIX_MAX_ATTEMPTS` the set escalates instead of re-dispatching.

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
- [ ] Branch-protection state on `main` is verified and documented (`gh api repos/tomcounsell/ai/branches/main/protection`); in `active` mode a runtime preflight refuses to dispatch (and pages) unless review-required protection is present (grep-verifiable: `branches/main/protection` present in the runner; test asserts active-mode-without-protection → escalate, no dispatch).
- [ ] The watchdog runs a fail-closed **merge/draft-state guard** (`gh pr view <pr> --json isDraft,state,mergedAt`): a not-draft / merged / closed / errored PR voids the fix → close (if open) + page (asserted, including the merged and command-error cases).
- [ ] The watchdog's **fail-closed** mechanical test-file-only guard voids a `fixed-silent` PR (closes it) and pages on any non-`tests/` path, non-zero exit, error, or empty output (asserted, including the error/empty cases).
- [ ] `blocked-escalate` hand-back → page with stuck-point content; `fixed-silent` + `tests/`-only PR (guard command succeeds) → low-urgency notify via `send_telegram(silent=True)` (no page); session-dead/hung-past-timeout → fail-safe page. All asserted via call-signature capture.
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
  - Role: rewrite dispatch mandate (bounded-fix); `tools.nightly_fix_handback` CLI; escalation/fail-safe watchdog preamble; dedup `fix_attempt_count` extension; guardrail constants.
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
- Persist `head_commit = git rev-parse HEAD` into `data/nightly_tests_last_run.json` each run; use it (NOT bare `main`) as the nightly baseline.
- Add a documented `baseline_ref` Input field to `.claude/agents/baseline-verifier.md` with a `${baseline_ref:-main}` shell default replacing the two hardcoded `main` tokens at Step 2 (~lines 54-56) — do not break `/do-test` callers.
- Handle absent `prev["head_commit"]` → route to escalate (no unbaselined fix).

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
- Watchdog preamble in `main()`: read prior dispatch session status + `nightly_fix_handback` (ORM); page on `blocked-escalate`, terminal-fail-with-no-handback, or hang-past-timeout. Identify the fixer PR **only** by the `pr_url` in the hand-back — never enumerate/search PRs; a `fixed-silent` disposition with a null/absent/unparseable `pr_url` → fail-safe page.
- Add the **fail-closed diff-path guard**: on `fixed-silent`, run `gh pr diff --name-only <pr_url>`; keep eligible for notify ONLY on zero-exit + non-empty + all-`tests/`; on ANY {non-zero exit, error, empty/unparseable output, non-`tests/` path} → void.
- Add the **fail-closed merge/draft-state guard (round-3)**: run `gh pr view <pr_url> --json isDraft,state,mergedAt`; keep eligible for notify ONLY when still draft AND `state == "OPEN"` AND `mergedAt` null; on ANY {not-draft, non-OPEN, merged, command error/empty} → void. Detective (next-night), catches a strayed `gh pr ready`/`gh pr merge`.
- On ANY guard voiding the fix → `gh pr close <pr_url>` (if still open) + page; only both-guards-pass downgrades to notify.
- **Verify + document current branch-protection state (round-3, prerequisite)**: run `gh api repos/tomcounsell/ai/branches/main/protection` and `gh repo view`; record the finding in `docs/features/nightly-alert-triage.md`. At plan time this returned `404 Not Found` (main NOT protected). If still absent, the feature doc must state that enabling review-required protection on `main` is a hard prerequisite for `NIGHTLY_FIX_MODE=active`.
- **Active-mode branch-protection runtime preflight (round-3)**: when `NIGHTLY_FIX_MODE == "active"`, before dispatching call `gh api repos/{owner}/{repo}/branches/main/protection` and require a `required_pull_request_reviews` block with `required_approving_review_count >= 1`; on 404/error/missing → refuse to dispatch, log the reason, route to escalate-and-page (fail toward paging). This makes the shadow→active prerequisite a code gate.
- Add `silent: bool = False` to the `send_telegram` wrapper (forwarded to `valor-telegram send --silent`); the happy-path notify uses `send_telegram(msg, silent=True)`, pages keep `silent=False`.
- Five named env-overridable guardrail constants (module-scope `os.environ.get`, provisional comments), incl. `NIGHTLY_FIX_MODE` (off/shadow/active, default shadow); gate the dispatch on mode. Extend `dispatched_hash` state with a **keyed `fix_attempts: {failing_set_hash: count}` map** (round-3 reset semantics): increment per dispatch attempt; a changed failing set gets a fresh key; **prune keys whose set is no longer red** so the map only holds currently-red sets; at `NIGHTLY_FIX_MAX_ATTEMPTS` escalate (page) instead of re-dispatching.

### 5. Tests (unit + integration)
- **Task ID**: build-tests
- **Depends On**: build-decision-gate, build-watchdog
- **Assigned To**: fix-gate-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Unit: decision gate (all branches), watchdog (all page paths + notify path), hand-back parsing (valid/null/malformed, incl. null `pr_url` → fail-safe page), **fail-closed diff-path guard** (all-tests diff → eligible; non-tests diff, non-zero exit, raised exception, empty output → page+close), **fail-closed merge/draft-state guard** (still-draft+OPEN+unmerged → eligible; not-draft, MERGED/non-null mergedAt, CLOSED, error/empty → page+close), **active-mode branch-protection preflight** (404/error/no-review-requirement → refuse+escalate+page; protection present → dispatch), notify signature (`send_telegram(silent=True)` on happy path vs `silent=False` on pages), mode gating (off/shadow/active), dedup keyed-map reset semantics (changed set → fresh key; pruned when green; cap-reached → escalate), empty-input and missing-baseline paths.
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
| Fail-closed merge/draft-state watchdog guard present (round-3 detective anti-criterion) | `grep -c "pr view" scripts/nightly_regression_tests.py` (the `gh pr view <pr> --json isDraft,state,mergedAt` guard) | output > 0 |
| Active-mode branch-protection runtime preflight present (round-3 shadow→active gate) | `grep -c "branches/main/protection" scripts/nightly_regression_tests.py` | output > 0 |
| No raw-Redis on Popoto keys in new code (anti-criterion) | `grep -Ec "\.hgetall\(\|\.hget\(\|r\.delete\(\|r\.srem\(\|r\.sadd\(" scripts/nightly_regression_tests.py tools/nightly_fix_handback.py` | match count == 0 |
| Up-front page removed from the `new_failures` branch (behavioral anti-criterion) | `pytest tests/unit/test_nightly_decision_gate.py -q -k "shadow or off or new_failures"` — the mode-gating + detection tests assert that on a `new_failures` run in `off`/`active` mode NO up-front page fires from the detection branch (only escalate/fail-safe paths page); `shadow` still pages as today. Behavioral assertion via `send_telegram` call capture, no bespoke AST script. | exit code 0 |
| Detection log sentinel present | `grep -c "nightly-fix.*detection" scripts/nightly_regression_tests.py` | output > 0 |
| Guardrail constants are env-overridable (module-scope) | `grep -c "os.environ.get(\"NIGHTLY_FIX" scripts/nightly_regression_tests.py` | output ≥ 5 |
| Low-urgency notify is a distinct call signature, not a bare string (anti-criterion) | `grep -c "send_telegram(.*silent=True" scripts/nightly_regression_tests.py` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room), round 2 — 2026-07-27. Verdict: NEEDS REVISION (1 blocker). Round-3 rows appended below. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness + History & Consistency + Scope & Value (all 3 agreed) | The round-1 "structural never-merge" resolution has no implementation seam. `tools.valor_session create` exposes no `--permission`/`--settings`/profile flag; `AgentSession` has no settings/permission field; and every `claude-cli` spawn is hardcoded to `--permission-mode bypassPermissions` (`agent/session_runner/harness/claude.py` L154-168) while `PermissionRequest` hooks "do not fire under `claude -p`" (`agent/session_runner/hook_edge.py` L188-190). A deny-list has nowhere to attach and, even attached, bypass mode does not evaluate deny rules. | **RESOLVED (round 2)** | Permission-profile mechanism **deleted** from the plan (Technical Approach → "Enforcement design"; `config/permission_profiles/nightly_fixer.json` removed; a new anti-criterion asserts the file does not exist). Replaced with a buildable defense-in-depth substrate needing NO permission subsystem: (1) **narrow mandate** (make-test-edits → run-tests → open-DRAFT-PR → hand-back → STOP; not an auto-continuing eng session); (2) **`gh pr create --draft`** — GitHub structurally refuses to merge a draft until a human marks it ready-for-review (the load-bearing never-auto-merge mechanism); (3) fail-closed watchdog guard; (4) no-auto-merge + shadow-first. No spike needed. |
| CONCERN | Risk & Robustness | The `gh pr diff --name-only` mechanical guard's failure path is unspecified: a PR-closed/auth-expired/network error returning an empty or errored path list could vacuously satisfy "every changed path under `tests/`" and silently notify instead of paging. | **RESOLVED (round 2)** | Guard made **fail-closed** (Technical Approach → watchdog; Solution key element; Step 4): downgrade to notify ONLY on zero-exit + non-empty + all-`tests/`; ANY {non-zero exit, error, empty/unparseable output, non-`tests/` path} → close PR + page. Failure-Path tests added for the non-zero, raised-exception, and empty-output cases. |
| CONCERN | Scope & Value | The "notify vs. page" tiering — the mechanism that keeps the happy path "not fully silent" — is verified only by `grep -c "notify\|low.urgency\|fyi"`, but the sole alert primitive `send_telegram(msg, dry_run=...)` has no urgency parameter, so the check passes on a bare string literal even if the call is functionally identical to a page. | **RESOLVED (round 2)** | Added `silent: bool = False` to the `send_telegram` wrapper (forwarded to `valor-telegram send --silent` / `disable_notification`); happy-path notify uses `send_telegram(msg, silent=True)`, pages keep `silent=False`. Anti-criterion changed to `grep -c "send_telegram(.*silent=True"` (call signature, not a prose substring); tests assert the `silent` kwarg via call capture. |
| CONCERN | History & Consistency | The plan is internally inconsistent about how settled the permission mechanism is: Resolved Decision #2 + Success Criteria treat structural denial as an accomplished, grep-verifiable fact, while Agent Integration hedges the wiring is an undetermined "build-time detail" to verify in an integration test written after the fact. | **RESOLVED (round 2)** | Resolved Decision #2 rewritten to a single honest enforcement decision (draft-PR + defense-in-depth, no permission profile); Agent Integration's "build-time detail" hedge removed (no session-create flag is needed). The `grep -c "nightly_fixer"` anti-criterion replaced with a pytest integration-test assertion (`test_nightly_fix_dispatch.py`) that the dispatch message contains `gh pr create --draft` and excludes `/do-merge`. |
| BLOCKER | Risk & Robustness (round 3) | Round 2's "draft-PR structural no-auto-merge" guarantee is only prompt-strength. The fixer is a `--role eng` `AgentSession` spawned with hardcoded `--permission-mode bypassPermissions` = unrestricted bash (`agent/session_runner/harness/claude.py` L154-168); nothing stops it running `gh pr ready <url>` then `gh pr merge <url>`. The diff-path watchdog inspects only paths, not merge/draft state, and runs on the next nightly (~24h later, after any merge already landed). | **RESOLVED (round 3)** | Honest limitation stated plainly (Technical Approach → Enforcement design): under `bypassPermissions` an in-session bash cannot be mechanically blocked from `gh pr ready`/`gh pr merge`; no in-session structural-prevention claim is made. The real structural backstop moved OUTSIDE the session: **server-side branch protection on `main`** (require non-author review approval) — verified ABSENT at plan time (404), so enabling it is a hard prerequisite for `active` mode, enforced by an `active`-mode runtime preflight (`gh api .../branches/main/protection` → refuse+escalate on 404/error/missing-review). Shadow-first default bounds blast radius. Watchdog extended with a fail-closed **merge/draft-state guard** (`gh pr view --json isDraft,state,mergedAt` → void+close+page on not-draft/merged/closed/error) — honestly labeled DETECTIVE (next-night), not preventive. Mandate + anti-criteria now forbid `gh pr ready`/`gh pr merge`/push-to-`main` (deterrent) and assert `--draft`. |
| CONCERN | Risk & Robustness (round 3) | `fix_attempt_count` dedup reset semantics were unspecified — when does a set's attempt count reset, and does the state grow unbounded? | **RESOLVED (round 3)** | Replaced scalar with a keyed `fix_attempts: {failing_set_hash: count}` map (Technical Approach → Dedup): key = sha256 of the sorted failing node-id set (changed set → fresh count); increment per dispatch attempt; **prune keys no longer red** (bounded, and a later regression starts fresh); at `NIGHTLY_FIX_MAX_ATTEMPTS` escalate instead of re-dispatch. |
| CONCERN | Scope & Value (round 3) | The bespoke AST verify script `scripts/_verify_no_page_in_new_failures_branch.py` is redundant with the mode-gating behavioral tests. | **RESOLVED (round 3)** | Removed the AST script + its Verification row; the up-front-page-removed anti-criterion is now the behavioral mode-gating test (`send_telegram` call capture on the `new_failures` run). |
| CONCERN | History & Consistency (round 3) | The shadow→active flip prerequisite (branch protection) was prose-only and unenforceable. | **RESOLVED (round 3)** | Added an in-code `active`-mode runtime preflight that verifies review-required branch protection before any dispatch and fails toward paging when absent — the prerequisite is now a code gate, not a hope. Tested. |
| CONCERN | Risk & Robustness (round 3) | The watchdog was ambiguous about which PR it inspects (new fixer PR vs a pre-existing PR). | **RESOLVED (round 3)** | Watchdog identifies the fixer PR **only** by the `pr_url` in the hand-back; never enumerates/searches PRs; a `fixed-silent` with null/absent/unparseable `pr_url` → fail-safe page (Technical Approach → new-vs-preexisting note). |
| NIT | History & Consistency (round 3) | `.env.example` completeness check requires a comment line above each `KEY=`; the five new constants would fail it as bare keys. | **RESOLVED (round 3)** | Update System now specifies each of the five keys ships as a two-line block (comment describing knob + default, then commented placeholder), with `NIGHTLY_FIX_MODE`'s comment noting `active` requires branch protection. |

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

No open questions remain — ready for critique (round 3 findings absorbed: honest `bypassPermissions` limitation, branch-protection structural backstop + builder-verify + `active`-mode preflight, shadow-first gating, merge/draft-state watchdog guard, mandate/anti-criteria deterrent, dedup reset semantics, AST script removed, env-completeness).
