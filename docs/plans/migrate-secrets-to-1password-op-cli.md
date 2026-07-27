---
status: Planning
type: chore
appetite: Large
owner: Valor Engels
created: 2026-07-27
tracking: https://github.com/tomcounsell/ai/issues/1813
last_comment_id: IC_kwDOEYGa088AAAABLzXM1g
---

# Migrate secrets from plaintext .env to 1Password service account + op CLI

## Problem

Every secret this system uses (114+ keys: `ANTHROPIC_*`, `CLAUDE_CODE_OAUTH_TOKEN`,
`SDLC_AGENT_GH_TOKEN`, `TELEGRAM_*`, `NOTION_API_KEY`, `SENTRY_*`, …) lives in a
single plaintext file at `~/Desktop/Valor/.env` (iCloud-synced; the repo `.env`
is a symlink to it). The agent runs `claude --permission-mode bypassPermissions`
on a machine it fully owns, so any session — or a prompt-injection bug — can
`cat ~/Desktop/Valor/.env` and read every credential. Under launchd, secrets are
additionally **baked in plaintext into the worker plist's `EnvironmentVariables`**
at install time.

**Current behavior:** plaintext secrets at rest (vault file + plist), no scoping
(all-or-nothing), no central revocation, no access audit, large blast radius.

**Desired outcome:** secrets resolved on demand from 1Password via `op` using a
scoped service account, with no plaintext values at rest (references only),
vault-scoped grants, central revocation, and audited reads.

> **This plan is PLAN + CRITIQUE only.** No build, no code change to secret
> loading, until the owner resolves the central cutover-strategy decision in
> Open Questions #1. Secret loading is the highest-blast-radius surface in the
> repo; a half-landed migration takes down auth for bridge, worker, and every
> agent on every machine simultaneously.

## Freshness Check

**Baseline commit:** `8ce18808b`
**Issue filed at:** 2026-06-29T09:07:11Z
**Disposition:** Minor drift (one primary seam deleted and re-mapped; substance intact)

