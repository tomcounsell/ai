---
status: Complete
type: feature
appetite: Medium
owner: Valor
created: 2026-04-02
tracking: https://github.com/tomcounsell/ai/issues/630
last_comment_id:
---

# Audit Hooks Skill

## Problem

Claude Code hooks have grown organically to 8 hook commands across 5 event types, with inconsistent safety practices. A manual audit uncovered: silent error swallowing (`|| true` without logging), `set -e` in bash hooks (fatal in hooks), missing `log_hook_error()` calls, and no codified best practices. When new hooks are added, there is no automated check that they follow established patterns.

**Current behavior:**
- No single reference document for hook safety patterns
- The existing `scripts/update/hooks.py` only audits SKILL.md frontmatter for `uv run` and missing scripts — it does not audit the actual hook scripts in `.claude/hooks/` or `settings.json` for safety, error logging, or timeout correctness
- `docs/features/hooks-session-logging.md` is marked "Superseded" but still exists, creating stale documentation
- Hook errors in `logs/hooks.log` are parseable by reflections but there is no dedicated reflections step that audits hook health

**Desired outcome:**
A `/audit-hooks` skill that codifies best practices, runs a comprehensive audit of settings.json and hook scripts, produces a structured report, and integrates with daily reflections for ongoing monitoring.

## Prior Art

- **Issue #42**: Standardize all commands and skills — closed, set precedent for skill structure
- **PR #525**: Wire Claude Code hooks to subconscious memory system — initial hook wiring, established `|| true` pattern
- **PR #603**: Fix hook session ID resolution via bridge-level registry — hook infrastructure fix
- **PR #327**: Remove dead SDLC stage-tracking code from hook files — previous hook cleanup
- **PR #195**: SDLC user-level hooks — original hook infrastructure
- **Issue #627**: Memory recall hook 344ms import tax — performance issue in PostToolUse hook discovered during the same audit session that created this issue

## Data Flow

1. **Entry point**: User runs `/audit-hooks` in Claude Code
2. **Skill SKILL.md**: Claude reads instructions and executes audit checks
3. **settings.json parse**: Extract all hook commands, matchers, timeouts, `|| true` presence
4. **Hook script inspection**: For each command, read the target script and check safety patterns
5. **BEST_PRACTICES.md comparison**: Each finding is classified against the codified rules
6. **Output**: Structured report with PASS/WARN/FAIL per hook, grouped by severity

For the reflections integration:
1. **Entry point**: Daily reflections run at 6:00 AM
2. **step_hooks_audit**: Scan `logs/hooks.log` for recent errors, validate settings.json consistency
3. **Findings**: Added to `state.findings["ai:hooks_audit"]` for inclusion in daily report

## Architectural Impact

- **New dependencies**: None — all checks use stdlib (json, pathlib, re)
- **Interface changes**: None — this is a new skill with no external API
- **Coupling**: Low — the skill reads settings.json and hook scripts but does not modify them
- **Data ownership**: No change — skill is read-only
- **Reversibility**: Trivial — delete the skill directory and reflections step

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1 (scope alignment on best practices list)
- Review rounds: 1 (code review)

## Prerequisites

No prerequisites — this work uses only stdlib and reads existing project files.

## Solution

### Key Elements

- **SKILL.md**: Prompt-only audit skill (following `audit-models` pattern) that instructs Claude to run checks and produce a report
- **BEST_PRACTICES.md**: Codified hook safety patterns — the authoritative reference for this project's hook conventions
- **Reflections integration**: Lightweight `step_hooks_audit` in `scripts/reflections.py` that scans `logs/hooks.log` and validates settings.json consistency

### Flow

**User invokes `/audit-hooks`** → Claude reads SKILL.md → Parses settings.json → Inspects each hook script → Compares against BEST_PRACTICES.md → Produces structured report → User decides what to fix

**Daily reflections** → `step_hooks_audit` → Scan hooks.log for errors → Validate settings.json → Add findings to daily report

### Technical Approach

- The skill is prompt-only (`disable-model-invocation: true`, `allowed-tools: Read, Grep, Glob, Bash`) — Claude executes the audit checks, not a Python script
- BEST_PRACTICES.md is a standalone reference document read by both the skill and humans
- The reflections step is a lightweight Python function (no subprocess spawning) that:
  1. Calls `extract_structured_errors()` on `logs/hooks.log`
  2. Parses `.claude/settings.json` to check `|| true` correctness and file existence
  3. Reports findings via `state.findings`
- Feature doc `docs/features/hooks-best-practices.md` replaces the stale `hooks-session-logging.md`

## Failure Path Test Strategy

### Exception Handling Coverage
- The reflections `step_hooks_audit` must wrap all file I/O in try/except and log warnings rather than crashing the reflections run
- No exception handlers in existing code are modified by this work

