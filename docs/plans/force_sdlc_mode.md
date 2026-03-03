---
status: Ready
type: bug
appetite: Small
owner: Valor
created: 2026-03-03
tracking: https://github.com/tomcounsell/ai/issues/246
---

# Force AgentSession into SDLC Mode

## Problem

SDLC-formatted responses (stage progress line, link footer) only render when `is_sdlc_job()` returns True, which requires `[stage]` entries in AgentSession history. These entries are written by `python -m tools.session_progress` — a CLI command that sub-skills are instructed to run but frequently don't (wrong session ID, subprocess context issues, skill skipping the step).

**Current behavior:**
Session 6426 ("SDLC issue 233") ran the full pipeline but got chat format because `session_progress` was never called.

**Desired outcome:**
Any session classified as `sdlc` at input routing time always renders SDLC format, regardless of whether sub-skills call `session_progress`.

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

- **Set SDLC mode at classification time**: When the input classifier tags a message as `classification=sdlc`, immediately mark the AgentSession
- **Dual check in is_sdlc_job()**: Check `classification_type == "sdlc"` as a fallback alongside existing `[stage]` history check

### Technical Approach

#### Fix 1: Mark SDLC mode in _execute_job (job_queue.py)

After classification and before SDK query, if `classification_type == "sdlc"`, write a stage entry:

```python
if agent_session and agent_session.classification_type == "sdlc":
    agent_session.append_history("stage", "SDLC_MODE activated")
```

This guarantees at least one `[stage]` entry exists, making `is_sdlc_job()` return True.

#### Fix 2: Fallback in is_sdlc_job() (models/agent_session.py)

Add secondary check so even if history is empty, classification_type works:

```python
def is_sdlc_job(self) -> bool:
    if self.classification_type == "sdlc":
        return True
    for entry in self._get_history_list():
        if isinstance(entry, str) and "[stage]" in entry.lower():
            return True
    return False
```

Both fixes together provide belt-and-suspenders reliability.

### Flow

**Message arrives** → classifier tags as `sdlc` → `_execute_job` writes `[stage] SDLC_MODE activated` to history → sub-skills may or may not call `session_progress` → `is_sdlc_job()` returns True regardless → summarizer renders SDLC format

## Rabbit Holes

- Rewriting the session_progress CLI tool — it can stay as-is for granular stage updates
- Adding new fields to AgentSession — `classification_type` already exists and is already set

## Risks

### Risk 1: False positive SDLC classification
**Impact:** Non-SDLC messages could get SDLC formatting
**Mitigation:** The classifier is already well-tuned for SDLC detection. The fallback only activates when classification_type is explicitly "sdlc".

## No-Gos (Out of Scope)

- Rewriting session_progress tracking
- Changing the classifier itself
- Adding new AgentSession fields

## Update System

No update system changes required — bridge-internal change only.

## Agent Integration

No agent integration required — bridge-internal change affecting session state management.

## Documentation

- [ ] Update `docs/features/summarizer-format.md` — note that SDLC mode is set at classification time
- [ ] Code comments on the dual-check in `is_sdlc_job()`

## Success Criteria

- [ ] Sessions classified as `sdlc` always have `is_sdlc_job()` return True
- [ ] SDLC-formatted output renders even when sub-skills skip `session_progress`
- [ ] Existing SDLC stage tracking still works when sub-skills do call `session_progress`
- [ ] Tests pass
- [ ] Documentation updated

## Team Orchestration

### Team Members

- **Builder (sdlc-mode)**
  - Name: sdlc-mode-builder
  - Role: Implement both fixes
  - Agent Type: builder
  - Resume: true

- **Validator (sdlc-mode)**
  - Name: sdlc-mode-validator
  - Role: Verify SDLC mode activation
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Implement SDLC mode fixes
- **Task ID**: build-sdlc-mode
- **Depends On**: none
- **Assigned To**: sdlc-mode-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `classification_type` check to `is_sdlc_job()` in `models/agent_session.py`
- Add SDLC_MODE history entry in `_execute_job()` in `agent/job_queue.py`
- Write tests verifying both paths

### 2. Validate
- **Task ID**: validate-sdlc-mode
- **Depends On**: build-sdlc-mode
- **Assigned To**: sdlc-mode-validator
- **Agent Type**: validator
- **Parallel**: false
- Run tests
- Verify is_sdlc_job returns True for sdlc-classified sessions

### 3. Final Validation
- **Task ID**: validate-all
- **Depends On**: validate-sdlc-mode
- **Assigned To**: sdlc-mode-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands

## Validation Commands

- `cd /Users/valorengels/src/ai && python -m pytest tests/test_summarizer.py -v -p no:postgresql` — summarizer tests
- `cd /Users/valorengels/src/ai && python -m ruff check models/agent_session.py agent/job_queue.py` — lint
