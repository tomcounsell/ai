# 1.4c part one records

The critique rounds, build record, and patch rounds of
[m1-4c-review.md](m1-4c-review.md), part one of task 1.4c of
[m1-4-checks.md](m1-4-checks.md).

## Critique round 1 (of 2): revise

The report covered both parts. The lead split the task: findings 1, 2, 3,
5, 6, 11, 12, and 13 are handled here; 4, 7, 8, 9, 10, 14, 15, 16, and 17
in m1-4c-verifier.md.

1. A reviewer and the kernel disagreeing on governance left the task stuck:
   `governance.json` is an input, the runner normalizes reviewer instances
   and notes into findings, and `record_check` computes the verdict, with
   a test per case (Design, The recorded verdict; Tests).
2. Deleting `verdict` could strand docs: `verdict` goes only with docs
   registered (Registration; registration itself settled in round 2).
3. The gate would measure a hand-played review: this part reruns on the
   host with 1.4b's machinery and merges before the 1.5 gate; the Done
   line is not amended.
5. The reviewer got the builder's live database: its turn runs inside
   `check_services` (Design, step 3; Threat model; Tests).
6. The candidate controlled the plan the reviewer reads: `plan.md` is the
   bytes at `f.plan["commit"]`; effect payloads are quoted and their
   fields listed (Design, step 3).
11. "Both counts" contradicted "no test-branch result": the reviewer gets
    only the review's own run (Design, step 2); part two adds the
    `macos`-skipped count.
12. The runner signature lacked the judgement port: it is
    `(fresh_for, port, model=None, seat="reviewer")`.
13. The `core/verdicts.py` row was incomplete: `record_critique`'s manual
    default, `tests/scripted.py`'s `**MANUAL`, and the new `record_check`
    fields are listed; `--behavior` is 1.4b's (Files, Registration).

## Critique round 2 (of 2): revise

Both rounds are spent; every finding is folded in. Findings 1 to 11 are
this part's; 12 is part two's.

1. Review waited on governance's calibration, so the gate could still
   measure a hand-played review: review is registered unconditionally,
   calibration is information (the lead's call under Tom's rule against
   invented safeguards); 1.4b registers docs the same way, so `verdict`
   goes in the same commit (Registration; Decided by default 6).
2. The advisory lock: part two's.
3. The head run's reuse key lacked the role: it includes the role, base
   runs stay shared, and 1.4b is told (Design, step 2; Files).
4. No seat and no path for a second seat's review: `seat="reviewer"`,
   `fresh_for` takes the seat, the model is `resolve_model(seat)` passed
   through `fresh_for`, and another seat appends `review.compared`
   (Design, step 6).
5. `governance_refused` outranked `changes`: `changes` is recorded with
   the ungranted instances as findings; `governance_refused` only on a
   `pass` (The recorded verdict; Decided by default 3).
6. The reviewer's checkout was never set up: seed cache cloned and
   `run_setup` run by the kernel before the turn, with an offline test
   (Design, step 3).
7. The live-port test was false: it asserts the live pid gone, no builder
   table, and the live instance back afterwards (Tests).
8. Docs wrong between the merges: m1-4-checks.md's rows and outline and
   valor-rebuild.md:257 are fixed in this part (Docs fixed).
9. A reviewer path reached git as a pathspec: it must equal a diff path,
   and `hunk_at` uses `--literal-pathspecs`, tested with `"."` (Design,
   step 5; The recorded verdict).
10. The lint record: `null` with no command, exit and duration always,
    ruff concise locations for `python-uv` without the message, exit only
    otherwise, with no timeout (Design, step 2).
11. The manual-leg refusals: the verdict is computed for the session leg
    only, and both refusals stay for `leg="manual"` until deletion (The
    recorded verdict).

## Build record

Built on `m1-4c-review`, rebased onto `3e1b97989` (1.4b with its patch
round 1), against the test database `valor_rebuild_test_14c1build`, never
the real ledger. The suite: 619 passed, 11 skipped, 1 failed. The failure
is `test_checks.py::test_the_check_layout_profile_and_a_planted_pgpass_link`,
whose task Postgres did not start in the full run; this part does not
touch it, and alone it passes. `uvx ruff check .` and `uvx ruff format --check .`: clean, apart from
the two known complaints in `docs/bridges/telegram.md` and
`docs/plans/m2-1-port.md`.
The live tests are written and were not run (they spend; `VALOR_LIVE=1`):
`tests/test_live_fresh.py` holds the two blind reviews, and
`tests/test_live_session.py` reaches the merge through the review runner.

Decided in the build, where the plan left it open:

1. Tests that write a check verdict without its runner do it through
   `record_check` on the `session` leg, with the judgements the local
   upstream gives (`scripted.judgements`, `scripted.check`).