**File:line references re-verified against main:**
- `config/settings.py:1062` — `settings = Settings()` at import time — **still holds** (issue cited `:413-420,575`; drifted to `:1062`, `env_file` gate at `:863`).
- `worker/__main__.py:27-37` — terminal dotenv load, `VALOR_LAUNCHD`-gated — **still holds** (issue cited `:26-38`).
- launchd plist bake — **still holds but re-mapped**: the authoritative path is now `scripts/update/service.py:270 _inject_env_into_plist()` via `dotenv_values()`; a parallel shell copy remains at `scripts/install_worker.sh:131-175` (issue cited `:80-120`).
- `agent/granite_container/pty_driver.py::_build_env()` (issue's PRIMARY seam) — **GONE**. Deleted wholesale in `17ab8c348` ("Delete the PTY substrate", the headless `claude -p` cutover, #1930/#1924). Re-mapped below.
- op-install precedent `scripts/update/sentry_cli.py` + `npm_tools.py` orchestrated in `run.py` — **still holds**.
- `.env` symlink creation `scripts/update/env_sync.py` (`VAULT_ENV_PATH = ~/Desktop/Valor/.env`) — **still holds**.

**Re-mapped seam (the one that drifted):** the child-env construction the issue
located in the deleted `_build_env()` now lives in the headless session runner:
- `agent/session_runner/role_driver.py:76 subscription_auth_env()` — injects `CLAUDE_CODE_OAUTH_TOKEN` from the process env, blanks `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN`.
- `agent/session_runner/harness/claude.py:69 stripped_harness_env()` — pops the three `ANTHROPIC_*` auth vars for the OAuth `claude` child.
- **Both inherit the worker process env.** The child does not read the vault directly — it receives whatever is in the worker's `os.environ`. This is the pivotal architectural fact for this plan (see Technical Approach).

**Cited sibling issues/PRs re-checked:** #1924 / #1930 (granite PTY teardown) — merged; the substrate is gone; this issue's goal survives the teardown per the owner's 2026-07-06 triage comment.

**Active plans in `docs/plans/` overlapping this area:** none touching secret loading.

**Notes:** No `op` / `OP_SERVICE_ACCOUNT_TOKEN` / `op://` exists anywhere in
scripts, config, or `.env.example` on main. Greenfield migration.

## Prior Art

No prior issues or merged PRs attempt a 1Password/op migration (`gh issue list
--state closed --search "1password op secrets"` and the PR equivalent both
return empty). The relevant prior art is the **pattern precedents** to mirror,
not prior attempts:
- **PR #921** — established `~/Desktop/Valor/.env` as the single source of truth with the repo symlink. This is the state being migrated away from.
- **sentry-cli / npm-tools install pattern** (`scripts/update/sentry_cli.py`, `npm_tools.py`, orchestrated in `run.py`) — the non-fatal, idempotent third-party-binary installer shape to mirror for `op`.
- **Granite PTY teardown (#1924/#1930)** — deleted the issue's primary seam; already landed.

## Research

**Queries used:**
- 1Password op CLI service account `op run --env-file` launchd macOS headless caching offline behavior
- 1Password service account `op run` performance latency many secrets startup connectivity rate limits

**Key findings (all directly shape the plan):**

1. **`op` does NOT work offline.** Every `op read` / `op run` makes a network
   round-trip to `my.1password.com`, ~1s per invocation, even with the desktop
   app installed and caching on. Disabling the network interface fails all
   queries. → This is the availability regression the issue flagged, now
   confirmed: a local file read cannot fail this way; `op` can. Source:
   [1password.community — does op work offline](https://www.1password.community/discussions/developers/does-1password-cli-work-offline-on-macos/28176),
   [rbt.rs — scaling secret management](https://rbt.rs/blog/scaling-secret-management-with-1password-cli/).

2. **The `op` cache daemon breaks under launchd on recent macOS.** `op` spawns
   `op daemon --background` for caching; on macOS Tahoe this triggers TCC
   permission dialogs and hangs — the *exact* class of failure that already
   forced the plist-bake workaround for iCloud files. Mitigation: set
   `OP_CACHE=false` (or `--cache=false` as a **global** flag before the `run`
   subcommand). The daemon gives no benefit in a headless LaunchAgent. Source:
   [openclaw#55459](https://github.com/openclaw/openclaw/issues/55459). → Directly
   answers open-question #2: `op` *can* run under launchd, but only with caching
   disabled, and it is network-dependent.

3. **Batch resolution is mandatory.** `op run --env-file` / `op inject` resolve
   ALL `op://` refs in a single CLI call; looping `op read` per secret means 114
   round-trips (~1s each) at startup. Batched overhead is ~0.6s once. Source:
   [rbt.rs](https://rbt.rs/blog/scaling-secret-management-with-1password-cli/). →
   Answers open-question #4: use `op run --env-file -- python -m worker`.

4. **Service-account rate limits are ACCOUNT-WIDE and brutal.** Business 50K/day,
   Teams 5K/day, shared across all service accounts on the account. A
   crash-looping process that invokes `op` on every restart can generate
   281,000+ requests in <24h and **lock out every service account for up to 24
   hours**, with no backoff duration surfaced. A systemd user hit a 15-minute
   block from ~15 restarts in 10 minutes. Sources:
   [openclaw#56217](https://github.com/openclaw/openclaw/issues/56217),
   [1password.community — rate limits](https://www.1password.community/developers-69/service-account-rate-limits-15-minutes-block-no-backoff-duration-shown-23967). →
   NEW severe risk not in the issue; intersects the worker supervisor's
   restart/backoff behavior (see Risk 3).

5. **Avoid the `op environment` feature with service accounts** (reported
   "Environment was not found" bug); stick to `op://vault/item/field` refs in an
   env-file. Source:
   [1password.community — SA cannot read Environments](https://www.1password.community/developers-69/1password-cli-bug-report-service-account-cannot-read-environments-24058).

## Data Flow

Secret from vault → running process, today vs. proposed:

**Today (terminal worker):** `~/Desktop/Valor/.env` (plaintext) → repo `.env`
symlink → `dotenv.load_dotenv()` in `worker/__main__.py` → `os.environ` →
`Settings()` at import → session runner inherits `os.environ` → `claude -p`
child env.

**Today (launchd worker):** `dotenv_values(.env)` at `/update` time (terminal
context) → baked into worker plist `EnvironmentVariables` (plaintext) → launchd
sets process env at boot → same downstream.

**Proposed (both):** `op://` refs in a template file → `op run --env-file=tpl
--no-masking -- python -m worker` (single batched resolve, `OP_CACHE=false`) →
resolved values in the worker process `os.environ` → `Settings()` at import →
session runner inherits → `claude -p` child. The bootstrap `OP_SERVICE_ACCOUNT_TOKEN`
is the ONE secret that must exist outside 1Password to reach `op` in the first
place (see Open Question #2 / Risk 2).

The critical property: because the session runner **inherits the worker process
env** (re-mapped seam), injecting once at worker-process launch covers every
downstream consumer with zero changes to `role_driver.py`, `settings.py`, or the
child-env construction. This is the least-invasive integration point and it is
the same point for both terminal and launchd paths.

## Architectural Impact

- **New dependencies:** `op` CLI binary (installed via `/update`, mirroring
  sentry-cli), a 1Password service account, and a hard network dependency on
  `my.1password.com` at worker/bridge startup.
- **Interface changes:** none at the Python layer if the `op run` wrapper
  approach (Option A) is chosen — the process env is populated before Python
  starts, so `Settings()` is unchanged. The lazy-`op read` approach (Option B)
  would touch every secret consumer.
- **Coupling:** increases coupling of process startup to an external network
  service (1Password API). Today startup couples only to a local file.
- **Data ownership:** secret material moves from the local filesystem (owned by
  the machine) to 1Password's vault (owned by the account, machine holds only a
  scoped token).
- **Reversibility:** designed to be fully reversible per-machine (Rollback Plan)
  — the plaintext `.env` path is retired last and can be restored by re-pointing
  the launch wrapper.

## Appetite

**Size:** Large

**Team:** Solo dev, PM, code reviewer (security-sensitive), + owner decision gate

**Interactions:**
- PM check-ins: 2-3 (this is the highest-blast-radius surface in the repo)
- Review rounds: 2+ (security review mandatory; cross-vendor review if available)

This is Large because of communication/coordination overhead and the mandatory
owner decision gate, not coding volume. The code is moderate; the risk is not.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Owner has decided cutover strategy (OQ#1) | (human gate — no code check) | Gates the entire build |
| 1Password service account provisioned | `op whoami >/dev/null 2>&1; echo $?` (presence-only; never echo the token) | Headless auth exists |
| `op` CLI installed | `command -v op >/dev/null; echo $?` | Binary available |
| Network reachability to 1Password | `op vault list >/dev/null 2>&1; echo $?` | `op` is not offline-capable (Research #1) |

Run via `python scripts/check_prerequisites.py docs/plans/migrate-secrets-to-1password-op-cli.md`.
**Hard rule for all checks:** presence/exit-code only — never echo any portion of
a secret value (not even a redacted prefix); Bash stdout persists to transcripts.

## Solution

### Key Elements

- **`op` installer in the update system** — a `scripts/update/op_cli.py` module
  mirroring `sentry_cli.py` (idempotent, non-fatal on failure), orchestrated in
  `scripts/update/run.py`.
- **Reference template** — a committed `.env.op.tpl` (or `.env.example`
  extended) holding `op://Vault/Item/field` references, never values.
- **Launch wrapper** — the worker/bridge/email launch resolves secrets in one
  batched `op run --env-file` call with `OP_CACHE=false`, so the process env is
  populated before Python imports `settings`.
- **Bootstrap token handling** — `OP_SERVICE_ACCOUNT_TOKEN` is the single secret
  that lives outside 1Password; its storage location is Open Question #2.
- **Failure/rollback posture** — explicit, documented behavior when `op` is
  unreachable, and a per-machine revert to the plaintext path.

### Flow

Machine `/update` → installs `op`, provisions token → worker launch resolves
`op://` refs via `op run` → process env populated → Python starts → auth works →
(on `op` failure) documented fallback fires → (on rollback) launch wrapper
re-points to plaintext `.env`.

### Technical Approach

**Injection point (decided, low-risk):** inject at **worker/bridge process
launch**, not per-session. Because the session runner inherits the process env
(re-mapped seam: `role_driver.py:76`, `harness/claude.py:69`), populating the
process env once via `op run --env-file -- <cmd>` covers all downstream
consumers with no Python changes. This is Option A from the issue's OQ#1 and is
strongly preferred over lazy per-secret `op read` (Option B), which would touch
every consumer and multiply rate-limit exposure (Research #3, #4).

**launchd specifics (from Research #2):** the launchd plist's `ProgramArguments`
becomes `op run --env-file=<tpl> -- .venv/bin/python -m worker` (or a thin
wrapper script), with `OP_CACHE=false` and `OP_SERVICE_ACCOUNT_TOKEN` in
`EnvironmentVariables`. This REPLACES the current `_inject_env_into_plist()`
plaintext bake — the plist no longer carries 114 secret values, only the
bootstrap token and `op://`-resolution wiring. `scripts/update/service.py:270`
and the parallel `scripts/install_worker.sh:131-175` both change.

**The tension this plan will NOT resolve silently (see OQ#1):** the safe
migration technique is a bounded dual-read window (try `op`, fall back to
plaintext `.env`) so a machine mid-rollout never loses auth — but CLAUDE.md
forbids half-migrations and parallel paths. These are in genuine tension. The
plan surfaces it as the central owner decision rather than picking one.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The `op`-install module must mirror sentry-cli's non-fatal pattern: catch subprocess/network errors, log a WARNING, return a structured `InstallResult(action="failed", ...)`, never raise into `/update`. Test asserts the WARNING and that `/update` continues.
- [ ] The launch-wrapper `op run` failure path must NOT be a bare `except: pass` — it must emit an observable signal (log + Sentry) and take the documented fallback (OQ#1-dependent). Test asserts the signal fires.

### Empty/Invalid Input Handling
- [ ] `op run` returns empty/partial env when a ref is malformed or a vault item is missing — test that the wrapper detects a missing required key and fails loudly rather than starting the worker with a half-populated env.
- [ ] Missing `OP_SERVICE_ACCOUNT_TOKEN` → wrapper must fail with a clear message, not a stack trace, and must not fall through to an unauthenticated `op` call.

### Error State Rendering
- [ ] `op` unreachable at startup → the failure must surface (log + Sentry + the out-of-band sentinel the bridge watchdog already reads), not be swallowed. A worker that can't get secrets must not silently crash-loop (Risk 3).

## Test Impact
- [ ] `tests/unit/test_valor_service_bootstrap.py` — UPDATE: the worker/email launch path changes from direct `python -m worker` to an `op run` wrapper; the launchd `ProgramArguments` assertions change. Sandbox needs an `op` stub.
- [ ] Tests asserting `_inject_env_into_plist()` bakes 114 vars into the plist (`scripts/update/service.py` coverage) — REPLACE: the plist no longer carries secret values; assert refs/bootstrap-token wiring instead.
- [ ] `worker/__main__.py` dotenv-load tests (if any assert `load_dotenv` runs when not under launchd) — UPDATE: behavior becomes conditional on the chosen cutover strategy (dual-read vs atomic).
- [ ] New: `tests/unit/test_op_cli.py` — CREATE: install/verify module (mirror `test_install_scripts_bootstrap.py` shape), with an `op` stub on PATH; covers installed/skipped/failed and the non-fatal contract.
- [ ] New: launch-wrapper failure-path tests — CREATE: `op` unreachable, missing token, partial resolve.

## Rabbit Holes

- **1Password Connect server.** Research surfaces Connect as the HA/caching
  answer to rate limits, but standing up a Connect server is a separate
  infrastructure project far beyond this migration's appetite. Note it as a
  future option; do not build it here.
- **Per-secret vault scoping / multiple service accounts.** The issue asks this
  plan to "establish a pattern" — establish ONE scoped service account and
  document the pattern; do not enumerate and re-vault all 114 keys into
  fine-grained vaults in this pass.
- **Making `Settings()` lazy (Option B).** Tempting for "cleanliness," but it
  touches every consumer and multiplies `op` calls. The `op run` wrapper makes
  it unnecessary. Avoid.
- **Retiring iCloud vault sync in the same PR as the cutover.** The iCloud sync
  of `projects.json` / `reflections.yaml` is separate plumbing from secret
  values; don't entangle its removal with the secrets cutover.

## Risks

### Risk 1: Availability regression — `op` is network-dependent, a local file is not
**Impact:** if `my.1password.com` is unreachable at worker/bridge startup, the
process cannot resolve secrets and cannot start. Today a local `.env` read never
fails this way. On a home/office machine with flaky uplink, this is a real new
outage mode for the whole system.
**Mitigation:** OQ#1 (dual-read fallback vs atomic); circuit-breaker on `op`
failure (Risk 3); document the dependency; consider caching the resolved env to
a tmpfs for the process lifetime so only *startup* needs connectivity, not every
turn (the session runner already only reads the process env at launch).

### Risk 2: Bootstrap chicken-and-egg — the token is itself a secret
**Impact:** `OP_SERVICE_ACCOUNT_TOKEN` grants access to everything in its scope
and must exist in plaintext *somewhere* the launch wrapper can read before `op`
is available. If it lives in the plist `EnvironmentVariables`, we have merely
moved the plaintext-at-rest problem from 114 keys to 1 (all-powerful) key. If it
lives in `~/.zshenv`, launchd doesn't source it.
**Mitigation:** OQ#2. Candidate: macOS Keychain (readable by the launchd user
without TCC-on-iCloud problems) fetched by the wrapper; or plist env as an
explicitly-accepted reduced-surface (1 scoped, revocable, audited token vs 114
raw secrets). Either way the token must be tightly vault-scoped so its
compromise ≠ full compromise.

### Risk 3: Rate-limit crash-loop lockout (account-wide, up to 24h)
**Impact:** service-account rate limits are shared across ALL service accounts
on the account (50K/day Business). The worker supervisor restarts on failure;
if `op` fails and the worker crash-loops, each restart burns `op` calls. A tight
loop can hit hundreds of thousands of calls and lock out **every machine** for
up to 24h with no recovery. This is a system-wide, multi-machine outage from a
single wedged worker.
**Mitigation:** MANDATORY circuit-breaker on `op` startup failure — bounded
retries with exponential backoff, then STOP (do not crash-loop). Reuse the
worker-supervisor backoff knobs (`WORKER_SUPERVISOR_BASE_BACKOFF_S`,
`MAX_RESTARTS`, `WINDOW_S`) rather than inventing new ones. Batch with `op run`
(one call, not 114). This risk alone argues against a naive atomic cutover
without a proven breaker.

### Risk 4: Half-landed migration takes down auth everywhere
**Impact:** because injection is at process launch and the same wrapper is used
by bridge, worker, and email bridge across all machines, a broken cutover breaks
all three roles on every machine at once.
**Mitigation:** OQ#1 decision + staged per-machine rollout + the Rollback Plan;
never cut over all machines in one `/update` wave.

## Race Conditions

### Race 1: `/update` re-installs plist while worker is mid-restart on new launch wrapper
**Location:** `scripts/update/service.py` install path + launchd restart.
**Trigger:** `/update` swaps the plist `ProgramArguments` to the `op run` wrapper
while the drain-before-restart gate (#2141) is deciding whether to restart.
**Data prerequisite:** the `op://` template and `OP_SERVICE_ACCOUNT_TOKEN` must
be present on the machine BEFORE the plist is swapped, or the restarted worker
resolves nothing.
**State prerequisite:** `op whoami` must succeed before the plaintext path is
retired on that machine.
**Mitigation:** ordered install — install `op`, provision token, verify
`op whoami`, THEN swap the plist; the drain gate already serializes the restart.
Verify-before-retire is a hard sequencing rule in the build.

## No-Gos (Out of Scope)

- [EXTERNAL] Creating the 1Password service account and issuing `OP_SERVICE_ACCOUNT_TOKEN` — requires a human in the 1Password admin UI; cannot be done by the agent.
- [EXTERNAL] Rotating/revoking the 114 existing secrets after migration — a human world-action in each upstream provider; out of scope for the cutover mechanism.
- [ORDERED] Retiring the iCloud plaintext-`.env` vault sync — must wait until every machine has cut over and been verified (a human-gated, per-machine event); sequenced after the migration lands, not in it.
- [SEPARATE-SLUG] 1Password Connect server for HA/caching — not filed yet; if the owner wants it, file a separate issue. Explicitly not built here (Rabbit Holes).
- Fine-grained per-need vault decomposition of all 114 keys — establish the single-scoped-account pattern only; full decomposition is a follow-on.

## Update System

Heavily update-coupled — this is the core of the work:
- **New installer module** `scripts/update/op_cli.py` mirroring `sentry_cli.py`
  (idempotent, non-fatal), orchestrated in `scripts/update/run.py` alongside the
  existing sentry-cli / npm-tools install steps.
- **Token provisioning** must be propagated per machine (OQ#2 decides the
  mechanism); `/update` verifies `op whoami` presence (exit-code only) as a gate,
  fail-open to the existing path until the cutover flag is set.
- **Plist generation change** — `_inject_env_into_plist()` (`service.py:270`) and
  the shell twin (`install_worker.sh:131-175`) stop baking secret values and
  start emitting the `op run` wrapper `ProgramArguments` + bootstrap token.
- **`.env.example`** grows the `op://` reference template and documents the
  bootstrap token (placeholder only; the completeness check needs a comment line
  above each key).
- **Single-machine-ownership** interaction: token scope is per machine under the
  ownership model — document in `docs/features/single-machine-ownership.md`
  cross-reference.

## Agent Integration

No new agent/MCP tool surface. This is bridge/worker-internal: the change is to
how the worker/bridge/email processes acquire their environment at launch, which
is upstream of everything the agent does. The session runner and `claude -p`
child are unaffected by construction (they inherit the process env). Integration
tests belong in the update-system and launch-wrapper suites, not the agent-tool
surface.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/secret-resolution-1password.md`: the `op run`
  injection flow, service-account scoping, bootstrap-token handling, offline/
  failure behavior, circuit-breaker, and the per-machine rollback runbook.
- [ ] Add entry to `docs/features/README.md` index table.
- [ ] Create `docs/infra/migrate-secrets-to-1password-op-cli.md` (durable infra
  doc): `op` dependency, service-account rate-limit ceilings, network
  requirement, rollback plan. (Infra docs are never archived.)
- [ ] Update `CLAUDE.md` `## Secrets` section: replace "all secrets go in
  `~/Desktop/Valor/.env`" with the `op://`-reference posture once cut over.

### Inline Documentation
- [ ] Comment the launch-wrapper `OP_CACHE=false` / global-flag-ordering
  gotcha (Research #2) so it isn't "cleaned up" later.
- [ ] Comment the circuit-breaker with the rate-limit rationale (Research #4).

## Success Criteria

- [ ] Owner has answered Open Question #1 (cutover strategy) and #2 (bootstrap token location) — build does not start otherwise.
- [ ] `op` CLI installed + updated by `/update` on bridge machines, non-fatal on failure (mirrors sentry-cli).
- [ ] Secrets resolved via a single batched `op run --env-file` at process launch; no per-secret `op read` loop.
- [ ] No plaintext secret VALUES at rest in the repo, `~/Desktop/Valor/.env`, or the worker plist (references + one scoped bootstrap token only).
- [ ] launchd worker verified starting via the `op run` wrapper with `OP_CACHE=false`.
- [ ] Circuit-breaker proven: a simulated `op` failure does NOT crash-loop (bounded retries then stop); test asserts call count is bounded.
- [ ] Rollback runbook proven on one machine: revert to plaintext `.env` restores a working system.
- [ ] `docs/features/secret-resolution-1password.md` + infra doc created.
- [ ] Tests pass (`/do-test`); lint/format clean.
- [ ] grep confirms no secret value is echoed to stdout anywhere in the new code paths (anti-criterion below).

## Team Orchestration

### Team Members
- **Builder (update-system)** — Name: `op-installer` — Role: `op_cli.py` install module + `run.py` wiring + plist wrapper generation — Agent Type: builder — Domain: update-system — Resume: true
- **Builder (launch-wrapper)** — Name: `launch-wrapper` — Role: `op run` wrapper + circuit-breaker + failure signals — Agent Type: builder — Domain: security/untrusted-input — Resume: true
- **Code reviewer (security)** — Name: `sec-reviewer` — Role: verify no plaintext-at-rest, no secret echo, token-scope soundness — Agent Type: code-reviewer — Resume: true
- **Validator** — Name: `migration-validator` — Role: verify success criteria incl. circuit-breaker and rollback — Agent Type: validator — Resume: true
- **Documentarian** — Name: `secrets-doc` — Agent Type: documentarian — Resume: true

## Step by Step Tasks

> Build does NOT begin until Open Questions #1 and #2 are answered by the owner.

### 1. op install module
- **Task ID**: build-op-installer
- **Depends On**: none (after OQ gate)
- **Validates**: tests/unit/test_op_cli.py (create), tests/unit/test_install_scripts_bootstrap.py
- **Assigned To**: op-installer
- **Agent Type**: builder
- **Parallel**: true
- Create `scripts/update/op_cli.py` mirroring `sentry_cli.py` (idempotent, non-fatal); wire into `run.py`.

### 2. Launch wrapper + circuit-breaker
- **Task ID**: build-launch-wrapper
- **Depends On**: none (after OQ gate)
- **Validates**: launch-wrapper failure-path tests (create), tests/unit/test_valor_service_bootstrap.py
- **Assigned To**: launch-wrapper
- **Agent Type**: builder
- **Parallel**: true
- Implement `op run --env-file` wrapper with `OP_CACHE=false`, bounded-retry circuit-breaker reusing worker-supervisor backoff knobs, loud failure signals.
- Implement the OQ#1 strategy (dual-read fallback OR atomic) exactly as the owner decided — not both.

### 3. Plist / service.py cutover
- **Task ID**: build-plist-cutover
- **Depends On**: build-op-installer, build-launch-wrapper
- **Assigned To**: op-installer
- **Agent Type**: builder
- **Parallel**: false
- Replace `_inject_env_into_plist()` value-bake with wrapper `ProgramArguments` + bootstrap token; mirror in `install_worker.sh`. Enforce verify-before-retire ordering (Race 1).

### 4. Security review
- **Task ID**: review-security
- **Depends On**: build-plist-cutover
- **Assigned To**: sec-reviewer
- **Agent Type**: code-reviewer
- **Parallel**: false
- Verify no plaintext values at rest, no secret echo, token scoping, circuit-breaker soundness.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: build-plist-cutover
- **Assigned To**: secrets-doc
- **Agent Type**: documentarian
- **Parallel**: false

### 6. Final validation
- **Task ID**: validate-all
- **Depends On**: review-security, document-feature
- **Assigned To**: migration-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify all success criteria incl. circuit-breaker call-count bound and a proven single-machine rollback.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `scripts/pytest-clean.sh tests/unit/test_op_cli.py tests/unit/test_valor_service_bootstrap.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| op installer wired | `grep -c "op_cli" scripts/update/run.py` | output > 0 |
| No plaintext value-bake remains | `grep -c "dotenv_values" scripts/update/service.py` | match count == 0 |
| No secret echoed to stdout (anti-criterion) | `grep -rnE "print\(.*(TOKEN\|KEY\|SECRET\|PASSWORD)" scripts/update/op_cli.py` | match count == 0 |
| Batched resolve, not per-secret loop (anti-criterion) | `grep -c "op read" scripts/update/op_cli.py` | match count == 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

**1. (CENTRAL — the decision this plan needs before any build) Cutover strategy:
bounded dual-read window vs single atomic cutover.**
The safe way to migrate secrets is a dual-read window — try `op`, fall back to
the plaintext `.env` — so a machine mid-rollout never loses auth. But CLAUDE.md
forbids half-migrations and parallel paths. These genuinely conflict here.
Two honest options:
- **(A) Time-boxed dual-read exception.** Accept a deliberate, explicitly
  bounded (e.g. one rollout wave, deadline in the plan) parallel path where the
  wrapper falls back to plaintext `.env` if `op` fails. *Risk:* violates the
  no-parallel-paths rule; the plaintext `.env` must still exist during the
  window, so the at-rest exposure is not eliminated until the window closes;
  requires discipline to actually close it.
- **(B) Single atomic cutover with proven rollback.** No fallback path; each
  machine flips from plaintext to `op` in one `/update`, gated by a verified
  `op whoami` + a rehearsed rollback runbook. *Risk:* if `op` is unreachable or
  the token is wrong at flip time, that machine loses auth immediately; recovery
  depends on the rollback working under pressure (the 3AM scenario).
I recommend (B) with a mandatory circuit-breaker and per-machine staged rollout
(never all machines at once), because (A)'s window leaves plaintext at rest —
the exact thing we're trying to remove — and the no-parallel-paths rule exists
for good reasons. But this is the owner's call to make, not mine to assume.

**2. Where does the bootstrap `OP_SERVICE_ACCOUNT_TOKEN` live, and how is it
scoped?** It is the one secret that must exist outside 1Password. Options: macOS
Keychain (no iCloud-TCC problem, readable by the launchd user) vs plist
`EnvironmentVariables` (reduces 114 raw secrets to 1 scoped/revocable/audited
token, but still plaintext-at-rest for that one token). How tightly is the token
vault-scoped so its compromise ≠ full compromise?

**3. Rate-limit posture (Risk 3).** Given the account-wide 50K/day ceiling shared
across all service accounts and the crash-loop lockout mode, is the mandatory
circuit-breaker + batched `op run` sufficient, or does the owner want to weigh
1Password Connect (self-hosted cache, unlimited re-reads) despite its
infrastructure cost? (Connect is a Rabbit Hole for THIS plan; asking whether it
should be a separate follow-on.)

**4. Offline availability (Risk 1).** `op` requires connectivity at startup. Is a
startup-only network dependency acceptable (secrets cached in the process env for
the process lifetime, only re-fetched on restart), or is the home/office uplink
flaky enough that even startup-time dependency is a problem the owner wants
mitigated further (e.g. a resolved-env tmpfs cache with a TTL)?
