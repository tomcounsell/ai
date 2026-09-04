# SDLC Router Decision Reconciliation

The SDLC router decides in two movements: the guard list (G1-G9) runs first, and
if nothing trips, the dispatch table (`DISPATCH_RULES`) picks a row. Those two
movements ask the guards about **different skills**. Reconciliation is the step
that makes them ask about the same one.

`agent/sdlc_router.py::reconcile_dispatch` re-runs the guard list against the
skill the dispatch table actually selected, so a guard veto constrains the
decision that is about to ship.

## Why a guard must see the selected dispatch

Guards read `context["proposed_skill"]` — a value supplied by whoever called
`decide_next_dispatch`. `guard_g3_pr_lock` is the clearest case: it fires only
when the proposed or previously dispatched skill is in the plan-stage family
(`/do-plan`, `/do-plan-critique`), and it exists to stop a lane with an open PR
from being sent back to planning.

The dispatch table's selection was never run past that. A caller invoking
`next-skill` with no `--proposed-skill` supplies nothing, the guard list sees an
empty proposal and steps aside, and the table is then free to select
`/do-plan-critique` on a lane with an open PR and an APPROVED review — precisely
the dispatch G3 is written to forbid. The guard's coverage depended on the
caller having independently guessed the answer first.

Reconciliation closes that by substituting `primary.skill` into the context and
running the same guard list again. Nothing about the guards changes; what
changes is that they are now asked about the real decision.

