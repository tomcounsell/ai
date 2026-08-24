---
status: Planning
type: bug
appetite: Small
owner: valorengels
created: 2026-04-20
tracking: https://github.com/tomcounsell/ai/issues/1060
last_comment_id:
revision_applied: true
---

# Browser Screenshot Dimension Fix

## Problem

When an agent takes browser screenshots during a session and that session is later resumed via `claude --resume <UUID>`, Claude Code replays all prior turns including the screenshots. If any screenshot exceeds 2000px in either dimension, Claude Code returns an error **as plain text with exit code 0**:

> "An image in the conversation exceeds the dimension limit for many-image requests (2000px). Start a new session with fewer images."

This text is delivered verbatim to the Telegram user, bypassing the existing stale-UUID fallback (which only fires on non-zero exit codes).

**Current behavior:**
- `tools/browser/__init__.py:screenshot()` sets `viewport={"width": 1280, "height": 720}` correctly but takes `full_page=True` screenshots that can be thousands of pixels tall
- All other browser functions (`navigate`, `extract_text`, `fill_form`, `click`, `wait_for_element`) call `browser.new_page()` with no viewport — Chromium defaults to 800×600, so any subsequent screenshot of those pages can be any size
- No dimension check or downscaling happens before `base64.b64encode(screenshot_bytes)`
- `agent/sdk_client.py` stale-UUID fallback only triggers when `returncode != 0`; the image-dimension error is exit code 0, so the fallback never fires

**Desired outcome:**
- Screenshots are bounded: longest edge ≤ 1280px before base64 encoding
- All browser functions use a consistent 1280px-wide viewport
- If Claude Code returns the image-dimension error string despite the above, `sdk_client.py` detects it and triggers the existing `full_context_message` fallback

## Freshness Check

**Baseline commit:** `018aa565a3d298573256dc8ce202a0b85e3b014f`
**Issue filed at:** 2026-04-20T03:19:10Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `tools/browser/__init__.py:69` — `navigate()` calls `browser.new_page()` with no viewport — confirmed
- `tools/browser/__init__.py:117` — `screenshot()` correctly uses `viewport={"width": 1280, "height": 720}` — confirmed
- `tools/browser/__init__.py:121-122` — `page.screenshot()` bytes passed to `base64.b64encode()` with no dimension check — confirmed
- `tools/browser/__init__.py:179,230,294,345` — `extract_text`, `fill_form`, `click`, `wait_for_element` all call `new_page()` without viewport — confirmed
- `agent/sdk_client.py:1647` — Stale-UUID fallback condition is `returncode != 0` only — confirmed

**Commits on main since issue was filed (touching referenced files):** None

**Active plans in `docs/plans/` overlapping this area:** None

## Prior Art

No prior issues or PRs found related to screenshot dimension limiting or viewport consistency in `tools/browser/`.

## Research

No relevant external findings — Pillow image resizing is well-documented in training data; no ecosystem pitfalls to surface. Anthropic's 2000px per-image limit in multi-image requests is a known constraint.

## Data Flow

1. **Agent invokes browser screenshot** → `tools/browser/__init__.py:screenshot(url, full_page=True)`
2. **Playwright captures** → `page.screenshot(full_page=True)` returns bytes; a 1280px-wide full-page screenshot of a long page can be 1280×8000px or more
3. **Base64 encoding** → `base64.b64encode(screenshot_bytes)` — no dimension check
4. **Agent embeds in response** → The base64 PNG is included in the Claude Code conversation transcript
5. **Session UUID stored** → `_store_claude_session_uuid()` persists the transcript UUID
6. **Next turn: `--resume`** → `agent/sdk_client.py` builds `cmd = harness_cmd + ["--resume", prior_uuid, message]`
7. **Claude Code replays transcript** → All prior turns including oversized screenshots re-evaluated
8. **Error returned** → Claude Code emits `"An image in the conversation exceeds the dimension limit..."` as text, exit code 0
9. **Fallback not triggered** → `returncode != 0` guard is not met; error text returned to Telegram

## Architectural Impact

