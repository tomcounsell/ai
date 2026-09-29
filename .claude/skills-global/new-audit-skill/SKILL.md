---
name: new-audit-skill
description: "Create an audit skill. Use on 'new audit skill', 'create an audit', 'add an audit', 'I want to check X for problems'."
allowed-tools: Read, Write, Edit, Glob, Grep, Bash
argument-hint: "<subject-to-audit>"
---

# New Audit Skill

Turn "I want to check X for problems" into an audit skill whose first real run produces
accurate, actionable findings. A template that only looks right does not count.

Done when:
- The skill exists: `name` matches its directory, and the description includes trigger synonyms
  ("check", "validate", "review", "scan", "lint", not only "audit").
- Every check has a name, a severity, and a pass/fail test someone can verify.
- The disposition (report only, auto-fix trivial, or apply behind a review gate) is stated.
- One run on real data found the problems the user named, with no false positives you can
  see. Revise and re-run until that holds.

This is a skill, so the generic rules apply: body shape, description, and model/effort
placement are in [../new-skill/SKILL.md](../new-skill/SKILL.md). The skeleton is
[AUDIT_TEMPLATE.md](AUDIT_TEMPLATE.md), and design choices with their trade-offs are in
[BEST_PRACTICES.md](BEST_PRACTICES.md).

## What to settle with the user

Take what the conversation already answers and ask only for the rest:
- the subject and where its items live;
- the problems they have seen, which become checks;
- the disposition;
- when someone would run it.

## Facts that shape the design

- **Script what code can verify.** Anything that is a regex, AST walk, or structural check goes
  in an `audit.py` script inside the new skill (flags `--json`, `--fix` if there is a fix mode, and a target). The
  prompt keeps only checks that need judgment. A hybrid is common.
- **Conservative thresholds.** A false positive costs more than a miss. When unsure, lower
  the severity.
- **Severity uses three levels:** FAIL/CRITICAL (broken, fix before shipping), WARN (drifted),
  INFO (optional). Every finding reads `[check-name] item: expected X, found Y`.
- **Negative claims** ("nothing handles X", "never called") need a project-wide search.
  Tell the audit to re-verify them before it reports.
- **Auto-fix** only when the fix is unambiguous and confined to the audited files. Report
  every change. Anything that becomes a permanent record, such as a commit or an issue, goes
  behind a review gate.
- **Placement:** a script-backed audit needs no `model:`. A read-only report whose findings a
  human reviews fits `sonnet` at `medium`. An audit whose output gets applied without a human
  needs `opus`.
- **Portability:** name it `audit-{subject}` or `do-{subject}-audit`. A cross-repo audit keeps
  its body generic and reads repo specifics (paths, conventions, exemptions) from a
  skill-context file.
