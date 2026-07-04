---
name: setup
description: "Use when configuring a new machine to run the Valor Telegram bridge. Triggered by 'setup', 'configure this machine', or 'new machine setup'."
disable-model-invocation: true
---

# Setup - New Machine Configuration

Configure this machine to run the Valor Telegram bridge. You do everything except the interactive Telegram login step.

## Prerequisites

**PREREQUISITE: Must be on latest main branch before running.**

```bash
cd ~/src/ai && git checkout main && git pull
```

Before starting, confirm the user has:
- Python 3.12+ installed
- The `ai` repo cloned at `~/src/ai` **on the main branch with latest changes pulled**
- Telegram API credentials (api_id and api_hash from https://my.telegram.org). If they don't have these, pause and explain how to get them before continuing.

## Setup Phases

Work through the phases in order. Each phase's full commands, tables, and rationale live in a sub-file — load it when you reach that phase.

| Phase | Steps | Load |
|-------|-------|------|
| 1. Environment | Python symlink, zshenv bootstrap, uv, venv + deps, `.env` | `references/environment.md` |
| 2. Authentication | Google Calendar OAuth, Claude SDK auth, Sentry CLI | `references/auth.md` |
| 3. Project config | `~/Desktop/Valor/projects.json`, persona overlays | `references/projects-config.md` |
| 4. Telegram login | Interactive login (USER ACTION — inline below) | (inline) |
| 5. Services + optional surfaces | Worker/reflections install (inline below); BYOB, computer-use, generation model | `references/optional-surfaces.md` |
| 6. Start + verify | Start bridge, health check, confirm to user | `references/verification.md` |

## Phase 1: Environment (Steps 0-3)

Load `references/environment.md` and complete, in order:

1. **Step 0.1** — Ensure bare `python` resolves to Python 3.12+ (hooks invoke bare `python` under `/bin/sh`; without the symlink every hook silently fails)
2. **Step 0.2** — Bootstrap the cross-machine shell env loader (`~/Desktop/Valor/zshenv.sh` via `scripts/update/zshenv_sync.py`)
3. **Step 1** — Install the `uv` package manager
4. **Step 2** — Create `.venv` and sync dependencies (`uv venv && uv sync --all-extras && uv pip install -e .`), then verify key imports
5. **Step 3** — Create `.env` from `.env.example` and fill required variables (`ACTIVE_PROJECTS`, `ANTHROPIC_API_KEY`, Telegram credentials). **Ask the user** which projects this machine should monitor.

## Phase 2: Authentication (Steps 4-5)

Load `references/auth.md` and complete:

1. **Step 4** — Google Calendar: OAuth credentials at `~/Desktop/Valor/google_credentials.json`, consent flow via `valor-calendar --reauth`, calendar mappings (auto-generated later by `/update`)
2. **Step 5** — Claude SDK auth (Claude Desktop app OAuth primary, API key fallback) and Sentry CLI token (`SENTRY_PERSONAL_TOKEN` in `~/Desktop/Valor/.env`)

Google OAuth consent and any Claude login are **user actions** — pause for them when the sub-file says so.

## Phase 3: Project Configuration (Step 6)

Load `references/projects-config.md` and configure `~/Desktop/Valor/projects.json` (iCloud-synced, private). Key invariants (full detail in the sub-file):

- Every project MUST have `working_directory` and `machine` (exact `ComputerName` — single-machine ownership)
- Always include the full `defaults` section; never set `respond_to_all: false`
- Seed persona overlays into `~/Desktop/Valor/personas/` without overwriting existing ones
- Verify every `working_directory` exists on disk

## Phase 4: Telegram Login (Step 7 — USER ACTION REQUIRED)

Check for an existing session:

```bash
ls data/*.session 2>/dev/null
```

**If a session file exists**: Skip to Phase 5.

**If no session file exists**: The user must complete an interactive login. Tell them:

> I've finished all the automated setup. One step requires your input -- the Telegram login sends a verification code to your phone.
>
> Please run this in a terminal:
> ```
> cd ~/src/ai && source .venv/bin/activate && python scripts/telegram_login.py
> ```
> Let me know when you're done.

**STOP HERE. Do not proceed until the user confirms the login is complete.**

After they confirm, verify the session was created:

```bash
ls data/*.session
```

If no session file appeared, something went wrong. Ask the user what happened and help debug.

## Phase 5: Services + Optional Surfaces (Steps 8-8.6)

### Step 8: Install Worker + Reflection Schedules

Three launchd services: the standalone worker, the reflection-scheduler subprocess (`python -m reflections` — its own supervised process since issue #1828, no longer inside the worker), and the SDLC reflection schedule:

```bash
cd ~/src/ai
./scripts/install_worker.sh
./scripts/install_reflection_worker.sh
./scripts/install_sdlc_reflection.sh
```

`install_reflection_worker.sh` self-gates on worker role (any project's `machine` matches this host; fail-open), so it installs wherever the worker does. Verify all loaded:

```bash
launchctl list | grep com.valor
```

Expect `com.valor.worker`, `com.valor.reflection-worker`, and `com.valor.sdlc-reflection` labels. The reflection scheduler logs to `logs/reflection_worker.log`; `python -m reflections --dry-run` validates the registry loads.

### Steps 8.5-8.6: Optional Surfaces (macOS only, operator-opt-in)

Load `references/optional-surfaces.md` for:

- **BYOB** (real-Chrome control via MCP) — bun install, pinned clone to `~/.byob/`, extension load (user click-through), `bun run doctor` verification
- **Computer-use (bcu)** — **prompt the user first**; opt-in sentinel + pinned release + two System Settings permissions (user actions)
- **Generation model selection** — RAM-based `gemma4:31b-mlx` vs `gemma4:31b-cloud` choice written to `~/.zshenv` (machine-local, never the vault `.env`)

Skip this entire phase on non-macOS hosts or when the user declines.

## Phase 6: Start the Bridge and Verify (Steps 9-11)

Load `references/verification.md` and complete:

1. **Step 9** — `mkdir -p logs`, start via `./scripts/valor-service.sh start`, confirm `Connected to Telegram` in `logs/bridge.log` and `pgrep -f telegram_bridge.py`
2. **Step 10** — Run the comprehensive health check (system tools, Python env, CLI tools, bridge status)
3. **Step 11** — Report final status to the user (bridge PID, active projects, monitored groups, next steps: test message + `/update`)

## Rules

- **You** do everything: deps, config, starting the bridge, verification
- **User** only does:
  1. Interactive Telegram login (Phase 4)
  2. Claude login (if not already done)
  3. Google OAuth consent (if no token exists)
- Never ask the user to start the bridge or check logs -- do it yourself
- Never set `respond_to_all: false` in project configs
- Always include the `defaults` section in `projects.json`
- Always verify `working_directory` paths exist on disk before starting
- The bridge must be confirmed running before you report success
- If anything fails, debug it yourself. Only escalate to the user if it requires their credentials or interactive input.

## Troubleshooting

Phase-specific troubleshooting lives at the bottom of each sub-file:

- uv / dependency issues → `references/environment.md`
- Calendar OAuth failures → `references/auth.md`
- Bridge won't start → `references/verification.md`
