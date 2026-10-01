---
tracking: none
slug: m1-2-state-machine
type: build
status: planned
critique_rounds: 2
review_rounds: 2
---

# 1.2 The SDLC state machine

Milestone 1.2 of [valor-rebuild.md](valor-rebuild.md). Goal: Tom never
coordinates the gaps between steps, and nothing merges on a model's say-so
(Mission item 1, **Bounded authority and spend**). The contract is
[sdlc-state-machine.md](../sdlc-state-machine.md); where this plan and that
doc differ, this plan fixes the doc in the same build.

Stakes: this replaces the kernel's state fold, adds constraints to the
ledger's schema, and moves the merge decision into the broker, all in the
kernel itself, so `critique_rounds: 2`, `review_rounds: 2`.

## What is true today (checked, not assumed)

- `tasks.status` folds four states (`live`, `waiting for Tom`,
  `delivered`, `stopped`). `session.run` loops turns until one of them
  changes. `session.record` writes `task.delivered` the moment a turn
  leaves `done.md`.
- `broker.Action.adds_governance` is a constructor argument. Nothing in
  `core/`, `tools/`, or `scripts/` ever sets it; `signals.collect` never
  reads it from an effect file. Only tests set it.
- `signals.PROTOCOL` names `push_branch` by hand. `signals.CLARIFY` tells
  the turn to write `question.md` "if you have no such questions", which
  makes a clarify turn with nothing to ask indistinguishable from one that
  asked.
