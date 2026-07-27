---
status: Planning
type: feature
appetite: Large
owner: Valor Engels
created: 2026-07-27
tracking: https://github.com/tomcounsell/ai/issues/2334
last_comment_id: 5086978978
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
test-stale with high confidence and within a hard blast-radius cap → a human is
paged **only** as the *escalation of last resort*: when any failure is code-regressed,
when classification is uncertain, when a cap is exceeded, or (fail-safe) when the fix
session dies/hangs. The last-resort alert carries the stuck point (what broke, what
was tried, why blocked, the specific decision needed), not a raw test dump.

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
4. **Autonomous fix path** *(new)*: dispatch an Eng session with a *bounded-fix* mandate (test-file-only edits, cite the source-contract-moving commit, run the SDLC pipeline, open a PR, **never merge, never push to main**). Silent — no up-front page.
5. **Structured hand-back** *(new)*: the dispatched session writes `data/nightly_fix_handbacks/{hash8}.json` at terminal: `{disposition, pr_url, what_broke, classification, tried, blocked_reason, decision_requested}`.
6. **Escalation / fail-safe watchdog** *(new)*: paging fires (a) immediately when the decision gate routes to escalate (any regression/inconclusive/cap-exceeded), with content from the classification; or (b) when a prior dispatch's hand-back reports `blocked-escalate`; or (c) fail-safe — the session is terminal-failed/killed/abandoned, OR hung past `NIGHTLY_FIX_HANDBACK_TIMEOUT_HOURS` with no hand-back. Silence must never swallow a red suite.
7. **Output**: a PR for human review (happy path, no page) OR a last-resort Telegram alert carrying the stuck point.

## Architectural Impact

- **New dependencies**: none external. Reuses `baseline-verifier` (parameterized), `tools.valor_session create`, the existing local-JSON state file, and `send_telegram`.
- **Interface changes**: `baseline-verifier` gains an optional baseline-ref parameter (default `main`, preserving every existing `/do-test` caller). New hand-back JSON contract. `data/nightly_tests_last_run.json` gains `head_commit` and per-dispatch `fix_attempt_count`.
- **Coupling**: keeps the nightly runner's existing "detect + local-JSON state, no Redis" discipline. The fixer session runs the standard SDLC pipeline — no new orchestration substrate.
- **Data ownership**: nightly state stays local JSON; the fixer session is a normal `AgentSession` owned by the worker. Hand-back is a file artifact (no raw-Redis Popoto writes).
- **Reversibility**: high — gated behind `NIGHTLY_FIX_ENABLED`; setting it false restores the current detect-and-page behavior.

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

No new secrets or external services.

## Solution

### Key Elements

- **Silent detection log**: remove the up-front unconditional `send_telegram` on the `new_failures` branch; replace with a log line at detection. The page becomes escalation-driven only.
- **Test-stale-vs-code-regressed classifier**: reuse `baseline-verifier`, pointed at the last-known-good commit, as the *necessary precondition* for autonomy; the dispatched session performs the final test-stale determination via git archaeology under a test-file-only constraint.
- **Decision gate**: a pure function mapping the classification + caps to one of `autonomous-fix | escalate`.
- **Bounded-fix session mandate**: a rewritten dispatch prompt that runs the SDLC pipeline, edits only `tests/`, cites the contract-moving commit, opens a PR, and never merges/pushes to main.
- **Structured hand-back protocol**: a JSON artifact the session writes at terminal, consumed by the escalation watchdog.
- **Escalation + fail-safe watchdog**: pages on `blocked-escalate`, on the escalate decision, or when the session dies/hangs past a bounded timeout with no hand-back.
- **Guardrail constants**: named, env-overridable caps (max failures, max changed files, max attempts, hand-back timeout, feature flag), each with a provisional/tunable comment.

### Flow

