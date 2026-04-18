---
status: docs_complete
type: chore
appetite: Medium
owner: Valor Engels
created: 2026-04-18
tracking: https://github.com/tomcounsell/ai/issues/1042
last_comment_id:
revision_applied: true
---

# SDLC Skills Audit: Close the Five Blind Spots

## Problem

The SDLC pipeline exists to prevent bugs from shipping, but observable bugs ship anyway. PR #1039 (two-tier no-progress detector) exposed five recurring patterns where each layer's blindness allows the next layer's bugs to pass through. The compound effect is that a bug can persist indefinitely with no automated signal.

**Current behavior:** Exception handlers swallow errors silently; integration tests confirm fixture-world rather than production-world; plan documents contain internal contradictions that survive two critique passes; the full test suite is not gating PRs (71 failures on `main`); and LLM-based verifiers produce different verdicts on identical input.

**Desired outcome:** Each of the five patterns has either a merged skill change that closes the gap or a filed sub-issue with a concrete next step. This is a skill-audit-and-harden chore, not a feature.

## Freshness Check

**Baseline commit:** `ecba01888c1f0498f9c8ce51e510bc7c78e0ee2a`
**Issue filed at:** 2026-04-18T08:39:02Z
**Disposition:** Unchanged — issue was filed today, no commits have landed on main since filing.

**File:line references re-verified:**
- `agent/agent_session_queue.py:2146-2147` — broad `except Exception` block for orphan repair — **line numbers drifted** (file is now 5461 lines); the pattern still holds but at a different location. The orphan repair `except Exception as inner:` with `logger.error` is confirmed present. Current search shows 449 `except Exception` occurrences in agent/ bridge/ monitoring/ combined.
- `tests/integration/test_session_zombie_health_check.py:119` — fixture-world integration test — **still present** at approximately line 119; creates `AgentSession` objects in-process without round-tripping through Redis serialization.
- `.claude/skills/do-plan-critique/CRITICS.md` — six critic personas, none with explicit "internal consistency" charter — **confirmed unchanged**, all six critics (Skeptic, Operator, Archaeologist, Adversary, Simplifier, User) lack cross-section consistency checking.
- `.claude/skills/do-test/SKILL.md` — Exception Swallow Scan is a post-hoc quality check, NOT a blocking gate — **confirmed**. It lives in "Quality Checks (Post-Test)" section, runs after tests pass, and does not gate the pipeline.
- `.claude/commands/do-merge.md` — merge gate checks TEST/REVIEW/DOCS stage markers but NOT full suite results on `main` — **confirmed**. The fallback `pytest tests/ -x -q` only triggers if the gate check script fails entirely.

**Cited sibling issues/PRs re-checked:**
- #1040 — OPEN: "SDLC router oscillates between critique/review stages with non-deterministic verdicts"
- #1041 — OPEN: "Test-suite debt: 60 failures + 11 errors on main (8 clusters, 0 regressions from #1036)"
- PR #1039 — MERGED 2026-04-18: "feat(health): two-tier no-progress detector (#1036)" — this is the retrospective trigger

**Commits on main since issue was filed:** None (issue filed today).

