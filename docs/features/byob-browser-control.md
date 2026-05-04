# BYOB Browser Control (Real Chrome, Logged-In)

**Issue:** [#1256](https://github.com/tomcounsell/ai/issues/1256)
**Plan:** [`docs/plans/byob_and_computer_use.md`](../plans/byob_and_computer_use.md)
**Followups:** [#1274](https://github.com/tomcounsell/ai/issues/1274) (per-skill migration of `agent-browser`-using skills)

## What it is

BYOB (Bring Your Own Browser) is a Chrome extension + native messaging host + MCP server stack that lets the agent read and act on the user's already-logged-in Chrome session via MCP tools (`byob_navigate`, `byob_click`, `byob_screenshot`, etc.) -- no `state.json` files in the repo, no per-session re-auth, no headless-fingerprint detection.

Communication chain:

```
agent (Claude Code) -> byob MCP server (node) -> byob-bridge -> Chrome extension -> active tab
```

All communication is local (Unix socket + native messaging). The Chrome extension key is unique per install; nothing is shared across machines.

## Decision Guide: which browser surface?

Three surfaces coexist after this work shipped:

| Surface | Anonymous? | Headless? | Parallel-safe? | Logged-in? | Use when |
|---|---|---|---|---|---|
| `agent-browser` (3rd-party CLI on PATH) | yes | yes | no (single-tab) | no | Existing skills that use it (do-pr-review, do-design-audit, etc.) -- migrating in #1274 |
| `bowser` (Playwright headless) | yes | yes | yes (`-s=` named sessions) | optional (CDP/profile flags) | Untrusted-link previews, parallel CI-style tests |
| **BYOB MCP** (`byob_*` tools) | no | no (real Chrome) | no (one DOM tree) | yes -- the user's actual session | Logged-in operations (Gmail, GitHub notifications, internal dashboards). Requires scheduler defer. |

If the agent needs to read Gmail or click a button on the user's logged-in GitHub, route to BYOB. If it just needs to capture an unauthenticated webpage screenshot, use `bowser` or `agent-browser`.

## Scheduler-layer serialization (Decision 2)

Real Chrome has **one** DOM tree. Two BYOB MCP clients driving it concurrently corrupt active-tab state. Mitigation lives at the worker scheduler layer, not as a file lock:

- New nullable Popoto field on AgentSession: `requires_real_chrome: bool` (default `False`).
- The worker session-pick loop in `agent/session_pickup.py` checks the flag before starting a candidate. If any currently-running session has `requires_real_chrome=True`, the new candidate is deferred until the running one finishes -- it stays `pending`, the next pop cycle retries.
- Two surfaces set the flag at session creation time:
  - `valor-session create --needs-real-chrome ...` (operator-driven)
  - The plan's machinery for inferring it from the session message (left for downstream wiring; the explicit-creation path is enough)

No `flock(2)`. No per-process collision guard. No "MCP-vs-CLI precedence" -- there is exactly one queue, regardless of which surface initiated the request. Per memory `feedback_field_backcompat_heal` (#1099, #1172): nullable Popoto field needs no migration code; `_heal_descriptor_pollution` walks fields generically.

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
      "env": { "BYOB_ALLOW_EVAL": "0" }
    }
  }
}
```

The `command` is `tsx` (a TypeScript runner); the `args[0]` points at BYOB's TypeScript entry. This matches BYOB's own "Manual MCP registration" recipe in its README. The registrar resolves both paths from `~/.byob/` automatically.

`BYOB_ALLOW_EVAL=0` is the security default per BYOB's README -- `browser_eval` (arbitrary JS execution) stays disabled. The registrar drift-heals back to `"0"` if it ever drifts.

## Block-list (BYOB upstream)

BYOB upstream blocks reading `chrome://` and `file://` URLs and login pages for Google / Microsoft / Apple accounts. For **login** pages, use `bowser --cdp` with a persistent profile instead -- BYOB is for already-authenticated operations on already-logged-in sessions.

## Tests

| Test file | What it covers |
|-----------|---------------|
| `tests/unit/test_mcp_byob_registrar.py` | Install / no-op / drift heal / lock contention / atomic write / other-server preservation. 10 tests. |
| `tests/integration/test_byob_scheduler.py` | Field round-trip, `_real_chrome_slot_busy()` across all relevant statuses, defer behavior, ordinary sessions not blocked, deferred candidate becomes eligible after holder completes. 12 tests. |

## Update flow

`scripts/update/run.py` Step 4.9 calls `mcp_byob.verify_byob_mcp(write=...)` on every `/update` invocation. `--verify` runs read-only (LOCK_SH); `--full`/`--cron` repair under LOCK_EX. The registrar is the same shape as `mcp_memory`, so the lock contention + drift-heal patterns are identical.

When `config/byob_pin.json` changes, `/update --full` rebuilds `~/.byob/dist/` (planned: snapshot to `~/.byob/dist.prev/` first, run `bun install && bun run build && bun run setup --skip-extension`, then run `scripts/update/byob_canary.js` end-to-end probe -- on canary failure restore from `dist.prev/`).

## Failure modes

- **BYOB MCP server fails to start** (bridge not running, socket missing, extension not loaded): Claude Code surfaces the MCP startup failure. `byob_*` tools are absent from the agent context. The agent surfaces "BYOB bridge not running -- start Chrome and run `~/.byob/start.sh`" rather than silently retrying. **There is no Playwright fallback** in the BYOB surface -- anonymous Playwright work belongs to `bowser`.
- **Lock contention on `~/.claude.json.lock`**: 3-attempt backoff (50/200/800ms). On exhaustion, the registrar returns `action="skipped"` and `/update` logs a warning; next `/update` invocation retries.
- **Two real-Chrome sessions queued at once**: scheduler defers the second; first runs to completion; pop loop picks the second on the next cycle.

## See also

- [Computer Use](computer-use.md) -- sibling feature for native macOS desktop automation via bcu
- [Agent Session Queue](agent-session-queue.md) -- pickup-loop architecture
- [Issue #1274](https://github.com/tomcounsell/ai/issues/1274) -- per-skill migration of `agent-browser`-using skills to BYOB
