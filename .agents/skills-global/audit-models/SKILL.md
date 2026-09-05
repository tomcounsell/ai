---
name: audit-models
description: "Audit data models for missing fields, naming drift, implicit relationships, cardinality, and inconsistent key types."
---

# Audit Models

Read the model layer and any project-declared ORM, universal fields, exemptions, and deprecated terms. Build a cross-model matrix of fields, types, key kinds, and actual relationships, tracing usage where names alone are ambiguous.
Check missing universal fields, apparently orphaned models, inconsistent names for the same concept, shared keys acting as hidden proxies, undocumented cardinalities, and type/key mismatches across relationships. Without a declared universal field, a field present on most models is a discussion candidate, not an automatic defect. A standalone model is not inherently broken: verify its role before assigning severity.
In Valor, inspect Popoto definitions without raw Redis reads or writes. Report confirmed structural problems separately from suspected coupling and documentation gaps, with evidence and practical consequences. Apply changes only when requested; do not mutate live model data to conduct a structural audit.
