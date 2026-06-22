---
status: Ready
type: feature
appetite: Small
owner: Valor Engels
created: 2026-06-22
tracking: https://github.com/tomcounsell/ai/issues/1751
last_comment_id:
revision_applied: true
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
  human, browser-gated, one-shot action → it belongs in No-Gos as `[EXTERNAL]`. Predictive
  expiry detection (knowing a lapse is coming) is desirable but is rotation scaffolding
  deferred to a future rotation slug; this slug ships presence + prefix detection only and
  accepts graceful degradation to #1750 recovery on lapse (see Resolved Decision 2).
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
3. **Load**: The repo `.env` (symlink → vault) is loaded into the worker process
   environment at startup, so the bare `CLAUDE_CODE_OAUTH_TOKEN` is present in
   `os.environ`. **No pydantic settings field is involved** — see the Technical Approach
   note on the `env_nested_delimiter` pitfall.
4. **Worker → PTY env**: The granite PTY driver's `_build_env()`
   (`pty_driver.py:272-295`) copies `os.environ` (which already carries
   `CLAUDE_CODE_OAUTH_TOKEN`), then blanks the three `ANTHROPIC_*` vars. It reads the token
   back via `os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")` and re-sets it explicitly **only
   when truthy** so the intent is visible at the injection point and the var is documented
   as deliberately *not* blanked. The per-session `_extra_env` overlay still merges on top.
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
- PM check-ins: 0-1 (decisions resolved in-plan; see Resolved Decisions)
- Review rounds: 1

This is a narrow, additive credential-plumbing change: one bare-env read in an existing
function (no settings field), a presence+prefix health check, plus docs and tests. All
prior open questions are now resolved with defensible defaults in the Resolved Decisions
section.

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

- **No settings field — read the bare env var directly**: `_build_env()` reads
  `os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")`. This is the chosen approach because
  `config/settings.py` uses `env_nested_delimiter="__"`, so a field on the nested
  `APISettings` model would bind **only** from `API__CLAUDE_CODE_OAUTH_TOKEN`, never the
  bare `CLAUDE_CODE_OAUTH_TOKEN` documented everywhere (the prereq check, `.env.example`,
  the vault). Adding it to `APISettings` would leave `settings.api.claude_code_oauth_token`
  silently `None` and the presence checks inert. `_build_env()` already does
  `os.environ.copy()`, so the bare var is in hand with **no settings import and no field**.
  (See Technical Approach for the rejected alternative.)
- **PTY env injection**: `_build_env()` in `pty_driver.py` reads the token via
  `os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")` and re-sets it explicitly **after** blanking
  the `ANTHROPIC_*` vars (only when truthy), so the long-lived subscription token is
  present in the TUI child without re-introducing API-key/base-URL leakage, and the intent
  is documented at the injection point.
- **Expiry / presence detection (presence + prefix only)**: A lightweight check (surfaced
  via the existing health surface — `python -m tools.doctor` and/or the dashboard) that
  reports whether the token is **present** and has the `sk-ant-oat01-` **prefix**. It does
  **not** attempt minted-date or N-day-to-expiry heuristics — that rotation scaffolding is
  excluded by the No-Gos and deferred to a future rotation slug. The check answers "is a
  plausibly-valid prevention token wired?", not "when does it expire?".
- **Graceful degradation**: When the token is absent or rejected, granite still paints
  `/login` and #1750's recovery (when it lands) clears it. No new hang path.

### Flow

Vault `.env` (operator mints token) → `/update` syncs symlink → worker process loads
`.env` into `os.environ` (bare `CLAUDE_CODE_OAUTH_TOKEN`) → granite spawns PTY →
`_build_env()` reads `os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")` and re-injects it → TUI
authenticates silently (no `/login` prompt) → session runs.

If token missing/expired → TUI paints `/login` → #1750 recovery (fallback). The
doctor/health surface reports presence + prefix so a missing/malformed token is visible
before a session strands.

