---
name: do-oop-audit
description: "Audit Python classes and OOP code for structural anti-patterns, naming inconsistencies, and data modeling issues. Use when reviewing class design, checking model health, validating object boundaries, or after refactoring. Also triggered by 'check my classes', 'review the data model', 'are there OOP problems', 'scan for design issues', 'lint class structure', 'audit models', 'validate OOP', or 'review object hierarchy'."
allowed-tools: Read, Grep, Glob, Bash
disable-model-invocation: true
---

# OOP / Data Modeling Audit

Scans Python class definitions for 14 structural anti-patterns covering field semantics, object boundaries, inheritance design, naming consistency, and coupling. Framework-agnostic: works with Django, SQLAlchemy, Pydantic, dataclasses, and vanilla Python. Produces a severity-grouped findings report and pauses for human review.

## What this skill does

1. Scans the target path for all `.py` files containing class definitions
2. Runs 14 semantic checks against each class and its relationships
3. Produces a structured findings report organized by severity (CRITICAL, WARNING, INFO)
4. Pauses for discussion — no auto-fix, findings only

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

### Example: E-commerce models

```
## OOP Audit Report

### Items Scanned
- User (12 fields, 3 methods) — models/user.py [Django]
- Order (23 fields, 8 methods) — models/order.py [Django]
- OrderItem (6 fields, 2 methods) — models/order.py [Django]
- ShippingAddress (9 fields) — models/shipping.py [Django]
- BillingAddress (8 fields) — models/shipping.py [Django]

### Findings

#### CRITICAL
- [god-object] Order: 23 fields spanning order details, shipping, billing, and payment status — split into Order + OrderShipping + OrderBilling
- [circular-reference] models/user.py <-> models/order.py: User imports Order for `recent_orders()`, Order imports User for `placed_by` — break with lazy import or move `recent_orders` to a service

#### WARNING
- [bool-should-be-timestamp] User.is_verified: boolean loses when verification happened — use `verified_at: datetime | None`
- [bool-should-be-timestamp] Order.is_shipped: same pattern — use `shipped_at: datetime | None`
- [inconsistent-naming] created_at vs date_created: User uses `created_at`, Order uses `date_created` — standardize to `created_at`
- [merge-candidates] ShippingAddress + BillingAddress: 7 of 9 fields identical, always used together — merge into Address with `address_type` enum
- [missing-base-class] User, Order, ShippingAddress: all define `created_at`, `updated_at`, `is_active` — extract TimestampedMixin
- [stringly-typed-field] Order.payment_meta: stores JSON as TextField — use JSONField or a structured PaymentInfo model

#### INFO
- [derived-field-not-property] Order.total_price: always computed as sum of OrderItem prices — should be @property or @cached_property
- [missing-semantic-type] User.email: raw CharField — use EmailField for built-in validation

### Summary
PASS: 60  WARN: 6  FAIL: 2
```

### Example: Pydantic API schemas

```
## OOP Audit Report

### Items Scanned
- UserCreate (5 fields) — schemas/user.py [Pydantic]
- UserResponse (8 fields) — schemas/user.py [Pydantic]
- APIConfig (18 fields, 2 validators) — config/settings.py [Pydantic]

### Findings

#### CRITICAL
- [god-object] APIConfig: 18 fields covering database, auth, email, storage, and feature flags — split into DatabaseConfig, AuthConfig, EmailConfig, etc.

#### WARNING
- [missing-base-class] UserCreate, UserResponse: share 5 identical fields — extract UserBase

#### INFO
- [empty-subclass] UserResponse: only adds `id` and `created_at` to UserCreate fields — consider a single User model with optional fields

### Summary
PASS: 38  WARN: 1  FAIL: 1
```

---

## After the Audit

Findings only. The skill never modifies source files. Next steps are decided by the human:

- Fix critical findings first (god objects, circular references)
- Batch warning-level fixes into a refactoring PR
- Track info-level findings as tech debt for future sprints
- Re-run the audit after fixes to verify resolution

## Version history

- v1.0.0 (2026-03-24): Initial — 14 checks, framework detection, severity filtering
