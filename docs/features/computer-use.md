# Computer Use (macOS Desktop Control)

**Issue:** [#1256](https://github.com/tomcounsell/ai/issues/1256)
**Plan:** [`docs/plans/byob_and_computer_use.md`](../plans/byob_and_computer_use.md) (Track 2)
**Supersedes:** `docs/plans/telegram_desktop_control.md` (status: Cancelled)

## What it is

Computer-use is the agent-facing surface for native macOS app automation. The agent drives Slack, Notes, Telegram Desktop, VS Code, etc. via the macOS Accessibility API -- click buttons, type text, screenshot windows -- without moving the user's cursor or stealing focus.

Stack:

```
agent -> valor-computer CLI -> tools/computer/ -> bcu loopback HTTP -> macOS Accessibility API
```

The **bcu** (background-computer-use) Swift app is the key piece: it reads the AX tree of any visible window, captures window screenshots, and dispatches AX actions. Bundle identity: `xyz.dubdub.backgroundcomputeruse`.

## Decision: separate from `tools/browser/`

Per spike-3 (rev1+), browser automation and desktop automation diverge fundamentally:

- Browser uses DOM element refs and URLs.
- Desktop uses window IDs, AX node refs, and app bundle IDs.

Unifying them in one Python module produces an incoherent interface. `tools/computer/` is a sibling to `tools/browser/`, not an extension.

## OS gate

bcu is **macOS-only**. The OS gate lives in `tools.computer.cli:main` (the `valor-computer` entry point), not in the SKILL.md (skills are markdown and cannot execute Python). On `sys.platform != "darwin"` the CLI prints to stderr:

> `computer-use is macOS-only. This machine runs <platform>; skipping.`

and exits **78** (`EX_CONFIG`). This is distinct from the generic exit-1 path used for other configuration errors so callers can branch on it.

## Files

| Path | Purpose |
|------|---------|
| `tools/computer/__init__.py` | HTTP wrapper for the bcu loopback API. Functions: `list_apps`, `list_windows`, `get_window_state`, `click`, `scroll`, `type_text`, `press_key`, `set_value`, `perform_secondary_action`, `drag`, `resize`, `set_window_frame`, `screenshot_window`. Stdlib-only (`urllib.request`). Reads base URL from `$TMPDIR/background-computer-use/runtime-manifest.json`. Raises `ComputerUseUnavailableError` when manifest absent or bcu not running. |
| `tools/computer/electron_bundles.py` | Known Electron bundle IDs (Slack, VS Code, Telegram Desktop, Discord, Notion, Figma, Spotify) for the AX-tree-staleness mitigation. |
| `tools/computer/cli.py` | argparse CLI. OS gate enforced at entry. Argument parsing for selector JSON. |
| `.claude/skills/computer-use/SKILL.md` | Agent-facing skill body. Documents `valor-computer` invocation patterns, Electron selector usage, error semantics. |
| `tools/computer/tests/test_computer_use.py` | 25 unit tests (mocked HTTP). |
| `tools/computer/tests/test_computer_use_integration.py` | 3 integration tests (live bcu); auto-skip when manifest absent. |

## Agent-integration shape

The `computer-use` skill body invokes **`valor-computer`** as the only invocation pattern. `python -m tools.computer ...` is intentionally not supported -- it would skip the OS gate and complicate test placement.

`pyproject.toml [project.scripts]`:
```toml
valor-computer = "tools.computer.cli:main"
```

## Selector-aware API for Electron apps (Race 3)

Electron apps lazily build their accessibility tree, so an AX node ref returned by `get_window_state` can become invalid before the next call -- even if the window stays open. For these targets, callers pass a **selector dict** instead of a raw AX ref:

```bash
valor-computer click <slack_window_id> \
  --selector '{"role":"AXButton","label":"Send","bundle_id":"com.tinyspeck.slackmacgap"}'
```

The module re-queries `get_window_state` internally and resolves the selector to a fresh AX ref before each action. The `bounds` field tie-breaks when multiple AX nodes match `role` + `label`.

The Electron bundle list is at `tools/computer/electron_bundles.py`. Add new bundles there.

## Loopback-only

The bcu HTTP server binds to `127.0.0.1` only. There is no remote control surface. All requests go through stdlib `urllib.request` to the loopback URL stored in `$TMPDIR/background-computer-use/runtime-manifest.json`.

## Permissions

bcu requires two macOS permissions, granted by the operator in System Settings:

- **Privacy & Security -> Accessibility** -> add `BackgroundComputerUse.app`
- **Privacy & Security -> Screen Recording** -> add `BackgroundComputerUse.app`

These cannot be granted programmatically. The `/setup` skill (Step 8.5) prompts the operator before installing bcu and surfaces the permission requirement.

## Install + opt-in

Computer-use is **opt-in**. The `/setup` skill asks "Do you want to enable computer-use?" before any download. On yes, it writes a sentinel at `~/.config/valor/computer-use-enabled` and proceeds. On no, the sentinel is never written and `/update` leaves bcu alone.

## Update flow (planned)

`scripts/update/run.py` will (per the plan) check the opt-in sentinel and:

- If opted-in but not installed: download the asset for the pinned release in `config/bcu_pin.json`, verify the SHA against the GitHub-published `.sha256` checksum, install to `~/Applications/BackgroundComputerUse.app` + `~/.local/bin/background-computer-use` symlink, prompt for permissions.
- If installed: SHA-compare against the pinned release; download + replace if drifted; re-prompt for Accessibility only if bundle identity changed.
- Rollback: keep `~/.local/bin/background-computer-use.prev` during update; restore on `/v1/list_apps` canary failure within 5s.

Pin bumps only via `/update --bump-bcu`.

## Example workflows

**Drive Notes:**

```bash
valor-computer list_apps                                # find com.apple.Notes
valor-computer list_windows --bundle-id com.apple.Notes # pick window_id
valor-computer click <window_id> --x 400 --y 300        # click body
valor-computer type_text <window_id> "Reminder: ..."
valor-computer screenshot_window <window_id> --output /tmp/notes.png
```

**Drive Slack with selectors (Electron):**

```bash
valor-computer click <slack_window> \
  --selector '{"role":"AXStaticText","label":"engineering","bundle_id":"com.tinyspeck.slackmacgap"}'

valor-computer click <slack_window> \
  --selector '{"role":"AXTextArea","label":"Message engineering","bundle_id":"com.tinyspeck.slackmacgap"}'

valor-computer type_text <slack_window> "Build complete"
valor-computer press_key <slack_window> return --mod cmd  # send (cmd-return)
```

## Failure modes

- **`{"error": "computer_use_unavailable", ...}`** -- bcu not installed, not opted in, or not running. CLI exits **78** with the structured error in stdout. Tell the user to run `/setup` and answer "yes" to the computer-use opt-in.
- **`{"error": "window_not_found", "window_id": N}`** -- the window closed between `list_windows` and the action. Re-call `list_windows` and retry.
- **`{"error": "selector_no_match", ...}`** -- the selector did not resolve. Inspect via `get_window_state` and refine `role`/`label`/`bounds`.
- **`{"error": "timeout", ...}`** -- bcu took longer than 10s. Loopback HTTP timeouts are unusual; check that the bcu app is responsive (Activity Monitor or `pgrep -f BackgroundComputerUse`).
- **bcu Accessibility permission revoked** (macOS upgrade or re-sign): bcu calls fail. The agent surfaces "bcu Accessibility permission revoked -- open System Settings -> Privacy & Security -> Accessibility". Operator re-grants.

## See also

- [BYOB Browser Control](byob-browser-control.md) -- sibling feature for real-Chrome browser automation
- `.claude/skills/computer-use/SKILL.md` -- the agent-facing usage guide
- `tools/computer/electron_bundles.py` -- list of Electron bundle IDs for selector re-querying
