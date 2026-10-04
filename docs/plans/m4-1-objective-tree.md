--- tracking: none slug: m4-1-objective-tree type: build status: built
critique_rounds: 2 review_rounds: 2 ---

# 4.1 The objective tree

Task 4.1 of [valor-rebuild.md](valor-rebuild.md), milestone 4. A task can name a
parent. The tree that results carries four rules the kernel holds: a child's
metered spending rolls up into its parent's reported spending, a child's effect
ceiling is never above its parent's, stopping a node fences its whole subtree,
and a child's report reaches the parent's next turn and its status.

Built on the rebuild branch at fba1da1da, with 1.4d and 2.1 merged; what it uses
of them is under "Built on 1.4d and 2.1", and every test in Tests is in
`tests/test_objective_tree.py`.

## Goal

Tom delegates a feature, then a workflow, then a product (Mission item 4): work
too large for one session is split into children, each a task with its own
Brief, and the split never widens authority or hides spending (the constraint
**Bounded authority, metered spending**; mission.md, "ceiling, conserved down
the tree"). Routines (4.3) are the first user: a routine is a standing objective
and each run is its child, so the routine's metered spending is the tree's
rollup.

Spending is metered only (Tom, 2026-10-03): "rolls up" means reported in the
parent's status and Brief; nothing refuses, pauses, or asks on money.

## Stakes

`critique_rounds: 2`, `review_rounds: 2`. The task changes the kernel's task
record, its stop, and what every turn's Brief carries. A mistake either lets a
child act above what its parent may, lets a stopped subtree keep spending or
performing, or shows Tom a spend that leaves part of the tree out.

## The Done items, as evidence

1. **Rollup.** A child's metered spending rolls up into its parent's reported
   spending. Evidence: `status` on a task reports `tree_spent_usd_micros`, its
   own charges plus every descendant's, and `tree_open_calls`, every call opened
   and not yet charged anywhere in the subtree. A test on real Postgres charges
   calls on a root, a child, and a grandchild (one of them stopped, one holding
   a `gateway.reserved` row) and reads each node's tree total as the exact sum
   over its subtree. The property test below states it for any tree.
2. **The ceiling.** A child's effect ceiling is never above its parent's.
   Evidence: a Hypothesis property test (`tests/test_objective_tree.py`) over
   generated sequences of starts, effect requests, and stops on real Postgres
   shows, after every sequence:
   - - every stored child's `max_effect_class` ranks at or below its
     parent's, and so at or below every ancestor's;
   - - every `effect.held` and `effect.intent` on a node is of a class at or
     below the lowest ceiling on its path to the root;
   - - a start that asked for more than its parent holds wrote no document
     and no `task.started`, and was never clipped to a lower class.
   A second property covers the pure rule (`tasks.child_ceiling`).
3. **Stop.** Stopping a parent refuses the calls and effects of a grandchild.
   Evidence: a test on real Postgres stops a root and shows the grandchild's
   next `spending.open_call` refused with `gateway.refused` (reason `stopped`),
   its next `broker.request` refused with `effect.refused` (reason `task
   stopped`), and a held `act` the grandchild had, approved by Tom before the
   stop, refused at release with `TaskStopped`. A second test runs a scripted
   turn on a grandchild (`tests/scripted.py`, no spend), stops the root
   mid-turn, and shows the turn ends `stopped` and `tasks.audit` on the
   grandchild is empty. A third shows a merged child under the stopped root
   keeps `merged` and refuses Tom's feedback.
4. **The report.** A child's report reaches the parent. Evidence: a test
   delivers a child (`task.delivered`) and shows the parent's next
   working-session prompt (`session.next_prompt`) carrying that child's delivery
   summary under the label `# Reports from your children`, and the parent's
   dispatched Brief carrying a Children section with the child's id,
   instruction, state, and subtree spending, kernel facts only. The parent's
   `status` lists the same reports.

The full suite at the merge base passes throughout.

## Threat model

What a turn controls: its workspace, the text it writes (its delivery summary,
its questions), and which effects it requests through `.valor/`. In this task a
turn cannot start a child (Decided by default), so a child's `parent_id`,
ceiling, instruction, `governance_grant`, and `harness` come only from Tom's
command line or kernel code.

What the kernel must never do with it:

- take a child's ceiling, parent, or stop state from anything a turn wrote;
  they come from the Brief documents and the `task.stopped` rows the kernel
  writes;
- clip a request for a ceiling above the parent's to one that fits; it refuses
  the start in full;
- let a node of a stopped subtree open a call, perform an effect, or be
  reopened once the stop has committed;
- put turn-written text into the Brief, the kernel's authoritative channel. A
  child's delivery summary reaches the parent only as prompt data under a label,
  the way answers, findings, and feedback do, and the parent's authority stays
  what its Brief and the broker say;
- refuse, pause, or ask on money at any node.

## Design

### The Brief and the start (`core/tasks.py`)

`Brief` gains `parent_id: str | None = None`. A stored Brief without it loads as
a root: `Brief.load` keeps the fields the dataclass has and the missing
`parent_id` takes its default. `task.started` carries `parent_id` for a child; a
root's payload is unchanged.

One pure rule, `child_ceiling(parent_ceiling, requested) -> str`: `requested` of
None gives the parent's ceiling; a requested class ranking at or below the
parent's is returned as given; one above raises `CeilingRefused`, naming both
classes. It never returns a class other than the one requested or inherited.

`tasks.start_child(conn, parent_id, *, ceiling=None, marker=None,
**brief_fields)` is the one path that writes a child, in one transaction:

1. takes the tree lock (`tree:<root id>`, the root found through `ancestors`;
   see The tree lock below);
2. reads the parent's Brief (`UnknownParent` for an unknown parent), refuses
   a calibration parent (`CalibrationTask`) and a fenced one (`TaskStopped`,
   below);
3. resolves the ceiling with `child_ceiling` before the Brief is built (a
   Brief with no ceiling fails its own check);
4. writes the document and `task.started`, whose marker fields are `marker`
   merged into the payload: `{"sdlc": 1}` when `marker` is None, so a child is
   an SDLC task by default.

`marker` is how 4.3 writes its objective node (an emulator run) as a child of
the routine's objective, passing its own `{"objective": NAME}`, under the
ceiling rule and the tree lock like any child. Its keys are 4.3's; 4.1 writes
the dict as given and refuses only one carrying `calibration` (a calibration
task cannot be stopped, so it cannot sit in a tree).

`tasks.start` given a Brief with `parent_id` set calls the same path with the
Brief's ceiling as the request.

Any task but a calibration task may be a parent: an SDLC task, a task started
before the state machine, or a node with no `sdlc` marker such as 4.3's
objective node. A calibration parent is refused because `stop` raises
`CalibrationTask` on one, which would abort the walk below and leave the subtree
unstoppable; the refusal keeps stop working.

A Brief is written once, so a node's ceiling never changes and the rule held
against the parent at start holds against every ancestor, by induction. The
broker reads only the node's own ceiling.

A child's workspace is its own, given the same way as any task's (`--workspace`
or `--project`). Its `governance_grant` and `harness` are neither inherited nor
bounded by the parent: they come only from Tom's command line or, in 4.3,
`routine.toml`, and no kernel path copies or invents a grant.

### The tree (`core/tasks.py`)

- `children(conn, task_id)`: the `task.started` rows whose payload contains
  `{"parent_id": task_id}`, in start order (the order of their `task.started`
  event ids; task ids are random), through the existing `events_payload_gin`
  index (`@>` containment).
- `subtree(conn, task_id)`: the descendants, breadth first, by repeated
  `children` reads.
- `ancestors(conn, task_id)`: the chain of parents up to the root, read from
  each Brief's `parent_id`, nearest first. 4.3 uses it to tell whether a task is
  routine-owned; the tree lock uses its last entry (or the task itself, for a
  root) as the root id.

No cycle is possible (a child's id is fresh, its parent older). No new table,
document kind, or index: a node is a task.

### Rollup (`core/tasks.py`)

`tree_spending(conn, task_id)` folds `spending(rows)` over each node of the
subtree and returns:

- `tree_spent_usd_micros`: the sum of every charge in the subtree;
- `tree_open_calls`: every call opened and not yet charged, keyed by call id,
  with its task id and estimate;
- `charges`: every `gateway.charged` row in the subtree as `{task_id, call_id,
  usd_micros, at}`, in event id order. 4.3's period fold filters these by `at`
  (its thirty days) and sums them; 4.1 does no period arithmetic.

It is a report: it reads rows from several streams without a transaction and
nothing reads it to decide anything. A stopped child's spending counts like any
other.

`status` adds `parent_id`, `fenced_by` (below), `children` (the reports), and
the two tree totals beside the node's own `spent_usd_micros` and `open_calls`.
The command line prints `tree_metered_spending` beside `metered_spending`.

### The fence (`core/tasks.py`, `core/session.py`)

A node is fenced when it or any ancestor has a `task.stopped` row.
`fenced_by(conn, task_id)` returns the nearest such node's id, or None;
`is_stopped` becomes `fenced_by(...) is not None`. Every reader that already
calls `is_stopped` under its own node's lock (the gateway's open, the broker's
request and release, the runner) gets the ancestor read with no change at its
call site. `session.feedback` takes the tree lock and adds the same read beside
its fold check, so feedback on a merged node under a stopped ancestor is
refused, naming the ancestor. This is the stop fence reaching nodes the walk
leaves unmarked, not a new check: a stopped task takes no feedback.

### The tree lock (`core/tasks.py`)

One advisory transaction lock per tree, `tree:<root id>`, taken by the three
writers that change what a tree holds or whether it may reopen: `start_child`,
`stop_tree`, and `session.feedback`. The bridge's binding (`intake._bind`),
which calls `tasks.stop` or `session.feedback` under a task's lock, takes the
tree lock first too. A root's tree lock is named by its own id, so a task with
no children takes it too. Each takes it before any task lock, so the order is
always tree, then task, and no cycle of waits forms. The readers of the fence
(the gateway's open, the broker's request and release, the runner) take only
their own node's task lock and read the fence through `is_stopped`.

A stop takes two locks (the tree's and the named node's) whatever the tree's
size, so Postgres's shared lock table is never the bound.

### Stop walks the subtree (`core/tasks.py`)

`tasks.stop_tree(conn, task_id, *, reason, by="tom", via="the command line",
role_played=False)` returns how many `task.stopped` rows it wrote: 0 when the
named node already has its own `task.stopped` row. `tasks.stop` keeps 2.1's
signature (`reason`, `by`, `via`, `role_played`) and returns `stop_tree(...) >
0`, so its callers are unchanged.

In one transaction it takes the tree lock, then the named node's task lock (so a
stop and an open on the named node never interleave), and:

1. **the named node** gets its own `task.stopped` row unless it has one,
   whatever its state, a merged one included: `{reason, by, provenance}` as 2.1
   writes it, and `pg_notify(valor_stop, <id>)`;
2. it walks the descendants top down, reading each node's children in start
   order. For each descendant:
   - - one with its own `task.stopped` row: the walk does not descend into
     it. Its subtree was walked when it stopped, and nothing starts under a
     fenced node. The test is the node's own row (`has_stop_row`), never
     `is_stopped`, which would see the named node's uncommitted row in the
     same transaction and prune every descendant;
   - - a `merged` one: no row, so its settled state stays `merged`; the walk
     descends into its children, which may be live. The fence reaches it
     through the ancestor read, and feedback on it is refused;
   - - any other: a `task.stopped` row with the same provenance plus
     `"by_stop_of": task_id`, and `pg_notify(valor_stop, <id>)`, so
     whatever process runs that node's turn hears its own id.

2.1's `events_notify` wakes `serve` on the walk's rows; its `schedule` passes
`merged` and `stopped` tasks by.

The command line prints `stopped (and N descendants)` or `already stopped`.

Why a row and an ancestor read: the row folds each stopped node to `stopped` and
carries the notification that kills its turn; the ancestor read covers merged
descendants. A generation each request carries would be a new field every reader
compares, for the same fence.

