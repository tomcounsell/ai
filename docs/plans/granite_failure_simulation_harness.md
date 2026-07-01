---
status: Planning
type: chore
appetite: Medium
owner: Valor
created: 2026-07-02
tracking: https://github.com/tomcounsell/ai/issues/1837
last_comment_id:
---

# Granite Failure-Simulation Test Harness

## Problem

Granite (the production session runner) drives the real Claude Code TUI over PTYs and detects turn boundaries by **screen-scraping the painted terminal for quiescence** (`agent/granite_container/pty_driver.py` → `read_until_idle()`). This scraping is coupled to specific Claude Code UI — the `bypassPermissions` bottom bar, the trust-folder dialog, the `/login` screen — that Anthropic ships and revises. When that UI changes, the idle heuristic breaks and sessions **wedge silently**.

The most damaging failures are silent: a session wedges, crashes, or loops without surfacing. Today those failures are **neither reproducible locally nor covered by deterministic tests** — there is no centralized fault-injection framework, and the only real-loop test burns the real Anthropic backend so it can't run at volume. Fixes ship "and hope" instead of red-test-first.

**Desired outcome:** a harness that turns each silent production failure into a reproducible local test, exercisable for free and at volume, so every downstream granite-hardening bet lands behind a failing injected-fault test.

## Freshness Check

Baseline: `main` @ `85830aff` (2026-07-02). Issue #1837 filed same day.

| Item | Disposition |
|------|-------------|
| `pty_driver.py` `IDLE_BAR`/`read_until_idle()`/`_build_env()` refs | **Unchanged** — verified present at cited locations |
| `tests/unit/granite_container/conftest.py` `GRANITE_LIVE_SMOKE` gate | **Unchanged** |
| `scripts/nightly_regression_tests.py` integration seam | **Unchanged** |
| #1740 (real-loop seam) | CLOSED — its test is the nightly seam to extend |
| #1688 (Stop-hook turn signal) | OPEN — referenced downstream, not a dependency |

No commits touched `agent/granite_container/`, `scripts/nightly_regression_tests.py`, or `tests/` since the issue was filed. **Disposition: Unchanged.**

## Prior Art

- **No centralized fault-injection framework exists.** `tests/unit/granite_container/` monkeypatches piecemeal: `_mock_driver()` (returns `MagicMock(spec=PTYDriver)` with a configurable `read_until_idle()`), `_idle_result()` factory in `test_container.py`, and a `poll_steering()` stub in `test_granite_mid_run_steering_unit.py`. These are the patterns the new framework generalizes.
- **#1740 (CLOSED)** — "Test-hardening: granite real-loop coverage that would have caught the canned-fallback regression." Added `tests/integration/test_granite_container_loop.py`, wired into `scripts/nightly_regression_tests.py` as an isolated subprocess gated on `_model_reachable()`. This is the seam this plan extends to run the ollama-backed suite.
- **#1688 (OPEN)** — "Hook-driven turn returns for granite PTY shuttle." The deterministic turn-boundary fix this harness will make TDD-able. Out of scope here.
- No prior attempt to build a fault-injection or free-substrate E2E harness for granite. Greenfield.

## Research

**Query:** ollama + Claude Code integration mechanism (env vars, TUI fidelity, cost).

