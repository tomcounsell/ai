---
status: Ready
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-07-16
tracking: https://github.com/tomcounsell/ai/issues/2104
last_comment_id:
revision_applied: true
revision_applied_at: 2026-07-16T03:20:09Z
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
3. **Transient branch (new, loop A):** on errno-5, sleep and re-`bootstrap`, up to `LAUNCHCTL_BOOTSTRAP_RETRIES` times. A non-EIO failure exits loop A immediately.
4. **Drain-race branch (kept):** if loop A exhausts with a still-failing bootstrap (or exits on a non-EIO failure), fall back to a single `kickstart -k`.
5. **Verification (new, opt-in, loop B — independent of loop A):** if `verify-pid` is passed (resident services), run a separate bounded loop that re-runs `launchctl print gui/<uid>/<label>` and checks for a `pid = <N>` line, sleeping between attempts. This loop never re-invokes bootstrap/kickstart.
6. **Output:** return 0 once loaded and (if `verify-pid`) live; else print the distinct WARNING and return 1 when either loop exhausts.

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
- **Opt-in live-PID verification (shell) — a SEPARATE bounded probe loop, not the bootstrap-retry loop:** a 4th arg `verify-pid`. When set, after the bootstrap/kickstart block reports load-success, run a *distinct* bounded probe loop that repeatedly runs `launchctl print gui/<uid>/<label>` and checks for a `pid = <N>` line, sleeping `LAUNCHCTL_BOOTSTRAP_RETRY_SLEEP` between attempts, up to `LAUNCHCTL_BOOTSTRAP_RETRIES` attempts. This probe **does NOT re-invoke `bootstrap` or `kickstart`** — the label is already registered, so re-bootstrapping cannot reproduce errno-5 and re-kickstarting is explicitly forbidden (drain-race single-shot). The two loops are independent: (a) errno-5 bootstrap-retry loop before load, (b) PID-wait probe loop after load. Only resident services pass `verify-pid`.
- **Preserved fail-loud contract:** the distinct `WARNING: launchctl bootstrap+kickstart failed for <label>` line and non-zero return remain, emitted only on genuine exhaustion (either loop exhausting).
- **Python parity (`service.py::install_worker`) — bootstrap retry ONLY:** add the same bounded, **errno-5-gated** retry loop around its `launchctl bootstrap` call (it already has a single-shot `_launchctl_label_running` PID check + kickstart fallback; it only lacks the retry). The Python PID check remains single-shot by design — parity covers the bootstrap retry, not the PID re-probe. Reuse the same env-var constant names so shell and Python share one tunable contract.

### Resident vs scheduled (the load-bearing constraint)

> **Build correction (authoritative — supersedes the original draft of this table).**
> During BUILD both watchdogs were verified to be *scheduled* one-shots, NOT resident:
> `com.valor.worker-watchdog` is `StartInterval 300` (heredoc in `install_worker.sh`) and
> `com.valor.bridge-watchdog` is `StartInterval 60` (heredoc in `valor-service.sh`) — neither
> has `RunAtLoad`/`KeepAlive`, so neither holds a persistent PID. Passing `verify-pid` to them
> would exhaust loop B, emit a spurious WARNING, and at the `|| exit 1` watchdog install site
> abort a real install. The wiring below reflects the corrected reality: `verify-pid` goes only
> to the 5 genuinely-resident sites, and `bootstrap_plist_idempotent` stays 3-arg (neither of
> its callers — update-cron, bridge-watchdog — is resident).

