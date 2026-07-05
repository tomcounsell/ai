---
name: do-oop-audit
description: "Audit Python classes for OOP and data-modeling anti-patterns. Use when reviewing class design, boundaries, or hierarchy, or 'check my classes', 'lint class structure', 'scan for design issues'."
allowed-tools: Read, Grep, Glob, Bash
disable-model-invocation: true
---

# OOP / Data Modeling Audit

**Goal:** surface structural weaknesses in Python class design — field semantics, object boundaries, inheritance, naming consistency, coupling — by running the 14 checks below against every class in the target path and reporting findings grouped by severity. Framework-agnostic: Django, SQLAlchemy, Pydantic, dataclasses, and vanilla Python. Findings only — the skill never modifies source files and pauses for human review. Also the right skill for requests like "review the data model", "are there OOP problems", or a class-design review after refactoring.

## Invocation

```
/do-oop-audit [path] [--framework django|sqlalchemy|pydantic|auto] [--severity critical|warning|info]
```

- `path`: Directory or file to audit. Defaults to current project root.
- `--framework`: Force a framework context. Default is `auto` (detect from imports and base classes).
- `--severity`: Minimum severity to include in the report. Default: show all.

## Quick start

1. **Enumerate**: Find all `.py` files in the target path containing `class ` definitions
2. **Detect framework**: Identify Django / SQLAlchemy / Pydantic / dataclasses / vanilla (see Framework Detection below)
3. **Check**: Run each of the 14 audit checks against every class and cross-class relationship
4. **Filter**: If `--severity` is set, exclude findings below the threshold
5. **Report**: Present findings grouped by severity
6. **Pause**: Wait for human review — never modify source files

---

## Framework Detection

Before running checks, detect the framework(s) in use. A project may use multiple frameworks.

| Framework | Detection signals |
|-----------|------------------|
| **Django** | `from django.db import models`, base class `models.Model`, `class Meta:`, `ForeignKey`, `ManyToManyField` |
| **SQLAlchemy** | `__tablename__`, `Column()`, `relationship()`, `declarative_base()`, `mapped_column()` |
| **Pydantic** | `BaseModel` base class, `model_config`, `Field()`, `model_validator` |
| **dataclasses** | `@dataclass` decorator, `field()` from `dataclasses` |
| **Vanilla** | Plain `class` with `__init__`, no ORM/validation framework detected |

Apply framework-specific heuristics when relevant:
- Django: `ForeignKey` implies relationships; `Meta.ordering` hints at query patterns
- SQLAlchemy: `relationship()` back-references reveal coupling
- Pydantic: `Field(...)` validators reveal semantic types
- dataclasses: `field(default_factory=...)` hints at mutable defaults

---

## Audit Checks

### 1. bool-should-be-timestamp
Fields like `is_verified: bool` lose temporal information. A `verified_at: datetime | None` preserves *when* something happened, not just *whether* it happened, enabling time-based queries and debugging.
**Severity**: WARNING

### 2. stringly-typed-field
Fields storing structured data as raw strings: JSON blobs, comma-separated lists, encoded enums, URLs-as-str. These bypass validation and make the schema lie about its types. The fix is a proper type (dict, list, Enum, `HttpUrl`, etc.).
**Severity**: WARNING

### 3. misnamed-field
Field names that no longer match their actual usage after codebase evolution. Example: `description` now holds markdown content, `email` now holds a username. Misleading names cause bugs when new developers trust the name over the implementation.
**Severity**: WARNING

### 4. derived-field-not-property
Fields whose value is always computed from other fields and never set independently. These should be `@property` (or `@computed_field` in Pydantic) to avoid stale data and redundant storage.
**Severity**: INFO

### 5. missing-semantic-type
Raw `str` or `int` where a domain type (Email, URL, Money, PhoneNumber, etc.) would add clarity and validation. Using primitive types for domain concepts scatters validation logic across the codebase instead of centralizing it.
**Severity**: INFO

### 6. god-object
Classes with 15+ fields spanning multiple concerns. These resist testing, resist change, and accumulate merge conflicts. Should be decomposed into focused classes with relationships.
**Severity**: CRITICAL

### 7. merge-candidates
Two classes with nearly identical fields, always used together, or connected by a 1:1 relationship that adds no value. The extra indirection increases complexity without benefit. Suggest merging or flattening.
**Severity**: WARNING

### 8. missing-base-class
Multiple classes sharing 3+ identical fields or methods that should inherit from a common base or mixin. Duplicated structure means duplicated bugs and divergent evolution.
**Severity**: WARNING

### 9. missing-junction-model
Many-to-many relationships with extra data stored on one side instead of a proper junction/through model. The extra data becomes impossible to query independently and creates awkward update patterns.
**Severity**: WARNING

### 10. deep-inheritance
Inheritance trees 3+ levels deep where composition or mixins would be cleaner. Deep hierarchies create fragile coupling: changing a grandparent breaks grandchildren unpredictably.
**Severity**: WARNING

### 11. empty-subclass
Subclasses that add no fields or methods beyond what the parent provides. These should typically be a field or enum value on the parent instead. The type system is being abused for data that belongs in a column.
**Severity**: INFO

### 12. inconsistent-naming
The same concept named differently across classes. Examples: `created_at` vs `date_created` vs `creation_date`, or `user_id` vs `owner_id` for the same FK. Inconsistency forces developers to memorize arbitrary conventions per class.
**Severity**: WARNING

### 13. circular-reference
Classes that import each other, creating dependency cycles. These resist refactoring, cause import-order bugs, and make the dependency graph unpredictable. Break with lazy imports, protocols, or architectural layering.
**Severity**: CRITICAL

### 14. over-coupled-class
A class that directly references 5+ other domain classes (via fields, type hints, or method signatures). High fan-out means changes ripple unpredictably — this class is a change amplifier that should be simplified or mediated.
**Severity**: WARNING

---

## Output Format

Present findings using this structure. Adapt the content to actual findings.

```
## OOP Audit Report

### Items Scanned
- ClassName (N fields, M methods) — path/to/file.py
  [Framework: Django | SQLAlchemy | Pydantic | dataclass | vanilla]

### Findings

#### CRITICAL
- [check-name] ClassName: specific finding with evidence and recommendation

#### WARNING
- [check-name] ClassName: specific finding with evidence and recommendation

#### INFO
- [check-name] ClassName: specific finding with evidence and recommendation

### Summary
PASS: N  WARN: N  FAIL: N
```

### Example findings (style reference)

Every finding names its check, the class, concrete evidence, and a recommendation:

- [god-object] Order: 23 fields spanning order details, shipping, billing, and payment status — split into Order + OrderShipping + OrderBilling
- [circular-reference] models/user.py <-> models/order.py: User imports Order for `recent_orders()`, Order imports User for `placed_by` — break with lazy import or move `recent_orders` to a service
- [bool-should-be-timestamp] User.is_verified: boolean loses when verification happened — use `verified_at: datetime | None`
- [merge-candidates] ShippingAddress + BillingAddress: 7 of 9 fields identical, always used together — merge into Address with `address_type` enum
- [derived-field-not-property] Order.total_price: always computed as sum of OrderItem prices — should be @property or @cached_property

---

## After the Audit

Findings only. The skill never modifies source files. Next steps are decided by the human:

- Fix critical findings first (god objects, circular references)
- Batch warning-level fixes into a refactoring PR
- Track info-level findings as tech debt for future sprints
- Re-run the audit after fixes to verify resolution
