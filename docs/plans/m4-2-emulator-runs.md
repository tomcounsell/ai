---
tracking: none
slug: m4-2-emulator-runs
type: record
---

# 4.2 emulator runs: the persona, before and after

The paired runs [m4-2-persona.md](m4-2-persona.md) asks for under "The
emulator evidence", one row per run, and the verdict per item under its
"Worse" rule and the #894 bar.

## Setup

- **Arms.** Both arms run on the rebuild branch at `968e7aa05`, each from
  its own worktree, against one test ledger. The after arm is that head
  unchanged, with the 4.2 persona. The before arm is the same head with
  one line in `tasks.dispatch` changed: the rendered persona is replaced
  by "You are Valor.", the text turns started with before 4.2. That
  pair measures the persona alone, on the pipeline that ships. Each
  `turn.started` carries `persona_sha256`, which shows the arm.
- **The p1 pair.** This pair reruns pso-c bare after
  [c15-persona-894.md](c15-persona-894.md). In the after arm, C15's
  persona patch (`persona/conduct.md` and `persona/delivery.md` only)
  is applied to the worktree with `git apply`. Its turns carry
  `persona_sha256`
  `61f4517ee877e71f9499101c07c2d370caf49f4d2433a70c7d0eab06b6ed427b`
  (11,141 bytes). The before arm is unchanged.
- **Emulator.** The driver is `tests.emulator.replay --judge`: an
  Opus-class stand-in plays Tom, and a Sonnet judge scores fidelity,
  correctness and simplicity (F/C/S). Labels are forced as the plan
  says.
- **The pso-c item** is built in the item directory.
  - Its base `953713a3` is #894's base tree minus `docs/plans`, as one
    squashed commit with no history. The base passes the leak check.
  - The Notion card was not reachable, so its key holds the six answers
    condensed from rebuild-demonstration.md's table and the issue's
    "Resolved requirements" section. Each line is marked as condensed.
  - The hidden test is the PR's `test_settings_notifications_consent.py`
    (5 tests). The reference passes 5 of 5 and the base 1 of 5.
- **Docs check.** It has no runner on this head, so for every docs verdict
  a fresh subagent, blind to the arm, plays the stage by hand. Every
  verdict was `no_change`, with findings.
- **Counting.** Hidden tests are counted from the pytest or unittest
  summary line, never from the exit code. "Broad" is the judge's
  suite run against base: failures that do not fail at base.

## Runs

Spend is kernel plus emulator (stand-in and judge). Rounds are feedback
rounds; Q is questions asked.

