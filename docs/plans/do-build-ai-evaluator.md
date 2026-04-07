---
status: In Progress
type: feature
appetite: Small
owner: Valor Engels
created: 2026-04-07
tracking: https://github.com/tomcounsell/ai/issues/740
last_comment_id:
---

# Do-Build AI Evaluator

## Problem

The `/do-build` pipeline uses `validate_build.py` for deterministic, regex-based checks (file existence, verification table, success criteria commands). This catches structural gaps but cannot evaluate whether the *intent* of the plan's acceptance criteria has been met. A build can pass all deterministic checks and still miss the semantic point of the feature — leaving a gap between "technically correct" and "actually done."

**Current behavior:**
After `validate_build.py` passes, the build advances directly to the review stage. There is no semantic check against the plan's `## Acceptance Criteria` section.

**Desired outcome:**
After `validate_build.py` passes and before advancing to the review stage, an AI evaluator reads the plan's `## Acceptance Criteria` and compares them against the actual `git diff main..HEAD`. It returns per-criterion verdicts (PASS / PARTIAL / FAIL with evidence). FAIL verdicts route to `/do-patch` (max 2 iterations); PARTIAL verdicts log as warnings; PASS verdicts proceed normally.

## Prior Art

- `scripts/validate_build.py` — Deterministic validator: file assertions, verification table, success criteria commands. This plan does NOT replace it; the AI evaluator runs after it.
- `.claude/skills/do-build/SKILL.md` steps 16b, 17 — validate_build.py currently runs at step 16b; the AI evaluator inserts between 16b and 17.
- `.claude/skills/do-patch/SKILL.md` — Already invoked by do-build for test failures. The AI evaluator reuses this same invocation path for FAIL verdicts.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No external prerequisites. `validate_build.py` already runs and exits 0 before the AI evaluator is invoked.

## Solution

### Key Elements

- **New evaluator script**: `scripts/evaluate_build.py` — takes plan path and optional `--diff` flag as inputs, reads `## Acceptance Criteria`, runs AI evaluation, outputs structured JSON verdicts.
- **SKILL.md insertion**: Add step 16c to `.claude/skills/do-build/SKILL.md`, between step 16b (`validate_build.py`) and step 17 (advance to review stage). The step invokes `evaluate_build.py` and handles verdict routing.
- **Verdict structure**: Each criterion gets `{"criterion": str, "verdict": "PASS"|"PARTIAL"|"FAIL", "evidence": str}`. Exit code 0 = all PASS/PARTIAL, exit code 2 = at least one FAIL, exit code 3 = no `## Acceptance Criteria` section found (skip with warning), exit code 1 = unexpected error (non-blocking).
- **FAIL routing**: Exit code 2 routes to `/do-patch` with the FAIL verdict evidence as the patch arg. Max 2 re-evaluation cycles. If still FAIL after 2 iterations, log and proceed (non-blocking fallback).
- **Non-blocking fallback**: Any evaluator error (agent timeout, API error, exit code 1) is logged as a warning and the pipeline continues to review. The evaluator is advisory, not a hard gate.
- **Read-only contract**: `evaluate_build.py` never modifies files. It reads the plan and diff, calls the AI, writes to stdout only.

### Flow

```
validate_build.py exits 0
  → evaluate_build.py (reads plan AC + git diff)
    → exit 3 (no AC section): log warning, skip to review stage
    → exit 1 (agent error): log warning, skip to review stage
    → exit 0 (all PASS/PARTIAL):
        PARTIAL verdicts: log as warnings in pipeline output
        Proceed to advance_stage('review')
    → exit 2 (one or more FAIL):
        Call /do-patch with FAIL verdict evidence (iteration 1)
          → re-run evaluate_build.py
          → still FAIL: call /do-patch again (iteration 2)
            → still FAIL: log "AI evaluator: 2 iterations reached, proceeding to review"
            → Proceed to advance_stage('review')
          → exit 0 or 3: Proceed to advance_stage('review')
```

### Technical Approach

**`scripts/evaluate_build.py`:**

```python
#!/usr/bin/env python3
"""AI semantic evaluator for build acceptance criteria.

Reads the plan's ## Acceptance Criteria section and compares against
git diff main..HEAD using an AI judge. Returns structured verdicts.

Exit codes:
    0 - All criteria PASS or PARTIAL (no FAIL)
    1 - Unexpected error (non-blocking — caller logs and proceeds)
    2 - One or more FAIL verdicts
    3 - No ## Acceptance Criteria section in plan (skip with warning)
"""
```

The script:
1. Parses `## Acceptance Criteria` from the plan (using the same `extract_section()` pattern as `validate_build.py`)
2. Runs `git diff main..HEAD` to capture the full diff
3. Calls the Anthropic API (Claude Haiku for speed) with a structured prompt asking for per-criterion verdicts
4. Outputs JSON to stdout: `{"verdicts": [{"criterion": ..., "verdict": ..., "evidence": ...}]}`
5. Exits with the appropriate code

