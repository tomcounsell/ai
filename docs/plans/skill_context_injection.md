---
status: Planning
type: feature
appetite: Medium
owner: Valor
created: 2026-03-16
tracking: https://github.com/tomcounsell/ai/issues/420
last_comment_id:
---

# Skill Context Injection and Sub-Skill Decomposition

## Problem

Skills are monolithic static templates with unresolved placeholders like `{pr_number}`, `{owner}/{repo}`, and `{slug}`. When the Observer dispatches a skill, the worker must derive these values from context clues (coaching messages, git state, `gh` CLI calls). This causes:

**Current behavior:**
- Workers hallucinate or skip steps when they fail to resolve a placeholder (e.g., the PR review branch-checkout bug at d77d2b0d)
- 300-600 line skills mix mechanical setup (checkout, mkdir) with judgment work (review code, write docs), causing cognitive overload and lost-place errors
- No sub-skill concept exists — every skill is a flat document

**Desired outcome:**
1. Skills receive pre-resolved context as `SDLC_*` environment variables injected by the SDK client
2. Large skills decompose into focused sub-skills, each with a single responsibility
3. Observer coaching messages include resolved variables for redundancy

## Prior Art

No prior closed issues or merged PRs found addressing skill context injection directly.

Related work referenced in the issue:
- **#397**: Project context propagation audit — same class of problem (context loss between layers) but focused on project config, not skill variables
- **#328**: Typed skill outcomes — structured contracts between skills, relevant pattern for sub-skill interfaces
- **#329**: Context fidelity modes — right-sized context for sub-agents
- **d77d2b0d**: PR review branch-checkout fix — the incident that motivated this issue
- **#181**: Hallucinated PR review findings — downstream symptom of context loss

## Data Flow

1. **Entry point**: Telegram message triggers `AgentSession` creation in `bridge/telegram_bridge.py`
2. **AgentSession fields populated**: `issue_url`, `plan_url`, `pr_url`, `branch_name`, `work_item_slug` are set during pipeline progression
3. **SDK client spawns Claude Code**: `agent/sdk_client.py` builds env vars (`GH_REPO`, `VALOR_SESSION_ID`, `JOB_ID`) and passes them to the subprocess
4. **Observer steers pipeline**: `bridge/observer.py` determines next stage, constructs coaching message with skill command (e.g., `/do-pr-review`)
5. **Claude Code expands skill**: Loads `SKILL.md`, encounters `{pr_number}` placeholders, must derive values from env/git/gh CLI
6. **Gap**: Session fields (pr_url, branch_name, slug) are NOT injected as env vars — the worker must re-derive them

## Architectural Impact

- **New dependencies**: None — uses existing `AgentSession` fields and env var injection pattern
- **Interface changes**: New `SDLC_*` env vars available to all skills; sub-skills invoked by parent skills
- **Coupling**: *Decreases* coupling — skills no longer depend on implicit context resolution
- **Data ownership**: No change — `AgentSession` remains the source of truth
- **Reversibility**: High — env vars are additive (skills can fall back to manual resolution), sub-skills are opt-in

## Appetite

**Size:** Medium

**Team:** Solo dev, PM for scope alignment

**Interactions:**
- PM check-ins: 1-2 (scope alignment on which skills to decompose first)
- Review rounds: 1 (code review)

## Prerequisites

No prerequisites — this work uses existing session fields and env var patterns.

## Solution

### Key Elements

- **SDLC env var injection**: Extend SDK client's env construction to include `SDLC_*` variables derived from AgentSession
- **Skill placeholder audit**: Catalog every `{variable}` across all 28 skills, map to session fields
- **Sub-skill decomposition**: Split `/do-pr-review` (362 lines) as proof-of-concept into focused sub-skills
- **Coaching message enrichment**: Include resolved variables in Observer coaching messages

### Flow

**Observer decides next stage** → coaching message includes resolved vars → **SDK client spawns Claude Code** with `SDLC_*` env vars → **Skill loads** and reads `$SDLC_PR_NUMBER` instead of guessing → **Sub-skill dispatched** with focused context → **Sub-skill completes** single responsibility

### Technical Approach

