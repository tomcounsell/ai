# Budget and effect class ruling, 2026-09-20

The architect read the M0 plans after reconcile and found the budget setting arbitrary: a per-space daily allowance of 2,000,000 tokens, 20 USD, and 10 cards, two of whose three numbers traced to no source, and a six-axis per-objective vector the model was asked to guess at framing. The same session renamed the effect classes. This record holds the rulings, the design edits made from them, and the cascade into the seams and the plans.

## The rulings

From the architect, in substance:

1. Runaway spend is the only legitimate concern a budget answers.
2. Infinite decomposition is a matter of decomposition practice, not budget. A model has a natural limit on how far it will split work before a piece is too small to hand off. In practice the opposite is the problem: too little decomposition, which an estimating step at delegation encourages.
3. Attention and consent are social behaviors the supervisor navigates from relative urgency, the person's availability, and so on. They are not ledger axes.
4. Cori sets the budget for each task in dollars (possibly wall clock), and orchestration uses token rates and seat prices when deciding to delegate.
5. The root of the budget is per project, never per day and never a total allowance. The return on the system is productivity. The person is, at first, the proxy for the value of a completed piece of work, and only a transitive proxy: the judgment moves to the record as the record earns it.
6. How much of a project a given kind of work deserves is dynamic. Visual design is part of the deliverable for a presentation, a nice-to-have on a user flow, and absent from backend work. No ratio between kinds of work is fixed in the kernel.
7. Complexity is removed until the system's behavior justifies a guardrail. Asked whether a per-space self-approval ceiling in dollars should replace the daily allowance as the bound on what the supervisor commits at the low classes: no. The supervisor's estimate is the only bound; the person sees the figure and the ledger says whether a ceiling is ever needed.
8. Effect classes are named, never numbered: `read`, `propose`, `act`. The old class 1 (sandbox writes, drafts, branches) and class 2 (reversible external effects) collapse into `propose`. Asked what the kernel rule for `propose` becomes: it is granted, and the supervisor decides when to ask before a reversible effect leaves the space; only an `act` always needs a person.

## What the design now says

**Budget** (architecture §4): one purpose, bounding runaway spend. One unit, `usd_micros`. The root is the objective: the supervisor estimates the cost at framing with a one-sentence basis, the estimate becomes the node's budget at Commit, children are carved from the parent's remaining, and only a person raises the root. There is no daily allowance, no per-space pool, and no ceiling on the estimate. Time is the existing `deadline` ceiling. Overrun is a card: the Brief ends, the node is FAILED with `budget_exhausted`, the disk is retained, and a `budget_increase` card's grant raises the root, re-approves, and delegates again. The supervisor's own turns are recorded, never budgeted; a turn is bounded by the render cap and one trigger per event, and its gateway token carries a kernel-set cap for its one model phase. The implicit Scribe is carved from the turn's objective when there is one and is its own kernel-set root when there is none. Attention and decomposition are measured and are not budgets. The ledger records estimate, actual, and verified outcome per task class.

**Effect classes** (README, architecture §4, tech stack §7): `read` on the grant; `propose` on the grant, with the supervisor asking first when it judges the person would want to be asked before something leaves the space; `act` always a person, per action, by passkey. Whether an effect stays in the sandbox or leaves the space is a fact about its route, since everything that leaves goes through the broker; the route decides what is verified (mandatory for anything that leaves the space and for every `act`, sampled for sandbox-only work) and what a card shows; the class decides who may authorize. The sandbox profiles are `scratch` and `verify` at `read`, `worktree` at `propose`. Away state pauses effects that would leave the space and every `act`. M0 runs at `propose` inside the sandbox; M2 ships the first proposals that leave the space; M3 ships `act`.

## Decisions made here from the rulings

Each is marked in the documents as decided from the rulings and can be overturned by the architect.

