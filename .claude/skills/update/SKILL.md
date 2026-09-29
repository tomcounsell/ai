---
name: update
description: "Use when deploying updates to this machine: pull, sync deps, verify, restart services. Triggered by 'update', 'deploy', 'pull and restart', or after git pull."
disable-model-invocation: true
---

# Update & Restart

Bring this machine to latest main: deps synced, environment verified, services restarted and healthy. The orchestrator does the work; you run it, read its output, and report every warning or error.

**Done when** the orchestrator has run on latest main and you have reported its result with every warning and error listed, plus anything left for a human (a skipped restart, a pending upgrade, a `needs_auth`).

## Run

Be on latest main first:

```bash
cd ~/src/ai && git checkout main && git fetch origin main && git merge --ff-only origin/main
```

Fetch and fast-forward a named ref, never a bare pull (#2650): `.git/FETCH_HEAD` is shared by every worktree, so a concurrent lane's fetch can retarget a bare pull's merge. If local changes block the checkout, commit them on a WIP branch; never bare `git stash` (the stash stack is repo-wide). The orchestrator handles its own stash during the pull.

```bash
cd ~/src/ai && .venv/bin/python scripts/update/run.py --full     # full update
cd ~/src/ai && .venv/bin/python scripts/update/run.py --verify   # check only, no changes
```

## Reading the result

- **`projects.json` validation gate (Step 4.6).** If `bridge/config_validation.py::validate_projects_config` fails (a bridge-contact identifier owned by more than one machine), the service restart is skipped and the bridge keeps serving the previously validated config. Report it as a failure that needs a config fix. See [Single-Machine Ownership](../../../docs/features/single-machine-ownership.md).
- **Warning channel (#2845).** The `--cron` summary (`up to date at <sha> (N warnings)` / `update failed at <sha>` plus bullets) is what the Telegram reply and any auto-spawned fix session detect. `gws auth` and `env-completeness` warn once per state transition; a `suppressed: ...` line names what is currently suppressed, and `python -m scripts.update.warn_state` or a full (non-`--quick`) `python -m tools.doctor` shows it on demand. Contract: [`/update` Warning Channel](../../../docs/features/update-warning-channel.md).
- **Critical upgrade pending.** When `pyproject.toml` pulls in a critical dep change (telethon, anthropic, claude-agent-sdk), the cron writes `data/upgrade-pending` and the run warns. A manual `--full` applies it; once the bridge is confirmed running on the new version, `rm ~/src/ai/data/upgrade-pending` (nothing clears it automatically).
- **markitdown backfill tip.** The first run that installs the `[knowledge]` extra appends a one-time tip: `run 'valor-ingest --scan ~/work-vault/' to backfill existing binary files into sidecars`. If asked why pre-existing vault PDFs or docs aren't indexed, point at that command; the watcher only picks up files modified after it starts.
- **Agent-judgment catchup** is the last step and best-effort: it runs only when bridge and worker are both up after a restart, is killed after 90s, and never fails the update. A `catchup: skipped` or `(swallowed)` line is not an update failure. See [Agent-Judgment Catchup](../../../docs/features/agent-judgment-catchup.md).

## Sub-files

| Sub-file | Load when |
|----------|-----------|
| `references/troubleshooting.md` | The run fails or the environment misbehaves afterward (venv, deps, calendar, machine identity, bridge/worker/reflection scheduler won't start) |
| `references/modules.md` | Debugging orchestrator internals, auto-bump, or calling a `scripts/update/` module directly |
