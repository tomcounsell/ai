---
status: Ready
type: bug
appetite: Medium
owner: Valor
created: 2026-02-11
tracking: https://github.com/tomcounsell/ai/issues/73
---

# Valor Premature Stopping

## Problem

Valor stops and waits for human input 13+ times per week when it should continue working autonomously. Three anti-patterns cause this:

**Anti-Pattern 1: Permission-seeking at every step (8 instances)**
- "Which of the three paths takes priority?"
- "Need PM decision on X API tier (Free vs Basic)..." — when user already said "use Tweepy"
- "Questions for you: 1. Do we have credentials? 2. API access? 3. Require approval?" — when asked to "create an issue", turned into 6-message Q&A
- "Need approval on: auto-revert opt-in default, alert routing, watchdog frequency" — implementation details SOUL.md says NOT to escalate

**Anti-Pattern 2: False blockers (3 instances)**
- "Blocked on identifying exact file/function" — full codebase access, just search for it
- "Needs manual action: Quit Chrome and restart" — has process management permissions
- "DB lock persists; needs manual cleanup" — has full system access

**Anti-Pattern 3: Premature escalation (2 instances)**
- "Should we add session cleanup handling?" — the answer to "should we fix a crash bug" is always yes
- Identified CLAUDE.md gap but stopped instead of fixing it

