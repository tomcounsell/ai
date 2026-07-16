---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-07-16
tracking: https://github.com/tomcounsell/ai/issues/2104
last_comment_id:
revision_applied: false
---

# launchctl_bootstrap_fail_soft — bootstrap retry + opt-in live-PID verification

## Problem

macOS `launchd` services are installed with `launchctl bootstrap gui/<uid> <plist>`,
which can fail transiently with `Bootstrap failed: 5: Input/output error` (errno-5 /
EIO) even when the plist is fine. The repo centralizes recovery in the shared shell
helper `scripts/lib/launchctl.sh::launchctl_bootstrap_fail_soft`, which every launchd
installer calls.

The helper recovers only ONE of the two errno-5 shapes:

- **Drain race (handled):** a `bootout` was just issued and the label is still
  registered/draining when `bootstrap` runs. The helper falls back to
  `kickstart -k gui/<uid>/<label>`, which restarts the already-registered label.
- **Fresh-install transient (NOT handled):** `bootstrap` hits a transient errno-5
  but the label is **not yet registered** in the domain. `kickstart -k` then also
  fails (nothing registered to kick), so the helper prints its WARNING and returns 1.
  Both callers do `... || exit 1`, so the installer exits and the service is left
  **DOWN**.

**Current behavior:**
1. `launchctl bootstrap` → transient errno-5 (label not yet registered).
2. `kickstart -k` → fails (nothing registered).
3. Helper returns 1 → installer `|| exit 1` → service down. No retry of the bootstrap.

Additionally, a `bootstrap` exit 0 does not prove the process actually spawned and
stayed up — a "successful" bootstrap can still leave a resident service with no
running PID.

**Desired outcome:**
1. The helper **retries the bootstrap** on a transient errno-5 with a bounded backoff
   (named, env-overridable constants) **before** falling back to `kickstart -k`. A
   plain re-`bootstrap` is exactly what cleared the 2026-07-15 incident manually.
2. For **resident** services, the helper **verifies a live PID** after a "successful"
   bootstrap/kickstart via `launchctl print gui/<uid>/<label>`, and treats a missing
   PID as a not-yet-live failure that feeds the retry loop.
3. The distinct, greppable `WARNING: launchctl bootstrap+kickstart failed for <label>`
   is preserved and returned non-zero **only** after retries are exhausted.

### Incident (2026-07-15)

A transient errno-5 during `install_reflection_worker.sh` left
`com.valor.reflection-worker` down. Manual recovery was simply re-running
`launchctl bootstrap` — the transient had cleared. A bounded bootstrap retry would
have auto-recovered it.

## Freshness Check

**Baseline commit:** `bc1a311b4`
**Issue filed at:** 2026-07-15T11:07:35Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `scripts/lib/launchctl.sh:38-55` — helper does one bootstrap then one kickstart -k, no retry, no PID check — still holds (verbatim).
- `scripts/install_reflection_worker.sh:153` — `launchctl_bootstrap_fail_soft ... || exit 1` — still holds.
- `scripts/install_worker.sh:174` (worker) + `:212` (watchdog) — `... || exit 1` — still holds.
- `scripts/install_email_bridge.sh:233`, `scripts/install_nightly_tests.sh:104`, `scripts/install_sdlc_reflection.sh:44` — `... || exit 1` — still hold.
- `scripts/valor-service.sh:393` (bootstrap_plist_idempotent), `:549` (bridge install), `:726` (worker-start) — `... || echo WARNING ...` (non-fatal) — still hold.

**Cited sibling issues/PRs re-checked:**
- #2013 / PR #2021 — merged (`29391ddf6`, "Harden shell launchctl bootstrap call sites to be fail-soft"). Introduced the helper.
- #2089 — fail-loud-on-down principle. Present.
- #2018 / #2017 (PR #2017 "Fix worker-restart EIO on stale-worker false-negative") — errno-5 hardening of `remote-update.sh`. Present.

**Commits on main since issue was filed (touching referenced files):** none touching `scripts/lib/launchctl.sh` (last change `29391ddf6` predates the issue).

