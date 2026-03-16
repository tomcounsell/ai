---
status: Ready
type: bug
appetite: Medium
owner: Valor
created: 2026-03-16
tracking: https://github.com/tomcounsell/ai/issues/417
last_comment_id:
---

# SDLC Pipeline Integrity: Session Context Loss, URL Validation, Merge Guard

## Problem

The SDLC pipeline has three integrity gaps that cause incorrect autonomous behavior:

**A. Session metadata lost during continuation (#400):** When `_enqueue_continuation` can't find the `AgentSession` by ID, it falls back to `enqueue_job()` creating a fresh session that loses `classification_type`, `context_summary`, `issue_url`, `pr_url`, and stage history. Workers then restart from scratch ("Let me check the active PRs...").

**B. Unvalidated URLs from worker output (thread 7525):** Observer's `update_session` tool accepts `issue_url` and `pr_url` from worker text output without validation. Workers can store wrong repo names or malformed URLs that propagate to status messages.

**C. No merge guard (#409):** SKILL.md defines MERGE as a human decision, but nothing enforces it. Workers have unrestricted bash access and can run `gh pr merge`. PR #214 in Popoto was merged by a worker without human authorization.

**Current behavior:**
- Sessions lose all metadata when Redis key expires or ORM query fails during continuation
- Observer stores any URL string workers provide, including wrong-repo URLs
- Workers can and do merge PRs autonomously

**Desired outcome:**
- Continuation always preserves full session state or fails loudly with diagnostics
- URLs are constructed deterministically from `GH_REPO` + extracted issue/PR numbers, not stored verbatim from worker text
- `gh pr merge` is blocked by a PreToolUse hook unless explicitly human-authorized
- MERGE is a gated pipeline stage with a `/do-merge` skill that checks prerequisites (review done, tests passed, docs reviewed)

## Prior Art

- **Issue #400**: Session metadata lost during `_enqueue_continuation` fallback path — Closed, but the consolidation issue #417 reopened the scope
- **Issue #409**: No merge guard — worker agents can merge PRs without human approval — Closed, consolidated into #417
- **Issue #374**: Observer returns early on continuation sessions due to session cross-wire — Fixed deterministic record selection in `_handle_update_session`
- **Issue #276**: SDLC session tracking: classifier never outputs 'sdlc' type — Fixed classification
- **Issue #353**: Summarizer: always render PR/issue links even when session tracking is missing — Related URL display issue

## Data Flow

### A. Continuation flow (session context loss)

1. **Entry**: Worker agent finishes a turn → `_execute_job` calls Observer
2. **Observer decides STEER**: Returns `{action: "steer", coaching_message: "..."}`
3. **`_enqueue_continuation`**: Queries `AgentSession.query.filter(session_id=job.session_id)`
4. **Happy path**: Session found → `_extract_job_fields` preserves all metadata → delete-and-recreate
5. **Failure path**: Session NOT found (Redis expiry/race) → falls back to `enqueue_job()` → creates new session with only `classification_type` and `work_item_slug` — everything else lost
6. **Output**: New worker starts with incomplete context

### B. URL construction flow (replacing verbatim storage)

1. **Entry**: Worker outputs text containing GitHub issue/PR numbers
2. **Observer**: Calls `update_session(issue_url=..., pr_url=...)` with URLs extracted from worker text
3. **`_handle_update_session`**: Currently stores URLs verbatim — wrong repo names propagate
4. **Fix**: Extract just the issue/PR number from the URL, then construct the canonical URL from `GH_REPO` + number
5. **Output**: Summarizer always shows correct repo in status messages

### C. Merge flow (no guard)

1. **Entry**: Worker runs `gh pr merge <N>` via Bash tool
2. **PreToolUse hook**: Current `pre_tool_use.py` only logs — does not inspect or block
3. **PostToolUse hook**: `post_tool_use.py` tracks merges but only *after* they execute
4. **Output**: PR merged without human approval

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

Three focused fixes in well-understood code paths. The merge guard is a new PreToolUse hook; the other two are targeted changes to existing functions.

## Prerequisites

No prerequisites — all changes are internal to the bridge/agent code.

## Solution

### Key Elements

- **Session fallback hardening**: Make `_enqueue_continuation` fallback preserve all available metadata from the Job object and log diagnostics (TTL check, key existence) before falling back
- **Deterministic URL construction**: Instead of validating worker-provided URLs, extract the issue/PR number and construct the URL deterministically from `GH_REPO`. The summarizer template becomes partially deterministic.
- **Merge guard hook**: Add a PreToolUse hook on the Bash matcher that blocks `gh pr merge` commands with an error message directing to human approval. Keep repo-specific merge protections loose for now — different repos may need different rules.
- **MERGE stage + `/do-merge` skill**: Add MERGE as a gated pipeline stage. The `/do-merge` skill checks prerequisites (PR review done, tests passed, docs reviewed) before allowing the human to authorize the merge.

### Flow

**A. Continuation fix:**
`_enqueue_continuation` → session not found → log Redis diagnostics (key TTL, existence check) → attempt to reconstruct from Job fields → if truly gone, propagate ALL fields from Job to new session via `enqueue_job()` → never silently lose metadata

**B. Deterministic URL construction:**
Observer calls `update_session(pr_url=X)` → `_handle_update_session` extracts issue/PR number via regex → constructs canonical URL as `https://github.com/{GH_REPO}/issues/{N}` or `https://github.com/{GH_REPO}/pull/{N}` → stores the constructed URL, not the worker-provided one

**C. Merge guard:**
Worker runs `gh pr merge` → PreToolUse hook detects command via regex → returns `{"decision": "block", "reason": "..."}` → agent sees the block message and cannot merge

**D. `/do-merge` gated stage:**
Pipeline reaches MERGE → `/do-merge` skill checks: REVIEW completed? TEST passed? DOCS done? → If all pass, presents the merge to the human for authorization → Human approves → merge executes

### Technical Approach

- **A**: Enhance the fallback path in `_enqueue_continuation` (line 1302-1322 of `agent/job_queue.py`). Add Redis key inspection before the query. Propagate `context_summary`, `expectations`, `issue_url`, `pr_url`, stage history from Job to the fallback `enqueue_job()` call.
- **B**: Add a `_construct_canonical_url(url, gh_repo)` function in `bridge/observer.py`. Extract the issue/PR number from the worker-provided URL via regex, then construct the canonical URL using `GH_REPO`. Call it in `_handle_update_session` before storing `issue_url` and `pr_url`. If no number can be extracted, log a warning and discard.
- **C**: Create `.claude/hooks/validators/validate_merge_guard.py` — a PreToolUse hook that regex-matches `gh pr merge` in Bash commands and returns a block decision. Register it in `.claude/settings.json` under `PreToolUse` with `"matcher": "Bash"`. Keep the implementation loose — different repos may need different merge protections later.
- **D**: Add MERGE to `STAGE_ORDER` in `bridge/stage_detector.py` and `DISPLAY_STAGES` in `bridge/pipeline_graph.py`. Create `.claude/skills/do-merge/SKILL.md` and `.claude/commands/do-merge.md`. The skill checks: (1) REVIEW stage completed, (2) TEST stage completed, (3) DOCS stage completed. If all gates pass, it delivers a merge-ready message to the human. The actual `gh pr merge` is only unblocked when invoked through this skill (the hook checks for a flag/env var set by do-merge).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_enqueue_continuation` fallback path: test that when session is missing, the new session has all metadata from Job
- [ ] `_handle_update_session` URL construction: test that URLs are reconstructed from `GH_REPO` + extracted number
- [ ] Merge guard hook: test that `gh pr merge` commands are blocked

### Empty/Invalid Input Handling
- [ ] URL constructor handles empty string, None, non-GitHub URLs, URLs without an extractable number
- [ ] Merge guard handles edge cases: `gh pr merge --help`, comments about merging, `echo "gh pr merge"`
- [ ] `/do-merge` handles: no PR URL on session, missing review/test/docs stages

### Error State Rendering
- [ ] Merge guard block message is clear and actionable ("PR merge requires human authorization")
- [ ] Session fallback logs include session_id and diagnostic info for debugging

## Rabbit Holes

- **Redesigning Redis session storage** — the Popoto ORM has known quirks but replacing it is a separate project
- **Full audit of all worker commands** — the merge guard is the critical case; general command sandboxing is out of scope
- **Session resumption via `prior_uuid`** (#232, #374) — related context leak risk but separate concern

## Risks

### Risk 1: Merge guard false positives
**Impact:** Legitimate merge commands blocked (e.g., in test scripts or documentation)
**Mitigation:** Only match in Bash tool input, use precise regex (`\bgh\s+pr\s+merge\b`), and allow `gh pr merge --help`

### Risk 2: URL number extraction fails
**Impact:** Legitimate URLs with unusual formats (e.g., GitHub Enterprise, non-standard paths) can't have numbers extracted
**Mitigation:** Log a warning when extraction fails but don't crash. The URL field is left as-is if no number can be extracted.

## Race Conditions

### Race 1: Session deletion during continuation
**Location:** `agent/job_queue.py:1301-1330`
**Trigger:** Redis key expires between Observer `read_session` and `_enqueue_continuation` query
**Data prerequisite:** AgentSession must exist in Redis when continuation is enqueued
**State prerequisite:** Session must not have been garbage-collected by Redis TTL
**Mitigation:** The existing fallback path handles this. Enhancement: add Redis `EXISTS` check before the Popoto query to get a definitive answer and better diagnostics.

## No-Gos (Out of Scope)

- Replacing Popoto ORM or changing Redis key management
- General command sandboxing beyond `gh pr merge`
- Session resumption via `prior_uuid` fixes (#232, #374)
- Per-repo merge protection rules (keep the merge guard loose; different repos will need different policies later)

## Update System

No update system changes required — all changes are internal to the bridge/agent code and hooks. The merge guard hook is registered in `.claude/settings.json` which is already synced by the update process.

## Agent Integration

No agent integration required — these are bridge-internal changes. The merge guard hook is a Claude Code PreToolUse hook, not an MCP tool. The URL validation runs inside the Observer which is bridge code.

## Documentation

- [ ] Create `docs/features/sdlc-pipeline-integrity.md` describing the three fixes and their motivation
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Update `docs/features/sdlc-enforcement.md` to reference the merge guard hook

## Success Criteria

- [ ] `_enqueue_continuation` fallback preserves `classification_type`, `context_summary`, `issue_url`, `pr_url`, and stage history
- [ ] Fallback path logs Redis key diagnostics (EXISTS, TTL) before creating new session
- [ ] Observer's `_handle_update_session` constructs URLs deterministically from `GH_REPO` + extracted number
- [ ] URLs with unextractable numbers are logged at warning level and discarded
- [ ] PreToolUse hook blocks `gh pr merge` commands with a clear error message
- [ ] Hook does NOT block `gh pr merge --help` or non-Bash mentions
- [ ] MERGE added to `STAGE_ORDER` and `DISPLAY_STAGES`
- [ ] `/do-merge` skill exists and checks REVIEW, TEST, DOCS prerequisites before presenting merge to human
- [ ] `PIPELINE_EDGES` updated: `("DOCS", "success")` → `"MERGE"` with MERGE having a `/do-merge` skill
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (session-hardening)**
  - Name: session-builder
  - Role: Fix `_enqueue_continuation` fallback and add Redis diagnostics
  - Agent Type: builder
  - Resume: true

- **Builder (url-construction)**
  - Name: url-builder
  - Role: Add deterministic URL construction to Observer's `_handle_update_session`
  - Agent Type: builder
  - Resume: true

- **Builder (merge-guard-and-stage)**
  - Name: merge-builder
  - Role: Create merge guard hook, MERGE stage tracking, and `/do-merge` skill
  - Agent Type: builder
  - Resume: true

- **Validator (all)**
  - Name: integrity-validator
  - Role: Verify all three fixes work correctly and don't regress
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Harden `_enqueue_continuation` fallback
- **Task ID**: build-session
- **Depends On**: none
- **Assigned To**: session-builder
- **Agent Type**: builder
- **Parallel**: true
- Add Redis `EXISTS` and `TTL` check for session key before Popoto query in `_enqueue_continuation`
- In fallback path, propagate ALL metadata from Job to `enqueue_job()`: `context_summary`, `expectations`, `issue_url`, `pr_url`, stage history via Job fields
- Log diagnostic info: session_id, key existence, TTL value, fallback reason
- Write unit test: mock session not found, verify fallback session has all metadata

### 2. Add deterministic URL construction to Observer
- **Task ID**: build-url
- **Depends On**: none
- **Assigned To**: url-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `_construct_canonical_url(url, gh_repo)` function in `bridge/observer.py`
- Extract issue/PR number from worker-provided URL via regex (e.g., `/issues/(\d+)`, `/pull/(\d+)`)
- Construct canonical URL as `https://github.com/{GH_REPO}/issues/{N}` or `https://github.com/{GH_REPO}/pull/{N}`
- Resolve `GH_REPO` from environment variable or derive from `session.working_dir`
- Call constructor in `_handle_update_session` before storing `issue_url` and `pr_url`
- Log warning when number extraction fails; discard the URL in that case
- Write unit test: correct repo URL constructed, wrong-repo URL corrected, non-GitHub URL discarded, None/empty handled

### 3. Create merge guard hook + MERGE stage + `/do-merge` skill
- **Task ID**: build-merge-guard
- **Depends On**: none
- **Assigned To**: merge-builder
- **Agent Type**: builder
- **Parallel**: true
- **3a. Merge guard hook:**
  - Create `.claude/hooks/validators/validate_merge_guard.py`
  - Match `\bgh\s+pr\s+merge\b` in Bash tool_input command field
  - Return `{"decision": "block", "reason": "PR merge requires human authorization. Use /do-merge to check prerequisites and request merge approval."}` when matched
  - Do NOT block: `gh pr merge --help`, non-Bash tools, echo/comments containing the phrase
  - Register in `.claude/settings.json` under PreToolUse with `"matcher": "Bash"`
  - Write unit test: verify block for `gh pr merge 42`, allow for `gh pr merge --help`, allow for `echo "gh pr merge"`
- **3b. MERGE stage tracking:**
  - Add `"MERGE"` to `STAGE_ORDER` in `bridge/stage_detector.py`
  - Add `"MERGE"` to `DISPLAY_STAGES` in `bridge/pipeline_graph.py`
  - Add `"MERGE": "/do-merge"` to `STAGE_TO_SKILL` in `bridge/pipeline_graph.py`
  - `PIPELINE_EDGES` already has `("DOCS", "success"): "MERGE"` — update `get_next_stage` to return the skill instead of `None` for MERGE
  - Add `SDLC_STAGES` update in `models/agent_session.py`
- **3c. `/do-merge` skill:**
  - Create `.claude/commands/do-merge.md` skill definition
  - The skill reads session stage progress and checks prerequisites: REVIEW completed, TEST completed, DOCS completed
  - If all gates pass: deliver a merge-ready message with PR link to the human, asking for explicit merge authorization
  - If gates fail: report which prerequisites are missing
  - The skill does NOT execute `gh pr merge` itself — it only validates and requests human approval

### 4. Validate all fixes
- **Task ID**: validate-all
- **Depends On**: build-session, build-url, build-merge-guard
- **Assigned To**: integrity-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/ -x -q`
- Verify merge guard hook is registered in `.claude/settings.json`
- Verify URL validation function exists and is called from `_handle_update_session`
- Verify fallback path in `_enqueue_continuation` propagates all metadata
- Run `python -m ruff check . && python -m ruff format --check .`

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: integrity-validator
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/sdlc-pipeline-integrity.md`
- Add entry to `docs/features/README.md` index table
- Update `docs/features/sdlc-enforcement.md` to reference merge guard

### 6. Final Validation
- **Task ID**: validate-final
- **Depends On**: document-feature
- **Assigned To**: integrity-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all verification commands
- Verify all success criteria met (including documentation)
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Merge guard registered | `python -c "import json; s=json.load(open('.claude/settings.json')); hooks=[h for g in s['hooks'].get('PreToolUse',[]) for h in g.get('hooks',[])]; assert any('merge_guard' in h.get('command','') for h in hooks)"` | exit code 0 |
| URL constructor exists | `grep -q '_construct_canonical_url' bridge/observer.py` | exit code 0 |
| Fallback preserves metadata | `grep -q 'context_summary' agent/job_queue.py \| grep -c 'enqueue_job'` | exit code 0 |
| MERGE in stage order | `python -c "from bridge.stage_detector import STAGE_ORDER; assert 'MERGE' in STAGE_ORDER"` | exit code 0 |
| do-merge skill exists | `test -f .claude/commands/do-merge.md` | exit code 0 |

---