### Empty/Invalid Input Handling
- The reflections step must handle: missing `logs/hooks.log` (no errors to report), malformed `settings.json` (log warning and skip), empty hook command strings
- The skill itself is prompt-only — Claude handles edge cases naturally

### Error State Rendering
- The skill produces a text report — error states are rendered as FAIL entries in the report table
- The reflections step adds findings to the daily report — no separate error rendering needed

## Test Impact

No existing tests affected — this is a greenfield feature adding a new skill, a new reflections step, and new documentation. No existing behavior or interfaces are modified.

## Rabbit Holes

- **Auto-fix mode**: Tempting to have the skill automatically patch hooks, but too risky for v1. Report-only is safer; the human decides what to fix.
- **Import weight profiling**: Measuring actual import times requires subprocess timing which is fragile and environment-dependent. Flag heavy imports heuristically (known slow modules) rather than measuring.
- **Cross-repo hook audit**: The skill will be hardlinked to `~/.claude/skills/`, but auditing hooks in other repos requires project-specific paths. Keep v1 focused on the ai repo's hooks; cross-repo is a future enhancement.
- **Hook execution tracing**: Runtime tracing of hook execution would be valuable but is a separate observability feature, not an audit.

## Risks

### Risk 1: Best practices become stale
**Impact:** New hook patterns emerge that aren't covered by BEST_PRACTICES.md, leading to false confidence.
**Mitigation:** The reflections step catches errors from `hooks.log` regardless of whether best practices are up to date. BEST_PRACTICES.md is a living document updated when new patterns are established.

### Risk 2: Reflections step adds latency
**Impact:** Daily reflections run takes longer.
**Mitigation:** The step only does file I/O (read hooks.log, parse settings.json) — no subprocess spawning, no API calls. Expected to add <1s.

## Race Conditions

No race conditions identified — the skill is read-only and the reflections step reads files that are only written by hook processes (which run during Claude Code sessions, not during the 6 AM reflections window).

## No-Gos (Out of Scope)

- Auto-fix mode (report-only in v1)
- Runtime import profiling (heuristic check only)
- Cross-repo hook auditing (ai repo only)
- Hook execution tracing / timing observability
- Modifying any existing hook scripts (this skill audits, it does not fix)

## Update System

The `/update` skill (`scripts/update/hardlinks.py`) already syncs `.claude/skills/` to `~/.claude/skills/` via hardlinks. The new `.claude/skills/audit-hooks/` directory will be picked up automatically — no changes to the update script or skill are needed.

No new dependencies or config files to propagate. No migration steps required.

## Agent Integration

