---
tracking: none
slug: d5-cutover-sweep-record
type: record
---

# D5 cutover sweep: every item, bare, clarify and routed

The `emulator` routine's sweep (`docs/routines.md`, `docs/emulator.md`)
over the seven items in `$VALOR_DEMO/items`, each in the `bare`, `clarify`
and `routed` arms, on the rebuild branch at commit `560a769b8` (C9, C11,
C12, C13, C14 and C15 merged). The bar (Before-cutover item "The
emulator's last sweep shows no baseline item worse than its bare run"): no
item scores worse than bare.

## Verdict

The bar is met on six of seven items. The miss is pso-a: clarify merged
but passed 19 of 22 hidden tests against bare's 20, because its own
question filed one endpoint on the wrong side of the permission split and
nothing in the answer corrected it. cut-a is judged on its supplementary
run on the fixed item, where all three arms merged; its first pass ran on a
defective suite line and is shown for the record.

| Item | Bar | Why |
|---|---|---|
| cut-a | met | supplementary run: all three merged at 8/9 hidden; clarify and routed 12 against bare's 11 (first pass on a defective suite line, not judged for the bar) |
| pop-a | met | all three merged at 59/59 hidden; clarify 11 and routed 12 against bare's 9 |
| pop-b | met | all three merged; clarify 11/11 and routed 10/11 hidden against bare's 10/11; totals 9 and 8 against 8 |
| pop-c | met | all three merged at 4/4/3 (docs only, no hidden test) |
| pso-a | not met | clarify merged at 4/3/4 but passed 19/22 hidden against bare's 20/22; routed merged at 4/3/4 and 20/22 |
| pso-b | met | all three merged at 4/4/3; the verify module passed in each |
| pso-c | met | all three merged at 4/5 hidden; clarify and routed 10 against bare's 8 |

The kernel defects that failed D4 do not recur. No routed run stopped for a
governance grant: the `governance.adds` leg answered false on all 159 hunks
it was asked across the seven routed runs. Every run that took a feedback
round merged afterwards (pop-a routed, pop-b clarify and routed, pso-c
bare). All eighteen arms of the six items besides cut-a merged.

## Setup

- **Code.** Worktree `~/src/valor-rebuild-d5`, branch `md5-sweep` at
  `560a769b8`, its own `.venv`, `VIRTUAL_ENV` unset. The persona at that
  commit is C15's. C16 (`eb3e9ea63`, persona) and C17 (`fec311323`, the
  docs verdict's short head) merged after the sweep's commit was fixed and
  are not in it. C16's own measured pso-c bare run (after-p3,
  `m4-2-emulator-runs.md`: 4/4/3, 4/5 hidden, 0 rounds, 1 question) is
  context only; the verdicts here rest on this sweep's runs.
- **Ledger.** Test database `valor_rebuild_test_d5`; the real ledger was
  not touched. `VALOR_DEMO=~/valor-tasks-d5/demo`, work directory
  `~/valor-tasks-d5/work`.
- **Memory off.** `VALOR_MEMORY=off`, so no run reads another run's
  memories and the arms stay independent.
- **Items.** cut-a, pop-a, pop-b, pop-c, pso-a, pso-b, pso-c, copied from
  `~/src/valor-demo`. `toy-greeter` and `pso-a2` are not in the sweep. Each
  item carries a `project` block as the cutover specs do: the Postgres
  items' setup line puts `postgresql@18/bin` on `PATH` and sets `SDKROOT`
  before `uv sync --frozen`; cut-a lists the `vector` extension.
- **cut-a's suite line.** The sweep's copy of cut-a runs
  `apps/podcast/tests` whole. That directory holds a Playwright end to end
  suite the item's `--extra test` sync does not install, so the suite fails
  at collection on the base. The item was fixed upstream (the suite line
  ignores `apps/podcast/tests/e2e`) while the sweep ran; the sweep's three
  cut-a arms ran on the defective line as they started, and a supplementary
  run of all three arms on the fixed item judges cut-a.
- **Serial.** One routine firing at a time; each firing drives one replay
  at a time, item by item, bare, clarify, routed.
- **Ports.** The block 7160 to 7179: `VALOR_TEST_PORTS` 7160 to 7167,
  `VALOR_PG_PORTS` 7168 to 7173, `VALOR_REDIS_PORTS` 7174 to 7179.
