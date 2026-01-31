---
name: make-plan
description: Create or update feature plan documents using Shape Up principles. Use when the user wants to plan a new feature, flesh out a plan, update an existing plan, or needs a structured approach to scoping work. Outputs to docs/plans/feature_{name}.md with problem statement, appetite, solution, risks, and boundaries.
allowed-tools: Read, Write, Edit, Glob, AskUserQuestion
---

# Make a Plan (Shape Up Methodology)

Creates structured feature plans in `docs/plans/` following Shape Up principles: narrow the problem, set appetite, rough out the solution, identify rabbit holes, and define boundaries.

## When to Use

- Planning a new feature
- Updating an existing plan
- User says "make a plan", "plan this out", "flesh out the idea"
- Scoping unclear or large requests
- Before starting significant implementation work

## Process

### Phase 1: Flesh Out at High Level

1. **Understand the request** - What's being asked?
2. **Narrow the problem** - Challenge vague requests:
   - Not: "redesign the auth system"
   - Yes: "login fails when users have 2FA enabled on certain providers"
3. **Set appetite** - Based on scope:
   - **Small**: 1-2 days (bug fixes, small enhancements)
   - **Medium**: 3-5 days (feature additions, moderate refactors)
   - **Large**: 1-2 weeks (new subsystems, major features)
4. **Rough out solution** - Key components and flow, stay abstract

### Phase 2: Write Initial Plan

Create `docs/plans/{feature_name}.md` with:

```markdown
---
status: Planning
appetite: [Small: 1-2 days | Medium: 3-5 days | Large: 1-2 weeks]
owner: [Name]
created: [YYYY-MM-DD]
---

# [Feature Name]

## Problem

[Real scenario showing the pain. User perspective. Specific, not vague.]

**Current behavior:**
[What happens now that's broken/painful]

**Desired outcome:**
[What success looks like]

## Appetite

**Time budget:** [Small: 1-2 days | Medium: 3-5 days | Large: 1-2 weeks]

**Team size:** [Solo | Pair | Small team]

## Solution

### Key Elements

- **[Component 1]**: [What it does, not how]
- **[Component 2]**: [What it does, not how]
- **[Component 3]**: [What it does, not how]

### Flow

[Breadboard-style flow showing user journey]

**Starting point** → [Action/affordance] → **Next place** → [Action/affordance] → **End state**

Example:
Settings page → Click "Enable 2FA" → Setup screen → Enter code → Confirmation → Back to settings (with 2FA enabled)

### Technical Approach

[High-level technical direction - stay abstract enough for implementation flexibility]

- [Key decision 1]
- [Key decision 2]
- [Integration points]

## Rabbit Holes & Risks

### Risk 1: [Description]
**Impact:** [What breaks if this goes wrong]
**Mitigation:** [How we'll handle it]

### Risk 2: [Description]
**Impact:** [What breaks if this goes wrong]
**Mitigation:** [How we'll handle it]

[Add more risks as needed]

## No-Gos (Out of Scope)

[Explicitly state what we're NOT doing. This is critical for scope control.]

- [Feature deferred to later]
- [Edge case we'll handle in v2]
- [Related but separate concern]

## Success Criteria

[Measurable outcomes tied to the appetite. What does "done" look like?]

- [ ] [Criterion 1]
- [ ] [Criterion 2]
- [ ] [Criterion 3]

---

## Open Questions

[Critical unknowns that need supervisor input before finalizing]

1. [Question about scope/approach]
2. [Question about priority/tradeoff]
3. [Question about technical constraint]
```

### Phase 3: Critique and Enumerate Questions

After writing the initial plan:

1. **Review assumptions** - What did I assume that might be wrong?
2. **Identify gaps** - What's unclear or risky?
3. **Enumerate questions** - List all questions needing supervisor input
4. **Add questions to plan** - Append to "Open Questions" section
5. **Send reply** - Notify user that plan draft is ready for review

**Message format:**
```
Plan draft created: docs/plans/{feature_name}.md

GitHub URL: https://github.com/tomcounsell/ai/blob/{branch}/docs/plans/{feature_name}.md

I've made the following key assumptions:
- [Assumption 1]
- [Assumption 2]
- [Assumption 3]

Please review the Open Questions section at the end of the plan and provide answers so I can finalize it.
```

### Phase 4: Finalize Plan

After receiving answers:

