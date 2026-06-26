# computer-use context — this repo (ai)

This repo provides the native-desktop-control CLI the `/computer-use` skill drives:
**`valor-computer`** (a wrapper over bcu, background-computer-use). The global skill body runs a
generic baseline that only declares the dependency; this file supplies the actual commands,
setup, and error handling. macOS-only.

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
  containing the loopback `base_url`. The CLI reads that manifest on every call. If absent, the
  CLI returns `{"error": "computer_use_unavailable", ...}` and exits 78.

## Quick start

```bash
# Discover what's open
valor-computer list_apps                 # all visible apps
valor-computer list_windows              # all open windows
valor-computer list_windows --bundle-id com.apple.Notes

# Inspect a window's AX tree
valor-computer get_window_state <window_id>

# Drive the window
valor-computer click <window_id> --x 100 --y 200
valor-computer type_text <window_id> "Hello world"
valor-computer screenshot_window <window_id> --output /tmp/notes.png

# Press a key with modifiers
valor-computer press_key <window_id> a --mod cmd          # Cmd-A
valor-computer press_key <window_id> return               # Return
```

## Core workflow

```
1. list_apps                           # find the bundle_id
2. list_windows --bundle-id ...        # pick the window_id
3. get_window_state <window_id>        # get AX tree for inspection (optional)
4. click / type_text / press_key       # drive the window
5. screenshot_window <window_id>       # capture proof
```

## Electron apps (stale-ref mitigation)

Electron apps lazily build their accessibility tree, so an AX node ref returned by
`get_window_state` can become invalid before your next call — even with the window still open.
Known Electron bundles include:

- `com.tinyspeck.slackmacgap` (Slack)
- `com.microsoft.VSCode` (VS Code)
- `org.telegram.desktop` (Telegram Desktop)
- `com.hnc.Discord` (Discord)
- `com.electron.notion`, `com.figma.Desktop`, `com.spotify.client`

For these targets, **pass a `--selector` JSON instead of a raw `--ref`**. The module re-queries
`get_window_state` internally on every call and resolves the selector to a fresh AX ref:

```bash
# Click the "Send" button in Slack regardless of stale refs
valor-computer click <slack_window_id> \
  --selector '{"role":"AXButton","label":"Send","bundle_id":"com.tinyspeck.slackmacgap"}'

# Set the value of a Discord text field
valor-computer set_value <discord_window_id> "hello" \
  --selector '{"role":"AXTextField","label":"Message","bundle_id":"com.hnc.Discord"}'
```

The `bounds` field (a `[x, y, w, h]` list) tie-breaks when multiple AX nodes match `role` + `label`.

## Loopback-only

The bcu HTTP server binds to `127.0.0.1` only. There is no remote control surface. All requests
go through `urllib.request` to the loopback URL stored in
`$TMPDIR/background-computer-use/runtime-manifest.json`.

## Common workflows

### Open Notes, type, screenshot

```bash
valor-computer list_apps                                  # pick bundle_id "com.apple.Notes"
valor-computer list_windows --bundle-id com.apple.Notes   # pick window_id, e.g. 12345
valor-computer click 12345 --x 400 --y 300                # click in the body (coords for stable native apps)
valor-computer type_text 12345 "Reminder: ship the plan"
valor-computer screenshot_window 12345 --output /tmp/notes-after.png
```

### Drive Slack via selector (Electron)

```bash
valor-computer list_windows --bundle-id com.tinyspeck.slackmacgap   # pick slack_window_id

valor-computer click <slack_window_id> \
  --selector '{"role":"AXStaticText","label":"engineering","bundle_id":"com.tinyspeck.slackmacgap"}'
valor-computer click <slack_window_id> \
  --selector '{"role":"AXTextArea","label":"Message engineering","bundle_id":"com.tinyspeck.slackmacgap"}'
valor-computer type_text <slack_window_id> "Build complete"
valor-computer press_key <slack_window_id> return --mod cmd   # send (cmd-return)
```

## Error handling

- `{"error": "computer_use_unavailable", ...}` → bcu not installed, not opted in, or not running. Exit code 78. Tell the user to run `/setup` and answer "yes" to the computer-use opt-in.
- `{"error": "window_not_found", "window_id": N}` → the window closed between `list_windows` and the action. Re-call `list_windows` and retry.
- `{"error": "selector_no_match", "selector": ...}` → the selector didn't resolve. Inspect via `get_window_state` and refine the role/label/bounds.
- `{"error": "timeout", ...}` → bcu took longer than 10s. bcu is loopback HTTP; transient timeouts are unusual — check that the bcu app is responsive.

## BYOB note (do not conflate surfaces)

When BYOB MCP tools are invoked, the registrar at `scripts/update/mcp_byob.py` keeps
`BYOB_ALLOW_EVAL=1` — `browser_eval` is enabled by default in this repo so skills like
`mermaid-render`, `do-discover-paths`, and `do-design-system` work out of the box. Computer-use
does not interact with BYOB; this note is here so the agent does not conflate the two surfaces.
