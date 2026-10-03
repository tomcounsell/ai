# 1.4b records

The calls, critique rounds and build record of
[m1-4b-runners.md](m1-4b-runners.md), task 1.4b of
[m1-4-checks.md](m1-4-checks.md).

## Decided by default

Reversible calls made by the build session, not questions for Tom.

1. **The build turn keeps resuming the working session; no fresh build
   session from the plan in 1.4b** (finding 2). It is a harness change,
   not part of the test or docs runners, and folding it in would widen a
   stakes-2 task. The cost evidence (about 70k input tokens of resumed
   context, $2.18 before any code, against a bare baseline that did the
   whole item for $1.57) makes it a candidate task after 1.4d.
2. **Fresh Postgres and Redis per suite run, on the task's own ports.**
   Base and head must not share state, and the trial showed what a check
   sharing the caller's services can reach (port 6379, the live Redis).
3. **Governance labels not settled by Tom's grant, his paragraph's named
   kinds, or "tests are not governance" stay drafted and count as
   information only.** Under CLAUDE.md, what counts as governance is
   Tom's call, so a drafted label cannot set a floor.
4. **A calibration that fails its entry check at rollout leaves that
   stage on the manual `verdict` path until its entry check passes.** That
   is not a stop: tasks keep running with that check recorded by hand, the
   failure is recorded, and the site routes once its entry check passes.
5. **Tests failing at both base and head are listed as `failing_at_base`
   and do not make the verdict red.** That matches sdlc-state-machine.md's
   definition, "failures at head that do not fail at base".
6. **The kernel computes the docs verdict from the commits it kept, and
   the session's own `changes` stands.** The turn's verdict file is
   turn-owned and only adds caution; it never removes it.
7. **No breadth replays of cut-a.** Its only failing hidden test checks a
   log message's wording, which is none of breadth's gap kinds (states,
   enumeration members, old bounds), so the replays would most likely add
   no label and cost two build turns. The breadth set is the #191 trial's
   two drafted rounds.

## Critique round 1 (of 2)

1. The docs clone could hardlink the mirror's objects: it is made from
   `file://<mirror>` through a temporary `valor-docs/<turn>` ref, and a
   test checks that no inode is shared (The docs runner, step 1).
2. A candidate's own failure looped as infrastructure: failures are
   classed by who controls them; a head setup failure, timeout, or missing
   JUnit file with a usable base is `red`, a base one is a reusable run
   decided by exit codes, and only kernel causes rerun (The test runner,
   step 3).
3. The breadth case set did not exist as described: the real cases are
   named (cut-a by replay, pop-a, pop-b, pso-a, pso-a2), a label `true`
   needs a failing hidden test of that kind, "every hidden test passed"
   labels nothing `false`, the #191 candidates are drafted, and both label
   directions are required (Calibration).
4. No run limit, and registration read like a run-time read: at most five
   runs per site per frozen case set, a closed set is recorded for Tom and
   is not a stop, and registration is a build-time code change (Calibration,
   Landing, Registration).
5. Governance labelled by commit: labelled by hunk, the guard and
   validator hunks positive, `tests/` hunks negative, the negative commits
   named (Calibration).
6. A SIGKILL could leave a check's services on the task's ports: the
   router's service start and `check_services` both reap the task's mark
   and remove stale `-svc` directories first, with a SIGKILL test (The
   check environment).
7. The `-svc` layout broke the `root.parent` assumptions: `harness_env`
   and `service_profile` take the work and bin directories explicitly, and
   `check_services` writes the check's `service.sb` (The check environment).
8. `read_junit` could block on a FIFO: it reads through `read_verdict`'s
   walk, lifted into `read_turn_file`, with a FIFO test (The test runner,
   step 4).
9. Nothing stopped a running suite: setup and suite run as async
   subprocesses in their own session, raced against the stop channel, and
   a stop kills the group (The test runner, step 2).
10. The entry check departed from the Done item, and only the first
    question was scored: the departure is stated and the build amends the
    Done line; every question is scored (Calibration, Done items).
11. The threat model's git claim was false while `git.hostile` ran
    unsandboxed: it takes the caller's profile, and the claim is restated
    for both commands (Threat model, The docs runner, step 3).

## Critique round 2 (of 2): revise

The rounds are spent; each finding is built in.

