---
status: Planning
type: feature
appetite: Small: 1-2 days
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
- Notion integration exists but Type property sync not implemented

**Desired outcome:**
- Incoming messages auto-classified before plan creation
- Classification locks when plan status changes to `Ready`
- Smooth reclassification UX during drafting phase
- Notion Type property set automatically (when using Notion)

## Appetite

**Time budget:** Small: 1-2 days

**Team size:** Solo

## Prerequisites

No prerequisites — classifier module (`tools/classifier.py`) and validation hooks already exist from #17.

## Solution

### Key Elements

- **Auto-classification hook**: Classify incoming Telegram messages using existing `classify_request_async()` before plan creation
- **Label immutability**: Add frontmatter validation that rejects `type:` changes after `status: Ready`
- **Reclassification command**: Simple `/reclassify <type>` skill for smooth drafting-phase changes
- **Notion sync**: Set Type property when creating Notion tasks (via MCP)

### Flow

**Auto-classification:**
Telegram message → Bridge receives → `classify_request_async()` → Classification stored in session context → `/make-plan` uses pre-classified type

**Immutability:**
Plan edit attempted → Hook checks: was previous status `Ready`+? → If yes and type changed → Block with error

**Reclassification:**
User: "this is actually a bug" → Agent invokes `/reclassify bug` → Updates frontmatter `type: bug` → Confirms change

### Technical Approach

1. **Bridge integration** — Call classifier on message intake, store result in session metadata
2. **Make-plan enhancement** — Read classification from session context, pre-populate `type:` field
3. **Stop hook addition** — Validate type immutability after Ready status
4. **Reclassify skill** — Simple skill that edits frontmatter type field

## Rabbit Holes

- **ML-based classification improvements** — Haiku is good enough; don't train custom models
- **Classification confidence thresholds** — Don't add complex logic for low-confidence cases; just use the classification
- **Historical reclassification audit logs** — Not needed; git history is sufficient
- **Multi-label support** — One type per plan; don't add support for multiple classifications

## Risks

### Risk 1: Auto-classification adds latency to message intake
**Impact:** Slower response times on first message
**Mitigation:** Classify asynchronously; don't block message acknowledgment. Use result when available, fall back to manual if not.

### Risk 2: Immutability blocks legitimate corrections
**Impact:** User can't fix misclassification after approval
**Mitigation:** Allow override with explicit flag (e.g., `--force-reclassify`) and log the change.

## No-Gos (Out of Scope)

- Custom ML classification models
- Classification analytics or dashboards
- Batch reclassification of existing plans
- Integration with external issue trackers beyond GitHub/Notion
- Classification hierarchy or sub-types

## Update System

No update system changes required — this feature uses existing dependencies (classifier module, Haiku API) and doesn't add new config files or services.

## Agent Integration

**Bridge changes required:**
- `bridge/telegram_bridge.py` needs to call `classify_request_async()` on message intake
- Store classification result in session metadata for `/make-plan` to consume

**No MCP changes needed** — classifier is called internally by the bridge, not exposed as an agent tool.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/classification.md` (create if not exists) describing auto-classification and immutability
- [ ] Add entry to `docs/features/README.md` index table

### Inline Documentation
- [ ] Document classification flow in bridge code comments
- [ ] Update make-plan SKILL.md with auto-classification behavior

## Success Criteria

- [ ] Incoming Telegram messages are auto-classified before `/make-plan` runs
- [ ] Classification is pre-populated in plan template during drafting
- [ ] Attempts to change `type:` after `status: Ready` are blocked with clear error
- [ ] `/reclassify <type>` skill works during Planning status
- [ ] Notion Type property is set when creating Notion tasks (if project uses Notion)
- [ ] Documentation updated and indexed

## Team Orchestration

### Team Members

- **Builder (classification-integration)**
  - Name: classification-builder
  - Role: Integrate auto-classification into bridge and make-plan
  - Agent Type: builder
  - Resume: true

- **Builder (immutability-hooks)**
  - Name: hooks-builder
  - Role: Add label immutability validation to stop hooks
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

### 1. Bridge Auto-Classification
- **Task ID**: build-auto-classify
- **Depends On**: none
- **Assigned To**: classification-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `classify_request_async()` call in `bridge/telegram_bridge.py` message handler
- Store classification in session metadata (Redis AgentSession)
- Ensure async execution doesn't block message acknowledgment

### 2. Make-Plan Classification Pre-Population
- **Task ID**: build-prepopulate
- **Depends On**: build-auto-classify
- **Assigned To**: classification-builder
- **Agent Type**: builder
- **Parallel**: false
- Update make-plan skill to read classification from session context
- Pre-populate `type:` field in plan template
- Allow manual override during drafting

### 3. Label Immutability Hook
- **Task ID**: build-immutability
- **Depends On**: none
- **Assigned To**: hooks-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `.claude/hooks/validators/validate_type_immutability.py`
- Check if previous version had `status: Ready` or beyond
- If type changed, return validation error
- Add hook to make-plan SKILL.md Stop hooks

### 4. Reclassify Skill
- **Task ID**: build-reclassify
- **Depends On**: none
- **Assigned To**: hooks-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `.claude/skills/reclassify/SKILL.md`
- Accept type argument (bug/feature/chore)
- Update frontmatter of current plan in context
- Validate status is still Planning

### 5. Notion Type Property Sync
- **Task ID**: build-notion-sync
- **Depends On**: none
- **Assigned To**: classification-builder
- **Agent Type**: builder
- **Parallel**: true
- Update make-plan skill Notion task creation
- Set Type property using Notion MCP tools
- Handle case where Type property doesn't exist in database

### 6. Integration Validation
- **Task ID**: validate-integration
- **Depends On**: build-auto-classify, build-prepopulate, build-immutability, build-reclassify, build-notion-sync
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Test auto-classification on sample messages
- Verify pre-population in plan template
- Test immutability enforcement
- Test reclassify skill
- Verify Notion sync (if test database available)

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-integration
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/classification.md`
- Add entry to `docs/features/README.md`
- Update make-plan SKILL.md inline docs

### 8. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met
- Generate final report

## Validation Commands

- `python -c "from tools.classifier import classify_request; print(classify_request('fix the broken button'))"` - Classifier works
- `grep -q "classify_request" bridge/telegram_bridge.py && echo "OK"` - Bridge integration exists
- `ls .claude/hooks/validators/validate_type_immutability.py` - Immutability hook exists
- `ls .claude/skills/reclassify/SKILL.md` - Reclassify skill exists
- `cat docs/features/classification.md` - Documentation exists

## Open Questions

1. **Confidence threshold**: Should low-confidence classifications (< 0.7) prompt user for confirmation, or just use the best guess?
2. **Override mechanism**: For immutability, should we allow `--force` override or require plan status rollback to Planning first?
3. **Notion database schema**: Should we auto-create the Type property if it doesn't exist, or just skip and warn?
