---
status: Planning
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-02-19
tracking: https://github.com/tomcounsell/ai/issues/139
---

# Daydream v2: Auto-Fix Critical Bugs via Plan-Build-PR

## Problem

When daydream detects a critical bug pattern — recurring Sentry error, repeated session failures, high-thrash corrections — it currently only creates a GitHub issue. A human must then triage, plan, and fix it manually.

For bugs where daydream has high-confidence root cause analysis (e.g., a `code_bug` reflection with a clear prevention rule), this adds unnecessary latency. The SDLC machinery (`/do-plan`, `/do-build`) already exists. Daydream should be able to invoke it.

**Current behavior:**
Daydream identifies `code_bug` patterns in step 7 (LLM reflection), writes them to `lessons_learned.jsonl`, and includes them in the daily GitHub issue — then stops. Human reads the issue, manually plans, manually builds.

**Desired outcome:**
After reflection, daydream evaluates whether any `code_bug` findings are actionable (confidence check + ignore log check). For those that pass, it spawns a `/do-plan` + `/do-build` sequence scoped to the specific bug, then posts the resulting PR link to the project's Telegram group. Human still reviews and merges — but the groundwork is done automatically.

## Appetite

**Size:** Medium

**Team:** Solo dev + PM

**Interactions:**
- PM check-ins: 1–2 (scope alignment on confidence criteria and ignore log UX)
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

- **Confidence scorer**: After LLM reflection, evaluate each `code_bug` reflection against a loose rubric — passes if any two of three criteria are met (see Technical Approach). Intentionally permissive; the ignore log handles false positives.
- **Ignore log**: A flat JSONL file (`data/daydream_ignore.jsonl`) where patterns can be silenced for 14 days. Added via `python scripts/daydream.py --ignore "pattern"`. Daydream auto-prunes expired entries each run.
- **Auto-fix trigger**: A new daydream step (step 8, inserted between LLM reflection and memory consolidation) that invokes Claude Code with do-plan + do-build for qualifying bugs.
- **Deduplication guard**: Before triggering, check GitHub for any open PR or issue referencing the same bug pattern.
- **Dry-run mode**: `python scripts/daydream.py --dry-run` shows what would trigger auto-fix without actually running it. Use this when testing changes to daydream itself.
- **PR notification**: After a PR is opened, post the PR link to the project's configured Telegram group.

### Flow

```
Step 7 (LLM reflection)
  → confidence scorer (any 2 of 3 criteria)
    → [fails: skip]
    → [passes: check ignore log]
      → [ignored: log + skip]
      → [not ignored: dedup check on GitHub]
        → [duplicate PR/issue found: skip]
        → [clear: spawn Claude Code with do-plan + do-build]
          → PR opened
          → post PR link to Telegram group
```

### Ignore Log Design

**File:** `data/daydream_ignore.jsonl`

**Entry format:**
```json
{"pattern": "bridge crashes on reconnect", "ignored_until": "2026-03-05", "reason": "already being tracked in issue #142"}
```

**CLI to add an entry:**
```bash
python scripts/daydream.py --ignore "pattern string"
# Optional: python scripts/daydream.py --ignore "pattern" --reason "tracking separately"
```

This appends an entry with `ignored_until` set to today + 14 days. Daydream auto-prunes entries where `ignored_until < today` at the start of the auto-fix step. The file can also be edited by hand.

### Dry-Run Mode

```bash
python scripts/daydream.py --dry-run
```

Runs all daydream steps but skips any action that modifies state: no Claude Code subprocess, no GitHub PR, no Telegram post, no lessons written. Prints what *would* have happened. **Use this when testing changes to daydream** — it's the safe way to validate new confidence criteria or step logic without triggering real side effects.

Note: `--dry-run` and `--ignore` are CLI flags on the existing script entry point, not separate scripts.

### Confidence Criteria (Loose — Any 2 of 3)

1. `category == "code_bug"` — reflection is categorized as a code bug
2. `prevention` field is non-empty — there's a concrete fix direction
3. `pattern` is ≥ 10 chars — pattern is specific enough to be actionable (lowered from 20)

This is intentionally permissive. Some false positives are fine — the ignore log handles anything that keeps coming up that we don't want to fix yet.

### Technical Approach

