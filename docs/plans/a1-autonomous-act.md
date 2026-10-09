---
tracking: none
slug: a1-autonomous-act
type: plan
status: planned
critique_rounds: 2
review_rounds: 2
---

# Valor acts on its own

Task A1 of `docs/plans/rebuild-finish-prompt.md` ("Track A"), under Tom's
ruling on autonomy of 2026-10-09 (`docs/plans/valor-rebuild-feedback.md`,
last section). Every citation is to the rebuild branch at 1b9cde3ed.

**Goal.** An `act` effect inside its task's ceiling leaves when the kernel
decides: the broker writes its intent and performs it, or, for a bridge's
declared type, asks its bridge to release it, in the same transaction that
checks it. Nothing waits for a tap. A task Tom starts, by message or by
command, runs at ceiling `act`. What reaches Tom about effects is a report
of what left. The approval path this orphans is removed. The ceiling, the
broker's refusals, the merge predicate's checks of the work, the
governance grant and its refusal, and stop are the authority, as they are
now.

**Serves.** Mission item 1 ("delivering within authority ... Tom never
coordinates the gaps between those steps") and item 6 ("Requiring Tom to
adjudicate internal process is a product defect"). It unblocks the
"Before cutover" item "A real task, Telegram message to a merged change Tom
used", which the prompt names blocked by `core/intake.py` (ceiling
`propose`) and `core/broker.py` `_request` (every `act` held).

