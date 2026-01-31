---
name: make-plan
description: Create or update feature plan documents using Shape Up principles. Use when the user asks to plan a feature, create a plan doc, update a plan, or flesh out an idea. Also use proactively when implementation work is requested but no plan exists yet.
---

# Make a Plan (Shape Up Method)

## When to Use This Skill

Use this skill when:
- User asks to "plan", "make a plan", "create a plan doc"
- User describes a feature idea that needs fleshing out
- User asks to "update a plan" or refine existing plan docs
- Implementation work is requested but no corresponding plan exists in `/docs/plans/`

**Use proactively**: If the user asks you to implement something non-trivial and no plan exists, invoke this skill FIRST to create the plan before building.

---

## Workflow

### Phase 1: High-Level Sketch
Understand the idea at a high level:
1. What problem are we solving? (outcome, not feature)
2. Who is this for? (user/use case)
3. What's the rough scope? (Small: 1-2d | Medium: 3-5d | Large: 1-2w)

Capture just enough to proceed - don't interview the user extensively yet.

---

### Phase 2: Write the Draft Plan

Create `/docs/plans/ACTIVE-{slug}.md` with the **Five Ingredients**:

```markdown
---
appetite: [Small/Medium/Large] ([X days/weeks])
status: Planning
owner: [Name]
created: [YYYY-MM-DD]
---

# [Feature Name]

## Problem
[2-3 sentences: Real scenario showing why this matters. User perspective. Specific pain, not vague "it would be nice".]

Example:
> "When Valor receives multiple Telegram messages in quick succession, the bridge processes them serially. Each job waits for the previous one to finish, even though they could run in parallel. A 5-message burst that could complete in 2 minutes takes 10 minutes."

## Appetite
**[Small/Medium/Large]** ([X days/weeks])

This is a time constraint, not an estimate. If the solution doesn't fit, we descope or don't do it.

## Solution (Breadboarded)

[Describe the approach at the right level of abstraction - NOT implementation details, NOT wireframes.]

Use **places**, **affordances**, and **connection lines**:
- **Places**: Key states, screens, or components
- **Affordances**: What can users/system do at each place?
- **Connections**: How do pieces flow together?

### Key Flows
1. **Flow 1**: [Trigger] → [Action] → [Outcome]
2. **Flow 2**: ...

### Fat Marker Sketch
[Optional: ASCII diagram or brief visual description if helpful]

```
[Place A] --[action]--> [Place B] --[result]--> [Place C]
```

**What we're NOT specifying:**
- Exact file structure
- Class/function names
- Database schema details
- UI pixel perfection

Leave room for the implementer to make decisions.

## Rabbit Holes (Risks & Unknowns)

[CRITICAL SECTION - don't skip this]

List what could derail this work:
- [ ] **Technical uncertainty**: [What's unknown? How will we handle it?]
- [ ] **Integration risk**: [What dependencies? What if they fail?]
- [ ] **Performance concern**: [What could be slow? What's the plan?]
- [ ] **Edge case**: [What weird scenarios exist? Are we handling them?]

Example:
- [ ] **Parallel git operations**: Current code assumes single working directory. Need to verify git worktrees work with our SDK client.
- [ ] **Redis connection pooling**: If 10 jobs spawn simultaneously, will we exhaust connections? Need to verify popoto handles this.

## No-Gos (Out of Scope)

[Explicitly state what we're NOT doing to hit the appetite]

- **Not doing [X]** because [reason - usually time/complexity]
- **Ignoring [edge case]** - can be handled manually or later
- **Deferring [feature Y]** - doesn't block the core value

Example:
- **Not implementing job prioritization** - FIFO is good enough for now
- **Ignoring failed job retry logic** - manual restart is fine initially
- **Not building a web UI** - CLI/logs are sufficient for Phase 1

## Open Questions

[Questions that need user/supervisor input before finalizing]

- [ ] **Q1**: [Specific question about requirements, approach, or constraints]
- [ ] **Q2**: ...

---

## Success Criteria

[How do we know this is done and working?]

- [ ] Criterion 1
- [ ] Criterion 2
- [ ] Criterion 3

Example:
- [ ] 5 parallel jobs complete in <3 minutes (vs 10+ minutes serially)
- [ ] Bridge survives restart with pending jobs (persistence works)
- [ ] No git conflicts between parallel worktrees
```

**Key Principles:**
- **Appetite first** - Time constraint drives scope decisions
- **Breadboarding language** - "Places, affordances, connections" not "files to change"
- **Rabbit holes mandatory** - Surface risks explicitly
- **No-gos explicit** - Say what we're NOT doing
- **No task breakdown** - Implementation details emerge during building, not planning

---

### Phase 3: Critique & Enumerate Questions

After writing the draft:

1. **Self-critique the assumptions**:
   - What did I assume about user needs?
   - What did I assume about technical approach?
   - What did I assume about existing system behavior?

2. **Extract open questions** from:
   - Rabbit holes that need decisions
   - No-gos that might be wrong
   - Assumptions that need validation

3. **Add questions to the "Open Questions" section** in the plan doc

4. **Save the plan** and reply to user:

