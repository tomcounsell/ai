# Zed Editor Setup for Django Projects

Migrating from PyCharm's run configurations and keybindings to Zed equivalents.

## Concepts

| PyCharm | Zed | Location |
|---------|-----|----------|
| Run Configurations | **Tasks** | `.zed/tasks.json` (project) or `~/.config/zed/tasks.json` (global) |
| Keymap | **Keymap** | `~/.config/zed/keymap.json` (global only) |
| Settings | **Settings** | `.zed/settings.json` (project) or `~/.config/zed/settings.json` (global) |
| Project interpreter | Language servers | Configured in settings under `"languages"` |

Key difference: PyCharm stores everything in `.idea/` per-project. Zed splits between `~/.config/zed/` (global) and `.zed/` (per-project).

## 1. Base Keymap

Use PyCharm/JetBrains keybindings as a starting point. In `~/.config/zed/settings.json`:

```json
{
  "base_keymap": "JetBrains"
}
```

This gives you most familiar shortcuts out of the box (cmd-shift-f for find in files, etc.).

## 2. Tasks (Run Configurations)

### File locations

- **Global tasks**: `~/.config/zed/tasks.json` — available in every project
- **Project tasks**: `.zed/tasks.json` in project root — only available in that project

### Task format

```json
[
  {
    "label": "Django Server",           // Name shown in task picker
    "command": "uv run python manage.py runserver",  // Shell command to run
    "env": {                            // Environment variables
      "DJANGO_SETTINGS_MODULE": "settings",
      "PYTHONUNBUFFERED": "1"
    },
    "use_new_terminal": true,           // true = own tab, false = reuse existing
    "allow_concurrent_runs": false,     // prevent duplicate instances
    "reveal": "always",                 // "always" | "no_focus" | "never"
    "hide": "never"                     // "never" | "always" | "on_success"
  }
]
```

### Variable substitution

Zed provides variables you can use in task commands:

- `$ZED_FILE` — absolute path to the currently open file
- `$ZED_FILENAME` — just the filename
- `$ZED_DIRNAME` — directory of the current file
- `$ZED_STEM` — filename without extension
- `$ZED_ROW` / `$ZED_COLUMN` — cursor position
- `$ZED_SELECTED_TEXT` — currently selected text
- `$ZED_WORKTREE_ROOT` — project root

Example: run tests on the current file:
```json
{
  "label": "Run Tests (current file)",
  "command": "DJANGO_SETTINGS_MODULE=settings uv run pytest -v $ZED_FILE"
}
```

### Typical Django project tasks

```json
[
  {
    "label": "Django Server",
    "command": "uv run python manage.py runserver",
    "env": { "DJANGO_SETTINGS_MODULE": "settings", "PYTHONUNBUFFERED": "1" },
    "use_new_terminal": true,
    "allow_concurrent_runs": false
  },
  {
    "label": "Tailwind Watch",
    "command": "uv run python manage.py tailwind watch",
    "env": { "DJANGO_SETTINGS_MODULE": "settings", "PYTHONUNBUFFERED": "1" },
    "use_new_terminal": true,
    "allow_concurrent_runs": false
  },
  {
    "label": "Run Tests",
    "command": "DJANGO_SETTINGS_MODULE=settings uv run pytest -v",
    "use_new_terminal": false
  },
  {
    "label": "Run Tests (current file)",
    "command": "DJANGO_SETTINGS_MODULE=settings uv run pytest -v $ZED_FILE",
    "use_new_terminal": false
  },
  {
    "label": "Lint Code",
    "command": "uv run pre-commit run --all-files",
    "use_new_terminal": false,
    "hide": "on_success"
  },
  {
    "label": "Django Shell",
    "command": "uv run python manage.py shell",
    "env": { "DJANGO_SETTINGS_MODULE": "settings", "PYTHONUNBUFFERED": "1" },
    "use_new_terminal": true
  },
  {
    "label": "Dev (Server + Tailwind)",
    "command": "uv run python manage.py runserver & uv run python manage.py tailwind watch; wait",
    "env": { "DJANGO_SETTINGS_MODULE": "settings", "PYTHONUNBUFFERED": "1" },
    "use_new_terminal": true
  }
]
```

## 3. Keybindings

Edit `~/.config/zed/keymap.json`. Format is an array of context blocks:

```json
[
  {
    "context": "Workspace",
    "bindings": {
      "key-combo": "action::Name"
    }
  }
]
```

### Task runner keybindings

The two key actions for running tasks:

| Action | What it does |
|--------|-------------|
| `task::Spawn` | Opens the task picker (choose which task to run) |
| `task::Rerun` | Re-runs the last task you ran |

Recommended bindings (mirrors PyCharm ctrl+r / ctrl+shift+r):

```json
{
  "context": "Workspace",
  "bindings": {
    "ctrl-r": "task::Rerun",
    "ctrl-shift-r": "task::Spawn",
    "alt-f12": "terminal_panel::ToggleFocus"
  }
}
```

### Other useful PyCharm-like bindings

```json
{
  "shift shift": "file_finder::Toggle",
  "cmd-1": "project_panel::ToggleFocus",
  "cmd-2": "git_panel::ToggleFocus"
}
```

## 4. Language Servers (Python)

In `~/.config/zed/settings.json` or `.zed/settings.json`:

```json
{
  "languages": {
    "Python": {
      "language_servers": ["pyright", "ruff"]
    }
  }
}
```

This gives you type checking (pyright) and linting/formatting (ruff) — equivalent to PyCharm's built-in inspections.

## 5. Workflow

1. `ctrl-shift-r` → pick "Django Server" → starts in a terminal tab
2. `ctrl-shift-r` → pick "Tailwind Watch" → starts in another tab
3. Edit code. `ctrl-r` re-runs whatever you last ran.
4. `alt-f12` toggles the terminal panel.

Or use the "Dev (Server + Tailwind)" compound task to start both at once.

## 6. Setup on a New Machine

1. Install Zed
2. Copy `~/.config/zed/settings.json` and `~/.config/zed/keymap.json` (global config)
3. Project tasks live in `.zed/tasks.json` — committed to the repo or copied manually
4. Install language servers: `uv add --dev pyright ruff` (Zed auto-detects them)

## Reference

- [Zed Tasks docs](https://zed.dev/docs/tasks)
- [Zed Key Bindings docs](https://zed.dev/docs/key-bindings)
- [Zed Language Server docs](https://zed.dev/docs/languages/python)
- Command palette: `cmd-shift-p` → type "tasks" to see all task-related actions
