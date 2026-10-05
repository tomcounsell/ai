# Follow-ups N1 to N3 from the 2.1 review

Three small fixes from the Round 13 checks of the m2-1 review. All three are
bug fixes; none adds a check, gate, hook, round, or review step.

Stakes: medium. Critique rounds: 1. Review rounds: 1.

## N1: record() keeps the parts of a turn that can be stored

In `core/session.py`, when the row `turn.collected` is refused where it is
written, even after `_answered()` has replaced each error and effect entry
Postgres refuses alone, `_reduce()` drops the largest part left and writes
the row again through `_written()` (alone, in its savepoint), dropping the
next largest each time, until the row is stored. Drops accumulate, so the
parts that fit are kept. If no reduction is stored, `_bare()` writes the
row that holds nothing the turn wrote.

The parts are each error, each effect entry, the question, the no-question
statement, the delivery note, the plan, and the screens. Sizes are JSON
lengths of the row `_answered()` returned, largest first.

- An error is replaced by "an error is unrecorded" with Postgres's reason.
- An effect entry keeps `effect_id`, `kind`, and `file`, with an `error`
  holding Postgres's reason, so the effect rows the broker already wrote
  still match it.
- Any other part is set to null (the screens to an empty list).
- The reduced row's verdict is `idle`, or `failed` for a turn that did not
  finish, as `_bare()` sets it; its candidate is null, and no
  `question.asked` or `plan.written` is written beside it. A `candidate`
  verdict with no candidate, or `asked` with no question row, is a row the
  fold cannot act on.
- The row written carries one more error, "the turn's signals: X, Y
  dropped to record the rest:" with Postgres's reason; that line is in the
  row `_written()` is asked to store, so the row tested is the row written.

The case this covers: two effect requests that each fit jsonb alone but not
together. One is kept whole, the other is answered with its reason, and every
effect keeps its `effect_id` and `kind`.

Tests in `tests/test_session.py`:
`test_a_turn_collected_row_past_what_jsonb_holds_is_written_reduced` (two
refusals), `test_drops_are_cumulative_until_the_row_is_stored` (three
refusals, two dropped), `test_a_reduced_question_turn_asks_nothing_and_folds`
(a question beside two refusals), and `test_the_largest_part_is_dropped_first`
(the question, the plan, the screens, and an error, each the largest part).

## N2: docs/harnesses.md lists the plan.json errors

The `.valor/plan.json` row of the signal table in `docs/harnesses.md` lists
what makes a plan an error, matching `_plan()`: a missing `path`,
`critique_rounds`, or `review_rounds`; a `path` that is not a string; a count
that is not an integer (booleans are not accepted) or is outside 0, 1, 2; a
`stakes` that is present and not a string; a `scope` that is present and not
a list; a plan not committed or changed in the working tree.

## N3: one vocabulary for JSON types in errors

`kind()` in `core/signals.py` names a JSON value's type with its article:
"an object", "a list", "a string", "a boolean", "null", "a number".
`core/session.py` (`_plan`) and `core/signals.py` (`_request`) both use it, so
an effect request with an integer `action_type` is answered "is a number",
and a dict is "an object".

Tests: `tests/test_signals.py` asserts "is an object" for a dict,
"is a list" for a list, and "is a number" for an integer.

## Record: patch round 1

Findings from the blind review and the test check of 5a5ae487f, all fixed.

- The N1 test passed `b""` to `json.dumps` and never reached the code. It
  now uses `_refusing_turn`'s own `ok.json`, and asserts the kept refusal
  is whole (`kind` `refused`, its full reason) and the dropped one answered.
- The reduced row's verdict is `idle` or `failed`, with no follow row and
  no candidate; the clarify probe (question plus two oversize refusals) is
  `test_a_reduced_question_turn_asks_nothing_and_folds`, which asserts the
  fold reads the row with nothing ignored.
- Drops accumulate until the row stores; the three-refusal probe is
  `test_drops_are_cumulative_until_the_row_is_stored`, and `ok.json` keeps
  its `effect_id` and `kind`.
- The error line naming the drops is in the row passed to `_written()`,
  which writes it in a savepoint; a refusal there drops the next part
  instead of aborting the record transaction. The candidate is not a part:
  it is always null in a reduced row, so dropping it frees nothing.
- `ruff format` and `ruff check` are clean on `core/session.py` and
  `tests/test_session.py`.
- `docs/harnesses.md` and `docs/data.md` say the same.