**Governance.** This removes an approval step and adds none: no check,
gate, hook, validator, review round, or guard. Every refusal that stays
exists today. It needs no `governance_grant` (the ruling: "Removing an
approval step is not adding one and needs no grant"). The review's
governance boolean must answer no on every hunk; a yes is an incident
against the classifier, not a grant request.

## What exists now

- `broker._request` (`core/broker.py:236-281`): refuses with no performer,
  a stopped task, a class above the ceiling, ungranted governance, or a
  performer's `refuse`; then `effect.held` for every `act` and every
  declared type (line 268), `effect.intent` and a perform for the rest.
- `broker.approve` (303) writes `approval.granted`; `broker.release`
  (334, 481) checks stop, the performer's `refuse`, the merge predicate
  (merges only), and an unused approval, then writes `release.requested`
  for a declared type or the intent and performs. `_refused_release` (353)
  appends `effect.refused` and an `effect_refused` notice once Tom's
  approval asked for the release. `pending` (565) lists held effects.
  `held_task` (398) finds a held effect's task.
- `machine.merge_predicate` (`core/machine.py:671`) has five terms; term 5
  is "an unused approval from Tom bound to this merge's digest".
- `verdicts.ensure_merge` (`core/verdicts.py:525`) requests the merge and
  skips while the merge effect is `held`, `in_flight`, or `done`, or was
  refused for the same payload with no grant since.
- `intake.bind` (`core/intake.py:388`, 431-464): a reply `approve` to an
  `effect` notice writes `approval.granted` and `release.requested` (owner
  the bridge, or `kernel`); a near miss of `approve` owes a notice; a steer
  on a task with held effects owes "waiting on approval". A message starts
  a task with the `Brief` default ceiling `propose` (491).
- `python -m core start` defaults to `propose` without a parent
  (`core/__main__.py:403`); `pending`, `approve`, `release` commands
  (780-812, 1066-1073); `status` prints "held for Tom ... approve with ...
  then release" (294-300).
- `serve.Kernel._kernel_release` and `_release` (`core/serve.py:668-712`)
  perform `release.requested` rows owned by `kernel`.
- `notices.owe` (`core/notices.py:98`) requests an `effect` card per held
  effect (`_held`, `effect_text`: "Reply approve to send it").
- The outbox (`core/bridge.py:425-478`) yields `release.requested` rows for
  its channel and calls `broker.release`; the email bridge reads
  `effect.held` for the task of an effect (`bridges/email/__init__.py:151`,
  218); the local page shows sends by joining `effect.held`
  (`bridges/local/__init__.py:51`).
- Readers of a merge's action from its `effect.held` row: the fold
  (`machine._merge_effect`, 566), `outcomes.merges` and `done_merges`
  (`core/outcomes.py:50`, 146), `audit_sample._merge_candidates` and
  `_merged` (`core/audit_sample.py:136`, 222), `fresh._effects`
  (`core/fresh.py:610`). `serve.MERGE_OUTCOMES` (355) and
  `broker.dangling` already read the intent first.
- `tasks._digest` (`core/tasks.py:736`): `effect.held` is state `pending`;
  `approval.granted` is attention kind `approval`. `session._effects_report`
  (`core/session.py:780`) says "held for Tom's approval".
- `ui/app.py` `/pending` page over `broker.pending`.
- The replay driver (`tests/emulator/replay.py:138-170`) approves and
  releases held pushes through `core pending/approve/release`; a held merge
  is its outcome `held`.
- Declared usage lines ("sent once Tom approves", `core/bridge.py:250-285`)
  and `push_branch`'s ("once Tom approves", `tools/push_branch.py:44`) are
  what a turn reads.

## Design

### The broker (`core/broker.py`)

`_request` keeps every refusal it has, in the same order, under the same
task lock. After them:

- A kernel performer's effect, `act` or not, gets its intent and is
  performed, as `propose` is now.
- A `merge` additionally evaluates the merge predicate in that
  transaction, with the git facts read from the mirror or workspace
  (moved from `_release`, unchanged), and a failing term is a refusal
  (`effect.refused` naming the terms) instead of an exception. When the
  facts cannot be read (`_git_facts` returns None on a `GitError`), the
  request writes no row and returns `unknown`, so the next step asks
  again: only a term that reads a fact about the work refuses. Its intent
  carries `landed` (`outcomes.landed`), as it does now at release.
  `MergeRefused` goes: nothing raises it.
- A declared type gets `effect.held` (the row carrying the action, held for
  its bridge) and `release.requested` (`effect_id`, `owner`) in the same
  transaction, and returns `released`. `effect.held` stays the row type so
  the bridges, the local page, and every reader of a send's action are
  unchanged; it now always has its `release.requested`.

`release(conn, performers, effect_id)` stays as the bridge's entry only:
it reads the held row, refuses a stopped task (`TaskStopped`), no
performer, or the performer's `refuse` (all three exist now), writes the
intent, and performs. The approval lookup, `NotApproved`, and the merge
branch go. Every refusal there is final: one `effect.refused` with
`at: release`, so the outbox never yields it again. The `effect_refused`
notice goes (decided by default below).

Removed: `approve`, `NotApproved`, `MergeRefused`, `pending`, `held_task`,
`approval_id` in
the intent row and in `release.requested`, and the module docstring's
approval paragraphs, rewritten to what the broker does.

`Outcome.kind` loses `pending`; a held row's standing answer (`_standing`,
`_prior`) is `released`.

### The merge predicate (`core/machine.py`)

Term 5 and the `approval_unused` argument go; four terms remain, each a
check of the work that exists now.

`ensure_merge` (`core/verdicts.py:525`):

- skips `in_flight`, `done`, and `failed` (today a `failed` merge is asked
  again only through a fresh tap; without the tap it would push again on
  every later step of the task), and drops `held` (no merge is held);
