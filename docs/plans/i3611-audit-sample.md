---
tracking: none
slug: i3611-audit-sample
type: plan
status: planned
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
fold. This is question 2 below.

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
   `review.decided` row with a `reviewer_verdict` in that task's ledger; any
   other task, sha, or label value is refused with the reason, and nothing is
   written. (Input validation of Tom's own command: there is nothing else to
   label.) A calibration task or a legacy task has no such row and so is
   refused by the same rule.
3. **Relabelling.** A second label on the same candidate is a new row; the
   fold reads the latest by row id and the old one stays in the ledger.
4. **The list.** `python -m core audit` prints the candidates not yet
   labelled by Tom himself, in this order:
   1. candidates a done merge carried (they left the workspace through an
      `act`), newest merge first;
   2. other candidates whose latest session review's `reviewer_verdict` is
      `pass`, newest first;
   3. candidates every session review sent back (`changes` only), newest
      first: the smaller share of failures.
   Each line shows the task id, the instruction, the base and candidate
   sha, whether it merged, and the mirror's `git diff BASE SHA` command to
   read the work. It shows no verdict, finding, `predicted_failure`, or
   requirement result. A candidate labelled only by a stand-in
   (`role_played`) stays on Tom's list.
5. **The scores.** `python -m core audit scores` prints, per verifier
   `model`, over Tom's own latest labels (no `role_played` label enters a
   figure):
   - the confusion counts, verifier `pass` or `changes` against label `pass`
     or `changes`, every cell shown;
   - false accepts (verifier `pass`, label `changes`) over labelled
     `changes`, and false rejects (verifier `changes`, label `pass`) over
     labelled `pass`, each as a count and its denominator;
   - the Brier score, the mean of `(predicted_failure - y)^2` with `y` 1 for
     `changes`, over the verdicts that carry `predicted_failure`, with that
     `n`;
   - `n` labelled candidates, how many merged, and, beside the figures, the
     count of stand-in labels left out.
   Each session `review.decided` row on a labelled candidate is one scored
   verdict (a candidate reviewed twice is two verdicts against one label).
   The verifier's own answer is scored: `reviewer_verdict`, not the
   computed verdict (a `governance_refused` from a reviewer's `pass` scores
   as `pass`).
6. **The status page.** `GET /audit` shows the list (as in 4, blind) and the
   scores (as in 5), from the same functions the command line calls. The nav
   gains "audit". Rows on the list do not link to `/task/ID`, which shows
   the verdict. GET only, escaped, read-only session, like every page.
7. **Nothing holds or sends.** No path reads the scores or labels to hold,
   refuse, redirect, or reorder work, and nothing sends the list to Tom.
   A test runs a task through merge with labels recorded on its candidates
   (including a `changes` label against a `pass`) and asserts the task's
   rows, the merge effect, and the notices are identical to a run without
   them.

The module is `core/audit_sample.py` (not `audit`, which is the stop
invariant's name, `core/tasks.py:830`): `record`, `labels`, `sample`,
`scores`, and `render`. `core/__main__.py` gains the `audit` subcommand
group in the shape of `merge-target` (`core/__main__.py:994-1006`).
`ui/app.py` gains `audit_page`.

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
unescaped. The fold reads `predicted_failure` only as a number in 0 to 1 and
skips any other value in the Brier term rather than trusting it.

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

If Tom, reading a merged candidate, finds a defect, he gives feedback
through `python -m core feedback`, which exists; this plan adds no path from
a label to `patch`.

If the blind verifier's `governance.adds` flags a hunk of this diff (the
refusal in item 2 is the likeliest), that is an incident against the
classifier, answered by a classifier change, never by a grant
(`docs/judgement-layer.md`, note 6, as amended by task 80e49c02).

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

None found in code.

## Left out

- **Lowering the highest class** (Governance above).
- **Labelling from the local page.** The status page is read-only by design
  (`ui/README.md:9`, `ui/app.py:1-5`); the local chat page posts messages,
  not other rows (`bridges/local/__init__.py:1-11`). A label form there is a
  new write surface with its own token threat model; it waits for Tom to
  want it (question 1).
- **Issue #3612's outcomes as labels.** Not built yet. `labels(conn)`
  returns `{task_id, candidate_sha, label, source, provenance}`; this task
  writes `source: "tom"`. Whichever of the two tasks lands second adds the
  outcome source there (a reverted merge reads as `changes`), and `scores`
  reports each source's count apart, as `docs/judgement-layer.md:293-294`
  does for a record.
- **A random draw or a fixed share.** No source gives a fraction; the order
  in item 4 is the weighting, and Tom's labels are the sample.
- **Scoring `requirements`** per requirement, `review.compared` rows at
  other seats, and the emulator judge's calibration (`docs/emulator.md:500-507`).
- **Counting labels in the attention log.** A label is attention Tom chose
  to spend, not a decision escalated to him (`docs/mission.md:139-142`).

## Tests

New `tests/test_audit_sample.py` (real Postgres, `VALOR_TEST_DB`), and one
page test in `tests/test_ui.py`. Fixtures write `review.decided` rows
through `verdicts.record_check` with `leg="session"` on tasks driven to the
needed state, as `tests/test_review.py:252-286` does.

- Label row shape and provenance; `--role-played` sets it.
- Refusals: unknown task; a sha with no review; a candidate with only a
  `kernel` review; a manual review; label `maybe`; each writes no row.
- The task's stream is untouched by a label: `ledger.read(task)` before and
  after are equal, and `machine.fold` gives the same state.
- Relabel: latest wins in `scores`; both rows remain.
- Order: a merged candidate above a newer unmerged `pass` above a newer
  `changes`-only candidate; a held or refused merge does not count as
  merged; a candidate whose task was patched (an older candidate) is listed
  on its own line.
- Blindness: the list's text and the page's list contain no
  `reviewer_verdict`, finding text, `predicted_failure` value, or
  `/task/` link (fixture findings use a marker string).
- A stand-in label leaves the candidate on Tom's list and out of every
  figure, and is counted apart.
- Scores, exact on a fixture: verdicts (`pass`, 0.2, label `pass`),
  (`pass`, 0.6, label `changes`), (`changes`, absent, label `pass`) give
  confusion pass/pass 1, pass/changes 1, changes/pass 1, changes/changes 0;
  false accepts 1 of 1; false rejects 1 of 2; Brier 0.1 with n 2.
- Two models on one candidate are scored apart.
- A reviewer `pass` recorded as `governance_refused` scores as `pass`.
- `review.compared` rows are not scored.
- Item 7's end-to-end: a merge run with and without labels yields the same
  task rows (ids aside), the same merge effect, and no notice for the audit
  stream.
- `/audit` renders, escapes a hostile instruction, refuses POST with 405,
  and its figures equal `audit scores`.

Suites: `tests/test_audit_sample.py`, `tests/test_ui.py`,
`tests/test_review.py`, `tests/test_corrections.py`, then the full suite.

## Questions for Tom

1. **Where do you want to label?** The terminal now (`python -m core audit
   label ...`), or on the local chat page as well? Assumed: the terminal
   for now; the page when you ask for it.
2. **Blind or confirm?** Labelling blind (you judge the work without seeing
   what the verifier said) costs more of your time per label but gives a
   true measure; confirming a shown verdict is quicker and biased toward
   agreeing. Assumed: blind, as the design says.