Why a descendant's call or effect needs no lock shared with the walk: a reader
on a descendant takes that node's task lock and reads the fence. One whose read
precedes the stop's commit is ordered before the stop, like a call opened a
moment earlier: its intent or call stands, and the stop's notification reaches
its runner, which revokes and drains it, as for any stop. One whose read follows
the commit is refused. Under the tree lock a start racing a stop is either
refused (it waited for the stop) or seen by the walk (it committed first).

A call already opened when the stop commits is still charged: a charge is never
refused, and the runner drains every call before `turn.ended`.

### The report (`core/tasks.py`, `core/session.py`)

`child_report(conn, child_id)` gives the child's id, instruction, state,
`tree_spent_usd_micros`, and its latest delivery (`summary` and `outcome`, or
none). The parent's reports list its direct children in start order. A
grandchild reaches the grandparent through its parent's own delivery and through
the subtree spending figure.

**In the Brief, kernel facts only.** `dispatch` appends a Children section as
the last section of any non-fresh Brief whose task has children, workspace or
not:

```
# Children

- <id> (<state>, metered spending $X.XXXXXX)
  > <each line of the instruction>
```

Children are listed in start order.

The instruction is Tom's or kernel code's, quoted line by line so a multi-line
one stays inside its bullet. Rendered from the ledger with fixed order and
format, so the same store gives byte-identical text; last, because it changes
most often. A fresh session gets no Children section.

