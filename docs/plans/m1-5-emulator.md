---
tracking: none
slug: m1-5-emulator
type: build
status: delivered-not-passed
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
event loop in a background thread that lives for the invocation. `issue`
and `retire` are plain methods, so the driver calls them on that loop by
wrapping each in a small coroutine handed to
`asyncio.run_coroutine_threadsafe`; `drain` is a coroutine and goes the
same way. The thread closes the gateway on exit. `claude_json` becomes:

1. `url = issue(emulator_task, call_id)`, `call_id` a fresh id naming
   the role and round (`stand-in.answer.2`, `judge`).
2. `claude -p` with `ANTHROPIC_BASE_URL=url`,
   `CLAUDE_CODE_OAUTH_TOKEN=TURN_TOKEN`, `CLAUDE_CONFIG_DIR` a fresh empty
   directory under the run's directory, every variable starting with a
   prefix in `harnesses.claude_code.DROP_ENV` (`CLAUDE`, `ANTHROPIC`,
   `PG`, `VALOR_PG`) and `AI_AGENT` dropped, the same safe-mode,
   tool-less, no-persistence flags as now, and `--model` the pinned id.
3. `retire(emulator_task)` in a `finally`.

Before any spend is read, and before the gateway closes, the driver runs
`drain(emulator_task)`, since a call's `gateway.charged` row is written
after its response ends and can land after the child exits. The result
records the task's `open_calls`, expected empty.

The call stays a Claude Code call, not a direct API call, because the
login credential is proven only through Claude Code. `costs.jsonl`, its
writer, and its readers go. The result's spend comes from the ledger:
`tasks.spending` over each task's rows gives `emulator_spend_usd` and
`kernel_spend_usd`. A separate task keeps the item task's spending
comparable to the baseline's kernel column, and lets a stopped item task
still be judged.

### Models (`tests/emulator/stand_in.py`, `tests/emulator/judge.py`)

- The stand-in uses `settings.SEATS["frontier"]` (`claude-opus-5-5`). The
  driver's `--stand-in-model` flag stays for a deliberate comparison run,
  its default the seat. The driver's `--model` is the working turn's model
  and is unchanged.
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
the candidate from `tasks.status`. `core/` gets no change for this. Diffs
are taken with `git.trusted` in the task's kernel mirror, a repository no
turn writes, between the base and that rev.

The judge's diff is the whole diff, as the baseline judge's was
(`5d90b4776:scripts/judge_replay.py`); no path the turn chose is read.
Each result records the diff's size in characters and lines and whether
the judge's 70,000 character truncation cut it. The pipeline's plan and
docs commits stay in the diff; the gate record notes that the baseline's
candidate diffs had none.

### The hidden-test tree and its profile (`tests/emulator/judge.py`)

The tree lives at `<task_dir>/checks/verify-<run>/`, made fresh by the
driver before each verification and filled by `git archive` of the rev
from the kernel mirror, as the baseline's export was. `checks/` is written
only by the kernel and no turn profile lists it, so no turn can write the
tree.

Verification runs under the baseline's profile: the working turn profile
as the baseline ran it, with the temp directories shared, plus that one
directory read-write and its `TMPDIR` set inside it, with the task's
services started. `core/workspace.py`'s `turn_profile` takes `tmp`
(whether the temp roots are shared; default not) and `rw` (paths added
read-write) for this. The commands, services, timeout, and profile are the
baseline's, so pop-a's and pso-a's second commands, which write
`/tmp/*_$$.txt`, run as they did; only the source tree moves, from the
turn's `state/work/tmp/verify` to this directory.

### The driver (`tests/emulator/replay.py`)

- `--run NAME` names the run; the default stays `{item}-{arm}`. A name
  whose result already has an outcome is refused unless `--rebuild` is
  given. Gate runs are named `<item>-gate` and `<item>-gate-2`, so
  `pop-b-routed.json` stays as it is.
