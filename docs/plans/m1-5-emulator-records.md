# 1.5 record: critique, patch rounds, checks, delivery, rebase

The record of [m1-5-emulator.md](m1-5-emulator.md).

Critique round 1 (of 2): revise. Every finding accepted. (1) Driver ends:
`held` only on a held merge, accepted or rounds spent; `NO RUNNER` and an
awaited grant exit unset; stopped and to-Tom outcomes; manual stages
named; `--run`. (2) `head_sha` from the `effect.held` row. (3) The tree at
`<task_dir>/checks/verify-<run>/`. (4) The equal-counts check on this
machine. (5) Spend from `core status` and `tasks.spending`. (6) A sidecar
for inferred key lines. (7) valor-rebuild.md's spend line and
docs/emulator.md fixed in the build. (8) The unmet gate goes to Tom as a
delivery not passed; either run meeting both bars passes. (9) Q2 moved to
Decided by default. (10) `start_calibration` takes `via` and `detail`.
(11) `DROP_ENV` reused; the gateway on a background loop thread.
(12) `tests/test_gateway_meter.py`; the spend mark. (13) The judge diff's
size and truncation recorded. (14) Hand-recorded verdicts named as
unmetered; the #191 trial scored once as information.

Critique round 2 (of 2): revise. Every finding accepted; both rounds
spent. (1) Verification under the baseline's profile plus the tree; the
check compares the whole output. (2) `release_pushes` skips every merge
effect. (3) `merge` split by delivery, grants, join and the merge effect.
(4) `issue` and `retire` on the gateway's loop; `drain` before spend.
(5) The gate waits for review's runner. (6) The judge diff's plan
exclusion (removed in patch round 1). (7) `--stand-in-model`. (8) Step 7's
own emulator task; hand-recorded verdicts read a `blind_checkout`.

