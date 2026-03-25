---
status: Planning
type: feature
appetite: Medium
owner: Valor
created: 2026-03-25
tracking: https://github.com/tomcounsell/ai/issues/520
last_comment_id:
---

# SDLC Stage Handoff via GitHub Issue Comments

## Problem

SDLC stages operate in isolation. When the Test stage sub-agent starts, it does not know that the Build stage hit a tricky edge case in auth middleware, or that the Review stage flagged a concern about error handling. Each stage re-discovers context that the previous stage already had.

**Current behavior:**
Sub-agents start each stage with only the plan document and their task prompt. Discoveries, decisions, and blockers from prior stages are lost between sessions.

**Desired outcome:**
Each SDLC stage reads prior stage comments from the tracking GitHub issue before starting, and posts a structured summary comment when finishing. The issue becomes the living record of the work -- visible to both agents and humans.

## Prior Art

- **PR #517** (Cross-agent knowledge relay: persistent findings from parallel work) -- Reverted. Over-engineered a Popoto Finding model with DecayingSortedField, ConfidenceField, and bloom filter deduplication for data that lives two days. The right solution is GitHub issue comments, not a custom data model.
- **PR #488** (Consolidate SDLC stage tracking) -- Successfully wired PipelineStateMachine into the SubagentStop hook for stage completion tracking. This is the same hook we will extend for comment posting.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #517 | Built a Finding model with Popoto, bloom filters, and similarity-based deduplication for cross-agent knowledge relay | Over-engineered -- used Redis persistence and custom models for ephemeral data that lives at most 2 days. Reverted entirely (PR a81042a7). |

**Root cause pattern:** The previous attempt treated stage findings as a data modeling problem (requiring a new model, extraction pipeline, and query system) when it is actually a communication problem solvable with existing infrastructure (GitHub issue comments).

## Data Flow

### On stage start (pre-dispatch)

1. **Entry point**: ChatSession constructs DevSession prompt (inline in `sdk_client.py` line 1466-1470)
2. **ChatSession instructions**: PM instructions tell ChatSession to fetch issue comments before spawning a DevSession
3. **GitHub API**: `gh api repos/{owner}/{repo}/issues/{number}/comments` fetches prior stage comments
4. **Prompt injection**: ChatSession appends a "Prior stage findings" section to the DevSession prompt
5. **Output**: DevSession starts with full context from prior stages

### On stage completion (SubagentStop hook)

1. **Entry point**: SubagentStop hook fires when DevSession completes (`agent/hooks/subagent_stop.py`)
2. **Extraction**: Hook extracts stage name, outcome, and key findings from the subagent's return value
3. **GitHub API**: `gh issue comment {number} --body "..."` posts a structured comment
4. **Output**: Structured comment visible in GitHub issue timeline

## Architectural Impact

- **New dependencies**: None -- uses `gh` CLI which is already available in all sessions
- **Interface changes**: None -- the DevSession prompt format gains an optional section, the SubagentStop hook gains an optional comment posting step
- **Coupling**: Minimal increase -- the hook reads tracking issue number from plan frontmatter or environment. Falls back gracefully if no tracking issue exists.
- **Data ownership**: GitHub becomes the canonical record of stage findings (replacing the reverted Redis-based Finding model)
- **Reversibility**: High -- the comment posting is additive (can be removed without breaking anything), and the prompt injection is optional context

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (well-specified issue)
- Review rounds: 1

## Prerequisites

No prerequisites -- `gh` CLI is already available and authenticated in all environments. The SubagentStop hook and ChatSession dispatch code both exist.

## Solution

### Key Elements

- **Comment reader**: A utility function that fetches and formats prior stage comments from a tracking issue
- **Prompt enrichment**: ChatSession instructions updated to include prior stage comments when dispatching DevSessions
- **Comment poster**: SubagentStop hook extended to post structured stage summary comments
- **Comment format**: Standardized markdown template with stage name, outcome, discoveries, files modified, and notes for next stage

### Flow

