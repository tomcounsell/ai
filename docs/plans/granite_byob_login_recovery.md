---
status: Planning
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-06-21
tracking: https://github.com/tomcounsell/ai/issues/1750
last_comment_id:
revision_applied: false
---

# Deterministic Granite /login Re-Auth Recovery via Pure-Python BYOB Driver

## Problem

The granite container (`agent/granite_container/`) drives interactive `claude` TUI sessions over PTYs, authenticating via a Claude subscription over OAuth (the PTY driver deliberately blanks `ANTHROPIC_API_KEY` to force the subscription path). When that OAuth token expires or rotates, a PTY paints a login frame mid-run.

**Current behavior:**
- `startup_parser.py:67-73` detects the login frame and emits `StartupEvent.LOGIN_PROMPT`.
- `container.py:751-758` matches the event but its `response` is `None`, so the startup loop **passively waits up to `STARTUP_HARD_CEILING_S` (600s)**, then exits `startup_unresolved` and fires a Telegram alert. A human must manually complete OAuth for the session to proceed — the agent is dead in the water until then.

**Desired outcome:**
- Granite **autonomously recovers** the login by driving the already-logged-in real Chrome (via BYOB) through the OAuth consent — a fixed, deterministic recipe with **no LLM intelligence in the loop**. The recipe is credential-independent (pure-Python MCP stdio client, no `claude` session), sidestepping the shared-credential bootstrap deadlock. On recovery failure it degrades to today's alert path (no new hang).

## Freshness Check

**Baseline commit:** `90791cbd90a3e280e5e3abcf680d1bc18a09f09a`
**Issue filed at:** 2026-06-21T08:01:20Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `agent/granite_container/startup_parser.py:67-73` — `_LOGIN_PATTERNS` detects login frame → `LOGIN_PROMPT`. Still holds (read at plan time).
- `agent/granite_container/container.py:751-758` — `LOGIN_PROMPT` branch returns `r.response` (=`None`). Still holds.

**Cited sibling issues/PRs re-checked:** None cited.

**Commits on main since issue was filed (touching referenced files):** None (`git log --since=createdAt -- agent/granite_container/` empty).

**Active plans in `docs/plans/` overlapping this area:** None.

**Notes:** Issue filed <1h before planning; no drift.

## Prior Art

No prior issues or PRs found related to this work (`gh issue list --search "granite login relogin authenticate"` and `gh pr list --search "granite login startup_parser"` both empty). This is greenfield recovery logic layered onto the existing detection in `startup_parser.py`.

## Research

**Queries used:**
- "Claude Code /login OAuth flow localhost callback vs paste code headless automation"