Build: the plan built as written, with three readings. `JUDGE_MODEL` is
`claude-sonnet-5-5`, the priced Sonnet id. The driver's ends are tested
through `step` with the fold and `core` stubbed (patch round 1 adds
scripted tasks for the router's answers). `release_pushes` checks that a
push's URL is the run's own origin before it releases one.

Patch round 1: review `changes`, test `gaps`; every finding fixed.

1. The driver gives every `core run` answer an ending: `IDLE`, `LOCK
   LOST`, `LEGACY` and unknown answers exit unset with the answer as the
   reason; `ALREADY RUNNING` waits on the run lock. Tested with scripted
   tasks through the real router.
2. `release_pushes` reads no workdir config; the URL is the kernel's.
3. The judge diff reads no turn-owned path; it is the whole diff.
4. The `mktemp` in `bin/` and the trusted git's directory on the turn's
   `PATH`; tested under the real profile.
5. `JUDGE_MODEL` cited to Claude Code 2.1.288's catalog.
6. `--base` in the stand-in's usage; baseline citations name 5d90b4776.

## Checks after patch round 1, at 9743cf0ba (review round 2 of 2)

- Test: `gaps`. Head 546 passed, 7 skipped; base 5d90b4776 506 passed
  with one lock-race failure that passes alone. Ruff clean. The driver's
  answers, `release_pushes`, and the whole judge diff hold under probes.
  Two `mktemp` forms diverge from `/usr/bin/mktemp`: `mktemp --
  -x.XXXXXX` writes into `$TMPDIR` rather than the current directory, and
  an attached `-t` prefix (`-tapp`, `-tout`) is misread. No test covers
  either.
- Review: `changes`; governance boolean no. The push-URL comparison is a
  security read of kernel-owned state; the shim and `PATH` are sound; the
  judge diff's 70,000-character truncation is the baseline's
  (`5d90b4776:scripts/judge_replay.py:45`). Findings: `docs/emulator.md`
  still names an exclusion in the judge record; the plan says both a live
  probe and the catalog fixed `JUDGE_MODEL`; the citation should be Claude
  Code 2.1.286, the version the baseline ran; `replay.py:382-383` cuts a
  failed `core run`'s error text to 2,000 and 300 characters with no
  source.
- Docs: `updated`, eb4a98e3d on `m1-5-docs2`.

## Delivery: delivered-not-passed

Review rounds are spent. Recommendation: accept one more patch that fixes
the two `mktemp` forms with tests, cites 2.1.286, states one source for
`JUDGE_MODEL` (the catalog, with rollout step 2 saying so), drops the
error-text cuts so the whole output is kept, and fixes the emulator doc's
judge line; rerun the three checks; merge if they pass.

## Tom's feedback (2026-10-03)

Tom, on one more patch round for nine deliveries with the scopes and order put to him: "All as recommended". The order: 1.4v, 2.1, 1.4b, 1.4s, 2.2, 2.3, 3b, 1.4u, 1.5. Valor decides any further round and the merge (valor-rebuild.md, Tom's feedback of 2026-10-03).

Scope: the Delivery's recommendation (both `mktemp` forms with tests, cite 2.1.286, one source for `JUDGE_MODEL`, the error-text cuts dropped, the emulator doc's judge line).

Question 1, the answer keys: Tom ruled the same day that technical calls are Valor's, never raised to him by default. The keys stand as written, with the inferred lines in the sidecar.

## Patch round 2

The Delivery's recommendation, as Tom's feedback scoped it; each finding
fixed in code, no guard added.

1. **`mktemp` forms.** The `mktemp` in `bin/` reads its arguments as
   `/usr/bin/mktemp`'s getopt_long does: `-t` and `-p` take the rest of
   their word or the next one (`-tapp`, `-dtout`, `-t -p`), `--` ends the
   options so what follows is a template, options may follow a template,
   and `--tmpdir` may be cut (`--tmp`). Only a call with no directory and
   no template gets `-p "$TMPDIR"`. Test:
   `test_mktemp_in_a_turn_reads_its_arguments_as_mktemp_does`, under the
   real turn profile; it fails on the round 1 shim.
2. **2.1.286, one source.** `JUDGE_MODEL` is the id Claude Code 2.1.286's
   baked catalog gives `sonnet` for the first party, `claude-sonnet-5-5`
   (read from the 2.1.286 binary on this Mac). `judge.py`,
   `docs/emulator.md`, the Design line, rollout step 2 and the decided doc
   name that catalog alone; the live probe is gone from the plan.
3. **Error-text cuts.** A failed `core run` keeps its whole error in the
   log and in `paused`; before, `paused` held only the first line, the
   command, not why it failed. Test:
   `test_a_core_run_that_fails_keeps_its_whole_error`; it fails on the
   round 1 code. The `said` line's 2,000 is the baseline's
   (5d90b4776:scripts/replay.py:304) and stays.
4. **The emulator doc's judge line.** Already fixed on the docs branch
   (the record names the diff's size and truncation, no exclusion);
   checked against `judge.py`'s result fields.

Rebase onto 3c and 4.2: `core/workspace.py` keeps both sides (the
trusted git's directory on `PATH` beside `VALOR_BROWSER`; provisioning
writes `mktemp` and installs `look`). 3c's cache test made its fake home
under pytest's temp directory, which a turn's profile now denies whole;
it reads the real home's Playwright cache and tries to write a probe into
its fixed build instead (`test_the_browser_is_the_fixed_build_and_a_turn_cannot_write_its_cache`).

Suite on the rebased head: 739 passed, 11 skipped; the cache test above
failed before its fix, and the 16 GB sweep test failed once under load
and passes alone. Ruff check clean; format check flags only
`docs/bridges/telegram.md` and the 2.1 plan.

## Rebase onto 9e5090663

Rebased from `m1-5-docs3` (base `ca620a91f`) onto `9e5090663`, after
1.4i, 1.4b, 1.4s, 1.4u, 1.4v, 1.4w, 3b and 1.4d. What merged on the tip is
the status quo; 1.5's changes ride on top.

Conflicts and their resolution:

1. **`core/workspace.py`, `profile`.** The tip collects every denied path
   in `hidden` so their ancestors are write-denied. 1.5's `tmp` flag keeps
   its form: a profile without `tmp` (every turn's) denies the three temp
   directories, a `fresh` one adds `~/.claude*`, and each adds what it
   denies to `hidden`.
2. **`core/workspace.py`, `harness_env`.** The tip's signature stands
   (`bin_dir` and `passfile` passed, so a check's service layout gets the
   task's `bin/`); 1.5 adds the trusted git's directory after `bin_dir` on
   `PATH` and keeps `MKTEMP` and `write_tools`, which provisioning calls
   before `_install_tools`.
3. **`docs/harnesses.md`.** The Known openings and The workspace sections
   moved to `sandbox-openings.md` and `workspace.md` on the tip; the
   pointers stand, and 1.5's edits move with them: the working session
   writes no shared temp directory, and a replay's spec is written by
   `tests/emulator/workspace.py`. The Files bullet keeps the tip's uv
   lines and 1.5's temp deny, `mktemp`, and git directory.
4. **`docs/tech-stack.md`.** The tip's wrapping, without the removed
   demonstration script.
5. **`tests/emulator/replay.py`.** The item format keeps the tip's
   `project` field and 1.5's "on the final commit".
6. **`tests/test_replay.py`.** The tip builds performers per task, so the
   push test passes `PushBranch(workdir, url=origin)` in `broker.Performers`
   where 1.5 registered it; the Brief carries `push_url` as in 1.5.

Carried for what the tip removed or changed, found by the suite:

- `idle_turns` is gone and `core run` no longer answers `IDLE`; a stop ends
  a hung turn. `IDLE` is out of the driver, `docs/emulator.md`, this plan
  and the decided doc, and the idle test is deleted (with no idle stop its
  scripted task runs on).
- `SEATS` entries are `(harness, model)` since 3b; the stand-in's `MODEL`
  is the frontier seat's id.
- 1.4b's replay spec test imports `replay_workspace` from `scripts/`; it
  uses the `tests.emulator.workspace` import.
- 1.4v's ancestor test makes its home under pytest's temp directory, which
  a turn's profile now denies whole; it passes `tmp`, so what it shows is
  the ancestor rule.
- The `mktemp` tests pass `bin_dir` and `passfile` to `harness_env`.

No cap, timeout or guard added.

Suite on the rebased head: 1138 passed, 21 skipped. Ruff check and
format check clean.

## Merged

The rebase onto 9e5090663 passed all three checks: test pass (base
1098/21, head 1138/21), review pass (governance boolean no), docs updated
at 4bde09032. The lead's suite on 4bde09032: 1138 passed, 21 skipped;
`ruff check` and `ruff format --check` clean. Backup
`valor_rebuild-20261004T021609Z.dump`. `valor-cori-rebuild` fast-forwarded
to 4bde09032; `uv sync`; no migration.

Rollout steps 1 to 4 are done. The takeover gate (Done item 5, rollout
steps 5 to 8) is open: it runs once 1.4c part one merges, and before it
scores #872 the psyoptimal item needs its `project` key.

Review findings carried as follow-ups:

- `judge.py` `export_final` runs `git archive` from `PATH`, not the
  kernel's trusted git. The mirror is kernel-owned, so this is consistency.
- `docs/harnesses.md` "Denied entirely" leaves out `performing_dir`.

## Takeover gate

Run on `m1-5-gate` at 6e123f88f. Result: all three items pass, each on
one run that meets both bars.

| Item | Passing run | Fidelity / correctness / simplicity | Hidden tests | Bar |
|---|---|---|---|---|
| pop-b (#191) | `pop-b-gate` | 3 / 4 / 1 | 11 of 11 | F2, 7 of 11 |
| pso-a (#872) | `pso-a-gate-2` | 4 / 4 / 4 | 20 of 22 | F3, 20 of 22 |
| pop-a (#633) | `pop-a-gate-b` | 4 / 4 / 3 | 14 of 14 | F4, 14 of 14 |

The baseline is n = 1 per item and arm, and one judge point is noise
(rebuild-baseline.md). The bars are the baseline's; the stand-in that
answered here is the frontier seat, `claude-opus-5-5`, where the baseline
had Sonnet, and no bare or clarify arm was rerun under it. The judge is
`claude-sonnet-5-5` (`JUDGE_MODEL`); the baseline did not record its
resolved id.

### Setup

- Suite at 6e123f88f: 1194 passed, 23 skipped, 1 failed. The failure is
  `test_start_project_from_the_command_line`, a port race; it passes run
  alone.
- Ledger: `VALOR_DB=valor_rebuild_test_gate_1_5_ledger`, a fresh database,
  so no gate row is in `valor_rebuild`. `VALOR_WORK=~/valor-tasks-gate-1-5`,
  apart from `~/valor-tasks`, because the workspace sweep stops the
  services of tasks it does not know. Ports 6460 to 6469.
- pso-a's `project` key (in `~/src/valor-demo/items/pso-a.json`):

  ```
  {"kind": "django",
   "setup": ["PATH=\"$PATH:/opt/homebrew/opt/postgresql@18/bin\" SDKROOT=/Library/Developer/CommandLineTools/SDKs/MacOSX.sdk uv sync --frozen"],
   "env": {"UV_PYTHON": "3.12", "DJANGO_SETTINGS_MODULE": "settings.test"},
   "suite": "uv run pytest -p no:cacheprovider -q --junitxml={junit} apps/team apps/public apps/api"}
  ```

  Sources: main's `projects.json` names psyoptimal's stack as Django and
  PostgreSQL; the popoto items give the uv and pytest shape; the item's
  own verify commands give `uv sync --frozen`, `settings.test` and the
  scope; psyoptimal carries `pytest.ini` and pytest-django. `psycopg2`
  builds from source: the setup sandbox's PATH has no keg-only
  `pg_config`, and `xcrun` cannot write its cache there, so clang finds no
  SDK without `SDKROOT`.
- pop-a's suite carries `--ignore=tests/test_connection.py`, as the item's
  broad verify command does. That file points popoto's global pool at
  port 6379, which the turn profile denies, and every later test errors.

### Runs

Spend is in US dollars. Kernel spend is gateway-metered; emulator spend is
the stand-in plus the judge.

| Run | Task | Emulator task | Outcome | Kernel | Emulator | Attention | Held merge | Final rev | Judge diff |
|---|---|---|---|---|---|---|---|---|---|
| `pop-b-gate` | 92b0400691fe | ec919d8390f1 | held, accepted | 7.1435 | 0.4114 | 1 question, 4 verdicts, 0 feedback rounds | e84ac44608bd | 0c74745c | 54,684 chars, 1,374 lines, not truncated |
| `pso-a-gate` | 9ed7c4dc84e8 | cfbeda1b06b9 | stopped, setup | 6.1915 | 0 | none | none | 24af1a58 | not judged |
| `pso-a-gate-b` | 6fbf68b55f13 | 50585cacaa58 | stopped, setup | 3.1227 | 0 | none | none | none | not judged |
| `pso-a-gate-c` | 1f7cd4127ea3 | 67a95752d601 | to Tom, loops spent | 4.4589 | 0.2802 | 4 verdicts | none | 88b7ff27 | 39,129 chars, 835 lines, not truncated |
| `pso-a-gate-2` | 46b075964b5b | 2eb63b42a0a0 | held, accepted | 3.3063 | 0.4042 | 2 verdicts | ad4c6ef50a03 | ce071426 | 33,527 chars, 702 lines, not truncated |
| `pop-a-gate` | 11f4eb44bf92 | 0bac6ef752af | stopped, suite | 5.3216 | 0 | none | none | fed015b3 | not judged |
| `pop-a-gate-b` | d81b5826067c | e6f9f56ead84 | held, accepted | 3.4939 | 0.2750 | 4 verdicts | 4d4b1787478b | fec95c56 | 26,898 chars, 657 lines, not truncated |

Total: kernel 33.0384, emulator 1.5711 with the trial's judge, 34.6096.

- `pop-b-gate`: the stand-in answered the one question. Broad suite 338
  passed, 11 skipped.
- `pso-a-gate` and `pso-a-gate-b`: the setup step failed on
  `pg_config`, then on clang's `stdlib.h`, so the test check went red on
  an environment fault and the task was stopped. `pop-a-gate`: the
  suite's `test_connection.py` fault above; 2258 tests errored at base
  and head, so the candidate's own tests read as new failures, and the
  task was stopped. These three runs are counted as environment runs of
  the item keys, not as the item's two runs.
- `pso-a-gate-c`: docs answered `changes` in both rounds (round 1, the
  shipped plan file left in place; round 2, a doc saying the staff
  permissions page grants permissions). With the loop spent the join is
  did_not_pass and the task goes to Tom. Judged as information: 4 / 4 / 3.
  The judge's hidden-test command errored on every test (0 ran, the
  services not yet up); the same command rerun under the task's sandbox
  gave 20 of 22. Below the bar as a to-Tom run, so pso-a ran again.
- `pso-a-gate-c` and `pso-a-gate-2`, hidden tests: the two failing are
  `test_archived_team_assign_and_revoke_return_json_403` and
  `test_archived_team_member_detail_modal_hides_assign_and_revoke`. Broad
  suite 1598 ran: 22 failing at base, 24 at head, the 2 new ones those.
- `pop-a-gate-b`: round 1's test check was red on one real failure,
  `test_meta_order_by_other_field_disables_pushdown`; review asked for it
  and for three Meta tests whose bounds tested nothing. Round 2 passed.
  The judge's broad suite shows 4 new failures, all Redis-down hook tests
  (`test_integrations_hooks.py` 3, `test_production_contracts.py` 1); the
  kernel's own test check lists all 4 as failing at base.

### Verdicts recorded by hand

Review and docs have no runner at 6e123f88f: `runners()` in
`core/__main__.py` registers judge, the working states, critique and
test, and `fresh.review_runner` is defined but not registered. Every
review and docs verdict below was recorded with `python -m core verdict
... --role-played` by a fresh Opus subagent from a blind checkout and the
kernel's own input builders. None is metered.

| Run | Round | Review | Docs |
|---|---|---|---|
| `pop-b-gate` | 1 | changes (KeyField SCAN WRONGTYPE; a docs tuple mismatch) | changes |
| `pop-b-gate` | 2 | pass, predicted failure 0.1 | no_change |
| `pso-a-gate-c` | 1 | pass | changes |
| `pso-a-gate-c` | 2 | pass | changes |
| `pso-a-gate-2` | 1 | pass, predicted failure 0.08 | no_change |
| `pop-a-gate-b` | 1 | changes | no_change |
| `pop-a-gate-b` | 2 | pass, predicted failure 0.06 | no_change |

No review answered the governance boolean yes. A hand-played docs stage
cannot commit, so its answers are `no_change` or `changes`; a doc edit
it would make is a patch round instead.

### The #191 trial

Task 75c0902b6e25, head 1cfb6000, scored once as information with emulator
task e93e0ad49f18: 1 / 2 / 1, hidden tests 4 of 11, broad suite 327
passed. Diff 69,197 chars, 1,568 lines, not truncated. Emulator spend
0.2003. It builds `Model.push(name, value)`, where the answer key has
`field.push`.

### Result files

In `~/src/valor-demo/results/`, outside the repo (they hold the answer
keys and hidden tests): `pop-b-gate.json`, `pso-a-gate.json`,
`pso-a-gate-b.json`, `pso-a-gate-c.json`, `pso-a-gate-2.json`,
`pop-a-gate.json`, `pop-a-gate-b.json`, `pop-b-trial-scored.json`.

### Lead's decisions on the gate

- The three runs stopped by the items' own setup faults (wrong project
  keys, a suite that needed a live connection) do not count: the bar
  measures the kernel, and those runs never reached its work. With them
  left out, all three items pass.
- `__main__.runners()` registers no review runner, so review verdicts were
  played by fresh subagents and not metered. This is a bug against the
  plan's runner list, which has review registered unconditionally; task
  1.5r fixes it. Docs was hand-played by design, through the driver's `NO
  RUNNER` exit, while governance's calibration entry check fails. One
  kernel run of pop-a with review carried by the kernel confirms the fix.
- Item edits live in `~/src/valor-demo/items`; result files stay in
  `~/src/valor-demo/results`, uncommitted, since they hold the answer keys.
