# Follow-ups N1 to N3 from the 2.1 review

Three small fixes from the Round 13 checks of the m2-1 review.

## N1: record()'s bare fallback row keeps recordable parts

**What it is.** In `core/session.py`, the `record()` function's bare fallback row (`_bare`) currently drops every part of the turn's result when the row after the broker is refused, even when only one part caused the refusal. The fix: keep the parts that are recordable and drop only the refused ones.

**How it works.** The `_answered()` function already splits each error and effect entry to be recorded separately. When `_written()` still refuses after `_answered()` has split the parts, `_bare()` is called with the `collected` dict that already contains the split parts. The current `_bare()` ignores that dict and creates a completely empty row. The fix passes the `collected` dict to `_bare()` and preserves the errors and effects parts (which already have refusal messages where needed).

**Test.** Add a test that:
1. Creates a mock `turn.collected` row with mixed parts (some normal, some with refusal messages)
2. Verifies that `_bare()` preserves the errors and effects
3. Fails without the fix because `_bare()` currently loses all parts

## N2: docs/harnesses.md documents wrong plan field type as error

**What it is.** The `docs/harnesses.md` file describes the `plan.json` signal at line 260 but does not explicitly state that a plan field of the wrong type is an error.

**How it works.** The existing text says "with counts outside 0 to 2, is an error and no plan" but this is specific to the numeric ranges. Add explicit documentation that fields with the wrong JSON type (e.g., a string where an int is expected) are also errors.

**Where.** In the table row for `.valor/plan.json` at line 260, clarify that type validation errors are errors just as out-of-range values are.

## N3: Fix article in signals.py line 229

**What it is.** Line 229 in `core/signals.py` has the message `"is a {type_name}"`. When the type is `int`, this produces "is a int" which should be "is an int".

**How it works.** The message is generated in the `_request()` function with f-string formatting. Add a helper to choose the correct article ("a" or "an") based on the type name's first letter.

**Scope:** All three are bug fixes only, no new guards or governance.

**Stakes:** Low. These are documentation, error message, and error recovery path fixes. No changes to working code paths, and all are reversible.

**Critique rounds:** 0. No design decisions to review.

**Review rounds:** 1. The changes are small but touch different areas (session, signals, docs).

**Decisions Tom may want to change:**
- For N1: whether to accept the collected dict in _bare() or take a different approach to preserving parts
- For N2: the exact wording of the plan field type error documentation
- For N3: whether to use a helper function or inline logic for the article choice