- passes `request_id = f"merge:{payload_sha256}:{len(f.granted)}"`, so
  `broker._prior`, under the task lock, answers a second request for the
  same payload and grants with the standing answer (in flight, done,
  failed, refused). Two steps racing (the kernel's `_once` and `core
  check`) make one intent. A new candidate or docs head is a new digest,
  a new grant a new count: each makes a fresh request, as now;
- treats a refusal written by the migration below (`at: migrate`) as no
  refusal, so a task whose merge was held when this rolls out requests it
  again with a fresh `request_id`.

The fold (`_merge_effect`) registers a merge effect from its
`effect.intent` when no `effect.held` came first (the new path); an old
merge with both rows folds as it does now. Its `merge_effect` carries the
refusal's `at`, which `ensure_merge` reads.

`router._once` (`core/router.py:361`) returns `moved` with the task's
state when the merge it just asked for landed (the task is `merged`), and
`delivered` otherwise, so `core run` and the replay driver read `merged`
in the same step.

### Readers of a merge's action

`outcomes.merges`, `outcomes.done_merges`, `audit_sample._merge_candidates`,
`audit_sample._merged`, and `fresh._effects` read a merge's action from its
`effect.intent`, falling back to the `effect.held` row for an intent that
lacks the action fields, the pattern `serve.MERGE_OUTCOMES` and
`broker.dangling` use now. An old merge has both rows and is counted once
(by effect id).

### Ceilings (`core/intake.py`, `core/__main__.py`)

A message-started task's Brief carries `max_effect_class="act"`.
`python -m core start` without `--parent` defaults to `act` ("A task Tom
starts from any channel runs at act"); `--ceiling` still asks for less, a
child's ceiling is still its parent's or lower (`tasks.child_ceiling`), a
routine's is its `routine.toml`'s, a calibration task's `read`. The
`Brief` dataclass default stays `propose`: only the two entry points Tom
starts from change. A message-started task already open at `propose`
keeps it: a Brief is written once (`core/tasks.py:55`) and a task's
ceiling never changes. Its act requests stay refused for ceiling; Tom
starts it again or it ends. The rollout record lists those tasks.

### Replay tasks build kernel performers only (`core/__main__.py`)

`_performers(b)` gives a `Brief.replay` task the kernel performers only
(push to its own bare origin, merge to the Brief's origin) and no
declared send. A replay's send then meets the existing `no performer`
refusal. Today a replay is kept from real chats only by the tap; without
it, a replayed request such as "email X about Y" would reach a real
person from an emulator run. This is not a guard: per Tom's 2026-10-07
ruling it makes the emulator's own wiring correct (an emulator never had
a reason to reach real chats); it judges no work and adds no step. It
chooses which performers the task is built with, as the Brief already
chooses its workspace. The emulator routine's sweep task runs no turn
(it runs the driver), and every replay it starts is a `Brief.replay`
child.

### Reports instead of cards (`core/notices.py`, `core/broker.py`)

`notices.owe` no longer requests an `effect` card; `_held` and
`effect_text` go. When an `act` effect's outcome is `done` (written by
`_perform` or `reconcile`, both through `_outcome`), the same transaction
requests one notice, kind `report`, `about_key` `report:<effect_id>`,
to the operator channel and chat (`notices.request`'s defaults), saying
what left in kernel words, for a merge (task, branch, head, the
delivery's summary line, each check's outcome: `delivered_text`) and for a
send (channel, recipients or chat, and the text or subject).

Who gets no report:

- A push: it is a step inside the task.
- A send that itself reached Tom: `telegram.send_message` to the operator
  chat, `email.send` whose every recipient is one of Tom's addresses
  (`settings.operator_email`), and every `local.send_message` (it always
  lands on Tom's page, which shows it, `bridges/local/__init__.py:41-53`).
- A replay task (`Brief.replay`): its merge goes to its own bare origin
  and no person used it; the replay driver reads the outcome.
- A child task (`Brief.parent_id` set): its act reports to its parent, not
  Tom. The parent's prompt already carries each child's latest delivery
  (`session._children_report`, `core/session.py:750`); the root's merge
  is what Tom hears of.

The `delivered` notice is owed in `merge` only when the delivery will not
merge by itself: the delivery did not pass, a governance instance awaits a
grant, or the merge effect was refused or failed. A delivery that merges
is reported once, by the merge's report.

### Binding (`core/intake.py`)

Removed: the `approve` row of the binding table, `_approve`, the `approve`
near miss, and the "waiting on approval" notice, with the docstring lines
that describe them (`core/intake.py` 6, 21-44, 97). `stop` and its near
miss stay exactly as behavior; the email near-miss text (412) reads
"Stops come by reply ...", since there is no approval to name.

A reply to a merge's `report` notice on a task in `merged` binds
`feedback` (the fold already takes `feedback` in `merged`,
`core/machine.py:81`), the way a reply to the `delivered` notice in
`merge` does now; without it Tom's only reply to a merged delivery would
bind `none`. The branch sits before the early return for merged and
stopped tasks (`core/intake.py:364`), which today binds every such reply
`none` before the notice is read. Any other reply to a merged or stopped
task stays `none` with a notice. A reply to a send's report on a running
task steers, like any reply to a notice.

The steer notice in `merge` ("reply to the delivered notice to give
feedback", 419-425) is owed only while a `delivered` notice stands for
the task, since a passing delivery no longer gets one.

### The kernel loop (`core/serve.py`)

`_kernel_release` and `_release` go: no row asks the kernel to release.

### Surfaces

- `python -m core`: `pending`, `approve`, `release` go, with their help
  lines; `status` drops its held-effect lines.
- `tasks._digest`: `effect.held` is state `released` (with its bridge).
  Attention kinds keep `approval` so a ledger's existing `approval.granted`
  rows still list; nothing writes one.
- `session._effects_report`: `released` reads "released to its bridge".
- `ui/app.py`: the `/pending` page and its link go.
- Declared usage lines and `push_branch`'s say the effect leaves when
  requested, not "once Tom approves". `skills/sdlc/channel.md` says the
  same.
- The replay driver: `release_pushes` goes (pushes leave by themselves).
  A task that reaches `merged` goes to the stand-in, whose accept or spent
  feedback rounds end the run `merged`; its feedback runs the task on.
  Outcomes `held` and "an effect other than a local push is held" go. Its
  tasks merge to their own bare origin; a remote merge still needs a
  granted merge-target pair (`core.targets`), as now.

## Threat model

The turn controls each request's action type, target, and payload, its
`request_id`, and its workspace (files, git config). It does not control an
effect's class (the performer's), the ceiling (the Brief's), whether a
merge adds governance (computed from the verdicts), or any kernel row.
With the tap gone, the kernel must never: perform an effect above the
task's ceiling; perform for a stopped task or after a stop lands between
request and bridge release; let a turn request a `merge` (session refuses
it, `core/session.py:447`); push the target branch through `push_branch`
(its `refuse`); merge a candidate the predicate's four terms reject, or one
whose facts it could not read; merge
a diff that adds governance without Tom's grant per instance; take a file
a send names from outside the workspace or with other bytes than its
digest (the declared checks and the bridges' sha256 reads). Each of these
is a check that runs now; this change moves the merge predicate from
release to request and removes nothing else. A turn that is talked into a
send by something it read can now send it; that is the authority the
ruling gives an `act` task, bounded by the ceiling, and no new guard is
added for it. A replay task cannot send at all (it has no send performer).
The kernel's own merges, tasks that change Valor itself, now land with no
tap and roll out through the existing `rollout.py` path, which stops at
schema and dependencies as now; nothing is added there.

## What stays exactly

- The governance paragraph, everywhere, byte for byte.
- `python -m core grant`, `guard.granted`, `_governance`, and the refusal
  "adds governance with no grant from Tom for instance ...", at request.
- Stop: `tasks.stop`, the `valor_stop` channel, `is_stopped` checks at
  request and at release, the email bridge's cancel on stop, the reply
  `stop`.
- Reconcile, dangling intents, the per-effect lock, intent before perform.

## Migration of held rows

The ledger may hold effects waiting on the removed path: an `effect.held`
with no intent, outcome, or refusal, and no `release.requested` owned by a
bridge (bridge-owned ones are released by the outbox as now, approval or
not). `db.migrate` appends, for each, one `effect.refused` with reason
"held for an approval the kernel no longer takes; request it again" and
`at: migrate`, under the task's lock. The selector excludes any effect
that already has an intent, outcome, or refusal, so running it again
selects nothing; `events_one_effect_row` stays the backstop it is. A merge refused this way is requested again by
`ensure_merge` with a fresh payload and the predicate checked; a send is
reported refused to the turn's next prompt. `migrate` also drops
`events_approval_used_once`, which guards a field no row carries. Rows
already written (`approval.granted`, old `release.requested`) stay; the
ledger is append-only. The rollout record lists how many rows the
migration refused and, for each merge, the outcome of its fresh
request. The real ledger holds exactly one such row (the lead read it):
effect f10a2751760c, a merge to `main` of task 75c0902b6e25 to
`/Users/valorengels/valor-tasks/75c0902b6e25/origin.git`, a local test
origin.

## Done, as evidence

1. A test task at ceiling `act` requests a `push_branch`; the ledger shows
   `effect.intent` then `effect.outcome` `done`, no `effect.held`, no
   `approval.granted`, and no `report` notice.
2. A task in `merge` with a passing delivery is merged by one `core run`:
   intent with `landed`, outcome `done`, state `merged`, one `report`
   notice carrying the delivery, no `delivered` notice.
3. A `telegram.send_message`, `email.send`, and `local.send_message` each
   go from request to `effect.outcome` through their outbox with no tap.
   `core run` on the task of Done 2 prints status `moved`, state
   `merged`.
4. A message-started task's Brief reads ceiling `act`; `core start` with no
   `--ceiling` reads `act`.
5. `python -m core approve`, `release`, `pending` are unknown commands;
   `grep -rn 'broker\.pending\|\bpending(\|broker\.approve\|\bapprove(\|NotApproved\|MergeRefused' core bridges ui tests`
   finds nothing (the pattern does not match `tasks.spending(`).
6. A governance-adding merge with no grant is refused at request with the
   same reason as now; after `core grant`, the next `run` merges it.
7. Migrating a test ledger holding an orphan held send and an orphan held
   merge writes one `effect.refused` each; migrating again writes none; the
   merge is then requested again and performed.
8. Every doc below reads in the status quo, and a new test,
   `tests/test_governance_paragraph.py`, compares the governance paragraph
   in `CLAUDE.md` byte for byte against every tracked file that holds its
   opening words (19 files today, all identical) and passes. It asserts a
   documented invariant, so it is a test, not governance.
9. A replay task's `telegram.send_message` is refused `no performer`, and
   a replay run whose item merges ends `merged` with no report notice.
10. A failed merge is not requested again for the same payload, and two
    concurrent `ensure_merge` calls write one intent.
11. Full suite green on the test database, ruff check and format clean.

## Tests

Changed in place: every test that calls `broker.approve` / `release` /
`pending` / `held_task` or the CLI commands (`grep` lists them: test_broker,
test_session, test_pipeline, test_kernel, test_serve, test_bridge,
test_intake, test_email_kernel, test_local_bridge, test_local_edges,
test_local_page, test_telegram_pipeline, telegram_port, test_attention,
test_credential_push, test_objective_tree, test_fresh, test_audit_sample,
test_expiry, test_live_turn, test_live_session, `tests/scripted.py`) now
asserts the effect leaves on request. The merge predicate tests drive the
same predicate terms through `request` instead of `release`. Tests of the
`approve` binding and its near miss are deleted with the feature; `stop`
binding tests stay as they are.

New or sharpened, the non-obvious cases:

- **Above the ceiling.** A `propose` task requesting `push_branch` and a
  child of a `propose` parent requesting a send: `effect.refused`, "act is
  above the task's ceiling propose", no intent, no performer call, no
  report.
- **Stopped.** A stopped task's `act` request is refused "task stopped".
  A declared send requested, then the task stopped before its bridge
  releases it: `release` refuses with `TaskStopped`, one `effect.refused`
  `at: release`, the outbox does not yield it again, no perform.
- **Bridge-type release.** A declared request writes `effect.held` and
  `release.requested` in one transaction (a kill between is impossible:
  one commit), returns `released`; the outbox yields it once; a
  performer's `refuse` at release (a file changed to a link after
  request) is final.
- **Message-started ceiling.** `intake.bind` of an operator message starts
  a task whose Brief has `max_effect_class == "act"`; a non-operator
  message still binds `none`; `core start --parent P` still takes the
  parent's ceiling.
- **Governance still refused.** A merge whose review named an instance:
  refused at request with no grant, no intent; `ensure_merge` does not
  pile a second refusal; `core grant` then `run` merges; a Brief
  `governance_grant` alone does not stand in.
- **Predicate at request.** Each of the four terms failing refuses the
  merge with that term named and writes no intent; term 4's git facts are
  read in the same transaction that writes the intent (a docs head that
  moved is refused). A mirror whose git read fails writes no row, returns
  `unknown`, and the next `ensure_merge` asks again.
- **Merge requested once.** A `failed` merge is not requested again for
  the same payload; a new candidate head is. Two concurrent
  `ensure_merge` calls on one task make one intent and one push (`_prior`
  under the task lock). A grant after a refusal makes a fresh request.
- **Replay performers.** A replay task's `telegram.send_message`,
  `email.send`, `local.send_message` are refused `no performer`; its push
  and merge to its bare origin are performed.
- **Reports.** A done merge or send writes one `report` notice; a failed
  or unknown one writes none; a reconciled `done` writes one; a done push
  writes none; a Telegram send to the operator chat, an email only to
  Tom's addresses, and any local send write none; a replay task's merge
  and a child task's merge write none; a `propose` effect writes none. A
  reply to a merge's report on a merged task binds `feedback` and sends
  the task to `patch` (the branch runs before the merged early return); a
  reply to a send's report on a running task steers; any other reply to a
  merged task binds `none`.
- **Steer notice.** A steer on a task in `merge` owes "reply to the
  delivered notice" only while a delivered notice stands.
- **Delivered notice.** Owed for a delivery that did not pass, one
  awaiting a grant, and a refused merge; not owed when the merge is done.
- **Migration.** As in Done item 7, plus: a held send with a bridge-owned
  `release.requested` is left for its outbox; a held effect already
  refused is not selected; the fold's `merge_effect` carries `at:
  migrate`, and `ensure_merge` requests that merge again.
- **Existing ceiling.** A message-started task whose Brief says `propose`
  still has its push refused for ceiling after migrate.
- **Fold.** A merge with only intent and outcome folds `merged`; an old
  ledger with held, approval, intent, outcome still folds `merged`; the
  readers of merges count each once.
- **Emulator.** `tests/emulator/stand_in.py:123` and
  `tests/emulator/common.py:301` read a merged task (state `merged` or
  merge effect `done`) instead of `merge_effect.state == "held"`; the
  stand-in reviews a merged delivery and its feedback runs the task on.

## Docs to update

Each in the status quo, plain language, no dashes of either kind:
`docs/mission.md` ("How metered spending is read" last sentence, the
Bounded authority constraint's practice, the attention-log table's
`Approval` row and "How it is read" axis 1, which says the log keeps
counting questions and feedback and has no authority taps left to count),
`docs/persona.md` (effect table, line 307), `docs/architecture.md`
("Approvals: approve, then release" becomes how an act leaves; lines 489-
512), `docs/sdlc-state-machine.md` (the `merge` state, four predicate
terms, lines 60, 102, 494-573), `docs/data.md` (row table: `effect.held`,
`approval.granted` read only, `release.requested`, `message.bound` `as`
values, the dropped index), `docs/judgement-layer.md` (line 50),
`docs/routines.md` (effect-class table, held-in-pending lines, 244-253,
and 238-240: a keep the lead asked for must land before the sweep's
delivery passes, since a passing sweep merges itself),
`docs/machine.md` (47-48, 259), `docs/harnesses.md` (13),
`README.md` (68, 94, outside the governance paragraph),
`tools/README.md` (15, 34), `routines/README.md` (22),
`docs/tech-stack.md` (command list, approval surface, phone approvals,
pending page), `docs/emulator.md` (outcomes, the driver's pushes),
`docs/spending-and-attention.md`, `docs/bridges/telegram.md`,
`docs/bridges/email.md`, `docs/bridges/local.md`, `core/README.md`,
`bridges/README.md`, `api/README.md`, `ui/README.md` (their prose outside
the governance paragraph), `skills/sdlc/channel.md`,
`skills/sdlc/review.md` (effects input), `docs/plans/valor-rebuild.md`
"Execution", and `.claude/skills/build/SKILL.md`. The governance
paragraph, including its phrase "the same approval surface as a merge or a
send", is not touched anywhere.

## Files it changes

`core/broker.py`, `core/machine.py`, `core/verdicts.py`, `core/intake.py`,
`core/notices.py`, `core/serve.py`, `core/__main__.py`, `core/tasks.py`,
`core/session.py`, `core/outcomes.py`, `core/audit_sample.py`,
`core/fresh.py`, `core/router.py` (status after a merge),
`core/git.py` (37, "Tom's approval binds"), `core/bridge.py` (usage
lines, docstrings, outbox except clause), `core/db.py` and
`core/schema.sql` (migration, index),
`tools/push_branch.py` (usage, docstring), `ui/app.py`,
`bridges/email/__init__.py`, `bridges/email/smtp.py` and
`bridges/telegram/send.py` (words "approved" become "requested"; the
sha256 checks stay), `bridges/local/__init__.py` (docstring),
`tests/emulator/replay.py`, `tests/emulator/stand_in.py`,
`tests/emulator/common.py`, the new `tests/test_governance_paragraph.py`,
the tests above, the docs above. `core/session.py` covers 29 (approval
covering reply-all recipients) as well as `_effects_report`.

## Rollout on Valor's Mac

Merges before A2, A3, B1. The rollout runs `migrate` (the held-row
migration and the index drop) and restarts the kernel. The new Telegram
and email bridge jobs stay disabled until the live window. The rollout
record lists the migration's count, the outcome of the fresh merge
request for f10a2751760c, and the message-started tasks still open at
`propose`. From this rollout on, the kernel's own merges (tasks that
change Valor) land with no tap and roll out through the existing
`rollout.py` path, which stops at schema and dependencies as now; nothing
is added to it.

Ports 6430-6439, test database `valor_rebuild_test_a1build`.

## Questions for Tom

None.

## Decided by default

- Reports go for a merge and for a send to anyone other than Tom's own
  chat, not for a push: the ruling is "a report on what was done", and a
  push per patch round is a step inside the task, noise to Tom. A
  replay's or a child's act reports to no one but its driver or parent.
- A replay task is built without send performers (see its section): the
  emulator's wiring, not a check.
- A message-started task already open at `propose` keeps its ceiling.
- `effect.held` stays the row type for a declared send, now always paired
  with `release.requested`: renaming it would change every bridge and
  reader of a send's action for no behavior.
- The `effect_refused` notice goes: the ruling sends Tom reports of what
  was done and blockers; a refused send reaches the turn's next prompt and
  the task's ledger.
- `core start` defaults to `act`, reading "any channel" to include the
  command line.
- Orphan held effects are refused rather than performed: performing an
  old hold would send stale work; the refusal reaches the task's next
  turn, and a held merge is requested again fresh, with the predicate
  checked on the current head.
- Feedback by reply to a merge's report: it feeds the feedback stream
  (Mission evidence "Tom's feedback, both directions"), and it is the only
  way Tom answers a merged delivery once the merge no longer waits in
  `merge`.

## Critique record

Round 1 (`~/src/valor-build-notes/critic-a1-r1.md`), verdict revise.
Answered: replay tasks build kernel performers only (B1); `ensure_merge`
skips `failed` and passes a payload-and-grants `request_id` so `_prior`
answers under the task lock (B2); an unreadable git fact writes no row
and returns `unknown` (B3); Done 8 is a byte comparison test (B4); the
Done 5 pattern, the missed files and docs, `MergeRefused`, the
report-feedback branch's place, the steer notice, `core run`'s status,
report recipients, the fold's `at`, the migration selector, existing
`propose` tasks, and the kernel's own merges (should-fix 1-10).
