---
tracking: https://github.com/tomcounsell/ai/issues/630
status: Shipped
---

# Hooks Best Practices & Audit

Claude Code hooks fire at lifecycle events (UserPromptSubmit, PreToolUse, PostToolUse, Stop, SubagentStop). This project has hooks for validation, memory, calendar logging, and SDLC tracking. The `/audit-hooks` skill and accompanying best practices document ensure all hooks follow consistent safety patterns.

## Quick Reference

- **Run audit:** `/audit-hooks` in Claude Code
- **Best practices:** `.claude/skills/audit-hooks/BEST_PRACTICES.md`
- **Hook settings:** `.claude/settings.json`
- **Error log:** `logs/hooks.log`
- **Hook scripts:** `.claude/hooks/` (Python) and `scripts/` (bash)

## Key Rules

1. **Stop/SubagentStop hooks** must have `|| true` (prevents session hangs)
2. **Advisory hooks** must have `|| true` (logging, memory, calendar never block)
3. **Validator hooks** must NOT have `|| true` (they exist to enforce rules)
4. **All `|| true` hooks** must call `log_hook_error()` on failure (no silent swallowing)
5. **Bash hooks** must use `set +e` and prefer venv binaries
6. **Python hooks** must minimize top-level imports (keep baseline <50ms)
7. **Timeouts** must match workload (5s simple, 10s git, 15s API)

See `.claude/skills/audit-hooks/BEST_PRACTICES.md` for full rules with examples.

## Reflections Integration

The daily reflections run includes a `hooks_audit` step that:
- Scans `logs/hooks.log` for errors in the last 24 hours
- Validates settings.json hook configuration consistency
- Reports findings in the daily maintenance summary

## Hook Classification

| Type | Purpose | `|| true` | Example |
|------|---------|-----------|---------|
| Validator | Block invalid operations | No | `validate_commit_message.py` |
| Advisory | Observe and enrich | Yes | `post_tool_use.py`, `stop.py` |
| Stop | Session cleanup | Yes | `calendar_hook.sh`, `stop.py` |

## Adding New Hooks

When adding a new hook:
1. Classify it (validator or advisory)
2. Follow the patterns in `BEST_PRACTICES.md`
3. Add `log_hook_error()` error handling for advisory hooks
4. Run `/audit-hooks` to verify compliance

## Related

- [Claude Code hooks documentation](https://docs.anthropic.com/en/docs/claude-code/hooks)
- [Session Transcripts](session-transcripts.md) — how hook-captured data is used
- [Subconscious Memory](subconscious-memory.md) — memory hooks (PostToolUse, Stop)
- [Google Calendar Integration](google-calendar-integration.md) — calendar hooks
