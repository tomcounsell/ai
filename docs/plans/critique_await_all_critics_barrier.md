---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-06-15
tracking: https://github.com/tomcounsell/ai/issues/1690
last_comment_id:
---

# /do-plan-critique: Hard Await-All Barrier for War-Room Critics

## Problem

`/do-plan-critique` spawns its seven war-room critics (Skeptic, Operator, Archaeologist, Adversary, Simplifier, User, Consistency Auditor) as **background tasks** via `run_in_background: true`, then relies on **prose instructions** ("you MUST wait and collect", "poll/await all six background agents") to make the driving subagent block until every critic finishes before synthesizing the verdict.

Prose is not an enforceable barrier. The driving subagent is itself an LLM that can — and did — return early, recording the verdict before late-arriving critics finished. Their findings are then silently dropped.

**Current behavior:**
- The skill spawns critics with `run_in_background: true` (`SKILL.md:154`, `CRITICS.md:7`).
- Step 3.5 ("Wait and Collect", added v1.3.0 / #1654) tells the subagent in prose to block on all background critics before aggregating (`SKILL.md:171-179`).
- Despite that prose barrier, in the #1681 / PR #1689 run a late-arriving **Adversary** critic measured (on a live transcript) that the shipped `mtime`-advancement freshness guard was defeated ~34% of tool-using turns — a measured **BLOCKER** — but the finding landed *after* the verdict was already recorded as "READY TO BUILD, 0 blockers." The bug shipped into a green PR and had to be patched post-build (`c478c13d`).
- The supervisor also reported the critique stage **stalled 3× before** in the same run: the driving subagent returned before any critic completed, so no verdict was recorded at all and the loop re-dispatched.

**Desired outcome:**
A critique run **cannot** record a verdict while any spawned critic is still running. The barrier is structural (enforced by the harness), not advisory. Either every critic's findings are gathered before synthesis, or the stage fails loudly — never an empty/partial verdict.

## Freshness Check

**Baseline commit:** `0d000e59cf39304b0861e93240d5623aad6f43f3`
**Issue filed at:** 2026-06-15T04:00:18Z
**Disposition:** Minor drift

**File:line references re-verified:**
- `.claude/skills-global/do-plan-critique/SKILL.md:154` — "Use `run_in_background: true` for all six" — **still holds** (and the count is stale: seven critics now run, see CRITICS.md §"Critic Selection").
- `.claude/skills-global/do-plan-critique/SKILL.md:171-179` — Step 3.5 "Wait and Collect" prose barrier — **still holds**. This is the v1.3.0 fix that the issue's run post-dates: the barrier was already present and still failed.
- `.claude/skills-global/do-plan-critique/CRITICS.md:7` — "spawned as a parallel Agent tool call with `run_in_background: true`" — **still holds**.
- `.claude/skills-global/do-plan-critique/SKILL.md:253-294` — Step 5.5 (verdict record + completion marker) and Step 5.6 (plan_revising lock) — **still holds**, unchanged by this work.

**Cited sibling issues/PRs re-checked:**
- #1681 — closed 2026-06-15T06:58:22Z; PR #1689 merged 2026-06-15T06:58:21Z. This is the run where the BLOCKER was dropped. Both landed *after* the v1.3.0 prose barrier (commit `6e943ea9`, 2026-06-13 02:05), confirming the prose barrier does not hold.
- #1654 — closed 2026-06-12T19:05:44Z. Added Step 3.5 prose "Wait and Collect" and the mandatory Step 5.5 finalize block. **This is the fix that proved insufficient** — it removed the "stall → no verdict" path *in prose* but left the spawn pattern fire-and-forget, so the LLM driver could still return early.

**Commits on main since issue was filed (touching referenced files):** None. The critique skill files are unchanged since `0d04f4ac` (2026-06-13).

**Active plans in `docs/plans/` overlapping this area:**
- `docs/plans/sdlc-plan-critique-revision.md` — concerns the revision *routing* after critique, not the critic-await barrier. No overlap with this fix.
- `docs/plans/pm-skips-critique-and-review.md` — concerns the router skipping critique entirely, not the in-skill barrier. No overlap.

**Notes:** The issue says "six" critics; the skill actually runs **seven** (Consistency Auditor was added). The fix must cover all dispatched critics, not a hardcoded six. The issue's "v1.3.0 already has a wait-and-collect step" is the *reason* this is not a duplicate: the prose barrier exists and demonstrably failed in a post-v1.3.0 run. The fix replaces the spawn mechanism, not the prose.

## Prior Art

- **#1654** ("war-room never finalizes: critics complete but verdict stays in_progress"): Added the Step 3.5 "Wait and Collect" prose barrier and the mandatory Step 5.5 finalize block (commit `6e943ea9`, v1.3.0, 2026-06-13). Outcome: removed the stall *in prose* but the BLOCKER-drop recurred in the #1681 run the next day. Directly relevant — this plan supersedes the spawn-mechanism half of that fix.
- **#1671 / #1672 / PR #1673** (commit `0d04f4ac`): Hardened Step 5.5 session resolution (`--issue-number` beats env-var on writes). Adjacent to verdict recording but orthogonal to the await barrier. Must not regress: the fix keeps Step 5.5 intact.
- No prior issue attempted to change the **spawn pattern** from `run_in_background` to a foreground await-all barrier. This is the first such attempt.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| #1654 (v1.3.0) | Added Step 3.5 prose: "you MUST wait and collect … block on every background critic before aggregating." | The barrier is **advisory text aimed at an LLM**, not a harness-enforced gate. `run_in_background: true` makes the Agent results retrievable later but does not *require* the driver to retrieve them before continuing. A driver that returns early (or that the supervisor resumes early) skips Steps 4/5/5.5 entirely or synthesizes on a partial set. Prose cannot enforce a synchronization invariant that the tool semantics leave optional. |

**Root cause pattern:** The synchronization point relies on the LLM *choosing* to block, when the tool-call lifecycle should *force* it to block. The repo already has a structural mechanism for exactly this — `do-sdlc/SKILL.md:70` dispatches parallel subagents "in one message so they run concurrently," and the harness does not return control to the model until **all** tool calls in that message resolve. Foreground Agent calls issued in a single assistant message are a hard await-all barrier; `run_in_background` is not. The fix is to switch the spawn pattern to the foreground-batch pattern the rest of the pipeline already uses.

## Architectural Impact

- **New dependencies:** None. The fix is skill-prose (`.claude/skills-global/do-plan-critique/`) plus one regression test.
- **Interface changes:** None to any Python API. The change is to *how* the critique skill instructs the Agent tool to spawn critics (foreground batch vs. `run_in_background`).
- **Coupling:** Decreases coupling — removes the implicit dependency on the driver/supervisor cooperating with a "please await" instruction.
- **Data ownership:** Unchanged. Step 5.5 (verdict record) and Step 5.6 (plan_revising lock) remain the sole owners of verdict/marker state.
- **Reversibility:** Trivially reversible — revert the skill edits and the test.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0-1 (the approach is settled by the issue; only the "fail loudly" wording may need a sign-off)
- Review rounds: 1 (war-room critique of this very plan, then standard PR review)

## Prerequisites

No prerequisites — this work modifies skill markdown and adds one pytest file; no external services or secrets are required.

## Solution

### Key Elements

- **Foreground await-all spawn (replaces `run_in_background`)**: All critics are dispatched as **foreground Agent calls in a single assistant message**. The harness does not return control to the driver until every critic call resolves, so the await-all barrier is structural — the driver physically cannot synthesize until all critic results are in hand. This is the same pattern `do-sdlc/SKILL.md:70` already uses for parallel-safe stage pairs.
- **Count-match assertion (N dispatched ⇒ N collected)**: Step 3.5 becomes an explicit reconciliation: the number of critic results in hand must equal the number of critics dispatched (the critic roster from CRITICS.md, currently seven; six if Archaeologist + User are skipped for a Small purely-internal plan). A mismatch is a loud failure, not a silent proceed.
- **Fail-loud on missing critic**: If any critic returns nothing retrievable, the skill re-dispatches that one critic (again foreground) and re-checks the count. The skill never aggregates a partial set and never returns an empty verdict — if it cannot reach a full roster after a re-dispatch, it emits an explicit error rather than recording a verdict.
- **Regression test on skill prose**: A unit test asserts the skill text encodes the barrier — no `run_in_background: true` in the critic-spawn instructions, presence of the foreground-batch + count-match language — modeled on `tests/unit/test_do_patch_ticks.py`.

### Flow

Critique invoked → read plan + context (Steps 1-1.5) → structural checks (Step 2) → **dispatch all critics as foreground Agent calls in one message (Step 3)** → harness blocks until every critic resolves → **count-match reconciliation: N collected == N dispatched, else re-dispatch the missing one (Step 3.5)** → aggregate + dedup (Step 4) → report (Step 5) → record verdict + marker (Step 5.5) → set plan_revising lock if needed (Step 5.6).

### Technical Approach

- **Switch the spawn instruction in three places**, all in `.claude/skills-global/do-plan-critique/`:
  1. `SKILL.md` Step 3 (line ~154): replace "Use `run_in_background: true` for all six" with the foreground-batch instruction — issue all critic Agent calls in a **single message** so they run concurrently and the harness awaits all before the driver continues. Correct the stale "six" to "all critics in the roster (Step 3 of CRITICS.md)."
  2. `SKILL.md` Step 3.5 (lines ~171-179): rewrite from "block on background agents / poll TaskOutput" to a **count-match reconciliation barrier** — the foreground batch already guarantees all results are present; Step 3.5's job becomes asserting `len(results) == len(dispatched_critics)` and re-dispatching any critic that returned nothing retrievable. Keep the "never aggregate a partial set" and "fail loudly, never return empty" guarantees explicit.
  3. `CRITICS.md` "How to Spawn" (line 7): replace "spawned as a parallel Agent tool call with `run_in_background: true`" with "spawned as foreground Agent tool calls issued together in a single message (concurrent execution, harness-enforced await-all)."
- **Keep Steps 5/5.5/5.6 untouched** — verdict record, completion marker co-location, and plan_revising lock semantics are correct and must not regress.
- **Update the repo addendum** `docs/sdlc/do-plan-critique.md` §"Wait-and-Collect" to describe the foreground await-all barrier instead of the background-poll barrier.
- **Bump the version history** in `SKILL.md` to a new entry referencing #1690.
- **Add a regression test** `tests/unit/test_do_plan_critique_barrier.py` asserting the skill prose encodes the barrier (see Test Impact).

**Why foreground-batch and not `parallel(...)`/explicit gather:** The Agent tool in this harness has no `parallel()` primitive; the canonical concurrency idiom (per `do-sdlc/SKILL.md:70` and the system reminder "When you launch multiple agents for independent work, send them in a single message … so they run concurrently") *is* the single-message foreground batch. That batch is precisely an await-all: the model cannot emit its next turn until every tool_result returns. This is the strongest barrier available without inventing new tooling.

## Failure Path Test Strategy

### Exception Handling Coverage
- No exception handlers in scope — this is skill-markdown prose plus a prose-assertion test. There is no Python `try/except` being added or modified.

### Empty/Invalid Input Handling
- The relevant "empty input" hazard is a **critic returning nothing**. The plan handles this explicitly: Step 3.5's count-match reconciliation detects a missing critic and forces a foreground re-dispatch; the skill never proceeds to aggregation (Step 4) on a partial set, and never records an empty verdict at Step 5.5. The regression test asserts the prose mandates re-dispatch-on-empty and "never return empty."

### Error State Rendering
- The user-visible failure path is "a critic could not be collected." The skill must surface this as an explicit loud failure (re-dispatch, then error if still short), not a silently-recorded verdict. The regression test asserts the "fail loudly / never returns empty" language is present in Step 3.5.

## Test Impact

- [ ] `tests/unit/test_do_plan_critique_barrier.py` — CREATE: assert the critique skill prose encodes the await-all barrier — (a) no `run_in_background: true` in the critic-spawn instructions of `SKILL.md` or `CRITICS.md`; (b) Step 3 instructs a single-message foreground batch; (c) Step 3.5 contains a count-match reconciliation ("N dispatched ⇒ N collected") and "never return empty / fail loudly" language; (d) Steps 5.5 and 5.6 are still present (no regression of verdict-record/marker/lock).
- No existing test reads `do-plan-critique/SKILL.md` — verified by grep over `tests/`. The change does not touch any Python module under test, so no existing unit/integration test asserts on behavior that this work alters. The SDLC router tests (`test_sdlc_router*.py`) consume the *recorded verdict*, which this fix does not change in shape — only its completeness — so they remain valid.

## Rabbit Holes

- **Do NOT try to convert the war-room into a Python orchestration module** with real `asyncio.gather`. The critique skill is an LLM-driven prose skill; rebuilding it as deterministic Python is a separate, much larger project (and would lose the per-critic LLM lens). The fix is a spawn-pattern change in prose, full stop.
- **Do NOT add a polling/timeout loop in prose** (e.g., "poll TaskOutput every N seconds"). That re-introduces the advisory-barrier weakness this plan removes. The foreground batch is the barrier; no polling is needed.
- **Do NOT touch the SDLC router's stale-critique handling** (#1639 row 2b/row 3). That is a separate, working mechanism; conflating it expands scope.
- **Do NOT renumber the critics or change critic lenses.** The count is dynamic (six or seven per CRITICS.md selection rules); the barrier must reconcile against the *actual* dispatched roster, not a hardcoded number.

## Risks

### Risk 1: Foreground critics run on `sonnet` and a single-message batch of 7 is slower wall-clock than fire-and-forget
**Impact:** The critique stage takes longer before returning (all critics must finish before the driver continues, by design).
**Mitigation:** This is the intended trade-off — correctness over latency. Critics are `model: sonnet` with focused 0-3-finding prompts, so each is fast; running them concurrently in one batch keeps total latency at roughly the slowest critic, not the sum. The pre-fix "fast" path was fast precisely because it dropped findings.

### Risk 2: A future harness change could make single-message Agent calls non-blocking
**Impact:** The structural barrier would silently weaken.
**Mitigation:** The regression test asserts the prose mandates the foreground-batch pattern and the count-match reconciliation. If the harness semantics ever change, the count-match step (N dispatched ⇒ N collected, else fail loudly) is the second line of defense and would catch a partial set regardless of how the calls were spawned.

## Race Conditions

### Race 1: Verdict recorded before all critic findings are collected (the bug)
**Location:** `.claude/skills-global/do-plan-critique/SKILL.md` Steps 3 → 3.5 → 5.5
**Trigger:** Critics spawned with `run_in_background: true`; driver proceeds to Step 4/5/5.5 (verdict record) before late critics finish, dropping their findings.
**Data prerequisite:** All dispatched critics' findings must be in hand before aggregation (Step 4) reads them.
**State prerequisite:** The driver must not be able to emit a verdict-record turn (Step 5.5) until the critic-results set is complete.
**Mitigation:** Replace `run_in_background` with a single-message foreground Agent batch — the harness blocks the driver's next turn until every critic tool_result returns (structural await-all). Step 3.5 then asserts `len(collected) == len(dispatched)` and re-dispatches any empty critic before aggregation. This makes "verdict before findings complete" structurally impossible, with the count-match assertion as a backstop.

## No-Gos (Out of Scope)

Nothing deferred — every relevant item is in scope for this plan: the await-all barrier, the count-match reconciliation, the fail-loud-on-empty path, the addendum-doc update, and the regression test.

This plan changes only the in-skill critic barrier. It deliberately does not touch the SDLC router's stale-critique dead-end handling (row 2b / row 3, fixed under #1639) — that is a working, separately-owned mechanism, not deferred work. No follow-up issue is implied or required.

## Update System

No update system changes required beyond the standard skill sync. `.claude/skills-global/do-plan-critique/` is hardlinked to `~/.claude/skills/` on every machine by `scripts/update/hardlinks.py::sync_claude_dirs()` automatically — editing the existing files in place needs no new registration, no `RENAMED_REMOVALS` entry (nothing is renamed or moved), and no new dependency. The next `/update` propagates the edited SKILL.md / CRITICS.md to every machine.

## Agent Integration

No agent integration required — this is a skill-prose change to an existing global skill plus a unit test. The critique skill is already invoked through the SDLC pipeline (`/sdlc` → `/do-plan-critique`); no new CLI entry point in `pyproject.toml`, no `.mcp.json` change, and no new bridge import. The regression test (`tests/unit/test_do_plan_critique_barrier.py`) is the integration check that the skill text encodes the barrier the pipeline relies on.

## Documentation

### Feature Documentation
- [ ] Update `docs/sdlc/do-plan-critique.md` §"Wait-and-Collect + Mandatory Finalize (#1654)" to describe the **foreground await-all batch** barrier and the **count-match reconciliation** (N dispatched ⇒ N collected), replacing the background-poll description. Add a reference to #1690.
- [ ] Update the `## Version history` block in `.claude/skills-global/do-plan-critique/SKILL.md` with a new entry (e.g. v1.4.0) referencing #1690: "Replace fire-and-forget `run_in_background` critic spawn with a single-message foreground await-all batch + count-match reconciliation; verdict can no longer record before all critics finish."

### External Documentation Site
- No external docs site for this repo's skills.

### Inline Documentation
- [ ] The skill prose IS the documentation; ensure Step 3, Step 3.5, and CRITICS.md "How to Spawn" read coherently after the edit (no dangling references to "background agents" or `TaskOutput` polling).

## Success Criteria

- [ ] No `run_in_background: true` remains in the critic-spawn instructions of `SKILL.md` Step 3 or `CRITICS.md` "How to Spawn" (verified by grep / by the new test).
- [ ] `SKILL.md` Step 3 instructs a single-message foreground Agent batch for all critics in the roster.
- [ ] `SKILL.md` Step 3.5 contains a count-match reconciliation ("N dispatched ⇒ N collected") and explicit "never aggregate a partial set / never return empty / fail loudly" language.
- [ ] `SKILL.md` Steps 5.5 (verdict record + co-located completion marker) and 5.6 (plan_revising lock) are unchanged and still present.
- [ ] `docs/sdlc/do-plan-critique.md` §Wait-and-Collect describes the foreground await-all barrier + count-match, referencing #1690.
- [ ] `tests/unit/test_do_plan_critique_barrier.py` exists and passes, asserting the four prose invariants above.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (critique-barrier)**
  - Name: critique-barrier-builder
  - Role: Edit the three spawn-instruction sites in `do-plan-critique/` and the repo addendum; bump the skill version history.
  - Agent Type: builder
  - Resume: true

- **Builder (barrier-test)**
  - Name: barrier-test-builder
  - Role: Write `tests/unit/test_do_plan_critique_barrier.py` modeled on `test_do_patch_ticks.py`.
  - Agent Type: test-engineer
  - Resume: true

- **Validator (barrier)**
  - Name: barrier-validator
  - Role: Verify all Success Criteria — grep for absent `run_in_background`, presence of foreground-batch + count-match prose, Steps 5.5/5.6 intact, test passes.
  - Agent Type: validator
  - Resume: true

### Available Agent Types

(Standard roster — builder, test-engineer, validator, documentarian used here.)

## Step by Step Tasks

### 1. Switch critic spawn to foreground await-all batch
- **Task ID**: build-spawn-pattern
- **Depends On**: none
- **Validates**: tests/unit/test_do_plan_critique_barrier.py (create)
- **Assigned To**: critique-barrier-builder
- **Agent Type**: builder
- **Parallel**: true
- In `.claude/skills-global/do-plan-critique/SKILL.md` Step 3 (~line 154): replace the `run_in_background: true` instruction with a single-message foreground Agent batch ("issue all critic Agent calls together in one message; the harness awaits all before you continue"). Fix the stale "six" to "all critics in the roster (CRITICS.md Critic Selection)".
- In `.claude/skills-global/do-plan-critique/CRITICS.md` "How to Spawn" (line 7): replace "parallel Agent tool call with `run_in_background: true`" with the foreground single-message-batch wording.
- Add a `## Version history` entry (v1.4.0) in SKILL.md referencing #1690.

### 2. Rewrite Step 3.5 as a count-match reconciliation barrier
- **Task ID**: build-step-3-5
- **Depends On**: build-spawn-pattern
- **Validates**: tests/unit/test_do_plan_critique_barrier.py (create)
- **Assigned To**: critique-barrier-builder
- **Agent Type**: builder
- **Parallel**: false
- Rewrite `SKILL.md` Step 3.5 (~lines 171-179): remove "background agents / poll / TaskOutput" language; state that the foreground batch already guarantees all results are present, so Step 3.5 asserts `len(collected) == len(dispatched)` and re-dispatches (again foreground) any critic that returned nothing retrievable.
- Keep explicit: "never aggregate a partial set", "never return an empty verdict", "fail loudly if the full roster cannot be reached after re-dispatch".
- Confirm Steps 5.5 and 5.6 remain unchanged.

### 3. Write the regression test
- **Task ID**: build-test
- **Depends On**: none
- **Validates**: tests/unit/test_do_plan_critique_barrier.py
- **Assigned To**: barrier-test-builder
- **Agent Type**: test-engineer
- **Parallel**: true
- Create `tests/unit/test_do_plan_critique_barrier.py` modeled on `tests/unit/test_do_patch_ticks.py` (read SKILL.md/CRITICS.md text, assert prose invariants).
- Assert: (a) `run_in_background: true` absent from the critic-spawn sections; (b) Step 3 mandates a single-message foreground batch; (c) Step 3.5 contains count-match + "never return empty / fail loudly" language; (d) "### Step 5.5" and "### Step 5.6" headers still present.

### 4. Update the repo addendum doc
- **Task ID**: document-addendum
- **Depends On**: build-spawn-pattern, build-step-3-5
- **Assigned To**: critique-barrier-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/sdlc/do-plan-critique.md` §"Wait-and-Collect + Mandatory Finalize" to describe the foreground await-all batch + count-match reconciliation, referencing #1690.

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-step-3-5, build-test, document-addendum
- **Assigned To**: barrier-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_do_plan_critique_barrier.py -q` → exit 0.
- Grep-confirm no `run_in_background` in the critic-spawn sections.
- Verify all Success Criteria, including Steps 5.5/5.6 intact and addendum updated.
- Generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Barrier test passes | `pytest tests/unit/test_do_plan_critique_barrier.py -q` | exit code 0 |
| No background spawn in critic instructions | `grep -n 'run_in_background' .claude/skills-global/do-plan-critique/SKILL.md .claude/skills-global/do-plan-critique/CRITICS.md` | output contains no `run_in_background: true` in Step 3 / How-to-Spawn (only the version-history line may reference the removed pattern) |
| Step 3.5 count-match present | `grep -in 'dispatched' .claude/skills-global/do-plan-critique/SKILL.md` | output > 0 |
| Steps 5.5/5.6 intact | `grep -c '### Step 5.5\|### Step 5.6' .claude/skills-global/do-plan-critique/SKILL.md` | output > 1 |
| Lint clean | `python -m ruff check tests/unit/test_do_plan_critique_barrier.py` | exit code 0 |
| Format clean | `python -m ruff format --check tests/unit/test_do_plan_critique_barrier.py` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **"Fail loudly" surface:** When the full critic roster genuinely cannot be reached after one foreground re-dispatch, should the skill (a) record a `MAJOR REWORK`-equivalent loud error verdict that the router treats as a stop, or (b) exit non-zero with no verdict and let the router's stale-critique path re-route? The plan assumes the stage emits an explicit error rather than any verdict (acceptance criterion 3: "fails loudly, never returns empty"), but the exact router contract for that error is worth a one-line confirmation.
2. **Critic count assertion granularity:** The roster is six or seven depending on CRITICS.md's "skip Archaeologist + User for Small purely-internal plans" rule. Should Step 3.5 reconcile against the *actually-dispatched* set (recommended) rather than a fixed number? The plan assumes yes — confirm there's no desire to always force all seven.
