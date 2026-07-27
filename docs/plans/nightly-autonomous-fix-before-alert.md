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
4. **Autonomous fix path** *(new)*: dispatch a fixer session **under a restricted permission profile that structurally denies merge/push-to-main** (see Technical Approach — the never-merge invariant is enforced by the Claude Code permission system, NOT the prompt). Its mandate names ONLY the Build, Test, and open-PR stages — never "run the full SDLC pipeline," never `/do-merge`. Bounded: test-file-only edits, cite the source-contract-moving commit, open a PR. No up-front page.
5. **Structured hand-back** *(new)*: the dispatched session persists its terminal hand-back **on its own `AgentSession` record via the Popoto ORM** (nullable `nightly_fix_handback` JSON field — not a separate file), so the watchdog (which already reads the session via the ORM) has a single, atomic source of truth: `{disposition, pr_url, what_broke, classification, tried, blocked_reason, decision_requested, written_at}`.
6. **Test-file-only mechanical guard** *(new)*: before the watchdog honors a `fixed-silent` disposition, it fetches the PR's changed paths (`gh pr diff --name-only`) and **rejects the fix mechanically if any path is outside `tests/`** — a source-file edit voids `fixed-silent` and converts it to an escalation page. This does not rely on the session honoring the prompt.
7. **Escalation / fail-safe watchdog** *(new)*: a **page** fires (a) immediately when the decision gate routes to escalate (any regression/inconclusive/cap-exceeded), with content from the classification; (b) when a prior dispatch's hand-back reports `blocked-escalate`; (c) when a `fixed-silent` PR fails the test-file-only mechanical guard; or (d) fail-safe — the session is terminal-failed/killed/abandoned, OR hung past `NIGHTLY_FIX_HANDBACK_TIMEOUT_HOURS` with no hand-back. Silence must never swallow a red suite.
8. **Output**: on the happy path, a PR for human review **plus a low-urgency notify** (not a page); otherwise a last-resort **page** carrying the stuck point.

## Architectural Impact

- **New dependencies**: none external. Reuses `baseline-verifier` (parameterized), `tools.valor_session create`, the existing local-JSON state file, and `send_telegram`.
- **Interface changes**: `baseline-verifier` gains a documented `baseline_ref` **Input field** (default `main` via a `${baseline_ref:-main}` shell default in the prompt body, preserving every existing `/do-test` caller). `AgentSession` gains a nullable `nightly_fix_handback` JSON field (the structured hand-back contract). `data/nightly_tests_last_run.json` gains `head_commit` and per-dispatch `fix_attempt_count`.
- **Coupling**: the nightly runner already reads Redis for the fixer's session status in the watchdog; persisting the hand-back on that same `AgentSession` record (rather than a parallel JSON file) keeps one source of truth and removes a file-write race. Local-JSON stays the store for detection/dedup state only. The fixer session runs a **capability-restricted** Build→Test→open-PR flow — no new orchestration substrate.
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

No new secrets or external services.

## Solution

### Key Elements

