# Multi-Instance Deployment

Valor runs on every machine as a service. Each machine is configured to monitor specific Telegram groups.

## How It Works

When a message arrives:
1. Bridge checks if the group matches any active project
2. If yes, injects that project's context and responds
3. If no, ignores the message

This allows multiple machines to run Valor, each monitoring different groups.

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
      "mention_triggers": ["@valor", "valor", "hey valor"]
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
1. Ensure only one project maps to each Telegram group
2. Check `~/Desktop/Valor/projects.json` for duplicate group entries

### Session isolation issues
1. Sessions are scoped by project - `tg_{project}_{chat_id}`
2. Different projects in same chat create separate sessions

## Update Polling

Every machine automatically polls for updates from `origin/main` every 30 minutes via the `com.valor.update` launchd plist. This ensures code changes propagate to all machines without relying on Telegram message delivery.

**How it works:**
1. `com.valor.update` fires every 1800 seconds (30 minutes) via `StartInterval`
2. Runs `scripts/remote-update.sh`, which calls the update orchestrator (`scripts/update/run.py --cron`)
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

| Service | Plist | Purpose | Required On |
|---------|-------|---------|-------------|
| Bridge | `com.valor.bridge` | Telegram I/O only (no embedded worker) | Bridge machines |
| Worker | `com.valor.worker` | Standalone session processing | All machines |
| Watchdog | `com.valor.bridge-watchdog` | Health monitoring, crash recovery | Bridge machines |
| Update | `com.valor.update` | Auto-pull from origin/main | All machines |

**Dev workstations** run Worker + Update. Sessions are processed and output is written to `logs/worker/`.

**Bridge machines** run Bridge + Worker + Watchdog + Update. The bridge handles Telegram I/O only; the standalone worker processes sessions and sends output via Telegram callbacks registered at startup.

Both Bridge and Worker must run on bridge machines. The bridge is I/O only and does not process sessions on its own.

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

## See Also

- Run `/setup` for full machine configuration
- See `config/projects.example.json` for template
- Check `bridge/telegram_bridge.py` for routing logic
- See [Worker Service](worker-service.md) for standalone worker details
