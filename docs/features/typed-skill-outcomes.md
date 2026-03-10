# Typed Skill Outcomes

Structured outcome contracts for `/do-*` skills that enable deterministic routing by the Observer without LLM classification.

## Problem

Our `/do-*` skills return unstructured prose. The Observer Agent and stage detector parse free-form text with regex heuristics and LLM classification to determine what happened. This is fragile: artifacts (PR URLs, plan paths) are extracted by regex from transcript text, and every skill outputs a different prose format.

## Solution

Every `/do-*` skill can emit a parseable `SkillOutcome` JSON block at completion. The block uses HTML comment syntax (`<!-- OUTCOME {...} -->`) so it is invisible in rendered markdown but parseable by the pipeline.

### SkillOutcome Dataclass

Defined in `agent/skill_outcome.py`:

```python
@dataclass
class SkillOutcome:
    status: str           # "success", "fail", "partial", "retry", "skipped"
    stage: str            # "PLAN", "BUILD", "TEST", "REVIEW", "DOCS"
    artifacts: dict       # {"pr_url": "...", "plan_path": "...", "branch": "..."}
    notes: str            # human-readable summary
    failure_reason: str | None = None
    next_skill: str | None = None
```

### Outcome Block Format

Skills emit the outcome as the last line of their output:

```
<!-- OUTCOME {"status":"success","stage":"BUILD","artifacts":{"pr_url":"https://github.com/org/repo/pull/42"},"notes":"PR created","next_skill":"/do-test"} -->
```

### Parser

`parse_outcome_from_text(text)` extracts the first `<!-- OUTCOME {...} -->` block from mixed prose. Returns `None` if no valid block is found (graceful degradation).

### Format Helper

`format_outcome(outcome)` produces the HTML comment block from a `SkillOutcome` instance.

## Observer Integration

The Observer (`bridge/observer.py`) now checks for a typed outcome before calling the LLM:

1. **Parse outcome** from worker output using `parse_outcome_from_text()`
2. **If outcome found with `status: "success"`** and remaining stages exist: steer to next stage (no LLM call needed)
3. **If outcome found with `status: "success"`** and all stages complete: deliver to human
4. **If outcome found with `status: "fail"`**: deliver to human with failure context
5. **If outcome found with ambiguous status** (`partial`, `retry`, `skipped`, unknown): fall through to LLM Observer
6. **If no outcome found**: fall back to current LLM-based routing (full backward compatibility)

## Stage Detector Cross-Check

The stage detector (`bridge/stage_detector.py`) accepts an optional `SkillOutcome` parameter in `apply_transitions()`:

- If the typed outcome says "success" but regex did not detect completion for that stage, a warning is logged
- If the typed outcome says "fail" but regex detected completion, a warning is logged (outcome takes priority)
- This cross-check catches drift between skill output format and detection patterns

## Outcome Contracts in SKILL.md

Each skill's SKILL.md includes an "Outcome Contract" section defining:

- **Valid status values** for that skill
- **Expected artifact keys** on success (e.g., `pr_url`, `branch`, `total_passed`)
- **Emission template** showing the exact block format to emit

Currently defined for:
- `.claude/skills/do-build/SKILL.md` - BUILD stage outcomes
- `.claude/skills/do-test/SKILL.md` - TEST stage outcomes

## Backward Compatibility

The system is fully backward compatible:

- If a skill does not emit an outcome block, `parse_outcome_from_text()` returns `None`
- The Observer falls back to LLM-based classification (existing behavior)
- The stage detector continues regex-based detection as before
- No changes to how skills are invoked or how Telegram messages are formatted

## Key Files

| File | Purpose |
|------|---------|
| `agent/skill_outcome.py` | `SkillOutcome` dataclass, parser, and format helper |
| `bridge/observer.py` | Outcome-first routing before LLM call |
| `bridge/stage_detector.py` | Cross-check typed outcome against regex detections |
| `.claude/skills/do-build/SKILL.md` | BUILD outcome contract |
| `.claude/skills/do-test/SKILL.md` | TEST outcome contract |
| `tests/unit/test_skill_outcome.py` | 34 unit tests for the module |

## Status

Shipped