**SKILL.md step 16c insertion:**

```markdown
16c. **Run AI semantic evaluation against acceptance criteria** - After validate_build.py passes:
    ```bash
    (cd $TARGET_REPO/.worktrees/{slug} && python scripts/evaluate_build.py $PLAN_PATH)
    ```
    - Exit code 0: all criteria PASS or PARTIAL — log PARTIAL verdicts as warnings, proceed to step 17
    - Exit code 2: one or more FAIL verdicts — invoke `/do-patch` with the FAIL evidence (max 2 iterations), then re-run evaluate_build.py
    - Exit code 3: no `## Acceptance Criteria` section — log "AI evaluator: no Acceptance Criteria section found, skipping" and proceed to step 17
    - Exit code 1 or any error: log "AI evaluator failed (non-blocking): {error}" and proceed to step 17
```

## Failure Path Test Strategy

### Exception Handling Coverage
- `evaluate_build.py`: Anthropic API errors, timeouts → catch all exceptions, print warning to stderr, exit 1 (non-blocking)
- `evaluate_build.py`: Missing `## Acceptance Criteria` section → exit 3 immediately with a warning message
- `evaluate_build.py`: Empty diff (no changes) → handle gracefully, pass all criteria with "no diff to evaluate" evidence
- SKILL.md step 16c: if `evaluate_build.py` itself is not found (missing file) → treated as exit 1 (non-blocking)

### Empty/Invalid Input Handling
- Plan with no `## Acceptance Criteria`: exit 3, logged warning, pipeline continues
- Plan with empty `## Acceptance Criteria` section (header present, no content): exit 3 (treat same as missing)
- Diff is empty string (no changes): evaluator should note this as evidence and likely emit FAIL/PARTIAL for most criteria

### Error State Rendering
- All FAIL verdict evidence is included in the `/do-patch` arg so the patcher has actionable context
- PARTIAL warnings are emitted as numbered lines: `WARNING: AC criterion PARTIAL — {criterion}: {evidence}`

## Test Impact

- [ ] `tests/unit/test_validate_build.py` — No change needed; tests the deterministic validator only and is unaffected by this plan's additions.

No other existing tests are affected — `evaluate_build.py` is a new script with no prior test coverage, and the SKILL.md change is additive (inserting a new step). The new test file `tests/unit/test_evaluate_build.py` is created by this plan as a deliverable, not a modification to existing tests.

## Rabbit Holes

- **Making PARTIAL a blocking verdict**: Out of scope per no-gos. PARTIAL is advisory only.
- **Adding the evaluator to `/do-test`**: Out of scope. The evaluator is a build gate, not a test-runner concern.
- **Replacing `validate_build.py`**: Out of scope. The deterministic validator runs first; the AI evaluator is additive.
- **Caching evaluator results across patch iterations**: Not worth the complexity. Each invocation re-reads the current diff.
- **Supporting evaluator on cross-repo builds**: The evaluator uses `git diff main..HEAD` from within the worktree CWD — it works transparently for cross-repo builds with no extra logic.

## Risks

### Risk 1: Evaluator cost and latency
**Impact:** Each build invocation makes an API call. On slow connections or under quota limits, this could add 10-30 seconds per build.
**Mitigation:** Use Claude Haiku (fastest, cheapest model). Set a 60-second timeout. Exit code 1 (non-blocking) on timeout. The pipeline is not delayed beyond 60 seconds in the worst case.

### Risk 2: False FAIL verdicts trigger unnecessary patches
**Impact:** If the AI evaluator is over-strict, it may FAIL criteria that are genuinely met, triggering up to 2 patch cycles on a working build.
**Mitigation:** Cap patch iterations at 2. After 2 FAIL iterations, the evaluator stops blocking and the pipeline proceeds to review. Human review catches any remaining gaps.

### Risk 3: SKILL.md not read in all invocation paths
**Impact:** If a `/do-build` invocation does not reload SKILL.md after the plan is changed, step 16c will not run.
**Mitigation:** SKILL.md is always read at the start of each `/do-build` invocation. The step insertion is in the canonical workflow sequence.

## Race Conditions

No race conditions — the evaluator is a sequential step in a single-orchestrator pipeline. It runs after `validate_build.py` and before the pipeline advances to the review stage.

## No-Gos (Out of Scope)

- Do NOT replace `validate_build.py` — the deterministic checks remain
- Do NOT add the evaluator to the `/do-test` pipeline
- Do NOT make PARTIAL a blocking verdict
- Do NOT modify the PR body format or add evaluator output to the PR description
- Do NOT evaluate criteria against tests alone — use the full `git diff main..HEAD`