1. The test runner waited on breadth's entry check: it is registered
   regardless, and an uncalibrated breadth's behaviors are information on
   `test.decided` and the delivery, never `gaps` (Landing, Registration).
2. The task's Redis keeps nothing across a restart (`--save ""
   --appendonly no`): the "builder's data intact" assertions are for
   Postgres only; for Redis the tests assert no key the suite wrote
   survives (The check environment, Tests).
3. A base setup failure was reused: it decides only its own run, and a
   failed setup runs once more in the same run before it counts as the
   commit's (The test runner, steps 2 and 3).
4. Per-test results at base and none at head: `red`, every base id
   absent, tested with `os._exit(0)` in `conftest.py` (The test runner,
   step 5).
5. A docs run that died after the turn redid the turn: a `docs.kept` row
   is reused and only governance is asked again, with a test (The docs
   runner, steps 2 and 3).
6. The governance set named commits without the hunks it claimed: 1.4a's
   tests come from `db2241e95`, `276e7d79a`, and `58b32cdd9`; `a30c03350`
   and `5368faebe` are gone; the counts are 10 positives and 22 negatives
   from `tom`, 18 drafted (Calibration).
7. The cut-a replays would add no label: dropped (Decided by default, 7).
8. The kept prefix read only paths: it reads `diff-tree -r` raw modes and
   keeps a commit only when every entry is a regular file, with a symlink
   and a gitlink test (The docs runner, step 3).
9. The threat model reached into the builder's clone: narrowed to the
   check and docs checkouts, the builder's clone named as out of scope
   (Threat model).
10. m1-4-checks.md said 1.4b had questions for Tom: it now says Decided by
    default, and its status line says `verdict` keeps `review` until 1.4c.

The five-runs-per-site limit stays: it was judged not governance.

## Build record

Calibration ran against the build database (`valor_rebuild_test_14bbuild`),
never the real ledger. The case files live under
`~/src/valor-demo/items/judgement/`.

| case file | SHA-256 | cases |
|---|---|---|
| `governance.adds.json` | `d3686c87a7ca4b58a298d258b45fe5f228fba089d7f9f2705e20d103906c476e` | 50: 10 `true` and 22 `false` from `tom`, 18 `false` drafted |
| `checks.test.breadth.json` | `5b8c92c7a913abef5d6df8d9f77e0286ac66f794e6b33e11c9ad0e2b0ef521d3` | 2: popoto #191 cases, every label drafted |

The four validator positives (`920b6f392`, `8bb12c001`, `e2a623a44`,
`1b8c9a27e`) each add their own validator file
(`validate_no_module_scope_env.py`, `validate_no_redis_flush.py`,
`validate_no_broad_process_kill.py`,
`validate_no_destructive_git_in_shared_checkout.py`); the positive is that
file's hunk in each.

**Breadth, run 1** (task digest `6d40ad2d94bb`): entry check false, as it
must be with no human label. On the drafted labels Jev was wrong twice on
`gap_bound` and the open-weight leg twice each on `gap_state` and
`gap_enum`. No call billed over its estimate (largest ratio 0.63 and
0.80). Spend $0.0066. `BREADTH.calibrated` stays `None`; breadth is
information.

**Governance, run 1** (task digest `2270554f20be`): entry check false.
Jev was wrong on 8 of the 22 `tom` negatives, all test code (scripted
stand-ins, fixtures, a recording script), and the open-weight leg on 1. Six
calls on each leg billed over the estimate, all on `uv.lock` and
`pyproject.toml` hunks of hashes (largest ratio 1.25 for Jev, 1.36 for the
open-weight leg). Spend $0.0199.

Changes before run 2: the estimate counts two bytes per token
(`settings.bytes_per_token`, was three), with
`tests/fixtures/judgement_hash_dense.json` holding the hunk that billed
furthest over; the question adds "Tests and the code that serves them
(fixtures, helpers, scripted stand-ins, recording scripts) are none of
these" (the governance paragraph's own "Tests are not governance").

**Governance, run 2** (task digest `aa30f9c0d5d4`): entry check false.
Jev wrong on 2 negatives (`tests/fixtures/record_judgement.py`, which exits
without `VALOR_LIVE=1`, and an abstain on `tests/scripted.py`); the
open-weight leg right on every case it answered, with two calls refused
by the provider (HTTP 429). No call over its estimate. Spend $0.0193.

Change before run 3: the question's test clause adds "even where they exit
early or refuse to run", and the `false` label reads "it adds none of
these, or adds only tests and the code that serves them".

**Governance, run 3** (task digest
`e47a2161d4dd2cc39bedb7a4d0883d95f62e5048030040479040829540f11e6f`,
calibration task `981e9498eec7`): entry check true. Both legs right on
all 32 `tom` cases and all 18 drafted ones, no error, no abstain; largest
estimate ratio 0.86 (Jev) and 0.89 (open-weight). Spend $0.0205.
`GOVERNANCE.calibrated` is that digest, the docs runner is registered, and
`MANUAL_STAGES` holds `review` alone.

**Rebase onto `e4b30b78c`** (1.4c part two, 1.4u's planning): no
conflicts. Built to the plans now on the base: `read_turn_file(dir_fd,
relpath)` keeps 1.4s's signature (no `max_bytes`, no `then=`;
`read_verdict` calls `_file_away` itself), `settings.junit_max_bytes` and
`settings.verdict_max_bytes` are gone and a report or verdict of any size
is read whole, `read_junit` walks from `lay.checks` by the check
directory's name, and the `suite.ran` reuse key includes the role
(m1-4c-review.md, step 2), so a review head run never stands in for the
test check's. The suite's output already goes to a file and the kernel
waits on the process, then reaps the group. The governance question's text
and the calibrated digest are unchanged.

The live session test asserts the held effects as 4.2's does: exactly one
merge held, at least one `push_branch` held, no outcome before approval,
every outcome granted, and the origin's branch at the last approved push,
an ancestor of the merged candidate. Each stage may push its own new
commits; an identical request already returns the existing effect.

**Patch round 1 of 2** (reviews `check-1-4b-review.md`,
`check-1-4b-test.md`, `check-1-4b-docs.md`):

1. `tests/fixtures/judgement_hash_dense.json` is in the commit (it was
   ignored by the fixture pattern and never added).
2. `workspace.fresh_dir` removes the old session directory with `rmtree`,
   so a read-only tree a sandboxed run left goes too; a test plants one.
3. No setup command or suite has a time limit, on the host or in the VM;
   a stop ends a running one. `settings.suite_timeout_s` is gone, with
   the "ran past" finding and its tests. `settings.setup_timeout_s` stays:
   provisioning in `core/workspace.py`, outside this diff, still reads it,
   and 1.4u removes it. m1-4c-verifier.md and m1-4c-review.md no longer
   lean on a suite timeout. The kernel polls the process once a second
   only to sample its footprint; that is not a limit.
4. A failed setup is not run again: it is a red at that commit.
5. A blind checkout that fails is cause `kernel`: no record, the branch
   reruns.
6. A parametrized id missing at head counts as deleted only when the
   diff touches that test's `parametrize` decorator: the base file is
   parsed with `ast` for the decorator's lines, and the `-U0` hunk headers
   give the base lines the diff touches. The toy suite's test uses its own
   `@parametrize(...)` decorator so the deletion case still holds.
7. The unused `workspace.docs_clone` is deleted (`fresh.docs_clone` is the
   one in use).
8. Each setup command's output goes to its own file,
   `<check dir>.setup-<n>.out` beside the suite's `<check dir>.suite.out`,
   and its record names the file.
9. The JUnit report's DOCTYPE and ENTITY guard is the `expat` parser's own
   declaration handlers, so a report in UTF-16 or any encoding it declares
   is read as XML; a UTF-16 report is a test. No size cap is added.

The plan's tests and records moved into m1-4b-tests.md and this file, so
each doc is under 600 lines.

## Checks, round 2 of 2, at c8a24d4ba

- Docs: updated, 3e1b97989 (the plan split into this file and
  `m1-4b-tests.md`).
- Test: gaps. 588 passed, 9 skipped from a clean checkout; ruff clean. No
  time limit on a run and a stop ends the group; one setup attempt;
  per-step setup files; a failed blind checkout is cause `kernel`. The
  one-second wait only samples the footprint, which is recorded and never
  compared.
- Review: changes. Governance boolean: no. No invented caps; round 1's
  findings 0 to 5, 7 and 8 are fixed.

Findings:

1. Review A: a missing parametrized id counts as deleted only when the
   diff touches that test's `parametrize` decorator lines. Cases kept
   outside the decorator (a module-level `CASES`, a fixture file, `ids=`
   computed elsewhere) land in `failures` when removed on purpose, so the
   head is red and no patch makes it green. Fix: also count names the
   decorator references, or return to the broader rule and document it.
   Test C, the same rule's other edge: a line inserted directly under the
   decorator counts as touching it.
2. Test A: `workspace.rmtree`'s handler raises `TypeError` (`open()`
   missing `flags`) on a directory with no read bit (0000, 0300, 0100), so
   `fresh_dir` crashes. 0500 trees work.
3. Test B: a JUnit report declaring `utf-16-le` or `utf-16-be` with no BOM
   makes expat raise `ValueError`, which `_declares` does not catch; the
   runner dies on output the commit controls.
4. Test D (low): a `.valor` entry in the tree gives cause `kernel`, so the
   branch reruns on every wake. Whether an earlier stage refuses such a
   tree is unconfirmed.

## Delivery: delivered, not passed

Review rounds are spent. Recommendation to Tom: one more patch for the
four findings. 1.4c part one is built on 3e1b97989 and is checked there.

## Tom's feedback (2026-10-03)

Tom, on one more patch round for nine deliveries with the scopes and order put to him: "All as recommended". The order: 1.4v, 2.1, 1.4b, 1.4s, 2.2, 2.3, 3b, 1.4u, 1.5. Valor decides any further round and the merge (valor-rebuild.md, Tom's feedback of 2026-10-03).

Scope: the four findings under Delivery (parametrize ids kept outside the decorator, `rmtree` on a directory with no read bit, BOM-less UTF-16 JUnit, a `.valor` entry rerunning as cause `kernel`).

## Patch round (Tom's feedback, 2026-10-03)

Rebased onto `ca620a91f` (3a, 3c and 4.2 merged). Plan-file conflicts
kept that branch's text. In `docs/harnesses.md` the Codex and Pi section
stays a pointer and its paragraph on the gateway's two metered formats
went into `harnesses-codex-pi.md`; `docs/README.md` lists `browser.md`
beside the two split docs; the live session test keeps the docs runner's
run and takes 4.2's "every tapped effect has its outcome". 3a's OpenAI
route estimates input with the same `settings.bytes_per_token`, which is
2 here: its tests compute the estimate from that setting, and
`tech-stack.md` says two bytes per token.

1. Parametrize ids fed from outside the decorator. A missing parametrized
   id is deleted when the diff touches what feeds that test's
   `parametrize` decorators at base (`_feeds_parametrize`): the
   decorators' lines (on the function or an enclosing class), the module
   or class level binding of every name they use, followed through the
   names those bindings use (a `CASES` built from `BASE`, an `ids=`
   function), any base file a used name is imported from, and any base
   file a string there names (a case file). A line removed inside one of
   those spans, or inserted between two of its lines, touches it; a line
   inserted just after a span's last line (directly under the decorator)
   does not. Tests: a case dropped from a list the decorator names through
   another name, an edited `ids=` function, an insertion inside a
   multi-line binding, an imported module, a case file, and an insertion
   under the decorator that deletes nothing.
2. `rmtree` on a directory with no read bit. When the directory could not
   be opened or listed, the handler makes it `0700` and removes it as a
   tree of its own, never following a link. Test: modes 0000, 0100 and
   0300, at the top of the check directory and inside it.
3. A JUnit report declaring an encoding expat cannot read (`utf-16-le` or
   `utf-16-be` with no BOM, a multi-byte or unknown name) is "not XML",
   no per-test result, as any other unparseable report. Test: four
   declarations.
4. A `.valor` entry. `blind_checkout` raises `ValorInTree` for the tree it
   checks out (only that one: the base's tree never lands in the
   checkout), and the test runner records that run as `cause:
   "commit"`: red at head, never rerun. An earlier stage does refuse such a
   tree (`session._keep` refuses a plan or candidate whose tree, read in
   the builder's clone, holds `.valor`), so this is reached only when the
   clone hides the entry from that read. Test: a candidate with `.VALOR/x`
   whose clone-side read is hidden is red with the finding and not rerun.

Suite on `ca620a91f` plus this round: 798 passed, 13 skipped (build
database, ports 6450 to 6459). `ruff check` clean; `ruff format --check`
flags only `docs/bridges/telegram.md` and `docs/plans/m2-1-port.md`.
