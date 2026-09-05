---
name: do-integration-audit
description: "Trace whether a feature is reachable and correctly wired across implementation, entrypoints, configuration, tests, and docs."
---

# Do Integration Audit

Search for the feature and synonyms in source, imports, routes, CLI commands, events, config, migrations, tests, and docs. Build an integration map of implementation, entrypoints, tests, documentation, configuration, and migrations.
Check orphan code, dead entrypoints, partial wiring, missing integration coverage, undocumented entrypoints, configuration gaps, stale references, inconsistent interfaces, non-reusable interfaces, internal naming drift, external naming drift, and missing error boundaries.
Before reporting, re-read every cited location. A claim that nothing handles a case requires project-wide search, including watchdogs, sibling services, hooks, and migration paths. Runtime claims require tracing actual bodies, guards, early returns, idempotency, flags, and exception paths; function-call proximity is not proof of execution. Mark unproven behavior as suspected.
Group verified findings by consequence and offer specific fixes. For a severity filter, omit lower-severity results but preserve incomplete-check disclosures. Audit-only requests produce findings; audit-and-fix requests continue through relevant repairs and verification.
