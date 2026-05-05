# Sub-Skill: Screenshot Capture

Mechanical work: start the app and capture screenshots of UI changes.

## Surface decision

Updated in #1274. Inherited from `do-pr-review/SKILL.md` — see the parent
skill's "Surface decision" section for the full allowlist and rationale.
TL;DR:

- **Allowlisted public host** (localhost, `*.vercel.app`, `*.netlify.app`,
  `*.pages.dev`, `*.fly.dev`, `*.railway.app`, `github.com` and similar
  known-public hosts) → use `agent-browser` (anonymous, headless).
- **Anything else** (authenticated dashboards, internal staging,
  SSO-protected URLs, unknown hosts) → use the BYOB MCP tools
  (`mcp__byob__*`, real Chrome, logged-in). Default-route unknown
  hosts to BYOB to close the public-URL-302s-to-login TOCTOU window.

The examples below are written for the `agent-browser` (allowlisted)
path. For BYOB, swap each command with its `mcp__byob__<verb>` analog.

## Context Variables

- `$SDLC_PR_NUMBER` — PR number for screenshot directory naming

## Prerequisites

PR branch must already be checked out (via checkout sub-skill).

For BYOB-routed URLs, the calling session must have
`requires_real_chrome=True` (set by bridge inference for SDLC runs, or
the `--needs-real-chrome` CLI flag for manual runs). Verify the BYOB
extension is connected before screenshotting:

```text
mcp__byob__list_tabs       # must return at least one Chrome tab
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

For an allowlisted (public) host:

```bash
agent-browser open http://localhost:8000
agent-browser snapshot -i
agent-browser screenshot generated_images/pr-${PR_NUMBER}/01_main_view.png
```

For a non-allowlisted (logged-in / internal) host, use BYOB instead:

```text
mcp__byob__navigate https://staging.example.internal/path
mcp__byob__screenshot   # save the returned image to
                        # generated_images/pr-${PR_NUMBER}/01_main_view.png
```

**Screenshot naming convention:**
- `01_main_view.png` — Primary affected view
- `02_feature_demo.png` — New feature in action
- `03_edge_case.png` — Edge case or error state

### 5. Cleanup

For the `agent-browser` path, close the browser, then stop the dev
server if started:

```bash
agent-browser close
```

For the BYOB path, no `close` step is needed — BYOB drives the user's
real Chrome and does not own a daemon process.

## Completion

Report the number of screenshots captured and their paths.
