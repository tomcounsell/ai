# Config Architecture

Unified configuration system that eliminates hardcoded paths, scattered env vars, and duplicate config files. All runtime configuration flows through `config/settings.py` (Pydantic Settings) and `config/paths.py` (path constants).

## Design

### Single Source of Truth

```
.env (secrets, overrides)
  |
config/settings.py (Pydantic Settings model)
  |
Application code: from config.settings import settings
```

All code imports `settings` from `config.settings` or path constants from `config/paths.py`. No hardcoded absolute paths, no inline env var reads for config values.

### Path Resolution

`config/paths.py` derives all paths from `Path(__file__)` -- no hardcoded usernames:

```python
from config.paths import PROJECT_ROOT, DATA_DIR, CONFIG_DIR, VALOR_DIR, LOGS_DIR, SRC_DIR
```

| Constant | Value | Description |
|----------|-------|-------------|
| `PROJECT_ROOT` | `Path(__file__).resolve().parent.parent` | Repository root |
| `DATA_DIR` | `PROJECT_ROOT / "data"` | Runtime state directory |
| `LOGS_DIR` | `PROJECT_ROOT / "logs"` | Log files |
| `CONFIG_DIR` | `PROJECT_ROOT / "config"` | Configuration files |
| `VALOR_DIR` | `Path.home() / "Desktop" / "Valor"` | Google auth, DM whitelist, calendar config |
| `HOME_DIR` | `Path.home()` | User home directory |
| `SRC_DIR` | `HOME_DIR / "src"` | Source code root |

### Settings Model

`config/settings.py` uses Pydantic Settings with nested models:

| Section | Class | Key Fields | Env Vars |
|---------|-------|------------|----------|
| Telegram | `TelegramSettings` | `session_name` (default: `valor_bridge`) | `TELEGRAM_SESSION_NAME` |
| Redis | `RedisSettings` | `url` (default: `redis://localhost:6379/0`) | `REDIS_URL` |
| Google Auth | `GoogleAuthSettings` | `credentials_dir` (default: `~/Desktop/Valor/`) | `GOOGLE_CREDENTIALS_DIR` |
| Models | `ModelSettings` | `ollama_vision_model` (default: `llama3.2-vision:11b`) | `OLLAMA_VISION_MODEL` |
| Paths | `PathSettings` | `project_root`, `data_dir`, `logs_dir`, `config_dir` | -- |
| API | `APISettings` | `claude_api_key`, `openai_api_key`, etc. | `CLAUDE_API_KEY`, etc. |

### Config Files

| File | Purpose | Source-Controlled |
|------|---------|-------------------|
| `.env` | Symlink → `~/Desktop/Valor/.env` (secrets) | No (gitignored) |
| `~/Desktop/Valor/.env` | **Secrets source of truth** (iCloud-synced, machine-private) | No (external vault) |
| `.env.example` | Documents the contents of `~/Desktop/Valor/.env` | Yes |
| `config/settings.py` | Pydantic Settings model (single source of truth for config schema) | Yes |
| `config/paths.py` | Path constants derived from `__file__` | Yes |
| `~/Desktop/Valor/projects.json` | Per-project config (working dirs, GitHub orgs, Telegram groups) | No (external, iCloud-synced) |
| `config/projects.example.json` | Template for projects.json | Yes |
| `config/models.py` | Model name constants | Yes |
| `config/personas/_base.md` | Shared persona base (identity, values, tools, philosophy) | Yes |
| `config/personas/{persona}.md` | Per-persona overlays (developer, project-manager, teammate) | Yes |
| `~/Desktop/Valor/` | Google OAuth tokens, DM whitelist, calendar config, `.env` vault | No (machine-local) |

### Secrets

All secrets live in **`~/Desktop/Valor/.env`** — the iCloud-synced vault. The repository `.env` is a symlink to this file:

```
~/src/ai/.env → ~/Desktop/Valor/.env
```

**Why this design:**
- `~/Desktop/Valor/` is iCloud-synced: secrets are available on every machine after sign-in, without any manual copy step
- The symlink means all existing `load_dotenv` and pydantic-settings `env_file=".env"` calls work unchanged — they resolve through the symlink transparently
- `.gitignore` covers `.env` by name, so the symlink entry is never committed regardless of what it points to

**Adding a new secret:**
1. Add the key/value to `~/Desktop/Valor/.env`
2. Add a documented placeholder to `.env.example`
3. Add the corresponding field to `config/settings.py`

Never write secrets directly to `repo/.env`. Writing to the symlink writes to the vault, but adding secrets to `~/Desktop/Valor/.env` directly is the canonical workflow and avoids confusion.

**On a fresh machine:** `scripts/remote-update.sh` and `scripts/update/env_sync.py` create the symlink automatically once iCloud has synced the vault file. Until then, a clear warning is logged.

### Credentials Location

All Google auth credentials and calendar config live in `~/Desktop/Valor/`:

| File | Purpose |
|------|---------|
| `~/Desktop/Valor/google_credentials.json` | OAuth client credentials (from Google Cloud Console) |
| `~/Desktop/Valor/google_token.<machine>.json` | Per-machine OAuth token (auto-generated) |
| `~/Desktop/Valor/calendar_config.json` | Calendar project-to-ID mapping |

Note: The DM whitelist is stored in the `dms.whitelist` array within `projects.json`, not as a separate file.

Override with `GOOGLE_CREDENTIALS_DIR` env var if needed.

## Adding New Config

1. Add a field to the appropriate settings class in `config/settings.py`
2. Set a sensible default value
3. Add the corresponding env var to `.env.example` with a description
4. Import via `from config.settings import settings` and access as `settings.section.field`
5. Never hardcode the value inline -- always go through settings

## Related

- `data/README.md` -- Runtime state directory contents and cleanup policy
- `.env.example` -- Complete env var reference
- `config/projects.example.json` -- Per-project config template
