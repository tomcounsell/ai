---
tracking: none
slug: m1-4b-runners
type: build
status: planned; awaiting critique round 1 of 2
critique_rounds: 2
review_rounds: 2
---

# 1.4b in full: calibration, the test runner, the docs runner

Task 1.4b of [m1-4-checks.md](m1-4-checks.md), milestone 1.4 of
[valor-rebuild.md](valor-rebuild.md). It lands the runners for
`checks.test` and `checks.docs`, the frozen case sets breadth and
governance route on, and the check environment the trial run of popoto
#191 showed is missing. The shared design (the task directory, the kernel
mirror, fresh sessions, project specs) is m1-4-checks.md's and is not
repeated here.

Built on the rebuild branch at 0cc15eb5f, after 1.4a's merge and the
popoto #191 trial. 514 tests collected at the base.

## Stakes

`critique_rounds: 2`, `review_rounds: 2`. The task changes the kernel (the
router's runner mapping, `record_check`, the judgement sites' git reads,
the check harness, the task's services) and stored data (`suite.ran` rows,
new fields on `test.decided` and `docs.decided`, `judgement.calibrated`
records). A mistake either counts a red suite as green, lets a docs commit
carry code into the merge, or runs candidate code outside its sandbox.

## The Done items it closes

From valor-rebuild.md, 1.4:

| Done item | What closes it here |
|---|---|
| Fresh sessions for critique, review, and docs as runners in `RUNNERS`; each stage removed from `verdict` as its runner lands | the docs runner (`fresh.docs_runner`) and the test runner (`checks.test_runner`) are registered; `test` and `docs` leave `verdicts.MANUAL_STAGES`, so `verdict` takes only `review` |
| Through the router: docs commits outside `machine.is_doc_path` dropped at turn end and recorded as a `changes` finding; a failed or stopped branch leaves no verdict and the next run reruns only it; docs commits do not ride into the next candidate | the docs runner's kept prefix, router tests with the scripted session |
| `checks.test` runs the suite at head and base and the breadth call; `test.decided` carries the command, the failures at head that do not fail at base, the behaviors, and breadth's model, confidence, cost, and guard id; the order settled | the test runner; order settled as breadth, then base, then head (the build fixes the Done line's "then the breadth call" to match) |
| The review and docs runners always pass `governance_from`, the test runner always passes `breadth`; `record_check` accepts neither as optional from a runner | `record_check` refuses a session-leg test verdict without `breadth` and a session-leg review or docs verdict without `governance_from` (review's runner is 1.4c's) |
| Breadth and governance route on the entry check (both legs right on every frozen case) and log every row; floors set from human labels once real tasks have produced thirty or more; no calibration-first machinery; each re-checks Jev's worst-case overhead against its own rows | case shapes for both sites in `judgement_sites.calibrate`, one frozen case set per site, the entry check in the record, the estimate ratio in the record |
| The kernel provisions each task's workspace, including the app's environment so the suite can run | each check's checkout runs the spec's setup from the lockfile, with fresh services on the task's ports |

`python -m core verdict` loses `test` and `docs`. It keeps `review` until
1.4c, which deletes the command.

## Threat model

What the turns control:

- **The test check.** The candidate's code runs at head: its setup, its
  `conftest.py`, its exit code, its JUnit file, everything it writes in its
  checkout, its cache copy, and its own service instances. The builder's
  working session also controls its clone and the contents of the task's
  live Postgres and Redis.
- **The docs turn.** All of its checkout: `.git/config`, hooks, refs,
  objects, `HEAD`, the commits it makes, `.valor/verdict.json`, its `tmp/`,
  and any link it plants anywhere in the checkout.

What the kernel must never do with any of it:

- Run git with a checkout's config or hooks. The only git that touches a
  turn-owned repository is `fetch_into_mirror`'s sandboxed `upload-pack`,
  behind its hostile-config refusal. Every later read (ancestry, paths,
  diffs, `.valor` in a tree) runs in the kernel mirror. Breadth and
  governance read the mirror, never `b.workspace`.
