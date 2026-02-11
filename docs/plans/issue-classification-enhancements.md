---
status: Planning
type: feature
appetite: Small
owner: Valor
created: 2026-02-08
tracking: https://github.com/tomcounsell/ai/issues/60
---

# Issue Classification: Edge Cases and Enhancements

## Problem

The core classification system (mandatory labels, GitHub label application, validation hooks) shipped in #17, but several enhancement opportunities remain:

**Current behavior:**
- Classification happens manually during planning — user must specify `type:` in frontmatter
- No immutability enforcement — `type:` can change even after plan approval
- Reclassification during drafting requires manual frontmatter edit

**Desired outcome:**
- Incoming messages auto-classified before plan creation, stored in session metadata
- Classification locks when plan status changes to `Ready`
- Smooth reclassification UX during drafting phase via `/reclassify` skill

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 0

All infrastructure exists. This is wiring existing pieces together.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Classifier module | `python -c "from tools.classifier import classify_request_async; print('OK')"` | Auto-classification engine |
| Redis running | `python -c "import popoto; popoto.redis_db.ping(); print('OK')"` | Session metadata storage |
| Bridge operational | `./scripts/valor-service.sh status` | Message intake target |

Run all checks: `python scripts/check_prerequisites.py docs/plans/issue-classification-enhancements.md`

## Solution

### Key Elements

- **Auto-classification in bridge**: Extend `classify_and_update_reaction()` in `bridge/telegram_bridge.py:722` to also call `classify_request_async()` and store the result
- **Session metadata field**: Add `classification_type` and `classification_confidence` fields to `AgentSession` model in `models/sessions.py`
- **Job metadata passthrough**: Add `classification_type` field to `RedisJob` model in `agent/job_queue.py` so the agent worker has access
- **Make-plan pre-population**: Update `.claude/skills/make-plan/SKILL.md` to read classification from job/session context
- **Label immutability hook**: New validator `.claude/hooks/validators/validate_type_immutability.py` following the pattern in `validate_plan_label.py`
- **Reclassify skill**: New `.claude/skills/reclassify/SKILL.md` for drafting-phase type changes

### Flow

**Auto-classification:**
```
Telegram message
  → bridge/telegram_bridge.py handler (line 717)
  → classify_and_update_reaction() extended (line 722)
    → classify_request_async(clean_text)  [non-blocking, asyncio.create_task]
    → store result in AgentSession.classification_type
    → pass classification_type to enqueue_job() (line 876)
  → /make-plan reads classification_type from session context
  → pre-populates type: field in plan frontmatter
```

**Immutability:**
```
Plan file saved
  → validate_type_immutability.py hook fires
  → reads git-committed version of the file (git show HEAD:path)
  → if previous status was Ready/In Progress/Complete AND type changed → block
```

**Reclassification:**
```
User: "this is actually a bug"
  → /reclassify bug
  → validates plan status is still Planning
  → updates frontmatter type: field
  → confirms change
```

### Technical Approach

1. **Extend `classify_and_update_reaction()`** (`bridge/telegram_bridge.py:722`) — Already runs as a non-blocking `asyncio.create_task`. Add `classify_request_async()` call alongside `get_processing_emoji_async()`. Store result in `AgentSession` via session_id already available in scope (line 710-714).

2. **Add fields to models** — `AgentSession` (`models/sessions.py`) gets `classification_type = Field(null=True)` and `classification_confidence = Field(type=float, null=True)`. `RedisJob` (`agent/job_queue.py`) gets `classification_type = Field(null=True)` for passthrough.

3. **Pass classification to job** — In `enqueue_job()` call (line 876), add `classification_type` parameter. Worker can then expose it to the agent session.

4. **Make-plan reads classification** — The skill reads the session's classification_type and uses it as the default `type:` value in the frontmatter template. User can still override.

5. **Immutability hook** — Follows `validate_plan_label.py` pattern: reads file, parses frontmatter, compares against git HEAD version. Exits 0 (pass) or 2 (fail).