**In the prompt, the child's words.** `session.next_prompt` renders, in order,
the entry prompt, 2.1's steering, the errors report, the effects report, and
last a section labelled `# Reports from your children` listing each child that
has delivered: its id, its delivery outcome, and its summary quoted line by
line. Every prompt of the parent carries each child's latest delivery, so a
report is never lost to a turn that failed or was stopped, and no spent-marker
is kept.

### Offered effects narrowed to the ceiling (`core/broker.py`, `core/session.py`)

The architecture's tree rule says capabilities are attenuated on dispatch.
`dispatch` receives usage lines from `session` through `run_turn`, not classes,
so the narrowing is in 1.4d's `Performers.offered(ceiling=None)`, which lists
only performers whose class ranks at or below `ceiling` when one is given, and
`session` calls it with the Brief's `max_effect_class`. A `read` child's channel
text offers no `propose` or `act` effect. The broker still refuses above the
ceiling either way; this makes what a turn is told match what it may do. It
applies to every task: a `propose` root does not see `push_branch` (`act`)
listed, which it could never use. Fresh sessions offer nothing, unchanged.

### The command line (`core/__main__.py`)

- `start --parent ID`: starts a child through `start_child`. `--ceiling`
  defaults to absent: with `--parent` the child takes the parent's ceiling,
  without it the default stays `propose`. `UnknownParent`, `TaskStopped`,
  `CalibrationTask`, and `CeilingRefused` each exit with `start refused:` and
  the reason, on both the `--workspace` and the `--project` path; on the
  `--project` path the provisioned workspace is removed first, as for a failed
  insert.
- `stop`: calls `stop_tree` and prints the count.
- `status`: adds `tree_metered_spending`.

## Built on 1.4d and 2.1

The build starts on a base with 1.4d and 2.1 merged
([m2-1-resident-kernel.md](m2-1-resident-kernel.md), `m1-4d-credential.md`) and
uses, as those plans give them:

- 1.4d's per-task `broker.Performers` (`get`, `offered`),
  `broker.request(conn, performers, task_id, action)`, `broker.release(conn,
  performers, effect_id)`, and `offered=` passed from `run_turn` to `dispatch`.
  4.1 adds `ceiling` to `offered`.
- 2.1's `tasks.stop(conn, task_id, *, reason, by, via, role_played)`, its row
  `{reason, by}` plus `provenance`; `stop_tree` takes the same and writes the
  same provenance on every row.
- 2.1's steering in `next_prompt`, after the entry prompt and before the
  errors report; the children's reports go last, after the effects report.
- 2.1's `events_notify` trigger and its `schedule`, which passes `merged` and
  `stopped` tasks by; its turn slot, which a child's turn takes like any task's.
  A child's delivery changes no state of the parent, so nothing wakes the parent
  on it.

2.1's file table lists 4.1 beside `core/schema.sql`; 4.1 changes no schema,
which is 2.1's table to correct. If either merges with a different shape, the
build applies the same rules on that shape and says so in its report.

## Tech debt absorbed

- `docs/architecture.md` and `core/README.md` name the spending fold
  `tasks.money`; the code's is `tasks.spending`. Both are fixed.
- architecture.md's Task and Brief, Metered spending, Stop, and Objective tree
  sections are brought to what is built.
- `docs/data.md` calls tree nodes "the next kind" of document; a node is a
  task. `task.started` and `task.stopped` list their new fields.
