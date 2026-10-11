---
tracking: none
slug: d4-cutover-sweep-record
type: record
---

# D4 cutover sweep: every item, bare, clarify and routed

The `emulator` routine's sweep (`docs/routines.md`, `docs/emulator.md`)
over the seven items in `$VALOR_DEMO/items`, each in the `bare`, `clarify`
and `routed` arms, on the rebuild branch at commit `3cad320f9` (A1, A2,
A3, B1 and C7 merged). The bar (`rebuild-finish-prompt.md`, D4): no item
scores worse than bare.

## Verdict

The bar is not met on five of seven items. Two kernel defects account for
four of the five; two runs pass one hidden test fewer than bare:

- **Routed stops on a false governance verdict.** Four of seven routed
  runs (cut-a, pop-b, pso-a, pso-b) stopped in merge awaiting a governance
  grant. The Jev `governance.adds` leg flagged hunks that add no check:
  bug fixes, docstrings, a plan doc, template copy, argument validation.
  Routed is the only arm that runs that leg (bare and clarify force the
  judgement upstream, which answers no). Routed tasks stop for Tom on
  diffs that add no check.
- **A merge after a feedback round is refused non-fast-forward.** Four
  runs (pop-a clarify, pop-b clarify, pop-c bare, pso-a bare) passed their
  checks after a feedback round and then ended "to tom": the merge push was
  rejected because the origin's main held the first merge's docs commit,
  which the work branch lacked. C9 (commit `d5ebddebb`) fixes this; it is
  not in `3cad320f9`.
- **One hidden test fewer.** pop-b clarify passed 9 of 11 hidden tests
  against bare's 10; pso-c routed passed 4 of 5 against bare's 5.

The judged scores of clarify and routed stay within one point of bare on
every item. Routed merged on three items (pop-a, pop-c, pso-c); on pop-a and
pop-c it scored as well as or better than bare.

| Item | Bar | Why |
|---|---|---|
| cut-a | not met | routed stopped for a grant; bare merged |
| pop-a | not met | clarify to Tom on the non-fast-forward merge; bare merged |
| pop-b | not met | clarify to Tom and 9/11 hidden against 10/11; routed stopped for a grant |
| pop-c | met | clarify and routed merged at 4/4/3; bare went to Tom at 3/4/2 |
| pso-a | met | bare went to Tom and routed stopped for a grant; clarify merged; all at 4/3/4 and 20/22 hidden |
| pso-b | not met | routed stopped for a grant; bare merged |
| pso-c | not met | routed merged at 3/3/3 but passed 4/5 hidden against bare's 5/5; clarify merged at 4/4/4 |

## Setup

- **Code.** Worktree `~/src/valor-rebuild-d4`, branch `md4-sweep` at
  `3cad320f9`, its own `.venv`, `VIRTUAL_ENV` unset.
- **Ledger.** Test database `valor_rebuild_test_d4`; the real ledger was
  not touched. `VALOR_DEMO=~/valor-tasks-d4/demo`, work directory
  `~/valor-tasks-d4/work`.
- **Memory off.** `VALOR_MEMORY=off`, so no run reads another run's
  memories and the arms stay independent.
- **Items.** cut-a, pop-a, pop-b, pop-c, pso-a, pso-b, pso-c
  (`items/INDEX.md`). `toy-greeter` (a smoke item) and `pso-a2` (the same
  request as pso-a) are not in the sweep.
- **Serial.** One routine firing at a time; each firing drives one replay
  at a time, item by item, bare, clarify, routed.
- **Ports.** The block 6870 to 6879 split into a test span (servers the
  replay starts, `VALOR_TEST_PORTS`) and the task services' spans
  (`VALOR_PG_PORTS`, `VALOR_REDIS_PORTS`).
- **Disk.** Free space on `/` reached zero once, during the first firing
  (cut-a bare's 5.1 GB workspace beside another runner's image builds);
  that firing died writing its report and the next firing carried the same
  run on. From then on a judged run's workspace is torn down at once (its
  final diff is kept in the build notes) and a decided step's export
  directories under `checks/` are deleted; free space stayed between 7 and
  18 GB.
- **Stopping for a grant.** A routed run awaiting a grant is stopped on the
  test database (`core stop`): the grant is Tom's and the replay does not
  tap. The driver records the outcome `stopped` and the judge scores the
  candidate. It counts as handed to Tom.
- **Run.** Routine run `fbad0f38ad54` under objective `f7690bcf3e5e`, over
  four firings. A first run, `5f6359af1674`, is void: its services shared
  one port span. The run finished and wrote its report,
  `$VALOR_DEMO/sweeps/fbad0f38ad54.json`. Wall time from start to report
  was about ten and a half hours.

## Results

F / C / S is the blind judge's fidelity, correctness and simplicity (0 to
5). Hidden is the reference PR's own tests run on the final commit, passed
of total. Rounds is feedback rounds; Q is question messages. Kernel is
Valor's turns; Emulator is the stand-in and judge; both are metered on the
test ledger. The baseline row is the replay baseline's bare run
(`rebuild-baseline.md`), for reference.

