---
tracking: none
slug: m1-2-state-machine
type: build
status: merged
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
ledger's schema, writes permanent guard rows to the real ledger, and moves
the merge decision into the broker, all in the kernel itself, so
`critique_rounds: 2`, `review_rounds: 2`.

## What is true today (checked, not assumed)

- `tasks.status` folds four states (`live`, `waiting for Tom`,
  `delivered`, `stopped`), with precedence stopped, then a delivery not
  reopened by feedback, then an unanswered question, then live.
  `session.run` loops turns until the state changes. `session.record`
  writes `task.delivered` the moment a turn leaves `done.md`.
- `broker.Action.adds_governance` is a constructor argument. Nothing in
  `core/`, `tools/`, or `scripts/` ever sets it; `signals.collect` never
  reads it from an effect file. Only tests set it. The broker refuses such
  an action when the Brief carries no `governance_grant`.
- `broker.release` reads the approval in one transaction and `_perform`
  writes `effect.intent` in another, so a row can land between the check
  and the intent.
- `signals.PROTOCOL` names `push_branch` by hand. `signals.CLARIFY` tells
  the turn to write `question.md` "if you have no such questions", which
  makes a clarify turn with nothing to ask indistinguishable from one that
  asked.
- `session.next_prompt` holds the feedback framing ("Tom reviewed your
  delivery and, as project manager, ...") and the "Tom answered your
  question" framing.
- `tools/push_branch.py` pushes to the remote named `origin` as the
  workspace's `.git/config` defines it. The turn can rewrite that file
  (the URL, a `pushurl`, a `url.*.insteadOf` rule), so today the
  destination of an approved push is under the turn's control. Its pinned
  git runner (hooks, fsmonitor, credential helper, SSH command disabled)
  is the only one in the repository.
- `Brief` is rebuilt from the task document with `Brief(**body)`, so any
  field a legacy document lacks needs a default.
- The real ledger `valor_rebuild` (read-only check, 2026-10-01): 1,416 rows,
  22 tasks, 18 event types. 21 `task.delivered`, 8 `question.asked` and 8
  answered, 5 `feedback.given`, 2 `task.stopped`, 19 held effects (17
  `push_branch`, 2 `outbox_send`), none still pending. No turn id repeats
  on `turn.started`, `turn.ended`, or `turn.collected`. Four tasks have no
  workspace; six have no `mode`. The `corrections` stream shares the
  events table but has no task document.
- The replay and demonstration workspaces sit on a work branch (for
  example `valor/work`) at the base commit, with `origin`'s `main` at that
  same base.
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
| `waiting`, `merged`, `stopped` | nothing runs; the router returns | |
| `merge` | the router requests the merge effect if it is missing, then returns | built here |

**The seam.** `core/router.py` takes a mapping from `State` or `Check` to a
runner, passed in by the composition root (`core/__main__.py`), never a
module global. A runner is an async callable that runs the work for one
state or branch and records its verdict row. When the current state (or a
branch of `checks` still without a verdict) has no runner, the router
returns `no runner`, names what is missing, writes nothing, and prints the
command that records the verdict by hand. 1.3 and 1.4 add entries to the
mapping and change nothing in the router.

**Manual verdicts, a stand-in that closes itself.** `python -m core verdict
TASK STAGE VERDICT` records a verdict for a stage with no runner (`judge`,
`critique`, `test`, `review`, `docs`), with `leg: "manual"` and provenance
(`by`, `via`, `at`, `role_played`), refused unless the task is in that
stage. It adds no checkpoint: each of those stages is already in the
granted pipeline, and the command only lets a person play one until its
runner exists. It refuses a stage that the composition root's runner
mapping covers, so the stand-in closes on its own as runners arrive, and
1.3 (judge) and 1.4 (critique, test, review, docs) each delete their
stage from it; 1.4 deletes the command. The router and scripts never call
it. A manual verdict is attention spent, so it is an attention entry of
its own kind, counted apart.

The judge uses the same stand-in: `start --mode bare|clarify` records
`judge.decided` `precise` or `thin` with `leg: "manual"` and the starter's
provenance in the same transaction as `task.started`. Without `--mode`,
the task waits in `judge` and `run` says so. 1.3 deletes `--mode`.

Why manual rows rather than skipping states: the predicate reads rows, so a
merge before 1.4 needs real `test.decided`, `review.decided`, and
`docs.decided` rows, and a row a person wrote, marked role-played when a
stand-in wrote it, is honest about who decided. Skipping would be the
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
  `CheckVerdict`, `Checks`, `Loops(critique_rounds, review_rounds)`,
  `Instance(id, path, hunk)` for a governance instance.
- `Fold`, the result of `fold(rows)`: `state`, `legacy`, `return_to` (in
  `waiting`), `plan` (the current `plan.written`), `loops`, `candidate`,
  `checks` (verdicts keyed to the current candidate only), `counts` since
  the window opened (critique revisions, review send-backs, repair round
  spent), `entry` (the row that moved the task into its current state,
  which the next turn's prompt is built from), `delivery` (the latest
  `task.delivered`), `governance` (the current candidate's instances and
  which are granted), `merge_effect` (the held merge effect for the
  current candidate, if any, and its state), and `ignored` (rows that
  were not a transition from the state they arrived in, with why).
- `fold` is total: it reads rows in id order, applies a row only when it is
  a transition from the current state, and ignores (and lists) everything
  else, including rows missing fields and rows of unknown types. It never
  raises. In particular it ignores a `turn.collected` whose `state` is not
  the current state (a turn started in a state the task has since left),
  and a `critique.decided` whose `plan_sha256` is not the current plan's.
- **Loop window.** Counts restart at `task.started` and at every
  `feedback.given`, as the doc says. `loops` is the larger, per count, of
  the current plan's value and every raise a critique made on the task, so
  a revised plan cannot lower a count a critique raised. A raise
  applies to the verdict carrying it: a `revise` that raises
  `critique_rounds` from 0 to 1 is itself sent back to `plan`. A `revise`
  goes to `plan` while revisions in the window are fewer than
  `critique_rounds`, else to `build` with its findings as the build's
  entry.
- **The join**, `join(checks, loops, counts) -> JoinResult`: the doc's
  seven rows, read in the table's order, first match wins:
  1. review `pass`, test `pass`, docs passing: `merge`, passed;
  2. review `governance_refused`: `merge`, governance refused;
  3. review `changes`, a review round left: `patch`;
  4. review `changes`, none left: `merge`, did not pass;
  5. review `pass`, test `red` or `gaps` or docs `changes`, repair round
     unspent: `patch`;
  6. review `pass`, test `red` or docs `changes`, repair round spent:
     `merge`, did not pass;
  7. review `pass`, test `gaps`, docs passing, repair round spent: `merge`,
     with the gaps.
  Row 2 reads review only, as the spec's table does; docs answering its
  governance boolean yes does not change the join's row. It changes what
  Tom is asked in `merge` (below). The join runs inside the fold the
  moment the third verdict for the current candidate lands. No row records
  the join itself: it is a function of the verdict rows, so it cannot
  disagree with them. The review loop guard fires on row 3 and on row 5
  (see Guards).
- **`governance_granted`** in `merge` after row 2 happens when every
  instance the refused review named has a `guard.granted` on the task. It
  returns to `checks` with the review verdict cleared and the test and
  docs verdicts kept, so only review runs again. A partial grant stays in
  `merge`.
- **Merged.** `merge` goes to `merged` on a done `effect.outcome` of the
  merge effect whose payload names the current candidate. A failed
  outcome stays in `merge` (the approval it consumed is spent; Tom taps
  again).
- `merge_predicate(fold, rows, effect, facts) -> list[str]`, the five
  terms of the doc, each a named failure when it does not hold. `facts`
  are what the broker reads from git at release (item 8). Term 1 also
  fails when the merge effect's payload names a candidate other than the
  current one. Term 2: review `pass`, and every governance instance named
  by the current review verdict has a `guard.granted` on the task. Term 4:
  docs `updated` or `no_change`; its recorded head is the payload's
  `head_sha`; per `facts`, the candidate's sha is an ancestor of that
  head, no merge commit lies between them, and every path in `git diff
  --no-renames --name-only` between them is a Markdown file that instructs
  no turn (`machine.is_doc_path`); and every instance the docs verdict named
  has a `guard.granted`. Term 5: an `approval.granted` for this effect id
  and payload digest with no `effect.intent` using it. Each term reads a
  row or a git fact; no model call decides.

### 2. Legacy tasks in the real ledger

`task.started` gains `"sdlc": 1`. A task whose `task.started` lacks it is
legacy, and the fold maps it with the old fold's own precedence,
read-only: `stopped` if stopped; else `merge` if a delivery has not been
reopened by later feedback; else `waiting` (returning to `build`) if a
question is unanswered; else `patch` if feedback followed a delivery; else
`build` if any turn ran; else `judge`. `Fold.legacy` is true. The router,
`answer`, `feedback`, `verdict`, and `grant` refuse a legacy task
("predates the state machine"); `status`, `ledger`, `stop`, `approve`, and
`release` work as before, so a legacy held push can still be released. No
row is rewritten, which is data.md's rule: the reader carries the old
shape forward.

The old four-state fold is kept only in a test, as the oracle the legacy
mapping is checked against (the way `MONEY_SQL` is kept in
`test_migrate_history.py`). Tasks are the ids in `documents` where `kind =
'task'`; the `corrections` and `guards` streams are not tasks and are never
folded as one.

### 3. Event types, payloads, and constraints

New or changed rows:

| Type | Writer | Payload | Constraint |
|---|---|---|---|
| `task.started` | `tasks.start` | as today, plus `sdlc: 1`, `target_branch`, `origin_url`, `base_sha` | |
| `judge.decided` | `tasks.start` (manual), the judge runner in 1.3 | `verdict` (`precise`, `thin`), `leg`, `judgement_id` (null when manual), `guard_id` (when `thin`), provenance when manual | enum; `events_one_judge` unique on `task_id` |
| `turn.started` | `runs` | as today, plus `state` | `events_one_turn_row` |
| `turn.collected` | `session` | as today, plus `state`, `verdict`, `candidate` `{sha, turn_id}` when `candidate`, `errors` (signals that did not count, and why) | verdict in the enum for its state, and non-null whenever `state` is present (legacy rows have neither); `events_one_turn_row` unique on `(type, turn_id)` for `turn.started`, `turn.ended`, `turn.collected`, `turn.reaped` |
| `question.asked` | `session` | as today, plus `state` (where `answered` returns) | |
| `plan.written` | `session` | `turn_id`, `path`, `commit`, `sha256` of the file at that commit, `stakes`, `critique_rounds`, `review_rounds`, `scope` (each addition with the debt it pays) | both counts in 0..2 |
| `critique.decided` | `verdict` CLI (manual), the critique runner in 1.4 | `plan_sha256`, `verdict` (`sound`, `revise`), `findings`, `raised` (either count), `leg`, `model`, `usd_micros`, `guard_id` (when `revise`), provenance when manual | enum; raised counts in 0..2 |
| `test.decided` | `verdict` CLI, 1.4 | `candidate`, `verdict` (`pass`, `red`, `gaps`), `command`, `failures`, `behaviors`, `leg`, `model`, `usd_micros`, `guard_id` (breadth, when behaviors are listed), provenance when manual | enum |
| `review.decided` | `verdict` CLI, 1.4 | `candidate`, `verdict` (`pass`, `changes`, `governance_refused`), `findings` (each with a kind, `debt` among them), `governance` `{adds, instances: [{id, path, hunk, summary, incident, mission_item}]}`, `leg`, `model`, `usd_micros`, `guard_id` (review loop, on a verdict that completes join row 3 or 5), provenance when manual | enum |
| `docs.decided` | `verdict` CLI, 1.4 | `candidate`, `verdict` (`updated`, `no_change`, `changes`), `head`, `paths` (from `git diff --no-renames --name-only`), `findings`, `governance` as review, `leg`, provenance when manual | enum |
| `task.delivered` | the verdict writer whose row completes a join that goes to `merge` | `candidate`, `outcome` (`passed`, `gaps`, `did_not_pass`, `governance_refused`), `summary` (the candidate turn's `done.md`), the three verdict event ids, `findings`, `gaps`, `scope`, instances awaiting a tap | |
| `feedback.given` | `session.feedback` | as today, plus `candidate` | |
| `guard.granted` | `migrate` (the seeded guards, on the `guards` stream), `guards.grant` (a governance instance, on the task) | `guard_id`, `name`, `incident`, `mission_items`, `granted_at`, `expires` (granted plus ninety days), `note` (Tom's literal message), provenance; on a task also `instance_id` and the `candidate` it was granted on | `events_one_guard` unique on `guard_id` |

**The enum constraint.** One `CHECK` constraint generated from
`machine.VERDICTS` by `machine.constraint_sql()`, so the enum lives in one
place. Its name is `events_verdict_in_enum_<digest>`, the digest of the
generated SQL, because Postgres deparses a stored constraint (casts,
parentheses) and its text never equals the generated text. `db.migrate`
reads `pg_constraint` under an advisory lock: the current name in place
means nothing to do; otherwise every older one is dropped and the current
one added in one transaction. It is always added `NOT VALID`, so history
is never rechecked: the rule is that a verdict value may be removed from
`VERDICTS` and history holding it does not refuse the change, while every
new row is checked. Nothing is rewritten, which `test_migrate_history.py`
proves on a copy of the real ledger. A verdict outside its enum is refused
by Postgres whatever code writes it.

**Governance instance ids.** A review or docs verdict names an instance by
a path and a line inside its hunk. The kernel reads the hunk itself from
the candidate's real diff (`git diff` from the task's base for review, from
the candidate to the docs head for docs) and refuses an instance it does
not find there; reviewer-supplied hunk text is never used. `id` is a digest
of the path, the hunk header's function context, and the hunk's added
lines, without line numbers, so a review rerun on the same candidate, or a
later candidate whose hunk did not change, names the same id, and the same
added lines in two functions are two instances. Identical added lines in
the same function context of the same file share an id. A moved or split
hunk gets new ids and needs new taps: accepted. A `guard.granted` binds to
the instance id, not to the candidate: an unchanged hunk stays granted
across patches. A task grant gets a fresh `guard_id` (`grant-<id>`), and
`events_one_instance_grant` makes `(task_id, instance_id)` unique, so the
same hunk granted on another task is not refused by `events_one_guard`.

A writer that would write a row the fold would ignore refuses instead:
`answer` needs `waiting`; `feedback` needs `merge` or `merged` and no merge
effect with an intent and no outcome; `verdict` needs the task in that
stage (and in `checks`, a current candidate); `grant` needs `merge` and an
instance of the current candidate's review or docs verdict without a
grant. A review naming an ungranted instance must be `governance_refused`,
and a `governance_refused` naming only instances already granted is
refused at write (such a review is `pass`), so a review cannot strand the
task in `merge`. Each takes the task's advisory lock, folds, checks, and appends in
one transaction, as `answer` does today.

### 4. Verdict writers and the join's effects: `core/verdicts.py` (new)

`record_judge`, `record_critique`, `record_check`, each under the task
lock. A review verdict that answers its governance boolean yes with an
instance not yet granted must be `governance_refused`; a `pass` with an
ungranted instance is refused at write, so the rows cannot say two things.
After appending a check verdict the writer folds again; if the join just
went to `merge`, it appends `task.delivered` in the same transaction.

The merge effect is requested by `core/verdicts.py::ensure_merge`,
called after that transaction commits and by the router on every run that
finds the task in `merge`: when the delivery passed or passed with gaps
and the fold shows no held, in-flight, or done merge effect for the
current candidate, it requests one. It requests nothing while any
governance instance of the candidate awaits Tom's tap, and does not repeat
a request refused with the same payload unless Tom has granted something
since, so repeated runs never fill the ledger with `effect.refused` rows. The broker's idempotency key makes a
repeat request return the same effect, so a crash between `task.delivered`
and the request leaves nothing stranded: the next `run` requests it. A
delivery that did not pass, or whose review refused governance, requests
no merge, since no predicate could release it; Tom's options there are
`feedback`, `grant`, or `stop`. When the merge is refused at request
because a docs instance awaits a tap, the delivery names it, and the next
`run` after Tom's `grant` requests it again (a refused effect is not a
prior outcome to the broker).

The manual docs verdict takes `--head` (default the candidate's sha) and
records the paths `git diff --no-renames --name-only` reports between the
candidate and that head, refusing a head that does not descend from the
candidate or a range holding a merge commit. It does not drop commits
outside doc paths; that is 1.4's docs runner. The predicate refuses the
merge instead (term 4), recomputing the paths at release.

**Limitation, stated in the doc.** Before 1.4 there is no separate docs
checkout, so manual docs commits sit in the builder's workspace on top of
the candidate. If the join sends the work to `patch`, those commits ride
into the next candidate, where the spec says docs commits belong to their
candidate and are not merged after a send-back. 1.4's docs session in its
own checkout ends this.

The verdict CLI registers the merge performer before it writes, since the
writer may request the merge.

### 5. The working-session runner: `core/session.py`

Kept: the turn loop, resume, money checks, the rule that an
answer, feedback, or findings are spent only by a turn that finishes, and
the effects report. Changed:

- It runs one state's turns (`clarify`, `plan`, `build`, `patch`) and
  returns when the fold leaves that state, so the router decides what runs
  next. `turn.started` records the state the turn runs in.
- `next_prompt` builds the prompt from `Fold.entry` as data under a short
  label: the instruction, Tom's answer, the critique's findings, every
  finding of the join, or Tom's feedback; `Continue.` once a turn in the
  state has finished. The framing sentences move to the stage files.
- `record` writes `turn.collected` with the state and verdict:
  - `clarify`: `question.md` is `asked`; `.valor/no_question.md` (new: why
    no question would change the result, and the intended approach) is
    `no_material_question`.
  - `plan`: `.valor/plan.json` (new) names the plan file and carries the
    stakes, both counts, and scope additions. The kernel reads
    the file at the workspace's HEAD through `core/git.py`; a plan not
    committed there, or counts outside 0..2, is an entry in `errors` and
    the verdict `idle`, and the next prompt says why. Valid: `plan.written`
    in the same transaction, verdict `planned`.
  - `build`, `patch`: `done.md` with a clean tree (`git status
    --porcelain` empty; `.valor/` is excluded already) is `candidate`, with
    the head sha and the turn id. A dirty tree is an `errors` entry and
    `idle`, and the next prompt says what is uncommitted. No
    `task.delivered`.
  - Any of them: `question.md` is `asked` and wins over the others in the
    same turn, as today. A signal that means nothing in the state (say
    `done.md` during `plan`) goes to `errors`. Neither signal is `idle`; a
    turn that did not finish and left neither is `failed`. Signals a failed
    turn left still count, as today.
  - An effect file whose action type is `merge` is recorded with the error
    "the merge is the kernel's to request" and never reaches the broker.
- `feedback` is taken in `merge` and `merged` only, carries the candidate,
  and opens a new loop window.

### 6. The router: `core/router.py` (new) and `python -m core run`

A run first takes a session-scoped `pg_try_advisory_lock` on `run:<task>`
on a connection it holds for the whole run; if another run holds it, it
returns `already running` and does nothing. A session lock lives only as
long as its connection, and an idle connection can die during a long turn,
so the run checks it (`SELECT 1`) before each turn and before recording a
turn's verdict, and returns `lock lost` if it is gone. A transaction-pooling
proxy between the kernel and Postgres would break this, since it keeps no
session; the kernel connects directly. Then it folds, and: `waiting`,
`merged`, `stopped`, and legacy tasks return at once with one status line;
`merge` calls `ensure_merge` and returns; a state with a runner runs it
and folds again; a state without one returns `no runner`. In `checks` it
runs each branch still missing a verdict for the current candidate, one at
a time (the Air's one turn slot), and returns `no runner` naming the
branches it could not run. The money-exhausted outcome (removed 2026-10-03: metered spending only; nothing refuses on money), two idle turns, and a failed
turn return as today. The router never writes a verdict.

`status` returns the fold: `state` is now the machine state's name, plus
`legacy`, `return_to`, `plan`, `loops`, `counts`, `candidate`, `checks`,
`governance`, `delivered`, alongside today's money, turns, effects, and
attention. `scripts/replay.py` and `scripts/role_play_tom.py` change in the
same commit: `waiting` replaces `waiting for Tom`, `merge` replaces
`delivered`, the driver releases a held `merge` when its payload's URL is
the run's own origin (the URL the approval binds, not whatever the
workspace's remote config says now), and a `no runner` line ends the run with that outcome rather
than looping to the run cap. So no replay can finish between 1.2 and 1.4;
the takeover gate in 1.5 is where replays run the whole pipeline.

### 7. Guards: `core/guards.py` (new)

**Seeded.** `db.migrate` seeds these on the `guards` stream, once each (the
unique index and an advisory lock make a second migrate a no-op, like
correction 1). Each row carries the fields below, `note` quoting Tom's
2026-10-01 pipeline decision ("Every stage is a checkpoint ... the plan
sets the loops"), and provenance `by: tom`, `via: "the 2026-10-01 pipeline
decision, seeded by migrate"`, `role_played: false`. These rows are
permanent in the real ledger, so the text is taken from the docs, with the
source named in the row.

| `guard_id` | What it holds or redirects | Incident (as the docs give it) | Mission items | Granted | Expires | Source |
|---|---|---|---|---|---|---|
| `intake.underspecified` | routes a request judged thin to `clarify` | psyoptimal #894 (task `32f800bce8a2`: two feedback rounds for three decisions one message would have settled); popoto #191 bare (fidelity 1, 3 of 11 hidden tests); popoto #188 bare (built a feature Tom did not want) | 3, 6 | 2026-10-01 | 2026-12-30 | sdlc-state-machine.md, `judge`, Guard record; judgement-layer.md, The guard entry; mission.md, How the first numbers are read |
| `checks.test.breadth` | a green suite with untested behaviors is `gaps`, which sends work to `patch` | every replay wrote fewer tests than its reference: #872 missed the archived-team guards, #191 the list key name and hash exclusion, the demonstration 10 tests against the reference's 35; on popoto #633 the clarify arm broke a bound in an existing test and was accepted | 1 | 2026-10-01 | 2026-12-30 | sdlc-state-machine.md, `checks.test`, Why and Guard record for breadth |
| `critique.loop` | a `revise` sends the plan back to `plan` | popoto #633: the clarify arm built on a wrong premise nothing read before code existed, and correctness fell from 5 to 2 | 1 | 2026-10-01 | 2026-12-30 | sdlc-state-machine.md, `critique`, Why |
| `review.loop` | the review checkpoint: a join sends work to `patch` on review `changes` (row 3) or in the repair round (row 5); the round count is Tom's 2026-10-01 pipeline decision | popoto #633: the stale-cache bug was moved, not removed, and a lenient Sonnet stand-in accepted it (rebuild-baseline.md, Review rounds). The second round has no incident of its own and falls to expiry on 2026-12-30 unless one occurs | 1 | 2026-10-01 | 2026-12-30 | sdlc-state-machine.md, `checks.review`, Why; rebuild-baseline.md, Review rounds |

The repair round is not a guard of its own: it fires under `review.loop`
(the driving session's decision, reversible). The single review per
candidate is a constraint of the setup plan with no expiry and is not
seeded.

**Firing.** A verdict row that holds or redirects work names the guard it
fires under: `judge.decided` `thin`; `critique.decided` `revise` that goes
back to `plan`; `test.decided` with listed behaviors; and the check verdict
that completes join row 3 or 5. Deleting an expired guard is a routine for
milestone 4; 1.2 only records.

**Granting an instance.** `python -m core grant TASK INSTANCE --note "..."
[--incident T] [--mission-item N] [--via V]` is Tom's tap on one
governance instance. It has no `--by` and no `--role-played`: a governance
grant is Tom's, written `by: tom`, `role_played: false`, and a stand-in
has no way to make one. It writes `guard.granted` on the task, bound to the
instance id, expiring ninety days out. Incident and mission item default
to what the verdict's instance named; a grant missing either is refused,
which is the governance paragraph's own rule ("missing either, it is not
added"), not a new one.

**Tom's tap is required and sufficient** (driving session's decision,
reversible, flagged for Tom): a governance instance merges only with its
own `guard.granted`, and the Brief's `governance_grant` neither substitutes
for that tap nor is needed beside it. The broker's current refusal ("the
Brief carries no `governance_grant`") becomes "an instance has no grant",
and the `test_kernel.py` assertion that names `governance_grant` changes
with it. The build amends predicate term 2 in
`docs/sdlc-state-machine.md` to say this. The governance paragraph itself
is not edited anywhere; it stays verbatim.

### 8. The broker: the governance flag, the merge, and one transaction

- `Action` loses `adds_governance`. `broker.request` computes it: for a
  `merge` action, true when the review or docs verdict for the candidate
  the payload names answered its governance boolean yes; for every other
  action, false. A merge that adds governance with any instance lacking a
  `guard.granted` is refused at request. `effect.held` keeps recording
  `adds_governance`, now as the broker computed it.
- **The merge destination is the kernel's, not the turn's.** At start, the
  kernel resolves `origin`'s push URL in the workspace (before any turn
  has run), as an absolute path (`get-url` returns a relative path as it
  was written), and records it as `origin_url` on the Brief and
  `task.started`.
  The merge payload carries `url`, `target_branch`, `head_sha`, and the
  candidate, so Tom's approval digest binds all of them. The performer
  pushes `head_sha` to `refs/heads/<target_branch>` at that URL, never with
  force. It reads the workspace's effective config with `git config --list
  --show-scope --includes` and refuses any `include.*` or `includeIf.*`,
  any `url.*` rule, and any `remote.*.pushurl` from the local or worktree
  scope: a rewrite applies even to an explicit URL, and an include can
  bring one in from a file `--get-regexp` on the local file never reads.
  Tom's global rewrites are his and are not refused. The performer's
  refusal is checked at request and again at release before the intent,
  so a refused release writes nothing and leaves the approval unused. `push_branch` does the same (recorded URL, same
  refusal) and refuses the task's `target_branch` as its target, so the
  only way onto the target branch is the merge and its predicate.
- **`target_branch`** is `--target-branch` at start, defaulting to
  `origin`'s `HEAD` symbolic ref as `git ls-remote --symref` reports it;
  when that cannot be resolved (an origin made with `git init --bare` on a
  machine with no `init.defaultBranch` has an unborn `master` HEAD, and
  `ls-remote` prints nothing), start refuses and asks for the flag.
  `scripts/replay_workspace.py` and `scripts/demo_workspace.sh` now set
  their origin's `HEAD` to `main`, and `scripts/replay.py` passes
  `--target-branch main` explicitly.
  Start also refuses a workspace on a detached `HEAD`. `base_sha` is the
  workspace's `HEAD` at start (1.4's test branch runs the suite there).
- **`release` in one transaction.** Today the approval is read in one
  transaction and the intent written in another. `release` becomes: take
  the task lock; read the held effect, any prior outcome, the stop fence,
  and the unused approval; for a `merge`, read the git facts (ancestry,
  merge commits, `git diff --no-renames --name-only` from the candidate to
  the head) and evaluate `merge_predicate`; write `effect.intent`; commit.
  The performer runs after the commit and the outcome lands in its own
  transaction, which keeps intent before outcome. A failing predicate
  raises `MergeRefused` naming the terms, writes nothing, and leaves the
  approval unused. No feedback or new candidate can land between the
  check and the intent, and feedback is refused while the merge's intent
  has no outcome.

The merge performer is `tools/push_branch.py`'s push with action type
`merge` and no `usage` line (turns are not offered it).

### 9. Stage files: `skills/sdlc/`

Plain files, no versioning: `clarify.md`, `plan.md`, `critique.md`,
`build.md`, `review.md`, `docs.md`, `patch.md`, each stating the stage's
goal and exit evidence (the signal file and the row it becomes), not
steps, and `channel.md`, the `.valor/` convention. `tasks.dispatch`
renders, after the Brief head and corrections: `channel.md` with the
effects list generated from the registered performers that offer a
`usage` line, then the file for the current state. The plan's path and
commit go in the Brief head once one exists. `critique.md`, `review.md`,
and `docs.md` are written now so there is one file per stage; 1.4's fresh
sessions render them. `judge` is a classifier prompt (judgement-layer.md,
1.3) and `merge` is the kernel's, so neither has a file. `skills/README.md`
says the directory holds these plain files until the versioned system is
designed.

### 10. Shared pinned git: `core/git.py` (new)

The pinned runner moves from `tools/push_branch.py` to `core/git.py`
(`head`, `branch`, `show`, `is_ancestor`, `merges_between`, `diff_paths`,
`clean`, `push_url`, `rewrite_rules`), and the performers import it. The
kernel reads a workspace the turn can write, so every read pins hooks,
fsmonitor, credential helper, and SSH command the same way.

### 11. Hypothesis

Added to the dev group in `pyproject.toml`, with `uv.lock` regenerated and
committed beside it. The build, test, and review stages run the suite from
this worktree's own venv, `~/src/valor-rebuild-m12/.venv` (made by `uv sync`
here), so the kernel checkout's venv is not touched:

    cd ~/src/valor-rebuild-m12 && VALOR_TEST_DB=valor_rebuild_test_m12 \
        .venv/bin/python -m pytest -q tests

The property test imports Hypothesis directly; it is not skipped when the
package is missing.

## Tech debt paid

| Debt | What pays it |
|---|---|
| `adds_governance` settable by any requester and set by none | computed by the broker from verdict rows (item 8) |
| an approved push's destination is under the turn's control | the recorded URL, bound into the digest, and the rewrite refusal (item 8) |
| `release` checks and writes the intent in two transactions | one transaction under the task lock (item 8) |
| `CLARIFY` tells Valor to write `question.md` with no question | `clarify.md` and `no_question.md` give `no_material_question` its own evidence |
| `PROTOCOL` hard-codes `push_branch` | the effects list is rendered from registered performers |
| `done.md` writes `task.delivered` at once | it is a candidate; `task.delivered` comes from the join |
| prompt text in `core/session.py` and `core/signals.py` | moved to `skills/sdlc/` |
| `tasks.status`, `session.open_question`, and `session.next_prompt` each fold the ledger their own way | one fold, `machine.fold`, read by all three |
| the pinned git runner lives in one performer only | `core/git.py`, shared by the kernel's reads and the performers |
| `test_the_clarify_mode_is_recorded_and_carried_in_the_brief...` encodes the Brief section 1.2 deletes | replaced by the judge row and stage rendering tests |

## Tests that show it works

New `tests/test_machine.py` (pure fold, no database) and
`tests/test_pipeline.py` (real Postgres, real git, real bare origins,
scripted turns as in `test_session.py`). All spend $0 except the two
`VALOR_LIVE` files.

**The model and the join.**
- Every row of the join table, parametrized, through the pure fold; and
  one row (`changes` with a round left) end to end through the router and
  the `verdict` CLI as a subprocess.
- Precedence: review `changes` with a round left and docs answering
  governance yes goes to `patch` (row 3, since row 2 reads review only);
  review `governance_refused` with test `red` goes to `merge` (row 2
  before row 5).
- A verdict outside its enum is refused at write: a raw `INSERT` as
  `valor_kernel` of each verdict type with a bad value raises
  `CheckViolation`; each value in `VERDICTS` is accepted; a
  `turn.collected` with a `state` and no verdict is refused; plan counts of
  3 and a critique raise of 3 are refused. Migrating twice leaves one
  constraint; replacing it in a scratch database with a narrower enum
  succeeds over history holding the removed value, and a new row with that
  value is refused.
- A second `judge.decided` and a second `turn.collected` for one turn are
  refused by their unique indexes.

**Loops.**
- `critique_rounds` 0, 1, 2 against a run of `revise` verdicts: the
  number of returns to `plan`, and the findings carried into the build's
  first prompt once exhausted. A `revise` raising 0 to 1 is itself sent
  back; a revised plan stating 0 after a raise to 2 still gets the second
  revision; a "raise" to 0 lowers nothing.
- A critique of an older plan digest is ignored.
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
- A `done.md` with uncommitted changes is not a candidate; the next prompt
  names what is uncommitted.
- A `turn.collected` whose state the task has left is ignored.
- `governance_granted` reruns only review: after the grant, test and docs
  verdicts stand, the router reports only `review` missing, and the next
  review verdict completes the join. A grant of one of two instances stays
  in `merge`. After a patch that leaves the granted hunk unchanged, the
  instance id is the same and stays granted; a changed hunk needs a new
  tap.

**The merge predicate and the destination.**
- All five terms hold: release pushes the head to the target branch of the
  recorded bare origin.
- Each term failing alone refuses the release with that term named,
  nothing written, and the approval still unused: no docs verdict; review
  `pass` with an ungranted instance (built by raw insert, since the writer
  refuses it); test `red`; `gaps` with the repair round unspent; docs
  paths touching `core/x.py`, a stage file, or a `CLAUDE.md`; a merge commit between the candidate and the
  docs head; a rename from `core/x.py` to `docs/x.md` (the old path counts);
  docs head not the payload's head; no approval.
- An approval for a different digest: the merge effect of candidate 1 is
  approved, feedback produces candidate 2 and its merge effect; candidate
  2's effect does not release on candidate 1's approval (term 5), and
  candidate 1's effect does not release although approved (term 1).
- The destination: after the merge is held, the turn sets `origin`'s URL,
  a `pushurl`, a `url.*.pushInsteadOf` rule, or an `include.path` naming a
  file that holds such a rule; the release refuses (writing nothing, the
  approval unused) or pushes only to the recorded URL, and the stranger
  repository receives nothing. `push_branch` to the target branch
  is refused. An effect file with action type `merge` is recorded with its
  error and never reaches the broker.
- Start: the target branch defaults to `origin`'s `HEAD` (in a workspace
  on a work branch, it is `main`, not the work branch); an origin whose
  `HEAD` is unborn needs the flag; a detached `HEAD` is refused; an origin
  made by exactly the commands `scripts/replay_workspace.py` runs resolves
  `main`; a relative remote URL is recorded absolute.
- Instance ids: the same added lines in two functions are two ids; the same
  hunk with its line numbers shifted keeps its ids; a review naming a line
  with no added lines is refused; a `governance_refused` naming only
  granted instances is refused.
- The replay driver leaves a held merge whose payload names another URL.
- Repeated runs while an instance awaits a tap add no `effect.refused`;
  after the grant the next run requests the merge.
- The governance flag: an effect file carrying `adds_governance` gets an
  effect recorded with `false`; a merge whose review named an ungranted
  instance is refused at request; a task whose Brief carries a
  `governance_grant` still needs the tap.
- A crash between `task.delivered` and the merge request (the writer is
  stopped after its transaction): the next `run` requests the merge, and a
  second `run` returns the same effect.
- Feedback is refused while the merge's intent has no outcome.

**States and Tom's rows.**
- A stopped task in every state (each of the eleven reached by its own
  prefix, then stopped): the fold says `stopped`; `run` returns without a
  turn; `answer`, `feedback`, `verdict`, `grant`, and the raise command (since removed) are
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
- `verdict` refuses a stage the runner mapping covers (a test mapping with
  a critique runner), and refuses a stage the task is not in.
- `grant` has no `--by` or `--role-played`; its row says `by: tom`,
  `role_played: false`; a grant without an incident is refused.
- Two `run` processes on one task: the second returns `already running`
  and starts no turn; a run whose lock connection is terminated returns
  `lock lost` and runs no turn.
- Stage rendering: the dispatched Brief carries `channel.md` and the
  current state's file, changes when the state does, and lists every
  registered performer offering a `usage` line and not `merge`.

**Totality, by property.** Hypothesis generates ledgers of well-formed
rows, rows with wrong or missing fields, unknown types, stale verdicts,
turns collected in a state already left, and legacy-shaped starts. For
every prefix: `fold` returns exactly one `State` and does not raise;
`stopped` is absorbing; `checks` holds verdicts for the current candidate
only; patches before a `merge` never exceed `review_rounds` plus one.

**Legacy.** A synthetic ledger holding every legacy shape, including one
where a delivery and an unanswered question coexist (the old precedence
says `delivered`), folds to the old oracle's mapping. A synthetic legacy
task with a held `push_branch` is approved and released. In
`test_migrate_history.py`, every task document of the copy of the real
ledger folds without raising, is `legacy`, and matches the oracle (skipped
where the machine has no `valor_rebuild`). The router, `answer`, and
`feedback` refuse a legacy task.

**Guards.** A fresh migrate holds the four seeded guards once each, with
the incident, mission items, grant date, and expiry of the table above; a
second migrate adds none.

**Kept green.** `test_replay.py`, `test_kernel.py` (its governance tests
now build the flag from a review verdict, and the grant assertion names
the missing tap), `test_attention.py` (with `verdict` and `grant` entries
counted apart), `test_session.py` (rewritten for states),
`test_migrate_history.py` (the new constraint and indexes on the copy, no
row or file node changed). `test_live_session.py` drives a real `claude -p`
task from `start --mode bare` through plan, manual critique and checks,
merge, and release; `test_live_turn.py` drops its `adds_governance`
request. Both stay behind `VALOR_LIVE=1` and are run once at build within
their declared spend.

## Docs the build makes true

`docs/sdlc-state-machine.md` (What exists; the `VERDICTS` change; the
manual leg and when it closes; predicate term 2; the docs-commit
limitation; stage file names), `docs/data.md` (event types, the new
indexes and the constraint, the fold section, legacy shapes),
`docs/architecture.md` (state; flow; the merge destination; failure table
rows that say design), `docs/harnesses.md` (signal files, prompts, skill
rendering), `docs/judgement-layer.md` and `docs/persona.md` (`CLARIFY` and
`PROTOCOL` references), `docs/tech-stack.md` (Hypothesis in use),
`core/README.md`, `skills/README.md`, `tools/README.md`, `tests/README.md`
if its index names files. The governance paragraph is not edited in any of
them.

## Out of scope

- The judgement port and every real judge, breadth, or governance call
  (1.3); `--mode` and the `judge` stage of `verdict` are deleted there.
- Fresh-session critique, review, and docs runners, the suite runner,
  dropping docs commits outside doc paths, the docs checkout, workspace
  provisioning, the container verifier, the GitHub credential (1.4); the
  `verdict` command is deleted there.
- The emulator and the takeover gate (1.5). No replay finishes until 1.4.
- The resident process and the supervisor (milestone 2); bridges and the
  tap cards (milestone 2).
- The versioned skill system; deleting expired guards (a milestone 4
  routine); any listing of guards beyond `python -m core ledger guards`
  (no governance dashboard).
- Concurrent branches in `checks`: the router runs them one at a time,
  which the doc allows with identical semantics.

## Build record

Built on `m1.2-state-machine` from plan `2ff4e5102` and critique round 2's
findings (the eight fixes above: the include bypass, the replay origin's
`HEAD`, the constraint digest and `NOT VALID`, the lock check, instance ids
from the real diff with function context and per-task uniqueness, the
`governance_refused` write rule, the driver's merge URL check, and no
merge requests while a tap is awaited). Settled while building:

- A performer's `refuse` runs at release too, before the intent (the
  rewrite refusal included), so a refused release consumes nothing.
- A new candidate clears the fold's merge effect, so a candidate after
  feedback gets its own merge request.
- The `verdict` command's suite option is `--suite-command`, since
  `--command` collided with the subcommand.
- Critique raises hold for the task, not only the loop window.
- The judge's manual verdict at start counts as a `verdict` attention
  entry, like any manual verdict.
- `ruff format --check .` reports a Python block in
  `docs/bridges/telegram.md` on the base commit as well; it is not this
  build's and is left alone. Code paths are clean.

Evidence: `cd ~/src/valor-rebuild-m12 && VALOR_TEST_DB=valor_rebuild_test_m12
.venv/bin/python -m pytest -q tests`: 211 passed, 3 skipped (the live
tests). With `VALOR_LIVE=1`, `test_live_turn.py` and `test_live_session.py`
passed once, metering about $0.17 together.

## Patch round 1 (review round 1 of 2)

On top of the docs session's `7a12ac817`. Every finding resolved:

- **R1, the kernel ran turn-chosen programs.** `git status` ran a filter
  clean driver the workspace's config named for paths a committed
  `.gitattributes` assigned, and `git diff` ran a textconv driver. Chosen:
  neutralise and refuse, not a kernel-owned clone, because whether the
  candidate's tree is clean is a question only the work tree answers, so
  the workspace must be read in place. `core/git.py` now runs every call
  with no global or system config and no inherited `GIT_*` variable, pins
  hooks, fsmonitor, credential helper, SSH and proxy commands, askpass,
  the attributes file, pager, automatic gc, and the `ext::` transport off,
  passes `--no-textconv --no-ext-diff` to every diff and ignores
  submodules in `status`, reads files through `cat-file blob`, and before
  any call refuses a workspace whose local or worktree config (includes
  followed) holds any key that can name a program, redirect a push, or
  pull config in (`git.hostile`). A refused workspace gets no candidate, no
  instance, no git facts, no start, and no push; the reason reaches the
  turn's next prompt or the caller. Release checks the performer's refusal
  before the predicate, so the reason names the config.
- **R2, doc paths came from the turn's plan.** `doc_paths` is gone from
  `plan.json`, `plan.written`, the plan stage file, and the predicate. A
  doc path is a Markdown file that instructs no turn: never a `CLAUDE.md`
  or `AGENTS.md` anywhere, nothing under `skills/`, `persona/`, or
  `.claude/` (`machine.is_doc_path`). The contract doc and the docs stage
  file say so.
- **R3, fold totality.** A `task.started` without a payload folds as
  legacy; a critique's raises are validated whole before either applies,
  and a raise that is not an integer 0 to 2 makes the row ignored. The
  Hypothesis strategy now generates rows with no payload, start rows of
  every shape, and mixed valid and invalid raises, and asserts that a row
  the fold ignored changed nothing that decides anything.
- **R4 and the test gaps T1 to T11** are tests in `tests/test_pipeline.py`
  and `tests/test_machine.py`: the planted filter, textconv, fsmonitor,
  and include (with a control showing plain git runs them) across done.md
  collection, instance computation, the predicate's git facts, release,
  and start; doc paths over code, stage files, `CLAUDE.md`, `AGENTS.md`,
  `persona/`, `.claude/`; a join-row-7 delivery released; a docs instance
  holding the merge until the grant; a docs head not descending; a test
  verdict with governance; grant refusals; legacy refusals including the
  CLI; review and repair rounds together; a failed merge outcome and the
  next run's new request; a held merge for another candidate; the missing
  judge and the merged status; the start command's manual judge; a check
  runner run for the missing branch only. Beyond the findings: feedback
  racing a release, two releases racing, and a plain `insteadOf` rewrite.
- **T12** is in `docs/plans/valor-rebuild.md`, 1.4 Done.
- **D1**, the Brief's docstring, says what the broker does.

Evidence: 233 passed, 3 skipped (from 211); ruff clean on the code.

## Delivery 1 (did not pass)

Review round 2, the last the plan allows, on `1f0ffebb2` (docs at
`1ec5988d6` on `m1.2-docs`): review `changes`, test `gaps`, docs `updated`.
With both review rounds spent, the join sends the task to Tom as a
delivery that did not pass (join row 4).

Review findings, blocking:

- **B1.** `machine.is_doc_path` compared case-sensitively; this Mac's file
  system does not, so `claude.md` or `Skills/build.md` passed as a doc and
  is read as `CLAUDE.md` or `skills/` on a clone. `CLAUDE.local.md` and
  `AGENTS.override.md` were missing from the instruction files.
- **B2.** A workspace-local `push.followTags=true` made the kernel's push
  also send a tag the turn made, which Tom's approval digest does not bind.
- **B3.** The kernel ran `git` by name on Tom's PATH, where
  `~/.local/bin` comes before `/usr/bin` and the sandbox does not deny
  writes to it, so a turn could plant a `git` the kernel ran outside the
  sandbox.

Non-blocking: N4 `hostile` ignored the config listing's return code; N5
keys missing from the refusal list (`core.alternateRefsCommand`, `*.cmd`,
`interactive.diffFilter`, `hook.*`, `extensions.partialClone`); N6 a
dangling merge intent stayed in flight forever while the docs said it was
reconciled; N9 a malformed `turn.ended` half-applied; N10 the plan named
`pipeline.ensure_merge`.

Test gaps: T-a a grant in checks or patch refused; T-b an unknown mode
refused; T-c inherited `GIT_*` variables dropped; T-d worktree-scope config
refused; T-e the feedback and release race asserting which interleaving
happened; T-f a boolean raise rejected.

## Proposed patch, awaiting Tom's feedback

Prepared while Tom is away; not authorised by the pipeline, which has
spent its review rounds. It is a candidate for his decision: his feedback
on the delivery is what would send it through the checks.

- **B1.** Doc paths compare every part casefolded; the instruction files
  are `CLAUDE.md`, `CLAUDE.local.md`, `AGENTS.md`, `AGENTS.override.md`,
  the directories `skills/`, `persona/`, `.claude/`. The contract doc and
  the docs stage file say so. Tests: every case variant, pure and through
  the merge predicate.
- **B2.** Every kernel git call pins `push.followTags=false`,
  `push.recurseSubmodules=no`, `push.gpgSign=false`; the push passes
  `--no-follow-tags --no-recurse-submodules --no-signed`; any `push.*` key
  in the workspace's own config is refused. Test: a turn-made annotated tag
  is not pushed, and `push.followTags` refuses the release.
- **B3.** The kernel runs git by absolute path (`settings.git_bin`,
  default `/usr/bin/git`, `VALOR_GIT` overrides) with a PATH of system
  directories only. `docs/harnesses.md`, Known openings, names the user's
  writable `~/.local/bin`, `~/Library/LaunchAgents`, and shell rc files.
  Test: a `git` planted first on PATH is not run.
- **N4.** Config that cannot be read is refused.
- **N5.** The refusal list gains `push.*`, `hook.*`,
  `core.alternateRefsCommand`, `interactive.diffFilter`,
  `extensions.partialClone`, and `*.cmd`; a parametrized test sets one
  key per entry of the whole list and checks the workspace is refused.
- **N6.** Reconciled, as the docs said: a process performing an effect
  holds a session lock on it from intent to outcome; `broker.reconcile`
  settles an intent whose lock is free through the performer's `lookup`
  (`done` or `failed`, marked `reconciled`), and the router runs it for a
  task's merge on the next run. Tests: a merge that landed before the crash
  becomes `merged`; one that did not is `failed` and a new merge is held;
  a live performer's lock leaves the intent alone.
- **N9.** `turn.ended` reads its turn id before changing anything, and a
  non-object result is ignored whole.
- **N10.** The plan names `core/verdicts.py::ensure_merge`.
- **T-a to T-f.** A grant in patch refused (checks was covered); an
  unknown mode; `git.env()` drops `GIT_DIR`, `GIT_CONFIG_PARAMETERS`,
  `GIT_EXTERNAL_DIFF` and the calls still work with no marker; worktree
  scope refused; the race forced in each order and raced, naming the
  interleaving; a boolean raise rejected.

Evidence: 303 passed, 3 skipped (from 233); ruff clean on the code.

## Proposed patch 2, awaiting Tom's feedback

The checks on the first proposed patch (`c20c55c1c`): review `changes`,
test `gaps` (303 passed), docs `updated` (`50594dc94`). This second and
last proposed patch answers them; after it the iteration stops and Tom
decides. Like the first, it is not authorised by the pipeline.

- **P1, the git binary.** `/usr/bin/git` is Apple's `xcrun` shim, which
  finds the real git through a per-user cache a turn can write; the
  reviewer made it run a planted script. The kernel now runs the Command
  Line Tools' git (`/Library/Developer/CommandLineTools/usr/bin/git`),
  chosen because it and every directory above it are root's and writable
  by no one else; Xcode's copy sits under `/Applications`, which the admin
  group (Tom, and any turn running as him) can write, and Homebrew's prefix
  is Tom's. `core/binaries.py` checks before every call that the binary,
  its `libexec/git-core`, and every directory above each are root-owned
  and not group- or other-writable, and refuses a git with no exec path
  beside it, which is what the shim is; `VALOR_GIT` overrides and is held
  to the same check. The `xcrun` variables are dropped from git's
  environment, and every call has a time limit (`git_timeout_s`). Test: a
  scratch copy of the cache (named by `xcrun_db`; the real one is never
  touched) poisoned to name a planted script makes the shim run it, and
  the kernel's git calls do not.
- **P1, `claude`, and two more found the same way.** The kernel launches a
  workspace turn as `sandbox-exec ... claude`; `sandbox-exec` was looked
  up by name on the turn's PATH, where `~/.local/bin` comes first, so a
  planted one would have run unsandboxed. It is now `/usr/bin/sandbox-exec`
  by absolute path, checked the same way. `claude` itself runs inside the
  sandbox, so a replaced one has the turn's own reach and no more; this is
  written in `docs/harnesses.md`, Known openings, with the one unsandboxed
  builder (`claude_code.turn`, tests only, never built by the router).
  The reaper ran `ps` by name on Tom's PATH outside any sandbox; it is now
  `/bin/ps`, checked. Homebrew's Postgres tools, run by the launchd backup,
  are named as an opening.
- **P2, reconcile.** A performer's `lookup` answers present, absent, or
  unknown. Any failure to read the remote is unknown, and `reconcile`
  writes nothing, so the merge stays in flight. Present means the target
  branch holds the commit at its tip or below it: the tip is fetched into
  the kernel's own ref (`refs/valor-kernel/lookup`) and ancestry read from
  what the remote sent, because a remote's tip alone cannot show ancestry.
  Absent is written `failed` only once the intent is older than
  `reconcile_after_s` (default 240 s, twice the git time limit), so a
  performer whose database connection dropped, freeing its lock while its
  push still runs, is not overtaken; if it finishes after a reconcile, its
  outcome yields to the recorded one. Tests for the three reproduced cases
  (unreachable remote, a branch moved on after the landing, a young intent)
  and a landing reconciled as `done`.
- **N-a.** The docs name only the merge as reconciled automatically;
  other dangling intents stay listed by `tasks.audit`.
- **N-b.** `http.*` is refused; the refusal message says every `push.*`
  and `http.*` key is refused.
- **N-c.** A failing unlock in a `finally` never hides the original error.
- **G1 to G4.** Lookup unknown and unreachable (the P2 tests); an
  independent table of required refused keys, so deleting a list entry
  fails a test; a failing `push_branch` frees its effect lock, and
  `reconcile` with no performer registered does nothing; `VALOR_GIT`
  naming a user-owned git is refused at the call and naming the trusted
  git is used.

Evidence: 337 passed, 3 skipped (from 303); ruff clean on the code. The
live tests were not rerun for this patch.

## Tom's feedback (delegated decision, 2026-10-02)

Decided on Tom's behalf under his delegation, and recorded as such: apply
proposed patch 2, narrowed to the changes below. No third blind review
runs. The class of finding that remains, the kernel reading a repository a
turn owns, is to be removed structurally after the takeover, by delivering
work as a bundle the kernel reads in its own repository, not hardened
further here. After this patch: the suite and the docs check, then the
merge on Tom's tap.

What the patch changes:

- **History the turn owns.** Every kernel git call ignores replace refs and
  grafts (`GIT_NO_REPLACE_OBJECTS=1`, `GIT_GRAFT_FILE=/dev/null`; the
  commit carried from `m1.4-checks`, `1a1a6235d`, reused so the later
  rebase drops it) and the commit-graph and multi-pack-index files
  (`core.commitGraph=false`, `core.multiPackIndex=false`). Tests: a replace
  ref, a graft, and a planted commit-graph each fool plain git and change
  neither `diff_paths` nor ancestry for the kernel.
- **One deadline per perform.** A performer sets one deadline,
  `git_timeout_s`, for all of its git calls together (`git.deadline`); git
  runs in its own process group, killed whole when it outlives its time.
  `reconcile_after_s` is checked at settings load to be at least twice
  `git_timeout_s`, so reconcile never reads a merge as missing while a
  perform could still be pushing it.
- **`DYLD_*`** variables are dropped from git's environment.
- **Tests** that `binaries.require` refuses a `ps` and a `sandbox-exec`
  that are not root's alone, and that an outcome `reconcile` wrote first
  stands over the performer's (the unique-violation path of a release).
- **`review.loop`**, as seeded: the review checkpoint, its incident popoto
  #633 (the stale-cache bug moved, not removed, and accepted by a lenient
  Sonnet stand-in; rebuild-baseline.md, Review rounds); the round count is
  Tom's 2026-10-01 pipeline decision; the second round has no incident of
  its own and falls to expiry on 2026-12-30 unless one occurs. The guard
  table above says the same.

## Rollout at merge

Not done by the builder. At merge, in the kernel checkout: `uv sync` (adds
Hypothesis to the dev group), then `python -m core migrate` on the real
ledger, which adds the constraint and indexes (no row rewritten, shown on
the copy) and seeds the four guards of the table, permanently.

## Questions for Tom (assumed answers; the build proceeds on them)

1. **A manual verdict command until 1.4.** Agreed by the driving session:
   yes, closing stage by stage as runners land.
2. **The Brief's `governance_grant` and per-instance taps.** Decided by the
   driving session, flagged for Tom: the tap is required and sufficient;
   the Brief's field does not substitute.
3. **The repair round.** Decided by the driving session: it fires under
   `review.loop`, not as its own guard.
4. **`answered` after `clarify` returns to `clarify`**, per the doc, which
   costs one short resumed turn before `plan`. Agreed: follow the doc.
5. **`review.loop`'s incident.** The doc gives no incident specific to a
   second review round; the row cites the one the doc places under review
   (the lenient stand-in on #191 and #633). Assumed right; since the row is
   permanent, Tom may want different text before rollout.

## Decided by default (reversible)

- Row and signal names: `judge.decided`, `plan.written`,
  `critique.decided`, `guard.granted`; `.valor/plan.json`,
  `.valor/no_question.md`.
- `idle` and `failed` in every working-session state's enum.
- Signals a failed turn left still count, as today.
- The legacy marker `sdlc: 1` on `task.started`, and the legacy mapping.
- Enum enforcement as one generated `CHECK` constraint rather than code
  alone.
- The join follows the spec's table literally; docs governance goes to Tom
  as an instance awaiting a tap in `merge`, not through the join.
- A grant binds to the instance id, so an unchanged hunk stays granted
  across patches.
- No merge effect is requested for a delivery that did not pass.
- `--target-branch` defaults to `origin`'s `HEAD`.
- `start` without `--mode` waits in `judge` (scripts pass `--mode`).
- Checks branches run one at a time.

## Checks on proposed patch 2, and the recommendation to Tom

Candidate `be1388e3e` plus the docs check's commit `b7879fe66`. Iteration
stopped here, by the driving session's rule, for Tom's decision.

- **Test:** `gaps`. 337 passed, 3 skipped; no regressions. The live tests
  ran once under `/usr/bin/sandbox-exec` and passed, spending $0.1748.
- **Review:** `changes`, governance no. The six findings on proposed patch 1
  are resolved, including a root-owned Command Line Tools git, a
  three-valued lookup, and an HTTPS push to GitHub that still works with a
  pinned header.
- **Docs:** `updated`.

Open, found independently by test and review:

1. Blocking. The turn owns `.git/refs/replace/` and `.git/info/grafts`,
   which are not config, so the refusal list cannot see them. A replace ref
   made `diff_paths` report a code commit as docs only, and made `holds`
   report a merge as landed when it never was. Fix: `GIT_NO_REPLACE_OBJECTS=1`
   and `GIT_GRAFT_FILE=/dev/null` in `core/git.py`'s `env()` (or
   `core.useReplaceRefs=false`), consider `objects/info/commit-graph`, and a
   test for each.
2. Non-blocking: one deadline for a whole perform, with git in its own
   process group killed on timeout; drop `DYLD_*` from git's environment;
   validate `reconcile_after_s` against `git_timeout_s`; refusal tests for
   `binaries.require` on `ps` and `sandbox-exec`; a test of the
   `UniqueViolation` path in `_release`.

**Recommendation:** give feedback that applies fix 1 and the non-blocking
items as one patch, then run test, review, and docs once more. Review found
a new, smaller class of hole each round (git config programs, then the xcrun
shim and PATH binaries, then replace refs), each closed when found; the
kernel's git reads of a workspace a turn controls are where they cluster.

## Merged

Merged 2026-10-02 at `2b9885e27` (inside the fast-forward to `a30c03350`)
on Tom's tap. Rollout done the same day: `uv sync`, then `python -m core
migrate` on the real ledger seeded the four guards (`intake.underspecified`,
`checks.test.breadth`, `critique.loop`, `review.loop`, each expiring
2026-12-30) and added the verdict constraint; the 1,416 earlier rows were
untouched.