No agent integration required — this is a Claude Code skill invoked directly by the human via `/audit-hooks`. It does not need MCP server exposure, bridge integration, or `.mcp.json` changes. The reflections integration runs as a step within the existing `scripts/reflections.py` process.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/hooks-best-practices.md` describing the audit skill and best practices (replaces stale `hooks-session-logging.md`)
- [ ] Update `docs/features/README.md` index: replace `hooks-session-logging.md` row with `hooks-best-practices.md`
- [ ] Delete `docs/features/hooks-session-logging.md` (marked Superseded, content is outdated)

### Inline Documentation
- [ ] Docstring on `step_hooks_audit` in reflections.py
- [ ] Comments in BEST_PRACTICES.md explaining the rationale for each rule

## Success Criteria

- [ ] `.claude/skills/audit-hooks/SKILL.md` exists with frontmatter and audit instructions
- [ ] `.claude/skills/audit-hooks/BEST_PRACTICES.md` codifies all hook safety patterns
- [ ] Running `/audit-hooks` produces a structured report covering: settings.json consistency, `|| true` correctness, error logging coverage, import weight flags, deployment readiness
- [ ] `docs/features/hooks-best-practices.md` replaces stale `hooks-session-logging.md`
- [ ] `docs/features/README.md` index updated with new doc entry
- [ ] Reflections `step_hooks_audit` scans `logs/hooks.log` and validates settings.json consistency
- [ ] `/update` propagates the new skill to all machines via hardlink sync (no changes needed — automatic)
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (skill)**
  - Name: skill-builder
  - Role: Create the audit-hooks skill (SKILL.md + BEST_PRACTICES.md)
  - Agent Type: builder
  - Resume: true

- **Builder (reflections)**
  - Name: reflections-builder
  - Role: Add step_hooks_audit to reflections.py
  - Agent Type: builder
  - Resume: true

- **Builder (docs)**
  - Name: docs-builder
  - Role: Create feature doc, update README index, delete stale doc
  - Agent Type: documentarian
  - Resume: true

- **Validator (all)**
  - Name: final-validator
  - Role: Verify all success criteria
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Create BEST_PRACTICES.md
- **Task ID**: build-best-practices
- **Depends On**: none
- **Assigned To**: skill-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `.claude/skills/audit-hooks/BEST_PRACTICES.md` codifying these rules:
  - Stop hooks MUST have `|| true` (failing stop hook blocks session exit)
  - Advisory hooks MUST have `|| true` (logging, memory, calendar, SDLC tracking hooks must never block)
  - Validator hooks MUST NOT have `|| true` (they exist to block; `|| true` defeats the purpose)
  - All `|| true` hooks MUST call `log_hook_error()` on failure (silent failure is invisible failure)
  - Bash hooks MUST use `set +e` (not `set -e` which causes hooks to exit on any subcommand failure)
  - Bash hooks MUST NOT use bare `exec` (prevents error recovery and logging)
  - Shell hooks MUST prefer venv binaries (`$CLAUDE_PROJECT_DIR/.venv/bin/` before system PATH)
  - Python hooks MUST minimize imports (lazy-import heavy modules; keep baseline <50ms)
  - Hook timeouts MUST match expected workload (5s simple, 10-15s API calls)
  - Each rule should include: rationale, good example, bad example

### 2. Create SKILL.md
- **Task ID**: build-skill
- **Depends On**: build-best-practices
- **Assigned To**: skill-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `.claude/skills/audit-hooks/SKILL.md` with frontmatter (`disable-model-invocation: true`, `allowed-tools: Read, Grep, Glob, Bash`)
- Follow the `audit-models` skill pattern: structured checks, severity levels, report format
- The skill should instruct Claude to:
  1. Read `.claude/settings.json` and extract all hook entries
  2. For each hook: check `|| true` correctness (advisory vs validator), timeout appropriateness, matcher specificity
  3. For each Python hook script: check for `try/except` + `log_hook_error()` at `__main__`, no bare `sys.exit(1)` in advisory hooks, flag known-heavy imports
  4. For each bash hook script: check `set +e`, no bare `exec`, error logging to hooks.log, venv-first binary resolution
  5. Check deployment readiness: all referenced scripts exist and are importable
  6. Produce structured report with PASS/WARN/FAIL per hook

### 3. Add reflections step
- **Task ID**: build-reflections
- **Depends On**: none
- **Assigned To**: reflections-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `step_hooks_audit` to `scripts/reflections.py`:
  - Register in the steps list (after `skills_audit`, before `redis_ttl_cleanup`)
  - Scan `logs/hooks.log` using `extract_structured_errors()` for errors in the last 24h
  - Parse `.claude/settings.json`: verify every hook command's target script exists, check `|| true` on Stop/SubagentStop hooks
  - Add findings to `state.findings["ai:hooks_audit"]`
  - Log summary: "Hooks audit: N errors in last 24h, M settings issues"
- Add unit test `tests/unit/test_hooks_audit_reflections.py` verifying the step handles missing files gracefully

### 4. Create feature documentation
- **Task ID**: build-docs
- **Depends On**: build-best-practices, build-skill
- **Assigned To**: docs-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/hooks-best-practices.md` describing:
  - The audit skill and how to run it
  - Summary of best practices (with link to full BEST_PRACTICES.md)
  - How the reflections integration works
  - How hooks are deployed via `/update`
- Update `docs/features/README.md`: replace `hooks-session-logging.md` row with `hooks-best-practices.md`
- Delete `docs/features/hooks-session-logging.md`

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-skill, build-reflections, build-docs
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `.claude/skills/audit-hooks/SKILL.md` and `BEST_PRACTICES.md` exist with correct frontmatter
- Verify `docs/features/hooks-best-practices.md` exists and `hooks-session-logging.md` is deleted
- Verify `docs/features/README.md` has updated entry
- Verify `step_hooks_audit` is registered in reflections step list
- Run `pytest tests/unit/test_hooks_audit_reflections.py -x`
- Run all success criteria checks

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Skill exists | `test -f .claude/skills/audit-hooks/SKILL.md` | exit code 0 |
| Best practices exists | `test -f .claude/skills/audit-hooks/BEST_PRACTICES.md` | exit code 0 |
| Feature doc exists | `test -f docs/features/hooks-best-practices.md` | exit code 0 |
| Stale doc removed | `test ! -f docs/features/hooks-session-logging.md` | exit code 0 |
| README updated | `grep -c "hooks-best-practices" docs/features/README.md` | output > 0 |
| Reflections step registered | `grep -c "hooks_audit" scripts/reflections.py` | output > 0 |
| Unit tests pass | `pytest tests/unit/test_hooks_audit_reflections.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .claude/skills/audit-hooks/ scripts/reflections.py` | exit code 0 |
| Format clean | `python -m ruff format --check scripts/reflections.py` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

1. ~~Should the best practices document include a version number or changelog, or is git history sufficient for tracking changes to the rules?~~ **Resolved:** Git history is sufficient. No version number needed.
2. ~~The issue mentions an import weight check flagging imports >100ms — should we use a fixed list of known-heavy modules (e.g., anthropic, openai, pandas) or attempt actual timing?~~ **Resolved:** Use heuristic (fixed list of known-heavy modules). Actual timing is fragile and environment-dependent.
