# PM Autonomous Skills: Friction Detection, Generation, Tracking, Expiry

The skill lifecycle system enables the PM to autonomously detect friction patterns, generate new skills via `/skillify`, track their effectiveness through analytics, and expire unused skills after a configurable period. This closes the loop between observing repeated pain points and shipping self-improving tooling.

## Lifecycle Overview

```
Reflections (daily maintenance)
  -> detect_friction(): scan Memory corrections for tool-related tags
  -> Surface friction patterns to PM session
  -> PM invokes /skillify to generate a new skill
  -> Generated skill ships via normal PR pipeline
  -> Analytics tracks invocations per skill
  -> refresh: extend expiry for recently used skills
  -> expire: remove skills not invoked within safety window
```

## Friction Detection

`detect_friction()` queries the Memory model for records with `category=correction` and checks for tool-related tags (`tool`, `params`, `flags`, `cli`, `command`, `argument`). Matching records indicate repeated human corrections around tooling -- a signal that a skill could automate the pattern away.

Detection uses exact-match heuristics only (no LLM classification). This keeps it fast and deterministic inside the reflections pipeline.

### Data Flow

1. Human corrects the agent during a session (e.g., "no, use `--format table` not `--format json`")
2. Post-session Haiku extraction saves the correction as a Memory record with `category=correction` and relevant tags
3. `detect_friction()` picks up the memory and surfaces it as a friction pattern
4. The PM session (or a human) decides whether to invoke `/skillify` to generate a skill

## Generated Skill Frontmatter

Skills created through this lifecycle include metadata in their SKILL.md frontmatter:

| Field | Type | Description |
|-------|------|-------------|
| `generated` | bool | Always `true` for auto-generated skills |
| `generated_at` | string | ISO date when the skill was created |
| `expires_at` | string | ISO date when the skill expires (default: 30 days from creation) |
| `source_pattern` | string | The friction pattern that triggered generation |

Example:

```yaml
---
name: my-generated-skill
generated: true
generated_at: 2026-04-11
expires_at: 2026-05-11
source_pattern: "Repeated correction on gws calendar date format"
---
```

## Expiry and Renewal

Generated skills expire after 30 days by default. Before removal, the system checks a 48-hour safety window: if the skill was invoked within the last 48 hours, it is kept regardless of the expiry date.

**Renewal**: The `refresh` command extends `expires_at` by 30 days for any generated skill that was invoked in the last 30 days. This means actively used skills renew themselves indefinitely.

**Expiry**: The `expire` command finds generated skills past their `expires_at` date, checks the 48h safety window via analytics, and creates a removal PR for skills that should be retired.

## CLI

All commands are available via `python -m tools.skill_lifecycle`:

| Command | Description |
|---------|-------------|
| `detect-friction` | Scan Memory corrections for tool-related friction patterns |
| `detect-friction --json` | Output friction patterns as JSON |
| `expire` | Find and expire generated skills past their expiry date |
| `expire --dry-run` | Show what would be expired without acting |
| `refresh` | Extend `expires_at` for recently invoked generated skills |
| `report` | Print per-skill invocation analytics (count, last used) |

## Analytics Integration

The report command queries the `analytics.db` SQLite database for `skill.invocation` metrics. Each skill invocation is recorded with dimensions including the skill name, enabling per-skill usage tracking and trend analysis.

## Key Files

| File | Purpose |
|------|---------|
| `tools/skill_lifecycle.py` | CLI and core logic: friction detection, expiry, refresh, reporting |
| `.claude/skills/` | Skills directory scanned for `generated: true` frontmatter |
| `data/analytics.db` | SQLite database with skill invocation metrics |

## See Also

- [Reflections](reflections.md) -- Daily maintenance pipeline that can trigger friction detection
- [Skills Audit](do-skills-audit.md) -- Validates SKILL.md files against template standards
- [Unified Analytics](unified-analytics.md) -- Metrics collection powering the skill report
