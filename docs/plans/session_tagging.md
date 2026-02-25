---
status: Planning
type: feature
appetite: Small
owner: Valor
created: 2025-02-25
tracking: https://github.com/tomcounsell/ai/issues/162
---

# Session Tagging System

## Problem

Sessions are created and completed with rich metadata (turn count, tool calls, branch, classification), but there's no way to categorize them by *what happened* during the session. The `tags` ListField exists on SessionLog but nothing writes to it.

**Current behavior:**
Sessions have an empty `tags` field. No way to filter sessions by activity type (PR review, daydream, hotfix, etc.). Session analytics are limited to raw counts.

**Desired outcome:**
Sessions are automatically tagged based on activity (e.g., "pr-review" when a PR is created, "daydream" when daydream runs). Tags can be queried to answer "show me all PR review sessions this week." Manual tagging is available for ad-hoc categorization.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this work builds on the existing SessionLog model with `tags` ListField from #161.

## Solution

### Key Elements

- **Auto-tagger module**: Analyzes session activity at completion time and applies tags
- **Tag management helpers**: Add/remove/list tags on SessionLog instances
- **Query interface**: Filter sessions by tags via Python API

### Flow

**Session starts** → activity happens → **Session completes** → auto-tagger runs → tags written to SessionLog

**Manual tag** → user or agent calls `add_tags(session_id, ["custom-tag"])` → tag persisted

**Query** → `SessionLog.query.filter(tags__contains="pr-review")` or helper function → filtered results

### Technical Approach

- Auto-tagging runs in `complete_transcript()` in `bridge/session_transcript.py` — the natural chokepoint where every session finalizes
- Tag inference is rule-based (not LLM) — pattern match on transcript content, branch names, skill invocations, classification_type
- `tools/session_tags.py` provides the public API: `add_tags()`, `remove_tags()`, `get_tags()`, `sessions_by_tag()`, `auto_tag_session()`
- Tag vocabulary is open (any string), but we define a set of well-known tags for auto-tagging

### Auto-Tag Rules

| Signal | Tag Applied |
|--------|-------------|
| `classification_type == "bug"` | `bug` |
| `classification_type == "feature"` | `feature` |
| `classification_type == "chore"` | `chore` |
| Branch name starts with `session/` | `sdlc` |
| Transcript contains `gh pr create` | `pr-created` |
| Transcript contains `TOOL_CALL: Skill(do-test` or `pytest` | `tested` |
| Session started by daydream script | `daydream` |
| `sender` contains "daydream" or session_id contains "daydream" | `daydream` |
| `work_item_slug` is set | `planned-work` |
| `turn_count >= 20` | `long-session` |

## Rabbit Holes

- **LLM-based tag inference**: Tempting to use an LLM to read transcripts and suggest tags, but rule-based is simpler, faster, and sufficient for v1
- **Tag taxonomy/ontology**: Don't build a formal tag hierarchy. Flat tags with conventions are enough
- **Tag-based Telegram commands**: Building `/tags` or `/sessions` Telegram commands is a separate feature — this issue is the data layer only

## Risks

### Risk 1: Popoto ListField query limitations
**Impact:** May not support `contains` queries natively on ListField
**Mitigation:** Implement `sessions_by_tag()` as a Python-side filter over `SessionLog.query.all()` — the dataset is small (< 10K sessions) so this is fine

### Risk 2: Transcript file reading at completion time
**Impact:** Reading large transcript files to pattern-match could slow session completion
**Mitigation:** Only read last 50 lines of transcript for auto-tagging signals. Most signals (PR create, test runs) appear near the end.

## No-Gos (Out of Scope)

- Telegram commands for tag management (separate feature)
- Tag-based dashboards or visualizations
- LLM-powered tag suggestion
- Tag namespacing or hierarchies
- Retroactive tagging of existing sessions (can be done manually if needed)

## Update System

No update system changes required — this feature adds a Python module and modifies existing bridge code. No new dependencies or config files.

## Agent Integration

No agent integration required — this is a bridge-internal change. The auto-tagger runs automatically at session completion. The `tools/session_tags.py` module provides a Python API that could be exposed via MCP later, but that's out of scope for this issue.

## Documentation

- [ ] Create `docs/features/session-tagging.md` describing the tagging system, auto-tag rules, and API
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Docstrings on all public functions in `tools/session_tags.py`

## Success Criteria

- [ ] `auto_tag_session(session_id)` correctly applies tags based on session metadata and transcript
- [ ] `add_tags()` / `remove_tags()` / `get_tags()` work correctly
- [ ] `sessions_by_tag(tag)` returns matching sessions
- [ ] Auto-tagging is called in `complete_transcript()` without breaking existing flow
- [ ] Unit tests cover all auto-tag rules
- [ ] Integration test: create a session, complete it, verify tags applied
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (session-tags)**
  - Name: tags-builder
  - Role: Implement session tagging module and integrate with transcript completion
  - Agent Type: builder
  - Resume: true

- **Validator (session-tags)**
  - Name: tags-validator
  - Role: Verify all tag operations work and auto-tagging integrates correctly
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Build session tags module
- **Task ID**: build-tags-module
- **Depends On**: none
- **Assigned To**: tags-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `tools/session_tags.py` with: `add_tags()`, `remove_tags()`, `get_tags()`, `sessions_by_tag()`, `auto_tag_session()`
- Implement auto-tag rules from the table above
- `auto_tag_session()` reads SessionLog metadata + last 50 lines of transcript

### 2. Integrate auto-tagger into transcript completion
- **Task ID**: build-integration
- **Depends On**: build-tags-module
- **Assigned To**: tags-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `auto_tag_session(session_id)` call in `complete_transcript()` in `bridge/session_transcript.py`
- Call it BEFORE status update so tags are written while session is still active
- Wrap in try/except so tagging failures never break session completion

### 3. Write tests
- **Task ID**: build-tests
- **Depends On**: build-integration
- **Assigned To**: tags-builder
- **Agent Type**: builder
- **Parallel**: false
- Unit tests for each auto-tag rule in `tests/unit/test_session_tags.py`
- Integration test: full session lifecycle with tag verification
- Test `add_tags`, `remove_tags`, `get_tags`, `sessions_by_tag`

### 4. Validate implementation
- **Task ID**: validate-tags
- **Depends On**: build-tests
- **Assigned To**: tags-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify all success criteria met
- Run validation commands
- Check that `complete_transcript()` still works when tagging fails

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-tags
- **Assigned To**: tags-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/session-tagging.md`
- Add entry to `docs/features/README.md` index table

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: tags-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all tests
- Verify documentation exists
- Generate final report

## Validation Commands

- `pytest tests/unit/test_session_tags.py -v` - Unit tests for tagging module
- `pytest tests/ -v` - Full test suite
- `ruff check tools/session_tags.py bridge/session_transcript.py` - Lint
- `black --check tools/session_tags.py bridge/session_transcript.py` - Format
- `python -c "from tools.session_tags import add_tags, auto_tag_session; print('imports ok')"` - Verify module loads
