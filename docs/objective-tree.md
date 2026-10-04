Part of [architecture.md](architecture.md).

# The objective tree

Serves Mission item 4: Tom delegates a feature, then a workflow, then a
product.

**Built.** A task is a node. A child is a task whose Brief names its
`parent_id` (`tasks.start_child`, or `python -m core start --parent ID`).
Rules the kernel enforces:

- A child's spending rolls up into its parent's reported spending; no
  money is carved from a parent. `status` reports a task's own metered
  spending and its subtree's (`tree_spent_usd_micros`), with every call
  open anywhere in the subtree (`tree_open_calls`).
- A child's effect ceiling never exceeds its parent's. A child given no
  ceiling takes its parent's; one asking above it is refused with nothing
  written (`tasks.CeilingRefused`), never clipped [11]. A turn is offered
  only the effects its ceiling allows (`Performers.offered`).
- A child cannot start under an unknown task, a calibration task, or a
  stopped or fenced one.
- A child's report reaches its parent: each working-session prompt of the
  parent carries every direct child's latest delivery, quoted under
  `# Reports from your children`, and the parent's Brief ends with a
  Children section holding kernel facts only (each child's id, state,
  subtree spending, and instruction), never a child's words. `status`
  lists the same reports.
- Stopping a node fences its subtree (see Stop in [architecture.md](architecture.md)).

Starting a child, stopping, and feedback take one advisory lock per tree,
on its root, before any task lock, so a child started during a stop is
either seen by the stop's walk or refused. Binding Tom's reply to a task
takes the same tree lock before the task's, since a reply can stop the task
or give it feedback.

**Design.** Decomposition cuts by outcome [13], one agent per leaf. Fan-out
and depth are bounded by the leaf criterion (one session), never by a
count. A turn that starts a child is a `propose` performer, built by the
first task that needs it.
