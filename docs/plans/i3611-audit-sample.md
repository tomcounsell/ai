---
tracking: none
slug: i3611-audit-sample
type: plan
status: merged
issue: tomcounsell/ai#3611
critique_rounds: 2
review_rounds: 2
governance_grant: none
---

# The audit sample and the verifier's calibration

Kernel task for issue #3611. Governed by `docs/architecture.md` ("Calibration
and autonomy", lines 402 to 409), `docs/mission.md` ("Independent checks",
lines 171 to 173), and `docs/judgement-layer.md` ("Calibration discipline",
from line 273; "Which uses are gates", line 340).

**Goal.** Build the evidence half of "autonomy shrinks automatically on
evidence": Tom's own label on a candidate the blind verifier judged, a list
of candidates for him to label weighted as the design weights it, and the
verifier's calibration (false accept, false reject, Brier score, each with
its sample size) on the command line and the status page. The half that
acts on that evidence (lowering the highest class Valor commits without
Tom) is not built here; see Governance.

Every citation below was read on this branch at 2d6ed8a8e.

## Where the issue's citations stand

The issue cites 5c11e496d. Re-read at 2d6ed8a8e:

- `docs/architecture.md:402-409` and `:509`: unchanged. Line 402 says the
  sample is "weighted toward work that left the workspace and every `act`,
  with a smaller share of failures"; line 404 that calibration is "false
  accept, false reject, and Brier score [12] with sample size; raw pass rate
  is never reported alone"; line 509 still reads "human audit sample
  (design)".
- `docs/judgement-layer.md:273`: unchanged in place; the file moved under
  task 80e49c02 but the section did not.
- `core/fresh.py:734-770` has moved: `final_verdict` is now
  `core/fresh.py:733`, `_review_fields` is `:758-802` (it reads
  `predicted_failure` at `:765` and range-checks it at `:790-793`), and the
  review runner passes it to the record at `:1070`. `core/verdicts.py:361`
  writes it into the `review.decided` payload only for a review recorded by
  a session (`leg` not `manual` or `kernel`). Nothing in `core/` reads it
  back: `grep -rn predicted_failure core` finds only those writes.
- `grep -rn predicted_failure core` also finds `core/fresh.py:1105`, the
  review runner's return value; it is a write, not a reader.
- `docs/mission.md:396`: unchanged, "no audit sample exists".
- `docs/plans/valor-rebuild-feedback.md:33`: unchanged, "Merges are
  Valor's call".

One premise needs a correction. The issue says the label is "Tom's right or
wrong on one verdict". The design says Tom audits without the judge's
scores in front of him (`docs/emulator.md:502-506`, citing [17]: a human
judging with a model's answer in view is worse calibrated than either
alone), and that labels come from humans, not from agreement with a model
(`docs/judgement-layer.md:280-283`). So Tom labels the work, `pass` or
`changes`, without seeing the verdict; right or wrong is computed by the
fold. The design settles this, so it is not put to Tom.

## What it builds on

- **The verdict.** `review.decided` on the task's own stream
  (`docs/data.md:126`): for a session's review, `reviewer_verdict`
  (`pass` or `changes`), `predicted_failure` (0 to 1, or absent),
  `requirements`, `model`, and `candidate` `{sha, turn_id}`
  (`core/verdicts.py:262-263`, `:356-362`). A `kernel` review has no
  `reviewer_verdict` and is not a verifier's judgement
  (`core/verdicts.py:243-244`). `review.compared` (a review at another
  seat) is information only (`docs/data.md:129`).
- **What left the workspace.** An `act` is "irreversible or money; a send, a
  merge, a payment" (`core/broker.py:8`). The only `act` that carries a
  candidate the verifier judged is the merge: `merge_action` names the
  candidate in its payload (`core/verdicts.py:509-522`), and an
  `effect.outcome` with kind `done` for that `effect_id` is the merge having
  happened (`core/machine.py:566-589`). So "work that left the workspace and
  every `act`" is, for the verifier, every candidate a done merge carried.
- **Provenance.** `ledger.provenance(by, via, role_played)`
  (`core/ledger.py:22-27`), used by corrections, approvals, answers.
- **A stream that is not a task.** `corrections` keeps its rows under
  `task_id = 'corrections'` (`core/corrections.py:27`, `:82-95`); task
  listings read `documents WHERE kind = 'task'` (`core/tasks.py:850-853`,
  `:892`), so such a stream is never listed or stepped as a task. No schema
  change: the kernel role already has `SELECT, INSERT` on `events`
  (`core/schema.sql:65`).
- **The status page.** `ui/app.py`: GET only, a read-only database
  session, every ledger value escaped (`ui/app.py:1-11`, `:178-201`).

## Done, as evidence

Each item is a test on real Postgres in the default suite.

