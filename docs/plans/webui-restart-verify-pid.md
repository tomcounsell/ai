---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-16
tracking: https://github.com/tomcounsell/ai/issues/2123
last_comment_id:
---

# valor-service.sh restart_webui — verify new PID + serving port before claiming success

## Problem

`./scripts/valor-service.sh restart` prints `Web UI restarted (PID: 36522)` while PID
36522 was 8h15m old — the UI process was never cycled. After merging UI-affecting
changes, `restart` claims the UI runs new code when it is still serving the old build.

**Current behavior:**
`restart_webui()` (`scripts/valor-service.sh:820-838`) captures the pre-kill PID via
`lsof -ti :8500 | head -1`, `kill -9`s only that single PID (fail-soft `|| true`),
respawns `python -m ui.app`, then re-reads `lsof -ti :8500 | head -1` and prints
`Web UI restarted (PID: $pid)` **without** comparing the post-respawn PID to the
pre-kill one or probing that the port actually serves HTTP. Any surviving old process
(multiple listeners on :8500, or a new spawn that fails to bind) is reported as a fresh
restart.

**Desired outcome:**
`restart` cycles the UI to a genuinely new PID and confirms the port serves before
printing success; a failure to cycle prints a loud `WARNING` instead of a false success
line. Mirrors the live-PID verification posture shipped for launchctl services in
PR #2104/#2109 and the #2104 "assert the effect, don't claim it" principle.

## Freshness Check

**Baseline commit:** ba6f10134d05faf737cd2ddd5ee9f74f18b99a38
**Issue filed at:** 2026-07-16T10:32:24Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `scripts/valor-service.sh:820-838` — `restart_webui()` reads/kills/respawns/re-reads
  and prints success on any PID present — still holds verbatim.
- `scripts/valor-service.sh:1108-1112` — `restart)` invokes `restart_webui` after
  `restart_bridge` + `restart_worker` — still holds.

**Cited sibling issues/PRs re-checked:**
- PR #2109 / #2104 — merged (commit `23da303d6`); introduced the launchctl live-PID
  verify pattern this plan mirrors. Did NOT touch `restart_webui`.

