# Scoping Principles (Shape Up)

## Narrow the problem

A plan solves one specific pain: "API responses take 3+ seconds when fetching users with nested
relationships", not "Improve the API". Push back on vague requests: what pain, who is blocked,
what is the real breakdown?

Multiple unrelated features are multiple plans (a grab-bag); split them.

### When the issue is fog-forward

Trigger: the issue carries a `## Fog (Not Yet Specified)` section or records blue-sky mode. The
direction is known but its specifics are not, so narrowing would fabricate certainty. Instead:

- Keep the low resolution. Resolve what can be resolved now (spikes, reading the code).
- Carry the rest as a **Not Yet Specified** list in the plan; items graduate into tasks as they clear.
- When the work spans several interdependent decisions across sessions, **chart a decision map**:
  one parent issue holding the destination, the decisions made so far, and the open fog, plus one
  child issue per decision. Resolve one decision at a time, record it on the map, then re-plan.
- One unknown direction is fog: chart it. Several known, unrelated features are a grab-bag: split them.

## Set appetite first

Appetite is a communication budget, not coding time; solo coding is fast and alignment is the
cost. Fix the interactions and let scope vary.

- **Small**: solo dev, no review. Ship it.
- **Medium**: solo dev + PM. 1-2 check-ins on scope, 1 review round.
- **Large**: solo dev + PM + reviewer(s). 2-3 PM check-ins, 2+ review rounds.

Do not pursue perfection beyond the budget, and do not add nice-to-haves beyond it.

## Shape the solution

Stay abstract (breadboard the flow as Place → Affordance → Place; no pixel-level or
implementation detail). Walk the use cases, name the rabbit holes, name the technical risks, and
make tradeoffs explicit.

## No-Gos: default to "do it now"

If a "follow-up" item touches only files already in this change set, fold it into the plan;
splitting it costs more coordination than the work. A No-Go is legitimate only when it is:

1. **External**: a human or external system must act (rotate a secret, click a third-party UI).
2. **Ordered**: it must wait for a human-gated event elsewhere (a separate deploy, a release window).
3. **Destructive**: an irreversible one-shot where review-before-execute is the safety mechanism.
4. **Separate slug**: genuinely a different feature with its own issue. File the issue first.

Plain "we'll do this later" is not a reason (#1325); the plan validator rejects untagged deferrals.