**Key findings:**
- Claude Code's OAuth has two redirect shapes confirmed by [Claude Code Authentication docs](https://code.claude.com/docs/en/authentication): interactive **localhost callback** (`redirect_uri=http://localhost:PORT/callback`, CLI runs a local server that catches the code automatically) and **manual paste** (`redirect_uri=https://platform.claude.com/oauth/code/callback`, displays a code to paste). This matches the two parallel tabs observed empirically (both shared one `code_challenge`/`state`). The localhost flow is the one that needs zero pasting — the recovery's happy path.
- **Alternative worth noting:** `claude setup-token` mints a ~1-year subscription-backed `CLAUDE_CODE_OAUTH_TOKEN` (no API key, no per-token billing). This does not eliminate the need for browser-drive recovery (setup-token itself needs the same OAuth, and the token still expires), but it dramatically reduces *how often* recovery fires. Captured as a mitigation in Risks. Source: [Claude Code Authentication docs](https://code.claude.com/docs/en/authentication), [headless OAuth issue #29983](https://github.com/anthropics/claude-code/issues/29983).

## Spike Results

All spikes were run empirically (live trial-and-error) during the issue-creation session. Reference scripts live at `/tmp/byob_spike.py`, `/tmp/relogin_e2e.py`, `/tmp/relogin_auto.py` on the dev machine.

### spike-1: Pure-Python can drive BYOB's MCP server with no LLM
- **Assumption**: "A plain Python process can speak to BYOB's MCP server and drive the real Chrome."
- **Method**: prototype
- **Finding**: BYOB's MCP server is stdio JSON-RPC (`tsx ~/.byob/packages/mcp-server/bin/byob-mcp.ts`, `BYOB_ALLOW_EVAL=1`, registered in `~/.claude.json` `mcpServers.byob`). A ~120-line stdio client completed `initialize` → `tools/list` (33 tools incl. `browser_navigate`, `browser_click`, `browser_read`, `browser_eval`, `browser_list_tabs`) → `browser_navigate` against the live shared Chrome bridge. Newline-delimited JSON-RPC; `notifications/initialized` after init.
- **Confidence**: high
- **Impact on plan**: Confirms option B (pure-Python driver). No `claude` session needed → no shared-credential deadlock.

### spike-2: The OAuth consent collapses to one deterministic click
- **Assumption**: "The browser BYOB drives is already logged into claude.ai, so OAuth is one Authorize click."
- **Method**: prototype
- **Finding**: `https://claude.ai/` redirects to `/new`; the consent page shows "Logged in as <user>" + a single **Authorize** button (sibling **Decline**). Buttons have **no `id`/`data-testid`/`name`** — only stable anchor is `innerText === 'Authorize'`. OAuth `client_id` = `9d1c250a-e61b-44d9-88ed-5944d1962f5e`.
- **Confidence**: high
- **Impact on plan**: The click step is a fixed recipe (no reasoning). Selector strategy must be text-based.

### spike-3: claude 2.1.185 login flow shape + paste payload
- **Assumption**: "We can extract the OAuth URL and complete login deterministically."
- **Method**: prototype (throwaway isolated `CLAUDE_CONFIG_DIR`, real `/login`)
- **Finding**: Flow = theme picker → "Select login method" menu (option 1 = subscription, default `❯1`) → claude **auto-opens** Chrome to a `localhost`-callback authorize URL **and** prints a `platform.claude.com` paste-flow fallback URL under "Browser didn't open? Use the url below (c to copy)" + a `Paste code here if prompted >` prompt. After Authorize, paste flow lands on `platform.claude.com/oauth/code/callback?code=...&state=...`; the paste payload is `{code}#{state}` (in the callback URL query params **and** a `<pre>`).
- **Confidence**: high
- **Impact on plan**: Three runtime flows must be handled; happy path uses the auto-opened localhost tab and needs no paste.

### spike-4: BYOB automation gotchas
- **Assumption**: "Driving the consent page is straightforward."
- **Method**: prototype
- **Finding**: (a) `browser_eval` of a **bare expression returns `null`** — must wrap in an IIFE; (b) `browser_eval` on a tab **after it navigates fails** (CDP context detaches) — poll `browser_list_tabs` for the callback URL instead; (c) clicking Authorize **before the React page hydrates** (~1.5s) is a silent no-op — wait for the button to exist, then retry the click until the tab leaves the authorize URL; (d) the auto-opened claude tab + a self-opened tab = two confusing tabs, so operate on claude's tab where possible; (e) possible trusted-event/mouse-movement gating — prefer `browser_click` (CDP trusted dispatch) over `eval`'d `.click()`.
- **Confidence**: high
- **Impact on plan**: Encodes the driver's wait/retry/poll discipline; dictates `browser_click` over eval-click.

## Data Flow

1. **Entry point**: A granite PTY (PM or Dev) paints an OAuth login frame mid-startup; `pty_driver` reads the buffer.
2. **Detection** (`startup_parser.parse_startup_frame`): regex matches → `StartupEvent.LOGIN_PROMPT`.
3. **Dispatch** (`container._handle_startup` → `run()` startup loop): the `LOGIN_PROMPT` branch (today `return None`) invokes `byob_relogin.recover_login(pty_driver, pty_buffer, ...)`.
4. **Recovery** (`byob_relogin`): spawns a pure-Python BYOB MCP stdio client → classifies flow (auto-opened localhost tab? platform paste URL? logged-out browser?) → drives Chrome (navigate/click/poll) → for paste flow, writes `{code}#{state}` into the PTY; for localhost flow, presses Enter; closes the MCP client.
5. **Settle**: the PTY leaves the login frame; the startup loop detects idle → proceeds to steady-state.
6. **Output / fallback**: on recovery success, the session runs normally. On failure (timeout/retries exhausted), `recover_login` returns a failure sentinel and the loop falls through to the existing `startup_unresolved` ceiling + Telegram alert.

## Architectural Impact

- **New dependencies**: `pexpect` (already in the venv, used by `pty_driver`) for PTY interaction; no new third-party libs — the BYOB MCP client is hand-rolled stdio JSON-RPC over `subprocess`. Hard runtime dependency on the BYOB install (`~/.byob`) and a running Chrome bridge.
- **Interface changes**: New module `agent/granite_container/byob_relogin.py` exposing a `recover_login(...)` entry + a small `BYOBClient` class. One call site changes in `container.py` (the `LOGIN_PROMPT` branch).
- **Coupling**: Adds a granite→BYOB coupling that did not exist. Isolated behind `byob_relogin` so the container only sees `recover_login()`.
- **Data ownership**: None changed. The keychain credential is owned/written by the `claude` PTY process itself (recovery just completes the browser side).
- **Reversibility**: High — revert the `container.py` call site to `return None` and delete the module; no schema/state migration.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1-2 (flow-selection ordering, fallback policy)
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| BYOB installed | `test -f ~/.byob/packages/mcp-server/bin/byob-mcp.ts && echo ok` | The MCP server the driver spawns |
| BYOB registered | `python -c "import json,os; d=json.load(open(os.path.expanduser('~/.claude.json'))); assert 'byob' in d.get('mcpServers',{})"` | Confirms the canonical invocation/env |
| pexpect available | `.venv/bin/python -c "import pexpect"` | PTY interaction |

Run all checks: `python scripts/check_prerequisites.py docs/plans/granite_byob_login_recovery.md`

## Solution

### Key Elements

- **`BYOBClient` (pure-Python MCP stdio client)**: Spawns `tsx byob-mcp.ts` with `BYOB_ALLOW_EVAL=1`, does the `initialize`/`notifications/initialized` handshake, exposes `navigate`, `click`, `list_tabs`, `eval`, `read` helpers over newline-delimited JSON-RPC. No LLM, no `claude` session.
- **Flow classifier**: Inspects `browser_list_tabs` + the PTY buffer to decide which of the three flows applies (auto-opened localhost authorize tab present → flow 1; only a printed `platform.claude.com` paste URL → flow 2; consent page shows logged-out / no account → flow 3).
- **`recover_login(pty_driver, pty_buffer, deadline)` routine**: Orchestrates the recipe per flow against the PTY + `BYOBClient`, bounded by a hard deadline and per-flow retries; returns a `ReloginOutcome` (success / failed-degrade).
- **Container wiring**: Replaces the passive `return None` in the `LOGIN_PROMPT` branch with a bounded `recover_login` attempt; on failure, falls through to the existing ceiling/alert.

### Flow

**PTY shows login frame** → `LOGIN_PROMPT` detected → `recover_login` classifies flow →
- **Flow 1 (localhost auto-complete):** find claude's auto-opened authorize tab → wait for hydration → `browser_click` Authorize (retry until tab leaves authorize URL) → localhost callback auto-completes → press **Enter** in PTY → **logged in**.
- **Flow 2 (paste fallback):** reconstruct the wrapped authorize URL from the PTY buffer → `browser_navigate` → click Authorize → poll `browser_list_tabs` for the `oauth/code/callback` URL → parse `{code}#{state}` → write into PTY at `Paste code here >` → Enter → **logged in**.
- **Flow 3 (browser needs auth):** detect logged-out consent → click **"Continue with Google"** (Google session usually live) → fall through to flow 1/2.
- **Any flow fails within deadline** → return failure → container falls back to `startup_unresolved` + Telegram alert.

### Technical Approach

- **Module**: `agent/granite_container/byob_relogin.py`. Promote the validated spike's `MCPStdioClient` into `BYOBClient`. Keep it synchronous (the container startup loop is synchronous, driven via `asyncio.to_thread` upstream) to match `pty_driver`'s pexpect style.
- **URL extraction (flow 2)**: From the PTY buffer, slice from the first `https://claude` to the `Paste\s*code` sentinel and strip all whitespace (de-wraps the terminal line-wrapping). Validated in spike-3.
- **Callback parse**: `urllib.parse` the callback URL's `code`/`state` from `browser_list_tabs` (NOT post-nav `eval` — spike-4b). Payload = `f"{code}#{state}"`.
- **Click discipline**: prefer `browser_click` (trusted CDP dispatch) targeting the Authorize button; fall back to IIFE-wrapped `eval` `.click()` only if needed. Poll for redirect; retry up to N (spike-4c/e).
- **Account guard**: before clicking Authorize, read the consent page's "Logged in as <user>" and compare against an expected-identity config (e.g. `config/identity.json` email); abort recovery (degrade to alert) on mismatch rather than authorize the wrong account.
- **Bounding**: a single overall deadline (well under the existing 600s ceiling, e.g. 90-120s) plus per-step timeouts; always close the `BYOBClient` subprocess in `finally`.
- **Trigger surface**: wire into the startup-phase `LOGIN_PROMPT` branch first. If `startup_parser` lacks patterns for the menu/auto-open frames, add them (the "Select login method" / "Opening browser" frames) so detection fires reliably.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `BYOBClient` subprocess spawn failure (missing tsx/BYOB) must log a warning and return a failure outcome (degrade to alert), with a test asserting the warning + outcome.
- [ ] MCP handshake timeout / JSON-RPC error must not raise out of `recover_login` — test asserts it returns failure, not an exception.
- [ ] Any `except Exception` in the new module must log (logger.warning) and set an observable outcome — no bare `pass`. Test each.

### Empty/Invalid Input Handling
- [ ] PTY buffer with no extractable URL (flow 2) → recovery returns failure with a logged reason (test with empty/garbled buffer fixture).
- [ ] `browser_list_tabs` returning zero authorize tabs (flow 1) within the deadline → fall through to flow 2 or failure (test).
- [ ] Callback URL missing `code`/`state` → failure, not a crash (test with malformed URL fixture).

### Error State Rendering
- [ ] On recovery failure the existing Telegram `startup_unresolved` alert must still fire — integration test asserts the fallback path is reached (BYOB client mocked to always fail).
- [ ] Recovery success/failure emits a `session_events` observability entry (consistent with existing startup-event logging) — test asserts the event is recorded.

## Test Impact

- [ ] `tests/` — no existing granite-startup test asserts `LOGIN_PROMPT` returns `None`; grep at build time to confirm. If one exists (e.g. `tests/unit/test_granite_startup_parser.py` or a container startup test), UPDATE it to assert the new recovery dispatch.
- [ ] `tests/unit/test_granite_*` — ADD new unit tests for `BYOBClient` (mocked subprocess) and the flow classifier / `recover_login` state machine (mocked BYOB client + fixture PTY frames).

No existing tests are expected to break — the change is additive (replaces an inert `return None` with a bounded call that defaults to the same fallback). Confirm with a grep for `LOGIN_PROMPT` across `tests/` during build; UPDATE any that asserted the old passive behavior.

## Rabbit Holes

- **Don't build a generic browser-OAuth framework.** Scope is exactly the Claude Code consent recipe (one client_id, known DOM). Generality is wasted time.
- **Don't try to make `browser_eval` work post-navigation.** It detaches; poll `browser_list_tabs`. Spike-4b already settled this — don't re-litigate.
- **Don't chase the localhost auto-complete as the *only* path.** The paste fallback and Google-unlock paths are required (per issue). A localhost-only implementation will strand real sessions.
- **Don't write/read the macOS Keychain directly.** The `claude` PTY owns the token exchange; recovery only completes the browser side and presses Enter / pastes the code.
- **Don't make the driver async.** The container startup loop is synchronous; matching pexpect's sync style avoids an event-loop bridge for no benefit.

## Risks

### Risk 1: Authorizing the wrong Claude account
**Impact:** If the browser is logged into a different claude.ai account, blind recovery would authorize the granite session as the wrong identity.
**Mitigation:** Account guard — read "Logged in as <user>" from the consent page and compare to the expected identity (`config/identity.json`) before clicking Authorize; abort to the alert path on mismatch.

### Risk 2: BYOB/Chrome bridge not running when recovery fires
**Impact:** No browser to drive → recovery can't complete.
**Mitigation:** Detect spawn/handshake failure fast, log, and degrade to the existing `startup_unresolved` alert — strictly no worse than today. Document the BYOB dependency in the feature doc.

### Risk 3: Consent-page DOM or login-method menu changes in a future claude/claude.ai release
**Impact:** Text-anchored selectors / menu navigation break; recovery silently fails.
**Mitigation:** Keep selectors centralized and text-based with a clear failure→alert path; add a feature-doc note to re-capture the DOM on breakage. **Strong complementary mitigation:** adopt `claude setup-token` → `CLAUDE_CODE_OAUTH_TOKEN` (~1yr subscription token) to make login-recovery a rare event rather than a hot path (tracked as a follow-up, see No-Gos).

### Risk 4: Trusted-event gating on the Authorize button
**Impact:** Synthetic clicks ignored; tab never redirects.
**Mitigation:** Use `browser_click` (CDP trusted dispatch) with hydration wait + retry-until-redirect; spike-4 showed eval-click can no-op.

## Race Conditions

### Race 1: Clicking Authorize before the consent page hydrates
**Location:** `byob_relogin.recover_login` (click step)
**Trigger:** Navigating/finding the tab and clicking immediately, before React attaches the button handler.
**Data prerequisite:** The Authorize button must exist AND be interactive.
**State prerequisite:** Page hydrated.
**Mitigation:** Poll for the button's existence (IIFE eval) then retry the click until `browser_list_tabs` shows the tab left the authorize URL (bounded retries). Spike-4c.

### Race 2: Two authorize tabs (claude's auto-open + a self-opened one)
**Location:** Flow classification
**Trigger:** Recovery opens its own tab while claude already auto-opened one; both share `code_challenge`/`state`.
**Data prerequisite:** Exactly one tab should be acted on.
**State prerequisite:** Know which tab is claude's.
**Mitigation:** Prefer claude's auto-opened localhost tab (flow 1); only self-navigate in flow 2 when no auto-opened tab exists. De-dupe by matching the current session's `state`.

### Race 3: Pasting the code before the PTY shows the paste prompt
**Location:** Flow 2 paste-back
**Trigger:** Writing `{code}#{state}` into the PTY before `Paste code here >` is painted.
**Data prerequisite:** The PTY must be at the paste prompt.
**State prerequisite:** Code captured from the callback.
**Mitigation:** Wait for the `Paste code here` sentinel in the PTY buffer (read_until) before writing; bounded timeout.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1751] Adopting `claude setup-token` / `CLAUDE_CODE_OAUTH_TOKEN` as a long-lived subscription credential to reduce relogin frequency — complementary to this recovery (the prevention track) but a distinct credential-management change, tracked in #1751.
- [EXTERNAL] Re-capturing the consent-page DOM / OAuth client_id if a future claude.ai release changes them — requires a human to observe the new flow on a machine with a live browser.

