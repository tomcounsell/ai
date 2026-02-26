---
status: In Progress
type: bug
appetite: Small: 1-2 days
owner: Valor
created: 2026-02-26
tracking: https://github.com/tomcounsell/ai/issues/181
---

# Review Validation Layer

## Problem

The `code-reviewer` subagent hallucinates findings during PR reviews. In PR #180, two "BLOCKER" findings were entirely fabricated:

1. **"Double session creation wipes metadata"** - Claimed `sdk_client.py:35` calls `AgentSession.create_session(session_id)`. No such call exists.
2. **"MarkdownV2 escaping breaks all formatting"** - Claimed a function `_escape_mdv2()` uses `re.escape()` incorrectly. No such function exists. The code uses `parse_mode="md"`.

The reviewer also referenced a non-existent file `docs/architecture/model-inventory.md`.

**Current behavior:**
The code-reviewer agent analyzes a PR diff, identifies "issues", classifies them by severity, and posts them directly to GitHub via `gh pr review --request-changes`. There is zero validation between analysis and posting. Hallucinated findings go straight to GitHub.

**Desired outcome:**
Every blocker and major finding is verified against the actual codebase before posting. Findings that reference non-existent functions, files, or code patterns are filtered out. Only verified findings get posted.

## Appetite

**Size:** Small: 1-2 days

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites - this work modifies existing skill files and agent prompts only.

## Solution

### Key Elements

- **Verification instructions in the skill**: Add a mandatory verification step to `/do-pr-review` between analysis and posting
- **Quote-actual-code rule**: Require the reviewer to quote the exact code it's citing (not paraphrased), making hallucinations self-evident
- **Grep-before-post check**: For each blocker, verify the claimed file path and function/pattern exist in the diff or codebase

### Flow

**Code-reviewer analyzes PR** → **For each blocker: verify file exists, function exists, quote actual code** → **Filter unverified findings** → **Post only verified findings**

### Technical Approach

The fix is entirely in the skill instructions and agent prompt. No Python code changes.

1. **Update `.claude/skills/do-pr-review/SKILL.md`** — Add a "Step 5.5: Verify Findings" section between analysis (Step 5) and posting (Step 6):
   - For each blocker finding, run `grep -n "function_name" file_path` to confirm the code exists
   - For each file reference, run `ls file_path` to confirm the file exists
   - Drop any finding where the claimed code cannot be located
   - Require each finding to include a verbatim code quote (not paraphrased)

2. **Update `.claude/agents/code-reviewer.md`** — Add a "Ground Truth Rule" section:
   - "NEVER cite a function, class, or file path you have not personally read with the Read tool in this session"
   - "ALWAYS include the exact code snippet from the file when flagging an issue"
   - "If you cannot find the code you're about to cite, DO NOT include it as a finding"

3. **Add a verification format requirement** — Each blocker must include:
   ```
   **File:** `path/to/file.py` (verified: exists)
   **Line:** 42
   **Code:** `actual_code_from_file`
   **Issue:** [description]
   ```

## Rabbit Holes

- **Building a Python-based validator agent**: Tempting to create a second agent that validates the first agent's findings programmatically. Overkill for this problem - the issue is prompt quality, not architecture.
- **Confidence scoring system**: Adding numerical confidence scores to findings sounds good but doesn't solve the root cause (the reviewer never verified its claims).
- **Automated test execution during review**: Running tests to prove/disprove findings is out of scope - the reviewer should only flag code quality issues, not test behavior.

## Risks

### Risk 1: Verification step slows down reviews
**Impact:** Reviews take longer due to extra grep/verification steps.
**Mitigation:** The verification is only for blocker and major findings (typically < 5 items). The overhead is negligible compared to the time wasted investigating false positives.

### Risk 2: Overly strict verification filters out valid findings
**Impact:** Real issues get dropped because the grep pattern doesn't match exactly.
**Mitigation:** The instruction says "if you cannot verify, downgrade to tech_debt with a note" rather than silently dropping. This preserves signal while preventing false blocker posts.

## No-Gos (Out of Scope)

- No new Python code or tools - this is purely prompt/skill engineering
- No second validator agent - one well-prompted agent is sufficient
- No changes to the code-reviewer's tool access (it already has Read, Glob, Grep)
- No retroactive fixes to past reviews

## Update System

No update system changes required - this modifies skill/agent markdown files only, which are pulled automatically by the update process.

## Agent Integration

No agent integration required - this modifies the code-reviewer agent prompt and the do-pr-review skill instructions, both of which are consumed directly by Claude Code.

## Documentation

- [ ] Update `docs/features/review-workflow-screenshots.md` if it references the review process steps (add note about verification step)
- [ ] Add inline comments in the SKILL.md explaining why the verification step exists (link to issue #181)

## Success Criteria

- [ ] `/do-pr-review` SKILL.md includes a verification step between analysis and posting
- [ ] `.claude/agents/code-reviewer.md` includes ground truth rules
- [ ] Each blocker finding in the posting template requires a verified file path and code quote
- [ ] Findings referencing non-existent files/functions would be caught by the new process
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (skill-updater)**
  - Name: skill-updater
  - Role: Update SKILL.md and agent prompt with verification instructions
  - Agent Type: builder
  - Resume: true

- **Validator (review-checker)**
  - Name: review-checker
  - Role: Verify the updated skill would catch the PR #180 hallucinations
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Update code-reviewer agent prompt
- **Task ID**: build-agent-prompt
- **Depends On**: none
- **Assigned To**: skill-updater
- **Agent Type**: builder
- **Parallel**: true
- Add "Ground Truth Rules" section to `.claude/agents/code-reviewer.md`
- Rules: never cite unread code, always include exact snippets, drop unverifiable findings

### 2. Update do-pr-review skill
- **Task ID**: build-skill-verification
- **Depends On**: none
- **Assigned To**: skill-updater
- **Agent Type**: builder
- **Parallel**: true
- Add "Step 5.5: Verify Findings" to `.claude/skills/do-pr-review/SKILL.md`
- Add verified code quote requirement to the posting template
- Add instruction to downgrade unverifiable blockers to tech_debt

### 3. Validate changes
- **Task ID**: validate-all
- **Depends On**: build-agent-prompt, build-skill-verification
- **Assigned To**: review-checker
- **Agent Type**: validator
- **Parallel**: false
- Read updated files and confirm verification step is present
- Mentally trace the PR #180 hallucination scenario through the new process
- Confirm the three fabricated findings would be caught
- Report pass/fail

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: skill-updater
- **Agent Type**: documentarian
- **Parallel**: false
- Check if `docs/features/review-workflow-screenshots.md` needs updating
- Run `/do-docs` cascade

## Validation Commands

- `grep -c "Ground Truth" .claude/agents/code-reviewer.md` - Confirms ground truth rules added
- `grep -c "Verify Findings" .claude/skills/do-pr-review/SKILL.md` - Confirms verification step added
- `grep -c "verified:" .claude/skills/do-pr-review/SKILL.md` - Confirms verified code quote template exists
