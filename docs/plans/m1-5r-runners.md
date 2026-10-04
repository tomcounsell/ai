---
tracking: none
slug: m1-5r-runners
type: bug
status: built
critique_rounds: 0
review_rounds: 1
---

# 1.5r: the kernel registers the review and docs runners

A bug fix against [m1-5-emulator.md](m1-5-emulator.md) ("What builds now
and what waits"), which has review carried by a registered runner at the
takeover gate.

## The bug

`runners()` in `core/__main__.py` registers judge, the working states,
critique and test. `fresh.review_runner` and `fresh.docs_runner` are built
and merged but not registered, so a task reaching `checks` stops with `NO
RUNNER` for review and docs. In 1.5's takeover gate
([m1-5-emulator-records.md](m1-5-emulator-records.md), "Takeover gate" and
"Lead's decisions on the gate") every review and docs verdict was played
by a fresh subagent through `python -m core verdict`, unmetered, and a
hand-played docs stage could not commit a doc edit.

## Done

- `runners()` maps every stage the state machine schedules to a runner:
  `judge`, `clarify`, `plan`, `critique`, `build`, `patch`, and the checks
  `test`, `review` (`fresh.review_runner(_fresh_for, judgement_port)`) and
  `docs` (`fresh.docs_runner(_fresh_for, judgement_port)`). `waiting`,
  `merge`, `merged` and `stopped` are the router's own.
- Review and docs ask governance through the same judgement port as
  judge and breadth.
- Docs that said review and docs wait for governance's entry check say
  they are registered: `core/README.md`, `docs/architecture.md`,
  `docs/harnesses.md`, `docs/judgement-layer.md`, `docs/mission.md`,
  `docs/sdlc-state-machine.md`, `docs/emulator.md`, and the docstrings in
  `core/verdicts.py` and `core/judgement_tasks.py`.

## Tests

- `test_every_stage_the_state_machine_schedules_has_a_runner`: the keys of
  `runners()` and `RUNNERS` are every `State` the router does not settle
  itself, and every `Check`.
- `test_review_and_docs_are_run_by_the_kernels_runners_through_one_driver_step`:
  one step of the emulator driver (`replay.step`) against a provisioned
  task, its `core run` the real router with the kernel's own `runners()`
  (fresh sessions played by the scripted session, governance the local
  upstream), carries critique, build, test, review and docs to
  `DELIVERED`, with review and docs recorded on the `session` leg and no
  verdict by hand.
- Both fail without the fix. `tests/test_judgement.py` no longer asserts
  docs has no runner; `tests/test_pipeline.py`'s verdict command test
  asserts the command refuses test, review, docs and build, each having a
  runner; `tests/test_live_session.py` (live only) expects
  `DELIVERED` and session-leg review and docs instead of hand verdicts.

## Stakes

Kernel: the runners every task runs at `checks`. `critique_rounds: 0` for
a bug fix of this size is the lead's call, recorded here.

## Left out

- `python -m core verdict` and `verdicts.MANUAL_STAGES` stay. With every
  stage registered the command refuses each, so it is the manual channel
  for a runner taken out again. 1.4c's plan names its deletion as part of
  registering review and docs; that is a larger change (the manual leg's
  tests, the emulator's `NO RUNNER` handling) and is the lead's to
  schedule.
- Governance has no calibration record that passes its entry check
  (`GOVERNANCE.calibrated` is None). Its answers already make governance
  instances wherever a review or docs verdict cites them; registering the
  runners changes who asks, not how an answer is read.
- The confirming kernel run of pop-a with review and docs carried by the
  kernel is the lead's, after merge.

## Build

Branch `m1-5r-runners` from fba1da1da. Suite: 1318 passed, 23 skipped.
`ruff check` and `ruff format --check` clean. The first suite run failed
one test, `test_the_verdict_command_records_by_hand_and_requests_the_merge`,
which recorded review and docs through `python -m core verdict`; with both
registered the command refuses them, and the test now asserts that.
