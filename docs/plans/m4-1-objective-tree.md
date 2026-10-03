---
tracking: none
slug: m4-1-objective-tree
type: build
status: planned
critique_rounds: 2
review_rounds: 2
---

# 4.1 The objective tree

Task 4.1 of [valor-rebuild.md](valor-rebuild.md), milestone 4. A task can
name a parent. The tree that results carries four rules the kernel holds:
a child's metered spending rolls up into its parent's reported spending, a
child's effect ceiling is never above its parent's, stopping a node stops
its whole subtree, and a child's report lands in the parent's dispatched
Brief and status.

Planned on the rebuild branch at 4cbc33669. Its build starts after 2.1
(the resident kernel) merges, and rebases onto it; what this plan assumes
of 2.1 is under "Assumed of 2.1".

## Goal

Tom delegates a feature, then a workflow, then a product (Mission item 4):
work too large for one session is split into children, each a task with its
own Brief, and the split never widens authority or hides spending (the
constraint **Bounded authority, metered spending**). Routines (4.3) are the
first user: a routine is a standing objective and each run is its child,
so the routine's metered spending over its period is the tree's rollup.

Spending is metered only (Tom's decision of 2026-10-03). "Rolls up" means
reported in the parent's status and Brief. Nothing refuses, pauses, or asks
Tom on money, at any node.

## Stakes

`critique_rounds: 2`, `review_rounds: 2`. The task changes the kernel's
task record, its stop, and what every turn's Brief carries. A mistake
either lets a child act above what its parent may, lets a stopped
subtree keep spending or performing, or shows Tom a spend that leaves part
of the tree out.

## The Done items, as evidence

1. **Rollup.** A child's metered spending rolls up into its parent's
   reported spending. Evidence: `status` on a task reports
   `tree_spent_usd_micros`, its own charges plus every descendant's, and
   `tree_open_calls`, every call opened and not yet charged anywhere in the
   subtree. A test on real Postgres charges calls on a root, a child, and a
   grandchild (one of them stopped, one holding a ledger's
   `gateway.reserved` row) and reads each node's tree total as the exact
   sum over its subtree. The property test below states it for any tree.
2. **The ceiling.** A child's effect ceiling is never above its parent's.
   Evidence: a Hypothesis property test (`tests/test_objective_tree.py`)
   over generated sequences of starts, effect requests, and stops on real
   Postgres shows, after every sequence:
   - every stored child's `max_effect_class` ranks at or below its
     parent's, and so at or below every ancestor's;
   - every `effect.held` and `effect.intent` on a node is of a class at or
     below the lowest ceiling on its path to the root;
   - a start that asked for more than its parent holds wrote no document
     and no `task.started`, and was never clipped to a lower class.
   A second property, over the pure rule alone (`tasks.child_ceiling`),
   covers every parent ceiling and every request, given or not.
3. **Stop.** Stopping a parent refuses the calls and effects of a
   grandchild. Evidence: a test on real Postgres stops a root and shows the
   grandchild's next `spending.open_call` refused with `gateway.refused`
   (reason `stopped`), its next `broker.request` refused with
   `effect.refused` (reason `task stopped`), and a held `act` the
   grandchild had, approved by Tom before the stop, refused at release
   with `TaskStopped`. A second test runs a scripted turn on a grandchild
   (`tests/scripted.py`, no spend), stops the root mid-turn, and shows the
   turn ends `stopped` and `tasks.audit` on the grandchild is empty.
4. **The report.** A child's report lands where the parent reads it.
   Evidence: a test delivers a child (`task.delivered`) and shows the
   parent's next dispatched Brief carrying a Children section with that
   child's id, instruction, state, subtree spending, and delivery summary;
   `turn.started` on the parent records that text and its digest, as it
   records every Brief. The parent's `status` lists the same reports.

The 39-test baseline and every test merged before this task pass
throughout.

## Threat model

What a turn controls: its workspace, the text it writes (its delivery
summary, its questions), and which effects it requests through `.valor/`.
In this task a turn cannot start a child (Left out), so a child's
`parent_id` and ceiling come only from Tom's command line or kernel code.

What the kernel must never do with it:

- take a child's ceiling, parent, or stop state from anything a turn
  wrote; they come from the Brief documents and the `task.stopped` rows the
  kernel writes;
- clip a request for a ceiling above the parent's to one that fits;
  it refuses the start in full;
- let a node of a stopped subtree open a call or perform an effect once
  the stop has committed;
- treat a child's delivery summary as anything but text rendered to the
  parent's turn. It changes no authority: the parent's ceiling is fixed in
  its Brief and the broker reads only that;
- refuse, pause, or ask on money at any node.

## Design

### The Brief and the start (`core/tasks.py`)

`Brief` gains `parent_id: str | None = None`. A stored Brief without it
loads as a root (`Brief.load` already drops nothing and defaults the rest).
`task.started` carries `parent_id` for a child; a root's carries none.

One pure rule, `child_ceiling(parent_ceiling, requested) -> str`:
`requested` of None gives the parent's ceiling; a requested class ranking
at or below the parent's is returned as given; one above raises
`CeilingRefused`, naming both classes. It never returns a class other than
the one requested or inherited.

`tasks.start` with `brief.parent_id` set, in its one transaction:

1. takes the parent's lock (`task:<parent_id>`), the same lock its stop
   takes;
2. reads the parent's Brief (`KeyError` for an unknown parent), and
   refuses a calibration parent (`CalibrationTask`) and a stopped one
   (`TaskStopped`);
3. applies `child_ceiling` to the parent's ceiling and the Brief's;
4. writes the document and `task.started`, unchanged.

Because a Brief is written once and the kernel role may only insert into
`documents`, a node's ceiling never changes after start, so the rule held
against the parent at start holds against every ancestor for the life of
the tree, by induction. The broker keeps reading only the node's own
ceiling; it needs no walk up the tree.

Code callers start a child through `tasks.start_child(conn, parent_id,
ceiling=None, **brief_fields)`, which resolves the ceiling with
`child_ceiling` under the parent's lock and writes the child in the same
transaction; `tasks.start` given a Brief with `parent_id` set goes through
the same path with the Brief's ceiling as the request. One code path writes
a child.

A child's workspace is its own, given the same way as any task's
(`--workspace` or `--project`); the tree shares no workspace.

### The tree (`core/tasks.py`)

`children(conn, task_id)` reads the direct children: the `task.started`
rows whose payload contains `{"parent_id": task_id}`, in id order, through
the existing `events_payload_gin` index (`@>` containment). `subtree(conn,
task_id)` walks down from a node by repeated `children` reads, in
breadth-first id order. There is no cycle to guard against: a child's id is
fresh at its start and its parent existed before it, so no task is ever its
own ancestor.

No new table, document kind, or index. A node is a task.

### Rollup (`core/tasks.py`)

`tree_spending(conn, task_id)` folds `spending(rows)` over each node of the
subtree and returns `tree_spent_usd_micros` (the sum) and
`tree_open_calls` (every open call, keyed by call id, with its task id and
estimate). It is a report: it reads rows from several streams without a
transaction and nothing reads it to decide anything. A stopped child's
spending counts like any other; money spent is spent.

`status` adds `parent_id`, `children` (the reports below), and the two
tree fields beside the node's own `spent_usd_micros` and `open_calls`. The
command line prints `tree_metered_spending` beside `metered_spending`.

The rollup is over all time. Spending over a period (a routine's thirty
days) is 4.3's, and filters the same rows by `at`.

### Stop fences the subtree (`core/tasks.py`)

`tasks.stop(conn, task_id, ...)` keeps its signature and its return (False
when the named task was already stopped). In one transaction it walks the
subtree top down, and for each node: takes the node's lock, writes
`task.stopped` if the node has none, then reads the node's children. The
named node's row is `{"reason": reason, "by": by}`, unchanged; each
descendant's adds `"by_stop_of": task_id`. For each row written it sends
`pg_notify(valor_stop, <node id>)`, so the process running any node's turn
hears its own id, as the runner listens. The command line prints
`stopped` with the count of descendants it stopped.

Why the fence is a row on every node, and not a generation each request
carries: the gateway, the broker's request and release, the runner, and
the fold all read `task.stopped` on their own task under its lock already.
Writing the row on each node keeps every one of those reads unchanged and
correct, keeps `events_one_stop` (one stop per task), and folds every
stopped descendant to `stopped` in its own status. A generation number
would be a new field each request carries and each reader compares, for the
same fence. The architecture's design note for nested stop is updated to
this.

Why the walk takes each node's lock before reading its children: a child
started under a node commits while holding that node's lock, so once the
stop holds the lock, every child of that node is visible to the next read
(read committed). A start racing a stop is therefore either refused
(it took the lock after the stop's row) or seen and stopped (it committed
first). Locks are taken parent before child on every path; a start takes
only its parent's lock and a call or effect only its own node's, so no
cycle of waits forms. Two stops of overlapping subtrees both lock top down
along tree paths.

A call already opened when the stop commits is still charged: a charge is
never refused, and the runner drains every call before it writes
`turn.ended`, as for any stop. A calibration task has no parent and is
never a child, so the walk never meets one.

### The report (`core/tasks.py`)

A child's report is derived, never copied: `child_report(conn, child_id)`
gives the child's id, instruction, state (its fold), `tree_spent_usd_micros`,
and its latest delivery (`summary` and `outcome`, or none). The parent's
reports list its direct children in start order. A grandchild reaches the
grandparent through its parent's own delivery and through the subtree
spending figure; the Brief lists one level.

`dispatch` appends a Children section as the last section of a working
session's Brief, when the task has children:

```
# Children

- <id> (<state>, metered spending $X.XXXXXX): <instruction>
  > <each line of the latest delivery summary>
```

It is rendered from the ledger at dispatch, ordered by start, with fixed
formatting, so the same store gives byte-identical text. It goes last
because it changes most often. A fresh session (critique, review, docs)
gets no Children section: it judges the candidate from the request, the
plan, and the diff, and a child's summary is another turn's narration.

### The command line (`core/__main__.py`)

- `start --parent ID`: starts a child. `--ceiling` defaults to absent; with
  `--parent` and no `--ceiling` the child takes the parent's ceiling, and
  without `--parent` the default stays `propose`. A refused start exits
  with `start refused:` and the reason (unknown parent, calibration parent,
  stopped parent, ceiling above the parent's). With `--project`, the
  workspace is provisioned only after the parent checks pass, and removed
  if the insert refuses, as the start already does for a failed insert.
- `stop`: prints `stopped (and N descendants)` or `already stopped`.
- `status`: adds `tree_metered_spending`.

## Assumed of 2.1

2.1's plan file did not exist when this was written. This plan assumes:

- `tasks.stop` stays the one stop path, writing `task.stopped` and a
  notification on `valor_stop` whose payload is the task id; whatever
  process runs a turn (the resident kernel) listens for its own task's id.
  If 2.1 moves the listener, the cascade still notifies each node's id.
- `tasks.dispatch` stays the one renderer of a turn's Brief, and the
  supervisor's "same store in, byte-identical context out" covers the
  Children section by its fixed order and format.
- 2.1 changes `core/broker.py` (the idempotency key gains the effect id).
  This task does not touch the broker.
- A child's turn takes the one turn slot like any task's. Waking a parent
  when a child delivers is not in 2.1's event list and is left out here.

If 2.1 merges with a different stop or dispatch path, the build applies
the same rules on that path and says so in its report.

## Tech debt absorbed

- `docs/architecture.md` and `core/README.md` name the spending fold
  `tasks.money`; the code's is `tasks.spending`. Both docs are fixed.
- `docs/architecture.md`'s Task and Brief, Metered spending, Stop, and
  Objective tree sections describe the tree as design; they are brought to
  what is built, including the stop fence as a row per node.
- `docs/data.md` says the tree's nodes are "the next kind" of document; a
  node is a task, so the line is corrected, and `task.started` and
  `task.stopped` list their new fields.
- `docs/tech-stack.md` marks the ceiling property as arriving with the
  tree; it becomes in use.
- `docs/routines.md` says the kernel has no objective tree; it is
  corrected, with the period report still 4.3's.

## Left out

- A turn starting a child. No performer is offered to turns; a child is
  started from the command line or by kernel code (the routine runner in
  4.3). A `start_child` performer, whose class and whose workspace rule need
  their own threat model, waits for the first task that needs to split
  itself (see the question below).
- A parent waiting on its children as a state, and the supervisor waking a
  parent when a child delivers.
- A per-task deadline (the architecture's design note); the idle bound
  stands in.
- Spending over a period; 4.3 builds it on `tree_spending`'s rows.
- Moving a task to another parent, or any change to a Brief after start.
- A count or depth limit on the tree. The leaf criterion (one session) is
  the only bound the design names, and it is a judgement of the turn or of
  Tom, not a kernel number.
- The status page view of the tree (4.3).

## Tests

All on real Postgres in the test database, in
`tests/test_objective_tree.py` unless named. None spends money.

**The ceiling, as properties.**

- `child_ceiling` over every parent class and every request (None or a
  class): the result ranks at or below the parent's; it equals the
  request when one is given and fits; None gives the parent's; a request
  above raises and never returns.
- The stateful property: Hypothesis draws a sequence of operations over a
  growing set of nodes: start (a root, or a child of a drawn node, with a
  drawn ceiling or none), request (a drawn node, a test performer of a
  drawn class: a read performer defined in the test, `WorkspaceWrite`
  for `propose`, `OutboxAppend` for `act`), open and charge (a drawn node,
  a drawn charge), and stop (a drawn node). After each sequence it checks
  the three ceiling statements of Done item 2, plus: every node with a
  stopped ancestor has its own `task.stopped`; no `gateway.opened` or
  `effect.intent` on a node has an id above its stop row's; and each node's
  `tree_spent_usd_micros` equals its own spend plus its children's tree
  totals, and equals the sum of every charge in its subtree. Each example
  uses fresh task ids in the session's database.

**The start.**

- A child with no ceiling given takes its parent's: a `read` parent gives
  a `read` child, not the root default `propose` (which would be refused).
- A child asking above its parent is refused: no document, no
  `task.started`, the message names both classes; the same request one
  rank lower succeeds unchanged.
- An unknown parent, a calibration parent, and a stopped parent are each
  refused with nothing written.
- A stored Brief document without `parent_id` loads as a root, and its
  status shows `parent_id: null`, no children, and tree totals equal to its
  own.
- From the command line: `start --parent` with and without `--ceiling`,
  and a refused start's exit text; with `--project`, a refused parent
  check provisions nothing.

**Stop.**

- Stopping a root writes `task.stopped` on the root, the child, and the
  grandchild in one transaction, the descendants' rows carrying
  `by_stop_of`; a listener on `valor_stop` hears all three ids.
- Stopping a child stops its subtree and leaves its parent and siblings
  running.
- The grandchild's call, effect request, and approved release after the
  root's stop are refused as in Done item 3.
- A grandchild's call opened before the stop and charged after it: the
  charge lands, the rollup counts it, and the next open is refused.
- The race: connection A starts a child under node Y and holds Y's lock
  before committing; connection B stops Y's parent X. B waits on Y's lock;
  when A commits, B's walk reads the new child and stops it. The reverse
  order (B holds Y's lock first) refuses A's start. Never a live child
  under a stopped ancestor.
- Stopping an already stopped root returns False and writes nothing.
- The live stop on a scripted grandchild turn, Done item 3.

**The report and rollup.**

- The parent's dispatched Brief carries the Children section with each
  child in start order, its state, its subtree spending, and its delivery
  summary quoted line by line; two renders over the same store are
  byte-identical; a fresh session's Brief has no Children section; a task
  with no children renders as before, byte for byte.
- A child's summary holding a Markdown heading stays inside its quote.
- A grandchild's delivery is not listed in the root's Brief; its spending
  is in its parent's subtree figure there.
- `status` on root, child, and grandchild: own spend, tree spend, open
  calls across the subtree with their task ids, including a
  `gateway.reserved` row on a child.

**Kept green.** Every existing test, the stop tests in `test_kernel.py`
and `test_reap.py` among them, unchanged.

## Files it changes

- `core/tasks.py`: `Brief.parent_id`, `child_ceiling`, `CeilingRefused`,
  the child path in `start` and `start_child`, `children`, `subtree`,
  `tree_spending`, `child_report`, the stop walk, the Children section in
  `dispatch`, the tree fields in `status`.
- `core/__main__.py`: `start --parent`, the `--ceiling` default, `stop`
  and `status` output.
- `tests/test_objective_tree.py` (new).
- Docs, by the docs stage: `docs/architecture.md`, `docs/data.md`,
  `docs/tech-stack.md`, `docs/routines.md`, `core/README.md`.

It does not change `core/schema.sql`, `core/broker.py`, `core/spending.py`,
`core/gateway.py`, or `core/runs.py`. Other tasks change `core/tasks.py`
and `core/__main__.py` (2.1 among them), so this task merges after 2.1,
one kernel merge at a time, rebased onto it.

## Rollout

No schema change: no table, index, constraint, or grant is added, and no
row is rewritten. A task without `parent_id` reads as a root.

1. `python -m core backup` before the merge, as for every kernel merge.
2. Fast-forward and push the rebuild branch.
3. Pull the running kernel's checkout and restart the kernel service (the
   resident kernel 2.1 installs), so `tasks.py`'s new stop and dispatch
   are the ones running. No `migrate`.
4. Check with one root and one child started from the command line on the
   kernel's own database, stopped together, and their status read.

## Decided by default

- **The fence is a `task.stopped` row on each node, written by one walk in
  one transaction**, not a generation each request carries. Every
  existing reader of the stop stays as it is; the architecture's design
  note is brought to it.
- **A child with no ceiling given takes its parent's.** A child is work the
  parent would otherwise do; the root default `propose` would refuse every
  child of a `read` parent.
- **The ceiling refusal at a child's start is not new governance.** It is
  the broker's ceiling rule ("a task's ceiling bounds everything beneath
  it", architecture.md) applied where the tree first exists, named in the
  milestone's Done, holding no work for review and asking Tom nothing. The
  blind verifier still answers its boolean over the diff.
- **A child's report is derived from its ledger at read time**, not a row
  written on the parent's stream, so it cannot fall out of step with the
  child and needs no writer at each child transition.
- **The Brief lists direct children only**, with subtree spending; deeper
  reports travel through each parent's own delivery.
- **Fresh sessions get no Children section**: blind checks judge from the
  request, the plan, and the diff.
- **No index is added**: the children read uses the existing GIN index on
  the payload. A measured slow read is a reason for one later.
- **The rollup is over all time and counts stopped nodes**; periods are
  4.3's.

## Questions for Tom

1. Should a turn be able to split its own task into children in this task,
   or only Tom from the command line and kernel code such as the routine
   runner? **Assumed:** only Tom and kernel code. A turn-facing
   `start_child` effect is built when a task first needs to split itself,
   with its own class and workspace rule.
