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
last section). Every citation is to `valor-cori-rebuild` at 1b9cde3ed.

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
  (`effect.refused` naming the terms) instead of an exception. Its intent
  carries `landed` (`outcomes.landed`), as it does now at release.
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

Removed: `approve`, `NotApproved`, `pending`, `held_task`, `approval_id` in
the intent row and in `release.requested`, and the module docstring's
approval paragraphs, rewritten to what the broker does.

`Outcome.kind` loses `pending`; a held row's standing answer (`_standing`,
`_prior`) is `released`.

### The merge predicate (`core/machine.py`)

Term 5 and the `approval_unused` argument go; four terms remain, each a
check of the work that exists now. `ensure_merge` drops `held` from its
skip list (no merge is held) and treats a refusal written by the migration
below (`at: migrate`) as no refusal, so a task whose merge was held when
this rolls out requests it again.

The fold (`_merge_effect`) registers a merge effect from its
`effect.intent` when no `effect.held` came first (the new path); an old
merge with both rows folds as it does now.

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
starts from change.

### Reports instead of cards (`core/notices.py`, `core/broker.py`)

`notices.owe` no longer requests an `effect` card; `_held` and
`effect_text` go. When an `act` effect's outcome is `done` (written by
`_perform` or `reconcile`, both through `_outcome`), the same transaction
requests one notice, kind `report`, `about_key` `report:<effect_id>`,
saying what left in kernel words: a merge (task, branch, head, the
delivery's summary line, each check's outcome: `delivered_text`), a push
(branch and commit), a send (channel, recipients or chat, and the text or
subject). A send whose target is the operator's own chat on the operator
channel gets no report: the message is what reached Tom.

The `delivered` notice is owed in `merge` only when the delivery will not
merge by itself: the delivery did not pass, a governance instance awaits a
grant, or the merge effect was refused or failed. A delivery that merges
is reported once, by the merge's report.

### Binding (`core/intake.py`)

Removed: the `approve` row of the binding table, `_approve`, the `approve`
near miss, and the "waiting on approval" notice. `stop` and its near miss
stay exactly. A reply to a merge's `report` notice on a task in `merged`
binds `feedback` (the fold already takes `feedback` in `merged`,
`core/machine.py:81`), the way a reply to the `delivered` notice in
`merge` does now; without it Tom's only reply to a merged delivery would
bind `none`. Any other reply to a merged or stopped task stays `none` with
a notice. A reply to any other report steers, like any reply to a notice.

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
(its `refuse`); merge a candidate the predicate's four terms reject; merge
a diff that adds governance without Tom's grant per instance; take a file
a send names from outside the workspace or with other bytes than its
digest (the declared checks and the bridges' sha256 reads). Each of these
is a check that runs now; this change moves the merge predicate from
release to request and removes nothing else. A turn that is talked into a
send by something it read can now send it; that is the authority the
ruling gives an `act` task, bounded by the ceiling, and no new guard is
added for it.

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
`at: migrate`, under the task's lock, idempotent through
`events_one_effect_row`. A merge refused this way is requested again by
`ensure_merge` with a fresh payload and the predicate checked; a send is
reported refused to the turn's next prompt. `migrate` also drops
`events_approval_used_once`, which guards a field no row carries. Rows
already written (`approval.granted`, old `release.requested`) stay; the
ledger is append-only. The rollout record lists how many rows the
migration refused.

## Done, as evidence

1. A test task at ceiling `act` requests a `push_branch`; the ledger shows
   `effect.intent` then `effect.outcome` `done`, no `effect.held`, no
   `approval.granted`, and one `report` notice.
2. A task in `merge` with a passing delivery is merged by one `core run`:
   intent with `landed`, outcome `done`, state `merged`, one `report`
   notice carrying the delivery, no `delivered` notice.
3. A `telegram.send_message`, `email.send`, and `local.send_message` each
   go from request to `effect.outcome` through their outbox with no tap.
4. A message-started task's Brief reads ceiling `act`; `core start` with no
   `--ceiling` reads `act`.
5. `python -m core approve`, `release`, `pending` are unknown commands;
   `grep` finds no `approve(`, `NotApproved`, `pending(` in `core/`,
   `bridges/`, `ui/`.
6. A governance-adding merge with no grant is refused at request with the
   same reason as now; after `core grant`, the next `run` merges it.
7. Migrating a test ledger holding an orphan held send and an orphan held
   merge writes one `effect.refused` each; migrating again writes none; the
   merge is then requested again and performed.
8. Every doc below reads in the status quo; the governance paragraph is
   byte-identical in each file holding it (the existing test that checks
   this passes).
9. Full suite green on the test database, ruff check and format clean.

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
  moved is refused).
- **Reports.** A done `act` writes one `report` notice; a failed or
  unknown one writes none; a reconciled `done` writes one; a send to the
  operator chat writes none; a `propose` effect writes none. A reply to a
  merge's report on a merged task binds `feedback` and sends the task to
  `patch`; a reply to a push's report steers.
- **Delivered notice.** Owed for a delivery that did not pass, one
  awaiting a grant, and a refused merge; not owed when the merge is done.
- **Migration.** As in Done item 7, plus: a held send with a bridge-owned
  `release.requested` is left for its outbox; a held effect already
  refused is untouched.
- **Fold.** A merge with only intent and outcome folds `merged`; an old
  ledger with held, approval, intent, outcome still folds `merged`; the
  readers of merges count each once.

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
`docs/routines.md` (effect-class table, held-in-pending lines, 244-253),
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
`core/fresh.py`, `core/bridge.py` (usage lines, docstrings, outbox
except clause), `core/db.py` and `core/schema.sql` (migration, index),
`tools/push_branch.py` (usage, docstring), `ui/app.py`,
`bridges/email/__init__.py`, `bridges/email/smtp.py` and
`bridges/telegram/send.py` (words "approved" become "requested"; the
sha256 checks stay), `bridges/local/__init__.py` (docstring),
`tests/emulator/replay.py`, the tests above, the docs above.

## Rollout on Valor's Mac

Merges before A2, A3, B1. The rollout runs `migrate` (the held-row
migration and the index drop) and restarts the kernel. The new Telegram
and email bridge jobs stay disabled until the live window; the rollout
record lists the migration's count.

Ports 6430-6439, test database `valor_rebuild_test_a1build`.

## Questions for Tom

1. Which effects get a report? Assumed: every `act` effect that left
   (merges, pushes, sends), except a message sent to Tom's own chat, which
   is its own report. If pushes to a task's branch are too many, the
   report could cover merges and sends only.

## Decided by default

- `effect.held` stays the row type for a declared send, now always paired
  with `release.requested`: renaming it would change every bridge and
  reader of a send's action for no behavior.
- The `effect_refused` notice goes: the ruling sends Tom reports of what
  was done and blockers; a refused send reaches the turn's next prompt and
  the task's ledger.
- `core start` defaults to `act`, reading "any channel" to include the
  command line.
- Orphan held effects are refused rather than performed: a held send may
  be days old, and a held merge is requested again fresh, with the
  predicate checked on the current head.
- Feedback by reply to a merge's report: the only way Tom answers a merged
  delivery once the merge no longer waits in `merge`.
