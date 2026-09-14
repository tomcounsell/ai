---
name: setup
description: "Configure a new machine to run the Valor bridge and workers, using its pinned environment and current setup procedures."
---

# Setup

Read the current setup references by phase: [environment](references/environment.md), [authentication](references/auth.md), [project configuration](references/projects-config.md), [optional surfaces](references/optional-surfaces.md), [mesh networking](references/mesh-network.md), and [verification](references/verification.md). Load only the phases needed for the machine. These describe the Valor application, including its genuine Claude runtime dependencies; they do not configure Codex authentication.
Inspect existing state before installing. Respect `.python-version`, external secret storage, and explicit `working_directory` plus exact machine ownership in projects.json. Keep optional browser, mesh, or model services opt-in rather than silently expanding setup.
Complete environment and service preparation before requesting any authentication interaction that only the user can perform. Never expose tokens or substitute personal auth for the configured service account. Use current installer scripts for worker/reflection services and the documented Telegram login step.
Verify config validation, service state, and actual bridge connection after starting. Report completed phases, remaining human auth actions, and concrete warnings. Do not claim a new machine is ready because installers merely exited successfully.
