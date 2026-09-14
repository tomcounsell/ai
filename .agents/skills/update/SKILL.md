---
name: update
description: "Update the designated Valor service checkout, synchronize dependencies, and verify restarted services on this machine."
---

# Update

Resolve the designated service checkout; an implementation worktree is not the deployment target. Inspect its branch, dirty state, and current release. Do not automatically stash or overwrite other work. Use an explicit fetch plus named-ref fast-forward rather than a bare pull relying on shared FETCH_HEAD.
Inspect `scripts/update/run.py --help` and the current orchestrator before running. For read-only verification use the documented `--verify` mode. For an authorized full update, run the pinned environment's `scripts/update/run.py --full` from the service checkout and follow its result to completion. The orchestrator may update dependencies, sync Claude infrastructure, validate machine ownership, restart services, and enqueue catchup work; preserve these real application dependencies rather than renaming them Codex.
Honor project-config validation gates and dependency pins. Never bypass a failed gate or hand-edit generated hook registrations. Read [troubleshooting](references/troubleshooting.md) when a step fails and [modules](references/modules.md) when diagnosing the orchestrator itself.
Check bridge and worker health and actual connection evidence after completion. Report all warnings, including suppressed-warning diagnostics, and partial failures. Skill conversion alone does not require an update or service restart. Codex skill installation is maintained separately by `scripts/codex_skills.py`.