### Technical Approach

- **Read the bare env var directly in `_build_env()`** — do **not** add a settings field.
  `config/settings.py` sets `env_nested_delimiter="__"` (`config/settings.py:374`), and
  `APISettings` is a nested `BaseModel` reached via `Settings.api`
  (`config/settings.py:397`). A field added to `APISettings` would therefore bind **only**
  from the env var `API__CLAUDE_CODE_OAUTH_TOKEN`, never the bare `CLAUDE_CODE_OAUTH_TOKEN`
  that the prereq check, `.env.example`, and the vault all use. The result would be
  `settings.api.claude_code_oauth_token is None` even when the token is correctly set,
  rendering every presence check inert. The load path must match the bare key the rest of
  the plan uses, so `_build_env()` reads `os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")`
  directly. (`_build_env()` already calls `os.environ.copy()`, so there is no extra import
  and no field to maintain.)
  - *Rejected alternative:* declaring `claude_code_oauth_token` on the **top-level**
    `Settings` (like `sdlc_agent_gh_token` at `config/settings.py:391`, which binds from the
    bare `SDLC_AGENT_GH_TOKEN`) would also work. It is rejected only because it adds a field
    and an import for a value `_build_env()` can read straight from `os.environ`; the direct
    read is the smaller change. Either way, **never** put the field on nested `APISettings`.
- **Inject in `_build_env()`** (`pty_driver.py:272-295`): after the three blanking lines,
  add `token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")` and set
  `env["CLAUDE_CODE_OAUTH_TOKEN"] = token` only when truthy. Do **not** blank it on the
  empty path — `os.environ.copy()` already carried any inherited value through, and not
  overwriting it preserves today's inherit-from-parent behavior so #1750 recovery still
  fires. Extend the `_build_env()` docstring to explain that `CLAUDE_CODE_OAUTH_TOKEN` is
  the prevention credential and is intentionally *not* one of the blanked vars.
- **Presence + prefix detection (no expiry heuristic)**: a check surfaced through
  `tools/doctor.py` (a new check) and/or the dashboard health block that reports whether
  `CLAUDE_CODE_OAUTH_TOKEN` is **present** and starts with `sk-ant-oat01-`. It does **not**
  compute or warn on expiry: there is no minted-date field and no N-day heuristic. Exact
  expiry / rotation warning is rotation scaffolding, excluded by the No-Gos and deferred to
  a future rotation slug (see Open Question 2). The check exists to confirm a plausibly
  valid prevention token is wired, not to predict its lapse.
