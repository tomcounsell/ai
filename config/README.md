# Configuration Directory

This directory contains configuration files for the Valor AI system.

## Files

### `projects.example.json` (Template)
Canonical template with complete field documentation. Copy this to create your projects.json:

```bash
mkdir -p ~/Desktop/Valor
cp config/projects.example.json ~/Desktop/Valor/projects.json
# Edit ~/Desktop/Valor/projects.json with your settings
```

### `personas/_base.md` (Shared identity)
Base persona file that gets prepended to all persona overlays. Contains shared identity, values, communication style, and philosophy. This stays in the repo because it is not private.

Persona overlay files (developer.md, project-manager.md, teammate.md) live in `~/Desktop/Valor/personas/` (iCloud-synced, private).

### `secrets/` (Git-ignored)
Directory for sensitive credentials (Google OAuth tokens, etc.). Created automatically by the settings system.

### `SOUL.md`
Legacy persona definition (fallback when persona system files are missing).

## Private Configuration (~/Desktop/Valor/)

The following files live outside the repo in `~/Desktop/Valor/` (iCloud-synced):

| File | Purpose |
|------|---------|
| `projects.json` | Project configs, chat IDs, machine names |
| `personas/developer.md` | Developer persona overlay |
| `personas/project-manager.md` | PM persona overlay |
| `personas/teammate.md` | Teammate persona overlay |

Override the projects.json path with the `PROJECTS_CONFIG_PATH` env var.

## Required Fields

Every project configuration MUST include:

- **`working_directory`**: Path to the project directory where the agent operates
  - Example: `"~/src/popoto"` (tilde is expanded at runtime)
  - This is where the agent will run commands, read/write files, and execute work
  - Missing this field will cause Path(None) errors

## Setup Steps

1. **Copy the example file**:
   ```bash
   mkdir -p ~/Desktop/Valor
   cp config/projects.example.json ~/Desktop/Valor/projects.json
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

4. **Create persona overlays**:
   ```bash
   mkdir -p ~/Desktop/Valor/personas
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

**Fix**: Add `working_directory` to the project or set a default in `~/Desktop/Valor/projects.json`.

### Configuration not taking effect

Restart the bridge after editing `projects.json`:
```bash
./scripts/valor-service.sh restart
```

## Example Configuration

See `projects.example.json` for a complete, documented example with all available fields.