1. **Overrun reuses the FAILED path** rather than a new pause state: `budget_exhausted` is already a FAILED reason with a card and a `FAILED` to `APPROVED` edge on the person's grant. The Executor's disk is retained as after any stop, and the redelegation starts fresh from the retained worktree.
2. **Turns are not budgeted.** No `allocate_turn`, no standing node, no turn allocation. The turn's gateway token carries a per-phase cap the kernel sets from the counted prompt plus `max_tokens` at the seat's price; usage rows name the conversation and the objective the turn regards.
3. **Time as deadline.** Ruling 4 allowed wall clock as a possible second axis. The documents use the existing `deadline` ceiling, set at delegation, so a hung tool is caught without a second consumable.
4. **Verification is a line item of the estimate.** The Verifier is delegated from the objective's remaining, as before; the supervisor's basis names what verifying that artifact kind costs. The fixed two-thirds split in the supervisor plan goes.
5. **The ledger records the estimate** beside the actual and the verified outcome, per task class. `Contract` carries `budget` and `basis`.
6. **The capability grammar** reads `name@class` with the named class: `read@read`, `write@propose`, `bash@propose`, `ask@read`. `EffectClass` is an ordered Literal with a rank function for `covers` and `ceiling`.
7. **`push_branch` is a `propose` action.** It was class 1; it stays the only effect built at M0 and is live-tested, never called by a worker tool (seams ruling 9).

## Design edits

| Document | Edit |
|---|---|
| README | Bounded row and the budgets paragraph state money, deadline, and effect class; the class table is named; the audit weighting is by route |
| architecture decisions table | budget row REVISED to money per objective; standing budget row REVISED to turns recorded; effect classes row REVISED to the names; away state row |
| architecture §1, §2, §3.1, §3.4, §3.5, §4, §5, §7, §8, §9 | as summarized above; the manifest loses `standing_budget` and gains nothing |
| tech stack §2 summary rows, §4, §5, §6 profiles, §7, §9, §10, §13, Limits | pre-check in money at the seat's price; question is a `read`; profiles by name; broker bullets by route and class; attention measured; milestones by name; passkey for `act` |
| prereqs | the effect ceiling row notes the rename |

## Cascade

Seams version 3 is the contract; each plan below was rewritten against it in the same series of commits. `01-events` lost the `standing:` lock namespace, `06-sandbox` changed only class names in prose, and `09-memory` needed nothing; each carries the version 3 header.

| Plan | What moved |
|---|---|
| `00-seams` | §0 no standing lock; §1.1 no `StandingBudget`; §1.2 named `EffectClass` and the grammar; §1.3 `Budget` is `usd_micros` alone; §1.4 `Contract.basis`; §1.10 action classes by name; §1.12 no standing card, `budget_increase` on an objective only; §3.2 no `allocate_turn`, `standing_remaining`, `raise_standing`; `raise_budget(objective_id, by, approval_id)` in their place; §3.7 cards charge nothing; §3.9 turn tokens carry a cap; §4 `budget.overrun` fields; §6 `budget_ledger` columns; Round two entries that named the standing node are superseded |
| `02-tree` | one-axis conservation; the standing node, its lock key, `allocate_turn`, and `TestStandingConservation` go; `raise_budget` and `estimate` on the node; `descendants` and `cards` consumption go |
| `03-spaces` | `StandingBudget`, `usd_micros_per_day`, and the budget refusal in `check_may_open` go; `EffectClass` named; the manifest's `max_effect_class: propose` |
| `04-gateway` | pre-check in money at the seat's price; turn tokens carry a per-phase cap and no ledger node |
| `05-worker` | `tool_calls` and `wall_clock_s` consumption go; `record_tool` consumes nothing; capabilities by name |
| `07-broker` | class names on the actions and the ledger column; approval required at `act` or when a card was issued |
| `08-supervisor` | no standing node, standing card, `allocate_turn`, or two-thirds split; framing produces an estimate with a basis; self-approval at `read` and `propose` with the Commit card as the supervisor's judgment; the Scribe carved from the turn's objective or its own root; S3 restated |
| `09-memory` | nothing; the plan names no class and no budget axis (the Scribe's named capabilities live in seams §1.5 and Version 3 item 6) |
| `10-surface` | the Commit card shows one dollar figure and its basis; no `standing_budget_card`; `issue_card` charges nothing; `self_approve` at `read` and `propose` |
| `11-verifier` | `should_verify` by route; capabilities by name; the Verifier's budget from the objective's remaining |
| `99-integration` | the walkthrough at `propose`; the two objectives record estimate and actual |

Code changed with the cascade: `schemas/space.py` (`EffectClass` named and ranked, `StandingBudget` removed), `infra/spaces/psyoptimal.yaml` (`max_effect_class: propose`, no standing budget), `tests/test_space.py`, and the class labels in the broker module docstrings.