- Follow a link a turn could plant. `verdict.json` and the JUnit file are
  read relative to a descriptor with `O_NOFOLLOW`, bounded in size; a
  JUnit file with a `DOCTYPE` is a suite with no per-test result.
- Run candidate code outside the check profile. Setup and suite both run
  under it, marked, reaped, and time-limited.
- Give a check the caller's environment, the builder's caches, or the
  task's live services. The check gets `turn_environment`'s allowlist, so
  no `VIRTUAL_ENV`; its own cache copy; and fresh service instances.
- Let anything one run writes reach another run. Each run's cache copy is
  cloned from the seed and deleted, and each run's services are made fresh
  and deleted.
- Take the docs head from anywhere but the docs checkout, or write it into
  the builder's clone.
- Treat the head suite's report as more than the candidate's own claim. A
  candidate can write its own JUnit file. The reviewer (1.4c) reruns the
  tests and reads the diff, `conftest.py` included; `test.decided` says
  which suite runs it read and never more.

## The order: breadth, then the suite at base, then at head

Breadth reads only the diff, costs about a cent and seconds, and is reused
per candidate. The suite costs minutes and the largest slice of the 16 GB
machine. So an outage in both judgement legs costs no suite run, and a red
suite costs one breadth call. If breadth is unanswered with reruns left
(`judgement_sites.Unanswered`), the runner returns `failed` before any
suite and the branch reruns. After `UNANSWERED_RUNS` failures breadth
returns the failed row's id, `breadth_outcome` lists the gap as a behavior,
and the suite runs.

## Design

### Judgement sites read the mirror (`core/judgement_sites.py`)

`breadth` and `governance` call `git.diff_paths`, `_diff`, and
`diff_hunks` on `b.mirror or b.workspace`, as `record_check` already does.
A task the kernel provisioned always has a mirror; a task started on a
plain directory keeps reading its workspace, which is the only repository
it has. The docs governance call (candidate to docs head) needs this: the
docs head lives only in the mirror. The module docstring's "before the
suite or the reviewer's turn" becomes "before the suite; before the
reviewer's turn; after the docs turn".

### Calibration: case shapes and the entry check (`core/judgement_sites.py`)

`calibrate(port, dsn, cases_path)` keeps its one-task, one-record shape
and gains the two sites' case shapes:

- **Breadth case**: `{"id", "inputs": {"diff", "tests"}, "labels":
  {"gap_state": {"value": true|false, "source", "evidence"}, "gap_enum":
  ..., "gap_bound": ...}}`.
- **Governance case**: `{"id", "inputs": {"path", "hunk", "paths"},
  "label": true|false, "source", "evidence"}`.
- **Judge case**: unchanged (`request`, `label` precise or thin).