**Finding** (source: https://docs.ollama.com/integrations/claude-code): `ollama launch claude` runs the **real `claude` binary** pointed at ollama's Anthropic-compatible endpoint by setting three env vars:

```
ANTHROPIC_BASE_URL=http://localhost:11434
ANTHROPIC_AUTH_TOKEN=ollama
ANTHROPIC_API_KEY=""
```

Permission prompts and permission rules remain operational (the TUI renders normally). Local models → free/unlimited. This is the **exact inverse** of production's `_build_env()` blanking (`pty_driver.py:272-311`), which blanks `ANTHROPIC_BASE_URL` to force the real endpoint. Informs the whole Substrate B design: granite already owns these three vars, so pointing them at ollama is the entire "free test backend" switch.

**Caveat (informs known gaps):** the doc does not explicitly guarantee the trust/permission/login dialogs render byte-identically to the Anthropic-backed binary — hence the mandatory fidelity check (Success Criteria #5). Endpoint-specific behaviors (OAuth expiry, real `/login` OAuth flow, 429s) are not reproducible via ollama and belong in Substrate A seam injection.

## Architectural Impact

Purely additive, test-only. No production code path changes. New code lives under `tests/` (a `granite_faults/` support package + test modules) and a small extension to `scripts/nightly_regression_tests.py`. The one non-test artifact is a golden-fixtures directory checked into the repo. No Popoto models, no migrations, no bridge/worker code touched.

## Appetite

**Medium.** Two substrates + a golden-recorder + nightly wiring, but greenfield and test-only with clear insertion points. Bounded by explicit No-Gos: this plan builds the *harness*, not the downstream fixes it enables.

## Prerequisites

- `ollama` installed and on PATH, with a small model pulled for the E2E substrate (e.g. `ollama pull gemma3` — exact tag resolved in Task 0). The classifier model `granite4.1:3b` is already a worker prerequisite.
- The `claude` binary present (already required by granite). Substrate B pins the version under test.
- No new Python dependencies expected — `pexpect` and `pytest` are already in the venv.

## Solution

Two complementary substrates behind a shared support package `tests/granite_faults/`.

### Substrate A — deterministic fault injection at the seams (CI-fast, no env gate, no model)

A `FaultScenario` support module that generalizes the existing piecemeal mocks into named injectors, each targeting one seam and asserting the recovery/detection path fires:

| Failure class | Injection point | Assertion |
|---|---|---|
| Turn-detection wedge | feed `read_until_idle()` a recorded frame stream with `IDLE_BAR` text mutated/removed | detector reports no-idle deterministically; no infinite wait |
| Startup-dialog / `/login` wedge | synthetic startup frames → `startup_parser.parse_startup_frame()` | correct dialog classification / `startup_unresolved` path |
| Process hang / U-state | stub PTY child that blocks `os.read` | bounded-read + respawn fires (per #1767/#1815); no unbounded block |
| Loop / non-convergence | scripted PM/classifier always emitting `[/dev]` | `DEFAULT_MAX_TURNS`/wrap-up guard terminates with a user-facing message |
| Crash | killed ollama classifier / corrupt JSONL / `send_cb` raises | fail-loud (`exception` exit + anomaly event), not silent |
| Silent no-progress tail | stub emits N frames then goes quiet | a make-silent-failures-loud detector hook fires within N (test asserts the *seam* exists and is observable; the detector itself is downstream) |

These reuse `IdleResult` and `MagicMock(spec=PTYDriver)` patterns; deterministic, sub-second, run in the default unit suite.

### Substrate B — ollama-backed real Claude Code E2E (free, unlimited, high-fidelity)

A fixture that launches the **real** `claude` binary with the three ollama env vars set (inverting `_build_env()`), behind a new `GRANITE_OLLAMA_SMOKE=1` env gate alongside the existing `GRANITE_LIVE_SMOKE` guard in `conftest.py`. Runs real PTY + TUI + startup dialogs + hooks against a free local model. Asserts a session completes without wedging and surfaces the real exit reason. Doubles as a **canary for new `claude` binary releases** — the exact thing that breaks production.

### Golden-recorder

A small tool/fixture that runs a Substrate B session and captures the frame stream + JSONL transcript + hook events into `tests/granite_faults/fixtures/`. Those recordings become the inputs the Substrate A replay+mutate tests consume — record real, replay-and-mutate deterministic.

### Nightly wiring + canary

Extend `scripts/nightly_regression_tests.py` to run the ollama-backed suite as an isolated subprocess (mirroring the existing granite integration invocation), gated so it self-skips when ollama/the model is unreachable, and pin the `claude` version it asserts against.

## Failure Path Test Strategy

This plan *is* a failure-path test strategy — but its own failure paths must also be handled:

- **ollama unreachable / model not pulled** → Substrate B and the nightly canary **self-skip with a logged reason** (mirror `_model_reachable()`), never hard-fail CI. Substrate A has no such dependency and always runs.
- **Golden fixtures drift from a new `claude` version** → the recorder is re-runnable; a documented `make`-style refresh step regenerates fixtures. Substrate A tests assert against the *mutation*, not exact bytes, to limit brittleness.
- **A stub PTY child leaks** → reuse the `conftest.py` autouse spawn-guard and ensure every injector tears down its stub in a fixture finalizer; assert no orphan PIDs after the suite.
- **The harness gives false confidence** → each injector must first be shown to FAIL against a deliberately-broken recovery path (red-first), proving it actually detects the fault, before asserting green.

## Test Impact

- [ ] `tests/unit/granite_container/test_container.py` — UPDATE: extract `_mock_driver()` / `_idle_result()` into the shared `tests/granite_faults/` support module and re-import, so both the existing tests and the new injectors share one source. Behavior of existing tests unchanged.
- [ ] `tests/unit/granite_container/conftest.py` — UPDATE: add the `GRANITE_OLLAMA_SMOKE` gate alongside the existing `GRANITE_LIVE_SMOKE` spawn guard (additive; default-off preserves current behavior).
- [ ] `scripts/nightly_regression_tests.py` — UPDATE: register the ollama-backed suite as an isolated subprocess with self-skip on unreachable ollama.
- New test modules under `tests/unit/granite_container/` (Substrate A) and `tests/integration/` (Substrate B) are net-new — no existing tests replaced or deleted.

## Rabbit Holes

- **Perfect TUI-frame fidelity.** Do NOT try to make ollama-rendered frames byte-identical to Anthropic-backed frames. Substrate A asserts against mutations of *recorded* frames; Substrate B asserts *behavioral* completion, not pixels.
- **Reproducing endpoint-specific failures via ollama.** OAuth expiry / real `/login` OAuth / 429s are seam-injected in Substrate A, not chased through ollama.
- **A general-purpose terminal-emulator model.** The injectors feed recorded/synthetic byte streams; do not build a full vt100 emulator.
- **Building the downstream detector here.** The silent-no-progress test asserts the observable seam exists; the detector implementation is a separate plan.

## Risks

- **ollama/model availability on CI machines** — mitigated by self-skip; Substrate A carries the deterministic coverage that must always run.
- **Fixture brittleness across `claude` versions** — mitigated by mutation-based assertions + a documented re-record step + version pinning in the canary.
- **Over-coupling to current private internals** (`IdleResult` shape, `read_until_idle` signature) — acceptable: this is white-box test infrastructure by design; it should break loudly if those seams change, which is a feature.

## No-Gos (Out of Scope)

The following are the bets this harness *enables*, explicitly NOT built here:
- The make-silent-failures-loud detector (root-cause-agnostic no-progress alarm + recovery).
- The Stop-hook turn signal (#1688).
- Startup pre-authorization (removing the startup-dialog scrape).
- The Shape B pluggable transport / `ClaudeSDKClient` metered hedge leg.

No production `agent/granite_container/` behavior changes. No Popoto models, no migrations.

## Update System

No `scripts/update/run.py` or `migrations.py` changes required for the core harness. One conditional follow-up: if the nightly canary is to run on bridge machines, the `/update` flow may need to ensure `ollama` + the pinned model are present — captured as a documented prerequisite, not automated in this plan (bridge-role gating mirrors `install_nightly_tests.sh`). No new secrets, no `.env` additions.

## Agent Integration

No agent integration required. This is test-only infrastructure — no new CLI entry point in `pyproject.toml [project.scripts]`, no `.mcp.json` change, no bridge import. The harness is invoked by `pytest` and `scripts/nightly_regression_tests.py` only; the agent never calls it at runtime.

## Documentation

- [ ] Create `docs/features/granite-failure-simulation-harness.md` describing the two substrates, the `GRANITE_OLLAMA_SMOKE` gate, the golden-recorder + re-record step, the ollama env-var inversion, and the fidelity-check result.
- [ ] Add an entry to `tests/README.md` (feature-marker index) for the new fault-injection module and E2E gate.
- [ ] Cross-link from `docs/features/granite-pty-production.md` (Observability / testing section) to the new harness doc.

## Success Criteria

1. Substrate A: one deterministic injector per failure class in the table, each red-first-proven (fails against a broken recovery path) then green, running in the default unit suite in sub-second time.
2. Substrate B: a `GRANITE_OLLAMA_SMOKE=1`-gated E2E test that launches the real `claude` binary against ollama and asserts a session completes without wedging.
3. Golden-recorder produces reusable fixtures under `tests/granite_faults/fixtures/`, consumed by Substrate A.
4. `scripts/nightly_regression_tests.py` runs the ollama-backed suite with self-skip on unreachable ollama and version-pinned canary assertion.
5. Documented fidelity check confirming (or noting divergence in) trust/permission/login dialog rendering under ollama.
6. No orphan `claude`/PTY processes after the suite; existing granite tests still pass unchanged.

## Step by Step Tasks

1. **Task 0 — spike/prereq:** confirm `ollama launch claude --model <tag>` works locally against the pinned `claude` version; resolve the model tag; run the ~10-min dialog-fidelity check (Success Criterion 5).
2. Create `tests/granite_faults/` support package; extract `_mock_driver()` / `_idle_result()` from `test_container.py` into it and re-point existing imports.
3. Build the `FaultScenario` injectors (Substrate A), one per failure class, red-first then green.
4. Build the golden-recorder and capture an initial fixture set under `tests/granite_faults/fixtures/`.
5. Build the Substrate B ollama-backed E2E fixture + `GRANITE_OLLAMA_SMOKE` gate in `conftest.py`.
6. Extend `scripts/nightly_regression_tests.py` with the ollama suite (self-skip + version-pinned canary).
7. Write docs (`docs/features/granite-failure-simulation-harness.md`, `tests/README.md` entry, cross-link).
8. Verify: full unit suite green, no orphan PIDs, existing granite tests unchanged.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Substrate A injectors pass | `pytest tests/unit/granite_container/ -n0` | exit 0, all injectors green |
| Existing granite tests unchanged | `pytest tests/unit/granite_container/ -n0 -k "not fault"` | exit 0 |
| Substrate B E2E (ollama machine) | `GRANITE_OLLAMA_SMOKE=1 pytest tests/integration/ -k ollama` | real session completes, no wedge |
| No leaked processes | `scripts/pytest-clean.sh tests/unit/granite_container/ && ps -o pid,comm \| grep -c claude` | no orphan `claude`/xdist PIDs |
| Nightly self-skips cleanly | `python scripts/nightly_regression_tests.py --dry-run` | ollama suite listed, skips when unreachable |

Additionally: each Substrate A injector is demonstrated **red-first** (temporarily break the recovery path, see the test fail) in the PR description, and the dialog-fidelity note is recorded in the feature doc.

## Open Questions

1. **Model tag for Substrate B** — `gemma3`, `gemma2`, or another small local model? (Resolved in Task 0; any small instruct model that the `claude` binary accepts over the ollama endpoint is fine — reasoning quality is irrelevant, we test the interface.)
2. **Should the nightly ollama canary run on bridge machines or only the skills/dev machine?** Leaning skills/dev only (bridge machines shouldn't spend cycles on it), which keeps the `/update` change to zero. Confirm.
3. **Fixture storage** — commit golden fixtures to the repo (reproducible, reviewable) vs. regenerate on demand? Leaning commit, with a documented re-record step. Confirm.