Nightly run detects newly-confirmed failures → classify (baseline-verifier vs last-green SHA) →
**decision gate**:
- *all test-stale-candidate & within caps* → dispatch bounded-fix session (silent) → session edits tests, opens PR, writes `fixed-silent` hand-back → **no page; PR waits for human review**.
- *any regression / inconclusive / cap exceeded* → page human now with the classification stuck-point.
- *session self-determines a code regression or judgment call mid-fix* → writes `blocked-escalate` hand-back → watchdog pages with what-was-tried + decision-requested.
- *session dies/hangs past timeout* → fail-safe watchdog pages ("autonomous fix died, suite still red").

### Technical Approach

**Reusing baseline-verifier in the no-branch nightly context (core design decision).**
`baseline-verifier` is built for PR review: it compares failing tests on a *feature branch* against `main`. Nightly has no feature branch — the suite runs on `main` HEAD, and the failure IS on `main`. The correct "known-good" reference is therefore **not** `main` but the **last-known-good commit**: since a *newly-confirmed* failure by definition was absent from the prior run's confirmed-failing set, the prior run's HEAD SHA is a commit where that test passed. So:
- Persist `head_commit = git rev-parse HEAD` in `data/nightly_tests_last_run.json` each run.
- Parameterize `baseline-verifier` with an optional baseline ref (default `main` — every current `/do-test` caller is unchanged). The nightly dispatch passes `prev["head_commit"]`.
- Interpretation in the nightly context:
  - **PASSED at last-green, FAILS at HEAD** → newly-broken since last-green. A source-contract-moving commit *or* a code regression landed in `last_green..HEAD`. This is the set eligible for the *test-stale determination*.
  - **FAILED at last-green too** (baseline-verifier `pre_existing`) → not caused by recent change; do not auto-touch → escalate/leave.
  - **`inconclusive`** (errored/not-found at baseline) → escalate; never guess.