### cut-a: cuttlefish #646

| Arm | Outcome | F / C / S | Hidden | Rounds | Q | Kernel | Emulator | Wall |
|---|---|---|---|---|---|---|---|---|
| baseline bare | accepted | 4 / 4 / 4 | 8/9 | 0 | 0 | | | 5 min |
| bare | merged | 4 / 4 / 4 | not measured | 0 | 0 | $3.31 | $1.01 | 12 min |
| clarify | merged | 4 / 3 / 4 | not measured | 0 | 0 | $4.20 | $1.02 | 3 min |
| routed | stopped, grant | 4 / 4 / 4 | not measured | 0 | 0 | $3.34 | $0.92 | 12 min |

Routed: three hunks in `synthesis.py` (a docstring and the dropping of
figures) were flagged as adding a check.

### pop-a: popoto #633

| Arm | Outcome | F / C / S | Hidden | Rounds | Q | Kernel | Emulator | Wall |
|---|---|---|---|---|---|---|---|---|
| baseline bare | accepted | 5 / 5 / 5 | 59/59 | 0 | 0 | | | 23 min |
| bare | merged | 4 / 4 / 3 | 59/59 | 0 | 0 | $4.26 | $0.31 | 44 min |
| clarify | to Tom, merge push | 4 / 3 / 4 | 59/59 | 1 | 0 | $5.68 | $0.28 | 88 min |
| routed | merged | 4 / 4 / 3 | 59/59 | 0 | 0 | $3.97 | $0.28 | 57 min |

Broad suite: 14 failures in each arm (the baseline run had 10); the same
count across arms.

### pop-b: popoto #191

| Arm | Outcome | F / C / S | Hidden | Rounds | Q | Kernel | Emulator | Wall |
|---|---|---|---|---|---|---|---|---|
| baseline bare | accepted | 1 / 3 / 2 | 3/11 | 0 | 0 | | | 10 min |
| bare | merged, rounds spent | 3 / 4 / 1 | 10/11 | 2 | 0 | $12.48 | $0.68 | 33 min |
| clarify | to Tom, merge push | 3 / 4 / 1 | 9/11 | 2 | 0 | $12.83 | $0.76 | 37 min |
| routed | stopped, grant | 3 / 4 / 2 | 10/11 | 0 | 0 | $7.91 | $0.18 | 23 min |

Hidden failures: bare `test_push_complex_types`; clarify that one and
`test_push_on_unsaved_model_raises`; routed
`test_capped_list_delete_cleans_redis_key`. Routed: five hunks flagged (a
plan doc, docstrings, constructor argument validation, a push on an
unsaved instance). Clarify paused once when its review check's image was
not held between build and run; the next firing resumed it.

### pop-c: popoto #188

| Arm | Outcome | F / C / S | Hidden | Rounds | Q | Kernel | Emulator | Wall |
|---|---|---|---|---|---|---|---|---|
| baseline bare | accepted | 4 / 3 / 4 | none | 1 | 0 | | | 8 min |
| bare | to Tom, merge push | 3 / 4 / 2 | none | 1 | 0 | $13.12 | $0.36 | 37 min |
| clarify | merged | 4 / 4 / 3 | none | 0 | 1 | $4.39 | $0.24 | 13 min |
| routed | merged | 4 / 4 / 3 | none | 0 | 1 | $4.16 | $0.24 | 14 min |

No hidden test (docs only). No hunk flagged in routed. Routed asked one
question before building, as clarify did.

### pso-a: psyoptimal #872

| Arm | Outcome | F / C / S | Hidden | Rounds | Q | Kernel | Emulator | Wall |
|---|---|---|---|---|---|---|---|---|
| baseline bare | accepted | 4 / 4 / 4 | 20/22 | 0 | 0 | | | 36 min |
| bare | to Tom, merge push | 4 / 3 / 4 | 20/22 | 1 | 0 | $9.53 | $0.50 | 38 min |
| clarify | merged | 4 / 3 / 4 | 20/22 | 0 | 1 | $4.25 | $0.47 | 21 min |
| routed | stopped, grant | 4 / 3 / 4 | 20/22 | 0 | 0 | $4.86 | $0.28 | 26 min |

Routed: three hunks flagged (a permission flag swapped in a template and
its views).

### pso-b: psyoptimal #893

| Arm | Outcome | F / C / S | Hidden | Rounds | Q | Kernel | Emulator | Wall |
|---|---|---|---|---|---|---|---|---|
| baseline bare | accepted | 4 / 4 / 3 | 55/55 | 0 | 0 | | | 3 min |
| bare | merged | 4 / 3 / 2 | not measured | 0 | 0 | $5.03 | $0.32 | 16 min |
| clarify | merged | 3 / 3 / 2 | not measured | 0 | 0 | $5.13 | $0.34 | 19 min |
| routed | stopped, grant | 4 / 3 / 2 | not measured | 0 | 0 | $4.88 | $0.14 | 27 min |

