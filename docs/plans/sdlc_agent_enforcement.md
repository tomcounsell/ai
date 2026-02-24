---
status: Planning
type: chore
appetite: Medium
owner: Valor
created: 2026-02-24
tracking: https://github.com/tomcounsell/ai/issues/160
---

# SDLC Agent Enforcement

## Problem

The Valor agent bypasses the full SDLC pipeline and commits code directly to `main`. Two root causes:

**Root cause 1 — SOUL.md instructs direct pushes:**
The `SDLC Pattern` section in `config/SOUL.md` describes an inline loop:
```
1. PLAN: State changes and rationale
2. BUILD: Implement changes
3. TEST: Run pytest, ruff, black
4. REVIEW: Self-check
5. SHIP: Commit with clear message, push
```
"Push" here means push to `main`. This is baked into the agent's core behavioral instructions. It's why the agent treats every code change as a hotfix.

**Root cause 2 — No enforcement at the SDK session layer:**
The `sdk_client.py` system prompt appends SOUL.md and completion criteria, but nothing at the session level enforces the mandatory pipeline. The repo-level Claude Code hooks (`validate_sdlc_on_stop.py`) fire correctly but only catch quality checks — they don't block the wrong pipeline path.

**Observed failures:**
- `c542d137` — new features in `agent/job_queue.py` and `bridge/summarizer.py` pushed directly to `main`
- `1312d364` — script fix pushed directly to `main`
- At 09:55 the agent self-reported: "I didn't run `/do-build`. The build was done manually — it bypassed the formal SDLC pipeline."

**Desired outcome:**
Every code change goes through the mandatory pipeline, enforced at the agent's behavioral layer:

```
Conversation → Issue → Plan (/do-plan) → Build (/do-build) → PR → Review → Merge
```