**Active plans in `docs/plans/` overlapping this area:** `docs/plans/completed/harden-launchctl-bootstrap-fail-soft.md` (the #2013 plan, completed). No active overlapping plan.

## Prior Art

- **PR #2021 (#2013)**: "Harden shell launchctl bootstrap call sites to be fail-soft" — introduced `launchctl_bootstrap_fail_soft` and the bootstrap→kickstart-fallback contract this plan extends. Established the shell-test harness pattern in `tests/unit/test_install_scripts_bootstrap.py` and `tests/unit/test_valor_service_bootstrap.py`.
- **#2089**: "Check launchctl bootstrap exit in install_worker; fail loud when worker down." Established the fail-loud-on-down principle. Its Python sibling `scripts/update/service.py::install_worker` already does live-PID verification (`_launchctl_label_running`, line 458) and a kickstart fallback — but has **no bootstrap retry**, the same gap this plan closes on the shell side.
- **PR #2017 (#2018/#2017)**: hardened `remote-update.sh`'s worker-restart not-loaded branch for the same errno-5 class — the canonical reference pattern.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #2021 (#2013) | Added `kickstart -k` fallback after a failed bootstrap | `kickstart -k` only recovers the drain-race shape (label already registered). It is a no-op for the fresh-install transient (label never registered), so that errno-5 shape still leaves the service down. No retry of the bootstrap itself — the one action that actually cleared the incident. |
| #2089 | Made `install_worker` fail loud + PID-verify | Correctly detects "down" but does not *recover* a transient — it reports the failure rather than retrying the bootstrap that would have cleared it. |

**Root cause pattern:** every prior fix treated errno-5 as a *state* to detect-or-restart-registered-label, never as a *transient* to wait out. The missing primitive is a bounded retry of the bootstrap itself.

## Data Flow

1. **Entry point:** an installer (`install_reflection_worker.sh`, `install_worker.sh`, `valor-service.sh worker-start`, …) or the `/update` flow (`scripts/update/reflection_arm.py` → shells out to `install_reflection_worker.sh`; `scripts/update/service.py::install_worker` → Python reimplementation).
2. **Shell helper:** `launchctl_bootstrap_fail_soft "gui/<uid>" "<plist>" "<label>" [verify-pid]` runs `launchctl bootstrap`.
3. **Transient branch (new):** on errno-5, sleep and re-`bootstrap`, up to `LAUNCHCTL_BOOTSTRAP_RETRIES` times.
4. **Drain-race branch (kept):** if the bootstrap attempts are exhausted with a still-failing bootstrap, fall back to `kickstart -k`.
5. **Verification (new, opt-in):** if `verify-pid` is passed (resident services), run `launchctl print gui/<uid>/<label>` and confirm a `pid = <N>` line; absence feeds the retry loop.
6. **Output:** return 0 once loaded-and-(optionally)-live; else print the distinct WARNING and return 1.

## Architectural Impact

- **New dependencies:** none. Pure shell + one added Python retry loop.
- **Interface changes:** `launchctl_bootstrap_fail_soft` gains an OPTIONAL 4th positional argument `verify-pid` (any non-empty value enables the live-PID probe). Backward compatible — existing 3-arg callers keep the current behavior *minus* the retry improvement (which is unconditional and benefits them too).
- **Coupling:** unchanged. The helper stays the single recovery primitive; fixing it fixes all shell call sites at once.
- **Data ownership:** unchanged.
- **Reversibility:** trivial — revert the helper + the two test files + the `service.py` retry loop.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0-1 (scope confirmation on the `service.py` parity question)
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies. Tests run against stub `launchctl` binaries on PATH; no real launchd services are touched.

## Solution

### Key Elements

- **Bounded bootstrap retry (shell):** wrap the `bootstrap` attempt in a retry loop keyed on transient errno-5 (`5: Input/output error` in captured stderr). Sleep `LAUNCHCTL_BOOTSTRAP_RETRY_SLEEP` seconds between attempts, up to `LAUNCHCTL_BOOTSTRAP_RETRIES` attempts. A non-EIO bootstrap failure breaks out immediately to the kickstart fallback (do not burn retries on a genuine plist error).
- **Opt-in live-PID verification (shell):** a 4th arg `verify-pid`. When set, after any path reports load-success, run `launchctl print gui/<uid>/<label>` and require a `pid = <N>` line. A missing PID is treated as not-yet-live and feeds the same retry loop. **Only resident services pass it.**
- **Preserved fail-loud contract:** the distinct `WARNING: launchctl bootstrap+kickstart failed for <label>` line and non-zero return remain, emitted only on genuine exhaustion.
- **Python parity (`service.py::install_worker`):** add the same bounded retry loop around its `launchctl bootstrap` call (it already has PID verification + kickstart fallback; it only lacks the retry). Reuse the same env-var constant names so shell and Python share one tunable contract.

### Resident vs scheduled (the load-bearing constraint)

| Service | plist trigger | Resident? | Passes `verify-pid`? |
|---|---|---|---|
| `com.valor.worker` | RunAtLoad + KeepAlive | yes | yes |
| `com.valor.worker-watchdog` | RunAtLoad + KeepAlive | yes | yes |
| `com.valor.reflection-worker` | RunAtLoad + KeepAlive | yes | yes |
| `com.valor.email-bridge` | RunAtLoad + KeepAlive | yes | yes |
| `com.valor.bridge` | RunAtLoad + KeepAlive | yes | yes |
| `com.valor.nightly-tests` | StartCalendarInterval | no | no |
| `com.valor.sdlc-reflection` | StartInterval | no | no |
| `com.valor.update` (update-cron) | StartInterval | no | no |
| log-rotate agent | StartInterval | no | no |

Blanket PID verification would falsely fail every scheduled service (they have no
persistent PID between runs). Hence `verify-pid` is opt-in per call site.

### Flow

Installer → `launchctl_bootstrap_fail_soft dom plist label [verify-pid]` → bootstrap →
(errno-5? sleep+retry ×N) → (still failing? kickstart -k) → (verify-pid? require live PID) →
return 0 loaded/live, or WARNING + return 1 on exhaustion.

### Technical Approach

- **Constants (grain-of-salt, provisional/tunable):**
  - `LAUNCHCTL_BOOTSTRAP_RETRIES` (default `3`) — total bootstrap attempts on transient EIO.
  - `LAUNCHCTL_BOOTSTRAP_RETRY_SLEEP` (default `2`) — seconds between attempts.
  - Shell: `local retries="${LAUNCHCTL_BOOTSTRAP_RETRIES:-3}"`. Python: read the same env keys with the same defaults, module-level, with a `# provisional/tunable` comment.
- **errno-5 detection:** capture `bootstrap` stderr (`err=$(launchctl bootstrap ... 2>&1)`); match `*"5: Input/output error"*`. Only that shape retries; other failures fall straight to kickstart.
- **Live-PID probe:** `launchctl print "gui/$(id -u)/$label" 2>/dev/null | grep -Eq '^[[:space:]]*pid = [0-9]+'`. A registered-but-not-running label prints no `pid =` line → treated as not-live.
- **Call-site wiring:** append `verify-pid` at the resident sites only:
  - `install_worker.sh:174` (worker) and `:212` (watchdog) — add `verify-pid`.
  - `install_reflection_worker.sh:153` — add `verify-pid`.
  - `install_email_bridge.sh:233` — add `verify-pid`.
  - `valor-service.sh:549` (bridge install) and `:726` (worker-start) — add `verify-pid`.
  - `valor-service.sh:393` (`bootstrap_plist_idempotent`) — hit for both update-cron (scheduled) and bridge-watchdog (resident). Thread a `verify-pid` argument through `bootstrap_plist_idempotent`'s signature so the watchdog caller (`:631`) opts in and the update-cron caller (`:592`) does not.
  - `install_nightly_tests.sh:104` and `install_sdlc_reflection.sh:44` — leave 3-arg (no PID check).
- **No behavioral change for scheduled sites** beyond the (safe, beneficial) unconditional bootstrap retry.

## Failure Path Test Strategy

### Exception Handling Coverage
- No `except Exception: pass` blocks are added. The shell helper's failure path already emits the greppable WARNING to stderr and returns non-zero — asserted by the double-failure tests.
- The `service.py::install_worker` retry loop logs each retry via `logger.warning` (observable); primary coverage is the shell harness plus the existing `service.py` return-value assertions.

### Empty/Invalid Input Handling
- Missing 4th arg → PID verification is skipped (documented default). Covered by the scheduled-script happy-path tests (no `print` call emitted).
- `launchctl print` emitting no `pid =` line (registered but not running) → treated as not-live and retried, then WARNING. Covered by a new `PRINT_NO_PID` stub knob.

### Error State Rendering
- The distinct `WARNING: launchctl bootstrap+kickstart failed for <label>` line must still reach stderr on genuine exhaustion — asserted by the updated double-failure tests and a new PID-verification-exhaustion test.

## Test Impact

- [ ] `tests/unit/test_install_scripts_bootstrap.py` — UPDATE: extend `LAUNCHCTL_STUB` with a `print` case emitting `pid = 4242` (and a `PRINT_NO_PID` knob to suppress it) and a `BOOTSTRAP_FAIL_TIMES` knob so a transient can clear after N attempts. `test_recover_via_kickstart` asserts exactly `len(labels)` bootstrap calls — UPDATE to account for the retry loop (permanent `BOOTSTRAP_FAIL` now yields `len(labels) * LAUNCHCTL_BOOTSTRAP_RETRIES` bootstrap calls before kickstart). Set `LAUNCHCTL_BOOTSTRAP_RETRY_SLEEP=0` in test env to keep runs fast.
- [ ] `tests/unit/test_install_scripts_bootstrap.py` — UPDATE (new cases): `test_retry_then_succeed` (transient clears on attempt 2, no kickstart, no WARNING) and `test_pid_verification_failure_warns` (resident script: bootstrap rc 0 but `print` shows no PID → retries exhausted → WARNING + non-zero). Scheduled scripts must NOT emit a `print` call (assert absence).
- [ ] `tests/unit/test_valor_service_bootstrap.py` — UPDATE: same `LAUNCHCTL_STUB` extensions (`print` + `pid =`, `PRINT_NO_PID`, `BOOTSTRAP_FAIL_TIMES`). `test_*_recover_via_kickstart` bootstrap-count assertions — UPDATE for the retry loop. Add a worker-start retry-then-succeed case and a bridge-watchdog PID-verification case; assert `bootstrap_plist_idempotent`'s update-cron label does NOT trigger a `print` probe while the watchdog label does. Set `LAUNCHCTL_BOOTSTRAP_RETRY_SLEEP=0`.
- [ ] No test file is DELETED or REPLACED — all changes are extensions of the two existing harnesses.

## Rabbit Holes

- **Do NOT** rewrite the installers' overall structure or the `bootstrap_plist_idempotent` idempotency logic — thread one argument, nothing more.
- **Do NOT** add retry to the `kickstart -k` fallback — the drain race is a single-shot recovery; retrying it adds latency without new recovery power.
- **Do NOT** try to parse full `launchctl print` output beyond the `pid =` line — that block's format is large and version-dependent; the `pid = <N>` line is the stable liveness signal.
- **Do NOT** attempt to make PID verification "smart" about scheduled services by reading the plist — opt-in per call site is simpler and unambiguous.
- **Do NOT** promote the retry constants into `config/settings.py`'s `TimeoutSettings` — these are shell-level install-time knobs, not runtime Python timeouts; env-overridable shell/module constants are the right altitude (name-locally criterion).

## Risks

### Risk 1: Retry loop masks a genuine plist error behind N sleeps
**Impact:** a real (non-transient) bootstrap failure would take `RETRIES × SLEEP` extra seconds before surfacing.
**Mitigation:** only errno-5 (`5: Input/output error`) stderr retries; any other bootstrap failure breaks immediately to the kickstart fallback. Defaults (3 × 2s) cap the worst case at ~6s.

### Risk 2: Live-PID probe races a slow-spawning resident process
**Impact:** a resident service that is slow to fork could show no PID on the first `print`, causing an unnecessary retry.
**Mitigation:** the retry loop's sleep (2s) gives launchd time to spawn; a missing PID simply triggers one more bounded attempt, then WARNING. This is strictly safer than today's "bootstrap rc 0 = success" assumption.

### Risk 3: Shell/Python constant drift
**Impact:** the `service.py` retry could diverge from the shell helper's tunables.
**Mitigation:** both read the same env-var names (`LAUNCHCTL_BOOTSTRAP_RETRIES`, `LAUNCHCTL_BOOTSTRAP_RETRY_SLEEP`) with identical defaults; documented together in the feature doc.

## Race Conditions

### Race 1: bootstrap rc 0 before the process is actually live
**Location:** `scripts/lib/launchctl.sh` (post-bootstrap), `scripts/update/service.py::install_worker`.
**Trigger:** `launchctl bootstrap` returns 0 the instant the plist is loaded, before launchd has spawned/settled the resident process.
**Data prerequisite:** the resident label must have a running PID before the installer reports success.
**State prerequisite:** launchd has completed the fork for RunAtLoad services.
**Mitigation:** the opt-in `launchctl print … | grep pid =` probe re-reads liveness after a bounded sleep; a not-yet-live read triggers one more attempt rather than a false success. This is exactly the race the fix closes.

## No-Gos (Out of Scope)

Nothing deferred — every relevant item (shell helper retry + PID verify, all
call-site wiring, Python `service.py::install_worker` retry parity, both test
harnesses, and the feature doc) is in scope for this plan. The one judgment call
(including the `service.py` retry rather than filing it separately) is resolved
in-scope: that path is what `/update` runs for the worker, and leaving it retry-less
while the shell path gains retry would be a half-fix (NO HALF-MIGRATION principle).

## Update System

**Load-bearing.** This change touches launchd install/update wiring that the `/update` flow runs:

- `scripts/update/reflection_arm.py` shells out to `install_reflection_worker.sh` (the incident path) — automatically picks up the hardened shell helper; **no change needed there**.
- `scripts/update/service.py::install_worker` is a Python reimplementation of the worker install used by `/update`; it already has PID verification + kickstart fallback but **lacks the bootstrap retry** — this plan adds the retry loop there for parity (in scope).
- No new dependencies, config files, or migration steps. The env-overridable constants default sensibly when unset, so existing installations need no migration.
- `scripts/remote-update.sh` itself is unchanged (its worker-restart errno-5 branch was already hardened by #2017/#2018).

## Agent Integration

No agent integration required — this is install/update-time shell + build-tooling infrastructure. There is no MCP surface, no `.mcp.json` change, and no `bridge/telegram_bridge.py` import. The helper is invoked only by launchd installers and the update flow, never by the agent at runtime.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/launchctl-bootstrap-fail-soft.md` documenting the helper's contract: the two errno-5 shapes, the bounded bootstrap retry, opt-in live-PID verification (resident vs scheduled table), the `LAUNCHCTL_BOOTSTRAP_RETRIES` / `LAUNCHCTL_BOOTSTRAP_RETRY_SLEEP` env knobs, and the fail-loud WARNING contract.
- [ ] Add entry to `docs/features/README.md` index table.

### Inline Documentation
- [ ] Update the header comment block in `scripts/lib/launchctl.sh` to document the retry loop, the 4th `verify-pid` arg, and the resident-vs-scheduled opt-in rule.
- [ ] Add the `# provisional/tunable` grain-of-salt comment on the constants in both the shell helper and `service.py`.

## Success Criteria

- [ ] `launchctl_bootstrap_fail_soft` retries the bootstrap on transient errno-5 up to `LAUNCHCTL_BOOTSTRAP_RETRIES` attempts before falling back to `kickstart -k`.
- [ ] With `verify-pid` set, the helper requires a live `pid = <N>` from `launchctl print` before returning 0; a missing PID feeds the retry loop and ultimately the WARNING.
- [ ] Resident call sites (worker, watchdog, reflection-worker, email-bridge, bridge, worker-start) pass `verify-pid`; scheduled call sites (nightly-tests, sdlc-reflection, update-cron) do not.
- [ ] `service.py::install_worker` gains the same bounded bootstrap retry (shares env-var constant names).
- [ ] The distinct `WARNING: launchctl bootstrap+kickstart failed for <label>` line is preserved and returned non-zero only on genuine exhaustion.
- [ ] Both shell-test harnesses updated and green; new retry-then-succeed and PID-verification-failure cases pass.
- [ ] Tests pass (`/do-test`) — `tests/unit/test_install_scripts_bootstrap.py`, `tests/unit/test_valor_service_bootstrap.py`.
- [ ] Documentation updated (`/do-docs`).

## Team Orchestration

Single builder + code reviewer. The change is one shell helper, one Python function, six call-site edits, and two test files — small enough for one focused builder, gated by review.

### Team Members

- **Builder (launchctl-helper)**
  - Name: launchctl-builder
  - Role: harden the shell helper, wire resident call sites, add `service.py` retry parity, update both test harnesses, write the feature doc
  - Agent Type: builder
  - Resume: true

- **Reviewer (launchctl-helper)**
  - Name: launchctl-reviewer
  - Role: verify the errno-5-only retry gating, resident-vs-scheduled opt-in correctness, WARNING preservation, and test coverage of retry + PID paths
  - Agent Type: code-reviewer
  - Resume: true

### Available Agent Types

builder, code-reviewer, documentarian, validator.

## Step by Step Tasks

### 1. Harden the shell helper
- **Task ID**: build-helper
- **Depends On**: none
- **Validates**: `tests/unit/test_install_scripts_bootstrap.py`, `tests/unit/test_valor_service_bootstrap.py`
- **Assigned To**: launchctl-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `LAUNCHCTL_BOOTSTRAP_RETRIES` / `LAUNCHCTL_BOOTSTRAP_RETRY_SLEEP` env-overridable constants (grain-of-salt comment).
- Wrap `bootstrap` in a bounded retry loop gated on `5: Input/output error` stderr; non-EIO failures break to kickstart.
- Add optional 4th arg `verify-pid`; when set, require a `pid = <N>` line from `launchctl print` and feed a missing PID back into the retry loop.
- Preserve the exact WARNING line + non-zero return on exhaustion.
- Update the header comment block.

### 2. Wire call sites (resident opt-in, scheduled unchanged)
- **Task ID**: build-callsites
- **Depends On**: build-helper
- **Assigned To**: launchctl-builder
- **Agent Type**: builder
- **Parallel**: false
- Append `verify-pid` at `install_worker.sh:174` + `:212`, `install_reflection_worker.sh:153`, `install_email_bridge.sh:233`, `valor-service.sh:549` + `:726`.
- Thread a `verify-pid` argument through `bootstrap_plist_idempotent` so the watchdog caller (`:631`) opts in and the update-cron caller (`:592`) does not.
- Leave `install_nightly_tests.sh:104` and `install_sdlc_reflection.sh:44` as 3-arg.

### 3. Python parity in service.py
- **Task ID**: build-service-py
- **Depends On**: build-helper
- **Assigned To**: launchctl-builder
- **Agent Type**: builder
- **Parallel**: false
- Add the bounded bootstrap retry loop around `install_worker`'s `launchctl bootstrap` call, reusing the same env-var constant names/defaults with a `# provisional/tunable` comment.
- Keep the existing `_launchctl_label_running` PID check and kickstart fallback.

### 4. Update both test harnesses
- **Task ID**: build-tests
- **Depends On**: build-helper, build-callsites
- **Validates**: `tests/unit/test_install_scripts_bootstrap.py`, `tests/unit/test_valor_service_bootstrap.py`
- **Assigned To**: launchctl-builder
- **Agent Type**: builder
- **Parallel**: false
- Extend `LAUNCHCTL_STUB` in both files: `print` case emitting `pid = 4242`, a `PRINT_NO_PID` knob, a `BOOTSTRAP_FAIL_TIMES` knob for transient-then-clear.
- Update the `recover_via_kickstart` bootstrap-count assertions for the retry loop; set `LAUNCHCTL_BOOTSTRAP_RETRY_SLEEP=0` in test env.
- Add `test_retry_then_succeed` and `test_pid_verification_failure_warns`; assert scheduled scripts emit no `print` probe and resident scripts do.

### 5. Feature documentation
- **Task ID**: document-feature
- **Depends On**: build-helper, build-callsites, build-service-py
- **Assigned To**: launchctl-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/launchctl-bootstrap-fail-soft.md`; add the `docs/features/README.md` index entry.

### 6. Review
- **Task ID**: review-all
- **Depends On**: build-tests, document-feature
- **Assigned To**: launchctl-reviewer
- **Agent Type**: code-reviewer
- **Parallel**: false
- Verify errno-5-only retry gating, resident-vs-scheduled opt-in, WARNING preservation, and that retry + PID paths are actually exercised by tests.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Helper tests pass | `scripts/pytest-clean.sh tests/unit/test_install_scripts_bootstrap.py tests/unit/test_valor_service_bootstrap.py -q` | exit code 0 |
| Retry constant present | `grep -c 'LAUNCHCTL_BOOTSTRAP_RETRIES' scripts/lib/launchctl.sh` | output > 0 |
| verify-pid probe present | `grep -c 'launchctl print' scripts/lib/launchctl.sh` | output > 0 |
| WARNING line preserved | `grep -c 'WARNING: launchctl bootstrap+kickstart failed' scripts/lib/launchctl.sh` | output > 0 |
| Scheduled sites stay 3-arg | `grep -E 'launchctl_bootstrap_fail_soft .*verify-pid' scripts/install_nightly_tests.sh scripts/install_sdlc_reflection.sh` | exit code 1 |
| service.py retry parity | `grep -c 'LAUNCHCTL_BOOTSTRAP_RETRIES' scripts/update/service.py` | output > 0 |
| Feature doc exists | `test -f docs/features/launchctl-bootstrap-fail-soft.md` | exit code 0 |
| Lint clean | `python -m ruff check scripts/update/service.py` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

None blocking. The one judgment call — including the `service.py::install_worker`
retry parity in this plan rather than deferring it — is resolved in-scope because
that path is what `/update` runs for the worker and leaving it retry-less while the
shell path gains retry would be a half-fix (NO HALF-MIGRATION principle).
