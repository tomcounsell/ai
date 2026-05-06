---
name: audit-tools
description: "Audit tools/ directory for structure compliance, test coverage, CLI quality, and documentation completeness. Use when checking tool health, validating tools, reviewing tool quality, or after adding or modifying a tool. Also use when someone says 'check the tools', 'are our tools documented', or 'which tools need work'."
allowed-tools: Read, Grep, Glob, Bash
disable-model-invocation: true
argument-hint: "[tool-name] [--fix]"
---

# Tools Audit

Validates every tool in `tools/` against STANDARD.md requirements and the interface documentation expectations defined here. Surfaces tools that are missing files, have incomplete docs, untested capabilities, or broken CLI help.

## What this skill does

1. Discovers all tool directories in `tools/` (skipping `_template`, `__pycache__`)
2. Runs 10 checks against each tool, grouped into Structure, Interface, Tests, and CLI
3. Produces a severity-grouped findings report per tool
4. Reports overall health summary with pass/warn/fail counts

## When to load sub-files

- For the full checklist with verification commands → read [CHECKS.md](CHECKS.md)

## Quick start

If `$ARGUMENTS` names a specific tool (e.g., `/audit-tools sms_reader`), audit only that tool. Otherwise audit all tools.

### Step 1: Discover tools

```bash
ls -d tools/*/ | grep -v __pycache__ | grep -v _template | sort
```

### Step 2: Run checks

Read [CHECKS.md](CHECKS.md) for the full check definitions. For each tool, run all 10 checks and record PASS/WARN/FAIL per check.

### Step 3: Report

Present findings using this format:

```
## Tools Audit Report

### tool-name (6/10 PASS, 3 WARN, 1 FAIL)

#### FAIL
- [test-coverage] 3 capabilities in manifest, only 1 has test coverage (search, analyze untested)

#### WARN
- [cli-help] --help output is 2 lines — should describe arguments, options, and examples
- [error-docs] README has no error handling section
- [output-types] Functions return untyped dicts — return types not documented

#### PASS
- [manifest-exists] manifest.json present and valid
- [readme-exists] README.md present with required sections
- [tests-exist] tests/ directory with test_sms_reader.py
- [inputs-documented] All public functions have typed parameters
- [examples] README has working code examples
- [cli-registered] Registered in pyproject.toml as valor-sms-reader

---

### Summary

| Status | Count |
|--------|-------|
| Fully compliant | 12 |
| Has warnings | 5 |
| Has failures | 3 |

Total: 20 tools, 164/200 checks passing
```

### Step 4: Disposition

This audit is **report only** — findings are presented for human review. If `--fix` was passed, create a GitHub issue with the full report for tracking.

## After the audit

This skill produces findings only. Next steps are decided by the human:
- Fix individual tools based on findings
- Create GitHub issues for tools that need significant work
- Use `/new-valor-skill` to scaffold missing structure for incomplete tools
- Delete tools that are abandoned (e.g., empty placeholders)

## Version history

- v1.0.0 (2026-03-19): Initial — replaces audit-next-tool with structured checks
