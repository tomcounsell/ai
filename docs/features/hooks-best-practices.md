# Hooks Best Practices

Audit skill and codified safety rules for Claude Code hooks, with daily reflections monitoring.

## Overview

Claude Code hooks run on every tool call, prompt submission, and session stop. A misconfigured hook can block work, silently swallow errors, or degrade performance. The `/audit-hooks` skill and daily `step_hooks_audit` reflections step keep hooks healthy.

## The `/audit-hooks` Skill

Run from any Claude Code session:

```
/audit-hooks
```

The skill reads `.claude/settings.json`, inspects every referenced hook script, and produces a PASS/WARN/FAIL report against 9 codified rules. It does not modify files — report only.

Skill definition: `.claude/skills/audit-hooks/SKILL.md`
Full rule specs: `.claude/skills/audit-hooks/BEST_PRACTICES.md`

## The 9 Rules

| # | Rule | Severity | Summary |
|---|------|----------|---------|
| 1 | Stop hooks must `\|\| true` | FAIL | Failing stop hooks block session exit |
| 2 | Advisory hooks must `\|\| true` | FAIL | Logging/memory hooks must never block the agent |
| 3 | Validators must NOT `\|\| true` | FAIL | `\|\| true` defeats the validation purpose |
| 4 | Log errors on `\|\| true` hooks | WARN | Silent failure is invisible failure |
| 5 | Bash: `set +e` not `set -e` | FAIL | `set -e` kills hooks on any subcommand failure |
| 6 | Bash: no bare `exec` | WARN | `exec` prevents error recovery |
| 7 | Shell: prefer venv binaries | WARN | System Python may have wrong deps |
| 8 | Python: minimize imports | WARN | Heavy top-level imports add latency to every hook run |
| 9 | Timeouts must match workload | WARN | No timeout = infinite hang risk |

## Daily Monitoring

The reflections system includes `step_hooks_audit` (after `skills_audit`, before `redis_ttl_cleanup`). Each day it:

1. Scans `logs/hooks.log` for errors in the last 24 hours
2. Validates that every hook command's target script exists
3. Checks `|| true` correctness on Stop/SubagentStop/advisory hooks
4. Reports findings to `state.findings["ai:hooks_audit"]`

## Deployment

The `/update` skill's hardlink sync (`scripts/update/hardlinks.py`) picks up `.claude/skills/audit-hooks/` automatically. No update script changes needed.

## Related

- [Reflections](reflections.md) — daily maintenance pipeline
- [Claude Code Memory](claude-code-memory.md) — hook-based memory integration
- [SDLC Enforcement](sdlc-enforcement.md) — user-level hooks for pipeline enforcement