- **No collision with the subscription-OAuth path**: the token *is* a subscription
  credential, so it complements the `ANTHROPIC_*` blanking rather than fighting it. The
  blanking stays exactly as-is.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_build_env()` has no `except Exception: pass` blocks today; the token injection
      must not add a silent swallow. The injection is a plain `os.environ.get` read — no
      guarded import — so the only branch is truthy/falsy.
- [ ] doctor/health presence check: any `except` around the presence/prefix read must log
      a warning, not silently report "healthy."

### Empty/Invalid Input Handling
- [ ] Token = `None` / empty string / whitespace → `_build_env()` must **not** set
      `CLAUDE_CODE_OAUTH_TOKEN` (and must not set it to `""`), so the inherit-from-parent
      and #1750-recovery paths remain intact. Add a unit test asserting the key is absent
      from the returned env when `CLAUDE_CODE_OAUTH_TOKEN` is unset in `os.environ`.
- [ ] Token present but malformed (not `sk-ant-oat01-...`) → `_build_env()` still sets it
      (Claude Code itself rejects it → `/login` → recovery). Test that a malformed token
      does not crash `_build_env()`. The doctor check flags the missing prefix as a
      warning.

### Error State Rendering
- [ ] doctor/dashboard presence warning is user-visible (operator-facing) — test that an
      absent or wrong-prefix token renders a warning, not a silent pass.
- [ ] Verify the warning message names the remediation (`claude setup-token` on a
      browser machine), so the operator knows the one human action required.

## Test Impact

- [ ] `tests/unit/test_granite_pty_driver.py` (or the nearest existing `_build_env` test;
      if none exists, CREATE `tests/unit/test_granite_oauth_token_env.py`) — ADD cases
      (set/unset `CLAUDE_CODE_OAUTH_TOKEN` in `os.environ` via monkeypatch):
      (a) token set in env → `CLAUDE_CODE_OAUTH_TOKEN` present in returned env and
      unchanged; (b) token unset/empty → key absent from returned env; (c) `ANTHROPIC_*`
      still blanked in both cases (regression guard for PR #1612 behavior).
- [ ] `tests/unit/test_doctor.py` (if present) — ADD: token presence/prefix check renders
      a warning on an absent or wrong-prefix token, and passes on a valid `sk-ant-oat01-`
      token. If doctor has no unit test harness, cover via a small focused test for the new
      check function. (No expiry/minted-date assertions — out of scope.)

No `config/settings.py` field is added, so **no `test_settings.py` change is needed** —
this is the key consequence of reading the bare env var directly. No existing tests are
expected to break: the change is additive (a new env key set only when truthy in
`_build_env`, blanking unchanged, plus a new doctor check). The new tests are the primary
coverage.

## Rabbit Holes

- **Automating token minting.** `claude setup-token` requires a browser and shows the
  token once. Do **not** try to script the mint or scrape the token — it is a deliberate
  human, once-a-year action. Stay in env-plumbing + detection territory.
- **A Keychain-backed secret store.** Tempting for "secure storage," but the entire system
  already standardizes on the vault `.env` symlink for secrets. Introducing Keychain here
  would be a parallel mechanism and a migration burden for one token. Use the vault.
- **Any expiry computation — JWT decode, minted-date field, or N-day warning.** This is
  rotation scaffolding, explicitly excluded by the No-Gos and deferred to a future rotation
  slug. Do not build a JWT decoder, do not add a `CLAUDE_CODE_OAUTH_TOKEN_MINTED` vault
  field, and do not emit "expires in N days" warnings. The detection here is **presence +
  `sk-ant-oat01-` prefix only**.
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
**Mitigation:** Proactive expiry *warning* is deliberately **out of scope** for this slug
(it is rotation scaffolding deferred to a future rotation slug). The accepted mitigation
here is the two-layer fallback: the token lapse degrades to `/login`, which #1750 recovery
clears, re-driving the `claude setup-token`/OAuth flow. The doctor presence+prefix check
catches the *absent/malformed* case (a fresh machine, a bad paste) but does **not** predict
the lapse date. Predictive expiry warning is the explicit job of the future rotation slug —
this plan accepts the once-a-year lapse degrading gracefully to recovery rather than being
warned in advance.

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
  human action on a browser-capable machine. The plan wires up *consumption* and a
  *presence+prefix* check of the token; the operator performs the actual mint/rotation.
- [SEPARATE-SLUG #1750] The `/login` browser-drive **recovery** path (detection +
  deterministic BYOB re-auth). This plan is the complementary prevention track; recovery is
  tracked and planned separately in #1750 and must not be modified here.
- [FUTURE-ROTATION-SLUG] Expiry detection and rotation warning — JWT decode, a
  `CLAUDE_CODE_OAUTH_TOKEN_MINTED` minted-date vault field, or any "expires in N days"
  heuristic. This slug ships **presence + `sk-ant-oat01-` prefix** detection only. The
  predictive expiry/rotation-reminder mechanism is deferred to a dedicated future rotation
  slug so the No-Gos stay internally consistent (no minted-date scaffolding sneaks back in
  via the detection check).

## Update System

No `/update` *script* changes required for propagation. The token lives in the vault
`~/Desktop/Valor/.env`, and repo `.env` is already a symlink → vault
(`scripts/update/env_sync.py:62`, wired at run.py Step 1.6). Adding a vault secret
propagates to every machine automatically on its next sync — the established
"add-a-secret" pattern (vault `.env` + `.env.example` placeholder + `config/settings.py`
field, no sync step).

The "token absent on a granite machine" advisory lives in the **doctor presence check**,
not the update flow (Resolved Decision 3). `/update` stays untouched — no advisory is added
there, so there is no risk of blocking the bridge restart.

## Agent Integration

No agent integration required — this is a worker/PTY-internal credential change. The agent
(Telegram bridge → worker → granite PTY) reaches the new behavior transparently: the token
is injected into the PTY child env, so granite sessions simply stop hitting `/login` as
often. No new CLI entry point, no MCP tool, no bridge import. The only operator-facing
surface is the doctor/dashboard presence+prefix check, which uses existing health plumbing.

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
      `CLAUDE_CODE_OAUTH_TOKEN` is the prevention credential, is read from the bare env var
      (not a settings field, because nested `APISettings` + `env_nested_delimiter="__"`
      would require `API__CLAUDE_CODE_OAUTH_TOKEN`), and is intentionally *not* blanked.
- [ ] Docstring for the doctor presence+prefix check.

## Success Criteria

- [ ] `_build_env()` reads the bare `CLAUDE_CODE_OAUTH_TOKEN` from `os.environ` (the repo
      `.env` symlink → vault carries it); **no field is added to `config/settings.py`**
      (the nested `APISettings` + `env_nested_delimiter="__"` pitfall is avoided). A
      `.env.example` placeholder with a leading comment line is present.
- [ ] `_build_env()` sets `CLAUDE_CODE_OAUTH_TOKEN` in the PTY child env when the token is
      present in `os.environ`, omits it when absent/empty, and leaves the three
      `ANTHROPIC_*` vars blanked in both cases.
- [ ] A granite PTY spawned with a valid token authenticates without painting a `/login`
      prompt (verified at least via the env-assembly unit test; live verification noted in
      the PR if a token is available on the test machine).
- [ ] doctor/dashboard reports token **presence + `sk-ant-oat01-` prefix** (no expiry
      heuristic), naming the `claude setup-token` remediation when absent/malformed.
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
  - Role: Add the `.env.example` placeholder and the `_build_env()` bare-env injection +
    docstring (no settings field); add the doctor/health presence+prefix check.
  - Agent Type: builder
  - Resume: true

- **Validator (token-plumbing)**
  - Name: token-validator
  - Role: Verify the env-assembly behavior (present/absent/malformed), the blanking
    regression guard, the no-leak assertion, and the presence+prefix warning rendering.
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
- Do **not** add a settings field. The token is read from the bare `os.environ` in
  `_build_env()` (the nested `APISettings` + `env_nested_delimiter="__"` pitfall makes a
  field bind only from `API__CLAUDE_CODE_OAUTH_TOKEN`, which would be silently `None`).
- Add a commented `CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-****` placeholder to `.env.example`
  (with a leading comment line, required by the completeness check).
- In `pty_driver.py::_build_env()`, after the three `ANTHROPIC_*` blanks, read
  `token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")` and set
  `env["CLAUDE_CODE_OAUTH_TOKEN"] = token` **only when truthy**; do not set it to `""`.
