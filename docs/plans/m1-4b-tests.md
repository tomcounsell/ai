# 1.4b tests

The tests for [m1-4b-runners.md](m1-4b-runners.md), task 1.4b of
[m1-4-checks.md](m1-4-checks.md).

Every test runs the real code against a real Postgres and real git; the
model turn is the scripted session from `tests/scripted.py`, and judgement
legs are the upstream fixture in `tests/judgement_upstream.py`.

Judgement sites and calibration:

- Breadth and governance answer when the builder's clone has lost the
  candidate (its objects removed after the fetch): they read the mirror.
- Governance over a docs diff whose head exists only in the mirror.
- `calibrate` on a breadth case file scores every question, not only the
  first; a drafted label counts in `drafted`, never in `entry_check`; one
  wrong `true` on a governance negative fails the entry check, and one
  wrong `false` on a positive does too.
- A case set whose non-drafted labels for a question are all `false` (or
  all `true`) gives `entry_check: false` with both legs right on every
  case.
- The record's `estimate` block: a fixture leg billing more input than
  estimated shows in `over_estimate`.

The check environment:

- With `VIRTUAL_ENV` and `PGPASSFILE` set in the kernel's own environment,
  the suite sees neither (the suite command prints its environment).
- A suite connecting to `localhost:6379` is refused; one connecting to the
  task's Redis port reaches the fresh instance, which does not hold a key
  the builder wrote to the task's Redis; a table the builder made in the
  task's Postgres is absent from the fresh database.
- After the runner, the task's own services are up on their ports, a table
  the builder made in the task's Postgres is still there, and the task's
  Redis holds no key the suite wrote.
- `PGPASSFILE` names `<check_dir>/tmp/pgpass`; a link planted at that path
  before the copy is refused.
- Setup runs in each check checkout.
- Failures classed by who controls them: a head setup failure and a head
  with no JUnit file and a runner failure exit, each with a usable base,
  record `red` with the failure as a finding, and the next run does not
  rerun the test branch. Each setup command's output is in its own file.
  A base setup failure decides its run by the exit codes
  and is set up again by the next run, never reused. A service that will
  not start records nothing and returns `failed`.
- A kernel SIGKILLed mid-suite (the test sends `SIGKILL` to a kernel
  subprocess while the check's Redis is up): on the next run the task's
  Redis on its port holds no key the suite wrote, no process under `valor.service.<task>` from the check
  layout is alive, and no `checks/*-svc/` directory is left.
- The check layout's `service.sb` sits at `<svc>/home/profiles/service.sb`
  and names the task's work directory and `bin/`; the suite's `PATH`
  holds the task's `bin/`, never `checks/bin`.
- A head suite that writes into its cache leaves the next head's run
  unaffected: a planted file is absent from the next clone of the seed.
- Changing a byte of a file in `bin/` changes the environment digest and
  reruns the base.

The test runner:

- The base suite runs once across two candidates; a `suite.ran` with a
  `cause` or that was stopped is run again, never reused.
- Breadth with both legs failing returns `failed` before any `suite.ran`.
- A JUnit file that is not XML, holds a `DOCTYPE` (UTF-8 or UTF-16), is a
  symlink, is a FIFO with no writer (the read returns at once), or sits in
  a linked check directory is "no per-test result", never a crash or a
  hang; a UTF-16 report is read; a report declaring an encoding expat
  cannot read (`utf-16-le` or `utf-16-be` with no BOM, an unknown name) is
  not XML; a report of any size is read whole.
- `fresh_dir` removes a read-only tree a run left behind, and a directory
  with no read bit (modes 0000, 0100, 0300), at the top or inside.
- A candidate whose tree holds `.valor` is red with that finding and is
  not rerun.
- A stop published while the suite sleeps: the suite's process group is
  gone (a child it forked included), no `suite.ran` is written for that
  run, and the runner returns `stopped` within seconds.
- A candidate whose `conftest.py` calls `os._exit(0)` before collecting is
  red, with every base test id counted as absent at head.
- One that marks a failing test `skip` is red.
- One that deletes a test's definition is not red and lists it under
  `deleted_at_head`; one that deletes a test's file but keeps the function
  elsewhere under the same name is judged by the name's removal; one that
  drops a parametrize case lists that id as deleted, whether the case sat
  in the decorator, a module list it names (followed through the names
  that list is built from), its `ids=` function, an imported module, or a
  case file it names; a line inserted just under the decorator deletes
  nothing.
- A test failing at base and head is listed under `failing_at_base` and
  does not make the verdict red.
- Both sides with no JUnit file: head fails, base passes is red; both fail
  is red with the note.
- With `BREADTH.calibrated` `None`, a breadth answer listing a behavior on
  a candidate with no failures records `pass`, the behavior under
  `breadth.information` and in the delivery, and the join goes to merge,
  not repair; with `calibrated` set, the same records `gaps`.
- `record_check` refuses a kernel leg test verdict without
  `breadth`, and a docs verdict without `governance_from`; `verdict
  test` and `verdict docs` are refused at the command line.

The docs runner, through the router:

- A docs commit touching `core/` is dropped and every later doc-only
  commit with it; `CLAUDE.md` and `Skills/x.md` (letter case) are dropped;
  each is a `changes` finding naming its paths.
- A docs commit adding `.valor`, a merge commit, a head that does not
  descend from the candidate, and a checkout whose `.git/config` sets
  `core.fsmonitor` (a marker file proves it never ran) each keep nothing
  and record `changes`.
- A `verdict.json` that is a symlink, or whose `head` is not a full commit
  id, is malformed: no verdict, and the next run reruns docs only.
- The builder's clone has the same refs and objects before and after the
  docs runner.
- A docs commit adding a symlink, and one adding a gitlink, are dropped
  with their paths as `changes` findings, even under `docs/`.
- A docs run that dies after `docs.kept` reruns governance only: no
  second turn, the same kept head, and a verdict.
- The docs clone shares no inode with the mirror: no file under the
  clone's `.git/objects` has the same `(st_dev, st_ino)` as any file under
  the mirror's `objects`, and the temporary `valor-docs/<turn>` ref is gone
  from the mirror.
- `git.hostile` over a checkout runs under the profile passed: a config
  `include.path` naming a file the profile denies fails the read, and the
  fetch keeps nothing.
- After a send-back the next candidate does not contain the docs commits,
  and the next docs session's inputs hold `previous-docs.patch`.
- A docs runner stopped mid-turn leaves no verdict; the next run reruns
  docs only while the test verdict stands. The same for a test runner
  stopped mid-suite, with review's verdict standing.
- Every join row through the router with the real test and docs runners.

The replay spec:

- `build()` with `setup`, `suite`, and `env` writes them; without them it
  writes `suite = "true"`.

Live, `VALOR_LIVE=1`, each test declaring its metered spend: one real docs
turn on the toy greeter candidate (expected about $0.50); one test runner
on the toy greeter with live breadth (expected about a cent, as
information).

