---
status: Planning
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-06-21
tracking: https://github.com/tomcounsell/ai/issues/1750
last_comment_id:
revision_applied: true
---

# Deterministic Granite /login Re-Auth Recovery via Pure-Python BYOB Driver

## Problem

The granite container (`agent/granite_container/`) drives interactive `claude` TUI sessions over PTYs, authenticating via a Claude subscription over OAuth (the PTY driver deliberately blanks `ANTHROPIC_API_KEY` to force the subscription path). When that OAuth token expires or rotates, a PTY paints a login frame mid-run.

**Current behavior:**
- `startup_parser.py:67-73` detects the login frame and emits `StartupEvent.LOGIN_PROMPT`.
- `container.py:751-758` matches the event but its `response` is `None`, so the startup loop **passively waits up to `STARTUP_HARD_CEILING_S` (600s)**, then exits `startup_unresolved` and fires a Telegram alert. A human must manually complete OAuth for the session to proceed — the agent is dead in the water until then.

**Frequency (C3 datum):** Token-expiry-mid-run is **rare but high-impact** — a single occurrence wedges a session for the full 600s ceiling and requires manual human OAuth. No `startup_unresolved`-attributable-to-`LOGIN_PROMPT` events are present in the current `logs/` window (effectively near-zero observed rate), but the cost-per-event (a fully dead session + a human in the loop) justifies a bounded, deterministic safety net rather than leaving the failure mode unhandled. The cheaper prevention track (#1751 `setup-token`) drives the rate further toward zero; this recovery is the unconditional fallback for whenever it still fires (see N1 / Risk 3).

**Desired outcome:**
- Granite **autonomously recovers** the login by driving the already-logged-in real Chrome (via BYOB) through the OAuth consent — a fixed, deterministic recipe with **no LLM intelligence in the loop**. The recipe is credential-independent (pure-Python MCP stdio client, no `claude` session), sidestepping the shared-credential bootstrap deadlock. On recovery failure it degrades to today's alert path (no new hang).
- **Relationship to #1751 (N1):** the long-lived `CLAUDE_CODE_OAUTH_TOKEN` (`setup-token`) is the *preferred* prevention — fewer expiries. This BYOB recovery is the *no-token fallback*: it covers any machine that hasn't adopted #1751 and any expiry of the long-lived token itself. The two are complementary, not redundant.

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
- **Impact on plan**: Two runtime flows are implemented — flow 1 (auto-opened localhost tab, happy path, no paste) and flow 2 (paste fallback). The logged-out case (what an earlier draft called "flow 3") is DEMOTED to a No-Go: the classifier returns an alert outcome with no Google-unlock handler. So the classifier has three branches (flow 1, flow 2, logged-out→alert) but only two implemented flows.

### spike-4: BYOB automation gotchas
- **Assumption**: "Driving the consent page is straightforward."
- **Method**: prototype
- **Finding**: (a) `browser_eval` of a **bare expression returns `null`** — must wrap in an IIFE; (b) `browser_eval` on a tab **after it navigates fails** (CDP context detaches) — poll `browser_list_tabs` for the callback URL instead; (c) clicking Authorize **before the React page hydrates** (~1.5s) is a silent no-op — wait for the button to exist, then retry the click until the tab leaves the authorize URL; (d) the auto-opened claude tab + a self-opened tab = two confusing tabs, so operate on claude's tab where possible; (e) possible trusted-event/mouse-movement gating — prefer `browser_click` (CDP trusted dispatch) over `eval`'d `.click()`.
- **Confidence**: high
- **Impact on plan**: Encodes the driver's wait/retry/poll discipline; dictates `browser_click` over eval-click.

## Data Flow

1. **Entry point**: A granite PTY (PM or Dev) paints an OAuth login frame mid-startup; `pty_driver` reads the buffer.
2. **Detection** (`startup_parser.parse_startup_frame`): regex matches → `StartupEvent.LOGIN_PROMPT`.
3. **Dispatch** (`container._handle_startup` → `run()` startup loop): the `LOGIN_PROMPT` branch (today `return None`) records `self._login_pty` (the PTY that matched — PM or Dev) and dispatches `byob_relogin.recover_login(login_pty, login_pty_buffer, deadline, ...)` on a `threading.Thread` (once, behind `_recovery_launched`). `_handle_startup` returns immediately; the loop applies the outcome once `_recovery_done` is set.
4. **Recovery** (`byob_relogin`): spawns a pure-Python BYOB MCP stdio client → classifies flow (auto-opened localhost tab? platform paste URL? logged-out browser?) → drives Chrome (navigate/click/poll) → for paste flow, writes `{code}#{state}` into the PTY; for localhost flow, presses Enter; closes the MCP client.
5. **Settle**: the PTY leaves the login frame; the startup loop detects idle → proceeds to steady-state.
6. **Output / fallback**: on recovery success, the session runs normally. On failure (timeout/retries exhausted), `recover_login` returns a failure sentinel and the loop falls through to the existing `startup_unresolved` ceiling + Telegram alert.

## Architectural Impact

- **New dependencies**: `pexpect` (already in the venv, used by `pty_driver`) for PTY interaction; no new third-party libs — the BYOB MCP client is hand-rolled stdio JSON-RPC over `subprocess`. Hard runtime dependency on the BYOB install (`~/.byob`) and a running Chrome bridge.
- **Interface changes**: New module `agent/granite_container/byob_relogin.py` exposing a `recover_login(login_pty, login_pty_buffer, deadline, ...)` entry + a small `BYOBClient` class and a `ReloginOutcome` dataclass. `container.py` gains three instance fields initialized in `__init__` (`_recovery_launched: bool = False`, `_recovery_done: threading.Event`, `_recovery_outcome: ReloginOutcome | None = None`) and a `_login_pty` capture; the `LOGIN_PROMPT` branch + the plateau-bail guard are the only logic changes.
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
- **Flow classifier** (three branches; two implemented + one alert-only): Inspects `browser_list_tabs` + the login PTY buffer. (1) auto-opened localhost authorize tab present in the BYOB-controlled Chrome → flow 1 (primary). (2) no auto-opened tab within the ~15s budget but a printed `platform.claude.com` paste URL in the PTY buffer → flow 2 (paste fallback). (3) logged-out consent page → return the failure sentinel (degrade to alert; no handler — see No-Gos). Per C2: flow 1 is the proven primary; flow 2 covers the case where claude's auto-open lands in a browser BYOB does not control — if neither an auto-opened authorize tab nor a paste URL is recoverable within budget, the classifier returns failure rather than spinning.
- **`recover_login(pty_driver, pty_buffer, deadline)` routine**: Orchestrates the recipe per flow against the PTY + `BYOBClient`, bounded by a hard deadline and per-flow retries; returns a `ReloginOutcome` (success / failed-degrade).
- **Container wiring (non-blocking)**: Replaces the passive `return None` in the `LOGIN_PROMPT` branch with a **thread-dispatched, idempotent** `recover_login` attempt. `_handle_startup` MUST stay fast and non-blocking — it is called every startup cycle and a 120s blocking call would starve the *other* PTY (no reads/idle detection on the non-login PTY). On first `LOGIN_PROMPT` the branch sets `self._recovery_launched = True` synchronously, spawns `recover_login` on a `threading.Thread` (writing `self._recovery_outcome` on completion), and returns `None`. On later cycles it returns `None` while pending; on success it writes the PTY response (Enter / pasted code). On failure or timeout it leaves the loop to hit the existing ceiling/alert.

### Flow

**PTY shows login frame** → `LOGIN_PROMPT` detected → recovery thread dispatched (once, behind `_recovery_launched`) → `recover_login` classifies flow →
- **Flow 1 (localhost auto-complete):** find claude's auto-opened authorize tab → wait for hydration → `browser_click` Authorize (retry until tab leaves authorize URL) → localhost callback auto-completes → press **Enter** in PTY → **logged in**.
- **Flow 2 (paste fallback):** reconstruct the wrapped authorize URL from the PTY buffer → `browser_navigate` → click Authorize → poll `browser_list_tabs` for the `oauth/code/callback` URL → parse `{code}#{state}` → write into PTY at `Paste code here >` → Enter → **logged in**.
- **Logged-out browser (formerly flow 3, now out of scope):** detected and short-circuited to failure → container falls back to `startup_unresolved` + Telegram alert (human completes OAuth). No automated Google-unlock — see No-Gos.
- **Any flow fails within deadline** → return failure → container falls back to `startup_unresolved` + Telegram alert.

### Technical Approach

- **Module**: `agent/granite_container/byob_relogin.py`. Promote the validated spike's `MCPStdioClient` into `BYOBClient`. Keep it synchronous (the container startup loop is synchronous, driven via `asyncio.to_thread` upstream) to match `pty_driver`'s pexpect style.
- **URL extraction (flow 2)**: From the PTY buffer, slice from the first `https://claude` to the `Paste\s*code` sentinel and strip all whitespace (de-wraps the terminal line-wrapping). Validated in spike-3.
- **Callback parse**: `urllib.parse` the callback URL's `code`/`state` from `browser_list_tabs` (NOT post-nav `eval` — spike-4b). Payload = `f"{code}#{state}"`.
- **Click discipline**: prefer `browser_click` (trusted CDP dispatch) targeting the Authorize button; fall back to IIFE-wrapped `eval` `.click()` only if needed. Poll for redirect; retry up to N (spike-4c/e).
- **Account guard**: before clicking Authorize, read the consent page's "Logged in as <user>" and compare against an expected-identity config (e.g. `config/identity.json` email); abort recovery (degrade to alert) on mismatch rather than authorize the wrong account.
- **Bounding**: a single overall deadline (120s, well under the existing 600s ceiling) plus per-step timeouts; always close the `BYOBClient` subprocess in `finally`.
- **Non-blocking dispatch + idempotency guard** (critique blocker + concern): `_handle_startup` runs every startup cycle and must return fast. The `LOGIN_PROMPT` branch sets `self._recovery_launched = True` **synchronously before** spawning the recovery `threading.Thread`, so re-entry on the next cycle (the login frame persists across many cycles) never spawns a second `tsx byob-mcp.ts` subprocess. The fast path is: `if not self._recovery_launched: launch; elif self._recovery_done.is_set(): apply` — O(1), race-free, exactly one recovery attempt per session. The recovery thread owns the 120s `recover_login` and the `BYOBClient` lifecycle; the loop keeps polling both PTYs meanwhile.
- **B1 — suppress the plateau early-bail while recovery is in flight** (critique blocker): the startup loop reaps a session as `startup_unresolved` after `STARTUP_PLATEAU_CYCLES` (=10, ~30s at the 3s cycle) of `response=None` + neither-PTY-idle (`_silent_start`, `container.py:1032`). A running recovery produces exactly that signature, so the plateau detector would kill it ~90s before the 120s recovery deadline. The bail MUST be gated: `if _plateau_count >= STARTUP_PLATEAU_CYCLES and _silent_start and not (self._recovery_launched and not self._recovery_done.is_set()):`. The 120s recovery deadline stays strictly under `STARTUP_HARD_CEILING_S` (600s) so the outer ceiling never reaps a pending recovery either.
- **B2 — capture the login PTY; never hardcode PM** (critique blocker): `_handle_startup` inspects both `result_pm`/`result_dev` but discards which PTY matched and the loop hardcodes `self._pm_pty.write(response)` (`container.py:1073`, self-described as "a heuristic"). If the login frame is on the **Dev** PTY, recovery would press Enter / paste the OAuth code into the wrong PTY (silent no-op + possible state corruption). When `chosen == ("login", r)`, record `self._login_pty = self._pm_pty if r is result_pm else self._dev_pty` and pass `self._login_pty` (and ITS buffer — the URL/paste sentinel slice must come from the same PTY) into `recover_login` as the write target. Non-login startup events (trust/update) keep their existing PM-write heuristic; only the login path needs accurate attribution.
- **C1 — thread-safety + no orphaned subprocess** (critique concern): build the complete immutable `ReloginOutcome` locally inside the thread, assign `self._recovery_outcome = outcome` as the final statement, then `self._recovery_done.set()` (a `threading.Event` created synchronously alongside `self._recovery_launched = True`). The loop checks `self._recovery_done.is_set()` before dereferencing `_recovery_outcome` — no torn/stale read. The thread's `finally` MUST close the `BYOBClient` (kill the `tsx byob-mcp.ts` subprocess) even if the loop already returned `startup_unresolved`, so a daemon thread cannot orphan the subprocess.
- **Trigger surface + detection patterns (non-conditional)**: wire into the startup-phase `LOGIN_PROMPT` branch. The existing `_LOGIN_PATTERNS` only match `"Sign in to continue"` / `"paste.*url.*continue"`, which do NOT match the real claude 2.1.185 re-auth frame (theme picker → **"Select login method"** menu → auto-open / "Browser didn't open" frame). Task 2 MUST add (not "if needed") case-insensitive patterns for `"Select login method"` and `"Browser didn't open"` / `"Opening browser"` to `startup_parser._LOGIN_PATTERNS`, with a fixture frame in the dispatch test asserting `LOGIN_PROMPT` detection on the menu frame. Without these the recovery is inert in the real scenario.
- **C4 — error-shadows-login precedence**: the parser prioritizes `_ERROR_PATTERNS` (incl. `"Login failed"` / `"Authentication failed"`) ABOVE `_LOGIN_PATTERNS` (`test_startup_parser.py` priority tests). If a real re-auth frame also carries an error-like substring, it would classify as `ERROR_MODAL` and recovery would never dispatch — silently regressing to today's alert. Task 2 MUST add a priority fixture feeding a captured re-auth frame (containing "Select login method") and assert `result.event == StartupEvent.LOGIN_PROMPT`; if a real frame legitimately carries both, codify the desired winner in the pattern/group ordering rather than leaving it to chance.

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

### Concurrency / Dispatch (critique B1/B2/C1)
- [ ] **B1**: while `_recovery_launched and not _recovery_done.is_set()`, the plateau detector must NOT bail — test drives ≥`STARTUP_PLATEAU_CYCLES` no-progress cycles with recovery "in flight" and asserts the loop keeps cycling (no premature `startup_unresolved`).
- [ ] **B2**: a `LOGIN_PROMPT` on the **Dev** PTY routes the recovery write to `self._dev_pty`, not `self._pm_pty` — test asserts `_login_pty` attribution for both PM-side and Dev-side login frames.
- [ ] **C1**: the recovery thread closes the `BYOBClient` subprocess in `finally` even when the loop already returned — test asserts no orphaned subprocess after an early loop exit (mocked client records `close()` called).

## Test Impact

- [ ] `tests/unit/granite_container/test_startup_parser.py::test_login_prompt_response_is_none` — KEEP (stays green): it asserts the *parser-layer* contract that the `LOGIN_PROMPT` event carries `response=None`. That contract is unchanged — recovery is triggered by the *event*, not by a parser response string. No edit needed; cited directly here rather than discovered by a speculative grep.
- [ ] `tests/unit/granite_container/test_startup_parser.py` — UPDATE: add fixtures + assertions for the new `_LOGIN_PATTERNS` entries (`"Select login method"`, `"Browser didn't open"` / `"Opening browser"`) → `LOGIN_PROMPT`.
- [ ] `tests/unit/granite_container/test_granite_byob_relogin.py` — ADD: unit tests for `BYOBClient` (mocked subprocess) and the flow classifier / `recover_login` state machine (mocked BYOB client + fixture PTY frames).
- [ ] `tests/unit/granite_container/test_granite_startup_login_dispatch.py` — ADD: container-level tests for the non-blocking thread dispatch, the `_recovery_launched` idempotency guard (no double-spawn), **B1** plateau-suppression-while-in-flight, **B2** PTY attribution (PM vs Dev login frame), **C1** finally-close of the subprocess, and the failure→`startup_unresolved`+alert degradation (BYOB mocked to fail).
- [ ] `tests/unit/granite_container/test_startup_parser.py` — UPDATE: **C4** priority fixture asserting a re-auth frame (with "Select login method") classifies `LOGIN_PROMPT`, not `ERROR_MODAL`.

No existing tests break — the change is additive (replaces an inert `return None` with a thread-dispatched call that defaults to the same fallback). New granite tests live under `tests/unit/granite_container/` (the established directory), not the `tests/unit/` root.

## Rabbit Holes

- **Don't build a generic browser-OAuth framework.** Scope is exactly the Claude Code consent recipe (one client_id, known DOM). Generality is wasted time.
- **Don't try to make `browser_eval` work post-navigation.** It detaches; poll `browser_list_tabs`. Spike-4b already settled this — don't re-litigate.
- **Don't chase the localhost auto-complete as the *only* path.** The paste fallback (flow 2) is required — a localhost-only implementation strands real sessions when the auto-open lands in the wrong browser. (The Google-unlock path was demoted to a No-Go after critique: no spike evidence, and it degrades safely to the alert.)
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

- [DEMOTED — was flow 3] Automated **Google-unlock** of a logged-out browser session ("Continue with Google" → fall through). No spike evidence exists for it (spikes 1-4 cover the MCP client, consent DOM, and gotchas only), it adds new DOM targets / a second click-retry round / a new failure mode (Google session also expired), and it contradicts the "no generic browser-OAuth framework" rabbit hole. When the consent page renders logged-out, recovery short-circuits to the existing `startup_unresolved` alert — a human completes OAuth. Re-scope as a follow-up only if logged-out browsers prove a real, recurring trigger in production.
- [SEPARATE-SLUG #1751] Adopting `claude setup-token` / `CLAUDE_CODE_OAUTH_TOKEN` as a long-lived subscription credential to reduce relogin frequency — complementary to this recovery (the prevention track) but a distinct credential-management change, tracked in #1751. **Sequencing rationale:** recovery ships first as the *unconditional* safety net — `setup-token` needs a per-machine human OAuth step to mint, has its own ~1yr rotation, and may not be adopted on every machine; the browser-drive recovery covers expiry regardless of whether #1751 lands. The two are independent, not competing.
- [EXTERNAL] Re-capturing the consent-page DOM / OAuth client_id if a future claude.ai release changes them — requires a human to observe the new flow on a machine with a live browser.

## Update System

No update-script changes required for the core recovery — it relies on BYOB, which `/update` already installs/registers (`scripts/update/mcp_byob.py`). The new module ships with the repo and is picked up by the worker on restart. The feature doc should note that machines running granite must have BYOB installed + a Chrome bridge running for recovery to function (already true on bridge machines).

## Agent Integration

No agent-facing tool/MCP changes. This is a **bridge/worker-internal** change: the recovery runs inside the granite container's startup loop, not via an agent tool or the bridge's message path. The agent never calls `recover_login` directly — it fires automatically on `LOGIN_PROMPT`. Integration tests live at the container level (mocked BYOB client + fixture PTY frames), not the agent-tool level.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/granite-login-recovery.md` describing the two-flow recovery (localhost auto-complete + paste fallback), the logged-out→alert degradation, the BYOB dependency, the account guard, and the failure→alert fallback.
- [ ] Add an entry to `docs/features/README.md` index table.
- [ ] Cross-link from `docs/features/granite-pty-production.md` (startup phase / login handling).

### External Documentation Site
- [ ] No external docs site for this repo — N/A.

### Inline Documentation
- [ ] Module docstring on `byob_relogin.py` capturing the spike-4 gotchas (IIFE eval, list_tabs polling, hydration retry, trusted click).
- [ ] Docstring on `recover_login` documenting the two in-scope flows (localhost auto-complete, paste fallback), the logged-out degradation, and the `ReloginOutcome` contract.

## Success Criteria

- [ ] `agent/granite_container/byob_relogin.py` exists with a pure-Python `BYOBClient` (no LLM, no `claude` session) that completes `initialize` + `browser_navigate`/`browser_click`/`browser_list_tabs`.
- [ ] The `LOGIN_PROMPT` branch in `container.py` invokes `recover_login` (on a thread, behind `_recovery_launched`) instead of returning `None`; grep confirms the call site references `byob_relogin`.
- [ ] Recovery dispatch is **non-blocking and idempotent**: `_handle_startup` returns fast every cycle, and the login frame persisting across cycles spawns exactly one `BYOBClient` subprocess (test asserts no double-spawn).
- [ ] Flows 1 and 2 are implemented (localhost auto-complete, paste fallback) with bounded retries and a 120s hard deadline (< 600s ceiling). A logged-out browser degrades to the alert (no automated Google-unlock — see No-Gos).
- [ ] `startup_parser._LOGIN_PATTERNS` matches the real re-auth frame ("Select login method" / "Browser didn't open") so the `LOGIN_PROMPT` branch actually fires in production (test asserts detection on the menu frame).
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
- **Validates**: tests/unit/granite_container/test_granite_byob_relogin.py (create)
- **Informed By**: spike-1 (stdio client works), spike-3 (flows), spike-4 (gotchas)
- **Assigned To**: byob-driver-builder
- **Agent Type**: builder
- **Parallel**: true
- Promote the spike `MCPStdioClient` into a `BYOBClient` (init handshake, navigate/click/list_tabs/eval/read, `finally`-close).
- Implement flow classifier + `recover_login(pty_driver, pty_buffer, deadline) -> ReloginOutcome` for **flows 1 and 2 only** (logged-out browser → short-circuit to failure; no Google-unlock — see No-Gos).
- Implement URL extraction (de-wrap), callback parse via `list_tabs`, account guard, hydration-aware retrying `browser_click`. 120s overall deadline + per-step timeouts.

### 2. Wire recovery into the container
- **Task ID**: build-container-wiring
- **Depends On**: build-byob-driver
- **Validates**: tests/unit/granite_container/test_granite_startup_login_dispatch.py (create)
- **Assigned To**: container-wiring-builder
- **Agent Type**: builder
- **Parallel**: false
- Replace `return None` in the `LOGIN_PROMPT` branch (`container.py` `_handle_startup`, ~line 752; verify against current source) with a **non-blocking, idempotent thread dispatch** of `recover_login`: set `self._recovery_launched` + create `self._recovery_done` (Event) synchronously before spawning the `threading.Thread`; the thread publishes an immutable `self._recovery_outcome` then sets `_recovery_done`; the loop applies the PTY response only after `_recovery_done.is_set()`. Never block the per-cycle dispatch. Degrade to the existing ceiling/alert on failure or timeout.
- **B1**: gate the plateau early-bail (`container.py:1032`) with `and not (self._recovery_launched and not self._recovery_done.is_set())` so a running recovery is not reaped as `startup_unresolved`.
- **B2**: capture `self._login_pty` at the `("login", r)` match site (`r is result_pm` → PM, else Dev) and pass it + its buffer into `recover_login`; do NOT hardcode `self._pm_pty` for the login write.
- **C1**: thread's `finally` closes the `BYOBClient` subprocess even after an early loop return.
- Add `startup_parser._LOGIN_PATTERNS` entries for the real re-auth frame (`"Select login method"`, `"Browser didn't open"` / `"Opening browser"`) — **non-conditional**, with a fixture-frame detection test AND a **C4** priority fixture asserting the re-auth frame classifies `LOGIN_PROMPT`, not `ERROR_MODAL`.
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
| Unit tests pass | `pytest tests/unit/granite_container/test_granite_byob_relogin.py tests/unit/granite_container/test_granite_startup_login_dispatch.py -q` | exit code 0 |
| No real-OAuth in tests | `! grep -rn "claude.ai/oauth/authorize" tests/` | exit code 0 |
| Lint clean | `python -m ruff check agent/granite_container/` | exit code 0 |
| Format clean | `python -m ruff format --check agent/granite_container/` | exit code 0 |
| Feature doc exists | `test -f docs/features/granite-login-recovery.md && echo ok` | output contains ok |

## Critique Results

**Verdict:** NEEDS REVISION (2 blockers) — war room FULL depth (Risk & Robustness, Scope & Value, History & Consistency), critiqued 2026-06-23.

### Blockers (must resolve before build)

**B1 — Plateau early-bail kills recovery mid-flight.** While the threaded recovery runs (up to 120s), the `LOGIN_PROMPT` branch returns `response=None` and the persisting login frame keeps both PTYs non-idle, so `_silent_start` is true every cycle. With `STARTUP_PLATEAU_CYCLES` (container.py:114) the startup loop bails early to `startup_unresolved` (container.py:1032) long before the 120s recovery thread finishes — the recovery is reaped mid-flight and the "on success writes PTY response" path never fires.
- *Implementation Note:* gate the bail — `if _plateau_count >= STARTUP_PLATEAU_CYCLES and _silent_start and not (self._recovery_launched and self._recovery_outcome is None):`. Ensure the 120s recovery deadline stays strictly under `STARTUP_HARD_CEILING_S` (600s) so the outer ceiling never reaps a pending recovery either.

**B2 — PTY attribution lost; recovery writes to the wrong PTY.** `_handle_startup` (container.py:719) inspects both `result_pm` and `result_dev` but discards which PTY matched and returns only `r.response`; the loop hardcodes `self._pm_pty.write(response)` (container.py:1073, self-described as "a heuristic"). If the login frame is on the Dev PTY, `recover_login` presses Enter / pastes the OAuth code into the wrong PTY — silent no-op recovery (and possible corruption of the other PTY's state).
- *Implementation Note:* when `chosen == ("login", r)`, record the source — `self._login_pty = self._pm_pty if r is result_pm else self._dev_pty` — and pass `self._login_pty` (and its buffer) into `recover_login` as the write target, not `self._pm_pty`. The URL/paste sentinel must be sliced from that same PTY's buffer.

### Concerns (fold Implementation Notes into the plan during revision)

**C1 — Thread-safety of `self._recovery_outcome` (Risk).** Background thread writes it; loop reads it every cycle with no described barrier — a torn/stale read could apply a half-populated `ReloginOutcome` and write garbage into the PTY.
- *Implementation Note:* build the complete immutable `ReloginOutcome` locally in the thread, assign `self._recovery_outcome = outcome` as the final statement, then `self._recovery_done.set()` (a `threading.Event` created synchronously alongside `self._recovery_launched=True`). Loop checks `if self._recovery_done.is_set():` before dereferencing. Never mutate the outcome after publishing. Also: ensure `recover_login`'s `finally` BYOBClient close runs even if the loop has already returned `startup_unresolved`, or the daemon thread orphans the `tsx byob-mcp.ts` subprocess.

**C2 — Flow 2 (paste fallback) may be dead weight (Scope).** Flow 2's justification rests on the unproven claim that the localhost auto-open "lands in the wrong browser." No spike evidences this; spike-3 shows claude auto-opens the localhost tab in the shared Chrome. The second flow doubles the state machine + test surface (URL de-wrap, callback poll, paste-back, Race 3) on an unproven premise.
- *Implementation Note:* in the flow classifier, if no auto-opened authorize tab appears in the BYOB-controlled Chrome within the ~15s budget, return the failure sentinel (degrade to alert) rather than entering a paste path. Consider deferring flow-2 code (tasks 1/3) until a production trace shows the auto-open targeting a browser BYOB does not control — the same evidentiary bar that demoted Google-unlock to a No-Go.

**C3 — Problem frequency unmeasured (Scope).** The plan never quantifies how often token-expiry-mid-run fires, and Research concedes #1751 (setup-token) "dramatically reduces how often recovery fires." A Medium-appetite multi-flow recovery may be solving a rare event already being driven toward zero by the cheaper prevention track.
- *Implementation Note:* add a one-line frequency datum to the Problem section sourced from existing logs (count of `startup_unresolved` exits attributable to `LOGIN_PROMPT` over the last N weeks). If ~0, consider folding into #1751; if non-trivial, keep the safety net (reinforces the flow-1-only minimal version per C2).

**C4 — New `_LOGIN_PATTERNS` vs error-shadows-login precedence (History).** The new patterns ("Select login method", "Browser didn't open") don't address the parser's documented error-shadows-login priority (`test_startup_parser.py:122-128`; `_ERROR_PATTERNS` includes "Login failed"/"Authentication failed"). If a new login pattern is shadowed by an ERROR_MODAL pattern on a real re-auth frame, recovery silently never dispatches and regresses to today's alert.
- *Implementation Note:* Task 2 adds a priority fixture feeding a captured 2.1.185 re-auth frame (containing "Select login method") and asserts `result.event == StartupEvent.LOGIN_PROMPT`, mirroring the existing `TestParserPriority` pattern; if the frame also carries an error-like substring, codify the desired winner in `_PATTERN_GROUPS` ordering.

**C5 — spike-3 "Three runtime flows" contradicts the two-flow Solution (History).** spike-3's retained text says "Three runtime flows" and the narrative still references a "former flow 3," while the Solution implements only Flow 1 + Flow 2 and demotes flow 3 to alert-only. A builder reading spike-3 may implement the forbidden flow-3 handler.
- *Implementation Note:* annotate the spike-3 entry — "(flow 3 = logged-out → DEMOTED to No-Go, classifier returns alert outcome, no handler)" — so the classifier's three branches are (Flow 1 localhost, Flow 2 paste, logged-out→alert) and "three branches" / "two implemented flows" are both literally true.

### Nits

- **N1 (History):** Note the eventual relationship to #1751 in-plan (token path preferred, BYOB recovery is the no-token fallback) so a future reader does not treat them as redundant. Greenfield claim and #1751 sequencing are otherwise sound.

### Structural checks

| Check | Status | Detail |
|-------|--------|--------|
| Required sections | PASS | Documentation, Update System, Agent Integration, Test Impact all present and non-empty |
| Task numbering | PASS | Tasks 1-6 sequential, no gaps |
| Dependencies valid | PASS | build-byob-driver → build-container-wiring → build-tests → validate-recovery → document-feature → validate-all; no cycles |
| File paths exist | PASS | All referenced source/test/doc paths exist; `byob_relogin.py` + 2 new test files correctly absent (to be created) |
| Prerequisites met | PASS | BYOB ts present, byob registered in ~/.claude.json, pexpect importable (this machine) |
| Cross-references | PASS | Success criteria map to tasks; No-Gos (Google-unlock, #1751) not present as planned work |

---

## Open Questions

_All three resolved at plan finalization (2026-06-23). Recorded here for traceability; the decisions are folded into the Solution / Technical Approach / Risks sections above._

1. **Flow-selection ordering & deadline.** RESOLVED: try flow 1 (auto-opened localhost tab) first; fall back to flow 2 (paste) if no auto-opened authorize tab appears within ~15s. (Former flow 3 / Google-unlock was demoted to a No-Go in the critique revision — a logged-out browser degrades to the alert.) Overall deadline 120s (well under the 600s ceiling), per-step timeouts inside that budget.
2. **Account-mismatch policy.** RESOLVED: hard-abort to the existing alert path on identity mismatch (safest, fully deterministic). No "Switch account" automation in scope — that is a deeper, less-deterministic flow and would risk authorizing under the wrong identity. Encoded as the account guard in Technical Approach + Risk 1.
3. **setup-token follow-up.** RESOLVED: this browser-drive recovery is the safety net; the long-lived `CLAUDE_CODE_OAUTH_TOKEN` prevention track is filed separately as #1751 (see No-Gos). This issue ships the recovery; #1751 reduces how often it fires.
