---
status: Ready
type: bug
appetite: Small
owner: Tom Counsell
created: 2026-09-16
tracking: https://github.com/tomcounsell/ai/issues/3316
revision_applied: true
revision_applied_at: 2026-09-16T15:54:58Z
---

# Widen the pattern-kill validator to cover ui.app, worker, and bridge processes

## Problem

A reviewer agent stopping its own throwaway dashboard with `pkill -f "python -m ui.app"` also killed the production dashboard on port 8500. The pattern matched every `python -m ui.app` process on the machine, not just the one on port 8517. The agent noticed and restarted production, so the outage was short, but nothing warned before the kill landed.

**Current behavior:**
`.claude/hooks/validators/validate_no_broad_process_kill.py` blocks pattern kills only when the pattern names a test runner (`_TEST_RUNNER_PATTERN`, by design per #2562). CLAUDE.md states the broader rule ("never clear processes by pattern; kill by PID"), but the hook enforces the narrow one. Every long-lived service on this machine — `python -m ui.app` (dashboard, 8500), `python -m worker` (session execution engine), `bridge/telegram_bridge.py`, `python -m reflections` (reflection worker), `monitoring/worker_watchdog.py` (watchdog) — is reachable by the same unguarded shape.

**Desired outcome:**
The same four kill shapes the validator already covers (`pkill -f`, `kill $(pgrep -f ...)`, `killall`, `pgrep ... | xargs kill`) are blocked when the pattern names any long-lived service, and the block message names the sanctioned stop path for that service (`scripts/valor-service.sh stop`, `worker-stop`, kill by PID for a throwaway instance). The test-runner block keeps working exactly as before.

## Freshness Check

**Baseline commit:** `471a42301`
**Issue filed at:** 2026-09-14T14:55:53Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `.claude/hooks/validators/validate_no_broad_process_kill.py:36` — `_TEST_RUNNER_PATTERN = r"(?:py\.?test|xdist|pytest-clean)"` still narrow — still holds
- `.claude/hooks/validators/validate_no_broad_process_kill.py:43-59` — four `_BLOCK_PATTERNS` verb shapes — still holds
- `scripts/valor-service.sh:898` — launches `python -m ui.app` — still holds
- `scripts/valor-service.sh:1300,1334` — `stop)` and `worker-stop)` verbs — still holds

**Cited sibling issues/PRs re-checked:**
- #2562 — closed (origin of the validator; its fix stands, this issue widens it)
- #3177 — still open (RSI tracking issue; cited only as surfacing context, unaffected)
- #3215 / PR #3315 — PR merged 2026-09-15 (improvement-controller lane 3; touches controller/dispatch code, not the validator or service scripts)

**Commits on main since issue was filed (touching referenced files):**
- None — `git log --since="2026-09-14T14:55:53Z"` over the validator, its test, and `valor-service.sh` is empty. HEAD moved (`6e22a9ec5` at recon time, `471a42301` at plan time) but none of the movement touches this area.

**Active plans in `docs/plans/` overlapping this area:** none — recent plans cover pytest-clean guards, worktree dispatch, and the RSI controller; none touch the process-kill validator.

**Notes:** The triage section in the issue body proposes inverting the guard (block all pattern kills, allowlist the sanctioned reapers) as the preferred fix with the widen as a stopgap. Recon defers the inversion (see Dropped in the issue's Recon Summary) because it would newly block currently-allowed commands. This plan implements the widen, structured as a data-driven service table so a future inversion reuses it.

## Prior Art

- **Issue #2562**: Full unit-suite runs SIGKILLED by agent-issued machine-wide `kill -9 $(pgrep -f bin/pytest)` — created the validator (commit `e2a623a44`, single creation commit, untouched since). Succeeded for its scope; deliberately narrow, which is exactly the gap this plan closes. No failed attempt; the narrowness was a design decision, not a missed root cause.
- **PR #3208**: Ancestor-safe process lookup closing the BSD pgrep class across every liveness probe (#3187, #3265) — related process-matching hygiene in liveness probes, not in the hook layer. Relevant as a caution: process-name matching has platform edge cases (BSD vs GNU pgrep), so new patterns should stay to plain substring alternatives, not clever regex.
- **Issue #2435** (via test names): a validator written but not dispatched blocks nothing — hence the existing `test_registered_in_the_bash_dispatcher` and dispatcher end-to-end tests, which this plan keeps passing untouched.

No previous fix for this exact problem exists (service-pattern kills were never guarded), so there is no **Why Previous Fixes Failed** section.

## Research

No relevant external findings — proceeding with codebase context and training data. The work is purely internal: one hook predicate, its unit test, and repo-owned service scripts. No external libraries, APIs, or ecosystem patterns are involved, so Phase 0.7 WebSearch is skipped per the skill.

## Data Flow

Single-file synchronous predicate; no multi-component flow to trace. For the record, the enforcement path is:

1. **Entry point**: agent issues a Bash command; `pre_tool_use_bash.py` dispatcher runs before execution
2. **Predicate**: `validate_no_broad_process_kill.find_violation(command)` returns a reason string or `None`
3. **Output**: a reason string blocks the command and shows the agent the sanctioned alternative; `None` lets it through

This plan changes only step 2 (what matches) and the reason text. Dispatcher wiring, hook registration, and the allow/block contract are untouched.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (scope is fully specified in the issue's Fix shape; the one design fork — widen vs invert — is already resolved in the issue's Recon Summary)
- Review rounds: 1 (standard PR review)

Solo dev work is fast — the bottleneck is alignment and review. One validator file plus its test plus one feature doc; no spikes needed because every assumption was verified by code read during recon.

## Prerequisites

No prerequisites — this work has no external dependencies. It touches one hook script and its unit test; both run with the repo's existing interpreter and pytest setup.

## Solution

### Key Elements

- **Service table**: a data-driven list of `(pattern, sanctioned stop path)` pairs for the long-lived services — `ui.app`, `python -m worker`, `telegram_bridge`, `email_bridge`, `monitoring/worker_watchdog.py` (plus the `worker-watchdog` hyphen alias from the launchd label), `python -m reflections` (plus the `reflection_worker` alias). Adding the next service is one table row, not a new regex. This answers the triage's arms-race concern structurally without taking on the inversion's false-positive risk. Production ground truth: the watchdog runs as `monitoring/worker_watchdog.py` (underscore, per `com.valor.worker-watchdog.plist` ProgramArguments) and the reflection worker runs as `python -m reflections` (per `com.valor.reflection-worker.plist` ProgramArguments and `scripts/install_reflection_worker.sh:140`); the hyphen/underscore aliases catch patterns naming the launchd labels instead of the command line.
- **Reused verb shapes**: the existing four `_BLOCK_PATTERNS` shapes (`pkill`, `kill $(pgrep ...)`, `killall`, `pgrep | xargs kill`) are parameterized over the service table instead of hardcoded to the test-runner pattern. The test-runner block keeps working exactly as before.
- **Reason router**: `find_violation` returns the existing pytest-specific reason for test-runner matches and a new service-specific reason (naming the sanctioned stop path plus kill-by-PID for throwaway instances) for service matches. The current single `_REASON` string cannot serve both audiences.

### Flow

**Starting point** → agent types a pattern kill naming a service → **hook blocks before execution** → reason names the sanctioned stop path → **agent runs the stop script or kills by PID** → services untouched

### Technical Approach

- Keep `_TEST_RUNNER_PATTERN` and the `_SANCTIONED` (`reap-xdist.sh`) exemption exactly as is; keep the dispatcher wiring untouched.
- Build the service patterns as plain substring alternatives (`ui\.app`, `telegram_bridge`, `email_bridge` / `bridge\.email_bridge`, `worker_watchdog` / `worker-watchdog` / `monitoring/worker_watchdog\.py`, `-m reflections` / `reflections` module plus the `reflection_worker` alias, plain `worker` bounded so it does not fire on words like `reap-xdist.sh`'s worker mentions or `homework` — match against the process-name position the kill shapes already anchor on, and cover with negative test rows). Per PR #3208's lesson, avoid clever regex; prefer literal alternatives.
- Careful scoping point the builder must handle: bare `worker` is a substring of many innocent strings. The plan requires negative rows (e.g. `pkill -f 'node dev-server'`, `killall Dock` already exist; add e.g. a command containing "worker" in a non-service sense that must stay allowed, or scope the pattern to `python -m worker` / `monitoring/worker_watchdog.py` / `worker_watchdog` / `worker-watchdog` / `-m reflections` / `reflection_worker` / `worker-stop`-adjacent forms). The exact scoping is the builder's call; the test rows are the contract. Decision: service-shaped forms only, never a bare `worker` substring match.
- `find_violation` checks the test-runner patterns first (preserving the existing reason text byte-for-byte, since existing tests assert `"reap-xdist.sh" in reason`), then the service table (returning the service-specific reason).
- Test rows per the issue's Fix shape: one BLOCKED row per service per verb shape, plus a negative row for a PID kill and rows proving the sanctioned stop commands (`scripts/valor-service.sh stop`, `worker-stop`) are not blocked.

## Failure Path Test Strategy

### Exception Handling Coverage
No exception handlers in scope — `find_violation` is a pure predicate with no try/except, and this plan adds none. A malformed command string simply matches nothing and is allowed through, which is the safe default for a blocklist.

### Empty/Invalid Input Handling
- [ ] `find_violation("")` returns `None` — already covered by `test_empty_command_is_allowed`; keep passing
- [ ] Add a test row for a non-string-adjacent edge already in scope: a command mentioning a service name in a read-only context (`ps aux | grep "ui.app"`, `pgrep -f worker | wc -l`) must stay allowed, mirroring the existing pytest read-only rows

### Error State Rendering
- [ ] The service-specific block reason names the sanctioned stop path — assert in tests that the reason for a service match contains the stop-path hint (mirroring the existing `assert "reap-xdist.sh" in reason` pattern for pytest matches), so a reason that renders without remediation fails the build

## Test Impact

- [ ] `tests/unit/test_validate_no_broad_process_kill.py` BLOCKED list — UPDATE: add one row per service pattern (`python -m ui.app`, `python -m worker`, `telegram_bridge`, `bridge.email_bridge` / `email_bridge`, `monitoring/worker_watchdog.py` / `worker_watchdog` with a `worker-watchdog` hyphen-alias row, `python -m reflections` / `-m reflections` with a `reflection_worker` alias row) in each kill-verb shape the validator covers (`pkill -f`, `kill $(pgrep -f ...)`, `killall`, `pgrep ... | xargs kill`), plus a negative row proving a PID kill stays allowed. The primary BLOCKED rows must use the production command lines (`monitoring/worker_watchdog.py`, `python -m reflections`); the hyphen/alias rows are secondary. Sanctioned stop path for the email bridge rows: `email-stop` (`email-disable` to keep it down).
- [ ] `tests/unit/test_validate_no_broad_process_kill.py` ALLOWED list — UPDATE: add rows proving the sanctioned stop paths are not blocked (`scripts/valor-service.sh stop`, `worker-stop`, `kill -9 <pid>`) and read-only inspection stays allowed
- [ ] `tests/unit/test_pre_tool_use_dispatcher.py` — no change expected: dispatcher wiring is untouched; the existing end-to-end test keeps passing as a regression guard

No other existing tests affected — the change is confined to one validator predicate and its unit test; no function signatures, imports, or dispatcher behavior change.

## Rabbit Holes

- Inverting the guard to block all `pkill -f` / `killall` with an allowlist for sanctioned reapers. Tempting because it deletes the whole pattern-enumeration class, but it newly blocks currently-allowed commands (`pkill -f 'node dev-server'`, `killall Dock`) and the allowlist design needs its own investigation. Deferred per the issue's Recon Summary; the service table built here becomes its seed data.
- Trying to distinguish "my throwaway ui.app on port 8517" from "production ui.app on 8500" inside the hook (e.g. parsing port numbers out of the command). The hook sees the kill command, not the victim's port — `pkill -f "python -m ui.app"` carries no port. The correct guidance is kill-by-PID, which the reason text already teaches.
- Extending coverage to every conceivable process name on the machine (editors, browsers, node dev servers). The table covers long-lived shared services only; per-PR #3208, broad process-name matching has platform edge cases, and each new row must earn its place with an incident or a shared-service argument.

## Risks

### Risk 1: Bare `worker` over-matches and blocks legitimate commands
**Impact:** Agents doing innocent process management (e.g. commands mentioning "worker" in another context) get blocked with a confusing service message, eroding trust in the hook.
**Mitigation:** Scope the pattern to service-shaped forms (`python -m worker`, `monitoring/worker_watchdog.py` / `worker_watchdog` / `worker-watchdog`, `-m reflections` / `reflection_worker`, `telegram_bridge`, `ui.app`) and require negative test rows proving near-miss strings stay allowed. The builder picks the exact scoping; the test rows are the contract.

### Risk 2: Reason-text change breaks the existing reason assertion
**Impact:** Existing tests assert `"reap-xdist.sh" in reason` for pytest matches; a careless refactor of the reason path turns the current green suite red.
**Mitigation:** The plan requires keeping the pytest reason byte-for-byte and routing (test-runner check first). The existing suite runs unchanged as the regression guard — see Test Impact.

## Race Conditions

No race conditions identified — `find_violation` is a synchronous pure-string predicate with no shared state, no I/O, and no concurrency. The hook runs once per Bash command before execution; there is nothing to interleave.

## No-Gos (Out of Scope)

Nothing deferred — every relevant item is in scope for this plan. The two tempting wider scopes (full guard inversion, per-port victim disambiguation) are documented as Rabbit Holes rather than deferred work because neither is a concrete deliverable with an owner: the inversion needs its own investigation and issue before it can be scoped, and port disambiguation is technically infeasible from the hook's vantage point.

## Update System

No update system changes required — this feature is purely internal. The validator ships inside the repo (`.claude/hooks/`, synced via the normal pull/update flow); no new dependencies, config files, service definitions, or migration steps are involved.

## Agent Integration

No agent integration required — this is hook-layer protection the agent hits automatically. No new CLI entry point in `pyproject.toml [project.scripts]`, no bridge import. The agent-facing surface is the block reason text itself, which is covered by the reason-content assertions in Failure Path Test Strategy. The existing dispatcher end-to-end tests (`test_dispatcher_blocks_the_transcript_command`) verify the agent's command path stays wired.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/pattern-kill-guard.md` describing the validator: which kill shapes are blocked, the service table (`ui.app`, `worker`, `telegram_bridge`, `email_bridge`, `monitoring/worker_watchdog.py` with `worker-watchdog` alias, `python -m reflections` with `reflection_worker` alias) with per-service sanctioned stop paths, and the kill-by-PID rule for throwaway instances
- [ ] Add entry to `docs/features/README.md` index table

### Inline Documentation
- [ ] Code comments on the service table in the validator explaining why each pattern is dangerous (which production process it names)

## Success Criteria

- [x] `pkill -f "python -m ui.app"` (the incident command shape) is blocked with a reason naming the sanctioned stop path
- [x] One BLOCKED test row per service (`ui.app`, `worker`, `telegram_bridge`, `email_bridge`, `monitoring/worker_watchdog.py` with `worker-watchdog` alias row, `python -m reflections` with `reflection_worker` alias row) per kill-verb shape, plus a PID-kill negative row
- [x] All pre-existing test rows pass unchanged (pytest block and ALLOWED list intact)
- [x] Tests pass (`/do-test` scope: `tests/unit/test_validate_no_broad_process_kill.py` green via `scripts/pytest-clean.sh`)
- [x] Documentation updated (`/do-docs` scope: `docs/features/pattern-kill-guard.md` created, README index entry added)

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead NEVER builds directly - they deploy team members and coordinate.

### Team Members

- **Builder (validator)**
  - Name: guard-builder
  - Role: Widen the validator predicate and extend its unit test
  - Agent Type: builder
  - Resume: true

- **Documentarian (guard doc)**
  - Name: guard-documentarian
  - Role: Create the pattern-kill-guard feature doc and index entry
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

Tier 1 core (`builder`, `validator`, `code-reviewer`, `test-engineer`, `documentarian`, `plan-maker`, `frontend-tester`) per the skill catalogue. No domain specialists needed — this is a synchronous string-predicate change with no async, Redis, or untrusted-input surface.

## Step by Step Tasks

### 1. Widen validator and tests
- **Task ID**: build-guard
- **Depends On**: none
- **Validates**: `tests/unit/test_validate_no_broad_process_kill.py` (extended), `tests/unit/test_pre_tool_use_dispatcher.py` (unchanged, regression)
- **Informed By**: none (no spikes; recon verified all premises by code read)
- **Assigned To**: guard-builder
- **Agent Type**: builder
- **Parallel**: true
- Add a service table `(pattern, stop-path)` for `ui.app`, `worker`, `telegram_bridge`, `email_bridge` (sanctioned stop: `email-stop`, `email-disable` to keep it down), `monitoring/worker_watchdog.py` (plus `worker-watchdog` hyphen alias), `python -m reflections` (plus `reflection_worker` alias) in `.claude/hooks/validators/validate_no_broad_process_kill.py`, parameterized over the existing four kill-verb shapes
- Route reasons: keep the pytest `_REASON` byte-for-byte for test-runner matches; return a service-specific reason naming the sanctioned stop path plus kill-by-PID for service matches
- Keep the `_SANCTIONED` (`reap-xdist.sh`) exemption and dispatcher wiring untouched
- Extend the test's BLOCKED list (one row per service per verb shape), ALLOWED list (sanctioned stop commands, PID kills, read-only service inspection), and add a reason-content assertion for service matches mirroring the existing `reap-xdist.sh` assertion
- Run `scripts/pytest-clean.sh tests/unit/test_validate_no_broad_process_kill.py -q` green

### 2. Documentation
- **Task ID**: document-feature
- **Depends On**: build-guard
- **Assigned To**: guard-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/pattern-kill-guard.md` describing the validator: blocked kill shapes, the service table (`ui.app`, `worker`, `telegram_bridge`, `email_bridge`, `monitoring/worker_watchdog.py` with `worker-watchdog` alias, `python -m reflections` with `reflection_worker` alias) with per-service sanctioned stop paths, the kill-by-PID rule
- Add entry to `docs/features/README.md` index table
- Validates: `test -f docs/features/pattern-kill-guard.md && grep -q pattern-kill-guard docs/features/README.md` exits 0

### 3. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-guard, document-feature
- **Assigned To**: guard-builder
- **Agent Type**: validator
- **Parallel**: false
- Run all Verification commands
- Verify all success criteria met (including documentation)
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Validator unit tests pass | `scripts/pytest-clean.sh tests/unit/test_validate_no_broad_process_kill.py -q` | exit code 0 |
| Incident command now blocked | `python -c "import importlib.util; s=importlib.util.spec_from_file_location('v','.claude/hooks/validators/validate_no_broad_process_kill.py'); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); assert m.find_violation('pkill -f \"python -m ui.app\"') is not None"` | exit code 0 |
| PID kill still allowed | `python -c "import importlib.util; s=importlib.util.spec_from_file_location('v','.claude/hooks/validators/validate_no_broad_process_kill.py'); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); assert m.find_violation('kill -9 88620') is None"` | exit code 0 |
| Feature doc created and indexed | `test -f docs/features/pattern-kill-guard.md && grep -q pattern-kill-guard docs/features/README.md` | exit code 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| NIT | History & Consistency | Key Elements still names a bare `worker` table entry while the Technical Approach decision requires service-shaped forms only, never a bare worker substring match. | addressed | Rephrased the Key Elements entry to `python -m worker`. |
| NIT | Risk & Robustness | The ALLOWED rows prove `scripts/valor-service.sh stop`, `worker-stop`, and PID kills stay unblocked, but omit the `email-stop` / `email-disable` stop path the plan names as sanctioned for the email bridge. | addressed | `email-stop` and `email-disable` ALLOWED rows are present in the PR's test file. |

---
