---
name: deepen
description: "Audit a module's code-level observability: structured logging, metrics, tracing. Triggered by 'deepen this module', 'add logging to', 'instrument this', 'make this debuggable'."
allowed-tools: Read, Grep, Glob, Bash
---

# Skill: /deepen

## Purpose
Add structured logging, metrics, and tracing to a specified module to make it debuggable and understandable in production.

## When to Use
- A new module has shipped but has no logging — debugging requires guesswork
- A bug was hard to reproduce because there was no trace of what happened
- Code review flags a module as "too shallow" — no error context, no timing, no state logging
- Before adding a complex feature to a module that currently has no instrumentation
- When the user says "add logging to X", "make X debuggable", or "instrument X"

## Steps

1. **Resolve the target module.** If invoked with no argument, scan for modules with zero `logging.getLogger` calls and list the top 5 by line count. Ask the user to confirm which to instrument.

2. **Audit the module against the 9-symptom checklist.** The checklist below is written with Python's `logging` module as the example — map each symptom to the project language's structured-logging equivalent (e.g. `tracing`/`log` in Rust, `pino`/`winston` in Node). Read the file(s) and check each symptom:
   - [ ] No `logging.getLogger(__name__)` at module level
   - [ ] Exception handlers with bare `pass` or only `raise` (no log)
   - [ ] Functions longer than 40 lines with no log statements
   - [ ] External I/O (HTTP, DB, file, subprocess) with no timing or error logging
   - [ ] State transitions with no record (state changes silently)
   - [ ] Loop bodies that process collections with no count/summary log
   - [ ] Return values from external calls not validated or logged on failure
   - [ ] No `__repr__` on key data objects (hard to log meaningfully)
   - [ ] Assertions with no message (assert x, but no context on failure)

3. **Rank by impact.** Score each found symptom:
   - Severity: critical (will hide production bugs) = 3, moderate = 2, cosmetic = 1
   - Frequency: how often this code path runs (estimate from call sites)
   - Score = severity × frequency

4. **Produce a ranked output.** List symptoms from highest to lowest score. For each:
   ```
   [score] symptom description
   Location: file.py:line
   Fix: add logging.error("context: %s", value, exc_info=True) in the except block
   ```

5. **DO NOT edit code.** This skill is read-only — it audits and recommends. To apply fixes, hand off to /do-plan with the ranked list as the problem statement.

6. **Suggest next steps.** If the module needs substantial work, say: "Run /do-plan with this ranked list to create implementation tasks."

## Output
A ranked list of instrumentation gaps with fix suggestions. No code changes.

## Anti-Patterns
- Do not edit any files — /deepen is a read-only audit skill.
- Use /observability when the goal is dashboards and alerts — /deepen is for code-level logging and tracing.
- Do not add logging to every line — over-logging is noise. Focus on decision points, I/O boundaries, and error paths.
- Do not use print()/console.log — always recommend the language's structured logger (e.g. `logging.getLogger(__name__)` in Python) and structured log records.
- Do not conflate metrics (counters, gauges) with logging — mention both but distinguish them.