- `docs/tech-stack.md`'s ceiling property becomes in use; `docs/routines.md`
  stops saying the kernel has no tree.

## Left out

- A turn starting a child (Decided by default: built by 4.3's failure triage).
- A parent waiting on its children as a state, and the supervisor waking a
  parent when a child delivers.
- A per-task deadline.
- Spending over a period; 4.3 sums `tree_spending`'s `charges` by `at`.
- Moving a task to another parent, or any change to a Brief after start.
- A count or depth limit on the tree: the leaf criterion (one session) is the
  only bound the design names, a judgement, not a kernel number.
- The status page view of the tree (4.3).

## Tests

All on real Postgres in the test database, in `tests/test_objective_tree.py`
unless named. None spends money.

**The ceiling, as properties.**

- `child_ceiling` over every parent class and every request (None or a class):
  the result ranks at or below the parent's; it equals the request when one is
  given and fits; None gives the parent's; a request above raises and never
  returns.
- The stateful property: Hypothesis draws a sequence of operations over a
  growing set of nodes: start (a root, or a child of a drawn node, with a drawn
  ceiling or none), request (a drawn node, a test performer of a drawn class,
  from a per-task `Performers` holding a read performer defined in the test,
  `WorkspaceWrite` for `propose`, and `OutboxAppend` for `act`), open and charge
  (a drawn node, a drawn charge), and stop (a drawn node). After each sequence
  it checks the three ceiling statements of Done item 2, plus: every node with a
  stopped ancestor is fenced, and has its own `task.stopped` unless it is
  merged; no `gateway.opened` or `effect.intent` on a node has an id above its
  nearest fencing stop row's; and each node's `tree_spent_usd_micros` equals its
  own spend plus its children's tree totals, and the sum of every charge in its
  subtree. Each example uses fresh task ids.

**The start.**

- A child with no ceiling given takes its parent's: a `read` parent gives a
  `read` child, not the root default `propose`.
- A child asking above its parent is refused: no document, no `task.started`,
  the message names both classes; one rank lower succeeds unchanged.
- An unknown parent, a calibration parent, a stopped parent, and a merged
  parent under a stopped ancestor are each refused with nothing written.
- A parent with no `sdlc` marker (a `task.started` shaped like one written
  before the state machine) takes a child, and stopping it stops the child.
- A stored Brief without `parent_id` loads as a root, tree totals equal to its
  own; `ancestors` on a grandchild gives child then root.
- A child's `governance_grant` and `harness` are what its start gave, never
  the parent's.
- From the command line: `start --parent` with and without `--ceiling`; each
  refusal's `start refused:` text on the `--workspace` path and on the
  `--project` path, where the workspace is removed and no traceback printed.

**Stop.**

- Stopping a root writes `task.stopped` on the root, the child, and the
  grandchild in one transaction, the descendants' rows carrying `by_stop_of` and
  the same provenance (`via`, `role_played`); a listener on `valor_stop` hears
  all three ids; `stop_tree` returns 3 and `stop` True.
- Stopping a merged root writes its own row; it folds `stopped` and Tom's
  feedback is refused.
- The walk's pruning reads the node's own row: with the named node's row
  written in the same transaction, its unstopped descendants are still walked
  and stopped.
- Stopping a child leaves its parent and siblings running.
- A merged child under the stopped root: no `task.stopped` row, status
  `merged` with `fenced_by` the root, Tom's feedback refused naming the root,
  and its own live child stopped by the walk.
- Pruning: no row under a child stopped earlier. Locks: during a walk over a
  tree with a hundred merged and live descendants, another connection can take
  any descendant's task lock; the walk holds the tree lock and the named node's
  only.
- The grandchild's call, effect request, and approved release after the root's
  stop are refused as in Done item 3.
- A call opened before the stop and charged after it is counted; the next open
  is refused.
- The race: connection A starts a child under node Y and holds the tree lock
  before committing; connection B stops Y's parent X and waits on the tree lock;
  when A commits, B's walk reads the new child and stops it. The reverse order
  refuses A's start. Feedback on a merged node racing a stop of its ancestor is
  likewise either refused or ordered before the stop, whose walk then stops the
  reopened node. Never an unfenced child under a stopped ancestor.
- `start_child` with `marker={"objective": "x"}` writes a child whose
  `task.started` has that marker and no `sdlc`, under the ceiling rule; a marker
  carrying `calibration` is refused; with no marker the child carries `sdlc: 1`.
- `tree_spending`'s `charges` lists every subtree charge with task id and
  `at`; `children` lists in start order whatever the ids sort to.
- Stopping a node with its own stop row returns 0 (`stop`: False) and writes
  nothing.
- The live stop on a scripted grandchild turn, Done item 3.

**The report, the Brief, the channel.**

- The parent's next prompt carries `# Reports from your children` with each
  delivered child's summary quoted line by line, in start order; a child that
  has not delivered is absent; after a failed parent turn the next prompt
  carries it again.
- The parent's Brief carries the Children section with id, state, subtree
  spending, and the quoted instruction, and no summary text; a summary holding
  `# Brief` or an instruction to the parent appears in no Brief.
- Two renders over the same store are byte-identical; a fresh session's Brief
  has no Children section; a parent with no workspace gets one; a task with no
  children renders as before apart from the effects list narrowed to its
  ceiling, and an `act` task with no children renders as before, byte for byte.
- A multi-line instruction stays inside its bullet; a grandchild's delivery is
  not in the root's prompt, its spending is.
- `Performers.offered(ceiling)`: a `read` task's channel text lists no
  `propose` or `act` performer; a `propose` task's lists no `act` one; an `act`
  task's lists all; `offered()` with no ceiling lists all. Existing tests that
  assert the full list on a lower ceiling are updated to it.
- `status` on root, child, and grandchild: own spend, tree spend, open calls
  across the subtree with their task ids, including a `gateway.reserved` row on
  a child.

**Kept green.** Every existing test, the stop tests in `test_kernel.py` and
`test_reap.py` among them.

## Files it changes

- `core/tasks.py`: `Brief.parent_id`, `child_ceiling`, `CeilingRefused`,
  `start_child` and the child path in `start`, `children`, `subtree`,
  `ancestors`, the tree lock, `fenced_by`, `has_stop_row` and `is_stopped`,
  `tree_spending`, `child_report`, `stop_tree` and `stop`, the Children section
  in `dispatch`, the tree fields in `status`.
- `core/session.py`: the reports section in `next_prompt`, the tree lock and
  fence read in `feedback`, the ceiling passed to `offered`.
- `core/broker.py`: `Performers.offered(ceiling=None)`.
- `core/__main__.py`: `start --parent`, the `--ceiling` default, the refusals
  on both start paths, `stop` and `status` output.
- `tests/test_objective_tree.py` (new); existing tests asserting the full
  offered list on a lower ceiling.
- Docs, by the docs stage: `docs/architecture.md`, `docs/data.md`,
  `docs/tech-stack.md`, `docs/routines.md`, `core/README.md`.

It does not change `core/schema.sql`, `core/spending.py`, `core/gateway.py`, or
`core/runs.py`. Other tasks change `core/tasks.py`, `core/session.py`,
`core/broker.py`, and `core/__main__.py` (2.1 among them, and 1.4d), so this
task merges after 1.4d and 2.1, one kernel merge at a time, rebased onto them.

## Rollout

No schema change: no table, index, constraint, or grant is added, and no row is
rewritten. A task without `parent_id` reads as a root.

1. `python -m core backup` before the merge, as for every kernel merge.
2. Fast-forward and push the rebuild branch.
3. Pull the running kernel's checkout and restart the kernel service (the
   resident kernel 2.1 installs), so the new stop, dispatch, and prompt are the
   ones running. No `migrate`.
