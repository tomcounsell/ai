---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-11
tracking: https://github.com/tomcounsell/ai/issues/2013
last_comment_id:
---

# Harden shell `launchctl bootstrap` call sites to be fail-soft

## Problem

A `/update` run on machine "Valor the Bald" aborted mid-service-install with:

```
Bootstrap failed: 5: Input/output error
Try re-running the command as root for richer errors.
```

The run terminated with no further `[update]` output — a hard abort. `errno 5`
(`Input/output error`) from `launchctl bootstrap` is the known macOS launchd race:
the service label is still registered/draining in the `gui/<uid>/` domain when
`bootstrap` runs (immediately after a `bootout`, or a stale half-load from a prior
crash). launchd refuses and returns errno 5.

`scripts/valor-service.sh` runs under `set -e` and has **bare** `launchctl bootstrap`
calls with no error tolerance and no kickstart fallback. Under `set -e`, a single
transient errno 5 aborts the whole `valor-service.sh install`/`restart`/`worker-start`
invocation — which `/update` calls via `scripts/update/service.py`. The `install_*.sh`
helper scripts share the identical bare-bootstrap shape and are latently exposed to the
same abort.

**Current behavior:** A transient launchd errno 5 on any bare `launchctl bootstrap`
aborts the entire update/service-install run, leaving remaining services uninstalled
and the machine on stale code.

**Desired outcome:** Every shell `launchctl bootstrap` call recovers from a transient
errno 5 via a `kickstart -k` fallback (the service ends up loaded), and a genuine
double-failure surfaces a distinct warning without hard-aborting the whole install —
matching the hardened pattern already established in `remote-update.sh` and `service.py`.

## Freshness Check

**Baseline commit:** `3859f490` (`git rev-parse HEAD` at plan time)
**Issue filed at:** 2026-07-11T00:41:50Z
**Disposition:** Minor drift (a sibling PR landed in the same problem space; the specific
call sites this issue names are untouched and still present).

**File:line references re-verified (all exact against `3859f490`):**
- `scripts/valor-service.sh:6` — `set -e` header — still holds.
- `scripts/valor-service.sh:390` — `bootstrap_plist_idempotent()`: `launchctl bootout ... 2>/dev/null || true` then bare `launchctl bootstrap "gui/$(id -u)" "$plist_path"` — still holds. Called by update-cron install (~L587) and watchdog install (~L626).
- `scripts/valor-service.sh:545` — bridge install: `stop_bridge` then bare `launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"` — still holds.
- `scripts/valor-service.sh:721` — `worker-start`: `launchctl bootout ... 2>/dev/null || true` then bare `launchctl bootstrap "gui/$(id -u)" "$WORKER_PLIST_PATH"` (inside `if ! is_worker_launchd_loaded`) — still holds.
- `scripts/install_worker.sh:167` — bare bootstrap under "# Load new version" — still holds. `:205` (watchdog) has a `bootout ... || true` immediately before its bare bootstrap; still bare bootstrap, still exposed.
- `scripts/install_reflection_worker.sh:150` — bare bootstrap — still holds.
- `scripts/install_nightly_tests.sh:101` — bare bootstrap (script runs under `set -euo pipefail`) — still holds.
- `scripts/install_email_bridge.sh:230` — bare bootstrap — still holds.
- `scripts/install_sdlc_reflection.sh:41` — bare bootstrap (script runs under `set -euo pipefail`) — still holds.

**Established correct pattern re-verified (targets to match):**
- `scripts/remote-update.sh:247-290` — `kickstart -k` first (loaded branch), and in the
  not-loaded branch: bare bootstrap captured with `|| BOOTSTRAP_RC=$?`, then `kickstart -k`
  recovery, declaring failure only if BOTH fail. Comment at L250 explicitly names "bootstrap
  error 5: label still registered".
- `scripts/update/service.py:804-811` (`install_log_rotate_agent`) — bootstrap rc-check then
  `kickstart -k` fallback on non-zero.

**Cited sibling issues/PRs re-checked:**
- #2017 / #2018 — MERGED/closed 2026-07-11T04:29 (after this issue was filed). "Fix
  worker-restart EIO on stale-worker false-negative in remote-update.sh." Hardened the
  **worker-restart not-loaded branch of `remote-update.sh` only**. It did NOT touch
  `valor-service.sh` or any `install_*.sh` helper. Complementary scope — this plan hardens
  the remaining bare-bootstrap call sites. No overlap in files changed.