- `session.next_prompt` holds the feedback framing ("Tom reviewed your
  delivery and, as project manager, ...") and the "Tom answered your
  question" framing.
- `tools/push_branch.py` holds the only pinned git runner (hooks, fsmonitor,
  credential helper, and SSH command disabled). The kernel will need the
  same pinning to read a candidate's head and a plan file from a workspace
  the turn can write.
- `Brief` is rebuilt from the task document with `Brief(**body)`, so any
  field a legacy document lacks needs a default.
- The real ledger `valor_rebuild` (read-only check, 2026-10-01): 1,416 rows,
  22 tasks, 18 event types. 21 `task.delivered`, 8 `question.asked` and 8
  answered, 5 `feedback.given`, 2 `task.stopped`, 19 held effects (17
  `push_branch`, 2 `outbox_send`). No turn id repeats on `turn.started`,
  `turn.ended`, or `turn.collected`. Four tasks have no workspace; six
  have no `mode`.
- Hypothesis is not installed in the kernel checkout's venv, and
  `pyproject.toml`'s dev group holds only pytest.
- `scripts/replay.py` and `scripts/role_play_tom.py` read `status["state"]`
  for `"waiting for Tom"`, `"delivered"`, and `"stopped"`, and release only
  held effects whose action type is `push_branch`.

## The boundary with 1.3 and 1.4

1.2 builds everything deterministic: the typed model, a total fold, verdict
rows refused at write when outside their enum, transitions, loops, the
join, the merge predicate enforced by the broker, stage files, and a router
that runs the runner registered for the current state.

| State | Runner in 1.2 | Arrives |
|---|---|---|
| `judge` | none; the start command's choice is recorded as a manual verdict | the judgement port, 1.3 |
| `clarify`, `plan`, `build`, `patch` | the working session (Claude Code, `.valor/` signals) | built here |
| `critique` | none | fresh session, 1.4 |
| `checks.test`, `checks.review`, `checks.docs` | none | 1.4 |
| `waiting`, `merge`, `merged`, `stopped` | nothing runs; the router returns | |

**The seam.** `core/router.py` takes a mapping from `State` or `Check` to a
runner, passed in by the composition root (`core/__main__.py`), never a
module global. A runner is an async callable that runs the work for one
state or branch and records its verdict row. When the current state (or a
branch of `checks` still without a verdict) has no runner, the router
returns `no runner`, names what is missing, writes nothing, and prints the
command that records the verdict by hand. 1.3 and 1.4 add entries to the
mapping and change nothing in the router.

**Manual verdicts.** `python -m core verdict TASK STAGE VERDICT` records a
verdict for a stage whose runner does not exist yet (`judge`, `critique`,
`test`, `review`, `docs`), with `leg: "manual"` and provenance (`by`, `via`,
`at`, `role_played`), refused unless the task is in that state. It is how a
real task crosses `critique` and `checks` before 1.4, and it is never
called by the router or any script. A manual verdict is attention Tom
spent, so it is an attention entry of its own kind. This is the bridge for
the judge as well: `start --mode bare|clarify` records the verdict
`precise` or `thin` with `leg: "manual"` and the starter's provenance in
the same transaction as `task.started`. Without `--mode`, the task waits in
`judge` and `run` says so. 1.3 deletes `--mode` and registers the judge
runner; the row type stays.

Why manual rows rather than skipping states: the predicate reads rows, so a
merge before 1.4 needs real `test.decided`, `review.decided`, and
`docs.decided` rows, and a row Tom (or the driving session, marked
role-played) wrote is honest about who decided. Skipping would be the
router faking a verdict.

## What will be built, per Done item

### 1. The typed model and the fold: `core/machine.py` (new)

Pure code, no I/O, importing only the standard library.

- `State` and `Check` as in the doc. `VERDICTS` as in the doc, with one
  change: every working-session state (`clarify`, `plan`, `build`,
  `patch`) also has `idle` and `failed`, because any turn can end with
  neither signal or fail, and `turn.collected` always carries a verdict.
  `waiting` has `answered`; `merge` has `released`, `feedback`,
  `governance_granted`; `merged` has `feedback`.
- `TRANSITIONS` exactly as the doc's table. `asked` from `clarify`,
  `plan`, `build`, `patch` goes to `waiting`; `answered` returns to the
  state named on the `question.asked` row. `idle` and `failed` stay put.
  Any state goes to `stopped` on `task.stopped`; nothing leaves it.
- `Candidate(sha, turn_id)`, `Finding(source, kind, text)`,
  `CheckVerdict`, `Checks`, `Loops(critique_rounds, review_rounds)`.
- `Fold`, the result of `fold(rows)`: `state`, `legacy`, `return_to` (in
  `waiting`), `plan` (the current `plan.written`), `loops` (the plan's
  counts, raised by any critique, never lowered), `candidate`, `checks`
  (verdicts keyed to the current candidate only), `counts` since the
  window opened (critique revisions, review send-backs, repair round
  spent), `entry` (the row that moved the task into its current state,
  which the next turn's prompt is built from), `delivery` (the latest
  `task.delivered`), `governance` (instances and which are granted), and
  `ignored` (rows that were not a transition from the state they arrived
  in, with why).
- `fold` is total: it reads rows in id order, applies a row only when it is
  a transition from the current state, and ignores (and lists) everything
  else, including rows missing fields and rows of unknown types. It never
  raises.
- **Loop window.** Counts restart at `task.started` and at every
  `feedback.given`, as the doc says. Critique: a `revise` goes to `plan`
  while revisions in the window are fewer than `critique_rounds`, else to
  `build` with its findings as the build's entry. Review and repair rounds
  as in the join below.
- **The join**, `join(checks, loops, counts) -> JoinResult`: the doc's
  seven rows, read in order, giving `merge` (passed, did not pass, with
  gaps, governance refused) or `patch` (with every finding of all three
  branches), and which count it spends. It runs inside the fold the moment
  the third verdict for the current candidate lands. No row records the
  join itself: it is a function of the verdict rows, so it cannot disagree
  with them.
- **Governance refused** at the join means review said
  `governance_refused`, or docs answered its governance boolean yes with an
  instance not yet granted (decided by default, see below), so a rule added
  in a doc reaches Tom the same way as one added in code.
- **`governance_granted`** in `merge` happens when every governance
  instance of the current candidate has a `guard.granted` row on the task.
  It returns to `checks` with the review verdict cleared and the test and
  docs verdicts kept, so only review runs again. A partial grant stays in
  `merge`.
- **Merged.** `merge` goes to `merged` on a done `effect.outcome` of the
  held merge effect whose payload names the current candidate. A failed
  outcome stays in `merge` (the approval it consumed is spent; Tom taps
  again).
- `merge_predicate(fold, rows, effect) -> list[str]`, the five terms of
  the doc, each a named failure when it does not hold. Term 1 also fails
  when the merge effect's payload names a candidate other than the current
  one. Term 4 reads `docs.decided`: its head is the merge payload's
  `head_sha`, its commits run from the candidate's sha to that head, and
  every changed path is a `.md` file or under a path the plan names in
  `doc_paths`. Term 5 is an `approval.granted` for this effect id and
  payload digest with no `effect.intent` using it. Deterministic: rows
  exist or they do not.

### 2. Legacy tasks in the real ledger

`task.started` gains `"sdlc": 1`. A task whose `task.started` lacks it is
legacy, and the fold maps it with the rule the old kernel used, read-only:
stopped is `stopped`; an unanswered question is `waiting` (returning to
`build`); a delivery not reopened by feedback is `merge`; feedback after a
delivery is `patch`; any turn otherwise is `build`; no turn is `judge`.
`Fold.legacy` is true. The router, `answer`, `feedback`, `verdict`, and
`grant` refuse a legacy task ("predates the state machine"); `status`,
`ledger`, `stop`, `approve`, and `release` work as before, so a held push
from a replay can still be released. No row is rewritten, which is
data.md's rule: the reader carries the old shape forward.

The old four-state fold is kept only in a test, as the oracle the legacy
mapping is checked against (the way `MONEY_SQL` is kept in
`test_migrate_history.py`).

### 3. Event types, payloads, and constraints

New or changed rows. Every verdict field is checked at write by one
`CHECK` constraint, `events_verdict_in_enum`, so a verdict outside its
enum is refused by Postgres whatever code writes it; `machine.VERDICTS` and
the constraint are compared by a test so they cannot drift. Adding a
`CHECK` scans the table and rewrites no row, which
`test_migrate_history.py` proves on a copy of the real ledger.

| Type | Writer | Payload | Constraint |
|---|---|---|---|
| `task.started` | `tasks.start` | as today, plus `sdlc: 1`, `target_branch`, `base_sha` | |
| `judge.decided` | `tasks.start` (manual), the judge runner in 1.3 | `verdict` (`precise`, `thin`), `leg`, `judgement_id` (null when manual), `guard_id` (when `thin`), provenance when manual | enum; `events_one_judge` unique on `task_id` |
| `turn.collected` | `session` | as today, plus `state` (the state the turn ran in), `verdict`, `candidate` `{sha, turn_id}` when `candidate`, `errors` (signals that did not count, and why) | enum per `state`, null allowed for legacy rows; `events_one_turn_row` unique on `(type, turn_id)` for `turn.started`, `turn.ended`, `turn.collected`, `turn.reaped` |
| `question.asked` | `session` | as today, plus `state` (where `answered` returns) | |
| `plan.written` | `session` | `turn_id`, `path`, `commit`, `sha256` of the file at that commit, `stakes`, `critique_rounds`, `review_rounds`, `scope` (each addition with the debt it pays), `doc_paths` | both counts in 0..2 |
| `critique.decided` | `verdict` CLI (manual), the critique runner in 1.4 | `plan_sha256`, `verdict` (`sound`, `revise`), `findings`, `raised` (either count), `leg`, `model`, `usd_micros`, `guard_id` (when `revise`), provenance when manual | enum; raised counts in 0..2 |
| `test.decided` | `verdict` CLI, 1.4 | `candidate`, `verdict` (`pass`, `red`, `gaps`), `command`, `failures`, `behaviors`, `leg`, `model`, `usd_micros`, `guard_id` (breadth), provenance when manual | enum |
| `review.decided` | `verdict` CLI, 1.4 | `candidate`, `verdict` (`pass`, `changes`, `governance_refused`), `findings` (each with a kind, `debt` among them), `governance` `{adds, instances: [{id, hunk, summary, incident, mission_item}]}`, `leg`, `model`, `usd_micros`, `guard_id` (review loop), provenance when manual | enum |
| `docs.decided` | `verdict` CLI, 1.4 | `candidate`, `verdict` (`updated`, `no_change`, `changes`), `head`, `commits` (each sha with its paths), `findings`, `governance` as review, `leg`, provenance when manual | enum |
| `task.delivered` | the verdict writer whose row completes a join that goes to `merge` | `candidate`, `outcome` (`passed`, `gaps`, `did_not_pass`, `governance_refused`), `summary` (the candidate turn's `done.md`), the three verdict event ids, `findings`, `gaps`, `scope` | |
| `feedback.given` | `session.feedback` | as today, plus `candidate` | |
| `guard.granted` | `migrate` (the seeded guards, on the `guards` stream), `guards.grant` (a governance instance, on the task) | `guard_id`, `name`, `incident`, `mission_items`, `granted_at`, `expires` (granted plus ninety days), `note` (Tom's literal message), provenance; on a task also `instance_id` and `candidate` | `events_one_guard` unique on `guard_id` |

A writer that would write a row the fold would ignore refuses instead:
`answer` needs `waiting`; `feedback` needs `merge` or `merged`; `verdict`
needs the task in that stage (and in `checks`, a current candidate); `grant`
needs `merge` with an ungranted instance. Each takes the task's advisory
lock, folds, checks, and appends in one transaction, as `answer` does
today.

### 4. Verdict writers and the join's effects: `core/verdicts.py` (new)

`record_judge`, `record_critique`, `record_check`, each under the task
lock. A review verdict that answers its governance boolean yes with an
instance not yet granted must be `governance_refused`; a `pass` with an
ungranted instance is refused at write, so the rows cannot say two things.
After appending a check verdict the writer folds again; if the join just
went to `merge`, it appends `task.delivered` in the same transaction, then
(outside it, as the broker always works) requests the merge effect when
the delivery passed or passed with gaps. A delivery that did not pass, or
whose governance was refused, requests no merge: no predicate could
release it. Tom's options there are `feedback`, `grant`, or `stop`.

The manual docs verdict takes `--head` (default the candidate's sha) and
records the commits and their paths as `git log --name-only` reports them
in the task's workspace, refusing a head that does not descend from the
candidate. It does not drop commits outside doc paths; that is 1.4's docs
runner. The predicate refuses the merge instead (term 4).

### 5. The working-session runner: `core/session.py`

Kept: the turn loop, resume, money checks, idle counting, the rule that an
answer, feedback, or findings are spent only by a turn that finishes, and
the effects report. Changed:

- It runs one state's turns (`clarify`, `plan`, `build`, `patch`) and
  returns when the fold leaves that state, so the router decides what runs
  next.
- `next_prompt` builds the prompt from `Fold.entry` as data under a short
  label: the instruction, Tom's answer, the critique's findings, every
  finding of the join, or Tom's feedback; `Continue.` once a turn in the
  state has finished. The framing sentences move to the stage files.
- `record` writes `turn.collected` with the state and verdict:
  - `clarify`: `question.md` is `asked`; `.valor/no_question.md` (new: why
    no question would change the result, and the intended approach) is
    `no_material_question`.
  - `plan`: `.valor/plan.json` (new) names the plan file and carries the
    stakes, both counts, scope additions, and `doc_paths`. The kernel reads
    the file at the workspace's HEAD through `core/git.py`; a plan not
    committed there, or counts outside 0..2, is an entry in `errors` and no
    verdict (`idle`), and the next prompt says why. Valid: `plan.written`
    in the same transaction, verdict `planned`.
  - `build`, `patch`: `done.md` is `candidate`, with the head sha read
    through `core/git.py` and the turn id. No `task.delivered`.
  - Any of them: `question.md` is `asked` and wins over the others in the
    same turn, as today. A signal that means nothing in the state (say
    `done.md` during `plan`) goes to `errors` and is not acted on. Neither
    signal is `idle`; a turn that did not finish and left neither is
    `failed`. Signals a failed turn left still count, as today.
- `feedback` is taken in `merge` and `merged` only, carries the candidate,
  and opens a new loop window.

### 6. The router: `core/router.py` (new) and `python -m core run`

Each call folds, then: `waiting`, `merge`, `merged`, `stopped`, and legacy
tasks return at once with one status line; a state with a runner runs it
and folds again; a state without one returns `no runner`. In `checks` it
runs each branch still missing a verdict for the current candidate, one at
a time (the Air's one turn slot), and returns `no runner` naming the
branches it could not run. Budget exhaustion, two idle turns, and a failed
turn return as today. The run loop never writes a verdict itself.

`status` returns the fold: `state` is now the machine state's name, plus
`legacy`, `return_to`, `plan`, `loops`, `counts`, `candidate`, `checks`,
`governance`, `delivered`, alongside today's money, turns, effects, and
attention. `scripts/replay.py` and `scripts/role_play_tom.py` change in the
same commit: `waiting` replaces `waiting for Tom`, `merge` replaces
`delivered`, and a `no runner` line ends the run with that outcome rather
than looping to the run cap. So no replay can finish between 1.2 and 1.4;
the takeover gate in 1.5 is where replays run the whole pipeline.

### 7. Guards: `core/guards.py` (new)

- **Seeded.** `db.migrate` seeds, on the `guards` stream, once each (the
  unique index and an advisory lock make a second migrate a no-op, like
  correction 1): `intake.underspecified` (the judge),
  `checks.test.breadth`, `critique.loop`, `review.loop`, and
  `join.repair_round` (see Questions). Each carries the incidents and
  mission items the state machine doc and judgement-layer.md give it,
  `granted_at` 2026-10-01, `expires` 2026-12-30, Tom's note quoting his
  2026-10-01 decision, and provenance `by: tom`, `via: "the 2026-10-01
  pipeline decision, seeded by migrate"`.
- **Firing.** A verdict row that holds or redirects work names the guard
  it fires under (`guard_id` on `judge.decided` `thin`, `critique.decided`
  `revise` that goes back to `plan`, `test.decided` with listed behaviors,
  and the check verdict that completes a join sending work to `patch`
  names `review.loop` or `join.repair_round`). Deleting an
  expired guard is a routine for milestone 4; 1.2 only records.
- **Granting an instance.** `python -m core grant TASK INSTANCE --note
  "..." [--incident T] [--mission-item N] [--by --via --role-played]` is
  Tom's tap on one governance instance, the same shape and provenance as
  `approve`. It writes `guard.granted` on the task with the expiry ninety
  days out. Incident and mission item default to what the review's
  instance named; a grant missing either is refused, which is the
  governance paragraph's own rule ("missing either, it is not added"), not
  a new one.

### 8. The broker's governance flag

`Action` loses `adds_governance`. `broker.request` computes it: for a
`merge` action, true when the review or docs verdict for the candidate the
payload names answered its governance boolean yes; for every other action,
false. The requester cannot set it in code, and an effect file never could.
A merge that adds governance with any instance lacking a `guard.granted`
is refused at request. `release` of a `merge` effect evaluates
`merge_predicate` under the task lock in the same transaction that writes
`effect.intent`, so no feedback or new candidate can land between the check
and the intent; a failing predicate raises `MergeRefused` naming the terms,
writes nothing, and leaves the approval unused. `effect.held` keeps
recording `adds_governance`, now as the broker computed it.

The merge performer is `tools/push_branch.py`'s push with action type
`merge`: it pushes the payload's `head_sha` (the docs head, or the
candidate's sha) to the Brief's `target_branch` on the workspace's
`origin`, never with force. `target_branch` is `--target-branch` at start,
defaulting to the branch checked out in the workspace then; `base_sha` is
that branch's head then (1.4's test branch runs the suite there).

### 9. Stage files: `skills/sdlc/`

Plain files, no versioning: `clarify.md`, `plan.md`, `critique.md`,
`build.md`, `review.md`, `docs.md`, `patch.md`, each stating the stage's
goal and exit evidence (the signal file and the row it becomes), not
steps, and `channel.md`, the `.valor/` convention. `tasks.dispatch`
renders, after the Brief head and corrections: `channel.md` with the
effects list generated from the registered performers that offer a
`usage` line (the merge performer offers none: the kernel requests it),
then the file for the current state. The plan's path and commit go in the
Brief head once one exists. `critique.md`, `review.md`, and `docs.md` are
written now so there is one file per stage; 1.4's fresh sessions render
them. `judge` is a classifier prompt (judgement-layer.md, 1.3) and `merge`
is the kernel's, so neither has a file. `skills/README.md` says the
directory holds these plain files until the versioned system is designed.

### 10. Shared pinned git: `core/git.py` (new)

The pinned runner moves from `tools/push_branch.py` to `core/git.py`
(`head`, `show`, `is_ancestor`, `changed_paths`), and the performer
imports it. The kernel reads a workspace the turn can write, so every read
pins hooks, fsmonitor, credential helper, and SSH command the same way.

## Tech debt paid

| Debt | What pays it |
|---|---|
| `adds_governance` settable by any requester and set by none | computed by the broker from verdict rows (item 8) |
| `CLARIFY` tells Valor to write `question.md` with no question | `clarify.md` and `no_question.md` give `no_material_question` its own evidence |
| `PROTOCOL` hard-codes `push_branch` | the effects list is rendered from registered performers |
| `done.md` writes `task.delivered` at once | it is a candidate; `task.delivered` comes from the join |
| prompt text in `core/session.py` and `core/signals.py` | moved to `skills/sdlc/` |
| `tasks.status`, `session.open_question`, and `session.next_prompt` each fold the ledger their own way | one fold, `machine.fold`, read by all three |
| the pinned git runner lives in one performer only | `core/git.py`, shared by the kernel's reads and the performers |
| `test_the_clarify_mode_is_recorded_and_carried_in_the_brief...` encodes the Brief section 1.2 deletes | replaced by the judge row and stage rendering tests |

## Tests that show it works

New `tests/test_machine.py` (pure fold, no database) and
`tests/test_pipeline.py` (real Postgres, real git, scripted turns as in
`test_session.py`). All spend $0 except the two `VALOR_LIVE` files.

**The model and the join.**
- Every row of the join table, parametrized, through the pure fold; and
  one row (`changes` with a round left) end to end through the router with
  manual verdicts.
- A verdict outside its enum is refused at write: a raw `INSERT` as
  `valor_kernel` of each verdict type with a bad value raises
  `CheckViolation`; each value in `VERDICTS` is accepted; plan counts of 3
  and a critique raise of 3 are refused. `VERDICTS` matches the constraint
  text.
- A second `judge.decided` and a second `turn.collected` for one turn are
  refused by their unique indexes.

**Loops.**
- `critique_rounds` 0, 1, 2 against a run of `revise` verdicts: the
  number of returns to `plan`, and the findings carried into the build's
  first prompt once exhausted. A critique raise from 1 to 2 gives a second
  revision; a "raise" to 0 lowers nothing.
- `review_rounds` 0, 1, 2 against a run of `changes`: patches before
  `merge` never exceed `review_rounds` plus one, and the last delivery is
  `did_not_pass` with every finding.
- The repair round spent once per window: `pass` with `red` patches once,
  then a second `red` goes to `merge` as did not pass; `gaps` after it goes
  to `merge` with the gaps listed.
- Feedback after `merged`: the task goes to `patch`, the window restarts,
  and review rounds are available again.

**Candidates and staleness.**
- A stale-candidate verdict is ignored: a `review.decided` for an older
  candidate arriving after a new one changes neither the join nor the
  predicate.
- A patch that answers findings with reasons and no code change (same sha,
  new turn id) is a new candidate: the old verdicts are stale and all three
  branches are missing again.
- `governance_granted` reruns only review: after the grant, test and docs
  verdicts stand, the router reports only `review` missing, and the next
  review verdict completes the join. A grant of one of two instances stays
  in `merge`.

**The merge predicate.**
- All five terms hold: release pushes the head to the target branch of a
  real bare origin.
- Each term failing alone refuses the release with that term named and
  nothing written: no docs verdict; review `pass` with an ungranted
  governance instance (refused at write, so built by raw insert); test
  `red`; `gaps` with the repair round unspent; docs commits touching
  `core/x.py` (outside doc paths), and the same path accepted when the
  plan names it in `doc_paths`; docs head not the payload's head; no
  approval.
- An approval for a different digest: the merge effect of candidate 1 is
  approved, feedback produces candidate 2 and its merge effect; candidate
  2's effect does not release on candidate 1's approval (term 5), and
  candidate 1's effect does not release although approved (term 1).
- The broker computes the governance flag: an effect file carrying
  `adds_governance` gets an effect recorded with `false`; a merge whose
  review named an ungranted instance is refused at request.

**States and Tom's rows.**
- A stopped task in every state (each of the eleven reached by its own
  prefix, then stopped): the fold says `stopped`; `run` returns without a
  turn; `answer`, `feedback`, `verdict`, `grant`, and `budget raise` are
  refused; a row appended after the stop by raw insert changes nothing.
- `answered` returns to the state named on `question.asked`, for each of
  `clarify`, `plan`, `build`, `patch`, and the next turn's prompt holds
  the answer; after a failed turn the answer is sent again.
- A clarify turn writing `no_question.md` goes to `plan` with no
  attention spent.
- A plan turn: `plan.json` naming an uncommitted file, or counts of 3,
  leaves `errors` and no `plan.written`, and the next prompt says why;
  `done.md` written during `plan` is not a candidate.
- `start --mode clarify` writes `judge.decided` `thin` with `leg: manual`
  and the starter's provenance; `start` without `--mode` leaves the task in
  `judge`, and `run` returns `no runner` naming `judge` and writing nothing.
  Likewise at `critique` and in `checks`.
- Stage rendering: the dispatched Brief carries `channel.md` and the
  current state's file, changes when the state does, and lists every
  registered performer offering a `usage` line and not `merge`.

**Totality, by property.** Hypothesis (added to the dev group) generates
ledgers of well-formed rows, rows with wrong or missing fields, unknown
types, stale verdicts, and legacy-shaped starts. For every prefix: `fold`
returns exactly one `State` and does not raise; `stopped` is absorbing;
`checks` holds verdicts for the current candidate only; patches before a
`merge` never exceed `review_rounds` plus one.

**Legacy.** A synthetic ledger holding every legacy shape folds to the old
oracle's mapping. In `test_migrate_history.py`, every task of the copy of
the real ledger folds without raising, is `legacy`, and matches the oracle
(skipped where the machine has no `valor_rebuild`). The router, `answer`,
and `feedback` refuse a legacy task; `release` of its held push still
works.

**Guards.** A fresh migrate holds the five seeded guards once each, with
incident, mission items, and expiry 2026-12-30; a second migrate adds
none; `grant` without an incident is refused.

**Kept green.** `test_replay.py`, `test_kernel.py` (its governance tests
now build the flag from a review verdict), `test_attention.py` (with
`verdict` and `grant` entries counted apart), `test_session.py` (rewritten
for states), `test_migrate_history.py` (the new constraint and indexes on
the copy, no row or file node changed). `test_live_session.py` drives a
real `claude -p` task from `start --mode bare` through plan, manual
critique and checks, merge, and release; `test_live_turn.py` drops its
`adds_governance` request. Both stay behind `VALOR_LIVE=1` and are run once
at build within their declared spend.

## Docs the build makes true

`docs/sdlc-state-machine.md` (What exists; the `VERDICTS` change; the
manual leg; the stage file names), `docs/data.md` (event types, the new
indexes and the constraint, the fold section, legacy shapes),
`docs/architecture.md` (state; flow; failure table rows that say design),
`docs/harnesses.md` (signal files, prompts, skill rendering),
`docs/judgement-layer.md` and `docs/persona.md` (`CLARIFY` and `PROTOCOL`
references), `docs/tech-stack.md` (Hypothesis in use), `core/README.md`,
`skills/README.md`, `tests/README.md` if its index names files.

## Out of scope

- The judgement port and every real judge, breadth, or governance call
  (1.3); `--mode` is deleted there.
- Fresh-session critique, review, and docs runners, the suite runner,
  dropping docs commits outside doc paths, workspace provisioning, the
  container verifier, the GitHub credential (1.4).
- The emulator and the takeover gate (1.5). No replay finishes until 1.4.
- The resident process and the supervisor (milestone 2); bridges and the
  tap cards (milestone 2).
- The versioned skill system; deleting expired guards (a milestone 4
  routine); any listing of guards beyond `ledger guards` (no governance
  dashboard).
- Concurrent branches in `checks`: the router runs them one at a time,
  which the doc allows with identical semantics.

## Rollout at merge

Not done by the builder. At merge, in the kernel checkout: `uv sync` (adds
Hypothesis to the dev group), then `python -m core migrate` on the real
ledger, which adds the constraint and indexes (no row rewritten, shown on
the copy) and seeds the five guards. The build's tests run from a venv in
this worktree (`uv sync` here) so the kernel checkout's venv is not
touched.

## Questions for Tom (assumed answers; the build proceeds on them)

1. **A manual verdict command until 1.4.** Without it no real task can
   pass `critique` or reach `merge` before 1.4. Assumed: yes, as
   `python -m core verdict`, each row `leg: manual` with provenance and
   counted as attention.
2. **The Brief's `governance_grant` and per-instance taps.** CLAUDE.md asks
   for one tap per instance; the predicate doc says "the Brief carries a
   grant and Tom tapped each instance". Assumed: a `guard.granted` per
   instance is what the predicate and the broker require, and the Brief's
   field is shown to the reviewer as Tom's advance intent, not a
   substitute for the taps. Tom's tap in `merge` grants even when the
   Brief says none, as the doc's `governance_granted` flow describes.
3. **The repair round as its own guard.** It redirects work and the doc's
   list names only the critique and review loops. Assumed: seeded as
   `join.repair_round` under the same 2026-10-01 grant and expiry.
4. **`answered` after `clarify` returns to `clarify`**, per the doc, which
   costs one short resumed turn before `plan`. Assumed: follow the doc.

## Decided by default (reversible)

- Row and signal names: `judge.decided`, `plan.written`,
  `critique.decided`, `guard.granted`; `.valor/plan.json`,
  `.valor/no_question.md`.
- `idle` and `failed` in every working-session state's enum.
- Signals a failed turn left still count, as today.
- The legacy marker `sdlc: 1` on `task.started`, and the legacy mapping.
- Enum enforcement as one `CHECK` constraint rather than code alone.
- Docs answering governance yes with an ungranted instance counts as
  governance refused at the join.
- No merge effect is requested for a delivery that did not pass.
- `--target-branch` defaults to the workspace's branch at start.
- `start` without `--mode` waits in `judge` (scripts pass `--mode`).
- Checks branches run one at a time.
