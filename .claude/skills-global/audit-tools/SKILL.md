---
name: audit-tools
description: "Audit tools/ for structure, tests, CLI quality, and docs. Use when checking tool health or after adding a tool: 'check the tools'."
allowed-tools: Read, Grep, Glob, Bash
disable-model-invocation: true
argument-hint: "[tool-name] [--fix]"
model: sonnet
effort: medium
---

# Tools Audit

Find every tool under `tools/` that is missing files, under-documented, untested, or has a
broken CLI. A human decides the fixes.

Done when: each audited tool has PASS/WARN/FAIL on all 10 checks in [CHECKS.md](CHECKS.md),
every non-PASS verdict states expected versus found, and a fleet summary closes the report. A
tool name in `$ARGUMENTS` limits the run to that tool. `--fix` means also filing a GitHub
issue with the report; it never edits tools.

## Repo context

If `.claude/skill-context/audit-tools.md` exists, read it and follow it. It declares the CLI
naming convention, the test runner, and any scaffolding command. Without it:
- a tool counts as registered if it has any `pyproject.toml [project.scripts]` entry;
- tests run with `python -m pytest`;
- missing structure is scaffolded by hand.

## Report

```
## Tools Audit Report
### sms_reader (6/10 PASS, 3 WARN, 1 FAIL)
#### FAIL
- [test-coverage] 3 capabilities in manifest, 1 tested (search, analyze untested)
#### WARN
- [cli-quality] --help is 2 lines: no arguments, options, or example
### Summary
Fully compliant N · Has warnings N · Has failures N — T tools, P/Q checks passing
```