## Update System

No update-script changes required for the core recovery — it relies on BYOB, which `/update` already installs/registers (`scripts/update/mcp_byob.py`). The new module ships with the repo and is picked up by the worker on restart. The feature doc should note that machines running granite must have BYOB installed + a Chrome bridge running for recovery to function (already true on bridge machines).

## Agent Integration

No agent-facing tool/MCP changes. This is a **bridge/worker-internal** change: the recovery runs inside the granite container's startup loop, not via an agent tool or the bridge's message path. The agent never calls `recover_login` directly — it fires automatically on `LOGIN_PROMPT`. Integration tests live at the container level (mocked BYOB client + fixture PTY frames), not the agent-tool level.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/granite-login-recovery.md` describing the three-flow recovery, the BYOB dependency, the account guard, and the failure→alert fallback.
- [ ] Add an entry to `docs/features/README.md` index table.
- [ ] Cross-link from `docs/features/granite-pty-production.md` (startup phase / login handling).

### External Documentation Site
- [ ] No external docs site for this repo — N/A.

### Inline Documentation
- [ ] Module docstring on `byob_relogin.py` capturing the spike-4 gotchas (IIFE eval, list_tabs polling, hydration retry, trusted click).
- [ ] Docstring on `recover_login` documenting the three flows and the `ReloginOutcome` contract.

## Success Criteria

- [ ] `agent/granite_container/byob_relogin.py` exists with a pure-Python `BYOBClient` (no LLM, no `claude` session) that completes `initialize` + `browser_navigate`/`browser_click`/`browser_list_tabs`.
- [ ] The `LOGIN_PROMPT` branch in `container.py` invokes `recover_login` instead of returning `None`; grep confirms the call site references `byob_relogin`.
- [ ] All three flows are implemented (localhost auto-complete, paste fallback, Google-unlock) with bounded retries and a hard deadline under 600s.
- [ ] Account guard aborts recovery (→ alert) when the consent page's logged-in identity ≠ expected identity.
- [ ] On recovery failure, behavior degrades to the existing `startup_unresolved` ceiling + Telegram alert (integration test with BYOB client mocked to fail).
- [ ] Unit tests cover flow selection, URL extraction, callback parsing, and the failure paths — none complete a real OAuth (mocked BYOB + fixture PTY frames).
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).

## Team Orchestration

### Team Members

- **Builder (byob-driver)**
  - Name: byob-driver-builder
  - Role: Implement `BYOBClient` + `recover_login` flow state machine in `byob_relogin.py`
  - Agent Type: builder
  - Resume: true

- **Builder (container-wiring)**
  - Name: container-wiring-builder
  - Role: Wire `recover_login` into the `LOGIN_PROMPT` branch in `container.py`; add `startup_parser` patterns for menu/auto-open frames if needed
  - Agent Type: builder
  - Resume: true

- **Test engineer (recovery-tests)**
  - Name: recovery-test-engineer
  - Role: Unit + integration tests with mocked BYOB client and fixture PTY frames
  - Agent Type: test-engineer
  - Resume: true

- **Validator (recovery)**
  - Name: recovery-validator
  - Role: Verify success criteria, failure-path degradation, no real-OAuth in tests
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: recovery-documentarian
  - Role: Feature doc + index + cross-links
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Build the BYOB driver + flow state machine
- **Task ID**: build-byob-driver
- **Depends On**: none
- **Validates**: tests/unit/test_granite_byob_relogin.py (create)
- **Informed By**: spike-1 (stdio client works), spike-3 (flows), spike-4 (gotchas)
- **Assigned To**: byob-driver-builder
- **Agent Type**: builder
- **Parallel**: true
- Promote the spike `MCPStdioClient` into a `BYOBClient` (init handshake, navigate/click/list_tabs/eval/read, `finally`-close).
- Implement flow classifier + `recover_login(pty_driver, pty_buffer, deadline) -> ReloginOutcome` for flows 1/2/3.
- Implement URL extraction (de-wrap), callback parse via `list_tabs`, account guard, hydration-aware retrying `browser_click`.

### 2. Wire recovery into the container
- **Task ID**: build-container-wiring
- **Depends On**: build-byob-driver
- **Validates**: tests/unit/test_granite_startup_login_dispatch.py (create)
- **Assigned To**: container-wiring-builder
- **Agent Type**: builder
- **Parallel**: false
- Replace `return None` in the `LOGIN_PROMPT` branch (`container.py:751-758`) with a bounded `recover_login` call; degrade to existing ceiling/alert on failure.
- Add `startup_parser` patterns for the "Select login method" / "Opening browser" frames if detection needs them.
- Emit a `session_events` entry for recovery attempt/outcome.

### 3. Tests (unit + integration, no real OAuth)
- **Task ID**: build-tests
- **Depends On**: build-byob-driver, build-container-wiring
- **Assigned To**: recovery-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- Unit-test `BYOBClient` (mocked subprocess), flow classifier, URL extraction, callback parse, account guard.
- Integration-test the failure→alert degradation (BYOB mocked to fail) and the success dispatch.
- Assert no test path completes a real OAuth or touches the Keychain.

### 4. Validate
- **Task ID**: validate-recovery
- **Depends On**: build-tests
- **Assigned To**: recovery-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify all success criteria; confirm failure-path degradation; grep that the call site references `byob_relogin`.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-recovery
- **Assigned To**: recovery-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/granite-login-recovery.md`; add index entry; cross-link from `granite-pty-production.md`.

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: recovery-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all verification checks; confirm docs exist; generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| New module present | `test -f agent/granite_container/byob_relogin.py && echo ok` | output contains ok |
| Call site wired | `grep -rn "byob_relogin" agent/granite_container/container.py` | exit code 0 |
| Unit tests pass | `pytest tests/unit/test_granite_byob_relogin.py tests/unit/test_granite_startup_login_dispatch.py -q` | exit code 0 |
| No real-OAuth in tests | `! grep -rn "claude.ai/oauth/authorize" tests/` | exit code 0 |
| Lint clean | `python -m ruff check agent/granite_container/` | exit code 0 |
| Format clean | `python -m ruff format --check agent/granite_container/` | exit code 0 |
| Feature doc exists | `test -f docs/features/granite-login-recovery.md && echo ok` | output contains ok |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

1. **Flow-selection ordering & deadline.** Proposed: try flow 1 (auto-opened localhost tab) first, fall back to flow 2 (paste) if no auto-opened tab within ~15s, attempt flow 3 (Google) only if the consent page is logged-out; overall deadline ~90-120s. Does that ordering/budget match your intent, or should paste be the default given the "login opens the wrong browser" case in flow 2?
2. **Account-mismatch policy.** On "Logged in as <wrong-account>", should recovery hard-abort to the alert (proposed, safest), or attempt "Switch account" automation? The latter risks a deeper, less-deterministic flow.
3. **setup-token follow-up.** Do you want #1751 (long-lived `CLAUDE_CODE_OAUTH_TOKEN`) filed now as the durable fix, with this browser-drive recovery as the safety net — or keep browser-drive as the primary mechanism?
