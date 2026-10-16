---
tracking: backlog:notice-ceo-shape
slug: notice-ceo-shape-carry
type: plan
status: planned
critique_rounds: 0
review_rounds: 1
---

# Land the finished notice change on the moved main

`docs/plans/notice-ceo-shape.md` is the plan; task e9c307e6cd91 built it and it
passed test, review and docs. Main then moved (`abb3f7d54`: a moved target
always rebases, never ends the task) and the old two-rebase cap ended it. This
carries that head; nothing of it is redone.

**Done in this stage.** The head (`edefbe4e9`, fetched from the old task's
workspace; it was not on this task's origin) is rebased onto main as
`valor/7554a93b`. One conflict, `core/verdicts.py` `ensure_merge`: kept main's
condition (`why == "refused"` or transient tries spent; `moved` no longer ends)
and the branch's `why=why` on `end`.

**Build.**
1. `core/notices.py` `MERGE_WHY`: drop the `moved` row. Since `abb3f7d54` no
   task ends with `why == "moved"`; an old row carrying it falls back to "I
   could not merge it". Update `merge_failure`'s docstring only if it says a
   moved merge ends.
2. Test budget. On the rebased head the suite fails one test:
   `tests/test_budget.py` 5080 lines against 5000 (main is at 4995; the
   carried test adds 85). The bar stays. Get under it without losing an
   assertion of documented behaviour: compact the new test in
   `tests/test_telegram_pipeline.py` (one-line tuples, the `SimpleNamespace`
   import at module top, fold the per-kind loops), then remove assertions
   elsewhere that the new test now covers (e.g. notice-text checks in
   `tests/test_pipeline.py:150-160`). Each removal is named in the commit with
   what still covers it. Target at most 4990, so the next change has room.
3. The previous review's small debt, all text: `docs/sdlc-state-machine.md:446`
   still says the merge report carries the status page link;
   `core/bridge.py:374` comment says the text carries the short id;
   `serve.ROLLOUT_STEPS["uncommitted"]` reads badly in its sentence ("...which
   the restart would run failed 3 times"); reword so the sentence reads.

**Out of scope.** Everything the original plan left out; any change to the
merge or rebase path beyond the one conflict line.

**Tests.** Full suite green, including `tests/test_budget.py` (both budgets)
and the carried notice test; under 5 minutes. The carried test still renders
every notice kind listed in the original plan after compaction.

**Governance.** None added; the conflict keeps main's rule, the rest is text
and test size.

**Stakes.** Notice text and one additive `why` field on `task.ended`;
reversible by revert. A wrong word reaches Tom; no merge or send path changes.