The row that most needs this constraint is row 2b
(`_rule_critique_verdict_stale`). That row is marker-agnostic on purpose
(#1639), so it can rescue a lane out of a CRITIQUE `in_progress` dead end, and
it must stay that way. Reconciliation constrains its output from the outside —
G3 vetoes the redirect on a shipped, open-PR lane — while leaving 2b free to
fire unmodified on the lane shape it exists for. Editing 2b to special-case the
open-PR lane would reintroduce the dead end it was written to escape.

G3's redirect target on a clean-review lane with DOCS outstanding is `/do-docs`,
carrying the reason constant `G3_REDIRECT_REASON_DOCS_PENDING`.

## The single-pass bound

Termination is the design constraint, not an afterthought. `reconcile_dispatch`
runs the guard list **at most twice**:

1. Once with `primary.skill` proposed.
   - No veto (`None`) → the table's selection ships unchanged.
   - A guard's own `Blocked` or `Terminal` → returned as-is. It is already a
     terminating decision with its own reason and `guard_id`; wrapping it would
     add nothing.
   - A guard `Dispatch` naming the same skill the table chose → **agreement,
     not a veto**. The table's own `row_id` and `reason` are kept.
     Reconciliation exists to withhold dispatches, never to relabel ones no
     guard objected to.
   - A guard `Dispatch` naming a different skill → a redirect, checked once
     more.
2. Once with the redirect proposed.
   - No veto → the redirect ships.
   - A `Dispatch` re-proposing the redirect target → agreement again; the
     redirect ships.
   - Anything else → a single `Blocked`, described below.

A second veto never triggers a third pass. The alternative — iterating until the
guards agree — converges on nothing and reaches G4's oscillation cap several
turns later, reporting "stage oscillation" for a condition that is actually
"the guards contradict each other". That misattribution cost #2771 and #2334 a
manual unwedge each. Stopping immediately with evidence is the fail-closed
direction.

## The by-reference invariant

`stage_states` and `meta` **must** be passed through to `evaluate_guards` by
reference, never copied, here or in any caller.

The guards are not pure. `guard_g5_artifact_hash_cache` rewrites
`stage_states["_verdicts"]["CRITIQUE"]["artifact_hash"]` in place when it
detects a legacy full-bytes hash, and logs a WARNING on that rewrite. Because
reconciliation calls `evaluate_guards` a second time on the *same objects*, the
second call sees the already-migrated record and silently steps aside. That is
the whole reason double invocation is idempotent.

A defensive copy of `stage_states` or `meta` anywhere between the two passes
would hand the second pass the pre-migration hash again, re-running the
migration branch and doubling the log noise for every reconciled decision. This
is exactly the "safe" cleanup a later contributor would make without knowing
the guards mutate. Do not add one.

`build_decision_inputs` shallow-copies both dicts for the evidence payload
only, so that a `Blocked` a supervisor reads later is a snapshot rather than a
live alias. That copy is downstream of the guard passes and does not break the
invariant.

A raising guard is not caught here. Rule predicates in `decide_next_dispatch`
are try/except-wrapped; guards are not, and reconciliation preserves that
asymmetry. Swallowing a raising guard into a `NO_RULE` block would misreport a
bug as a routing hole.

## Reading a `Blocked` that carries two verdicts

When the redirect is itself vetoed, the router returns:

```
Blocked(
  reason="reconciliation: guard veto did not converge — table selected row
          '2b' ('/do-plan-critique'), redirect to '/do-docs' was itself vetoed",
  guard_id=RECONCILE_DEADLOCK_GUARD_ID,   # "RECONCILE_DEADLOCK"
  decision_inputs={...},
)
```

`RECONCILE_DEADLOCK` is a sentinel `guard_id` in the same short-code namespace
as `G2`/`G4`/`G7` and `NO_RULE`, so a consumer matches on it without parsing
prose. It does not mean a numbered guard fired. It means the routing table and
the guard list produced answers that do not compose, and no third opinion is
going to be requested.

`decision_inputs` carries what the decision was made from and what disagreed:

| Key | What it holds |
|---|---|
| `stage_states` / `meta` | The facts the router read, snapshotted |
| `unrecorded_dispatch` | The previous-dispatch-was-never-recorded signal, or `None` |
| `selected_row` / `selected_skill` | The dispatch table's own answer |
| `first_redirect` | The first guard's counter-proposal (`skill`, `reason`, `row_id`) |
| `vetoing_guard` | The second pass's verdict, summarized by its type |

For a supervisor, this is the difference between "the router refused" and "the
router refused *because*". A control plane that cannot show its own inputs
cannot be checked against a field report: the #3065 batch reported a `NO_RULE`
on a state (CRITIQUE APPROVED and completed, BUILD in progress, no PR) that a
dispatch row has owned since `c1e991972`, and the report could be neither
confirmed nor refuted, because the payload carried no `stage_states` and no
`meta`. The `NO_RULE` fallthrough now carries the same evidence for the same
reason.

Two verdicts in one `Blocked` is an instruction about where to look. The
disagreement is between the named row and the named guard, and one of them is
wrong about this lane. Resolving it means changing a rule or a guard, not
re-running the router: the state it read is attached, and re-running on the
same state produces the same deadlock.

## Diagnostic-only evidence never changes the decision

`Dispatch.unrecorded_dispatch` and `Blocked.decision_inputs` are both declared
`compare=False`. Evidence is not identity: two dispatches of the same skill for
the same reason are the same decision whether or not the previous one was
recorded, and a refusal's identity is its reason and `guard_id`, not the state
dump attached for a human. Without `compare=False`, attaching evidence would
silently redefine equality for every caller that compares a decision against an
expected `Dispatch(...)`.

`detect_unrecorded_dispatch` is a pure read. It writes nothing and never
changes which skill is dispatched; it names the hole — a skill dispatched with
no record in `_sdlc_dispatches`, a record naming a different skill, or a router
slot still `confirmed: False` — so a supervisor sees it on the decision itself
rather than inferring it from a G4 block four turns later.

## Related

- [SDLC Router Oscillation Guard](sdlc-router-oscillation-guard.md) — the guard
  list G1-G9 this step re-runs, including G4's oscillation cap and G8's
  artifact verification.
- [SDLC Lane Identity](sdlc-lane-identity.md) — the recorded slug G8's branch
  probe resolves through, and the evidence-gated repair at that decision point.
- [Machine-Readable Definition of Done](machine-readable-dod.md) — the graded
  verification outcomes the merge gate reads.
- Source: `agent/sdlc_router.py` (`reconcile_dispatch`, `build_decision_inputs`,
  `detect_unrecorded_dispatch`), `tools/sdlc_next_skill.py`
  (`build_decision_context`, the one context builder both `decide_next_dispatch`
  callers share, and `decide`, which surfaces `decision_inputs` in the CLI's
  JSON payload).
- GitHub issue: #3065