```
Plan draft created at docs/plans/ACTIVE-{slug}.md

I made the following assumptions:
- [Assumption 1]
- [Assumption 2]
- [Assumption 3]

Open questions that need your input:
1. [Question 1]
2. [Question 2]
3. [Question 3]

Please review and provide answers so I can finalize the plan.
```

**Stop here and wait for user response.**

---

### Phase 4: Finalize the Plan

After receiving user answers:

1. **Update the plan doc** with answers:
   - Revise sections based on feedback
   - Remove answered questions from "Open Questions"
   - Add new "No-Gos" if scope changed
   - Update "Rabbit Holes" if new risks emerged

2. **Mark status as Ready**:
   ```yaml
   status: Ready
   finalized: [YYYY-MM-DD]
   ```

3. **Suggest an activation prompt** for when it's time to implement:

```
Plan finalized at docs/plans/ACTIVE-{slug}.md

When you're ready to implement, use this prompt:

---
Implement the plan at docs/plans/ACTIVE-{slug}.md

Follow the SDLC pattern:
1. PLAN: Review the plan doc and create implementation checklist
2. BUILD: Implement the solution
3. TEST: Run all tests, ensure they pass
4. REVIEW: Self-check against success criteria in the plan
5. SHIP: Commit, push, merge to main
6. CLEANUP: Move plan to docs/plans/completed/ or delete it

Focus on the "Solution" and "Success Criteria" sections.
Address all "Rabbit Holes" as you encounter them.
Respect all "No-Gos" - do not expand scope.

Start with Phase 1 [if plan has phases], ship it, then continue.
---

Reply when complete with:
- What was built
- Test results
- Any deviations from plan (with rationale)
- Link to commit/PR
```

---

## Examples

### Example 1: User asks for a feature directly

**User**: "Add authentication to the API"

**You (invoke this skill)**:
1. Sketch high-level (JWT? Session? OAuth? Appetite?)
2. Write draft plan with assumptions (JWT, 3 days)
3. List questions: "Should we use JWT or sessions? Do we need OAuth? What about password reset?"
4. Wait for answers
5. Finalize plan
6. Suggest activation prompt

### Example 2: User asks to "make a plan"

**User**: "Make a plan for parallel job execution"

**You (invoke this skill)**:
1. Understand problem (serial execution bottleneck)
2. Write draft plan (git worktrees + popoto persistence)
3. Critique assumptions (worktree cleanup? merge conflicts?)
4. List questions: "Should we limit max parallel jobs? What happens on git conflicts?"
5. Wait for answers
6. Finalize plan
7. Suggest activation prompt

### Example 3: User asks to update existing plan

**User**: "Update the plan to use Redis instead of JSON files"

**You (invoke this skill)**:
1. Read existing plan at docs/plans/ACTIVE-*.md
2. Revise "Solution" section (replace JSON persistence with popoto)
3. Update "Rabbit Holes" (Redis connection, async wrapping)
4. Update "No-Gos" if scope changed
5. List new questions if any
6. Wait for user confirmation
7. Finalize

---

## Important Notes

- **One plan, one file**: Each feature gets its own `ACTIVE-{slug}.md`
- **ACTIVE prefix**: Plans in progress use `ACTIVE-` prefix
- **Completed plans**: Move to `docs/plans/completed/` or delete after merge to main
- **No implementation details in plans**: Plans describe WHAT and WHY, not HOW at code level
- **Rabbit holes are non-negotiable**: Every plan must have this section filled out
- **Questions consolidation**: Ask ALL questions at once in Phase 3 - don't trickle them

---

## Anti-Patterns (Don't Do This)

❌ **Don't write task lists in plans**
```markdown
## Implementation Steps
- [ ] Create User model
- [ ] Add password hashing
- [ ] Write login endpoint
```
This is too prescriptive. Let the implementer figure this out.

❌ **Don't specify exact code structure**
```markdown
## Solution
We'll create a new file `auth/jwt.py` with a `generate_token()` function...
```
This is implementation detail, not a plan.

❌ **Don't skip rabbit holes**
```markdown
## Rabbit Holes
TBD
```
If you can't think of risks, you don't understand the problem yet.

❌ **Don't ask questions one at a time**
Bad: "What auth method should we use?" [wait] "What about password reset?" [wait]
Good: List all 5 questions at once in Phase 3.

---

## Success Indicators

You've done this right if:
- ✅ Plan fits on 1-2 pages (concise, not comprehensive)
- ✅ Non-technical person can understand the problem and value
- ✅ Implementer has clear direction but room for creativity
- ✅ Rabbit holes section makes you slightly uncomfortable (real risks listed)
- ✅ No-gos section prevents feature creep
- ✅ All critical questions asked in ONE message, not spread across 10

---

## File Naming Convention

- `ACTIVE-{slug}.md` - Plan in progress
- `{slug}.md` in `docs/plans/completed/` - Implemented and shipped (optional archive)

Slug format: lowercase, hyphens, descriptive
- Good: `parallel-job-execution`, `api-authentication`, `redis-job-persistence`
- Bad: `feature-1`, `new-thing`, `update`
