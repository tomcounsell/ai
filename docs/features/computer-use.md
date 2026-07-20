# Computer Use (macOS Desktop Control)

**Issue:** [#1256](https://github.com/tomcounsell/ai/issues/1256), [#2114](https://github.com/tomcounsell/ai/issues/2114) (v0.1.0 contract migration)
**Plan:** [`docs/plans/byob_and_computer_use.md`](../plans/byob_and_computer_use.md) (Track 2)
**Supersedes:** `docs/plans/telegram_desktop_control.md` (status: Cancelled)

## What it is

Computer-use is the agent-facing surface for native macOS app automation. The agent drives Slack, Notes, Telegram Desktop, VS Code, etc. via the macOS Accessibility API -- click buttons, type text, screenshot windows -- without moving the user's cursor or stealing focus.

Stack:

```
agent -> valor-computer CLI -> tools/computer/ -> bcu loopback HTTP -> macOS Accessibility API
```

The **bcu** (background-computer-use) Swift app is the key piece: it reads the AX tree of any visible window, captures window screenshots, and dispatches AX/CGEvent actions. Bundle identity: `xyz.dubdub.backgroundcomputeruse`. Pinned to **v0.1.0** in `config/bcu_pin.json`.

## The v0.1.0 contract

`tools/computer/` speaks the exact contract published by the pinned release's `RouteRegistry.swift` (the generator for the live `GET /v1/routes` catalog):

- **Every discovery/state/action route is `POST` with a JSON body.** Only `/health`, `/v1/bootstrap`, and `/v1/routes` are GET.
- **`window` is a string stable ID** returned by `list_windows` -- not an integer.
- **`list_windows` requires `app`** (name, bundle ID, or query string).
- **Element targeting** uses `target` dicts: `{"kind": "display_index" | "node_id" | "refetch_fingerprint", "value": ...}`. Values come from the `get_window_state` tree.
- **Staleness is handled server-side.** Pass the `stateToken` from a prior `get_window_state` response and bcu rejects actions against a stale tree; `refetch_fingerprint` targets re-resolve automatically. There is no client-side selector re-resolution.
- **Screenshots flow through `get_window_state`** via its `imageMode` request field (`path` | `base64` | `omit`); the response carries `screenshot.image` (`imagePath` or `imageBase64` plus `pixelWidth`/`pixelHeight`/`mimeType`). There is no dedicated screenshot route.
- **`press_key` takes a single `key` string** -- chords like `cmd+shift+a` go in the string; there is no modifiers array.
- Action responses carry `ok`, `classification`, `preStateToken`/`postStateToken`, `warnings`.

## Decision: separate from `tools/browser/`

Per spike-3 (rev1+), browser automation and desktop automation diverge fundamentally:

- Browser uses DOM element refs and URLs.
- Desktop uses string window IDs, AX node targets, and app queries.

Unifying them in one Python module produces an incoherent interface. `tools/computer/` is a sibling to `tools/browser/`, not an extension.

## OS gate

bcu is **macOS-only**. The OS gate lives in `tools.computer.cli:main` (the `valor-computer` entry point), not in the SKILL.md (skills are markdown and cannot execute Python). On `sys.platform != "darwin"` the CLI prints to stderr:

> `computer-use is macOS-only. This machine runs <platform>; skipping.`

and exits **78** (`EX_CONFIG`). This is distinct from the generic exit-1 path used for other configuration errors so callers can branch on it.

## Files

| Path | Purpose |
|------|---------|
| `tools/computer/__init__.py` | HTTP wrapper for the bcu v0.1.0 loopback API. Functions: `bootstrap`, `is_ready`, `list_apps`, `list_windows`, `get_window_state`, `screenshot`, `click`, `scroll`, `type_text`, `press_key`, `set_value`, `perform_secondary_action`, `drag`, `resize`, `set_window_frame`. Stdlib-only (`urllib.request`); `bootstrap()` is the only GET wrapper. Reads base URL from `$TMPDIR/background-computer-use/runtime-manifest.json`. Raises `ComputerUseUnavailableError` when manifest absent or bcu not running. |
| `tools/computer/cli.py` | argparse CLI. OS gate enforced at entry. `--target` JSON parsing, `--state-token` pass-through. |
| `.claude/skill-context/computer-use.md` | Repo-specific skill context. Documents `valor-computer` invocation patterns, target/stateToken usage, error semantics. |
| `config/bcu_pin.json` | Pinned bcu release tag (`v0.1.0`) consumed by /setup's opt-in installer. |
| `tools/computer/tests/test_computer_use.py` | Contract-level tests against a stdlib fake HTTP server -- method, path, and full JSON body shape for every route. |
| `tools/computer/tests/test_computer_use_integration.py` | Live-gated integration tests (real bcu); auto-skip when manifest absent. |

## Agent-integration shape

The `computer-use` skill context invokes **`valor-computer`** as the only invocation pattern. `python -m tools.computer ...` is intentionally not supported -- it would skip the OS gate and complicate test placement.

`pyproject.toml [project.scripts]`:
```toml
valor-computer = "tools.computer.cli:main"
```

## Screenshots (`screenshot` convenience command)

`valor-computer screenshot <window>` wraps `get_window_state`:

- Without `--output`: requests `imageMode: "path"` and returns the server-side `imagePath`.
- With `--output PATH`: requests `imageMode: "base64"`, decodes the image, and writes it to `PATH` (result carries `saved_to`).
- Error dicts from `get_window_state` pass through verbatim.

## Loopback-only

The bcu HTTP server binds to `127.0.0.1` only. There is no remote control surface. All requests go through stdlib `urllib.request` to the loopback URL stored in `$TMPDIR/background-computer-use/runtime-manifest.json` (key: `baseURL`; port is dynamic -- never assume a fixed port).

## Permissions

bcu requires two macOS permissions, granted by the operator in System Settings:

- **Privacy & Security -> Accessibility** -> add `BackgroundComputerUse.app`
- **Privacy & Security -> Screen Recording** -> add `BackgroundComputerUse.app`

These cannot be granted programmatically. The `/setup` skill (Step 8.5) prompts the operator before installing bcu and surfaces the permission requirement.

## Readiness gating (`bootstrap`)

`GET /v1/bootstrap` reports whether bcu is ready to run action routes. Its
`instructions.ready` boolean is the gate: `true` means click/type/screenshot will
succeed; `false` means bcu is running but the two macOS permissions above are not
granted yet. The response also carries `instructions.summary`, `instructions.agent`
(agent-facing recovery steps), `instructions.user` (user-facing recovery text), and
`permissions` (per-permission `granted`/`promptable`).

`valor-computer bootstrap` exposes this as a preflight check the agent runs **once
per session before the first action**:

- **exit 0** — `instructions.ready == true`; proceed with actions.
- **exit 78** (`EX_CONFIG`) — bcu is reachable but `instructions.ready == false`
  (permissions ungranted). The printed payload carries `instructions.user`; relay
  it to the user and stop rather than issuing blind actions.
- **exit 78** — bcu unavailable (`computer_use_unavailable`; manifest absent or app
  not running), same as every other command.
- **exit 1** — any other error (e.g. an HTTP 500 from bcu), not a readiness signal.

The exit code lets a script gate the first action: `valor-computer bootstrap &&
valor-computer click ...`. The gate is a single explicit call — it is deliberately
**not** injected into every action wrapper (that would double every request). The
`is_ready(bootstrap_result)` module predicate centralizes the "no error and
`instructions.ready` truthy" decision so the CLI and any future caller agree.

## Install + opt-in

Computer-use is **opt-in**. The `/setup` skill asks "Do you want to enable computer-use?" before any download. On yes, it writes a sentinel at `~/.config/valor/computer-use-enabled` and proceeds. On no, the sentinel is never written and `/update` leaves bcu alone.

## Update flow

`config/bcu_pin.json` pins `release_tag: "v0.1.0"`. The /update resolver downloads the asset for the pinned release, verifies the SHA against the GitHub-published `.sha256` companion asset, and installs to `~/Applications/BackgroundComputerUse.app`. Pin bumps only via `/update --bump-bcu`.

## Example workflows

**Drive Notes:**

```bash
valor-computer list_windows Notes                     # pick the string window ID
valor-computer click <window> --x 400 --y 300         # click body (coords)
valor-computer type_text <window> "Reminder: ..."
valor-computer screenshot <window> --output /tmp/notes.png
```

**Drive Slack via targets:**

```bash
valor-computer list_windows Slack
valor-computer get_window_state <window>              # node IDs + stateToken

valor-computer click <window> \
  --target '{"kind":"node_id","value":"<channel_node>"}' --state-token <tok>

valor-computer type_text <window> "Build complete"
valor-computer press_key <window> cmd+return          # send (chord in key string)
```

## Failure modes

- **`{"error": "computer_use_unavailable", ...}`** -- bcu not installed, not opted in, or not running. CLI exits **78** with the structured error in stdout. Tell the user to run `/setup` and answer "yes" to the computer-use opt-in.
- **`{"error": "not_found", "path": ...}`** -- the route does not exist on the running bcu (version drift between the installed app and this CLI). Check the installed version against `config/bcu_pin.json`.
- **Stale `stateToken` / unknown window** -- a JSON-level rejection in the action response (not an HTTP 404). Re-run `get_window_state` (or `list_windows`) and retry with fresh values.
- **`{"error": "invalid_argument", ...}`** -- caller mistake (e.g. `click` with both `--target` and `--x/--y`, or neither). CLI exits 1.
- **`{"error": "timeout", ...}`** -- bcu took longer than 10s. Loopback HTTP timeouts are unusual; check that the bcu app is responsive (Activity Monitor or `pgrep -f BackgroundComputerUse`).
- **bcu Accessibility permission revoked** (macOS upgrade or re-sign): bcu calls fail. The agent surfaces "bcu Accessibility permission revoked -- open System Settings -> Privacy & Security -> Accessibility". Operator re-grants.

## Live verification status

The contract was migrated against the tagged v0.1.0 Swift source (`RouteRegistry.swift`) and asserted with fake-server contract tests. Live smoke testing runs on the computer-use opt-in machine after `/update` (`valor-computer list_apps`).

## See also

- [BYOB Browser Control](byob-browser-control.md) -- sibling feature for real-Chrome browser automation
- `.claude/skill-context/computer-use.md` -- the agent-facing usage guide