- **Silent detection log**: remove the up-front unconditional `send_telegram` on the `new_failures` branch; replace with a log line at detection. The page becomes escalation-driven only.
- **Shadow-first rollout**: `NIGHTLY_FIX_MODE` ships defaulting to `shadow` — classify, run the decision gate, log the decision, but page-as-today and dispatch nothing. Flip to `active` only after real shadow logs confirm the gate's judgment. De-risks the blast radius of an autonomous test-editor.
- **Test-stale-vs-code-regressed classifier**: reuse `baseline-verifier`, pointed at the last-known-good commit, as the *necessary precondition* for autonomy; the dispatched session performs the final test-stale determination via git archaeology under a test-file-only constraint.
- **Decision gate**: a pure function mapping the classification + caps to one of `autonomous-fix | escalate`.
- **Structurally merge-incapable fixer**: the fixer session is dispatched under a **restricted permission profile that denies `gh pr merge`, `git push` to main, and the `/do-merge` skill** — the never-merge invariant is enforced by the permission system, not by prompt text. Its mandate names ONLY Build, Test, and open-PR stages.
- **Test-file-only mechanical guard**: the watchdog rejects any `fixed-silent` PR whose diff touches a non-`tests/` path (`gh pr diff --name-only`), converting it to a page — a mechanical backstop independent of the prompt.
- **Structured hand-back protocol**: persisted on the `AgentSession` record via the ORM (nullable `nightly_fix_handback` field), consumed by the escalation watchdog.
- **Escalation + fail-safe watchdog**: pages on `blocked-escalate`, on the escalate decision, on a test-file-only guard failure, or when the session dies/hangs past a bounded timeout with no hand-back. On the happy path it emits a **low-urgency notify** (not a page).
- **Guardrail constants**: five named, env-overridable caps read via **raw `os.environ.get` at nightly-script module scope** (max failures, max changed files, max attempts, hand-back timeout, and the `NIGHTLY_FIX_MODE` gate), each with a provisional/tunable comment. Deliberately NOT promoted to `config/settings.py` — see Technical Approach for the rationale.

### Flow

Nightly run detects newly-confirmed failures → classify (baseline-verifier vs last-green SHA) →
**decision gate** (in `shadow` mode: log the decision below, then page-as-today and dispatch nothing; in `active` mode, execute it):
- *all test-stale-candidate & within caps* → dispatch merge-incapable fixer session (silent) → session edits tests, opens PR, persists `fixed-silent` hand-back on its `AgentSession` record → watchdog runs the test-file-only mechanical guard on the PR diff → **guard passes: low-urgency notify, PR waits for human review; guard fails (non-`tests/` path): page**.
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
5. Executes **only the Build, Test, and open-PR stages** — `/do-build` → `/do-test` (which itself re-runs baseline-verifier on the fix branch to confirm the change introduces no NEW regressions) → open a PR via `/do-pr`. The mandate **never names "run the full SDLC pipeline" and never names `/do-merge`.**

**Structural never-merge (BLOCKER resolution — this is the whole point of the feature).**
Prompt text alone cannot prevent an auto-continuing pipeline from reaching Merge, and a silent unreviewed merge to `main` is strictly worse than an alert. The fixer is therefore made **structurally incapable of merging or pushing to main**, via the Claude Code permission system rather than the mandate wording:
- The fixer `AgentSession` is dispatched with a dedicated **restricted permission profile** — a `nightly-fixer` deny-list layered onto the session's `settings` (`config/permission_profiles/nightly_fixer.json`, applied via the session's `--permission`/settings seam in `tools.valor_session create`). The deny rules are: `Bash(gh pr merge:*)`, `Bash(git push:*)` (the fixer opens its PR via `gh pr create`, which does not require a manual push to `main`; branch pushes for the PR go through `gh`), and the `/do-merge` skill is not on its allow-list. A blocked call surfaces as a permission denial the session cannot self-approve.
- Because the capability is absent, the fixer *cannot* advance past open-PR even if a stray prompt or an auto-continue nudge told it to. The mandate names only Build/Test/open-PR; the permission profile guarantees it.
- Belt-and-suspenders: the watchdog's test-file-only mechanical guard (below) and the anti-criteria greps assert the merge-incapable posture, but the profile is the load-bearing mechanism.

**Decision gate** (`decide_fix_or_escalate(classification, new_failures, caps) -> "autonomous-fix" | "escalate"`): pure, unit-testable. Autonomy iff `classification.regressions == [] and classification.inconclusive == [] and set(new_failures) ⊆ set(test_stale_candidates) and len(new_failures) <= NIGHTLY_FIX_MAX_FAILURES and fix_attempt_count < NIGHTLY_FIX_MAX_ATTEMPTS`. baseline-verifier returns discrete buckets (no numeric score), so "high confidence" is expressed as bucket membership, not a threshold float. In `shadow` mode the gate's verdict is computed and logged but not acted on.

