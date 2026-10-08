---
tracking: none
slug: i3612-merge-outcomes
type: plan
status: planned
critique_rounds: 2
review_rounds: 2
---

# What happened to merged work

GitHub issue #3612 (`tomcounsell/ai`), issue 4 of
[critique-issues-out.md](critique-issues-out.md). Every citation below is to
`valor-cori-rebuild` at 2d6ed8a8e, re-read for this plan; the issue cited
5c11e496d.

**Goal.** For each merge a task made, `python -m core status` and the
status page show what came after it: Tom's feedback after it, later merges
that touched the same paths, whether it was reverted, and whether someone
used it. All of it is read from the ledger and the kernel's own git
repositories when asked, beside spending and attention. Nothing reads it to
decide anything.

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
- The merge is a fast-forward push of `head_sha` onto the target branch,
  never a merge commit (`tools/push_branch.py:167-189`; the predicate's term
  4 refuses merge commits, `core/machine.py:700-712`). Its payload carries
  `url`, `target_branch`, `head_sha`, and the candidate
  (`core/verdicts.py:509-522`). The Brief carries `base_sha`, `mirror`,
  `origin_url`, `target_branch`, and, for a provisioned task, `project`
  with its `repo` (`core/tasks.py:27-79`, `core/workspace.py:973-996`).
- Feedback after a merge is a `feedback.given` row with provenance
  (`core/session.py:652-706`), folded into the attention log
  (`core/tasks.py:753-762`). Nothing folds it as an outcome of the merge it
  followed.
- `tasks.status` (`core/tasks.py:685-721`) reports the delivery and its
  `outcome` (`passed`, `gaps`, `did_not_pass`, `governance_refused`;
  `docs/data.md:132`), which is the join's verdict before the merge, not
  what happened after it. The issue's `core/tasks.py:638-647` is
  `child_report`, a parent's view of a child, which reports the same field.
- The kernel's git: the per-task mirror `<work>/<task>/kernel.git`, which
  only the kernel writes and which holds the base and every candidate and
  docs head (`core/workspace.py:12-13,938-941`); the task's bare origin
  `origin.git`, the merge's target when the spec has no `merge_url`
  (`core/workspace.py:9-11,991-993`); and the shared cache
  `<work>/cache/<name>-<digest>.git`, a bare clone of the spec's `repo`
  fetching `+refs/heads/*` on every provisioning (`core/workspace.py:693-730`).
  `python -m core workspace remove` deletes a merged task's directory,
  mirror included, and writes `workspace.removed` (`core/__main__.py:495-509`).
- The status page is read-only (`ui/app.py:1-11`, `default_transaction_read_only`
  at `ui/app.py:186-189`); its task page prints `tasks.status` as JSON
  (`ui/app.py:97-115`) and its index lists state, spending, and attention
  (`ui/app.py:75-94`).
- No row records a use. `docs/data.md` is the row registry.

## Design

### The merges of a task (`core/outcomes.py`, new)

