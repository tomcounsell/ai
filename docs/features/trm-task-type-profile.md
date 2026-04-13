# TRM Registry: TaskTypeProfile

Task-Relevant Maturity (TRM) registry for Grove-style delegation decisions. The PM session consults `TaskTypeProfile` before spawning dev sessions to determine the right level of instruction granularity.

**Based on:** Andy Grove's *High Output Management* — supervision style should match the agent's demonstrated familiarity with the *specific task type*, not just global skill level.

## Problem

Before this feature, the PM session used identical step-by-step SDLC scaffolding for every dev session, regardless of whether the task type was well-practiced or novel. A bug we've fixed dozens of times got the same scaffolding as a greenfield feature the system had never attempted.

## How It Works

### Task Type Classification

At session completion, `auto_tag_session()` (Rule 7) derives a `task_type` from session metadata:

| Signal | Derived task_type |
|--------|-------------------|
| `rework_triggered = "true"` | `rework-triggered` |
| `classification_type = "bug"` | `bug-fix` |
| SDLC branch + `pr-created` tag | `sdlc-build` |
| SDLC branch + `tested` tag (no PR) | `sdlc-test` |
| SDLC branch + slug, no PR/tested | `sdlc-plan` |
| slug set, no SDLC branch | `greenfield-feature` |
| No signals | `None` (unclassified) |

Rule 7 is idempotent — it only sets `task_type` if not already set. Setting `session.task_type` directly before `auto_tag_session()` runs will preserve the manual value.

### TaskTypeProfile Model

Stored in Redis via Popoto, keyed by `project_key + task_type`.

```python
class TaskTypeProfile(Model):
    project_key = KeyField()
    task_type = KeyField()
    session_count = IntField(default=0)
    avg_turns = FloatField(default=0.0)
    rework_rate = FloatField(default=0.0)         # fraction with rework_triggered=True
    failure_stage_distribution = Field(null=True) # JSON: {"sdlc-build": 2, ...}
    delegation_recommendation = IndexedField(default="structured")
    last_updated = SortedField(type=float, partition_by="project_key")
```

### Delegation Recommendation

`delegation_recommendation` is re-derived on every profile update:

| Condition | Recommendation |
|-----------|----------------|
| `session_count < 5` | `structured` |
| `rework_rate > 0.3` | `structured` |
| Otherwise | `autonomous` |

**Structured**: PM sends full step-by-step SDLC scaffolding to the dev session.  
**Autonomous**: PM sends objective + constraints only — no step-by-step walkthrough.

### Profile Update Flow

```
Session completes
  → finalize_session()
      → auto_tag_session()        # sets task_type (Rule 7)
      → session.status = "completed" + save()
      → update_task_type_profile()  # reads completed session, updates metrics
```

The profile update runs **after** the status save so that `update_task_type_profile()` can verify `status == "completed"`. The update is wrapped in `try/except` — profile failures never block session finalization.

### PM Consultation

Before building the dev session prompt in `agent/sdk_client.py`:

```python
_trm_task_type = _infer_task_type_from_message(message, classification)
_delegation = get_delegation_recommendation(project_key, _trm_task_type)
# → "structured" or "autonomous"
```

`_infer_task_type_from_message()` is pattern-based (no LLM): checks for SDLC stage keywords (`stage: build`, `do-test`, etc.) and issue URL presence.

## Task Type Vocabulary

Defined in `models/agent_session.py` as `TASK_TYPE_VOCABULARY`:

```python
TASK_TYPE_VOCABULARY = {
    "sdlc-build",
    "sdlc-test",
    "sdlc-patch",
    "sdlc-plan",
    "bug-fix",
    "greenfield-feature",
    "rework-triggered",
}
```

## AgentSession Fields

Two new additive fields on `AgentSession`:

- `task_type = IndexedField(null=True)` — task category from vocabulary; queryable via `AgentSession.query.filter(task_type="sdlc-build")`
- `rework_triggered = Field(null=True)` — `"true"` or `"false"` string; set when a session retries prior output

## Public API

```python
# models/task_type_profile.py

update_task_type_profile(session_id: str) -> None
# Call after session completion; no-op if task_type is None or status != "completed"

get_delegation_recommendation(project_key: str | None, task_type: str | None) -> str
# Returns "structured" or "autonomous"; never raises; defaults to "structured"
```

## Bootstrapping

New installations start with no profiles. All task types begin at `session_count=0` → `"structured"` (safe default). Profiles accumulate over time as dev sessions complete. After 5 successful low-rework completions of a task type, delegation becomes `"autonomous"`.

## Reversibility

All changes are additive:
- Removing `task_type` / `rework_triggered` fields from `AgentSession` leaves the system fully functional
- Removing `TaskTypeProfile` causes `get_delegation_recommendation()` to return `"structured"` (existing behavior preserved)
- Removing the profile update hook from `finalize_session()` has no effect on session status transitions

## Race Conditions

Profile updates are eventually-consistent. Two concurrent completions of the same `task_type` may produce a `session_count` off by ±1. Acceptable — profiles are advisory metrics, not authoritative records.

## Files

| File | Purpose |
|------|---------|
| `models/task_type_profile.py` | TaskTypeProfile model, update and lookup functions |
| `models/agent_session.py` | `task_type`, `rework_triggered` fields, `TASK_TYPE_VOCABULARY` |
| `tools/session_tags.py` | Rule 7 in `auto_tag_session()` + `_derive_task_type()` helper |
| `models/session_lifecycle.py` | Profile update hook at step 5.5 in `finalize_session()` |
| `agent/sdk_client.py` | TRM consultation in PM dispatch, `_infer_task_type_from_message()` |
| `tests/unit/test_task_type_profile.py` | Unit tests: recommendation derivation, safe defaults, metrics |
| `tests/unit/test_session_lifecycle.py` | Unit tests: hook ordering, skip guard, failure safety |
| `tests/unit/test_session_tags.py` | Unit tests: Rule 7 task_type derivation (added to existing file) |
| `tests/integration/test_session_finalize.py` | Integration: full pipeline from session completion to profile update |
