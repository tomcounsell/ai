---
name: cowork
description: "Create or maintain recurring agent workflows, Codex automations, or an explicitly requested Claude cloud routine."
---

# Cowork

Resolve the actual desired runtime from the request and existing configuration. For Codex reminders, monitors, and recurring work, discover and use the native automation tool. Prefer a current-task heartbeat for follow-ups; use a standalone scheduled task only when requested. Inspect existing automations before creating one, and preserve notification preferences. Do not invent shell cron jobs as substitutes for supported Codex scheduling.
Keep the prompt tied to a maintained skill or committed recipe. Specify scope, cadence/timezone, success evidence, meaningful-change notifications, failure behavior, and deduplication. A quiet successful run and a failed run are different: inspect run history rather than inferring health from no output.
For an explicitly requested Anthropic Routine, preserve that product choice, check its current official documentation and account capabilities, and prepare or update a versioned routine specification. Cloud execution cannot assume local files, Redis, browser sessions, or local secrets. Do not claim Codex automation is an Anthropic cloud routine or promise cloud execution from a local heartbeat.
When migrating a live schedule, verify a successful replacement run before disabling the original, then prevent duplicate side effects. Record live IDs and actual verification status.