- Extend the `_build_env()` docstring per Inline Documentation.

### 2. Add presence + prefix health check
- **Task ID**: build-presence-check
- **Depends On**: build-token-plumbing
- **Validates**: tests for the doctor/health check
- **Assigned To**: token-builder
- **Agent Type**: builder
- **Parallel**: false
- Add a check to `tools/doctor.py` (and/or the dashboard health block) reporting whether
  `CLAUDE_CODE_OAUTH_TOKEN` is present in `os.environ` and starts with `sk-ant-oat01-`.
- Do **not** compute expiry, add a minted-date field, or warn on time-to-expiry — that is
  deferred to the future rotation slug (see No-Gos).
- Ensure the warning (on absent/wrong-prefix) names the `claude setup-token` remediation.

### 3. Validate plumbing + check
- **Task ID**: validate-token
- **Depends On**: build-token-plumbing, build-presence-check
- **Assigned To**: token-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify present/absent/malformed env-assembly behavior and the `ANTHROPIC_*` blanking
  regression guard.
- Verify the no-leak assertion and the presence+prefix warning rendering.
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
| No settings field added | `grep -c claude_code_oauth_token config/settings.py` | `0` (deliberately not on nested APISettings) |
| Env injection wired | `grep -n CLAUDE_CODE_OAUTH_TOKEN agent/granite_container/pty_driver.py` | output contains `os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")` |
| INFRA doc exists | `test -f docs/infra/granite-oauth-token.md` | exit code 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | critique | Field on nested `APISettings` + `env_nested_delimiter="__"` binds only from `API__CLAUDE_CODE_OAUTH_TOKEN`, never the bare `CLAUDE_CODE_OAUTH_TOKEN` — `settings.api.claude_code_oauth_token` would be silently `None`, presence checks inert. | Solution / Technical Approach / Tasks 1-2 / Success Criteria / Verification | Dropped the settings field entirely. `_build_env()` reads the bare `os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")` directly (it already does `os.environ.copy()`). Load path now matches the bare key used by the prereq check and `.env.example`. Rejected top-level-`Settings` alternative documented. |
| CONCERN | critique | `CLAUDE_CODE_OAUTH_TOKEN_MINTED` minted-date + N-day expiry warning is rotation scaffolding the No-Gos exclude — internally inconsistent. | Solution / Rabbit Holes / No-Gos / Risk 2 / Resolved Decision 2 | Scoped detection down to presence + `sk-ant-oat01-` prefix. Added an explicit `[FUTURE-ROTATION-SLUG]` No-Go; removed all minted-date/JWT/expiry-warning references. Risk 2 now accepts graceful degradation to #1750 recovery. |