**Structured hand-back — persisted on the `AgentSession` record via the ORM (reconsidered per critique).** The fixer is *already* a normal `AgentSession` the worker owns, and the watchdog *already* reads that record via `AgentSession.query`. So rather than duplicate a parallel file-based store (with its own atomic-write dance and read/write race), the hand-back lives on the session itself: add a nullable `nightly_fix_handback` JSON field to `AgentSession`. The fixer writes it at terminal through a thin, documented helper CLI (`python -m tools.nightly_fix_handback write ...`) that loads the session by id and calls `session.save()` — **ORM only, never raw Redis**. Schema: `{disposition: "fixed-silent"|"blocked-escalate"|"still-working", pr_url, what_broke, classification, tried, blocked_reason, decision_requested, written_at}`. This is an additive nullable field: per `_heal_descriptor_pollution` (issues #1099/#1172) existing records read it as `None` with no backcompat code; it is still registered as a no-op idempotent entry in `scripts/update/migrations.py` per the repo's Popoto-schema-change convention.

**Escalation watchdog + fail-safe.** At the start of each nightly run, before running tests, inspect the *prior* dispatch (if any): read its session status **and its `nightly_fix_handback` field** via `AgentSession.query` (ORM — never raw Redis).
- `blocked-escalate` → **page** with the hand-back's stuck-point content; clear the pending-dispatch marker.
- session terminal `failed`/`killed`/`abandoned` with no `fixed-silent` hand-back → fail-safe **page**.
- session non-terminal but older than `NIGHTLY_FIX_HANDBACK_TIMEOUT_HOURS` → fail-safe **page** ("fix attempt hung, suite still red").
- `fixed-silent` with a `pr_url` → run the **test-file-only mechanical guard**: `gh pr diff --name-only <pr_url>`; if every changed path is under `tests/`, emit a **low-urgency notify** (not a page) pointing at the PR; if ANY path is outside `tests/`, treat the fix as void → **page** ("autonomous fix touched non-test files — review required"). This mechanical guard runs in the nightly runner's watchdog preamble and does not trust the session's self-report.

**Worst-case fail-safe latency (critique correction).** The watchdog is a *preamble to the next nightly run*, so `NIGHTLY_FIX_HANDBACK_TIMEOUT_HOURS` does not bound *detection* latency — it only decides whether a still-running dispatch is judged "hung" when the preamble next fires. The true worst-case latency for surfacing a hung/dead fixer is therefore **~one nightly cadence (~24h)**, not the 6h timeout. This is **accepted for the first cut**: (a) `shadow` mode is the default, so no real dispatch happens until we opt in; (b) a hung fixer produces no merge (structurally impossible) and leaves the suite in exactly the red state a human would have seen under today's detect-and-page anyway — the feature cannot make latency *worse* than the status quo, only the happy path faster. If ~24h proves unacceptable after `active` rollout, a dedicated intra-day launchd watchdog is filed as follow-up **#2405**; it is deliberately out of scope here to avoid a second scheduled job before the core gate is proven.

**Dedup extension.** Extend the existing `dispatched_hash` mechanism to also persist `fix_attempt_count` per failing-set hash, so the same failing set is not re-attempted beyond `NIGHTLY_FIX_MAX_ATTEMPTS` — a set that resists autonomous fixing escalates instead of re-dispatching every night.

**In-process, not Cowork, for the first cut** (open question 5): reuse the existing `maybe_dispatch_triage_session` machinery (rename → `maybe_dispatch_fix_session` with the new mandate). Cowork parity is filed as follow-up **#2405**.

**Guardrail constants — raw `os.environ.get` at module scope, NOT `config/settings.py` (critique decision).** All five knobs (`NIGHTLY_FIX_MODE`, `NIGHTLY_FIX_MAX_FAILURES`, `NIGHTLY_FIX_MAX_CHANGED_FILES`, `NIGHTLY_FIX_MAX_ATTEMPTS`, `NIGHTLY_FIX_HANDBACK_TIMEOUT_HOURS`) are declared as module-level constants in `scripts/nightly_regression_tests.py`, each read via `os.environ.get(...)` with an in-code default and a one-line provisional/tunable grain-of-salt comment. This is a deliberate, stated choice, not an oversight: `config/settings.py`'s `TimeoutSettings` is the home for cross-cutting timeout/retry/TTL values consumed across the bridge/worker/agent runtime (per `docs/features/config-timeout-catalog.md`'s promote-vs-name-locally criterion). These five are single-consumer knobs of one standalone launchd script (`nightly_regression_tests.py` imports nothing from the runtime config surface today), so naming them locally keeps them next to their only reader and avoids polluting the global settings namespace. If a second consumer ever appears, promote them then. The anti-criterion greps for `os.environ.get("NIGHTLY_FIX` to assert no bare magic numbers survive in the new paths.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The existing `except Exception` swallow in `maybe_dispatch_*` (dispatch failure → "no dispatch") is preserved; add a test asserting a failed dispatch logs a warning AND leaves `dispatched_hash`/`fix_attempt_count` untouched so a retry is possible.
- [ ] baseline-verifier invocation failure (worktree add fails, timeout) → classified `inconclusive` → **must route to escalate**, not to autonomous-fix. Test the fail-toward-paging path explicitly.
- [ ] `nightly_fix_handback` field absent/null/malformed on the session record when the watchdog reads it → treated as "no hand-back" → fail-safe page (never silently assume success). Test with a `None` field and a non-dict/invalid payload.
- [ ] `fixed-silent` hand-back whose PR diff touches a non-`tests/` path → the mechanical guard voids the fix → **page** (not a notify). Test by stubbing `gh pr diff --name-only` to return a `src/` path.

