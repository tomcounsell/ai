---
status: Ready
type: feature
appetite: Small
owner: Valor
created: 2026-03-12
tracking: https://github.com/tomcounsell/ai/issues/365
last_comment_id:
---

# PR Reference Routing and PM Telegram Guide

## Problem

Two gaps in the current system:

**Current behavior:**
1. The fast-path regex in `bridge/routing.py` only handles `issue N` and bare `#N` patterns. Sending `PR 363` falls through to LLM classification instead of fast-pathing to SDLC. Additionally, bare `#N` conflicts with Telegram's native hashtag/topic feature -- the `#` is consumed by Telegram and never reaches the bot.
2. SDLC's Step 1 (`SKILL.md`) only resolves issues via `gh issue view`. It has no logic to detect a PR reference, inspect PR state, and resume from the correct pipeline stage.
3. No PM-facing documentation exists describing how to trigger pipelines from Telegram.

**Desired outcome:**
- `PR 363` fast-paths to SDLC and the skill detects it's a PR, checks state, and dispatches correctly
- Bare `#N` pattern is removed (broken on Telegram)
- A PM guide documents all interaction patterns

## Prior Art

- **PR #321**: Observer Agent — replaced auto-continue/summarizer with stage-aware SDLC steerer. Related but focused on pipeline progression, not routing/intake.
- No prior issues found related to PR reference routing or PM guide documentation.

## Data Flow

1. **Entry point**: PM sends `PR 363` in Telegram
2. **Bridge routing** (`bridge/routing.py`): `classify_work_request()` matches the fast-path regex and returns `"sdlc"`
3. **SDLC skill** (`.claude/skills/sdlc/SKILL.md`): Step 1 detects "PR" keyword, runs `gh pr view 363` to get branch, review state, check status
4. **SDLC dispatch**: Based on PR state (tests failing → `/do-patch`, review blockers → `/do-patch`, all green → `/do-docs`), dispatches the correct sub-skill
5. **Output**: Agent resumes pipeline from the PR's current position

## Architectural Impact

- **New dependencies**: None
- **Interface changes**: Regex pattern in `classify_work_request()` changes; SDLC SKILL.md gains PR-aware resolution
- **Coupling**: No change — same routing path, extended pattern
- **Data ownership**: No change
- **Reversibility**: Trivial — revert regex and SKILL.md

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **Fast-path regex update**: Replace `^(?:issue\s+#?\d+|#\d+)$` with `^(?:issue|pr)\s+#?\d+$` — drops bare `#N`, adds `pr N` support
- **SDLC PR resolution**: Step 1 in SKILL.md detects issue vs PR reference and uses appropriate `gh` command
- **PM Telegram guide**: New doc at `docs/features/telegram-pm-guide.md` covering message patterns, session resumption, and signals

### Flow

**PM sends "PR 363"** → routing regex matches → classified as `sdlc` → SDLC Step 1 runs `gh pr view 363` → Step 2 uses PR state for assessment → Step 3 dispatches correct sub-skill

### Technical Approach

- Regex change is a single-line edit in `routing.py` line 367
- SKILL.md Step 1 gets a conditional: if input matches `pr N` pattern, use `gh pr view` instead of `gh issue view`
- PM guide is a standalone doc with tables for message patterns, session actions, and signals

## Failure Path Test Strategy

### Exception Handling Coverage
- No exception handlers in scope of the regex change
- SKILL.md is documentation, not executable code

### Empty/Invalid Input Handling
- The regex already requires at least one digit after the keyword — empty/whitespace inputs won't match
- Existing fallback to LLM classification handles edge cases

### Error State Rendering
- Not applicable — routing is internal, no user-visible rendering changes

## Rabbit Holes

- Building complex PR-vs-issue detection heuristics — `gh issue view` already works for PRs (GitHub treats PRs as issues), so only `gh pr view` is needed for PR-specific state (reviews, checks)
- Adding support for other reference patterns like commit SHAs or branch names — separate concern
- Trying to make `#N` work on Telegram — it's a platform limitation, not worth fighting