## Update System

`scripts/evaluate_build.py` is a new file that ships with the codebase. No update script changes needed — `git pull` will pull it automatically. No new dependencies beyond what is already in `requirements.txt` (Anthropic SDK already present).

No migration steps for existing installations. The new step is additive; existing builds without `## Acceptance Criteria` sections will receive exit code 3 (skip) and proceed unaffected.

## Agent Integration

No agent integration changes required. `evaluate_build.py` is invoked by the `/do-build` skill orchestrator, not by the Telegram bridge or any MCP server. The script reads the plan file and calls the Anthropic API directly (same pattern as other scripts in `scripts/`).

No changes to `.mcp.json`, `mcp_servers/`, or `bridge/telegram_bridge.py` needed.

## Documentation

- [ ] Create `docs/features/do-build-ai-evaluator.md` describing the AI evaluator step, its placement in the pipeline, verdict types, and routing logic
- [ ] Update `docs/features/README.md` to add an entry for `do-build-ai-evaluator.md`

## Acceptance Criteria

- [ ] A new evaluator step runs after `validate_build.py` passes and before the build advances to the review stage (step 16c in SKILL.md)
- [ ] The evaluator reads plan `## Acceptance Criteria` and compares against `git diff main..HEAD`
- [ ] The evaluator returns a structured per-criterion verdict (PASS / PARTIAL / FAIL) with evidence
- [ ] FAIL verdicts route to `/do-patch` (max 2 iterations) before the build advances to review
- [ ] PARTIAL verdicts are logged as warnings and do not block the pipeline
- [ ] The evaluator is read-only — `evaluate_build.py` never modifies any files
- [ ] If the plan has no `## Acceptance Criteria` section, the step is skipped with a logged warning (exit code 3)
- [ ] Evaluator failures (agent errors, timeouts) are non-blocking — pipeline logs and proceeds (exit code 1)
- [ ] `tests/unit/test_evaluate_build.py` covers section parsing, verdict formatting, and exit code logic
- [ ] `pytest tests/unit/ -x -q` passes

## Success Criteria

- [ ] `scripts/evaluate_build.py` exists and is executable
- [ ] `python scripts/evaluate_build.py --help` exits 0 with usage text
- [ ] Running `evaluate_build.py` on a plan with no AC section exits 3 with a warning message
- [ ] Running `evaluate_build.py` on a plan where diff matches all criteria exits 0
- [ ] `.claude/skills/do-build/SKILL.md` contains step 16c with AI evaluator invocation
- [ ] `pytest tests/unit/test_evaluate_build.py` passes

## Team Orchestration

### Team Members

- **Builder (evaluator-script)**
  - Name: script-builder
  - Role: Create `scripts/evaluate_build.py` with all verdict logic, exit codes, and Anthropic API integration
  - Agent Type: builder
  - Resume: true

- **Builder (skill-update)**
  - Name: skill-updater
  - Role: Insert step 16c into `.claude/skills/do-build/SKILL.md`
  - Agent Type: builder
  - Resume: true

- **Validator (evaluator)**
  - Name: evaluator-validator
  - Role: Verify evaluate_build.py logic, exit codes, and SKILL.md step insertion
  - Agent Type: validator
  - Resume: true

### Available Agent Types

builder, validator

## Step by Step Tasks

### 1. Create scripts/evaluate_build.py
- **Task ID**: build-evaluator-script
- **Depends On**: none
- **Validates**: `python scripts/evaluate_build.py --help` exits 0
- **Assigned To**: script-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `scripts/evaluate_build.py` with:
  - `extract_section()` to pull `## Acceptance Criteria` from plan text (same pattern as `validate_build.py`)
  - `get_git_diff()` to run `git diff main..HEAD` and return output string
  - `evaluate_criteria(criteria_text, diff_text)` that calls Anthropic API (Claude Haiku) with a structured prompt requesting per-criterion verdicts
  - JSON output to stdout: `{"verdicts": [{"criterion": str, "verdict": "PASS|PARTIAL|FAIL", "evidence": str}]}`
  - Exit codes: 0 (all PASS/PARTIAL), 1 (error), 2 (any FAIL), 3 (no AC section)
  - 60-second timeout on API call; catch all exceptions → exit 1 with warning to stderr
  - CLI: `python scripts/evaluate_build.py <plan-path>` with `--help` flag
  - `--dry-run` flag: parse criteria and diff but skip API call, output mock verdicts for testing
- Use `anthropic` SDK already in requirements (same import pattern as other tools)

