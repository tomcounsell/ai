# Follow-ups N1 to N3 from the 2.1 review

Three small fixes from the Round 13 checks of the m2-1 review.

## N1: record()'s bare fallback row keeps recordable parts

**What it is.** In `core/session.py`, the `record()` function's bare fallback row (`_bare`) currently drops every part of the turn's result when the row after the broker is refused, even when only some parts caused the refusal. The fix: use the same part-dropping strategy as `_storable()` to drop largest parts one by one until the row fits, keeping what can be stored.

**The scenario.** The review's N1 refers to a case where Postgres refuses the row only when parts are together, not individually. For example, two large effect entries (a.json and b.json) each fit jsonb alone but together exceed its limit. After `_answered()` splits them, each effect entry is separately storable but the whole row is still refused.

**How it works.** When the row is refused after `_answered()` has tried, apply the same loop from `_storable()` (lines 364-371):
1. Sort all parts by size (question, plan, done, no_question, screens, each effect entry)
2. Drop the largest part, mark it with `UNSTORABLE` and Postgres's reason
3. Ask `ledger.unstorable()` again
4. Repeat until the row stores or no parts are left
5. If nothing fits, fall back to today's fully bare row

This keeps ok effects (with effect_id and kind) and whichever of a.json/b.json fits, while marking the other with its refusal reason but keeping its effect_id and file name.

**The follow row.** The question.asked or plan.written follow row: today `_bare()` drops it. Keep the same behavior (no follow row), so the idle/failed verdict stands without a question or plan that was never ledgered.

**Test.** Update the existing test at tests/test_session.py:933 (`test_a_turn_collected_row_past_what_jsonb_holds_is_written_bare`):
- Assert that the turn.collected row is written (not bare with "with all it holds")
- Assert that at least one effect (ok.json) is present with kind 'pending' and its effect_id
- Assert that exactly one of a.json/b.json keeps its full entry, the other has effect_id/kind/file but an error
- Assert verdict is idle
- The test currently asserts `effects == []`, which will be false; update it to check the new structure

## N2: docs/harnesses.md documents plan field type errors

**What it is.** The `docs/harnesses.md` file describes the `plan.json` signal but does not comprehensively list which field types are errors.

**Match the code.** The validation in `_plan()` (core/session.py:121-143) requires:
- `path`: must be a string; wrong type is an error
- `critique_rounds`, `review_rounds`: must be integers (not booleans); wrong type or out of range (not 0/1/2) is an error
- `stakes`: must be a string (if present); wrong type is an error
- `scope`: must be a list (if present); wrong type is an error

**Where and what.** In the table row for `.valor/plan.json` at docs/harnesses.md:259-260, replace the phrase about counts "outside 0 to 2" with a complete field-by-field type contract, including that booleans are rejected for counts.

## N3: Fix article and use shared type vocabulary

**What it is.** Line 229 in `core/signals.py` has `"is a {type_name}"`. When the type is `int`, this produces "is a int" which should be "is an int".

**How it works.** `core/session.py` already has `_kind()` (line 169) which returns type names with the correct article: `"an object"`, `"a list"`, `"a string"`, `"a boolean"`, `"null"`, `"a number"`. Move it to `core/signals.py` and use it in `_request()` at line 229, so all type errors across the turn speak the same vocabulary.

**Update tests.** Tests at tests/test_signals.py:309-310 assert the old format (`"is a dict"`, `"is a list"`). They will change to `"is an object"`, `"is a list"` respectively. Also add an assertion for the int case to test N3 specifically (that `"is a number"` appears, never `"is a int"`).

**Scope:** All three are bug fixes only, no new guards or governance. No changes to working code paths except the fallback that is already tested.

**Stakes:** Medium. N1 fixes the last write that records a turn; a bug here loses the entire turn's record and orphans effect rows. The fallback has a test (line 933), so it is testable.

**Critique rounds:** 1. N1's design (part-dropping logic) should be reviewed before building.

**Review rounds:** 1. Changes touch session, signals, and docs; test changes are needed.

**Decisions Tom may want to change:**
- For N1: the exact loop condition for dropping parts (stop when row fits, or keep trying until all gone)
- For N2: the verbosity and placement of the type contract description
- For N3: whether to move `_kind()` or inline the logic; the expected test values
