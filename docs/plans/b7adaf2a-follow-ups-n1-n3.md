# Follow-ups N1 to N3 from the 2.1 review

Three small fixes from the Round 13 checks of the m2-1 review.

## N1: record()'s bare fallback row keeps recordable parts

**What it is.** In `core/session.py`, the `record()` function's bare fallback row (`_bare`) currently drops every part of the turn's result when the row after the broker is refused, even when only some parts caused the refusal. The fix: drop largest parts one by one until the row fits, keeping what can be stored. The mechanism differs from `_storable()` because after the broker, effect entries carry `effect_id` and `kind` (added by line 434), which must be preserved to avoid orphaning effect rows already written by the broker.

**The scenario.** The review's N1 refers to a case where Postgres refuses the row only when parts are together, not individually. For example, two large effect entries (a.json and b.json) each fit jsonb alone but together exceed its limit. After `_answered()` splits them (core/session.py:495-505), each entry is separately storable but the whole row is still refused.

**How it works.** When the row is refused after `_answered()` has tried, create a new reduction loop:
1. Gather all droppable parts: each error entry, each effect entry, question, plan, done, no_question, screens, and candidate (not verdict)
2. Sort by JSON size (largest first)
3. Drop the largest part: for errors, replace with the answered format `"an error is unrecorded: {UNSTORABLE}: {Postgres reason}"`; for effects, replace with `{effect_id, kind, file, error: "..."}` as `_answered` does (line 497-502)
4. Try to write the row through `_written()` (the savepoint, as with `_answered`)
5. Repeat until the row stores or no parts are left
6. If nothing fits, fall back to today's fully bare row with unconditional `ledger.append`
7. Add one error entry to the reduced row: "the turn's signals could not all fit together; {part type/name} was dropped to record the rest"

The reduced row carries: verdict (idle or failed), candidate=None (never a partially-dropped candidate), no follow row (so idle/failed never links to a question or plan that was not ledgered). This keeps ok effects with their effect_id and kind, and whichever of a.json/b.json fits, while answering the other with its refusal reason, effect_id, kind, and file name intact.

**Test.** Update the existing test at tests/test_session.py:933 (rename it, since `is_written_bare` no longer fits):
- Assert that the turn.collected row is written with verdict idle
- Assert that ok.json is present with kind 'pending' and its effect_id
- Assert that exactly one of a.json/b.json is fully present (unsure which, since both are same size)
- Assert that the other has effect_id, kind, file, and an error message (the dropped effect)
- Assert candidate is None
- Assert the effect.refused and effect.held counts remain unchanged (2 and 1 respectively)
- The test currently asserts `effects == []`, which will change to the new structure

## N2: docs/harnesses.md documents plan field type errors

**What it is.** The `docs/harnesses.md` file describes the `plan.json` signal but does not comprehensively list which field types are errors.

**Match the code.** The validation in `_plan()` (core/session.py:121-143) requires:
- `path`: must be present (string); missing or wrong type is an error
- `critique_rounds`, `review_rounds`: must be present (integers, not booleans); missing, wrong type, or out of range (not 0/1/2) is an error
- `stakes`: must be a string if present; wrong type is an error
- `scope`: must be a list if present; wrong type is an error

**Where and what.** In the table row for `.valor/plan.json` at docs/harnesses.md:260 (third row of the signal table), expand the description to document the complete field contract: which fields are required, what type each must have (including that booleans are rejected for counts), and which errors make a plan unreadable (missing path, missing count, wrong type, count out of range).

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
