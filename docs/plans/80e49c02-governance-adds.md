---
tracking: none
slug: 80e49c02-governance-adds
type: plan
status: planned
critique_rounds: 2
review_rounds: 2
---

# governance.adds counts only a step that judges work and can stop it

Kernel change to the blind verifier's question (task 80e49c02eac7). It adds
no check, gate, hook, or review step; it narrows what the existing one counts.

**Incident.** Review row 1436 on task 09975a1c2e52, the expiry-lock fix for
review N4. `governance.adds` answered true on two hunks: the advisory lock in
`core/routines.py`, and the `docs/routines.md` sentence describing it. The
Opus reviewer wrote "This is not governance". A reviewer cannot remove an
instance (`docs/judgement-layer.md`, note 6), so a confirmed fix waited on
Tom's tap.

**Tom's ruling (2026-10-07).** Count as a new checkpoint only a step that
looks at work and can stop it or send it back. An ordinary fix that makes
code correct, like a lock that makes two runs take turns, is no checkpoint,
the way tests already are.

**Stakes.** This question decides which diffs wait on Tom; worded too
narrowly, a real gate merges without his tap, and worded as now, correct
fixes stall behind one.

## What is built

1. **The ruling, recorded.** `docs/plans/valor-rebuild-feedback.md` gets a
   section "Tom's ruling on governance.adds (2026-10-07)": the ruling in his
   terms, the incident (row 1436, task 09975a1c2e52, the two hunks), and
   what it changes (the question's wording, reuse by task digest, note 6).

2. **The question** (`core/judgement_tasks.py`, `GOVERNANCE`, question
   `adds`). The text keeps its opening ("Does this hunk add a check, gate,
   hook, validator, review round, or approval step") and replaces the gloss
   with section 4's wording: a step that judges work, a request, or an
   action and holds, redirects, or refuses it on that judgement, or a step
   someone must pass. Tests and the code that serves them (fixtures,
   helpers, scripted stand-ins, recording scripts) are none of these, even
   where they exit early or refuse to run; nor is code that makes the work
   itself correct (a lock or a transaction that makes two runs take turns);
   nor is prose that only describes what code does.
   The `false` label becomes: "it adds none of these: only tests and the
   code that serves them, code that makes the work itself correct, or prose
   that describes what code does". The `true` label stays.
   `calibrated` stays `None` (no passing record exists); the comment above
   it is unchanged. The task digest changes, which is intended: every row
   this question answers from now on carries a new `task_sha256`.

3. **Reuse by task digest** (`core/judgement_sites.py`, `governance().one`).
   Today a hunk reuses any settled row with the same hunk id and
   `inputs_sha256`, so after step 2 a rerun of review would reuse the
   answer to the old question. `governance` computes
   `judgement.task_sha256(GOVERNANCE, port.signature())` once, and `mine`
   keeps only rows whose `task_sha256` equals it. That filter applies to
   both the settled reuse and the spent-reruns reuse, so a changed question
   (or a changed leg prompt or model, which the digest also covers) is asked
   fresh with its own reruns. The docstring's "the same input" becomes "the
   same input under the same question".
   `governance_outcome` counts spent reruns with `judgement.unanswered_count`,
   which keyed on hunk and inputs only, so old failures counted toward a new
   question's limit and a hunk became unjudged, a needless tap, one run
   early. Built after critique: `unanswered_count` also matches
   `task_sha256` when the key names it, beside `inputs_sha256`, and
   `governance_outcome` passes the row's own. The breadth site's call does
   not name it and is unchanged.

4. **Note 6** (`docs/judgement-layer.md`, "The ten use shapes", item 6)
   gains one sentence after "a reviewer can add instances and cannot remove
   one": a reviewer's note that contests an instance is an incident against
   `governance.adds`, answered by a change to the classifier, never by a
   grant.

## Tests

In `tests/test_judgement_sites.py`, beside the existing governance tests:

- **A reworded question is asked fresh.** Run `governance` on a candidate
  with a scripted port, then run it again with `GOVERNANCE` replaced (via
  `dataclasses.replace`, passed through `monkeypatch` on the module's
  `GOVERNANCE`) by one whose question text differs. The second run returns
  new judgement ids for every hunk, the scripted port records a call per
  hunk, and the new rows carry the new `task_sha256`. A third run with the
  reworded question reuses the second run's ids with no calls (the existing
  reuse still holds under one question).
- **Spent reruns are per question.** A hunk that failed `UNANSWERED_RUNS`
  times under the old question is asked again under the new one, not
  returned as its last failure; one failure under the new question leaves
  a rerun, and the second makes it unjudged.
- **The wording.** One test asserts the question names what it excludes:
  the text contains "makes the work itself correct" and "only describes what
  code does", and the `false` label names both. This pins documented
  behavior (Tom's ruling), not a new check.

Suites run: `tests/test_judgement_sites.py`, `tests/test_judgement.py`,
`tests/test_review.py`, `tests/test_pipeline.py`, `tests/test_docs_runner.py`,
then the full suite; pre-existing failures separated by running them on the
base commit.

## Rollout on Valor's Mac (not built here)

`governance.adds.json` lives only on Valor's Mac, under
`~/src/valor-demo/items/judgement/`. These are section 4's steps 2 and 3.

1. **Labelled cases** (section 4, step 2). Add the four cases section 4 of
   `~/src/valor-build-notes/philosophy-governance.md` names to
   `governance.adds.json`, each with its hunk text, path, paths, and Tom's
   label, source `tom`. Two are the incident's hunks, both labelled
   `false`: the advisory lock hunk in `core/routines.py` and the
   `docs/routines.md` sentence describing it, both from task 09975a1c2e52.
   The other two are as the advisor's file names them; I could not read
   that file from this workspace (the sandbox refuses paths outside it), so
   the rollout reads them there.
2. **Calibration run 4** (section 4, step 3). Run the governance
   calibration on the updated case file against the merged question. If the
   entry check passes, land `GOVERNANCE.calibrated` on the record's
   `task_sha256` in a follow-up commit and record the run in
   `docs/plans/m1-4b-records.md`; if it fails, record the failing cases
   there and leave `calibrated` `None`.

## Out of scope

- Steps 2 and 3 in this workspace (above).
- Any new check, gate, hook, or review step; any change to how instances
  are made, granted, or refused.
- Re-judging task 09975a1c2e52: once this merges, its next review run
  asks the new question because the digest changed (step 3), with no
  extra work.
- `CLAUDE.md`, the persona, and the README governance paragraphs are
  unchanged; the ruling refines the classifier's reading, not the rule.
