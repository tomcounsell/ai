---
tracking: none
slug: i3612-merge-outcomes
type: plan
status: merged
critique_rounds: 2
review_rounds: 2
---

# What happened to merged work

GitHub issue #3612 (`tomcounsell/ai`), issue 4 of
[critique-issues-out.md](critique-issues-out.md). Every citation below is to
`valor-cori-rebuild` at 2d6ed8a8e, re-read for this plan; the issue cited
5c11e496d.

**Goal.** For each delivery a task made, and each merge it made,
`python -m core status` and the status page show what came after it: Tom's
feedback after the merge, later merges that touched the same paths,
whether it was reverted, and whether someone used it. A merge's paths are
recorded in the ledger when the merge is released, so rework is a ledger
fold; git is read only for whether the merge is still on its branch and
whether it was reverted. All of it is shown beside spending and attention.
Nothing reads it to decide anything.

**Serves.** Evidence "Working results in real use" (`docs/mission.md:130-131`,
read per delivery at `docs/mission.md:146-150`: "did it run, did someone use
it, and was each defect found in use resolved without Tom coordinating ...
No delivery so far has been used ... so this evidence is empty"), and
Mission item 1, "testing actual use ... and resolving discovered defects"
(`docs/mission.md:23-26`). The issue also names Mission item 4
(`docs/mission.md:34-36`, less supervision needs a result to read).

## What exists now

- A task's record of its merge ends at the merge effect's `effect.outcome`
  with `kind: done`; the fold enters `merged` there
  (`core/machine.py:586-588`). `merged` takes only `feedback`, which goes to
  `patch` (`docs/sdlc-state-machine.md:61`, `core/machine.py:59,81`), so one
  task can merge more than once.
- The merge is a push without force of `head_sha` onto the target branch,
  never a merge commit (`tools/push_branch.py:167-189`, `core/git.py:621-641`;
  the predicate's term 4 refuses merge commits, `core/machine.py:700-712`).
  Other tasks merge to the same branch (`projects/valor.toml` `merge_url`),
  so a candidate provisioned before another task's merge lands only after
  taking that merge in. Its payload carries `url`, `target_branch`,
  `head_sha`, and the candidate (`core/verdicts.py:509-522`). The Brief
  carries `base_sha`, `mirror`, `origin_url`, `target_branch`, and, for a
  provisioned task, `project` (`core/tasks.py:27-79`,
  `core/workspace.py:973-996`).
- `broker._release` evaluates the merge predicate inside the transaction
  that writes the effect's `effect.intent`, reading git facts from the
  mirror (or a `--workspace` task's workspace, through `git.run`'s hostile
  check) in `_git_facts` (`core/broker.py:509-557`). The intent row carries
  the action whole so `reconcile` can settle a killed perform from it
  (`core/broker.py:410-479,582-591`); both the perform and the reconcile
  path then write `effect.outcome`.
- A delivery is a `task.delivered` row with the candidate and the join's
  outcome (`docs/data.md:132`), written whether or not a merge follows.
  Work can reach use with no kernel merge: task 6fd4e0439ac1's issues were
  posted as #3609 to #3612, and task 80e49c02eac7 landed through a hand
  merge commit, 6afd8706f.
- Feedback after a merge is a `feedback.given` row with `on_delivery` and
  provenance (`core/session.py:652-706`, `docs/data.md:131`), folded into
  the attention log (`core/tasks.py:753-762`). Nothing folds it as an
  outcome of the merge it followed.
- `tasks.status` (`core/tasks.py:685-721`) reports the delivery and its
  `outcome`, the join's verdict before the merge. The issue's
  `core/tasks.py:638-647` is `child_report`, which reports the same field.
  `tasks.index` (`core/tasks.py:846-884`) reads every task's rows and
  returns state, spending, and attention, not the rows.
- The kernel's git: the per-task mirror `<work>/<task>/kernel.git`, which
  only the kernel writes (`core/workspace.py:12-13,938-941`); the task's
  bare `origin.git`, the merge's target when the spec has no `merge_url`
  (`core/workspace.py:9-11,991-993`), which only this task's merges reach;
  and the shared cache `<work>/cache/<name>-<digest>.git`, a bare clone
  fetching `+refs/heads/*` on every provisioning, named inside `_cache`
  (`core/workspace.py:693-730`).
- `machine.is_doc_path` (`core/machine.py:647-658`) says whether a path is
  a Markdown file a docs commit may touch; instruction files are not.
- The status page is read-only (`ui/app.py:1-11,186-189`); its task page
  prints `tasks.status` as JSON (`ui/app.py:97-115`) and its index lists
  state, spending, and attention (`ui/app.py:75-94`).
- No row records a use. `docs/data.md` is the row registry.

## Design

### What a merge landed, recorded at release (`core/broker.py`)

When `_release` has found the merge predicate holds, it computes `landed`
and writes it into the merge's `effect.intent` payload, in the same
transaction:

- **`before`**: the task's previous `done` merge's `head_sha`, or
  `Brief.base_sha` for its first.
- **`commits`** and **`paths`**: the merge's own commits and the paths they
  change, from one `git.trusted` call in the kernel mirror with the target's
  cache objects borrowed (`GIT_ALTERNATE_OBJECT_DIRECTORIES=<cache>/objects`,
  as `blind_checkout` borrows):
  `git log --no-merges --ignore-missing -z --format=%x00%H --name-only --no-renames before..head_sha --not <earlier> <tip>`,
  where `<earlier>` is every `head_sha` of a `done` merge by any task to the
  same `url` and `target_branch`, and `<tip>` is the cache's
  `refs/heads/<target_branch>`, the target as the kernel last fetched it.
  `--ignore-missing` skips a head the repository lacks; such a head is not
  reachable from this one, so dropping it changes nothing. This leaves out
  other tasks' work and hand-landed commits the candidate took in.
  `--no-renames` lists both paths of a rename. The output is read as bytes
  and each path decoded with `errors="replace"`, so a non-UTF-8 name is a
  reading, not an error.

Only the mirror is read: a `--workspace` task records
`landed: {"before": ..., "commits": null, "paths": null, "why": "no kernel copy"}`
and no git runs on its workspace for `landed`. The whole computation,
`<earlier>` from `outcomes.done_merges(conn, url, branch)` (below) and the
git read in one `git.threaded` call, sits in an `except Exception`: any
failure records null `commits` and `paths` with the exception's type under
`why`, and the release goes on. The intent is written with
`ledger.try_append`; when Postgres refuses it, it is written again with
`landed` cut to `before`, null `commits` and `paths`, and `why` naming the
refusal. Recording `landed` never stops or fails a merge.

It sits on the intent because the intent is the one row both paths share:
the perform writes the outcome from the push and `reconcile` writes it from
the target's `lookup`, which has no mirror history to compute from. The
commits are fixed before the push, and a merge counts only once its
outcome is `done`, so the paths are those of what landed. The push is
without force, so a concurrent merge by another task can only have landed
first if this head contains it; that merge's outcome row is then written
unless it is still in flight, in which case its paths may appear in this
one's too.

### The merges and deliveries of a task (`core/outcomes.py`, new)

`outcomes` imports `ledger`, `machine`, and `git`, never `tasks` or
`broker`, so both can import it.

`merges(rows)` is a pure fold over one task's rows. Each `effect.outcome`
with `kind: done` whose effect is an `effect.held` with `action_type:
merge` is one merge: `effect_id`, `head_sha`, `url`, `target_branch` (from
the held payload), `at` and `event_id` (the outcome row), and `landed`
(from the intent; None on a merge recorded before this change). A
`failed` outcome is not a merge.

`deliveries(rows)` lists each `task.delivered` row: its event id,
candidate, outcome, and time. A legacy or calibration task has none of
either.

For each merge, from the rows:

- **`feedback`**: every `feedback.given` row after this merge's outcome row
  and before the task's next delivery, with its text and provenance. Feedback
  given while the task was in `merge` belongs to no merge.
- **`used`**: every `delivery.used` row whose `effect_id` is this merge's.

For each delivery, **`used`**: every `delivery.used` row naming that
delivery with no `effect_id`. `used_count`, on both, counts the rows whose
`role_played` is not true; a role-played mark is listed and not counted,
since a stand-in using a feature is not real use (`docs/mission.md:148`).

`done_merges(conn, url=None, branch=None)` is one query: `effect.held` rows
with `payload->>'action_type' = 'merge'` joined by `effect_id` to their
`effect.outcome` with `kind: done` and their `effect.intent`, optionally
filtered by `url` and `target_branch`, returning task id, effect id, head,
url, branch, outcome time, and `landed`, in outcome order. Every
cross-task reading uses it, never a read of every ledger.

### Rework, from the ledger

`rework(merge, done)` takes one merge and the `done_merges` rows for its
`url` and `target_branch`. For every other task's merge whose outcome
comes after this one's, it splits the shared paths with
`machine.is_doc_path` into `shared_code` and `shared_docs` (finding 1):

- **`later`**: merges with a non-empty `shared_code`: task id, effect id,
  `merged_at`, `days_after` (one decimal), `shared_code`, `shared_docs`.
- **`later_docs`**: merges that share only doc paths, with the same
  fields, so they are shown and not read as rework.
- **`later_unknown`**: later merges whose `paths` are None, by task id, so
  an unknown never reads as "no rework".

When this merge's own `paths` are None, `later` and `later_docs` are None
with the reason "paths not recorded". The same task's own later merge is
the next entry in its `merges`, not repeated here.

### Revert and on-branch, read from git

These are the only readings that change after a merge, so they are the only
git reads on the status surfaces. `revert(brief, merge)`:

- A task whose `origin_url` is its own `origin.git` (the spec has no
  `merge_url`) gets `revert: None` and `later: None`, `later_docs: None`,
  each with the reason "private target": only this task's merges reach
  that origin, so no other reading exists (finding 5).
- A `--workspace` task gets `revert: None`, "no kernel copy of the target".
- Otherwise the repository is `workspace.cache_path(origin_url, work)` with
  `work = Path(brief.mirror).parent.parent` (finding 6). With no cache
  there, `revert: None`, "no cache of the target".
- When the cache does not hold the head (`cat-file -e <head>^{commit}`),
  `on_branch: None`, `revert: None`, "cache not fetched since the merge".
- From the cache: `on_branch`, from `merge-base --is-ancestor head_sha
  refs/heads/<branch>` through `git.ancestry`, exit 0 true, 1 false, any
  other None with git's message; `reverted_by`, from
  `git.trusted(cache, "log", "--format=%H%x00%B%x1e",
  f"{head_sha}..refs/heads/{branch}", text=False)` decoded with
  `errors="replace"`, each commit whose body holds `This reverts commit <40 hex>` naming a sha in the merge's recorded
  `commits`, with the reverting commit's sha. GitHub's revert button writes
  the same line in the body under a "Reverts owner/repo#N" title, so both
  are read. A merge with `commits: None` has `reverted_by: None`. `as_of`
  is the cache's `FETCH_HEAD` time; `source: "cache"`.

Each read runs in `git.threaded`. No call reaches a network; the cache is
read as last fetched, and `as_of` says when.

`_cache`'s naming (`core/workspace.py:697-704`) moves into
`cache_path(origin, work)`; `_cache` calls it with `source or spec.repo`;
no provisioning behavior changes.

`after_merge(conn, brief, rows)` assembles the merges with feedback, used,
rework (one `done_merges` call per distinct url and branch), and revert.

`tasks.status` is unchanged: the router, `fresh`, and `checks` call it on
every step (`core/router.py:288-309`, `core/fresh.py:180-240`), and none of
them reads outcomes.

### The `used` mark

`python -m core used TASK_ID --by B [--delivery EVENT_ID] [--note TEXT] [--via V] [--role-played]`
appends one `delivery.used` row on the task, under the task's lock, and
prints its id. `--by` is required and names who used the work. The row
names a delivery (`delivery_event_id`, `candidate`): `--delivery` when
given; else, when the task has a `done` merge, the delivery its latest
merge carried (the latest `task.delivered` before that merge's
`effect.held`); else the task's latest delivery. `effect_id` and
`head_sha` are those of the `done` merge that carried the named delivery,
else both null; then `note` and the provenance fields
(`ledger.provenance`, `core/ledger.py:24-28`). A task with no delivery gets
"task X has no delivery to mark used"; a `--delivery` that is not one of
the task's "task X has no delivery N"; an unknown task "no task X". These say what the row names, not whether the work is
good. A stopped task is marked like any other. `machine.fold` ignores the
row type, so the state does not move.

No turn can write it: it is no performer, signal, or effect, and the turn
has no database credential.

### Surfaces

- `python -m core status TASK_ID` adds `after_merge` (the merges) and
  `deliveries_used` (deliveries with marks and no merge), beside
  `metered_spending` and `attention_counts` (`core/__main__.py:711-715`).
- The task page shows an "After merge" table per merge (head, merged at,
  feedback count and texts, used count and marks, paths, later merges with
  shared code paths and days, later doc-only merges, revert) and a "Used"
  list per delivery without a merge, above the JSON; every value goes
  through `esc`.
- `tasks.index` calls `outcomes.merges(rows)` on the rows it already reads
  and `outcomes.done_merges(conn)` once, and adds `merges`,
  `feedback_after`, `used`, and `reworked` (later merges with shared code)
  per task (finding 9). The index column shows them; the index runs no git.

## Threat model

The turn controls the commits that land: their paths and messages, so a
path name and a revert line are turn-authored text, and a turn can write a
false "This reverts commit" line. These are shown, escaped, with the
reverting commit's sha; no code path decides on them. The kernel must
never: read the builder's clone (`repo/`), only the mirror, the cache, or a
`--workspace` workspace through the hostile check the merge already
applies; run git `trusted` outside the repositories only it writes; fetch
from a network during a status read; run git on a turn-written directory
from the status page; write a row from the status page; or let a merge's
outcome feed the router, the broker's decision, the fold, or a verdict.
`landed` is written by the kernel from its own read; `delivery.used` only
by the command line, with provenance.

## Governance

Nothing here adds a check, gate, hook, validator, review round, or approval
step: every reading is shown and none holds, redirects, or refuses work.
No grant is needed. `landed` is recorded after the predicate holds and a
failure to read it never stops a merge. The `used` command's refusal of a
task with no delivery is about what the row names, not a judgement of
work. The verifier calibration that would use these readings as labels
(issue 3, #3611) and any rule that acts on a revert are out of scope;
acting on them would be a gate needing its own incident and Tom's tap.

## Stakes

2 and 2: a new ledger row type, a new field on the merge's intent, and
reads of stored data and the kernel's repositories
(`docs/plans/valor-rebuild.md`, "a change to stored data ... is a 2"). No
migration: both are rows and payload fields in the existing table.

## Done, as evidence

1. `python -m core status` on a merged task in the test database prints
   `after_merge` with the merge's head, feedback after it, recorded paths,
   later merges split into code and docs with `days_after`, the revert
   reading, and used marks, each from rows and git built in the test.
2. A merge released in the test database carries `landed` on its intent,
   with its own commits and paths and not those of a merge it took in.
3. `python -m core used` writes `delivery.used` with provenance on a task
   with a merge and on one with only a delivery; the state is unchanged.
4. The task page renders the After merge table and Used list, escaped; the
   index shows the column; both through the read-only session.
5. The suite is green at head where it was at base; ruff clean.

## Tests

`tests/test_outcomes.py` (new), on real git repositories in `tmp_path` and
the test database:

- **Two merges of one task.** Merge, feedback, merge again: two entries;
  the second's `landed.before` is the first's head; the feedback sits on
  the first.
- **A failed merge then a done one.** One merge listed; the failed effect
  is not.
- **Feedback before a merge is no merge's.** Feedback in `merge` (sent to
  patch) and a later merge: the merge lists no feedback.
- **Own commits only.** A merges `a.py`; B, provisioned before A merged,
  takes A's head in, adds `b.py`, merges: B's `landed.paths` is `["b.py"]`
  and B's `commits` exclude A's.
- **Rework.** Task A merges `a.py`; B (same url and branch, later) touches
  `a.py` and `b.py` and is under `later` with `shared_code: ["a.py"]` and
  its days; C touches only `c.py` and is not listed; D touches `a.py` on
  another branch and is not; E merged before A is not.
- **Doc-only overlap.** B shares only `docs/data.md` with A: under
  `later_docs`, not `later`. A shared `CLAUDE.md` counts as code.
- **A rename counts both paths.** B renames `a.py`; it is listed against A.
- **Unrecorded paths.** A merge with no `landed` gives `paths: None`,
  `later: None`; a later merge with no `landed` appears in A's
  `later_unknown`, never as no overlap.
- **A hand commit on the target.** A commit to `a.py` landed on the
  target outside any merge row, in the cache, and taken in by B, is absent
  from B's `landed.paths`.
- **A `--workspace` merge.** `landed` has null paths with "no kernel copy",
  and no git runs on the workspace for it.
- **Turn-authored bytes.** A revert body holding byte 0xff gives a reading,
  not an error; a non-ASCII `.md` path lands in `shared_docs`.
- **Revert.** In the cache, a commit after the head whose body reverts one
  of the merge's commits gives `reverted_by` with its sha; a GitHub-style
  revert ("Reverts owner/repo#N" title, "This reverts commit X." body) is
  detected; one naming a commit outside `commits`, or an abbreviated sha,
  is not; a head on the branch gives `on_branch: true`, a branch moved off
  the head `on_branch: false`, and a head the cache lacks `on_branch: None`
  with "cache not fetched since the merge".
- **Where revert reads.** A task on its own `origin.git` gives `revert`,
  `later`, and `later_docs` None with "private target"; a `--workspace`
  task gives `revert: None`; a spec whose `repo` differs from its
  `merge_url` reads from `cache_path(merge_url, work)` when that exists,
  and gives "no cache of the target" when it does not.
- **The cache's age.** `as_of` is the cache's `FETCH_HEAD` time.
- **`used`.** On a merged task the row names the latest delivery, effect
  id, and head, with note and provenance, and attaches to that merge; on a
  delivered task with no merge it names the delivery with null effect and
  is listed under the delivery; on a task in `merge` likewise; after a
  second merge it names the second; on a stopped merged task it records
  and the merges are still shown; no delivery and an unknown task exit
  with the named messages; a role-played mark is listed and not in
  `used_count`; the fold's state never moves. Merge, feedback, patch, and
  back in `merge` with a new delivery: the mark names the merged delivery,
  not the new one; `--delivery` names another. `used` without `--by` exits
  with the argparse error.
- **Legacy and calibration tasks** give empty lists and no error.
- **`tasks.status` has no `after_merge`**, and runs no git (a stand-in
  `git.trusted` that raises is never reached), so router steps are
  unchanged.
- **`done_merges`** returns other tasks' done merges on one url and branch
  from one query.
- **`cache_path`** names the directory `_cache` creates.

`tests/test_broker.py`: a released merge's intent carries `landed`; a
`GitError`, and a `ValueError`, in the read record null paths and the merge
still lands; an unstorable `paths` still writes the intent and the merge
lands; a reconciled merge keeps the intent's `landed`.

`tests/test_tasks.py`: `index` returns `merges`, `feedback_after`, `used`,
and `reworked`.

`tests/test_ui.py`: the task page of a merged task shows the table and
escapes a path holding `<script>`; a delivered task with a mark and no
merge shows the Used list; the index column shows the counts; a task with
neither shows none.

Runs: `tests/test_outcomes.py`, `tests/test_broker.py`, `tests/test_tasks.py`,
`tests/test_ui.py`, `tests/test_machine.py`, `tests/test_session.py`,
`tests/test_objective_tree.py`, `tests/test_workspace.py`, then the full
suite; failures also checked at base.

## Files it changes

`core/outcomes.py` (new), `core/broker.py` (`landed` on the merge intent),
`core/git.py` (`has_commit`, `ancestry`), `core/tasks.py` (`index` fields),
`core/__main__.py` (`used`, `status`, the docstring), `core/workspace.py`
(`cache_path`), `ui/app.py`, `tests/test_outcomes.py` (new),
`tests/test_broker.py` (new), `tests/test_tasks.py` (new), `tests/test_ui.py`. Docs,
for the docs check: `docs/data.md` (the `delivery.used` row and `landed` on
`effect.intent`), `docs/mission.md` (how real use is read; work landed by
hand has no merge row, so its paths, rework, and revert are not read; no
delivery is marked used yet), `docs/sdlc-state-machine.md` (after
`merged`, what the status reads), `core/README.md`, `ui/README.md`.

## Absorbs

`_cache`'s naming, one function both the provisioning and the read use.

## Leaves out

- "Did it run": a rollout record is issue 2 (#3610).
- The verifier calibration and its labels: issue 3 (#3611), which reads
  `delivery.used` and `landed` as recorded here.
- Any action on a revert or rework: no watch, no revert, no notice.
- Marking use from a Telegram or email message; the command line only.
- A rework window: the issue said 14 days and no doc gives the number;
  every later overlapping merge is listed with `days_after`, so the reader
  sees the gap.
- Back-filling `landed` for merges recorded before this change: they show
  "paths not recorded".
- Fetching the remote to freshen the cache.
- Paths, rework, and revert for work landed by hand: there is no merge
  row to read them from.

## Rollout on Valor's Mac

1. `.venv/bin/python -m core backup`.
2. Pull the merge into the kernel checkout; `launchctl kickstart -k
   gui/$(id -u)/com.valor.kernel`.
3. Restart the status page: stop the running `python -m ui` by its PID,
   start it again, and read `http://127.0.0.1:8790/` for 200.
4. `python -m core status` on one merged task; record what it shows under
   Merged.

## Questions for Tom

None.

## Decided by default

- Marking a delivery used is not attention: it is evidence offered, not a
  decision put to Tom (`docs/mission.md:43-47`), so it is listed under the
  merge or delivery and left out of `attention_counts`.
- `--by` is required and names whoever used the work; a role-played mark
  is shown and not counted as real use.
- What `landed` cannot exclude, stated: hand commits landed on the target
  after the cache was last fetched, and a merge commit's own conflict
  resolution (`--no-merges`). The cache is fetched at every provisioning,
  so the first is the gap since the last task started.

## Decisions the lead may change

- `landed` is written on the merge's `effect.intent`, not its outcome: the
  intent is the row both the perform and `reconcile` paths share, and the
  commits are fixed before the push. The finding asked for "when the merge
  lands"; a merge counts only once its outcome is `done`, so the recorded
  paths are those of what landed.
- The used-mark row is `delivery.used`, not `merge.used`, since it can name
  a delivery with no merge.
- Merges recorded before this change get no git fallback for paths: the
  status surfaces read git only for revert and on-branch.
- No 14-day window (Leaves out).
- Revert detection reads only the `This reverts commit` line `git revert`
  and GitHub's revert both write, and branch ancestry; a hand-written undo
  without that line is not seen.

## Critique round 1

Critique `critic-3612-r1`, verdict `revise`; all ten findings accepted.

1. **Doc paths make every later merge rework.** Shared paths split by
   `machine.is_doc_path` into `shared_code` and `shared_docs`; `later` needs
   shared code, doc-only overlaps go to `later_docs`. Test added.
2. **`used` refused the deliveries actually used.** The row is renamed
   `delivery.used`; it names the latest delivery, plus the latest merge's
   effect and head when there is one. Refused only for an unknown task or
   one with no delivery. Marks without a merge are listed per delivery.
   Tests for no-merge and `merge`-state tasks; `docs/mission.md` says hand
   landed work has no merge row.
3. **A moved target branch mixes in other tasks' paths.** Paths come from
   the merge's own commits, `log --no-merges` excluding every earlier done
   merge head on the same url and branch. Test added.
4. **Per-view git and an unnamed cross-task lookup.** `before`, `commits`,
   and `paths` are recorded as `landed` at release, from the mirror the
   broker already reads; rework is a ledger fold; other tasks' merges come
   from one query, `outcomes.done_merges`. Git on the status surfaces is
   only revert and on-branch. Recorded on the intent rather than the
   outcome, for the reason under Decisions. No read-time fallback for older
   merges: they show "paths not recorded".
5. **Private origin readings are empty by construction.** Such a task shows
   `revert` and rework as None, "private target". Test added.
6. **Cache chosen by `project.repo == origin_url`.** Reads
   `cache_path(origin_url, work)` directly. Test added.
7. **Helpers and `trusted` contradicted, no revert helper.** Named calls:
   `git.trusted` `merge-base --is-ancestor` and `log --format=%H%x00%B%x1e`
   on the cache; the `run`-based `git.own_changes` only for a `--workspace`
   task at release.
8. **Missed tests.** Added: failed then done merge; stopped after merge;
   GitHub-style revert; doc-only overlap; hostile `--workspace` config.
9. **`core/tasks.py` missing.** `index` calls `outcomes.merges` on the rows
   it reads and `done_merges` once; listed in files, with tests.
10. **Questions for Tom were technical.** Both moved to "Decided by
    default"; the section is empty.

## Critique round 2

Critique `critic-3612-r2`, verdict `revise`; all seven findings accepted.
The lead's decision: fold every finding as the report's fixes describe and
go to build with no third critique, since the findings are concrete and
governance is clean once finding 3 is fixed.

1. **`landed` read a turn-owned workspace.** The read is removed: a
   `--workspace` task records null `commits` and `paths` with "no kernel
   copy", and `git.own_changes` is dropped. Nothing the kernel records
   comes from a turn-owned workspace. The hostile-config test is replaced
   by one showing no git runs on the workspace for `landed`.
2. **Hand-landed commits credited to the merge.** The mirror's log borrows
   the target cache's objects and also excludes the cache's tip of the
   target branch. The remainder (hand commits fetched after the cache was,
   merge-commit resolutions) is under Decided by default. Test added.
3. **Recording `landed` could stop the merge.** One `git.threaded` call
   with `--ignore-missing` replaces the per-head `cat-file`; the whole
   computation is wrapped in `except Exception` and records nulls with the
   exception's type; the intent is written with `try_append` and, when
   refused, again with `landed` cut to `before` and the reason. Recording
   `landed` never stops or fails a merge. Tests added.
4. **`on_branch` could not be false; a stale cache read as an error.** A
   head the cache lacks reads `on_branch: None`, "cache not fetched since
   the merge"; `git.ancestry` maps exit 0, 1, and other. Tests added.
5. **Turn-authored bytes.** Paths are read with `-z` and bodies as bytes,
   both decoded with `errors="replace"`. Tests added.
6. **A mark joined two deliveries.** With a `done` merge, the mark names
   the delivery that merge carried; `--delivery` names another. Test added.
7. **`--by` had no default stated.** `--by` is required for `used`, a
   usage fix, not a check; the help and `docs/data.md` say it names who
   used the work. Test added.

## Build record

Branch `i3612-merge-outcomes`, built to the plan with critique round 2
folded. Built as designed, with these points the plan left open:

- Names #3611 joins against: `delivery.used` carries `used_id`,
  `delivery_event_id`, `candidate`, `effect_id`, `head_sha`, `note`, and
  `provenance`. The merge's `effect.intent` carries `landed`: `before`,
  `commits`, `paths`, and `why` (null when read). `outcomes.done_merges`
  returns `task_id`, `effect_id`, `head_sha`, `url`, `target_branch`,
  `merged_at`, `event_id` (the outcome row), and `landed`. Each
  `after_merge` entry adds `delivery_event_id`, `paths`, `feedback`, `used`,
  `used_count`, `later`, `later_docs`, `later_unknown`, `rework_why`,
  `revert` (`on_branch`, `reverted_by`, `as_of`, `source`), and
  `revert_why`.
- Feedback after a merge is every `feedback.given` after its outcome and
  before the task's next `task.delivered`, so feedback given in `merge`
  belongs to no merge.
- `python -m core status` on an unknown task exits "no task X", since it
  now loads the Brief.
- `workspace.cache_path` holds the cache's naming `_cache` used, so the
  status reads the same directory provisioning fetched into.
- Feedback rows written by hand in tests carry `on_delivery` and the
  candidate, as `core/session.py` writes them; `tasks._digest` reads both.

Tests: `tests/test_outcomes.py` (30), `tests/test_broker.py` (5),
`tests/test_tasks.py` (1), and four in `tests/test_ui.py`. Mutation check:
dropping the cache tip from the log's `--not` fails the hand-commit test.
Suite, without `tests/test_container.py` and
`tests/test_harness_contract.py`: 1496 passed, 25 skipped, and 8 failed
with 267 errors, all in thirteen files that ran while the disk was full
("No space left", `initdb`); those thirteen files run again alone: 370
passed, 1 skipped. `tests/test_harness_contract.py`: the pi variants pass;
the claude_code turns end `failed` at base 0027e237f as at the head, the
known alias fault (the CLI maps `haiku` to `claude-haiku-5-5`, which has
no price; fixed on `fix-haiku-alias`), and its stop test hangs, so it was
run apart.
Ruff: `ruff check .` and `ruff format --check .` pass.

## Merged

- Checks on `6d8376bc3`, rebased onto `cd6e94aad`:
  - **Test: `gaps`.** No regressions. The head suite, without `tests/test_container.py` and `tests/test_harness_contract.py`, gave 1677 passed, 25 skipped, 3 failed. All three also fail at base: the fresh-session test in `tests/test_workspace.py`, and two `tests/test_pi.py` cases. The harness contract's claude_code cases: 16 passed, 1 skipped.
  - **Review: `pass`.** It adds no check, gate or review step, and no invented caps.
  - **Docs: `updated`.** `f2f48e950`, `core/README.md`, the `--via` option.
- Patch `c3504be59`, for the gaps both checks named:
  - a `before` or `head` the mirror lacks records null `landed` with why `GitError`, not an empty one, with a test;
  - the plan's feedback window reads "next delivery", as built.
  - `tests/test_outcomes.py` and `tests/test_broker.py` pass at the patch: 36 tests.
- Known limits, not changed:
  - a hand commit the candidate took in counts as the merge's own when the target cache was last fetched before it;
  - a revert of the merge's revert still lists the first revert under `reverted_by`.
- Backup `valor_rebuild-20261008T111835Z.dump` was taken before the merge.
- Rolled out on Valor's Mac:
  - The kernel restarted on the merged tip.
  - The status page was restarted by its PID; `/` returns 200 and shows the "after merge" column.
  - This ledger has no released merge. Its only merge, `75c0902b6e25`, is held in `merge`. `status` on that task gives `after_merge: []` and `deliveries_used: []`.