- **New dependency**: `Pillow>=10.0` added to `pyproject.toml` — used only inside `tools/browser/__init__.py:screenshot()`
- **Interface unchanged**: `screenshot()` return dict already has `width`/`height` keys; after downscaling these will reflect the actual output dimensions (possibly smaller than the original)
- **Coupling**: No new coupling. Both changes (`tools/browser/` viewport + `agent/sdk_client.py` sentinel) are isolated to their respective files
- **Reversibility**: Both changes are additive (a new guard condition in each file); easy to revert

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Pillow installable | `uv pip show pillow 2>/dev/null \|\| uv pip install pillow` | Image downscaling |

Run all checks: `python scripts/check_prerequisites.py docs/plans/browser-screenshot-dimension-fix.md`

## Solution

### Key Elements

- **Consistent viewport**: All five `browser.new_page()` call sites in `tools/browser/__init__.py` use `viewport={"width": 1280, "height": 720}`
- **Screenshot downscaler**: After `page.screenshot()` returns bytes, open with Pillow, downscale proportionally if longest edge > 1280px, re-encode as PNG
- **SDK sentinel fallback**: In `agent/sdk_client.py`, after receiving `result_text` from a `--resume` call, detect the sentinel string `"exceeds the dimension limit"` and trigger the existing `full_context_message` fallback path

### Technical Approach

**`tools/browser/__init__.py`:**

1. Add `Pillow>=10.0` to `pyproject.toml`.
2. Add import: `from io import BytesIO` and lazy `from PIL import Image` (inside `screenshot()` or at module level behind a try/except similar to the Playwright guard).
3. Replace all five bare `browser.new_page()` calls with `browser.new_page(viewport={"width": 1280, "height": 720})`.
4. In `screenshot()`, after `screenshot_bytes = page.screenshot(full_page=full_page)`, add:
   ```python
   screenshot_bytes = _downscale_if_needed(screenshot_bytes, max_dim=1280)
   ```
   where `_downscale_if_needed(data: bytes, max_dim: int) -> bytes`:
   - Opens with `Image.open(BytesIO(data))`
   - If `max(img.width, img.height) > max_dim`, computes `scale = max_dim / max(img.width, img.height)`, resizes with `img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)`
   - Returns PNG bytes via `BytesIO`
   - On any Pillow exception (including empty/invalid input): logs a warning, returns original `data` bytes unchanged — never returns empty bytes (safe fallback that preserves the screenshot even if downscaling fails)
5. Update the `dimensions` dict reported in the return value to reflect actual output dimensions (post-downscale).

**`agent/sdk_client.py`:**

After the `_run_harness_subprocess` call that uses `--resume`, and **before** the existing `returncode != 0` stale-UUID check, add an exit-code-0 image-dimension check:

```python
IMAGE_DIMENSION_SENTINEL = "exceeds the dimension limit"

# NOTE: This check is intentionally different from the stale-UUID fallback's design
# decision to avoid stderr substring matching (documented in _run_claude_harness docstring
# as "brittle across CLI versions and locales"). Key distinctions:
#   1. This checks result_text (stdout assembled by Claude Code as a structured result),
#      not stderr — stdout result strings are stable protocol output, not log noise.
#   2. This fires ONLY when prior_uuid was set (resume path) — sentinel is meaningless
#      outside that context.
#   3. The image-dimension error arrives with exit code 0, making the returncode != 0
#      fallback below structurally unable to catch it — a separate check is required.
# The _run_claude_harness docstring should note that exit-code-0 image-dimension errors
# have a separate sentinel check above the returncode check.
if prior_uuid and result_text and IMAGE_DIMENSION_SENTINEL in result_text:
    logger.warning(
        f"[harness] Image dimension error on --resume for session_id={session_id}; "
        "triggering full_context_message fallback"
    )
    if full_context_message is not None:
        fallback_msg = _apply_context_budget(full_context_message)
        fallback_cmd = harness_cmd + [fallback_msg]
        result_text, session_id_from_harness, _ = await _run_harness_subprocess(
            fallback_cmd, working_dir, proc_env,
            on_sdk_started=on_sdk_started, on_stdout_event=on_stdout_event,
        )
    else:
        logger.error(
            f"[harness] Image dimension error on --resume, no full_context_message available"
        )
        result_text = (
            "I couldn't resume because the session history contains images that are too large. "
            "Please start a new thread."
        )
```

