# Phase 1: Environment — Python, Shell, Dependencies, .env

Load this when starting a fresh-machine setup (Steps 0-3).

## Step 0.1: Ensure bare `python` resolves to Python 3.12+

Tooling that shells out to bare `python` runs under `/bin/sh`, which does not honor zsh aliases, and macOS ships only `python3`. Claude Code hooks are **not** affected — project hooks exec `.claude/hooks/hook_python` (which resolves the repo venv) and global hooks are generated with an absolute interpreter path — so a failure here does not silently disable validators. It still breaks any script or tool that invokes bare `python`.

Verify first, from **inside the repo** (cwd matters — see the pyenv case below):

```bash
cd ~/src/ai && /bin/sh -c 'python --version'   # expected: Python 3.12.x or newer
```

The fix depends on PATH order, so check that before symlinking anything:

```bash
/bin/sh -c 'echo $PATH' | tr ':' '\n' | head
```

**No pyenv (plain macOS).** Drop a symlink in a user-writable dir that is on PATH (no sudo):

```bash
ln -sf "$(command -v python3)" /opt/homebrew/bin/python
```

**pyenv present.** `~/.pyenv/shims` almost always precedes `/opt/homebrew/bin`, so the symlink above is shadowed by the shim and fixes nothing. The shim resolves through this repo's committed `.python-version`, and pyenv does **not** prefix-match: a pin of `3.14` needs a `versions/3.14` entry — installing `3.14.5` alone still fails with ``version `3.14' is not installed``. Give pyenv a matching version:

```bash
pyenv install "$(cat ~/src/ai/.python-version)".5   # exact patch; see `pyenv install --list`
ln -s 3.14.5 ~/.pyenv/versions/3.14                 # alias the pin to the build
pyenv rehash
```

If `pyenv install` reports `definition not found`, pyenv is too old for that patch release — `brew upgrade pyenv` refreshes the build definitions.

The update orchestrator (`scripts/update/run.py`) verifies this via `check_python_alias()` and warns if it fails.

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

## Step 1: Install uv

```bash
command -v uv >/dev/null || { curl -LsSf https://astral.sh/uv/install.sh | sh; export PATH="$HOME/.local/bin:$PATH"; }
uv --version
```

## Step 2: Virtual environment and dependencies

```bash
cd ~/src/ai
uv venv
uv sync --all-extras
uv pip install -e .    # editable install registers the valor-* CLIs
.venv/bin/python -c "import telethon, httpx, dotenv, anthropic, google_auth_oauthlib; print('Dependencies OK')"
```

Debug any failure here before continuing.

## Step 3: Secrets and `.env`

Secrets live in the iCloud-synced vault `~/Desktop/Valor/.env`; the repo `.env` is a symlink to it. `/update` (`scripts/update/env_sync.py`) replaces a regular-file `.env` with that symlink, so anything written into a repo-local `.env` is lost. Create the link:

```bash
cd ~/src/ai && ln -sfn ~/Desktop/Valor/.env .env
```

If the vault `.env` is missing (first machine ever, or iCloud not yet synced), wait for iCloud or seed the vault file from `.env.example`. Required values (ask the user for any that are placeholder or missing; write them to the vault file):

| Variable | Required | Notes |
|----------|----------|-------|
| `ANTHROPIC_API_KEY` | Yes | Starts with `sk-ant-` |
| `TELEGRAM_API_ID` | Yes | Numeric, from my.telegram.org |
| `TELEGRAM_API_HASH` | Yes | Hex string, from my.telegram.org |
| `TELEGRAM_PHONE` | Yes | With country code, e.g. `+1234567890` |
| `TELEGRAM_PASSWORD` | If 2FA on | Telegram 2FA password |

Which projects this machine serves comes from each project's `machine` field in `projects.json` (Phase 3), not from `.env`; `ACTIVE_PROJECTS` is only a fallback when no project names this machine. Ask the user which projects this machine should own.

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
