---
name: setup
description: "Use when configuring a new machine to run the Valor Telegram bridge. Installs all dependencies, authentication, and service startup. Triggered by 'setup', 'configure this machine', or 'new machine setup'."
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

### Ensure bare `python` resolves to Python 3.12+

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

## Step 0: Vault Location (`VALOR_VAULT_DIR`)

Before anything else, establish where Valor's secrets vault lives on this machine. The vault holds the canonical `.env`, `projects.json`, identity overlays, persona overlays, Google credentials, and reflections registry. Every later step reads from it.

**Security note**: Vaults under `~/Desktop`, `~/Documents`, or `~/iCloud Drive` are TCC-restricted on macOS — launchd-spawned processes can't read files there at runtime. The install scripts work around this by baking the entire `.env` (including API keys) into each launchd plist's `EnvironmentVariables` dict and `chmod 0600`-ing the plist. Vaults under `~/.valor` or any other non-TCC path keep secrets in `<vault>/.env` only. The picker labels each option with its security posture so the user can pick informed.

Resolution cascade (in order):

1. **`VALOR_VAULT_DIR` already set in the user's shell env or invocation**: use it as-is, skip the picker.
2. **User passed a `--vault-dir <path>` directive in their `/setup` invocation**: use that path, skip the picker.
3. **`~/.valor/.env` exists** (machine already configured to the preferred default): use `~/.valor` and skip the picker.
4. **`~/Desktop/Valor/.env` exists** (machine on the legacy default): use `~/Desktop/Valor` and skip the picker.
5. **Nothing pre-set**: prompt the user via the harness-aware picker (next sub-step).

### 0.1 Detect pre-set vault location

```bash
if [ -n "${VALOR_VAULT_DIR:-}" ]; then
  echo "VALOR_VAULT_DIR already set: $VALOR_VAULT_DIR — skipping picker"
elif [ -f "$HOME/.valor/.env" ]; then
  export VALOR_VAULT_DIR="$HOME/.valor"
  echo "Existing vault found at $VALOR_VAULT_DIR (preferred default) — using it"
elif [ -f "$HOME/Desktop/Valor/.env" ]; then
  export VALOR_VAULT_DIR="$HOME/Desktop/Valor"
  echo "Existing vault found at $VALOR_VAULT_DIR (legacy default) — using it"
fi
```

If none matched, continue to 0.2.

### 0.2 Run the vault-location picker

Invoke the harness-aware shim. In Claude Code, it emits a JSON instruction on stdout and exits 78; in a TTY, it prompts inline and prints the chosen path.

```bash
cd ~/src/ai
VALOR_HARNESS=claude-code .venv/bin/python -m tools.install.prompt vault-picker > /tmp/vault-picker.json
echo "exit=$?"
```

Read `/tmp/vault-picker.json` and call **AskUserQuestion** using its fields:

- `question` → the question text
- `header` → the question header (max 12 chars)
- For each `options[i]`: pass `label`, `description` (becomes the option's description), and remember the `value` so you can map the chosen label back to the value to use.

If the user picks **Custom path…** (value `__custom__`), run a second helper invocation to gather the free-form path, then pass that JSON into a follow-up AskUserQuestion (use a single-question form with no preset options — the user types one):

```bash
VALOR_HARNESS=claude-code .venv/bin/python -c "
from tools.install.prompt import ask_input, InstallPromptDeferred
try:
    ask_input('Enter the absolute path for your Valor vault directory', header='Vault path')
except InstallPromptDeferred:
    pass
" > /tmp/vault-picker-custom.json
```

The follow-up JSON has `kind: ask_input` — render it as a free-text question and capture the user's typed path.

### 0.3 Validate and set `VALOR_VAULT_DIR`

```bash
CHOSEN_PATH="<the-path-the-user-picked>"
.venv/bin/python -c "
import sys
from pathlib import Path
from config.settings import VaultPathInvalid, VaultSettings
p = Path('$CHOSEN_PATH').expanduser()
try:
    VaultSettings._validate_dir(p)
except VaultPathInvalid as e:
    print(f'ERROR: {e}', file=sys.stderr); sys.exit(1)
" || { echo "Vault path rejected (in-repo or ephemeral root). Re-run /setup."; exit 1; }

export VALOR_VAULT_DIR="$(.venv/bin/python -c "from pathlib import Path; print(Path('$CHOSEN_PATH').expanduser())")"
mkdir -p "$VALOR_VAULT_DIR"
```

### 0.4 Persist `VALOR_VAULT_DIR` for future sessions

Write it into the new vault's `.env` so that subsequent processes (the bridge, worker, calendar hooks) pick it up without relying on the user's shell rc:

```bash
touch "$VALOR_VAULT_DIR/.env"
grep -q '^VALOR_VAULT_DIR=' "$VALOR_VAULT_DIR/.env" \
  || echo "VALOR_VAULT_DIR=$VALOR_VAULT_DIR" >> "$VALOR_VAULT_DIR/.env"
```

### 0.5 Repoint the repo `.env` symlink

The repo's `.env` is a symlink to `$VALOR_VAULT_DIR/.env`. If the user picked a non-default location, repoint it:

```bash
cd ~/src/ai
rm -f .env
ln -s "$VALOR_VAULT_DIR/.env" .env
```

After Step 0 completes, `$VALOR_VAULT_DIR` is exported for this shell, persisted in the vault `.env`, and the repo `.env` symlink points at the chosen location. All later steps use `${VALOR_VAULT_DIR}` instead of a hardcoded vault path.

### 0.6 Bootstrap cross-machine shell env loader

Cross-machine secrets and shell config live in the iCloud-synced vault (legacy default `~/Desktop/Valor/`). `~/.zshenv` itself does NOT sync (it's in `$HOME`), so each new machine needs a one-line bootstrap that sources the vault loader. The update script self-heals this on every run via `scripts/update/zshenv_sync.py`, but on a fresh machine the easiest path is to run that module directly before the first `/update`:

```bash
cd ~/src/ai
.venv/bin/python -c "from scripts.update.zshenv_sync import sync_zshenv; r = sync_zshenv(); print(r)"
```

That:
- Seeds the vault's `zshenv.sh` with a default loader if missing (only the very first machine ever does this — subsequent machines inherit the file via iCloud).
- Appends a `[ -f <vault>/zshenv.sh ] && source ...` guard to `~/.zshenv` if missing.

After it runs, open a fresh shell and confirm a shared secret is loaded (e.g., `echo "${SENTRY_PERSONAL_TOKEN:+set}"` — should print `set` if the vault `.env` defines it). If the vault hasn't synced yet, the guard line is still safe (it's `[ -f ]`-gated) and will activate as soon as iCloud lands the file.

If you need to add new cross-machine shell config later (PATH tweaks shared across all Valor machines, shell functions, etc.), edit the vault's `zshenv.sh` directly — it syncs everywhere automatically. Keep host-specific config in the local `~/.zshenv` or `~/.zshrc`.

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

**Ask the user** which project(s) this machine should monitor. The available projects are defined in `${VALOR_VAULT_DIR}/projects.json` -- check the full list there. Common options:
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

## Step 4: Google Calendar Configuration

Set up Google Calendar integration for work time tracking.

### 4.1 Get Google OAuth credentials

Check if credentials exist:

```bash
ls "${VALOR_VAULT_DIR}/google_credentials.json"
```

If missing, ask the user to:
1. Go to Google Cloud Console (project: Yudame General)
2. Enable Google Calendar API
3. Create OAuth 2.0 Client ID (Desktop app)
4. Download JSON and save to `${VALOR_VAULT_DIR}/google_credentials.json`

### 4.2 Run OAuth consent flow

Check if token already exists:

```bash
ls "${VALOR_VAULT_DIR}/google_token.json" 2>/dev/null
```

If no token exists, run the OAuth flow:

```bash
cd ~/src/ai

# This will open browser for Google OAuth consent
.venv/bin/valor-calendar --reauth
```

The user must complete the OAuth consent in their browser. After completion, verify the token is valid:

```bash
.venv/bin/valor-calendar --check
```

### 4.3 Create calendar mappings

The calendar config is auto-generated by the `/update` command. For now, ensure the Google Calendars exist with matching names:

**Required calendars in Google Calendar:**
- **"Internal Projects"** (default for Claude Code sessions and internal projects)
- Optional: Create dedicated calendars for client projects that need separate time tracking
  - Example: For "Dev: PsyOPTIMAL" group -> create "PsyOPTIMAL" calendar
  - Internal projects (valor, popoto, flutter-template, etc.) use the default calendar

After setup, run `/update` to auto-generate `config/calendar_config.json`.

## Step 5: Authentication Configuration

The SDK uses Max subscription OAuth via the Claude Desktop app (no API credits needed).

```bash
# Check if Claude Desktop app is running (provides OAuth for CLI)
if pgrep -f "Claude.app" > /dev/null; then
  echo "Claude Desktop is running (provides subscription auth)"
else
  echo "Claude Desktop is not running"
  echo "Start /Applications/Claude.app to enable subscription auth"
  echo "Without it, the bridge will fall back to API key billing"
fi

# Verify API key exists as fallback
if grep -q 'ANTHROPIC_API_KEY=sk-ant-' .env 2>/dev/null; then
  echo "API key configured (fallback if Desktop auth fails)"
else
  echo "No API key fallback configured"
fi
```

**How authentication works:**
- **Primary**: Claude Desktop app (if running) provides OAuth authentication
- **Fallback**: API key from `.env` (`ANTHROPIC_API_KEY`)
- **Force API billing**: Set `USE_API_BILLING=true` in `.env`

The SDK spawns Claude Code CLI subprocesses that inherit authentication from the running Claude Desktop app. No separate login command is needed.

### Sentry CLI Authentication

`sentry-cli` is installed automatically by `/update`. After installation, authenticate:

```bash
# Login to Sentry (generates auth token)
sentry-cli login

# Or set token directly in ${VALOR_VAULT_DIR}/.env
# SENTRY_PERSONAL_TOKEN=sntrys_...
# The SDK automatically injects this as SENTRY_AUTH_TOKEN for Eng (and Teammate) sessions
```

The token is stored in `${VALOR_VAULT_DIR}/.env` as `SENTRY_PERSONAL_TOKEN` and auto-injected into agent sessions by `sdk_client.py`.

## Step 6: Project Configuration (`${VALOR_VAULT_DIR}/projects.json`)

Project configuration lives in `${VALOR_VAULT_DIR}/projects.json` (typically iCloud-synced when the vault sits on iCloud-managed paths; private). The contents may be shared across machines via whatever sync mechanism the user picked.

Check if it exists. If not, create from the repo example:

```bash
mkdir -p "${VALOR_VAULT_DIR}"
cp config/projects.example.json "${VALOR_VAULT_DIR}/projects.json"
```

Edit `${VALOR_VAULT_DIR}/projects.json` for this machine's projects.

**Critical rules when editing projects.json:**

1. **Every project MUST have `working_directory`** -- absolute path to the repo on this machine
2. **Every project MUST have `machine`** -- the exact `ComputerName` of the single machine that owns it (`scutil --get ComputerName`). This is the source of truth for ownership; whitelists, groups, and email patterns all inherit from it. Two projects on different machines must never share a Telegram group, email contact, or DM whitelist contact id — see [Single-Machine Ownership](../../../docs/features/single-machine-ownership.md).
3. **Always include the full `defaults` section** -- copy it from the example if missing
4. **DO NOT set `respond_to_all: false`** -- the default is `true`, which is correct. Omit the field entirely from project-level telegram config.
5. **Keep project telegram config minimal** -- usually just `"groups": {"Eng: ProjectName": {"persona": "engineer"}}` is sufficient
6. **Verify paths exist on disk** -- run `ls` on each `working_directory` to confirm

**No per-contact ownership edits.** When adding this machine, you do not edit `dms.whitelist`, individual `telegram.groups` entries, or `email.contacts/domains` to "exclude" other machines. Just set each project's `machine` field once. The validator (`bridge/config_validation.py`) and the update gate (`scripts/update/run.py` Step 4.6) will enforce that no contact is owned by two machines.

Example minimal project entry:

```json
{
  "projects": {
    "myproject": {
      "name": "My Project",
      "working_directory": "~/src/myproject",
      "telegram": {
        "groups": {
          "Eng: My Project": {"persona": "engineer"}
        }
      },
      "github": {
        "org": "orgname",
        "repo": "reponame"
      },
      "context": {
        "tech_stack": ["Python"],
        "description": "What the agent should focus on"
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

### Persona overlays

Persona overlay files live in `${VALOR_VAULT_DIR}/personas/`. The loader (`agent.sdk_client.load_persona_prompt`) prefers the private overlay when present and falls back to the in-repo template (`config/personas/<persona>.md`) otherwise. Seeding the private overlays from the in-repo defaults at setup time gives the agent identical behavior on every fresh machine without waiting for cross-machine sync.

The engineer and customer-service personas have in-repo templates that are version-controlled and PR-reviewable:
- `config/personas/engineer.md` — Engineer SDLC-owner playbook (CRITIQUE/REVIEW gates, Mode 3 parallel orchestrator, `merge_authorized` bypass)
- `config/personas/customer-service.md` — Customer-service overlay for `customer-service`-persona sessions

Seed them into the vault if not already present (do NOT overwrite — existing overlays may carry per-machine customizations):

```bash
mkdir -p "${VALOR_VAULT_DIR}/personas"

for persona in engineer customer-service; do
  src="config/personas/${persona}.md"
  dst="${VALOR_VAULT_DIR}/personas/${persona}.md"
  if [ ! -f "$dst" ]; then
    cp "$src" "$dst"
    echo "Seeded $dst from $src"
  else
    echo "$dst already exists — leaving in place (run \`diff\` to compare with $src)"
  fi
done
```

The `teammate` persona has no in-repo template — there is no `config/personas/teammate.md`. A teammate overlay is purely operator-authored under `${VALOR_VAULT_DIR}/personas/teammate.md`; if absent, the loader has no fallback for that persona, so a teammate-using machine must author its own overlay.

If the machine is already running and you want to inspect drift between the in-repo template and the private overlay:

```bash
diff config/personas/engineer.md "${VALOR_VAULT_DIR}/personas/engineer.md"
diff config/personas/customer-service.md "${VALOR_VAULT_DIR}/personas/customer-service.md"
```

The persona loader emits a WARNING log line if a known load-bearing substring is missing from the private engineer overlay (e.g., `CRITIQUE` for the pipeline gate, `Mode 3` for the parallel orchestrator, `merge_authorized` for the stale-baseline bypass). The `/update` script also runs an engineer-overlay drift check (`scripts/update/persona_drift.py`, Step 4.10). Watch `logs/bridge.log` after the first session for these warnings — they signal that the private overlay has rolled back and should be re-synced.

### Cross-machine reuse

If the project is already defined on another machine's `projects.json`, copy its entry rather than writing from scratch (whatever sync mechanism the user picked — iCloud for vaults under `~/Desktop`/`~/Documents`, manual copy for `~/.valor`, etc. — determines how it gets there).

After editing, verify all working directories exist:

```bash
# For each project's working_directory, confirm it exists
ls ~/src/<project_dir>
```

## Step 7: Telegram Login (USER ACTION REQUIRED)

Check for an existing session:

```bash
ls data/*.session 2>/dev/null
```

**If a session file exists**: Skip to Step 8.

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

## Step 8: Install Reflections Scheduler

Install the reflections daily maintenance plist (runs at 6 AM Pacific):

```bash
cd ~/src/ai
./scripts/install_reflections.sh
```

Verify it loaded:

```bash
launchctl list | grep com.valor.reflections
```

If the output shows the `com.valor.reflections` label, the scheduler is installed. It will run `scripts/reflections.py` daily at 6 AM, performing log review, session analysis, LLM reflection, and memory consolidation.

## Step 8.5: Optional BYOB + Computer-Use Install (macOS only)

These two surfaces are operator-opt-in. Skip on non-macOS hosts.

### BYOB (real-Chrome control)

BYOB lets the agent read and act on the user's already-logged-in Chrome via MCP tools (`byob_navigate`, `byob_click`, etc.) -- no `state.json` files in the repo, no per-session re-auth.

```bash
# 1. Install bun if not already present
command -v bun >/dev/null || curl -fsSL https://bun.sh/install | bash

# 2. Clone BYOB to ~/.byob/ and check out the pinned commit
PIN=$(python3 -c "import json; print(json.load(open('config/byob_pin.json'))['commit'])")
if [ ! -d ~/.byob ]; then
  git clone https://github.com/wxtsky/byob ~/.byob
fi
git -C ~/.byob fetch
git -C ~/.byob checkout "$PIN"

# 3. Build + register the native messaging host
cd ~/.byob && bun install && bun run setup
cd ~/src/ai

# 4. Register the BYOB MCP server in ~/.claude.json (idempotent, self-healing)
python -c "from scripts.update import mcp_byob; r = mcp_byob.verify_byob_mcp(write=True); print(r.message)"
```

After install, the user must:
1. Open Chrome → `chrome://extensions` → toggle **Developer mode** ON (top-right) → click **Load unpacked** (top-left) → select `~/.byob/packages/extension/output/chrome-mv3/` (the BYOB extension cannot be auto-installed; this is an operator click-through).
2. **Quit Chrome completely** (`⌘Q` on macOS — closing windows is not enough). Reopen Chrome. Chrome only re-reads the Native Messaging config on full restart.

Verify with BYOB's own diagnostic — this is authoritative across BYOB versions and tells you exactly what's wrong if anything's off:

```bash
cd ~/.byob && bun run doctor
```

Expected output (all green checkmarks):
- ✓ Native Messaging manifest registered
- ✓ Launcher script present
- ✓ Bridge process: pid N, deviceId UUID, uptime Ns
- ✓ IPC socket: `~/.byob/bridges/<deviceId>.sock`

If any line is red, the message points at the exact fix. The most common case is "no live bridge — extension never connected" which means the user hasn't loaded the extension yet, or loaded it into a different Chrome profile than the one being tested.

Note: the IPC socket path is **per-device** (UUID-keyed under `~/.byob/bridges/`), not a fixed `~/.byob/run/byob.sock`. The MCP server discovers the socket at startup; callers should never hardcode the path.

### Computer-Use (bcu, native macOS app control)

bcu drives Slack, Notes, Telegram Desktop, etc. via the macOS Accessibility API without moving the user's cursor. **Prompt the user before installing**:

> Do you want to enable computer-use (lets the agent drive native macOS apps -- Slack, Notes, etc. -- without moving your cursor)?

On **yes**:
```bash
# Write the opt-in sentinel
mkdir -p ~/.config/valor && touch ~/.config/valor/computer-use-enabled

# Resolve the pinned bcu release
TAG=$(python3 -c "import json; print(json.load(open('config/bcu_pin.json'))['release_tag'])")

# Download + verify SHA + install -- /update handles this on every run too,
# so the SETUP-time fetch is just bootstrap. See scripts/update/run.py.
echo "bcu pinned tag: $TAG"
echo "Run: python scripts/update/run.py --full to fetch + install + permission-prompt."
```

After install, the user must grant **two** permissions in System Settings:
- Privacy & Security -> Accessibility -> add `BackgroundComputerUse.app`
- Privacy & Security -> Screen Recording -> add `BackgroundComputerUse.app`

These permissions cannot be granted programmatically.

On **no**: skip everything. Don't write the sentinel; `/update` will leave bcu alone.

## Step 8.6: Generation Model Selection (RAM-based)

Free-text generation (memory titles, the test AI judge, knowledge-doc
summarization) runs on a larger `gemma4:31b` model. Classification (bridge
routing, memory-audit, email triage) runs on the resident `granite4.1:3b` and
needs no choice here. Pick the generation variant from this machine's RAM:

- **RAM ≥ `MIN_LOCAL_GEN_RAM_GB` (48 GB)** → local Apple-Silicon MLX variant
  `gemma4:31b-mlx` (the ~18-20 GB MLX 32B coexists with granite + nomic-embed + OS).
- **RAM < 48 GB** → Ollama Cloud variant `gemma4:31b-cloud` (a lightweight hosted
  pointer that fits any machine, including a 16 GB host).

Write the choice to `~/.zshenv` — **machine-local**, NOT the vault
`${VALOR_VAULT_DIR}/.env` (an iCloud-synced vault `.env` would propagate one
machine's variant to every other machine and break per-machine semantics). The
write is grep-before-append idempotent:

```bash
RAM_GB=$(( $(sysctl -n hw.memsize) / 1024 / 1024 / 1024 ))
if [ "$RAM_GB" -ge 48 ]; then
  GEN_MODEL="gemma4:31b-mlx"
else
  GEN_MODEL="gemma4:31b-cloud"
fi
LINE="export MODELS__OLLAMA_GENERATION_MODEL=$GEN_MODEL"
grep -qxF "$LINE" ~/.zshenv 2>/dev/null || echo "$LINE" >> ~/.zshenv
echo "Generation model: $GEN_MODEL (RAM=${RAM_GB}GB)"
```

Then ensure the chosen tag (the RAM guard inside `ensure_generation_model()`
re-checks and degrades a misconfigured mlx tag to a soft warning — it never pulls
18 GB on a small host):

```bash
python -c "from config.models import ensure_generation_model; ok,d=ensure_generation_model('$GEN_MODEL'); print(('OK' if ok else 'WARN'), d)"
```

**Cloud-signin warning:** when `GEN_MODEL` ends in `:cloud`, the machine must be
signed in to Ollama Cloud (`ollama list` shows a `:cloud` entry). If not, warn the
user to run `ollama signin` — generation is fail-soft, so this does not block setup.

The launchd worker does not read the shell, so `scripts/install_worker.sh` parses
`MODELS__*` lines from `~/.zshenv` and injects them into the plist
`EnvironmentVariables` block — no extra action needed here.

## Step 9: Start the Bridge

Ensure the logs directory exists, then start the bridge as a background process:

```bash
mkdir -p logs
```

Start the bridge using the service script:

```bash
./scripts/valor-service.sh start
```

Wait a few seconds, then verify it started:

```bash
sleep 4 && tail -20 logs/bridge.log 2>/dev/null
```

Check for these indicators in the logs:
- `Agent backend: Claude Agent SDK` -- correct backend
- `Active projects: [...]` -- the projects you configured
- `Monitored groups: [...]` -- the Telegram groups
- `Connected to Telegram` -- successful connection

Also verify the process is running:

```bash
pgrep -f telegram_bridge.py
```

## Step 10: Final Verification

Run a comprehensive health check:

```bash
cd ~/src/ai

echo "=== System Tools ==="
claude --version
gh --version
git --version
uv --version

echo ""
echo "=== Python Environment ==="
.venv/bin/python --version
.venv/bin/python -c "import telethon; import anthropic; import google_auth_oauthlib; print('Dependencies OK')"

echo ""
echo "=== CLI Tools ==="
.venv/bin/valor-calendar --version 2>/dev/null || echo "valor-calendar: Not found (run 'uv pip install -e .' again)"
.venv/bin/python -m tools.sms_reader.cli recent --limit 1 | grep -q "rowid" && echo "SMS reader: OK" || echo "SMS reader: FAIL"

echo ""
echo "=== Bridge Status ==="
./scripts/valor-service.sh status
```

## Step 11: Confirm to User

Report the final status to the user with:

- **Bridge status**: Running (with PID)
- **Agent backend**: Claude Agent SDK
- **Active projects**: Which projects are configured
- **Monitored groups**: Which Telegram groups are being watched
- **Next steps**:
  1. Send a test message in the Telegram group to verify it responds
  2. Run `/update` to generate calendar config

## Rules

- **You** do everything: deps, config, starting the bridge, verification
- **User** only does:
  1. Interactive Telegram login (Step 7)
  2. Claude login (if not already done)
  3. Google OAuth consent (if no token exists)
- Never ask the user to start the bridge or check logs -- do it yourself
- Never set `respond_to_all: false` in project configs
- Always include the `defaults` section in `projects.json`
- Always verify `working_directory` paths exist on disk before starting
- The bridge must be confirmed running before you report success
- If anything fails, debug it yourself. Only escalate to the user if it requires their credentials or interactive input.

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

### Calendar OAuth fails
1. Verify credentials file exists: `ls "${VALOR_VAULT_DIR}/google_credentials.json"`
2. Ensure Google Calendar API is enabled in Cloud Console
3. Re-run OAuth: `.venv/bin/valor-calendar --reauth`

### Bridge won't start
1. Check logs: `tail -50 logs/bridge.log`
2. Verify Telegram session: `ls data/*.session`
3. Test imports: `.venv/bin/python -c "import telethon; print('OK')"`
