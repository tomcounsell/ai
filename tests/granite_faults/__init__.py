"""Shared support package for the granite failure-simulation test harness.

Substrate A (deterministic fault injection at the seams) lives here as
reusable building blocks:

- ``mocks`` — the ``IdleResult`` factory and ``MagicMock(spec=PTYDriver)``
  builders, generalized from the piecemeal copies that grew inside
  ``tests/unit/granite_container/test_container.py``. One source of truth
  for the mock-driver pattern the container-loop tests share.
- ``scenarios`` — the ``FaultScenario`` injectors, one per failure class
  in the plan's Substrate A table. Each replays a recorded / synthetic
  frame stream (or scripts a classifier) at a real granite seam and
  asserts the recovery / detection path fires deterministically.
- ``fixtures/`` — small, reviewable recorded-frame seed fixtures the
  replay-and-mutate injectors (class 1 especially) consume.

Everything here is test-only. No production ``agent/granite_container/``
behavior is touched — this is white-box test infrastructure by design
(plan Risks: "over-coupling to current private internals ... acceptable").
"""