| Service | plist trigger | Resident? | Passes `verify-pid`? |
|---|---|---|---|
| `com.valor.worker` | RunAtLoad + KeepAlive | yes | yes |
| `com.valor.reflection-worker` | RunAtLoad + KeepAlive | yes | yes |
| `com.valor.email-bridge` | RunAtLoad + KeepAlive | yes | yes |
| `com.valor.bridge` | RunAtLoad + KeepAlive | yes | yes |
| `com.valor.worker` (via `worker-start`) | RunAtLoad + KeepAlive | yes | yes |
| `com.valor.worker-watchdog` | StartInterval 300 | no | no |
| `com.valor.bridge-watchdog` | StartInterval 60 | no | no |
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
- **errno-5 detection (both shell and Python):** capture `bootstrap` stderr (shell: `err=$(launchctl bootstrap ... 2>&1)`, match `*"5: Input/output error"*`; Python: `"5: Input/output error" in (bootstrap.stderr or "")`). Only that shape retries; any other non-zero failure falls straight to `kickstart -k` immediately (do not burn `RETRIES × SLEEP` on a genuine plist error). This gating is identical in the shell helper and `service.py::install_worker`.
- **Live-PID probe (shell) — bounded probe loop, no re-bootstrap:**
  ```sh
  for i in $(seq 1 "$retries"); do
      launchctl print "gui/$(id -u)/$label" 2>/dev/null | grep -Eq '^[[:space:]]*pid = [0-9]+' && return 0
      sleep "$sleep"
  done
  # fall through to WARNING + return 1
  ```
  A registered-but-not-running label prints no `pid =` line → the loop re-probes (never re-bootstraps/re-kickstarts), then WARNs on exhaustion.
- **Call-site wiring (corrected per the build-correction note above):** append `verify-pid` at the 5 genuinely-resident sites only:
  - `install_worker.sh:174` (worker) — add `verify-pid`. Its `:212` **watchdog** site stays 3-arg (worker-watchdog is `StartInterval 300` — scheduled, no PID).
  - `install_reflection_worker.sh:153` — add `verify-pid`.
  - `install_email_bridge.sh:233` — add `verify-pid`.
  - `valor-service.sh:549` (bridge install) and `:726` (worker-start) — add `verify-pid`.
  - `valor-service.sh:393` (`bootstrap_plist_idempotent`) — stays **3-arg**. Neither of its callers is resident: update-cron (`:592`) and bridge-watchdog (`:631`, `StartInterval 60`) are both scheduled. No dead `verify-pid` plumbing is threaded through its signature.
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
- [ ] `tests/unit/test_valor_service_bootstrap.py` — UPDATE: same `LAUNCHCTL_STUB` extensions (`print` + `pid =`, `PRINT_NO_PID`, `BOOTSTRAP_FAIL_TIMES`). `test_*_recover_via_kickstart` bootstrap-count assertions — UPDATE for the retry loop. Add worker-start retry-then-succeed and PID-verification cases. Per the build correction, `bootstrap_plist_idempotent` stays 3-arg (both callers scheduled), so NO call site under it triggers a `print` probe. Set `LAUNCHCTL_BOOTSTRAP_RETRY_SLEEP=0`.
- [ ] No test file is DELETED or REPLACED — all changes are extensions of the two existing harnesses.

## Rabbit Holes

- **Do NOT** rewrite the installers' overall structure or the `bootstrap_plist_idempotent` idempotency logic — thread one argument, nothing more.
- **Do NOT** add retry to the `kickstart -k` fallback — the drain race is a single-shot recovery; retrying it adds latency without new recovery power.
- **Do NOT** try to parse full `launchctl print` output beyond the `pid =` line — that block's format is large and version-dependent; the `pid = <N>` line is the stable liveness signal.
- **Do NOT** attempt to make PID verification "smart" about scheduled services by reading the plist — opt-in per call site is simpler and unambiguous.
- **Do NOT** promote the retry constants into `config/settings.py`'s `TimeoutSettings` — these are shell-level install-time knobs, not runtime Python timeouts; env-overridable shell/module constants are the right altitude (name-locally criterion).

## Risks

