# Configuration Directory

This directory contains configuration files for the Valor AI system.

The vault directory (where private configuration like `projects.json`, persona overlays, and `.env` live) is configurable per machine via the `VALOR_VAULT_DIR` environment variable. The established default is `~/Desktop/Valor/`. Run `/setup` for the interactive picker, or set `VALOR_VAULT_DIR` in your shell rc to pick a non-default location.

## Files

### `projects.example.json` (Template)
Canonical template with complete field documentation. Copy this to your vault to create your private projects.json:

```bash
VAULT="${VALOR_VAULT_DIR:-$HOME/Desktop/Valor}"
mkdir -p "$VAULT"
cp config/projects.example.json "$VAULT/projects.json"
# Edit $VAULT/projects.json with your settings
```

### `personas/segments/` (Composable identity)
Prompt segments assembled per `manifest.json` by `load_persona_prompt()`. Contains `identity.md` (shared identity and values), `work-patterns.md` (communication style), and `tools.md` (tool guidance). These stay in the repo because they are not private.

Persona overlay files (developer.md, project-manager.md, teammate.md) live in `<vault>/personas/` (private; default vault `~/Desktop/Valor/`).

### `secrets/` (Git-ignored)
Directory for sensitive credentials (Google OAuth tokens, etc.). Created automatically by the settings system.

### `identity.json`
Structured identity data (name, email, timezone, org). Per-instance overrides via `<vault>/identity.json`.

### `personas/segments/`
Composable prompt segments: `identity.md`, `work-patterns.md`, `tools.md`. Assembled by `load_persona_prompt()` per `manifest.json`.

## Private Configuration (in your vault)

The following files live outside the repo in `<vault>/` (default `~/Desktop/Valor/`):

| File | Purpose |
|------|---------|
| `projects.json` | Project configs, chat IDs, machine names |
| `personas/developer.md` | Developer persona overlay |
| `personas/project-manager.md` | PM persona overlay |
| `personas/teammate.md` | Teammate persona overlay |
| `.env` | Secrets (API keys, tokens) — symlinked from repo `.env` |
| `identity.json` (optional) | Per-instance identity override |
| `google_credentials.json` | Google OAuth client credentials |
| `google_token.<machine>.json` | Per-machine OAuth tokens |

Override the projects.json path with the `PROJECTS_CONFIG_PATH` env var. Override the vault location entirely with `VALOR_VAULT_DIR`.

## Required Fields

Every project configuration MUST include:

- **`working_directory`**: Path to the project directory where the agent operates
  - Example: `"~/src/popoto"` (tilde is expanded at runtime)
  - This is where the agent will run commands, read/write files, and execute work
  - Missing this field will cause Path(None) errors

## Setup Steps

1. **Copy the example file** into your vault:
   ```bash
   VAULT="${VALOR_VAULT_DIR:-$HOME/Desktop/Valor}"
   mkdir -p "$VAULT"
   cp config/projects.example.json "$VAULT/projects.json"
   ```

2. **Edit working directories**:
   Update all `working_directory` fields with your actual paths (tilde `~` is expanded at runtime):
   ```json
   {
     "projects": {
       "my-project": {
         "working_directory": "~/src/my-project",
         ...
       }
     },
     "defaults": {
       "working_directory": "~/src/ai"
     }
   }
   ```

3. **Configure Telegram groups**:
   Set which Telegram groups each project monitors:
   ```json
   "telegram": {
     "groups": {
       "Dev: My Project": {"persona": "developer"}
     }
   }
   ```

4. **Create persona overlays** in your vault:
   ```bash
   mkdir -p "${VALOR_VAULT_DIR:-$HOME/Desktop/Valor}/personas"
   # Create developer.md, project-manager.md, teammate.md
   ```

5. **Set active projects** in `.env`:
   ```bash
   ACTIVE_PROJECTS=project1,project2
   ```

## Validation

The bridge validates configuration on startup and will:
- **Warn** if `defaults.working_directory` is missing
- **Error** if a project is missing `working_directory` and no default is set
- **Fail gracefully** with a user-friendly message if configuration is invalid

Check logs for validation messages:
```bash
tail -f logs/bridge.log
```

## Troubleshooting

### "TypeError: argument should be a str or an os.PathLike object where __fspath__ returns a str, not 'NoneType'"

**Cause**: A project is missing the `working_directory` field.

**Fix**: Add `working_directory` to the project or set a default in `<vault>/projects.json` (default vault `~/Desktop/Valor/`; configurable via `VALOR_VAULT_DIR`).

### Configuration not taking effect

Restart the bridge after editing `projects.json`:
```bash
./scripts/valor-service.sh restart
```

## Example Configuration

See `projects.example.json` for a complete, documented example with all available fields.
