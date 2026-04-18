# SDLC Skills Audit: Five Blind Spots Closed

**Shipped:** 2026-04-18 | **Issue:** #1042 | **PR:** see git history

## Summary

PR #1039 exposed five recurring patterns where each layer of the SDLC pipeline's blindness allows the next layer's bugs to pass through. This audit identified and fixed all five patterns through targeted, additive edits to skill markdown files.

## The Five Patterns and Fixes

### Pattern 1 — Exception Swallow Gate (do-test)

**Observed impact:** Exception handlers in `agent/` and `bridge/` swallow errors silently. The old advisory scan ran after tests passed and reported findings without blocking the pipeline.

**Fix applied:** Promoted the Exception Swallow Scan from advisory (Quality Checks, post-test) to a mandatory blocking gate in `do-test/SKILL.md`. The gate runs before OUTCOME emission and scans the diff (not the full codebase) for new `except Exception` blocks. A block fails the gate unless it:
1. Contains `logger`, `log.`, `warning`, `error`, or `raise` in the handler body, OR
2. Has an inline `# swallow-ok: {reason}` comment where the reason is at least 10 non-whitespace characters

**Carve-out convention:** `except Exception:  # swallow-ok: safe during shutdown, task already cancelled`

**File:** `.claude/skills/do-test/SKILL.md`

---

### Pattern 2 — Serialization Boundary Critic (do-plan-critique)

**Observed impact:** Integration tests create Popoto models in-memory and call methods directly without round-tripping through Redis serialization. Plans naming these as "integration tests" were not challenged.

**Fix applied:** Extended the Skeptic critic's "LOOK FOR" checklist in `do-plan-critique/CRITICS.md` with a serialization-boundary item: plans naming integration tests are challenged on whether those tests actually round-trip through Redis/persistence/serialization.

**File:** `.claude/skills/do-plan-critique/CRITICS.md`

---

### Pattern 3 — Internal Consistency Critic (do-plan-critique)

**Observed impact:** Plan documents contain internal contradictions that survive two critique passes. The six existing critics had no cross-section consistency checking charter.

**Fix applied:** Added a seventh critic persona — **Consistency Auditor** — to `do-plan-critique/CRITICS.md`. Its charter is to detect contradictions BETWEEN sections: spike findings vs. task steps (different from Propagation Check, which verifies spike results are carried forward), success criteria vs. Technical Approach, No-Gos vs. Solution, and any two sections naming different components for the same responsibility.

**Scope differentiation:** The Consistency Auditor does NOT duplicate the Propagation Check (PR #815). It checks for contradictions between sections, not whether spike results were carried into task steps.

**File:** `.claude/skills/do-plan-critique/CRITICS.md`

---

### Pattern 4 — Full Suite Gate (do-merge)

**Observed impact:** 71 failures on `main` existed without automated detection at merge time. The merge gate checked TEST/REVIEW/DOCS stage markers but did not validate the full suite on the target branch.

**Fix applied:** Added a Full Suite Gate to `do-merge.md` after the Lockfile Sync Check. The gate runs `pytest tests/ -q --tb=no` on the PR branch and compares results against `data/main_test_baseline.json`. Failures in the baseline are pre-existing (non-blocking); failures NOT in the baseline are new regressions (blocking).

**Red-main recovery path:** If the baseline file does not exist, all failures are written as the new baseline (bootstrap mode) — the gate does not block clean PRs because of pre-existing red-main failures.

**File:** `.claude/commands/do-merge.md`

---

### Pattern 5 — Deterministic Pre-Verdict Checklist (do-pr-review)

**Observed impact:** LLM-based reviewers produce different verdicts on identical input because each run's salience drives what surfaces. No fixed set of items was required for evaluation before writing the verdict.

**Fix applied:** Added a mandatory 12-item Pre-Verdict Checklist to `do-pr-review/sub-skills/code-review.md`. The checklist must be evaluated for every PR before writing the verdict. Each item gets `PASS | FAIL | N/A`. An "Approved" verdict with any `FAIL` items is not valid — FAIL items must be promoted to findings.

**Checklist items:** plan acceptance criteria, No-Gos, except Exception blocks, integration test serialization boundary, plan internal consistency, no debug artifacts, public API docstrings, breaking change migration, tests for new behavior, failure path coverage, UI screenshots, docs for user-facing changes.

**File:** `.claude/skills/do-pr-review/sub-skills/code-review.md`

---

## Decisions

- **#1040 remains open** as an independent tracking issue. The Pre-Verdict Checklist (Pattern 5) reduces oscillation frequency by making verifier output more deterministic, but does not fix the router-level oscillation logic.
- **#1041 remains open** as an independent tracking issue. The Full Suite Gate (Pattern 4) blocks new regressions on green main; fixing the existing 71 failures is #1041's scope.
- **Retrospective doc location:** `docs/features/` (not `docs/sdlc/`) — this is a shipped feature, not a per-stage process note.

## Files Changed

| File | Change |
|------|--------|
| `.claude/skills/do-plan-critique/CRITICS.md` | Added Consistency Auditor (critic #7) + serialization-boundary item in Skeptic |
| `.claude/skills/do-test/SKILL.md` | Promoted Exception Swallow Scan to blocking gate before OUTCOME emission |
| `.claude/commands/do-merge.md` | Added Full Suite Gate with red-main recovery path and baseline comparison |
| `.claude/skills/do-pr-review/sub-skills/code-review.md` | Added mandatory 12-item Pre-Verdict Checklist |
| `docs/features/sdlc-skills-audit.md` | This document |
