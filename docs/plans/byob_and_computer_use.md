---
status: Ready
type: feature
appetite: Large
owner: Valor
created: 2026-05-02
tracking: https://github.com/tomcounsell/ai/issues/1256
last_comment_id: 4360511073
---

# BYOB Real-Chrome Control + macOS Computer Use

## Problem

The agent runs on the user's own machine but treats the web like a stranger. Skills that depend
on browser automation — including `do-pr-review`, `do-design-audit`, `linkedin`, `mermaid-render`,
and `do-discover-paths` — hit three structural limits:

**Current behavior:**
- Logged-in sites (Gmail, GitHub, LinkedIn, internal dashboards) require manual auth flows per
  session or stale `state.json` files checked into the repo. Every Google product and internal
  dashboard is effectively blocked to the agent.
- Cloudflare / PerimeterX / Datadome bot detection rejects the headless Playwright fingerprint,
  blocking the agent from many modern sites.
- The agent cannot drive any non-browser application — no "screenshot Telegram Desktop", no "click
  in Notes.app", no "drive Xcode build". No path exists for native macOS control.
- Headed mode steals focus and the user's mouse pointer during browser sessions.

**Desired outcome:**
- The agent can read and act on the user's already-logged-in Chrome without any cookie/state files
  in the repo and without re-auth per session.
- The agent can drive native macOS apps via a loopback HTTP API that does **not** move the cursor
  or steal focus, so the user can keep working while automation runs.
- All 10+ downstream skills that currently use `agent-browser` or `bowser` continue to work with
  minimal churn — the swap happens behind the `tools/browser/` abstraction wherever possible.

## Freshness Check

**Baseline commit:** `04a07bc3cefcdc52556414473bd120f2c9bcf926`
**Issue filed at:** 2026-05-01T15:32:11Z
**Disposition:** Minor drift (one sibling issue closed; no code has changed under these paths)

**File:line references re-verified:**
- `tools/browser/__init__.py` — issue claims it is the swappable abstraction; confirmed, no changes
  since filing. The module implements `navigate`, `screenshot`, `extract_text`, `fill_form`,
  `click`, and `wait_for_element` all backed directly by `sync_playwright`.
- `tools/browser/README.md` — "This abstraction layer allows swapping the underlying tool" —
  confirmed still present at line 239.
- `.claude/skills/agent-browser/SKILL.md` — still present, unchanged.
- `.claude/skills/bowser/SKILL.md` — still present, unchanged.

**Cited sibling issues/PRs re-checked:**
- #66 (Desktop control for Telegram Desktop app) — **closed** 2026-05-01T15:28:43Z, just before
  this issue was filed. Closed in favor of this umbrella issue. The `telegram_desktop_control`
  plan (`docs/plans/telegram_desktop_control.md`) still has `status: Ready` — it is a candidate
  for replacement by the `computer-use` track in this plan. Resolution: keep it as reference for
  the Telegram-specific workflow that computer-use must support; note in No-Gos that
  `telegram_desktop_control` plan is superseded by this work.

**Commits on main since issue was filed (touching referenced files):** None.

**Active plans in `docs/plans/` overlapping this area:**
- `docs/plans/telegram_desktop_control.md` — directly overlaps Track 2 (computer-use).
  Disposition: **superseded** by this plan. Update `telegram_desktop_control.md` status to
  `Cancelled` as a task step.

**Notes:** No drift that changes plan premise. Freshness check baseline is `04a07bc3`.

## Prior Art

No prior GitHub issues or merged PRs in this repository propose BYOB or background-computer-use.
The issue body notes that prior issues proposing homegrown solutions for these same gaps were
closed by the maintainer in favor of this umbrella.

- **Issue #66** (Desktop control for Telegram Desktop) — Proposed a bespoke `agent-desktop` CLI
  using AppleScript + Quartz. Closed in favor of adopting `background-computer-use` via this issue.
  The AppleScript/Quartz approach would not have generalized beyond keyboard-drive interaction
  (no element refs, no accessibility tree, macOS-specific friction). The `background-computer-use`
  runtime supersedes it with a richer accessibility-tree API.

## Research

**Queries used:**
- "wxtsky byob bring your own browser MCP Chrome extension native messaging 2025 2026"
- "actuallyepic background-computer-use macOS accessibility API loopback HTTP agent automation"
- "byob MCP vs CLI shim token cost chrome devtools protocol worktree session isolation"

**Key findings:**

