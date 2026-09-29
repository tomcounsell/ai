---
name: setup
description: "Use when configuring a new machine to run the Valor Telegram bridge. Triggered by 'setup', 'configure this machine', or 'new machine setup'."
disable-model-invocation: true
---

# Setup - New Machine Configuration

Configure this machine to run the Valor Telegram bridge. You do all of it yourself: deps, config, services, starting the bridge, verification, and debugging failures. The user only does what needs their hands or credentials: the Telegram login, a Claude login if needed, Google OAuth consent, Chrome/System Settings click-throughs, and supplying secrets.

**Done when** the bridge is running and `logs/bridge.log` shows `Connected to Telegram`, the Step 10 health check is clean, and you have reported bridge PID, active projects, monitored groups, and next steps (send a test message; run `/update`).

## Prerequisites

Be on latest main:

```bash
cd ~/src/ai && git checkout main && git fetch origin main && git merge --ff-only origin/main
```

The user needs Python 3.12+, the repo at `~/src/ai`, and Telegram API credentials (api_id, api_hash from https://my.telegram.org). If they lack the credentials, explain how to get them before continuing.

## Phases

Work through them in order; each sub-file holds that phase's commands and troubleshooting.

| Phase | Covers | Load |
|-------|--------|------|
| 1. Environment | bare `python` resolution, zshenv bootstrap, uv, venv + deps, `.env` symlink and secrets | `references/environment.md` |
| 2. Authentication | Google Calendar OAuth, Claude auth, Sentry token | `references/auth.md` |
| 3. Project config | `~/Desktop/Valor/projects.json`, persona overlays | `references/projects-config.md` |
| 4. Telegram login | interactive login (below) | inline |
| 5. Services + optional surfaces | worker and reflection scheduler (below); BYOB, computer-use, generation model, mesh network | `references/optional-surfaces.md`, `references/mesh-network.md` |
| 6. Start + verify | start bridge, health check, report | `references/verification.md` |

## Constraints

- `projects.json`: every project has `working_directory` (verified on disk) and `machine` (exact `scutil --get ComputerName`; single-machine ownership). Always include the full `defaults` section. Never set `respond_to_all: false`.
- Secrets go in the vault `~/Desktop/Valor/.env`; the repo `.env` is a symlink to it (see CLAUDE.md "Secrets").
- Optional surfaces (BYOB, computer-use, mesh network) are macOS-only and opt-in: ask before installing each; skip on decline or non-macOS.
- Escalate to the user only for credentials or interactive input.

## Phase 4: Telegram login (user action)

If `ls data/*.session` finds a session file, skip to Phase 5. Otherwise tell the user:

> I've finished the automated setup. One step needs you: the Telegram login sends a verification code to your phone. Please run this in a terminal and tell me when it's done:
> ```
> cd ~/src/ai && source .venv/bin/activate && python scripts/telegram_login.py
> ```

Stop and wait for their confirmation, then check `ls data/*.session`. If no session file appeared, ask what happened and debug with them.

## Phase 5: worker and reflection scheduler

```bash
cd ~/src/ai
./scripts/install_worker.sh
./scripts/install_reflection_worker.sh
launchctl list | grep com.valor   # expect com.valor.worker and com.valor.reflection-worker
```

`install_reflection_worker.sh` self-gates on worker role (any project's `machine` matches this host; fail-open). The scheduler logs to `logs/reflection_worker.log`; `python -m reflections --dry-run` validates the registry loads.