ChatSession dispatches DevSession → reads issue comments → injects into prompt → DevSession executes stage → SubagentStop fires → posts structured comment → next stage reads it

### Technical Approach

**Component 1: `utils/issue_comments.py` -- Comment reader/writer utility**

A small utility module with two functions:

1. `fetch_stage_comments(issue_number: int) -> list[dict]` -- Calls `gh api` to fetch issue comments, filters for ones matching the stage comment format (identified by the structured header pattern), returns parsed list of stage/outcome/findings.

2. `post_stage_comment(issue_number: int, stage: str, outcome: str, findings: list[str], files: list[str], notes: str) -> bool` -- Formats and posts a structured comment via `gh issue comment`. Returns True on success, False on failure (never raises).

Both functions use subprocess to call `gh` CLI -- no new Python dependencies needed.

**Component 2: ChatSession prompt enrichment**

Update the PM instructions in `sdk_client.py` (lines 1458-1479) to add a step between "Assess the current stage" and "Spawn one dev-session":

> "1.5. **Gather prior stage context** -- if a tracking issue exists, run `gh api repos/{owner}/{repo}/issues/{number}/comments` and include a summary of prior stage findings in the DevSession prompt."

This is an instruction change only -- the ChatSession (Claude agent) does the fetching and prompt construction itself. No Python code change needed for the dispatch path beyond updating the instruction text.

**Component 3: SubagentStop hook extension**

Extend `agent/hooks/subagent_stop.py` to post a stage summary comment after `_register_dev_session_completion()`:

1. Resolve the tracking issue number from the parent session's plan slug (look up `docs/plans/{slug}.md` frontmatter `tracking:` field) or from the `SDLC_TRACKING_ISSUE` environment variable
2. Extract stage name and outcome from the hook's input data and the PipelineStateMachine
3. Call `post_stage_comment()` to post the structured comment
4. Wrap in try/except -- comment posting must never crash the hook

**Component 4: Environment variable propagation**

Add `SDLC_TRACKING_ISSUE` to the environment variables set by `sdk_client.py` when launching sessions with a tracking issue. This gives the SubagentStop hook access to the issue number without needing to re-parse plan frontmatter.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `fetch_stage_comments` handles `gh` CLI failures (non-zero exit, timeout) -- returns empty list
- [ ] `post_stage_comment` handles `gh` CLI failures -- returns False, logs warning
- [ ] SubagentStop hook wraps comment posting in try/except -- never crashes the hook
- [ ] All `gh` calls have reasonable timeouts (10s)

### Empty/Invalid Input Handling
- [ ] `fetch_stage_comments` with non-existent issue number returns empty list
- [ ] `post_stage_comment` with empty findings list posts comment with "No notable findings" placeholder
- [ ] SubagentStop hook with no tracking issue skips comment posting silently
- [ ] ChatSession instructions handle case where no prior comments exist (empty context)

### Error State Rendering
- [ ] Stage comment format is human-readable in GitHub UI (verified visually)
- [ ] Error messages from `gh` CLI are logged at warning level, not error (comment failure is non-critical)

## Test Impact

- [ ] `tests/unit/test_subagent_stop_hook.py` -- UPDATE: add test cases for comment posting path (existing tests for stage completion recording remain valid)

No other existing tests affected -- the ChatSession prompt change is to instruction text (not testable via unit tests), and the utility module is entirely new code.

## Rabbit Holes

- Building an LLM-powered extraction pipeline for stage findings (the SubagentStop hook already has `_extract_outcome_summary` -- extend it, do not replace it with an LLM call)
- Adding comment threading or reply chains on GitHub issues (flat comments are sufficient for stage handoff)
- Persisting stage findings in Redis alongside the GitHub comments (GitHub is the single source of truth -- no dual-write)
- Making the comment format configurable or template-driven (hardcoded format is fine for internal tooling)
- Deduplicating comments if a stage runs multiple times (duplicate comments are informative, not harmful)

## Risks

