---
tracking: none
slug: m1-5-emulator
type: build
status: planned
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

Spending is metered and reported, never capped. The gate stops on
nothing but its own Done items: no run, item, or call has a spend limit,
and the "$25 per-run cap" in valor-rebuild.md's gate line is not a
condition here. Each run's spend is written into its result and into the
gate record as information.

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
| Stand-in and judge calls go through the gateway and meter onto one task's spending | a run's result names its emulator task; that task's `gateway.opened` and `gateway.charged` rows carry `route: gateway`, one pair per stand-in or judge call; `python -m core spending <emulator task>` matches the result's `emulator_spend_usd`; no `costs.jsonl` is written anywhere |
| The stand-in is Opus-class | every stand-in call's `gateway.charged` row names `claude-opus-5-5` (the `frontier` seat); every `role_played` row's `by` names it |
| The judge is the baseline's Sonnet judge, pinned | every judge call's charged row names one Sonnet id, recorded in the result's `judge_model` |
| The gate: three items reach a held merge | for each of pop-b (#191), pso-a (#872), pop-a (#633): a run whose result has `outcome: held`, a `merge` attention row unanswered, the candidate and merge payload head from `tasks.status`, hidden tests and fidelity scored; the gate record in this file lists them against the bars below |
| `/tmp` is no longer shared between runs | the working turn profile denies `/private/tmp`, `/private/var/tmp`, `/private/var/folders`; the profile test passes; a turn writing `/tmp/x` is refused while its `TMPDIR` write succeeds |

### The gate's bars

From rebuild-baseline.md, the judge's 0 to 5 scores (fidelity,
correctness, simplicity) and hidden tests passed:

