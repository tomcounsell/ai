---
name: do-deploy
description: "Verify and deploy merged Valor changes through the established local and fleet update process."
---

# Do Deploy

Resolve the requested PR or release and verify it is merged before deploying. Read `scripts/remote-update.sh`, `scripts/update/run.py`, service help, and relevant deployment docs for current fleet behavior; do not invent a remote push mechanism.
Operate on the designated service checkout, not an implementation/review worktree. Inspect its branch and dirty state. Fetch the named upstream ref and fast-forward only when safe; shared FETCH_HEAD is not a reliable merge target with concurrent worktrees. Do not automatically stash another task's changes or force-update remote machines.
Confirm the merged commit is included locally. Check `scripts/valor-service.sh status`, `worker-status`, recent bridge errors, and the Telegram connection evidence. Restart through the service manager when deployment requires it, then verify again. Remote machines normally pick up merged main through their update schedule; inspect declared heartbeat/status data and distinguish pending from healthy.
Report release identity, local health, remote verification or pending status, and rollback path. Missing remote evidence is not a successful fleet deployment. Use $update for a requested full machine update.