- Add `SDLC_*` env vars in `sdk_client.py` at the same location as existing `GH_REPO`/`VALOR_SESSION_ID` injection (~line 682-693)
- Derive values from `AgentSession` fields: `pr_url` → `SDLC_PR_NUMBER` + `SDLC_PR_BRANCH`, `work_item_slug` → `SDLC_SLUG`, `plan_url` → `SDLC_PLAN_PATH`, `issue_url` → `SDLC_ISSUE_NUMBER`
- Update Observer's coaching message construction (~line 637-652) to append resolved variables
- Create `do-pr-review/sub-skills/` directory with focused sub-skill files
- Update all `{pr_number}` references in skills to use `$SDLC_PR_NUMBER` with fallback

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] SDK client env injection: test that missing session fields produce no env var (not empty string)
- [ ] Observer coaching: test that missing fields don't produce malformed coaching messages

### Empty/Invalid Input Handling
- [ ] `SDLC_PR_NUMBER` with None pr_url: env var should be absent, not "None"
- [ ] `SDLC_SLUG` with None work_item_slug: env var should be absent
- [ ] Skills must still work when `SDLC_*` vars are absent (backward compatibility)

### Error State Rendering
- [ ] Not applicable — no user-visible output changes

## Test Impact

No existing tests affected — this is a greenfield feature adding new env var injection logic and sub-skill files. No prior test coverage exists for skill context injection or sub-skill decomposition.

## Rabbit Holes

- **Rewriting all 28 skills at once**: Only decompose `/do-pr-review` as proof-of-concept. Other skills can be migrated incrementally in follow-up work
- **Dynamic skill loading**: Don't build a framework for dynamically composing sub-skills at runtime — static file-based dispatch is sufficient
- **Template engine**: Don't build a Jinja-style template processor for skills — `$ENV_VAR` references are simpler and more debuggable than `{placeholder}` substitution

## Risks

### Risk 1: Stale env vars
**Impact:** If session fields are outdated (e.g., PR was force-pushed, branch renamed), env vars carry stale data
**Mitigation:** Skills should treat env vars as hints, not gospel. Critical operations (checkout, review posting) should still validate against live `gh` state

### Risk 2: Sub-skill invocation failure
**Impact:** If Claude Code can't load a sub-skill file, the parent skill hangs or fails
**Mitigation:** Parent skill includes inline fallback instructions if sub-skill loading fails. Keep sub-skills as guidance documents, not hard dependencies

## Race Conditions

No race conditions identified — env var injection happens synchronously in `sdk_client.py` before subprocess spawn, and session fields are read-only at that point.

## No-Gos (Out of Scope)

- Decomposing all skills — only `/do-pr-review` as proof-of-concept
- Changing the Observer's decision logic — only enriching its coaching messages
- Building a sub-skill registry or discovery mechanism
- Modifying `AgentSession` model fields — only reading existing ones
- Replacing `GH_REPO` with `SDLC_REPO` — they complement each other per issue constraints

## Update System

No update system changes required — this feature modifies internal skill files and SDK client code. No new dependencies or config files. The `/update` skill will pull the changes automatically.

## Agent Integration

No agent integration required — this is an internal pipeline improvement. The MCP servers and bridge don't need changes. The SDK client changes are transparent to the agent (it just sees new env vars).

## Documentation

### Feature Documentation
- [ ] Create `docs/features/skill-context-injection.md` describing the env var injection pattern and sub-skill convention
- [ ] Add entry to `docs/features/README.md` index table

### Inline Documentation
- [ ] Code comments on the SDLC env var injection block in `sdk_client.py`
- [ ] README in `do-pr-review/sub-skills/` explaining the sub-skill pattern

## Success Criteria

- [ ] SDK client injects `SDLC_PR_NUMBER`, `SDLC_PR_BRANCH`, `SDLC_SLUG`, `SDLC_PLAN_PATH`, `SDLC_ISSUE_NUMBER`, `SDLC_REPO` env vars when session has corresponding fields
- [ ] Missing session fields produce no env var (not empty string or "None")
- [ ] `/do-pr-review` decomposed into sub-skills: checkout, code-review, screenshot, post-review
- [ ] Sub-skills reference `$SDLC_PR_NUMBER` instead of `{pr_number}`
- [ ] Observer coaching messages include resolved context variables
- [ ] Existing SDLC pipeline works without regressions (backward compatibility)
- [ ] Skills still work when `SDLC_*` env vars are absent (fallback to manual resolution)
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (sdk-injection)**
  - Name: sdk-builder
  - Role: Implement SDLC env var injection in sdk_client.py and Observer coaching enrichment
  - Agent Type: builder
  - Resume: true

