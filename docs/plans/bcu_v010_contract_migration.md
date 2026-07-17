---
status: Ready
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-07-17
tracking: https://github.com/tomcounsell/ai/issues/2114
last_comment_id: none
revision_applied: true
revision_applied_at: 2026-07-17T05:47:14Z
---

# bcu v0.1.0 Contract Migration (valor-computer CLI)

## Problem

`tools/computer/__init__.py` and `tools/computer/cli.py` were written against an older/planned bcu (background-computer-use) API contract. The shipped v0.1.0 runtime — installed via /setup on Tom's MacBook Air, the first computer-use opt-in machine — rejects most of what the CLI sends.

**Current behavior:**
- `list_apps` / `list_windows` / `get_window_state` are sent as `GET` with query params → bcu returns `not_found` (v0.1.0 serves them as `POST` with JSON bodies).
- `screenshot_window` calls `GET /v1/screenshot_window`, a route that does not exist in v0.1.0.
- Action bodies use `window_id` (int) + raw AX `ref` objects; v0.1.0 expects `window` (string stable ID), `target` objects (`display_index` / `node_id` / `refetch_fingerprint`), and optional `stateToken` stale-target guards.
- `scroll` sends `dx`/`dy` (v0.1.0: `direction` + `pages`); `drag` sends from/to point pairs (v0.1.0: window motion `toX`/`toY`); `resize` sends `width`/`height` (v0.1.0: `handle` + `toX`/`toY`); `press_key` sends a `modifiers` array (v0.1.0: single `key` chord string).
- The homegrown Electron selector re-resolution (`_resolve_selector`, `_walk_ax_tree`, `tools/computer/electron_bundles.py`) duplicates what v0.1.0 now does server-side via `stateToken` + `refetch_fingerprint` targets.
- `config/bcu_pin.json` still pins `release_tag: "latest"` — future upstream releases would drift us silently.