- **Disk.** A judged run's workspace is torn down at once (its final diff
  is kept in the build notes) and a decided step's export directories under
  `checks/` are deleted. Each run builds its own dependency image of
  several GB; free space fell to 2.3 GB during cut-a and to 1.2 GB during
  pso-a routed's image build, and recovered when each build ended. The
  first cut-a clarify run was stopped to free disk (below).
- **Stopping for a grant.** A routed run awaiting a grant would be stopped
  on the test database (`core stop`), judged, and counted as handed to
  Tom. None awaited one.
- **Run.** Routine run `b85a5241e16c` under its own objective, over two
  firings: the first drove every arm in order, and the second resumed the
  two runs the first left paused (pop-a routed, pop-b routed). The run
  finished and wrote its report, `$VALOR_DEMO/sweeps/b85a5241e16c.json`;
  wall time from start to report was about ten and a half hours.
- **cut-a supplementary run.** Routine run `f2e827c86a9d` under the same
  objective, from a demo directory (`~/valor-tasks-d5/demo-cut-a`) whose
  items hold only the fixed cut-a; the rest of the environment is the
  sweep's. It ran in one firing and wrote its report,
  `~/valor-tasks-d5/demo-cut-a/sweeps/f2e827c86a9d.json`, in 63 minutes.

## Results

F / C / S is the blind judge's fidelity, correctness and simplicity (0 to
5). Hidden is the reference PR's own tests run on the final commit, passed
of total, read from the verify command's output. Broad is the item's
broader suite: failures on the final commit, and how many of those are
beyond the base checkout's own failure list (only those beyond base are a
regression signal). Rounds is feedback rounds; Q is question messages.
Kernel is Valor's turns; Emulator is the stand-in and judge; both are
metered on the test ledger. The baseline row is the replay baseline's bare
run (`rebuild-baseline.md`), for reference.

### cut-a: cuttlefish #646

Supplementary run `f2e827c86a9d`, fixed item (judges the item):

| Arm | Outcome | F / C / S | Hidden | Broad | Rounds | Q | Kernel | Emulator | Wall |
|---|---|---|---|---|---|---|---|---|---|
| baseline bare | accepted | 4 / 4 / 4 | 8/9 | | 0 | 0 | | | 5 min |
| bare | merged | 4 / 4 / 3 | 8/9 | 0 | 0 | 0 | $3.08 | $1.01 | 20 min |
| clarify | merged | 4 / 4 / 4 | 8/9 | 0 | 0 | 0 | $3.08 | $1.00 | 23 min |
| routed | merged | 4 / 4 / 4 | 8/9 | 0 | 0 | 0 | $2.48 | $1.00 | 18 min |

First pass in run `b85a5241e16c`, defective suite line (not judged for
the bar):

| Arm | Outcome | F / C / S | Hidden | Broad | Rounds | Q | Kernel | Emulator | Wall |
|---|---|---|---|---|---|---|---|---|---|
| bare | to Tom, test check red | 4 / 4 / 4 | 8/9 | 0 | 0 | 0 | $4.74 | $0.92 | 36 min |
| clarify | stopped, not judged | | | | 0 | 0 | $4.35 | | 30 min |
| routed | to Tom, test check red | 4 / 5 / 4 | 8/9 | 0 | 0 | 0 | $4.01 | $0.91 | 18 min |

Every judged arm of both runs fails one hidden test,
`test_drops_unknown_figure_type_and_keeps_rest`, on log wording only: it
expects "dropped 1 unparseable figure" and each candidate logs its own
warning naming the dropped figure. The broad suite has no base failure list
and ran green in each arm. In the first pass the kernel's test check went
red on the suite line (Findings); the first clarify run paused on the docs
verdict's short head and was then stopped (`core stop`) to free disk, its
outcome `stopped (lead: disk; first cut-a pass superseded by rerun)`.

### pop-a: popoto #633

| Arm | Outcome | F / C / S | Hidden | Broad | Rounds | Q | Kernel | Emulator | Wall |
|---|---|---|---|---|---|---|---|---|---|
| baseline bare | accepted | 5 / 5 / 5 | 59/59 | | 0 | 0 | | | 23 min |
| bare | merged | 4 / 3 / 2 | 59/59 | 14, 4 beyond base | 0 | 0 | $7.59 | $0.31 | 36 min |
| clarify | merged | 4 / 4 / 3 | 59/59 | 14, 4 beyond base | 0 | 1 | $4.73 | $0.35 | 38 min |
| routed | merged | 4 / 4 / 4 | 59/59 | 14, 4 beyond base | 1 | 0 | $5.65 | $0.42 | 37 min |

