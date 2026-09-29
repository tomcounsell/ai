---
name: do-integration-audit
description: "Audit a named feature's integration: orphan code, dead wiring, missing tests, config gaps. Use when asked to audit a feature's wiring."
allowed-tools: Read, Grep, Glob, Bash, Agent
argument-hint: "<feature-topic> [--path <dir>] [--severity critical|warning|info]"
disable-model-invocation: true
model: opus
effort: medium
---

# Feature Integration Audit

Find the gaps between "the code exists" and "the feature works end to end" for one named
feature. A feature can be present but unreachable, tested only with mocks, or documented
without its entry points. Map every integration surface (implementation, entry points, tests,
docs, config, migrations), run the 12 checks, and report. The value is a report where every
finding survives verification. Never modify source files; stop for human review.

Input: `$ARGUMENTS` is the feature topic, plus an optional `--path` scope (default: the
project root) and an optional `--severity` floor (default: all). The topic is a semantic
label, not a path, so search synonyms and trace imports, routes, CLI commands, event
handlers, config keys, tests, and docs. If discovery finds fewer than 3 files, confirm the
topic with the user before going on.

Done when:
- The report below lists the discovered surfaces and the integration map.
- Every finding names its check, cites file:line evidence, and states the runtime consequence.
- Every finding has passed the verification pass.

## Verification pass (before reporting)

Draft findings go stale: line numbers drift, and hypotheses form before every file has been
read. Before writing the report, go back and prove each claim again:
- **Re-read the cited file:line** with Read and confirm it still says what you claim.
- **A negative claim** ("nothing handles X", "no test covers", "nothing imports") needs a
  project-wide search. Sibling systems such as watchdogs, hooks, other processes, and
  migrations often handle it from outside the feature's directory.
- **A dynamic claim** ("runs twice", "never called", "drops data") needs a trace through the
  function bodies. A call does not prove execution: guards, early returns, idempotency checks,
  and flags intervene. If you can't prove it from the code, mark it "suspected" or drop it.
- **Contradictions inside the report** count as a failed verification; keep the claim the code
  supports.
- **Try to falsify each claim.** For every claim, run the search that would prove you wrong.

Drop what fails. Where a finding survives in revised form, note the revision inline. For each
surviving CRITICAL finding, spawn a fresh subagent with only the claim and its file:line, and
ask it to disprove the claim, returning PASS, FAIL, or REVISE. Report the finding only if it
holds.

## Checks

| Check | Severity | Finding |
|---|---|---|
| `orphan-code` | CRITICAL | Implementation that no entry point or application code imports |
| `dead-entrypoint` | CRITICAL | A route, CLI command, handler, or cron job defined but not registered in the app's wiring |
| `partial-wiring` | WARNING | `TODO`, `FIXME`, `NotImplementedError`, stub bodies, or commented-out blocks in feature code (not tests) |
| `missing-integration-test` | WARNING | No test drives the real path from entry point to side effect. Mocked unit tests don't count |
| `undocumented-entry` | WARNING | An entry point with no README, doc page, help text, or docstring |
| `config-gap` | WARNING | A config read that has no default or "required" note, or is missing from example config and setup docs |
| `stale-reference` | WARNING | Callers outside the feature using names, paths, or APIs that no longer exist in it |
| `inconsistent-interface` | WARNING | The same operation named, argued, or returned differently across API, CLI, SDK, and internal surfaces |
| `non-reusable-interface` | WARNING | Core logic coupled to one caller (for example, it takes a request object), with no shared service layer |
| `internal-naming-drift` | WARNING | Synonyms for one concept inside the feature (`subscription` / `plan` / `membership`) |
| `external-naming-drift` | WARNING | Other systems naming the feature inconsistently: foreign keys, config prefixes, aliases, log messages, URL paths |
| `missing-error-boundary` | INFO | An entry point that lets feature exceptions crash its caller, where no framework-level handler exists |

## Report

```
## Integration Audit Report: {feature-topic}
### Discovery
N files: implementation N · entry points N (routes/CLI/events) · tests N (unit N, integration N) · docs N · config keys N · migrations N
### Integration Map
| Surface | File | Status (connected / dead / orphaned) |
### Findings
#### CRITICAL
- [orphan-code] auth/backends.py:14: TokenBackend never used; AUTH_BACKEND defaults to ModelBackend, so the code ships but never runs.
#### WARNING
#### INFO
### Summary
PASS: N  WARN: N  FAIL: N
```
