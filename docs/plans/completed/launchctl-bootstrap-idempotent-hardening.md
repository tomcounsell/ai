---
status: Complete
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-11
tracking: https://github.com/tomcounsell/ai/issues/2013
last_comment_id:
---

# launchctl bootstrap idempotent hardening

## Problem

A `/update` run on machine "Valor the Bald" aborted hard with:

```
Bootstrap failed: 5: Input/output error
Try re-running the command as root for richer errors.
```

The run terminated with no further `[update]` output — a hard abort mid-service-install, not a graceful degradation.

**Current behavior:**
`scripts/valor-service.sh` runs under `set -e` and calls `launchctl bootstrap` **bare** (no `|| true`, no `if` guard, no fallback) at three sites. When `bootstrap` returns errno 5 (`Input/output error` — the service label is still registered/draining in the `gui/<uid>/` domain, e.g. immediately after a `bootout` or from a stale half-load left by a prior crash), `set -e` aborts the entire `valor-service.sh install`/`restart`/`worker-start` invocation. `/update` invokes these via `scripts/update/service.py::install_service`/`restart_service`, so a transient launchd race takes down the whole update. The `install_*.sh` helper scripts share the identical bare-bootstrap shape and are latently exposed to the same abort.

**Desired outcome:**
Every shell `launchctl bootstrap` call site is idempotent (bootout-before-bootstrap) and fail-soft: on a bootstrap failure it falls back to `launchctl kickstart -k`, and a transient errno-5 never aborts the whole update. This matches the pattern the repo already established in `scripts/remote-update.sh` (issue #1898) and `scripts/update/service.py::install_log_rotate_agent`.

## Freshness Check

**Baseline commit:** 19829e66 (`git rev-parse HEAD` at plan time)
**Issue filed at:** 2026-07-11 (within the last hour, but issue was authored by this same session)
**Disposition:** Unchanged

**File:line references re-verified:**
- `scripts/valor-service.sh:390` — `bootstrap_plist_idempotent()` bare bootstrap after `bootout ... || true` — still holds (verified verbatim).
- `scripts/valor-service.sh:545` — bridge install bare bootstrap after `stop_bridge` — still holds.
- `scripts/valor-service.sh:721` — `worker-start` bare bootstrap after `bootout ... || true`, inside `if ! is_worker_launchd_loaded` — still holds.
- `scripts/remote-update.sh:247-281` — established `kickstart -k` + `if`-wrapped `bootout`+`bootstrap` fallback pattern — still holds (target to match).
- `scripts/update/service.py:804-811` — `install_log_rotate_agent` bootstrap rc-check + `kickstart -k` fallback — still holds (target to match).
- `install_worker.sh:167`/`:205`, `install_reflection_worker.sh:150`, `install_nightly_tests.sh:101`, `install_email_bridge.sh:230`, `install_sdlc_reflection.sh:41` — bare bootstrap shape confirmed at each line.

**Cited sibling issues/PRs re-checked:**
- #1407 (launchctl load vs bootstrap mismatch) — closed; established the bootout-before-bootstrap requirement this plan builds on.
- #1898 (bridge/worker restart hardening) — merged; `remote-update.sh:247-281` is its artifact and the canonical pattern to mirror.

**Commits on main since issue was filed (touching referenced files):** none.

**Active plans in `docs/plans/` overlapping this area:** none.

**Notes:** All cited line references match exactly. No drift.

## Prior Art

- **Issue #1407**: "Worker watchdog L2/L3 active recovery broken: launchctl load vs bootstrap mismatch leaves worker permanently down" — established that services must be registered via `bootstrap` (not `load`) in `gui/<uid>/`, and that a `bootout` must precede `bootstrap` to avoid "already bootstrapped". This plan extends that: bootout-before-bootstrap is necessary but insufficient, because bootout can leave the label mid-drain and the subsequent bootstrap still errno-5s.
- **PR #1914 / Issue #1898**: "Update verifies running-process release matches pulled HEAD; bridge restart on cron path" — introduced the `kickstart -k` first / `if`-wrapped `bootout`+`bootstrap` fallback pattern in `remote-update.sh:247-281` with the explicit comment "bootstrap error 5: label still registered". This is the exact pattern to propagate into `valor-service.sh` and the `install_*.sh` helpers.
- **PR #1978 / Issue #1964**: "isolate test_already_up_to_date from live launchd services" — the `test_remote_update.py` test that asserts an up-to-date run never leaks a `launchd bootstrap error`; confirms the test suite already guards this class of failure and gives a template for a new regression test.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #1407 | Switched worker recovery from `load` to `bootout`+`bootstrap` in `valor-service.sh` | Bootout-before-bootstrap fixed the "already bootstrapped" case but not the errno-5 race where the label is still draining; the bare `bootstrap` under `set -e` still aborts. |
| PR #1898 | Added `kickstart -k` + `if`-wrapped fallback in `remote-update.sh` | Fixed the cron restart path only. The fix was never propagated to `valor-service.sh`'s own install/restart verbs or the `install_*.sh` helpers, leaving the same bare-bootstrap abort in every path `/update` reaches through `service.py::install_service`. |

**Root cause pattern:** The correct idempotent+fail-soft bootstrap pattern was invented once (in `remote-update.sh`) but never centralized or propagated to the sibling shell scripts. This plan closes that gap by hardening every remaining bare-bootstrap call site to the same contract.

## Architectural Impact

- **New dependencies**: none.
- **Interface changes**: none — the shell functions keep their signatures; only their internal bootstrap logic changes.
- **Coupling**: unchanged. This is a resilience fix internal to the shell install scripts.
- **Data ownership**: unchanged.
- **Reversibility**: trivially reversible (revert the diff). No state migration.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1 (code review of the shell diff + regression test)

## Prerequisites

No prerequisites — this work modifies shell scripts already in the repo and has no external dependencies. (macOS `launchctl` is the deploy-time target, not a build-time requirement; tests assert on script *source text* and `plutil -lint`, not live launchd.)

## Solution

### Key Elements

- **Shared shell helper `bootstrap_or_kickstart <domain-target> <plist-path> <label>`**: a single function in `scripts/valor-service.sh` that encapsulates the idempotent+fail-soft sequence — bootout the label (tolerant), `bootstrap`, and if bootstrap returns non-zero, fall back to `kickstart -k`; never return non-zero in a way that aborts `set -e`. All three `valor-service.sh` call sites route through it.
- **`install_*.sh` hardening**: each standalone installer replaces its bare `launchctl bootstrap` with the same bootout → bootstrap → `kickstart -k` fallback inline (these scripts don't share a lib, so the sequence is inlined but identical in behavior), wrapped so a bootstrap failure logs a warning and attempts recovery rather than aborting.
- **Regression test**: a new unit test asserts every hardened script contains the `kickstart` fallback token and that no bare `launchctl bootstrap` line remains un-guarded (source-text assertion, matching the existing `test_remote_update.py` / `test_install_reflection_worker.py` style).

### Flow

`/update` (cron or manual) → `run.py` → `service.py::install_service` → `valor-service.sh install` → `bootstrap_or_kickstart` per service → bootstrap succeeds OR kickstart-k recovers → update continues (no abort).

Manual install → `./scripts/install_worker.sh` (etc.) → inline bootout → bootstrap → on failure `kickstart -k` → script exits 0 on recovery.

### Technical Approach

- **`valor-service.sh`**: add `bootstrap_or_kickstart()`. It runs `launchctl bootout "$target" 2>/dev/null || true`, then captures `launchctl bootstrap "$domain" "$plist"`; on non-zero it echoes a scannable warning and runs `launchctl kickstart -k "$target" 2>/dev/null || true`. The function itself must not trip `set -e` — capture the bootstrap rc into a variable rather than letting it propagate. Refactor `bootstrap_plist_idempotent()` (L390), the bridge install (L545), and `worker-start` (L721) to call it. The `worker-start` `else` branch (loaded → `kickstart`) already exists and stays.
- **`install_*.sh` helpers**: replace each bare `launchctl bootstrap "gui/$(id -u)" "$PLIST_DST"` with a guarded block: `if ! launchctl bootstrap "gui/$(id -u)" "$PLIST_DST"; then echo "WARN: bootstrap failed; attempting kickstart"; launchctl kickstart -k "gui/$(id -u)/$LABEL" 2>/dev/null || true; fi`. Under `set -euo pipefail`, the `if !` guard suppresses `set -e` for the bootstrap, so the script no longer aborts on errno 5. Every script already does `bootout ... 2>/dev/null || true` before bootstrap — that stays.
- **Idempotency contract** matches `service.py::install_log_rotate_agent` (L804-811): bootout (if loaded) → bootstrap → on failure kickstart -k → verify. No behavioral change on the happy path (bootstrap succeeds, kickstart never runs).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] No `except Exception: pass` blocks — this is a shell-only change. The shell analog (silent `|| true`) is intentional only on the *bootout* (already tolerant) and the *kickstart fallback* (best-effort recovery). The *bootstrap* failure is NOT silently swallowed — it emits a scannable `WARN`/echo line before attempting recovery, so the failure is observable in the update log.

### Empty/Invalid Input Handling
- [ ] `bootstrap_or_kickstart` is called with fixed internal arguments (label/plist paths derived from constants), never user input. No empty/None input path exists. If a plist path is missing, `launchctl bootstrap` fails and the kickstart fallback runs — covered by the failure test.

### Error State Rendering
- [ ] The user-visible output is the `/update` log. Test asserts that a bootstrap failure produces a warning line (observable) rather than a silent abort. The existing `test_remote_update.py::test_already_up_to_date` already guards that `launchd bootstrap error` does not leak on the no-op path.

## Test Impact

- [ ] `tests/integration/test_install_reflection_worker.py::test_self_skip_and_stale_plist_removal` — UNCHANGED: asserts `"launchctl bootout" in installer_src`; the hardened script still contains `launchctl bootout`, so this passes as-is. No update needed.
- [ ] `tests/integration/test_remote_update.py` — UNCHANGED: asserts on `remote-update.sh` (already hardened by #1898), which this plan does not touch. No update needed.
- [ ] `tests/unit/test_update_install_worker.py` — VERIFY: read before build to confirm it does not assert on the exact bare-bootstrap line shape; if it asserts bootstrap presence, the hardened block still contains `launchctl bootstrap`, so it passes. Builder must run it and confirm green.

No existing tests are expected to break — the change is additive (adds a `kickstart` fallback around existing `bootstrap` calls) and preserves the `bootout` + `bootstrap` tokens every current test asserts on. New regression tests are added (see Success Criteria).

## Rabbit Holes

- **Rewriting the whole launchd lifecycle** into a single shared library sourced by all scripts. Tempting for DRY, but the `install_*.sh` scripts are intentionally standalone (runnable on a fresh machine without the venv). Inlining the guarded block is the right scope; a shared lib is a separate refactor.
- **Adding retry loops with sleeps** around bootstrap. The `kickstart -k` fallback already handles the "label still draining" race; adding timed retries invites flakiness and slows every update. Out of scope.
- **Touching the Python `service.py` install helpers.** They already capture output via `run_cmd` and degrade to a WARN — they are NOT the bug. Changing them is scope creep.
- **Chasing the exact machine-specific emitter.** The fix hardens all bare-bootstrap sites uniformly; pinpointing which one fired on "Valor the Bald" is unnecessary — every one is a latent abort.

## Risks

### Risk 1: kickstart -k fallback masks a genuinely broken plist
**Impact:** A malformed plist that fails bootstrap would also fail kickstart, but the script would continue instead of aborting — potentially hiding a real install failure.
**Mitigation:** The bootstrap failure emits a scannable `WARN` line before the fallback, so the failure is visible in the update log (not silent). `plutil -lint` validation already precedes bootstrap in the `install_*.sh` scripts, catching malformed plists earlier. The verify phase of `/update` independently checks that services are actually running.

### Risk 2: Behavioral change on the happy path
**Impact:** If the refactor accidentally alters the success path, healthy installs could regress.
**Mitigation:** On the happy path (bootstrap returns 0) the kickstart branch never executes — behavior is byte-for-byte identical. The regression test asserts the `bootstrap` token is still present. Manual `worker-status`/`status` verification after build confirms services still load.

## Race Conditions

### Race 1: label still draining in gui/<uid> after bootout
**Location:** `scripts/valor-service.sh:389-390`, `:544-545`, `:720-721`; `install_*.sh` bootstrap lines.
**Trigger:** `launchctl bootout` returns before launchd has fully deregistered the label; the immediately-following `bootstrap` sees a still-registered label and returns errno 5.
**Data prerequisite:** none (launchd domain state, not app data).
**State prerequisite:** the label must be fully deregistered before bootstrap can succeed.
**Mitigation:** This is precisely the race being fixed. The `kickstart -k` fallback recovers by kill-restarting the still-loaded service instead of re-bootstrapping, sidestepping the deregistration timing entirely. This is the same mitigation `remote-update.sh:249-260` uses.

## No-Gos (Out of Scope)

- Nothing deferred — every relevant bare-bootstrap call site (`valor-service.sh` ×3, `install_worker.sh` ×2, `install_reflection_worker.sh`, `install_nightly_tests.sh`, `install_email_bridge.sh`, `install_sdlc_reflection.sh`) is in scope for this plan. The Python `service.py` helpers are correctly excluded because they already degrade gracefully via `run_cmd` (not a No-Go — genuinely not part of the bug).

## Update System

This change **is** part of the update system — `scripts/valor-service.sh` and the `install_*.sh` scripts are the launchd install/reload path invoked by `scripts/update/run.py`. No new dependencies or config files. No migration needed: the hardened scripts are picked up automatically on the next `/update` pull (the scripts are code, propagated by `git pull` at the top of `remote-update.sh`). Existing installations self-heal on their next update run — the first hardened run replaces the bare bootstrap with the guarded version.

`scripts/update/migrations.py` needs no entry — no Popoto models or persisted state change.

## Agent Integration

No agent integration required — this is a shell-internal resilience change to the launchd install path. No new CLI entry point, no MCP surface, no bridge import. The agent never invokes `launchctl bootstrap` directly; it reaches these scripts only through `/update`.

## Documentation

### Feature Documentation
- [ ] Add a troubleshooting entry to `.claude/skills/update/references/troubleshooting.md` documenting the `Bootstrap failed: 5: Input/output error` symptom, that it is now auto-recovered via kickstart fallback, and how to verify (`launchctl list | grep com.valor`).
- [ ] Update `docs/features/bridge-self-healing.md` (or the nearest launchd-lifecycle doc) with a one-line note that `valor-service.sh` and the `install_*.sh` scripts use bootout → bootstrap → kickstart-k fallback for idempotent, fail-soft installs.

### Inline Documentation
- [ ] Add a comment on `bootstrap_or_kickstart()` explaining the errno-5 race and referencing issues #1407 and #1898.

[No external docs site in this repo.]

## Success Criteria

- [ ] `scripts/valor-service.sh` has a single `bootstrap_or_kickstart()` helper; all three former bare-bootstrap sites route through it.
- [ ] Every `install_*.sh` script guards its `launchctl bootstrap` with a kickstart -k fallback so a bootstrap failure never aborts the script under `set -e`.
- [ ] A new regression test asserts every hardened script contains a `kickstart` fallback and no un-guarded bare `launchctl bootstrap` remains.
- [ ] `bash -n` syntax check passes on every modified script.
- [ ] Existing `test_install_reflection_worker.py`, `test_remote_update.py`, `test_update_install_worker.py` still pass unchanged.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (shell-hardening)**
  - Name: shell-builder
  - Role: Refactor `valor-service.sh` + `install_*.sh` bootstrap sites; add regression test; add docs.
  - Agent Type: builder
  - Resume: true

- **Validator (shell-hardening)**
  - Name: shell-validator
  - Role: Verify all bootstrap sites hardened, `bash -n` clean, tests green, docs present.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Harden valor-service.sh bootstrap sites
- **Task ID**: build-valor-service
- **Depends On**: none
- **Validates**: tests/unit/test_bootstrap_hardening.py (create), `bash -n scripts/valor-service.sh`
- **Assigned To**: shell-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `bootstrap_or_kickstart()` helper mirroring `remote-update.sh:247-281` semantics.
- Route `bootstrap_plist_idempotent()` (L390), bridge install (L545), and `worker-start` (L721) through it.
- Ensure the helper does not trip `set -e` (capture bootstrap rc; kickstart fallback is `|| true`).

### 2. Harden install_*.sh bootstrap sites
- **Task ID**: build-install-scripts
- **Depends On**: none
- **Validates**: `bash -n` on each script; tests/unit/test_bootstrap_hardening.py (create)
- **Assigned To**: shell-builder
- **Agent Type**: builder
- **Parallel**: true
- Guard `launchctl bootstrap` in `install_worker.sh` (L167, L205), `install_reflection_worker.sh` (L150), `install_nightly_tests.sh` (L101), `install_email_bridge.sh` (L230), `install_sdlc_reflection.sh` (L41) with an `if ! bootstrap; then WARN + kickstart -k; fi` block.
- Keep the preceding `bootout ... || true` and any `plutil -lint` validation.

### 3. Add regression test + docs
- **Task ID**: build-test-docs
- **Depends On**: build-valor-service, build-install-scripts
- **Validates**: `pytest tests/unit/test_bootstrap_hardening.py -q`
- **Assigned To**: shell-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `tests/unit/test_bootstrap_hardening.py`: for each hardened script, assert source contains `kickstart` near a `bootstrap`, and no bare un-guarded `launchctl bootstrap` remains (allow the `if !`-guarded and helper-routed forms).
- Add troubleshooting entry + self-healing doc note + inline comment (see Documentation section).

### 4. Final validation
- **Task ID**: validate-all
- **Depends On**: build-test-docs
- **Assigned To**: shell-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `bash -n` on every modified script; run the new + existing affected tests; verify all success criteria and docs present.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| valor-service.sh syntax | `bash -n scripts/valor-service.sh` | exit code 0 |
| install scripts syntax | `for f in scripts/install_worker.sh scripts/install_reflection_worker.sh scripts/install_nightly_tests.sh scripts/install_email_bridge.sh scripts/install_sdlc_reflection.sh; do bash -n "$f" || exit 1; done` | exit code 0 |
| kickstart fallback present in valor-service.sh | `grep -c 'kickstart -k' scripts/valor-service.sh` | output > 0 |
| kickstart fallback present in every install script | `for f in scripts/install_worker.sh scripts/install_reflection_worker.sh scripts/install_nightly_tests.sh scripts/install_email_bridge.sh scripts/install_sdlc_reflection.sh; do grep -q 'kickstart' "$f" || exit 1; done` | exit code 0 |
| No un-guarded bare bootstrap in install scripts | `grep -rnE '^\s*launchctl bootstrap ' scripts/install_worker.sh scripts/install_reflection_worker.sh scripts/install_nightly_tests.sh scripts/install_email_bridge.sh scripts/install_sdlc_reflection.sh` | exit code 1 |
| Regression test passes | `pytest tests/unit/test_bootstrap_hardening.py -q` | exit code 0 |
| Existing install-worker test passes | `pytest tests/unit/test_update_install_worker.py -q` | exit code 0 |
| Lint clean | `python -m ruff check tests/unit/test_bootstrap_hardening.py` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

None — the root cause is confirmed, the target pattern already exists in-repo (`remote-update.sh` #1898), and the scope is a mechanical propagation of that pattern to the remaining bare-bootstrap call sites.