### 2. Write unit tests for evaluate_build.py
- **Task ID**: build-evaluator-tests
- **Depends On**: build-evaluator-script
- **Validates**: `pytest tests/unit/test_evaluate_build.py -v` exits 0
- **Assigned To**: script-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `tests/unit/test_evaluate_build.py` with:
  - `test_extract_section_with_ac_section`: parses criteria from a plan with `## Acceptance Criteria`
  - `test_extract_section_without_ac_section`: returns empty string when section is absent
  - `test_exit_code_3_on_missing_ac_section`: runs evaluate_build.py on a temp plan file without AC section, asserts exit code 3
  - `test_exit_code_0_on_mock_pass`: uses `--dry-run` flag, asserts exit code 0
  - `test_exit_code_1_on_missing_plan`: runs evaluate_build.py on a nonexistent file path, asserts exit code 1 or prints usage
  - Mock the Anthropic API call (do not make real API calls in unit tests)

### 3. Insert step 16c into SKILL.md
- **Task ID**: update-skill-md
- **Depends On**: build-evaluator-tests
- **Validates**: `grep "evaluate_build.py" .claude/skills/do-build/SKILL.md` returns a match
- **Assigned To**: skill-updater
- **Agent Type**: builder
- **Parallel**: false
- Read `.claude/skills/do-build/SKILL.md`
- Insert step 16c between step 16b (validate_build.py) and step 17 (advance_stage review):
  ```
  16c. **Run AI semantic evaluation against acceptance criteria** - After validate_build.py passes, run the AI evaluator:
      ```bash
      (cd $TARGET_REPO/.worktrees/{slug} && python scripts/evaluate_build.py $PLAN_PATH)
      ```
      - Exit code 0: all criteria PASS or PARTIAL — log any PARTIAL verdicts as warnings, proceed to step 17
      - Exit code 2: FAIL verdicts found — invoke `/do-patch` with the FAIL evidence as the patch description (max 2 iterations), then re-run evaluate_build.py; if FAIL persists after 2 iterations, log "AI evaluator: 2 iterations reached, proceeding to review" and proceed to step 17
      - Exit code 3: no `## Acceptance Criteria` section — log "AI evaluator: no Acceptance Criteria section, skipping" and proceed to step 17
      - Exit code 1 or any error: log "AI evaluator failed (non-blocking): {error}" and proceed to step 17
  ```
- Ensure the wording matches the issue requirements (FAIL → patch, PARTIAL → warning only, errors → non-blocking)

### 4. Create docs/features/do-build-ai-evaluator.md
- **Task ID**: build-docs
- **Depends On**: update-skill-md
- **Validates**: `test -f docs/features/do-build-ai-evaluator.md` exits 0
- **Assigned To**: skill-updater
- **Agent Type**: builder
- **Parallel**: false
- Create `docs/features/do-build-ai-evaluator.md` with:
  - Overview: what the AI evaluator does and where it fits in the build pipeline
  - Pipeline position: runs after `validate_build.py`, before `advance_stage('review')`
  - Verdict types: PASS, PARTIAL, FAIL — what each means and how the pipeline responds
  - Routing logic: FAIL → `/do-patch` (max 2 iterations), PARTIAL → warning only, errors → non-blocking
  - Exit code reference table
  - How to disable: if plan has no `## Acceptance Criteria`, evaluator is automatically skipped
- Update `docs/features/README.md` to add an entry for `do-build-ai-evaluator.md`

### 5. Validate all
- **Task ID**: validate-all
- **Depends On**: build-docs
- **Assigned To**: evaluator-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/ -x -q` and confirm exit 0
- Confirm `scripts/evaluate_build.py` exists and `python scripts/evaluate_build.py --help` exits 0
- Confirm `.claude/skills/do-build/SKILL.md` contains "evaluate_build.py" and "16c"
- Confirm `docs/features/do-build-ai-evaluator.md` exists
- Generate final pass/fail report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Evaluator script exists | `test -f scripts/evaluate_build.py` | exit code 0 |
| Evaluator --help works | `python scripts/evaluate_build.py --help` | exit code 0 |
| SKILL.md updated | `grep -c "evaluate_build.py" .claude/skills/do-build/SKILL.md` | output >= 1 |
| Step 16c present | `grep -c "16c" .claude/skills/do-build/SKILL.md` | output >= 1 |
| Feature docs created | `test -f docs/features/do-build-ai-evaluator.md` | exit code 0 |
| Unit tests pass | `pytest tests/unit/test_evaluate_build.py -v` | exit code 0 |
| Full test suite | `pytest tests/ -x -q` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). -->
| Severity | Critic | Finding | Status |
|----------|--------|---------|--------|

---

## Open Questions

1. Should the evaluator use Claude Haiku or Sonnet? Haiku is faster and cheaper; Sonnet has better reasoning. Recommendation: start with Haiku (same model tier as other scripts). Can upgrade if evaluation quality is insufficient.
2. Should PARTIAL verdicts be counted and reported as a build metric? Out of scope for this plan — warnings only.
