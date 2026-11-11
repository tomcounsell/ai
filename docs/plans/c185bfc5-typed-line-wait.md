# The typed-line call tests wait for the typed line to reach Live

**Defect.** `tests/test_call.py::test_a_typed_line_is_answered_in_the_chat_past_a_tool_call_until_someone_speaks`
failed once under the parallel suite at main e1dbb3a5d (the message was lost on the routine's resume).
It passes alone and in 48 loaded repeats.

**Cause (reproduced).** `Call.chat` spawns `_typed`, which writes the `call.heard` row, then sets
`self.typed = True` and sends `[typed by NAME] ...` to Live. The test sleeps a fixed 0.1 s after
`chat()` and then has the fake Live reply. When the row's write takes longer than that, the reply
begins (`_begin`) with `typed` still False, so it counts as spoken and its audio streams. With
`_heard` slowed by 0.3 s the test fails exactly so: `assert [(24000, True)] == []` at line 666.
Production is not affected: Live cannot answer a typed line before `_typed` sends it, which is
after the flag is set. The defect is the test's fake answering early.

**Fix (test only).** In `tests/test_call.py`, each `await asyncio.sleep(0.1)` that follows a
`c.the.chat(...)` (lines 610, 623, 652) becomes
`assert await until(lambda: ("[typed by NAME] TEXT", True) in c.session.contents())`, the file's
existing `until` helper, the same pattern as 9d241dc2b and b8692f9c1. `core/call.py` is unchanged.

**Out of scope.** The other fixed waits in the file (`push`'s 0.1 s, `QUIET_S + 0.2`): none has
failed, and changing them buys nothing this defect needs. Setting `typed` before the row's write:
it would change production (a spoken reply beginning during the write would be posted instead).

**Tests.** Run with `_heard` slowed by 0.3 s (a scratch file, not committed): the two typed-line
tests pass, where the first fails today. Then `tests/test_call.py` whole, and the full suite with
`-n auto`, green. No governance is added.
