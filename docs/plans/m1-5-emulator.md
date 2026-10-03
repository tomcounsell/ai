---
tracking: none
slug: m1-5-emulator
type: build
status: planned; revised after critique round 1, awaiting round 2
critique_rounds: 2
review_rounds: 2
---

# 1.5: the emulator in `tests/emulator/`, metered through the gateway, and the takeover gate

Milestone 1.5 of [valor-rebuild.md](valor-rebuild.md). The replay scripts
become a test package, every stand-in and judge call goes through the
kernel's gateway onto one task's spending, the stand-in moves to an
Opus-class model, and the full pipeline runs on popoto #191, psyoptimal
#872, and popoto #633 against the bars in
[rebuild-baseline.md](rebuild-baseline.md). The shared design of the task
directory, the kernel mirror, fresh sessions, and project specs is
[m1-4-checks.md](m1-4-checks.md)'s and is not repeated here.

Spending is metered and reported, never capped. The gate ends on its own
Done items only: no run, item, or call has a spend limit, and the
milestone's gate line drops its per-run cap in this build (see Docs
fixed). Each run's spend is written into its result and into the gate
record as information. The role-played steps (the stand-in through the
gateway is metered; any check verdict recorded by hand through
`python -m core verdict` is not) are named in the record.

## Stakes

```
critique_rounds: 2
review_rounds: 2
```

The move is mechanical, but the gate is the evidence that the rebuilt
pipeline can take over, and the metering change touches the gateway's
callers. Two rounds each.

## The Done items, as evidence

| Done item | Evidence |
|---|---|
| The replay scripts live in `tests/emulator/` | `git ls-files scripts/` lists none of `replay.py`, `replay_common.py`, `replay_workspace.py`, `role_play_tom.py`, `judge_replay.py`; `python -m tests.emulator.replay --help` exits 0 from the repository root |
| Stand-in and judge calls go through the gateway and meter onto one task's spending | a run's result names its emulator task; that task's `gateway.opened` and `gateway.charged` rows carry `route: gateway`, one pair per stand-in or judge call; the metered spending `python -m core status <emulator task>` prints matches the result's `emulator_spend_usd`; no `costs.jsonl` is written anywhere |
| The stand-in is Opus-class | every stand-in call's `gateway.charged` row names `claude-opus-5-5` (the `frontier` seat); every `role_played` row's `by` names it |
| The judge is the baseline's Sonnet judge, pinned | every judge call's charged row names one Sonnet id, recorded in the result's `judge_model` |
| The gate: three items reach a held merge | for each of pop-b (#191), pso-a (#872), pop-a (#633): a run whose result has `outcome: held`, which the driver writes only when `tasks.status` shows `merge_effect.state == "held"`; hidden tests and fidelity scored against the bars below; the gate record in this file lists them |
| `/tmp` is no longer shared between runs | the working turn profile denies `/private/tmp`, `/private/var/tmp`, `/private/var/folders`; a turn writing `/tmp/x` is refused while its `TMPDIR` write succeeds |

### The gate's bars

From rebuild-baseline.md, the judge's 0 to 5 scores (fidelity,
correctness, simplicity) and hidden tests passed:

