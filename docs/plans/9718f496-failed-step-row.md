# Plan: a failed step leaves a row, and a verdict after prose is read

Task 9718f496dee7. A kernel bug fix. Revised after critique round 1.

## The problem, as the code has it

- `fresh.final_verdict` (core/fresh.py:704) accepts a bare JSON object, or
  a message that is a single fenced block, cut at `rfind("```")`. A
  reviewer that writes prose first and then a fenced verdict is refused
  with "the final message is not a JSON object". Only the review runner
  reads its verdict this way (core/fresh.py:1017). Critique and docs read
  `.valor/verdict.json` through `workspace.read_verdict` (core/fresh.py:248,
  561); they are not touched.
- Every runner's `{"status": "failed", ...}` goes back through
  `router.step` to `serve.Kernel._step` with nothing written. `_step` marks
  the rows up to `latest` seen, so the task never steps again until another
  row arrives, and the ledger has nothing to say why. An exception from a
  step goes the same way: `_step` catches it, logs it to the kernel log,
  sets `{"status": "failed"}`, and marks the rows seen; it is not parked.
- Incident (a) is the first path. Incident (b), a review that ended after
  its governance rows with no row at all, is one of the review runner's
  `failed(...)` returns between governance and the turn (manifests,
  verify, checkout, services, inputs; core/fresh.py:846-990) or an
  exception there. Each returns or raises through `router._once`, so one
  write in `router.step` covers all of them. That task's ledger is not
  reachable from this workspace, so the cause of (b) is unverified, and
  the delivery says so.

## What will be built

