---
slug: sdlc-1414-thrash-detector-and-confidence-gate-fixes
status: Ready
type: chore
appetite: Small
tracking: https://github.com/tomcounsell/ai/issues/1414
last_comment_id:
revision_applied: false
---

# sdlc-1414: Thrash Detector Signal + High-Confidence Gate Fixes

## Background

Issue #1414 was filed by the reflections system claiming agents exhibit "thrashing" (high
tool-call volume, low success) and proposed capping tool calls per plan phase to 10.

Recon (session `0_1779456793423`, recorded on the issue) **dropped** the original claims:
the "thrashing" signal is an artifact of a flawed ratio in the detector, and the proposed
10-call cap would harm legitimate plan/recon work. The articulated-plan concern it
gestured at is already enforced by existing plan validators.

The recon surfaced **two narrow pre-requisite fixes** that should be done before any
further work in this area. This plan scopes those two fixes only.

The originating issue is closed; this plan codifies the pre-requisite work the recon
identified rather than the (rejected) original ask.

## Problem

1. **`reflections/session_intelligence.py:65-77`** flags sessions as "thrashing" when
   `1 - (turn_count / tool_calls) > 0.5` with `tool_calls > 5`. `turn_count` is *total
   assistant turns*, not *successful turns*, so any session that averages more than ~2
   tool calls per turn is labeled thrashing. Empirically ~50% of recent productive
   sessions trip this — including completed, successful SDLC runs. The detector cannot
   distinguish productive multi-tool turns from real thrashing.

2. **`reflections/utils.py:411-425`** `is_high_confidence()` requires 2-of-3 of
   `category == "code_bug"`, non-empty prevention, pattern ≥10 chars. A non-code-bug
   reflection (e.g., `category="poor_planning"`) can clear the gate on prevention +
   pattern length alone. That is how #1414 itself reached "high-confidence" status
   despite asserting an agent-behavior pattern rather than a code defect.

## Desired Outcome

- Thrash detector either emits a real signal or emits nothing — no more 50% false-positive
  rate on healthy sessions.
- High-confidence gate requires `category == "code_bug"` AND at least one other
  supporting criterion, so non-code-bug reflections cannot ride into the auto-fix path
  on length alone.

## Freshness Check

Baseline commit: `69749977` (main, 2026-05-23).

- **Unchanged** — Re-read `reflections/session_intelligence.py:65-77` and
  `reflections/utils.py:411-425` on 2026-05-23. Both code blocks match the recon
  description verbatim; `THRASH_RATIO_THRESHOLD = 0.5` still lives at
  `reflections/utils.py:36`. No drift since the recon was recorded on 2026-05-22.
- No commits have landed on `reflections/session_intelligence.py` or
  `reflections/utils.py` since issue creation.
- No active plan in `docs/plans/` targets the reflections detector or confidence gate
  (greped for `session_intelligence`, `is_high_confidence`, `THRASH_RATIO_THRESHOLD`).
- Issue is closed but pre-requisite work is real and unaddressed; proceeding on the
  recon's revised premise.

## Research

No external libraries or APIs are introduced — both fixes are internal to
`reflections/`. WebSearch skipped per Phase 0.7 guidance for purely-internal work.

## Prior Art

- Issue #1414 (closed) — original false-positive thrashing claim, source of the recon
  that this plan acts on.
- No prior PRs touch `THRASH_RATIO_THRESHOLD` or `is_high_confidence`.

## Appetite

**Small.** Two narrow edits in two files, plus tests.

## Solution

### Fix 1: Thrash detector signal

Replace the `turn_count / tool_calls` ratio in
`reflections/session_intelligence.py:65-77` with a signal that actually correlates with
thrashing. Two candidate signals; pick whichever is cheapest to compute from existing
session log data:

- **Repeated identical tool calls** — count consecutive identical `(tool_name, args_hash)`
  invocations in the session log; flag if the count exceeds a small threshold.
- **Repeated failed tool results** — count tool results carrying an `is_error: true`
  flag (or equivalent) in a sliding window; flag if the failure rate within the window
  exceeds a threshold.

If neither signal is cheaply computable from current log structure, **delete the
thrash-detection block entirely** and remove `THRASH_RATIO_THRESHOLD` from
`reflections/utils.py:36`. A missing detector is strictly better than one with a 50%
false-positive rate filing bugs against healthy sessions.

### Fix 2: Confidence gate

Change `reflections/utils.py:411-425` so the gate requires `category == "code_bug"` AND
at least one of the other two criteria, rather than 2-of-3:

```python
def is_high_confidence(reflection: dict) -> bool:
    if reflection.get("category") != "code_bug":
        return False
    return (
        bool(reflection.get("prevention", "").strip())
        or len(reflection.get("pattern", "")) >= 10
    )
```

