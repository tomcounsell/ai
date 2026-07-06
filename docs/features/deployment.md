# Multi-Instance Deployment

Valor runs on every machine as a service. Each machine owns a subset of projects defined in `~/Desktop/Valor/projects.json`, identified by the `projects.<key>.machine` field matching the local `ComputerName`.

## How It Works

When a message arrives:
1. Bridge checks if the group/sender/contact matches any *active* project (one owned by this machine)
2. If yes, injects that project's context and responds
3. If no, ignores the message — the machine that owns it will pick it up

**Strict ownership invariant:** every bridge-contact identifier (Telegram DM contact id, Telegram group name, email contact, email domain) is owned by exactly one machine. Two machines must never both initiate work on the same incoming message. See [Single-Machine Ownership](single-machine-ownership.md) for the full rule and validator behavior.

## Setup

### 1. Define projects in ~/Desktop/Valor/projects.json

```json
{
  "projects": {
    "myproject": {
      "name": "MyProject",
      "working_directory": "~/src/myproject",
      "telegram": {
        "groups": ["Dev: MyProject"]
      },
      "github": { "org": "myorg", "repo": "myrepo" },
      "context": {
        "tech_stack": ["Python", "React"],
        "description": "Focus areas for AI responses"
      }
    }
  },
  "defaults": {
    "working_directory": "~/src/ai",
    "telegram": {
      "respond_to_all": true,
      "respond_to_mentions": true,
      "respond_to_dms": true,
      "mention_triggers": ["@valor", "valor", "valorengels", "hey valor"]
    },
    "response": {
      "typing_indicator": true,
      "max_response_length": 4000,
      "timeout_seconds": 300
    }
  }
}
```

### 2. Set ACTIVE_PROJECTS in .env

```bash
# Single project
ACTIVE_PROJECTS=myproject

# Multiple projects on same machine
ACTIVE_PROJECTS=valor,popoto,django-project-template
```

### 3. Start the service

```bash
./scripts/valor-service.sh install
```

## Context Injection

When a message arrives from a configured group, the bridge injects project context:

```
PROJECT: MyProject
FOCUS: Focus areas for AI responses
TECH: Python, React
REPO: myorg/myrepo
```

Session IDs are scoped per project: `tg_myproject_123456`

## Example Deployment

| Machine | ACTIVE_PROJECTS | Monitors |
|---------|-----------------|----------|
| mac-a | valor | Dev: Valor |
| mac-b | popoto,django-project-template | Dev: Popoto, Dev: Django Template |
| mac-c | valor,popoto,django-project-template | All groups |

Multiple machines can monitor different groups, or one machine can monitor all.

## Critical Configuration Rules

1. **Every project MUST have `working_directory`** - Absolute path to the repo
2. **Always include the `defaults` section** - Copy from example if missing
3. **DO NOT set `respond_to_all: false`** - Default is `true`, omit the field
4. **Keep project telegram config minimal** - Usually just `"groups": [...]`
5. **Verify paths exist on disk** - Run `ls` on each `working_directory`

## Troubleshooting

### Bridge not responding to messages
1. Check `ACTIVE_PROJECTS` in `.env` includes your project key
2. Verify the Telegram group name matches exactly (case-sensitive)
3. Check `tail -f logs/bridge.log` for routing decisions

### Wrong project context
1. Ensure only one project maps to each Telegram group (validator catches this — see [Single-Machine Ownership](single-machine-ownership.md))
2. Check `~/Desktop/Valor/projects.json` for duplicate group entries

### "projects.json validation failed" in update logs
The update script's green-light gate (Step 4.6 in `scripts/update/run.py`) blocked the service restart because two machines now claim the same bridge-contact identifier. Read the logged error — it lists the conflicting entries — and either remove the duplicate or change the `machine` field on one of the projects. Full failure-mode table: [Single-Machine Ownership](single-machine-ownership.md#failure-modes--responses).

