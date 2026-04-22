# Hook Standards

Rules the `/claude-standards` skill uses to audit Claude Code hooks.

**Asset location:** `.claude/settings.json` (hook registrations) and `.claude/hooks/**` (hook scripts).

**Canonical reference:** This repo already maintains a detailed hook best-practices document at `.claude/skills/audit-hooks/BEST_PRACTICES.md`, used by the `/audit-hooks` skill. To avoid drift, `/claude-standards` **defers** to that file for hook rules.

---

## Rules

All hook rules live in [`.claude/skills/audit-hooks/BEST_PRACTICES.md`](../audit-hooks/BEST_PRACTICES.md). When `/claude-standards` audits hooks, it should:

1. Read that file for the current rule set.
2. Apply the same Validator vs Advisory classification described there.
3. Report using the same severity model (FAIL/WARN/PASS).

If the hooks domain needs standards that are scoped to `/claude-standards` but not `/audit-hooks` (unlikely), add them below.

_No additional rules defined here._

---

## Auto-fix eligible

- (none) — hook edits affect runtime. Defer all remediation to human review via `/audit-hooks`.