### Empty/Invalid Input Handling
- [ ] `new_failures == []` → no classification, no dispatch, no page (clean-run path unchanged). Test.
- [ ] `prev["head_commit"]` absent (first run after deploy, or state migrated from an older schema without the field) → cannot establish a last-green baseline → escalate (do not attempt an unbaselined fix). Test.
- [ ] Empty/whitespace `disposition` in a hand-back → treated as invalid → fail-safe page. Test.

### Error State Rendering
- [ ] Escalation Telegram content is asserted to contain the stuck-point fields (what broke, tried, decision requested) — not a raw node-ID dump — on the `blocked-escalate` path.
- [ ] Fail-safe page fires (asserted via `send_telegram` call capture) when the session is terminal-failed with no hand-back.
- [ ] `fixed-silent` + guard-pass emits a **low-urgency notify** (distinct call/flag from the high-urgency page), asserted via call capture — the happy path is not fully silent.

### Mode Gating
- [ ] `NIGHTLY_FIX_MODE=shadow` (the default): the decision gate runs and logs its verdict, but NO fixer session is dispatched AND the up-front page still fires as today. Asserted: `valor_session create` not called, `send_telegram` called.
- [ ] `NIGHTLY_FIX_MODE=off`: exact current detect-and-page behavior; no classification, no gate. Asserted.
- [ ] `NIGHTLY_FIX_MODE=active`: full dispatch path exercised.

## Test Impact

- [ ] `scripts/nightly_regression_tests.py` tests (search `tests/unit/` for `nightly` / `maybe_dispatch_triage_session` / `dispatched_hash`) — UPDATE: the `new_failures` branch no longer pages up front; assertions that expect an immediate `send_telegram` on new failures must move to the escalate/fail-safe paths. If `maybe_dispatch_triage_session` is renamed, UPDATE its tests to the new name + bounded-fix prompt.
- [ ] Any test asserting the dispatch prompt contains "Do NOT attempt an auto-hotfix" — REPLACE: the mandate names only Build/Test/open-PR (test-file-only, never `/do-merge`), and the never-merge posture is asserted structurally via the permission profile.
- [ ] `tests/unit/` baseline-verifier tests (if any assert the hardcoded `main` ref) — UPDATE: `baseline_ref` Input field with `${baseline_ref:-main}` default; assert the default still resolves to `main` for existing callers.
- [ ] `tests/unit/` AgentSession model/schema tests — UPDATE/ADD: assert the new nullable `nightly_fix_handback` field round-trips via the ORM and reads `None` on legacy records.
- [ ] If no direct nightly-runner unit tests exist yet, the build ADDS them (decision-gate, watchdog, hand-back parsing, mechanical guard, mode gating) — see Step by Step Tasks.