Routed: seven hunks flagged (template copy, a banner, a docstring, display
flags, context keys). The leg's own summaries of those hunks say they add
no check ("copy only", "nothing is enforced").

### pso-c: psyoptimal #894

| Arm | Outcome | F / C / S | Hidden | Rounds | Q | Kernel | Emulator | Wall |
|---|---|---|---|---|---|---|---|---|
| bare | merged | 3 / 4 / 3 | 5/5 | 0 | 1 | $6.27 | $0.41 | 35 min |
| clarify | merged | 4 / 4 / 4 | 5/5 | 0 | 1 | $7.12 | $0.35 | 31 min |
| routed | merged | 3 / 3 / 3 | 4/5 | 0 | 2 | $5.76 | $0.39 | 43 min |

The baseline has no bare run of this item (the reference passes 5/5, the
base 1/5). Routed failed `test_checkbox_renders_below_sms_consent`, the one
new failure in the broad suite too (23 against the base's 22). No hunk
flagged in routed.

## Bar

An arm is worse than bare on an item when any of these holds:

- it ends short of a merge where bare merged ("to Tom" and "stopped for a
  grant" both count as short of a merge);
- its judged total (F + C + S) is more than one point below bare's (the
  baseline's two bare runs of pso-a differ by one point);
- it passes fewer hidden tests than bare.

Read with no one-point allowance, cut-a clarify (11 against 12) and pso-b
clarify (8 against 9) are also worse; neither changes an item's verdict.

Against the baseline's bare runs, this sweep's bare is lower on pop-a
(11 against 15 judged; hidden equal) and higher on pop-b (8 against 6;
hidden 10/11 against 3/11).

## Findings

- **Governance leg precision.** In four routed runs the `governance.adds`
  leg answered yes, with p_true near 0.8, on hunks that add no check; pop-a
  and pop-c routed were not flagged. A yes stops the task in merge for a
  grant only Tom can give.
- **Merge after a feedback round.** The first merge's docs commit lives on
  the origin and in the kernel's mirror, not on the work branch, so the
  second merge's push is not a fast-forward. Fixed by C9 (`d5ebddebb`),
  after this sweep's commit.
- **cut-a hidden tests do not run.** The task database role `app` has
  CREATEDB but is not a superuser (`core/workspace.py`), so cuttlefish's
  migration `CREATE EXTENSION vector` is refused and all nine hidden tests
  error at setup.
- **pso-b hidden tests do not run.** The judge's verify `PATH` has no
  `pg_config`, so `uv sync` cannot build `psycopg2` when the task's uv cache
  holds no built wheel. cut-a clarify failed the same way. pso-a and pso-c,
  the same repository, had the wheel cached and ran.
- **The replay driver loses its tail.** pop-a clarify's driver exited
  nonzero after the merge, before the stand-in's review; the routine counts
  a nonzero exit as a failed run and keeps only stdout and stderr in memory.
  It was resumed by hand with the routine's own replay command, once,
  alongside the routine's next replay. C9 fixes the exit.
- **Image not held.** pop-b clarify's review check failed in its VM step:
  the candidate's image was not held between build and run. The next firing
  resumed it.
- **Port spans.** Three ports per service span were too few: paused and
  stopped runs keep their ports until judged and torn down, and pop-c
  clarify and routed, then pso-c clarify and routed, were refused at
  `core start`. Widening the database span over the test span made pso-c's
  Postgres fail to bind, because the replay's own servers take ports from
  the test span. The final firing gives each its own span. Each of these
  runs was driven from the start by a later firing; none was a rerun of a
  finished run.
- **The report's hidden-test field reads exit codes.** `hidden_tests` in
  the sweep report is each verify command's exit code. The items' verify
  commands end in a pipe through `grep`, so a run with failing hidden tests
  reads 0. The counts in this record are read from each command's output
  in the result files.

## Spending

From the test ledger `valor_rebuild_test_d4`:

| | Kernel | Emulator | Total | Wall |
|---|---|---|---|---|
| bare (7 runs) | $54.00 | $3.59 | $57.59 | 3.6 h |
| clarify (7 runs) | $43.60 | $3.45 | $47.05 | 3.5 h |
| routed (7 runs) | $34.88 | $2.42 | $37.30 | 3.4 h |
| run `fbad0f38ad54` | $132.48 | $9.46 | $141.94 | |

The routine reports $141.94 for the run and $142.89 for the routine over
the last 30 days; the difference, $0.95, is the void run `5f6359af1674`.
Bare costs most because three of its runs took feedback rounds (pop-b
$12.48, pop-c $13.12, pso-a $9.53). A routed run stopped for a grant ends
before merge and its docs step, so it spends less.

## Question for Tom

Routed runs stop on the governance leg's yes for hunks that add no check.
Should the leg's verdict on a bug fix, a docstring or template copy be
treated as a precision defect and fixed in the leg before cutover, rather
than tapped through per instance? Assumed answer: yes, fixed in the leg
as a bug fix (it adds no governance); no grant is given meanwhile.
