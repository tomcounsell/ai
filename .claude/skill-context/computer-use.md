# computer-use context — this repo (ai)

This repo provides the native-desktop-control CLI the `/computer-use` skill drives:
**`valor-computer`** (a wrapper over bcu, background-computer-use, pinned to v0.1.0 in
`config/bcu_pin.json`). The global skill body runs a generic baseline that only declares the
dependency; this file supplies the actual commands, setup, and error handling. macOS-only.

## Platform constraint

The `valor-computer` CLI enforces macOS-only at its entry point: on non-macOS hosts it prints
`computer-use is macOS-only. This machine runs <platform>; skipping.` to stderr and exits 78
(`EX_CONFIG`). The skill body never reaches the bcu HTTP layer on Linux/Windows.

## Prerequisites

- bcu (background-computer-use) installed via `/setup` opt-in. The `/setup` skill prompts
  "Do you want to enable computer-use?". On yes, it writes `~/.config/valor/computer-use-enabled`,
  downloads the bcu binary, and prompts the user to grant **Accessibility** + **Screen Recording**
  permissions in System Settings.
- bcu app must be running. It writes `$TMPDIR/background-computer-use/runtime-manifest.json`
  containing the loopback `baseURL`. The CLI reads that manifest on every call. If absent, the
  CLI returns `{"error": "computer_use_unavailable", ...}` and exits 78.

## Readiness gating (run once per session, before the first action)

Before issuing the first action in a session, run the preflight check:

```bash
valor-computer bootstrap
```

This calls `GET /v1/bootstrap` and reports whether bcu is ready. Gate on the exit
code:

- **exit 0** (`instructions.ready == true`) — permissions granted; proceed with
  `list_apps` / `click` / `type_text` / etc.
- **exit 78** with `instructions.ready == false` in the payload — bcu is running but
  macOS **Accessibility** / **Screen Recording** permission is not granted. Do **not**
  issue actions (they will fail). Relay the payload's `instructions.user` text to the
  user and stop.
- **exit 78** with `{"error": "computer_use_unavailable", ...}` — bcu not installed,
  not opted in, or not running. Tell the user to run `/setup` and answer "yes" to the
  computer-use opt-in.
- **exit 1** — some other error (e.g. bcu returned HTTP 500); surface it.

You only need this once per session — bcu readiness does not change between actions
within a session. Chain it to gate the first action:

```bash
valor-computer bootstrap && valor-computer list_windows Notes
```

## Quick start

Window IDs are **strings** returned by `list_windows`.

```bash
# Discover what's open
valor-computer list_apps                 # all visible apps
valor-computer list_windows Notes        # windows for an app (name, bundle ID, or query)

# Inspect a window's AX tree (response carries stateToken + screenshot + tree)
valor-computer get_window_state <window>

# Drive the window
valor-computer click <window> --x 100 --y 200
valor-computer type_text <window> "Hello world"
valor-computer screenshot <window> --output /tmp/notes.png

# Press a key or chord (no modifiers flag — chords go in the key string)
valor-computer press_key <window> cmd+a
valor-computer press_key <window> return
```

## Core workflow

```
0. bootstrap                        # once per session: gate on instructions.ready
1. list_apps                        # find the app
2. list_windows <app>               # pick the string window ID
3. get_window_state <window>        # AX tree + stateToken + screenshot (optional)
4. click / type_text / press_key    # drive the window
5. screenshot <window>              # capture proof
```

## Targets and stateToken (element-level actions)

Element-level actions (`click`, `scroll`, `set_value`, `perform_secondary_action`, optionally
`type_text`) take a `--target` JSON:

```json
{"kind": "node_id" | "display_index" | "refetch_fingerprint", "value": ...}
```

`node_id` / `display_index` values come from the `get_window_state` tree. Staleness is handled
**server-side**: pass `--state-token` (the `stateToken` from the same `get_window_state`
response) and bcu rejects actions against a stale tree; `refetch_fingerprint` targets re-resolve
automatically. `click` also accepts literal `--x`/`--y` coordinates instead of a target
(mutually exclusive).

```bash
# Click a specific element by node ID, guarded against staleness
valor-computer click <window> --target '{"kind":"node_id","value":"n42"}' --state-token <tok>

# Set a text field's value
valor-computer set_value <window> "hello" --target '{"kind":"node_id","value":"n7"}'

# Scroll a list down two pages
valor-computer scroll <window> --target '{"kind":"node_id","value":"n3"}' --direction down --pages 2
```

## Loopback-only

The bcu HTTP server binds to `127.0.0.1` only. There is no remote control surface. All requests
go through `urllib.request` to the loopback URL stored in
`$TMPDIR/background-computer-use/runtime-manifest.json`.

## Common workflows

### Open Notes, type, screenshot

```bash
valor-computer list_windows Notes                        # pick the string window ID
valor-computer click <window> --x 400 --y 300            # click in the body
valor-computer type_text <window> "Reminder: ship the plan"
valor-computer screenshot <window> --output /tmp/notes-after.png
```

### Drive Slack via targets

```bash
valor-computer list_windows Slack                        # pick the window ID
valor-computer get_window_state <window>                 # find node IDs + stateToken
valor-computer click <window> --target '{"kind":"node_id","value":"<channel_node>"}' --state-token <tok>
valor-computer type_text <window> "Build complete"
valor-computer press_key <window> cmd+return             # send
```

## Error handling

- `{"error": "computer_use_unavailable", ...}` → bcu not installed, not opted in, or not running. Exit code 78. Tell the user to run `/setup` and answer "yes" to the computer-use opt-in.
- `{"error": "not_found", "path": ...}` → route missing on the running bcu (version drift). Check the installed bcu against `config/bcu_pin.json`.
- Action responses carry `ok`, `classification`, and `warnings` — a stale `stateToken` surfaces as a JSON-level rejection; re-run `get_window_state` and retry with the fresh token.
- `{"error": "timeout", ...}` → bcu took longer than 10s. bcu is loopback HTTP; transient timeouts are unusual — check that the bcu app is responsive.

## BYOB note (do not conflate surfaces)

When BYOB MCP tools are invoked, the registrar at `scripts/update/mcp_byob.py` keeps
`BYOB_ALLOW_EVAL=1` — `browser_eval` is enabled by default in this repo so skills like
`mermaid-render`, `do-discover-paths`, and `do-design-system` work out of the box. Computer-use
does not interact with BYOB; this note is here so the agent does not conflate the two surfaces.