Justification for anything not affected: the base detector mechanics (`run_tests`, `reconfirm_serial`, run lock, TTFT gate, `load_env_or_die`) are untouched — this feature layers a classification/decision/escalation stage around the existing `new_failures` branch.

## Rabbit Holes

- **Building a general-purpose "is this test stale?" classifier.** Do not. The mechanical precondition (baseline-verifier vs last-green) plus the test-file-only session constraint plus escalate-on-doubt is the whole design. Trying to fully mechanize the test-stale judgment reproduces the #2399 Group-2 trap.
- **Reworking baseline-verifier's internals.** Only add an optional baseline-ref parameter; do not touch its junitxml classification or worktree lifecycle.
- **A bespoke session-orchestration/steering substrate for the fixer.** It's a normal `AgentSession` running only Build/Test/open-PR under a restricted permission profile. Do not invent a new runner; do not let it run the full pipeline through Merge.
- **Cowork migration now.** Deferred to #2405. Resisting it here is the point.
- **Tuning the guardrail numbers to perfection.** Ship provisional env-overridable constants with grain-of-salt comments; tune from real nightly data later.

## Risks

### Risk 1: The fixer weakens a test to make a genuine regression pass (the catastrophic case)
**Impact:** A real bug is masked by a test edit and silently merged after a rubber-stamp review.
**Mitigation:** Defense in depth — (a) the **structural** merge/push-to-main denial via the `nightly-fixer` restricted permission profile (load-bearing: the fixer *cannot* merge even if instructed); (b) the watchdog's **mechanical** test-file-only guard on the PR diff (voids + pages on any non-`tests/` path — does not trust the prompt); (c) escalate on any inconclusive/regression bucket; (d) the fix branch re-runs baseline-verifier in `/do-test`; (e) a human reviews the PR before anything lands on main; (f) Verification anti-criteria assert both the absence of `/do-merge` in the fixer path and the presence of the permission-profile wiring.

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

The invariants "never push/merge to main" and "never edit source (non-test) files to make a failing test pass" are NOT deferrals — they are permanent safety boundaries of this feature, enforced **structurally** by the `nightly-fixer` restricted permission profile and the watchdog's mechanical test-file-only guard (not by prompt text), and asserted as Verification anti-criteria below.

## Update System