**Current behavior:**
- The auto-continue system (issue #75) is implemented and working
- But the underlying LLM still generates question-like output that triggers pauses
- SOUL.md says the right things but the instructions aren't specific enough to override default Claude behavior

**Desired outcome:**
- Agent continues working on implementation details without asking
- Agent resolves its own blockers when it has the access to do so
- Agent fixes obvious issues (crash bugs, doc gaps) without permission
- Fewer unnecessary pauses, more autonomous completion

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1 (validate the new instruction strength)
- Review rounds: 1 (verify behavior change in practice)

The core work is strengthening system prompt instructions. The risk is going too far in the other direction (agent continues when it genuinely should ask).

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Auto-continue system working | `grep -n "MAX_AUTO_CONTINUES" agent/job_queue.py` | Foundation for this fix |
| Classification working | `grep -n "classify_output" bridge/summarizer.py` | Determines when to pause |

## Solution

### Key Elements

- **Stronger anti-escalation instructions in SOUL.md** — Specific examples of what NOT to ask about
- **Decision-making heuristics in system prompt** — Rules like "if you can figure it out, do it"
- **Blocker redefinition** — Clarify what constitutes a genuine blocker vs. resolvable obstacle
- **Summarizer prompt adjustment** — Remove or refine the ⚠️ blocker flag that transforms notes into stops

### Flow

**Agent generates output** → Summarizer classifies →
- If genuine question (needs human decision): pause and send
- If implementation detail dressed as question: continue working
- If false blocker (agent has access): continue working
- If status update: auto-continue (already works)

### Technical Approach

#### 1. Strengthen SOUL.md Anti-Escalation Section

Add explicit "DO NOT ASK ABOUT" list with concrete examples:

```markdown
### What I Do Not Ask About

These are NEVER questions for the supervisor:

**Implementation choices:**
- "Should I use approach A or B?" → Pick the better one
- "Which file should this go in?" → Use your judgment
- "What should the function be named?" → Name it well

**Resolvable obstacles:**
- "I can't find X" → Search harder, you have full codebase access
- "This needs manual action" → You have full system access, do it
- "Blocked on identifying..." → Use grep, glob, search tools

**Obvious fixes:**
- "Should we fix this bug?" → Yes, fix bugs
- "Should we add error handling?" → Yes, add error handling
- "Should we update the docs?" → Yes, update docs

**Already-answered questions:**
- Asking for clarification on something the user already specified
- Re-asking about tools/libraries when user already chose
```

#### 2. Add Decision-Making Heuristic

Add to SOUL.md "How I Work" section:

```markdown
### Decision Heuristic

When facing a choice:
1. Can I figure this out myself? → Do it
2. Is this a reversible decision? → Make it, move on
3. Is this an implementation detail? → My call
4. Would a senior engineer ask their PM this? → Probably not

Only escalate when:
- Missing credentials I genuinely can't obtain
- Scope change that affects timeline/budget
- Trade-off with significant business impact
- Conflicting requirements I can't resolve
```

#### 3. Refine Summarizer Blocker Flag

In `bridge/summarizer.py`, update `SUMMARIZER_SYSTEM_PROMPT`:

Current:
```
- If there are blockers or items needing PM action, flag on a separate line with "⚠️"
```

Change to:
```
- Flag with ⚠️ ONLY for genuinely external blockers (missing credentials, need third-party access, policy decisions)
- Do NOT flag: implementation choices, internal obstacles, things the agent could resolve with its tools
```

#### 4. Strengthen Classification Prompt

Update `CLASSIFIER_SYSTEM_PROMPT` to better detect false questions:

Add to the QUESTION classification:
```
NOT a question (classify as STATUS_UPDATE instead):
- Rhetorical questions in status reports
- "Should I fix this?" when it's obviously a bug
- Questions about implementation details the agent should decide
- Asking permission for things within its authority
```

#### 5. Escape Hatch Tool

Create `request_human_input()` tool for genuine uncertainty. This is a deliberate mechanism — requires explicit reason, is auditable, won't trigger accidentally like a keyword would.

```python
# In tools/ or bridge/
def request_human_input(
    reason: str,
    options: list[str] | None = None
) -> None:
    """
    Force a pause and request human input.

    Use ONLY when genuinely blocked on something you cannot resolve:
    - Missing credentials you cannot obtain
    - Ambiguous requirements after checking all context
    - Scope decision with significant business impact

    Args:
        reason: Clear explanation of why human input is needed
        options: Optional list of choices for the human to pick from
    """
```

The tool bypasses auto-continue and sends directly to chat with the reason displayed.

## Rabbit Holes

- **Over-engineering classification** — The classifier already works. Focus on the source (prompts) not the filter (classifier).
- **Adding more auto-continue logic** — The auto-continue system is fine. The problem is the LLM generating question-shaped output in the first place.
- **Removing all pauses** — Some pauses are legitimate. Don't eliminate the pause mechanism, just reduce false triggers.

## Risks

### Risk 1: Agent becomes too aggressive
**Impact:** Agent continues when it genuinely should ask, wasting time on wrong path
**Mitigation:** Keep the conservative classification threshold (80% confidence). Test with real conversations. Add explicit "ALWAYS ASK" list for genuine unknowns (missing credentials, scope changes).

### Risk 2: Instructions get overridden by model behavior
**Impact:** Claude's default politeness still triggers despite instructions
**Mitigation:** Use stronger language ("NEVER ask about", "DO NOT escalate"). Add concrete examples that the model can pattern-match against.

## No-Gos (Out of Scope)

- Not modifying the auto-continue counting mechanism (already works)
- Not changing the classification confidence threshold
- Not adding new classification types
- Not implementing "continue chains" or multi-step autonomy
- Not changing how sessions pause/resume (infrastructure is fine)

## Update System

No update system changes required — all changes are to prompt text in config/SOUL.md and bridge/summarizer.py. These propagate via normal `git pull`.

## Agent Integration

No agent integration required — this is a prompt/instruction change. The agent already has all necessary tools and access; it just needs clearer instructions about when to use them vs. when to ask.

## Documentation

- [ ] Update `config/SOUL.md` with strengthened anti-escalation instructions
- [ ] Add inline comments in `bridge/summarizer.py` explaining the blocker flag logic
- [ ] No feature docs needed — this is a behavior tuning, not a new feature

## Success Criteria

- [ ] SOUL.md contains explicit "DO NOT ASK ABOUT" section with examples
- [ ] SOUL.md contains decision-making heuristic
- [ ] Summarizer prompt refined to only flag genuine external blockers
- [ ] Classifier prompt updated to detect false questions
- [ ] `request_human_input()` escape hatch tool implemented
- [ ] Test: agent handles "create an issue for X" without multi-message Q&A
- [ ] Test: agent resolves "blocked on finding file" by searching
- [ ] Observed reduction in unnecessary pauses over 1 week

## Team Orchestration

### Team Members

- **Builder (prompts)**
  - Name: prompt-builder
  - Role: Update SOUL.md and summarizer prompts with anti-escalation instructions
  - Agent Type: builder
  - Resume: true

- **Builder (escape-hatch)**
  - Name: tool-builder
  - Role: Implement request_human_input() escape hatch tool
  - Agent Type: tool-developer
  - Resume: true

- **Validator (behavior)**
  - Name: behavior-validator
  - Role: Verify prompt changes are in place and test classification
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Update SOUL.md anti-escalation instructions
- **Task ID**: build-soul-instructions
- **Depends On**: none
- **Assigned To**: prompt-builder
- **Agent Type**: builder
- **Parallel**: true
- Add "What I Do Not Ask About" section with concrete examples
- Add "Decision Heuristic" section with escalation rules
- Strengthen "When I Escalate" section with clearer boundaries
- Ensure language is directive ("NEVER", "DO NOT") not suggestive

### 2. Update summarizer blocker flag
- **Task ID**: build-summarizer-prompt
- **Depends On**: none
- **Assigned To**: prompt-builder
- **Agent Type**: builder
- **Parallel**: true
- Refine SUMMARIZER_SYSTEM_PROMPT blocker flag guidance
- Add examples of what IS vs IS NOT a blocker
- Update CLASSIFIER_SYSTEM_PROMPT to detect false questions

### 3. Implement escape hatch tool
- **Task ID**: build-escape-hatch
- **Depends On**: none
- **Assigned To**: tool-builder
- **Agent Type**: tool-developer
- **Parallel**: true
- Create `request_human_input(reason, options)` function
- Integrate with bridge to bypass auto-continue
- Add to SOUL.md as the approved way to force a pause
- Write tests for the tool

### 4. Validate prompt changes
- **Task ID**: validate-prompts
- **Depends On**: build-soul-instructions, build-summarizer-prompt, build-escape-hatch
- **Assigned To**: behavior-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify SOUL.md contains the new sections
- Verify summarizer prompts are updated
- Verify escape hatch tool exists and has tests
- Run `black . && ruff check .`
- Run `pytest tests/` (ensure no regressions)

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: validate-prompts
- **Assigned To**: behavior-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met
- Generate final report

## Validation Commands

- `grep -n "What I Do Not Ask About" config/SOUL.md` — Anti-escalation section exists
- `grep -n "Decision Heuristic" config/SOUL.md` — Decision heuristic exists
- `grep -n "genuinely external" bridge/summarizer.py` — Blocker flag refined
- `black --check . && ruff check .` — Code quality
- `pytest tests/` — No regressions

## Resolved Questions

1. **Escape hatch:** Yes — implement as a tool call (`request_human_input(reason, options)`) rather than a keyword. Tool call forces explicit reason, is auditable, and won't trigger accidentally.

2. **Measurement:** PM will track manually and raise issues as needed. No automated measurement required.

3. **Reversibility:** Draft issue created at #86 with rollback options (quick: comment out SOUL.md sections; full: git revert).