- **Builder (skill-decomposition)**
  - Name: skill-builder
  - Role: Decompose /do-pr-review into sub-skills, update placeholder references
  - Agent Type: builder
  - Resume: true

- **Builder (skill-audit)**
  - Name: audit-builder
  - Role: Audit all skills for placeholder variables, document mapping to session fields
  - Agent Type: builder
  - Resume: true

- **Validator (pipeline)**
  - Name: pipeline-validator
  - Role: Verify end-to-end pipeline still works, env vars injected correctly
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Create feature documentation
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. SDLC Env Var Injection
- **Task ID**: build-sdk-injection
- **Depends On**: none
- **Assigned To**: sdk-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `SDLC_*` env var injection to `agent/sdk_client.py` after existing env var block (~line 693)
- Extract PR number from `session.pr_url`, slug from `session.work_item_slug`, issue number from `session.issue_url`, branch from `session.branch_name`
- Only set env var when field is non-None and non-empty
- Add unit tests for the extraction logic

### 2. Observer Coaching Enrichment
- **Task ID**: build-coaching
- **Depends On**: none
- **Assigned To**: sdk-builder
- **Agent Type**: builder
- **Parallel**: true
- Update coaching message construction in `bridge/observer.py` (~line 637-652) to append resolved context variables
- Format: `"Continue with /do-pr-review. SDLC_PR_NUMBER=220, SDLC_SLUG=my-feature, SDLC_BRANCH=session/my-feature"`
- Only include variables that have values

### 3. Skill Placeholder Audit
- **Task ID**: build-audit
- **Depends On**: none
- **Assigned To**: audit-builder
- **Agent Type**: builder
- **Parallel**: true
- Scan all 28 skills for `{variable}` placeholders and `$ENV_VAR` references
- Create audit document mapping each placeholder to its corresponding `SDLC_*` env var
- Identify decomposition candidates (skills >150 lines with distinct phases)

### 4. Sub-Skill Decomposition (/do-pr-review)
- **Task ID**: build-decompose
- **Depends On**: build-audit
- **Assigned To**: skill-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `do-pr-review/sub-skills/` directory with: `checkout.md`, `code-review.md`, `screenshot.md`, `post-review.md`
- Update parent `SKILL.md` to orchestrate sub-skills and pass context
- Replace `{pr_number}` with `$SDLC_PR_NUMBER` (with fallback instructions)
- Ensure `/do-skills-audit` can validate sub-skill files

### 5. Validate Pipeline
- **Task ID**: validate-pipeline
- **Depends On**: build-sdk-injection, build-coaching, build-decompose
- **Assigned To**: pipeline-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify env vars are injected correctly by tracing sdk_client code path
- Verify Observer coaching messages include context variables
- Verify sub-skills load and contain valid instructions
- Run existing tests to confirm no regressions

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-pipeline
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/skill-context-injection.md`
- Add entry to `docs/features/README.md` index table
- Add README to `do-pr-review/sub-skills/`

### 7. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: pipeline-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met (including documentation)
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| SDLC env vars in sdk_client | `grep -c 'SDLC_' agent/sdk_client.py` | output > 3 |
| Sub-skills exist | `ls .claude/skills/do-pr-review/sub-skills/*.md \| wc -l` | output > 2 |
| Feature docs exist | `test -f docs/features/skill-context-injection.md` | exit code 0 |
| Backward compat | `grep -c 'fallback\|SDLC_.*or\|if.*SDLC' .claude/skills/do-pr-review/SKILL.md` | output > 0 |

---

## Open Questions

1. **Sub-skill invocation mechanism**: Should sub-skills be invoked via `Read` tool (load the file as instructions) or via a new slash command pattern? The former is simpler but less discoverable.

2. **Env var naming convention**: The issue proposes `SDLC_PR_NUMBER` but some skills also need `SDLC_REPO` which overlaps with `GH_REPO`. Should we use `SDLC_REPO` as an alias or always defer to `GH_REPO` for repo context?

3. **Which skills to decompose after the proof-of-concept**: `/do-build` (638 lines) and `/do-test` (574 lines) are the largest. Should we plan for their decomposition in this issue or defer to a follow-up?
