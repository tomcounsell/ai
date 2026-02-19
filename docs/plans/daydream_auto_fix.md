---
status: Planning
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-02-19
tracking: https://github.com/valorengels/ai/issues/139
---

# Daydream v2: Auto-Fix Critical Bugs via Plan-Build-PR

## Problem

When daydream detects a critical bug pattern — recurring Sentry error, repeated session failures, high-thrash corrections — it currently only creates a GitHub issue. A human must then triage, plan, and fix it manually.

For bugs where daydream has high-confidence root cause analysis (e.g., a `code_bug` reflection with a clear prevention rule), this adds unnecessary latency. The SDLC machinery (`/do-plan`, `/do-build`) already exists. Daydream should be able to invoke it.

**Current behavior:**
Daydream identifies `code_bug` patterns in step 7 (LLM reflection), writes them to `lessons_learned.jsonl`, and includes them in the daily GitHub issue — then stops. Human reads the issue, manually plans, manually builds.

**Desired outcome:**
After reflection, daydream evaluates whether any `code_bug` findings are high-confidence. For those that are, it spawns a `/do-plan` + `/do-build` sequence scoped to the specific bug, then posts the resulting PR link to the project's Telegram group. Human still reviews and merges — but the groundwork is done automatically.

## Appetite

**Size:** Medium

**Team:** Solo dev + PM

**Interactions:**
- PM check-ins: 1–2 (scope alignment — specifically what "high confidence" means, and budget cap mechanics)
- Review rounds: 1 (code review before merge)

Solo dev work is fast — the bottleneck is alignment and review. Appetite measures communication overhead, not coding time.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `ANTHROPIC_API_KEY` | `python -c "from dotenv import dotenv_values; assert dotenv_values('.env').get('ANTHROPIC_API_KEY')"` | Claude API for do-plan/do-build |
| `gh` CLI authenticated | `gh auth status` | Creating PRs and issues |
| `/do-plan` skill exists | `test -d .claude/skills/do-plan` | Plan creation |
| `/do-build` skill exists | `test -d .claude/skills/do-build` | Build execution |

Run all checks: `python scripts/check_prerequisites.py docs/plans/daydream_auto_fix.md`

## Solution

### Key Elements

- **Confidence scorer**: After LLM reflection, evaluate each `code_bug` reflection against a rubric (category match, pattern specificity, presence of a clear prevention rule, no existing open PR for the same pattern)
- **Auto-fix trigger**: A new daydream step (step 8, inserted between current step 7 LLM reflection and step 8 memory consolidation) that invokes Claude Code with the do-plan and do-build skills for qualifying bugs
- **Budget cap**: Each auto-fix attempt is bounded by a token/cost limit; exceeded attempts are logged and skipped
- **Deduplication guard**: Before triggering, check GitHub for any open PR or issue referencing the same bug pattern to avoid redundant work
- **PR notification**: After a PR is opened, post the PR link to the project's configured Telegram group (reusing existing Telegram posting logic in step 10)

### Flow

Daydream step 7 (LLM reflection) → **confidence scorer** → [low confidence: skip] → [high confidence: dedup check] → [duplicate found: skip] → **spawn Claude Code with do-plan + do-build** → PR opened → **post PR link to Telegram group**

### Technical Approach

- Add a `step_auto_fix_bugs()` method to `DaydreamRunner`, registered as step 8 (shifting current steps 8–10 to 9–11)
- Confidence scoring is rule-based (no extra LLM call): a reflection qualifies if `category == "code_bug"` AND `prevention` field is non-empty AND `pattern` is at least 20 chars (specific enough to be actionable)
- Spawn Claude Code as a subprocess: `claude --print --dangerously-skip-permissions "Implement the bug fix described below. Use /do-plan to create a plan, then /do-build to implement it as a PR. Bug: {summary}. Pattern: {pattern}. Prevention: {prevention}."`
- Budget cap: `DAYDREAM_AUTO_FIX_BUDGET_USD` env var (default: `$2.00`), checked against Anthropic usage API or estimated from model pricing
- Skip if any open GitHub issue or PR body contains the pattern string (substring match via `gh issue list --search`)
- State tracking: `auto_fix_attempts` list on `DaydreamState`, persisted in `state.json`

## Rabbit Holes

- **Multi-step confidence scoring with its own LLM call** — Rule-based scoring is sufficient and avoids compounding costs. Don't add another Haiku call just to score confidence.
- **Automated merging of the PR** — Auto-fix always stops at PR creation. Merging is always human. Don't add auto-merge logic.
- **Cross-machine coordination** — Each machine operates on its own repos per `config/projects.json`. No need for cross-machine dedup; just check the project's own GitHub.
- **Fixing bugs in external dependencies** — Auto-fix only applies to this codebase's own code. External service bugs (e.g., Telegram API quirks) are out of scope.
- **Retry logic for failed auto-fix attempts** — If the Claude Code subprocess fails, log it and move on. Don't retry in the same daydream run.

