---
status: Ready
type: bug
appetite: Small
owner: Valor
created: 2026-03-06
tracking: https://github.com/tomcounsell/ai/issues/280
---

# Fix Summarizer Question Fabrication

## Problem

The summarizer (Haiku) fabricates questions that were never in the raw agent output. Declarative statements like "I will add sdlc to classifier categories" get reframed as "? Should classifier be updated to output 'sdlc'?" — a question the agent never asked.

**Current behavior:**
- `SUMMARIZER_SYSTEM_PROMPT` says to surface "decisions needing input" — too vague
- `expectations` field says "What specific input, decision, or approval the agent needs" — encourages Haiku to hunt for implicit decisions
- Haiku over-interprets planned work as needing approval, fabricates questions
- False questions trigger dormant session state (waiting for input that was never requested)

**Desired outcome:**
- Questions in the summary are **only** those verbatim present in the raw output
- Declarative/informational statements are never reframed as questions
- `expectations` is `None` when no explicit questions exist in the output
- Zero false-dormant sessions from fabricated questions

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **Tighten the prompt**: Replace vague "decisions needing input" with explicit instruction to only surface verbatim questions (sentences ending in `?` directed at the human)
- **Tighten expectations field**: Change from "what the agent needs" to "only set when the raw output contains an explicit question directed at the human"
- **Add negative examples**: Show Haiku what NOT to do (reframing statements as questions)

### Technical Approach

#### 1. Fix `SUMMARIZER_SYSTEM_PROMPT` (lines 919-928)

Replace the current question extraction instruction:

```
If the output contains questions, decisions needing input, or items requiring human approval,
list them AFTER the bullets, separated by "---" on its own line. Prefix each with "? ":
```

With a stricter version:

```
If the output contains EXPLICIT questions directed at the human (sentences that literally
end with "?" and ask the human to decide or provide input), list them AFTER the bullets,
separated by "---" on its own line. Prefix each with "? ":

NEVER fabricate questions. NEVER reframe declarative statements as questions.
If the agent says "I will do X", that is NOT a question — it is a plan.
Only surface questions that are VERBATIM in the raw output.
```

#### 2. Fix `expectations` field instruction (lines 891-894)

Replace:

```
expectations: What specific input, decision, or approval the agent needs from the human.
Set to null when the work is self-contained and no human input is needed.
```

With:

```
expectations: Set ONLY when the raw output contains an explicit question directed at the
human (a sentence ending in "?" that asks for a decision, approval, or input). Copy the
question verbatim. Set to null when no explicit questions exist — even if the agent
describes plans or next steps. Declarative statements are NOT questions.
```

#### 3. Add negative examples to the prompt

After the question format example, add:

```
WRONG — do NOT do this:
Raw: "I will add sdlc to classifier categories"
Output: ? Should classifier be updated to output 'sdlc'?   ← FABRICATED

RIGHT:
Raw: "I will add sdlc to classifier categories"
Output: • Added sdlc to classifier categories   ← No question, no "---"
```

## Rabbit Holes

- **Post-hoc question detection in Python**: Tempting to add regex validation that strips fabricated questions after Haiku responds. The fix belongs in the prompt, not in post-processing — Haiku needs to stop generating them, not have them removed after.
- **Restructuring the entire summarizer**: The summarizer works well overall. This is a targeted prompt fix, not a rewrite.

## Risks

### Risk 1: Over-suppression of real questions
**Impact:** Haiku might stop surfacing genuine questions the agent asked.
**Mitigation:** The negative examples explicitly show the contrast (fabricated vs. real). Integration test with real Haiku API validates real questions are preserved.

### Risk 2: Prompt length
**Impact:** Adding negative examples increases prompt token count.
**Mitigation:** The additions are ~100 tokens. The prompt is well under context limits.

## No-Gos (Out of Scope)

- Restructuring the summarizer architecture
- Changing how `expectations` flows to session state (that's working correctly)
- Fixing the dormant session classification (separate issue — if expectations is null, the session won't go dormant)

## Update System

No update system changes required — this is a prompt-only change in bridge code.

## Agent Integration

No agent integration required — this modifies the summarizer prompt which runs in the bridge process.

## Documentation

### Inline Documentation
- [ ] Update the comment block above `SUMMARIZER_SYSTEM_PROMPT` explaining the anti-fabrication rules

### Feature Documentation
- [ ] Update `docs/features/summarizer.md` if it exists, noting the anti-fabrication constraint

## Success Criteria

- [ ] Declarative statements ("I will do X") produce `expectations=None`
- [ ] Explicit questions ("Should we use A or B?") produce non-null `expectations` with verbatim text
- [ ] Mixed outputs: only real questions surfaced, declarative plans are bullet-pointed
- [ ] Future-tense plans ("will update X, will add Y") produce `expectations=None`
- [ ] Code snippets with `?` characters not treated as questions
- [ ] Tests pass (`/do-test`)
- [ ] Bridge restarted after deployment

## Team Orchestration

### Team Members

- **Builder (prompt-fix)**
  - Name: prompt-builder
  - Role: Update SUMMARIZER_SYSTEM_PROMPT and expectations instruction
  - Agent Type: builder
  - Resume: true

- **Test Engineer**
  - Name: prompt-tester
  - Role: Write tests covering fabrication scenarios from issue #280
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: prompt-validator
  - Role: Verify prompt changes and test results
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Update summarizer prompt
- **Task ID**: build-prompt-fix
- **Depends On**: none
- **Assigned To**: prompt-builder
- **Agent Type**: builder
- **Parallel**: true
- Update `SUMMARIZER_SYSTEM_PROMPT` lines 919-928: replace "decisions needing input" with verbatim-only instruction
- Update `expectations` field instruction lines 891-894: require explicit questions only
- Add negative examples after the question format block

### 2. Write tests
- **Task ID**: build-tests
- **Depends On**: none
- **Assigned To**: prompt-tester
- **Agent Type**: test-engineer
- **Parallel**: true
- Implement all 10 test cases from issue #280
- Both mock-based (fast) and integration (real Haiku) tests

### 3. Validate
- **Task ID**: validate-all
- **Depends On**: build-prompt-fix, build-tests
- **Assigned To**: prompt-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify prompt changes match plan
- Verify lint passes

## Validation Commands

- `python -m pytest tests/test_summarizer.py -v --tb=short -k "fabricat or question"` — targeted question tests
- `python -m ruff check bridge/summarizer.py` — lint
- `python -m pytest tests/ -x --timeout=120` — full suite
