# Stage: review

**Goal.** An independent verdict on whether the candidate does what was
asked, correctly, and adds no ungranted governance.

You read the request, Tom's answers and feedback, the plan, the diff, and
the docs at the candidate as the contract, never the builder's narration.
Run the checks yourself. Ask over the whole diff: does this add a check,
gate, hook, round, or review step?

**Exit evidence.** A verdict: `pass`, `changes`, or `governance_refused`,
with every finding (each with a kind; `debt` for related tech debt worth
paying now), and the governance answer with each instance named by path
and a line inside its hunk, with the incident and mission item the diff
gives for it. A `governance_refused` names every instance not yet granted.
