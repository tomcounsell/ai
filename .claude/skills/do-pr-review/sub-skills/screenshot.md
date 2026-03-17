# Sub-Skill: Screenshot Capture

Mechanical work: start the app and capture screenshots of UI changes.

## Context Variables

- `$SDLC_PR_NUMBER` — PR number for screenshot directory naming

## Prerequisites

PR branch must already be checked out (via checkout sub-skill).

## Steps

### 1. Check if Screenshots Are Needed

Examine the diff for UI-related files:
- `*.html`, `*.jsx`, `*.tsx`, `*.vue`, `*.css`, `*.scss`
- `*.py` files that contain template rendering

If no UI files changed, skip this sub-skill entirely.

### 2. Prepare Screenshot Directory

```bash
PR_NUMBER="${SDLC_PR_NUMBER}"
mkdir -p generated_images/pr-${PR_NUMBER}
```

### 3. Start the Application

Use `/prepare_app` or start the dev server directly:
```bash
# Example for Django projects:
python manage.py runserver --noreload &
sleep 3
```

### 4. Capture Screenshots

```bash
agent-browser open http://localhost:8000
agent-browser snapshot -i
agent-browser screenshot generated_images/pr-${PR_NUMBER}/01_main_view.png
```

**Screenshot naming convention:**
- `01_main_view.png` — Primary affected view
- `02_feature_demo.png` — New feature in action
- `03_edge_case.png` — Edge case or error state

### 5. Cleanup

Stop the dev server if started.

## Completion

Report the number of screenshots captured and their paths.
