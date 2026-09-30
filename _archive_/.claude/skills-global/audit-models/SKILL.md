---
name: audit-models
description: "Audit Popoto Redis models for gaps and naming drift. Use when reviewing data model health or the data layer, checking model integrity, validating Redis models, or scanning for model issues."
disable-model-invocation: true
allowed-tools: Read, Grep, Glob, Bash
---

# Model Audit

**Goal:** surface structural weaknesses in the data-model layer — relationship gaps, missing fields, naming drift, implicit coupling — by extracting every model's fields, types, and keys from `models/*.py` and running the six checks below. Findings only, grouped by severity; never auto-fix. Designed for human-in-the-loop review with the project architect.

## Repo Context Probe

If `.claude/skill-context/audit-models.md` exists, read it and honor its declarations; otherwise use the generic defaults described below.

The context file is where a repo declares its ORM, its universal fields (fields every model must carry), its exempt models, and its legacy field terms. When absent, the generic defaults listed under each check apply.

## Audit Checks

### 1. Missing Universal Fields

Fields declared as universal should appear on every model (or have an explicit exemption). The repo's context file declares the universal-field list and the exempt models.

**Generic default (no context file):** treat any field present on most models (~75%+) as a candidate universal field, and flag the models missing it for discussion rather than as hard failures.

### 2. Orphan Models

Models with no foreign key reference to or from any other model. A model is "connected" if:
- It has a field that references another model's primary key, OR
- Another model has a field referencing its primary key

### 3. Naming Consistency

Flag when:
- The same concept uses different field names across models (e.g. `session_id` vs `agent_session_id` for the same thing)
- Field names contain legacy/deprecated terms (the context file declares the repo's list; none by default)
- A field name is ambiguous without its model context (e.g. `id` fields that don't clarify what they identify)

### 4. Implicit Proxy Relationships

Two models share a KeyField (e.g. both have `chat_id`) but one has a field the other lacks (e.g. `project_key`). This suggests the model without the field is using the shared key as an implicit proxy for the missing field.

### 5. Cardinality Documentation

For each pair of related models, state the expected cardinality:
- 1:1, 1:N, N:M
- Flag any relationship where cardinality is unclear or undocumented

### 6. Key Type Consistency

When two models share a field name, their types and key kinds should match (e.g. `chat_id` should be the same type everywhere — not KeyField in one and Field in another).

## How to Run

Read every file in `models/` (excluding `__init__.py`). For each model class:

1. Extract: class name, all fields with their types and key kinds (for Popoto: KeyField, Field, SortedField, etc. — adapt to the repo's ORM)
2. Build a cross-model field matrix
3. Run each check above
4. Present findings grouped by severity:
   - **CRITICAL**: Orphan models, missing universal fields on core models
   - **WARNING**: Naming drift, implicit proxies, type inconsistencies
   - **INFO**: Cardinality documentation gaps

## Output Format

```
## Model Audit Report

### Models Scanned
- ModelName (N fields, keys: [list])

### Findings

#### CRITICAL
- [missing-universal] TelegramMessage: missing `project_key`
- [orphan] DeadLetter: no FK references to/from other models

#### WARNING
- [naming-drift] AgentSession.agent_session_id vs convention: should be `id`
- [implicit-proxy] Link uses `chat_id` as proxy for missing `project_key`

#### INFO
- [cardinality] AgentSession -> BridgeEvent: expected 1:N, no FK exists
```

## After the Audit

This skill produces findings only. Next steps are decided by the human:
- Create a GitHub issue for actionable findings
- Update the repo's context file (`.claude/skill-context/audit-models.md`) if exemptions are justified
- Plan and execute fixes through the repo's standard development workflow (in this repo: the SDLC pipeline)