---

## Resolved Decisions

The four prior open questions are resolved with defensible defaults. Each is a decision,
not a dangling question — if any is wrong, it's a one-line plan edit, but the build does
not wait on confirmation.

1. **Token storage = vault `~/Desktop/Valor/.env`, bare `CLAUDE_CODE_OAUTH_TOKEN` line.**
   Decided: the vault `.env`, consistent with the repo's "all secrets in the vault `.env`"
   convention and the symlink propagation model. macOS Keychain is **out of scope** — it
   would add a parallel secret mechanism and a migration burden for one token (see Rabbit
   Holes). The token is a bare line, not nested.

2. **Expiry detection = presence + `sk-ant-oat01-` prefix check only.** Decided: no
   minted-date field, no JWT decode, no time-to-expiry warning. The `sk-ant-oat01-` token
   is not a reliably introspectable JWT, and predictive expiry is rotation scaffolding the
   No-Gos exclude. The minted-date heuristic is deferred to a future rotation slug. The
   accepted lapse mitigation is graceful degradation to #1750 recovery (see Risk 2).

3. **`/update` propagation = no script change.** Decided: the vault `.env` symlink already
   propagates the secret to every machine on its next sync (`scripts/update/env_sync.py:62`,
   wired at run.py Step 1.6). No `/update` script edit is needed. The optional non-fatal
   "token absent on a granite machine" advisory is folded into the doctor presence check
   instead of the update flow — keeping `/update` untouched.

4. **Single-machine ownership = N/A; the token is a SHARED secret.** Decided: the token is
   subscription-backed (it counts against the Claude plan), **not** a bridge-contact
   identifier tied to `projects.<key>.machine`. Single-machine ownership (which governs
   Telegram DMs, group names, email contacts/domains) does not apply. It is a global vault
   secret like `ANTHROPIC_API_KEY` — shared across every machine, owned by none.