`merges(brief, rows)` is a pure fold over one task's rows. Each
`effect.outcome` with `kind: done` whose effect is the task's merge effect
(the `effect.held` with `action_type: merge`, matched by `effect_id`) is one
merge: `effect_id`, `head_sha`, `url`, `target_branch` (from the held
payload), `at` (the outcome row's time), `event_id`, and `before`: the
previous merge's `head_sha` on this task, or `Brief.base_sha` for the first.
A legacy or calibration task has none.

For each merge, ledger only:

- **`feedback`**: every `feedback.given` row after this merge's outcome row
  and before the task's next merge, with its text and provenance. Feedback
  given while the task was in `merge` belongs to no merge.
- **`used`**: every `merge.used` row naming this merge's `effect_id`, with
  note and provenance. `used_count` counts the rows whose `role_played` is
  not true; a role-played mark is listed and not counted, since a stand-in
  using a feature is not real use (`docs/mission.md:148`, a replay is not
  real use).

### Paths, rework, and revert, read from git when asked

`after_merge(conn, task_id)` adds to each merge:

- **`paths`**: `git.diff_paths(repo, before, head_sha)`
  (`core/git.py:537-542`, `--no-renames`, so a rename lists both paths),
  in the first repository that holds both commits: the mirror when the
  Brief names one and no `workspace.removed` row exists; for a
  `--workspace` task, the workspace, read through `git.run`'s hostile check
  as the merge predicate already does (`core/broker.py:511-514`); then the
  project cache. None when none holds them, shown as "unknown", never as
  empty.
- **`later`**: every merge by another task to the same `url` and
  `target_branch` whose outcome row comes after this one, whose own `paths`
  share at least one path with this merge's: its task id, effect id,
  `merged_at`, `days_after` (one decimal), and the shared paths. A later
  merge whose paths are unknown is listed under `later_unknown` by task id,
  so an unknown never reads as "no rework". The same task's own later merge
  is the next entry in its `merges`, not repeated here.
- **`revert`**: read from the repository that tracks the target branch:
  the task's `origin.git` when `origin_url` is that local path, else the
  project cache when `project.repo` equals `origin_url`; for a
  `--workspace` task, or when neither exists, `revert` is None
  ("unknown"). From it: `on_branch`, whether `head_sha` is an ancestor of
  `refs/heads/<target_branch>` (`git.is_ancestor`, `core/git.py:529-530`);
  `reverted_by`, each commit in `head_sha..refs/heads/<target_branch>` whose
  message holds `This reverts commit <40 hex>` (the line `git revert`
  writes) naming a commit in `before..head_sha`, with that commit's sha;
  `source` (`origin` or `cache`) and `as_of`, the cache's `FETCH_HEAD`
  time (the origin is the merge's own target, so it is current).

Each git read uses the existing helpers in a worker thread
(`git.threaded`); `trusted` only on the mirror, the origin, and the cache,
which no turn writes. No call reaches a network: the cache is read as it
was last fetched, and `as_of` says when.

`_cache`'s file name (`core/workspace.py:699-704`) moves into
`cache_path(origin, work)` so the read finds the same directory; `work` is
`Path(brief.mirror).parent.parent`. `_cache` calls it; no behavior changes.

`tasks.status` is unchanged: the router, `fresh`, and `checks` call it on
every step (`core/router.py:288-309`, `core/fresh.py:180-240`), and none of
them reads outcomes. Only the two surfaces below call `after_merge`.

### The `used` mark

`python -m core used TASK_ID [--note TEXT] [--by B] [--via V] [--role-played]`
appends one `merge.used` row on the task: `effect_id` and `head_sha` of its
latest merge, the note, and provenance (`ledger.provenance`,
`core/ledger.py:24-28`), under the task's lock, and prints the row's id. A
task with no merge gets "task X has no merge to mark used"; an unknown task
"no task X". These answer what the mark is about (a mark names a merge),
not whether the work is good. `machine.fold` ignores the row type, so the
state does not move; feedback after a mark still goes to `patch`.

No turn can write it: it is no performer, signal, or effect, and the turn
has no database credential.

### Surfaces

- `python -m core status TASK_ID` adds `after_merge`: the list from
  `after_merge`, beside `metered_spending` and `attention_counts`
  (`core/__main__.py:711-715`).
- The task page shows an "After merge" table per merge (head, merged at,
  feedback count and texts, used count and marks, paths, later merges with
  shared paths and days, revert) above the JSON; every value goes through
  `esc`.
- The index gains an "after merge" column for tasks with a merge: merges,
  feedback after, used, all from rows it already reads (`ui/app.py:75-94`,
  `core/tasks.py:846-884`); no git on the index.

## Threat model

The turn controls the commits that land: their paths and messages, so a
path name and a revert line are turn-authored text, and a turn can write a
false "This reverts commit" line. These are shown, escaped, with the
reverting commit's sha; no code path decides on them. The kernel must
never: read the builder's clone (`repo/`), only the mirror, the origin, the
cache, or a `--workspace` workspace through the hostile check the merge
already applies; run git `trusted` outside the repositories only it writes;
fetch from a network during a status read; write a row from the status
page; or let a merge's outcome feed the router, the broker, the fold, or a
verdict. `merge.used` is written only by the command line, with provenance.

## Governance

Nothing here adds a check, gate, hook, validator, review round, or approval
step: every reading is shown and none holds, redirects, or refuses work.
No grant is needed. The `used` command's refusal of a task with no merge
is about what the row names, not a judgement of work. The verifier
calibration that would use these readings as labels (issue 3, #3611) and
any rule that acts on a revert are out of scope; acting on them would be a
gate needing its own incident and Tom's tap.

## Stakes

2 and 2: a new ledger row type and reads of stored data and the kernel's
repositories (`docs/plans/valor-rebuild.md`, "a change to stored data ...
is a 2"). No migration: `merge.used` is a row in the existing table.

## Done, as evidence

1. `python -m core status` on a merged task in the test database prints
   `after_merge` with the merge's head, feedback after it, paths, later
   overlapping merges with `days_after`, revert reading, and used marks,
   each from rows and git built in the test.
2. `python -m core used` writes `merge.used` with provenance, and the
   task's state is still `merged`.
3. The task page renders the After merge table, escaped; the index shows
   the column; both through the read-only session.
4. The suite is green at head where it was at base; ruff clean.

## Tests

`tests/test_outcomes.py` (new), on real git repositories in `tmp_path` and
the test database:

- **Two merges of one task.** Merge, feedback, merge again: two entries;
  the second's `before` is the first's head; the feedback sits on the first.
- **Feedback before a merge is no merge's.** Feedback in `merge` (sent to
  patch) and a later merge: the merge lists no feedback.
- **Rework.** Task A merges `a.py`; B (same url and branch, later) touches
  `a.py` and `b.py` and is listed with shared `a.py` and its days; C
  touches only `c.py` and is not; D touches `a.py` on another branch and is
  not; E merged before A is not.
- **A rename counts both paths.** B renames `a.py`; it is listed against A.
- **A removed mirror falls back to the cache**; with neither holding the
  commits, `paths` is None and a later merge with unknown paths appears in
  `later_unknown`, never as no overlap.
- **Revert.** A commit after the head reverting a commit inside the range
  gives `reverted_by` with its sha; one naming a commit outside the range,
  or an abbreviated sha, does not; a target branch moved off the head gives
  `on_branch: false`; a `--workspace` task gives `revert: None`.
- **The cache's age.** `as_of` is the cache's `FETCH_HEAD` time.
- **`used`.** The row carries effect id, head, note, provenance; the fold's
  state stays `merged`; a mark on a task with no merge and on an unknown
  task exits with the named messages; a role-played mark is listed and not
  in `used_count`; a mark after a second merge names the second.
- **Legacy and calibration tasks** give an empty list and no error.
- **`tasks.status` has no `after_merge`**, and runs no git (a stand-in
  `diff_paths` that raises is never reached), so router steps are unchanged.
- **`cache_path`** names the directory `_cache` creates.

`tests/test_ui.py`: the task page of a merged task shows the table and
escapes a path holding `<script>`; the index column shows merges, feedback
after, and used; a task with no merge shows none.

Runs: `tests/test_outcomes.py`, `tests/test_ui.py`, `tests/test_machine.py`,
`tests/test_session.py`, `tests/test_objective_tree.py`,
`tests/test_workspace.py`, then the full suite; failures also checked at
base.

## Files it changes

`core/outcomes.py` (new), `core/__main__.py` (`used`, `status`, the
docstring), `core/workspace.py` (`cache_path`), `ui/app.py`,
`tests/test_outcomes.py` (new), `tests/test_ui.py`. Docs, for the docs
check: `docs/data.md` (the `merge.used` row), `docs/mission.md` (how real
use is read: the readings exist; no merge is marked used yet),
`docs/sdlc-state-machine.md` (after `merged`, what the status reads),
`core/README.md`, `ui/README.md`.

## Absorbs

`_cache`'s naming, now one function both the provisioning and the read use.

## Leaves out

- "Did it run": a rollout record is issue 2 (#3610).
- The verifier calibration and its labels: issue 3 (#3611).
- Any action on a revert or rework: no watch, no revert, no notice.
- Marking use from a Telegram or email message; the command line only.
- A rework window: the issue said 14 days; every later overlapping merge
  is listed with `days_after` instead, so no number is chosen and the
  reader sees the gap.
- Fetching the remote to freshen the cache.

## Rollout on Valor's Mac

1. `.venv/bin/python -m core backup`.
2. Pull the merge into the kernel checkout; `launchctl kickstart -k
   gui/$(id -u)/com.valor.kernel`.
3. Restart the status page: stop the running `python -m ui` by its PID,
   start it again, and read `http://127.0.0.1:8790/` for 200.
4. `python -m core status` on one merged task; record what it shows under
   Merged.

## Questions for Tom

1. Should marking a merge used count as attention, beside questions and
   feedback? Assumed no: it is evidence Tom offers, not a decision put to
   him (`docs/mission.md:43-47`), so it is listed under the merge and left
   out of `attention_counts`.
2. Is "used" only Tom's word, or may it record someone else using the
   result? Assumed anyone may be named with `--by`; a role-played mark is
   shown and not counted as real use.

## Decisions the lead may change

- No 14-day window (Leaves out).
- Revert detection reads only `git revert`'s own message line and branch
  ancestry; a hand-written undo without that line is not seen.
- The index shows ledger-only counts; paths, rework, and revert are on the
  task page and the command only, so the index runs no git.