| Item | Bar | Fidelity at least | Hidden tests at least |
|---|---|---|---|
| pso-a (#872) | bare (F4, 20 of 22) | 3 | 20 of 22 |
| pop-a (#633) | bare (F5, 14 of 14) | 4 | 14 of 14 |
| pop-b (#191) | clarify (F3, 7 of 11) | 2 | 7 of 11 |

"Within one point" means at most one below, so #191's fidelity bar is 2:
the milestone names its clarify run's fidelity, 3, as the score to be
within one point of. An item passes if either of its two runs meets both
bars. Both runs are recorded. A stopped task, or one the pipeline hands
to Tom after its loops are spent, is a run below the bar and uses the
rerun.

The attention each item took (questions asked, feedback rounds, the held
merge) is counted from its `role_played` and attention rows and written
beside its scores. Spend per run (the item task's kernel spend and the
emulator task's stand-in and judge spend) is written beside them too.

If an item has no passing run after its rerun, the gate is a Done item
not met: the delivery goes to Tom as not passed, with both runs, the
findings, and a recommendation, as every unmet Done item does.

## Threat model

- The turn controls its clone, its commits, its delivery note, and the
  diff text. The stand-in and judge read that text, so it can carry prompt
  injection into their calls. Both run tool-less (`--tools ""`, safe mode,
  no MCP), with a fresh empty Claude Code config, the kernel's `DROP_ENV`
  applied, and only the gateway's placeholder token, so an injected
  instruction can change a score or an answer, nothing else. The hidden
  tests are run by the driver, not judged, so the gate's test numbers do
  not depend on the judge.
- The driver never runs git in a turn-owned repository outside the
  sandbox. The stand-in's delivery context and the judge's export read the
  kernel mirror, never the turn's workdir. The hidden-test tree lives in a
  directory no turn can write.
- No stand-in or judge process holds a real credential: the gateway
  replaces the placeholder with the kernel's login on the way out, and
  retires the token after each call.
- The driver never releases a held merge. The gate's evidence is the held
  merge.

## Design

### The package (`tests/emulator/`)

`git mv` moves the five scripts into `tests/emulator/` with an
`__init__.py`:

```
tests/emulator/__init__.py
tests/emulator/replay.py
tests/emulator/common.py          (from replay_common.py)
tests/emulator/workspace.py       (from replay_workspace.py)
tests/emulator/stand_in.py        (from role_play_tom.py)
tests/emulator/judge.py           (from judge_replay.py)
```

No name matches `test_*.py`, so pytest collects none of them. Each is run
as a module (`python -m tests.emulator.replay ITEM --arm ARM`), which
`pythonpath = ["."]` already allows, and the `sys.path` inserts go.
Imports between them become `from tests.emulator import common`.

`scripts/demo_workspace.sh` (the first demonstration's workspace) is
superseded by kernel provisioning and is deleted with the demo-profile
tests in `tests/test_demo_sandbox.py` that read it. The sandbox profile
tests that read the kernel's profiles stay.

### Metering (`tests/emulator/common.py`, `core/tasks.py`)

One emulator task per run. A calibration task already folds as
calibration, carries no `sdlc` marker, is read-only, and is refused by
runs, session, guards, verdicts, and stop: exactly a task that only
meters. `tasks.start_calibration` gains two optional arguments, `via`
(the command recorded in its provenance, default `python -m core
calibrate`) and `detail` (a dict merged into the `task.started` payload),
and takes its instruction from `detail` when one is given. The driver
calls it as

```
start_calibration(conn, "emulator", via="python -m tests.emulator.replay",
                  detail={"instruction": "meter emulator run RUN",
                          "emulator": {"run": RUN, "item_task": TASK}})
```

at the run's first invocation, writes the id into the result file, and
reuses it on resume. Nothing else in `core/` changes for it.

The driver is synchronous. It starts its own `Gateway(dsn,
credential=ClaudeLogin())` on an ephemeral loopback port, on an asyncio
event loop in a background thread that lives for the invocation; `issue`
and `retire` are called on that loop through
`asyncio.run_coroutine_threadsafe`, and the thread closes the gateway on
exit. `claude_json` becomes:

1. `url = issue(emulator_task, call_id)`, `call_id` a fresh id naming
   the role and round (`stand-in.answer.2`, `judge`).
2. `claude -p` with `ANTHROPIC_BASE_URL=url`,
   `CLAUDE_CODE_OAUTH_TOKEN=TURN_TOKEN`, `CLAUDE_CONFIG_DIR` a fresh empty
   directory under the run's directory, every variable starting with a
   prefix in `harnesses.claude_code.DROP_ENV` (`CLAUDE`, `ANTHROPIC`,
   `PG`, `VALOR_PG`) and `AI_AGENT` dropped, the same safe-mode,
   tool-less, no-persistence flags as now, and `--model` the pinned id.
3. `retire(emulator_task)` in a `finally`.

The call stays a Claude Code call, not a direct API call, because the
login credential is proven only through Claude Code. `costs.jsonl`, its
writer, and its readers go. The result's spend comes from the ledger:
`tasks.spending` over each task's rows gives `emulator_spend_usd` and
`kernel_spend_usd`. A separate task keeps the item task's spending
comparable to the baseline's kernel column, and lets a stopped item task
still be judged.

### Models (`tests/emulator/stand_in.py`, `tests/emulator/judge.py`)

- The stand-in uses `settings.SEATS["frontier"]` (`claude-opus-5-5`). The
  `--model` flag stays for a deliberate comparison run and defaults to the
  seat.
- The judge uses a pinned Sonnet id, `JUDGE_MODEL` in `judge.py`. At build
  the alias `sonnet` is resolved once (one tool-less `claude -p --model
  sonnet` call through the gateway, its charged row's model read back),
  and that id is written into `JUDGE_MODEL`. It is expected to be
  `claude-sonnet-5-5`. The baseline did not record its resolved id; the
  gate record says so beside the scores.

### Kernel-owned reads (`tests/emulator/stand_in.py`, `tests/emulator/judge.py`)

The rev read is the merge's `head_sha` when a merge is held, else the
candidate. `head_sha` is taken from the payload of the task's
`effect.held` ledger row whose effect id is `status.merge_effect.effect_id`;
the candidate from `tasks.status`. `core/` gets no change for this.
`workspace.blind_checkout(mirror, base, rev, dest)` writes the tree, and
the stand-in's delivery diff is taken there.

The judge's diff is base to that rev with `docs/plans/` left out, so it
compares with the baseline's diffs, which carried no plan document. Each
result records the diff's size in characters and lines, the paths left
out, and whether the judge's 70,000 character truncation cut it. The
pipeline's docs commits stay in the diff; the gate record notes that the
baseline's diffs had none.

### The hidden-test tree (`tests/emulator/judge.py`)

The tree lives at `<task_dir>/checks/verify-<run>/`, made fresh by the
driver with `workspace.fresh_dir` before each verification. `checks/` is
written only by the kernel and no turn profile lists it, so no turn can
write the tree. Verification runs the item's `verify` commands under the
working turn's profile with that one directory added read-write and its
`TMPDIR` set inside it, with the task's services started, as the baseline
did. Only the source tree moves, from the turn's `state/work/tmp/verify`
to this directory; the commands, services, and timeout are the
baseline's.

### The driver (`tests/emulator/replay.py`)

- `--run NAME` names the run; the default stays `{item}-{arm}`. A name
  whose result already has an outcome is refused unless `--rebuild` is
  given. Gate runs are named `<item>-gate` and `<item>-gate-2`, so
  `pop-b-routed.json` stays as it is.
- `MAX_RUNS` goes. The pipeline's own loop endings end a task.
- `MAX_FAILED_RUNS` goes. A failed run exits the driver with the outcome
  unset and the failure printed; the next invocation resumes, as an unset
  outcome already does.
- `NO RUNNER` exits the driver with the outcome unset and the stage named.
  This covers every stage left in `verdicts.MANUAL_STAGES` with no
  runner registered at gate time: test, review, or docs (docs stays
  manual while governance's entry check fails). The verdict is recorded
  through `python -m core verdict` by a fresh Opus subagent, and the next
  invocation resumes the same task.
- A task in `merge` whose merge effect is not held (a governance instance
  awaiting Tom's tap, or a refused payload) exits the driver with the
  outcome unset and the reason named; it resumes after Tom's tap. The
  stand-in reviews only when `merge_effect.state == "held"`.
- At a held merge the stand-in reviews: accept, or its two feedback rounds
  spent, ends the run with `outcome: held`, the feedback count recorded.
  Feedback inside the two rounds is recorded and sends the task to patch.
- A stopped task ends the run with `outcome: stopped`. A task handed to
  Tom after its loops are spent ends it with `outcome: to tom`.
- `release_pushes` keeps approving `push_branch` to the local origin and
  never answers a merge.
- The result gains `emulator_task`, `emulator_spend_usd`,
  `kernel_spend_usd`, `judge_model`, `stand_in_model`, `attention`
  (counts by kind), `final_rev`, `merge_effect_id`, and the judge diff's
  size, exclusions, and truncation.

### `/tmp` (`core/workspace.py`)

The working turn profile denies `/private/tmp`, `/private/var/tmp`, and
`/private/var/folders` as the fresh-session profile does. `TMPDIR` is
already per turn. The check environment's services keep their own
sockets under the task directory, so nothing the suite needs lives in
`/tmp`.

### Docs fixed in the same build

- `docs/plans/valor-rebuild.md`: the gate line's "Spend within the $25
  per-run cap" becomes "Spend is metered and reported in the gate record,
  with no cap."
- `docs/emulator.md`:
  - paths, module invocation, and the metering section;
  - line 15 ("gates nothing") and lines 426 to 428 ("refuses no
    merge... None has been named") say the takeover gate is milestone
    1.5's one-time Done item, set by Tom, not a standing rule;
  - line 156 (`run cap`, `failed`) describes the driver's ends as above;
  - line 403 ("$25 cap per full run") and lines 478 to 479 ("$25 per
    full emulator run ... about 14 runs") say spend is metered and
    reported only, and stops nothing.
- `tests/README.md`, `docs/architecture.md`, `docs/harnesses.md`,
  `docs/tech-stack.md`, and the `core/settings.py` docstring that names
  `scripts/demo_workspace.sh`.

## What builds now and what waits

| Part | When |
|---|---|
| The move, the package, imports, `demo_workspace.sh` removal | now |
| Metering through the gateway, the emulator task, costs.jsonl removal | now |
| Pinned stand-in and judge models, the judge id probe | now |
| Mirror reads, the judge diff's exclusions, the hidden-test tree | now |
| The `/tmp` profile change and its tests | now |
| Driver ends, `--run`, result fields | now |
| Rebase over 1.4b's edits to `replay.py` and `replay_workspace.py` (the item's `project` key) | after 1.4b merges |
| The equal-counts check under both profiles | after 1.4b (check environments) |
| The gate runs | after 1.4b, 1.4d, and 1.4c's review runner merge |
| Merge of this branch | after 1.4d, per the fan-out table |

1.4c's review runner (a host rerun on 1.4b's machinery) merges before the
gate runs, so review has a runner at gate time. Any stage still in
`MANUAL_STAGES` without a runner then is handled by the driver's
`NO RUNNER` exit above.

1.4b edits `scripts/replay.py` and `scripts/replay_workspace.py`. This
branch moves them with `git mv` in a commit of its own containing no
other change, so the rebase carries 1.4b's edits by rename detection;
any hunk it does not carry is applied by hand and named in the build
notes.

## Tech debt absorbed

| Debt | Where |
|---|---|
| `/tmp` shared between runs | `core/workspace.py`, the working turn profile |
| Stand-in and judge spend outside the ledger (`costs.jsonl`) | `tests/emulator/common.py` |
| The judge's unrecorded model id | `tests/emulator/judge.py` (`JUDGE_MODEL`) |
| Reads of the turn's workdir by the driver, and a hidden-test tree a turn can write | `tests/emulator/stand_in.py`, `judge.py` |
| `sys.path` imports between the scripts | the package |
| `scripts/demo_workspace.sh` kept beside kernel provisioning | deleted |
| The #191 trial's held merge never judged | scored once, as information (rollout step 7) |
| Answer keys for cuttlefish #646, popoto #191, popoto #188 unconfirmed | a question for Tom below; inferred lines listed in a sidecar |

## Left out

- The `routed` arm's full sweep over every item (milestone 4).
- Bare and clarify reruns of the baseline under the new stand-in. The
  bars stay the baseline's; the gate record notes the stand-in changed.
- Any spend limit, run count, or stop on cost.
- Metering a check verdict recorded by hand: it stays outside the ledger,
  and the gate record says which were.
- The build's carried session (about 70k input tokens per build turn,
  trial finding 2). Reported in the gate record, not changed here.
- Releasing a held merge, locally or to GitHub. 1.4d's one live push is
  its own.

## Tests

All under `tests/`, collected by pytest, each marked
`@pytest.mark.spend(usd=0)`. None makes a model call; the gateway is
pointed at a local fake provider as `tests/test_gateway_meter.py` does.

- `test_emulator_package.py` (new): every module imports as
  `tests.emulator.*`; `python -m tests.emulator.replay --help` exits 0;
  no file under `tests/emulator/` matches `test_*.py`; `scripts/` holds
  none of the five names.
- `test_replay.py`, `test_replay_arms.py`: imports from the package;
  existing cases unchanged otherwise.
- `test_emulator_metering.py` (new):
  - a stand-in call and a judge call through a gateway on a fake provider
    write one `gateway.opened` and one `gateway.charged` row each on the
    emulator task, and `tasks.spending` over its rows equals the result's
    `emulator_spend_usd`;
  - the child sees `CLAUDE_CODE_OAUTH_TOKEN=TURN_TOKEN`, a
    `CLAUDE_CONFIG_DIR` that is empty and inside the run directory, and
    none of the driver's own `CLAUDE*`, `ANTHROPIC*`, `PG*`, `VALOR_PG*`
    variables (the driver's environment is seeded with a fake
    `ANTHROPIC_API_KEY` and a `PGPASSFILE` to prove both are dropped);
  - the gateway runs on its background thread while the driver calls
    synchronously; the token is refused after the call returns and after
    the call raises (retired from the driver's thread);
  - no `costs.jsonl` exists after a run.
- The emulator task: it folds as calibration; `runs`, `session`,
  `guards`, `verdicts`, and `stop` refuse it; its `task.started` carries
  the run, the item task, the emulator instruction, and the driver's
  `via`; an item task's spending is unchanged by stand-in and judge calls.
- Models: the stand-in's command names `claude-opus-5-5`; `JUDGE_MODEL` is
  a full id and never an alias (`sonnet`, `opus`, `haiku`, or one ending
  `-latest` fails).
- Mirror reads: with a held merge in a scripted task, the rev read is the
  `effect.held` row's `head_sha`; with a candidate only, the candidate.
  Non-obvious: the turn's workdir is moved away before the call, and the
  call still succeeds and returns the same diff; a workdir commit newer
  than the candidate does not appear in it. A second, refused merge
  effect does not change which `effect.held` row is read.
- Judge diff: a candidate carrying `docs/plans/x.md` yields a diff without
  it and a result naming it as left out; a diff over 70,000 characters is
  recorded as truncated with its full size.
- Hidden-test tree: it is created under `<task_dir>/checks/verify-<run>/`;
  a write to it under the working turn profile (without the added path)
  is refused; the verification profile allows it.
- Driver ends, each with a scripted task:
  - a failed run leaves the outcome unset; the next invocation resumes
    the same task and the same emulator task;
  - `NO RUNNER` for review leaves the outcome unset and names the stage;
    after a `verdict` row the next invocation continues;
  - a task in `merge` with an ungranted governance instance leaves the
    outcome unset and the stand-in is not called;
  - a held merge with the stand-in accepting ends `held`; one with its
    feedback rounds spent ends `held` with the count; neither answers the
    merge;
  - a stopped task ends `stopped`;
  - `--run` names the result file; a name with an outcome is refused
    without `--rebuild`.
- `/tmp`: the working turn profile denies a write to `/private/tmp/x` and
  under `/private/var/folders`, and allows one under the turn's `TMPDIR`.
  Non-obvious: two turns of different tasks writing the same `/tmp` name
  cannot see each other's file, because neither can write it.
- Non-obvious, run once on this machine after 1.4b and recorded, not in
  the suite: for each gate item, its `verify` commands on base plus
  `ref/<item>.ref.diff`, from the new tree location, under the old
  working profile and the new one, give equal hidden-test counts. This is
  what shows the `/tmp` change does not move a gate number.

## Files it changes

Other tasks also change `core/`; the edits here are small and named.

| File | Change |
|---|---|
| `scripts/replay.py` | moved to `tests/emulator/replay.py`, then edited |
| `scripts/replay_common.py` | moved to `tests/emulator/common.py`, then edited |
| `scripts/replay_workspace.py` | moved to `tests/emulator/workspace.py` |
| `scripts/role_play_tom.py` | moved to `tests/emulator/stand_in.py`, then edited |
| `scripts/judge_replay.py` | moved to `tests/emulator/judge.py`, then edited |
| `scripts/demo_workspace.sh` | deleted |
| `tests/emulator/__init__.py` | new |
| `tests/test_emulator_package.py` | new |
| `tests/test_emulator_metering.py` | new |
| `tests/test_replay.py`, `tests/test_replay_arms.py` | imports, the `--help` path, the driver cases |
| `tests/test_demo_sandbox.py` | the `demo_workspace.sh` cases removed; `/tmp` and hidden-test tree cases added |
| `tests/scripted.py`, `tests/test_pipeline.py` | docstring paths |
| `tests/README.md` | the emulator's place |
| `core/tasks.py` | `start_calibration` takes optional `via` and `detail` |
| `core/workspace.py` | the working turn profile denies the temp directories |
| `core/settings.py` | docstring only |
| `docs/plans/valor-rebuild.md` | the gate line's spend sentence |
| `docs/emulator.md`, `docs/architecture.md`, `docs/harnesses.md`, `docs/tech-stack.md` | as in Docs fixed |
| `docs/plans/m1-5-emulator.md` | the gate record, after the runs |
| a sidecar `~/src/valor-demo/items/<item>.key-inferred.md` per key | outside the repository: the inferred lines of each key, listed; the `*.key.md` files stay byte-identical |

## Rollout

1. Build the parts marked "now" on this branch, the `git mv` commit
   first. Suite green.
2. Resolve the judge's Sonnet id once and commit it in `JUDGE_MODEL`.
3. When 1.4b merges, rebase; confirm the `project` key handling survived
   the rename and the items carry their `project` key. Run the
   equal-counts check under both profiles and record it.
4. When 1.4d merges, rebase again. Suite green.
5. When 1.4c's review runner merges, rebase. Suite green.
6. Run the gate: `python -m tests.emulator.replay ITEM.json --arm routed
   --run ITEM-gate` for pop-b, pso-a, pop-a, one at a time. On a
   `NO RUNNER` exit a fresh Opus subagent records the named stage's
   verdict and the driver is invoked again. Judge each held run. Rerun
   once an item with no passing run, as `ITEM-gate-2`.
7. Score the #191 trial's held merge (task `75c0902b6e25`, candidate
   `aeb94f19`, head `1cfb6000`) once with the same judge and hidden
   tests, as information beside the gate, not as a gate run.
8. Write the gate record into this file: per item, scores, hidden tests,
   attention, both spends, task ids, held merge ids, judge diff sizes and
   truncation; the judge id; the stand-in change; which verdicts were
   recorded by hand and so unmetered; the baseline's n = 1 caveat; the
   trial's scores from step 7.
9. Review rounds, then the merge held for Tom's tap.

## Decided by default

- The package name `tests/emulator/` and the short module names
  (`common`, `workspace`, `stand_in`, `judge`).
- `scripts/demo_workspace.sh` is deleted, not moved.
- The emulator task reuses the calibration task (`calibration:
  "emulator"`) with its own `via` and instruction, rather than a new task
  kind, so `machine.fold` and every refusal are unchanged.
- One emulator task per run, not per item or per gate.
- The driver runs its own gateway on an ephemeral port on a background
  event-loop thread, rather than a long-lived one.
- Stand-in and judge stay `claude -p` calls, not direct API calls.
- The stand-in is the `frontier` seat; the judge is one pinned Sonnet id
  resolved from the baseline's alias.
- `head_sha` is read from the `effect.held` row, not added to the fold.
- The judge's diff leaves out `docs/plans/`; its size and any truncation
  are recorded.
- The hidden-test tree lives at `<task_dir>/checks/verify-<run>/`.
- `MAX_RUNS` and `MAX_FAILED_RUNS` are removed; a failed run, a
  `NO RUNNER` stage, and an awaited grant each exit for a resume.
- The stand-in's two feedback rounds are kept; spending them at a held
  merge still ends `held`.
- Gate runs use the `routed` arm and are named `<item>-gate`.
- An item passes if either run meets both bars; both are recorded.
- The gate ends at a held merge, scored and never released, as the
  milestone states.
- Inferred key lines are listed in a sidecar; the keys stay
  byte-identical.
- Hidden verification keeps the baseline's commands, profile, and
  services; only its source tree moves.
- The working turn profile denies the temp directories outright rather
  than per-task subdirectories of them.
- The #191 trial's held merge is scored once as information.
- The carried session cost is reported, not changed.

## Questions for Tom

1. **The answer keys for cuttlefish #646, popoto #191, and popoto #188.**
   Do they say what you meant? #191 is a gate item; its key infers the
   push order (LPUSH), eager load, and that save replaces the list.
   Assumed: the keys stand as written, the inferred lines are listed in a
   sidecar, and the gate runs on them.

## Record

Critique round 1 (of 2): revise. Every finding accepted.

1. Driver ends: `held` only on `merge_effect.state == "held"`, accepted or
   rounds spent; `NO RUNNER` and an awaited grant exit unset with the
   stage or reason named; stopped and to-Tom outcomes; every manual stage
   named; `--run` in the design. 1.4c's review runner merges before the
   gate.
2. `head_sha` read from the `effect.held` row; no `core/` change.
3. The tree at `<task_dir>/checks/verify-<run>/`, kernel-written, added
   to the verification profile only.
4. The equal-counts check: `verify` on base plus the ref diff, both
   profiles, on this machine.
5. Spend read from `python -m core status` and `tasks.spending` over rows.
6. A sidecar for inferred lines; keys byte-identical; path
   `items/*.key.md`.
7. valor-rebuild.md's spend line and docs/emulator.md lines 15, 156, 403,
   426 to 428, and 478 to 479 fixed in the build.
8. The unmet gate goes to Tom as a delivery not passed; either run meeting
   both bars passes; #191's F at least 2 stated.
9. Q1 kept; Q2 moved to Decided by default.
10. `start_calibration` takes `via` and `detail`.
11. `DROP_ENV` reused, `PGPASSFILE` seeded in the test; the gateway on a
    background event-loop thread.
12. `tests/test_gateway_meter.py`; `@pytest.mark.spend(usd=0)`.
13. The judge's diff leaves out `docs/plans/`; size and truncation
    recorded.
14. Hand-recorded verdicts named as unmetered; the #191 trial's held
    merge scored once as information.
