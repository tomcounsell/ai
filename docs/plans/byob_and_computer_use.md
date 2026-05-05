---
status: Ready
type: feature
appetite: Large
owner: Valor
created: 2026-05-02
tracking: https://github.com/tomcounsell/ai/issues/1256
last_comment_id: IC_kwDOEYGa088AAAABA-geYQ
revision_applied: true
revision_applied_at: 2026-05-04
revision_addresses: sha256:be258f15a1b2
revision_pass: 4
followups: ["https://github.com/tomcounsell/ai/issues/1274"]
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
- BYOB ships as a **new, additive surface** (MCP tools loaded into the agent context). Existing
  `agent-browser` and `bowser` skills are untouched in this plan — they keep working as anonymous /
  parallel surfaces. Per-skill migration to BYOB happens incrementally in followup issue
  [#1274](https://github.com/tomcounsell/ai/issues/1274), not here.

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

4. **MCP vs CLI shim — connection lifecycle is the real driver, not token cost**:
   The user's GitHub comment on issue #1256 (m13v) reframed this question correctly:
   "the MCP-vs-CLI question isn't really about token cost, it's about connection lifecycle.
   Tools-as-MCP means full schema on every turn but the agent composes sequences inside one
   tool call. CLI shim saves tokens but each invocation respawns auth and re-handshakes the
   extension, which adds latency per step and breaks long sessions when the native messaging
   host gets GC'd." Chrome DevTools MCP (which ships ~40+ tools — see
   `ChromeDevTools/chrome-devtools-mcp` issue #340) confirms verbose schemas have measurable
   token cost, but that is a secondary consideration. The deciding factor is that
   CLI-per-invocation re-handshakes the native messaging host on every call, breaking long
   sequences. **Conclusion: MCP is the correct surface for the agent-facing BYOB integration.**
   The ~30 tools are loaded once per session, not per turn. The `agent-browser` CLI is kept as
   a thin shell-out helper for non-MCP-aware code paths but routes through the same byob-bridge
   socket — it does NOT re-handshake the native messaging host on each call.

5. **Chrome session isolation for parallel worktrees**: One real Chrome DOM tree means parallel
   Claude Code sessions will collide if they both drive Chrome via BYOB. **Rev4 mitigation
   (Decision 2):** Serialize at the worker scheduler layer via a new nullable
   `AgentSession.requires_real_chrome` field — the worker defers concurrent real-Chrome
   sessions at queue-pick time. No filesystem locks. Parallel anonymous work uses `bowser`
   (Playwright headless) as the parallel-anonymous lane.

6. **Electron app AX tree staleness** (source: GitHub issue #1256 comment by m13v): bcu's
   accessibility-tree refs can go stale between query and click on Electron apps (Slack, VS Code,
   Telegram Desktop) where the AX tree builds lazily. The `list_windows`/`get_window_state` →
   `click` path must tolerate stale refs by re-querying on 404 from bcu. This is treated as a
   first-class race in Race Conditions.

7. **`tools/browser/__init__.py` callers**: Verified by grep — the only callers are tests of the
   private `_downscale_if_needed` helper. No production code imports `tools.browser.navigate`
   or any other public function. The "Python wrapper as the seam" framing in the issue body is
   aspirational; in fact this Python module has no relationship to the `agent-browser` CLI binary
   at all. **Rev4 disposition:** Delete the unused `navigate`/`screenshot`/`extract_text`/
   `fill_form`/`click`/`wait_for_element` public wrappers (zero callers), keep
   `_downscale_if_needed` as a Pillow utility, and update the module docstring to clarify it has
   no relationship to `agent-browser` or BYOB. Tests for `_downscale_if_needed` continue to run.

## Spike Results

### spike-1: MCP vs CLI for BYOB — latency and session stickiness
- **Assumption**: "CLI shim per call is feasible if reconnect is fast"
- **Method**: code-read (byob source, issue comment)
- **Finding**: Issue commenter with BYOB implementation experience confirms CLI-per-invocation
  re-handshakes the native messaging host on each call, adding latency and breaking long sequences
  when the host gets GC'd. MCP keeps the connection alive across the session. CLI shim is ruled out.
- **Confidence**: high
- **Impact on plan (rev4)**: MCP is the integration surface. BYOB is registered under
  `mcpServers.byob` in `~/.claude.json` (managed by `scripts/update/mcp_byob.py`, modeled on
  `scripts/update/mcp_memory.py`). The agent reaches BYOB exclusively via the MCP tools — there
  is no separate BYOB CLI in this plan. The 3rd-party `agent-browser` binary on PATH is
  untouched.

### spike-2: shape of the agent-browser CLI binary (rev4)
- **Assumption** (rev3): "The `agent-browser` CLI binary is editable — replace its internals."
- **Method**: shell inspection — `which agent-browser`, `file $(which agent-browser)`, `ls -la`
- **Finding**: `/opt/homebrew/bin/agent-browser` is a symlink to
  `/opt/homebrew/lib/node_modules/agent-browser/bin/agent-browser-darwin-arm64`. That target is
  a **Mach-O 64-bit arm64 binary** (`agent-browser@0.9.1`), distributed via the upstream npm
  package. It is not editable Python source. The rev1/rev2/rev3 plan's "rewrite
  `tools/browser/cli.py`" approach assumed an editable Python entry point that does not exist.
  Separately, `tools/browser/__init__.py` is a Python module with `_downscale_if_needed` plus
  unused `navigate`/`screenshot`/etc. wrappers — it has zero production callers (verified by
  `grep`). It has no relationship to the `agent-browser` CLI binary; that relationship was an
  invented dependency in earlier revisions.
- **Confidence**: high (Mach-O verified by `file`)
- **Impact on plan (rev4)**: Treat `agent-browser` (the CLI binary) as an immutable 3rd-party
  dependency — leave it on PATH, do not edit, fork, or rebuild it. **BYOB is built as a parallel
  surface** (MCP tools registered with Claude Code), not as a replacement at the binary level.
  Skills that currently shell out to `agent-browser` keep working in this plan; they migrate to
  BYOB incrementally via [#1274](https://github.com/tomcounsell/ai/issues/1274). The
  `tools/browser/__init__.py` Python module's docstring is updated to clarify it is a Pillow
  utility (`_downscale_if_needed`) with no relationship to `agent-browser` or BYOB; the unused
  `navigate`/`screenshot`/etc. wrappers are deleted in this plan to honor "no legacy code
  tolerance" (per CLAUDE.md). Tests for `_downscale_if_needed` continue to run.

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

### spike-4: bowser fate — retire or keep as anonymous-Chrome fallback (rev4)
- **Assumption**: "bowser is fully redundant with BYOB"
- **Method**: code-read + issue analysis
- **Finding**: `bowser` uses `playwright-cli` (headless, throwaway profile) — genuinely different
  from BYOB (real Chrome, logged-in). Three skills post-ship cover three distinct surfaces:
  - `agent-browser` (3rd-party CLI, headless Playwright, anonymous, single-tab)
  - `bowser` (headless Playwright, anonymous, parallel — for untrusted-link preview and CI-style
    test runs)
  - **BYOB MCP tools** (real Chrome, logged-in, single Chrome DOM, serialized at scheduler layer
    — see Decision 2)
- **Confidence**: high
- **Impact on plan (rev4)**: All three coexist. Per-skill migration of `agent-browser`-using
  skills to BYOB is tracked in [#1274](https://github.com/tomcounsell/ai/issues/1274), not here.
  The `agent-browser` SKILL.md and binary are untouched in this plan.

## Data Flow

### Track 1 — BYOB browser automation (additive surface)

1. **Entry**: Skill (or agent prompt) invokes a BYOB MCP tool by name (e.g., `byob_navigate`,
   `byob_click`). MCP tools are loaded into the agent context when Claude Code starts and the
   `byob` server is registered in `~/.claude.json`.
2. **Worker scheduler gate** (Decision 2): Before the dev session runs, the worker checks
   whether the session is marked as needing real Chrome (see `## Solution → Scheduler-Layer
   Serialization`). If another real-Chrome session is currently executing, the new session waits
   in the queue rather than starting concurrently. There is no per-process file lock.
3. **BYOB MCP server**: Node process (run via `tsx`) spawned by Claude Code's MCP runtime when
   the agent session loads. Holds a persistent connection to `byob-bridge` over a per-device
   Unix socket under `~/.byob/bridges/<deviceId>.sock`. The MCP server discovers the socket at
   startup; callers must never hardcode a fixed socket path. (Documented during PR #1277 live
   setup — BYOB v0.3+ uses UUID-keyed per-device sockets, not a single `~/.byob/run/byob.sock`
   path that earlier rev plans assumed.)
4. **byob-bridge**: Communicates with the Chrome extension over Native Messaging.
5. **Chrome extension** (MV3): Receives commands, operates on the currently active tab in the
   user's real Chrome session.
6. **Result**: DOM snapshots, screenshots, and interaction results flow back up the chain to the
   MCP client (Claude Code), which surfaces them to the agent.

**Coexistence with `agent-browser` and `bowser`:** Both pre-existing surfaces are untouched in
this plan. Skills that currently shell out to `agent-browser` keep working — they migrate to
BYOB incrementally via [#1274](https://github.com/tomcounsell/ai/issues/1274). `bowser` stays as
the parallel-anonymous lane.

**Failure behavior:** If the BYOB MCP server fails to start (bridge not running, socket missing,
extension not loaded), Claude Code surfaces the MCP startup failure. The agent sees a missing-tool
error when it tries to call `byob_*` tools and routes to `bowser` or surfaces a "BYOB bridge not
running — start Chrome and run `~/.byob/start.sh`" message to the user. There is no Playwright
fallback in the BYOB surface — anonymous Playwright work belongs to `bowser`, and that distinction
is intentional.

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
- **Interface changes**: BYOB exposes ~30 new MCP tools (`byob_navigate`, `byob_click`,
  `byob_screenshot`, etc.) loaded into the agent context. New `tools/computer/__init__.py` and
  `valor-computer` CLI are net-new — no existing code depends on them. **The 3rd-party
  `agent-browser` CLI binary is untouched** (Decision 1; spike-2). `tools/browser/__init__.py`
  Python module loses its unused public wrappers; `_downscale_if_needed` stays.
- **Coupling**: Adds MCP server registration to `~/.claude.json` `mcpServers.byob` (managed
  idempotently by a new `scripts/update/mcp_byob.py` registrar modeled on
  `scripts/update/mcp_memory.py`). Adds one nullable Popoto field to `AgentSession` for the
  scheduler-layer serialization gate (Decision 2). Both are low coupling.
- **Data ownership**: Chrome session state lives in the user's actual Chrome profile (no files
  in repo). bcu state is ephemeral (session IDs in memory). The `AgentSession` field is
  per-session ephemeral state; no historical data migration needed.
- **Reversibility**: BYOB can be disabled by removing the `mcpServers.byob` block from
  `~/.claude.json` (the registrar can do this idempotently) and bcu is removed via
  `rm -rf ~/Applications/BackgroundComputerUse.app && rm -f ~/.local/bin/background-computer-use`.
  The `AgentSession` scheduler field is nullable — clearing it is a no-op for sessions that don't
  need real Chrome. Reasonably reversible.

## Appetite

**Size:** Large

**Team:** Solo dev + PM check-ins

**Interactions:**
- PM check-ins: 2 (scope alignment on BYOB MCP wiring, sign-off on bowser fate)
- Review rounds: 1 (code review of MCP integration and tools/computer/ module)

## Prerequisites

The Prerequisites table only gates on **operator-required** state — things the build itself
cannot create. Build-installable dependencies (`bun`, `~/.byob` clone) are handled inside
`build-byob-mcp` (Step 1) using the `command -v X || install_command` pattern, not as gating
prereqs. bcu install is **not a build gate** — the integration tests skip when the manifest is
absent, and the OS gate in `tools.computer.cli:main` covers manifest-missing at runtime.

Each Check Command is a single shell command with no escaped-pipe gymnastics — the parser at
`scripts/check_prerequisites.py:58` splits cells on raw `|`.

| Requirement | Check Command | Install Command (if missing) | Purpose |
|-------------|---------------|------------------------------|---------|
| Chrome (not Chromium) | `test -d "/Applications/Google Chrome.app"` | `brew install --cask google-chrome` | BYOB targets real Chrome |
| Node.js ≥18 | `node -e "process.exit(parseInt(process.versions.node) < 18 ? 1 : 0)"` | `brew install node` | BYOB build dep |

Run all checks: `python scripts/check_prerequisites.py docs/plans/byob_and_computer_use.md`

### Operator Setup (out of build scope)

These items require operator interaction (Chrome extension click-through, System Settings
permission grants) and are **not** required for the build to succeed. The BYOB extension is
installed during `bun run setup` (Step 1, build-byob-mcp); bcu install is delegated to the
`/setup` skill (Step 7, build-skill-updates) which prompts the user before doing anything.
Integration tests that require these are marked `@pytest.mark.integration` and skip cleanly
when the underlying state is absent.

| Operator Step | When | Provides |
|---------------|------|----------|
| Click "Load extension" in Chrome (BYOB MV3) → fully quit & reopen Chrome (`⌘Q`) | After `bun run setup` runs in Step 1 | Bridge process activation; per-device socket appears under `~/.byob/bridges/<deviceId>.sock` (verify with `cd ~/.byob && bun run doctor`) |
| Run `/setup` and answer "yes" to computer-use opt-in | Anytime after build merges | Downloads + installs `BackgroundComputerUse.app` |
| Grant Accessibility permission to bcu | First bcu launch | `osascript`-callable AX |
| Grant Screen Recording permission to bcu | First bcu launch | bcu can screenshot windows |

## Solution

### Key Elements

- **BYOB MCP server** registered in `~/.claude.json` under `mcpServers.byob`: Exposes real-Chrome
  automation tools (`byob_navigate`, `byob_click`, `byob_screenshot`, etc.) to Claude Code as MCP
  tools. This is the **only** agent-facing surface for BYOB — there is no separate BYOB-backed
  CLI in this plan. The 3rd-party `agent-browser` binary on PATH is untouched (Decision 1).
- **`scripts/update/mcp_byob.py` registrar**: New module modeled directly on
  `scripts/update/mcp_memory.py`. Idempotently writes the `mcpServers.byob` block to
  `~/.claude.json` under `fcntl.flock(LOCK_EX | LOCK_NB)` on `~/.claude.json.lock` with the same
  3-attempt backoff pattern (50ms / 200ms / 800ms) `mcp_memory.py` uses. Wired into
  `scripts/update/run.py` alongside `mcp_memory`.
- **Scheduler-layer serialization** (Decision 2): New nullable Popoto field
  `requires_real_chrome: bool` on `AgentSession`. The worker's session-pick loop in
  `worker/__main__.py` checks this flag at session-start time — if any currently-running session
  has `requires_real_chrome=True`, the new candidate session waits in the queue rather than
  starting concurrently. Replaces the rev3 `flock(2)` design entirely.
- **`tools/browser/__init__.py` Python module**: Unused public wrappers
  (`navigate`, `screenshot`, `extract_text`, `fill_form`, `click`, `wait_for_element`) are
  **deleted** — zero production callers per spike-2. `_downscale_if_needed` is preserved as a
  Pillow utility. Module docstring rewritten to clarify it is unrelated to `agent-browser` or
  BYOB. `tools/browser/manifest.json` and `tools/browser/README.md` are updated to match (or
  deleted entirely if the post-edit module surface is just `_downscale_if_needed`).
- **`agent-browser` skill, binary, and existing SKILL.md**: Untouched in this plan. Per-skill
  migration to BYOB happens in [#1274](https://github.com/tomcounsell/ai/issues/1274).
- **`bowser` SKILL.md**: Untouched in this plan. Stays as the parallel-anonymous lane.
- **`tools/computer/__init__.py`**: New module. Wraps bcu loopback HTTP API. Functions:
  `list_apps`, `list_windows`, `get_window_state`, `click`, `scroll`, `type_text`, `press_key`,
  `set_value`, `perform_secondary_action`, `drag`, `resize`, `set_window_frame`,
  `screenshot_window`. Reads base URL from
  `$TMPDIR/background-computer-use/runtime-manifest.json`. Returns `dict` results (success or
  `{"error": ...}`). Raises `ComputerUseUnavailableError` if manifest not found (OS-gate).
  **Selector-aware API for Electron apps** (rev3 C3): when the target window's bundle_id
  matches a known Electron app, callers may pass a `selector={'role': '...', 'label': '...',
  'bounds': (...)}` dict instead of a raw AX ref; the module re-queries `get_window_state`
  internally and resolves the selector to a fresh ref before each action.
- **`computer-use` skill** at `.claude/skills/computer-use/SKILL.md`: New skill. macOS-only.
  Wraps `tools/computer/` for agent use. Documents OS-gate behavior.
- **`/setup` skill** updated: BYOB extension install + native messaging registration; bcu download
  + Accessibility + Screen Recording permission prompts.
- **`/update` skill** updated: Pull BYOB repo and rebuild extension; re-register native messaging
  host if version changed; re-download bcu binary if SHA mismatch. Calls
  `scripts/update/mcp_byob.py` to verify/heal the `~/.claude.json` registration.
- **`telegram_desktop_control` plan** status updated to `Cancelled`: superseded by this work.

### Flow

**Browser automation (logged-in, real Chrome)**
Agent invokes `byob_*` MCP tool → BYOB MCP server → byob-bridge → Chrome extension → user's
real tab → result back to agent. Worker has already serialized this session at queue-pick time
based on `AgentSession.requires_real_chrome`.

**Browser automation (anonymous/parallel)**
Skill invokes `agent-browser` or `bowser` (unchanged). Untouched in this plan.

**Desktop automation**
`computer-use` skill calls `valor-computer list_windows` → `tools/computer/__init__.py` reads
manifest → bcu HTTP → macOS Accessibility API → window list returned → skill selects target →
calls `valor-computer click` (with selector for Electron apps) → bcu performs action without
stealing cursor → result returned.

### Technical Approach

- **BYOB MCP server registration via `~/.claude.json`**: The repo does **not** ship a `.mcp.json`
  file; Claude Code reads MCP server config from `~/.claude.json` under `mcpServers`. The new
  `scripts/update/mcp_byob.py` registrar adds:
  ```json
  "mcpServers": {
    "byob": {
      "type": "stdio",
      "command": "~/.byob/packages/mcp-server/node_modules/.bin/tsx",
      "args": ["~/.byob/packages/mcp-server/bin/byob-mcp.ts"],
      "env": { "BYOB_ALLOW_EVAL": "0" }
    }
  }
  ```
  (BYOB v0.3+ ships its MCP server as a TypeScript entry executed via `tsx`. Both paths resolve
  inside the BYOB workspace after `bun install` runs in `~/.byob/`. This matches BYOB's own
  "Manual MCP registration" recipe in upstream README.)
  The registrar holds `fcntl.flock(LOCK_EX | LOCK_NB)` on `~/.claude.json.lock` with the same
  3-attempt backoff (50 ms / 200 ms / 800 ms) used by `scripts/update/mcp_memory.py:46`,
  because `~/.claude.json` is rewritten by the Claude Code harness on every session event and
  direct rename without the lock will race with the harness and corrupt the 5400-line file. The
  registrar is wired into `scripts/update/run.py` alongside `mcp_memory` so every `/update`
  invocation re-verifies the registration and self-heals drift.

- **Scheduler-layer serialization** (Decision 2): The worker session-pick loop adds one check:
  ```python
  # Pseudocode in worker/__main__.py session-pick path
  if candidate.requires_real_chrome:
      if any(s.requires_real_chrome for s in currently_running_sessions):
          continue  # defer this candidate; pick the next
  ```
  Sessions set `requires_real_chrome=True` at creation time when:
  - The dev session's plan or prompt declares the work needs BYOB (e.g., `/do-build` for a plan
    whose tasks reference `byob_*` MCP tools), OR
  - The session is created with `--needs-real-chrome` on `valor-session create`, OR
  - A future hook detects `byob_*` tool invocation in-flight and steers the session — out of
    scope for this plan; the explicit-creation path is enough.

  The Popoto field is added to `models/agent_session.py` as a nullable `bool` (default `False`).
  Per memory `feedback_field_backcompat_heal` (issues #1099, #1172), no extra backcompat code
  is needed; `_heal_descriptor_pollution` walks all fields generically.

  **MCP and CLI both** route through the same scheduler-aware session, so the rev3 "MCP-vs-CLI
  precedence" question is moot — there is exactly one queue, regardless of which surface
  initiated the request.

- **`tools/browser/__init__.py` cleanup**: Delete the unused `navigate`, `screenshot`,
  `extract_text`, `fill_form`, `click`, `wait_for_element` public functions (zero production
  callers per spike-2). Keep `_downscale_if_needed`. Update the module docstring to clarify it
  is a Pillow utility unrelated to `agent-browser` or BYOB. Update or delete
  `tools/browser/README.md` and `tools/browser/manifest.json` to match the post-cleanup
  surface — no half-states.

- **`tools/computer/` HTTP client**: Use `urllib.request` (stdlib) to avoid new dependencies.
  Each function reads manifest, constructs request, handles `ConnectionRefusedError` → converts
  to `ComputerUseUnavailableError`. Timeout: 10s per call.

- **OS gate**: Enforced in `tools.computer.cli:main` (the `valor-computer` entry point), not in
  the SKILL.md (skills are markdown and cannot execute Python). On `sys.platform != "darwin"`
  the CLI prints `"computer-use is macOS-only. This machine runs {platform}; skipping."` to
  stderr and exits 78 (`EX_CONFIG`). The SKILL.md documents this behavior so the agent knows to
  expect the message; tests in `tools/computer/tests/test_computer_use.py` cover both branches
  by patching `sys.platform`.

- **Selector-aware Electron API** (rev3 C3): For target windows whose bundle_id matches a known
  Electron app (`com.tinyspeck.slackmacgap`, `com.microsoft.VSCode`, `org.telegram.desktop`,
  `com.hnc.Discord`, etc. — list lives in `tools/computer/electron_bundles.py`), `tools/computer`
  click/set_value/drag accept a `selector` argument: `selector={'role': 'button',
  'label': 'Send', 'bounds': (x, y, w, h)}`. The module re-queries `get_window_state`
  internally before each action and resolves the selector to a fresh AX ref. For non-Electron
  apps, raw AX refs are accepted as before.

- **Downstream skill migration**: **Out of scope for this plan.** The `agent-browser` CLI binary
  stays on PATH and skills that use it keep working unchanged. Per-skill migration to BYOB is
  tracked in [#1274](https://github.com/tomcounsell/ai/issues/1274).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] **BYOB MCP server startup failure**: When the BYOB bridge is not running (Chrome not open,
  extension not loaded, or `cd ~/.byob && bun run doctor` reports any red status), `byob` MCP
  server start must fail with a clear stderr message that Claude Code can surface. Test: kill
  the bridge process (`pkill -f byob-bridge.ts`), start a fresh agent session, assert the MCP
  server entry shows a startup error and the `byob_*` tools are absent from the agent context.
  (Per-device socket path under `~/.byob/bridges/<deviceId>.sock` is discovered by the MCP
  server at startup — never hardcode the path.)
- [ ] **`scripts/update/mcp_byob.py` lock contention**: When `~/.claude.json.lock` is held,
  the registrar must use the 3-attempt backoff (50ms / 200ms / 800ms) — same pattern as
  `mcp_memory.py`. Test: hold the lock in a fixture, call the registrar, assert it retries and
  eventually fails with a `LockContended` error rather than blocking indefinitely.
- [ ] **Scheduler-layer serialization** (Decision 2): When session A has
  `requires_real_chrome=True` and is running, queueing session B with
  `requires_real_chrome=True` must defer B until A finishes. Test (`tests/integration/`): create
  two such sessions, assert worker picks A, runs to completion, then picks B; confirm B did not
  start while A was running by checking session start_at timestamps.
- [ ] **Scheduler does NOT serialize unrelated sessions**: A session with
  `requires_real_chrome=False` must not be deferred while a real-Chrome session runs. Test:
  create a real-Chrome session and an ordinary session, assert both run concurrently.
- [ ] `tools/computer/__init__.py` must raise `ComputerUseUnavailableError` when the runtime
  manifest is absent. Test: call any function when manifest does not exist.

### Empty/Invalid Input Handling
- [ ] `tools/computer.click(window_id=None, x=0, y=0)` → `ComputerUseUnavailableError` or
  `ValueError` (not a silent no-op).
- [ ] `tools/computer.type_text(window_id=1, text="")` → success (empty string is valid).
- [ ] `tools/computer.click(window_id=1, selector={})` → `ValueError` (empty selector is invalid).

### Error State Rendering
- [ ] `computer-use` skill body must surface bcu errors to the agent output, not swallow them.
  The skill should print the raw error dict when `tools/computer` returns `{"error": ...}`.
- [ ] **BYOB-down behavior at the agent layer**: When the BYOB MCP server fails to start, the
  agent sees missing-tool errors when it tries to call `byob_*` tools. The agent surfaces this
  to the user as "BYOB bridge not running — start Chrome and run `~/.byob/start.sh`" rather than
  silently retrying. There is no Playwright fallback in the BYOB surface.

## Test Impact

- [ ] `tools/browser/tests/test_agent_browser.py` — **DELETE OR UPDATE**: Existing tests exercise
  `agent-browser` CLI behavior, which is **untouched in this plan**. If the file tests behavior
  the 3rd-party CLI guarantees, delete it (we don't test our 3rd-party deps). If it tests
  `tools/browser/__init__.py` Python wrappers being deleted in this plan, delete those test
  cases. Final disposition decided during build by reading each test case.
- [ ] `tools/browser/tests/test_downscale.py` — **NO CHANGE**: Pure unit test of
  `_downscale_if_needed`. Unaffected.
- [ ] `tests/happy-paths/SCHEMA.md` — **NO CHANGE** in this plan. The discovery stage still uses
  `agent-browser`. (Updated separately under [#1274](https://github.com/tomcounsell/ai/issues/1274)
  when `do-discover-paths` migrates.)
- [ ] **New: `tests/integration/test_byob_scheduler.py`** — covers Decision 2:
  - Two sessions with `requires_real_chrome=True` are serialized.
  - One real-Chrome session and one ordinary session run concurrently.
  - The Popoto field round-trips through `AgentSession.save()` and `.query.filter()` correctly.
- [ ] **New: `tests/unit/test_mcp_byob_registrar.py`** — covers `scripts/update/mcp_byob.py`:
  - Idempotent write (running twice is a no-op).
  - Lock contention triggers the 3-attempt backoff.
  - Drift-heal: pre-existing wrong values get corrected.
  - Modeled on whatever pattern `tests/unit/test_mcp_memory_registrar.py` uses (or its
    equivalent).
- [ ] **New: `tools/computer/tests/test_computer_use.py`** — unit tests mocking bcu HTTP
  responses, including Electron selector-resolution path (rev3 C3) and OS gate.
- [ ] **New: `tools/computer/tests/test_computer_use_integration.py`** — live bcu calls, marked
  `@pytest.mark.integration`, includes Notes.app end-to-end smoke.

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
**Impact:** BYOB MCP tools stop working when Chrome auto-updates and the extension API surface
changes.
**Mitigation:** Pin BYOB to a specific git commit in `config/byob_pin.json` (only bumped via
`/update --bump-byob`). After any BYOB rebuild, `/update` runs an **end-to-end** post-install
canary (rev3 C2): a small Node script (`scripts/update/byob_canary.js`) discovers the active
per-device socket under `~/.byob/bridges/<deviceId>.sock` (not a fixed path — BYOB v0.3+ uses
UUID-keyed sockets, see `cd ~/.byob && bun run doctor` for the canonical discovery), connects,
sends a `byob_navigate('about:blank')` followed by a `byob_get_title` round-trip with
`socket.settimeout(30)`, and asserts both succeed. A stale socket file from a crashed previous
run will fail `connect()` — that is treated as a canary failure (not as "socket not yet
ready"). On canary failure, restore the previous BYOB tree from `~/.byob.prev/`
(`rm -rf ~/.byob && mv ~/.byob.prev ~/.byob`) and surface an alert. (BYOB does not expose CDP
on a TCP port; the legacy `agent-browser connect 9222` workflow is unrelated to this plan.)

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

### Race 1: Two real-Chrome sessions try to run simultaneously
**Location:** Worker session-pick loop in `worker/__main__.py`
**Trigger:** Two `AgentSession` records with `requires_real_chrome=True` are queued at roughly
the same time (e.g., parallel `valor-session create` calls, or two PM-spawned dev sessions
both needing BYOB).
**Data prerequisite:** Chrome has one DOM tree; concurrent access from two MCP clients corrupts
active-tab state.
**State prerequisite:** BYOB MCP server is running; two sessions are eligible to be picked.
**Mitigation (Decision 2):** Worker session-pick loop refuses to start a second
`requires_real_chrome=True` session while one is currently running — defers to the next pick
cycle. No file locks; no per-process collision guard. **MCP and CLI both** route through the
same scheduler-aware session, so a single queue is the source of truth regardless of which
surface initiated the request. Memory `feedback_field_backcompat_heal` confirms the new Popoto
field needs no migration code; `_heal_descriptor_pollution` walks fields generically.

### Race 2: bcu window ID goes stale between `list_windows` and `click`
**Location:** `tools/computer/__init__.py` — any action after a `list_windows` call
**Trigger:** App closes or minimizes between the window-list query and the subsequent action call.
**Data prerequisite:** Window ID from `list_windows` must still correspond to a valid window at
click time.
**State prerequisite:** Target application window is still open.
**Mitigation:** bcu returns HTTP 404 for stale window IDs. `tools/computer/__init__.py` catches
`404` responses and converts them to `{"error": "window_not_found", "window_id": N}`. Skills
must re-call `list_windows` and retry.

### Race 3: Electron AX tree refs go stale between `get_window_state` and `click`
**Location:** `tools/computer/__init__.py` — any AX-ref-based action after `get_window_state`
on an Electron app (Slack, VS Code, Telegram Desktop, Discord)
**Trigger:** Electron apps build their AX tree lazily. A scroll, modal open, or DOM update
inside the Electron renderer between the query and the action invalidates the AX node ref —
the window itself stays open, so HTTP 404 doesn't fire, but the click lands on the wrong
element or no element at all.
**Data prerequisite:** AX node ref returned by `get_window_state` must still correspond to the
intended UI element when `click` fires.
**State prerequisite:** Target window is still open and visible.
**Mitigation:** When acting on an Electron app, `tools/computer/__init__.py` re-queries
`get_window_state` immediately before each action and resolves the target by a stable property
(role + label + bounds) rather than caching the ref across actions. Document this in
`computer-use` SKILL.md as a usage rule for Electron targets. The `is_electron` heuristic:
match the bcu `bundle_id` against a known list (`com.tinyspeck.slackmacgap`,
`com.microsoft.VSCode`, `org.telegram.desktop`, `com.hnc.Discord`, etc.) and add a config knob
in the SKILL.md for additions.

## No-Gos (Out of Scope)

- Committing cookies, auth tokens, or `state.json` files to the repo — ever.
- Supporting BYOB with non-Chrome browsers (Firefox, Safari, Arc).
- Making `computer-use` work on Linux or Windows.
- Custom Chrome extension development — use BYOB upstream as-is.
- Retrofitting the old `agent-desktop` CLI approach from `telegram_desktop_control` plan.
- Parallel BYOB sessions on the same Chrome (one real Chrome DOM tree — scheduler serialization
  is the correct model; rev4 Decision 2).
- **Editing, forking, or rebuilding the 3rd-party `agent-browser` CLI binary.** It stays on PATH,
  untouched. Per-skill migration to BYOB is tracked separately in
  [#1274](https://github.com/tomcounsell/ai/issues/1274). (Decision 1, rev4)
- **Per-process file locks** (`flock(2)` on `~/.byob/session.lock` or similar). Serialization
  lives at the worker scheduler layer (Decision 2, rev4) — there is exactly one queue, regardless
  of whether a session reaches BYOB via MCP or CLI.
- Backwards-compatibility shims — per CLAUDE.md "no legacy code tolerance". Replace cleanly.
- Supporting `BYOB_ALLOW_EVAL=1` by default. `browser_eval` stays disabled.

## Update System

The `/update` skill (`scripts/remote-update.sh` and `scripts/update/run.py`) must be extended
with:

0. **BYOB MCP registration verification**: New step in `scripts/update/run.py` that calls
   `scripts/update/mcp_byob.py` (modeled on `scripts/update/mcp_memory.py`). The registrar holds
   `fcntl.flock(LOCK_EX | LOCK_NB)` on `~/.claude.json.lock` with the same 3-attempt backoff
   (50ms / 200ms / 800ms), idempotently writes the `mcpServers.byob` block, and self-heals
   drift. Runs on every `/update` invocation regardless of whether BYOB itself is being rebuilt.

1. **BYOB update step**: After pulling main, check if BYOB pinned commit has changed
   (`config/byob_pin.json` holds the pinned upstream commit SHA). If different from
   `git -C ~/.byob rev-parse HEAD`:
   - **Snapshot the entire `~/.byob/` tree** to `~/.byob.prev/` for rollback. BYOB v0.3+ is a
     workspace monorepo with build artifacts under `packages/*/output/` and `packages/*/dist/`
     — there is no single top-level `dist/` to copy. Snapshot the whole tree.
   - `git -C ~/.byob fetch && git -C ~/.byob checkout <pinned-sha>`.
   - Run `bun install && bun run setup` in `~/.byob/`. (`bun run setup` builds the extension,
     re-registers the native messaging host, and prompts for MCP-client registration — press
     enter through the registration prompt; we use `scripts/update/mcp_byob.py` for that.)
   - **Post-install canary** (end-to-end, rev3 C2): First run `cd ~/.byob && bun run doctor` —
     it must report all green (manifest, launcher, bridge process, IPC socket). Then run
     `scripts/update/byob_canary.js` which discovers the per-device socket under
     `~/.byob/bridges/<deviceId>.sock`, connects, and asserts a
     `byob_navigate('about:blank')` + `byob_get_title` round-trip succeeds within 30s. On
     canary failure, restore the previous tree (`rm -rf ~/.byob && mv ~/.byob.prev ~/.byob`)
     and alert the user.
   - Only bump the pin via `/update --bump-byob`.
2. **bcu update check + install step**:
   - First, detect whether bcu is opted-in on this machine (sentinel: `~/.config/valor/computer-use-enabled`,
     written by `/setup` only after the user confirms).
   - **Pinned upstream source**: Track the latest stable release from
     `https://api.github.com/repos/actuallyepic/background-computer-use/releases/latest`. Resolve
     the `.dmg` (or `.zip`) asset URL from the release JSON. Pin the **release tag** in
     `config/bcu_pin.json` (committed to repo) — `/update` reads this to know what version to
     install, and only bumps it when explicitly told via `/update --bump-bcu`. This prevents
     silent breakage from upstream changes between deploys.
   - If opted in but not installed, treat as a fresh install: download the asset for the pinned
     release, verify SHA against the GitHub-published `.sha256` checksum file (release asset),
     install to `~/Applications/BackgroundComputerUse.app` (the standard upstream location) and
     `~/.local/bin/background-computer-use` symlink, and prompt for Accessibility + Screen
     Recording permission.
   - If already installed, compare the installed binary's SHA against the SHA from the pinned
     release. If different, download and replace; re-prompt for Accessibility permission only if
     the bundle identity changed.
   - On any install/update hiccup (download fail, SHA mismatch, permission missing), surface a
     clear, actionable alert to the user (e.g., "bcu update failed: SHA mismatch — skipping;
     run `/setup` to retry"). Never fail the rest of the update silently.
   - **Rollback path**: Keep the previous binary at `~/.local/bin/background-computer-use.prev`
     during update. If the new binary fails its post-install canary (`/v1/list_apps` returns
     HTTP 200 within 5s), restore the previous binary and surface an alert.
3. **Chrome version check**: Read Chrome version. If changed since last update, force re-run
   of `bun run setup` to ensure native messaging registration is fresh.

All bcu and BYOB steps are macOS-only (guard: `[[ "$(uname)" == "Darwin" ]]`). On non-macOS
machines, the update script no-ops these steps and prints a one-line note.

## Agent Integration

- **BYOB as MCP server**: Register `byob` under `mcpServers` in `~/.claude.json` via the new
  `scripts/update/mcp_byob.py` registrar. Claude Code loads BYOB MCP tools (`byob_navigate`,
  `byob_click`, `byob_screenshot`, etc.) into the agent context when the server is active. **No
  CLI entry point is added in this plan** — the agent reaches BYOB exclusively through MCP
  tools.
- **Scheduler-layer browser-use serialization** (Decision 2): The agent never has to think about
  serialization. The worker session-pick loop reads `AgentSession.requires_real_chrome` and
  defers concurrent real-Chrome sessions automatically. Two surfaces (manual `valor-session
  create --needs-real-chrome` and inferred-from-plan) set the flag at session creation time.
- **`tools/computer/` Python module + `valor-computer` CLI**: The skill invokes
  `valor-computer <command>` via Bash. The CLI is declared as a `[project.scripts]` entry in
  `pyproject.toml`:
  ```toml
  valor-computer = "tools.computer.cli:main"
  ```
  This is the only invocation pattern for the skill — `python -m tools.computer ...` is not
  used (it would bypass the entry-point shim and complicate the OS gate). All bcu HTTP calls
  happen inside `tools.computer.cli:main` and the underlying `tools/computer/` functions; the
  skill never speaks HTTP directly.
- **`agent-browser` skill and existing skills using it**: **Untouched in this plan.** The
  `agent-browser` binary stays on PATH; skill files are unchanged. Migration to BYOB is tracked
  in [#1274](https://github.com/tomcounsell/ai/issues/1274).
- **Integration test**: After setup, the agent calling `byob_navigate('https://github.com')` +
  `byob_get_title` should return the user's GitHub notifications page (logged-in view), not the
  public homepage.

## Documentation

- [ ] Create `docs/features/byob-browser-control.md` describing BYOB integration, the
  BYOB / `agent-browser` / `bowser` decision guide (real-Chrome / anonymous-headless /
  parallel-headless), the scheduler-layer serialization model (Decision 2), and known
  limitations (block-list, one-session-at-a-time serialization).
- [ ] Create `docs/features/computer-use.md` describing bcu integration, macOS-only OS gate,
  permission requirements, the Electron selector-aware API, and example skill workflows.
- [ ] Update `docs/features/tools-reference.md`: Add `tools/computer/` section; add a BYOB MCP
  tools section. Do **not** modify the `agent-browser` entry — that migration is tracked in
  [#1274](https://github.com/tomcounsell/ai/issues/1274).
- [ ] Update `docs/features/skills-dependency-map.md`: Add `computer-use` skill node and BYOB
  MCP node. Leave `agent-browser` node untouched.
- [ ] Add entry to `docs/features/README.md` index table for both new feature docs.
- [ ] Update `config/personas/segments/tools.md`: Add a BYOB section and a `computer-use`
  section. Leave the `agent-browser` section as is — it stays accurate until #1274 lands.
- [ ] Update `docs/plans/telegram_desktop_control.md` status to `Cancelled` with note:
  "Superseded by docs/plans/byob_and_computer_use.md — Track 2 (computer-use skill)."
- [ ] Cross-link this plan ↔ #1274 in both directions (this plan's frontmatter `followups` is
  set; the followup issue references this plan as its blocker).

## Success Criteria

**Technical:**
- [ ] BYOB MCP tools (`byob_navigate`, `byob_get_title`, `byob_screenshot`, etc.) are loaded into
  the agent context after `/update` runs and Chrome is open with the BYOB extension. `byob_navigate('https://github.com')` + `byob_get_title` returns the user's
  authenticated GitHub view (logged-in page, not public homepage) — zero `state.json` files
  committed to repo.
- [ ] `computer-use` skill can list apps, list windows, click, type, and screenshot `Notes.app`
  on macOS without moving the user's cursor.
- [ ] **Existing `agent-browser`-using skills** (`do-design-audit`, `do-pr-review`, `linkedin`,
  `mermaid-render`, `do-discover-paths`, `prepare-app`, `do-test`, `do-design-system`) keep
  passing their existing invocation patterns **without modification** — confirming this plan
  is purely additive. (Migration of these skills to BYOB is tracked in
  [#1274](https://github.com/tomcounsell/ai/issues/1274).)
- [ ] `computer-use` invoked on non-macOS machine exits 78 with stderr containing "computer-use
  is macOS-only", not confusing output.
- [ ] `/setup` skill prompts for BYOB extension install and bcu Accessibility + Screen Recording
  permissions.
- [ ] `/update` re-runs BYOB rebuild and bcu SHA check on each pull when pin changes; runs the
  `mcp_byob.py` registrar on every invocation.
- [ ] `BYOB_ALLOW_EVAL` is unset by default in the `~/.claude.json` `mcpServers.byob.env` entry.
- [ ] bcu HTTP server documented as loopback-only in `computer-use` SKILL.md.
- [ ] `docs/plans/telegram_desktop_control.md` status is `Cancelled`.
- [ ] `config/byob_pin.json` and `config/bcu_pin.json` exist and pin specific upstream versions.
- [ ] **`AgentSession.requires_real_chrome` field** exists on the Popoto model and is honored by
  the worker session-pick loop — verified by `tests/integration/test_byob_scheduler.py`.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).

**User-facing (manual verification):**
- [ ] **Cursor not stolen during bcu actions**: With user actively typing in another app, run
  `valor-computer click <Notes-window-id> 100 200` and confirm the user's keystrokes continue
  to land in the original app and the cursor does not jump.
- [ ] **Two real-Chrome sessions serialized**: Create two `AgentSession`s with
  `requires_real_chrome=True` at roughly the same time. Confirm via the dashboard or logs that
  the second waited for the first to finish before starting — not concurrent execution, not a
  hard error.
- [ ] **BYOB-down clarity**: Stop Chrome, then have the agent attempt a `byob_*` MCP call.
  Confirm the user-facing message reads "BYOB bridge not running — start Chrome and run
  `~/.byob/start.sh`" — no silent retry, no Playwright fallback.
- [ ] **Electron selector retry**: Drive Slack via `valor-computer` with a `selector` argument —
  open a channel, click a message, scroll to invalidate AX refs, click again. Confirm the second
  click resolves correctly via the selector → fresh-ref re-query path.

## Team Orchestration

### Team Members

- **Builder (byob-integration)**
  - Name: byob-builder
  - Role: BYOB MCP registration via `scripts/update/mcp_byob.py` (modeled on `mcp_memory.py`),
    `~/.byob/` install + `bun run setup`, BYOB end-to-end canary script,
    `AgentSession.requires_real_chrome` Popoto field + worker scheduler check
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
  - Role: Cancel `telegram_desktop_control` plan; update `/setup` and `/update` skills with
    BYOB + bcu install steps and the `mcp_byob.py` registrar invocation; clean up
    `tools/browser/__init__.py` (delete unused public wrappers, keep `_downscale_if_needed`,
    update docstring). **Does not touch** `.claude/skills/agent-browser/SKILL.md` or
    `.claude/skills/bowser/SKILL.md` — that work is in #1274.
  - Agent Type: builder
  - Resume: true

- **Test Engineer (browser + scheduler)**
  - Name: browser-test-engineer
  - Role: Add `tests/integration/test_byob_scheduler.py` (Decision 2), add
    `tests/unit/test_mcp_byob_registrar.py`, add `tools/computer/tests/` suite. Audit
    `tools/browser/tests/test_agent_browser.py` and delete or update test cases to match the
    cleaned-up Python module.
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

### 1. Install BYOB and register the MCP server in `~/.claude.json`
- **Task ID**: build-byob-mcp
- **Depends On**: none
- **Validates**: `~/.claude.json` `mcpServers.byob` entry exists with `BYOB_ALLOW_EVAL=0`;
  `bun run setup` succeeds in `~/.byob/`; `config/byob_pin.json` holds the pinned upstream
  commit SHA; `tests/unit/test_mcp_byob_registrar.py` passes (idempotency, lock contention,
  drift heal)
- **Informed By**: spike-1 (MCP is correct surface), Decision 1 (no CLI swap), rev3 critique B1
  (registration goes in `~/.claude.json`, not `.mcp.json`)
- **Assigned To**: byob-builder
- **Agent Type**: builder
- **Parallel**: true
- Ensure `bun` is installed: `command -v bun || curl -fsSL https://bun.sh/install | bash`
- Clone `wxtsky/byob` to `~/.byob/`, check out the pinned commit, run
  `bun install && bun run setup`
- Create `config/byob_pin.json` with `{"commit": "<sha>", "checked_at": "<iso8601>"}`
- Create `scripts/update/mcp_byob.py` modeled on `scripts/update/mcp_memory.py`:
  - Holds `fcntl.flock(LOCK_EX | LOCK_NB)` on `~/.claude.json.lock` with the same 3-attempt
    backoff (50ms / 200ms / 800ms)
  - Idempotently writes the `mcpServers.byob` block with `BYOB_ALLOW_EVAL=0`
  - Self-heals drift on every invocation
- Wire `mcp_byob.py` into `scripts/update/run.py` alongside `mcp_memory`
- Run the registrar; verify `~/.claude.json` `mcpServers.byob` is present
- Verify BYOB is healthy after Chrome + extension are running by `cd ~/.byob && bun run doctor` (must report green for manifest, launcher, bridge process, and per-device IPC socket under `~/.byob/bridges/<deviceId>.sock`)

### 1b. Add `AgentSession.requires_real_chrome` field + worker scheduler check
- **Task ID**: build-scheduler-gate
- **Depends On**: build-byob-mcp
- **Validates**: `tests/integration/test_byob_scheduler.py` passes — two real-Chrome sessions
  serialize, mixed sessions run concurrently
- **Informed By**: Decision 2 (scheduler-layer serialization, no flock)
- **Assigned To**: byob-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `requires_real_chrome: bool` (nullable, default `False`) to `models/agent_session.py`.
  Per memory `feedback_field_backcompat_heal`, no extra backcompat code needed —
  `_heal_descriptor_pollution` walks fields generically.
- Add `--needs-real-chrome` flag to `tools/valor_session/create.py` that sets the field at
  session creation
- Modify `worker/__main__.py` session-pick loop: before starting a candidate, if
  `candidate.requires_real_chrome` is True, check whether any currently-running session has
  `requires_real_chrome=True`; if so, skip this candidate and pick the next one
- Add the necessary observation to the dashboard (`localhost:8500/dashboard.json`) so the
  serialization state is visible to the user (memory `reference_dashboard_json`)

### 2. Clean up `tools/browser/__init__.py` Python module
- **Task ID**: build-tools-browser-cleanup
- **Depends On**: none
- **Validates**: `tools/browser/__init__.py` no longer exposes unused public wrappers; module
  docstring clarifies it is a Pillow utility unrelated to `agent-browser` or BYOB;
  `tools/browser/tests/test_downscale.py` still passes
- **Informed By**: spike-2 (rev4) — module has zero production callers; Decision 1 — module is
  unrelated to the `agent-browser` binary
- **Assigned To**: skill-builder
- **Agent Type**: builder
- **Parallel**: true
- Delete the unused public wrappers (`navigate`, `screenshot`, `extract_text`, `fill_form`,
  `click`, `wait_for_element`) from `tools/browser/__init__.py`
- Keep `_downscale_if_needed` as a Pillow utility
- Rewrite the module docstring to read approximately: "Pillow utilities. Unrelated to the
  `agent-browser` 3rd-party CLI on PATH and unrelated to the BYOB MCP surface."
- Update `tools/browser/manifest.json` and `tools/browser/README.md` to match the post-cleanup
  surface — or delete them entirely if the post-edit module is just `_downscale_if_needed`.
  No half-states. (Per CLAUDE.md "no legacy code tolerance".)
- The 3rd-party `agent-browser` binary at `/opt/homebrew/bin/agent-browser` is **not touched**.

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
- **Validates**: `valor-computer list_apps` on non-macOS exits 78 with the documented message;
  `.claude/skills/computer-use/SKILL.md` exists and references `valor-computer` (not `python
  -m tools.computer`).
- **Informed By**: spike-3, research finding on bcu endpoints
- **Assigned To**: computer-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `.claude/skills/computer-use/SKILL.md` referencing `valor-computer` as the only
  invocation surface (no `python -m tools.computer`)
- Document the OS gate behavior — gate is enforced in `tools.computer.cli:main`, not the skill
  body. SKILL.md states: "On non-macOS machines, `valor-computer` exits 78 with
  `computer-use is macOS-only`."
- Document bcu HTTP server is loopback-only
- Document `BYOB_ALLOW_EVAL` stays unset for browser operations
- Include example workflow: `valor-computer list_apps` → find Notes.app → `list_windows` →
  `click` → `type_text` → `screenshot_window`

### 5. Write tests for BYOB scheduler, MCP registrar, and computer-use
- **Task ID**: build-tests
- **Depends On**: build-byob-mcp, build-scheduler-gate, build-computer-module
- **Validates**: `tests/integration/test_byob_scheduler.py`,
  `tests/unit/test_mcp_byob_registrar.py`, `tools/computer/tests/`
- **Informed By**: Decision 2 (scheduler), spike-3 (computer module shape), rev3 critique B1
  (registrar pattern)
- **Assigned To**: browser-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- **Audit `tools/browser/tests/test_agent_browser.py`**: For each test case, decide DELETE (it
  tests the 3rd-party CLI's behavior, which we don't own) or UPDATE (it tests something the
  cleaned-up Python module still does). Apply the disposition. Drop any "BYOB-fallback inside
  the CLI" tests — that path doesn't exist in this plan.
- Create `tests/integration/test_byob_scheduler.py`:
  - `test_two_real_chrome_sessions_serialize`: Two `AgentSession`s with
    `requires_real_chrome=True` → second waits for first to finish.
  - `test_real_chrome_does_not_block_unrelated`: One real-Chrome and one ordinary session run
    concurrently.
  - `test_field_round_trips`: Save and reload an `AgentSession` with the new field, assert it
    survives.
- Create `tests/unit/test_mcp_byob_registrar.py`:
  - Idempotency: running the registrar twice produces the same `~/.claude.json`.
  - Lock contention: 3-attempt backoff fires when the lock is held.
  - Drift heal: a wrong `mcpServers.byob.env.BYOB_ALLOW_EVAL` value is corrected on next run.
- Create `tools/computer/tests/__init__.py`
- Create `tools/computer/tests/test_computer_use.py`:
  - Unit tests with mocked HTTP responses for each `tools/computer` function
  - Test `ComputerUseUnavailableError` when manifest absent
  - Test HTTP 404 → `window_not_found` error dict
  - Test OS gate in `tools.computer.cli:main` — patch `sys.platform` to a non-darwin value
    (e.g., `"linux"`); assert exit code 78 and stderr contains "computer-use is macOS-only"
  - **Test Electron selector resolution**: mock the bundle_id detection, assert
    `tools/computer.click(window_id, selector={...})` re-queries `get_window_state` and resolves
    the selector to a fresh AX ref before each call.
- Create `tools/computer/tests/test_computer_use_integration.py`:
  - Integration tests marked `@pytest.mark.integration`
  - Requires live bcu running; skip if manifest absent
  - One end-to-end test driving Notes.app: open Notes, find window, click in body, type,
    screenshot — verify text appears in the screenshot OCR.

### 6. Update setup/update skills and cancel superseded plan
- **Task ID**: build-skill-updates
- **Depends On**: none
- **Validates**: `docs/plans/telegram_desktop_control.md` status is `Cancelled`; `/setup` and
  `/update` skills include BYOB and bcu steps
- **Informed By**: Decision 1 (agent-browser/bowser SKILL.md edits are out of scope, in #1274)
- **Assigned To**: skill-builder
- **Agent Type**: builder
- **Parallel**: true
- **Do NOT modify** `.claude/skills/agent-browser/SKILL.md` or `.claude/skills/bowser/SKILL.md`
  in this plan. Those edits belong in [#1274](https://github.com/tomcounsell/ai/issues/1274).
- Update `docs/plans/telegram_desktop_control.md`: Set status to `Cancelled`, add note:
  "Superseded by docs/plans/byob_and_computer_use.md — Track 2 (computer-use skill)"
- Update `/setup` skill: Add BYOB extension install step (`bun run setup` in `~/.byob/`);
  before any bcu work, **prompt the user**: "Do you want to enable computer-use (drives native
  macOS apps for the agent)?". On yes, write the opt-in sentinel
  (`~/.config/valor/computer-use-enabled`), then download the latest bcu binary, install,
  request Accessibility + Screen Recording permissions, and surface any install hiccup to the
  user with an actionable next step. On no, skip bcu entirely (no sentinel written).
- Update `/update` skill: Run `scripts/update/mcp_byob.py` registrar on every invocation; add
  BYOB rebuild step (with end-to-end canary); add the bcu **install-or-update** flow gated on
  the opt-in sentinel (fresh install if missing, SHA-compare upgrade if present); always alert
  the user on install/update failure; add Chrome version change detection → force
  `bun run setup`

### 7. Write feature documentation
- **Task ID**: document-feature
- **Depends On**: build-byob-mcp, build-scheduler-gate, build-computer-skill, build-skill-updates
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
- Verify `~/.claude.json` `mcpServers.byob` is present with `BYOB_ALLOW_EVAL=0`. Restart Claude
  Code; confirm `byob_*` MCP tools are loaded into the agent context.
- Have the agent call `byob_navigate('https://github.com')` then `byob_get_title` — confirm
  authenticated GitHub view (not public homepage).
- Run: `valor-computer list_windows` on macOS — confirm bcu responds
- Run `valor-computer list_apps` on a non-macOS host (or with `sys.platform` patched to
  `"linux"` in a test fixture) — confirm exit 78 + stderr contains "computer-use is macOS-only"
- Run: `pytest tests/integration/test_byob_scheduler.py tests/unit/test_mcp_byob_registrar.py
  tools/browser/tests/ tools/computer/tests/ -v -x`
- Run: `python -m ruff check . && python -m ruff format --check .`
- Verify all success criteria are met
- Confirm existing `agent-browser`-using skills still work unchanged (smoke-test one of:
  `do-design-audit`, `do-pr-review`, `linkedin`)
- Confirm `docs/plans/telegram_desktop_control.md` status is `Cancelled`

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/integration/test_byob_scheduler.py tests/unit/test_mcp_byob_registrar.py tools/computer/tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| BYOB MCP registered + eval disabled | `python -c "import json,os; s=json.load(open(os.path.expanduser('~/.claude.json')))['mcpServers']['byob']; assert s['env'].get('BYOB_ALLOW_EVAL','0')=='0'"` | exit code 0 |
| computer-use skill exists | `test -f .claude/skills/computer-use/SKILL.md` | exit code 0 |
| telegram_desktop_control cancelled | `grep 'status: Cancelled' docs/plans/telegram_desktop_control.md` | exit code 0 |
| valor-computer CLI exists | `python -c "import tools.computer.cli"` | exit code 0 |
| AgentSession scheduler field exists | `python -c "from models.agent_session import AgentSession; assert hasattr(AgentSession, 'requires_real_chrome')"` | exit code 0 |
| mcp_byob registrar exists | `test -f scripts/update/mcp_byob.py` | exit code 0 |
| feature docs created | `test -f docs/features/byob-browser-control.md && test -f docs/features/computer-use.md` | exit code 0 |

## Critique Results

Verdict from rev1 critique (artifact `sha256:4ed1ce3b...`): **NEEDS REVISION**. The findings
below were derived by re-applying the war-room critic lenses to the rev1 plan during this
revision pass. They are recorded here as a durable trace of what changed and why.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Skeptic / Consistency Auditor | Open Question 1 resolution and Technical Approach claim Python `tools/browser/__init__.py` will dispatch via "the Anthropic SDK in-process MCP client". This is architecturally invalid — Claude Code's MCP client lives in the Node-based harness; arbitrary Python subprocesses cannot reach it. The plan and its Data Flow contradicted themselves. | Spike-2 redo + Solution rewrite + Open Question 1 rewrite | Verified by `grep -rn "from tools.browser"` that no production code imports the Python module. The actual integration seam is the `agent-browser` CLI binary. The Python module gets a deprecation docstring; nothing else changes there. The CLI now talks to byob-bridge over `~/.byob/run/byob.sock` directly — no MCP-from-Python machinery needed. Spike-2 finding rewritten; Data Flow Track 1 simplified; Solution and Technical Approach rewritten; Open Questions 1 resolution rewritten. |
| CONCERN | Adversary | Race Conditions covers stale window IDs (HTTP 404 path) but does not address Electron-app AX tree staleness — refs invalidated by lazy AX tree rebuilds in Slack/VS Code/Telegram-Desktop/Discord even when the window stays open. The user's GitHub comment (m13v) flagged this explicitly. | Race 3 added | New "Race 3: Electron AX tree refs go stale" section. Mitigation: re-query `get_window_state` immediately before each action when bundle_id matches a known Electron app; resolve targets by stable property (role + label + bounds) rather than caching AX refs across actions. Test coverage added in Step 5. |
| CONCERN | Skeptic / Operator | "Silent fallback to Playwright if BYOB bridge unavailable" inside the Python module masks BYOB outages — the agent would attempt logged-in operations on an anonymous browser and silently fail authentication. | Replaced silent fallback with hard error | The CLI exits 1 with a clear actionable message when BYOB is unavailable. There is **no Playwright fallback inside the CLI**. Callers explicitly route to `bowser` for anonymous/parallel work. Documented in Solution, Data Flow, and Step 2. |
| CONCERN | Operator | Update System describes "download latest bcu binary" and "BYOB version has changed" but does not pin to specific upstream versions, has no rollback path on a bad install, and has no post-install canary. Drift on either dependency would silently break the agent. | Pinning files + rollback paths + canaries added | `config/byob_pin.json` and `config/bcu_pin.json` hold pinned upstream versions; bumps are gated behind `/update --bump-byob` / `--bump-bcu`. BYOB rollback: `~/.byob/dist.prev/` snapshot, restored on socket-not-appearing canary failure. bcu rollback: `~/.local/bin/background-computer-use.prev` symlink, restored on `/v1/list_apps` canary failure. Documented in Update System section. |
| CONCERN | Operator | `bun` is listed as a prereq but `/setup` had no install command. New machines fail at first `bun run setup` with no clear recovery path. | Prereqs table now has Install Command column | Added "Install Command (if missing)" column to the Prerequisites table. `bun` row: `curl -fsSL https://bun.sh/install \| bash`. Step 1 build task now starts with `bun --version || curl -fsSL https://bun.sh/install \| bash`. |
| CONCERN | User | Success Criteria are entirely technical (test-pass, lint-clean, files-exist). The headline user-facing claim — "automation does not steal the user's cursor or focus" — has no acceptance verification. | User-facing manual verification subsection added to Success Criteria | New Success Criteria subsection lists four manual verifications: cursor-not-stolen test (user types while bcu drives a window), parallel-session graceful failure (two concurrent `agent-browser` invocations), BYOB-down clarity (Chrome stopped → exit 1, no Playwright), and Electron AX retry (Slack click after AX invalidation). |
| NIT | Archaeologist | Research finding 4 (MCP-vs-CLI) frames the question as token cost in the prose summary but the spike conclusion is about connection lifecycle (per the GitHub commenter). Mismatch in framing. | Research finding 4 prose updated to lead with connection lifecycle | Reframed the Research finding to lead with connection lifecycle (per m13v's comment) and demote token cost to a secondary consideration. Spike-1 already had this right; only the prose intro changed. |

### Rev3 — Internal consistency sweep (2026-05-04)

After rev2 was committed the router re-dispatched `/do-plan` with the same critique verdict
hash. Rather than waiting for a fresh critique to surface new findings, the rev3 pass audited
the rev2 plan against its own architectural decisions and fixed residual contradictions left
over from the rev1 → rev2 transition. No critic raised these explicitly; they are
self-discovered consistency drift.

| Severity | Origin | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Self-audit | Failure Path Test Strategy still listed "BYOB bridge socket absent: `tools/browser/` must fall back to Playwright without crashing" — directly contradicts the rev2 "no silent Playwright fallback" decision. | Failure Path Test Strategy rewritten | Rewrote the section to test the BYOB-down → exit 1 path, the lock-contention → exit 2 path, and the agent-layer surfacing of CLI errors. Removed every "fall back to Playwright" assertion. |
| CONCERN | Self-audit | Risk 1 mitigation referenced `agent-browser connect 9222` as the post-rebuild health check. BYOB does not expose CDP on a TCP port — it uses the Unix socket at `~/.byob/run/byob.sock`. The mitigation step was unrunnable. | Risk 1 mitigation rewritten | Replaced with the documented post-install canary: socket appears within 10s of starting Chrome, `agent-browser open about:blank && agent-browser get title` against the socket. Restore from `dist.prev/` on canary failure. Explicitly noted the legacy CDP-port workflow is gone. |
| CONCERN | Self-audit | Step 2 build task said "Update tools/browser/manifest.json to remove `playwright` from declared dependencies" — but the actual file does not list playwright. The instruction was a no-op and would have left the manifest still pointing at the old upstream (`vercel-labs/agent-browser`). | Step 2 manifest task rewritten | Replaced with the actual edits: change `source.repository` to `wxtsky/byob`, change `commands.install` to the BYOB clone+setup invocation, drop `state save auth.json` from the `authenticated-session` workflow (no auth-state files in repo with BYOB). |
| CONCERN | Self-audit | Agent Integration section said the `computer-use` skill invokes `python -m tools.computer ...` AND `valor-computer ...`. Two invocation patterns for one skill creates ambiguity for downstream test code and OS-gate placement. | Agent Integration narrowed to single invocation pattern | Skill uses `valor-computer` exclusively. `python -m tools.computer` is explicitly excluded. The OS gate lives inside `tools.computer.cli:main` so the entry-point shim catches it before any HTTP work. |
| CONCERN | Self-audit | Plan claimed "OS gate in computer-use skill: Skill checks `sys.platform == "darwin"` at entry." Skills are markdown files — they cannot execute Python. The gate had nowhere to live. | OS gate relocated to `tools.computer.cli:main` | Plan now states the gate is enforced in the CLI entry point (exit 78 / `EX_CONFIG`) and that the skill body merely documents the expected behavior. Step 4 and the test step updated to reflect this. Validation step in Step 8 updated to assert exit 78 + stderr message rather than a vague "OS gate message". |

### Rev4 — Architectural correction (2026-05-04)

After the rev3 critique returned `NEEDS REVISION` (artifact `sha256:be258f15…`), the PM session
reviewed the open architectural questions and made two binding decisions that reshape the plan.
Rev4 applies those decisions and carries forward the rev3 critique's still-valid findings.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Skeptic / Operator / Consistency Auditor | Rev3 plan registers BYOB in `.mcp.json` (11 places) but this repo uses `~/.claude.json` `mcpServers` — pattern in `scripts/update/mcp_memory.py`. Following rev3 as written would produce a tracked-but-unloaded file Claude Code never reads. | New `scripts/update/mcp_byob.py` registrar | Modeled on `scripts/update/mcp_memory.py`. Holds `fcntl.flock(LOCK_EX | LOCK_NB)` on `~/.claude.json.lock` with the existing 3-attempt backoff (50ms / 200ms / 800ms — see `mcp_memory.py:46`) because `~/.claude.json` is rewritten by the Claude Code harness on every session event. Wired into `scripts/update/run.py` alongside `mcp_memory`. All `.mcp.json` references in Solution, Update System, Verification, and Success Criteria replaced with `~/.claude.json` `mcpServers.byob`. |
| BLOCKER | Operator / Adversary | Rev3 Prerequisites table had broken check commands (escaped pipes inside backticks broke the parser; `node --version | awk` failed; `Quartz` Python module not in deps). | Prerequisites table rewritten | Each row is now a single shell command with no escaped-pipe gymnastics. Node check uses `node -e`. Screen Recording probe uses `screencapture` (no Python OS framework dep). `Quartz` removed entirely. Added a one-line note above the table about the parser's `|`-splitting limitation. |
| BLOCKER | Skeptic / Archaeologist / Consistency Auditor (PM-overridden) | Rev3 critique B3 said `tools/browser/__init__.py` was "half-deprecated" because the public wrappers stayed live with only a docstring change — incompatible with CLAUDE.md "no legacy code tolerance". PM-level decision: the Python module has no relationship to the `agent-browser` CLI binary; the rev3 framing was an invented dependency. | Decision 1 (rev4): PM directive | The unused public wrappers (`navigate`, `screenshot`, `extract_text`, `fill_form`, `click`, `wait_for_element`) are deleted (zero production callers per spike-2). `_downscale_if_needed` is preserved as a Pillow utility. Module docstring rewritten to clarify it is unrelated to `agent-browser` or BYOB. The 3rd-party `agent-browser` CLI binary (Mach-O at `/opt/homebrew/lib/node_modules/agent-browser/bin/agent-browser-darwin-arm64`, version 0.9.1) is **untouched** — the rev1/rev2/rev3 "edit `tools/browser/cli.py`" approach assumed an editable Python entry point that does not exist. Per-skill migration to BYOB is tracked in [#1274](https://github.com/tomcounsell/ai/issues/1274). |
| CONCERN (PM-elevated to architectural decision) | Adversary / Operator | Rev3 `flock(2)` on `~/.byob/session.lock` with 5s timeout: timeout too short for real Chrome page loads (false-positive "busy" exits), MCP path doesn't respect the same lock as the CLI path → racing, locks block in-flight work rather than scheduling around it. | Decision 2 (rev4): scheduler-layer serialization | New `AgentSession.requires_real_chrome` Popoto field (nullable, default `False`). Worker session-pick loop in `worker/__main__.py` checks the flag and defers concurrent real-Chrome sessions. MCP and CLI both route through the same scheduler-aware session, so the rev3 "MCP-vs-CLI precedence" question becomes moot — there is exactly one queue. No file locks. Per memory `feedback_field_backcompat_heal` (issues #1099, #1172), no extra backcompat code is needed; `_heal_descriptor_pollution` walks fields generically. |
| CONCERN | Operator | Rev3 Risk 1 post-install canary was "socket appears within 10s of Chrome start" — but a stale socket file from a crashed prior run passes the existence check while `connect()` fails. Native messaging registration drift leaves the socket present but the extension end disconnected; canary passes, rollback never fires. | End-to-end probe canary | New `scripts/update/byob_canary.js` connects to the socket and runs `byob_navigate('about:blank')` + `byob_get_title` round-trip with `socket.settimeout(30)`. ECONNREFUSED is treated as canary failure (not "socket not yet ready"). On failure, restore from `dist.prev/` and alert. |
| CONCERN | Adversary | Rev3 Race 3 mitigation said `tools/computer/__init__.py` "re-queries `get_window_state` immediately before each action and resolves the target by a stable property (role + label + bounds)" — but Step 3's API surface took raw AX refs. The mitigation was described but not designed. | Selector-aware API | `tools/computer/__init__.py` `click`/`set_value`/`drag` accept a `selector={'role': ..., 'label': ..., 'bounds': ...}` dict for Electron-bundle-id targets. Module re-queries internally and resolves the selector to a fresh ref before each action. Bundle-id list lives in `tools/computer/electron_bundles.py`. Test in Step 5 mocks the bundle_id detection and asserts the re-query fires. |
| CONCERN | Consistency Auditor / User | Rev3 Success Criteria listed `do-design-review` as a downstream skill that must pass, but no such skill exists. The list of 9 was 8 + 1 sub-skill miscounted. | Success Criteria corrected | Removed `do-design-review` from the list. The 8 actual skills + `do-pr-review` screenshot sub-skill cover the blast radius from the issue body. |
| CONCERN | Skeptic / Archaeologist | Rev3 manifest-edit step covered 3 fields but missed others (`source.package`, `requires.binaries`, `commands.verify`). Internally inconsistent manifest produces silent capability mismatches at tool-selection time. | Dissolved by Decision 1 | With Decision 1, the unused public wrappers in `tools/browser/__init__.py` are deleted entirely. `tools/browser/manifest.json` and `tools/browser/README.md` are updated to match the post-cleanup surface (`_downscale_if_needed` only) — or deleted entirely. Either way, no half-states. |
| NIT | Simplifier | Rev3 Verification table had two near-identical Python one-liners that re-open `~/.claude.json` twice. | Combined into one check | Single Verification row asserts both `mcpServers.byob` exists and `BYOB_ALLOW_EVAL='0'` in one Python invocation. |

---

## Open Questions

_All resolved. Question 1 was rewritten in rev4 to reflect Decisions 1 and 2 from the PM
session — see the Rev4 row in the Critique Results table for the architectural correction._

1. **BYOB integration architecture — RESOLVED (rev4 final)**: The agent reaches BYOB
   exclusively via the registered MCP server (loaded into Claude Code's runtime when
   `~/.claude.json` `mcpServers.byob` is present). **No CLI entry point is added in this plan.**
   The 3rd-party `agent-browser` CLI binary (Mach-O at
   `/opt/homebrew/lib/node_modules/agent-browser/bin/agent-browser-darwin-arm64`, version 0.9.1)
   is **untouched** — it is not editable Python. Per-skill migration of existing
   `agent-browser`-using skills to BYOB is tracked in
   [#1274](https://github.com/tomcounsell/ai/issues/1274). Browser-use serialization (parallel
   real-Chrome session collisions) is handled at the worker scheduler layer via a new nullable
   `AgentSession.requires_real_chrome` field — no `flock(2)` in this plan. See `## Solution`,
   `## Technical Approach`, and the Track 1 Data Flow.

2. **`/setup` scope for bcu — RESOLVED**: Automated download + install of latest bcu, gated on
   an explicit user opt-in prompt ("Do you want to enable computer-use?"). Opt-in writes a
   sentinel at `~/.config/valor/computer-use-enabled`. The `/update` skill checks the same
   sentinel on every run: fresh install if opted-in but missing, SHA-compare upgrade otherwise.
   Pinning is via `config/bcu_pin.json` and only bumps via `/update --bump-bcu`. Rollback path
   keeps the previous binary at `.prev` with a post-install canary. Any install/update hiccup
   surfaces a clear actionable alert to the user. See `## Update System` and Step 6.

3. **`telegram_desktop_control` plan — RESOLVED**: No Telegram-specific workflows are needed.
   General-purpose `computer-use` is sufficient. The `telegram_desktop_control.md` plan is
   cancelled (Step 6) — no follow-on plan.