- `MAX_RUNS` goes. The pipeline's own loop endings end a task.
- `MAX_FAILED_RUNS` goes. After each `core run` the driver reads its
  answer's first word: `QUESTION`, `DELIVERED`, `STOPPED`, `MERGED` go on;
  `ALREADY RUNNING` blocks on the task's run lock (`pg_advisory_lock` on
  `run:<task>`), lets it go, and goes on; `FAILED`, `IDLE`, `LOCK LOST`,
  `LEGACY` and any other answer exit with the outcome unset and the answer
  as the reason. The next invocation resumes, as an unset outcome does.
- `release_pushes` approves and releases a held `push_branch` when the push
  URL in the kernel's record (`core workspace show`) is the bare origin the
  kernel provisioned in the task's directory; it never reads the turn's
  git config. It skips every `merge` effect of the task: the
  current one and every earlier one a later candidate superseded (nothing
  withdraws them). The exit "an effect other than a local push is held
  for Tom" fires only for a held effect that is neither.
- `NO RUNNER` exits the driver with the outcome unset and the stage named.
  This covers every stage left in `verdicts.MANUAL_STAGES` with no runner
  registered at gate time. A fresh Opus subagent records the verdict
  through `python -m core verdict`, reading a `blind_checkout` from the
  kernel mirror at the candidate (test, review) or at the docs head
  (docs), never the turn's workdir. The next invocation resumes the same
  task.
- A task in `merge` is read from `tasks.status`, in this order:
  - `delivery.outcome == "did_not_pass"`: the run ends `outcome: to tom`;
  - a governance instance not granted, or `join.outcome ==
    "governance_refused"`: the driver exits with the outcome unset,
    "awaiting a grant", and resumes after Tom's tap;
  - `merge_effect.state == "refused"`: the run ends `outcome: to tom`,
    since no tap re-requests the same payload unless something is granted;
  - `merge_effect.state == "held"`: the stand-in reviews. Accept, or its
    two feedback rounds spent, ends the run with `outcome: held`, the
    feedback count recorded. Feedback inside the two rounds is recorded
    and sends the task to patch.
  - otherwise (no merge effect yet): the next `core run`.
- A stopped task ends the run with `outcome: stopped`.
- The result gains `emulator_task`, `emulator_spend_usd`,
  `kernel_spend_usd`, `open_calls`, `judge_model`, `stand_in_model`,
  `attention` (counts by kind), `final_rev`, `merge_effect_id`, and the
  judge diff's size and truncation.

### `/tmp` (`core/workspace.py`)

The working turn profile denies `/private/tmp`, `/private/var/tmp`, and
`/private/var/folders` as the fresh-session profile does. `TMPDIR` is
already per turn, but macOS `mktemp` and `/usr/bin`'s `git` and `python3`
shims use the user temp directory whatever it says. The kernel writes a
`mktemp` into the shared `bin/` that hands it `-p "$TMPDIR"` when the
caller names no directory and no template, and puts the trusted git's
directory after `bin/` on the turn's `PATH`. Services keep their sockets
under the task directory.

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
| The equal-output check at both tree locations | after 1.4b (check environments) |
| The gate runs | after 1.4b, 1.4s, 1.4c part one (the review runner), and 1.4d merge |
| Merge of this branch | after 1.4d, per the fan-out table |