4. Rollout check on the kernel's own database: start a root with the
   instruction `ROLLOUT CHECK 4.1: not work, stopped at once` and a child whose
   instruction is `ROLLOUT CHECK 4.1 child: not work, stopped at once`, both
   `--ceiling read`, read their status, and stop the root.

## Decided by default

- **The fence is a stop row on the named node and each unsettled descendant
  plus an ancestor read**, not a generation; merged descendants keep `merged`;
  the walk prunes at a node's own stop row; one tree lock serves the three tree
  writers; `start_child` takes 4.3's marker.
- **A child with no ceiling given takes its parent's.** The root default
  `propose` would refuse every child of a `read` parent.
- **The ceiling refusal at a child's start is not new governance**: it is the
  broker's ceiling rule applied where the tree first exists ("the kernel refuses
  a request it cannot meet in full rather than clipping it", architecture.md),
  named in the milestone's Done, asking Tom nothing. The blind verifier still
  answers its boolean over the diff.
- **The Brief carries kernel facts about children; their words go in the
  prompt**, every prompt carrying each child's latest delivery.
- **Offered effects are narrowed to the ceiling for every task**, in 1.4d's
  `Performers.offered`, per the architecture's "attenuated on dispatch".
- **A turn-facing `start_child` is a `propose` performer**, per routines.md
  ("start a child task, inside its own ceiling"), built by the first task that
  needs it: 4.3's failure triage, where a failure starts a child to investigate.
  4.3's plan does not build it either; whichever task first needs it writes its
  threat model (its workspace rule and where the child's instruction, now
  turn-written, is rendered).