| Run | Task | Outcome | Rounds | Q | F/C/S | Hidden | Broad | Spend |
|---|---|---|---|---|---|---|---|---|
| pso-a bare before | 62998beb8d74 | held | 0 | 0 | 4/3/4 | 20/22 | 2 | $4.15 + $0.45 |
| pso-a bare after | 168d45c18415 | held | 0 | 0 | 4/3/3 | 20/22 | 32, environment | $5.02 + $0.54 |
| pop-a bare before | 0a5e5b60abfd | held | 1 | 0 | 4/4/3 | 59/59 | 4 | $5.50 + $0.40 |
| pop-a bare after | 7a1c150c3ecf | held | 2 | 1 | 3/3/1 | 59/59 | 4 | $12.30 + $0.44 |
| pop-a bare after-r2 | 236105e147dc | held | 1 | 0 | 4/4/3 | 59/59 | 4 | $6.26 + $0.45 |
| cut-a bare before | b245741cabbf | held | 0 | 0 | 4/3/4 | 8/9 (re-verify) | 127 passed (re-verify) | $1.77 + $0.97 |
| cut-a bare after | 670a4d807c75 | held | 0 | 0 | 4/4/3 | 8/9 (re-verify) | 128 passed (re-verify) | $3.67 + $1.00 |
| pop-b clarify before | 95b9bfd1dd5c | held | 2 | 1 | 2/3/1 | 9/11 | 348 passed | $13.71 + $0.67 |
| pop-b clarify after | eca4e81807ed | held | 0 | 1 | 4/3/2 | 10/11 | collection errors | $7.46 + $0.40 |
| pso-b bare before | c190fa51eb9a | held | 0 | 0 | 4/3/3 | unmeasured | unmeasured | $4.85 + $0.29 |
| pso-b bare before-r2 | af140f378314 | held | 0 | 0 | 4/4/3 | view 59/59 | 0 | $3.47 + $0.27 |
| pso-b bare after | 2fca3e44f965 | held | 0 | 0 | 4/4/3 | view 63/63 | 0 | $4.68 + $0.27 |
| pop-c clarify before | 4666cd7457e2 | held | 1 | 1 | 4/4/3 | none in item | 300 passed | $6.18 + $0.27 |
| pop-c clarify after | be7b007bf29e | held | 0 | 1 | 3/4/2 | none in item | 314 passed | $6.42 + $0.24 |
| pso-c clarify before | ff942c815596 | held | 0 | 1 | 3/4/4 | 5/5 | 0 | $5.65 + $0.33 |
| pso-c clarify after | 7fff8509fb19 | held | 0 | 2 | 3/3/3 | 4/5 | 1 | $5.51 + $0.38 |
| pso-c clarify after-r2 | 7ac3ef2d3587 | held | 0 | 1 | 4/4/4 | 5/5 | 0 | $6.19 + $0.34 |
| pso-c bare before | aae2e8a72b78 | held | 2 | 0 | 4/4/4 | 5/5 | 0 | $9.25 + $0.40 |
| pso-c bare after | d76dfb8bcd02 | held | 2 | 0 | 4/4/3 | 5/5 | 0 | $10.68 + $0.47 |
| pso-c bare before-p1 | 4b5cc43cb666 | held | 2 | 0 | 3/3/4 | 3/5 | 2 (the hidden 2) | $7.62 + $0.38 |
| pso-c bare after-p1 | 82fd01e7f009 | held | 0 | 1 | 3/4/3 | 5/5 | 0 | $6.12 + $0.42 |
| pso-c bare after-p2 | 6b7dd86844c2 | held | 1 | 1 | 4/4/3 | 5/5 | 0 | $9.32 + $0.88 |

- **after-r2 rows.** Each is the one rerun the "Worse" rule allows, and
  the item is judged on it. Both runs are recorded.
- **pso-b.** The before row's verify failed in `uv sync`: psycopg2 builds
  from source and found no `pg_config` (the gap C12 fixes). Its
  candidate was torn down before the failure was seen. before-r2 is a
  fresh before run on the same code. It replaces the lost result and is
  not a second sample. pso-b has no hidden reference test. Its first
  verify command runs the candidate's own
  `apps.public.tests.test_individual180_view`, so that column reads
  "view".
- **cut-a.** Both arms are re-verified on their kept candidates
  (`d74c7ac9`, `37ce0ad8`) in the C12 verify environment, with SDKROOT
  set (below). Both fail the same hidden test,
  `test_drops_unknown_figure_type_and_keeps_rest`. Without SDKROOT, the
  before arm's re-verify fails in the psycopg2 compile ("'assert.h' file
  not found"), so it measures nothing.
- **pso-b re-verify.** Both arms are re-verified in the same environment:
  after `cf7e8224` 63/63, 0 broad; before-r2 `c3ef1933` 59/59, 0 broad.
  These match the rows.

## Verdicts

| Item and arm | Verdict | On |
|---|---|---|
| pso-a bare | not worse | Same hidden count, F and C equal, rounds equal |
| pop-a bare | not worse | after-r2: rounds 1 against 1, F/C 4/4 against 4/4 |
| cut-a bare | not worse | Re-verify 8/9 in both arms, C one point higher |
| pop-b clarify | not worse | Fewer rounds, hidden 10/11 against 9/11, F two points higher |
| pso-b bare | not worse | After against before-r2: rounds 0 and 0, F/C equal |
| pop-c clarify | not worse | F one point lower (within the rule), rounds 0 against 1 |
| pso-c clarify | not worse | after-r2: 5/5, F/C 4/4 against 3/4 |
| pso-c bare | not worse; misses the #894 bar | Rounds 2 against 2, no question asked |
| pso-c bare, p1 | not worse; after-p1 and after-p2 miss the #894 bar (items 3 and 6) | See below |