The four broad failures beyond base are the same in each arm: three tests in
`tests/test_integrations_hooks.py` (Redis down) and
`TestFailFast::test_read_hook_is_bounded_when_redis_hangs`. No arm's diff
touches the hooks or the connection code; they are the judge host's
environment. Routed paused once on the docs verdict's short head (below)
and the second firing resumed it.

### pop-b: popoto #191

| Arm | Outcome | F / C / S | Hidden | Broad | Rounds | Q | Kernel | Emulator | Wall |
|---|---|---|---|---|---|---|---|---|---|
| baseline bare | accepted | 1 / 3 / 2 | 3/11 | | 0 | 0 | | | 10 min |
| bare | merged | 3 / 4 / 1 | 10/11 | 0 | 0 | 1 | $7.70 | $0.47 | 24 min |
| clarify | merged | 3 / 5 / 1 | 11/11 | 0 | 2 | 0 | $10.72 | $0.67 | 37 min |
| routed | merged | 3 / 3 / 2 | 10/11 | 0 | 1 | 1 | $8.55 | $0.57 | 27 min |

Hidden failures: bare `test_push_on_unsaved_model_raises`; routed
`test_push_complex_types`. The broad suite has no base failure list and ran
green in each arm. Routed paused once on the docs verdict's short head and
the second firing resumed it.

### pop-c: popoto #188

| Arm | Outcome | F / C / S | Hidden | Broad | Rounds | Q | Kernel | Emulator | Wall |
|---|---|---|---|---|---|---|---|---|---|
| baseline bare | accepted | 4 / 3 / 4 | none | | 1 | 0 | | | 8 min |
| bare | merged | 4 / 4 / 3 | none | 0 | 0 | 1 | $5.65 | $0.26 | 18 min |
| clarify | merged | 4 / 4 / 3 | none | 0 | 0 | 1 | $4.27 | $0.25 | 13 min |
| routed | merged | 4 / 4 / 3 | none | 0 | 0 | 1 | $4.27 | $0.21 | 15 min |

No hidden test (docs only).

### pso-a: psyoptimal #872

| Arm | Outcome | F / C / S | Hidden | Broad | Rounds | Q | Kernel | Emulator | Wall |
|---|---|---|---|---|---|---|---|---|---|
| baseline bare | accepted | 4 / 4 / 4 | 20/22 | | 0 | 0 | | | 36 min |
| bare | merged | 4 / 3 / 3 | 20/22 | 24, 2 beyond base | 0 | 0 | $5.42 | $0.49 | 29 min |
| clarify | merged | 4 / 3 / 4 | 19/22 | 25, 3 beyond base | 0 | 1 | $4.62 | $0.44 | 26 min |
| routed | merged | 4 / 3 / 4 | 20/22 | 24, 2 beyond base | 0 | 0 | $3.99 | $0.43 | 25 min |

Each arm fails `test_archived_team_assign_and_revoke_return_json_403` and
`test_archived_team_member_detail_modal_hides_assign_and_revoke`. Clarify
also fails `test_api_available_evaluations_200_for_coach`. The broad suite
runs over `apps.public` with the hidden test file in place, so its
failures beyond base are the hidden failures counted again; no arm has a
regression beyond them.

Why clarify fails the third test: the test asserts that a Coach gets 200
from `api:team-available-evaluations` (`TeamEvaluationsView`). Clarify's
question sorted "the team-evaluations API" into the configuring actions
that stay on `MANAGE_EVALUATIONS`, then asked only about revoke and core
values. The stand-in answered those two and was silent on the API, as the
answer key is; nothing corrected the reading. The candidate kept the view
on `MANAGE_EVALUATIONS` and its own test asserts 403 for a coach. Bare and
routed moved the view to `ASSIGN_EVALUATIONS`, as the reference does (it
is the list a coach assigns from). The endpoint has no caller in the app,
so the practical weight is low; the miss stands against the bar. No arm
was rerun or patched.

### pso-b: psyoptimal #893