No step may be skipped. No code reaches `main` without a PR.

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1 (confirm exact wording of mandatory flow in SOUL.md)
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **SOUL.md rewrite of SDLC section**: Replace the inline PLAN/BUILD/TEST/SHIP loop with the mandatory pipeline. Remove "SHIP: push". Add explicit instructions for the full Issue → Plan → Build → PR flow and when each skill is invoked.
- **SOUL.md: remove hotfix language**: Search SOUL.md for any language permitting direct commits or pushes to `main`. Remove it entirely.
- **sdk_client.py completion check**: Add a pre-completion hook in `ValorAgent` that detects if a code commit was pushed directly to `main` in the current session and blocks session completion with a clear instruction.
- **CLAUDE.md workflow section**: The project-level CLAUDE.md already has `Phase 1: Conversation` with hotfix language. Update it to match the new mandatory flow (this is the repo's own CLAUDE.md, read by Claude Code sessions in this repo).

### Flow

```
Code request arrives
        ↓
Is there a GitHub issue?
  No  → Create issue first
  Yes ↓
Is there a plan?
  No  → /do-plan {slug}
  Yes ↓
/do-build {plan or issue}
        ↓
PR created on session/{slug} branch
        ↓
PR reviewed and merged
        ↓
main updated
```

### Technical Approach

**SOUL.md changes** (primary fix):

Replace the current SDLC Pattern block:
```
### SDLC Pattern (Mandatory for Code Changes)
1. PLAN: State changes
2. BUILD: Implement
3. TEST: pytest, ruff, black
4. REVIEW: self-check
5. SHIP: Commit and push
```

With the mandatory pipeline:
```
### Mandatory Development Pipeline (No Exceptions)

ALL code changes — no matter how small — follow this pipeline:

1. ISSUE: Ensure a GitHub issue exists describing the change
2. PLAN: Run /do-plan {slug} referencing the issue
   - Raise all open questions during planning
   - Resolve questions before proceeding to build
3. BUILD: Run /do-build {plan path or issue number}
   - do-build creates a worktree + session/{slug} branch
   - Agents implement, test, and lint on the feature branch
   - do-build opens a PR automatically
4. REVIEW: PR is reviewed (human or /do-pr-review)
5. MERGE: PR merged to main by human

NEVER commit code directly to main.
NEVER push to main — all pushes go to session/{slug} branches.
NEVER skip the Issue or Plan phases — they are not optional.
```

**sdk_client.py completion check**:

Add a `_check_no_direct_main_push()` method called at session end (in the Stop signal handler or equivalent). The check:
1. Reads `sdlc_state.json` for the session — if `code_modified: true`, verify the current git branch is NOT `main`
2. If on `main` with code modified: block completion, print instruction to create a branch and PR

This is a belt-and-suspenders layer on top of the SOUL.md behavioral change.

**CLAUDE.md update** (project-level, this repo):

Replace `Phase 1: Conversation` hotfix line with the mandatory pipeline. Already attempted and reverted — do it correctly this time as part of this plan.

## Rabbit Holes

- **Enforcing issue/plan existence programmatically**: Don't try to verify a GitHub issue exists before allowing a commit. Behavioral instruction is enough — enforcement at the push level is the backstop.
- **Subagent enforcement**: Builder subagents run on feature branches already (worktrees). Don't try to enforce the full pipeline on subagent sessions — they're already inside a `/do-build` pipeline.
- **Retroactive cleanup**: Don't attempt to move existing direct-to-main commits into PRs. Accept the history, enforce going forward.
- **Per-repo config**: Don't add per-repo overrides for the mandatory flow. It's universal.

## Risks

### Risk 1: SOUL.md change breaks conversational code snippets
**Impact:** Agent refuses to write a quick code example or explain a function in conversation because it tries to invoke the full pipeline
**Mitigation:** The mandatory pipeline applies to *committing and pushing* code changes, not to writing code in conversation. Clarify this explicitly in the SOUL.md wording: "When Valor *writes code to the filesystem*, the pipeline applies. Explaining or drafting code in conversation does not require a pipeline."

### Risk 2: Legitimate docs-only commits to main blocked
**Impact:** Plan updates, README edits, config tweaks get blocked by the sdk_client.py check
**Mitigation:** The check uses the same `sdlc_state.json` logic as the Stop hook — if `code_modified` is false (no `.py`/`.js`/`.ts` files written), the check passes. Docs-only pushes to main are unaffected.

### Risk 3: /do-build itself needs to push to main for plan migration
**Impact:** `migrate_completed_plan.py` commits plan deletions directly to main
**Mitigation:** Plan migration writes only `.md` deletions — not code files. The `sdlc_state.json` check will see `code_modified: false` and allow it.

## No-Gos (Out of Scope)

- No enforcement on subagent sessions (builder agents inside `/do-build`)
- No programmatic check that a GitHub issue exists before planning
- No retroactive cleanup of existing direct-to-main commits
- No per-repo overrides to the mandatory pipeline
- No changes to the `/do-build` worktree or PR flow (already correct)
- No new MCP tools or bridge changes

## Update System

The update skill syncs `.claude/` hardlinks across machines. SOUL.md and sdk_client.py are synced via the standard `git pull` flow in `scripts/remote-update.sh`. No update system changes required.

## Agent Integration

No agent integration changes required — this is a behavioral instruction change (SOUL.md) and a session-level enforcement change (sdk_client.py). No MCP server changes, no bridge changes, no `.mcp.json` changes.

## Documentation

- [ ] Update `docs/features/sdlc-enforcement.md` — add section covering agent-level enforcement (SOUL.md + sdk_client.py check)
- [ ] Update `docs/features/README.md` if a new doc is created

## Success Criteria

- [ ] SOUL.md no longer contains "SHIP: push" or any instruction to commit/push directly to main
- [ ] SOUL.md contains the mandatory pipeline: Issue → Plan → Build → PR → Review → Merge
- [ ] `sdk_client.py` blocks session completion when code was modified on `main` branch directly
- [ ] CLAUDE.md `Phase 1` no longer mentions hotfixes
- [ ] A test session that modifies a `.py` file and tries to push to `main` is blocked at the SDK layer
- [ ] A test session that modifies only `.md` files and pushes to `main` is NOT blocked
- [ ] Existing tests pass (`pytest tests/unit/`)
- [ ] `ruff check .` and `black --check .` pass

## Team Orchestration

### Team Members

- **Builder (soul-sdk)**
  - Name: soul-sdk-builder
  - Role: Update SOUL.md SDLC section, add sdk_client.py completion check, update CLAUDE.md workflow
  - Agent Type: builder
  - Resume: true

- **Validator (enforcement)**
  - Name: enforcement-validator
  - Role: Verify SOUL.md contains correct pipeline instructions, test sdk_client.py check with mock sessions, run full test suite
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Update sdlc-enforcement.md to cover agent-level enforcement
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Update SOUL.md and CLAUDE.md
- **Task ID**: build-soul
- **Depends On**: none
- **Assigned To**: soul-sdk-builder
- **Agent Type**: builder
- **Parallel**: true
- Replace the inline SDLC loop in `config/SOUL.md` with the mandatory pipeline block
- Remove all language in SOUL.md that permits direct commits or pushes to `main`
- Update `CLAUDE.md` Phase 1 to remove hotfix exception and state the mandatory pipeline

### 2. Add sdk_client.py completion check
- **Task ID**: build-sdk-check
- **Depends On**: none
- **Assigned To**: soul-sdk-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `_check_no_direct_main_push(session_id)` method to `ValorAgent` in `agent/sdk_client.py`
- Call it from the session completion path
- Reads `sdlc_state.json`: if `code_modified: true` and current branch is `main`, block with instruction
- Write unit tests in `tests/unit/test_sdk_client_sdlc.py`

### 3. Validate enforcement
- **Task ID**: validate-enforcement
- **Depends On**: build-soul, build-sdk-check
- **Assigned To**: enforcement-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify SOUL.md no longer contains direct-push instructions
- Verify SOUL.md contains the mandatory pipeline (Issue → Plan → Build → PR)
- Test sdk_client.py check: code session on main → blocked; docs session on main → allowed; code session on feature branch → allowed
- Run `pytest tests/unit/` — all pass
- Run `ruff check .` and `black --check .` — all pass

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-enforcement
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/sdlc-enforcement.md` to add "Agent-Level Enforcement" section covering SOUL.md changes and sdk_client.py check

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: enforcement-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify all success criteria met
- Generate final report

## Validation Commands

- `grep -n "SHIP\|push to main\|hotfix\|direct.*main" config/SOUL.md` — should return no results
- `grep -n "mandatory\|Issue.*Plan\|do-plan\|do-build" config/SOUL.md` — should return the pipeline block
- `python -c "from agent.sdk_client import ValorAgent; print('sdk imports ok')"` — verify no import errors
- `pytest tests/unit/test_sdk_client_sdlc.py -v` — SDK enforcement tests pass
- `pytest tests/unit/ -q` — full unit suite passes
- `ruff check .` — no errors
- `black --check .` — no changes needed

## Open Questions

1. Should the sdk_client.py check hard-block (raise exception, session cannot end) or soft-block (log warning, allow override with `SKIP_SDLC=1`)? The Stop hook uses soft-block (exit 2, user can bypass). Recommend matching that pattern for consistency.
2. The SOUL.md "SDLC Pattern" section also drives inline test loops during `/do-build` subagent sessions. Does replacing it risk breaking the test-fail loop behavior that builder agents currently use? Or is that loop driven by `do-build/SKILL.md` directly and SOUL.md is redundant there?
