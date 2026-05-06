# Sub-Skill: Screenshot Capture

Mechanical work: start the app and capture screenshots of UI changes.

## Surface

Screenshot capture runs against the user's real, logged-in Chrome via
BYOB MCP (`mcp__byob__browser_*`). Public preview deploys and
authenticated staging URLs are screenshotted the same way — there is
no anonymous-headless fallback (retired in #1256).

## Context Variables

- `$SDLC_PR_NUMBER` — PR number for screenshot directory naming

## Prerequisites

PR branch must already be checked out (via checkout sub-skill).

The calling session must have `requires_real_chrome=True` (set by
bridge inference for SDLC runs, or the `--needs-real-chrome` CLI flag
for manual runs). Verify the BYOB extension is connected before
screenshotting:

```text
mcp__byob__browser_list_tabs       # must return at least one Chrome tab
```

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

```text
mcp__byob__browser_navigate(url="http://localhost:8000", waitUntil="networkidle")
mcp__byob__browser_read(url="http://localhost:8000", reuseTab=true, screens=1)
mcp__byob__browser_screenshot(tabId=<tab>, savePath="generated_images/pr-${PR_NUMBER}/01_main_view.png")
```

**Screenshot naming convention:**
- `01_main_view.png` — Primary affected view
- `02_feature_demo.png` — New feature in action
- `03_edge_case.png` — Edge case or error state

### 5. Cleanup

No browser close step is needed — BYOB drives the user's real Chrome
and does not own a daemon process. Stop the dev server if you started
one.

## Completion

Report the number of screenshots captured and their paths.
