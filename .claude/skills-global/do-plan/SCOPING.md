# Scoping Principles (Shape Up Methodology)

These principles guide how to narrow, size, and scope a plan before writing it.

## 1. Narrow the Problem

**Bad:** "Improve the API"
**Good:** "API responses take 3+ seconds when fetching user data with nested relationships"

Push back on vague requests. Ask:
- What specific pain are we solving?
- Who's blocked and how?
- What's the real breakdown?

### When the issue is fog-forward

Trigger: the issue carries a `## Fog (Not Yet Specified)` section or records blue-sky mode in its body. Here the goal is a direction whose specifics are genuinely unknown, so narrowing it would fabricate certainty. Instead:

- Keep the low resolution. Resolve what can be resolved now (spikes, reading the code).
- Carry the rest as a **Not Yet Specified** list in the plan. Items graduate into tasks as they clear.
- When the work spans several interdependent decisions across sessions, **chart a decision map**: one parent issue holding the destination, the decisions made so far, and the open fog, plus one child issue per decision. Resolve one decision at a time, record each result on the map, then re-plan.
- Tell fog from a grab-bag. One unknown direction is fog: chart it. Several known, unrelated features are a grab-bag: split them (see below).

## 2. Avoid Grab-Bags

**Bad:** "Redesign the dashboard"
**Good:** "Dashboard takes too long to load; users can't find their recent projects"

Multiple unrelated features = multiple plans.

## 3. Set Appetite First

Communication budget drives scope, not the other way around.

Solo dev work is a rounding error — alignment is the real cost.
Fixed interactions -> variable scope = shipping
Fixed scope -> variable interactions = never shipping

**Appetite sizing** - Based on communication overhead, not dev time (solo coding is fast; alignment is the bottleneck):
- **Small**: Solo dev, no review. Ship it.
- **Medium**: Solo dev + PM. 1-2 check-ins to align on scope, 1 review round.
- **Large**: Solo dev + PM + reviewer(s). 2-3 PM check-ins, 2+ review rounds.

## 4. Walk Through Use Cases

Validate the flow step by step:
- Where does the user start?
- What do they do?
- Where do they end up?
- What can go wrong?

## 5. Surface Rabbit Holes

Call out tempting but wasteful avenues:
- "Don't try to support every auth provider — just Google and GitHub"
- "Don't build a custom date picker — use the browser native one"
- "Offline sync looks important but will triple the scope"

## 6. Identify Technical Risks

Call out things that could fail:
- "Third-party API might be rate-limited"
- "Database migration could fail on large datasets"
- "Browser compatibility unknowns"

## 7. Define Boundaries

State what we're NOT doing:
- "Not building a full calendar - just a day picker"
- "Not handling offline mode in this iteration"
- "Not supporting bulk operations yet"

**Default to "do it now" over "defer to follow-up."** The agent writes plists,
deletes schema fields, edits multi-machine configs (in this repo, via `/update`), and runs
migrations every day. If a "follow-up" item touches only files already in this
change set, fold it into the plan — splitting it across PRs costs more
coordination than the work itself.

A No-Go is only legitimate when one of these is true:

1. **External:** a human or external system must act (rotate a secret, click a
   third-party UI, sign a contract).
2. **Ordered:** the work must wait for a human-gated event in another system
   (a separate deploy, a release window, a downstream merge).
3. **Destructive:** irreversible one-shot where review-before-execute is the
   safety mechanism (data deletion, schema migration on hot tables).
4. **Separate slug:** the work is meaningfully a different feature with its
   own issue, not just leftover scraps from this one. File the issue first.

Plain "we'll do this later" is the laziness pattern this rule is designed to
block (issue #1325). The plan validator enforces tagged justifications on every
deferral.

## 8. Good is Relative

Success is relative to appetite:
- Small appetite -> ship without discussion
- Medium appetite -> align once, review once
- Large appetite -> iterate on alignment, multiple review rounds

Don't pursue perfection beyond the communication budget.

## Tips

- **Stay abstract in solutions** - Don't specify exact UI or implementation details
- **Use breadboarding** - Show flow as: Place -> Affordance -> Place
- **Fat marker sketches** - Simple diagrams, avoid pixel-perfect mockups
- **Challenge yourself** - Could this be simpler? What can we cut?
- **Make tradeoffs explicit** - "We're choosing speed over completeness here"

## Anti-Patterns to Avoid

- **Over-specifying** - Don't write implementation details in the plan
- **Estimation-first** - Don't start with "how long will this take?" — ask "how many people need to weigh in?"
- **Kitchen sink** - Don't add "nice to haves" beyond the appetite
- **Perfect solutions** - Don't design for every edge case
- **Skipping risks** - Don't ignore technical unknowns
- **Vague success** - Don't leave "done" undefined