This sentinel check fires **only** when `prior_uuid` was set (i.e., a `--resume` was attempted) and the result text contains the known error string, so normal non-resume responses are unaffected. The builder must also update the `_run_claude_harness` docstring to note that exit-code-0 image-dimension errors have a separate sentinel check placed above the `returncode != 0` guard.

## Failure Path Test Strategy

### Exception Handling Coverage
- `_downscale_if_needed`: Pillow import failure or `Image.open` failure must NOT raise — must log and return original bytes unchanged
- `fill_form` already has `except Exception: pass` on line 239 (form field fill failures) — this is pre-existing and not in scope of this plan

### Empty/Invalid Input Handling
- `_downscale_if_needed(b"", ...)` → Pillow will raise; catch and return original `data` bytes unchanged (never empty bytes — returning empty bytes would corrupt the screenshot response). This matches the Pillow-unavailable fallback behavior already specified above.
- `sdk_client` sentinel check: guard with `result_text and IMAGE_DIMENSION_SENTINEL in result_text` — never fires on empty/None `result_text`

### Error State Rendering
- If both `--resume` and `full_context_message` paths fail: plain-language message delivered to user (not raw API error)
- If Pillow downscale fails silently: screenshot still sent (possibly oversized) — the sdk_client sentinel provides the backstop

## Test Impact

- `tools/browser/tests/test_agent_browser.py` — These are integration tests using live Playwright; the viewport change has no observable output change (Chromium already uses a sensible default). No updates needed.
- No existing unit tests for `sdk_client.py`'s resume fallback logic — new tests will be created.

## Rabbit Holes

- **Switching screenshot format to JPEG**: PNG is correct for UI screenshots (lossless); JPEG would degrade text readability. Not worth it.
- **Centralizing browser context creation**: Refactoring all five functions to share a single `browser.new_page` factory is a larger cleanup. Out of scope — just fix the viewport argument inline.
- **Downscaling all images in the conversation history retroactively**: Not feasible without modifying Claude Code internals. The fix is at write time (before screenshots enter the transcript).

## Risks

### Risk 1: Pillow not available in deployment
**Impact:** `_downscale_if_needed` import fails; screenshot function broken
**Mitigation:** Guard with try/except on PIL import (same pattern as Playwright guard at top of file). Log a warning and return original bytes if Pillow unavailable.

### Risk 2: Sentinel string changes between Claude Code versions
**Impact:** Image-dimension error not detected; raw error delivered to user
**Mitigation:** The sentinel `"exceeds the dimension limit"` is the stable portion of the error string — more stable than the surrounding text. The browser-side fix (downscaling) makes this fallback rarely needed anyway.

## Race Conditions

No race conditions identified — all operations are synchronous within a single subprocess result handling block, and `_downscale_if_needed` operates on already-downloaded bytes.

## No-Gos (Out of Scope)

- Fixing `fill_form`'s `except Exception: pass` blocks (pre-existing; separate issue)
- Adding viewport to the `agent-browser` CLI tool (wraps these functions but controls viewport separately)
- Downscaling images sent by Telegram users (separate code path: `bridge/media.py`)

## Update System

`Pillow` must be added to `pyproject.toml`. The `/update` skill runs `uv sync` which will pick up the new dependency automatically — no additional update steps.

## Agent Integration

No MCP server changes needed. `tools/browser/__init__.py` is called by the `agent-browser` CLI tool which is registered in `.mcp.json`. The downscaling is transparent — the returned `image_base64` is just smaller.

## Documentation

No feature documentation needed — this is a bug fix with no user-visible API changes.

## Success Criteria

- [ ] All five `browser.new_page()` calls use `viewport={"width": 1280, "height": 720}`
- [ ] `screenshot()` downscales output so longest edge ≤ 1280px (verified by unit test with synthetic tall page bytes)
- [ ] `Pillow>=10.0` in `pyproject.toml`
- [ ] `sdk_client.py` sentinel check fires and triggers `full_context_message` fallback when image-dimension error is returned (unit test with mocked subprocess)
- [ ] Raw `"exceeds the dimension limit"` never delivered to Telegram when `full_context_message` is available
- [ ] Pillow import failure degrades gracefully (screenshot still works, no exception raised)
- [ ] Tests pass (`/do-test`)

## Team Orchestration