**Commits on main since issue was filed (touching referenced files):** none since the
issue was filed 2026-07-16T10:32Z; last touch was `23da303d6` (#2109), pre-dating it.

**Active plans in `docs/plans/` overlapping this area:** none.

**Notes:** Issue filed today; code lines re-read and match exactly. No drift.

## Prior Art

- **PR #2109 / #2104**: launchctl bootstrap errno-5 retry + opt-in live-PID verify — 
  established the "verify the process is actually live before reporting success" pattern
  for launchd-managed services (`bridge`, `worker`, watchdogs). `restart_webui` is a
  plain `nohup` spawn (not launchd-managed) and was never covered by that work. This plan
  extends the same posture to the web UI restart path.
- No prior issue/PR specifically addressed `restart_webui` false success.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — self-contained bash change plus a shell-level test that runs the real
script against stubbed `lsof`/`kill`/spawn on PATH (existing harness in
`tests/unit/test_valor_service_bootstrap.py`).

## Solution

### Key Elements

- **Old-PID capture + full kill**: capture the set of PIDs on :8500 before killing, and
  kill *all* of them (not just `head -1`), so a multi-listener case can't leave a
  survivor that gets misreported.
- **New-PID assertion**: after respawn, the reported PID must differ from every pre-kill
  PID. If the only PID on :8500 is one that existed before, that is a failure to cycle.
- **Serving probe**: confirm the port actually answers HTTP (a bounded `curl` to a cheap
  UI route) before declaring success — a bound-but-wedged socket is not "restarted".
- **Loud failure**: on failure to cycle or serve, print `WARNING: Web UI restart failed
  to cycle ...` to stderr and return non-zero, instead of a green success line.

### Flow

`restart` → `restart_webui` → capture OLD pids on :8500 → kill all OLD pids → wait for
port to free → spawn new `ui.app` → wait/poll for a NEW pid to bind → probe port serves
→ **new pid ∉ OLD pids AND serves** → `Web UI restarted (PID: <new>)`; otherwise loud
`WARNING` + non-zero return.

### Technical Approach

- Introduce a small helper (e.g. `webui_pids_on_port`) that returns all PIDs on :8500 so
  both the kill and the verify steps share one definition of "who is on the port".
- Bounded polling: after respawn, poll `lsof` for up to a few seconds for a PID that is
  NOT in the pre-kill set (avoids the fixed `sleep 2` racing a slow bind — the likely
  contributor to the observed misreport when the new process hadn't yet bound).
- Serving probe uses `curl -sf -m <timeout> http://localhost:8500/<cheap-route>`; on
  non-2xx/timeout treat as not-serving. Keep the route cheap — do NOT hit
  `/dashboard.json` (that endpoint is itself slow per #2122); use a lightweight route
  (e.g. a partial/health route or root) so this probe stays fast and independent of #2122.
- Port and timeouts as named locals at the top of the function (grain-of-salt provisional
  values, overridable via env) rather than inline magic numbers.
- `set -e` safety: `restart_webui` runs under `set -e`; the verify/probe steps must be
  written so an expected non-zero (`lsof` empty, `curl` fail) does not abort the script —
  guard with `|| true` / `if` blocks as the surrounding functions already do.

## Failure Path Test Strategy

### Exception Handling Coverage
- The only "swallow" in scope is the fail-soft `kill -9 ... || true`; the new WARNING
  path makes a failure-to-cycle observable (stderr WARNING + non-zero return) rather than
  silent. Test asserts the WARNING line and return code on the not-cycled and not-serving
  scenarios.

### Empty/Invalid Input Handling
- Empty `lsof` output (no process on :8500 before restart — cold start) must be handled:
  the function should spawn and verify the new PID serves without a "must differ from old"
  failure when there was no old PID. Test covers the cold-start (no prior listener) case.

### Error State Rendering
- The failure line is user-visible operator output. Tests assert the loud WARNING renders
  on (a) old PID survives / new PID never binds, and (b) a PID binds but the port does not
  serve — and that success renders only when a NEW PID both binds and serves.

## Test Impact

- [ ] `tests/unit/test_valor_service_bootstrap.py` — UPDATE (additive): add `restart`
  (webui) scenarios using new `lsof`/`kill`/`curl` stubs and a stub `python` on PATH.
  No existing test in this file exercises `restart_webui`, so existing cases are
  unaffected; the change only adds new stubs and test functions.

No other existing tests are affected — `restart_webui` currently has zero test coverage,
and the change is confined to that function plus one new helper in the same script.

## Rabbit Holes

- Do NOT convert the web UI to a launchd-managed service to reuse the launchctl verify
  path — that is a much larger change and out of scope; this is a plain `nohup` spawn and
  stays one.
- Do NOT try to fix or depend on `/dashboard.json` latency here (that is #2122) — pick a
  cheap serving route so the probe is fast regardless of #2122.
- Do NOT add a general port-management abstraction; keep the helper local to this script.

## Risks

### Risk 1: Serving probe flakiness on a slow-starting UI
**Impact:** A genuinely-restarting UI that binds slowly could trip the WARNING if the
probe window is too tight.
**Mitigation:** Bounded poll with a few retries before declaring not-serving; timeouts as
named, env-overridable locals so operators can widen them. The default window comfortably
exceeds observed `ui.app` bind time.

### Risk 2: Multiple legitimate listeners on :8500
**Impact:** Killing all PIDs on :8500 could over-kill if something else legitimately binds
the port.
**Mitigation:** :8500 is the dedicated `ui.app` port in this repo; killing all listeners
on it is the intended cycle semantics. No behavior change vs. today except we no longer
leave a survivor.

## Race Conditions

### Race 1: New process not yet bound when success is probed
**Location:** `scripts/valor-service.sh:831-834` (fixed `sleep 2` then single `lsof`)
**Trigger:** `python -m ui.app` takes longer than the fixed sleep to bind :8500.
**Data prerequisite:** a NEW PID must be bound to :8500 before we read it.
**State prerequisite:** the OLD PID(s) must be gone before the new one binds.
**Mitigation:** replace the fixed sleep with a bounded poll that waits for a PID NOT in
the pre-kill set and for the port to serve; treat timeout as failure (loud WARNING),
never as success.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2122] `/dashboard.json` ~20s latency — the serving probe deliberately
  avoids that endpoint; fixing the endpoint's latency is tracked separately as #2122.

Nothing else deferred — the fix and its test coverage are fully in scope for this plan.

## Update System

No update system changes required — `scripts/valor-service.sh` is already propagated to
every machine by the existing update flow; this is an internal edit to a shipped script
with no new deps, config, or migration.

## Agent Integration

No agent integration required — this is an operator/CLI-internal change to a bash service
manager. No MCP surface, `.mcp.json`, or `bridge/telegram_bridge.py` wiring is involved.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/bridge-self-healing.md` (or the closest existing
  valor-service/restart reference) with a note that `restart` verifies the web UI cycled
  to a new PID and serves before reporting success, and prints a loud WARNING otherwise.
  If no suitable existing doc section exists, add a short note where the other
  `restart`/service-management behavior is documented.

### Inline Documentation
- [ ] Comment in `restart_webui` explaining the new-PID-differs + serves verification and
  referencing #2123 (matching the #2104/#2109 comment style already in the script).

## Success Criteria

- [ ] `restart` yields a genuinely new `ui.app` PID (differs from the pre-kill PID),
  verified live.
- [ ] Failure to cycle (old PID survives / new PID never binds) prints a loud WARNING to
  stderr and returns non-zero instead of a false success line.
- [ ] A PID that binds but does not serve HTTP is treated as failure (loud WARNING), not
  success.
- [ ] Cold start (no prior listener on :8500) succeeds when the new PID binds and serves.
- [ ] New test coverage in `tests/unit/test_valor_service_bootstrap.py` for the success,
  not-cycled, and not-serving scenarios.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).

## Step by Step Tasks

### 1. Harden restart_webui + add helper
- **Task ID**: build-restart-webui
- **Depends On**: none
- **Validates**: tests/unit/test_valor_service_bootstrap.py
- **Assigned To**: solo dev
- **Agent Type**: builder
- **Parallel**: false
- Add `webui_pids_on_port` helper (all PIDs on :8500).
- Rewrite `restart_webui`: capture OLD pids, kill all, wait for port free, respawn,
  bounded-poll for a NEW pid not in OLD set, probe the port serves (cheap route, bounded
  curl), print success only when new-and-serving; else loud WARNING + non-zero return.
- Named env-overridable local timeouts; `set -e`-safe guards; #2123 comment.

### 2. Add shell-level test coverage
- **Task ID**: build-tests
- **Depends On**: build-restart-webui
- **Validates**: tests/unit/test_valor_service_bootstrap.py
- **Assigned To**: solo dev
- **Agent Type**: builder
- **Parallel**: false
- Add stubs (`lsof`, `kill`, `curl`, `python`) so the harness can drive `restart` webui.
- Cases: success (new PID + serves), not-cycled (old PID survives), not-serving (binds but
  curl fails), cold-start (no prior listener).

### 3. Documentation
- **Task ID**: document-feature
- **Depends On**: build-restart-webui
- **Assigned To**: solo dev
- **Agent Type**: documentarian
- **Parallel**: false
- Update the restart/service doc note per the Documentation section.

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-tests, document-feature
- **Assigned To**: solo dev
- **Agent Type**: validator
- **Parallel**: false
- Run the new tests + ruff; confirm all Success Criteria.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Webui restart tests pass | `pytest tests/unit/test_valor_service_bootstrap.py -q` | exit code 0 |
| Success asserts new PID | `grep -c 'Web UI restarted' scripts/valor-service.sh` | output > 0 |
| WARNING path exists | `grep -c 'WARNING' scripts/valor-service.sh` | output > 0 |
| No naked head-1 success | `grep -n 'lsof -ti :8500' scripts/valor-service.sh` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