1. **Update plan** - Incorporate feedback, remove Open Questions section
2. **Mark as finalized** - Update frontmatter: `status: Ready`
3. **Suggest implementation prompt** - Provide a clear prompt for starting work

**Message format:**
```
Plan finalized: docs/plans/{feature_name}.md

GitHub URL: https://github.com/tomcounsell/ai/blob/{branch}/docs/plans/{feature_name}.md

When you're ready to implement, use this prompt:

---
Implement the plan in docs/plans/{feature_name}.md

Follow the solution approach, stay within the appetite, and avoid the identified rabbit holes. Check off success criteria as you complete them.
---
```

## Principles to Enforce

### 1. Narrow the Problem
**Bad:** "Improve the API"
**Good:** "API responses take 3+ seconds when fetching user data with nested relationships"

Push back on vague requests. Ask:
- What specific pain are we solving?
- Who's blocked and how?
- What's the real breakdown?

### 2. Avoid Grab-Bags
**Bad:** "Redesign the dashboard"
**Good:** "Dashboard takes too long to load; users can't find their recent projects"

Multiple unrelated features = multiple plans.

### 3. Set Appetite First
Time drives scope, not the other way around.

Fixed time → variable scope = shipping
Fixed scope → variable time = never shipping

### 4. Walk Through Use Cases
Validate the flow step by step:
- Where does the user start?
- What do they do?
- Where do they end up?
- What can go wrong?

### 5. Surface Rabbit Holes
Call out risks explicitly:
- "Third-party API might be rate-limited"
- "Database migration could fail on large datasets"
- "Browser compatibility unknowns"

### 6. Define Boundaries
State what we're NOT doing:
- "Not building a full calendar - just a day picker"
- "Not handling offline mode in this iteration"
- "Not supporting bulk operations yet"

### 7. Good is Relative
Success is relative to appetite:
- Small appetite → simple solution
- Large appetite → more sophistication

Don't pursue perfection beyond the time budget.

## Examples

### Example: Vague Request

**User:** "Make the auth better"

**Response:**
```
I need to narrow this down. What specific problem are you trying to solve?

- Is login failing for certain users?
- Is the flow too complicated?
- Is there a security concern?
- Is it too slow?

Or is there a specific story: "Users with [X situation] can't [Y action] because [Z breakdown]"?
```

### Example: Grab-Bag Request

**User:** "Redesign the settings page - add dark mode, notification preferences, profile editing, and export functionality"

**Response:**
```
This feels like multiple separate features bundled together. Each of these could be its own plan:

1. Dark mode toggle
2. Notification preferences
3. Profile editing
4. Data export

Which one is most urgent? Or is there a single pain point driving all of these?
```

### Example: Good Request

**User:** "Users can't reschedule meetings when the original attendee is offline. They have to wait for them to come back online to approve the change."

**Response:**
```
Great - this is specific. I'll create a plan for async meeting rescheduling.

Starting with Medium appetite (3-5 days) since it involves workflow changes and possibly notifications.
```

## Tips

- **Stay abstract in solutions** - Don't specify exact UI or implementation details
- **Use breadboarding** - Show flow as: Place → Affordance → Place
- **Fat marker sketches** - Simple diagrams, avoid pixel-perfect mockups
- **Challenge yourself** - Could this be simpler? What can we cut?
- **Make tradeoffs explicit** - "We're choosing speed over completeness here"

## Anti-Patterns to Avoid

❌ **Over-specifying** - Don't write implementation details in the plan
❌ **Estimation-first** - Don't start with "how long will this take?"
❌ **Kitchen sink** - Don't add "nice to haves" beyond the appetite
❌ **Perfect solutions** - Don't design for every edge case
❌ **Skipping risks** - Don't ignore technical unknowns
❌ **Vague success** - Don't leave "done" undefined

## Output Location

All plans go to: `docs/plans/{feature_name}.md`

Use snake_case for filenames:
- `async_meeting_reschedule.md`
- `dark_mode_toggle.md`
- `api_response_caching.md`

## Status Tracking

Status is tracked in the plan document's YAML frontmatter:
- `status: Planning` - Initial draft being created
- `status: Ready` - Finalized and ready for implementation
- `status: In Progress` - Being implemented
- `status: Complete` - Shipped to production
- `status: Cancelled` - Not pursuing this

Update status as work progresses. Keep all tracking in the plan document itself.
