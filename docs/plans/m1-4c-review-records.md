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

## Patch round 2

From review-1-4c1-rb (`changes`) and test-1-4c1-rb (`pass`) at eb8fda0ad,
with the docs at 22948b08c. Rebased onto 2418d02c8 first: the conflicts
were `docs/emulator.md` (the tip's text whole, which already names
`NO RUNNER`) and `docs/architecture.md` (the verifier "built, not
registered", with the tip's line on the emulator's judge).

1. F1. The reviewer's setup runs with `workspace.setup_harness`: a
   profile whose writable roots are the checkout, `cache/`, and
   `setup-tmp/`, its TMPDIR and uv's cache and Pythons there. It cannot
   write the session's `claude/`, `pi/`, or `tmp/`. After setup,
   `workspace.setup_left` looks at the checkout through a descriptor,
   following no link, for `.valor` and each `BLIND_LEFT_OUT` entry.
   Tests: a setup writing `.pi/settings.json` gets the `kernel` leg
   `changes` and no reviewer turn; a setup writing into `../pi`,
   `../claude`, and `../tmp` lands nothing there and the session runs.
2. F2. A setup that leaves `.valor`, or no directory where the checkout
   was (a link), gets the `kernel` leg `changes` naming why, with the
   governance ids and `verify`, through the same code as a `.valor`
   tree. `failed` stays for kernel faults. Tests: `.valor` from setup;
   the planted link, whose target is untouched.
3. `_lint` reads the output file line by line in a worker thread.
4. `record_check` takes a `kernel` review as `changes` only, the tip's
   refusal narrowed, as the docstring and data.md say: a `pass` there
   would be a review pass with no blind session. The kernel row keeps
   `verify` and the computed verdict, with no `reviewer_verdict`,
   `predicted_failure`, or `requirements`. Test: `pass` and
   `governance_refused` on the kernel leg are refused.
5. architecture.md's risk table and sdlc-state-machine.md's "What runs"
   say review is built and not registered, recorded by hand until then.
6. Test: a `.valor` candidate with a governance hunk names the instance
   on the kernel leg, as a `governance` finding.

No cap, timeout, or guard added.

## Patch round 3

From review-1-4c1-p2 (`changes`) at 3ab97a19f, with the docs at
7de450afd. Base still 2418d02c8. Only the reviewer's setup step.

1. F4. `workspace.setup_profile` adds one write deny after the setup's
   allows: the checkout directory as a literal, and every `.git` path
   component under it, in any case, as written and resolved. Setup cannot
   commit, set config, plant hooks or an fsmonitor, write
   `.git/info/exclude`, make a nested `.git` (directory, file, or link),
   hard-link into `.git`, or rename, remove, `chmod`, `chflags`, or set an
   ACL or xattr on the checkout, so it cannot move the checkout aside and
   put another repository in its place. With no write path left, no git
   config pin was added to the session: its repository holds only what
   `blind_checkout` wrote, and the tree cannot carry `.git`. The rest of
   the checkout stays writable: the one project spec's setup is
   `uv sync --frozen`, which makes `.venv` in the checkout. Test: a setup
   that commits, sets `core.fsmonitor`, `core.hooksPath`, writes
   `.git/info/exclude`, and makes `sub/.git`, `deep/.GIT`, and a
   `nest/.git` file; every try exits nonzero, the reviewer's `git log`
   is `candidate`, `base`, and its `git status` runs no fsmonitor.
2. F5. An `OSError` from `setup_left` or `write_inputs` after setup goes
   to `commits_own`: the `kernel` leg `changes` with the error.
   `write_inputs` moved out of `review_inputs` so an error building the
   inputs from the mirror stays `failed`. Tests: setup `chmod 500 .` and
   `chmod 000 .` are refused and the session runs; an `OSError` from
   either function records the `kernel` leg `changes`. The link test now
   shows the swap refused and the session running in the kernel's
   checkout.
3. F6. The setup profile reads `<check_dir>/tmp/pgpass`. Test: setup
   creates a table with `psql` on the fresh Postgres, and the reviewer
   sees it.

No cap, timeout, or guard added.

## Patch round 4

From review-1-4c1-p3 (`changes`) at b765f0bab, with the docs at
cf4487b4e. Base still 2418d02c8.

1. F7. Every Claude Code session the harness starts, `workspace_turn`
   (fresh and resumed) and `turn`, runs with `--setting-sources user`
   (Claude Code 2.1.289 takes `user`, `project`, `local`). The user
   source is the turn's own `CLAUDE_CONFIG_DIR`, so a `.claude/settings.json`
   or `settings.local.json` in the checkout, from the tree or the setup,
   sets no model URL, environment, model, permissions, or `apiKeyHelper`.
   Test, both builders: a checkout settings file whose `env` names a
   second scripted upstream as `ANTHROPIC_BASE_URL` and a marker model;
   the second upstream gets no request, the gateway's upstream gets the
   calls, and the model is the kernel's. Without the flag both cases
   failed: the model call reached the second upstream.
2. N1. workspace.md and the threat model say an error after setup is
   treated as the candidate's. The behavior is unchanged.
3. N2. workspace.md: the setup runs "under a profile
   (`workspace.setup_profile`) that writes only" the checkout.
4. harnesses.md and tech-stack.md list `--setting-sources user` with the
   command line.

No cap, timeout, or guard added.

## Patch round 5

From review-1-4c1-p4 (`changes`) at de44821cf. Base still 2418d02c8.

1. F8. Every Claude Code session the harness starts, `workspace_turn`
   (fresh and resumed) and `turn`, runs with `--setting-sources ""`
   (Claude Code 2.1.289's `--help`: a comma-separated list of `user`,
   `project`, `local`; the empty list loads none). The kernel puts
   nothing in a settings file: the credential is the gateway's and the
   environment is the kernel's. A config directory the turn cannot write
   was not an option: Claude Code writes its sessions, transcript, and
   `.claude.json` there, and the profile covers `claude` and everything
   it starts alike. Test: a turn writes `$CLAUDE_CONFIG_DIR/settings.json`
   mid-turn, naming a second scripted upstream on a dev port the profile
   lets a turn reach and a marker model; every later call goes to the
   gateway with the kernel's model, and the second upstream gets none.
   With `user` the test failed: the next call reached the second upstream.
2. F9. Review's verdict is the session's final message, one JSON object,
   bare or in one fenced block, read from the turn's result on the
   harness's stdout (`fresh.final_verdict`); the runner reads no verdict
   file. A tool process under Claude Code holds `/dev/null` and a regular
   file on fds 0 to 2 and no pipe or socket, so nothing it leaves running
   can write that result. Findings, governance instances, notes,
   `predicted_failure`, and `requirements` come in the same object.
   Tests: under the real Claude Code, a process the turn leaves running
   keeps `.valor/verdict.json` forged and writes into every pipe, socket,
   and stdio fd it holds; the file ends forged and the turn's result is
   the model's own final message. Through the runner, a scripted reviewer
   whose left-running process keeps the file saying `pass` and whose final
   message says `changes` records `changes` (with the file read, it
   recorded `pass`). `final_verdict` takes bare and fenced objects and
   refuses prose, a list, and no text.
3. `skills/sdlc/review.md` and `verdict.md` give review's verdict as the
   final message; critique and docs keep the file. harnesses.md,
   tech-stack.md, workspace.md, `core/README.md`, the `claude_code.py` and
   `fresh.py` docstrings, and this plan's threat model, design, failure
   modes, and tests say so.

No cap, timeout, or guard added.

## Merged

Checks on round 5 (c38c549ac): test pass (settings from every source,
before a turn, mid-turn, and before a resumed turn, set nothing; a
malformed final message gives no verdict, never a forged pass), review
pass (F8 and F9 closed; governance yes for the task, under the standing
pipeline grant of 2026-10-01, and none added by the round), docs updated
8f8cf7470 (architecture.md, sdlc-state-machine.md, m1-4c-outline.md).

Merge: suite on 8f8cf7470, 1195 passed, 23 skipped, ruff clean. Backup
`valor_rebuild-20261004T071116Z.dump`. `valor-cori-rebuild` fast-forwarded
from 2418d02c8 to 8f8cf7470.

Rollout: step 2 has no kernel process to restart until 2.1 merges; the
kernel checkout is this repository and `uv sync` holds. Step 3 runs on the
first real task after the merge. 1.5's takeover gate (its Done item 5) can
now run.

Follow-ups: docs and critique still read `.valor/verdict.json`; they move
to `final_verdict` when the docs stage is registered. A Pi reviewer gets
its own probes in milestone 3. `final_verdict` assumes the result text is
a string, and `parse` raises on stdout that is a JSON list or number.