A worse result or a missed bar is a 4.2 patch finding.
- **pso-c clarify after** was worse on its first run: hidden 4/5 against
  5/5. This is a 4.2 patch finding; its rerun is not worse.
- **pop-a bare after** took 2 rounds against 1. This is a 4.2 patch
  finding; its rerun is not worse.
- **pso-c bare after** missed the #894 bar: 2 feedback rounds and no
  question. Round 1 said the sports dates were only an example; round 2
  said the Sports gating covers inactive memberships. This is the patch
  C15 makes.

## #894: the p1 pair

- **after-p1** asked one question in its first turn, with defaults:
  - is the sports-dates rule the whole requirement or one case of a
    wider rule;
  - who is asked for sports dates;
  - does the banner replace the old nudge.

  The stand-in's answer carries key items 1, 3, 4 and 6. The run
  delivered with 0 feedback rounds, hidden 5/5 and 0 broad.
- **The judge's divergences from the six answers.** There is one, on
  item 3: teams with a null or blank sector count as Sports, so their
  members are asked for sports dates. That is a build error made after
  the right answer, not a missed question; before-p1 has the same rule.
  On item 4, the old first-name header block stays only for the
  language hint, which the judge calls a partial miss. The rest are
  copy or structure, which the key leaves to Valor.
- **before-p1**, beside it, took 2 rounds on the same two misses as
  pso-c bare before. It asked nothing and passed hidden 3/5: the
  consent time is restamped on every save, and the checkbox label
  differs from the onboarding consent text.
- **after-p1 misses the bar.** The judge names item 3, and the bar is
  that its divergences name none of Tom's six answers. The null-sector
  slip shows in both arms, so the one rerun the "Worse" rule allows
  is taken: after-p2, the after arm only, same setup, C15's persona.
- **after-p2** (same persona digest) asked one question in its first
  turn. It named the wider reading as option (b) but defaulted to the
  narrow one; the stand-in chose (b). Round 1 said both consents count
  as missing items, and the fix adds the notifications-consent
  checkbox. Rounds pass (1). The sector rule is right this time. The
  judge names item 6: the banner's main button links to plain settings
  with no `?tab=`, though each listed item deep-links to its tab. So
  after-p2 misses the bar as written.
- **Result.** after-p1 misses the bar on item 3 and after-p2 on item 6.
  The one rerun is used, so the task goes back to patch: C16, persona
  text. Its after-arm rerun runs on the same after worktree.

## Findings

- **Broad-suite causes** (information only).
  - pso-a bare after: 32 failures not at base (26 prose service, 4 v1
    API, 2 hidden) in a diff that touches team permissions only.
    Valor's own check on the same commit saw none. The base, run in the
    judge's environment, is clean. Cause: the environment at judge
    time.
  - pop-b clarify after: six test files failed collection in the judge's
    run, but collected in Valor's own check on the same commit. The base
    is clean. The commit is gone, so code against judge state cannot be
    settled.
  - pop-a: the same 4 failures, not at base, in every run of both arms.
- **VM verify never ran tests (C14).** All 24 `verify.ran` events in the
  test ledger, both arms, failed in setup or ran no tests. The pso setup
  line begins `PATH=... SDKROOT=... uv sync`, so the deps-image matcher
  (`^uv sync`) misses it, and the offline sync runs in the bare base
  image. popoto's offline sync exits 1. Each failure is recorded with
  cause "commit", though base fails the same way, and the review
  decided without VM results. Both arms are hit equally, so the
  comparison stands.
- **SDKROOT in the items' verify lines.** cut-a's and pso-b's verify
  lines do not set SDKROOT, so psycopg2 cannot compile in the sandbox.
  This gap is in the items, which are test data; the cutover project
  specs carry SDKROOT in their own setup line
  ([cutover-data.md](cutover-data.md)). The re-verifies set it for all
  four runs.
- **Disk.** The data disk filled during the runs. The lead stopped nine
  runs mid-run by SIGTERM, and they were resumed. Later runs work on an
  APFS image on an external disk, where a teardown takes about an hour
  against 16 seconds on the data disk: `workspace.rmtree` makes several
  syscalls per entry.