This blocks non-code-bug categories (e.g., `poor_planning`, `process_gap`) from the
auto-fix path while preserving the existing behavior for legitimate code-bug reflections.

## Data Flow

`reflections/session_intelligence.py` consumes `AgentSession` records and writes thrash
findings to its output dict, which is consumed by the daily reflection LLM step in
`reflections/utils.py:271-349`. `is_high_confidence` gates whether a reflection is
auto-filed as an issue. Both fixes are local to the reflections package; no callers
outside `reflections/` reference these symbols directly.

## No-Gos

- **Do NOT** introduce a tool-call cap per plan phase. Legitimate recon and plan work
  routinely uses 30–200 tool calls; a cap would cripple real work.
- **Do NOT** broaden the high-confidence gate to admit more categories. The point of
  the fix is to *narrow* it, not widen it.
- **Do NOT** reopen issue #1414. If either fix lands, it is tracked on its own commit;
  this plan's tracking issue remains closed as the recon recommended.

## Update System

No update system changes required — both files are loaded at runtime from the existing
`reflections/` package; no new dependencies, services, or config keys are added.

## Agent Integration

No agent integration required — `reflections/session_intelligence.py` runs inside the
reflection scheduler, not via Telegram or any agent surface. No new CLI entry point and
no bridge import changes.

## Failure Path Test Strategy

- **Thrash detector**: feed the detector a synthetic session log with (a) productive
  multi-tool turns (should NOT flag), (b) repeated identical tool calls (SHOULD flag if
  that signal is chosen), and (c) repeated tool errors (SHOULD flag if that signal is
  chosen). If the detector is deleted instead, assert the output dict no longer carries
  a `thrash_sessions` key (or that the key is always empty).
- **Confidence gate**: parametrized test covering
  `(category, prevention, pattern_len) → expected` for the new rule. Must include the
  exact failing case from #1414: `category="poor_planning"`, non-empty prevention,
  pattern≥10 → MUST return `False` under the new rule.

## Test Impact

- [ ] `tests/unit/test_reflections_utils.py::test_is_high_confidence_*` (if present) —
      UPDATE: existing 2-of-3 assertions must be rewritten for the new
      "code_bug AND (prevention OR pattern)" rule. Tests asserting that a non-code-bug
      reflection with prevention + pattern returns `True` MUST flip to assert `False`.
- [ ] `tests/unit/test_reflections_session_intelligence.py` (if present) — UPDATE or
      DELETE: any test asserting the old `1 - turn/tool_calls > 0.5` thrash behavior
      becomes invalid. Replace with tests for the new signal, or delete if the
      detection block is removed entirely.
- [ ] No-existing-test fallback: if neither test module exists, this section's
      acceptance is that the new tests in **Failure Path Test Strategy** are created
      and pass.

## Rabbit Holes

- Rewriting the entire reflections detector taxonomy. Out of scope — only the two
  recon-identified blocks are in scope.
- Adding `source_session` propagation into filed issues
  (`reflections/utils.py:271-349`). Worth doing but separate concern; would inflate
  this plan's appetite beyond Small.
- Per-category confidence gates (different rules for different categories). Premature
  generalization; the recon only justifies tightening to code-bug-only.

## Step by Step Tasks

1. **Survey existing tests** — `grep -rn 'is_high_confidence\|THRASH_RATIO_THRESHOLD\|thrash_sessions' tests/`
   to find every test that will need to UPDATE/DELETE/REPLACE per the Test Impact list.
2. **Fix 2 first (smaller, lower risk)** — edit `reflections/utils.py:411-425` to the
   "code_bug AND (prevention OR pattern)" rule. Update or write the unit test.
3. **Choose the thrash signal** — inspect a handful of recent session logs to confirm
   that either "repeated identical tool calls" or "repeated tool errors" is cheaply
   derivable from existing log structure. If neither is, plan to delete the block.
4. **Fix 1** — implement the chosen signal in `reflections/session_intelligence.py:65-77`,
   or delete the block plus `THRASH_RATIO_THRESHOLD` if no cheap signal exists. Update
   or write the unit test.
5. **Run** `pytest tests/unit/test_reflections*.py -n0` and ensure both unit tests pass.
6. **Run** `python -m ruff format . && python -m ruff check reflections/`.
7. **Commit, push, open PR** with `Closes #1414` (or, since #1414 is already closed,
   reference it as context only — do not close again).

## Success Criteria

- `is_high_confidence({"category": "poor_planning", "prevention": "x", "pattern": "x"*20})`
  returns `False`.
- `is_high_confidence({"category": "code_bug", "prevention": "x", "pattern": ""})`
  returns `True`.
- Re-running the reflection scheduler over the last week of sessions does NOT file new
  issues for productive sessions with high tool-per-turn ratios.
- Unit tests pass; `ruff check reflections/` clean.

## Open Questions

None — recon settled the scope. Both fixes are mechanical.
