---
name: audit-hooks
description: "Audit repository agent hooks for failure behavior, validator enforcement, timeouts, logging, and deployment correctness."
---

# Audit Hooks

Identify the runtime and its real hook registration. For Valor's Claude hooks, inspect `.claude/hooks/manifest.toml` and generated settings; these remain Claude infrastructure even when Codex audits them. Do not invent equivalent Codex hooks or assume they execute in this session.
Read [hook checks](BEST_PRACTICES.md). Enumerate event, matcher, command, timeout, and advisory/validator role. Advisory logging/enrichment must not block a session; validators must preserve their refusal exit status rather than suppress it with `|| true`. Evaluate timeouts, heavy imports, interpreter paths, command existence, exception logging, and shell error behavior in context.
Inspect recent hook errors and distinguish absence of logs from successful execution. Compile or parse scripts without executing their live side effects. Report PASS/WARN/FAIL by hook with cited evidence. If fixes are requested, change the manifest or source and use the documented generator, never hand-edit generated hook blocks.