6. **Reclassify skill** — Minimal SKILL.md that finds the active plan, checks `status: Planning`, updates the `type:` field.

## Rabbit Holes

- **ML-based classification improvements** — Haiku is good enough; don't train custom models
- **Classification confidence thresholds** — Don't add complex logic for low-confidence cases; just use the best guess. The user can always `/reclassify` if wrong.
- **Historical reclassification audit logs** — Not needed; git history is sufficient
- **Multi-label support** — One type per plan; don't add support for multiple classifications
- **Classification-based emoji reactions** — Tempting (🐛/✨/🔧) but the existing intent-based emoji system (`get_processing_emoji_async`) serves a different purpose. Don't conflate.
- **Notion Type property sync** — No MCP infrastructure exists in this codebase (no `.mcp.json`, no `mcp_servers/` directory). Notion integration is minimal (URL detection only). Defer entirely until Notion is actively used for tracking.

## Risks

### Risk 1: Auto-classification adds latency to message intake
**Impact:** Slower response times on first message
**Mitigation:** Already mitigated by design — `classify_and_update_reaction()` runs as `asyncio.create_task()` (non-blocking). Classification result is stored asynchronously; if it hasn't completed by the time `/make-plan` runs, fall back to manual.

### Risk 2: Immutability blocks legitimate corrections
**Impact:** User can't fix misclassification after approval
**Mitigation:** Require rolling status back to `Planning` first via `/reclassify`, which checks status. This is explicit and auditable via git history. No `--force` flag needed.

### Risk 3: Race condition between classification and job enqueue
**Impact:** Classification result not available when job is enqueued (lines 722 vs 876)
**Mitigation:** Await classification within `classify_and_update_reaction()` before storing. The entire function is already fire-and-forget via `create_task`, so awaiting internally is fine. If classification fails, `classification_type` stays null and make-plan asks manually.

## No-Gos (Out of Scope)

- Custom ML classification models
- Classification analytics or dashboards
- Batch reclassification of existing plans
- Notion Type property sync (no MCP infrastructure exists; defer to separate issue)
- Classification hierarchy or sub-types
- Changing the existing emoji reaction system

## Update System

No update system changes required — this feature uses existing dependencies (classifier module, Haiku API) and doesn't add new config files or services. The new `AgentSession` and `RedisJob` fields are nullable and backward-compatible.

## Agent Integration

**Bridge changes required:**
- `bridge/telegram_bridge.py:722` — Extend `classify_and_update_reaction()` to also call `classify_request_async()` from `tools/classifier.py`
- Store classification result in `AgentSession` (looked up by session_id already in scope at line 710)
- Pass `classification_type` to `enqueue_job()` at line 876

**No MCP changes needed** — classifier is called internally by the bridge, not exposed as an agent tool. No `.mcp.json` or `mcp_servers/` directory exists in this codebase.

**Make-plan skill** — `.claude/skills/make-plan/SKILL.md` needs to document that `type:` may be pre-populated from auto-classification. The agent reads classification from session context (injected as environment or system prompt context by the SDK client).

## Documentation

### Feature Documentation
- [ ] Create `docs/features/classification.md` describing auto-classification flow, immutability rules, and reclassify skill
- [ ] Add entry to `docs/features/README.md` index table

### Inline Documentation
- [ ] Document classification flow in bridge code comments at the integration point
- [ ] Update `.claude/skills/make-plan/SKILL.md` Phase 2 with auto-classification behavior

## Success Criteria

- [ ] Incoming Telegram messages are auto-classified before `/make-plan` runs
- [ ] Classification is pre-populated in plan template during drafting
- [ ] Attempts to change `type:` after `status: Ready` are blocked with clear error
- [ ] `/reclassify <type>` skill works during Planning status
- [ ] All existing tests still pass (`pytest tests/`)
- [ ] Documentation updated and indexed

## Team Orchestration

### Team Members