`CASE_ACTIONS` is replaced by each question's own `proceed` set: a label
`false` expects `proceed`, `true` expects `caution`. Each case is asked
once per leg; every question of a multi-question task is scored on its
own. Sources are `reference` (a merged human-written test settles it),
`tom` (a decision of Tom's settles it), or `drafted`.

`_record` gains, per leg:

- `per_question`: confusion counts per question, with n.
- `entry_check`: true when the leg is right on every question of every
  case whose label is not `drafted`. Right counts both directions: a wrong
  `false` lets a gap or a governance hunk through, a wrong `true` sends
  every candidate to repair or puts a tap on every diff of that kind.
- `drafted`: the same counts for drafted labels, as information.
- `estimate`: from the calibration task's own `gateway.opened` and
  `gateway.charged` rows, the largest and the median ratio of billed input
  tokens to `estimated_input`, and how many calls billed more than
  estimated. This is the Done item's re-check of Jev's overhead
  (`tools/jev.py`, `Jev.estimate`). The estimate sets the input-size limit
  and the charge used when a provider reports no usage, so a call billed
  over its estimate means the estimate is too low: the build changes
  `Jev.estimate` and makes the record again.

The record's top-level `entry_check` is every leg's. Brier scores stay
information beside their n.

**The frozen case sets** live outside the repo under
`~/src/valor-demo/items/judgement/`, beside `intake.underspecified.json`:

- `checks.test.breadth.json`: every baseline run and demonstration
  delivery whose item has hidden reference tests, except the items 1.5's
  takeover gate scores (popoto #191, psyoptimal #872, popoto #633). Each
  case is the candidate diff split as breadth splits it. Labels come from
  the reference tests: `true` for a kind where a hidden reference test
  failed on the candidate and exercises that kind, `false` for every kind
  when every hidden test passed. Where the reference does not settle the
  kind, the label is `drafted`.
- `governance.adds.json`, at most 50 hunks, five per commit in diff order:
  positives, source `tom`, from the commits of 1.2 and 1.3 that seed or
  route the checkpoints Tom granted on 2026-10-01, and from the commits on
  `main` that add a file under `.claude/hooks/validators/` or a hook
  registration (the kinds his paragraph names); negatives, source `tom`,
  from hunks that add only tests (his paragraph: "Tests are not
  governance"); every other hunk, source `drafted`.

The case files' SHA-256 digests go into the build record before the first
run. After run 1 no case is added, removed, or relabelled; between runs
only a question's wording or a leg's fixed rendering changes, each change
listed.

**Landing.** A site whose record passes its entry check lands with
`BREADTH.calibrated` or `GOVERNANCE.calibrated` set to the record's
`task_sha256`, and its runner registered. The floors keep their values;
the comment "provisional: set by 1.4's calibration record" becomes "set
from human labels once real tasks have produced thirty or more rows",
which is the Done item. A site whose entry check fails still lands its
runner code, but the runner is not registered and its stage stays on the
manual `verdict` path until the entry check passes. The failure is
recorded in the build record; the build does not stop or ask Tom. Nothing
in the kernel reads the record at run time; whether a runner is
registered follows the latest record's entry check.

### The check environment (`core/workspace.py`)

**Fresh services per suite run** (finding 5). New `check_services(lay,
check_dir, project, task_id)`, a context manager:

1. `stop_services(task_id, lay)`: the task's live instances stop, so their
   ports are free and nothing the builder left in them is read.
2. A `Layout` rooted at `checks/<name>-svc/` (outside the check's own
   directory, so the suite cannot touch the data files). On it,
   `_init_postgres` with the project's roles and a new password, and
   `start_services` on the task's own ports. Redis starts with an empty
   directory.
3. The pgpass file is copied into `<check_dir>/tmp/pgpass` with
   `O_NOFOLLOW | O_EXCL`; `harness_env` builds the environment from that
   layout with `PGPASSFILE` and the spec's `{passfile}` pointed at the
   copy, and `DATABASE_URL`, `TEST_DB_PASSWORD`, and `REDIS_*` carrying the
   fresh instances.
4. On exit: `stop_services(task_id, svc_layout)`, the `-svc` directory
   removed, and `start_services(task_id, lay, ...)` again, so the router's
   view of the task's services holds for the next runner.

The service mark stays `valor.service.<task>`; the task's own instances
are down while a check's are up, so the reap at step 4 takes only the
check's. Ports are the task's, so the spec's `env` (`{port}`) and the check
profile's allowed ports need no change. `localhost:6379`, where the live
old system's Redis listens, is not in the check profile's ports, so a test
that reaches it fails at base and at head alike (popoto's
`tests/test_connection.py`).

**`check_harness(..., services=True)`** keeps the environment from step 3
and points `UV_CACHE_DIR`, `UV_PYTHON_INSTALL_DIR`, and `npm_config_cache`
at the run's own cache copy under `<check_dir>/cache/`. `turn_environment`
already drops everything of the caller's but `HOME`, `USER`, `LOGNAME`,
`SHELL`, `LANG`, `LC_ALL`, and `TERM`, so an inherited `VIRTUAL_ENV`
never reaches a check.

**Setup from the lock in every check checkout** (finding 8). New
`run_setup(checkout, harness, commands, mark)`, lifted from `_setup` with
the directory as a parameter; `_setup` calls it for the builder's clone.
The spec's setup is what pins: `projects/valor.toml` already says `uv sync
--frozen`, and the replay spec gets the item's own setup (below).

**The seed cache.** The base run's setup writes into its own
`<check_dir>/cache/`. When it succeeds the kernel clones that directory to
`checks/seed/` (`/bin/cp -c -R`, an APFS clone, near free on disk) before
the base suite runs. Every later run (each head, a base rerun) gets its own
clone of the seed as `<check_dir>/cache/`, writable during its setup and
suite (`uv run` syncs before it runs, so a read-only cache breaks it), and
deleted with the check directory. The base is the merged commit the task
started from, so nothing a candidate wrote is in the seed. If the seed is
missing when a head runs (a base reused from `suite.ran`, a sweep), the
base's setup runs again to make it, without the suite.

### The test runner (`core/checks.py`, new)

`test_runner(port) -> Runner` for `Check.TEST`:

1. Fold; `b.mirror` is required. `breadth_id = await
   judgement_sites.breadth(port, ctx.dsn, ctx.task_id)`.
2. `suite(lay, b, sha=b.base_sha, role="base")`, reused when a usable
   `suite.ran` exists, then `suite(lay, b, sha=candidate, role="head")`.
   Each: `workspace.fresh_dir(lay.checks / f"test-{role}-{sha[:12]}")`,
   `blind_checkout` from the mirror (refusing a top-level `.valor`),
   `run_setup`, then the suite command with `{junit}` replaced by
   `<check_dir>/tmp/junit.xml`, under `check_services` and the check
   profile, marked `test-<task>-<role>`, reaped, with
   `settings.suite_timeout_s` (default 1,800, `VALOR_SUITE_TIMEOUT_S`).
   `ctx.alive()` is checked before each suite and before each write.
3. Each run appends `suite.ran`: commit, role, command, environment
   digest, exit code, test ids by outcome (passed, failed, errored,
   skipped), duration, output tail, peak footprint, and `infrastructure`
   (setup failed, killed or stopped, timed out, a service would not start,
   or no JUnit file while the exit code says the runner itself failed).
   The environment digest covers the lockfiles at that commit (read with
   `git show` in the mirror), the setup commands, the spec's `env`, and the
   bytes of each file in the work directory's `bin/`. A `suite.ran` with
   the same commit, command, and digest and `infrastructure: false` is
   reused, so the base runs once per task and a crash after a suite does
   not rerun it. One with `infrastructure: true` is never reused.
4. `read_junit(check_dir)`: opened relative to the check directory's
   descriptor with `O_NOFOLLOW`, at most `settings.junit_max_bytes`
   (default 50 MB), refused if it holds a `DOCTYPE`, parsed with
   `xml.etree.ElementTree`. Any failure is "no per-test result", never a
   crash. Test ids are `classname::name`.
5. `compare(base, head, removed)` gives three lists:
   - `failures`: ids failing or erroring at head that passed or did not
     exist at base, and every id that passed at base and is absent or
     skipped at head, unless the diff removes its definition. A skip counts
     as an absence because a candidate can skip a failing test as easily
     as delete it.
   - `deleted_at_head`: ids absent at head whose definition the diff
     removes. For Python, `removed_definitions(mirror, base, head)` reads
     the diff in the mirror: a deleted test file, or a removed line that
     defines the test's function or class name (parametrized ids reduced
     to the name); a diff that touches a test's parametrize decorator
     counts its missing ids as deleted. For other kinds, an id whose name
     string the diff removes is deleted; other missing ids are failures.
   - `failing_at_base`: ids failing or erroring at both base and head.
     They are not counted at head and are listed for the reviewer and Tom,
     so a test that cannot pass in the sandbox (the rebuild's own `/bin/ps`
     tests, popoto's `test_connection.py`) is shown, never hidden.
   With no per-test result on either side, the exit codes decide: red when
   head fails and base passes; red with the note "the suite fails at base
   too, and without per-test results no failure at head can be told apart"
   when both fail. This answers the existing `checks.test` question; it is
   not a new check.
6. `record_check(conn, task_id, Check.TEST, None, breadth=breadth_id,
   command=..., failures=..., deleted_at_head=..., failing_at_base=...,
   suites=[base_event, head_event], head=candidate, leg="kernel")`. The
   kernel computes the verdict as today: red if any failure, else gaps if
   breadth lists a behavior, else pass. An infrastructure failure at head
   records nothing and returns `failed`, so the branch reruns.

The test runner runs no model turn, so it has no turn id. `_session_leg`
takes a third leg, `kernel`, which names the `suite.ran` rows it read in
place of a turn; breadth's model, confidence, cost, and guard id come from
the breadth row inside `record_check`, as today.

### The docs runner (`core/fresh.py`)

`docs_runner(fresh_for, port, model=None)` for `Check.DOCS`, following
`critique_runner`'s shape:

1. Fold; `b.mirror` required. `check_dir =
   workspace.fresh_dir(lay.checks / f"docs-{candidate[:12]}")`. A real
   clone (not a blind checkout) of the candidate from the mirror, made by
   the kernel with `git.trusted`, refusing a top-level `.valor`. Git
   identity through the environment (`GIT_AUTHOR_NAME`, `GIT_COMMITTER_*`:
   "Valor docs"). Inputs under `.valor/inputs/`: `request.md`, `plan.md`
   (the plan commit's file, from the mirror), `diff.patch` (base to
   candidate), and `previous-docs.patch` (the previous candidate's docs
   commits, from the mirror ref of the last `docs.decided`, when there is
   one, so the session keeps what still holds).
2. `check_harness(lay, check_dir, [], env, services=False)`; one turn
   through `runs.run_turn(..., state=State.CHECKS.value, fresh="docs")`,
   seat `frontier`. `read_verdict` gives `verdict`, `findings`, and `head`
   (a full commit id, or absent when nothing was committed).
3. When `head` differs from the candidate: `fetch_into_mirror(b.mirror,
   checkout, head, f"refs/valor/docs-raw/{turn_id}", check profile, mark)`
   (finding 6: the head is fetched from where the session committed, with
   the check's own profile as the `upload-pack` sandbox). Then, in the
   mirror only:
   - the candidate must be an ancestor of the head, and `rev-list --merges
     candidate..head` must be empty;
   - walking `rev-list --reverse candidate..head`, each commit's paths
     (`diff-tree --no-renames -r --name-only`) and its tree's `.valor`
     (`tree_has_valor`, trusted, in the mirror) are read; the kept head is
     the last commit before the first one that touches a path outside
     `machine.is_doc_path` or adds `.valor`;
   - `refs/valor/docs/<turn_id>` is set to the kept head with `update-ref`,
     and the raw ref deleted.
   A refused fetch, a head that does not descend, or a merge commit keeps
   nothing: the kept head is the candidate, and the reason is a `changes`
   finding. Each dropped commit is a `changes` finding naming its paths.
4. `governance_ids = await judgement_sites.governance(port, dsn, task,
   candidate, kept)` over the kept diff, after the turn because the diff
   exists only then (empty when nothing was kept).
5. `record_check(conn, task_id, Check.DOCS, verdict, head=kept,
   governance_from=governance_ids, dropped=[...], findings=...,
   leg="session", model, usd_micros, turn_id)`. The kernel computes the
   verdict: `changes` if anything was dropped or refused, or if the session
   said `changes`; else `updated` when the kept head differs from the
   candidate; else `no_change`.

`record_check` for a session-leg docs verdict checks that `head` resolves
in the mirror under a `refs/valor/docs/` ref and descends from the
candidate, and fetches nothing. `_docs_into_mirror`, which fetched a hand
recorded head from the builder's clone, is deleted with the manual docs
path.

### Registration and the manual path (`core/__main__.py`, `core/verdicts.py`)

`runners(judgement_port)` adds `Check.TEST: checks.test_runner(port)` and
`Check.DOCS: fresh.docs_runner(_fresh_for, port)`. `MANUAL_STAGES` becomes
`{"review": Check.REVIEW}`; the parser's `--suite-command`, `--failure`,
`--behavior`, and `--head` options go with the test and docs paths.
`record_check` raises `VerdictRefused` for a non-manual TEST without
`breadth` and a non-manual REVIEW or DOCS without `governance_from`.

### The replay spec (`scripts/replay_workspace.py`, `scripts/replay.py`)

`build()` takes `kind`, `setup`, `suite`, and `env` and writes them into
`project.toml`; `replay.py` passes them from the item's `project` key. An
item with no `project` key keeps `kind = "plain"` and `suite = "true"`,
and its `test.decided` names the command `true`, so a delivery says the
suite was not run. For popoto #191 the key is: setup `uv sync --frozen
--extra dev`, env `UV_PYTHON = "3.12"`, suite `uv run pytest -p
no:cacheprovider -q -m 'not slow and not benchmark' --junitxml={junit}
tests`.

### Docs fixed in the same build

- `docs/sdlc-state-machine.md`: which stages have runners; the "Before
  1.4" limitation on docs commits goes; `checks.test` says breadth, then
  the suite at base, then at head, with `deleted_at_head` and
  `failing_at_base`; `checks.docs` says where the head is fetched from and
  that the kernel computes the verdict.
- `docs/plans/valor-rebuild.md`, 1.4 Done: "runs the suite at head and
  base, then the breadth call" becomes "runs the breadth call, then the
  suite at base and at head"; "asked before the reviewer's or docs turn"
  becomes "asked before the reviewer's turn and after the docs turn".
- `docs/harnesses.md` and `docs/machine.md` where they describe a check's
  services or cache.

## Answers to the trial's findings

- **Finding 5 (the task's environment, never the caller's).** Each suite
  run gets fresh Postgres and Redis on the task's provisioned ports, built
  from the project spec, with the environment `harness_env` makes for
  them and `turn_environment`'s allowlist, so no `VIRTUAL_ENV` and no
  reach to `localhost:6379`. Tests that need what the sandbox denies fail
  at base and head and are listed as `failing_at_base`.
- **Finding 6 (the docs head).** The docs session commits in its own
  clone, and the kernel fetches the head from that clone into the mirror.
  The builder's clone never sees it.
- **Finding 8 (setup from the lock).** Every check checkout runs the
  spec's setup, and the replay spec carries the item's setup (`uv sync
  --frozen`), so pandas resolves to the lock's 2.3.3 at base and head.
- **Finding 2 (a fresh build session).** A question for Tom (Questions,
  1). Assumed: no change in 1.4b.
- **Finding 3** (the build turn did not use the task's Redis) and
  **finding 7** (breadth keeps finding gaps) need nothing here: the turn's
  environment already carries `REDIS_URL`, and row 7 delivering with the
  gaps listed is the designed outcome.

## Tech debt absorbed

- `_docs_into_mirror` fetching from the builder's clone (finding 6).
- `judgement_sites.breadth` and `governance` reading `b.workspace`.
- `check_harness(services=True)` leaving `PGPASSFILE` on the task's
  `home/pgpass`, which the check profile denies.
- The replay spec's `suite = "true"` with no setup (finding 8).
- `CASE_ACTIONS` holding only the judge's case shape.
- The judgement floors' "provisional" comments.

## Left out

- The review runner, its rerun of the tests, and deleting `verdict`
  (1.4c). After 1.4b the review rerun is a fresh sandboxed checkout like
  the test branch's (valor-rebuild.md); the container verifier stays after
  takeover. m1-4-checks.md's split table still describes 1.4c with a
  container; that row is 1.4c's to settle.
- A fresh build session from the plan (Questions, 1).
- Floors set from human labels: they wait for thirty or more real rows.
- Running checks in parallel: the router runs them one at a time.
- Any change to the working session's services or caches.

## Tests

Every test runs the real code against a real Postgres and real git; the
model turn is the scripted session from `tests/scripted.py`, and judgement
legs are the upstream fixture in `tests/judgement_upstream.py`.

Judgement sites and calibration:

- Breadth and governance answer when the builder's clone has lost the
  candidate (its objects removed after the fetch): they read the mirror.
- Governance over a docs diff whose head exists only in the mirror.
- `calibrate` on a breadth case file scores each question on its own; a
  drafted label counts in `drafted`, never in `entry_check`; one wrong
  `true` on a governance negative fails the entry check, and one wrong
  `false` on a positive does too.
- The record's `estimate` block: a fixture leg billing more input than
  estimated shows in `over_estimate`.

The check environment:

- With `VIRTUAL_ENV` and `PGPASSFILE` set in the kernel's own environment,
  the suite sees neither (the suite command prints its environment).
- A suite connecting to `localhost:6379` is refused; one connecting to the
  task's Redis port reaches the fresh instance, which does not hold a key
  the builder wrote to the task's Redis; a table the builder made in the
  task's Postgres is absent from the fresh database.
- After the runner, the task's own services are up on their ports with the
  builder's data intact.
- `PGPASSFILE` names `<check_dir>/tmp/pgpass`; a link planted at that path
  before the copy is refused.
- Setup runs in each check checkout; a setup failure is `infrastructure`
  and reruns.
- A head suite that writes into its cache leaves the next head's run
  unaffected: a planted file is absent from the next clone of the seed.
- Changing a byte of a file in `bin/` changes the environment digest and
  reruns the base.

The test runner:

- The base suite runs once across two candidates; a `suite.ran` that timed
  out or was stopped is run again, never reused.
- Breadth with both legs failing returns `failed` before any `suite.ran`.
- A JUnit file that is not XML, holds a `DOCTYPE`, is a symlink, or is
  over the size bound is "no per-test result", never a crash.
- A candidate whose `conftest.py` exits 0 before collecting is red (every
  base test absent at head).
- One that marks a failing test `skip` is red.
- One that deletes a test's definition is not red and lists it under
  `deleted_at_head`; one that deletes a test's file but keeps the function
  elsewhere under the same name is judged by the name's removal; one that
  drops a parametrize case lists that id as deleted.
- A test failing at base and head is listed under `failing_at_base` and
  does not make the verdict red.
- Both sides with no JUnit file: head fails, base passes is red; both fail
  is red with the note.
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

## Expected spend, as information

Calibration runs in the build, against a build database: breadth about 10
cases by two legs, governance up to 50 hunks by two legs, expected about
$0.50 in all. The live tests above, about $0.50. A docs turn in a real
task, about $0.50 to $1.00 at the frontier seat. All metered; nothing
refuses or pauses on money.

## Rollout

1. Add the `project` key to the items under `~/src/valor-demo/items/`
   that have a suite (outside the repo).
2. The two calibration runs against the real ledger, one per site:
   `python -m core calibrate <cases>` for `checks.test.breadth.json` and
   `governance.adds.json`. Each record's `task_sha256` is compared by hand
   with the landed `calibrated` digest, and its `entry_check` must be true.
3. If a real-ledger record fails its entry check, the site's runner is
   unregistered in a commit that restores its stage to `MANUAL_STAGES`, so
   the stage stays manual until its entry check passes, and the failure is
   recorded in this plan file.
4. Restart the kernel so `RUNNERS` holds the new runners.

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