1. **BYOB communication chain** (source: [github.com/wxtsky/byob](https://github.com/wxtsky/byob)):
   `AI tool → byob-mcp → byob-bridge → Chrome extension → tab`. MCP server is the native
   integration surface; a CLI shim would require wrapping stdio messages, not a natural fit.
   Runs over Unix socket + Native Messaging. All communication is local — zero outbound traffic.
   On laptop sleep, byob auto-resets all debug sessions (clean state on next call). Installs via
   `bun run setup`, which generates unique extension key, builds the MV3 extension, and registers
   the native messaging host.

2. **BYOB security defaults** (source: byob README): `browser_eval` (JS execution) is off by
   default, gated by `BYOB_ALLOW_EVAL=1`. Blocked URLs include `chrome://`, `file://`, and login
   pages for Google/Microsoft/Apple. Extension key is unique per install (not shared across
   machines).

3. **background-computer-use (bcu)** (source:
   [github.com/actuallyepic/background-computer-use](https://github.com/actuallyepic/background-computer-use)):
   Swift app exposing a loopback HTTP API. Reads window screenshots and Accessibility tree state.
   Key endpoints: `/v1/list_apps`, `/v1/list_windows`, `/v1/get_window_state`, `/v1/click`,
   `/v1/scroll`, `/v1/type_text`, `/v1/press_key`, `/v1/set_value`, `/v1/perform_secondary_action`,
   `/v1/drag`, `/v1/resize`, `/v1/set_window_frame`. Self-documenting catalog at `GET /v1/routes`.
   Optional on-screen cursor objects for visual feedback. macOS permissions attach to the signed
   host app identity `xyz.dubdub.backgroundcomputeruse`.

4. **MCP tool count / token cost tradeoff**: Chrome DevTools MCP ships ~40+ tools, and a GitHub
   issue on `ChromeDevTools/chrome-devtools-mcp` (issue #340) confirms verbose schemas add
   measurable token cost. The BYOB upstream ships as a native MCP but also supports a bridge mode
   that external processes call via stdio. Given the issue commenter's observation (comment
   IC_kwDOEYGa088AAAABA-geYQ) that CLI-per-invocation re-handshakes the native messaging host
   and breaks long sessions, **MCP is the correct integration surface for BYOB**. The ~30 tools
   are loaded once per session, not per turn.

5. **Chrome session isolation for parallel worktrees**: One real Chrome DOM tree means parallel
   Claude Code sessions will collide if they both click via BYOB. Mitigation: add a session slot
   guard in `tools/browser/` that serializes BYOB calls (single-slot mutex via a lock file), and
   document this as a known limitation. Parallel headless work should fall back to the Playwright
   path (keep `bowser` as anonymous-Chrome fallback).

## Spike Results

### spike-1: MCP vs CLI for BYOB — latency and session stickiness
- **Assumption**: "CLI shim per call is feasible if reconnect is fast"
- **Method**: code-read (byob source, issue comment)
- **Finding**: Issue commenter with BYOB implementation experience confirms CLI-per-invocation
  re-handshakes the native messaging host on each call, adding latency and breaking long sequences
  when the host gets GC'd. MCP keeps the connection alive across the session. CLI shim is ruled out.
- **Confidence**: high
- **Impact on plan**: MCP is the integration surface. BYOB is registered in `.mcp.json` as a new
  MCP server. `tools/browser/` calls the MCP tools rather than shelling out to a CLI.

### spike-2: `tools/browser/` backend-swap viability
- **Assumption**: "The existing `tools/browser/__init__.py` abstraction can front BYOB without
  restructuring the whole module"
- **Method**: code-read
- **Finding**: `tools/browser/__init__.py` wraps `sync_playwright` directly with no adapter
  pattern — every public function re-opens a Playwright browser. The module is a thin wrapper, not
  a plugin-style adapter. Swapping the backend requires replacing the internals of each function.
  The public surface (`navigate`, `screenshot`, `extract_text`, `fill_form`, `click`,
  `wait_for_element`) can be preserved exactly — callers see no change. Skills that call
  `agent-browser` CLI directly (not through `tools/browser/`) require separate skill-file edits.
- **Confidence**: high
- **Impact on plan**: Keep the existing public interface of `tools/browser/__init__.py` unchanged.
  Replace the internals to call BYOB MCP with Playwright fallback. Skills calling `agent-browser`
  CLI directly must be updated to call `agent-browser` (BYOB CLI wrapper) instead.

### spike-3: `computer-use` skill shape — separate `tools/computer/` vs extending `tools/browser/`
- **Assumption**: "Both browser BYOB and bcu native control can share one `tools/browser/`
  abstraction"
- **Method**: code-read + research
- **Finding**: The capability sets diverge fundamentally. Browser automation uses DOM element refs
  and URLs. Desktop automation uses window IDs, accessibility tree node references, and app
  bundle IDs. Attempting to unify them in one Python module creates an incoherent interface. A
  separate `tools/computer/` module is the right shape — same structural pattern as
  `tools/browser/`, separate concerns.
- **Confidence**: high
- **Impact on plan**: Create `tools/computer/__init__.py` as a new module wrapping bcu HTTP API.
  `tools/browser/` keeps its existing public interface backed by BYOB MCP.

### spike-4: bowser fate — retire or keep as anonymous-Chrome fallback
- **Assumption**: "bowser is fully redundant with BYOB-backed agent-browser"
- **Method**: code-read + issue analysis
- **Finding**: `bowser` uses `playwright-cli` (headless, throwaway profile) — genuinely different
  from BYOB (real Chrome, logged-in). The issue commenter's point about worktree collision
  (parallel sessions hitting one real Chrome DOM) means there is a legitimate use case for a
  headless anonymous fallback: (a) previewing untrusted links from Telegram without leaking
  the real Chrome session, (b) parallel CI-style test runs that need isolated browsers. However,
  `bowser` and `agent-browser` are the same tool shape — keeping both is confusing. Resolution:
  **retire `agent-browser`** (replaced by BYOB-backed version with same CLI surface), **keep
  `bowser`** but document it explicitly as the "anonymous headless fallback" for untrusted-content
  and parallel-session use cases. Update `bowser`'s SKILL.md to make this role explicit.
- **Confidence**: high
- **Impact on plan**: Two separate skills post-ship: `agent-browser` (BYOB, logged-in real Chrome)
  and `bowser` (Playwright headless, anonymous). Both remain. The agent-browser SKILL.md is
  updated in place; no deletion.

## Data Flow

### Track 1 — BYOB browser automation

1. **Entry**: Skill (e.g., `do-pr-review`) invokes `agent-browser open <url>` via Bash tool.
2. **Skill ↔ CLI**: `agent-browser` CLI (kept as the surface binary) now routes through BYOB MCP
   server instead of launching headless Playwright.
3. **BYOB MCP server**: Node process spawned by Claude's MCP runtime. Holds a persistent
   connection to `byob-bridge` over Unix socket.
4. **byob-bridge**: Communicates with the Chrome extension over Native Messaging.
5. **Chrome extension** (MV3): Receives commands, operates on the currently active tab in the
   user's real Chrome session.
6. **Result**: DOM snapshots, screenshots, and interaction results flow back up the chain to the
   MCP client (Claude Code), which routes results to the calling skill.

For skills that call `tools.browser` Python functions directly (not via CLI):
1. **Python caller** calls `tools.browser.navigate(url)` or similar.
2. **`tools/browser/__init__.py`** dispatches to BYOB via the **Anthropic SDK in-process MCP
   client** (no subprocess per call). The same MCP server registered in `.mcp.json` is reused.
3. Same BYOB → byob-bridge → extension → Chrome path from step 3 above.
4. **Fallback**: If BYOB bridge is not running, fall through to Playwright headless (existing
   `sync_playwright` path). Failure is surfaced via `BrowserError` with `category="byob_unavailable"`.

### Track 2 — background-computer-use desktop control

1. **Entry**: `computer-use` skill is invoked; it calls `tools/computer/__init__.py` functions.
2. **`tools/computer/__init__.py`**: Reads `$TMPDIR/background-computer-use/runtime-manifest.json`
   for `base_url`. Makes HTTP GET/POST calls to the bcu loopback API.
3. **bcu HTTP server** (Swift app, running on macOS): Reads the Accessibility tree, captures
   window screenshots, dispatches macOS Accessibility API actions against target windows.
4. **macOS Accessibility API**: Performs the action (click, type, etc.) without moving the user's
   cursor.
5. **Result**: JSON response from bcu API → parsed by `tools/computer/__init__.py` → returned to
   skill.

## Architectural Impact

- **New dependencies**: BYOB (bun, Node.js ≥18, Chrome extension), bcu (Swift app binary, macOS
  only), `bun` runtime for byob setup.
- **Interface changes**: `tools/browser/__init__.py` public API preserved exactly. New
  `tools/computer/__init__.py` is a net-new module — no existing code depends on it.
- **Coupling**: Adds MCP server registration in `.mcp.json` for BYOB. This is low coupling —
  Claude Code handles the MCP lifecycle; the agent sees 30 new tools in context when the skill
  runs.
- **Data ownership**: Chrome session state lives in the user's actual Chrome profile (no files in
  repo). bcu state is ephemeral (session IDs in memory).
- **Reversibility**: BYOB can be disabled by removing its entry from `.mcp.json` and reverting
  `tools/browser/__init__.py` internals. bcu is a macOS app — uninstalling it is `rm -rf`.
  Reasonably reversible.

## Appetite

**Size:** Large

**Team:** Solo dev + PM check-ins

**Interactions:**
- PM check-ins: 2 (scope alignment on BYOB MCP wiring, sign-off on bowser fate)
- Review rounds: 1 (code review of MCP integration and tools/computer/ module)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `bun` runtime | `bun --version` | Required for BYOB setup |
| Chrome (not Chromium) | `test -d "/Applications/Google Chrome.app"` | BYOB targets real Chrome |
| Node.js ≥18 | `node --version \| awk -F. '{exit ($1 < 18)}'` | BYOB build dep |
| byob cloned | `test -d ~/.byob` | BYOB native messaging host install target |
| bcu binary | `test -f "$TMPDIR/background-computer-use/runtime-manifest.json"` | bcu running |
| Accessibility permission | `osascript -e 'tell application "System Events" to name of processes'` | bcu requires it |
| Screen Recording permission | `python -c "import Quartz.CoreGraphics as CG; img = CG.CGWindowListCreateImage(CG.CGRectNull, CG.kCGWindowListOptionIncludingWindow, 1, 0); assert img is not None"` | bcu screenshots |

Run all checks: `python scripts/check_prerequisites.py docs/plans/byob_and_computer_use.md`

## Solution

### Key Elements

- **BYOB MCP server** registered in `.mcp.json`: Exposes real-Chrome automation tools
  (`byob_navigate`, `byob_click`, `byob_screenshot`, etc.) to Claude Code as MCP tools.
- **`tools/browser/__init__.py` internals replaced**: Public API (`navigate`, `screenshot`,
  `extract_text`, `fill_form`, `click`, `wait_for_element`) preserved. Internals route to BYOB
  MCP first; fall through to Playwright if BYOB bridge unavailable.
- **`agent-browser` SKILL.md updated**: Reflects BYOB as the backing implementation. CDP
  connection section updated to explain BYOB replaces the manual `--remote-debugging-port` workflow.
- **`bowser` SKILL.md updated**: Explicitly documented as "anonymous headless fallback — use for
  untrusted-link preview and parallel CI-style test runs where real Chrome session isolation is
  needed."
- **`tools/computer/__init__.py`**: New module. Wraps bcu loopback HTTP API. Functions:
  `list_apps`, `list_windows`, `get_window_state`, `click`, `scroll`, `type_text`, `press_key`,
  `set_value`, `drag`, `resize`, `set_window_frame`, `screenshot_window`. Reads base URL from
  `$TMPDIR/background-computer-use/runtime-manifest.json`. Returns `dict` results (success or
  `{"error": ...}`). Raises `ComputerUseUnavailableError` if manifest not found (OS-gate).
- **`computer-use` skill** at `.claude/skills/computer-use/SKILL.md`: New skill. macOS-only.
  Wraps `tools/computer/` for agent use. Documents OS-gate behavior.
- **`/setup` skill** updated: BYOB extension install + native messaging registration; bcu download
  + Accessibility + Screen Recording permission prompts.
- **`/update` skill** updated: Pull BYOB repo and rebuild extension; re-register native messaging
  host if version changed; re-download bcu binary if SHA mismatch.
- **`telegram_desktop_control` plan** status updated to `Cancelled`: superseded by this work.

### Flow

**Browser automation (logged-in)**
Skill invokes `agent-browser open <url>` → BYOB MCP server → byob-bridge → Chrome extension →
user's real tab → result back to skill

**Browser automation (anonymous/parallel)**
Skill invokes `bowser -s=<session> open <url>` → Playwright headless → throwaway profile → result

**Desktop automation**
`computer-use` skill calls `tools/computer.list_windows()` → bcu HTTP → macOS Accessibility API →
window list returned → skill selects target → calls `tools/computer.click(window_id, x, y)` →
bcu performs action without stealing cursor → result returned

### Technical Approach

- **BYOB MCP server registration**: Add entry to `.mcp.json`:
  ```json
  "byob": {
    "command": "node",
    "args": ["~/.byob/dist/mcp-server.js"],
    "env": { "BYOB_ALLOW_EVAL": "0" }
  }
  ```
  The MCP client (Claude Code harness) spawns this process on startup when the server is listed.

- **`tools/browser/` BYOB routing (in-process MCP)**: Each function checks for BYOB availability
  by attempting to import or call a thin `_byob_client()` helper that uses the **Anthropic SDK's
  in-process MCP client** to talk to the registered BYOB MCP server. No subprocess shell-out per
  call. If the BYOB bridge is running (`~/.byob/run/byob.sock` exists), dispatch in-process via
  the MCP client; otherwise fall through to `sync_playwright`. This is a best-effort fallback,
  not a feature flag — when BYOB is installed and Chrome is open, it is always preferred. The
  in-process approach avoids per-call native-messaging-host re-handshake (see spike-1).

- **Session collision guard**: A `threading.Lock` (or `filelock` on `~/.byob/session.lock`) in
  `tools/browser/` serializes calls when BYOB is active. Multiple parallel worktrees that would
  call BYOB concurrently are queued (5s timeout, then error). The `bowser` path is unaffected —
  it remains fully parallel.

- **`tools/computer/` HTTP client**: Use `urllib.request` (stdlib) to avoid new dependencies.
  Each function reads manifest, constructs request, handles `ConnectionRefusedError` → converts
  to `ComputerUseUnavailableError`. Timeout: 10s per call.

- **OS gate in `computer-use` skill**: Skill checks `sys.platform == "darwin"` at entry. On
  non-macOS, exits with: `"computer-use is macOS-only. This machine runs {platform}; skipping."`.

- **Downstream skill updates**: Skills that shell out to `agent-browser` CLI directly (SKILL.md
  files for `do-design-audit`, `do-pr-review`, `mermaid-render`, `linkedin`, `do-discover-paths`,
  `prepare-app`, `do-test`, `do-design-system`) require no changes if `agent-browser` CLI binary
  still exists and routes to BYOB internally. The SKILL.md docstrings may need notes updated.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `tools/browser/__init__.py` has `except Exception as e: return {"error": ...}` in every
  public function — each must have a test asserting the `"error"` key is present and non-empty
  when Playwright (or BYOB) raises. Existing tests in `tools/browser/tests/` cover Playwright
  paths; BYOB fallback tests use a mock BYOB unavailable scenario.
- [ ] `tools/computer/__init__.py` must raise `ComputerUseUnavailableError` when the runtime
  manifest is absent. Test: call any function when manifest does not exist.
- [ ] BYOB bridge socket absent: `tools/browser/` must fall back to Playwright without crashing.
  Test: rename `~/.byob/run/byob.sock` and call `navigate()`.

### Empty/Invalid Input Handling
- [ ] `tools/browser.navigate("")` — empty URL → `{"error": "..."}`, not a crash.
- [ ] `tools/computer.click(window_id=None, x=0, y=0)` → `ComputerUseUnavailableError` or
  `ValueError` (not a silent no-op).
- [ ] `tools/computer.type_text(window_id=1, text="")` → success (empty string is valid).

### Error State Rendering
- [ ] `computer-use` skill body must surface bcu errors to the agent output, not swallow them.
  The skill should print the raw error dict when `tools/computer` returns `{"error": ...}`.
- [ ] BYOB MCP connection failure must surface a clear message to the skill output:
  "BYOB bridge not running — start Chrome and run `~/.byob/start.sh`."

## Test Impact

- [ ] `tools/browser/tests/test_agent_browser.py` — **UPDATE**: The entire file calls
  `agent-browser` CLI directly via `subprocess`. After the swap, these tests remain valid if the
  `agent-browser` CLI is still on PATH (wrapping BYOB). Add a new test class
  `TestByobBridgeFallback` that mocks the BYOB socket as absent and confirms Playwright path
  activates. Existing tests become integration tests requiring Chrome + BYOB running.
- [ ] `tools/browser/tests/test_downscale.py` — **NO CHANGE**: Pure unit test of `_downscale_if_needed`.
  Unaffected by backend swap.
- [ ] `tests/happy-paths/SCHEMA.md` — **UPDATE**: Update the "discovery stage uses agent-browser"
  note to "discovery stage uses BYOB-backed agent-browser".
- [ ] New tests to create: `tools/computer/tests/test_computer_use.py` (unit tests mocking bcu
  HTTP responses) and `tools/computer/tests/test_computer_use_integration.py` (live bcu calls,
  marked `pytest.mark.integration`).

## Rabbit Holes

- **Building a custom Chrome extension from scratch**: BYOB ships a complete MV3 extension.
  Do not re-implement it. Adopt upstream as-is.
- **Unifying `tools/browser/` and `tools/computer/` into one module**: The interfaces are
  fundamentally different (DOM refs vs window IDs). One module serving both concerns becomes an
  incoherent mess. Keep them separate.
- **Supporting non-Chrome browsers via BYOB**: BYOB is Chrome-only. Do not attempt to make it
  work with Firefox or Safari. That is a future project.
- **Making bcu work on Linux/Windows**: bcu is a Swift macOS app. Cross-platform support is an
  upstream concern, not ours.
- **Full `telegram_desktop_control` feature migration**: The Telegram-specific plan (#66) scoped
  features like "navigate to specific chats by name". That level of Telegram-specific automation
  is out of scope for this foundational capability work. Computer-use provides the primitive
  (`click`, `type_text`, `screenshot_window`) — higher-level Telegram workflows build on top.
- **Parallel BYOB sessions**: Chrome has one DOM tree. Making BYOB concurrent requires upstream
  tab-isolation changes. Out of scope — serialize via lock and document the limitation.

## Risks

### Risk 1: BYOB upstream breaks on Chrome updates
**Impact:** `agent-browser` skill stops working entirely when Chrome auto-updates and the
extension API surface changes.
**Mitigation:** Pin BYOB to a specific git commit in `/setup`. Add a health-check step to
`/update` that runs `agent-browser connect 9222 && agent-browser get url` after any BYOB rebuild.
Alert if health check fails.

### Risk 2: bcu Accessibility permission grant is persistent but fragile
**Impact:** A macOS upgrade or re-sign can revoke the Accessibility permission for `xyz.dubdub.backgroundcomputeruse`, silently breaking the computer-use skill.
**Mitigation:** `tools/computer/__init__.py` checks for a valid accessibility permission on first
call using `osascript` as a canary. Surface clear error: "bcu Accessibility permission revoked —
open System Settings → Privacy & Security → Accessibility".

### Risk 3: BYOB blocked on login pages it doesn't know about
**Impact:** The skill attempts to read an authenticated page; BYOB's block-list rejects it
(Google accounts, Microsoft accounts, etc.), causing silent failure.
**Mitigation:** Document the block-list in `agent-browser` SKILL.md. For login pages, instruct
the agent to use `bowser` with `--cdp` flags or a persistent profile instead.

### Risk 4: byob native messaging host not re-registered after Chrome update
**Impact:** Chrome updates can invalidate the native messaging host registration, breaking BYOB
silently.
**Mitigation:** `/update` re-runs `bun run setup` after any detected Chrome version change
(`defaults read /Applications/Google\ Chrome.app/Contents/Info.plist CFBundleShortVersionString`).

## Race Conditions

### Race 1: Parallel worktrees both acquire BYOB bridge simultaneously
**Location:** `tools/browser/__init__.py` — BYOB dispatch path
**Trigger:** Two `agent-browser` calls from different parallel Claude Code sessions, both in
BYOB mode, simultaneously click different elements.
**Data prerequisite:** BYOB maintains one active tab reference per call; a second call mid-flight
corrupts the active tab state.
**State prerequisite:** BYOB bridge socket is connected and active.
**Mitigation:** `filelock` on `~/.byob/session.lock` with 5-second timeout. If lock cannot be
acquired, emit a logged warning and fall through to `bowser` (Playwright path).

### Race 2: bcu window ID goes stale between `list_windows` and `click`
**Location:** `tools/computer/__init__.py` — any action after a `list_windows` call
**Trigger:** App closes or minimizes between the window-list query and the subsequent action call.
**Data prerequisite:** Window ID from `list_windows` must still correspond to a valid window at
click time.
**State prerequisite:** Target application window is still open.
**Mitigation:** bcu returns HTTP 404 for stale window IDs. `tools/computer/__init__.py` catches
`404` responses and converts them to `{"error": "window_not_found", "window_id": N}`. Skills
must re-call `list_windows` and retry.

## No-Gos (Out of Scope)

- Committing cookies, auth tokens, or `state.json` files to the repo — ever.
- Supporting BYOB with non-Chrome browsers (Firefox, Safari, Arc).
- Making `computer-use` work on Linux or Windows.
- Custom Chrome extension development — use BYOB upstream as-is.
- Retrofitting the old `agent-desktop` CLI approach from `telegram_desktop_control` plan.
- Parallel BYOB sessions (one real Chrome DOM tree — serialization is the correct model).
- Backwards-compatibility shims — per CLAUDE.md "no legacy code tolerance". Replace cleanly.
- Supporting `BYOB_ALLOW_EVAL=1` by default. `browser_eval` stays disabled.

## Update System

The `/update` skill (`scripts/remote-update.sh`) must be extended with:

1. **BYOB update step**: After pulling main, check if BYOB version has changed
   (`git -C ~/.byob log --oneline -1`). If changed, run `bun install && bun run build` in
   `~/.byob/` and re-register the native messaging host (`bun run setup --skip-extension`).
2. **bcu update check + install step**:
   - First, detect whether bcu is opted-in on this machine (sentinel: `~/.config/valor/computer-use-enabled`,
     written by `/setup` only after the user confirms).
   - If opted in but not installed, treat as a fresh install: download the latest bcu binary
     from upstream (GitHub release asset), verify SHA against the published checksum, install
     to `~/.local/bin/background-computer-use` (or the upstream-recommended path), and prompt
     for Accessibility + Screen Recording permission.
   - If already installed, compare the installed binary's SHA against the latest published SHA.
     If different, download and replace; re-prompt for Accessibility permission only if the
     bundle identity changed.
   - On any install/update hiccup (download fail, SHA mismatch, permission missing), surface a
     clear, actionable alert to the user (e.g., "bcu update failed: SHA mismatch — skipping;
     run `/setup` to retry"). Never fail the rest of the update silently.
3. **Chrome version check**: Read Chrome version. If changed since last update, force re-run
   of `bun run setup` to ensure native messaging registration is fresh.

All bcu and BYOB steps are macOS-only (guard: `[[ "$(uname)" == "Darwin" ]]`). On non-macOS
machines, the update script no-ops these steps and prints a one-line note.

## Agent Integration

- **BYOB as MCP server**: Register `byob` in `.mcp.json`. Claude Code loads BYOB MCP tools
  (`byob_navigate`, `byob_click`, `byob_screenshot`, etc.) into the agent context when the
  server is active. The agent uses these via the existing `agent-browser` skill pattern — no
  new CLI entry points needed.
- **`tools/computer/` Python module**: Invoked from the `computer-use` skill via Bash
  (`python -m tools.computer list_windows --json`). Add CLI entry points in `pyproject.toml`:
  ```toml
  valor-computer = "tools.computer.cli:main"
  ```
  The `computer-use` skill calls `valor-computer` commands.
- **Skills that use `agent-browser` directly**: No code changes needed in skill files — the
  `agent-browser` binary name is preserved. The backing changes are transparent to callers.
- **Integration test**: After setup, `agent-browser open https://github.com && agent-browser
  get title` should return the user's GitHub notifications page (logged-in view), not the public
  homepage.

## Documentation

- [ ] Create `docs/features/byob-browser-control.md` describing BYOB integration, BYOB-vs-bowser
  decision guide, and known limitations (block-list, single-session serialization).
- [ ] Create `docs/features/computer-use.md` describing bcu integration, macOS-only OS gate,
  permission requirements, and example skill workflows.
- [ ] Update `docs/features/tools-reference.md`: Add `tools/computer/` section; update
  `tools/browser/` entry to note BYOB backing.
- [ ] Update `docs/features/skills-dependency-map.md`: Add `computer-use` skill node; update
  `agent-browser` node to note BYOB backing.
- [ ] Add entry to `docs/features/README.md` index table for both new feature docs.
- [ ] Update `config/personas/segments/tools.md`: Replace `agent-browser` section with BYOB note;
  add `computer-use` section.
- [ ] Update `docs/plans/telegram_desktop_control.md` status to `Cancelled` with note:
  "Superseded by docs/plans/byob_and_computer_use.md — Track 2 (computer-use skill)."

## Success Criteria

- [ ] `agent-browser open https://github.com && agent-browser get title` returns the user's
  authenticated GitHub view (logged-in page, not public homepage) — zero `state.json` files
  committed to repo.
- [ ] `computer-use` skill can list apps, list windows, click, type, and screenshot `Notes.app`
  on macOS without moving the user's cursor.
- [ ] `bowser` SKILL.md explicitly documents its role as "anonymous headless fallback for
  untrusted-link preview and parallel CI-style sessions."
- [ ] All downstream skills (`do-design-audit`, `do-pr-review`, `linkedin`, `mermaid-render`,
  `do-discover-paths`, `prepare-app`, `do-test`, `do-design-system`, `do-design-review`) pass
  their existing invocation patterns without modification.
- [ ] `computer-use` invoked on non-macOS machine returns a clear error message, not confusing
  output.
- [ ] `/setup` skill prompts for BYOB extension install and bcu Accessibility + Screen Recording
  permissions.
- [ ] `/update` re-runs BYOB rebuild and bcu SHA check on each pull.
- [ ] `BYOB_ALLOW_EVAL` is unset by default in `.mcp.json` entry.
- [ ] bcu HTTP server documented as loopback-only in `computer-use` SKILL.md.
- [ ] `docs/plans/telegram_desktop_control.md` status is `Cancelled`.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).

## Team Orchestration

### Team Members

- **Builder (byob-integration)**
  - Name: byob-builder
  - Role: BYOB MCP registration, `tools/browser/__init__.py` internals replacement, BYOB
    fallback logic, session collision guard
  - Agent Type: builder
  - Resume: true

- **Builder (computer-use)**
  - Name: computer-builder
  - Role: `tools/computer/__init__.py` module, `valor-computer` CLI entry point,
    `computer-use` SKILL.md, OS gate
  - Agent Type: builder
  - Resume: true

- **Builder (skill-updates)**
  - Name: skill-builder
  - Role: Update `agent-browser` SKILL.md (BYOB docs), `bowser` SKILL.md (fallback role),
    cancel `telegram_desktop_control` plan, update `/setup` and `/update` skills
  - Agent Type: builder
  - Resume: true

- **Test Engineer (browser)**
  - Name: browser-test-engineer
  - Role: Update `tools/browser/tests/test_agent_browser.py`, add BYOB fallback unit tests,
    add `tools/computer/tests/` suite
  - Agent Type: test-engineer
  - Resume: true

- **Validator (integration)**
  - Name: integration-validator
  - Role: Verify BYOB smoke test (authenticated GitHub page), bcu Notes.app demo, downstream
    skills pass, OS gate on non-macOS
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: feature-documentarian
  - Role: Write `docs/features/byob-browser-control.md` and `docs/features/computer-use.md`;
    update index docs and tools-reference
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

Tier 1 (Core): builder, validator, test-engineer, documentarian
Tier 2 (Specialists needed here): mcp-specialist (for MCP server registration review)

## Step by Step Tasks

### 1. Register BYOB MCP server
- **Task ID**: build-byob-mcp
- **Depends On**: none
- **Validates**: `.mcp.json` contains `byob` entry; `bun run setup` succeeds in `~/.byob/`
- **Informed By**: spike-1 (MCP is correct surface), spike-4 (bowser stays as fallback)
- **Assigned To**: byob-builder
- **Agent Type**: builder
- **Parallel**: true
- Clone `wxtsky/byob` to `~/.byob/`, run `bun install && bun run setup`
- Add `byob` MCP server entry to `.mcp.json` with `BYOB_ALLOW_EVAL=0`
- Verify `~/.byob/run/byob.sock` exists after Chrome + BYOB are running

### 2. Replace `tools/browser/__init__.py` internals with BYOB routing
- **Task ID**: build-byob-browser
- **Depends On**: build-byob-mcp
- **Validates**: `tools/browser/tests/test_agent_browser.py` (existing), new
  `test_byob_fallback.py`
- **Informed By**: spike-2 (public API preserved, internals replaced), spike-4 (Playwright fallback)
- **Assigned To**: byob-builder
- **Agent Type**: builder
- **Parallel**: false
- Preserve public surface: `navigate`, `screenshot`, `extract_text`, `fill_form`, `click`,
  `wait_for_element`
- Replace internals: check for BYOB socket (`~/.byob/run/byob.sock`); if present, route to
  BYOB subprocess/MCP call; else fall through to existing `sync_playwright` path
- Add `filelock` on `~/.byob/session.lock` to serialize BYOB calls (5s timeout → bowser fallback)
- Add `BrowserError` category `"byob_unavailable"` for BYOB-not-running case
- Ensure clear user-facing error when BYOB bridge not running

### 3. Create `tools/computer/` module
- **Task ID**: build-computer-module
- **Depends On**: none
- **Validates**: `tools/computer/tests/test_computer_use.py` (new)
- **Informed By**: spike-3 (separate module, not merged into tools/browser/)
- **Assigned To**: computer-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `tools/computer/__init__.py` with functions: `list_apps`, `list_windows`,
  `get_window_state`, `click`, `scroll`, `type_text`, `press_key`, `set_value`,
  `perform_secondary_action`, `drag`, `resize`, `set_window_frame`, `screenshot_window`
- Read base URL from `$TMPDIR/background-computer-use/runtime-manifest.json`; raise
  `ComputerUseUnavailableError` (subclass of `RuntimeError`) if manifest absent
- Use `urllib.request` (stdlib only — no new deps)
- Handle HTTP 404 → `{"error": "window_not_found", "window_id": N}`
- Create `tools/computer/cli.py` with argparse CLI (`valor-computer` entry point)
- Add `valor-computer = "tools.computer.cli:main"` to `pyproject.toml [project.scripts]`

### 4. Create `computer-use` skill
- **Task ID**: build-computer-skill
- **Depends On**: build-computer-module
- **Validates**: `computer-use` skill invocation on non-macOS returns clear error
- **Informed By**: spike-3, research finding on bcu endpoints
- **Assigned To**: computer-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `.claude/skills/computer-use/SKILL.md`
- Include OS gate: check `sys.platform == "darwin"`, exit with message on non-macOS
- Document bcu HTTP server is loopback-only
- Document `BYOB_ALLOW_EVAL` stays unset for browser operations
- Include example workflow: list apps → find Notes.app → list windows → click → type → screenshot

### 5. Write browser and computer-use tests
- **Task ID**: build-tests
- **Depends On**: build-byob-browser, build-computer-module
- **Validates**: `tools/browser/tests/`, `tools/computer/tests/`
- **Informed By**: spike-2 (public API unchanged), spike-3 (computer module shape)
- **Assigned To**: browser-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- Update `tools/browser/tests/test_agent_browser.py`:
  - Add `TestByobFallback` class: mock BYOB socket absent, assert Playwright path activates
  - Mark existing integration tests `@pytest.mark.integration` (require live Chrome)
- Create `tools/computer/tests/__init__.py`
- Create `tools/computer/tests/test_computer_use.py`:
  - Unit tests with mocked HTTP responses for each `tools/computer` function
  - Test `ComputerUseUnavailableError` when manifest absent
  - Test HTTP 404 → `window_not_found` error dict
  - Test platform-specific OS gate behavior
- Create `tools/computer/tests/test_computer_use_integration.py`:
  - Integration tests marked `@pytest.mark.integration`
  - Requires live bcu running; skip if manifest absent

### 6. Update skill files and downstream docs
- **Task ID**: build-skill-updates
- **Depends On**: none
- **Validates**: `grep -r "agent-browser" .claude/skills/` returns no misleading docs
- **Informed By**: spike-4 (bowser stays as fallback with explicit role)
- **Assigned To**: skill-builder
- **Agent Type**: builder
- **Parallel**: true
- Update `agent-browser` SKILL.md: Document BYOB as backing implementation; update CDP
  section to explain BYOB replaces manual `--remote-debugging-port` workflow; add note
  about single-session serialization limitation
- Update `bowser` SKILL.md: Add explicit "Role: anonymous headless fallback" section;
  document use cases: untrusted-link preview, parallel CI-style test runs
- Update `docs/plans/telegram_desktop_control.md`: Set status to `Cancelled`, add note:
  "Superseded by docs/plans/byob_and_computer_use.md — Track 2 (computer-use skill)"
- Update `/setup` skill: Add BYOB extension install step (`bun run setup` in `~/.byob/`);
  before any bcu work, **prompt the user**: "Do you want to enable computer-use (drives native
  macOS apps for the agent)?". On yes, write the opt-in sentinel
  (`~/.config/valor/computer-use-enabled`), then download the latest bcu binary, install,
  request Accessibility + Screen Recording permissions, and surface any install hiccup to the
  user with an actionable next step. On no, skip bcu entirely (no sentinel written).
- Update `/update` skill: Add BYOB rebuild step; add the bcu **install-or-update** flow gated
  on the opt-in sentinel (fresh install if missing, SHA-compare upgrade if present); always
  alert the user on install/update failure; add Chrome version change detection → force
  `bun run setup`

### 7. Write feature documentation
- **Task ID**: document-feature
- **Depends On**: build-byob-browser, build-computer-skill, build-skill-updates
- **Assigned To**: feature-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/byob-browser-control.md`
- Create `docs/features/computer-use.md`
- Update `docs/features/tools-reference.md`
- Update `docs/features/skills-dependency-map.md`
- Update `docs/features/README.md` index table
- Update `config/personas/segments/tools.md`

### 8. Final validation
- **Task ID**: validate-all
- **Depends On**: build-tests, build-skill-updates, document-feature
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Run: `agent-browser open https://github.com && agent-browser get title` — confirm
  authenticated page (not public GitHub)
- Run: `valor-computer list_windows` on macOS — confirm bcu responds
- Run: `computer-use` on non-macOS platform — confirm OS gate message
- Run: `pytest tools/browser/tests/ tools/computer/tests/ -v -x`
- Run: `python -m ruff check . && python -m ruff format --check .`
- Verify all success criteria are met
- Confirm `docs/plans/telegram_desktop_control.md` status is `Cancelled`

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tools/browser/tests/ tools/computer/tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| BYOB MCP registered | `python -c "import json; d=json.load(open('.mcp.json')); assert 'byob' in d.get('mcpServers',{})"` | exit code 0 |
| computer-use skill exists | `test -f .claude/skills/computer-use/SKILL.md` | exit code 0 |
| telegram_desktop_control cancelled | `grep 'status: Cancelled' docs/plans/telegram_desktop_control.md` | exit code 0 |
| byob_eval disabled | `python -c "import json; d=json.load(open('.mcp.json')); assert d['mcpServers']['byob']['env'].get('BYOB_ALLOW_EVAL','0') == '0'"` | exit code 0 |
| valor-computer CLI exists | `python -c "import tools.computer.cli"` | exit code 0 |
| feature docs created | `test -f docs/features/byob-browser-control.md && test -f docs/features/computer-use.md` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique. Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

_All resolved 2026-05-03 by Tom (issue #1256)._

1. **BYOB MCP integration depth — RESOLVED**: Use the **in-process Anthropic SDK MCP client** to
   call the registered BYOB MCP server. No per-call subprocess shell-out. See `## Technical
   Approach` and the Track 1 Data Flow.

2. **`/setup` scope for bcu — RESOLVED**: Automated download + install of latest bcu, gated on
   an explicit user opt-in prompt ("Do you want to enable computer-use?"). Opt-in writes a
   sentinel at `~/.config/valor/computer-use-enabled`. The `/update` skill checks the same
   sentinel on every run: fresh install if opted-in but missing, SHA-compare upgrade otherwise.
   Any install/update hiccup must surface a clear actionable alert to the user. See
   `## Update System` and Step 6.

3. **`telegram_desktop_control` plan — RESOLVED**: No Telegram-specific workflows are needed.
   General-purpose `computer-use` is sufficient. The `telegram_desktop_control.md` plan is
   cancelled (Step 6) — no follow-on plan.