- **Builder (classification-integration)**
  - Name: classification-builder
  - Role: Integrate auto-classification into bridge, add model fields, wire to make-plan
  - Agent Type: builder
  - Resume: true

- **Builder (immutability-and-reclassify)**
  - Name: hooks-builder
  - Role: Add label immutability hook and reclassify skill
  - Agent Type: builder
  - Resume: true

- **Validator (integration)**
  - Name: integration-validator
  - Role: Verify all classification flows work end-to-end
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Create feature documentation
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Add Model Fields
- **Task ID**: build-model-fields
- **Depends On**: none
- **Assigned To**: classification-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `classification_type = Field(null=True)` and `classification_confidence = Field(type=float, null=True)` to `AgentSession` in `models/sessions.py`
- Add `classification_type = Field(null=True)` to `RedisJob` in `agent/job_queue.py`
- Add `classification_type` parameter to `enqueue_job()` function signature and pass to `RedisJob.create()`

### 2. Bridge Auto-Classification
- **Task ID**: build-auto-classify
- **Depends On**: build-model-fields
- **Assigned To**: classification-builder
- **Agent Type**: builder
- **Parallel**: false
- Extend `classify_and_update_reaction()` in `bridge/telegram_bridge.py:722` to call `classify_request_async(clean_text)`
- Store result in `AgentSession` via `session_id` (available at line 710-714)
- Pass `classification_type` to `enqueue_job()` call at line 876
- Handle classification failure gracefully (leave field null)

### 3. Make-Plan Pre-Population
- **Task ID**: build-prepopulate
- **Depends On**: build-auto-classify
- **Assigned To**: classification-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `.claude/skills/make-plan/SKILL.md` Phase 2 to note that `type:` may be pre-populated
- Add instruction for agent to check session context / job metadata for classification_type
- Allow manual override during drafting

### 4. Label Immutability Hook
- **Task ID**: build-immutability
- **Depends On**: none
- **Assigned To**: hooks-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `.claude/hooks/validators/validate_type_immutability.py` following `validate_plan_label.py` pattern
- Read current file and git HEAD version; compare frontmatter `type:` and `status:` fields
- If previous status was `Ready`/`In Progress`/`Complete` and type changed → exit 2 with error message
- Register hook in appropriate skill Stop hooks

### 5. Reclassify Skill
- **Task ID**: build-reclassify
- **Depends On**: none
- **Assigned To**: hooks-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `.claude/skills/reclassify/SKILL.md`
- Accept type argument (bug/feature/chore)
- Find the active plan document (from session context or current branch)
- Validate status is `Planning` — reject if `Ready` or beyond
- Update frontmatter `type:` field and confirm change

### 6. Integration Validation
- **Task ID**: validate-integration
- **Depends On**: build-auto-classify, build-prepopulate, build-immutability, build-reclassify
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify model fields exist on `AgentSession` and `RedisJob`
- Test `classify_request_async()` returns valid result
- Verify immutability hook rejects type changes after Ready status
- Verify reclassify skill structure
- Run `pytest tests/` to ensure no regressions

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-integration
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/classification.md`
- Add entry to `docs/features/README.md`
- Update `.claude/skills/make-plan/SKILL.md` inline docs

### 8. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met (including documentation)
- Generate final report

## Validation Commands

- `python -c "from tools.classifier import classify_request_async; print('OK')"` - Classifier async function importable
- `python -c "from models.sessions import AgentSession; assert hasattr(AgentSession, 'classification_type'); print('OK')"` - AgentSession has classification field
- `python -c "from agent.job_queue import RedisJob; assert hasattr(RedisJob, 'classification_type'); print('OK')"` - RedisJob has classification field
- `python .claude/hooks/validators/validate_type_immutability.py docs/plans/issue-classification-enhancements.md` - Immutability hook runs
- `ls .claude/skills/reclassify/SKILL.md` - Reclassify skill exists
- `test -f docs/features/classification.md && echo "OK"` - Feature documentation exists
- `pytest tests/ -x -q` - All tests pass
