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

Examine the changed file list for UI-related extensions and patterns:
- `*.html`, `*.htm` — HTML templates
- `*.jsx`, `*.tsx` — React components
- `*.vue` — Vue single-file components
- `*.css`, `*.scss`, `*.sass`, `*.less` — Stylesheets
- `*.js`, `*.ts` — JavaScript/TypeScript (when the file is under a `ui/`, `frontend/`, `static/`, `assets/`, `templates/`, or `components/` directory)
- Django/Jinja template files (`.html` under `templates/`)
- Any file whose path contains `ui/`, `frontend/`, `static/`, `web/`, `assets/`, or `templates/`
- `*.py` files that contain template rendering

**Set the UI gate flag early:**

```bash
UI_FILES=$(gh pr diff $PR_NUMBER --name-only | grep -E '\.(html|htm|jsx|tsx|vue|css|scss|sass|less)$|/(ui|frontend|static|web|assets|templates)/' || true)
UI_CHANGES_DETECTED=false
SCREENSHOTS_CAPTURED=0

if [ -n "$UI_FILES" ]; then
  UI_CHANGES_DETECTED=true
  echo "UI files changed — visual proof required before approval:"
  echo "$UI_FILES"
fi
```

If no UI files changed, skip this sub-skill entirely — the visual proof gate is
a no-op (`SCREENSHOTS_CAPTURED=0` is fine and approval is not blocked).

After each successful screenshot in Step 4, increment the counter:
`SCREENSHOTS_CAPTURED=$((SCREENSHOTS_CAPTURED + 1))`.

### 2. Prepare Screenshot Directory

```bash
PR_NUMBER="${SDLC_PR_NUMBER}"
mkdir -p generated_images/pr-${PR_NUMBER}
```

### 3. Start the Application

Start the dev server using the repo's `## Running` README section:
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

## Visual Proof Gate (evaluated after capture, before posting any approval)

If `UI_CHANGES_DETECTED=true` AND `SCREENSHOTS_CAPTURED=0`, the review MUST NOT
approve. Set a gate failure flag:

```bash
VISUAL_PROOF_GATE_FAILED=false
if [ "$UI_CHANGES_DETECTED" = "true" ] && [ "$SCREENSHOTS_CAPTURED" -eq 0 ]; then
  VISUAL_PROOF_GATE_FAILED=true
  echo "VISUAL PROOF GATE FAILED: UI files were changed but no screenshots were captured."
  echo "This PR cannot be approved without visual proof of the UI changes."
  echo "Posting 'Request Changes' verdict with a note to capture screenshots."
fi
```

The post-review step consumes this flag: if `VISUAL_PROOF_GATE_FAILED=true`,
override any otherwise-clean verdict to `CHANGES_REQUESTED` and inject this
**blocker** finding regardless of the code-review verdict:

```
**File:** `(PR diff — UI files without visual proof)`
**Code:** `(see UI_FILES list from Step 1)`
**Issue:** UI changes detected but no BYOB screenshots were captured. Visual
proof is required before this PR can be approved. At least one screenshot of
the affected UI must be included in the review.
**Severity:** blocker
**Fix:** Start the app, navigate to the affected page(s) via BYOB MCP, capture
at least one screenshot with mcp__byob__browser_screenshot, and re-run the
review.
```

This blocker is real and counts toward the verdict: the review MUST post as
`CHANGES_REQUESTED`, not `APPROVED`, regardless of other findings. The blocker
text should name the specific UI files that changed and explain that at least
one screenshot is required before this PR can be approved.

## Completion

Report the number of screenshots captured and their paths, plus the values of
`UI_CHANGES_DETECTED` and `VISUAL_PROOF_GATE_FAILED` for the post-review step.
