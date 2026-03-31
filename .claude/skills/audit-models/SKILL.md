---
name: audit-models
description: "Audit Popoto Redis models for relationship gaps, missing fields, naming inconsistencies, and architectural weaknesses. Use when reviewing data model health with the architect."
disable-model-invocation: true
allowed-tools: Read, Grep, Glob, Bash
---

# Model Audit

Introspects `models/*.py` to surface structural weaknesses in the Popoto data layer. Designed for human-in-the-loop review sessions with the architect (Tom).

## What this skill does

1. Scan all model files in `models/` and extract class definitions, fields, types, and keys
2. Run heuristic checks against the config below
3. Output a structured findings report organized by severity
4. Pause for discussion — do NOT auto-fix anything

## Audit Checks

### 1. Missing Universal Fields

Fields declared as universal should appear on every model (or have an explicit exemption).

**Universal fields:**
- `project_key` (KeyField) — every model that stores project-scoped data

**Exempt models:** ReflectionIgnore (global config, not project-scoped)

### 2. Orphan Models

Models with no foreign key reference to or from any other model. A model is "connected" if:
- It has a field that references another model's primary key, OR
- Another model has a field referencing its primary key

### 3. Naming Consistency

Flag when:
- The same concept uses different field names across models (e.g. `session_id` vs `agent_session_id` for the same thing)
- Field names contain legacy/deprecated terms (configurable: `job`, `redis`, `log`)
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

1. Extract: class name, all fields with their types and popoto field kinds (KeyField, Field, SortedField, etc.)
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
- Update the config in this skill if exemptions are justified
- Use `/sdlc` to plan and execute fixes
