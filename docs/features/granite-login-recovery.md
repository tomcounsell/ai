# Granite `/login` Re-Auth Recovery (BYOB driver)

**Status:** Shipped · **Issue:** [#1750](https://github.com/tomcounsell/ai/issues/1750) · **Plan:** [`docs/plans/granite_byob_login_recovery.md`](../plans/granite_byob_login_recovery.md)

## What it does

When a granite PTY session ([`agent/granite_container/`](../../agent/granite_container/)) hits an
OAuth `/login` re-auth frame mid-startup, the container **autonomously recovers** the login by
driving the already-logged-in real Chrome through the OAuth consent — a fixed, deterministic recipe
with **no LLM in the loop**. On any failure it degrades to the pre-existing `startup_unresolved`
ceiling + Telegram alert, so it is never worse than the prior behavior (a 600s hang then a human
completes OAuth by hand).

This is the **recovery** backstop. The **prevention** track is the long-lived
`CLAUDE_CODE_OAUTH_TOKEN` ([`docs/infra/granite-oauth-token.md`](../infra/granite-oauth-token.md)),
which drives the re-auth frequency toward zero. The two are complementary: token rotation is the
right fix; this recovery covers any machine that hasn't adopted the token and any expiry of the
token itself.

## Why a pure-Python BYOB driver

All `claude` sessions on a machine share `~/.claude` credentials (the macOS Keychain item
`Claude Code-credentials`). If the trigger is a full OAuth expiry, you **cannot** spawn a fresh
`claude` session to drive the browser — it hits the same login wall (the shared-credential
bootstrap deadlock). So recovery speaks to **BYOB**'s MCP server as a pure-Python stdio JSON-RPC
client (`agent/granite_container/byob_relogin.py::BYOBClient`) — no `claude` session, no Claude
OAuth dependency. The BYOB-driven Chrome is already authenticated to claude.ai, so the consent
collapses to a single deterministic **Authorize** click.

## The two recovery flows (plus an alert-only branch)

The classifier inspects `browser_list_tabs` + the login PTY buffer and picks one of three branches:

| # | Flow | Recipe |
|---|------|--------|
| 1 | **Localhost auto-complete (primary)** | claude auto-opened a localhost-callback authorize tab → wait for hydration → account guard → `browser_click` Authorize (retry until the tab leaves the authorize URL) → localhost callback auto-completes → press **Enter** into the PTY. |
| 2 | **Paste fallback** | no auto-opened tab within `AUTO_OPEN_TAB_BUDGET_S`, but a printed `platform.claude.com` paste URL is in the buffer → de-wrap the line-wrapped URL → navigate → account guard → click Authorize → poll `browser_list_tabs` for the `oauth/code/callback` URL → parse `{code}#{state}` → wait for the `Paste code here >` sentinel → write the payload + Enter. |
| — | **Logged-out (alert-only, NOT implemented)** | consent page renders logged-out, or neither an auto-opened tab nor a recoverable paste URL appears within budget → return failure → degrade to alert. There is **no** automated Google-unlock (explicit No-Go: no spike evidence, and it degrades safely). |

## Account guard (fail-closed)

Before clicking Authorize, recovery reads the consent page's "Logged in as &lt;user&gt;" identity and
compares it to the expected identity (`config/identity.json` email). The guard is **fail-closed**:

- A mismatch aborts to the alert path.
- A **missing/empty** page identity is always treated as a mismatch (never a skip — the naive
  `if page_identity and page_identity != expected` would silently skip the guard and authorize any
  account).
- A `None` expected identity also fails closed — recovery never authorizes unless the identity is
  positively confirmed to match.

## Container wiring (non-blocking, idempotent)

`Container._handle_startup` runs every startup cycle and must return fast, so recovery does **not**
block it:

- On the first `LOGIN_PROMPT`, the container records `self._login_pty` (PM **or** Dev — never
  hardcoded to PM) and dispatches `recover_login(login_pty, login_pty_buffer, deadline, ...)` on a
  **daemon `threading.Thread`**, guarded by `self._recovery_launched` set synchronously before the
  spawn so the persisting login frame never spawns a second `tsx byob-mcp.ts` subprocess.
- The thread builds an immutable `ReloginOutcome`, assigns `self._recovery_outcome` as its final
  data statement, then sets `self._recovery_done` (a `threading.Event`). The loop checks
  `_recovery_done.is_set()` **before** dereferencing the outcome — no torn/stale read.
- **Plateau-bail suppression (B1):** a running recovery produces the same signature the plateau
  detector reaps (`response=None`, neither PTY idle), so the early-bail is gated with
  `and not (self._recovery_launched and not self._recovery_done.is_set())`. The 120s recovery
  deadline (`GRANITE_RELOGIN_DEADLINE_S`, `RECOVERY_HARD_DEADLINE_S`) stays strictly under the 600s
  `STARTUP_HARD_CEILING_S`, so the outer ceiling never reaps a pending recovery either.
- The thread's `finally` always closes the `BYOBClient` subprocess, even after an early loop exit,
  so a daemon thread cannot orphan `tsx`.

## Observability

Every recovery attempt records a `session_events` entry with a stable, queryable shape:

```json
{"event": "login_recovery", "outcome": "success|failed", "flow": 1, "reason": "..."}
```

This makes a silently-inert feature observable in production (greppable from the dashboard/logs),
not merely mock-satisfiable in tests.

## Dependencies and prerequisites

- **BYOB** must be installed (`~/.byob/packages/mcp-server/bin/byob-mcp.ts`) and registered in
  `~/.claude.json` (`mcpServers.byob`) with `BYOB_ALLOW_EVAL=1`. `/update` already installs and
  registers BYOB (`scripts/update/mcp_byob.py`) — no new update-system wiring.
- A **Chrome bridge** must be running and logged into claude.ai under the expected identity.
- `pexpect` (already in the venv) for PTY interaction.

If BYOB is missing or the bridge is down, `BYOBClient.start()` returns `False`, recovery returns a
failure outcome, and the session degrades to the existing alert — strictly no worse than today.

## Known limitations / re-capture on breakage

The consent-page selectors are text-anchored (`innerText === 'Authorize'`, "Logged in as &lt;user&gt;")
because the buttons carry no `id`/`data-testid`. If a future claude.ai release changes the consent
DOM or the OAuth client_id, recovery silently fails and falls back to the alert — a human then
re-captures the new flow on a machine with a live browser and updates the selectors centralized in
`byob_relogin.py`.

## Tests

All browser-free and deterministic — no test completes a real OAuth or touches the Keychain (fixture
URLs use `claude.example` / `example.test` hosts):

- [`tests/unit/granite_container/test_granite_byob_relogin.py`](../../tests/unit/granite_container/test_granite_byob_relogin.py)
  — `BYOBClient` transport (injected fake subprocess), `recover_login` flows 1/2, account guard,
  URL extraction, callback parse, never-raises.
- [`tests/unit/granite_container/test_granite_startup_login_dispatch.py`](../../tests/unit/granite_container/test_granite_startup_login_dispatch.py)
  — non-blocking thread dispatch, idempotency (one spawn), B1 plateau suppression, B2 PTY
  attribution, C1 finally-close, failure→`startup_unresolved` degradation, observability event shape.
- [`tests/unit/granite_container/test_startup_parser.py`](../../tests/unit/granite_container/test_startup_parser.py)
  — new `_LOGIN_PATTERNS` ("Select login method", "Browser didn't open", "Opening browser") and the
  C4 priority fixture asserting the re-auth frame classifies `LOGIN_PROMPT`, not `ERROR_MODAL`.

## See also

- [Granite PTY Container: Production Path](granite-pty-production.md) — startup phase / login handling
- [Granite OAuth Token Prevention](../infra/granite-oauth-token.md) — the prevention track (#1751)
