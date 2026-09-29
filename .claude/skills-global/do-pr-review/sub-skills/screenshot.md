# Sub-Skill: Screenshot Capture

Capture visual proof of UI changes and evaluate the visual proof gate. The
surface is a browser MCP (`mcp__byob__browser_*`) driving the user's real,
logged-in Chrome, so authenticated staging URLs work; there is no
anonymous-headless fallback. Honor any real-Chrome session requirement the
context file declares, and confirm the MCP is connected
(`mcp__byob__browser_list_tabs` returns at least one tab) before capturing.

## 1. Detect UI Changes

```bash
UI_FILES=$(gh pr diff "$PR_NUMBER" --name-only | grep -E '\.(html|htm|jsx|tsx|vue|css|scss|sass|less)$|/(ui|frontend|static|web|assets|templates)/' || true)
```

Also count Python files that render templates. If `UI_FILES` is empty, skip
this sub-skill: the gate is a no-op.

## 2. Capture

1. Start the app per the repo README's `## Running` section (in the
   background), and stop it when done if you started it.
2. Navigate to each affected view and save screenshots to
   `generated_images/pr-$PR_NUMBER/` (`01_main_view.png`, `02_feature_demo.png`,
   `03_edge_case.png`; one to three is typical), e.g.
   `mcp__byob__browser_screenshot(tabId=<tab>, savePath="generated_images/pr-$PR_NUMBER/01_main_view.png")`.
3. Read each screenshot and state in the review what it shows against the
   plan's intended UI. A visible mismatch is a finding.

## Visual Proof Gate

UI files changed with zero screenshots captured (browser unavailable, app
failed to start, or step skipped) fails the gate: the verdict is
`CHANGES_REQUESTED` regardless of other findings, with this blocker naming the
changed UI files:

```
**File:** `(PR diff — UI files without visual proof)`
**Code:** `(changed UI files: ...)`
**Issue:** UI changes detected but no screenshots were captured. Visual proof is
required before this PR can be approved.
**Severity:** blocker
**Fix:** Start the app, capture at least one screenshot of each affected page
through the browser MCP, and re-run the review.
```

## Completion

Report the screenshot count and paths, whether UI changes were detected, and
whether the gate failed.
