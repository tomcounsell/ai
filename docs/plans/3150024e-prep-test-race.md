# Prep-order test race (verify red at d7362c3a2)

`tests/test_call.py::test_prep_runs_on_the_queue_moving_not_on_talk_and_questions_go_first`
failed under `-n auto` with order `['prep', 'prep', 'prep', 'When did the import ship?']`.

**Cause (test, not code).** The test pushes `open_decisions`, `settle` and `ask`
to the fake Live session, each `push` sleeping a fixed 0.1 s, then sets the
gate that holds the second prep. The call's receive loop handles those tool
calls in order, but `open_decisions` opens a database connection and `ask`
awaits `worker_ready`; under parallel load they are not done in 0.3 s. The
gate opens with the question not yet on `Call.questions`, so the prep queued
by `open_decisions` runs before it. `Call.work` (core/call.py:1793) already
puts a question asked meanwhile first; that rule holds only for a question
actually queued, which is what the docstring says.

**Fix.** Before `gate.set()`, wait until the call has taken the question:
`assert await until(lambda: "when did the import ship" in c.the.asked)`
(`Call.asked` is keyed by the question's lowercase words and is set in the
same step that queues it). Since tool calls are handled in order, this also
means the `open_decisions` and second `settle` were handled. No change to
`core/call.py`. Adds no governance.

**Out of scope.** Other `test_call.py` sleeps; the production queue.

**Tests.** Reproduce first: delay the fake's tool handling (e.g. a 0.3 s
sleep in `worker_ready` for this test) and see the red with the old test and
green with the fix, then drop the delay. Then run `tests/test_call.py` alone
and the full suite with `-n auto`, the test green in both.

Stakes: a test-only change, reversible. Critique rounds 0, review rounds 0.
