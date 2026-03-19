# Config Architecture

Unified configuration system that eliminates hardcoded paths, scattered env vars, and duplicate config files. All runtime configuration flows through `config/settings.py` (Pydantic Settings) and `config/paths.py` (path constants).

## Design

### Single Source of Truth

```
.env (secrets, overrides)
  ↓
config/settings.py (Pydantic Settings model)
  ↓
Application code: from config.settings import settings
```

All code imports `settings` from `config.settings` or path constants from `config/paths.py`. No hardcoded absolute paths, no inline env var reads for config values.

### Path Resolution

`config/paths.py` derives all paths from `Path(__file__)` -- no hardcoded usernames:

```python
from config.paths import PROJECT_ROOT, DATA_DIR, CONFIG_DIR, SECRETS_DIR, LOGS_DIR, SRC_DIR
```

| Constant | Value | Description |
|----------|-------|-------------|
| `PROJECT_ROOT` | `Path(__file__).resolve().parent.parent` | Repository root |
| `DATA_DIR` | `PROJECT_ROOT / "data"` | Runtime state directory |
| `LOGS_DIR` | `PROJECT_ROOT / "logs"` | Log files |
| `CONFIG_DIR` | `PROJECT_ROOT / "config"` | Configuration files |
| `SECRETS_DIR` | `CONFIG_DIR / "secrets"` | Google auth tokens, gitignored |
| `HOME_DIR` | `Path.home()` | User home directory |
| `SRC_DIR` | `HOME_DIR / "src"` | Source code root |

### Settings Model

`config/settings.py` uses Pydantic Settings with nested models:

| Section | Class | Key Fields | Env Vars |
|---------|-------|------------|----------|
| Telegram | `TelegramSettings` | `session_name` (default: `valor_bridge`) | `TELEGRAM_SESSION_NAME` |
| Redis | `RedisSettings` | `url` (default: `redis://localhost:6379/0`) | `REDIS_URL` |
| Google Auth | `GoogleAuthSettings` | `credentials_dir` (default: `config/secrets/`) | `GOOGLE_CREDENTIALS_DIR` |
| Models | `ModelSettings` | `ollama_vision_model` (default: `llama3.2-vision:11b`) | `OLLAMA_VISION_MODEL` |
| Paths | `PathSettings` | `project_root`, `data_dir`, `logs_dir`, `config_dir` | -- |
| Database | `DatabaseSettings` | `path`, `echo`, `pool_size` | -- |
| API | `APISettings` | `claude_api_key`, `openai_api_key`, etc. | `CLAUDE_API_KEY`, etc. |

### Config Files

| File | Purpose | Source-Controlled |
|------|---------|-------------------|
| `.env` | Secrets and environment overrides | No (gitignored) |
| `.env.example` | Template documenting all env vars | Yes |
| `config/settings.py` | Pydantic Settings model (single source of truth) | Yes |
| `config/paths.py` | Path constants derived from `__file__` | Yes |
| `config/projects.json` | Per-project config (working dirs, GitHub orgs, Telegram groups) | No (gitignored) |
| `config/projects.example.json` | Template for projects.json | Yes |
| `config/secrets/` | Google OAuth tokens, DM whitelist | No (gitignored) |
| `config/models.py` | Model name constants | Yes |

### Path Fallback Behavior

Google auth tokens live in `config/secrets/`. If tokens are not found there, the code checks `~/Desktop/claude_code/` as a secondary location. The DM whitelist follows the same pattern: `config/dm_whitelist.json` first, then `~/Desktop/claude_code/dm_whitelist.json`. The secondary path check is temporary (one release cycle).

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
