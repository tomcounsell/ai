# BYOB Browser Control (Real Chrome, Logged-In)

**Issue:** [#1256](https://github.com/tomcounsell/ai/issues/1256)
**Plan:** [`docs/plans/byob_and_computer_use.md`](../plans/byob_and_computer_use.md)

## What it is

BYOB (Bring Your Own Browser) is a Chrome extension + native messaging host + MCP server stack that lets the agent read and act on the user's already-logged-in Chrome session via MCP tools (`mcp__byob__browser_navigate`, `mcp__byob__browser_click`, `mcp__byob__browser_screenshot`, etc.) -- no `state.json` files in the repo, no per-session re-auth, no headless-fingerprint detection.

It is the **only** browser surface in this repo. The legacy `agent-browser` and `bowser` skills, plus the `bowser` subagent, were retired in #1256. Public pages and authenticated dashboards both go through BYOB.

Communication chain:

```
agent (Claude Code) -> byob MCP server (node) -> byob-bridge -> Chrome extension -> active tab
```

All communication is local (Unix socket + native messaging). The Chrome extension key is unique per install; nothing is shared across machines.

## Tab targeting discipline (always pass an explicit `tabId`)

BYOB has **no content/URL-based tab matching**. It cannot "find the tab the user is showing." With no `tabId`, every tool falls back to one of two defaults, neither of which tracks user intent:

- `browser_navigate` with no `tabId` → opens a **brand-new background tab** (`packages/extension/lib/handlers/navigate.ts`), never reuses an existing one.
- Every other tool (`click`, `type`, `eval`, `read`, `screenshot`, …) with no `tabId` → `chrome.tabs.query({active: true, lastFocusedWindow: true})` (per-handler `activeTabId()` / `tab.ts` `openOrReuse` reuse-active path).

`lastFocusedWindow: true` means "whichever Chrome window most recently held focus," **not** "the window the user is looking at." When the user runs Claude Code from a terminal and/or has more than one Chrome window open, this default is ambiguous and reliably lands on the wrong tab — in practice the same early/pinned tab every time, regardless of which tab the user left active. Telling the user to "leave the right tab active" does not fix it.

**The only deterministic targeting is an explicit `tabId`.** The required discipline whenever driving BYOB:

1. `browser_list_tabs` — returns every tab's `id`, `url`, `title`, `windowId`.
2. Match the intended tab by URL/title.
3. Pass that `tabId` into **every** subsequent `navigate`/`click`/`read`/`screenshot`/etc. call.
4. Optionally `browser_switch_tab <id>` to bring it to the foreground first.

Never rely on the active-tab default. Reach for `list_tabs → match → explicit tabId` by default.

## Bridge inference

`agent/byob_skill_triggers.py` exposes `BYOB_SKILL_TRIGGERS` (registry) and `infer_requires_real_chrome(text)` (case-insensitive regex match with first-person/intent phrasing). Both Telegram and email bridge enqueue paths call this before `enqueue_agent_session()` so bridge-spawned sessions get the `requires_real_chrome` scheduler gate engaged automatically.

CLI users set the flag explicitly: `valor-session create --needs-real-chrome ...`.

## Scheduler-layer serialization (Decision 2)

Real Chrome has **one** DOM tree. Two BYOB MCP clients driving it concurrently corrupt active-tab state. Mitigation lives at the worker scheduler layer, not as a file lock:

- New nullable Popoto field on AgentSession: `requires_real_chrome: bool` (default `False`).
- The worker session-pick loop in `agent/session_pickup.py` checks the flag before starting a candidate. If any currently-running session has `requires_real_chrome=True`, the new candidate is deferred until the running one finishes -- it stays `pending`, the next pop cycle retries.
- Two surfaces set the flag at session creation time:
  - `valor-session create --needs-real-chrome ...` (operator-driven)
  - The plan's machinery for inferring it from the session message (left for downstream wiring; the explicit-creation path is enough)

No `flock(2)`. No per-process collision guard. No "MCP-vs-CLI precedence" -- there is exactly one queue, regardless of which surface initiated the request. Per memory `feedback_field_backcompat_heal` (#1099, #1172): nullable Popoto field needs no migration code; Popoto default-fills absent fields at lazy-load (see [`popoto-descriptor-pollution-ledger.md`](popoto-descriptor-pollution-ledger.md), #2083).

## Files

| Path | Purpose |
|------|---------|
| `scripts/update/mcp_byob.py` | Idempotent registrar that writes `mcpServers.byob` into `~/.claude.json` under `fcntl.flock(LOCK_EX|LOCK_NB)` on `~/.claude.json.lock`. Modeled directly on `mcp_memory.py`. |
| `config/byob_pin.json` | Pinned BYOB upstream commit. Bump only via `/update --bump-byob`. |
| `config/bcu_pin.json` | Pinned bcu release tag (used by the computer-use sibling feature). |
| `models/agent_session.py` | New `requires_real_chrome` Field. |
| `agent/session_pickup.py` | New `_real_chrome_slot_busy()` helper + the pickup-loop gate (both async and sync-fallback paths). |
| `agent/agent_session_queue.py` | `_push_agent_session(...)` now accepts `requires_real_chrome`. |
| `tools/valor_session.py` | New `--needs-real-chrome` flag on `valor-session create`. |
| `ui/app.py` + `ui/data/sdlc.py` | Field surfaced in `/dashboard.json` so operators can see why a real-Chrome session is being deferred. |

## ~/.claude.json `mcpServers.byob` shape

```json
{
  "mcpServers": {
    "byob": {
      "type": "stdio",
      "command": "/Users/<you>/.byob/packages/mcp-server/node_modules/.bin/tsx",
      "args": ["/Users/<you>/.byob/packages/mcp-server/bin/byob-mcp.ts"],
      "env": { "BYOB_ALLOW_EVAL": "1" }
    }
  }
}
```

The `command` is `tsx` (a TypeScript runner); the `args[0]` points at BYOB's TypeScript entry. This matches BYOB's own "Manual MCP registration" recipe in its README. The registrar resolves both paths from `~/.byob/` automatically — it accepts either the workspace-root tsx (`~/.byob/node_modules/.bin/tsx`) or the package-local tsx (`~/.byob/packages/mcp-server/node_modules/.bin/tsx`) depending on which layout `bun install` produced.

`BYOB_ALLOW_EVAL=1` is the repo default. BYOB is standard infrastructure on every machine and skills (`mermaid-render`, `do-discover-paths`, `do-design-system`) need `browser_eval` to function. The registrar drift-heals back to `"1"` if the value drifts.

## Block-list (BYOB upstream)

BYOB upstream blocks reading `chrome://` and `file://` URLs and login pages for Google / Microsoft / Apple accounts. BYOB is for already-authenticated operations on already-logged-in sessions; if a target requires a fresh login flow, the user signs in interactively and BYOB picks up the resulting session.

## Tests

| Test file | What it covers |
|-----------|---------------|
| `tests/unit/test_mcp_byob_registrar.py` | Install / no-op / drift heal / lock contention / atomic write / other-server preservation. 10 tests. |
| `tests/integration/test_byob_scheduler.py` | Field round-trip, `_real_chrome_slot_busy()` across all relevant statuses, defer behavior, ordinary sessions not blocked, deferred candidate becomes eligible after holder completes. 12 tests. |

## Update flow

`scripts/update/run.py` Step 4.9 calls `mcp_byob.verify_byob_mcp(write=...)` on every `/update` invocation. `--verify` runs read-only (LOCK_SH); `--full`/`--cron` repair under LOCK_EX. The registrar is the same shape as `mcp_memory`, so the lock contention + drift-heal patterns are identical.

When `config/byob_pin.json` changes, `/update --full` rebuilds the BYOB workspace under `~/.byob/` (planned: snapshot the entire tree to `~/.byob.prev/` first, then `git -C ~/.byob fetch && git -C ~/.byob checkout <pin>` followed by `bun install && bun run setup`, then verify with `bun run doctor` and the planned `scripts/update/byob_canary.js` end-to-end probe -- on canary failure `rm -rf ~/.byob && mv ~/.byob.prev ~/.byob`). BYOB v0.3+ is a monorepo (`packages/bridge/`, `packages/extension/`, `packages/mcp-server/`) with build artifacts under `packages/*/output/` and `packages/*/dist/`; there is no single top-level `dist/` to snapshot.

## Failure modes

- **BYOB MCP server fails to start** (bridge not running, socket missing, extension not loaded): Claude Code surfaces the MCP startup failure. `byob_*` tools are absent from the agent context. The agent surfaces "BYOB bridge not running -- run `cd ~/.byob && bun run doctor` to diagnose, most commonly the Chrome extension needs to be loaded" rather than silently retrying. **There is no fallback browser surface** — BYOB is the only one.
- **Lock contention on `~/.claude.json.lock`**: 3-attempt backoff (50/200/800ms). On exhaustion, the registrar returns `action="skipped"` and `/update` logs a warning; next `/update` invocation retries.
- **Two real-Chrome sessions queued at once**: scheduler defers the second; first runs to completion; pop loop picks the second on the next cycle.

## Setup gotchas (BYOB v0.3+)

These are the realities of BYOB's actual on-disk layout. Verified during PR #1277 live setup; documented here so future operators don't waste time on the same drift.

| What you might assume | What's actually true |
|---|---|
| `~/.byob/extension/` is the extension folder for "Load unpacked" | The folder is `~/.byob/packages/extension/output/chrome-mv3/` (built by `bun run setup`). The parent `packages/extension/` directory has source, not a `manifest.json` Chrome can load. |
| BYOB MCP server is JavaScript: `node ~/.byob/dist/mcp-server.js` | BYOB v0.3+ ships a TypeScript entry executed via tsx: `~/.byob/packages/mcp-server/node_modules/.bin/tsx ~/.byob/packages/mcp-server/bin/byob-mcp.ts`. Both paths resolve inside the BYOB workspace after `bun install`. The `mcp_byob.py` registrar uses these correct paths. |
| The IPC socket is at a fixed `~/.byob/run/byob.sock` | The socket is **per-device**, UUID-keyed: `~/.byob/bridges/<deviceId>.sock`. The deviceId is generated at first bridge launch. The MCP server discovers the socket itself; callers must never hardcode the path. |
| `cd ~/.byob && bun run setup` makes the bridge start | The bridge starts only when the **extension connects to the native messaging host** — i.e., after Chrome has the extension loaded and has been **fully restarted** (`⌘Q`, not just close window). Until then `bun run doctor` reports "no live bridge". |
| Closing the Chrome window is enough to pick up native messaging changes | No. Chrome reads native messaging config at startup only. Use `⌘Q` (macOS) or fully quit (other OSes) and reopen. |
| `bun run setup` rebuilds incrementally | `bun run setup` runs the full install workflow and prompts you to multi-select MCP clients to register. We don't use BYOB's auto-registration — we use `scripts/update/mcp_byob.py` instead — so just press **enter** through the registration prompt. |

**Canonical verification command** for any state question: `cd ~/.byob && bun run doctor`. It tells you which step is broken and how to fix it. Do not poke specific paths to verify.

## See also

- [Computer Use](computer-use.md) -- sibling feature for native macOS desktop automation via bcu
- [Agent Session Queue](agent-session-queue.md) -- pickup-loop architecture
- [Issue #1256](https://github.com/tomcounsell/ai/issues/1256) -- BYOB adoption + retirement of legacy `agent-browser`/`bowser` surfaces