### Risk 1: Retry loop masks a genuine plist error behind N sleeps
**Impact:** a real (non-transient) bootstrap failure would take `RETRIES × SLEEP` extra seconds before surfacing. Per call site the worst case is ~6s (3 × 2s); a single `/update` run touches ~6 resident call sites, so the cumulative worst-case added latency on a genuine failure is closer to ~36s.
**Mitigation:** only errno-5 (`5: Input/output error`) stderr retries; any other bootstrap failure breaks immediately to the kickstart fallback, so a genuine plist error surfaces on the first attempt (no added latency). The ~36s cumulative bound applies only to the pathological case of a persistent EIO across every resident service — an already-degraded machine where the extra seconds are immaterial. `LAUNCHCTL_BOOTSTRAP_RETRY_SLEEP` is env-tunable to 0 for latency-sensitive runs (and tests set it to 0).

### Risk 2: Live-PID probe races a slow-spawning resident process
**Impact:** a resident service that is slow to fork could show no PID on the first `print`, causing an unnecessary retry.
**Mitigation:** the dedicated PID-wait probe loop (loop B) sleeps between attempts, giving launchd time to spawn; a missing PID simply triggers one more `launchctl print` re-probe (never a re-bootstrap/re-kickstart), then WARNING on exhaustion. This is strictly safer than today's "bootstrap rc 0 = success" assumption.

### Risk 3: Shell/Python constant drift and probe-method divergence
**Impact:** the `service.py` retry could diverge from the shell helper's tunables; and the two live-PID probes use different mechanisms (shell: `launchctl print … | grep 'pid = <N>'`; Python `_launchctl_label_running`: `launchctl list` + PID-column check).
**Mitigation:** both read the same env-var names (`LAUNCHCTL_BOOTSTRAP_RETRIES`, `LAUNCHCTL_BOOTSTRAP_RETRY_SLEEP`) with identical defaults; documented together in the feature doc. The two probe mechanisms are an **accepted, documented divergence** — they are independently maintained and are NOT required to match line-for-line; only the `LAUNCHCTL_BOOTSTRAP_*` env-var names/defaults must stay in sync. (The shell probe is a bounded re-probe loop; the Python probe stays single-shot by design — see the parity note in Task 3.)

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

- [x] `launchctl_bootstrap_fail_soft` retries the bootstrap (loop A) on transient errno-5 up to `LAUNCHCTL_BOOTSTRAP_RETRIES` attempts before falling back to a single `kickstart -k`; non-EIO failures skip the retry.
- [x] With `verify-pid` set, the helper runs a SEPARATE bounded PID-wait probe loop (loop B) that re-runs `launchctl print` (never re-bootstraps/re-kickstarts) and requires a live `pid = <N>` before returning 0; exhaustion emits the WARNING.
- [x] Resident call sites (worker, reflection-worker, email-bridge, bridge, worker-start) pass `verify-pid`; scheduled call sites (BOTH watchdogs, nightly-tests, sdlc-reflection, update-cron) do not; `bootstrap_plist_idempotent` stays 3-arg.
- [x] `service.py::install_worker` gains the same bounded, errno-5-gated bootstrap retry (shares env-var constant names); its PID check stays single-shot by design (bootstrap-retry parity only).
- [x] The distinct `WARNING: launchctl bootstrap+kickstart failed for <label>` line is preserved and returned non-zero only on genuine exhaustion.
- [x] Both shell-test harnesses updated and green; new retry-then-succeed and PID-verification-failure cases pass.
- [x] Tests pass (`/do-test`) — `tests/unit/test_install_scripts_bootstrap.py`, `tests/unit/test_valor_service_bootstrap.py`.
- [x] Documentation updated (`/do-docs`).

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
- **Loop A (before load):** wrap `bootstrap` in a bounded retry loop gated on `5: Input/output error` stderr; non-EIO failures break out immediately to the single `kickstart -k` fallback.
- Add optional 4th arg `verify-pid`. When set, after the bootstrap/kickstart block reports load-success, run **Loop B (after load):** a SEPARATE bounded probe loop that re-runs `launchctl print gui/<uid>/<label>` + greps for `pid = <N>`, sleeping between attempts. Loop B must NOT re-invoke `bootstrap` or `kickstart` (see BLOCKER resolution — the two loops are independent).
- Preserve the exact WARNING line + non-zero return on exhaustion of either loop.
- Update the header comment block to document both loops, the 4th arg, and the resident-vs-scheduled opt-in rule.
- **Commit split:** land Loop A (bootstrap retry) as commit 1 and the `verify-pid` 4th arg + Loop B as commit 2, so the proven incident fix is separately reviewable from the hardening (both in the one PR).