- **Every refusal has a source or a function; nothing caps the tree.**
  Ceiling: architecture.md and routines.md ("The kernel refuses"). Stopped or
  fenced parent, feedback on a fenced merged node: the stop fence. A calibration
  parent or marker: stop cannot walk through one. Unknown parent: no Brief to
  read. Nothing routes to Tom. Checked against Tom's rule on invented caps;
  nothing was dropped.
- **The Brief lists direct children only**; the rollup is over all time.
- **The merged base is as the plan gives it.** `Performers.offered` takes a
  ceiling; `tasks.stop` keeps 2.1's signature and returns `stop_tree(...) > 0`,
  so the bridge's stop stops the subtree too; the tree is documented in
  architecture.md's The objective tree (no `docs/objective-tree.md` exists);
  `docs/metered-spending.md` also named `tasks.money` and is fixed.
- **Scope is "Spending is metered only (Tom, 2026-10-03)"**; the plan has no
  separate feedback section, and nothing in the tree caps or refuses on money.
- **Answers need no fence of their own.** An answer needs `waiting`; the walk
  gives every unmerged descendant its own row (it folds `stopped`) and a merged
  node is not `waiting`, so `answer`'s state check refuses one under a stopped
  ancestor. Tested.
- **Smaller choices.** `ancestors` is one recursive query; the Brief's
  spending reads to the micro-dollar (`$1.500000`), the command line's to four
  places; the property test also draws merges, forged as `tests/test_machine.py`
  builds them; `stop` prints `stopped`, `stopped (and N descendants)`, or
  `already stopped`.

## Critique round 1 (of 2): revise

1. A child's summary left the parent's Brief for `next_prompt`.
2. The walk prunes at stopped nodes; merged nodes keep `merged`, fenced by
   the ancestor read, feedback refused.
3. The question was dropped; a turn-facing `start_child` is 4.3's.
4. `ancestors` built; any non-calibration task may be a parent.
5. Offered effects narrowed to the ceiling.
6. `stop_tree` returns a count; start refusals caught on both paths; the
   rollout check labelled; smaller wordings fixed.
7. Grant and harness never inherited; Children section without a workspace;
   multi-line instructions quoted.

## Critique round 2 (of 2): revise

Both rounds are spent; each finding is folded in.

1. The walk's prune and the zero return read the node's own row
   (`has_stop_row`), never `is_stopped`; tested.
2. The named node always gets its row, a merged one included; only merged
   descendants are exempt; tested.
3. Built on 1.4d's `Performers.offered(ceiling)` and 2.1's stop signature;
   "Built on 1.4d and 2.1" places the reports after 2.1's steering.
4. One `tree:<root>` lock for `start_child`, `stop_tree`, and `feedback`; no
   lock per node.
5. `start_child` takes a `marker` for 4.3's objective node; the marker is
   4.3's.
6. The render test allows the narrowed effects list.
7. `tree_spending` returns `charges` with `at`; children in start order.

## Patch round 1

`intake._bind` takes the tree lock before the task lock (no other task-lock
holder calls `start_child`, `stop_tree`, or `feedback`). Tests: a bridge stop
and a bridge feedback, each bound while the command line's stop holds the tree
lock, complete without deadlock; a bridge "stop" on a root stops child and
grandchild. An unknown parent raises `UnknownParent`, the only lookup error the
command line catches. `tree_spending` folds `spending` per node. Answers:
"Decided by default", tested.
