# Configuration Directory

This directory contains configuration files for the Valor AI system.

## Files

### `projects.json` (Required)
Main configuration file that defines which projects Valor monitors and how it responds to messages.

**IMPORTANT**: This file is git-ignored because it contains machine-specific paths and settings.

### `projects.json.example` (Template)
Template file with complete field documentation. Copy this to create your `projects.json`:

```bash
cp config/projects.json.example config/projects.json
# Edit config/projects.json with your settings
```

### `SOUL.md`
Valor's persona definition and system prompt.

## Required Fields

Every project configuration MUST include:

- **`working_directory`**: Absolute path to the project directory where the agent operates
  - Example: `"/Users/yourname/src/popoto"`
  - This is where the agent will run commands, read/write files, and execute work
  - Missing this field will cause Path(None) errors

## Setup Steps

1. **Copy the example file**:
   ```bash
   cp config/projects.json.example config/projects.json
   ```

2. **Edit working directories**:
   Update all `working_directory` fields with your actual paths:
   ```json
   {
     "projects": {
       "my-project": {
         "working_directory": "/Users/yourname/src/my-project",
         ...
       }
     },
     "defaults": {
       "working_directory": "/Users/yourname/src/ai"
     }
   }
   ```

3. **Configure Telegram groups**:
   Set which Telegram groups each project monitors:
   ```json
   "telegram": {
     "groups": ["Dev: My Project"],
     "respond_to_all": false,
     "respond_to_mentions": true
   }
   ```

4. **Set active projects** in `.env`:
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

**Fix**: Add `working_directory` to the project or set a default:
```json
{
  "projects": {
    "my-project": {
      "working_directory": "/absolute/path/to/project",
      ...
    }
  },
  "defaults": {
    "working_directory": "/fallback/path"
  }
}
```

### Configuration not taking effect

Restart the bridge after editing `projects.json`:
```bash
./scripts/valor-service.sh restart
```

## Example Configuration

See `projects.json.example` for a complete, documented example with all available fields.
