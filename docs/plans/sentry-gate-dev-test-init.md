---
status: Planning
type: bug
appetite: Small
owner: Valor
created: 2026-07-10
tracking: https://github.com/tomcounsell/ai/issues/1834
last_comment_id: 4911946680
---

# Sentry: gate init so dev/test runs stop reporting as production

## Problem

Local `pytest` runs and manually-started bridge/worker processes on dev machines
report synthetic and dev-only errors into the **production** Sentry project
(`yudame/valor`). 13 of 20 recent unresolved `valor` issues originated from a dev
machine (`Mac.local`) tagged `environment: production` — most from a single test
batch.

**Current behavior:**
- PR #1948 (merged) already suppresses Sentry init entirely under `PYTEST_CURRENT_TEST`
  or `CI` (AC#1 — done). But a dev machine that starts the real bridge/worker
  **outside** pytest still initializes Sentry with `environment="production"`,
  because `configure_sentry()` defaults `environment=os.getenv("SENTRY_ENVIRONMENT", "production")`
  with no machine awareness.
- `ui/app.py` hard-indexes `claude_auth["subscription_type"]` (VALOR-BX); the
  `_get_claude_auth_health()` error paths omit that key, so a failed `claude auth
  status` raises `KeyError` inside `dashboard_json`/`health` — which is itself then
  captured to Sentry.

**Desired outcome:**
- Real Sentry events from a machine that is **not** a designated bridge machine are
  tagged with a non-production environment (`development`), so production stays clean.
- `dashboard_json`/`health` never raise `KeyError` when Claude auth status is
  unavailable.

## Freshness Check

**Baseline commit:** `12ff552d7bf0c9dc2e23fc2a4f6b96294157d0a7`
**Issue filed at:** 2026-07-01T07:12:11Z
**Disposition:** Major drift (fix site moved by #1948; AC#1 + AC#3 already satisfied) — proceeding on a revised, narrowed premise per the issue's own upstream-change comment.

**File:line references re-verified:**
- `bridge/telegram_bridge.py:74-84` (issue's quoted inline `sentry_sdk.init`) — **GONE.** #1948 replaced it with `configure_sentry("bridge", before_send=_sentry_before_send)` at `bridge/telegram_bridge.py:78`. The init logic now lives once in `monitoring/sentry_config.py::configure_sentry()`.
- `ui/app.py:538,571` (issue's cited hard-index sites) — **drifted.** Current hard-index sites are `ui/app.py:822` (`dashboard_json`) and `ui/app.py:882` (`health`). The dict is built in `_get_claude_auth_health()` at `ui/app.py:531`; its two error returns (lines 543, 557) omit `subscription_type`.

**Cited sibling issues/PRs re-checked:**
- #1948 — merged 2026-07-08 (commit `ea05ddd3`). Extracted the shared `configure_sentry()` chokepoint and added `tests/unit/test_worker_sentry_init.py`. This is the layering point for the remaining work.
- #1835 — the orphan-noise `before_send` filter that #1948 threaded through `configure_sentry`. Unrelated to the environment gate; must not be disturbed.

**Commits on main since issue was filed (touching referenced files):**
- `ea05ddd3` feat(sentry): orphan-noise before_send filter (#1948) — **changed root cause / already satisfies AC#1 and AC#3.** The `PYTEST_CURRENT_TEST or CI` early-return means test runs no longer report at all; `test_configure_sentry_skips_under_pytest_even_with_dsn` already asserts init is not called under pytest.

**Active plans in `docs/plans/` overlapping this area:** None. `docs/plans/completed/sentry-orphan-noise-filter.md` is the completed #1948 plan (reference only).

**Notes:** Remaining scope narrows to (a) AC#2 dev-vs-prod / machine-aware environment resolution inside `configure_sentry()`, and (b) the `ui/app.py` VALOR-BX `KeyError` cleanup. AC#1 and AC#3 are done — this plan extends, not duplicates, `test_worker_sentry_init.py`.

## Prior Art

- **PR #1948**: `feat(sentry): orphan-noise before_send filter (#1835)` — extracted `configure_sentry()`, added the pytest/CI guard and `test_worker_sentry_init.py`. Succeeded; this plan builds directly on it.
- **PR #1587 / #1470**: Sentry triage auto-action + delta-based notify — triage-side, unrelated to init gating. No conflict.
- **Issue #858**: `Telegram auth errors flood Sentry — repeated capture during hibernation` — resolved via the bridge's `_sentry_before_send` hibernation filter, which #1948 composed with `drop_orphan_noise`. Confirms `before_send` is the noise-suppression seam; the environment gate is orthogonal and must leave `before_send` untouched.

## Research

No relevant external findings — proceeding with codebase context and training data. Sentry's `environment` init kwarg is a stable, well-understood parameter; the only decision is how this repo derives the value, which is a purely internal (single-machine-ownership) concern.

## Data Flow

1. **Entry point**: `bridge/telegram_bridge.py:78` calls `configure_sentry("bridge", before_send=_sentry_before_send)` at import time; `worker/__main__.py` calls `configure_sentry("worker", before_send=drop_orphan_noise)` at startup.
2. **Guard**: `configure_sentry()` returns early (no init) if `PYTEST_CURRENT_TEST` or `CI` is set. (Unchanged — AC#1.)
3. **DSN gate**: returns early if `SENTRY_DSN` is unset. (Unchanged.)
4. **Environment resolution (NEW)**: `_resolve_environment()` returns `SENTRY_ENVIRONMENT` if explicitly set; else `"production"` if this machine is a designated bridge machine (owns ≥1 project in `projects.json`); else `"development"`.
5. **Init**: `sentry_sdk.init(..., environment=<resolved>, before_send=<passed-through>)`.
6. **Output**: events captured on a dev machine's real bridge/worker run carry `environment=development`; production project sees only genuine production-machine events.

Separately, for VALOR-BX: `dashboard_json`/`health` read `_get_claude_auth_health()` → build a response dict → hard-index `subscription_type`. When `claude auth status` fails, the key is absent → `KeyError` propagates out of the request handler → captured to Sentry. Fix at the consumer read.

## Architectural Impact

- **New dependencies**: none. `configure_sentry()` gains a self-contained machine-ownership check that reads `~/Desktop/Valor/projects.json` + `scutil --get ComputerName` directly — the **same pattern already duplicated** in `ui/data/machine.py`, `bridge/update.py`, `tools/google_workspace/auth.py`, and `scripts/update/readme_check.py`. `monitoring/` deliberately does **not** import `ui/` (correct layer direction preserved).
- **Interface changes**: `configure_sentry()` signature unchanged. Internal helper `_resolve_environment()` (and a private `_is_designated_bridge_machine()`) added.
- **Coupling**: no new cross-package coupling. Machine-name duplication is pre-existing; centralizing all four copies is explicitly out of scope (see No-Gos).
- **Reversibility**: trivial — revert two files.

## Appetite

**Size:** Small

**Team:** Solo dev, 1 review round

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

Two narrow, well-scoped edits (one chokepoint function + one consumer-read fix) plus focused unit tests. No new deps, no migration, no agent surface.

## Prerequisites

No prerequisites — this work has no external dependencies. `SENTRY_DSN` need not be present at test time (tests mock `sentry_sdk` and the machine-ownership helper).

## Solution

### Key Elements

- **`_is_designated_bridge_machine()`** (new, `monitoring/sentry_config.py`): reads `projects.json` and the local ComputerName, returns `True` iff this machine owns ≥1 project (`projects.<key>.machine` == ComputerName, case-insensitive). Any failure (missing/unreadable file, `scutil` error) returns `False` — fail-to-development, which is the safe direction for this issue's goal.
  - **BUILD REQUIREMENT (Critique Concern #1 — load-bearing):** After resolving `machine = get_machine_name().lower()`, add `if not machine: return False` **before** iterating projects. Otherwise, when `scutil` fails/returns empty (`machine == ""`) and any `projects.json` entry has a missing/empty `machine` field, the mirrored predicate `project.get("machine", "").lower() == machine` evaluates `"" == ""` → `True` and mis-tags `production` — the exact inverse of the intended fail-to-development guarantee. Also replicate `config_path.exists()` before `read_text()` and the case-insensitive compare exactly as `ui/data/machine.py::get_machine_project_keys` does (this is the fifth copy of that predicate; it must not diverge). The predicate is bit-for-bit the same one `bridge/config_validation.py::validate_projects_config` enforces (`proj_cfg.get("machine")`) — confirm during build (Nit #6).
- **`_resolve_environment()`** (new): explicit `SENTRY_ENVIRONMENT` wins; else `"production"` for designated bridge machines; else `"development"`.
- **`configure_sentry()`** (modified): replace the inline `os.getenv("SENTRY_ENVIRONMENT", "production")` with `_resolve_environment()`; update the module/function docstring so it no longer describes #1834's gating as "layered on later" (that legacy note is now false — remove it, per no-legacy-traces).
  - **BUILD REQUIREMENT (Critique Concern #2 — observability):** Before `sentry_sdk.init`, emit one `logger.info` stating the resolved environment plus its raw inputs (ComputerName and the matched project key, or `"none"`), so a wrong tag on a real machine is diagnosable from `logs/bridge.log` / `logs/worker.log` without needing Sentry itself.
- **`ui/app.py` VALOR-BX fix**: `claude_auth["subscription_type"]` → `claude_auth.get("subscription_type")` at both `:822` and `:882`. Kept bundled in this plan (Critique Concern #3): the issue explicitly lists VALOR-BX as related cleanup and it is a two-line change; shipping it in the same PR keeps the issue's acceptance criteria closed in one place. The `.get()` consumer-side fix is the chosen approach; the `_get_claude_auth_health()` error dicts are NOT modified (see Rabbit Holes).

### Flow

Dev machine starts real bridge (no pytest) → `configure_sentry("bridge", ...)` → DSN present, no guard → `_resolve_environment()` → not a designated bridge machine → `environment="development"` → `sentry_sdk.init(environment="development")` → production project stays clean.

Production bridge machine → same path → `_is_designated_bridge_machine()` True → `environment="production"` → unchanged from today.

### Technical Approach

- **Machine-ownership check is self-contained.** Do not import `ui.data.machine` into `monitoring` (wrong layer direction). Mirror the existing lightweight `projects.json` + `scutil` read already duplicated four times in the repo. Path: `~/Desktop/Valor/projects.json` (same literal `ui/data/machine.py` uses).
- **Explicit override precedence.** `SENTRY_ENVIRONMENT`, when set, always wins — preserves the existing escape hatch and lets a designated machine be forced to a staging environment if ever needed.
- **Fail-to-development is deliberate.** If `projects.json` is unreadable, resolve to `"development"`. A real production bridge machine always has a readable `projects.json` (the bridge cannot route without it), so the only machines that hit the fallback are misconfigured/dev ones — exactly the ones that should not report as production. Document this inline.
- **Pytest guard is upstream of environment resolution**, so `_resolve_environment()` never runs under a test unless a test explicitly clears `PYTEST_CURRENT_TEST` (the existing init-path tests do). Those tests must therefore control machine ownership deterministically — see Test Impact.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_is_designated_bridge_machine()` wraps its `projects.json` read + `scutil` call in `try/except` returning `False`. Add a test that patches the read to raise and asserts the helper returns `False` (and thus `_resolve_environment()` → `"development"`) — observable behavior, not a swallowed pass.
- [ ] `configure_sentry()`'s existing early-returns (pytest/CI guard, no-DSN) already have coverage in `test_worker_sentry_init.py`; unchanged.

### Empty/Invalid Input Handling
- [ ] `_is_designated_bridge_machine()` with empty ComputerName (`scutil` returns "") must return `False` (no project matches an empty machine name) — add assertion.
- [ ] `_get_claude_auth_health()` error return (no `subscription_type` key) feeding `dashboard_json`: assert `.get()` yields `None`, not `KeyError`.

### Error State Rendering
- [ ] `dashboard_json` and `health` must return a valid response with `claude_auth_subscription_type: null` when Claude auth is unavailable — test the error branch, not just the healthy branch.

## Test Impact

- [ ] `tests/unit/test_worker_sentry_init.py::test_configure_sentry_inits_when_dsn_set_and_no_guard` — UPDATE: it clears `PYTEST_CURRENT_TEST` and asserts `environment == "production"`. After this change, environment resolves via machine ownership, so this test would depend on the real host's `projects.json`. Make it deterministic: patch `_is_designated_bridge_machine` → `True` (or set `SENTRY_ENVIRONMENT=production`) and keep the `production` assertion.
- [ ] `tests/unit/test_worker_sentry_init.py::test_configure_sentry_passes_before_send_for_bridge` — UPDATE: also clears the guard and reaches the init path; pin machine ownership (patch `_is_designated_bridge_machine` → `True`) so it doesn't read the host `projects.json`. `before_send` assertion unchanged.
- [ ] `tests/unit/test_worker_sentry_init.py::test_configure_sentry_passes_before_send_for_worker` — UPDATE: same pinning as above.
- [ ] `tests/unit/test_worker_sentry_init.py::test_configure_sentry_skips_under_pytest_even_with_dsn` — NO CHANGE (AC#3, already passing; guard short-circuits before environment resolution).
- [ ] `tests/unit/test_worker_sentry_init.py` — ADD: `test_environment_development_when_not_bridge_machine` (ownership False, no `SENTRY_ENVIRONMENT` → `environment == "development"`), `test_environment_production_when_bridge_machine` (ownership True → `"production"`), `test_explicit_sentry_environment_overrides` (`SENTRY_ENVIRONMENT=staging` wins over ownership), and a failure-path test (`projects.json` read raises → `"development"`).
- [ ] `tests/unit/test_ui_app.py` — ADD (or extend): a test that `dashboard_json`/`health` do not raise when `_get_claude_auth_health()` returns the error dict lacking `subscription_type`, asserting the response carries `claude_auth_subscription_type: None`.

## Rabbit Holes

- **Centralizing machine-name resolution.** There are already four `_get_machine_name()`/`get_machine_name()` copies. Consolidating them into `config/machine.py` is a tempting cross-cutting refactor but is out of scope and would balloon the blast radius. Match the existing duplication pattern instead.
- **Richer environment taxonomy.** Do not invent `staging`/`ci`/`local` tiers or per-project environments. Two buckets (`production` vs `development`) plus the explicit `SENTRY_ENVIRONMENT` override fully satisfy the issue.
- **Reworking `before_send` / orphan-noise filtering.** #1835/#1948 own that seam. Leave it alone.
- **Fixing `_get_claude_auth_health()`'s error dicts to include the key.** The issue prescribes the `.get()` consumer-side fix; adding the key to both error returns is a second, redundant change. Prefer the single prescribed `.get()` fix.

## Risks

### Risk 1: A real production bridge machine mis-resolves to `development`
**Impact:** Genuine production errors would be under-tagged, hiding them from the production environment filter.
**Mitigation:** `_is_designated_bridge_machine()` matches `projects.<key>.machine` against ComputerName exactly as the existing `get_machine_project_keys()` does — the production bridge machine already relies on this exact match to route messages, so if it resolved wrongly the bridge would already be non-functional. `SENTRY_ENVIRONMENT` remains an explicit override for any edge case.

### Risk 2: Existing init-path tests become host-dependent
**Impact:** Flaky tests that pass/fail based on whether the test host owns a project.
**Mitigation:** Explicitly enumerated in Test Impact — all three init-path tests are updated to pin machine ownership deterministically before this ships.

## Race Conditions

No race conditions identified — `configure_sentry()` runs once synchronously at process startup, before any concurrency; the ownership check is a synchronous file read. The `ui/app.py` fix is a synchronous dict read within a request handler.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1997] Centralizing the four duplicated `_get_machine_name()` implementations into one shared `config/machine.py` helper — pre-existing tech debt, not caused by this issue; would expand blast radius across `ui/`, `bridge/`, `tools/`, and `scripts/`. Filed as #1997.
- Anti-criterion for the above: this plan must NOT add an `import` of `ui.data.machine` into `monitoring/sentry_config.py` (would invert layering). Verified in the Verification table.

## Update System

No update system changes required — the gate reads `projects.json`, which is already iCloud-synced and present on every machine by existing wiring. No new dependencies, config files, or migrations. `SENTRY_ENVIRONMENT` is an already-existing optional env var.

## Agent Integration

No agent integration required — this is a bridge/worker-internal initialization change plus a dashboard-endpoint bug fix. No new CLI entry point, MCP tool, or bridge import surface.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/sentry-triage.md` (or add a short `## Environment gating` subsection) describing: pytest/CI suppression (from #1948), and the new dev-vs-prod resolution (designated bridge machine → `production`; other machines → `development`; `SENTRY_ENVIRONMENT` overrides). Cross-reference `docs/features/single-machine-ownership.md`.
- [ ] Add a one-line entry to `docs/features/README.md` index if a new doc file is created (not needed if folded into `sentry-triage.md`).

### Inline Documentation
- [ ] Update the `configure_sentry()` docstring to remove the now-false "#1834's dev-vs-prod gating layers on top later" note and describe the implemented resolution.
- [ ] Comment the fail-to-development rationale in `_is_designated_bridge_machine()`.

## Success Criteria

- [ ] On a machine that owns no project (no `projects.<key>.machine` match), with `SENTRY_DSN` set and no `SENTRY_ENVIRONMENT`, `configure_sentry()` calls `sentry_sdk.init` with `environment="development"`.
- [ ] On a designated bridge machine, `environment="production"` (unchanged behavior).
- [ ] Explicit `SENTRY_ENVIRONMENT` always wins.
- [ ] `dashboard_json` and `health` return successfully with `claude_auth_subscription_type: null` when `claude auth status` fails (no `KeyError`).
- [ ] `monitoring/sentry_config.py` does not import `ui.data.machine`.
- [ ] Operational (Nit #5): `configure_sentry()` logs the resolved environment + inputs at INFO so a non-bridge dev machine's real bridge/worker run is confirmable to carry `environment=development` from its process log.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).

## Team Orchestration

### Team Members

- **Builder (sentry-gate)**
  - Name: sentry-gate-builder
  - Role: Implement `_resolve_environment()` + `_is_designated_bridge_machine()` in `monitoring/sentry_config.py`, wire into `configure_sentry()`, fix `ui/app.py` VALOR-BX, update docstrings.
  - Agent Type: builder
  - Resume: true

- **Test builder (sentry-gate)**
  - Name: sentry-gate-tester
  - Role: Update the three host-dependent init-path tests to pin ownership; add environment-resolution tests + failure-path test; add the `ui/app.py` error-branch test.
  - Agent Type: test-engineer
  - Resume: true

- **Validator (sentry-gate)**
  - Name: sentry-gate-validator
  - Role: Verify all success criteria + Verification table.
  - Agent Type: validator
  - Resume: true

### Available Agent Types

Tier 1 core agents suffice (`builder`, `test-engineer`, `validator`, `documentarian`).

## Step by Step Tasks

### 1. Implement environment resolution
- **Task ID**: build-sentry-gate
- **Depends On**: none
- **Validates**: tests/unit/test_worker_sentry_init.py
- **Assigned To**: sentry-gate-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `_is_designated_bridge_machine()` to `monitoring/sentry_config.py`: read `~/Desktop/Valor/projects.json` + `scutil --get ComputerName`, return `True` iff a `projects.<key>.machine` matches (case-insensitive); `try/except` → `False`.
- Add `_resolve_environment()`: explicit `SENTRY_ENVIRONMENT` wins; else `production` if designated bridge machine; else `development`.
- Replace the inline `os.getenv("SENTRY_ENVIRONMENT", "production")` in `configure_sentry()` with `_resolve_environment()`.
- Update the module + `configure_sentry()` docstrings; remove the stale "#1834 layers on later" note.

### 2. Fix VALOR-BX KeyError
- **Task ID**: build-ui-keyerror
- **Depends On**: none
- **Validates**: tests/unit/test_ui_app.py
- **Assigned To**: sentry-gate-builder
- **Agent Type**: builder
- **Parallel**: true
- Change `claude_auth["subscription_type"]` → `claude_auth.get("subscription_type")` at `ui/app.py:822` and `:882`.

### 3. Tests
- **Task ID**: build-tests
- **Depends On**: build-sentry-gate, build-ui-keyerror
- **Assigned To**: sentry-gate-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Update the three init-path tests in `test_worker_sentry_init.py` to pin `_is_designated_bridge_machine` (patch → True) so they don't read the host `projects.json`.
- Add: development-default, production-on-bridge, explicit-override, and `projects.json`-read-raises (→ development) tests.
- Add a `test_ui_app.py` test asserting `dashboard_json`/`health` do not raise when claude auth health lacks `subscription_type`.

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: build-sentry-gate
- **Assigned To**: sentry-gate-tester (or documentarian)
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/sentry-triage.md` with the environment-gating subsection; cross-reference single-machine-ownership.

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-tests, document-feature
- **Assigned To**: sentry-gate-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the Verification table; confirm all success criteria.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Sentry init tests pass | `pytest tests/unit/test_worker_sentry_init.py -q` | exit code 0 |
| UI app tests pass | `pytest tests/unit/test_ui_app.py -q` | exit code 0 |
| No ui import in monitoring (layering anti-criterion) | `grep -c "ui.data.machine\|from ui" monitoring/sentry_config.py` | match count == 0 |
| No hard-index of subscription_type remains | `grep -c 'claude_auth\["subscription_type"\]' ui/app.py` | match count == 0 |
| Environment resolver present | `grep -c "_resolve_environment" monitoring/sentry_config.py` | output > 0 |
| Lint clean | `python -m ruff check monitoring/sentry_config.py ui/app.py` | exit code 0 |
| Format clean | `python -m ruff format --check monitoring/sentry_config.py ui/app.py` | exit code 0 |

## Critique Results

Verdict: **READY TO BUILD (with concerns)** — 0 blockers, 4 concerns, 2 nits. All embedded below.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Risk/Consistency | Empty ComputerName + empty `machine` field → `""==""` mis-tags production | Key Elements build requirement | Add `if not machine: return False` before iterating; mirror `get_machine_project_keys` incl. `exists()` + case-insensitive compare |
| CONCERN | Risk (Skeptic/Operator) | No observability of resolved environment | Key Elements build requirement + Success Criteria | `logger.info` resolved env + ComputerName + matched project key before `sentry_sdk.init` |
| CONCERN | Scope (Simplifier/User) | VALOR-BX bundled with env gate | Key Elements decision | Kept bundled: issue lists it as related cleanup, two-line change, closes ACs in one PR |
| CONCERN | Consistency | Open Q #2 contradicts Rabbit Holes | Open Questions resolved | Deleted OQ#2; `.get()` only, error dicts untouched |
| NIT | Scope (User) | Success criteria all unit-level | Success Criteria | Added operational INFO-log confirmation criterion |
| NIT | History (Archaeologist) | Predicate alignment with config_validation unverified | Key Elements build requirement | Confirm predicate matches `validate_projects_config` during build |

---

## Open Questions

_Resolved during critique revision pass:_
1. **Environment label for dev machines**: RESOLVED — use `"development"` (two-bucket taxonomy; no machine-name-suffixed labels, per Rabbit Holes "Richer environment taxonomy").
2. **VALOR-BX fix breadth**: RESOLVED — consumer-side `.get()` at the two call sites only; the `_get_claude_auth_health()` error dicts are NOT modified (Rabbit Holes already forecloses adding the key). Deleted the prior contradiction flagged by Critique Concern #4.