### Team Members

- **Builder (browser-fix)**
  - Name: browser-builder
  - Role: Fix viewport consistency in all `new_page()` calls; add Pillow downscaler in `screenshot()`; add Pillow to `pyproject.toml`
  - Agent Type: builder
  - Resume: true

- **Builder (sdk-sentinel)**
  - Name: sdk-builder
  - Role: Add image-dimension sentinel check and plain-language fallback in `agent/sdk_client.py`
  - Agent Type: builder
  - Resume: true

- **Validator**
  - Name: fix-validator
  - Role: Verify both builds, run tests, confirm no regressions
  - Agent Type: validator
  - Resume: true

### Step by Step Tasks

#### 1. Fix browser viewport and add Pillow downscaler
- **Task ID**: build-browser
- **Depends On**: none
- **Validates**: `tools/browser/tests/test_agent_browser.py` (existing), new unit test for downscale
- **Assigned To**: browser-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `Pillow>=10.0` to `pyproject.toml` dependencies
- Add `from io import BytesIO` import; guard PIL import with try/except
- Add `_downscale_if_needed(data: bytes, max_dim: int = 1280) -> bytes` helper
- Replace all 5 bare `browser.new_page()` calls with `browser.new_page(viewport={"width": 1280, "height": 720})`
- Call `_downscale_if_needed` in `screenshot()` after `page.screenshot()` returns bytes
- Update `dimensions` dict to reflect actual post-downscale dimensions
- Write unit test: create synthetic tall PNG (1280×4000), assert `_downscale_if_needed` returns image with max dimension ≤ 1280

#### 2. Add image-dimension sentinel fallback in sdk_client.py
- **Task ID**: build-sdk
- **Depends On**: none
- **Validates**: new unit tests in `tests/unit/test_sdk_client.py` or equivalent
- **Assigned To**: sdk-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `IMAGE_DIMENSION_SENTINEL = "exceeds the dimension limit"` constant
- After `_run_harness_subprocess` result when `prior_uuid` was set, check for sentinel before the existing `returncode != 0` check
- If sentinel matched and `full_context_message` available: retry via `full_context_message` fallback
- If sentinel matched and `full_context_message` is None: return plain-language error string
- Write unit test: mock subprocess returning sentinel text with exit code 0 → verify retry fires with `full_context_message`
- Write unit test: mock subprocess returning sentinel text, `full_context_message=None` → verify plain-language string returned (not raw sentinel text)

#### 3. Validate all
- **Task ID**: validate-all
- **Depends On**: build-browser, build-sdk
- **Assigned To**: fix-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `uv run pytest tools/browser/tests/ -v` — confirm pass
- Run `uv run pytest tests/ -k "sdk_client or harness" -v` — confirm sentinel tests pass
- Confirm Pillow in `uv pip list` output
- Verify all 5 `new_page()` calls have viewport via grep
- Confirm no raw sentinel text in any test output

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `uv run pytest tools/browser/tests/ tests/ -x -q 2>/dev/null \|\| pytest tools/browser/tests/ -x -q` | exit code 0 |
| Pillow installed | `python -c "from PIL import Image; print(Image.__version__)"` | exit code 0 |
| All new_page have viewport | `grep -n "new_page()" tools/browser/__init__.py` | exit code 1 (no bare calls) |
| Sentinel constant defined | `grep -n "IMAGE_DIMENSION_SENTINEL" agent/sdk_client.py` | output contains IMAGE_DIMENSION_SENTINEL |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Skeptic, Archaeologist, Consistency Auditor | sdk_client sentinel check contradicts documented "no stderr substring gate" design decision without distinguishing rationale | Plan revision (revision_applied: true) | Added inline comment in code block distinguishing stdout result_text from stderr, resume-path scope, and exit-code-0 specificity. Builder must also update `_run_claude_harness` docstring. |
| NIT | Adversary | `_downscale_if_needed` failure fallback ambiguity — "(or original)" was parenthetical, not committed | Plan revision (revision_applied: true) | Resolved: always return original `data` bytes unchanged on any Pillow exception including empty input. Never return empty bytes. |
| NIT | Simplifier | Team Orchestration verbosity for Small appetite | No action — parallel structure is valid and compliant | Acceptable as-is. |