## Risks

### Risk 1: Runaway API costs
**Impact:** A badly-calibrated confidence scorer triggers auto-fix on every daydream run, burning API budget.
**Mitigation:** Hard budget cap via `DAYDREAM_AUTO_FIX_BUDGET_USD` (default $2.00). Log every triggered attempt with cost estimate. Add a `--dry-run` flag to daydream that shows what would be auto-fixed without actually triggering.

### Risk 2: Claude Code subprocess hangs
**Impact:** Daydream blocks indefinitely waiting for `do-plan` + `do-build` to finish.
**Mitigation:** Set a timeout on the subprocess (default: 10 minutes via `subprocess.run(..., timeout=600)`). If timeout exceeded, log and continue to next daydream step.

### Risk 3: Auto-fix creates bad PRs that pollute the repo
**Impact:** Low-quality auto-generated PRs that don't actually fix the bug, requiring cleanup.
**Mitigation:** Confidence criteria are strict — only `code_bug` category with a specific `prevention` rule. The PR always requires human review before merge. We can also add a `DAYDREAM_AUTO_FIX_ENABLED=false` env var to disable globally.

### Risk 4: Dedup check misses duplicate PRs
**Impact:** Multiple PRs for the same bug pattern accumulate.
**Mitigation:** Check both open issues AND open PRs via `gh pr list --search`. Use the `pattern` field as the search key, which is specific enough to catch duplicates.

## No-Gos (Out of Scope)

