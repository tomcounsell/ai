# Blue-Sky / Fog-Forward Planning

The SDLC on-ramp used to assume well-scoped work. A loose direction whose specifics are genuinely unknown got pushed back to be narrowed before the system would engage, which fabricates certainty nobody has. Two global skills now accept fog and say plainly what is unknown.

Source: issue #2340. The decision-map idea comes from [Wayfinder](https://github.com/mattpocock/skills/blob/main/skills/engineering/wayfinder/SKILL.md), borrowed as a lightweight affordance.

## `do-issue` blue-sky mode

Step 1 of `.claude/skills-global/do-issue/SKILL.md` chooses a mode:

- **Well-scoped** (default): verifiable acceptance criteria can be written from what the requester gave.
- **Blue-sky**: writing verifiable acceptance criteria would require inventing specifics the requester did not give.

The skill picks blue-sky only when the requester signals exploration, so an unattended run never stalls on the choice. The chosen mode is recorded in the issue body.

What blue-sky mode changes:

| Area | Blue-sky behavior |
|------|-------------------|
| Recon | Reads the area to ground the direction; fan-out only for cheap concerns. `## Recon Summary` keeps its four-bucket shape, so the recon gate still passes. |
| Definitions | Define what you can; terms the exploration exists to pin down go in the Fog section. |
| Acceptance criteria | Signals the fog cleared, still checkable. |
| `## Fog (Not Yet Specified)` | Lists known unknowns and the decisions that hang on each. Also where open questions from Step 4 live. |

`CHECKLIST.md` carries a one-line blue-sky reading on four checks (No undefined jargon, Measurable acceptance criteria, Observed not inferred, Recon performed). The kill criterion stays in force: an unobserved "could/would" pain still means file nothing. Not already decided and the other falsification checks apply unchanged.

## `do-plan` fog handling

Trigger: the issue carries a `## Fog (Not Yet Specified)` section or records blue-sky mode in its body. `SKILL.md` Phase 1 step 2 points to "When the issue is fog-forward" in `.claude/skills-global/do-plan/SCOPING.md`, which says:

- Keep the low resolution and resolve what can be resolved now.
- Carry the rest as a **Not Yet Specified** list in the plan; items graduate into tasks as they clear.
- For work spanning several interdependent decisions across sessions, chart a **decision map**: one parent issue (destination, decisions so far, open fog) and one child issue per decision, resolved one at a time with the result recorded on the map, then re-plan. Plain `gh` is enough.
- One unknown direction is fog: chart it. Several known, unrelated features are a grab-bag: split them.

## Fog and model selection

Phase 1.5 of `do-plan` sends survey and research spikes to the cheapest capable model and spends the plan author's strongest reasoning on the load-bearing decision the fog hangs on.

## Deferred: a dedicated charting skill

The full Wayfinder-style map discipline would be its own global skill. Its name (candidates `do-chart`, `do-wayfinder`, `do-map`, `do-survey`) is an owner decision tracked on #2340 and precedes building it. Once built, that skill absorbs and replaces the decision-map guidance in `SCOPING.md`, so the two never coexist as divergent sources.

## Not included

No validators or hooks enforce the Fog section, since re-imposing crispness would defeat the mode. The `/sdlc` router is unchanged.
