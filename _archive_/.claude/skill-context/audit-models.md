# audit-models context — this repo (ai)

This repo's declarations for the `/audit-models` checks. The global skill body runs a generic
baseline; this file supplies the repo-specific values.

## ORM and location

Popoto (Redis-backed ORM). All models live in `models/*.py`. Field kinds to extract:
`KeyField`, `Field`, `SortedField`, `AutoField`. Findings must recommend Popoto ORM
operations only — never raw Redis on Popoto-managed keys (enforced by
`.claude/hooks/validators/validate_no_raw_redis_delete.py`).

## Universal fields (check 1)

- `project_key` (KeyField) — required on every model that stores project-scoped data.

**Exempt models:** `ReflectionIgnore` (global config, not project-scoped).

## Legacy / deprecated field terms (check 3)

Flag field names containing: `job`, `redis`, `log`.

## Naming conventions (check 3)

- `parent_agent_session_id` is the canonical FK to AgentSession. Flag any new
  `agent_session_id`-shaped FK that doesn't follow it.
- The `session_id` namespace is reserved for Claude Code / SDK session ids — flag fields
  using `session_id` to reference an AgentSession.