- Add `auto_fix_attempts: list[dict]` field to `DaydreamState` dataclass (default_factory=list, so old state files load cleanly)
- Add `step_auto_fix_bugs()` to `DaydreamRunner`, registered as step 8; shift current steps 8–10 to 9–11
- Implement `load_ignore_log()` and `prune_ignore_log()` helpers in `scripts/daydream.py`
- Spawn Claude Code: `claude --print --dangerously-skip-permissions "..."` with 10-minute timeout
- No budget cap — API usage caps are already enforced at the account level
- Skip if any open GitHub issue or PR body contains the pattern string (via `gh issue list --search` + `gh pr list --search`)
- State tracking: `auto_fix_attempts` list on `DaydreamState` (for the daily report)
- Add CLI arg parsing: `--dry-run` and `--ignore "pattern"` flags to `main()`

## Rabbit Holes

- **Confidence scoring with its own LLM call** — The 2-of-3 rule-based check is sufficient. No extra Haiku call.
- **Automated merging of the PR** — Always stops at PR creation. Merging is always human.
- **Cross-machine ignore log syncing** — Each machine has its own `data/daydream_ignore.jsonl`. No syncing needed.
- **Fixing bugs in external dependencies** — Only applies to this codebase's own code.
- **Retry logic for failed auto-fix attempts** — Log it, move on. No retry in the same run.
- **A UI for the ignore log** — The JSONL file + CLI flag is the UI. Don't build anything fancier.

## Risks

### Risk 1: Loose confidence triggers too many auto-fixes
**Impact:** Redundant PRs that require cleanup.
**Mitigation:** The ignore log is specifically designed for this. If a pattern keeps triggering and you don't want it to, `--ignore` it. The 14-day expiry means ignored things will surface again eventually in case circumstances change.

### Risk 2: Claude Code subprocess hangs
**Impact:** Daydream blocks indefinitely waiting for do-plan + do-build to finish.
**Mitigation:** 10-minute subprocess timeout. If exceeded, log and continue to next step.

### Risk 3: Dedup check misses duplicate PRs
**Impact:** Multiple PRs for the same bug pattern.
**Mitigation:** Check both `gh issue list --search` and `gh pr list --search`. Pattern field is specific enough to catch duplicates in most cases.

## No-Gos (Out of Scope)

- Budget cap — not needed, API usage caps are enforced at the account level
- Auto-merging PRs — always human review before merge
- Cross-machine ignore log coordination
- Fixing bugs in external services or dependencies
- Retry logic within a single daydream run
- Non-`code_bug` reflection categories (misunderstanding, poor_planning, etc.)
- Sentry-triggered auto-fix (Sentry is skipped in standalone mode; separate effort)

## Update System

Add `DAYDREAM_AUTO_FIX_ENABLED` to `.env.example` with a comment explaining how to disable. The update skill (`scripts/remote-update.sh`) should create `data/daydream_ignore.jsonl` if it doesn't exist (touch-safe).

No schema migrations needed — `DaydreamState` uses flexible dict-based `step_progress`. The new `auto_fix_attempts` field defaults to `[]` so old state files load without error.

## Agent Integration

No new MCP server needed — daydream is a standalone script. The auto-fix step invokes Claude Code as a subprocess (`claude --print`).

PR links are posted to Telegram via the existing `step_post_to_telegram` helper.

No `.mcp.json` changes. No bridge changes.

Integration tests: `tests/test_daydream_auto_fix.py` — mock the Claude Code subprocess and verify:
1. Reflections passing 2+ of 3 confidence criteria trigger subprocess
2. Reflections passing only 1 criterion are skipped
3. Ignored patterns are skipped (and not yet expired)
4. Expired ignore entries are pruned and pattern re-triggers
5. Duplicate PR detection prevents re-triggering
6. `DAYDREAM_AUTO_FIX_ENABLED=false` disables step entirely
7. `--dry-run` mode runs the step without calling subprocess

## Documentation

- [ ] Create `docs/features/daydream-auto-fix.md` describing the auto-fix capability, confidence criteria, ignore log, dry-run mode, and how to disable it
- [ ] Add entry to `docs/features/README.md` index table under Daydream
- [ ] Update `docs/features/daydream.md` (if it exists) with the new step 8 and a note that **`--dry-run` should be used when testing changes to daydream**
- [ ] Add `DAYDREAM_AUTO_FIX_ENABLED` to `.env.example` with comment
- [ ] Update `CLAUDE.md` Quick Commands table to include `python scripts/daydream.py --dry-run` and `python scripts/daydream.py --ignore "pattern"`