| Arm | Outcome | F / C / S | Hidden | Broad | Rounds | Q | Kernel | Emulator | Wall |
|---|---|---|---|---|---|---|---|---|---|
| baseline bare | accepted | 4 / 4 / 3 | 55/55 | | 0 | 0 | | | 3 min |
| bare | merged | 4 / 4 / 3 | 66/66 | 22, 0 beyond base | 0 | 0 | $4.45 | $0.28 | 35 min |
| clarify | merged | 4 / 4 / 3 | 62/62 | 22, 0 beyond base | 0 | 1 | $4.50 | $0.35 | 27 min |
| routed | merged | 4 / 4 / 3 | 63/63 | 22, 0 beyond base | 0 | 0 | $4.92 | $0.26 | 28 min |

pso-b has no hidden test file: its first verify command runs the
repository's `test_individual180_view` module, which holds each
candidate's own added tests, so the totals differ by those tests. No arm
has a failure in it.

### pso-c: psyoptimal #894

| Arm | Outcome | F / C / S | Hidden | Broad | Rounds | Q | Kernel | Emulator | Wall |
|---|---|---|---|---|---|---|---|---|---|
| bare | merged | 2 / 3 / 3 | 4/5 | 23, 1 beyond base | 1 | 1 | $8.69 | $0.56 | 44 min |
| clarify | merged | 3 / 4 / 3 | 4/5 | 23, 1 beyond base | 0 | 1 | $7.48 | $0.38 | 31 min |
| routed | merged | 3 / 3 / 4 | 4/5 | 23, 1 beyond base | 0 | 1 | $5.62 | $0.37 | 31 min |

The baseline has no bare run of this item. Each arm fails
`test_checkbox_renders_below_sms_consent`, the one broad failure beyond
base too:
the inferred notifications consent checkbox is labelled differently from
the onboarding wording the test expects.

Bare's fidelity of 2, against the answer key's six items, from the judge's
divergences:

- **Item 6, where it links: missed.** No call to action deep links to
  `?tab=` of the first missing item; each item carries its own link, all
  but one to plain settings.
- **Item 3, who is asked: partly.** Teams with no sector count as Sports.
  Bare's question offered that as its default and the stand-in answered
  "Teams with no sector set: your call", so the reading came through the
  exchange.
- **Item 1, what incomplete means: partly.** Both consents count, but they
  are split out as optional under another heading, so a banner of consents
  only is not "Complete your profile".
- **Items 2, 4 and 5** (one date is enough; replace the existing nudge;
  recomputed, nothing stored, no dismiss) are not named.
- Minor: the language hint stays in the page header, and the logic is a
  `User` property plus a view helper with the sports codes added to
  `sector_choices`.

## Bar

An arm is worse than bare on an item when any of these holds:

- it ends short of a merge where bare merged ("to Tom" and "stopped for a
  grant" both count as short of a merge);
- its judged total (F + C + S) is more than one point below bare's;
- it passes fewer hidden tests than bare.

pso-a clarify is worse by the third rule (19/22 against 20/22). No arm on
any other item is worse by any rule, and read with no one-point allowance
no arm's total is below its bare's.
On cut-a's first pass bare and routed both ended short of a merge for the
same reason, the suite line, which is why the item is judged on the
supplementary run.

Against the baseline's bare runs, this sweep's bare is lower on pop-a (9
against 15 judged; hidden equal) and pso-a (10 against 12; hidden equal),
and higher on pop-b (8 against 6; hidden 10/11 against 3/11).

## Findings

- **cut-a's suite line was defective.** The sweep's cut-a item ran
  `apps/podcast/tests` whole; its `e2e/conftest.py` imports Playwright,
  which the item's sync does not install, so the base's suite fails at
  collection and gives no base failure list. With no list, the head's
  environment failures all counted against the change, the test check
  went red, and the task went to Tom. Bare noticed "the suite ran no tests
  at head" and ignored `e2e` in the root conftest; the check still counted
  the failures against the change. Routed's repair round answered without
  a code change and went red again. The item's spec was at fault, not the
  kernel; the fixed item ran in the supplementary run.
- **The docs verdict refuses an abbreviated head.** Three of 38 docs
  verdicts named the head as a 12 character commit id (cut-a clarify,
  pop-a routed, pop-b routed). The kernel refused each ("the head is not a
  full commit id"), the step failed, and the replay paused; a later firing
  resumed pop-a and pop-b routed, and both merged. C17 (`fec311323`)
  resolves an abbreviated head in the docs clone; it is not in this
  sweep's commit.
- **The governance leg stopped nothing.** Across the seven routed runs the
  `governance.adds` leg answered 159 hunks, all false; the highest p_true
  was 0.34 (pop-c routed) and the next 0.29 (pso-c routed). No hunk was
  flagged, so there were no hunks to judge by hand. In D4 four of seven
  routed runs stopped on that leg's false positives; C11 holds.
- **A merge after a feedback round goes through.** Four runs took a
  feedback round (pop-a routed, pop-b clarify twice, pop-b routed, pso-c
  bare) and each merged afterwards; C9 holds.
- **Hidden tests run on every item.** cut-a's nine hidden tests (blocked in
  D4 by the `vector` extension) and pso-b's verify module (blocked by
  `pg_config`) both ran; C12 holds.
