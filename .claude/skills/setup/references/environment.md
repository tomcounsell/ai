# Phase 1: Environment — Python, Shell, Dependencies, .env

Load this when starting a fresh-machine setup (Steps 0-3).

## Step 0.1: Ensure bare `python` resolves to Python 3.12+

Claude Code hooks invoke bare `python` under `/bin/sh`, which does not honor zsh aliases. macOS does not ship a `python` binary by default — only `python3`. Without this symlink every hook that uses `python` silently fails with `command not found`, surfacing errors in the UI and disabling validators (no-raw-redis-delete, plan-section checks, SDLC reminders, etc.).

```bash
# Verify python3 is 3.12+
python3 --version

# Create the symlink in a user-writable PATH dir (no sudo)
ln -sf "$(command -v python3)" /opt/homebrew/bin/python

# Confirm /bin/sh resolves it
/bin/sh -c 'python --version'  # expected: Python 3.12.x or newer
```

The update orchestrator (`scripts/update/run.py`) verifies this via `check_python_alias()` and fails loudly if missing.

## Step 0.2: Bootstrap cross-machine shell env loader

Cross-machine secrets and shell config live in `~/Desktop/Valor/` (iCloud-synced). `~/.zshenv` itself does NOT sync (it's in `$HOME`), so each new machine needs a one-line bootstrap that sources the vault loader. The update script self-heals this on every run via `scripts/update/zshenv_sync.py`, but on a fresh machine the easiest path is to run that module directly before the first `/update`:

```bash
cd ~/src/ai
.venv/bin/python -c "from scripts.update.zshenv_sync import sync_zshenv; r = sync_zshenv(); print(r)"
```

That:
- Seeds `~/Desktop/Valor/zshenv.sh` with a default loader if missing (only the very first machine ever does this — subsequent machines inherit the file via iCloud).
- Appends a `[ -f ~/Desktop/Valor/zshenv.sh ] && source ...` guard to `~/.zshenv` if missing.

After it runs, open a fresh shell and confirm a shared secret is loaded (e.g., `echo "${SENTRY_PERSONAL_TOKEN:+set}"` — should print `set` if the vault `.env` defines it). If the vault hasn't synced yet, the guard line is still safe (it's `[ -f ]`-gated) and will activate as soon as iCloud lands the file.

If you need to add new cross-machine shell config later (PATH tweaks shared across all Valor machines, shell functions, etc.), edit `~/Desktop/Valor/zshenv.sh` directly — it syncs everywhere automatically. Keep host-specific config in the local `~/.zshenv` or `~/.zshrc`.

## Step 1: Install uv Package Manager

We use `uv` for fast, reliable Python package management (much faster than pip).

```bash
# Check if uv is already installed
if ! command -v uv &> /dev/null; then
  echo "Installing uv package manager..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

# Verify installation
uv --version
```

## Step 2: Virtual Environment & Dependencies

```bash
cd ~/src/ai

# Create virtual environment with uv (auto-creates with pip support)
uv venv

# Sync all dependencies including dev tools from pyproject.toml
uv sync --all-extras

# Install package in editable mode (registers CLI tools)
uv pip install -e .
```

This will:
- Create `.venv/` with Python 3.12 (or latest)
- Install all dependencies including:
  - Telegram bridge (telethon, httpx)
  - Claude SDK integration (anthropic, claude-agent-sdk)
  - Google Calendar (google-auth-oauthlib, google-api-python-client)
  - Job queue (popoto, redis)
  - Summarization (ollama)
  - Dev tools (pytest, ruff, mypy)
- Register CLI tools (`valor-calendar`, `valor-telegram`)

Verify key imports work:

```bash
.venv/bin/python -c "import telethon; import httpx; import dotenv; import anthropic; import google_auth_oauthlib; print('Dependencies OK')"
```

If this fails, debug before continuing.

## Step 3: Environment File (.env)

Check if `.env` exists. If not:

```bash
cp .env.example .env
```

**Ask the user** which project(s) this machine should monitor. The available projects are defined in `~/Desktop/Valor/projects.json` -- check the full list there. Common options:
- Single project: `ACTIVE_PROJECTS=psyoptimal`
- Multiple: `ACTIVE_PROJECTS=valor,popoto`
- All: `ACTIVE_PROJECTS=valor,django-project-template,popoto,psyoptimal,flutter-project-template,cuttlefish,yudame-research`

Edit `.env` and ensure these are set:

| Variable | Required | Notes |
|----------|----------|-------|
| `ACTIVE_PROJECTS` | Yes | Comma-separated project keys |
| `ANTHROPIC_API_KEY` | Yes | Starts with `sk-ant-` |
| `TELEGRAM_API_ID` | Yes | Numeric, from my.telegram.org |
| `TELEGRAM_API_HASH` | Yes | Hex string, from my.telegram.org |
| `TELEGRAM_PHONE` | Yes | With country code, e.g. `+1234567890` |
| `TELEGRAM_PASSWORD` | If 2FA on | Telegram 2FA password |
| `TELEGRAM_SESSION_NAME` | No | Defaults to `valor_bridge` |

If any required values are placeholder/missing, ask the user to provide them. The shared API keys file at `~/src/.env` may have `ANTHROPIC_API_KEY` and other keys -- check there first.

## Troubleshooting

### uv not found after install
```bash
export PATH="$HOME/.local/bin:$PATH"
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
```

### Dependencies won't install
```bash
rm -rf .venv
uv venv
uv sync --all-extras
```