1. **`final_verdict` (review only).** Fences are found by line: a fence
   line is a line whose stripped text is ```` ``` ```` or ```` ```json ````
   (opening) or ```` ``` ```` (closing); a ```` ``` ```` substring inside a
   line is never a fence. A JSON string cannot hold a raw newline, so a
   fence line cannot occur inside the verdict's strings, and a finding that
   quotes code with ```` ``` ```` does not cut the block. The verdict is the
   whole message as a JSON object (as now), or else the last fenced block,
   which must close the message (only whitespace after its closing fence
   line) and whose content must parse to a JSON object. Prose before the
   block is allowed and ignored. Refused, with a reason naming which: no
   JSON object and no fenced block; a last block that is not a JSON object
   (an earlier block is never fallen back to, so a block the reviewer
   quoted is never taken for its verdict); text after the last block. The
   docstring, and the review prompt wording that describes the final
   message (if it says "only" a JSON object, it stays true; checked in
   `skills/` and core/fresh.py), stay accurate. `workspace.read_verdict`
   and the critique and docs prompts are not edited.

2. **`step.failed`.** `router.step` writes one `step.failed` row when the
   step's outcome is `failed`, whether a runner returned it or `_once`
   raised. Payload: `state` (the folded state), `check` (`test`, `review`,
   `docs`, or null outside `checks`; `_once` reports which check it ran),
   `reason` (the turn's `result` text, or the exception's one-line form),
   `turn_id` (the turn's id, or null when no turn ran). The write follows
   the rule every runner write follows: `alive()` first, and when the run
   lock is gone the step returns `lock lost` with no row; then, under
   `task:<id>`'s lock in a transaction, as `workspace.failed` is. Not
   written for `moved`, `stopped`, preempted, `lock lost`, `already
   running`, or a task found stopped.
   - **Returned failure:** after the row is written, `tasks.status` is read
     again, so the returned `out["state"]` carries `failed_step`, and
     `python -m core run` prints it.
   - **Exception:** the row is written, then the original exception is
     re-raised. If writing the row itself fails (the database being down is
     a likely cause of both), the write's failure is logged and the
     original exception is re-raised, never the write's, so `serve`'s log
     still names the real cause.
   Because `router.step` writes it, `python -m core run` and `serve`
   record it alike.

3. **Status shows it.** `tasks.status` gains `failed_step`: the latest
   `step.failed` row's payload with its row id and time, while it is the
   task's latest row other than the quiet ones (notices and gateway rows,
   today's `serve.QUIET`, moved to `core/ledger.py` so both read one list);
   otherwise null. `python -m core status` prints it with the rest; the
   status page (`ui/app.py` task page) shows it in the status block and the
   row in its ledger table, with no page change beyond that.

4. **Docs.** `docs/data.md` gains the `step.failed` row.
   `docs/architecture.md` (the supervisor turn), `docs/sdlc-state-machine.md`
   (the control loop, the working-session `failed` outcome, the checks
   fork, and the review branch's final-message wording), and
   `core/README.md` (the serve paragraph) say a failed step writes
   `step.failed` and the task then waits for a row it did not write.
   `core/README.md` today says a task "whose step or release raised, is
   parked ... tried again on its next row or the next `serve_tick_s`
   wake"; for a raised step that is false (`_step` catches it and marks
   the rows seen), so the paragraph is corrected to keep parking for
   services held elsewhere and a raised release, and to say a failed or
   raised step writes `step.failed` and waits for a new row.

## The decision: the task waits for a new row

A failed step does not step again on the next serve tick. It steps again
when a row it did not write arrives (Tom's steer, an answer, a grant), or
on `python -m core run`, or once on a kernel restart, as today. Why:

- docs/architecture.md, the supervisor turn: "A task steps again only when
  a row it did not write arrives ... so one event is one step." The
  `step.failed` row is written through `ledger.append`, so it lands in
  `ledger.WRITTEN` and is marked seen like any row a step writes; no serve
  change is needed. `machine.fold` puts the unknown type in
  `Fold.ignored`, so the fold does not change.
- docs/sdlc-state-machine.md: a `failed` turn ends the run, and "the next
  run retries from the ledger"; a task needs Tom on "a failed turn".
- A retry every `serve_tick_s` would rerun a frontier review against a
  failure that is usually deterministic (incident (a) would have failed
  identically every tick for seven hours). Spending is metered and never
  capped, so nothing would bound that loop, and a retry limit has no
  source to set it from. Waiting is the only bound that adds no limit.

The fix for the seven hours is that the stall is now in the record, and
fix 1 removes the failure that caused it.

## Out of scope

- `workspace.read_verdict`, and the critique and docs prompts.
- A notice to Tom on `step.failed`. No failed turn sends one today; adding
  one changes what reaches Tom, which is his call. Named in the delivery.
- `lock lost` and `already running` also end a step with no row; they are
  not failures of the task and are left as they are.
- Any retry, backoff, or limit. Any new check or gate.
- Rerunning incident tasks 234852e586f4 and 13fe23bd0686; a steer on each
  does that once this lands.

## Tests

- `tests/test_review.py::test_the_final_message_is_the_verdict_object`,
  new cases: prose then a ```` ```json ```` block is read; prose then a bare
  fence is read; prose then a ```` ```json ```` block whose finding text
  contains ```` ``` ```` (inside the JSON string) is read whole; two blocks,
  the last one is the verdict; prose then a block holding an array, or
  invalid JSON, is refused, even when an earlier block is a valid object;
  a block followed by prose is refused; prose with no block is still
  refused; the existing cases still pass.
- A review runner (scripted, no live model) whose turn ends with a final
  message that is not a verdict: the step writes one `step.failed` with
  `check` `review`, a reason beginning `no verdict:`, and the turn's id;
  the step's returned `state` carries `failed_step` (so `core run` prints
  it); `tasks.status(...)["failed_step"]` carries it; the `core status`
  output and the task page show it.
- A runner that raises: one `step.failed` with the exception's line and
  `turn_id` null; the original exception still reaches the caller.
- A runner that raises and the `step.failed` append raises too: the
  original exception, not the append's, reaches the caller, and the
  append's failure is logged.
- A failed step whose run lock is gone (`alive()` false): `lock lost`, no
  row.
- A working-session turn that fails (`build`): `step.failed` with `check`
  null and the turn id.
- No row for `moved`, `stopped`, preempted, `lock lost`.
- `serve`: after a failed step the next tick does not step the task; a
  `message.steered` row steps it again (beside
  `test_a_failed_job_parks_the_task` in `tests/test_serve.py`).
- `failed_step` goes null once a later non-quiet row is written, and stays
  set across a later notice or gateway row.
- The full suite, with base failures separated from new ones.

## Stakes

A kernel change to what every failed step records and to how the review
verdict is read; a wrong parse would let a quoted block pass as a verdict,
and a wrong row would mislead every stalled task's record.
Critique rounds 1, review rounds 2.
