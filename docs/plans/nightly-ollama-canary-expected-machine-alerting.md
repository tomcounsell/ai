---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-02
tracking: https://github.com/tomcounsell/ai/issues/1841
last_comment_id:
---

# Nightly Ollama Canary — Expected-Machine Alerting

## Problem

The nightly ollama canary is Substrate B of the granite failure-simulation harness
(#1837 / PR #1839): `scripts/nightly_regression_tests.py::run_ollama_suite()` runs the
real `claude` binary against a local ollama model to catch Claude Code TUI changes
before they wedge production granite. Its whole reason to exist is fighting silent
failure — yet three of its own exit paths fail silently.

**Current behavior** (`scripts/nightly_regression_tests.py` at `7592dd25`):

- **Self-skip** (`run_ollama_suite()`, lines 430-435): when
  `ollama_reachable_for_nightly()` is False, it logs and returns — no Telegram alert.
  If ollama dies or the pinned qwen tag is removed on the canary machine, the canary
  stops running forever with no signal. A canary that silently stops is
  indistinguishable from a passing one.
- **Subprocess exception** (lines 471-473): `log(...)` and `return`, no alert.
- **JSON-report parse failure** (lines 475-479): `log(...)` and `return`, no alert.

Correct-and-out-of-scope paths (leave untouched): the **timeout** path already alerts
(lines 464-470); the **version-drift** canary already alerts-and-still-runs
(`claude_canary_alert()`, lines 401-418); baseline/regression messages (lines 495-510).

**Desired outcome:** on the machine designated to run the canary, a night where the
ollama suite did not actually execute (skip, subprocess failure, or unparseable report)
produces a loud Telegram signal. Everywhere else, self-skip stays silent as designed.

## Freshness Check

**Baseline commit:** `7592dd256f61186129b21e7328d75bad4a4f2757`
**Issue filed at:** 2026-07-02T04:25:49Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `scripts/nightly_regression_tests.py:430-435` — self-skip is log-only — still holds (exact match).
- `scripts/nightly_regression_tests.py:471-473` — subprocess-exception is log-only — still holds (exact match).
- `scripts/nightly_regression_tests.py:475-479` — report-parse-failure is log-only — still holds (exact match).
- `scripts/nightly_regression_tests.py:464-470` — timeout DOES alert — still holds (out of scope).
- `scripts/nightly_regression_tests.py:401-418` — `claude_canary_alert()` drift alert-and-still-run — still holds (out of scope).
- `scripts/nightly_regression_tests.py:346-357` — `NIGHTLY_MODEL_EXPECTED` skip-visibility gate (the pattern to mirror, from #1740) — still holds.
- `scripts/nightly_regression_tests.py:364-380` — `ollama_reachable_for_nightly()` monkeypatchable seam — still holds.

**Cited sibling issues/PRs re-checked:**
- #1740 — the `NIGHTLY_MODEL_EXPECTED` gate this plan mirrors; landed, code present at 346-357.
- #1837 / PR #1839 — harness that introduced the ollama canary; landed.
- PR #1840 — the qwen backend pin; landed as `7592dd25` (current HEAD). This is the exact
  change that makes reachability machine-specific, which drives the env-var decision below.

**Commits on main since issue was filed (touching referenced files):** none
(`git log --since=2026-07-02T04:25:49Z -- scripts/nightly_regression_tests.py tests/unit/test_nightly_regression_tests.py` is empty; HEAD `7592dd25` was committed 02:45 UTC, before the issue).

**Active plans in `docs/plans/` overlapping this area:** none. The two completed harness
plans (`docs/plans/completed/granite_failure_simulation_harness.md`,
`granite_realloop_test_hardening.md`) are shipped, not active.

**Notes:** No drift. All line references exact. Proceeding on the issue's premises unchanged.

## Prior Art

- **#1740**: introduced the `NIGHTLY_MODEL_EXPECTED` skip-visibility gate for the granite
  real-loop suite (`scripts/nightly_regression_tests.py:346-357`). Succeeded and in
  production. This plan extends the same idea to the ollama suite — mirror, don't reinvent.
- **#1837 / PR #1839**: built the granite failure-simulation harness, including
  `run_ollama_suite()` with its self-skip design. The self-skip was deliberately log-only
  because Substrate B must never hard-fail the nightly run on a machine without a model.
  This plan keeps that "never hard-fail" property while adding a *soft* alert on the
  expected machine.
- **PR #1840**: pinned the ollama backend to `qwen*` tags with no fallback. This is why
  ollama reachability now differs from anthropic-model reachability — a bridge machine can
  reach the anthropic model (`NIGHTLY_MODEL_EXPECTED=1`) while having no qwen tag. Directly
  informs the env-var decision (see Technical Approach).

No prior *failed* attempt exists — this is the first pass at ollama-suite alerting. The
"Why Previous Fixes Failed" section is therefore omitted.

## Research

No relevant external findings — proceeding with codebase context and training data. This
is a purely internal change to a launchd-scheduled script (no external libraries, APIs, or
ecosystem patterns).

## Data Flow

1. **Entry point**: launchd fires the nightly job (or an operator runs
   `python scripts/nightly_regression_tests.py`), which calls `main()` → `run_ollama_suite()`.
2. **Reachability probe**: `ollama_reachable_for_nightly()` returns True/False.
   - **False → self-skip** (lines 430-435). *This is the first silent path.*
3. **Subprocess run**: `subprocess.run([... pytest ... --json-report ...])`.
   - **`subprocess.TimeoutExpired` → alerts** (already correct, lines 464-470).
   - **Other exception → return** (lines 471-473). *Second silent path.*
4. **Report parse**: `json.loads(Path(PYTEST_JSON_OLLAMA_TMP).read_text())`.
   - **`FileNotFoundError` / `JSONDecodeError` → return** (lines 475-479). *Third silent path.*
5. **Output**: on the happy path, baseline/regression/clean messages via `send_telegram`
   and state persisted to `LAST_RUN_OLLAMA_FILE`. Under this change, the three silent paths
   gain a conditional `send_telegram` gated on the expected-machine flag; the delivery
   mechanism (`send_telegram` → `valor-telegram send --chat "Eng: Valor"`) is unchanged.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1 (confirm the env-var decision and machine-provisioning stance)
- Review rounds: 1 (lite pipeline pass — single-file change plus unit tests)

## Prerequisites

No prerequisites — this work has no external dependencies. It edits one script and its
unit test file; the unit tests monkeypatch the reachability seam and never touch a real
ollama or the network.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| pytest available | `python -m pytest --version` | Run the unit suite |

## Solution

### Key Elements

- **Per-suite expected-machine flag** (`NIGHTLY_OLLAMA_EXPECTED`): a new env var, distinct
  from `NIGHTLY_MODEL_EXPECTED`, that marks *this* machine as the one expected to actually
  run the ollama canary. When truthy, the three silent paths alert; when unset, they stay
  log-only exactly as today.
- **Alert on self-skip**: when the flag is set and the reachability probe returns False,
  send a Telegram alert carrying the skip reason, in addition to the existing log line.
- **Alert on subprocess failure**: when the flag is set and the pytest subprocess raises a
  non-timeout exception, send a Telegram alert.
- **Alert on parse failure**: when the flag is set and the JSON report can't be read/parsed,
  send a Telegram alert.

### Flow

Nightly launchd fire → `run_ollama_suite()` → reachability probe

- Probe False, flag unset → log "skipped, unreachable", return (silent, unchanged)
- Probe False, flag set → log + **Telegram: "ollama canary did not run — unreachable (reason)"**, return
- Probe True → run pytest subprocess
  - subprocess raises, flag set → log + **Telegram: "ollama canary subprocess failed"**, return
  - report unparseable, flag set → log + **Telegram: "ollama canary report unparseable"**, return
  - happy path → baseline/regression/clean messages (unchanged)

### Technical Approach

- **Introduce `NIGHTLY_OLLAMA_EXPECTED`, do NOT reuse `NIGHTLY_MODEL_EXPECTED`.** This is the
  central decision. `NIGHTLY_MODEL_EXPECTED=1` is set in `com.valor.nightly-tests.plist`,
  which `install_nightly_tests.sh` installs **only on bridge-role machines** (where the
  anthropic model is reachable). After the PR #1840 qwen pin, those bridge machines have no
  qwen tag, so the ollama suite *legitimately* self-skips there every night. Reusing
  `NIGHTLY_MODEL_EXPECTED` would turn every bridge machine into a nightly false-alarm
  generator. A separate `NIGHTLY_OLLAMA_EXPECTED` var, set only on the designated ollama
  canary machine (the skills/dev machine per harness plan Resolved Decision #2), keeps
  ollama reachability decoupled from anthropic-model reachability.
- Read the flag once at the top of `run_ollama_suite()`, mirroring the existing
  `nightly_model_expected = bool(os.environ.get("NIGHTLY_MODEL_EXPECTED", "").strip())`
  pattern at line 350. Name it `ollama_expected`.
- In each of the three silent paths, keep the existing `log(...)` call (self-skip must still
  be silent-but-logged when unexpected) and add a guarded
  `if ollama_expected: send_telegram(<reason>, dry_run=dry_run)`. The alert message must name
  the failure mode and carry the reason (e.g. the exception string, or "ollama/model
  unreachable") so the reader knows which path fired.
- **Reachability decoupling is by design, not by omission:** the self-skip must remain a
  soft return (never `raise`) on both machine types — an unexpected machine stays silent, an
  expected machine gets a soft alert but the nightly run still proceeds to the TTFT gate.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The subprocess-exception handler (`except Exception` at line 471) and the parse-failure
      handler (`except (FileNotFoundError, json.JSONDecodeError)` at line 477) each gain a
      test asserting the **observable** behavior: alert fires when `NIGHTLY_OLLAMA_EXPECTED`
      is set, and does NOT fire when it's unset (log-only). The existing swallow-to-log
      behavior is preserved on the unexpected machine.
- [ ] The reachability-probe swallow (`ollama_reachable_for_nightly`, lines 378-380) already
      has a test (`test_probe_swallows_import_errors_to_false`) — unchanged.

### Empty/Invalid Input Handling
- [ ] `NIGHTLY_OLLAMA_EXPECTED` empty string / whitespace-only / unset all resolve to falsy
      via `.strip()` truthiness, mirroring the `NIGHTLY_MODEL_EXPECTED` idiom. Add a test that
      an empty-string value stays log-only (does not alert).
- [ ] Not agent-output processing — no silent-loop risk.

### Error State Rendering
- [ ] User-visible output here is the Telegram alert. Each new alert path gets a test
      asserting `send_telegram` is called with a message containing the failure reason
      (skip reason / exception text / parse-error text), so the signal is actionable, not
      an empty ping.

## Test Impact

- [ ] `tests/unit/test_nightly_regression_tests.py::TestRunOllamaSuiteSelfSkip::test_unreachable_skips_with_logged_reason_and_no_subprocess`
      — UPDATE: this test currently asserts `mock_telegram.assert_not_called()` with no
      explicit env control. Add `monkeypatch.delenv("NIGHTLY_OLLAMA_EXPECTED", raising=False)`
      (or `patch.dict(os.environ, ...)`) so it explicitly exercises the **unexpected-machine**
      case and stays green regardless of ambient env. Its assertion (skip logged, no
      subprocess, no alert) remains correct for the unexpected machine.
- [ ] `tests/unit/test_nightly_regression_tests.py` — ADD a new test class
      `TestRunOllamaSuiteExpectedMachine` covering: (a) self-skip + flag set → alert with
      reason; (b) subprocess-exception + flag set → alert, and + flag unset → no alert;
      (c) parse-failure + flag set → alert, and + flag unset → no alert; (d) empty-string
      flag → no alert. All via the `ollama_reachable_for_nightly` / `subprocess` /
      `send_telegram` patch seams already used in the file.

No other existing tests are affected — the change is additive (a new env-gated branch) and
does not alter the happy-path baseline/regression/clean/timeout/drift behavior those tests
cover.

## Rabbit Holes

- **Staleness check (`LAST_RUN_OLLAMA_FILE.run_at` older than N days → alert).** Tempting,
  but a trap for this issue. The failure mode it catches — launchd never firing the job at
  all — cannot be self-detected from inside the script (if the script never runs, nothing
  runs the staleness check). The "ran but skipped for N nights" mode is already covered by
  the self-skip alert this plan adds. Adding staleness also forces a "how old is too old"
  policy decision and a firing point outside `run_ollama_suite()`. Leave it out; it is a
  separate concern with marginal added value here.
- **Reworking the install/gating so the canary machine auto-provisions the flag.** The
  nightly plist is bridge-role-gated and the canary is a non-bridge (skills/dev) machine;
  untangling that is a scope explosion. Keep provisioning as a documented operator step
  (see Update System) rather than re-architecting `install_nightly_tests.sh`.
- **Adding the new var to `com.valor.nightly-tests.plist`.** Do NOT — that plist lands on
  bridge machines that lack qwen, so setting `NIGHTLY_OLLAMA_EXPECTED=1` there would
  reintroduce the alert-storm this plan exists to avoid.

## Risks

### Risk 1: Alert storm on the wrong machine
**Impact:** If `NIGHTLY_OLLAMA_EXPECTED` were set on a machine without qwen (e.g. by
copying it into the shared plist), every nightly run would fire a false self-skip alert.
**Mitigation:** The whole point of a *separate* var is that it is set only on the designated
canary machine. Documentation explicitly warns against adding it to the shared plist; the
shared plist is left untouched by this plan.

### Risk 2: Inert alerting (flag never set anywhere)
**Impact:** Code ships correct but no machine sets `NIGHTLY_OLLAMA_EXPECTED`, so the canary
stays silent — the original problem persists in practice.
**Mitigation:** Documentation gives the exact operator step to set the flag on the canary
machine, and this is called out as an Open Question / `[EXTERNAL]` No-Go so the human
completing the PR knows the code is inert until the flag is provisioned. Unit tests prove
the *behavior* is correct; provisioning is the operator's one-time action.

## Race Conditions

No race conditions identified — `run_ollama_suite()` runs synchronously in a single-threaded
launchd-invoked process; the env var is read once and the alert paths are sequential. The
pytest work happens in a child subprocess with its own report file, already isolated.

## No-Gos (Out of Scope)

- [EXTERNAL] Setting `NIGHTLY_OLLAMA_EXPECTED=1` on the physical canary (skills/dev) machine
  and ensuring that machine actually runs the nightly job. This needs a human action on a
  specific machine the agent cannot reach; the harness plan (Resolved Decision #2) already
  treats ollama/model presence as a documented prerequisite, not automated. The PR ships the
  code + tests + docs; the operator flips the flag.
- Staleness check for the launchd-never-fired failure mode — described in Rabbit Holes as a
  distinct concern with marginal added value; not deferred as a tracked promise, deliberately
  left unbuilt.

## Update System

No `scripts/update/run.py` or `migrations.py` changes required, and no change to
`install_nightly_tests.sh` or `com.valor.nightly-tests.plist`. The new
`NIGHTLY_OLLAMA_EXPECTED` var must NOT go into the shared bridge-machine plist (it would
alert-storm machines without qwen). Provisioning is a one-time operator step on the
designated canary machine, documented in `docs/features/nightly-regression-tests.md`
(e.g. export the var in that machine's launchd job / shell profile / `.env` used by the
nightly invocation). This is intentionally manual — it mirrors the existing bridge-role
gating stance for Substrate B.

## Agent Integration

No agent integration required — this is a launchd-scheduled bridge-internal script. It has
no MCP surface, no `.mcp.json` entry, and the bridge does not import it. The only outward
surface is the existing `send_telegram` helper (already wired to `valor-telegram`), which
this plan reuses without modification.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/nightly-regression-tests.md`: add a "Granite Ollama Substrate B
      Suite" alert-conditions table (self-skip / subprocess-failure / parse-failure rows,
      each "expected machine only"), and a new subsection explaining `NIGHTLY_OLLAMA_EXPECTED`
      — what it gates, why it is a **separate** var from `NIGHTLY_MODEL_EXPECTED` (qwen pin →
      ollama reachability differs from anthropic-model reachability), and the operator step to
      set it on the canary machine plus the explicit warning not to add it to the shared plist.
- [ ] No `docs/features/README.md` index change needed — the nightly-regression-tests entry
      already exists.

### Inline Documentation
- [ ] Comment the new env-gate read in `run_ollama_suite()` referencing #1841 and #1740,
      mirroring the existing #1740 comment at line 346, and note *why* it is a distinct var.

## Success Criteria

- [ ] On an expected machine (`NIGHTLY_OLLAMA_EXPECTED` truthy), the self-skip path sends a
      Telegram alert carrying the skip reason; with the var unset, the path stays log-only.
- [ ] Subprocess-exception and report-parse-failure paths alert on an expected machine and
      stay log-only when the var is unset.
- [ ] Existing behavior preserved: timeout alert, version-drift alert-and-still-run, and
      baseline/regression/clean messages are byte-for-byte unchanged.
- [ ] Unit coverage via the existing monkeypatchable seams (`ollama_reachable_for_nightly`,
      `subprocess`, `send_telegram`) asserts alert-on-skip / alert-on-subprocess-failure /
      alert-on-parse-failure fire only when the expected gate is set.
- [ ] `com.valor.nightly-tests.plist` is unchanged (verified — no `NIGHTLY_OLLAMA_EXPECTED`
      added to the shared bridge plist).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (ollama-alert)**
  - Name: ollama-alert-builder
  - Role: Add the `NIGHTLY_OLLAMA_EXPECTED` gate and the three alert branches to
    `run_ollama_suite()`; add/adjust unit tests.
  - Agent Type: builder
  - Resume: true

- **Validator (ollama-alert)**
  - Name: ollama-alert-validator
  - Role: Verify success criteria and Verification rows; confirm plist untouched and
    existing happy-path tests still green.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: nightly-docs
  - Role: Update `docs/features/nightly-regression-tests.md`.
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Add the expected-machine gate and alert branches
- **Task ID**: build-ollama-alert
- **Depends On**: none
- **Validates**: tests/unit/test_nightly_regression_tests.py
- **Assigned To**: ollama-alert-builder
- **Agent Type**: builder
- **Parallel**: false
- In `scripts/nightly_regression_tests.py::run_ollama_suite()`, read
  `ollama_expected = bool(os.environ.get("NIGHTLY_OLLAMA_EXPECTED", "").strip())` near the
  top, with a comment referencing #1841 / #1740 and noting why it is a distinct var.
- Self-skip path (lines 430-435): keep the `log(...)`; add
  `if ollama_expected: send_telegram(<skip-reason message>, dry_run=dry_run)` before `return`.
- Subprocess-exception path (lines 471-473): keep the `log(...)`; add
  `if ollama_expected: send_telegram(<subprocess-failed message incl. exc>, dry_run=dry_run)`.
- Report-parse-failure path (lines 475-479): keep the `log(...)`; add
  `if ollama_expected: send_telegram(<report-unparseable message incl. exc>, dry_run=dry_run)`.
- Do NOT touch the timeout path, drift canary, baseline/regression/clean logic, or the plist.

### 2. Add / update unit tests
- **Task ID**: build-ollama-tests
- **Depends On**: build-ollama-alert
- **Validates**: tests/unit/test_nightly_regression_tests.py
- **Assigned To**: ollama-alert-builder
- **Agent Type**: builder
- **Parallel**: false
- UPDATE `TestRunOllamaSuiteSelfSkip::test_unreachable_skips_with_logged_reason_and_no_subprocess`
  to explicitly clear `NIGHTLY_OLLAMA_EXPECTED` (unexpected-machine case).
- ADD `TestRunOllamaSuiteExpectedMachine` covering skip/subprocess/parse alert-on-expected and
  no-alert-on-unset (including empty-string value), using the existing patch seams.

### 3. Validation
- **Task ID**: validate-ollama-alert
- **Depends On**: build-ollama-tests
- **Assigned To**: ollama-alert-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the Verification commands below; confirm all success criteria; confirm plist unchanged.

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-ollama-alert
- **Assigned To**: nightly-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/nightly-regression-tests.md` per the Documentation section.

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: ollama-alert-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the full unit suite for the file and confirm docs + criteria complete.

## Verification

| Name | Command | Expected |
|------|---------|----------|
| Ollama suite tests pass | `python -m pytest tests/unit/test_nightly_regression_tests.py -q` | `exit code 0` |
| Expected-machine test class exists | `grep -c "TestRunOllamaSuiteExpectedMachine" tests/unit/test_nightly_regression_tests.py` | `output > 0` |
| New env gate present | `grep -c "NIGHTLY_OLLAMA_EXPECTED" scripts/nightly_regression_tests.py` | `output > 0` |
| Shared plist NOT contaminated (anti-criterion) | `grep -c "NIGHTLY_OLLAMA_EXPECTED" com.valor.nightly-tests.plist` | `match count == 0` |
| Docs mention the new var | `grep -c "NIGHTLY_OLLAMA_EXPECTED" docs/features/nightly-regression-tests.md` | `output > 0` |

## Open Questions

1. **Env-var naming — confirm `NIGHTLY_OLLAMA_EXPECTED` (per-suite) over reusing
   `NIGHTLY_MODEL_EXPECTED`.** The plan resolves this to a separate var (rationale: qwen pin
   decouples ollama reachability from anthropic-model reachability; the shared var lives on
   bridge machines that lack qwen and would alert-storm). Flagging for explicit sign-off since
   the issue left it as a planning decision.
2. **Machine provisioning stance.** The plan ships code + tests + docs and treats setting the
   flag on the canary machine as a manual `[EXTERNAL]` operator step (the nightly plist is
   bridge-role-gated and the canary is a skills/dev machine). Acceptable to leave provisioning
   manual, or do you want the install path reworked to auto-set it on the canary machine (a
   larger change than the "small single-file" scope this issue describes)?
3. **Staleness check.** Confirmed out of scope (can't self-detect launchd-never-fired from
   inside the script; the self-skip alert covers the "ran but skipped" mode). Agree to drop?