| Item | Bar | Fidelity at least | Hidden tests at least |
|---|---|---|---|
| pso-a (#872) | bare | 3 (bare 4) | 20 of 22 |
| pop-a (#633) | bare | 4 (bare 5) | 14 of 14 |
| pop-b (#191) | clarify run | 2 (clarify 3) | 7 of 11 |

"Within one point" means at most one below. Each item may be rerun once;
the better of the two runs counts and both are recorded. The attention
each item took (questions asked, feedback rounds, the held merge) is
counted from its `role_played` and attention rows and written beside its
scores. Spend per run (the item task's kernel spend and the emulator
task's stand-in and judge spend) is written beside them too.

## Threat model

- The turn controls its clone, its commits, its delivery note, and the
  diff text. The stand-in and judge read that text, so it can carry prompt
  injection into their calls. Both run tool-less (`--tools ""`, safe mode,
  no MCP), with a fresh empty Claude Code config and only the gateway's
  placeholder token, so an injected instruction can change a score or an
  answer, nothing else. The hidden tests are run by the driver, not judged,
  so the gate's test numbers do not depend on the judge.
- The driver never runs git in a turn-owned repository outside the
  sandbox. The stand-in's delivery context and the judge's export read the
  kernel mirror at the candidate or merge payload head, never the turn's
  workdir.
- No stand-in or judge process holds a real credential: the gateway
  replaces the placeholder with the kernel's login on the way out, and
  retires the token after each call.
- The driver never releases a held merge. The gate's evidence is the held
  merge.

## Design

### The package (`tests/emulator/`)

`git mv` moves the five scripts into `tests/emulator/` with an
`__init__.py`, keeping their names:

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

One emulator task per run. `tasks.start_calibration(conn, "emulator")`
already writes a task that folds as calibration, carries no `sdlc`
marker, is read-only, and is refused by runs, session, guards, verdicts,
and stop: exactly a task that only meters. The driver starts one at the
run's first invocation, writes its id into the result file, and reuses it
on resume. `start_calibration` gains an optional `detail` dict merged into
the `task.started` payload, so the row also carries
`{"emulator": {"run": RUN, "item_task": TASK}}`; nothing else in `core/`
changes for it.

The driver opens its own `Gateway(dsn, credential=ClaudeLogin())` on an
ephemeral loopback port for the life of the invocation. `claude_json`
becomes:

1. `url = gateway.issue(emulator_task, call_id)`, `call_id` a fresh id
   naming the role and round (`stand-in.answer.2`, `judge`).
2. `claude -p` with `ANTHROPIC_BASE_URL=url`,
   `CLAUDE_CODE_OAUTH_TOKEN=TURN_TOKEN`, `CLAUDE_CONFIG_DIR` a fresh empty
   directory under the run's directory, every other `CLAUDE*` and
   `ANTHROPIC*` variable dropped, the same safe-mode, tool-less,
   no-persistence flags as now, and `--model` the pinned id.
3. `gateway.retire(emulator_task)` in a `finally`.

The call stays a Claude Code call, not a direct API call, because the
login credential is proven only through Claude Code. `costs.jsonl`, its
writer, and its readers go. The result's spend comes from the ledger:
`emulator_spend_usd` is `tasks.spending(emulator_task)`, `kernel_spend_usd`
is `tasks.spending(item_task)`. A separate task keeps the item task's
spending comparable to the baseline's kernel column, and lets a stopped
item task still be judged.

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

`delivery_context` and `export_final` read the kernel mirror: the rev is
the merge payload head when a merge is held, else the candidate, both from
`tasks.status`. `workspace.blind_checkout(mirror, base, rev, dest)` writes
the tree into the run's directory, and the diff is taken there. Hidden
verification keeps the baseline's commands, sandbox profile, and services
so the counts compare; only its source tree changes, from the turn's
workdir to the mirror checkout. `ws_git` stays for the driver's own
checkouts and keeps its pinned hooks and fsmonitor settings.

### The driver (`tests/emulator/replay.py`)

- `MAX_RUNS` goes. The pipeline's own loop endings end a task; the driver
  runs runs until the task is held, stopped, or done.
- `MAX_FAILED_RUNS` goes. A failed run exits the driver with the outcome
  unset and the failure printed; the next invocation resumes, as an
  unset outcome already does.
- `release_pushes` keeps approving `push_branch` to the local origin, and
  never answers a merge. At a held merge the stand-in reviews: accept ends
  the run with `outcome: held`; feedback is recorded and sends the task to
  patch. The baseline's two feedback rounds stay, for comparability.
- The result gains `emulator_task`, `emulator_spend_usd`,
  `kernel_spend_usd`, `judge_model`, `stand_in_model`, `attention`
  (counts by kind), and `final_rev`.
- A run name already in `results/` with an outcome is refused unless
  `--rebuild` is given; gate runs are named `<item>-gate` and
  `<item>-gate-2`, so `pop-b-routed.json` stays as it is.

### `/tmp` (`core/workspace.py`)

The working turn profile denies `/private/tmp`, `/private/var/tmp`, and
`/private/var/folders` as the fresh-session profile does. `TMPDIR` is
already per turn. The check environment's services keep their own
sockets under the task directory, so nothing the suite needs lives in
`/tmp`.

### Docs fixed in the same build

`docs/emulator.md` (paths, module invocation, the metering section, the
spend lines say metered and reported with no cap), `tests/README.md`,
`docs/architecture.md`, `docs/harnesses.md`, `docs/tech-stack.md`, and the
`core/settings.py` docstring that names `scripts/demo_workspace.sh`.

## What builds now and what waits

| Part | When |
|---|---|
| The move, the package, imports, `demo_workspace.sh` removal | now |
| Metering through the gateway, the emulator task, costs.jsonl removal | now |
| Pinned stand-in and judge models, the judge id probe | now |
| Mirror reads for the stand-in and judge | now |
| The `/tmp` profile change and its tests | now |
| Driver ends, result fields, gate run names | now |
| Rebase over 1.4b's edits to `replay.py` and `replay_workspace.py` (the item's `project` key) | after 1.4b merges |
| The gate runs | after 1.4b (test and docs runners, check environments, the `project` key on the items) and 1.4d (performers, the GitHub credential, transcripts) merge |
| Merge of this branch | after 1.4d, per the fan-out table |

Until 1.4c lands the review runner, the gate's review check is played by
a fresh Opus subagent through `python -m core verdict`, as in the #191
trial. The verdict row records it as role-played.

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
| Reads of the turn's workdir by the driver | `tests/emulator/stand_in.py`, `judge.py` |
| `sys.path` imports between the scripts | the package |
| `scripts/demo_workspace.sh` kept beside kernel provisioning | deleted |
| Answer keys for cuttlefish #646, popoto #191, popoto #188 unconfirmed | a question for Tom below; the build marks the inferred lines in each key |

## Left out

- The `routed` arm's full sweep over every item (milestone 4).
- Bare and clarify reruns of the baseline under the new stand-in. The
  bars stay the baseline's; the gate record notes the stand-in changed.
- Any spend limit, run count, or stop on cost.
- The build's carried session (about 70k input tokens per build turn,
  trial finding 2). Reported in the gate record, not changed here.
- Releasing a held merge to GitHub. 1.4d's one live push is its own.
- The review runner (1.4c).

## Tests

All under `tests/`, collected by pytest. None makes a model call; the
gateway is pointed at a local fake provider as `tests/test_gateway.py`
already does.

- `test_emulator_package.py` (new): every module imports as
  `tests.emulator.*`; `python -m tests.emulator.replay --help` exits 0;
  no file under `tests/emulator/` matches `test_*.py`; `scripts/` holds
  none of the five names.
- `test_replay.py`, `test_replay_arms.py`: imports from the package;
  existing cases unchanged otherwise.
- `test_emulator_metering.py` (new):
  - a stand-in call and a judge call through a gateway on a fake provider
    write one `gateway.opened` and one `gateway.charged` row each on the
    emulator task, and `tasks.spending` equals the result's
    `emulator_spend_usd`;
  - the child process sees `CLAUDE_CODE_OAUTH_TOKEN=TURN_TOKEN`, a
    `CLAUDE_CONFIG_DIR` that is empty and inside the run directory, and no
    other `CLAUDE*` or `ANTHROPIC*` variable from the driver's
    environment (the driver's environment is seeded with a fake
    `ANTHROPIC_API_KEY` to prove it is dropped);
  - the token is refused after the call returns (retired), and after the
    call raises;
  - no `costs.jsonl` exists after a run.
- The emulator task: it folds as calibration; `runs`, `session`,
  `guards`, `verdicts`, and `stop` refuse it; its `task.started` carries
  the run and item task; an item task's spending is unchanged by stand-in
  and judge calls.
- Models: the stand-in's command names `claude-opus-5-5`; the judge's
  names `JUDGE_MODEL`, which is a full id and never an alias (`sonnet`,
  `opus`, `haiku`, or one ending `-latest` fails).
- Mirror reads: with a held merge in a scripted task, `delivery_context`
  and `export_final` read the merge payload head from the mirror; with a
  candidate only, the candidate. Non-obvious: the turn's workdir is moved
  away before the call, and the call still succeeds and returns the same
  diff; a workdir commit newer than the candidate does not appear in it.
- Driver ends: a scripted failed run leaves the outcome unset and the
  next invocation resumes the same task and the same emulator task; a
  held merge with the stand-in accepting ends with `outcome: held` and no
  merge answered; a run name with an outcome is refused without
  `--rebuild`.
- `/tmp`: the working turn profile denies a write to `/private/tmp/x` and
  `/private/var/folders/...` and allows one under the turn's `TMPDIR`.
  Non-obvious: two turns of different tasks writing the same `/tmp` name
  cannot see each other's file, because neither can write it.
- Non-obvious, run once at build and recorded, not in the suite: each
  gate item's base suite under the new working profile shows no failure
  absent from `ref/<item>.base-failures.txt`, so the `/tmp` change does not
  move a hidden-test count.

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
| `tests/test_replay.py`, `tests/test_replay_arms.py` | imports, the `--help` path, new driver cases |
| `tests/test_demo_sandbox.py` | the `demo_workspace.sh` cases removed; `/tmp` cases added |
| `tests/scripted.py`, `tests/test_pipeline.py` | docstring paths |
| `tests/README.md` | the emulator's place |
| `core/tasks.py` | `start_calibration` takes an optional `detail` |
| `core/workspace.py` | the working turn profile denies the temp directories |
| `core/settings.py` | docstring only |
| `docs/emulator.md`, `docs/architecture.md`, `docs/harnesses.md`, `docs/tech-stack.md` | paths, metering, spend reported with no cap |
| `docs/plans/m1-5-emulator.md` | the gate record, after the runs |
| `~/src/valor-demo/items/*.json`, `keys/*` | outside the repository: inferred key lines marked |

## Rollout

1. Build the parts marked "now" on this branch, the `git mv` commit
   first. Suite green.
2. Resolve the judge's Sonnet id once and commit it in `JUDGE_MODEL`.
3. When 1.4b merges, rebase; confirm the `project` key handling survived
   the rename and the items carry their `project` key.
4. When 1.4d merges, rebase again. Suite green.
5. Run the base-suite check of each gate item under the new profile and
   record it.
6. Run the gate: `python -m tests.emulator.replay ITEM.json --arm routed
   --run ITEM-gate` for pop-b, pso-a, pop-a, one at a time, review played
   through `verdict` by a fresh Opus subagent. Judge each held run. Rerun
   once any item below its bar, as `ITEM-gate-2`.
7. Write the gate record into this file: per item, scores, hidden tests,
   attention, both spends, task ids, held merge ids; the judge id; the
   stand-in change; the baseline's n = 1 caveat.
8. Review rounds, then the merge held for Tom's tap.

If an item stays below its bar after its rerun, the gate record says so
with both runs and the build stops there for Tom; nothing is retried
past the one rerun.

## Decided by default

- The package name `tests/emulator/` and the short module names
  (`common`, `workspace`, `stand_in`, `judge`).
- `scripts/demo_workspace.sh` is deleted, not moved.
- The emulator task reuses the calibration task (`calibration:
  "emulator"`) rather than a new task kind, so `machine.fold` and every
  refusal are unchanged.
- One emulator task per run, not per item or per gate.
- The driver runs its own gateway on an ephemeral port rather than a
  long-lived one.
- Stand-in and judge stay `claude -p` calls, not direct API calls.
- The stand-in is the `frontier` seat; the judge is one pinned Sonnet id
  resolved from the baseline's alias.
- `MAX_RUNS` and `MAX_FAILED_RUNS` are removed; a failed run exits for a
  resume.
- The stand-in's two feedback rounds are kept.
- Gate runs use the `routed` arm and are named `<item>-gate`.
- The better of an item's two runs counts; both are recorded.
- Hidden verification keeps the baseline's commands, profile, and
  services; only its source tree moves to the mirror checkout.
- The working turn profile denies the temp directories outright rather
  than per-task subdirectories of them.
- The carried session cost is reported, not changed.

## Questions for Tom

1. **The answer keys for cuttlefish #646, popoto #191, and popoto #188.**
   Do they say what you meant? #191 is a gate item; its key infers the
   push order (LPUSH), eager load, and that save replaces the list.
   Assumed: the keys stand as written, the inferred lines are marked in
   each key, and the gate runs on them.
2. **The gate ends at a held merge.** Is a held merge, scored and
   unreleased, the takeover evidence you want, or should each item's
   merge be released to its local origin as well? Assumed: held, never
   released.