1. **A label is a row.** `python -m core audit label TASK SHA pass|changes
   [--note TEXT] [--by tom] [--via "the command line"] [--role-played]`
   appends one `audit.labelled` row on the `audit` stream:
   `{task_id, candidate_sha, label, note, provenance}`. It writes nothing on
   the task's stream, so the task's fold, its state, and the kernel's
   scheduling of it are unchanged, and no notice is requested.
2. **What may be labelled.** A label names a candidate that has at least one
   `review.decided` row with a `reviewer_verdict` in that task's ledger. Any
   other task, sha, or label value is a lookup that finds nothing, and the
   command exits with the reason and writes nothing, the way the command
   line treats an unknown task now (`core/__main__.py:312`, "KeyError for
   an unknown task"). It lives in the `__main__` command path, before
   `record`. It judges no work and holds no task, request, or effect: there
   is nothing else to label. A calibration task or a legacy task has no
   such row and so finds nothing.
3. **Relabelling.** A second label on the same candidate is a new row; the
   fold reads the latest real label by row id and the old one stays in the
   ledger. A later stand-in label never replaces a real one: stand-in
   labels are folded apart and only counted.
4. **The list.** `python -m core audit` prints one list of the candidates
   with no real label yet (a label counts as real when its `role_played` is
   false, whatever `--by` names; a stand-in's label leaves the candidate on
   the list). There are no group headings and no merged column. Each line
   shows the task id, the instruction, the base and candidate sha, and the
   mirror's `git diff BASE SHA` command to read the work (BASE is the task's
   `Brief.base_sha`, `core/checks.py:697`; the candidate holds only `sha`
   and `turn_id`, `core/machine.py:156-158`), and nothing else:
   no verdict, finding, `predicted_failure`, requirement result, or merge
   state. The order is a weighted random order (Efraimidis and Spirakis):
   each candidate's key is `u^(1/w)`, sorted high first, where `u` is the
   first 64 bits of `sha256(task_id + ":" + str(event_id))` over 2^64, and
   `event_id` is the kernel-assigned ledger id of the candidate's first
   session `review.decided` row. The turn authors the candidate's sha but
   not that id, so it cannot steer its candidate down the list. The order
   is the same on every run and a new candidate takes its key's place. `w`
   is 4 if a done merge carried the candidate (it left the workspace
   through an `act`), else 2 if its latest session review's
   `reviewer_verdict` is `pass`, else 1. The weights are Valor's reading of
   `docs/architecture.md:402` ("weighted toward work that left the
   workspace and every `act`, with a smaller share of failures"); `audit
   scores` prints them. They set the order; the scores in item 5 weight
   each label by its stratum's population, and the strata are the weights'
   own classes. That holds when Tom labels in list order: skipping
   candidates by choice is a selection the weights cannot undo, and the
   page and `audit scores` say so. A line's place says
   only that its candidate is somewhat more likely to be in a heavier
   stratum; `pass` and `changes` candidates interleave. A merged candidate's
   verdict can be read off the repository (it merged, so its verifier
   passed); for merged work, blindness covers the findings and
   `predicted_failure` only.
5. **The scores.** `python -m core audit scores` prints, per verifier
   `model` (a review row with no `model` is grouped under `unknown`), over
   the latest real label per candidate:
   - **Units.** Each session `review.decided` row with a `reviewer_verdict`
     on a labelled candidate is one scored verdict; a candidate reviewed
     twice (a grant drops the review and re-enters checks,
     `core/machine.py:550-558`) is two verdicts against one label. Every
     `n` below counts verdicts, and the line also prints the number of
     labelled candidates.
   - **Strata.** A verdict's stratum is its `reviewer_verdict` and its
     candidate's list weight `w` (item 4), so every verdict in a stratum
     had the same chance of being labelled. For each stratum `s` the
     scores print `N_s`, the session verdicts in the ledger, and `n_s`, the
     labelled ones. Each labelled verdict weighs `N_s / n_s`, so each
     figure estimates the whole population of verdicts, not the list's
     order. The rates and the Brier score are ratio estimates: consistent,
     not exactly unbiased at small `n`.
   - **Coverage.** A figure is printed only when every stratum it sums
     over has `n_s` at least 1; otherwise it prints "not estimable" and
     names the uncovered strata. Of verifier `pass`, labelled `changes`,
     sums over the `pass` strata; of verifier `changes`, labelled `pass`,
     over the `changes` strata; false accept, false reject, and Brier over
     every stratum with `N_s` above 0. This is output formatting: nothing
     reads it.
   - **A known limit.** A candidate labelled while unmerged and merged
     afterwards moves to the `w` 4 stratum; its label then counts there,
     though it was drawn at weight 2 or 1. Not built for.
   - the raw confusion counts, verifier `pass` or `changes` against label
     `pass` or `changes`, every cell shown;
   - conditioned on the verdict: of verifier `pass`, the weighted share
     labelled `changes`, and of verifier `changes`, the weighted share
     labelled `pass`, each with its `n`;
   - false accepts (verifier `pass`, label `changes`) over label `changes`,
     and false rejects (verifier `changes`, label `pass`) over label
     `pass`, both weighted, each with its `n`;
   - the Brier score, the weighted mean of `(predicted_failure - y)^2` with
     `y` 1 for `changes`, over verdicts whose `predicted_failure` is a
     number (not a bool) in 0 to 1, with that `n`; any other value is
     skipped and counted as skipped;
   - the list weights, and, beside the figures, the count of stand-in
     labels left out and the count of real labels per `by`.
   The verifier's own answer is scored: `reviewer_verdict`, not the
   computed verdict (a `governance_refused` from a reviewer's `pass` scores
   as `pass`). Labels from the merge outcomes (below) are scored apart under
   their own source and never pooled with human labels.
6. **The status pages.** `GET /audit` shows the list (as in 4, blind) and
   nothing else; `GET /audit/scores` shows the scores (as in 5), the same
   split as `audit` and `audit scores` on the command line, from the same
   functions. The scores sit apart because a stratum's `N` and `n` beside
   the list show how many listed candidates hold each verdict, and with one
   listed candidate, which verdict it holds. The nav gains "audit", and the
   list links to the scores. Rows on the list do not link to `/task/ID`,
   which shows the verdict. GET only, escaped, read-only session, like
   every page. The pages carry no figure from `governance.adds`, instances,
   grants, or guards (see Governance).
7. **Nothing holds or sends.** No path reads the scores or labels to hold,
   refuse, redirect, or reorder work, and nothing sends the list to Tom.
   A test runs a task through merge with labels recorded on its candidates
   (including a `changes` label against a `pass`) and asserts the task's
   rows, the merge effect, and the notices are identical to a run without
   them; another asserts a review turn's dispatched text is the same with
   and without labels.
8. **Merge outcomes as labels.** Built on `core/outcomes.py` from #3612
   (see "The hand-off with #3612"): a merge `git revert` undid reads as a
   `changes` label with source `revert` on the candidate it carried.

The module is `core/audit_sample.py` (not `audit`, which is the stop
invariant's name, `core/tasks.py:830`): `record`, `labels`, `sample`,
`scores`, and `render`. `core/__main__.py` gains the `audit` subcommand
group in the shape of `merge-target` (`core/__main__.py:994-1006`).
`ui/app.py` gains `audit_page` and `audit_scores_page`.

## Threat model

The turn controls the review session's final message, and through it
`reviewer_verdict`, `predicted_failure` (range-checked at record,
`core/fresh.py:790-793`), `requirements`, and findings; it controls the
candidate's code and therefore what Tom reads. It cannot write the ledger
(`docs/architecture.md:501`). The kernel must never: accept a label from
anything but Tom's command (only `__main__` calls `record`; no turn, signal,
bridge, or performer does); let a label or a score hold, refuse, reorder, or
redirect any task or effect; send the list or a score to Tom as a question
or notice; render labels or scores into any turn's Brief (the verifier
never sees its own calibration); or put a turn-written value on the page
unescaped. The list's order key is hashed from a ledger id the kernel
assigns, not from the candidate's sha, so a turn cannot move its own
candidate out of the sample by varying its commit. The `audit` command
carries the same shell trust as every `--by` command: whoever runs it is
trusted to name themselves, and anyone but Tom, Valor's sessions
included, labels with `--role-played`. The fold reads `predicted_failure` only as a number (not a bool)
in 0 to 1 and skips any other value in the Brier term rather than trusting
it; an older or hand-written row may hold anything. The turn also controls
commit messages, so a builder can write a false "This reverts commit" line:
revert labels are scored only under their own source, never pooled with
human labels, and nothing acts on either.

## Governance

**This plan adds no check, gate, hook, validator, review round, or approval
step, and needs no grant.** By Tom's ruling of 2026-10-07
(`docs/plans/valor-rebuild-feedback.md:56-58`), a checkpoint is "a step that
looks at work and can stop it or send it back", and "A judgement that only
labels (for the ledger, for measurement) adds no governance"
(`docs/judgement-layer.md:340`). Here:

- a label is a row nothing reads to stop or send back anything (item 7);
- the list is a reading list Tom opens when he chooses; no task waits on it,
  no merge depends on it, and nothing sends it;
- the scores are measurement with no threshold and no action.

**Not a governance dashboard.** The page measures the verifier's judgement
of work quality, the evidence "Independent checks" names
(`docs/mission.md:171-173`) and the audit sample `docs/architecture.md:509`
places in the design; it does not show guards, restraint, or governance.
The code matches: no figure on the page or in `audit scores` comes from
`governance.adds`, instances, grants, or guards, and a test asserts it. A
reviewer `pass` the kernel recorded as `governance_refused` scores as the
reviewer's `pass`, so the governance outcome never enters a figure. The
`ui/README.md` Scope line this task adds ("the audit list and the
verifier's calibration") states the same point beside its Not-here line.

If Tom, reading a merged candidate, finds a defect, he gives feedback
through `python -m core feedback`, which exists; this plan adds no path from
a label to `patch`.

The refusal in item 2 is the command line's existing input handling, built
the same way (`core/__main__.py:312`). If the blind verifier's
`governance.adds` still flags a hunk of this diff, that is an incident
against the classifier, answered by a classifier change, never by a grant
(`docs/judgement-layer.md`, note 6, as amended by task 80e49c02). This
task's merge then waits on that classifier change, not on Tom.

**The half not built, and what its grant would cover.** Lowering the
highest class Valor commits without Tom on a degrading series
(`docs/architecture.md:406-407`) holds merges on a judgement: a gate. It
serves the constraint **Reliable stop, recovery, and correction**
("Autonomy shrinks automatically on evidence"). It has no incident: no
merged work has been found wrong. This plan is what would produce one: the
first `audit.labelled` `changes` on a candidate a done merge carried whose
verifier said `pass` (a false accept on merged work) is that incident. The
proposal then needs Tom's tap for exactly one instance: the kernel lowering
`max_effect_class` for new tasks (so merges are held for his tap) when the
verifier's false-accept count or Brier score passes a ceiling Tom sets,
ledgered as a guard with that incident, that mission item, and a ninety-day
expiry; raising it back stays only Tom's decision, as a grant. Nothing of it
is built or stubbed here.

## Stakes

Kernel code and a new stored row type: `critique_rounds: 2`,
`review_rounds: 2`. No migration, no money path, no effect.

## Tech debt absorbed

- `docs/architecture.md:509` and `:402-409`, `docs/mission.md:171` and
  `:396`: brought to the status quo by the docs check (the audit list and
  the label row are built; the lowering is design).
- `docs/data.md`: the row table gains `core/audit_sample.py`,
  `audit.labelled`, and the `audit` stream; line 126's `predicted_failure`
  gains its reader.
- `ui/README.md` Scope lists the audit view; `core/README.md` lists the
  module.
- `docs/judgement-layer.md` "Where labels come from" gains the audit
  labels and reverted merges as label sources for the blind verifier, and
  says that anyone but Tom, Valor's sessions included, labels with
  `--role-played`.

None found in code.

## Left out

- **Lowering the highest class** (Governance above).
- **Labelling from the local page.** Cut, as Valor's decision. The issue
  asks for labels "recorded from the command line and the local page"; the
  command line is built. The status page is read-only by design
  (`ui/README.md:9`, `ui/app.py:1-5`), and the local chat page posts
  messages, not other rows (`bridges/local/__init__.py:1-11`), so a label
  form there is a new write surface with its own token threat model. It is
  a follow-up task, not a question.
- **A fixed share or a quota.** No source gives a fraction; the weighted
  order in item 4 is the sampling, and the labels Tom chooses to give are
  the sample.
- **Scoring `requirements`** per requirement, `review.compared` rows at
  other seats, and the emulator judge's calibration (`docs/emulator.md:500-507`).
- **`delivery.used` as a label.** A use mark says someone used a merge, not
  that the candidate was right, and its absence says nothing; it is not
  scored.
- **Counting labels in the attention log.** A label is attention Tom chose
  to spend, not a decision escalated to him (`docs/mission.md:139-142`).

## The hand-off with #3612

**What this plan needs from #3612.** For each done merge of a task, keyed
by its `effect_id`: whether a commit on the target branch reverts a commit
that merge brought in, or unknown when no repository can tell. At the
rebase, the names #3612 merged are mapped to that statement. If #3612
merges without a per-merge revert reading, item 8 is dropped as if #3612
were stopped.

The names below are those of #3612's plan as it stands; they are what the
mapping starts from, not a contract. #3612's plan (`docs/plans/i3612-merge-outcomes.md`, commit `0027e237f`)
builds `core/outcomes.py`. `outcomes.done_merges(conn, url=None,
branch=None)` is the cross-task query of done merges: task id, `effect_id`,
head, url, branch, outcome time, and `landed` (`{before, commits, paths}`
from the merge's `effect.intent`, the task's own commits only, None for
older merges or when unreadable). `outcomes.revert(brief, merge)` gives
`revert`, None when no repository can tell, or `on_branch`, `reverted_by`
(each commit whose body says it reverts a commit in `landed.commits`, with
its sha; None when `commits` is None), `source`, and `as_of`.
`after_merge(conn, brief, rows)` assembles them per task. It also adds
`delivery.used` rows (`delivery_event_id`, `candidate`, `effect_id`,
`head_sha`, `note`, provenance). A merge does not name the candidate:
a merge's `head_sha` is the docs head when docs committed
(`core/verdicts.py:519`), so the candidate is read from the task's
`effect.held` row with the same `effect_id`, `payload.candidate.sha`
(`core/verdicts.py:520`).

This task owns the join, so nothing depends on #3612's builder knowing of
it:

- `audit_sample.labels(conn)` returns rows
  `{task_id, candidate_sha, label, source, provenance, event_id}`.
  `audit.labelled` rows give `source: "tom"` (a real label, whoever `by`
  names) or are counted apart when role-played.
- `labels` reads `outcomes.done_merges(conn)` once and, per task, the
  merges' `revert` readings; for each merge whose `revert.reverted_by` is
  not empty, yields label `changes`, source `revert`, provenance
  `{by: "git", via: "revert of <sha>", role_played: false}` on the merge's
  candidate. A merge with `revert` None, a `reverted_by` that is
  None or empty, or `on_branch` false alone yields no label: no revert seen is not a `pass`.
- `scores` computes every figure per source; `revert` figures print under
  their own heading with their own `n`, as `docs/judgement-layer.md:293-294`
  counts a record's labels by origin. Only `scores` and `/audit/scores` call it,
  so the git reads run only when asked.

**Order.** Build starts now; item 8 and its tests are built on a rebase
onto #3612's merge, and this task merges after #3612. That keeps items 1
to 7 waiting on a plan still in revision; it is Valor's call, made by the
lead: keep the order. The other choice, merging 1 to 7 first and adding
item 8 in a later task, stays open to the lead. `labels` reads #3612's
outcomes from a third place beside its two status surfaces; the docs pass
names it wherever #3612's docs list the callers. If the lead stops
#3612, item 8 is dropped and the docs say no outcome source exists. The
docs pass adds one line to `docs/judgement-layer.md` "Where labels come
from": Tom's audit labels and reverted merges label the blind verifier's
verdicts, scored apart by source.

## Tests

New `tests/test_audit_sample.py` (real Postgres, `VALOR_TEST_DB`), and one
page test in `tests/test_ui.py`. Fixtures write session `review.decided`
rows through `scripted.check` and `scripted.checks` (`tests/scripted.py:289-308`),
which ask the scripted governance judgements one per hunk and pass
`predicted_failure` and `model` through `**kw`.

- Label row shape and provenance; `--role-played` sets it.
- Refusals: unknown task; a sha with no review; a candidate with only a
  `kernel` review; a manual review; label `maybe`; each writes no row.
- The task's stream is untouched by a label: `ledger.read(task)` before and
  after are equal, and `machine.fold` gives the same state.
- Relabel: latest wins in `scores`; both rows remain.
- Order: the key is `u^(1/w)` from the candidate's hash, stable across two
  runs; a new candidate does not reorder the others; a held or refused
  merge weighs as unmerged; a candidate whose task was patched (an older
  candidate) is listed on its own line.
- Blindness: the list's text and the page's list contain no
  `reviewer_verdict`, finding text, `predicted_failure` value, merge state,
  or `/task/` link (fixture findings use a marker string); on a fixture of
  unmerged `pass` and `changes` candidates with fixed shas, the two kinds
  interleave in the printed order, and the columns are the same for both.
- A stand-in label leaves the candidate on the list and out of every
  figure, and is counted apart. A label with `--by alice` and no
  `--role-played` is, by `ledger.provenance`'s definition, Tom's own: it
  is scored under source `tom` and printed under the `by` it names.
- A later stand-in label does not override a real one: Tom labels `pass`,
  then a role-played `changes`; `pass` is scored.
- Scores, exact on a fixture for one model. Verdicts: V1 `pass`, merged,
  0.2, label `pass`; V2 `pass`, unmerged, 0.6, label `changes`; V3
  `changes`, unmerged, absent, label `pass`; V4 `pass`, merged, unlabelled;
  V5 `changes`, unmerged, unlabelled. Strata (verdict, `w`): (`pass`, 4)
  N 2 n 1, (`pass`, 2) N 1 n 1, (`changes`, 1) N 2 n 1. Weights: V1 2,
  V2 1, V3 2. Raw
  confusion pass/pass 1, pass/changes 1, changes/pass 1, changes/changes 0.
  Of verifier `pass`, labelled `changes` 1/3 (n 2); of verifier `changes`,
  labelled `pass` 1 (n 1); false accepts 1 (n 1); false rejects 0.5 (n 2);
  Brier 0.08 (n 2; unweighted it would be 0.1). Strata printed with `N_s`
  and `n_s`; a stratum (`changes`, 4) with N 0 prints 0.
- Coverage: a fixture where (`changes`, 1) has N 2 and n 0 prints the
  `pass`-conditioned rate and prints "not estimable" for false accept,
  false reject, and Brier, naming (`changes`, 1).
- The order key: a candidate's key is the same whatever its sha, given the
  same first review row id; a candidate reviewed `pass` after an earlier
  `changes`, unmerged, weighs 2.
- Two reviews of one candidate by the same model (a grant re-enters checks)
  are two verdicts against one label: `n` 2, labelled candidates 1.
- A `predicted_failure` that is a string, 1.5, -0.1, or `true` (rows written
  straight to the ledger) is skipped in the Brier term, `n` excludes it, and
  the skipped count shows it.
- A session review row with no `model` is scored under `unknown`.
- Revert labels (on #3612's merge): a merge whose `reverted_by` is not
  empty labels its candidate `changes` under source `revert`, scored apart
  from Tom's; `revert` None, a `reverted_by` that is None or empty, and
  `on_branch` false alone give no label; the candidate is the held payload's, not the docs
  head.
- Two models on one candidate are scored apart.
- A reviewer `pass` recorded as `governance_refused` scores as `pass`.
- `review.compared` rows are not scored.
- The scores and the page carry no `governance.adds`, instance, grant, or
  guard field (keys of `scores`' result and the page's text).
- A review turn's Brief has no audit content: `tasks.dispatch(conn, task,
  fresh="review")` returns the same text before and after labels are
  recorded (`turn.started` stores that text, `core/runs.py:155-167`, so this
  covers what a turn is given).
- Item 7's end-to-end: a merge run with and without labels yields the same
  task rows (ids aside), the same merge effect, and no notice for the audit
  stream.
- `/audit` renders, escapes a hostile instruction, refuses POST with 405,
  and carries no score or stratum count; `/audit/scores` refuses POST,
  escapes a hostile model name, and its figures equal `audit scores`.

Suites: `tests/test_audit_sample.py`, `tests/test_ui.py`,
`tests/test_review.py`, `tests/test_corrections.py`, then the full suite;
`tests/test_outcomes.py` exists only after #3612 and is run on the rebase.

## Critique round 1

Critic `critic-3611-r1`, verdict `revise`; the lead accepted all eight
findings. Each is addressed; none was refused.

1. **The order showed the verdict.** One list, no groups, no merged column;
   a stable weighted random order (`u^(1/w)` from the candidate hash,
   weights 4, 2, 1, printed with the scores); merged work's verdict is
   inferable from the repository, stated in item 4; the blindness test
   asserts `pass` and `changes` interleave and share columns.
2. **The figures were biased by the sampling.** Every figure is now a
   stratified estimate: strata are verdict by merged, each labelled verdict
   weighs `N_s / n_s`, the verdict-conditioned rates are reported, the
   label-conditioned false accept and false reject are reweighted, and the
   population counts print beside them. Strata include merge state, not
   verdict alone, because the list weights depend on it. The fixture test
   is rewritten (Brier 0.08 weighted against 0.1 raw).
3. **The questions were answered.** The Questions section is removed; the
   local-page cut is under Left out as Valor's decision and a follow-up.
4. **The page and "no governance dashboard".** A Governance paragraph says
   what the page measures; no governance figure enters the code; a test
   asserts it; the `ui/README.md` Scope line carries it.
5. **Missing tests.** Added: two reviews of one candidate (denominators
   count verdicts; labelled candidates printed too); a later stand-in label
   does not override (and "real" is `role_played` false, whatever `by`
   says); bad `predicted_failure` values; a row with no `model` under
   `unknown`; the Brief has no audit content, through `tasks.dispatch`.
6. **The refusal.** Built as the command line's lookup failure, in the
   `__main__` path, citing `core/__main__.py:312`; the forecast is
   replaced by the case where it is flagged, and that the merge then waits
   on the classifier change, not Tom.
7. **The fixture citation.** Now `tests/scripted.py:289-308`.
8. **The #3612 hand-off.** "The hand-off with #3612" writes the join
   against what that plan builds (`after_merge`'s `revert`, the held
   effect's candidate, `merge.used` not a label). This task owns the join
   and merges after #3612, so nothing rests on #3612's builder; the docs
   pass adds the line to "Where labels come from".

## Critique round 2

Critic `critic-3611-r2`, verdict `revise`. The lead accepted all six
findings, folded here as written, and sent the plan to build with no third
critique: both critique rounds are spent, the findings are concrete, and
none touches governance. Round 1's claim that "no choice of weights biases
a figure" is withdrawn.

1. **Strata with no labels skewed the figures.** Item 5's Coverage rule:
   a figure prints only when every stratum it sums over has `n_s` at least
   1, else "not estimable" with the uncovered strata named; the strata each
   figure needs are listed. A test covers (`changes`, 1) with N 2, n 0.
2. **Strata did not match the design, and one weight was undefined.** `w`
   is now 4 if a done merge carried the candidate, else 2 if its latest
   session review is `pass`, else 1, so every candidate has one. The
   stratum is (`reviewer_verdict`, `w`). The fixture's figures are
   unchanged. The assumption (labels taken in list order), the ratio
   estimates' small-`n` bias, and the known limit (a candidate merged after
   its label changes stratum) are written into items 4 and 5 and printed
   on the page and by `audit scores`.
3. **A turn could steer the order key.** `u` is hashed from the task id
   and the ledger id of the candidate's first session `review.decided`
   row, which the kernel assigns. Chosen over a seed in kernel config: it
   needs no new setting and the turn cannot choose it. The threat model
   says so; a test asserts the key does not depend on the sha.
4. **Who counts as the labeller.** Wording only, no check added. The
   `alice` test now reads a non-`tom` `by` without `--role-played` as Tom's
   own by provenance's definition, printed under that `by`. The docs line
   says anyone but Tom, Valor's sessions included, labels with
   `--role-played`; the threat model states the shell trust.
5. **The #3612 hand-off named identifiers.** "What this plan needs from
   #3612" states the dependency as what it means; the names are mapped at
   the rebase; with no per-merge revert reading, item 8 is dropped. The
   docs pass corrects #3612's "only the two surfaces" sentence. The order
   (items 1 to 7 wait for #3612's merge) is kept as the lead's call; the
   other choice is named.
6. **Small points.** `tests/test_outcomes.py` is run on the rebase. The
   diff's BASE is `Brief.base_sha`, cited in item 4.

## Build record

Built by `builder-3611` on branch `i3611-audit-sample`, from `i3611-plan`
at 1cf124090, items 1 to 7 and their tests.

Item 8 was built by `builder-3611b` after a rebase onto #3612's merge
(7f1d2c300); see "Item 8" below.

What was built:

- `core/audit_sample.py`: `reviewed` (the lookup), `record`, `labels`,
  `in_force`, `sample`, `scores`, `figures`, `key`, `weight`, and two
  renderers, `render_list` and `render_scores` (the plan's `render`, split
  by what it renders). A merged candidate is a merge's `effect.held` row
  naming it with an `effect.outcome` `done` for the same effect.
- `core/__main__.py`: `audit`, `audit label`, `audit scores`. The label
  value is an argparse choice; an unknown task and a candidate with no
  session review are refused in the command path before `record`, writing
  nothing.
- `ui/app.py`: `GET /audit` (the list only), `GET /audit/scores`, and
  the nav entry; the scores page is `render_scores`' text, so it and
  `audit scores` print the same figures. `ui/README.md` Scope and `core/README.md` carry the lines.
- Tests: `tests/test_audit_sample.py` (24 tests, every case the Tests
  section names for items 1 to 7) and one page test in `tests/test_ui.py`.

Departures from the plan's wording, none in behavior:

- The scoring fixtures write session `review.decided` rows straight into
  the ledger rather than through `scripted.check`, since the scores read
  rows and nothing else, and each fixture needs exact shas and many
  candidates. The end-to-end test (item 7) runs two scripted tasks through
  `scripted.check` and the merge, labels one from the command line, and
  covers the Brief through `tasks.dispatch(..., fresh="review")`.
- "Real labels per `by`" counts the labels in force (latest real label
  per candidate), not every row, so a relabel counts once.
- A forecast that is absent is counted as `absent`, apart from `skipped`
  (present and not a number in 0 to 1).

Docs left for the docs check: `docs/data.md` (the row and stream),
`docs/architecture.md:509` and `:402-409`, `docs/mission.md:171` and
`:396`, and `docs/judgement-layer.md` "Where labels come from".

Suite, without `tests/test_container.py` (run concurrently by other
agents) and `tests/test_harness_contract.py`: 1614 passed, 25 skipped, 7
failed, 42 errors, on a disk at 100% with other suites running. The 42
errors are `initdb` and pytest temp folder failures from the full disk.
Every failing and erroring file, rerun alone, passes:
`test_credentials`, `test_telegram_gap`, `test_telegram_inbound`,
`test_telegram_outbox`, `test_telegram_pipeline`,
`test_provision_restart_gaps`, `test_transcripts`, `test_audit_sample`,
and `test_ui`. `test_review` had 4 timeouts when rerun with them
("the turn never started"), and all 11 of its failing cases pass alone.
`tests/test_harness_contract.py`'s `claude_code` cases end `failed` and
the stop case hangs; the same cases fail the same way at 1cf124090, and
this diff touches no harness. Ruff check and format are clean.

### Item 8

Built by `builder-3611b` on the rebase onto 7f1d2c300. The `ui/app.py`
conflict kept both: #3612's After merge table and index column, and
`GET /audit`; `core/README.md`, `ui/README.md`, and `tests/test_ui.py` keep
both sides too.

The mapping. #3612 merged a per-merge revert reading:
`outcomes.revert(brief, merge)` returns `on_branch`, `reverted_by` (each
commit on the target after the head whose body says it reverts one of the
merge's `landed.commits`; None when those are None), `as_of`, and `source`,
or None with why. `outcomes.done_merges(conn)` gives every done merge with
its `landed`. So item 8 is built as "The hand-off with #3612" says:

- `audit_sample.reverted(conn)` reads `done_merges` once, the candidate of
  each from its `effect.held` row (`payload.candidate.sha`, not the docs
  head), each task's Brief once, and each merge's `revert` reading in a
  worker thread (`git.threaded`). A merge whose `reverted_by` is not empty
  gives label `changes`, source `revert`, provenance `{by: "git", via:
  "revert of <the first reverted commit>", at: <the cache's as_of>,
  role_played: false}`, ordered by the merge's outcome row id. Nothing is
  stored.
- `labels(conn)` is the recorded rows (`recorded`, the former `labels`)
  then the revert labels. The list (`sample`) reads `recorded` only: it is
  the list of candidates without Tom's label, and the `audit` command runs
  no git.
- `scores` has `sources["tom"]` and `sources["revert"]`, each per model;
  `render_scores` prints a `## <model>, labels from revert` block apart.
  A revert label lands on a merged candidate, so its figures sit in the
  `w` 4 strata and the others print "not estimable" until labelled.
  `labels_by` and the stand-in count stay Tom's.
- Docs: `core/outcomes.py`'s docstring and `core/README.md` name the audit
  scores as the third reader of revert readings;
  `docs/sdlc-state-machine.md`'s "After a merge" says the same.

Tests in `tests/test_audit_sample.py` (2 new; they reuse
`tests/test_outcomes.py`'s `World` on real git): a reverted merge labels
its candidate (not the docs head) `changes` under `revert`, scored apart
(Tom's source has no verdict), printed apart, and the candidate stays on
the list; a private target (`revert` None), no revert (`reverted_by`
empty), unrecorded commits (`reverted_by` None), and a branch moved off
the head (`on_branch` false) give no label. The governance-key test now
walks every source.

Suite at this head, without `tests/test_container.py` and
`tests/test_harness_contract.py`: 1702 passed, 25 skipped, 5 failed. Three
fail at base as the #3612 merge record lists (the fresh-session test in
`tests/test_workspace.py`, two `tests/test_pi.py` cases). The two
`tests/test_ports.py` failures found no four free ports in a row while
other suites ran; the file passes alone. `tests/test_audit_sample.py`,
`tests/test_ui.py`, `tests/test_outcomes.py`, and `tests/test_broker.py`:
74 passed. Ruff check and format pass.

### Patch round 1

From the blind review (`review-3611`), both accepted by the lead:

- The scores moved off `/audit` to `GET /audit/scores`. On one page with
  the list, each `(verdict, w)` stratum's `N` and `n` showed how many
  unlabelled candidates of a weight class hold each verdict, so with one
  listed candidate (or a class all of one verdict) a verdict was readable
  before Tom labelled it, which item 4 avoids. `/audit` is the list only and
  links to the scores, as `audit` and `audit scores` split on the command
  line. Item 6, the Tests section, `ui/README.md`, and the `ui/app.py`
  docstring say so. The test in `tests/test_ui.py` asserts `/audit` has no
  stratum count, model, or verdict, and `/audit/scores` escapes a hostile
  model name and equals `render_scores`.
- Item 7's end-to-end test compared the two runs' row types in order. The
  scripted governance judgements fan out at once, so `gateway.charged` and
  `judgement.answered` rows interleave differently from run to run (2 of 6
  runs failed). It now compares the row types as a multiset and in order
  with those two types set aside; the checks on state, the merge effect,
  and the audit stream are unchanged. It passed 10 of 10 runs alone.

## Merged

- Checks on `c0816b2c2`, based on `7f1d2c300`:
  - **Test: `red`.** It found two flaky tests and no product fault. The full suite, without `tests/test_container.py` and `tests/test_harness_contract.py`, showed only the base failures: the workspace fresh-session test and two `tests/test_pi.py` cases.
  - **Review: `changes`.** It found two things: item 7's end-to-end test ordered concurrent rows, and the scores beside the list showed verdicts by stratum. Governance: no.
  - **Docs: `updated`.** `d3c98d5a7`: data, architecture, mission and judgement-layer. The replay baseline moved to `docs/judgement-baseline.md`.
- Patch round 1, `60b078164`:
  - `/audit` shows the list only, and the scores are on `/audit/scores`.
  - Item 7's test compares the concurrent rows as a multiset.
  - Review of the patch: `pass`. Governance: no.
- Patch `5784185a0`: `test_no_revert_seen_gives_no_label` fetches after each merge, so each head reaches the cache before the next force push. It passed 12 of 12 runs alone. `tests/test_audit_sample.py`, `tests/test_ui.py` and `tests/test_outcomes.py`: 69 passed.
- Backup `valor_rebuild-20261008T124449Z.dump`, taken before the merge.
