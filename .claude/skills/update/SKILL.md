---
name: update
description: "Use when deploying updates to this machine: pull, sync deps, verify, restart services. Triggered by 'update', 'deploy', 'pull and restart', or after git pull."
disable-model-invocation: true
---

# Update & Restart

Pull the latest changes from the remote repository, sync dependencies, and restart the bridge service.

## Instructions

**PREREQUISITE: Must be on latest main branch before running.**

```bash
cd ~/src/ai && git checkout main && git pull
```

If there are local changes, stash them first: `git stash`. The update orchestrator also handles this, but being on main is required.

Run the full update orchestrator and report the results:

```bash
cd ~/src/ai && .venv/bin/python scripts/update/run.py --full
```

The orchestrator will:
- Pull latest changes (with automatic stash/unstash)
- Sync `.claude` hardlinks and audit skill hooks
- Check for pending critical dependency upgrades
- **Verify machine identity** — reads `scutil --get ComputerName`, matches against `machine` field in `~/Desktop/Valor/projects.json`, reports which projects this machine handles
- Sync dependencies if pyproject.toml changed
- Verify critical dependency versions
- Check/pull Ollama summarizer model
- Verify CLI tools and SDK authentication
- **Validate `projects.json` (green-light gate)** — runs `bridge/config_validation.py::validate_projects_config` over the full config (Step 4.6). Enforces that every bridge-contact identifier (DM contact id, Telegram group, email contact, email domain wildcard) resolves to exactly one machine. On failure: log the error, skip the service restart, leave the running bridge serving on the previously-validated config. See [Single-Machine Ownership](../../../docs/features/single-machine-ownership.md).
- Install/restart bridge, worker, caffeinate, nightly-tests, and log-rotate services (skipped if the validation gate failed; daily reflections run inside the worker's reflection scheduler)
- Set up global calendar hook and generate config
- Check MCP server configuration
- **Best-effort agent-judgment catchup (strictly last step)** — runs `valor-catchup` only after every service-management and health check above, and only when BOTH the bridge and worker report running. Failure or timeout is logged and swallowed; `/update` completion is wholly independent of its outcome. See [Agent-Judgment Catchup](#agent-judgment-catchup-strictly-last-step) below.

After running, report the result. If there are warnings or errors, list each one clearly.

**First-install backfill reminder (markitdown):** When the update run is the first to install the `[knowledge]` extra on this machine (detected by `scripts/update/deps.py`'s lockfile-diff check), the Telegram summary appends a one-line tip: `run 'valor-ingest --scan ~/work-vault/' to backfill existing binary files into sidecars.` The reminder is gated by `~/.cache/valor/markitdown-backfill-reminded` and fires only once per machine. If the user asks why existing PDFs/docs in the vault are not yet indexed after update, point them at this command — the watcher only picks up files modified after it starts.

**Log rotation:** The orchestrator installs the user-space log-rotate LaunchAgent on every `--full` run (`com.valor.log-rotate.plist` → `~/Library/LaunchAgents/`). No root/sudo needed; the LaunchAgent runs `scripts/log_rotate.py` every 30 minutes to rotate any `logs/*.log` file over 10 MB. The installer is content-idempotent — if the rendered plist matches the installed file, the bootout/bootstrap cycle is skipped entirely. If a stale `/etc/newsyslog.d/valor.conf` exists from prior releases, the orchestrator attempts `sudo -n rm` (non-interactive) to remove it; if sudo requires a password, the cleanup is skipped with a warning and retried next run.

The orchestrator automatically cleans up sessions as part of Step 5.5:
- Corrupted sessions (invalid IDs, unsaveable records) are deleted and indexes rebuilt
- Running/pending sessions older than 120 min with no live process are transitioned to `killed`
- Terminal sessions (killed/abandoned/failed/completed) are preserved for reflections to analyze
- Reflections handles its own 90-day expiry of old session records

### Agent-Judgment Catchup (strictly last step)

After all service-management and health checks, the orchestrator runs `run_catchup_step` (`scripts/update/run.py`) as the **final** action of `run_update` — invoking the `valor-catchup` CLI (issue #1709, see [Agent-Judgment Catchup](../../../docs/features/agent-judgment-catchup.md)).

`valor-catchup` reads each owned chat's recent thread, asks an LLM judge which inbound human messages are genuinely unanswered, and enqueues recovery sessions only for those — recovering messages whose original session hung or was killed *without replying* (which the mechanical catchup and reconciler skip forever).

**Best-effort contract — the step never blocks or fails `/update`:**

- **Health gate.** Invoked only when BOTH `service.get_service_status(...).running` AND `service.get_worker_status(...).running` are true. If either is down, the step logs a `catchup: skipped — ...` line and returns. It is also gated on `do_service_restart`, so verify-only and follower-skip runs never trigger recovery enqueues.
- **Subprocess + tight timeout.** `valor-catchup` runs as a subprocess with a tight per-invocation timeout (`CATCHUP_STEP_TIMEOUT_SECONDS`, 90s). A hung Telethon connect or stalled LLM call is killed on expiry and never stalls `/update`.
- **Failure/timeout swallowed.** Any failure, non-zero exit, or timeout is logged (`catchup: ... (swallowed)`) and swallowed. `run_catchup_step` never raises and never flips `UpdateResult.success` — `/update` completion is independent of `valor-catchup`'s outcome.

The `valor-catchup` CLI is propagated automatically via `pip install -e .` during the existing dependency-sync step — it's a `[project.scripts]` entry in `pyproject.toml`, so no extra propagation wiring is needed.

### Auto-Bump Critical Dependencies

The update system automatically checks PyPI for newer versions of `anthropic` and `claude-agent-sdk` on every run. When a newer version is available:

1. Bumps the pin in `pyproject.toml`
2. Runs `uv sync` to install the new version
3. Runs a smoke test (import check + `pytest tests/unit/test_docs_auditor_substrate.py -x -q`)
4. If smoke test passes: commits and pushes the bump
5. If smoke test fails: rolls back `pyproject.toml` and re-syncs old versions

This means SDK upgrades happen automatically and safely — no manual intervention needed unless a breaking change causes test failures.

### Critical Dependency Handling (git-driven changes)

When `pyproject.toml` changes via git pull with critical dep version changes (telethon, anthropic, claude-agent-sdk):

- The cron job (`remote-update.sh`) detects the change and writes `data/upgrade-pending`
- Running `/update` manually will apply the upgrade with proper verification

If `data/upgrade-pending` exists:
```bash
# Check what's pending
cat ~/src/ai/data/upgrade-pending

# After /update applies the upgrade and verifies the bridge starts:
rm ~/src/ai/data/upgrade-pending
```

### Verification Only

To check the environment without making changes:
```bash
cd ~/src/ai
.venv/bin/python scripts/update/run.py --verify
```

### Reinstall Launchd Services

After update, reinstall launchd plists to pick up any template changes. Daily reflections run inside the worker's reflection scheduler (`agent/reflection_scheduler.py`, driven by `config/reflections.yaml`), so reinstalling the worker covers them; the SDLC reflection has its own separate plist installer:

```bash
cd ~/src/ai
./scripts/install_worker.sh
./scripts/install_sdlc_reflection.sh
```

The install script substitutes `__PROJECT_DIR__` and `__HOME_DIR__` placeholders with the current machine's paths. This ensures plists work on any machine without hardcoded usernames.

## When to Load Sub-Files

| Sub-file | Load when... |
|----------|-------------|
| `references/troubleshooting.md` | An update run fails or the environment misbehaves afterward (venv, deps, calendar, machine identity, bridge/worker won't start) |
| `references/modules.md` | Debugging orchestrator internals or calling a `scripts/update/` module directly (git.py, deps.py, verify.py, gws_auth.py, calendar.py, MCP registration, BYOB/bcu update steps, service.py) |

## Node toolchain (soft prerequisite)

Machines that run the `do-design-system` skill also need Node + npm (for
`npx @google/design.md`). `remote-update.sh` runs `npm ci --only=prod`
guarded by:

```bash
if [ -f "$PROJECT_DIR/package.json" ] && command -v npm >/dev/null 2>&1; then
    ( set +o pipefail; cd "$PROJECT_DIR" && npm ci --only=prod ) \
        || echo "[update] npm ci failed (non-fatal); continuing"
fi
```

The non-pipefail subshell + `|| echo` trailer guarantee a missing `npm`
or a transient install failure never aborts the parent update. Machines
without Node simply skip the block silently; design-system tooling then
falls back to Python-only emission (`--generate --no-node`) for
`design-system.md` / `brand.css` / `source.css`. Lint and DTCG / Tailwind
exports still require Node and are only produced on Node-equipped
machines. See `docs/features/design-system-tooling.md` for the full
fallback semantics.
