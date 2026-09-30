# NN. <Component name>

| | |
|---|---|
| Slug | `<slug>` |
| Milestone | M0 |
| Status | in progress \| ready \| reconciled |
| Seams version | the version line of `00-seams.md` this plan was written against |
| Owns | packages and tables this plan creates, from the index |
| Depends on | slugs whose seams this plan consumes |
| Written | date, and the commit this plan was written against |

## Purpose

One paragraph. Which property of M0 this component provides, in the words of the milestone table (tech stack §10), and what the two first objectives cannot do without it.

## What the documents say

A list, one line per decision this plan builds on: the section, its status, and the sentence that matters. This is the plan's evidence, and a reviewer checks it first. Anything the plan does that is not on this list is marked "Decided here:" in the Design section.

## What exists

Code from `docs/prereqs.md` and spikes this plan builds on or replaces, with paths. Say which spike code is lifted into the component and which is left in `spikes/`.

## Seams

**Consumed**: each schema, Protocol, kernel call, event type, or table from `00-seams.md` this component uses, by section.

**Provided**: what this component implements from that document, by section. Everything this component exposes to another appears in the seams document; nothing exposed appears only here.

## Design

The shortest description a builder needs. Requirements the index carries for this slug under "Requirements carried from the spikes" appear here first, each naming the task that meets it. Then modules and what each holds, tables and their columns where this plan owns them, the functions with signatures where they are the seam, the control flow for the one or two paths that matter. No alternatives. Every choice the documents did not make is a line beginning "Decided here:" with its reason.

## Tasks

In build order. Each is one commit's worth, with an acceptance check that is a command, a test name, or an observable.

1. **<task>.** What to build. *Accept:* `uv run pytest tests/test_x.py::test_y` green, or the observable.
2. ...

## Properties

For anything under `kernel/`, `gateway/`, or `broker/`. Each invariant as a Hypothesis property: the operations it ranges over, the statement that must hold, and the test name. Spike 01 (budget conservation) and spike 05 (capability attenuation) are the pattern.

## Out of scope

What this plan leaves for M1 or later, with the section that defers it, and any slice split off from this plan with a proposed slug. TODOs from the refinement record that touch this component are listed here by name so a builder knows they were seen and left alone.

## Risks

What could make this plan wrong, and which spike number or prerequisite finding bears on it. Short.

## Questions for the architect

Only questions the documents cannot answer and the plan cannot proceed under a stated assumption. Each with the assumption the plan currently makes, so work continues while the question waits.

## Seam amendments

Changes this plan needs in `00-seams.md`, each as the exact text to add or change and the reason. The lead accepts or refuses them at reconcile and records the decision here.

## Findings

Places where the design documents contradict each other, the code, or a spike result, with line references. The lead carries these into a review record. This plan builds against the more recent source and says which.