**Desired outcome:** `valor-computer` speaks the exact v0.1.0 contract (verified against the tagged source's `RouteRegistry.swift`), screenshots flow through `get_window_state` `imageMode`, the pin is a real tag, and docs/skill/persona examples match reality.

## Freshness Check

**Baseline commit:** 84f3c12d5
**Issue filed at:** 2026-07-16 (during /setup on Tom's MacBook Air)
**Disposition:** Unchanged (one item already fixed and excluded)

**File:line references re-verified:**
- `tools/computer/__init__.py:276-302` — discovery/state wrappers send GET — still holds.
- `tools/computer/__init__.py:480-488` — `screenshot_window` targets the removed route — still holds.
- `tools/computer/__init__.py:106-115` — manifest reader accepts both `base_url` and `baseURL` — the issue's "manifest key casing" bullet is **already fixed on main**; excluded from scope.
- `config/bcu_pin.json` — `release_tag: "latest"` placeholder — still holds.

**Cited sibling issues/PRs re-checked:** none cited.

**Commits on main since issue was filed (touching referenced files):** none touching `tools/computer/` or `config/bcu_pin.json`.

**Active plans in `docs/plans/` overlapping this area:** none.

## Prior Art

- **PR that shipped computer-use** (`docs/features/computer-use.md`, `tools/computer/`): built the wrapper before any bcu release existed, coding to a planned contract — the root cause of this drift.
- The setup-session hotfix on main fixed manifest key casing (`baseURL`), the only part of the drift already handled.
- No prior attempts at this migration; no related closed issues found beyond #2114 itself.

## Research

External contract source (no WebSearch needed — the vendor repo is public and pinned):

- `actuallyepic/background-computer-use` tag `v0.1.0` (commit 52116ac), `Sources/BackgroundComputerUse/API/RouteRegistry.swift` — the machine-readable route catalog served at `/v1/routes`. This is the authoritative contract used throughout this plan.
- Release `v0.1.0` is the only published release; asset `BackgroundComputerUse.app.zip`, sha256 `37ff202b6155ffdd29d30609049d5d12d9e9d3d93e22375f7268e171e39352eb`.
- `skills/background-computer-use/references/runtime.md` (upstream) confirms: manifest key is `baseURL`; clients must not assume a fixed port; `/v1/bootstrap` `instructions.ready` gates action routes.

### v0.1.0 contract summary (from RouteRegistry.swift)

| Route | Method | Request body (required in bold) |
|---|---|---|
| `/health`, `/v1/bootstrap`, `/v1/routes` | GET | — |
| `/v1/list_apps` | POST | `{}` |
| `/v1/list_windows` | POST | **`app`** (name, bundle ID, or query) |
| `/v1/get_window_state` | POST | **`window`** (string ID), `imageMode: path\|base64\|omit` (default `path`), `includeMenuBar`, `menuPath`, `webTraversal`, `maxNodes`, debug flags |
| `/v1/click` | POST | **`window`**, `stateToken`, `target` XOR `x`+`y`, `mode: single\|double`, `clickCount`, `mouseButton`, `cursor`, `imageMode`, `debug` |
| `/v1/scroll` | POST | **`window`**, **`target`**, **`direction: up\|down\|left\|right`**, `pages`, `stateToken`, `verificationMode`, `cursor`, `imageMode` |
| `/v1/perform_secondary_action` | POST | **`window`**, **`target`**, **`action`** (exact label), `actionID`, `stateToken`, `menuPath`, `cursor`, `imageMode` |
| `/v1/drag` | POST | **`window`**, **`toX`**, **`toY`**, `cursor` (window motion) |
| `/v1/resize` | POST | **`window`**, **`handle`** (left/right/top/bottom/topLeft/topRight/bottomLeft/bottomRight), **`toX`**, **`toY`**, `cursor` |
| `/v1/set_window_frame` | POST | **`window`**, **`x`**, **`y`**, **`width`**, **`height`**, `animate` (default true), `cursor` |
| `/v1/type_text` | POST | **`window`**, **`text`**, `target` (optional), `focusAssistMode: none\|focus\|focus_and_caret_end`, `stateToken`, `cursor`, `imageMode` |
| `/v1/press_key` | POST | **`window`**, **`key`** (key or chord string; NO modifiers array), `stateToken`, `cursor`, `imageMode` |
| `/v1/set_value` | POST | **`window`**, **`target`**, **`value`** (string), `stateToken`, `cursor`, `imageMode` |

`target` = `{"kind": "display_index"|"node_id"|"refetch_fingerprint", "value": int|str}`. `get_window_state` responses carry `stateToken`, `screenshot.image.imagePath|imageBase64` (`pixelWidth`/`pixelHeight`, `mimeType`), `tree`, `focusedElement`, `notes`. Action responses carry `ok`, `classification`, `preStateToken`/`postStateToken`, `warnings`.

**Live verification status:** no bcu runtime on this machine (macOS opt-in, installed only on Tom's MacBook Air). All work in this plan is **contract-level** — request shapes asserted against a local fake HTTP server; the contract itself extracted from the tagged Swift source, which generates the live `/v1/routes` catalog. Live verification deferred to the opted-in machine.

## Data Flow

1. **Entry point:** agent runs `valor-computer <command> ...` (Bash tool; `pyproject.toml [project.scripts]` → `tools.computer.cli:main`).
2. **CLI (`tools/computer/cli.py`):** OS gate (darwin-only, exit 78) → argparse → dispatch to module functions.
3. **Module (`tools/computer/__init__.py`):** reads `baseURL` from `$TMPDIR/background-computer-use/runtime-manifest.json` → builds POST JSON body per contract → `urllib` request → returns parsed dict (or structured error dict; `ComputerUseUnavailableError` when manifest/server absent).
4. **bcu Swift app:** resolves window/target, dispatches AX/CGEvent action, returns verification-rich JSON.
5. **Output:** CLI prints JSON; screenshot path/bytes surfaced from `get_window_state.screenshot.image`.

## Solution

Full cutover of `tools/computer` to the v0.1.0 contract. No compatibility shims, no legacy paths.

**Module (`tools/computer/__init__.py`):**
- All discovery/state/action wrappers become POST with JSON bodies; `window` params are strings.
- `list_windows(app)` replaces `list_windows(bundle_id=...)` (contract field is `app`, accepts name/bundle/query; required).
- `get_window_state(window, *, image_mode="path", include_menu_bar=None, max_nodes=None)` — the single state+screenshot surface.
- New `screenshot(window, output=None)` convenience: calls `get_window_state` with `imageMode: "base64"` when `output` is given (decode + write file), else `imageMode: "path"` and returns the server-side `imagePath`. Replaces `screenshot_window` (deleted).
- Action wrappers accept `target: dict | None` (passed through verbatim) and `state_token: str | None`; `click` keeps `x`/`y` (mutually exclusive with `target`, per contract) plus `mode`/`click_count`/`mouse_button`.
- `scroll(window, target, direction, pages=None, ...)`; `drag(window, to_x, to_y)`; `resize(window, handle, to_x, to_y)`; `set_window_frame(window, x, y, width, height, animate=None)`; `type_text(window, text, target=None, focus_assist_mode=None, ...)`; `press_key(window, key, ...)` (modifiers removed — chords go in the key string); `set_value(window, target, value, ...)`; `perform_secondary_action(window, target, action, action_id=None, ...)`.
- Delete `_resolve_selector`, `_walk_ax_tree`, and `tools/computer/electron_bundles.py` — v0.1.0 handles staleness server-side (`stateToken` + `refetch_fingerprint`). Update module docstring accordingly.
- Keep `_read_base_url` (dual-casing) and the error-dict/`ComputerUseUnavailableError` transport behavior; 404 mapping simplifies to `{"error": "not_found", "path": ...}` (no more window_id-keyed variant — unknown windows are a JSON-level error in v0.1.0, not an HTTP 404).
- `screenshot()` MUST return the `get_window_state` error dict unchanged when `"error" in state` — never touch `state["screenshot"]["image"]` or attempt base64 decode on an error payload (guard lives in the module, not just the CLI).
- No `bootstrap()` wrapper in this migration (critique blocker: no consumer). Readiness-gating via `/v1/bootstrap` `instructions.ready` is filed as a follow-up issue.

**CLI (`tools/computer/cli.py`):**
- Subcommands mirror the new module signatures: `list_windows <app>`; `get_window_state <window> [--image-mode path|base64|omit]`; `screenshot <window> [--output PATH]` (replaces `screenshot_window`); `click <window> [--x --y | --target JSON] [--state-token T] [--mode single|double] [--button left|right|middle]`; `scroll <window> --target JSON --direction up|down|left|right [--pages N]`; `type_text <window> TEXT [--target JSON] [--focus-assist none|focus|focus_and_caret_end]`; `press_key <window> KEY` (drop `--mod`); `set_value <window> VALUE --target JSON`; `perform_secondary_action <window> --target JSON --action LABEL [--action-id ID]`; `drag <window> --to-x --to-y`; `resize <window> --handle H --to-x --to-y`; `set_window_frame <window> X Y W H [--no-animate]`.
- `window` positional args are strings (no `type=int`).
- Exit-code behavior unchanged: 78 for OS gate / unavailable, 1 for error dicts, 0 otherwise.

**Pin (`config/bcu_pin.json`):** `release_tag: "v0.1.0"`, refresh `checked_at`, drop the placeholder note. (No stored sha256 — the `/update` resolver already verifies against the release's `.sha256` companion asset.)

**Docs/skill/persona:** update every `screenshot_window` / `--bundle-id` / int-window example (see Documentation section).

## Step by Step Tasks

1. Create worktree `.worktrees/bcu_v010_contract_migration` on branch `session/bcu_v010_contract_migration`.
2. Rewrite `tools/computer/__init__.py` to the v0.1.0 contract (POST bodies, string windows, target/stateToken, `screenshot()` via `get_window_state` imageMode, `bootstrap()`, delete selector machinery).
3. Delete `tools/computer/electron_bundles.py`.
4. Rewrite `tools/computer/cli.py` subcommands to match; keep OS gate and exit-code contract.
5. Rewrite `tools/computer/tests/test_computer_use.py` as contract-level tests against a stdlib fake HTTP server: assert method, path, and full JSON body shape for every route; screenshot output-file writing; error mapping; OS gate; CLI dispatch.
6. Update `tools/computer/tests/test_computer_use_integration.py`: keep live-gated tests (skip when manifest absent) but target the v0.1.0 routes; drop `screenshot_window`.
7. Update `config/bcu_pin.json` to `v0.1.0`.
8. Update docs (`docs/features/computer-use.md`, `docs/features/tools-reference.md`), skill context (`.claude/skill-context/computer-use.md` — the sole skill-side content target; `.claude/skills-global/computer-use/SKILL.md` is a generic baseline needing NO edits), persona (`config/personas/segments/tools.md`), and `CLAUDE.md` quick-command rows (`list_windows --bundle-id` → `list_windows <app>`, `screenshot_window` → `screenshot`, int window_id → string window).
9. Run ruff + the `tools/computer/tests/` suite (narrow scope); open PR with `Closes #2114`, stating live-verified vs contract-only.
10. Post-merge: on the opted-in machine (Tom's MacBook Air) after `/update`, run `valor-computer list_apps` as a live smoke test and report success/failure back on #2114 — the PR's contract-only status must not stand indefinitely.

## Success Criteria

- Every request the module emits matches the v0.1.0 `RouteRegistry.swift` schema: method POST, correct path, required fields present, no stale fields (`window_id`, `bundle_id`, `dx`/`dy`, `modifiers`, `ref`, `from_x`…). Asserted by fake-server tests.
- `grep -rn "screenshot_window\|bundle_id\|electron_bundles" tools/ docs/features/ config/personas .claude/skill* CLAUDE.md` returns no computer-use hits (full cutover, no legacy traces). Historical docs (`docs/roadmap-*`, `docs/plans/`) are deliberately out of scope for this sweep.
- `valor-computer` on a non-darwin host still exits 78; with no manifest still exits 78 with `computer_use_unavailable`.
- ruff format + check clean; `scripts/pytest-clean.sh tools/computer/tests/ -n0` green.

## Failure Path Test Strategy

- Fake server returning HTTP 404 → `{"error": "not_found", ...}`, CLI exit 1.
- Fake server returning HTTP 500 with body → `{"error": "http_500", "message": ...}`.
- Connection refused → `ComputerUseUnavailableError` → CLI exit 78.
- Missing/malformed manifest → exit 78.
- `click` with both `target` and `x`/`y` → `ValueError`/`invalid_argument` (contract says mutually exclusive).
- `click` with neither → `invalid_argument`.
- Invalid `--target` JSON on CLI → `SystemExit` with clear message.
- `screenshot()` when `get_window_state` returns `{"error": "not_found", ...}` → error dict returned verbatim, no `KeyError`/`binascii.Error` (fake-server test).

## Test Impact

- [ ] `tools/computer/tests/test_computer_use.py` — REPLACE: rewrite as contract-level fake-server tests (all route shapes changed; selector/electron tests deleted with the feature).
- [ ] `tools/computer/tests/test_computer_use_integration.py` — UPDATE: live-gated tests move to POST routes and `get_window_state` imageMode; `screenshot_window` test removed.
- [ ] No other test files import `tools.computer` or `electron_bundles` (verified via grep).

## Risks

- **Contract read from source, not a live server.** Mitigated: `RouteRegistry.swift` is the generator for the live `/v1/routes` catalog, and the pinned tag matches the installed release. Residual risk of server-side behavior nuances (e.g. error payload shapes) — flagged in the PR as contract-only.
- **Agent muscle memory / docs drift**: any other doc or memory referencing `screenshot_window` goes stale — swept by the grep in Success Criteria.
- **`list_windows` interface change** (`--bundle-id` → positional `app`) breaks saved invocations; acceptable — the old form never worked against v0.1.0 anyway.

## Rabbit Holes

- Do NOT implement cursor sessions (`cursor` request field) beyond passing through an optional JSON blob — full cursor lifecycle is future work.
- Do NOT build a client-side stale-target retry loop; `stateToken` is passed through, retries are the caller's business.
- Do NOT attempt to install/run bcu on this machine to get live verification — it is opt-in hardware-gated work for the MacBook Air.
- Do NOT redesign the skill UX; only update examples to the new command shapes.

## No-Gos

- No compatibility aliases for removed flags/commands (`--bundle-id`, `--mod`, `screenshot_window`) — full cutover.
- No changes to bcu itself or its install scripts beyond the pin value.
- No new Popoto models — no migrations needed.

## Update System

`config/bcu_pin.json` is consumed by /setup's computer-use opt-in installer on opted-in machines. Changing `release_tag` from `latest` to `v0.1.0` makes the installed version deterministic; the resolver already handles explicit tags (release-JSON asset lookup + sha256 verify), so **no changes to `scripts/update/run.py` or `scripts/update/migrations.py` are required**. Non-opted-in machines are unaffected. Machines already running v0.1.0 (MacBook Air) see no version change — only the CLI starts speaking the right contract after `/update` pulls main.

## Agent Integration

No new entry points: `valor-computer` remains the single CLI in `pyproject.toml [project.scripts]`, invoked via Bash by the `computer-use` skill. The skill body and `.claude/skill-context/computer-use.md` are updated to the new subcommand shapes (that IS the agent integration surface). No MCP server or `.mcp.json` changes. No bridge imports.

## Documentation

- [ ] Update `docs/features/computer-use.md` — POST contract, `get_window_state` imageMode screenshots, `screenshot` convenience command, target/stateToken semantics, pin now `v0.1.0`.
- [ ] Update `docs/features/tools-reference.md` computer-use rows.
- [ ] Update `.claude/skill-context/computer-use.md` examples (`.claude/skills-global/computer-use/SKILL.md` is generic — no edits needed).
- [ ] Update `config/personas/segments/tools.md` example block.
- [ ] Update `CLAUDE.md` quick-command table rows for `valor-computer`.
