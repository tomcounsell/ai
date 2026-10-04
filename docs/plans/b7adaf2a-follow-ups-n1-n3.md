# Follow-ups N1 to N3 from the 2.1 review

Three small fixes from the Round 13 checks of the m2-1 review. All three are
bug fixes; none adds a check, gate, hook, round, or review step.

Stakes: medium. Critique rounds: 1. Review rounds: 1.

## N1: record() keeps the parts of a turn that can be stored

In `core/session.py`, when the row `turn.collected` is refused where it is
written, even after `_answered()` has replaced each error and effect entry
Postgres refuses alone, `_reduce()` drops the largest part left and asks
again, until the row is stored. If no reduction is stored, `_bare()` writes the
row that holds nothing the turn wrote.

The parts are each error, each effect entry, the question, the no-question
statement, the plan, the delivery note, the screens, and the candidate; the
verdict is never dropped. Sizes are JSON lengths, largest first.

- An error is replaced by "an error is unrecorded" with Postgres's reason.
- An effect entry keeps `effect_id`, `kind`, and `file`, with an `error`
  holding Postgres's reason, so the effect rows the broker already wrote
  still match it.
- Any other part is set to null.
- The reduced row's candidate is null, and one error names the part dropped
  and gives Postgres's reason.

The case this covers: two effect requests that each fit jsonb alone but not
together. One is kept whole, the other is answered with its reason, and every
effect keeps its `effect_id` and `kind`.

Test: `test_a_turn_collected_row_past_what_jsonb_holds_is_written_reduced` in
`tests/test_session.py`.

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
