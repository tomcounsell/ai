# Memory bench test reads the fixture instead of the result

**Defect.** `tests/test_memory_bench.py:100` opens the bench's result with
`next(out.glob("v-test-*.json"))`. `core.memory.bench` writes two files in
`out`: `v-test-<date>.json` (the scores) and `v-test-<date>-popoto-fixture.json`
(popoto's fixture). The glob matches both, and `next` takes whichever the
directory lists first. When it is the fixture, its `questions` carry no
`with_undated`, and the test fails with `KeyError: 'with_undated'`
(reproduced on 0727d0d01 with `uv run pytest tests/test_memory_bench.py`).

**Fix.** The test reads the result by its own name pattern,
`out.glob("v-test-????-??-??.json")`, which the fixture's name cannot match.
No production change: `core/memory.py` names its files correctly and nothing
else globs them.

**Out of scope.** Renaming the files `bench` writes; any change to scoring.

**Tests.** `tests/test_memory_bench.py` passes, run alone and in the full
suite (`uv run pytest -q -n auto -p no:cacheprovider`). Beyond the obvious
case: the test also asserts exactly one file matches the result pattern, so
a second match cannot pass silently again.

**Stakes.** A test-only change to one glob; trivially reversible.
Critique rounds 0, review rounds 0. No governance added.