- New env-overridable constants (`NIGHTLY_FIX_MODE`, `NIGHTLY_FIX_MAX_FAILURES`, `NIGHTLY_FIX_MAX_CHANGED_FILES`, `NIGHTLY_FIX_MAX_ATTEMPTS`, `NIGHTLY_FIX_HANDBACK_TIMEOUT_HOURS`) have safe in-code defaults (`NIGHTLY_FIX_MODE` defaults to `shadow`), so no `.env` propagation is required for the feature to run. Document them in `.env.example` (commented) for discoverability only, noting the `off`/`shadow`/`active` rollout path.
- **Popoto schema change**: the additive nullable `AgentSession.nightly_fix_handback` field is registered as an idempotent no-op entry in `scripts/update/migrations.py`'s `MIGRATIONS` dict per the repo convention. No data backfill runs (`_heal_descriptor_pollution` reads legacy records as `None`); the entry exists to satisfy the schema-change convention and record the field's introduction.
- No new launchd job: the escalation/fail-safe watchdog runs as a preamble inside the existing nightly launchd invocation, so `scripts/install_nightly_tests.sh` needs no change. (A dedicated intra-day watchdog, if the ~24h fail-safe latency proves too slow after `active` rollout, is deferred to #2405.)
- The `nightly-fixer` restricted permission profile (`config/permission_profiles/nightly_fixer.json`) is a new repo-tracked config file — no per-machine propagation needed since it is committed, not vaulted.
- No `data/` directory or gitignore entry is added (the hand-back moved onto the ORM record; the earlier file-based store is dropped).
- No `/update` skill changes required — this is internal to an already-deployed scheduled job.

## Agent Integration

- The dispatched fixer is an Eng `AgentSession` created via the existing `python -m tools.valor_session create --role eng --slug ... --json --message ...` path — already agent-reachable — **but dispatched under the `nightly-fixer` restricted permission profile** so it is structurally merge/push-incapable. The build wires the profile through the session-create seam (the exact flag/settings hand-off is a build-time detail of `tools.valor_session create`; verify the deny rules actually load in the integration test).
- New helper CLI `python -m tools.nightly_fix_handback` (write/read) so the fixer session has a documented, testable way to emit its structured hand-back. It loads the target `AgentSession` and persists the hand-back via `session.save()` (ORM only). Register it in `pyproject.toml [project.scripts]` if a console entry point is warranted (e.g. `valor-nightly-fix-handback`), otherwise `python -m` invocation is sufficient — decide at build time.
- The nightly runner itself is a script, not a bridge tool; no bridge import changes.
- Integration test: assert (a) the dispatched session is created with the merge-incapable profile (deny rules present) and a mandate naming only Build/Test/open-PR; (b) a `fixed-silent` hand-back with a `tests/`-only PR diff emits a low-urgency notify and no page; (c) a `fixed-silent` hand-back whose PR diff touches a non-`tests/` path is voided → page; (d) a `blocked-escalate` hand-back fires a page.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/nightly-alert-triage.md` — the mandate flipped from investigate-only to bounded-fix-before-alert; document the classification gate, the never-weaken-assertions invariant, the structural merge-incapable permission profile, the mechanical test-file-only guard, the ORM-based hand-back protocol, the notify-vs-page tiering, the shadow→active rollout, and the guardrail constants.
- [ ] Cross-link from `docs/features/nightly-regression-tests.md` (base detector) and note the new stage in the flow.
- [ ] Add/confirm entry in `docs/features/README.md` index.

### Inline Documentation
- [ ] Each guardrail constant carries a grain-of-salt/provisional comment and its env-override name.
- [ ] Docstring the decision-gate function, the hand-back schema, and the baseline-ref parameterization of `baseline-verifier`.

## Success Criteria

- [ ] In `active` mode, on a run where every newly-confirmed failure is test-stale-candidate and within caps, **no up-front Telegram page** fires; a merge-incapable fixer session is dispatched and (on success) a PR exists plus a low-urgency notify — verified end-to-end in an integration test with a seeded state file.
- [ ] The default `NIGHTLY_FIX_MODE=shadow` computes+logs the gate verdict but dispatches nothing and still pages as today; `off` reproduces exact current behavior. Both asserted.
- [ ] On any `regression`/`inconclusive` classification, on cap-exceeded, or on a missing last-green baseline, the run **escalates** (pages) and does NOT dispatch an autonomous fix.
- [ ] The fixer is dispatched under a permission profile that **denies** `gh pr merge` / push-to-main and lacks `/do-merge` (structural, not prompt-only); its mandate names only Build/Test/open-PR (grep-verifiable: no `do-merge`/`do_merge` in the fixer path).
- [ ] The watchdog's mechanical test-file-only guard voids a `fixed-silent` PR that touches any non-`tests/` path and pages instead (asserted).
- [ ] `blocked-escalate` hand-back → page with stuck-point content; `fixed-silent` + `tests/`-only PR → low-urgency notify (no page); session-dead/hung-past-timeout → fail-safe page. All asserted.
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
  - Role: verify the never-weaken-assertions surface — the structural merge-incapable permission profile actually denies merge/push (not prompt-only), the mechanical test-file-only guard voids+pages on non-`tests/` diffs, escalate-on-doubt paths, notify-vs-page tiering, shadow-mode default, fail-safe page, anti-criteria green.
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

### 3. Merge-incapable dispatch + permission profile + ORM hand-back
- **Task ID**: build-dispatch-handback
- **Depends On**: none
- **Validates**: `tests/unit/test_nightly_fix_dispatch.py`, `tests/unit/test_nightly_fix_handback.py` (create)
- **Assigned To**: escalation-builder
- **Agent Type**: builder
- **Parallel**: true
- Add the `nightly-fixer` restricted permission profile (`config/permission_profiles/nightly_fixer.json`: deny `Bash(gh pr merge:*)`, `Bash(git push:*)`, no `/do-merge`) and wire it through the `tools.valor_session create` session-settings seam.
- Rewrite the dispatch prompt so the mandate names ONLY Build/Test/open-PR (test-file-only edits, cite contract-moving commit, open a PR) — never "full SDLC pipeline," never `/do-merge`. Rename `maybe_dispatch_triage_session` → `maybe_dispatch_fix_session`.
- Add the nullable `AgentSession.nightly_fix_handback` field + a no-op idempotent `MIGRATIONS` entry in `scripts/update/migrations.py`.
- Add `tools/nightly_fix_handback.py` (loads the session, persists via `session.save()`; strict read treats null/absent/malformed as no-hand-back).

### 4. Escalation + fail-safe watchdog + mechanical guard + guardrail constants + dedup
- **Task ID**: build-watchdog
- **Depends On**: build-dispatch-handback
- **Validates**: `tests/unit/test_nightly_escalation_watchdog.py` (create)
- **Assigned To**: escalation-builder
- **Agent Type**: builder
- **Parallel**: false
- Watchdog preamble in `main()`: read prior dispatch session status + `nightly_fix_handback` (ORM); page on `blocked-escalate`, terminal-fail-with-no-handback, or hang-past-timeout.
- Add the mechanical test-file-only guard: on `fixed-silent`, run `gh pr diff --name-only`; all-`tests/` → low-urgency notify; any non-`tests/` path → page.
- Five named env-overridable guardrail constants (module-scope `os.environ.get`, provisional comments), incl. `NIGHTLY_FIX_MODE` (off/shadow/active, default shadow); gate the dispatch on mode; extend `dispatched_hash` state with `fix_attempt_count`.

### 5. Tests (unit + integration)
- **Task ID**: build-tests
- **Depends On**: build-decision-gate, build-watchdog
- **Assigned To**: fix-gate-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Unit: decision gate (all branches), watchdog (all page paths + notify path), hand-back parsing (valid/null/malformed), mechanical guard (all-tests vs non-tests diff), mode gating (off/shadow/active), empty-input and missing-baseline paths.
- Integration: seeded `data/nightly_tests_last_run.json` → active-mode silent-fix path (no page, notify, dispatch under merge-incapable profile) and escalate path (page, no dispatch); shadow-mode (page + no dispatch).

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
| Structural merge-denial profile present (anti-criterion) | `grep -Eq "gh pr merge" config/permission_profiles/nightly_fixer.json && grep -Eq "git push" config/permission_profiles/nightly_fixer.json && echo ok` | prints `ok` |
| Fixer dispatched under the profile (anti-criterion) | `grep -c "nightly_fixer" scripts/nightly_regression_tests.py` | output > 0 |
| No auto-merge anywhere in the fixer path (anti-criterion) | `grep -c "do-merge\|do_merge" scripts/nightly_regression_tests.py config/permission_profiles/nightly_fixer.json` | match count == 0 |
| Mechanical test-file-only guard present (anti-criterion) | `grep -c "pr diff --name-only" scripts/nightly_regression_tests.py` | output > 0 |
| No raw-Redis on Popoto keys in new code (anti-criterion) | `grep -Ec "\.hgetall\(\|\.hget\(\|r\.delete\(\|r\.srem\(\|r\.sadd\(" scripts/nightly_regression_tests.py tools/nightly_fix_handback.py` | match count == 0 |
| Up-front page removed *from the `new_failures` branch specifically* (scoped anti-criterion) | `python scripts/_verify_no_page_in_new_failures_branch.py` (AST helper the build adds: parses `main()`, locates the `if/elif` whose test references `new_failures`, asserts NO `send_telegram(` Call node in that subtree; exit 0 if clean) | exit code 0 |
| Detection log sentinel present | `grep -c "nightly-fix.*detection" scripts/nightly_regression_tests.py` | output > 0 |
| Guardrail constants are env-overridable (module-scope) | `grep -c "os.environ.get(\"NIGHTLY_FIX" scripts/nightly_regression_tests.py` | output ≥ 5 |
| Low-urgency notify distinct from page | `grep -c "notify\|low.urgency\|fyi" scripts/nightly_regression_tests.py` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Resolved Decisions (critique round 1)

These were the plan's original Open Questions; the critique (1 BLOCKER + 6 CONCERNs) resolved them. Recorded here so the next critique round sees the decisions rather than re-litigating them.

1. **baseline-verifier semantic adaptation — RESOLVED (owner-confirmed in the design-constraints comment).** Known-good ref maps to the *last-green nightly SHA* (`prev["head_commit"]`), not `main`, because nightly runs on main and the failure IS on main — diffing against `main` would mask a regression that already landed there. Implemented via a documented `baseline_ref` Input field with a `${baseline_ref:-main}` default (Technical Approach), preserving every `/do-test` caller. No nightly-specific classifier.
2. **Structural never-merge — RESOLVED (BLOCKER).** Prompt-only never-merge is insufficient against an auto-continuing pipeline. The fixer is dispatched under a restricted permission profile (`config/permission_profiles/nightly_fixer.json`) that denies `gh pr merge` / `git push` and lacks `/do-merge`; its mandate names only Build/Test/open-PR. See Technical Approach → "Structural never-merge."
3. **Guardrail constants home — RESOLVED (CONCERN).** Five knobs as module-scope `os.environ.get` reads with provisional comments, deliberately NOT promoted to `config/settings.py` (single-consumer script; rationale in Technical Approach). Provisional defaults: `NIGHTLY_FIX_MAX_FAILURES=15`, `NIGHTLY_FIX_MAX_CHANGED_FILES=10`, `NIGHTLY_FIX_MAX_ATTEMPTS=1`, `NIGHTLY_FIX_HANDBACK_TIMEOUT_HOURS=6`, `NIGHTLY_FIX_MODE=shadow`.
4. **Shadow-first rollout — RESOLVED (CONCERN).** `NIGHTLY_FIX_MODE` ships defaulting to `shadow` (classify + log the gate verdict, page-as-today, dispatch nothing); flip to `active` after observing shadow logs. `off` restores exact current behavior.
5. **Happy-path notification — RESOLVED (CONCERN).** The `fixed-silent` path emits a low-urgency notify (not a page) so a human knows a fix PR is waiting; only genuine blockers page.
6. **Test-file-only mechanical guard — RESOLVED (CONCERN).** The watchdog rejects any `fixed-silent` PR whose diff (`gh pr diff --name-only`) touches a non-`tests/` path, converting it to a page — a mechanical backstop that runs in the nightly runner, independent of the prompt.
7. **Hand-back storage — RESOLVED (CONCERN).** Persisted on the `AgentSession` record via the ORM (nullable `nightly_fix_handback` field) rather than a duplicate file store, eliminating Race 1's torn-read hazard.

No open questions remain — ready for critique round 2.
