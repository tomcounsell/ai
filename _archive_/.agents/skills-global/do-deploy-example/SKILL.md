---
name: do-deploy-example
description: "Develop a project-specific deployment runbook or adapt a deployment example to a named hosting environment."
---

# Do Deploy Example

This is a deployment template capability, not a default production command. Discover the actual platform, release source, migration needs, health checks, rollback mechanism, and project authorization from repo configuration and the request.
Write or update the project deployment runbook with preflight, immutable release/commit identity, deployment commands, health verification, and rollback. Replace example hostnames and commands with verified values before execution; never run a generic example against a guessed production target.
When the user requests deployment, use the environment's documented procedure and verify the resulting release and user-visible health. For Sites projects use the installed hosting skill. For Valor fleet deployment use $do-deploy. Report the actual deployed version, health evidence, and any failed or unavailable verification.