### Risk 1: GitHub API rate limiting
**Impact:** Comment fetching or posting fails during rapid stage transitions.
**Mitigation:** Use `gh` CLI which handles auth and rate limiting. Comments are non-critical -- failure to post/read does not block stage execution. The `gh` CLI retries automatically on rate limit responses.

### Risk 2: Large comment volume on long-running issues
**Impact:** Many stage comments could make the issue noisy for human readers.
**Mitigation:** Stage comments use a consistent header format that GitHub collapses in the timeline. The structured format makes them scannable. This is acceptable for issues that typically have fewer than 20 stage transitions.

### Risk 3: ChatSession context bloat from reading all comments
**Impact:** Long-running issues with many comments could consume excessive context in the ChatSession prompt.
**Mitigation:** The ChatSession instructions will specify reading only the last 5 stage comments (most recent context is most valuable). Older stage findings are available in the issue timeline if needed.

## Race Conditions

No race conditions identified -- stage transitions are sequential (one DevSession completes before the next is spawned). Comment posting happens in the SubagentStop hook which fires synchronously after the subagent returns. Two stages cannot be posting comments simultaneously for the same tracking issue.

## No-Gos (Out of Scope)

- LLM-powered extraction of findings from session transcripts (use the existing `_extract_outcome_summary` pattern)
- Bidirectional sync between issue comments and any other storage (GitHub is the single source of truth)
- Comment editing or updating (append-only -- new comments per stage, never edit old ones)
- Cross-issue comment linking (each tracking issue is self-contained)
- Custom comment formats per stage type (one format fits all)
- Memory system integration (this is SDLC infrastructure, not subconscious memory)

## Update System

No update system changes required -- this feature modifies agent hooks and SDK client instructions. No new dependencies, no new config files, no migration steps. The changes propagate via normal git pull on update.

## Agent Integration

No MCP server changes required. The `gh` CLI is already available in all agent sessions. The changes are:

1. **SubagentStop hook** (`agent/hooks/subagent_stop.py`) -- extended directly, no MCP wrapping needed since hooks run in the bridge process
2. **ChatSession instructions** (`agent/sdk_client.py`) -- instruction text change, not a tool
3. **Utility module** (`utils/issue_comments.py`) -- called by the hook, not exposed as an agent tool

The agent does not need to invoke these functions as tools -- they run automatically as part of the SDLC lifecycle.

## Documentation

- [ ] Create `docs/features/sdlc-stage-handoff.md` describing the comment-based stage handoff system
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Update inline docstrings in `agent/hooks/subagent_stop.py` to reflect the comment posting responsibility

## Success Criteria

- [ ] DevSession prompt includes prior stage comments from the tracking issue (verified by checking ChatSession instruction text)
- [ ] SubagentStop hook posts structured summary comment on stage completion (integration test)
- [ ] Comments are human-readable in the GitHub UI (visual verification)
- [ ] Works when no tracking issue exists (skip gracefully -- unit test)
- [ ] No new models, no Redis -- pure GitHub API (code review)
- [ ] Integration test: mock stage completion produces correctly formatted comment
- [ ] Tests pass (`pytest tests/ -x -q`)
- [ ] Lint clean (`python -m ruff check .`)

## Team Orchestration

### Team Members

- **Builder (hook-and-utils)**
  - Name: hook-builder
  - Role: Implement utils/issue_comments.py and extend SubagentStop hook
  - Agent Type: builder
  - Resume: true

- **Builder (instructions)**
  - Name: instructions-builder
  - Role: Update ChatSession PM instructions in sdk_client.py
  - Agent Type: builder
  - Resume: true

- **Validator**
  - Name: integration-validator
  - Role: Verify end-to-end comment posting and reading
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Create utils/issue_comments.py
- **Task ID**: build-utils
- **Depends On**: none
- **Validates**: `python -c "from utils.issue_comments import fetch_stage_comments, post_stage_comment; print('OK')"`
- **Assigned To**: hook-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `utils/issue_comments.py` with `fetch_stage_comments()` and `post_stage_comment()`
- Both functions use subprocess to call `gh` CLI
- `fetch_stage_comments` parses JSON response, filters for stage-formatted comments
- `post_stage_comment` formats markdown comment body and posts via `gh issue comment`
- Both functions handle errors gracefully (empty list / False return, never raise)