### 2. Wire call sites (resident opt-in, scheduled unchanged)
- **Task ID**: build-callsites
- **Depends On**: build-helper
- **Assigned To**: launchctl-builder
- **Agent Type**: builder
- **Parallel**: false
- Append `verify-pid` at `install_worker.sh:174` + `:212`, `install_reflection_worker.sh:153`, `install_email_bridge.sh:233`, `valor-service.sh:549` + `:726`.
- Leave `bootstrap_plist_idempotent` 3-arg — both its callers (update-cron `:592`, bridge-watchdog `:631`) are scheduled `StartInterval` jobs with no persistent PID, so no `verify-pid` plumbing is threaded through its signature.
- Leave `install_nightly_tests.sh:104` and `install_sdlc_reflection.sh:44` as 3-arg.

### 3. Python parity in service.py
- **Task ID**: build-service-py
- **Depends On**: build-helper
- **Assigned To**: launchctl-builder
- **Agent Type**: builder
- **Parallel**: false
- Add the bounded, **errno-5-gated** bootstrap retry loop around `install_worker`'s `launchctl bootstrap` call — gate on `"5: Input/output error" in (bootstrap.stderr or "")`; on a non-EIO non-zero rc, break immediately to the existing `kickstart -k` fallback (do not retry). Reuse the same env-var constant names/defaults with a `# provisional/tunable` comment.
- Keep the existing `_launchctl_label_running` PID check and kickstart fallback. **Parity is bootstrap-retry only:** the Python PID check remains single-shot by design (no bounded re-probe loop) — state this in the code comment and it is reflected in Success Criteria.

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

<!-- Populated by /do-plan-critique (war room). -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness + History | Missing-PID path collided with errno-5-only-retry + no-kickstart-retry constraints | Split into two independent bounded loops: loop A (errno-5 bootstrap retry, before load) + loop B (PID-wait re-probe via `launchctl print`, after load, no re-bootstrap/kickstart) | Solution/Data Flow/Task 1 rewritten; loop B never re-enters bootstrap/kickstart |
| CONCERN | Risk & Robustness | service.py retry gating unspecified (any-failure vs errno-5) | Task 3 + Technical Approach now specify errno-5-gated retry, non-EIO → immediate kickstart | `"5: Input/output error" in (bootstrap.stderr or "")` |
| CONCERN | History & Consistency | "Parity" overstated; Python PID check stays single-shot | Scoped parity to "bootstrap retry only"; Python PID check single-shot by design, stated in Task 3 + Success Criteria | — |
| CONCERN | History & Consistency | Probe-method divergence (print+grep vs list+PID-column) unacknowledged | Added to Risk 3 as accepted, documented divergence; only env-var names must stay in sync | — |
| CONCERN | Scope & Value | retry (proven fix) + PID-verify (hardening) bundled | Kept together, land as two commits (Task 1 commit split) so incident fix is separately reviewable | Commit 1 = loop A; commit 2 = verify-pid + loop B |
| NIT | Risk & Robustness | Risk 1 latency understated cumulative (~36s across ~6 sites) | Risk 1 updated with ~36s cumulative bound + tunable sleep note | — |

---

## Open Questions

None blocking. The one judgment call — including the `service.py::install_worker`
retry parity in this plan rather than deferring it — is resolved in-scope because
that path is what `/update` runs for the worker and leaving it retry-less while the
shell path gains retry would be a half-fix (NO HALF-MIGRATION principle).