1.4c part one (the review runner, a host rerun on 1.4b's machinery)
registers review unconditionally and merges before the gate runs, so
review has a runner at gate time. Any stage still in `MANUAL_STAGES`
without a runner then (docs, while governance's entry check fails) is
handled by the driver's `NO RUNNER` exit, and the gate record names it as
hand-played. `python -m core verdict` stays while any check lacks a
runner, so that exit is usable exactly when it is needed.

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
  - a fake provider that delays the end of its stream after the body is
    sent: after `drain`, the spend read equals the charge and
    `open_calls` is empty;
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
- Judge diff: a task whose plan names `app.py` still shows its change to
  `app.py`; a diff over 70,000 characters is recorded as truncated with its
  full size.
- Hidden-test tree: it is created under `<task_dir>/checks/verify-<run>/`
  from the mirror; a write to it under the working turn profile (without
  the added path) is refused; the verification profile allows it and a
  write to `/tmp`, as the baseline's did.
- Driver ends, each with a scripted task:
  - a failed run leaves the outcome unset; the next invocation resumes
    the same task and the same emulator task;
  - `NO RUNNER` for review leaves the outcome unset and names the stage;
    after a `verdict` row the next invocation continues;
  - a task in `merge` with an ungranted governance instance, and one
    whose join is `governance_refused`, leave the outcome unset and the
    stand-in is not called;
  - a task whose delivery did not pass ends `to tom`; one whose merge
    effect was refused ends `to tom`;
  - a held merge with the stand-in accepting ends `held`; one with its
    feedback rounds spent ends `held` with the count; neither answers the
    merge;
  - a held merge does not trip the left-effect exit; after feedback and a
    second held merge, the first still does not, and the rev is read from
    the second; a held effect of another action does trip it;
  - a stopped task ends `stopped`;
  - with the real router and its answer line: `IDLE`, `LOCK LOST` and
    `LEGACY` exit with the answer as the reason; `ALREADY RUNNING` waits
    until the holder lets the lock go, then returns with nothing set;
  - a push is released only when the record's URL is the kernel's origin;
  - `--run` names the result file; a name with an outcome is refused
    without `--rebuild`.
- `/tmp`: the working turn profile denies a write to `/private/tmp/x` and
  under `/private/var/folders`, and allows one under the turn's `TMPDIR`.
  Non-obvious: two turns of different tasks writing the same `/tmp` name
  cannot see each other's file, because neither can write it. Under the
  profile with the turn's `PATH`, `mktemp` (bare, `-d`, `-t`) makes its
  file in `TMPDIR`, and `git` and `python3` print nothing on stderr.
- Non-obvious, run once on this machine after 1.4b and recorded, not in
  the suite: for each gate item, its `verify` commands on base plus
  `ref/<item>.ref.diff`, under the baseline's profile, from the baseline's
  tree location and from the new one, give the same whole output (the
  hidden-test counts and the broad suite's base-failure comparison, with
  paths, timings, and process ids normalised). This is what shows the
  move of the tree does not move a gate number.

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
| `tests/test_demo_sandbox.py` | the `demo_workspace.sh` cases removed; `/tmp`, `mktemp`, `git`, and hidden-test tree cases added |
| `tests/scripted.py`, `tests/test_pipeline.py` | docstring paths |
| `tests/README.md` | the emulator's place |
| `core/tasks.py` | `start_calibration` takes optional `via` and `detail` |
| `core/workspace.py` | the working turn profile denies the temp directories; `turn_profile` takes `tmp` and `rw` for verification; `bin/mktemp` and the trusted git's directory on the turn's `PATH` |
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
   equal-output check and record it.
4. When 1.4d merges, rebase again. Suite green.
5. When 1.4s and 1.4c part one merge, rebase. Suite green.
6. Run the gate: `python -m tests.emulator.replay ITEM.json --arm routed
   --run ITEM-gate` for pop-b, pso-a, pop-a, one at a time. On a
   `NO RUNNER` exit a fresh Opus subagent records the named stage's
   verdict and the driver is invoked again. Judge each held run. Rerun
   once an item with no passing run, as `ITEM-gate-2`.
7. Score the #191 trial's held merge (task `75c0902b6e25`, candidate
   `aeb94f19`, head `1cfb6000`) once with the same judge and hidden
   tests, as information beside the gate, not as a gate run. It starts an
   emulator task of its own and writes a new result,
   `pop-b-trial-scored.json`, leaving `pop-b-routed.json` as it is.
8. Write the gate record into this file: per item, scores, hidden tests,
   attention, both spends, task ids, held merge ids, judge diff sizes and
   truncation; the judge id; the stand-in change; which verdicts were
   recorded by hand and so unmetered; the baseline's n = 1 caveat; the
   trial's scores from step 7.
9. Review rounds, then the merge held for Tom's tap.

## Decided by default

The choices made without asking Tom are in
[m1-5-emulator-decided.md](m1-5-emulator-decided.md).

## Questions for Tom

1. **The answer keys for cuttlefish #646, popoto #191, and popoto #188.**
   Do they say what you meant? #191 is a gate item; its key infers the
   push order (LPUSH), eager load, and that save replaces the list.
   Assumed: the keys stand as written, the inferred lines are listed in a
   sidecar, and the gate runs on them.

## Record

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
