# A3 records

The build record of [a3-docs-runner.md](a3-docs-runner.md).

## Docs is registered without a passing record

`Check.DOCS` is in `core/__main__.runners`, and `GOVERNANCE.calibrated`
is `None`: no governance calibration record has passed its entry check.
Governance still answers on every review and docs diff and logs every
row; an instance still needs Tom's tap before a merge.

## Why governance calibration run 4 failed

1. The question's gloss ("judges work, a request, or an action and holds,
   redirects, or refuses it") is wider than Tom's ruling ("looks at work
   and can stop it or send it back"). A parser refusing malformed input,
   a lock telling a second run "already running", and a refusal of an
   unreadable request all match its verbs. The open-weight leg's notes
   say so in those words.
2. Hunks come from `git diff -W`, so the legs judge the enclosing
   function, not only the added lines. The lock hunk's context holds an
   unchanged `raise Refused`.
3. Jev lands just under its 0.65 floor on test hunks (0.59 to 0.64), a
   plan document (0.59), and notice text rendering (0.55 to 0.57). It
   abstains there, and an abstention counts as caution.
4. The open-weight provider answered 429 to 22 of run 4's 58 calls (16 on
   `false` cases) and to 22 of the 118 diagnostic calls, with no
   Retry-After, and the next call went through. Calibration asks each leg
   alone, so each 429 was scored wrong. `judgement_sites.calibrate` now
   asks a rate-limited case again (`asked_again` on the record).
5. Both legs vary between identical calls: open weight gave 0.95 and 0.05
   on the same lock case.

A revised gloss and rubric are in section 4 of the plan, not built.

## Diagnostic passes

Four passes, through the judgement port, metered on
`valor_rebuild_test_a3build`: calibration tasks `52106d0c5b78`,
`19b0ea8b835c`, `f95ad68cb45c`, and `347c4cbec9f3`, 60,496 usd_micros
($0.0605) in all. Their ledger rows went with the test database at the
next suite run; their answers are kept in
`~/src/valor-build-notes/a3/diag*.json`, with the script (`diag.py`), the
case ids, and the question variants asked.

## Rollout

Merged at `e2a0e6158` on top of C3 (`daee94e81`). The lead's focused run
(pipeline, emulator metering, judgement, fresh; not container) gave 284
passed. The kernel was restarted with `launchctl kickstart -k`; no schema
change, so no migration. The docs runner now reports its verdict from the
final message of its own session, and `python -m core verdict` no longer
exists.