- **A clarify question can settle a reading without asking it.** pso-a
  clarify's question filed a callerless endpoint under configuring and
  asked about two other points; neither the stand-in nor the answer key
  spoke to it, and the build followed the question's own reading. pso-c
  bare's question offered "teams with no sector count" as a default the
  stand-in handed back.
- **Bare asks questions.** Bare forces the request judgement to `precise`,
  yet bare asked one question on pop-b, pop-c and pso-c. The forced
  judgement routes the request; it does not stop the build from asking.
- **Per-run dependency images strain disk.** Each run builds its own
  dependency image of several GB. Free space fell to 2.3 GB during cut-a
  and to 1.2 GB during pso-a routed's build; the build helper pauses a
  waiting run but cannot pause one whose build has started. The first
  cut-a clarify run was stopped (`core stop`) to free its image and
  workspace, and the images of the two paused routed runs were deleted
  before they resumed; both rebuilt their image on first use without error.
- **The broad suite counts the hidden file.** On pso-a and pso-c the broad
  command runs over the directory holding the hidden test file, so hidden
  failures appear as broad failures beyond base; on pop-a, four Redis hook
  tests fail beyond base in every arm on the judge host. Neither is a
  regression.

## Spending

From the test ledger `valor_rebuild_test_d5`, run `b85a5241e16c`:

| | Kernel | Emulator | Total | Wall |
|---|---|---|---|---|
| bare (7 runs) | $44.25 | $3.30 | $47.54 | 3.7 h |
| clarify (7 runs) | $40.66 | $2.45 | $43.10 | 3.4 h |
| routed (7 runs) | $37.01 | $3.17 | $40.19 | 3.0 h |
| run `b85a5241e16c` | $121.92 | $8.91 | $130.83 | 10.6 h |

The clarify figures include the first cut-a clarify run, stopped
unjudged ($4.35 kernel). The routine reports $130.83 for the run.

Supplementary run `f2e827c86a9d` (cut-a, three arms): kernel $8.64,
emulator $3.01, total $11.65, 63 minutes. The routine reports $142.48 over
the last 30 days for the two runs.

## Against D4

| Item | D4 bar | D5 bar | D4 bare | D5 bare | D4 clarify | D5 clarify | D4 routed | D5 routed |
|---|---|---|---|---|---|---|---|---|
| cut-a | not met | met | merged 12 | merged 11, 8/9 | merged 11 | merged 12, 8/9 | grant 12 | merged 12, 8/9 |
| pop-a | not met | met | merged 11, 59/59 | merged 9, 59/59 | to Tom 11, 59/59 | merged 11, 59/59 | merged 11, 59/59 | merged 12, 59/59 |
| pop-b | not met | met | merged 8, 10/11 | merged 8, 10/11 | to Tom 8, 9/11 | merged 9, 11/11 | grant 9, 10/11 | merged 8, 10/11 |
| pop-c | met | met | to Tom 9 | merged 11 | merged 11 | merged 11 | merged 11 | merged 11 |
| pso-a | met | not met | to Tom 11, 20/22 | merged 10, 20/22 | merged 11, 20/22 | merged 11, 19/22 | grant 11, 20/22 | merged 11, 20/22 |
| pso-b | not met | met | merged 9 | merged 11, 66/66 | merged 8 | merged 11, 62/62 | grant 9 | merged 11, 63/63 |
| pso-c | not met | met | merged 10, 5/5 | merged 8, 4/5 | merged 12, 5/5 | merged 10, 4/5 | merged 9, 4/5 | merged 10, 4/5 |

Each cell is outcome, judged total (F + C + S) and hidden tests where
measured. D4's misses came from the kernel: routed stopped for grants it
did not need, and merges after a round were refused. Neither recurs. D5's
one miss on the six items besides cut-a is a judged difference in one
clarify run's reading of the request.
