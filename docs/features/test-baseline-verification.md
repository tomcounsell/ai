# Test Baseline Verification

Prevents regressions from being silently dismissed as "pre-existing" by actually running failing tests against `main` and classifying each one. Includes a circuit breaker that escalates to planning after 3 failed regression fix attempts.

## Problem

During `/do-test`, when tests fail on a feature branch, the agent frequently claims "X failures are pre-existing on main" — but never verifies this. The claim is LLM improvisation. This means:

1. **Regressions slip through** — a test broken by the branch gets dismissed as pre-existing
2. **No confidence** — the reviewer can't trust the claim without manually checking
3. **Spin loops** — no escape when regression fixes don't converge

## Solution

### Baseline Verifier Subagent

`baseline-verifier` (`.claude/agents/baseline-verifier.md`) receives failing test IDs from do-test, creates an isolated git worktree at `main` HEAD, runs only those tests against main, and returns structured JSON.

**Classification rules:**

| Branch Result | Main Result | Classification |
|--------------|-------------|----------------|
| FAIL | PASS | **Regression** — broken by this branch, must fix |
| FAIL | FAIL | **Pre-existing** — already failing on main, does not block |
| FAIL | ERROR/SKIP/not found | **Inconclusive** — flag for human review |

**Return format:**
```json
{
  "baseline_commit": "abc1234",
  "regressions": ["tests/unit/test_api.py::test_rate_limit"],
  "pre_existing": ["tests/unit/test_auth.py::test_expired_token"],
  "inconclusive": [],
  "raw_output": "..."
}
```

### Verified Classification Table

do-test replaces the vague "X pre-existing" with:

```
### Failure Classification (verified against main @ abc1234)

| Test | Branch | Main | Verdict |
|------|--------|------|---------|
| test_auth::test_expired_token | FAIL | FAIL | Pre-existing ✓ |
| test_api::test_rate_limit | FAIL | PASS | **REGRESSION** |
| test_bridge::test_reconnect | FAIL | ERROR | Inconclusive |

**Regressions: 1** (must fix — attempt 1/3)
**Pre-existing: 1** (verified against main)
**Inconclusive: 1** (needs human review)
```

### OUTCOME Status Rules

| Condition | Status | Effect |
|-----------|--------|--------|
| No failures | `success` | Proceed to review |
| Only pre-existing (zero regressions) | `partial` | Proceed to review (does not block) |
| Regressions exist, attempt < 3 | `fail` | Route to do-patch |
| Same regressions persist after 3 attempts | `blocked` | Escalate to do-plan |

### Regression Circuit Breaker

do-test reads `regression_fix_attempt` from the prior `<!-- OUTCOME -->` block in conversation history (Claude Sonnet can parse it directly — no explicit wiring needed).

**Counter logic:**
- If the same regression test IDs persist from the prior attempt: increment counter
- If the regression set changes (different test IDs): reset counter to 1
- After 3 attempts with the same persistent regressions: emit `status: blocked` with `next_skill: /do-plan`

**Escalation OUTCOME example:**
```
<!-- OUTCOME {"status":"blocked","stage":"TEST","artifacts":{"regression_fix_attempt":3,"persistent_regressions":["tests/unit/test_api.py::test_rate_limit"],"escalation":"regression_unfixable"},"notes":"3 regression fix attempts failed for test_rate_limit. Escalating to planning — route to /do-plan for human review of approach.","next_skill":"/do-plan"} -->
```

The LLM Observer sees `status: blocked` + explicit "route to /do-plan" in notes and steers the pipeline back to planning, where the human is asked to choose: update the test expectations, rethink the implementation, or defer.

## Worktree Isolation

The verifier uses `git worktree add /tmp/baseline-verify-<pid> main` for an isolated environment:

- Only the failing tests are run (not the full suite) — keeps verification fast
- `.env` is copied to the worktree so tests have the same runtime config
- The worktree is always cleaned up (`git worktree remove --force`) even on error
- `git worktree prune` runs at start (defensive cleanup of orphaned worktrees)

## Key Files

| File | Purpose |
|------|---------|
| `.claude/agents/baseline-verifier.md` | Subagent: worktree creation, test execution, classification |
| `.claude/skills/do-test/SKILL.md` | Integration: dispatches verifier, builds table, circuit breaker |

## Status

Shipped