**Commits on main since issue was filed (touching referenced files):**
- `d1a40aba` (#2017) — touched `scripts/remote-update.sh` (worker-restart block) and its
  test. Does NOT change the `valor-service.sh` / `install_*.sh` call sites this plan targets.
  Its hardened not-loaded branch is a second reference pattern to match.

**Active plans in `docs/plans/` overlapping this area:** none.

**Notes:** All issue line references are exact at `3859f490`. The problem is still present
and reproducible by code inspection: three bare bootstraps in `valor-service.sh` under
`set -e`, and five+ bare bootstraps across the `install_*.sh` helpers.

## Prior Art

- **PR #2017 / Issue #2018**: "Fix worker-restart EIO on stale-worker false-negative in
  `remote-update.sh`" — MERGED 2026-07-11. Hardened the worker-restart not-loaded branch of
  `remote-update.sh` with a bootstrap-rc-capture + `kickstart -k` recovery, declaring failure
  only if both fail, and surfacing the raw launchd errno. Succeeded. **This is the canonical
  reference pattern** for the present fix; the present issue covers the sibling call sites
  (`valor-service.sh` + `install_*.sh`) that #2017 did not touch.
- **`remote-update.sh:247-290`** (pre-existing) — `kickstart -k` first on the loaded branch;
  comment at L250 explicitly names "bootstrap error 5: label still registered".
- **`service.py::install_log_rotate_agent:804-811`** (pre-existing) — bootstrap rc-check then
  `kickstart -k` fallback; the Python-side established pattern.

No prior attempt to fix `valor-service.sh`/`install_*.sh` bootstrap tolerance was found —
this is the first hardening of those specific call sites.

## Why Previous Fixes Failed

No prior fix *failed*. PR #2017 correctly hardened `remote-update.sh` but was scoped to that
one script's worker-restart branch. The other shell call sites in `valor-service.sh` and the
`install_*.sh` helpers were left with the same bare-bootstrap shape — this plan closes that gap.
There is no failed-fix pattern to analyze.

## Data Flow

1. **Entry point**: `/update` (cron or manual) runs `scripts/remote-update.sh`, which calls
   into `scripts/update/service.py::install_service` / `restart_service`.
2. **service.py**: invokes `scripts/valor-service.sh install` / `restart` / `worker-start`
   as a subprocess. `valor-service.sh` runs under `set -e`.
3. **valor-service.sh**: reaches a bare `launchctl bootstrap` (bridge install L545,
   `bootstrap_plist_idempotent` L390 for update-cron/watchdog, or `worker-start` L721).
   launchd returns errno 5 (label still registered) → non-zero exit → `set -e` aborts the
   whole subprocess → `service.py` sees a failed install → `/update` reports a hard abort
   and stops. Remaining services never install.
4. **install_*.sh helpers**: separately invoked (some by `service.py`, some manually). Each
   reaches a bare `launchctl bootstrap`; the same errno 5 aborts that helper.
5. **Output**: `/update` prints `Bootstrap failed: 5: Input/output error` and terminates with
   no further `[update]` output.

The fix inserts a fail-soft recovery at step 3/4: on bootstrap non-zero, run `kickstart -k`
against the same label; the service ends up loaded and the run continues.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1-2 (confirm the fail-soft-vs-hard-fail decision for genuine double-failures)
- Review rounds: 1

This is a mechanical hardening of ~8 known call sites across 6 shell files, matching an
already-established in-repo pattern. The only judgment call is failure-visibility semantics.

## Prerequisites

No prerequisites — this work has no external dependencies. It is a shell-only change tested
with a stubbed `launchctl` on `PATH`; no real launchd services are touched.

## Solution

### Key Elements

- **Shared hardened-bootstrap helper**: one bash function, `launchctl_bootstrap_fail_soft`,
  that does bootout-before-bootstrap, then `kickstart -k` fallback on bootstrap failure,
  returning 0 when the service ends up loaded and non-zero (with a distinct warning) only
  when both attempts fail. Lives in a small sourced library so all six scripts share one
  implementation.
- **valor-service.sh call-site conversion**: replace the three bare `launchctl bootstrap`
  calls (L390, L545, L721) with calls to the helper. Because the script is under `set -e`,
  guard the call so a genuine double-failure warns and continues rather than hard-aborting
  the whole install (fail-soft — the whole point of the issue).
- **install_*.sh call-site conversion**: replace the bare bootstraps in `install_worker.sh`
  (:167 and :205), `install_reflection_worker.sh` (:150), `install_nightly_tests.sh` (:101),
  `install_email_bridge.sh` (:230), and `install_sdlc_reflection.sh` (:41) with helper calls.
- **Shell test harness**: a sandbox test that runs the real scripts with a stub `launchctl`
  on `PATH` that mimics the errno-5 EIO on `bootstrap`, asserting recovery via `kickstart -k`.

### Flow

`/update` runs → `valor-service.sh install` under `set -e` → reaches a bootstrap call →
`launchctl_bootstrap_fail_soft` bootout+bootstrap → (errno 5) → `kickstart -k` recovery →
service loaded → helper returns 0 → install continues → `/update` completes normally.

### Technical Approach

- **Shared helper location**: create `scripts/lib/launchctl.sh` exporting
  `launchctl_bootstrap_fail_soft <domain> <plist> <label>`. Each of the six scripts sources it
  via a path derived from its own `SCRIPT_DIR`/`$(dirname "${BASH_SOURCE[0]}")` (every one of
  these scripts already computes such a variable, so sourcing is robust). A single sourced
  implementation honors the repo's DRY / no-legacy principle over inlining the same block eight
  times.
- **Helper semantics** (mirrors `remote-update.sh:247-290` and `service.py:804-811`):
  ```bash
  launchctl_bootstrap_fail_soft() {
    local domain="$1" plist="$2" label="$3"
    launchctl bootout "${domain}/${label}" 2>/dev/null || true   # defensive; tolerate absent
    if launchctl bootstrap "$domain" "$plist" 2>/dev/null; then
      return 0
    fi
    # bootstrap failed (commonly errno 5 = label still registered/draining).
    # kickstart -k is the atomic recovery — same primitive remote-update.sh prefers.
    if launchctl kickstart -k "${domain}/${label}" 2>/dev/null; then
      return 0
    fi
    echo "WARNING: launchctl bootstrap+kickstart failed for ${label}" >&2
    return 1
  }
  ```
  The function must receive the **label** (each call site already knows it: `$label`,
  `$WORKER_PLIST_NAME`, `$LABEL`, etc.) so bootout and kickstart target the right service.
  Note the bridge-install site (L545) currently derives no explicit `bootout` — it relies on
  `stop_bridge`; the helper's internal defensive bootout supersedes that need but `stop_bridge`
  stays (it also terminates the process, not only the launchd label).
- **`set -e` fail-soft semantics**: under `set -e`, `valor-service.sh` call sites invoke the
  helper so that the recoverable errno-5 path (returns 0) never aborts, and a genuine
  double-failure warns and continues rather than tearing down the whole install. Concretely,
  wrap the call as `launchctl_bootstrap_fail_soft ... || echo "WARNING: ... continuing" >&2`
  (or capture rc and log) so remaining services still install and `/update` completes and
  reports. This is the decisive interpretation of the issue's "never let a bootstrap errno-5
  abort the whole install." See Open Question 1 for the confirm.
- **`install_*.sh` under `set -euo pipefail`**: same call shape. For the standalone installers,
  a single failed install exiting non-zero is acceptable (they are invoked one-per-service, so
  there is no "abort the batch" concern) — but for consistency they use the same helper, and
  whether a genuine double-failure should exit non-zero or warn-and-continue is settled by the
  same Open Question 1.
- **No behavior change on the happy path**: when bootstrap succeeds first try, the helper
  returns 0 with no kickstart — identical observable behavior to today.

## Failure Path Test Strategy

### Exception Handling Coverage
- No Python `except Exception: pass` blocks are in scope — this is shell-only. The shell
  analogue (bare `launchctl bootstrap` aborting under `set -e`) is precisely the failure this
  plan fixes, and it is covered by the new stubbed-`launchctl` harness tests below.

### Empty/Invalid Input Handling
- The helper receives three positional args (domain, plist, label) that every call site already
  has in scope. A test asserts the helper warns (non-zero) when both bootstrap and kickstart
  fail (the genuine-failure path), so a truly dead service is never silently masked.

### Error State Rendering
- The genuine double-failure path emits a distinct, greppable `WARNING: launchctl
  bootstrap+kickstart failed for <label>` to stderr. A test asserts this line appears and that
  the recoverable errno-5 path does NOT emit it (no false alarms).

## Test Impact

No existing tests affected — this change is additive hardening of shell call sites and adds a
new sourced helper. `tests/unit/test_update_install_worker.py` exercises plist *env-var
injection*, not the bootstrap call, so it is untouched. `tests/unit/test_remote_update_shell.py`
targets `remote-update.sh` (a different script already hardened by #2017) and is untouched. The
happy-path (first-try bootstrap succeeds) preserves identical observable behavior, so no
existing assertion changes.

- [ ] New: `tests/unit/test_valor_service_bootstrap.py` — CREATE: sandbox harness runs the real
      `valor-service.sh` bootstrap paths with a stub `launchctl` that mimics errno-5 EIO; asserts
      `kickstart -k` recovery and that the install continues.
- [ ] New: `tests/unit/test_install_scripts_bootstrap.py` — CREATE: parametrized over the five
      `install_*.sh` helpers, same stub-`launchctl` harness; asserts each recovers via
      `kickstart -k` and does not abort on a transient errno 5.

## Rabbit Holes

- **Do NOT refactor `service.py`'s Python install helpers.** They already degrade gracefully via
  `run_cmd` (capture output, return bool, log WARN) and are explicitly out of scope per the
  issue's recon. Touching them re-opens surface area already fixed elsewhere.
- **Do NOT re-harden `remote-update.sh`.** #2017 already did the worker-restart branch. Only
  reuse its pattern; do not modify it.
- **Do NOT attempt to detect the specific errno value** (5 vs others) and branch on it. The
  established pattern treats *any* bootstrap failure as "try kickstart -k" — matching
  `remote-update.sh` and `service.py`. Parsing launchd errno strings is brittle and unnecessary.
- **Do NOT add retry/sleep loops** beyond the single kickstart fallback. The established pattern
  is one recovery attempt; more is scope creep.

## Risks

### Risk 1: `kickstart -k` fires against a label that is genuinely not loaded
**Impact:** `kickstart -k` returns non-zero (rc 113 "Could not find service") if the label truly
isn't registered, which could be misread as a hard failure.
**Mitigation:** The helper only reaches `kickstart -k` after a `bootstrap` that failed — and an
errno-5 bootstrap failure specifically means the label *is* already registered, so `kickstart -k`
is the correct primitive. If both bootstrap and kickstart fail, that is a genuine failure and the
warning path is correct. Tests cover both the recover and double-fail branches.

### Risk 2: Sourcing the shared helper introduces a path-resolution fragility
**Impact:** If a script is invoked via a symlink or from an unexpected CWD, the `source` path
could miss.
**Mitigation:** Derive the library path from `$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)`
(the pattern `valor-service.sh` already uses at its top), which is symlink- and CWD-robust. A
harness test runs each script from a tmp sandbox to prove sourcing resolves.

### Risk 3: Changing fail-soft semantics masks a real, persistent service failure
**Impact:** If `valor-service.sh` warns-and-continues on a genuine double-failure, a truly dead
service could go unnoticed in a long update log.
**Mitigation:** The warning line is distinct and greppable (`WARNING: launchctl
bootstrap+kickstart failed for <label>`), matching the diagnosability intent of #2017's
`RESTART FAILED` line. Open Question 1 confirms whether valor-service.sh should warn-continue or
hard-fail on genuine double-failure.

## Race Conditions

The bug itself IS a launchd race (label still registered/draining in `gui/<uid>/` when
`bootstrap` runs). The fix does not introduce new concurrency in the scripts — each call site is
synchronous and single-threaded.

### Race 1: Label still registered in `gui/<uid>/` domain at bootstrap time
**Location:** `valor-service.sh:390,545,721`; `install_*.sh` bootstrap sites.
**Trigger:** A `bootstrap` issued immediately after a `bootout` (or after a prior crash left a
half-loaded label) while launchd is still draining the label from the domain.
**Data prerequisite:** The label must be fully unregistered from `gui/<uid>/` before a fresh
`bootstrap` can succeed.
**State prerequisite:** launchd domain state for the label is "absent".
**Mitigation:** The helper does a defensive `bootout ... || true` first, then on a still-failing
`bootstrap` falls back to `kickstart -k`, which atomically kill+restarts the already-registered
label without requiring the drain to complete — exactly the `remote-update.sh` mitigation.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2018] The `remote-update.sh` worker-restart not-loaded branch — already fixed
  by PR #2017 (issue #2018, merged). Not re-touched here.
- Nothing else deferred — every relevant shell bootstrap call site named in the issue
  (`valor-service.sh` ×3, `install_*.sh` ×5+) is in scope for this plan. The Python
  `service.py` install helpers are excluded because they already degrade gracefully (not a
  deferral — they are correct as-is).

## Update System

This fix *is* an update-system hardening: it changes `scripts/valor-service.sh` and the
`scripts/install_*.sh` helpers that `/update` (`scripts/remote-update.sh` →
`scripts/update/service.py`) invokes. No new dependencies, config files, or migration steps are
introduced — the shared helper (`scripts/lib/launchctl.sh`) is a new repo file that propagates
via the normal `git pull` in `remote-update.sh`; it needs no registration. Existing installations
pick up the hardened behavior on their next `/update`. No change to `scripts/update/run.py`,
`hardlinks.py`, or `migrations.py` is required.

## Agent Integration

No agent integration required — this is an infrastructure/shell change to launchd service
management. No MCP server, `.mcp.json`, `pyproject.toml [project.scripts]`, or
`bridge/telegram_bridge.py` changes. The agent already invokes these scripts via its Bash tool
(`./scripts/valor-service.sh ...`); the change is transparent to that surface.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/bridge-self-healing.md` (or the closest update/service-management
      doc) with a short note that all shell `launchctl bootstrap` call sites are fail-soft via
      the shared `scripts/lib/launchctl.sh` helper (bootout → bootstrap → `kickstart -k`
      recovery), referencing #2013 alongside the #2017 `remote-update.sh` note.
- [ ] If no dedicated service-management feature doc exists, add a subsection to
      `docs/deployment.md` describing the hardened-bootstrap convention and pointing new
      `install_*.sh` scripts at the shared helper.

### Inline Documentation
- [ ] Header comment on `scripts/lib/launchctl.sh` explaining the errno-5 rationale and the
      `kickstart -k` recovery, cross-referencing #2013 and #2017.
- [ ] Comment at each converted call site is unnecessary beyond the helper name (self-documenting).

## Success Criteria

- [ ] `scripts/lib/launchctl.sh` exists exporting `launchctl_bootstrap_fail_soft`.
- [ ] All three bare `launchctl bootstrap` calls in `valor-service.sh` (L390, L545, L721) route
      through the helper; `grep -n 'launchctl bootstrap' scripts/valor-service.sh` shows only the
      helper definition/call, no bare site.
- [ ] All bare `launchctl bootstrap` calls in the five `install_*.sh` helpers route through the
      helper.
- [ ] A stubbed-`launchctl` harness test proves errno-5 EIO on `bootstrap` recovers via
      `kickstart -k` and the run continues (does not abort).
- [ ] A test proves a genuine bootstrap+kickstart double-failure emits the distinct `WARNING`
      line (failure is surfaced, not masked).
- [ ] Happy-path (first-try bootstrap succeeds) invokes no `kickstart` — identical observable
      behavior to today.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (shell-hardening)**
  - Name: bootstrap-builder
  - Role: Create the shared helper and convert all bare-bootstrap call sites in
    `valor-service.sh` and the `install_*.sh` helpers.
  - Agent Type: builder
  - Domain: shell / launchd service management
  - Resume: true

- **Builder (shell-tests)**
  - Name: bootstrap-test-builder
  - Role: Write the stubbed-`launchctl` sandbox harness tests for the recover and double-fail
    paths, modeled on `tests/unit/test_remote_update_shell.py`.
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: bootstrap-validator
  - Role: Verify no bare `launchctl bootstrap` remains, tests pass, happy path unchanged.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Create the shared hardened-bootstrap helper
- **Task ID**: build-helper
- **Depends On**: none
- **Validates**: `tests/unit/test_valor_service_bootstrap.py` (create)
- **Assigned To**: bootstrap-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `scripts/lib/launchctl.sh` with `launchctl_bootstrap_fail_soft <domain> <plist> <label>`
  implementing bootout → bootstrap → `kickstart -k` recovery → distinct WARNING on double-fail,
  per the Technical Approach snippet.
- Add a header comment explaining the errno-5 rationale, cross-referencing #2013 and #2017.

### 2. Convert valor-service.sh call sites
- **Task ID**: build-valor-service
- **Depends On**: build-helper
- **Validates**: `tests/unit/test_valor_service_bootstrap.py` (create)
- **Assigned To**: bootstrap-builder
- **Agent Type**: builder
- **Parallel**: false
- Source `scripts/lib/launchctl.sh` near the top of `valor-service.sh` using a
  `BASH_SOURCE`-derived path.
- Replace the bare `launchctl bootstrap` at L390 (`bootstrap_plist_idempotent`, label `$label`),
  L545 (bridge install, label = bridge plist label), and L721 (`worker-start`, label
  `$WORKER_PLIST_NAME`) with `launchctl_bootstrap_fail_soft` calls that warn-and-continue on
  non-zero (fail-soft under `set -e`) per Open Question 1's resolution.

### 3. Convert install_*.sh helper call sites
- **Task ID**: build-install-scripts
- **Depends On**: build-helper
- **Validates**: `tests/unit/test_install_scripts_bootstrap.py` (create)
- **Assigned To**: bootstrap-builder
- **Agent Type**: builder
- **Parallel**: false
- Source the helper and replace bare bootstraps in `install_worker.sh` (:167 main, :205
  watchdog), `install_reflection_worker.sh` (:150), `install_nightly_tests.sh` (:101),
  `install_email_bridge.sh` (:230), `install_sdlc_reflection.sh` (:41). Pass the label each site
  already has in scope (`$LABEL`, `$WATCHDOG_LABEL`, etc.).

### 4. Write the stubbed-launchctl harness tests
- **Task ID**: build-tests
- **Depends On**: build-valor-service, build-install-scripts
- **Assigned To**: bootstrap-test-builder
- **Agent Type**: test-engineer
- **Parallel**: false
- Model the sandbox harness on `tests/unit/test_remote_update_shell.py`: a stub `launchctl` on
  `PATH` that returns errno-5 EIO on `bootstrap` (configurable), records calls, and succeeds on
  `kickstart`.
- Create `tests/unit/test_valor_service_bootstrap.py`: assert each valor-service bootstrap path
  recovers via `kickstart -k` on errno 5, continues the run, and emits the WARNING only on
  double-fail.
- Create `tests/unit/test_install_scripts_bootstrap.py`: parametrized over the five installers,
  same recover + double-fail assertions.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: build-tests
- **Assigned To**: bootstrap-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/bridge-self-healing.md` (or `docs/deployment.md`) with the hardened-
  bootstrap convention and the shared-helper pointer.

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: bootstrap-validator
- **Agent Type**: validator
- **Parallel**: false
- Confirm `grep -n 'launchctl bootstrap' scripts/valor-service.sh scripts/install_*.sh` shows no
  bare call site (only helper calls / the helper definition).
- Run the new tests and the full shell-test suite; confirm green.
- Verify happy-path invokes no kickstart.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Helper exists | `test -f scripts/lib/launchctl.sh && grep -c 'launchctl_bootstrap_fail_soft' scripts/lib/launchctl.sh` | output > 0 |
| No bare bootstrap in valor-service.sh | `grep -n 'launchctl bootstrap' scripts/valor-service.sh \| grep -v 'launchctl_bootstrap_fail_soft'` | exit code 1 |
| No bare bootstrap in install scripts | `grep -rn 'launchctl bootstrap' scripts/install_worker.sh scripts/install_reflection_worker.sh scripts/install_nightly_tests.sh scripts/install_email_bridge.sh scripts/install_sdlc_reflection.sh \| grep -v 'launchctl_bootstrap_fail_soft'` | exit code 1 |
| Helper sourced by valor-service.sh | `grep -c 'lib/launchctl.sh' scripts/valor-service.sh` | output > 0 |
| New tests pass | `pytest tests/unit/test_valor_service_bootstrap.py tests/unit/test_install_scripts_bootstrap.py -q` | exit code 0 |
| Full test suite | `pytest tests/ -x -q` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

1. **Genuine double-failure semantics under `set -e`:** When BOTH `bootstrap` and the
   `kickstart -k` fallback fail (a truly dead service, not a transient errno 5), should
   `valor-service.sh` (a) warn-and-continue so the rest of the install completes and `/update`
   reports, or (b) hard-fail that install invocation non-zero (like #2017's `RESTART FAILED`
   worker path)? The plan currently assumes (a) fail-soft for `valor-service.sh` — matching the
   issue's "never let a bootstrap errno-5 abort the whole install" — with a distinct greppable
   WARNING for diagnosability. Confirm (a), or switch to (b) for parity with #2017's failure
   visibility. (The `install_*.sh` helpers are invoked one-per-service, so this question only
   materially affects `valor-service.sh`.)