## Success Criteria

- [ ] New `step_auto_fix_bugs()` method in `DaydreamRunner` registered as step 8
- [ ] Confidence scorer passes reflections matching any 2 of 3 criteria
- [ ] Ignore log (`data/daydream_ignore.jsonl`) checked before triggering; expired entries pruned each run
- [ ] `--ignore "pattern"` CLI flag appends a 14-day entry to the ignore log
- [ ] `--dry-run` CLI flag skips all side effects, prints what would have triggered
- [ ] Dedup check queries GitHub open PRs and issues before triggering
- [ ] Claude Code subprocess invoked with 10-minute timeout
- [ ] PR link posted to project's Telegram group after successful auto-fix
- [ ] `auto_fix_attempts` tracked in `DaydreamState`
- [ ] `DAYDREAM_AUTO_FIX_ENABLED=false` disables the feature
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead NEVER builds directly — they deploy team members and coordinate.

### Team Members

- **Builder (auto-fix-step)**
  - Name: daydream-builder
  - Role: Implement the auto-fix step, confidence scorer, ignore log, dry-run mode, and CLI flags in daydream.py
  - Agent Type: builder
  - Resume: true

- **Builder (tests)**
  - Name: test-builder
  - Role: Write `tests/test_daydream_auto_fix.py` with mocked subprocess and full scenario coverage
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: daydream-validator
  - Role: Verify implementation meets all success criteria and integrates cleanly with existing steps
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: daydream-docs
  - Role: Create `docs/features/daydream-auto-fix.md`, update README index, update CLAUDE.md quick commands
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
- Add `auto_fix_attempts: list[dict]` field to `DaydreamState` dataclass with `field(default_factory=list)`
- Add `step_auto_fix_bugs()` to `DaydreamRunner`; register as step 8, shift steps 8–10 to 9–11
- Implement confidence scorer: pass if any 2 of 3 criteria met (code_bug category, non-empty prevention, pattern ≥ 10 chars)
- Implement `load_ignore_log()`, `prune_ignore_log()`, and `is_ignored()` helpers using `data/daydream_ignore.jsonl`
- Implement dedup check via `gh issue list --search` and `gh pr list --search`
- Implement subprocess invocation with 10-minute timeout
- Implement `DAYDREAM_AUTO_FIX_ENABLED` env var guard (default `true`)
- Add `--dry-run` and `--ignore "pattern"` CLI flags to `main()`
- Post PR link to Telegram via existing `step_post_to_telegram` helper

### 2. Write tests
- **Task ID**: build-tests
- **Depends On**: none
- **Assigned To**: test-builder
- **Agent Type**: test-engineer
- **Parallel**: true
- Create `tests/test_daydream_auto_fix.py`
- Mock subprocess to avoid real Claude Code calls
- Test: 2-of-3 confidence passing triggers subprocess
- Test: 1-of-3 confidence skips
- Test: active ignore entry silences pattern
- Test: expired ignore entry is pruned and re-triggers
- Test: dedup check prevents duplicate PR
- Test: `DAYDREAM_AUTO_FIX_ENABLED=false` disables step
- Test: `--dry-run` mode runs step without subprocess call

### 3. Validate implementation
- **Task ID**: validate-auto-fix
- **Depends On**: build-auto-fix, build-tests
- **Assigned To**: daydream-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/test_daydream_auto_fix.py -v`
- Run `black scripts/daydream.py && ruff check scripts/daydream.py`
- Verify step numbering has no gaps or duplicates
- Verify `DaydreamState` loads from old state.json without `auto_fix_attempts`
- Verify `python scripts/daydream.py --help` shows `--dry-run` and `--ignore`
- Check all success criteria

### 4. Write documentation
- **Task ID**: document-feature
- **Depends On**: validate-auto-fix
- **Assigned To**: daydream-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/daydream-auto-fix.md`
- Add entry to `docs/features/README.md`
- Update `docs/features/daydream.md` with step 8 and dry-run note
- Update `CLAUDE.md` Quick Commands table with `--dry-run` and `--ignore` entries
- Add `DAYDREAM_AUTO_FIX_ENABLED` to `.env.example`

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
- `python scripts/daydream.py --dry-run` — show what would trigger without acting
- `python scripts/daydream.py --ignore "test pattern" && cat data/daydream_ignore.jsonl` — verify ignore log append
