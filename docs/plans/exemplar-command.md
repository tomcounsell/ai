---
tracking: backlog:exemplar
slug: exemplar-command
type: plan
status: planned
critique_rounds: 0
review_rounds: 1
---

# `python -m core exemplar`: record work Tom loved

**Carried (2026-10-16).** Task 80f69f9e52da built, tested, reviewed and
documented this; the kernel's since-removed two-rebase cap ended it. Its plan
and build commits are rebased onto main here (one conflict: both sides
appended tests to the end of `tests/test_kernel.py`; both kept). Code and docs
stand as built. On main the rebased suite gives 115 passed, 1 skipped, and
one failure: `test_budget` counts 5,076 test lines against its 5,000 (main
alone is 4,995). The bar does not move, so the only work left is to fit it:
1. Compact the three carried tests into two (render, and record-plus-turn-
   plus-CLI) keeping every assertion listed under Tests below.
2. Take the remaining overage from `tests/` without dropping any assertion:
   duplicated setup, helpers inlined once used once, multi-line literals the
   110-column formatter allows on one line. No test file is deleted.
3. Full suite green, `test_budget` included, total test lines <= 5,000.

Adds no check, gate, hook, guard or refusal. Empty text is already a
`ValueError` in `corrections.payload`; the command reports it the way
`correct` does.

**Cause.** `core/corrections.py` has `SOURCE_CLASSES = ("direct", "exemplar")`
and `record(..., source_class=...)`, but the only caller, `correct` in
`core/__main__.py`, always passes the default `direct`. And `render` puts every
row under "# Corrections from Tom", so an exemplar, once recorded, would read
to every turn as a correction.

**Build.**
1. `core/__main__.py`: `exemplar TEXT [--by tom] [--via "the command line"]`,
   parsed like `correct` without `--record` (that flag confines a client fact,
   which is a correction's job). It calls `corrections.record(...,
   source_class="exemplar")` and prints `exemplar N recorded, ledger row ID`;
   a `ValueError` exits `exemplar refused: ...`. Same stream, same numbering
   sequence as corrections. Help text gains the command next to `correct`,
   and the `corrections` line says it lists exemplars too.
2. `core/corrections.py`: `payload`'s empty-text message names the class
   ("an exemplar has text"). `render` splits rows by source class: direct
   rows render exactly as today; exemplar rows follow in a
   "# Exemplars from Tom" section ("Work Tom loved, and why, in the order he
   gave them."), same line format. With no exemplar, output is byte-identical
   to today, so every existing Brief and `turn.started` digest is unchanged.
   With exemplars and no direct correction, the corrections section still
   says "None recorded." Docstring updated.
3. `correct` has no withdraw command; `corrections` is the list and now shows
   both. So no withdraw counterpart is built.
4. Docs: `docs/mission.md` (drop "nothing records one yet"; no sentence about
   live ledger contents), `docs/architecture.md` Corrections and exemplars
   (Built names the command and the section), `docs/data.md` Brief section,
   `docs/bridges/telegram.md` unchanged (it describes intake, still Design),
   `core/README.md` scope line.

**Out of scope.** Withdrawing or superseding an exemplar or correction;
recording one from Telegram or the judgement layer's feedback-kind task;
`--record` or `--role-played` on `exemplar`; any change to memory recall
(`memory/recall.py` already never makes the corrections stream a candidate).

**Stakes.** Low and reversible: an additive command and a render change that
leaves today's Brief text unchanged, but the render runs in every turn.

**Tests** (in `tests/test_kernel.py`; the suite is at its 15-file budget, so
no new file):
- Recording a correction, an exemplar, then a correction numbers them 1, 2, 3
  on one stream, each with its source class and provenance `by`/`via`.
- `render` of direct rows only equals the pre-change text (pinned literal);
  of an empty list is still "None recorded."
- `render` of mixed rows: direct rows under Corrections, exemplars under
  Exemplars after them, each with its number; exemplars alone leave
  Corrections at "None recorded."
- The turn prompt `tasks` builds for a task carries a recorded exemplar once,
  in its section.
- CLI: `python -m core exemplar "..." --by tom` against the test database
  records one exemplar and prints its number; `python -m core corrections`
  lists it; whitespace text exits non-zero with `exemplar refused:` and
  records nothing; `--help` names `exemplar`.
- `tests/test_budget.py` still passes.
