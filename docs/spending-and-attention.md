# Spending and attention in the ledger

Two cross-cutting records of the SDLC state machine ([sdlc-state-machine.md](sdlc-state-machine.md)): what a task spends, and where Tom acts on it.

## Metered spending

Not a state. Every model call in every state is metered by the gateway and
its price recorded on the task, shown in `status` as `Metered spending`.
Money never refuses a call, ends a run, or changes the task's state; only
stop refuses calls. A turn that deliberately called the provider with the
machine's Claude login would spend outside the meter; Tom accepted that on
2026-10-01 (see [architecture.md](architecture.md), Limits).

## Attention in the ledger

Mission item 6 makes attention a ledger item. Every point where Tom acts on
a task is a row with provenance: `question.asked` and `question.answered`
(from `clarify`, `plan`, `build`, `patch`) and `feedback.given` (from
`merge`, `merged`) carry `by`, `via`, `at`, and `role_played`;
an `approval.granted` row the ledger already holds carries Tom's literal
message and the same provenance, and is still counted; nothing writes
one now; a guard grant carries his message, the incident, the
mission item, and the expiry. Questions and feedback rounds count as
interruptions, approvals and grants separately (`attention_counts`); both are shown on
the delivery and never block (Tom, 2026-10-01). The attention log's format
is specified in [mission.md](mission.md).