## Risks

### Risk 1: Regex too narrow
**Impact:** Some valid PR references don't match (e.g., `pull request 363`)
**Mitigation:** Keep it simple — `pr N` and `issue N` are the documented patterns. LLM fallback catches anything else.

## Race Conditions

No race conditions identified — routing is synchronous and stateless.

## No-Gos (Out of Scope)

- Do NOT keep the bare `#N` pattern — it conflicts with Telegram topics
- Do NOT add complex PR-vs-issue detection heuristics
- Do NOT create a separate routing path for PRs — they use the same SDLC pipeline
- Do NOT modify the LLM classification prompt — the fast path handles the new pattern

## Update System

No update system changes required — routing and skill changes deploy via normal git pull.

## Agent Integration

No agent integration required — this is a bridge-internal routing change plus skill documentation update. No new tools or MCP servers needed. The `gh` CLI is already available.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/telegram-pm-guide.md` describing PM interaction patterns
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Update inline comments in `routing.py` to document the extended regex

### Inline Documentation
- [ ] Code comments on the regex pattern explaining why bare `#N` is excluded

## Success Criteria

- [ ] `PR 363` and `pr 363` fast-path to SDLC classification (not LLM fallback)
- [ ] `issue 363` and `issue #363` still work as before
- [ ] Bare `#363` no longer matches the fast path
- [ ] SDLC SKILL.md documents PR-aware resolution in Step 1
- [ ] `docs/features/telegram-pm-guide.md` exists with message patterns, session resumption, and signals
- [ ] `docs/features/README.md` has entry for the PM guide
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (routing-and-docs)**
  - Name: routing-builder
  - Role: Update regex, SKILL.md, and create PM guide
  - Agent Type: builder
  - Resume: true

- **Validator (routing-and-docs)**
  - Name: routing-validator
  - Role: Verify regex behavior and doc completeness
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Update fast-path regex in routing.py
- **Task ID**: build-routing
- **Depends On**: none
- **Assigned To**: routing-builder
- **Agent Type**: builder
- **Parallel**: true
- Replace regex on line 367 from `^(?:issue\s+#?\d+|#\d+)$` to `^(?:issue|pr)\s+#?\d+$`
- Update the log message to say "issue/PR reference" instead of "issue reference"
- Update the comment on line 366 to mention PR support and explain why bare `#N` is excluded

### 2. Update SDLC SKILL.md Step 1
- **Task ID**: build-sdlc-skill
- **Depends On**: none
- **Assigned To**: routing-builder
- **Agent Type**: builder
- **Parallel**: true
- Add PR-aware resolution logic to Step 1: detect if input is "PR N" vs "issue N"
- For PR references: use `gh pr view N` to get branch, review state, check status
- Document that PR state informs Step 2 assessment (skip to correct stage)

### 3. Create PM Telegram guide
- **Task ID**: build-pm-guide
- **Depends On**: none
- **Assigned To**: routing-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `docs/features/telegram-pm-guide.md` with message patterns, session resumption, and signal tables
- Add entry to `docs/features/README.md` index

### 4. Validate changes
- **Task ID**: validate-all
- **Depends On**: build-routing, build-sdlc-skill, build-pm-guide
- **Assigned To**: routing-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify regex matches `pr 363`, `PR 363`, `issue 363`, `issue #363`
- Verify regex does NOT match `#363`
- Verify SKILL.md has PR resolution in Step 1
- Verify PM guide exists and README index entry is present
- Run all validation commands

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| PM guide exists | `test -f docs/features/telegram-pm-guide.md` | exit code 0 |
| README entry | `grep -c "telegram-pm-guide" docs/features/README.md` | output > 0 |
| Regex matches PR | `python -c "import re; assert re.match(r'^(?:issue|pr)\s+#?\d+$', 'pr 363')"` | exit code 0 |
| Regex rejects bare hash | `python -c "import re; assert not re.match(r'^(?:issue|pr)\s+#?\d+$', '#363')"` | exit code 0 |
