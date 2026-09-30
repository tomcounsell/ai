---
allowed-tools: Bash(cd ~/src/ai && git checkout main && git fetch origin main && git merge --ff-only origin/main:*), Bash(cd ~/src/ai && .venv/bin/python scripts/update/run.py --full:*)
description: Bring this machine to latest main — sync deps, verify environment, restart services. Runs deterministically via inline bash execution, not model discretion.
---

# Update & Restart

The commands below already ran as part of expanding this command — you are reading their actual output, not deciding whether to run them.

## Fast-forward main

!`cd ~/src/ai && git checkout main && git fetch origin main && git merge --ff-only origin/main`

## Update orchestrator (`scripts/update/run.py --full`)

!`cd ~/src/ai && .venv/bin/python scripts/update/run.py --full`

---

Report the results above to the user: summarize what changed, and list every warning or error clearly. Do not re-run any of the above — it already happened. If something needs follow-up (e.g. `data/upgrade-pending` still present, a failed service restart), investigate using `.claude/skills/update/references/troubleshooting.md` and `.claude/skills/update/references/modules.md`.