2. A `.valor` the reviewer checkout's setup leaves is not removed:
   `write_inputs` refuses it and the run is `failed`, so the kernel never
   deletes through a path setup could have replaced with a link.
3. The lint runs inside the head run, after the suite, with its own
   output file. The test runner and the review runner share
   `base_and_head` and `stop_heard`.
4. `verify.json` carries ids, counts, codes, and lint locations, no tail.
   `governance_outcome` also returns the abstained hunk ids, which
   `governance.json` lists.
5. `git.hunks` returns nothing unless the diff names exactly the one file
   asked for, under `--literal-pathspecs`: `"."`, a directory, or a magic
   pathspec has no hunk, so a reviewer instance there is a finding.
6. A governance finding names the line only when the instance has one
   (kernel instances may not).
7. `record_critique` refuses a leg other than `session` or `kernel`
   before it reads anything, as `record_check` does.
8. A review at another seat returns `compared`. `effects.md` lists the
   effects with a held or refused row.
9. The offline set-up test uses the plain-kind toy: the probe runs the
   suite's own command in the set-up checkout under the reviewer's
   profile, since a `python-uv` project needs the network to seed. The
   `python-uv` lint locations are tested on `lint_locations` and
   `lint_command`.
10. The router test probes a file in the builder clone and the listing of
    the check directory: the builder's `done.md` is filed away once the
    kernel reads it, and the test branch removes its checkouts after its
    run. The fresh database is shown by the builder's table being absent;
    `data_directory` needs a role the kernel's role does not have.
11. In the router test the candidate's suite fails, so the rerun after
    the grant records `pass` and the join sends the work to `patch`; the
    test asserts the rerun up to its `review.decided`.
12. `docs/sdlc-state-machine.md` stays under 600 lines with its
    spending and attention pointing at `docs/spending-and-attention.md`;
    the attention text there and in architecture.md names a verdict by
    hand (`leg: manual`) only as a row the fold still reads.

## Patch round 1 (of 2)

From check-1-4c1-review (`changes`), check-1-4c1-test (`pass`) and
check-1-4c1-docs (`updated`, fast-forwarded onto 3a176d498).

1. The kernel no longer removes `.valor` from the reviewer's checkout
   after setup. A link setup planted in place of the checkout made that
   removal delete `.valor` in another directory. `write_inputs` opens
   the checkout without following links and refuses an existing
   `.valor`, so the run ends `failed` and nothing outside is touched.
   Test: setup swaps the checkout for a link to a directory holding
   `.valor/done.md`; the file stays.
2. The reviewer setup reads `tasks.is_stopped` after its `LISTEN`, as
   `base_and_head` does. Test: a stop written at the last lock check
   before setup; setup never starts.
3. A failed reviewer setup runs the turn, as Decided by default item 10
   says.
4. `lint_command` puts `--output-format concise` right after each
   `ruff check`, never at the end of the line.
5. The threat model says these runs have no time limit.
6. The stop test covers a stop during the lint: no `verify.ran`, every
   marked process gone.

## Checks, round 1 (of 2), at c56fce056

- Docs: updated, 3a176d498 (harnesses.md, judgement-layer.md, emulator.md).
- Test: pass, 620 passed, 11 skipped.
- Review: changes. The kernel's `rmtree(checkout/.valor)` followed a `repo`
  link that setup planted; a stop written before the reviewer setup
  listened was missed. Governance boolean: no. Patch round 1 above.

## Checks, round 2 (of 2), at c6f787946

- Docs: updated, 5fcc81e6b. "No free text" in `verify.json` now reads "no
  message or output tail"; the lint paths are the one string the candidate
  chooses.
- Test: pass. 623 passed, 11 skipped. A planted link leaves its target
  untouched; a stop before setup is heard; the concise flag lands only
  after each `ruff check` under `&&`, `;`, `||`, newlines and `uvx`; a stop
  during the lint reaps the group; a setup exit of 3 reaches
  `verify.json` as `reviewer_setup_exit`.
- Review: pass. Governance boolean: no. No invented caps. Decided by
  default 10 holds: a forced `changes` would be a new gate, and `failed`
  would rerun with no way to Tom. No path left where the kernel follows a
  link in the checkout.

## Delivery: merge held for Tom's tap

Notes for the merge:

- It is built on 3e1b97989, the tip of 1.4b's docs branch, and 1.4b is
  delivered, not passed. It merges after 1.4b; if 1.4b is patched, this
  rebases and its checks run again on the new head.
- Not blocking: a reviewer setup that exits nonzero has no committed test;
  a lint command that already passes `--output-format` gets the flag
  twice and ruff exits 2 (the valor spec does not).
