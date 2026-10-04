---
tracking: none
slug: m1-5r-runners
type: bug
status: merged
critique_rounds: 0
review_rounds: 1
---

# 1.5r: the kernel registers the review runner

A bug fix against [m1-5-emulator.md](m1-5-emulator.md) ("What builds now
and what waits"), which has review carried by a registered runner at the
takeover gate and docs hand-played while governance's entry check fails.

## The bug

`runners()` in `core/__main__.py` registers judge, the working states,
critique and test. `fresh.review_runner` is built and merged but not
registered, so a task reaching `checks` stops with `NO RUNNER` for review.
1.4c part one was to register review unconditionally. In 1.5's takeover
gate ([m1-5-emulator-records.md](m1-5-emulator-records.md), "Takeover
gate" and "Lead's decisions on the gate") every review verdict was played
by a fresh subagent through `python -m core verdict`, unmetered.

Docs is not part of the bug. The 1.5 plan has docs hand-played (the
driver's `NO RUNNER` exit) while governance's calibration entry check
fails, and 1.4b left it unregistered on purpose
([m1-4b-records.md](m1-4b-records.md), "Calibration on the real ledger").

## Done

- `runners()` registers `Check.REVIEW: fresh.review_runner(_fresh_for,
  judgement_port)`, so every stage the state machine schedules has a
  runner except `checks.docs`. `waiting`, `merge`, `merged` and `stopped`
  are the router's own.
- Review asks governance through the same judgement port as judge and
  breadth.
- `python -m core verdict` and `verdicts.MANUAL_STAGES` stay: the command
  records docs by hand and refuses review, test, build and every other
  stage that has a runner.
- Docs that said review waits for governance's entry check say it is
  registered: `core/README.md`, `docs/architecture.md`,
  `docs/harnesses.md`, `docs/judgement-layer.md`, `docs/mission.md`,
  `docs/sdlc-state-machine.md`, and the docstrings in `core/__main__.py`
  and `core/verdicts.py`. They keep docs registered once governance passes
  its entry check.

## Tests

- `test_every_stage_the_state_machine_schedules_has_a_runner_but_docs`:
  the keys of `runners()` and `RUNNERS` are every `State` the router does
  not settle itself and every `Check` except `DOCS`, which the plan leaves
  manual while governance's entry check fails.
- `test_review_is_run_by_the_kernels_runner_and_docs_pauses_the_driver_for_its_verdict`:
  one step of the emulator driver (`replay.step`) against a provisioned
  task, its `core run` the real router with the kernel's own `runners()`
  (fresh sessions played by the scripted session, governance the local
  upstream), carries critique, build, test and review, review recorded on
  the `session` leg, and pauses on `NO RUNNER` for docs; `python -m core
  verdict TASK docs no_change` then records docs on the `manual` leg and
  the task reaches `merge`.
- Both fail without the fix. `tests/test_judgement.py` asserts docs is
  unregistered, as before. `tests/test_pipeline.py`'s verdict command test
  asserts the command refuses test, review and build and records docs by
  hand through a review round to a held merge. `tests/test_live_session.py`
  (live only) expects review on the `session` leg and docs by hand.

## Stakes

Kernel: the runners every task runs at `checks`. `critique_rounds: 0` for
a bug fix of this size is the lead's call, recorded here; `review_rounds:
1` is the lead's call.

## Left out

- Registering docs. It waits for a governance calibration record on the
  real ledger that passes its entry check (`GOVERNANCE.calibrated` is
  None).
- The confirming kernel run of pop-a with review carried by the kernel is
  the lead's, after merge.

## Build

Branch `m1-5r-runners` from fba1da1da. The first build registered review
and docs; suite 1318 passed, 23 skipped.

## Patch round 1

The lead's decision: register review only. Registering docs was the
first build's reading of the gate decision, and it contradicts the 1.5
plan (docs hand-played while governance's entry check fails) and 1.4b's
choice to leave docs unregistered; only the missing review registration
is the bug. This round removes `Check.DOCS` from `runners()`, restores the
docs lines that say docs waits for governance's entry check, restores
`test_judgement`'s assertion that docs is unregistered, and has the
pipeline, live session and emulator tests expect review on the session
leg and docs through `NO RUNNER` and the verdict command.

Suite: 1318 passed, 23 skipped. `ruff check` and `ruff format --check`
clean.

## Round 1 checks

- test-1-5r: gaps. No test passed `--finding` to `python -m core verdict`
  once the hand-played review verdict was removed. The lead added it at
  merge: the docs verdict's finding parses and lands in the ledger.
- review-1-5r: pass. Governance boolean no; no invented caps.
- docs-1-5r: updated, 9e89a84f1.

## Merged

- Lead suite on 792d997f2: 1378 passed, 25 skipped; ruff clean.
- Backup `valor_rebuild-20261004T152636Z.dump`.
- `valor-cori-rebuild` fast-forwarded a8e7f67d0 to 792d997f2.
- Rollout: one kernel run of pop-a, which shows a real review session's
  spending.
