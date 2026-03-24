# OOP / Data Modeling Audit Skill

**Skill path**: `.claude/skills/do-oop-audit/SKILL.md`
**Type**: Prompt-only audit (no script)
**Disposition**: Report only (read-only, never modifies source files)

## What it does

The `/do-oop-audit` skill scans Python class definitions for 14 structural anti-patterns covering field semantics, object boundaries, inheritance design, naming consistency, and coupling. It works on any Python project and detects framework context automatically (Django, SQLAlchemy, Pydantic, dataclasses, vanilla Python).

## Usage

```
/do-oop-audit [path] [--framework django|sqlalchemy|pydantic|auto] [--severity critical|warning|info]
```

- `path`: Directory or file to audit (defaults to project root)
- `--framework`: Override auto-detection
- `--severity`: Filter minimum severity to report

## Audit checks

| # | Check | Severity | What it catches |
|---|-------|----------|----------------|
| 1 | bool-should-be-timestamp | WARNING | Boolean fields that lose temporal information |
| 2 | stringly-typed-field | WARNING | Structured data stored as raw strings |
| 3 | misnamed-field | WARNING | Field names that no longer match actual usage |
| 4 | derived-field-not-property | INFO | Computed values stored as fields instead of properties |
| 5 | missing-semantic-type | INFO | Raw primitives where domain types would add validation |
| 6 | god-object | CRITICAL | Classes with 15+ fields spanning multiple concerns |
| 7 | merge-candidates | WARNING | Redundant classes that should be combined |
| 8 | missing-base-class | WARNING | Duplicated fields/methods across classes without shared base |
| 9 | missing-junction-model | WARNING | M2M relationships with extra data on the wrong side |
| 10 | deep-inheritance | WARNING | Inheritance trees 3+ levels deep |
| 11 | empty-subclass | INFO | Subclasses that add nothing beyond the parent |
| 12 | inconsistent-naming | WARNING | Same concept named differently across classes |
| 13 | circular-reference | CRITICAL | Mutual imports creating dependency cycles |
| 14 | over-coupled-class | WARNING | Classes referencing 5+ other domain classes |

## Output format

Findings are grouped by severity (CRITICAL, WARNING, INFO) with a summary line showing PASS/WARN/FAIL counts. Each finding includes the check name, affected class, specific evidence, and a recommendation.

## Design decisions

- **Prompt-only**: All 14 checks require semantic judgment (e.g., "is this a god object?", "does this field name match its usage?"). A static analyzer cannot reliably answer these questions.
- **Framework-agnostic**: Auto-detects the framework from imports and base classes, then applies framework-specific heuristics (e.g., Django ForeignKey implies relationships, Pydantic Field() reveals semantic types).
- **Report-only disposition**: The skill presents findings for human review and never modifies source files. Fixes are left to the developer.

## Related

- `/audit-models`: Popoto Redis model-specific audit (different scope)
- `/do-docs-audit`: Documentation accuracy audit (same pattern)
- `/do-skills-audit`: Skill file quality audit (same pattern)
- `.claude/skills/new-audit-skill/AUDIT_TEMPLATE.md`: Template this skill follows