**The final test-stale-vs-code-regressed determination is the dispatched session's job, not a mechanical verdict.** baseline-verifier tells us "newly broken since last-green" — it does NOT by itself prove test-stale (a genuine bug also passes-then-fails). #2399 Group 2 proved a mechanical bucket is insufficient. Therefore the dispatched session:
1. Reads `git log last_green..HEAD -- <source-of-failing-test>` to find the governing commit.
2. Is **hard-constrained to edit only files under `tests/`** — if a correct fix would require touching source, that is a code regression → escalate, do not proceed.
3. Must cite the specific contract-moving commit that legitimizes each test change (the evidence #2402 produced by hand).
4. Escalates on ANY of: a suspected code regression, a contract-*direction* judgment call (Group-2 shape), inability to cite a legitimizing commit, or a cap exceeded.
5. Runs the standard SDLC pipeline (`/do-build` → `/do-test`), which itself re-runs baseline-verifier on the fix branch to confirm the change introduces no NEW regressions, then opens a PR via `/do-pr` — **never `/do-merge`, never push to main**.

**Decision gate** (`decide_fix_or_escalate(classification, new_failures, caps) -> "autonomous-fix" | "escalate"`): pure, unit-testable. Autonomy iff `classification.regressions == [] and classification.inconclusive == [] and set(new_failures) ⊆ set(test_stale_candidates) and len(new_failures) <= NIGHTLY_FIX_MAX_FAILURES and fix_attempt_count < NIGHTLY_FIX_MAX_ATTEMPTS`. baseline-verifier returns discrete buckets (no numeric score), so "high confidence" is expressed as bucket membership, not a threshold float.

**Structured hand-back** (`data/nightly_fix_handbacks/{hash8}.json`): written by the fixer session at terminal via a tiny helper CLI (`python -m tools.nightly_fix_handback write ...`) so the session has a first-class, documented way to signal. Schema: `{disposition: "fixed-silent"|"blocked-escalate"|"still-working", pr_url, what_broke, classification, tried, blocked_reason, decision_requested, written_at}`. The nightly runner reads it (plain file read — no Redis) on the next run's escalation-watchdog preamble.

**Escalation watchdog + fail-safe.** At the start of each nightly run, before running tests, inspect the *prior* dispatch (if any): read its session status (via `AgentSession.query` ORM — never raw Redis) and its hand-back file.
- `blocked-escalate` → page with the hand-back's stuck-point content; clear the pending-dispatch marker.
- session terminal `failed`/`killed`/`abandoned` with no `fixed-silent` hand-back → fail-safe page.
- session non-terminal but older than `NIGHTLY_FIX_HANDBACK_TIMEOUT_HOURS` → fail-safe page ("fix attempt hung, suite still red"). Timeout MUST be < the nightly cadence so a hang is caught within one cycle.
- `fixed-silent` with a `pr_url` → no page (log only); the PR is the deliverable.

**Dedup extension.** Extend the existing `dispatched_hash` mechanism to also persist `fix_attempt_count` per failing-set hash, so the same failing set is not re-attempted beyond `NIGHTLY_FIX_MAX_ATTEMPTS` — a set that resists autonomous fixing escalates instead of re-dispatching every night.

**In-process, not Cowork, for the first cut** (open question 5): reuse the existing `maybe_dispatch_triage_session` machinery (rename → `maybe_dispatch_fix_session` with the new mandate). Cowork parity is filed as follow-up **#2405**.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The existing `except Exception` swallow in `maybe_dispatch_*` (dispatch failure → "no dispatch") is preserved; add a test asserting a failed dispatch logs a warning AND leaves `dispatched_hash`/`fix_attempt_count` untouched so a retry is possible.
- [ ] baseline-verifier invocation failure (worktree add fails, timeout) → classified `inconclusive` → **must route to escalate**, not to autonomous-fix. Test the fail-toward-paging path explicitly.
- [ ] Hand-back file missing/corrupt when the watchdog reads it → treated as "no hand-back" → fail-safe page (never silently assume success). Test with a truncated/absent JSON file.

### Empty/Invalid Input Handling
- [ ] `new_failures == []` → no classification, no dispatch, no page (clean-run path unchanged). Test.
- [ ] `prev["head_commit"]` absent (first run after deploy, or state migrated from an older schema without the field) → cannot establish a last-green baseline → escalate (do not attempt an unbaselined fix). Test.
- [ ] Empty/whitespace `disposition` in a hand-back → treated as invalid → fail-safe page. Test.

### Error State Rendering
- [ ] Escalation Telegram content is asserted to contain the stuck-point fields (what broke, tried, decision requested) — not a raw node-ID dump — on the `blocked-escalate` path.
- [ ] Fail-safe page fires (asserted via `send_telegram` call capture) when the session is terminal-failed with no hand-back.

## Test Impact

- [ ] `scripts/nightly_regression_tests.py` tests (search `tests/unit/` for `nightly` / `maybe_dispatch_triage_session` / `dispatched_hash`) — UPDATE: the `new_failures` branch no longer pages up front; assertions that expect an immediate `send_telegram` on new failures must move to the escalate/fail-safe paths. If `maybe_dispatch_triage_session` is renamed, UPDATE its tests to the new name + bounded-fix prompt.
- [ ] Any test asserting the dispatch prompt contains "Do NOT attempt an auto-hotfix" — REPLACE: the mandate changed to bounded-fix (test-file-only, open-PR-never-merge).
- [ ] `tests/unit/` baseline-verifier tests (if any assert the hardcoded `main` ref) — UPDATE: parameterized baseline ref, default still `main`.
- [ ] If no direct nightly-runner unit tests exist yet, the build ADDS them (decision-gate, watchdog, hand-back parsing) — see Step by Step Tasks.

Justification for anything not affected: the base detector mechanics (`run_tests`, `reconfirm_serial`, run lock, TTFT gate, `load_env_or_die`) are untouched — this feature layers a classification/decision/escalation stage around the existing `new_failures` branch.

## Rabbit Holes

- **Building a general-purpose "is this test stale?" classifier.** Do not. The mechanical precondition (baseline-verifier vs last-green) plus the test-file-only session constraint plus escalate-on-doubt is the whole design. Trying to fully mechanize the test-stale judgment reproduces the #2399 Group-2 trap.
- **Reworking baseline-verifier's internals.** Only add an optional baseline-ref parameter; do not touch its junitxml classification or worktree lifecycle.
- **A bespoke session-orchestration/steering substrate for the fixer.** It's a normal `AgentSession` running the normal SDLC pipeline. Do not invent a new runner.
- **Cowork migration now.** Deferred to #2405. Resisting it here is the point.
- **Tuning the guardrail numbers to perfection.** Ship provisional env-overridable constants with grain-of-salt comments; tune from real nightly data later.

## Risks

### Risk 1: The fixer weakens a test to make a genuine regression pass (the catastrophic case)
**Impact:** A real bug is masked by a test edit and silently merged after a rubber-stamp review.
**Mitigation:** Defense in depth — (a) test-file-only edit constraint; (b) escalate on any inconclusive/regression bucket; (c) the fix branch re-runs baseline-verifier in `/do-test`; (d) **never auto-merge — a human reviews the PR**; (e) a Verification anti-criterion greps the dispatch mandate to prove the never-merge/never-push-to-main instruction is present.

### Risk 2: baseline-verifier's branch-vs-main model is misapplied to nightly
**Impact:** Wrong classification → either false autonomy (dangerous) or false escalation (noisy).
**Mitigation:** The last-green-SHA mapping is the explicit design; on any missing/absent baseline the gate fails toward escalate. Covered by the empty-input tests. Flagged as Open Question 1 for critique.

### Risk 3: Silence swallows a regression (session dies, no page ever fires)
**Impact:** A red suite goes unnoticed — worse than today.
**Mitigation:** The fail-safe watchdog pages on terminal-failure/hang past a bounded timeout (< nightly cadence). Explicitly tested.

### Risk 4: Re-dispatch storm / cost blowup
**Impact:** The same failing set spawns a fresh fixer session every night, burning tokens.
**Mitigation:** `fix_attempt_count` extends `dispatched_hash` dedup; past `NIGHTLY_FIX_MAX_ATTEMPTS` the set escalates instead of re-dispatching.

## Race Conditions

### Race 1: Watchdog reads a hand-back the fixer session is still writing
**Location:** `data/nightly_fix_handbacks/{hash8}.json` write (fixer session) vs read (next nightly preamble).
**Trigger:** A long-running fixer session still executing when the next nightly fires.
**Data prerequisite:** The hand-back file must be complete before the watchdog treats it as authoritative.
**State prerequisite:** A partial/absent hand-back must not read as success.
**Mitigation:** Atomic write (write to `{hash8}.json.tmp`, `os.replace` to final) so a reader never sees a partial file; the watchdog treats absent/corrupt as "no hand-back" → fail-safe (never as success). The nightly run lock already prevents two nightly runs overlapping.

### Race 2: Two nightly invocations overlap
**Location:** `main()` entry.
**Mitigation:** Unchanged — the existing `_acquire_run_lock()` flock already serializes nightly runs; the loser is a no-op.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2405] Migrating the nightly fixer dispatch to a Claude Cowork routine (parity with #2209). First cut stays in-process per open question 5.
- [SEPARATE-SLUG #2405] Any cross-run learning / auto-tuning of the guardrail constants from historical nightly data — the constants ship provisional and env-overridable; adaptive tuning is out of scope.

The invariants "never push/merge to main" and "never edit source (non-test) files to make a failing test pass" are NOT deferrals — they are permanent safety boundaries of this feature, enforced by the test-file-only constraint and asserted as Verification anti-criteria below.

## Update System

- New env-overridable constants (`NIGHTLY_FIX_ENABLED`, `NIGHTLY_FIX_MAX_FAILURES`, `NIGHTLY_FIX_MAX_CHANGED_FILES`, `NIGHTLY_FIX_MAX_ATTEMPTS`, `NIGHTLY_FIX_HANDBACK_TIMEOUT_HOURS`) have safe in-code defaults, so no `.env` propagation is required for the feature to run. Document them in `.env.example` (commented) for discoverability only.
- No new launchd job: the escalation/fail-safe watchdog runs as a preamble inside the existing nightly launchd invocation, so `scripts/install_nightly_tests.sh` needs no change.
- `data/nightly_fix_handbacks/` is created on demand and gitignored (like `data/nightly_tests.lock`). Add the ignore entry.
- No `/update` skill changes required — this is internal to an already-deployed scheduled job.

## Agent Integration

- The dispatched fixer is an Eng `AgentSession` created via the existing `python -m tools.valor_session create --role eng --slug ... --json --message ...` path — already agent-reachable; only the prompt/mandate changes.
- New helper CLI `python -m tools.nightly_fix_handback` (write/read) so the fixer session has a documented, testable way to emit its structured hand-back. Register it in `pyproject.toml [project.scripts]` if a console entry point is warranted (e.g. `valor-nightly-fix-handback`), otherwise `python -m` invocation is sufficient — decide at build time.
- The nightly runner itself is a script, not a bridge tool; no bridge import changes.
- Integration test: assert the dispatched session is created with the bounded-fix mandate (test-file-only, open-PR-never-merge) and that a `fixed-silent` hand-back with a `pr_url` suppresses the page while a `blocked-escalate` hand-back fires one.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/nightly-alert-triage.md` — the mandate flipped from investigate-only to bounded-fix-before-alert; document the classification gate, the never-weaken-assertions invariant, the hand-back protocol, the escalation/fail-safe watchdog, and the guardrail constants.
- [ ] Cross-link from `docs/features/nightly-regression-tests.md` (base detector) and note the new stage in the flow.
- [ ] Add/confirm entry in `docs/features/README.md` index.

### Inline Documentation
- [ ] Each guardrail constant carries a grain-of-salt/provisional comment and its env-override name.
- [ ] Docstring the decision-gate function, the hand-back schema, and the baseline-ref parameterization of `baseline-verifier`.

## Success Criteria

- [ ] On a run where every newly-confirmed failure is test-stale-candidate and within caps, **no up-front Telegram page** fires; a bounded-fix session is dispatched and (on success) a PR exists — verified end-to-end in an integration test with a seeded state file.
- [ ] On any `regression`/`inconclusive` classification, on cap-exceeded, or on a missing last-green baseline, the run **escalates** (pages) and does NOT dispatch an autonomous fix.
- [ ] The dispatch mandate string contains the test-file-only + never-merge/never-push-to-main instruction (grep-verifiable).
- [ ] `blocked-escalate` hand-back → page with stuck-point content; `fixed-silent` + `pr_url` → no page; session-dead/hung-past-timeout → fail-safe page. All three asserted.
- [ ] Every guardrail number is a named env-overridable constant with a provisional comment (grep-verifiable: no bare magic numbers in the new code paths).
- [ ] No raw-Redis operations on Popoto-managed keys in the new code (session status read via `AgentSession.query`).
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
  - Role: verify the never-weaken-assertions surface — test-file-only constraint present in the mandate, escalate-on-doubt paths, fail-safe page, anti-criteria green.
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
- Persist `head_commit = git rev-parse HEAD` into `data/nightly_tests_last_run.json` each run.
- Add an optional baseline-ref parameter to `.claude/agents/baseline-verifier.md` (default `main` — do not break `/do-test` callers).
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

### 3. Bounded-fix dispatch mandate + hand-back CLI
- **Task ID**: build-dispatch-handback
- **Depends On**: none
- **Validates**: `tests/unit/test_nightly_fix_dispatch.py`, `tests/unit/test_nightly_fix_handback.py` (create)
- **Assigned To**: escalation-builder
- **Agent Type**: builder
- **Parallel**: true
- Rewrite the dispatch prompt to the bounded-fix mandate (test-file-only edits, cite contract-moving commit, run SDLC, open PR, **never merge, never push to main**). Rename `maybe_dispatch_triage_session` → `maybe_dispatch_fix_session`.
- Add `tools/nightly_fix_handback.py` (atomic write via tmp+`os.replace`; strict read that treats absent/corrupt as no-hand-back).

### 4. Escalation + fail-safe watchdog + guardrail constants + dedup
- **Task ID**: build-watchdog
- **Depends On**: build-dispatch-handback
- **Validates**: `tests/unit/test_nightly_escalation_watchdog.py` (create)
- **Assigned To**: escalation-builder
- **Agent Type**: builder
- **Parallel**: false
- Watchdog preamble in `main()`: read prior dispatch session status (ORM) + hand-back; page on `blocked-escalate`, terminal-fail-with-no-handback, or hang-past-timeout.
- Named env-overridable guardrail constants with provisional comments; extend `dispatched_hash` state with `fix_attempt_count`.

### 5. Tests (unit + integration)
- **Task ID**: build-tests
- **Depends On**: build-decision-gate, build-watchdog
- **Assigned To**: fix-gate-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Unit: decision gate (all branches), watchdog (all three page paths + no-page), hand-back parsing (valid/absent/corrupt), empty-input and missing-baseline paths.
- Integration: seeded `data/nightly_tests_last_run.json` → silent-fix path (no page, dispatch happens) and escalate path (page, no dispatch).

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
| Dispatch mandate forbids merge/push-to-main (anti-criterion) | `grep -Eic "never (merge\|push).*(main)\|do not merge\|open a pr" scripts/nightly_regression_tests.py` | output > 0 |
| No auto-merge in the fixer path (anti-criterion) | `grep -c "do-merge\|do_merge" scripts/nightly_regression_tests.py` | match count == 0 |
| No raw-Redis on Popoto keys in new code (anti-criterion) | `grep -Ec "\.hgetall\(\|\.hget\(\|r\.delete\(\|r\.srem\(\|r\.sadd\(" scripts/nightly_regression_tests.py tools/nightly_fix_handback.py` | match count == 0 |
| Up-front unconditional page removed (anti-criterion) | `python -c "import ast,sys; src=open('scripts/nightly_regression_tests.py').read(); sys.exit(0 if 'detection' in src.lower() else 1)"` | exit code 0 |
| Guardrail constants are env-overridable | `grep -c "os.environ.get(\"NIGHTLY_FIX" scripts/nightly_regression_tests.py` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **baseline-verifier's semantic adaptation to the no-branch nightly context.** The plan maps "known-good ref" to the *last-green nightly SHA* (`prev["head_commit"]`) rather than `main`, because nightly runs on main and the failure is on main. Do you agree with this mapping, and with parameterizing baseline-verifier (default `main`) rather than writing a nightly-specific classifier? This is the load-bearing design decision.
2. **Guardrail defaults.** Proposed provisional starting values (all env-overridable): `NIGHTLY_FIX_MAX_FAILURES=15` (#2399 had 11), `NIGHTLY_FIX_MAX_CHANGED_FILES=10`, `NIGHTLY_FIX_MAX_ATTEMPTS=1`, `NIGHTLY_FIX_HANDBACK_TIMEOUT_HOURS=6` (must be < nightly cadence). And should `NIGHTLY_FIX_ENABLED` default **on** or ship **off** for a shadow/observe-only period first? A shadow mode (classify + log the decision, but still page and don't dispatch) would de-risk the first deploy.
3. **Test-file-only as a hard guard vs. a prompt instruction.** The mandate instructs test-file-only edits, and `/do-pr-review` would catch a source edit — but should the build add a *mechanical* guard (e.g. the fixer's PR is rejected/flagged if its diff touches non-`tests/` paths) rather than relying on the session honoring the prompt? This is the strongest defense against the catastrophic case.
