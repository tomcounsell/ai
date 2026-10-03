---
tracking: none
slug: m4-1-objective-tree
type: build
status: planned; revised after critique round 1, awaiting round 2
critique_rounds: 2
review_rounds: 2
---

# 4.1 The objective tree

Task 4.1 of [valor-rebuild.md](valor-rebuild.md), milestone 4. A task can
name a parent. The tree that results carries four rules the kernel holds:
a child's metered spending rolls up into its parent's reported spending, a
child's effect ceiling is never above its parent's, stopping a node fences
its whole subtree, and a child's report reaches the parent's next turn and
its status.

Planned on the rebuild branch at 4cbc33669. Its build starts after 2.1
(the resident kernel) merges, and rebases onto it; what this plan assumes
of 2.1 is under "Assumed of 2.1".

## Goal

Tom delegates a feature, then a workflow, then a product (Mission item 4):
work too large for one session is split into children, each a task with its
own Brief, and the split never widens authority or hides spending (the
constraint **Bounded authority, metered spending**; mission.md, "ceiling,
conserved down the tree"). Routines (4.3) are the first user: a routine is
a standing objective and each run is its child, so the routine's metered
spending is the tree's rollup.

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
   grandchild (one of them stopped, one holding a `gateway.reserved` row)
   and reads each node's tree total as the exact sum over its subtree. The
   property test below states it for any tree.
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
   turn ends `stopped` and `tasks.audit` on the grandchild is empty. A
   third shows a merged child under the stopped root keeps `merged` and
   refuses Tom's feedback.
4. **The report.** A child's report reaches the parent. Evidence: a test
   delivers a child (`task.delivered`) and shows the parent's next
   working-session prompt (`session.next_prompt`) carrying that child's
   delivery summary under the label `# Reports from your children`, and the
   parent's dispatched Brief carrying a Children section with the child's
   id, instruction, state, and subtree spending, kernel facts only. The
   parent's `status` lists the same reports.

The full suite at the merge base passes throughout.

## Threat model

What a turn controls: its workspace, the text it writes (its delivery
summary, its questions), and which effects it requests through `.valor/`.
In this task a turn cannot start a child (Decided by default), so a
child's `parent_id`, ceiling, instruction, `governance_grant`, and
`harness` come only from Tom's command line or kernel code.

What the kernel must never do with it:

- take a child's ceiling, parent, or stop state from anything a turn
  wrote; they come from the Brief documents and the `task.stopped` rows the
  kernel writes;
- clip a request for a ceiling above the parent's to one that fits; it
  refuses the start in full;
- let a node of a stopped subtree open a call, perform an effect, or be
  reopened once the stop has committed;
- put turn-written text into the Brief, the kernel's authoritative
  channel. A child's delivery summary reaches the parent only as prompt
  data under a label, the way answers, findings, and feedback do, and the
  parent's authority stays what its Brief and the broker say;
- refuse, pause, or ask on money at any node.

## Design

### The Brief and the start (`core/tasks.py`)

`Brief` gains `parent_id: str | None = None`. A stored Brief without it
loads as a root: `Brief.load` keeps the fields the dataclass has and the
missing `parent_id` takes its default. `task.started` carries `parent_id`
for a child; a root's payload is unchanged.

One pure rule, `child_ceiling(parent_ceiling, requested) -> str`:
`requested` of None gives the parent's ceiling; a requested class ranking
at or below the parent's is returned as given; one above raises
`CeilingRefused`, naming both classes. It never returns a class other than
the one requested or inherited.

`tasks.start_child(conn, parent_id, *, ceiling=None, **brief_fields)` is
the one path that writes a child, in one transaction:

1. takes the parent's lock (`task:<parent_id>`), the same lock its stop
   takes;
2. reads the parent's Brief (`KeyError` for an unknown parent), refuses a
   calibration parent (`CalibrationTask`) and a fenced one (`TaskStopped`,
   below);
3. resolves the ceiling with `child_ceiling` before the Brief is built
   (a Brief with no ceiling fails its own check);
4. writes the document and `task.started`.

`tasks.start` given a Brief with `parent_id` set calls the same path with
the Brief's ceiling as the request.

Any task but a calibration task may be a parent: an SDLC task, a task
started before the state machine, or a node with no `sdlc` marker such as
4.3's objective node. A calibration parent is refused because `stop`
raises `CalibrationTask` on one, which would abort the walk below and
leave the subtree unstoppable; the refusal keeps stop working.

Because a Brief is written once and the kernel role may only insert into
`documents`, a node's ceiling never changes after start, so the rule held
against the parent at start holds against every ancestor for the life of
the tree, by induction. The broker keeps reading only the node's own
ceiling.

A child's workspace is its own, given the same way as any task's
(`--workspace` or `--project`). Its `governance_grant` and `harness` are
neither inherited nor bounded by the parent: they come only from Tom's
command line or, in 4.3, `routine.toml`, and no kernel path copies or
invents a grant.

### The tree (`core/tasks.py`)

- `children(conn, task_id)`: the `task.started` rows whose payload
  contains `{"parent_id": task_id}`, in id order, through the existing
  `events_payload_gin` index (`@>` containment).
- `subtree(conn, task_id)`: the descendants, breadth first, by repeated
  `children` reads.
- `ancestors(conn, task_id)`: the chain of parents up to the root, read
  from each Brief's `parent_id`, nearest first. 4.3 uses it to tell
  whether a task is routine-owned.

There is no cycle to guard against: a child's id is fresh at its start
and its parent existed before it, so no task is its own ancestor.

No new table, document kind, or index. A node is a task.

### Rollup (`core/tasks.py`)

`tree_spending(conn, task_id)` folds `spending(rows)` over each node of the
subtree and returns `tree_spent_usd_micros` (the sum) and
`tree_open_calls` (every open call, keyed by call id, with its task id and
estimate). It is a report: it reads rows from several streams without a
transaction and nothing reads it to decide anything. A stopped child's
spending counts like any other.

`status` adds `parent_id`, `fenced_by` (below), `children` (the reports),
and the two tree fields beside the node's own `spent_usd_micros` and
`open_calls`. The command line prints `tree_metered_spending` beside
`metered_spending`.

The rollup is over all time. Spending over a period is 4.3's, and filters
the same rows by `at`.

### The fence (`core/tasks.py`, `core/session.py`)

A node is fenced when it or any ancestor has a `task.stopped` row.
`fenced_by(conn, task_id)` returns the nearest such node's id, or None;
`is_stopped` becomes `fenced_by(...) is not None`. Every reader that
already calls `is_stopped` under its own node's lock (the gateway's open,
the broker's request and release, the runner) gets the ancestor read with
no change at its call site. `session.feedback` adds the same read beside
its fold check, so feedback on a merged node under a stopped ancestor is
refused, naming the ancestor. This is the stop fence reaching nodes the
walk leaves unmarked, not a new check: a stopped task already takes no
feedback.

### Stop walks the subtree (`core/tasks.py`)

`tasks.stop_tree(conn, task_id, *, reason, by)` returns how many
`task.stopped` rows it wrote (0 when the named node was already stopped).
`tasks.stop` keeps its signature and returns `stop_tree(...) > 0`, so its
callers are unchanged. In one transaction the walk goes top down from the
named node, and for each node:

1. takes the node's lock;
2. a node already stopped: the walk does not descend into it. Its subtree
   was walked when it stopped, and nothing starts under a fenced node;
3. a `merged` node: no row, so its settled state stays `merged`; the walk
   descends into its children, which may be live. The fence reaches it
   through the ancestor read;
4. any other node: writes `task.stopped` (`{"reason", "by"}` on the named
   node, plus `"by_stop_of": task_id` on a descendant) and sends
   `pg_notify(valor_stop, <node id>)`, so whatever process runs that node's
   turn hears its own id;
5. reads the node's children and continues.

The command line prints `stopped (and N descendants)` or `already stopped`.

Why a row on each unsettled node and an ancestor read: the row folds each
stopped node to `stopped` in its own status and carries the notification
that kills its turn; the ancestor read covers merged nodes and anything
the walk did not mark. A generation number each request carries would be a
new field each reader compares, for the same fence. The architecture's
design note for nested stop is brought to this.

Why each node's lock before reading its children: a child started under a
node commits while holding that node's lock, so once the stop holds it,
every child of that node is visible to the next read (read committed). A
start racing a stop is either refused or seen and stopped. Locks go parent
before child on every path; a start takes only its parent's lock and a
call or effect only its own node's, so no cycle of waits forms.

The walk locks only nodes it does not prune: live and merged nodes. A
routine objective stopped once locks its unstopped runs once, and a node
stopped earlier costs nothing.

A call already opened when the stop commits is still charged: a charge is
never refused, and the runner drains every call before `turn.ended`.

### The report (`core/tasks.py`, `core/session.py`)

`child_report(conn, child_id)` gives the child's id, instruction, state,
`tree_spent_usd_micros`, and its latest delivery (`summary` and
`outcome`, or none). The parent's reports list its direct children in
start order. A grandchild reaches the grandparent through its parent's own
delivery and through the subtree spending figure.

**In the Brief, kernel facts only.** `dispatch` appends a Children section
as the last section of any non-fresh Brief whose task has children,
workspace or not:

```
# Children

- <id> (<state>, metered spending $X.XXXXXX)
  > <each line of the instruction>
```

The instruction is Tom's or kernel code's, quoted line by line so a
multi-line one stays inside its bullet. Rendered from the ledger with
fixed order and format, so the same store gives byte-identical text; last,
because it changes most often. A fresh session gets no Children section.

**In the prompt, the child's words.** `session.next_prompt` appends, after
the entry prompt and the effects report, a section labelled
`# Reports from your children` listing each child that has delivered: its
id, its delivery outcome, and its summary quoted line by line. Every
prompt of the parent carries each child's latest delivery, so a report is
never lost to a turn that failed or was stopped, and no spent-marker is
kept.

### Offered effects narrowed to the ceiling (`core/broker.py`, `core/tasks.py`)

The architecture's tree rule says capabilities are attenuated on dispatch.
`broker.offered(ceiling=None)` lists only performers whose class ranks at
or below `ceiling` when one is given; `dispatch` passes the task's ceiling,
so a `read` child's channel text offers no `propose` or `act` effect. The
broker still refuses above the ceiling either way; this makes what a turn
is told match what it may do. It applies to every task: a `propose` root does
not see `push_branch` (`act`) listed, which it could never use.

### The command line (`core/__main__.py`)

- `start --parent ID`: starts a child through `start_child`. `--ceiling`
  defaults to absent: with `--parent` the child takes the parent's
  ceiling, without it the default stays `propose`. `KeyError`,
  `TaskStopped`, `CalibrationTask`, and `CeilingRefused` each exit with
  `start refused:` and the reason, on both the `--workspace` and the
  `--project` path; on the `--project` path the provisioned workspace is
  removed first, as for a failed insert.
- `stop`: calls `stop_tree` and prints the count.
- `status`: adds `tree_metered_spending`.

## Assumed of 2.1

2.1's plan file did not exist when this was written. This plan assumes:

- `tasks.stop` stays the one stop path, writing `task.stopped` and a
  notification on `valor_stop` whose payload is the task id; whatever
  process runs a turn (the resident kernel) listens for its own task's id.
  If 2.1 moves the listener, the walk still notifies each node's id.
- `tasks.dispatch` stays the one renderer of a turn's Brief and
  `session.next_prompt` of its prompt, and the supervisor's "same store
  in, byte-identical context out" covers the Children section and the
  reports by their fixed order and format.
- 2.1 changes `core/broker.py` (the idempotency key gains the effect id);
  this task changes only `offered` there, and rebases onto 2.1.
- A child's turn takes the one turn slot like any task's. Waking a parent
  when a child delivers is not in 2.1's event list and is left out here.

If 2.1 merges with a different stop, dispatch, or prompt path, the build
applies the same rules on that path and says so in its report.

## Tech debt absorbed

- `docs/architecture.md` and `core/README.md` name the spending fold
  `tasks.money`; the code's is `tasks.spending`. Both docs are fixed.
- `docs/architecture.md`'s Task and Brief, Metered spending, Stop, and
  Objective tree sections describe the tree as design; they are brought to
  what is built: the fence as a row per unsettled node plus the ancestor
  read, and capabilities narrowed on dispatch.
- `docs/data.md` says the tree's nodes are "the next kind" of document; a
  node is a task, so the line is corrected, and `task.started` and
  `task.stopped` list their new fields.
- `docs/tech-stack.md` marks the ceiling property as arriving with the
  tree; it becomes in use.
- `docs/routines.md` says the kernel has no objective tree; it is
  corrected, with the period report still 4.3's.

## Left out

- A turn starting a child (Decided by default: built by 4.3's failure
  triage).
- A parent waiting on its children as a state, and the supervisor waking a
  parent when a child delivers.
- A per-task deadline (the architecture's design note); the idle bound
  stands in.
- Spending over a period; 4.3 builds it on `tree_spending`'s rows.
- Moving a task to another parent, or any change to a Brief after start.
- A count or depth limit on the tree. The leaf criterion (one session) is
  the only bound the design names, and it is a judgement, not a kernel
  number.
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
  stopped ancestor is fenced, and has its own `task.stopped` unless it is
  merged; no `gateway.opened` or `effect.intent` on a node has an id above
  its nearest fencing stop row's; and each node's `tree_spent_usd_micros`
  equals its own spend plus its children's tree totals, and the sum of
  every charge in its subtree. Each example uses fresh task ids.

**The start.**

- A child with no ceiling given takes its parent's: a `read` parent gives
  a `read` child, not the root default `propose`.
- A child asking above its parent is refused: no document, no
  `task.started`, the message names both classes; one rank lower succeeds
  unchanged.
- An unknown parent, a calibration parent, a stopped parent, and a merged
  parent under a stopped ancestor are each refused with nothing written.
- A parent with no `sdlc` marker (a `task.started` shaped like one written
  before the state machine) takes a child, and stopping it stops the
  child.
- A stored Brief document without `parent_id` loads as a root; its status
  shows `parent_id: null`, `fenced_by: null`, no children, and tree totals
  equal to its own.
- `ancestors` on a grandchild gives child then root; on a root, empty.
- A child's `governance_grant` and `harness` are what its start gave,
  never the parent's.
- From the command line: `start --parent` with and without `--ceiling`;
  each refusal's `start refused:` text on the `--workspace` path and on
  the `--project` path, where the workspace is removed and no traceback
  printed.

**Stop.**

- Stopping a root writes `task.stopped` on the root, the child, and the
  grandchild in one transaction, the descendants' rows carrying
  `by_stop_of`; a listener on `valor_stop` hears all three ids; `stop_tree`
  returns 3 and `stop` True.
- Stopping a child stops its subtree and leaves its parent and siblings
  running.
- A merged child under the stopped root: no `task.stopped` row, status
  `merged` with `fenced_by` the root, Tom's feedback refused naming the
  root, and its own live child stopped by the walk.
- Pruning: stopping a root whose child was stopped earlier writes no row
  under that child and takes no lock there (the grandchild's lock stays
  free to another connection during the walk).
- The grandchild's call, effect request, and approved release after the
  root's stop are refused as in Done item 3.
- A grandchild's call opened before the stop and charged after it: the
  charge lands, the rollup counts it, and the next open is refused.
- The race: connection A starts a child under node Y and holds Y's lock
  before committing; connection B stops Y's parent X. B waits on Y's lock;
  when A commits, B's walk reads the new child and stops it. The reverse
  order refuses A's start. Never an unfenced child under a stopped
  ancestor.
- Stopping an already stopped root returns 0 (`stop`: False) and writes
  nothing.
- The live stop on a scripted grandchild turn, Done item 3.

**The report, the Brief, the channel.**

- The parent's next prompt carries `# Reports from your children` with
  each delivered child's summary quoted line by line, in start order; a
  child that has not delivered is absent; after a failed parent turn the
  next prompt carries it again.
- The parent's Brief carries the Children section with id, state, subtree
  spending, and the quoted instruction, and no summary text; a summary
  holding `# Brief` or an instruction to the parent appears in no Brief.
- Two renders over the same store are byte-identical; a fresh session's
  Brief has no Children section; a parent with no workspace gets one; a
  task with no children renders as before, byte for byte.
- A multi-line instruction stays inside its bullet.
- A grandchild's delivery is not in the root's prompt; its spending is in
  its parent's subtree figure in the root's Brief.
- A `read` task's channel text lists no `propose` or `act` performer; a
  `propose` task's lists no `act` one; an `act` task's lists all. Existing
  tests that assert the full list on a lower ceiling are updated to it.
- `status` on root, child, and grandchild: own spend, tree spend, open
  calls across the subtree with their task ids, including a
  `gateway.reserved` row on a child.

**Kept green.** Every existing test, the stop tests in `test_kernel.py`
and `test_reap.py` among them.

## Files it changes

- `core/tasks.py`: `Brief.parent_id`, `child_ceiling`, `CeilingRefused`,
  `start_child` and the child path in `start`, `children`, `subtree`,
  `ancestors`, `fenced_by` and `is_stopped`, `tree_spending`,
  `child_report`, `stop_tree` and `stop`, the Children section and the
  ceiling passed to `offered` in `dispatch`, the tree fields in `status`.
- `core/session.py`: the reports section in `next_prompt`, the fence read
  in `feedback`.
- `core/broker.py`: `offered(ceiling=None)`.
- `core/__main__.py`: `start --parent`, the `--ceiling` default, the
  refusals on both start paths, `stop` and `status` output.
- `tests/test_objective_tree.py` (new); existing tests asserting the full
  offered list on a lower ceiling.
- Docs, by the docs stage: `docs/architecture.md`, `docs/data.md`,
  `docs/tech-stack.md`, `docs/routines.md`, `core/README.md`.

It does not change `core/schema.sql`, `core/spending.py`,
`core/gateway.py`, or `core/runs.py`. Other tasks change `core/tasks.py`,
`core/session.py`, `core/broker.py`, and `core/__main__.py` (2.1 among
them), so this task merges after 2.1, one kernel merge at a time, rebased
onto it.

## Rollout

No schema change: no table, index, constraint, or grant is added, and no
row is rewritten. A task without `parent_id` reads as a root.

1. `python -m core backup` before the merge, as for every kernel merge.
2. Fast-forward and push the rebuild branch.
3. Pull the running kernel's checkout and restart the kernel service (the
   resident kernel 2.1 installs), so the new stop, dispatch, and prompt
   are the ones running. No `migrate`.
4. Rollout check on the kernel's own database: start a root with the
   instruction `ROLLOUT CHECK 4.1: not work, stopped at once` and a child
   whose instruction is `ROLLOUT CHECK 4.1 child: not work, stopped at
   once`, both `--ceiling read`, read their status, and stop the root.
   The two tasks stay in the append-only ledger under those labels.

## Decided by default

- **The fence is a `task.stopped` row on each unsettled node, written by
  one pruned walk in one transaction, plus an ancestor read in
  `is_stopped`**, not a generation each request carries. Every existing
  reader of the stop keeps its call site.
- **A merged node gets no stop row**, so its settled state is kept; the
  ancestor read refuses its reopening.
- **The walk prunes at a node already stopped**, sound because nothing
  starts under a fenced node.
- **A child with no ceiling given takes its parent's.** The root default
  `propose` would refuse every child of a `read` parent.
- **The ceiling refusal at a child's start is not new governance.** It is
  the broker's ceiling rule ("a task's ceiling bounds everything beneath
  it", architecture.md) applied where the tree first exists ("the kernel
  refuses a request it cannot meet in full rather than clipping it"),
  named in the milestone's Done, holding no work for review and asking
  Tom nothing. The refusals of a stopped or calibration parent are the
  stop fence. The blind verifier still answers its boolean over the diff.
- **The Brief carries kernel facts about children; their words go in the
  prompt.** Every prompt carries each child's latest delivery, so none is
  lost and no marker is kept.
- **Offered effects are narrowed to the ceiling for every task**, per the
  architecture's "attenuated on dispatch".
- **Any non-calibration task may be a parent**, and 4.1 builds
  `ancestors`, which 4.3 reads.
- **A turn-facing `start_child` is a `propose` performer**, per
  routines.md ("start a child task, inside its own ceiling"), built by the
  first task that needs it: 4.3's failure triage, where a failure starts a
  child to investigate. 4.3's plan does not build it either; whichever
  task first needs it writes its threat model (its workspace rule and
  where the child's instruction, now turn-written, is rendered).
- **Every refusal has a source or a function; nothing caps the tree.**
  The ceiling refusal: architecture.md, The objective tree, and
  routines.md ("The kernel refuses"). A stopped or fenced parent, and
  feedback on a fenced merged node: the stop fence (architecture.md, "Stopping
  a node fences its subtree"; the lead's call on finding 2). A calibration
  parent: stop cannot walk through one. An unknown parent: there is no
  Brief to read. No depth, count, or size limit, and nothing routes to
  Tom. Checked against Tom's standing rule on invented caps; nothing was
  dropped.
- **The Brief lists direct children only**, with subtree spending.
- **No index is added**: the children read uses the existing GIN index.
- **The rollup is over all time and counts stopped nodes**; periods are
  4.3's.

## Critique round 1 (of 2): revise

1. *A child's summary in the parent's Brief.* Removed. The Children
   section carries id, state, subtree spending, and the quoted instruction;
   summaries go in `session.next_prompt` under `# Reports from your
   children`. Done item 4 and the threat model say so.
2. *The walk relabels settled nodes and grows without bound.* The walk
   prunes at stopped nodes; merged nodes get no row and keep `merged`;
   `is_stopped` reads ancestors (`fenced_by`), and `session.feedback`
   refuses a merged node under a stopped ancestor. Tests: a merged child
   under a stopped root, and pruning.
3. *The question was not intent.* Dropped. `start_child` is recorded as a
   `propose` performer built by 4.3's failure triage.
4. *4.3's assumptions.* 4.1 builds `ancestors`; any non-calibration task
   may be a parent, tested with a no-`sdlc` parent.
5. *Ceilings not narrowed at dispatch.* `broker.offered(ceiling)` filters
   the channel text to the node's ceiling, for every task.
6. *Smaller premises.* `stop_tree` returns the count and `stop` keeps its
   bool; "the full suite at the merge base"; `Brief.load` wording fixed;
   the calibration refusal's reason stated; every start refusal caught on
   both paths with the ceiling resolved before the Brief; the rollout
   check's tasks labelled `ROLLOUT CHECK 4.1`.
7. *Gaps.* `governance_grant` and `harness` come only from Tom or
   `routine.toml`; a parent with no workspace gets the Children section;
   multi-line instructions are quoted.
