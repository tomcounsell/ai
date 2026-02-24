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

**Root cause 1 — SOUL.md contains SDLC workflow instructions that instruct direct pushes:**

`config/SOUL.md` has an "Orchestration Instructions" section (lines ~460–510) with an inline SDLC loop:
```
1. PLAN: State changes and rationale
2. BUILD: Implement changes
3. TEST: Run pytest, ruff, black
4. REVIEW: Self-check
5. SHIP: Commit with clear message, push
```
"Push" means push to `main`. This is baked into the persona doc, which is wrong — SOUL.md should only describe who Valor is, not how pipelines work. These workflow instructions have no place in a persona doc.

**Root cause 2 — No SDLC enforcement in the SDK wiring:**

`agent/sdk_client.py` builds the system prompt from SOUL.md + completion criteria. Nothing at the SDK session layer enforces the mandatory pipeline. The agent has no hardwired understanding of Issue → Plan → Build → PR.

**Observed failures:**
- `c542d137` — new features in `agent/job_queue.py` and `bridge/summarizer.py` pushed directly to `main`
- Agent self-reported: "I didn't run `/do-build`. The build was done manually — it bypassed the formal SDLC pipeline."

**Desired outcome:**

The mandatory pipeline is enforced at the SDK wiring level — injected into every agent session by `sdk_client.py`, independent of any config file or persona doc:

```
Conversation → Issue → Plan (/do-plan) → Build (/do-build) → PR → Review → Merge
```

No step skipped. No code on `main` without a PR. SOUL.md stays as pure persona.

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **Strip SOUL.md of all workflow/orchestration content**: Remove the entire "Orchestration Instructions" section and "SDLC Pattern" block. SOUL.md = persona, attitude, purpose only.
- **Hardcode SDLC rules in `sdk_client.py`**: Add a `SDLC_WORKFLOW` constant and `load_sdlc_rules()` function directly in the SDK client. These rules are injected into every agent session's system prompt as a third block after SOUL.md and completion criteria.
- **SDK pre-completion check**: Add logic in `ValorAgent` to detect if a code session pushed directly to `main` and block session completion (soft-block, bypassable with `SKIP_SDLC=1`).

### System Prompt Structure (after this change)

```
sdk_client.py builds system prompt as:

[SOUL.md — persona, attitude, purpose, communication style]
---
[SDLC_WORKFLOW — mandatory pipeline rules, hardcoded in sdk_client.py]
---
[Work Completion Criteria — from CLAUDE.md]
```

### SDLC_WORKFLOW content (to hardcode in sdk_client.py)

```
## Mandatory Development Pipeline

ALL code changes follow this pipeline — no exceptions, no hotfixes:

1. ISSUE: A GitHub issue must exist describing the change.
2. PLAN: Run /do-plan {slug} referencing the issue.
   - Raise all open questions during planning.
   - Do not proceed to build until questions are resolved.
3. BUILD: Run /do-build {plan path or issue number}.
   - do-build creates a worktree + session/{slug} branch.
   - Agents implement, test, and lint on the feature branch.
   - do-build opens a PR automatically when done.
4. REVIEW: PR is reviewed (/do-pr-review or human review).
5. MERGE: PR is merged to main by a human.

NEVER commit code directly to main.
NEVER push code to main — all code pushes go to session/{slug} branches.
NEVER skip Issue or Plan — they are mandatory, not optional.

Plan/doc changes (.md, .json, .yaml) may be committed directly to main.
Code changes (.py, .js, .ts) never go directly to main.
```

### Pre-completion check in `ValorAgent`

In `sdk_client.py`, add a check that fires when the session ends:
- Read `data/sessions/{session_id}/sdlc_state.json`
- If `code_modified: true`: run `git rev-parse --abbrev-ref HEAD`
- If current branch is `main`: emit a block message instructing the agent to create a branch and PR
- Soft-block: honor `SKIP_SDLC=1` env var (same as Stop hook pattern)

### SOUL.md cleanup

Remove from `config/SOUL.md`:
- The entire `## Orchestration Instructions` section
- The `### SDLC Pattern (Mandatory for Code Changes)` block
- The `### Parallel Execution` and `### Validation Loop` sub-sections (if workflow-specific)
- Any other non-persona content

Keep in SOUL.md:
- Who Valor is, communication style, values, personality
- Relationship to Valor Engels
- Response tone and format preferences

## Rabbit Holes

- **Enforcing issue/plan existence programmatically before session start**: Too complex, too many false positives. Behavioral instruction at session level is sufficient.
- **Per-project SDLC config**: Don't add project-level overrides. Universal enforcement.
- **Subagent enforcement**: Builder subagents run inside worktrees on feature branches — they're already in the pipeline. Don't enforce the full outer pipeline on inner subagent sessions.
- **Retroactive PR for past direct commits**: Not in scope.
- **Changing SOUL.md tone or persona**: Only remove workflow content, do not rewrite persona.

## Risks

### Risk 1: Removing SDLC from SOUL.md breaks builder subagent behavior
**Impact:** Builder agents that rely on the SOUL.md SDLC loop for test/fix iteration lose their behavioral guidance
**Mitigation:** Builder agents are invoked via `/do-build` which has its own SKILL.md with the test-fail loop. SOUL.md's SDLC section is redundant for builders. Verify by checking that do-build builder agents cite SKILL.md, not SOUL.md, for test loop behavior.

### Risk 2: Hardcoded SDLC rules drift out of sync with actual skills
**Impact:** The hardcoded prompt says `/do-plan` but the skill was renamed or moved
**Mitigation:** The SDLC_WORKFLOW block references skills by name only. Skills are stable (`/do-plan`, `/do-build`). Keep the block short — pipeline stages only, not implementation detail.

