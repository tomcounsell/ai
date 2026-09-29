---
name: audit-models
description: "Audit data models for relationship gaps and naming drift. Use when reviewing, validating, or checking the data layer or models."
disable-model-invocation: true
allowed-tools: Read, Grep, Glob, Bash
model: sonnet
effort: medium
---

# Model Audit

Surface structural weaknesses in the data-model layer: relationship gaps, missing fields,
naming drift, implicit coupling. This is for review with the project architect, so it
produces findings only and never fixes anything.

Done when: every model class in the model directory (default `models/*.py`) is listed with its
fields, types, and key kinds, all six checks have run across the cross-model field matrix,
and findings are reported by severity.

## Repo context

If `.claude/skill-context/audit-models.md` exists, read it and follow it. It declares the ORM,
the universal fields and exempt models, the legacy terms, and naming conventions. Without it,
adapt to whatever ORM the models use.

## Checks

| Check | Severity | Flag when |
|---|---|---|
| `missing-universal` | CRITICAL on core models, else WARNING | A declared universal field is absent without an exemption. With no declaration, treat fields on about 75% or more of models as candidates and flag the rest for discussion |
| `orphan` | CRITICAL | No field references another model's key, and no model references this one |
| `naming-drift` | WARNING | One concept under different field names, a legacy term, or an ambiguous name such as a bare `id` |
| `implicit-proxy` | WARNING | Two models share a key field, and one has a field the other lacks. The shared key is likely standing in for the missing field |
| `key-type` | WARNING | A field name shared across models with a different type or key kind |
| `cardinality` | INFO | A related pair whose cardinality (1:1, 1:N, N:M) is unclear or undocumented |

## Report

```
## Model Audit Report
### Models Scanned
- ModelName (N fields, keys: [...])
### Findings
#### CRITICAL
- [orphan] DeadLetter: no references to or from other models
#### WARNING
- [implicit-proxy] Link uses `chat_id` as a proxy for missing `project_key`
#### INFO
```

The human chooses the next steps: file issues, add justified exemptions to the context file,
or route fixes into the repo's standard development workflow.