### 2. Extend SubagentStop hook
- **Task ID**: build-hook
- **Depends On**: build-utils
- **Validates**: `python -c "from agent.hooks.subagent_stop import subagent_stop_hook; print('OK')"`
- **Assigned To**: hook-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `_post_stage_comment()` helper to `agent/hooks/subagent_stop.py`
- Resolve tracking issue from `SDLC_TRACKING_ISSUE` env var or plan frontmatter
- Extract stage name from PipelineStateMachine current stage
- Extract outcome and findings from `_extract_outcome_summary()`
- Call `post_stage_comment()` inside try/except
- Wire into `subagent_stop_hook()` after `_register_dev_session_completion()`

### 3. Add SDLC_TRACKING_ISSUE env var propagation
- **Task ID**: build-env-var
- **Depends On**: none
- **Validates**: `grep 'SDLC_TRACKING_ISSUE' agent/sdk_client.py`
- **Assigned To**: instructions-builder
- **Agent Type**: builder
- **Parallel**: true
- In `sdk_client.py`, when resolving a work item with a tracking issue, set `SDLC_TRACKING_ISSUE={number}` in the session environment
- Parse the tracking URL from plan frontmatter to extract the issue number

### 4. Update ChatSession PM instructions
- **Task ID**: build-instructions
- **Depends On**: none
- **Validates**: `grep 'prior stage' agent/sdk_client.py`
- **Assigned To**: instructions-builder
- **Agent Type**: builder
- **Parallel**: true
- Update the PM dispatch instructions in `sdk_client.py` (lines 1458-1479)
- Add step 1.5: "Gather prior stage context" -- fetch issue comments and include in DevSession prompt
- Specify reading only the last 5 stage comments to limit context bloat
- Instruct ChatSession to look for comments with the structured stage header format

### 5. Write unit tests
- **Task ID**: build-tests
- **Depends On**: build-utils, build-hook
- **Validates**: `pytest tests/unit/test_issue_comments.py tests/unit/test_subagent_stop_hook.py -v`
- **Assigned To**: hook-builder
- **Agent Type**: test-engineer
- **Parallel**: false
- Create `tests/unit/test_issue_comments.py` testing fetch and post functions with mocked subprocess
- Update `tests/unit/test_subagent_stop_hook.py` with test cases for comment posting path
- Test graceful handling of missing tracking issue, `gh` CLI failures, empty comments

### 6. Write integration test
- **Task ID**: build-integration-test
- **Depends On**: build-utils, build-hook
- **Validates**: `pytest tests/integration/test_stage_comment.py -v`
- **Assigned To**: hook-builder
- **Agent Type**: test-engineer
- **Parallel**: false
- Create `tests/integration/test_stage_comment.py`
- Test: post a comment to a real test issue, fetch it back, verify format
- Use a dedicated test issue (create one in the test, clean up after)

### 7. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-utils, build-hook, build-env-var, build-instructions, build-tests, build-integration-test
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/ -x -q` to verify no regressions
- Run `python -m ruff check .` for lint
- Verify all success criteria are met

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Utils module exists | `python -c "from utils.issue_comments import fetch_stage_comments, post_stage_comment"` | exit code 0 |
| Hook imports clean | `python -c "from agent.hooks.subagent_stop import subagent_stop_hook"` | exit code 0 |
| Env var propagation | `grep -q 'SDLC_TRACKING_ISSUE' agent/sdk_client.py` | exit code 0 |
| PM instructions updated | `grep -q 'prior stage' agent/sdk_client.py` | exit code 0 |
| Unit tests exist | `test -f tests/unit/test_issue_comments.py` | exit code 0 |
| Integration test exists | `test -f tests/integration/test_stage_comment.py` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

None -- the issue provides sufficient specification including solution sketch, acceptance criteria, and definitions. All touch points have been identified and validated through code reading.
