# Model Data Integrity Audit

Audit Popoto Redis ORM models for data integrity violations: redundant state, misclassified fields, orphan writes/reads, and stale-property risks. This complements the structural audit skill (`.claude/skills/audit-models/SKILL.md`) which covers relationships and naming.

## Principles

### 1. Single Source of Truth

Every piece of information on a model must have exactly ONE authoritative source. If the same information can be derived from existing stored state, it must be a `@property`, not a stored `Field`. Flag any stored field that duplicates or could be computed from other fields on the same model (or related models reachable by key lookup).

### 2. Stored vs Derived

- **Stored `Field()`**: For canonical data that cannot be computed from other state. Examples: timestamps of external events, user-provided input, foreign keys.
- **`@property`**: For anything derivable from stored fields. If a field is written in one place and read in another purely to cache a computation, it should be a property instead (Redis is already in-memory -- caching a derived value in another field adds sync risk for negligible performance gain).

### 3. No Redundant State

Two fields that must be kept in sync are a bug waiting to happen. Flag pairs where one could derive from the other. Classic example: a `classification_type` field alongside a property like `is_sdlc` that derives from that field -- the property pattern is correct here since `is_sdlc` simply checks `classification_type == "sdlc"`.

### 4. Field Usage Audit

For each stored `Field` on every model, trace:
- **(a) Writers**: Every code path that sets the field (grep for `model.field_name =` and `field_name=` in constructors/create calls)
- **(b) Readers**: Every code path that reads the field (grep for `model.field_name` access, excluding writes)
- **(c) Derivability**: Whether the value could be computed from other stored fields instead

Flag:
- **Write-only fields**: Written but never read (dead data, delete them)
- **Read-only fields**: Read but never written after creation (verify they are set on create; if not, they are always-default dead weight)
- **Derivable fields**: Could be replaced by a `@property` computing the same value from other stored fields

### 5. Property Correctness

For each `@property` on every model, verify:
- It reads only from stored fields on `self`, other properties on `self`, or stable external lookups (e.g., related model by key)
- It does NOT depend on transient runtime state, mutable globals, or fields on unrelated models without a key relationship
- It does NOT have side effects (no writes, no API calls, no logging)

## Execution Steps

### Step 1: Discover Models

```bash
# Find all Popoto model classes
grep -rn "class.*Model)" models/ --include="*.py"
```

Read every file in `models/` (excluding `__init__.py`). For each class inheriting from `Model`, extract:
- Class name
- All `Field()`, `KeyField()`, `SortedField()`, `AutoKeyField()` declarations with their names
- All `@property` methods with their return expressions
- All regular methods that read or write fields

### Step 2: Build Field Inventory

For each model, build a table:

| Field Name | Field Type | Writers (file:line) | Readers (file:line) | Derivable? | Issue |
|------------|-----------|--------------------|--------------------|-----------|-------|

Use `Grep` across the entire codebase (not just `models/`) to find all read and write sites for each field.

### Step 3: Check Principles

For each model, check all five principles and flag violations:

- **[redundant-state]** Two fields where one could derive from the other
- **[should-be-property]** A stored field that is only set by copying/computing from other stored fields
- **[write-only]** Field is written but never read anywhere
- **[read-never-written]** Field is read but never explicitly written (always uses default)
- **[stale-property]** A @property that depends on external mutable state without a key relationship
- **[side-effect-property]** A @property that writes data or calls external services
- **[sync-risk]** Two fields that must be manually kept in sync across code paths

### Step 4: Output Report

```
## Data Integrity Audit Report

### Models Scanned
- ModelName: N stored fields, M properties

### Field Inventory
(One table per model, as described in Step 2)

### Findings

#### CRITICAL
- [redundant-state] ModelName: `field_a` duplicates what `field_b` + `field_c` already express
- [write-only] ModelName.field_x: written at bridge/foo.py:42 but never read

#### WARNING
- [should-be-property] ModelName.derived_field: only set by computing from `base_field`, convert to @property
- [sync-risk] ModelName: `status` and `status_label` must be updated together (3 call sites, only 2 update both)

#### INFO
- [read-never-written] ModelName.optional_field: always uses default value "", consider removing
```

### Step 5: Recommendations

For each finding, recommend a specific action:
- **Delete**: Remove the field entirely (write-only, read-never-written)
- **Convert to @property**: Replace stored field with a computed property
- **Consolidate**: Merge two redundant fields into one stored + one derived
- **Fix property**: Remove external state dependency from a @property

## After the Audit

This command produces findings only. Do NOT auto-fix anything. Next steps:
- Discuss findings with the architect
- Create a GitHub issue for actionable fixes
- Use `/sdlc` to plan and execute changes