### Session isolation issues
1. Sessions are scoped by project - `tg_{project}_{chat_id}`
2. Different projects in same chat create separate sessions

## Update Polling

Every machine automatically polls for updates from `origin/main` every 30 minutes via the `com.valor.update` launchd plist. This ensures code changes propagate to all machines without relying on Telegram message delivery.

**How it works:**
1. `com.valor.update` fires every 1800 seconds (30 minutes) via `StartInterval`
2. Runs `scripts/remote-update.sh`, which first does `git pull --ff-only` in bash, then calls the update orchestrator (`scripts/update/run.py --cron --no-pull`)
3. If new commits are detected: pulls changes, syncs dependencies (if dep files changed), writes `data/restart-requested`
4. The bridge session queue detects the restart flag and triggers a graceful restart after in-flight sessions complete

**Verify polling is active:**
```bash
# Check the update plist is loaded
launchctl list | grep com.valor.update

# View recent update activity
tail -20 logs/update.log
```

**Install or reinstall:**
```bash
./scripts/valor-service.sh install
```

The Telegram `/update` command remains available as a manual override for immediate updates on the receiving machine.

For more details on the update polling mechanism, see [Bridge Self-Healing](bridge-self-healing.md#10-update-polling-comvalorupdate).

## Service Topology

Each machine runs a subset of these four services:

All plist labels below use the `${SERVICE_LABEL_PREFIX}` configured in `.env`
(default `com.valor`). Forks override this for coexistence — see
`docs/guides/setup.md` for details.

| Service | Plist | Purpose | Required On |
|---------|-------|---------|-------------|
| Bridge | `${SERVICE_LABEL_PREFIX}.bridge` | Telegram I/O only (no embedded worker) | Bridge machines |
| Worker | `${SERVICE_LABEL_PREFIX}.worker` | Standalone session processing | All machines |
| Watchdog | `${SERVICE_LABEL_PREFIX}.bridge-watchdog` | Health monitoring, crash recovery | Bridge machines |
| Update | `${SERVICE_LABEL_PREFIX}.update` | Auto-pull from origin/main | All machines |

**Dev workstations** run Worker + Update. Sessions are processed and output is written to `logs/worker/`.

**Bridge machines** run Bridge + Worker + Watchdog + Update. The bridge handles Telegram I/O only; the standalone worker processes sessions and sends output via Telegram callbacks registered at startup.

Both Bridge and Worker must run on bridge machines. The bridge is I/O only and does not process sessions on its own.

### Email Bridge

The email bridge runs as an optional service on bridge machines. It polls IMAP every 30 seconds and routes inbound emails to agent sessions via contact-based or domain-based project matching.

**Prerequisites:** Add `email.contacts` and/or `email.domains` to `projects.json` and set IMAP/SMTP credentials in `.env`:

```bash
IMAP_HOST=imap.gmail.com
IMAP_PORT=993
IMAP_USER=valor@yuda.me
IMAP_PASSWORD=<gmail-app-password>
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=valor@yuda.me
SMTP_PASSWORD=<gmail-app-password>
```

```bash
# Lifecycle
./scripts/valor-service.sh email-start
./scripts/valor-service.sh email-stop
./scripts/valor-service.sh email-restart

# Status (warns if last poll is > 5 minutes ago)
./scripts/valor-service.sh email-status

# Dead letter queue (failed SMTP sends)
./scripts/valor-service.sh email-dead-letter list
./scripts/valor-service.sh email-dead-letter replay --all
```

See [Email Bridge](email-bridge.md) for full architecture and configuration details.

### Worker Installation

```bash
# Install standalone worker (dev workstations)
./scripts/install_worker.sh

# Manage the worker
./scripts/valor-service.sh worker-start
./scripts/valor-service.sh worker-stop
./scripts/valor-service.sh worker-restart
./scripts/valor-service.sh worker-status
./scripts/valor-service.sh worker-logs
```

See [Worker Service](worker-service.md) for full details.

### Log Rotation

Log rotation runs in user space via a LaunchAgent — no root needed. See
[Log Rotation](log-rotation.md) for the full design.

`/update --full` installs `com.valor.log-rotate.plist` to
`~/Library/LaunchAgents/` via `scripts/update/service.py::install_log_rotate_agent()`.
The agent runs `scripts/log_rotate.py` every 30 minutes, globbing `logs/*.log`
and rotating any file over 10 MB (keeping 3 backups). Startup rotation in
`scripts/valor-service.sh::rotate_log()` handles event-driven rotation on
every service restart.

The installer is content-idempotent — it skips `launchctl bootout`/`bootstrap`
when the rendered plist already matches the installed file, so running
`/update --full` twice in a row is a no-op the second time.

Machines updated before this migration had `/etc/newsyslog.d/valor.conf`
installed. `remove_newsyslog_config()` removes it via `sudo -n rm` during
`/update --full`; when sudo isn't cached the cleanup is skipped with a
warning and retried on the next update.

## Granite PTY Pool

Bridge-originated sessions execute through the granite PTY container (see
[Granite PTY Container: Production Path](granite-pty-production.md)). The worker
holds a bounded, singleton pool of interactive `claude` TUI pairs.

**One env var, set in `~/Desktop/Valor/.env` (optional, default is correct):**

```bash
# Hard max concurrent PM+Dev PTY pairs. Note the DOUBLE underscore
# (pydantic nested-settings delimiter). Default 3.
GRANITE__PTY_POOL_SIZE=3
```

**Relationship to `MAX_CONCURRENT_SESSIONS`:** the pool size is intentionally
**smaller** than `MAX_CONCURRENT_SESSIONS` (default 8) so the Redis queue
absorbs over-cap sessions instead of overcommitting memory. Each
`claude --permission-mode bypassPermissions` PTY consumes ~200 MB resident,
so a full pool of 3 pairs is ~1.2 GB. Memory-constrained machines can set
`GRANITE__PTY_POOL_SIZE=1` or `2`.

**Growth path (3 → 6):** the default can rise to 6 once health/observability
and memory management land (follow-on issues). When raising it, verify the
`ThreadPoolExecutor` size (`min(32, os.cpu_count()+4)`) accommodates
`MAX_CONCURRENT_SESSIONS × pool_size` long-lived threads, and update the
semaphore-cap assertion in `tests/unit/granite_container/test_pty_pool.py`.

**Orphan cleanup:** on a worker SIGKILL, PTY children survive. The next worker
startup reads `data/granite_pty_pids.json` and PID-kills them (PID-targeted,
never `pkill -f`, so an operator's personal `claude` session is untouched).

### Reverting the granite cutover

The cutover is all-or-nothing with no runtime feature flag. To roll back to the
headless harness path on incident:

1. `git revert <merge-sha>` (or `git revert -m 1 <merge-sha>` for a merge
   commit) and `git push`.
2. Restart the worker: `./scripts/valor-service.sh worker-restart`.
3. Drain stuck sessions from `telegram:outbox:*` — inspect
   `redis-cli LRANGE telegram:outbox:{session_id} 0 -1`; the drafter is
   idempotent on retried `[/user]` payloads.
4. No manual flag toggling, no env var changes.

## `claude` CLI Version Pin (D1a, issue #1817)

The live `claude` CLI is installed via the **native installer**:
`~/.local/bin/claude` is a symlink into
`~/.local/share/claude/versions/<version>/`. It is not an npm package — it is
never listed in `scripts/update/npm_tools.py`'s `MANAGED_PACKAGES`, and it
must stay that way (adding it there would either no-op, since npm doesn't own
the binary, or force-switch the fleet onto the npm install path, which is not
how the CLI actually got onto this machine).

The native installer floats `claude` to latest on auto-update. Historically,
a minor-version bump has reworded the interactive TUI's scraped markers
(`IDLE_BAR`, `PROMPT_GLYPH`, `SPINNER_EVIDENCE_RE` in
`agent/granite_container/pty_driver.py`; the trust-folder prompt pattern in
`agent/granite_container/startup_parser.py`) with no code change on our side
— a silent, fleet-wide PTY-session hang. Two checks guard against this:

- **D1a (version pin):** `scripts/update/verify.py`'s `check_claude_version_pin()`
  compares the installed version to `PINNED_CLAUDE_VERSION`. The value has a
  **single source of truth** in `config/models.py` (default `2.1.198`, env-
  overridable via `PINNED_CLAUDE_VERSION`), imported by both this D1a check and
  the #1839 ollama-canary drift alert in
  `scripts/nightly_regression_tests.py`, and mirrored as a typed catalog entry
  at `config/settings.py` `Settings.pinned_claude_version`. A drift logs a
  WARNING by default (non-blocking — a version bump does not necessarily
  break anything). Provisioning a canary against a not-yet-fleet version is
  tracked in issue #1854.
- **D1b (contract-check):** `worker/__main__.py` calls
  `agent.granite_container.pty_driver.verify_tui_marker_contract()` at
  startup, which re-runs each scraped-marker regex against a golden sample of
  known-good CLI output. A mismatch logs CRITICAL — but only hard-fails
  startup when at least one role is configured `pty`-transport (a fully
  `headless` fleet is immune to TUI-marker drift by construction; see
  `docs/features/README.md` for the per-role transport hedge).

Both checks share one enforcement flag, off by default:

```bash
# Set to "1" to hard-fail /update and worker startup on a detected
# claude-CLI-contract drift (version pin OR TUI marker mismatch). Off by
# default — a drift does not necessarily mean anything is actually broken.
CLAUDE_CONTRACT_CHECK_ENFORCE=1
```

### Bump procedure

Bumping `PINNED_CLAUDE_VERSION` is a **deliberate** procedure, not something
to do reflexively on every drift warning:

1. Confirm the new `claude` version is actually installed and stable on at
   least one machine (`readlink ~/.local/bin/claude`).
2. Re-verify the D1b scraped markers still match the new version's actual
   TUI output — run a live PTY session (or the existing granite smoke-test
   tooling) and confirm idle detection, the trust-folder prompt, and the
   spinner-evidence heuristic still fire correctly. If any marker's shape
   changed, update the regex in `pty_driver.py`/`startup_parser.py` FIRST,
   and update `verify_tui_marker_contract()`'s golden samples to match.
3. Update the `PINNED_CLAUDE_VERSION` default in `config/models.py` (the single
   source of truth — `scripts/update/verify.py`, `scripts/nightly_regression_tests.py`,
   and `config/settings.py` all read it from there) to the new version string.
4. Note the bump here (version, date, what — if anything — changed in the
   scraped markers):

   | Version | Date | Notes |
   |---|---|---|
   | 2.1.197 | 2026-07-02 | Initial pin (issue #1817) |
   | 2.1.198 | 2026-07-03 | Single-sourced the pin in `config/models.py` (shared with the #1839 nightly canary); default set to the current fleet version. Markers unchanged. |
   | 2.1.201 | 2026-07-06 | **Marker drift (issue #1918):** the renderer stopped re-emitting the bypass-permissions bar cells after a turn — the bar paints only in the welcome frame, so post-write `IDLE_BAR` checks went permanently blind (fleet-wide `startup_unresolved` plateaus since 2026-07-01). Added `AGENTS_HINT_BAR` (`← for agents`, the footer hint that DOES repaint on every turn return; live-probed at 24x80 and 50x200) as an accepted idle-bar signature alongside `IDLE_BAR`/`OVERLAY_BAR`. |

## See Also

- Run `/setup` for full machine configuration
- See `config/projects.example.json` for template
- Check `bridge/telegram_bridge.py` for routing logic
- See [Worker Service](worker-service.md) for standalone worker details
- See [Granite PTY Container: Production Path](granite-pty-production.md) for the session-execution substrate