### Risk 3: Pre-completion check fires inside `/do-build` worktrees
**Impact:** A builder agent inside a worktree on a feature branch triggers the main-branch check incorrectly
**Mitigation:** The check compares current branch to `main`. Worktree branches are `session/{slug}` — they will never match `main`. The check is safe for all worktree sessions.

## No-Gos (Out of Scope)

- No changes to SOUL.md persona/attitude content — only remove workflow sections
- No changes to `/do-build`, `/do-plan`, or other skills
- No changes to bridge or MCP servers
- No per-project SDLC config
- No enforcement on subagent sessions
- No retroactive cleanup of existing direct-to-main commits

## Update System

SOUL.md and sdk_client.py are part of the repo synced by `git pull` in `scripts/remote-update.sh`. No update system changes required — changes propagate automatically on next update.

## Agent Integration

No agent integration changes required. This is purely internal to `agent/sdk_client.py` and `config/SOUL.md`. No MCP servers, no bridge, no `.mcp.json`.

## Documentation

- [ ] Update `docs/features/sdlc-enforcement.md` — add "Agent SDK enforcement" section describing the SDLC_WORKFLOW injection and pre-completion check
- [ ] Add note to `docs/features/README.md` if a new doc is created

## Success Criteria

**SOUL.md:**
- [ ] `config/SOUL.md` contains no SDLC workflow instructions, no "SHIP: push", no pipeline steps
- [ ] `config/SOUL.md` contains only persona/attitude/purpose content

**SDK wiring:**
- [ ] `agent/sdk_client.py` has a `SDLC_WORKFLOW` constant with the mandatory pipeline text
- [ ] `load_system_prompt()` injects `SDLC_WORKFLOW` between SOUL.md and completion criteria
- [ ] `ValorAgent` has a pre-completion check that blocks code-on-main sessions
- [ ] `SKIP_SDLC=1` bypasses the pre-completion check

**Behavioral:**
- [ ] A session that modifies a `.py` file and is on `main` branch is blocked at completion
- [ ] A session that modifies only `.md` files and pushes to `main` is NOT blocked
- [ ] A session on a `session/{slug}` branch (inside do-build) is NOT blocked regardless of file type

**Quality:**
- [ ] `pytest tests/unit/` passes
- [ ] `ruff check .` passes
- [ ] `black --check .` passes

## Team Orchestration

### Team Members

- **Builder (sdk)**
  - Name: sdk-builder
  - Role: Strip SOUL.md of orchestration content; add SDLC_WORKFLOW to sdk_client.py; add pre-completion check; write tests
  - Agent Type: builder
  - Resume: true

- **Validator (enforcement)**
  - Name: enforcement-validator
  - Role: Verify SOUL.md is persona-only; verify SDK wiring contains correct pipeline instructions; run behavioral tests and full test suite
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Update docs/features/sdlc-enforcement.md with SDK enforcement section
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Strip SOUL.md orchestration content
- **Task ID**: build-soul-cleanup
- **Depends On**: none
- **Assigned To**: sdk-builder
- **Agent Type**: builder
- **Parallel**: true
- Read `config/SOUL.md` in full
- Remove the `## Orchestration Instructions` section and all sub-sections (Task Classification, SDLC Pattern, Parallel Execution, Validation Loop, Response Pattern)
- Preserve all persona/attitude/purpose/communication content
- Do NOT rewrite or rephrase persona content — only delete the workflow sections

### 2. Add SDLC_WORKFLOW to sdk_client.py
- **Task ID**: build-sdk-wiring
- **Depends On**: none
- **Assigned To**: sdk-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `SDLC_WORKFLOW` module-level constant to `agent/sdk_client.py` with the mandatory pipeline text (see Solution section)
- Update `load_system_prompt()` to inject it between SOUL.md content and completion criteria
- Add `_check_no_direct_main_push(session_id)` method to `ValorAgent`
- Call the check from the appropriate session completion path
- Write `tests/unit/test_sdk_client_sdlc.py` covering: code-on-main blocked; docs-on-main allowed; code-on-feature-branch allowed; SKIP_SDLC=1 bypasses

### 3. Validate enforcement
- **Task ID**: validate-enforcement
- **Depends On**: build-soul-cleanup, build-sdk-wiring
- **Assigned To**: enforcement-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify SOUL.md contains no workflow/SDLC instructions
- Verify SOUL.md still reads as a coherent persona document
- Run SDK behavioral tests
- Run `pytest tests/unit/` — all pass
- Run `ruff check .` and `black --check .` — all pass

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-enforcement
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/sdlc-enforcement.md` — add "Agent SDK Enforcement" section

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: enforcement-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite and verify all success criteria met

## Validation Commands

- `grep -c "SDLC\|SHIP\|pipeline\|do-build\|do-plan" config/SOUL.md` — should return 0
- `grep -c "SDLC_WORKFLOW" agent/sdk_client.py` — should return ≥ 2 (definition + usage)
- `python -c "from agent.sdk_client import load_system_prompt; p = load_system_prompt(); assert 'Mandatory Development Pipeline' in p; print('ok')"` — passes
- `pytest tests/unit/test_sdk_client_sdlc.py -v` — all pass
- `pytest tests/unit/ -q` — all pass
- `ruff check .` — no errors
- `black --check .` — no changes

## Open Questions

1. **Soft-block vs hard-block for the pre-completion check**: Should it match the Stop hook pattern (soft-block, bypassable with `SKIP_SDLC=1`) or be a hard block that cannot be overridden? Recommend soft-block for consistency.

2. **Where exactly in `ValorAgent` does the pre-completion check fire?** The SDK session runs as an async generator yielding events. Is there a clean "session end" hook, or does it need to wrap the `run()` method's finally block?
