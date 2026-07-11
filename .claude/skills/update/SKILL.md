---
name: update
description: "Use when deploying updates to this machine: pull, sync deps, verify, restart services. Triggered by 'update', 'deploy', 'pull and restart', or after git pull."
disable-model-invocation: true
---

# Update & Restart

Bring this machine to latest main: deps synced, environment verified, services restarted and healthy. The orchestrator does the work — your job is to run it, read its output, and report every warning or error.

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
- Check for pending critical dependency upgrades; sync dependencies if pyproject.toml changed; verify critical versions
- **Verify machine identity** — reads `scutil --get ComputerName`, matches against the `machine` field in `~/Desktop/Valor/projects.json`, reports which projects this machine handles
- Check/pull Ollama summarizer model; verify CLI tools and SDK authentication
- **Validate `projects.json` (green-light gate)** — runs `bridge/config_validation.py::validate_projects_config` over the full config (Step 4.6). Enforces that every bridge-contact identifier (DM contact id, Telegram group, email contact, email domain wildcard) resolves to exactly one machine. On failure: log the error, skip the service restart, leave the running bridge serving on the previously-validated config. See [Single-Machine Ownership](../../../docs/features/single-machine-ownership.md).
- Install/restart services (skipped if the validation gate failed): bridge, worker, the reflection-scheduler subprocess (`com.valor.reflection-worker` — installed right after the worker so at most a brief zero-scheduler window; issue #1828), nightly-tests, caffeinate, and log-rotate
- Set up global calendar hook and generate config; verify MCP server registration (memory Step 4.8, BYOB Step 4.9 — idempotent, self-healing)
- **Best-effort agent-judgment catchup (strictly last step)** — see below

After running, report the result. List every warning or error clearly.

**First-install backfill reminder (markitdown):** the first run that installs the `[knowledge]` extra appends a one-time tip to the Telegram summary: `run 'valor-ingest --scan ~/work-vault/' to backfill existing binary files into sidecars` (gated by `~/.cache/valor/markitdown-backfill-reminded`). If the user asks why pre-existing vault PDFs/docs aren't indexed, point them at that command — the watcher only picks up files modified after it starts.

**Log rotation:** every `--full` run installs the user-space LaunchAgent `com.valor.log-rotate.plist` (content-idempotent, no sudo) which rotates any `logs/*.log` over 10 MB every 30 minutes. A stale root-era `/etc/newsyslog.d/valor.conf` is removed via non-interactive `sudo -n rm`; if sudo needs a password, cleanup is skipped with a warning and retried next run.

**Obsolete launchd-job sweep (Step 1.56):** every run boots out and deletes LaunchAgents for features that have been fully removed from the codebase, so a deleted feature's plist doesn't keep loading and failing on already-provisioned machines forever. The list of removed jobs lives in `scripts/update/service.py::OBSOLETE_SERVICE_SUFFIXES` — this is the launchd analog of `RENAMED_REMOVALS` in `hardlinks.py`: **when you delete a launchd-backed feature (its install script + code), add its label suffix there** so `/update` cleans up the stale job on every machine. The sweep is idempotent and fail-soft (a machine that never had the job is a no-op); removals are logged and reported in the run summary. Runs unconditionally alongside the plist-PATH heal (Step 1.55), not gated on the service-restart step.

**Session cleanup (Step 5.5):** corrupted sessions are deleted and indexes rebuilt; running/pending sessions older than 120 min with no live process transition to `killed`; terminal sessions are preserved for reflections (which handles its own 90-day expiry).

### Agent-Judgment Catchup (strictly last step)

The **final** action of `run_update` invokes the `valor-catchup` CLI (issue #1709, see [Agent-Judgment Catchup](../../../docs/features/agent-judgment-catchup.md)): it reads each owned chat's recent thread, asks an LLM judge which inbound human messages are genuinely unanswered, and enqueues recovery sessions only for those.

**Best-effort contract — the step never blocks or fails `/update`:**

- **Health gate.** Runs only when BOTH bridge and worker report running AND `do_service_restart` is set (verify-only and follower-skip runs never trigger recovery enqueues). Otherwise it logs `catchup: skipped — ...` and returns.
- **Subprocess + tight timeout.** Runs as a subprocess with `CATCHUP_STEP_TIMEOUT_SECONDS` (90s); a hung Telethon connect or stalled LLM call is killed on expiry.
- **Failure swallowed.** Any failure, non-zero exit, or timeout is logged (`catchup: ... (swallowed)`) and never flips `UpdateResult.success`.

The CLI propagates via `pip install -e .` during the dependency-sync step (`[project.scripts]` entry) — no extra wiring.

### Auto-Bump Critical Dependencies

Every run checks PyPI for newer `anthropic` and `claude-agent-sdk` versions (only the lockfile-maintainer machine runs auto-bump; others skip to avoid racing). When a newer version is available:

1. Bumps the pin in `pyproject.toml`
2. Runs `uv sync` to install the new version
3. Runs a smoke test (import check + `pytest tests/unit/test_docs_auditor_substrate.py -x -q`)
4. If smoke test passes: commits and pushes the bump
5. If smoke test fails: rolls back `pyproject.toml` and re-syncs old versions

### Critical Dependency Handling (git-driven changes)

When `pyproject.toml` changes via git pull with critical dep version changes (telethon, anthropic, claude-agent-sdk), the cron job (`remote-update.sh`) writes `data/upgrade-pending`; running `/update` manually applies the upgrade with proper verification.

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

After update, reinstall launchd plists to pick up any template changes. The reflection scheduler runs in its own launchd subprocess (`python -m reflections`, label `com.valor.reflection-worker`) — out-of-process from the worker since issue #1828 — so three installers cover the set:

```bash
cd ~/src/ai
./scripts/install_worker.sh
./scripts/install_reflection_worker.sh
./scripts/install_sdlc_reflection.sh
```

`install_reflection_worker.sh` self-gates on worker role (any project's `machine` matches this host; fail-open) and removes its stale plist on non-worker machines. Verify with `python -m reflections --dry-run` (loads the registry, exits 0) and `tail -f logs/reflection_worker.log`. Install scripts substitute `__PROJECT_DIR__` and `__HOME_DIR__` placeholders, so plists work on any machine without hardcoded usernames.

## When to Load Sub-Files

| Sub-file | Load when... |
|----------|-------------|
| `references/troubleshooting.md` | An update run fails or the environment misbehaves afterward (venv, deps, calendar, machine identity, bridge/worker/reflection-scheduler won't start) |
| `references/modules.md` | Debugging orchestrator internals or calling a `scripts/update/` module directly (git.py, deps.py, verify.py, gws_auth.py, calendar.py, MCP registration, service.py) |

## Node toolchain (soft prerequisite)

Machines that run the `do-design-system` skill also need Node + npm (for `npx @google/design.md`). `remote-update.sh` runs `npm ci --omit=dev` guarded by:

```bash
if [ -f "$PROJECT_DIR/package.json" ] && command -v npm >/dev/null 2>&1; then
    ( set +o pipefail; cd "$PROJECT_DIR" && npm ci --omit=dev ) \
        || echo "[update] npm ci failed (non-fatal); continuing"
fi
```

The non-pipefail subshell + `|| echo` trailer guarantee a missing `npm` or a transient install failure never aborts the parent update. Machines without Node skip the block silently; design-system tooling falls back to Python-only emission (`--generate --no-node`). Lint and DTCG / Tailwind exports still require Node. See `docs/features/design-system-tooling.md`.
