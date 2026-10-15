---
tracking: none
slug: ask-reconciliation
type: build
status: planning
critique_rounds: 0
review_rounds: 0
governance_grant: required, one tap per instance through python -m core grant
---

# Asks: reconciled before they start, and an inbox with backpressure

(draft: sections being filled)

## The request

On 2026-10-15 the lead filed `backlog:slack-dm-reply-context` with
`python -m core routine verify --finding`, and it started task
`133f2ae0965b` with no look at the other open tasks. Asked how such tasks
are reconciled with other open tasks of the same scope, the answer was that
they are not. Tom then asked for:

> agent orchestration so that the task writer gets feedback from the
> reconciler confirming the shape of the task, for which the requestor might
> have additional useful context and an unknown level of importance on what
> is being asked for. at the same time, there should be some backpressure on
> the inbox for asks

This plan reads that request as three parts:

1. **Reconciliation.** Before an ask becomes work, a reconciler reads it
   against the open tasks on its project and proposes a shape: start it,
   fold it into a task that already covers it, supersede a narrower task, or
   split it.
2. **The writer confirms.** The proposal goes back to whoever wrote the
   ask, with the scope restated, the importance the reconciler assumes, and
   only the questions that would change the decision. The writer confirms or
   amends it, adding context and importance.
3. **Backpressure.** A project has a bounded number of asks in flight.
   Asks past the bound wait in a queue, ordered by importance, and are
   reconciled again when a place frees.

## Freshness check

The baseline is `8f8d0ae3f`, the head of `main` on 2026-10-15. The
claims below were read at that commit (the skeleton is `d1aade8cc`).
Disposition: **unchanged**. No other plan in `docs/plans/` touches intake,
the judge's inputs, or scheduling order.

## What exists

**Dedupe is an exact key match.** `findings.start_fix`
(`core/findings.py:131-170`) takes `finding:<key>` locks and starts
nothing when a task holds a key. Keys are free-text slugs, so two
spellings of one defect start two tasks. Only routine and hand-filed
findings carry keys. Tom's chat asks (`core/intake.py:590-612`) and CLI
`start` (`core/__main__.py:694-714`) have no dedupe at all.

**The judge sees one ask alone.** Site `intake.underspecified`
(`core/judgement_tasks.py:17-73`) reads the instruction, an empty thread,
and the project name (`core/judgement_sites.py:94-96`). It has no input
naming another task. `precise` goes to `plan`, and `thin` to `clarify`
(`core/machine.py:447-453`).

**Clarify asks only Tom.** A `question.asked` row moves the task to
`waiting`. The question notice goes to the operator chat
(`core/notices.py:85-86`), whoever wrote the ask. Anyone can answer
through `python -m core answer --by B` (`core/session.py:741-777`), and the
row carries provenance. No routine answers.

**Folding has no landing for an open task.** `session.feedback`
(`core/session.py:780-830`) accepts `merge`, `merged`, and `ended` only.
`message.steered` reaches a running task in any non-terminal state
(`core/machine.py:418-439`), but only intake writes it
(`core/intake.py:542-556`). The kernel never stops a task itself. Stops
come from Tom (`core/tasks.py:534-576`).

**Scheduling has no importance.** One foreground and one background
harness job run per process (`core/serve.py:425-426`). Each takes the
ready task with the oldest latest activity (`core/serve.py:706-712`).
Routine and replay subtrees are background (`core/tasks.py:473-488`).
No priority field exists.

**There is no queue or cap.** `docs/machine.md:179` says "an incoming
request becomes a queued task", but no queue is built. `docs/mission.md`
item 6 counts attention and never blocks on it, and `m1-4u-caps` removed
the earlier invented caps. The steering inbox in
`docs/judgement-layer.md:387-395` is designed, not built.

## Governance: these are guards

## Design

## Out of scope

## Stakes

## Tests

## Done

## Rollout

## Open questions

## Record