- Auto-merging PRs — always human review before merge
- Fixing bugs in external services or dependencies
- Retry logic within a single daydream run
- Cross-machine coordination for dedup
- Handling non-`code_bug` reflection categories (misunderstanding, poor_planning, etc.)
- Sentry-triggered auto-fix (Sentry is currently skipped in standalone mode; that's a separate effort)

## Update System

The `DAYDREAM_AUTO_FIX_ENABLED` and `DAYDREAM_AUTO_FIX_BUDGET_USD` env vars need to be documented in `.env.example`. The update skill (`scripts/remote-update.sh`) should remind operators to set these if desired.

No schema migrations needed — `DaydreamState` uses a flexible `step_progress` dict. The new `auto_fix_attempts` field on `DaydreamState` should have a default in the dataclass so old state files load without error.

## Agent Integration

No new MCP server needed — daydream runs as a standalone script (`scripts/daydream.py`). The auto-fix step invokes Claude Code directly as a subprocess using `claude --print`. This is the same pattern used by the `/do-build` skill when it shells out.

The auto-fix PR link is posted to Telegram via the existing `step_post_to_telegram` helper in `step_create_github_issue`. The new auto-fix step will call the same helper with the PR URL.

No `.mcp.json` changes needed. No bridge changes needed.

Integration test: `tests/test_daydream_auto_fix.py` — mock the Claude Code subprocess and verify that:
1. High-confidence `code_bug` reflections trigger a subprocess call
2. Low-confidence reflections are skipped
3. Duplicate PR detection prevents re-triggering
4. Budget cap stops triggering when threshold is reached

## Documentation

- [ ] Create `docs/features/daydream-auto-fix.md` describing the auto-fix capability, confidence criteria, budget cap, and how to disable it
- [ ] Add entry to `docs/features/README.md` index table under Daydream
- [ ] Update `docs/features/daydream.md` (if it exists) with the new step 8
- [ ] Add `DAYDREAM_AUTO_FIX_ENABLED` and `DAYDREAM_AUTO_FIX_BUDGET_USD` to `.env.example` with comments

## Success Criteria

- [ ] New `step_auto_fix_bugs()` method in `DaydreamRunner` registered as step 8
- [ ] Confidence scorer filters to only `code_bug` reflections with non-empty `prevention` and `pattern` ≥ 20 chars
- [ ] Dedup check queries GitHub open PRs and issues before triggering
- [ ] Claude Code subprocess called with do-plan + do-build instructions, with 10-minute timeout
- [ ] Budget cap enforced via `DAYDREAM_AUTO_FIX_BUDGET_USD` env var
- [ ] PR link posted to project's Telegram group after successful auto-fix
- [ ] `auto_fix_attempts` tracked in `DaydreamState` and persisted to state.json
- [ ] `DAYDREAM_AUTO_FIX_ENABLED=false` disables the feature
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead NEVER builds directly — they deploy team members and coordinate.

### Team Members

- **Builder (auto-fix-step)**
  - Name: daydream-builder
  - Role: Implement the auto-fix step in daydream.py — confidence scorer, dedup check, subprocess invocation, budget cap, Telegram notification
  - Agent Type: builder
  - Resume: true

- **Builder (tests)**
  - Name: test-builder
  - Role: Write `tests/test_daydream_auto_fix.py` with mocked subprocess and scenario coverage
  - Agent Type: test-engineer
  - Resume: true

- **Validator (auto-fix-step)**
  - Name: daydream-validator
  - Role: Verify the auto-fix implementation meets all success criteria and integrates cleanly with existing daydream steps
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: daydream-docs
  - Role: Create `docs/features/daydream-auto-fix.md` and update the README index and .env.example
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

**Builders:** `builder`, `test-engineer`, `documentarian`
**Validators:** `validator`, `code-reviewer`

## Step by Step Tasks

### 1. Implement auto-fix step
- **Task ID**: build-auto-fix
- **Depends On**: none
- **Assigned To**: daydream-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `auto_fix_attempts: list[dict]` field to `DaydreamState` dataclass with default_factory
- Add `step_auto_fix_bugs()` method to `DaydreamRunner`
- Register as step 8, shift existing steps 8–10 to 9–11 (update step numbers and docstrings)
- Implement confidence scorer: `code_bug` category + non-empty `prevention` + `pattern` >= 20 chars
- Implement dedup check via `gh issue list --search` and `gh pr list --search`
- Implement subprocess invocation: `claude --print --dangerously-skip-permissions "..."` with 10-minute timeout
- Implement budget cap from `DAYDREAM_AUTO_FIX_BUDGET_USD` env var (default `2.00`)
- Implement `DAYDREAM_AUTO_FIX_ENABLED` env var guard (default `true`)
- Post PR link to Telegram via `step_post_to_telegram` helper

### 2. Write tests
- **Task ID**: build-tests
- **Depends On**: none
- **Assigned To**: test-builder
- **Agent Type**: test-engineer
- **Parallel**: true
- Create `tests/test_daydream_auto_fix.py`
- Mock subprocess to avoid real Claude Code calls
- Test: high-confidence reflection triggers subprocess
- Test: low-confidence reflection (wrong category, short pattern, no prevention) is skipped
- Test: dedup check with matching open PR prevents trigger
- Test: budget cap stops triggering
- Test: `DAYDREAM_AUTO_FIX_ENABLED=false` disables step

### 3. Validate auto-fix implementation
- **Task ID**: validate-auto-fix
- **Depends On**: build-auto-fix, build-tests
- **Assigned To**: daydream-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/test_daydream_auto_fix.py -v`
- Run `black scripts/daydream.py && ruff check scripts/daydream.py`
- Verify step numbering is consistent (no gaps or duplicates)
- Verify `DaydreamState` loads cleanly from old state.json without `auto_fix_attempts`
- Check success criteria checklist

### 4. Write documentation
- **Task ID**: document-feature
- **Depends On**: validate-auto-fix
- **Assigned To**: daydream-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/daydream-auto-fix.md`
- Add entry to `docs/features/README.md`
- Update `.env.example` with `DAYDREAM_AUTO_FIX_ENABLED` and `DAYDREAM_AUTO_FIX_BUDGET_USD`

### 5. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: daydream-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite: `pytest tests/`
- Confirm all success criteria checked off
- Verify docs exist and are accurate
- Generate final pass/fail report

## Validation Commands

- `pytest tests/test_daydream_auto_fix.py -v` — unit tests for auto-fix step
- `pytest tests/` — full test suite
- `black scripts/daydream.py && ruff check scripts/daydream.py` — code quality
- `python -c "from scripts.daydream import DaydreamState; s = DaydreamState(); print(s.auto_fix_attempts)"` — state loads cleanly
- `python scripts/daydream.py --dry-run 2>&1 | grep auto_fix` — dry-run shows what would trigger

---

## Open Questions

1. **Budget cap mechanism**: Should the `DAYDREAM_AUTO_FIX_BUDGET_USD` cap be per-run (reset each daydream run) or cumulative across all runs (tracked in `state.json` or a separate file)? Per-run is simpler; cumulative gives better cost control long-term.
2. **`--dry-run` flag**: Should a `--dry-run` flag be added to `daydream.py` (showing what would auto-fix without acting) as part of this plan, or deferred? It's useful for testing confidence criteria but adds scope.
3. **Confidence threshold tuning**: The initial criteria (category=code_bug, non-empty prevention, pattern≥20 chars) may be too loose or too strict. Should we also require that the reflection's `source_session` is from the last 24h (to avoid acting on stale bugs)?