**Active plans in `docs/plans/` overlapping this area:** `sdlc-review-loop-guard.md` (mentions #1042 as adjacent but non-overlapping), `sdlc-router-oscillation-guard.md` (addresses #1040, not #1042 patterns).

**Notes:** Line numbers in `agent_session_queue.py` have drifted significantly — builders should search by symbol/context rather than relying on line numbers from the issue.

## Prior Art

- **PR #866** — "Retroactive SDLC audit: catalog gaps across merged PRs, deduplicate, and ship targeted fixes" (merged 2026-04-10) — a prior full SDLC audit; found and addressed architectural gaps but did not target the five specific patterns in this issue. No overlap.
- **PR #815** — "feat(sdlc): propagation check, Implementation Note field, and concern-triggered revision pass" (merged 2026-04-07) — addressed plan→build consistency (Propagation Check in `/do-plan`), partially relevant to Pattern 3.
- **Issue #697** — "Fix happy path testing pipeline" (closed) — addressed test pipeline gaps in a different area; not directly relevant.

No prior issues or PRs specifically addressed: (a) Exception Swallow Scan as a mandatory gate, (b) integration-test serialization boundary enforcement, (c) Internal Consistency critic persona, or (d) deterministic checklist for LLM verifiers.

## Research

No relevant external findings — this is purely internal SDLC skill editing with no external library or API dependencies. Proceeding with codebase context.

## Spike Results

### spike-1: Does `do-test` currently block on Exception Swallow Scan results?
- **Assumption**: "The scan might already gate the pipeline in a way not immediately obvious from the SKILL.md"
- **Method**: code-read
- **Finding**: The Exception Swallow Scan lives in `## Quality Checks (Post-Test)` in `do-test/SKILL.md`. It runs after tests pass and reports findings but does not emit a `status: fail` OUTCOME. It is strictly advisory.
- **Confidence**: high
- **Impact on plan**: Pattern 1 fix is definitively needed — the scan must be promoted to a blocking gate with a clear fail condition.

### spike-2: Does `do-plan-critique` have any cross-section consistency checking anywhere?
- **Assumption**: "An Internal Consistency check might already exist in a sub-skill or hidden step"
- **Method**: code-read
- **Finding**: `CRITICS.md` defines exactly six personas. None of their "LOOK FOR" checklists includes cross-section claim verification. No sub-file adds consistency checking. The gap is confirmed.
- **Confidence**: high
- **Impact on plan**: Pattern 3 fix is to add a seventh critic persona: **Consistency Auditor** with explicit cross-section checking charter.

### spike-3: Does `do-pr-review` emit a structured deterministic checklist alongside its prose verdict?
- **Assumption**: "A YAML checklist might exist in sub-skills even if not in the main SKILL.md"
- **Method**: code-read (sub-skills: checkout.md, code-review.md, post-review.md, screenshot.md)
- **Finding**: `post-review.md` formats findings as a markdown comment block with `## Review: Approved` or `## Review: Changes Requested` headings. The verdict is prose + an inline `<!-- OUTCOME ... -->` tag. No structured per-item checklist exists. Each run's salience drives what surfaces.
- **Confidence**: high
- **Impact on plan**: Pattern 5 fix is to add a **mandatory pre-verdict checklist** in `code-review.md` — a fixed set of items that must be evaluated (and marked pass/fail) before the LLM writes its verdict.

## Data Flow

This plan touches SDLC skill text files (`.claude/skills/` and `.claude/commands/`), not runtime code paths. No data-flow trace is required for skill-doc edits.

## Architectural Impact

- **New dependencies**: None — all changes are to skill markdown files and one Python hook.
- **Interface changes**: The OUTCOME contract format from `do-test` will gain a new field (`swallow_gate`). The `do-plan-critique` OUTCOME will gain an Internal Consistency section. The `do-pr-review` structured comment will gain a checklist table.
- **Coupling**: No coupling changes — all edits are additive to existing skill docs.
- **Reversibility**: Skill doc changes are trivially reversible with a git revert.

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1 (scope alignment before build)
- Review rounds: 1

This is five targeted edits to skill markdown files plus one Python validator hook. The bottleneck is getting each change right — not code volume.

## Prerequisites

No prerequisites — all changes are to skill documentation files and one Python hook; no external dependencies.

## Solution

### Key Elements

- **Pattern 1 — Exception Swallow Gate**: Promote the Exception Swallow Scan from advisory to blocking in `do-test/SKILL.md`. Define a clear fail condition: any new `except Exception` block in `agent/` or `bridge/` that neither re-raises nor emits a metric/log must fail the TEST stage.
- **Pattern 2 — Serialization Boundary Critic**: Add a serialization-boundary challenge to `do-plan-critique/CRITICS.md` (as an extension to the existing Skeptic or as a new inline check). Plans that name integration tests must be challenged: do those tests round-trip through the persistence/serialization layer?
- **Pattern 3 — Internal Consistency Critic**: Add a seventh critic persona to `do-plan-critique/CRITICS.md`: the **Consistency Auditor**. Its charter: detect contradictions between spike findings, step-by-step tasks, and success criteria within the same plan.
- **Pattern 4 — Full Suite Gate in do-merge**: Add a full-suite check to `do-merge.md`'s Prerequisites Check. Gate on whether `pytest tests/` passes on `main` at merge time. Define handling for pre-existing failures on `main` (the "red-main recovery" path).
- **Pattern 5 — Deterministic Pre-Verdict Checklist**: Add a fixed checklist to `do-pr-review/sub-skills/code-review.md` that must be evaluated before writing the verdict. Each item gets a binary pass/fail. The verdict summary MUST include this completed checklist.

### Technical Approach

All five changes are additive edits to skill markdown files:

1. **`do-test/SKILL.md`** — Add an "Exception Swallow Gate" step immediately before the OUTCOME emission. The gate scans the diff (not the full codebase) for new `except Exception` blocks. Any new block that passes the `grep -Ev "logger|log\.|warning|error|raise"` filter (note: `-E` flag required for alternation) is a gate failure. Emit `status: fail` if gate fails. Add a carve-out for shutdown/cleanup patterns via an inline `# swallow-ok: {reason}` comment convention — the reason string must be at least 10 characters (use `grep -E "# swallow-ok: .{10,}"` to detect valid carve-outs; whitespace-only or single-character reasons do NOT pass).
2. **`do-plan-critique/CRITICS.md`** — Add the Consistency Auditor as critic #7. Charter: cross-check every spike finding against every task step; cross-check every architecture claim against every success criterion; flag any pair of statements that contradict each other.
3. **`do-plan-critique/CRITICS.md`** — Extend the Skeptic's "LOOK FOR" list with a serialization-boundary item: "Integration tests named in the plan — do they round-trip through Redis/persistence? Or do they create objects in-memory and call methods directly? In-memory-only is a unit test, not an integration test."
4. **`do-merge.md`** — After the Lockfile Sync Check, add a Full Suite Gate: run `pytest tests/ -x -q --tb=no` on the PR branch (already checked out). Compare failures against the stored baseline in `data/main_test_baseline.json`. If the baseline file exists, failures appearing in the baseline are pre-existing (non-blocking); failures NOT in the baseline are new regressions (blocking). If the baseline file does not exist, treat all failures as regressions. After a clean merge, write the current failure list back to `data/main_test_baseline.json` (post-merge write step). If `main` is already red, fork to a red-main recovery path: log the pre-existing failures, allow the green PR to merge (do not block clean PRs because of pre-existing red baseline), document them in the PR comment.
5. **`do-pr-review/sub-skills/code-review.md`** — Add a mandatory Pre-Verdict Checklist section (12 fixed items) that the reviewer must evaluate for every PR. The checklist is emitted as a structured markdown table in the review comment, alongside the prose verdict. "Approved" requires the checklist to be complete — not just "LLM says approved."

## Failure Path Test Strategy

### Exception Handling Coverage
- [x] Pattern 1 change is itself about exception handling — the new gate must not be bypassable by an empty diff (no new files touched)
- [x] The `# swallow-ok:` carve-out must require a non-empty reason string; bare `# swallow-ok:` should not pass the gate
- [x] A `# swallow-ok:` with fewer than 10 characters (e.g. `# swallow-ok: x` or whitespace-only) does not pass the gate — only reasons of 10+ characters qualify

### Empty/Invalid Input Handling
- [x] The Exception Swallow Gate grep must handle files with no `except` blocks without error (zero matches = pass)
- [x] The Consistency Auditor must handle plans with no spike sections gracefully (skip cross-check if section missing)

### Error State Rendering
- [x] If the full-suite gate fails in do-merge, the blocking message must include the number of failures and the first failure name — not just "tests failed"

## Test Impact

These changes are all to skill markdown files and one Python validator hook. No existing Python tests are directly affected. However:

- [x] `tests/unit/test_skill_docs.py` (if it exists) — CHECK: verify no test asserts on the old CRITICS count or do-test OUTCOME format. Run `grep -rn "CRITICS\|Exception Swallow\|do-merge" tests/` to find any. Checked: no assertions on CRITICS count or Exception Swallow format found.
- [x] `.claude/hooks/validators/` — the new `validate_exception_swallow_gate.py` hook is new code; it needs a unit test in `tests/unit/test_hooks.py` or equivalent. N/A: gate implemented as skill doc prose, not a Python hook — no new hook created.

No existing integration or e2e tests are affected — skill docs are not exercised by the test suite directly.

## Rabbit Holes

- **Rewriting all six existing critics**: The Consistency Auditor is additive. Do NOT refactor the six existing critics while adding the seventh — that's scope creep with high regression risk.
- **Automated full-suite CI**: Wiring pytest to GitHub Actions CI is a separate infrastructure project (#1041 tracks this). The plan here is scoped to the `/do-merge` skill only.
- **Memoizing LLM verdicts by commit SHA** (proposed in #1040 resolution path): This overlaps with the oscillation fix in #1040. Do NOT implement verdict memoization here — coordinate with that issue instead.
- **Fixing the 71 failures on main**: That's #1041's scope. This plan only adds the gate that *blocks new merges when main is green*. Fixing the existing red baseline is out of scope.
- **Classifying all 449 existing `except Exception` blocks**: The gate only scans the *diff* (new blocks introduced by the PR). Backfilling is a separate chore.

## Risks

### Risk 1: Full Suite Gate makes do-merge too slow
**Impact:** Adding `pytest tests/` (~7 minutes) to the merge gate increases pipeline wall-clock time significantly.
**Mitigation:** The gate runs `pytest tests/ -x -q --tb=no` (fail-fast, quiet) on `main` — not the full verbose suite. If `main` is already green, the run exits quickly. If `main` is red, the gate should skip (per the red-main recovery path) rather than block.

### Risk 2: Exception Swallow Gate produces false positives
**Impact:** The grep-based scan may flag legitimate shutdown-safe handlers, frustrating builders.
**Mitigation:** The `# swallow-ok: {reason}` carve-out comment convention lets builders annotate intentional swallows inline. The gate only scans the diff, not the whole codebase.

### Risk 3: Consistency Auditor adds noise without signal
**Impact:** A seventh critic that fires on every minor plan produces review fatigue.
**Mitigation:** The Consistency Auditor's "DO NOT flag" list must be well-specified: no findings for plans with no spikes, no findings for stylistic inconsistencies (only semantic contradictions).

## Race Conditions

No race conditions identified — all changes are to skill documentation files and one Python hook running in a single-process scan context.

## No-Gos (Out of Scope)

- Do NOT rewrite or refactor the existing six critic personas
- Do NOT implement verdict memoization by commit SHA (that belongs to #1040)
- Do NOT fix the 71 existing test failures on `main` (that belongs to #1041)
- Do NOT close #1040 or #1041 as part of this plan — they remain independent tracking issues
- Do NOT add full-suite CI to GitHub Actions — scope is the `/do-merge` skill only
- Do NOT add a serialization-boundary enforcement in the Python test harness — scope is skill doc prose only

## Update System

No update system changes required — all changes are to skill documentation files (`.claude/skills/`) and one Python hook (`.claude/hooks/validators/`). These are part of the Claude Code config that is already synced via git pull on each machine.

## Agent Integration

No agent integration required — skill docs are read by Claude Code sessions directly; they are not exposed via MCP servers or the bridge.

## Documentation

- [x] Update `.claude/skills/do-plan-critique/CRITICS.md` with the Consistency Auditor persona (this IS the deliverable — no separate feature doc needed)
- [x] Update `.claude/skills/do-test/SKILL.md` with the Exception Swallow Gate (inline)
- [x] Update `.claude/commands/do-merge.md` with the Full Suite Gate section
- [x] Update `.claude/skills/do-pr-review/sub-skills/code-review.md` with the Pre-Verdict Checklist
- [x] Create `docs/features/sdlc-skills-audit.md` summarizing the five patterns and the fixes applied (post-merge retrospective doc)
- [x] Add entry to `docs/features/README.md` index table for the new retrospective doc

## Success Criteria

- [x] Pattern 1: Exception Swallow Scan in `do-test/SKILL.md` is a blocking gate (not advisory). New `except Exception` blocks without `# swallow-ok:` fail the TEST stage OUTCOME.
- [x] Pattern 2: Skeptic critic in `CRITICS.md` includes a serialization-boundary item in its "LOOK FOR" checklist.
- [x] Pattern 3: A seventh "Consistency Auditor" critic persona exists in `CRITICS.md` with an explicit cross-section consistency charter.
- [x] Pattern 4: `do-merge.md` includes a Full Suite Gate check that runs `pytest tests/ -x -q` on the target branch before merge. Red-main recovery path is documented.
- [x] Pattern 5: `do-pr-review/sub-skills/code-review.md` includes a Pre-Verdict Checklist (≥10 items) that must be evaluated before the verdict is written.
- [x] #1040 and #1041 remain open as independent tracking issues (not subsumed by this plan).
- [x] `docs/features/sdlc-skills-audit.md` exists and records the five patterns with their fix dispositions.
- [ ] Tests pass (`/do-test`)

## Team Orchestration

### Team Members

- **Builder (skill-docs)**
  - Name: skill-docs-builder
  - Role: Edit CRITICS.md, do-test SKILL.md, do-merge.md, code-review.md
  - Agent Type: builder
  - Resume: true

- **Builder (hook)**
  - Name: hook-builder
  - Role: Create validate_exception_swallow_gate.py hook (if needed as standalone validator)
  - Agent Type: builder
  - Resume: true

- **Validator (skill-docs)**
  - Name: skill-docs-validator
  - Role: Verify each skill doc change is self-consistent and does not break existing patterns
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Create docs/features/sdlc-skills-audit.md retrospective
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Add Consistency Auditor critic to CRITICS.md
- **Task ID**: build-critic-7
- **Depends On**: none
- **Validates**: manual review — no automated test for skill doc prose
- **Assigned To**: skill-docs-builder
- **Agent Type**: builder
- **Parallel**: true
- Read `.claude/skills/do-plan-critique/CRITICS.md` in full
- Add critic #7: **Consistency Auditor** after critic #6 (User)
- Open the critic definition with a scope differentiation paragraph: "SCOPE DIFFERENTIATION: The Propagation Check in `/do-plan` (PR #815) verifies that spike results are carried forward into task steps. This critic does NOT re-verify that. Instead, it checks for contradictions BETWEEN sections: does the No-Gos list contradict the Solution? Does a Success Criterion assume behavior the Technical Approach explicitly excludes? Does spike-N claim component A owns responsibility X while the Architecture section assigns X to component B?"
- Charter: detect contradictions between spike findings↔task steps, architecture claims↔success criteria, and any two sections that make incompatible assertions about the same component
- "LOOK FOR" list: spike finding contradicted by a task step; success criterion that assumes behavior the Technical Approach doesn't implement; two sections that name different components for the same responsibility; No-Gos that contradict items in the Solution
- "DO NOT flag" list: stylistic differences, prose inconsistencies, plans with no spike section (skip cross-check), plans under Small appetite (optional), spike↔task consistency (that is the Propagation Check's domain — do not duplicate it)
- Update critic selection note: "All seven critics run by default"

### 2. Extend Skeptic with serialization-boundary item
- **Task ID**: build-skeptic-extend
- **Depends On**: none
- **Validates**: manual review
- **Assigned To**: skill-docs-builder
- **Agent Type**: builder
- **Parallel**: true
- In the Skeptic's "LOOK FOR" list, add: "Integration tests named in the plan — do they round-trip through Redis/persistence/serialization? Tests that create Popoto models in-memory and call methods directly are unit tests, not integration tests. Flag if the plan names them as integration tests."

### 3. Promote Exception Swallow Scan to blocking gate in do-test
- **Task ID**: build-swallow-gate
- **Depends On**: none
- **Validates**: manual review + `grep -n "Exception Swallow Gate\|swallow-ok" .claude/skills/do-test/SKILL.md`
- **Assigned To**: skill-docs-builder
- **Agent Type**: builder
- **Parallel**: true
- Read `.claude/skills/do-test/SKILL.md` in full
- Move the Exception Swallow Scan from "Quality Checks (Post-Test)" into a new "Exception Swallow Gate" step that runs *before* the OUTCOME emission
- Gate logic: scan the diff (not the full codebase) for new `except Exception` blocks. For each new block: pass if it contains `logger`, `log.`, `warning`, `error`, or `raise`; pass if it has an inline `# swallow-ok: {reason}` comment where the reason is at least 10 non-whitespace characters (use `grep -E "# swallow-ok: .{10,}"` to detect valid carve-outs — bare `# swallow-ok:`, whitespace-only, or single-character reasons do NOT pass); fail otherwise. Use `grep -Ev "logger|log\.|warning|error|raise"` (the `-E` flag is required for alternation — without it `|` is treated as a literal character)
- If gate fails: emit `<!-- OUTCOME {"status":"fail","stage":"TEST","artifacts":{"swallow_gate":"failed","new_swallows":[...]}} -->`
- Document the `# swallow-ok: {reason}` convention in the gate section with example (e.g. `# swallow-ok: safe during shutdown, task already cancelled`)
- Keep the existing post-test advisory scan in "Quality Checks" for the full-codebase sweep (unchanged)

### 4. Add Full Suite Gate to do-merge
- **Task ID**: build-fullsuite-gate
- **Depends On**: none
- **Validates**: manual review + `grep -n "Full Suite Gate\|red-main" .claude/commands/do-merge.md`
- **Assigned To**: skill-docs-builder
- **Agent Type**: builder
- **Parallel**: true
- Read `.claude/commands/do-merge.md` in full
- After the Lockfile Sync Check section, add a new "Full Suite Gate" section:
  - Run `pytest tests/ -x -q --tb=no` on the PR branch (already checked out)
  - Load `data/main_test_baseline.json` if it exists. This file contains a `failing_tests` list written by the previous clean merge. Failures in the baseline are pre-existing (non-blocking). Failures NOT in the baseline are new regressions (blocking). If the baseline file does not exist, treat all failures as regressions.
  - If all tests pass: emit `FULL_SUITE: PASS` and update `data/main_test_baseline.json` to `{"failing_tests": []}` after merge
  - If tests fail AND all failures are pre-existing (in baseline): emit `FULL_SUITE: PASS (pre-existing N failures noted)` and log the failure names; proceed with merge
  - If tests fail AND new regressions exist (NOT in baseline): emit `FULL_SUITE: FAIL — N new regression(s): [list]` and set GATES_FAILED
  - Red-main recovery: if the baseline file does not exist and tests fail, document all failures in the PR comment, allow merge, and write the failure list to `data/main_test_baseline.json` as the new baseline (bootstrapping). Do not block clean PRs because of pre-existing red baseline.
  - After each clean merge: write current failure list (empty or baseline) back to `data/main_test_baseline.json`
- Note: this gate adds wall-clock time; the fail-fast `-x` flag minimizes it

### 5. Add Pre-Verdict Checklist to code-review.md
- **Task ID**: build-checklist
- **Depends On**: none
- **Validates**: `grep -c "Pre-Verdict Checklist" .claude/skills/do-pr-review/sub-skills/code-review.md`
- **Assigned To**: skill-docs-builder
- **Agent Type**: builder
- **Parallel**: true
- Read `.claude/skills/do-pr-review/sub-skills/code-review.md` in full
- Add a "Pre-Verdict Checklist" section that must be completed before writing the verdict
- Checklist items (exactly these, in order):
  1. All plan acceptance criteria — checked against diff
  2. No-Gos from plan — none violated
  3. New `except Exception` blocks — each has logger/raise/swallow-ok
  4. New integration tests — exercise serialization boundary (not in-memory only)
  5. Plan internal consistency — spike findings match task steps
  6. No hardcoded secrets or debug artifacts
  7. New public APIs — docstrings present
  8. Breaking changes — migration path documented
  9. Tests added for new behavior
  10. Tests cover the failure path (not just happy path)
  11. UI changes (if any) — screenshot captured
  12. Docs updated for user-facing changes
- The checklist must be emitted as a markdown table in the review comment with `PASS | FAIL | N/A` for each item
- "Approved" verdict requires all items to be evaluated (no blank entries). Items marked FAIL must become findings.

### 6. Validate all skill doc changes
- **Task ID**: validate-skill-docs
- **Depends On**: build-critic-7, build-skeptic-extend, build-swallow-gate, build-fullsuite-gate, build-checklist
- **Assigned To**: skill-docs-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify critic #7 exists and has "LOOK FOR" and "DO NOT flag" sections
- Verify Skeptic "LOOK FOR" contains serialization-boundary item
- Verify Exception Swallow Gate is before OUTCOME emission in do-test SKILL.md
- Verify `# swallow-ok:` convention is documented with example
- Verify Full Suite Gate section exists in do-merge.md with red-main recovery path
- Verify Pre-Verdict Checklist has exactly 12 items in code-review.md
- Verify no existing patterns were broken (do-merge gate flow still works end-to-end)

### 7. Write retrospective feature doc
- **Task ID**: document-feature
- **Depends On**: validate-skill-docs
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/sdlc-skills-audit.md` with:
  - Summary of the five patterns and their observed impact
  - One-line description of each fix applied (with skill file reference)
  - Decision: #1040 and #1041 remain independent tracking issues (not subsumed)
  - Date shipped and PR number
- Add entry to `docs/features/README.md` index table

### 8. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: skill-docs-validator
- **Agent Type**: validator
- **Parallel**: false
- Confirm all success criteria checkboxes are met
- Verify docs/features/sdlc-skills-audit.md exists
- Run `grep -rn "CRITICS\|Exception Swallow\|do-merge" tests/` to confirm no existing tests assert on changed patterns
- Report final pass/fail

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Consistency Auditor exists | `grep -c "Consistency Auditor" .claude/skills/do-plan-critique/CRITICS.md` | output > 0 |
| Skeptic serialization item | `grep -c "serialization" .claude/skills/do-plan-critique/CRITICS.md` | output > 0 |
| Exception Swallow Gate in do-test | `grep -c "Exception Swallow Gate" .claude/skills/do-test/SKILL.md` | output > 0 |
| swallow-ok convention documented | `grep -c "swallow-ok" .claude/skills/do-test/SKILL.md` | output > 0 |
| Full Suite Gate in do-merge | `grep -c "Full Suite Gate" .claude/commands/do-merge.md` | output > 0 |
| Red-main recovery documented | `grep -c "red-main" .claude/commands/do-merge.md` | output > 0 |
| Pre-Verdict Checklist in code-review | `grep -c "Pre-Verdict Checklist" .claude/skills/do-pr-review/sub-skills/code-review.md` | output > 0 |
| Feature doc exists | `test -f docs/features/sdlc-skills-audit.md` | exit code 0 |
| Tests pass | `pytest tests/ -x -q` | exit code 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Skeptic, Adversary | `grep -v "..."` uses basic regex — `\|` not interpreted as alternation without `-E` flag, causing 100% false positives | Technical Approach item 1, Task 3 | Changed `grep -v` to `grep -Ev` in all gate filter references |
| CONCERN | Skeptic, Operator | Baseline comparison method for Full Suite Gate unspecified | Technical Approach item 4, Task 4 | Added `data/main_test_baseline.json` artifact approach with read-side and write-side steps |
| CONCERN | Adversary | `# swallow-ok:` minimum length not enforced — single-char or whitespace reasons bypass gate | Technical Approach item 1, Task 3, Failure Path | Added 10-char minimum requirement; gate uses `grep -E "# swallow-ok: .{10,}"` |
| CONCERN | Archaeologist | Consistency Auditor scope overlaps with Propagation Check (PR #815) | Task 1 (build-critic-7) | Added scope differentiation paragraph to critic definition; explicitly excludes spike↔task domain |
| NIT | — | Tasks 6, 7, 8 missing `Validates` field | Tasks 6, 7, 8 | Noted; not addressed in this revision pass (structural formatting only) |
| NIT | — | Retrospective doc belongs in `docs/sdlc/` not `docs/features/` | Documentation section, Task 7 | Noted; left as-is — decision deferred to build stage |

---

## Open Questions

1. **Pattern 4 (full suite gate) timing**: Should the full suite run on the PR branch *before* merge, or on `main` *after* merge? Running on the PR branch is safer (can block) but means merging a green PR into a red `main` might still break things. Running on `main` after merge catches regressions but can't block. **Recommendation**: Run on PR branch; treat pre-existing `main` failures as known debt (logged but not blocking).

2. **#1040 subsumption decision**: Issue #1040 covers router oscillation from non-deterministic verdicts (Pattern 5's meta-level effect). The Pre-Verdict Checklist in this plan hardens the verifier but does not fix the oscillation router logic itself. Decision: #1040 remains open as an independent tracking issue. This plan's Pattern 5 fix is a prerequisite that reduces oscillation frequency but does not replace #1040's router-level fix.
