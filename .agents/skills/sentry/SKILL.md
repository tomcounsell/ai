---
name: sentry
description: "Inspect and classify Valor Sentry issues, or apply specifically requested triage changes with verified scope."
---

# Sentry

Use the existing `reflections.sentry_triage.run_sentry_triage` procedure or available Sentry tools. Inspect the implementation before invoking it so dry-run and notification behavior are understood. Set `SENTRY_TRIAGE_APPLY=0` explicitly for inspection; do not inherit a live setting from the shell.
Use the existing classes: A noise/test errors, B transient failures, C actionable bugs (the configured event threshold), D ambiguous review, E stale issues (the configured inactivity window). The source defaults are ten events and thirty days; verify current configuration rather than treating these as universal Sentry rules.
Show a dry-run summary and actionable/ambiguous items. Apply when the user has requested the concrete Sentry/GitHub mutations, preserving any existing authorization. A skill name alone does not authorize Telegram notifications; use a supported non-notifying path if needed, or complete the report and identify the coupled side effect before proceeding.
Read back changed states and created issues. Report actual counts and distinguish dry-run, disabled, failed, and applied results.
