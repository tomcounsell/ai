---
status: Planning
type: feature
appetite: Small
owner: Valor Engels
created: 2026-06-22
tracking: https://github.com/tomcounsell/ai/issues/1751
last_comment_id:
---

# Granite Long-Lived OAuth Token (setup-token Prevention Track)

## Problem

Granite's interactive Claude Code PTYs (PM + Dev TUIs) authenticate via the Claude
**subscription OAuth** path. The PTY driver deliberately blanks `ANTHROPIC_API_KEY`,
`ANTHROPIC_BASE_URL`, and `ANTHROPIC_AUTH_TOKEN` so the TUI uses the Max-subscription
OAuth credential rather than an API key or the ollama substrate
(`agent/granite_container/pty_driver.py:291-295`). That OAuth *access* token is
short-lived. When it lapses mid-session the TUI paints a `/login` prompt, which is the
trigger that issue #1750's browser-drive recovery exists to clear.

**Current behavior:**
The subscription OAuth access token expires frequently, so granite PTYs repaint
`/login` prompts on a recurring basis. Recovery (#1750) is a safety net that drives the
real browser through the OAuth consent flow — useful, but it is a heavyweight,
browser-dependent interruption we do not want firing routinely.

**Desired outcome:**
Re-auth becomes a roughly once-a-year event instead of a frequent interruption. A
long-lived (~1-year) subscription-backed `CLAUDE_CODE_OAUTH_TOKEN`, minted via
`claude setup-token`, is present in the granite PTY child environment so the TUI
authenticates without prompting. #1750's recovery remains the fallback for the rare case
where even the long-lived token lapses or is missing.

## Freshness Check

**Baseline commit:** `44bfcb357f1ce3bae888d566a3e94a233d45855c`
**Issue filed at:** 2026-06-21T08:09:13Z (≈1 day before plan time)
**Disposition:** Unchanged

**File:line references re-verified:**
- `agent/granite_container/pty_driver.py:291-295` — issue claimed "the PTY driver blanks
  `ANTHROPIC_API_KEY` to force the subscription path." Confirmed still present and exact:
  `_build_env()` copies `os.environ` then sets `ANTHROPIC_API_KEY=""`,
  `ANTHROPIC_BASE_URL=""`, `ANTHROPIC_AUTH_TOKEN=""`. It does **not** touch
  `CLAUDE_CODE_OAUTH_TOKEN`, so a token present in the worker's environment already
  survives into the PTY child today — the injection point is clean.
- `agent/granite_container/pty_driver.py:386-388` — `_build_env()` is called in
  `spawn()`, then `_extra_env` (per-session overlay) is merged on top. Confirmed.

**Cited sibling issues/PRs re-checked:**
- #1750 — still OPEN, has a plan (`docs/plans/granite_byob_login_recovery.md`), labeled
  `hold`. Its plan explicitly lists this work as `[SEPARATE-SLUG #1751]` in its No-Gos,
  confirming the prevention/recovery split. No code from #1750 has merged yet, so this
  plan does not depend on #1750 having landed.

**Commits on main since issue was filed (touching referenced files):**
- None touching `agent/granite_container/pty_driver.py`. The only commit since filing is
  `44bfcb35` (the #1750 plan doc) — irrelevant to the PTY env code.

**Active plans in `docs/plans/` overlapping this area:**
- `granite_byob_login_recovery.md` (#1750) — the complementary **recovery** track. Not an
  overlap to merge: #1750 clears a login prompt after it appears; this plan prevents the
  prompt from appearing. They touch different code (`container.py` login-dispatch vs.
  `pty_driver.py` env assembly). Coordinated, not conflicting.

**Notes:** No drift. The injection point (`_build_env`) is unchanged and the env var is
not blanked, exactly as the issue assumed.

## Prior Art

- **#1546 (closed 2026-06-05)**: "PoC: granite operator drives a REAL interactive Claude
  Code session via PTY (no `claude -p`)" — established the PTY-driven TUI substrate and
  the OAuth-subscription auth model this plan extends. Confirms the substrate is the
  Claude subscription (opus/sonnet), not ollama.
- **PR #1612**: Introduced the three-var blanking in `_build_env()` after a live failure
  where ollama env leaked into the PTY. Directly informs the constraint: we must add
  `CLAUDE_CODE_OAUTH_TOKEN` *without* re-introducing API-key or base-URL leakage.
- **#1750 (open, plan + hold)**: The recovery track. This plan's prevention reduces how
  often #1750 fires. No prior *failed* fix for this exact problem — this is the first
  attempt at the prevention track, so there is no "Why Previous Fixes Failed" section.

No prior issues attempted the long-lived-token approach; greenfield within this repo.

## Research

**Queries used:**
- `claude setup-token CLAUDE_CODE_OAUTH_TOKEN long-lived expiry authentication`

**Key findings:**
- `claude setup-token` mints a **~1-year** OAuth token, format `sk-ant-oat01-...`, tied to
  the Claude subscription plan (Pro/Max/Team/Enterprise). Usage counts against the plan's
  limits — **no separate API invoice**. Source:
  https://code.claude.com/docs/en/authentication — this is why the token is the right
  prevention lever: it preserves the subscription billing model the blanking already
  enforces, it just makes the credential long-lived.
- The token is **displayed once** at mint time and cannot be retrieved again; regenerate
  before expiry by running `claude setup-token` again **on a browser-capable machine**.
  Source: same doc + community confirmations. This shapes the rotation story: minting is a
  human, browser-gated, one-shot action → it belongs in No-Gos as `[EXTERNAL]`, and we
  need an expiry-detection mechanism so a lapse is caught *before* it strands a session.
- Claude Code reads the token from the `CLAUDE_CODE_OAUTH_TOKEN` env var. When set, it is
  used for auth and, being a subscription credential, does **not** collide with the
  blanked-API-key OAuth path — it *is* the subscription path, pre-supplied.

## Data Flow

1. **Mint (human, one-shot, ~yearly)**: Operator runs `claude setup-token` on a
   browser-capable machine, copies the `sk-ant-oat01-...` token into the iCloud vault
   `~/Desktop/Valor/.env` as `CLAUDE_CODE_OAUTH_TOKEN=...`.
2. **Propagation**: Repo `.env` is a symlink → vault `.env`
   (`scripts/update/env_sync.py:62`, created at run.py Step 1.6). The new secret is
   visible to every machine that syncs the vault — no per-machine edit, no `/update`
   script change.
3. **Load**: `config/settings.py` (pydantic `BaseSettings`, `env_file=".env"`) reads the
   token into a new `settings.api.claude_code_oauth_token` field at process startup.
4. **Worker → PTY env**: The granite PTY driver's `_build_env()`
   (`pty_driver.py:272-295`) copies `os.environ`, blanks the three `ANTHROPIC_*` vars, and
   **explicitly sets `CLAUDE_CODE_OAUTH_TOKEN`** from the settings value (falling back to
   any inherited env value). The per-session `_extra_env` overlay still merges on top.
5. **Spawn**: `pexpect.spawn("claude", ..., env=env)` (`pty_driver.py:390-399`) launches
   the TUI with the long-lived token present → the TUI authenticates without painting a
   `/login` prompt.
6. **Fallback**: If the token is absent or expired, the TUI still paints `/login`, which
   #1750's `startup_parser` → `LOGIN_PROMPT` → `recover_login()` path handles. Prevention
   degrades cleanly to recovery.

## Appetite

**Size:** Small

**Team:** Solo dev, PM check-in for the Open Questions

**Interactions:**
- PM check-ins: 1-2 (confirm token-storage location and expiry-detection cadence)
- Review rounds: 1

This is a narrow, additive credential-plumbing change: one settings field, one explicit
env-set in an existing function, an expiry-detection check, plus docs and tests. The
bottleneck is the operator decisions in Open Questions, not coding time.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `CLAUDE_CODE_OAUTH_TOKEN` minted and in vault | `python -c "from dotenv import dotenv_values; t=dotenv_values('.env').get('CLAUDE_CODE_OAUTH_TOKEN'); assert t and t.startswith('sk-ant-oat01-'), 'missing or malformed token'"` | Long-lived subscription token present for granite PTYs |

Run all checks: `python scripts/check_prerequisites.py docs/plans/granite_oauth_token_prevention.md`

Note: the prerequisite check is the gate, not a build blocker — code paths must tolerate
the token being absent (graceful degradation to #1750 recovery). The check exists so the
operator knows the token is wired before relying on prevention.

## Solution

### Key Elements

- **Settings field**: A new `claude_code_oauth_token: str | None` on `APISettings`
  (`config/settings.py`), read from the vault `.env` like every other secret. Optional —
  default `None` so the system runs without it.
- **PTY env injection**: `_build_env()` in `pty_driver.py` explicitly sets
  `CLAUDE_CODE_OAUTH_TOKEN` from the settings value (or inherited env) **after** blanking
  the `ANTHROPIC_*` vars, so the long-lived subscription token is present in the TUI child
  without re-introducing API-key/base-URL leakage.
- **Expiry / presence detection**: A lightweight check (surfaced via the existing health
  surface — `python -m tools.doctor` and/or the dashboard) that reports whether the token
  is present and warns as it nears expiry, so a lapse is caught before it strands a
  session rather than discovered when a PTY hangs.
- **Graceful degradation**: When the token is absent or rejected, granite still paints
  `/login` and #1750's recovery (when it lands) clears it. No new hang path.

### Flow

Vault `.env` (operator mints token) → `/update` syncs symlink → worker process loads
`settings.api.claude_code_oauth_token` → granite spawns PTY → `_build_env()` injects
`CLAUDE_CODE_OAUTH_TOKEN` → TUI authenticates silently (no `/login` prompt) → session runs.

If token missing/expired → TUI paints `/login` → #1750 recovery (fallback) → doctor/health
surface had already warned of impending expiry.

### Technical Approach

- **Add the settings field** to `APISettings` in `config/settings.py` mirroring
  `claude_api_key` (`config/settings.py:31`). Reuse/extend the existing
  `validate_api_keys` validator (min-length sanity only — `sk-ant-oat01-...` tokens are
  long, so the len≥10 floor is satisfied). Add a `.env.example` placeholder with a comment
  line above it (required by the completeness check).
- **Inject in `_build_env()`** (`pty_driver.py:272-295`): after the three blanking lines,
  add `token = settings...claude_code_oauth_token or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")`
  and set `env["CLAUDE_CODE_OAUTH_TOKEN"] = token` only when truthy. Do **not** blank it
  on the empty path — leaving it unset preserves today's inherit-from-parent behavior and
  lets #1750 recovery still fire. Extend the `_build_env()` docstring to explain that
  `CLAUDE_CODE_OAUTH_TOKEN` is the prevention credential and is intentionally *not* one of
  the blanked vars.
- **Expiry detection**: prefer a presence + format check plus an optional issued-at/expiry
  heuristic surfaced through `tools/doctor.py` (a new check) and/or the dashboard health
  block. If the token's expiry is not introspectable from the string alone, fall back to a
  configurable "minted_on" date in the vault (`CLAUDE_CODE_OAUTH_TOKEN_MINTED=YYYY-MM-DD`)
  and warn when within N days of the 1-year mark. (See Open Question 2.)
- **No collision with the subscription-OAuth path**: the token *is* a subscription
  credential, so it complements the `ANTHROPIC_*` blanking rather than fighting it. The
  blanking stays exactly as-is.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_build_env()` has no `except Exception: pass` blocks today; the token injection
      must not add a silent swallow. If the settings import is guarded, assert the
      fallback to `os.environ.get` is observable (logged or returns the inherited value).
- [ ] doctor/health expiry check: any `except` around token parsing must log a warning,
      not silently report "healthy."

### Empty/Invalid Input Handling
- [ ] Token = `None` / empty string / whitespace → `_build_env()` must **not** set
      `CLAUDE_CODE_OAUTH_TOKEN` (and must not set it to `""`), so the inherit-from-parent
      and #1750-recovery paths remain intact. Add a unit test asserting the key is absent
      from the returned env when the setting is empty.
- [ ] Token present but malformed (not `sk-ant-oat01-...`) → settings validator behavior
      documented; the env is still set (Claude Code itself rejects it → `/login` →
      recovery). Test that a malformed token does not crash `_build_env()`.

### Error State Rendering
- [ ] doctor/dashboard expiry warning is user-visible (operator-facing) — test that an
      expired/near-expiry token renders a warning, not a silent pass.
- [ ] Verify the warning message names the remediation (`claude setup-token` on a
      browser machine), so the operator knows the one human action required.

## Test Impact

- [ ] `tests/unit/test_granite_pty_driver.py` (or the nearest existing `_build_env` test;
      if none exists, CREATE `tests/unit/test_granite_oauth_token_env.py`) — ADD cases:
      (a) token set in settings → `CLAUDE_CODE_OAUTH_TOKEN` present in env and unchanged;
      (b) token empty/None → key absent from env; (c) `ANTHROPIC_*` still blanked in both
      cases (regression guard for PR #1612 behavior).
- [ ] `tests/unit/test_settings.py` (if present) — ADD: `claude_code_oauth_token` field
      loads from env and defaults to `None`. If no such file exists, fold into the new
      test module above.
- [ ] `tests/unit/test_doctor.py` (if present) — ADD: token presence/expiry check renders
      a warning on a near-expiry/absent token. If doctor has no unit test harness, cover
      via a small focused test for the new check function.

No existing tests are expected to break — the change is additive (new optional field, new
env key set only when truthy, blanking unchanged). The new tests are the primary coverage.

## Rabbit Holes

- **Automating token minting.** `claude setup-token` requires a browser and shows the
  token once. Do **not** try to script the mint or scrape the token — it is a deliberate
  human, once-a-year action. Stay in env-plumbing + detection territory.
- **A Keychain-backed secret store.** Tempting for "secure storage," but the entire system
  already standardizes on the vault `.env` symlink for secrets. Introducing Keychain here
  would be a parallel mechanism and a migration burden for one token. Use the vault.
- **Parsing/validating the OAuth JWT to compute exact expiry.** If the token isn't a
  decodable JWT (or the format isn't guaranteed), do not build a brittle decoder. A
  minted-on date + 1-year heuristic is sufficient for "warn before it strands a session."
- **Reworking #1750's detection.** Recovery detection is #1750's job. This plan only
  reduces how often it fires; it must not touch `startup_parser` login patterns.

## Risks

### Risk 1: Token leaks into logs or non-PTY child processes
**Impact:** A long-lived (~1-year) subscription credential in plaintext logs is a serious
exposure.
**Mitigation:** Only set `CLAUDE_CODE_OAUTH_TOKEN` inside `_build_env()` (granite PTY
children), never echo it. Add a test asserting the token value never appears in any
log/format string the change introduces. Treat it with the same handling as
`ANTHROPIC_API_KEY` — never printed, never committed (vault-only, `.env.example`
placeholder only).

### Risk 2: Silent expiry strands a session a year out
**Impact:** When the token lapses, sessions fall back to `/login`; if #1750 hasn't landed
or also fails, the PTY hangs at its `startup_unresolved` ceiling.
**Mitigation:** The doctor/health expiry check warns *before* the 1-year mark so the
operator re-mints proactively. The fallback to #1750 recovery is the second line of
defense; both together mean a missed re-mint degrades gracefully rather than hard-failing.

### Risk 3: Operator pastes a stale/wrong token, masking real failures
**Impact:** A malformed token still suppresses nothing — Claude Code rejects it →
`/login` → recovery — but the operator may believe prevention is working.
**Mitigation:** The prerequisite check validates the `sk-ant-oat01-` prefix; the doctor
check confirms presence and format. A malformed token surfaces as a warning, not a silent
pass.

## Race Conditions

No race conditions identified. The token is read once from settings at process startup and
injected synchronously into each PTY child's env dict at spawn time. There is no shared
mutable token state, no concurrent rotation, and no cross-process write — minting happens
out-of-band (human, vault edit) and is picked up only on the next process start.

## No-Gos (Out of Scope)

- [EXTERNAL] Minting and rotating the `CLAUDE_CODE_OAUTH_TOKEN` itself. `claude
  setup-token` requires a real browser and displays the token once; this is an irreducible
  human action on a browser-capable machine. The plan wires up *consumption* and
  *expiry-warning* of the token; the operator performs the actual mint/rotation.
- [SEPARATE-SLUG #1750] The `/login` browser-drive **recovery** path (detection +
  deterministic BYOB re-auth). This plan is the complementary prevention track; recovery is
  tracked and planned separately in #1750 and must not be modified here.

## Update System

No `/update` *script* changes required for propagation. The token lives in the vault
`~/Desktop/Valor/.env`, and repo `.env` is already a symlink → vault
(`scripts/update/env_sync.py:62`, wired at run.py Step 1.6). Adding a vault secret
propagates to every machine automatically on its next sync — the established
"add-a-secret" pattern (vault `.env` + `.env.example` placeholder + `config/settings.py`
field, no sync step).

One optional `/update` touch to consider (Open Question 3): a non-fatal advisory in the
update flow that warns when `CLAUDE_CODE_OAUTH_TOKEN` is absent on a granite-bearing
machine, so a fresh machine isn't silently relying on the short-lived path. This is a
warning only — it must never block the bridge restart.

## Agent Integration

No agent integration required — this is a worker/PTY-internal credential change. The agent
(Telegram bridge → worker → granite PTY) reaches the new behavior transparently: the token
is injected into the PTY child env, so granite sessions simply stop hitting `/login` as
often. No new CLI entry point, no MCP tool, no bridge import. The only operator-facing
surface is the doctor/dashboard expiry warning, which uses existing health plumbing.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/granite-pty-production.md` — document the
      `CLAUDE_CODE_OAUTH_TOKEN` prevention mechanism, how it relates to the `ANTHROPIC_*`
      blanking, and the once-a-year mint/rotation procedure.
- [ ] Cross-link from `docs/features/granite-login-recovery.md` (the #1750 doc, once it
      exists) noting prevention (#1751) vs. recovery (#1750).
- [ ] Add/confirm an entry in `docs/features/README.md` index for the prevention behavior
      (or fold into the existing granite-pty-production entry).
- [ ] Create `docs/infra/granite-oauth-token.md` — INFRA doc capturing: current auth
      state, the new long-lived-token requirement, the ~1-year rotation cadence, the
      browser-machine mint constraint, and the rollback (remove the vault key → revert to
      short-lived OAuth + #1750 recovery).

### External Documentation Site
- [ ] N/A — this repo has no external docs site for this area.

### Inline Documentation
- [ ] Extend the `_build_env()` docstring in `pty_driver.py` to explain that
      `CLAUDE_CODE_OAUTH_TOKEN` is the prevention credential and is intentionally *not*
      blanked.
- [ ] Docstring for the new settings field and the doctor expiry check.

## Success Criteria

- [ ] `settings.api.claude_code_oauth_token` loads from the vault `.env`, defaults to
      `None`, with a `.env.example` placeholder.
- [ ] `_build_env()` sets `CLAUDE_CODE_OAUTH_TOKEN` in the PTY child env when the token is
      present, omits it when absent, and leaves the three `ANTHROPIC_*` vars blanked in
      both cases.
- [ ] A granite PTY spawned with a valid token authenticates without painting a `/login`
      prompt (verified at least via the env-assembly unit test; live verification noted in
      the PR if a token is available on the test machine).
- [ ] doctor/dashboard reports token presence and warns before expiry, naming the
      `claude setup-token` remediation.
- [ ] The token value never appears in logs (assertion test).
- [ ] `docs/infra/granite-oauth-token.md` exists; `granite-pty-production.md` updated.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] grep confirms `_build_env` references the new settings field / `CLAUDE_CODE_OAUTH_TOKEN`.

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead
NEVER builds directly — they deploy team members and coordinate.

### Team Members

- **Builder (token-plumbing)**
  - Name: token-builder
  - Role: Add the settings field, `.env.example` placeholder, and the `_build_env()`
    injection + docstring; add the doctor/health expiry check.
  - Agent Type: builder
  - Resume: true

- **Validator (token-plumbing)**
  - Name: token-validator
  - Role: Verify the env-assembly behavior (present/absent/malformed), the blanking
    regression guard, the no-leak assertion, and the expiry-warning rendering.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: token-docs
  - Role: Write the INFRA doc, update `granite-pty-production.md`, the README index entry,
    and inline docstrings.
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

(see template reference — builder, validator, documentarian used here)

## Step by Step Tasks

### 1. Add settings field + env injection
- **Task ID**: build-token-plumbing
- **Depends On**: none
- **Validates**: tests/unit/test_granite_oauth_token_env.py (create), config/settings.py
- **Assigned To**: token-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `claude_code_oauth_token: str | None` to `APISettings` in `config/settings.py`,
  extending the existing `validate_api_keys` validator coverage.
- Add a commented `CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-****` placeholder to `.env.example`.
- In `pty_driver.py::_build_env()`, after the three `ANTHROPIC_*` blanks, set
  `CLAUDE_CODE_OAUTH_TOKEN` from `settings.api.claude_code_oauth_token` (falling back to
  any inherited `os.environ` value) **only when truthy**; do not set it to `""`.
- Extend the `_build_env()` docstring per Inline Documentation.

### 2. Add expiry / presence health check
- **Task ID**: build-expiry-check
- **Depends On**: build-token-plumbing
- **Validates**: tests for the doctor/health check
- **Assigned To**: token-builder
- **Agent Type**: builder
- **Parallel**: false
- Add a check to `tools/doctor.py` (and/or the dashboard health block) reporting token
  presence + format and warning when near the ~1-year expiry (using the minted-on
  heuristic if exact expiry is not introspectable — see Open Question 2).
- Ensure the warning names the `claude setup-token` remediation.

### 3. Validate plumbing + check
- **Task ID**: validate-token
- **Depends On**: build-token-plumbing, build-expiry-check
- **Assigned To**: token-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify present/absent/malformed env-assembly behavior and the `ANTHROPIC_*` blanking
  regression guard.
- Verify the no-leak assertion and the expiry-warning rendering.
- Run validation commands; report pass/fail.

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-token
- **Assigned To**: token-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/infra/granite-oauth-token.md`; update
  `docs/features/granite-pty-production.md` and the features README index.
- Confirm inline docstrings landed.

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: token-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all verification checks; confirm every success criterion (including docs).
- Generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/ -x -q -k oauth_token` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Field exists | `python -c "from config.settings import settings; assert hasattr(settings.api, 'claude_code_oauth_token')"` | exit code 0 |
| Env injection wired | `grep -n CLAUDE_CODE_OAUTH_TOKEN agent/granite_container/pty_driver.py` | output contains CLAUDE_CODE_OAUTH_TOKEN |
| INFRA doc exists | `test -f docs/infra/granite-oauth-token.md` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Token storage location.** The plan assumes the vault `~/Desktop/Valor/.env`
   (`CLAUDE_CODE_OAUTH_TOKEN`), consistent with every other secret and the symlink
   propagation model — *not* macOS Keychain. Confirm the vault is the intended home, or
   state a reason to prefer Keychain (which would add a parallel secret mechanism).

2. **Expiry-detection mechanism.** Is the `sk-ant-oat01-...` token introspectable for an
   exact expiry (e.g., decodable), or should we rely on an operator-supplied
   `CLAUDE_CODE_OAUTH_TOKEN_MINTED=YYYY-MM-DD` date plus a 1-year heuristic to warn before
   it strands a session? The plan defaults to the minted-date heuristic; confirm.

3. **`/update` advisory.** Should `/update` emit a non-fatal warning when
   `CLAUDE_CODE_OAUTH_TOKEN` is absent on a granite-bearing machine (so a fresh machine
   isn't silently on the short-lived path), or is the doctor/dashboard check sufficient?
   This is the only optional touch to the update flow.

4. **Single-machine ownership.** The token is a **shared** subscription credential (like
   `ANTHROPIC_API_KEY`), not tied to `projects.<key>.machine`. Confirm there's no desire to
   make it per-machine — recon found no ownership interaction, so the plan treats it as a
   global vault secret.
