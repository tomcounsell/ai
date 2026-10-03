# The emulator

The emulator is Valor's outward-facing fitness function. It replays requests
Tom actually made, from the commit each was made against, and scores what
Valor builds against what a human decided at the time: the merge, the review,
and the clarifications Tom gave. It answers the acceptance question with
evidence instead of narration: given an imperfectly specified goal, did Valor
return something that works, reflects good judgement, and cost Tom less
attention than the original did?

It serves the Evidence section of the mission ("Independent checks: blind
verification, the emulator's human-labelled cases, and the audit sample") and
measures Mission items 1 (own the whole job), 3 (absorb ambiguity, ask only
when it matters), and 6 (attention spent as carefully as money). It is a
measurement. It refuses nothing on its own; what it reports goes to Tom as
evidence for his decision. The one gate read from it is milestone 1.5's
takeover gate in docs/plans/valor-rebuild.md, a one-time Done item Tom set,
not a standing rule.

## Terms

| Term | Meaning |
|---|---|
| Item | One historical request, as a JSON file: the repository, the clean base commit, the request verbatim, the answer key, the reference, the services its tests need, and the verification commands |
| Run | One item replayed in one arm, in its own workspace, as one kernel task. Named `<item>-<arm>` unless `--run` names it |
| Arm | The condition a run is replayed under: `bare` (the judge decides `precise`, so the request goes straight to the plan), `clarify` (the judge decides `thin`, so Valor inspects, then sends its material questions and intended approach before editing), or `routed` (the real judgement legs decide) |
| Clean base | A commit holding the repository as it stood when Tom asked, with nothing that already states the answer |
| Answer key | Tom's recorded intent for the item: his words where they exist (an issue comment, a Notion card, his review), and inferences from the merged code, marked as such, where they do not |
| Reference | The merged PR's diff: one accepted realization of the intent, shown to the judge and never to Valor |
| Stand-in | A model that plays Tom from the answer key: it answers Valor's questions and reviews each delivery as project manager. Its every reply is ledgered with `role_played` true |
| Blind judge | One model call that scores the final commit on fidelity, correctness, and simplicity, without seeing the arm, the questions, or the feedback |
| Hidden reference test | The reference PR's own test file, dropped into the exported final commit and run there |
| Broad suite | The app's relevant test suite on the final commit, reported as failures not already failing at the base |
| Leak check | The two checks that the run could not see the answer: the base tree before the run, the transcripts after it |

## What an item is labelled by

Every label comes from a human decision that already happened. The emulator
never asks a model what Tom would have wanted and treats the answer as truth.

- **The merge decision.** The PR that shipped is the reference. A request Tom
  declined is labelled by the decline: popoto #188 merged as docs only, so
  building the feature is the wrong outcome there (rebuild-baseline.md,
  pop-c).
- **The review decision.** Where Tom's review found a defect, the defect is
  part of the label. popoto #633's review found a stale `len()` cache; the
  answer key carries his contract, and the stand-in raises it as feedback if
  a delivery repeats the defect (rebuild-baseline.md, pop-a).
- **Recorded clarifications.** Questions Tom answered before the original
  build are the answer key. psyoptimal #894's six Notion answers labelled
  every delivery in the first demonstration (rebuild-demonstration.md,
  "Against the reference answers").

Valor has to discover Tom's intent rather than be told it, which is the
setting Cooperative Inverse Reinforcement Learning describes [3]: the answer
key is the reward Valor does not know, and the stand-in reveals it only in
answer to a question.

## The machinery

A package, `tests/emulator/`, run as modules (`python -m
tests.emulator.replay`) and never collected by pytest: `replay.py` (the
driver), `workspace.py`, `stand_in.py`, and `judge.py`, sharing
`common.py`. Items, answer keys, results, and each
run's spec live in the experiment directory, `valor-demo`, outside the
repository, set by `VALOR_DEMO`; the kernel keeps each run's workspace
under its `work_dir` setting (`~/valor-tasks`).

### The item

```json
{
  "name": "pop-b",
  "repo": "tomcounsell/popoto",
  "base": "<clean base sha>",
  "pr": 191,
  "reference_diff": "ref/pop-b.ref.diff",
  "services": ["redis"],
  "request": "Tom's request, verbatim",
  "answer_key": "pop-b.key.md",
  "verify": ["shell commands run on the final commit, sandboxed"]
}
```

`repo` is `OWNER/NAME` or a local repository path. `pr` names the reference
on GitHub; `reference_diff` is a file in its place, required when the clean
base is a rebuilt commit the PR does not descend from. `services` is any of
`postgres` and `redis`. Relative paths resolve against the item file. Items
and answer keys stay outside every run directory, where no turn's sandbox can
read them.

`verify` carries the hidden reference test: the PR's test file, embedded in
the command, written into the tree as `test_zz_hidden_reference`, and run.
A second command runs the broad suite and prints failures, which the item
compares against the base's own failure list. An item names opaque labels
(`pso-a`, `pop-b`), so nothing in a run directory names the PR; the mapping
lives in an index beside the items.

### The workspace: `tests/emulator/workspace.py`

Fetches the item's repository into a bare cache of the experiment's own
(through `gh`, as Tom) and writes the run's project spec,
`runs/<run>/project.toml`. The kernel provisions the rest when the driver
starts the task (`core/workspace.py`; docs/workspace.md):

- **The clone.** At the base commit with no tags, one branch, its reflog
  expired and garbage collected, so it holds no commit after the base.
- **The origin.** A local bare repository is the clone's only remote,
  `main` at the base, and its `HEAD` names `main`, so a task's merge has a
  target branch. Only the broker's `push_branch` and `merge` performers
  write it, after approval. The run's spec names no `merge_url`, so nothing reaches GitHub.
- **Services.** A Postgres cluster of the task's own, with its own roles,
  passwords, and port, separate from the kernel's database, and a
  `redis-server` of its own when the run asks, with no persistence and its
  file-writing commands closed. The turn's environment points the app's
  tests at both.
- **Isolation.** A `home/` the turn can read and not write: a git config with
  no credential helper, an empty gh config, and the sandbox-exec profiles.

The working session's profile lets a turn read and write its own clone,
caches, and state, and nothing else of the work directory or `~/src`: not
other runs, the caches, the items, the answer keys, the results, or Tom's
checkouts. It denies his notes, mail, messages, cloud folders, keys, gh
config, and earlier transcripts. On loopback a turn reaches the kernel's
gateway, the ports 8000 to 8009, and its task's own services; the
machine's own Postgres (5432) and Redis (6379) are out of reach. Denies
precede allows, because a network rule after the loopback allows makes
sandbox-exec refuse allowed ports (rebuild-demonstration.md, "Kernel
findings", 3). Turns run with web fetch and search off.

`--teardown RUN` stops the run's task and removes its workspace through
the kernel (`python -m core workspace remove`), which stops its services
and frees their ports.

This serves the constraint "Bounded authority, metered spending" for replays (a turn holds
no credential that reaches the world) and the validity of the measurement:
a turn that can read the answer is not being measured.

### The driver: `tests/emulator/replay.py`

`python -m tests.emulator.replay ITEM.json --arm bare|clarify|routed
[--run NAME] [--judge]` builds the
workspace's spec, starts a kernel task (`python -m core start --project SPEC
--base SHA`) at effect ceiling `act`, no governance grant, with Opus 5.5 as the model, and loops `python -m core run` until one
of the outcomes below. The kernel has no switch for the arm. For `bare` and
`clarify` the driver starts the local judgement upstream (`python -m
tests.judgement_upstream --answer precise|thin`) and points the kernel's leg
endpoints at it (`VALOR_JEV_URL`, `VALOR_OPEN_WEIGHT_URL`), so the real
judge runner, port, metering, and rows run and decide as forced; each
attempt's endpoint host on the row (127.0.0.1) shows the arm was forced, and
a loopback endpoint needs no key. `routed` uses the real endpoints and the
keys in the kernel key directory.

| Outcome | When |
|---|---|
| `held` | the task holds its merge and the stand-in accepted it, or its feedback rounds (default 2) are spent; the feedback count is recorded |
| `to tom` | the delivery did not pass, or the merge was refused: the pipeline hands the task to Tom |
| `stopped` | the task was stopped |
| `an effect other than a local push is held for Tom` | a held effect that is neither a `push_branch` nor a merge |

A task in `merge` is read from its status in this order: a delivery that
did not pass, then a governance instance not granted or a join of
`governance_refused`, then a refused merge, then a held merge, and
otherwise it runs on. Three cases exit the driver with the outcome unset
and say why: a stage awaiting Tom's grant, `NO RUNNER` (a check stage with
no runner, whose verdict is recorded by hand from a blind checkout of the
kernel mirror), and a failed run. The next invocation resumes the same
task.

Critique runs as the kernel's fresh session, metered and recorded on the run's task.

The driver approves and releases a held `push_branch` only when the
workspace's push URL is exactly the run's own bare origin, under Tom's
standing permission for pushes to local copies; it records that permission
in the approval note. It never answers a merge: every merge effect of the
task is skipped, the current one and any a later candidate superseded, and
the held merge is the run's evidence. Any other held effect stays held and
ends the run. This is the effect-class constraint applied to the driver: it
holds authority only for pushes to the run's own local origin, which Tom
pre-authorized.

A run whose result file has no outcome resumes its task; one with an
outcome is refused unless `--rebuild` is given. Each driver holds one of
`VALOR_DEMO_SLOTS` lock files for its whole invocation, so at most that many
replays run turns at once. The default is three, which the experiments ran on
a 64 GB machine; on the 16 GB M4 Air set it to one, matching the constraint
of one `claude -p` at a time.

### The stand-in: `tests/emulator/stand_in.py`

Plays Tom from the answer key, never beyond it. On a question it answers
briefly, by number, reveals only what was asked, and says "Your call." where
the key is silent. If Valor states an approach that contradicts the key on
something that changes what gets built, it adds the single most important
correction. At a held merge it reads Valor's delivery note and the diff
from the base to the merge's head, taken from the task's kernel mirror and
never the turn's workdir, and either accepts, when the delivery does what the key asks in
substance, or gives one point of project-manager feedback: the single most
important divergence, in one to three sentences.

Its model is the `frontier` seat (`claude-opus-5-5`); the driver's
`--stand-in-model` names another for a deliberate comparison. Every reply
enters the ledger through `python -m core answer|feedback
--role-played`, with `by` set to `stand-in (<model>)`. The attention a run
spends is therefore real ledger data with honest provenance: the emulator
counts questions and feedback rounds the same way the attention log counts
Tom's, and the `role_played` flag keeps simulated attention apart from his.
This serves Mission item 6 and the requirement of the constraint "Reliable
stop, recovery, and correction" that corrections carry provenance (rebuild-demonstration.md, "Kernel findings",
5).

### The judge: `tests/emulator/judge.py`

Runs the item's verification on the final commit (the held merge's head,
else the candidate) and nothing else. The commit is exported with `git
archive` from the task's kernel mirror into a fresh tree at
`<task_dir>/checks/verify-<run>/`, which only the kernel writes, so no turn
can write it. Each command runs there under the working turn's profile as
the baseline ran it (the temp directories shared, the verification
directory added read-write, `TMPDIR` inside it), with the task's
environment and services, and whatever a command leaves running is reaped
and listed. Uncommitted work,
a virtualenv included, is absent: an item's commands set up what they need.

Then one model call, blind to the arm, sees the request, the answer key, the
reference diff, the candidate's diff from the mirror, and the verification
output, and nothing that names the arm, the questions, or the feedback. The
candidate's diff leaves out the task's own plan document (`plan.path`); the
result records the diff's size, the path left out, and whether the 70,000
character limit cut it. The model is `JUDGE_MODEL`, one pinned Sonnet id. It scores 0 to 5:

- **Fidelity.** Does it build what the requester intended, at the intended
  scope? The answer key is the authority; the reference shows one accepted
  realization. Missing intended behavior and unrequested additions both cost.
- **Correctness.** Does it work, including the edge cases the intent implies,
  and is it tested? Failing or missing tests count against it.
- **Simplicity.** Is it as small and clear as the job allows, in the
  codebase's existing patterns?

It lists the divergences that matter, most important first. Differences the
answer key leaves open are not faults. An earlier verdict on the same run
moves to `judge_history`.

The judge is blind because LLM judges favor position, length, and their own
style [16]; it never sees the executor's account of itself. Its scores are
never reported alone: the hidden reference tests are a check the judge
cannot talk its way around, and a judged proxy that is optimized against
fails in the ways Goodhart's law predicts [14].

### The leak check

Two layers, because a turn that finds the answer measures nothing.

- **Before the run: the base tree.** A search of the base for the feature's
  own terms and for any plan or spec document touching it, plus the count of
  commits after the base and the list of refs, run by hand when each clean
  base is chosen.
- **After the run: the transcripts.** A scan of every tool call in the run's
  Claude Code transcripts for the upstream repository's URL, the GitHub API,
  `gh` subcommands that read issues or PRs, `curl` or `git fetch` against
  GitHub, the PR and issue numbers, and a PyPI install of the package under
  test. Any web fetch or search is a hit. The result file records
  `leak_suspect`, the hits, and the `/tmp` paths this run shares with other
  runs' transcripts. The scan lives in the experiment directory, beside the
  items.

A hit is read by hand. The baseline's two hits were both editable local
installs, cleared (rebuild-baseline.md, "Caveats").

### The record

Each run writes `results/<run>.json`, atomically, after every step:

- the item, arm, model, stand-in model, and feedback cap;
- the task id, the emulator task, the workspace (`replay.json`), and the
  outcome;
- `questions` (each with its answer and provenance) and `feedback` (each with
  the delivery it answers and its provenance), read from the kernel's
  attention state; `feedback_rounds`; `attention`, counts by kind;
- `deliveries`, the delivery note of every `task.delivered` row;
- `turns`, each with its outcome, its gateway-metered dollars, and the
  harness's own cumulative figure;
- `kernel_spend_usd`, the item task's metered spending, and
  `emulator_spend_usd`, the emulator task's, with its `open_calls`;
- `final_rev`, the held merge's head or the candidate, `merge_effect_id`,
  and the diff stat against the base, from the mirror;
- `judge_model`, and `judge`: scores, divergences, rationale, the diff's
  size, exclusion, and truncation, every verification command's exit code
  and output tail, and reaped processes;
- the leak check's fields.

### Metering

Every stand-in and judge call goes through the kernel's gateway, which the
driver runs on an event loop in a thread of its own. Each run has one
emulator task, a calibration task (`tasks.start_calibration`, its
`task.started` naming the run and the item task): it only meters, and
every SDLC writer refuses it. Each call is issued a token on that task,
runs `claude -p` tool-less with the gateway's placeholder token, a fresh
empty Claude Code config under the run's directory, and the kernel's
`DROP_ENV` applied, and has its token retired after it however it ends. The
gateway sets the kernel's login on the way out and writes a
`gateway.opened` and a `gateway.charged` row per call. The driver drains
the gateway before reading spend, since a charge lands after the response
ends. A separate task keeps the item task's spending comparable with the
baseline's kernel column. A check verdict recorded by hand is not metered.

## What the first runs showed

The emulator has fourteen runs: the first demonstration (psyoptimal #894,
one bare run carried through two feedback rounds) and the replay baseline
(six items, each bare and clarify, plus one bare rerun of psyoptimal #872
after a harness fix, thirteen runs). Both records are its seed set.

### About Valor

These findings justify mechanisms in other docs; the emulator is how they
were found and how they will be rechecked.

- **One-line asks need a question before the build.** popoto #191: bare put
  `push()` on the field class and scored fidelity 1, hidden tests 3/11;
  clarify asked where `push()` lives and reached fidelity 3, 7/11. popoto
  #188: bare built the feature and needed a feedback round to pivot to docs;
  clarify was told before building and finished at fidelity 5 for half the
  spend. psyoptimal #894: three decisions, two feedback rounds, $1.74 of
  rework (rebuild-baseline.md, "Clarify"; rebuild-demonstration.md,
  "Attention log").
- **Precise requests gain nothing from a question.** On #872, #646, and #893
  clarify asked sensible questions and changed nothing in the outcome. On
  #633 its question carried a wrong premise and the build reproduced a
  variant of the defect Tom's review had caught (rebuild-baseline.md, pop-a).
- **Plan, critique, and revise rounds bought nothing measurable** at this
  size of work; a short statement of intended approach carried what the plan
  carried (rebuild-baseline.md, "Plan, critique, revise").
- **Every replay wrote fewer tests than its reference**, and the hidden tests
  found what that missed (rebuild-baseline.md, "Test breadth").
- **No run looked at a page in a browser**, including the UI items. Testing
  actual use is not yet measured (rebuild-baseline.md, "Browser use").

### About itself

The emulator's own weaknesses, each with what the design does about it.

**Plan documents committed before the build leak the answer.** The process
that built the reference PRs committed its plan, critique, and revision docs
to `main` before building, so a PR's base commit usually holds the design.
popoto #191's base held the full design document. Every item needs a clean
base: the parent of the first plan commit when everything between it and the
PR's base is plan-only (#872, #893), or the PR's base tree minus
`docs/plans/` rebuilt as one squashed commit with no history (#646, #191,
#188), in which case the item carries its reference as a diff file. The
first demonstration's own workspace clone could not be reused for later
items: its history held the answers to #872 and #796.

**The stand-in is a lenient reviewer.** It accepted every run, including
#191 bare, which the judge scored fidelity 1, and #633 clarify, correctness
2. It accepted #633 clarify after one round with the stale-cache bug moved
rather than removed. It sometimes said more than it was asked (it quoted
Tom's #633 contract in answer to a design question) and once leaked meta
("the key doesn't settle") (rebuild-baseline.md, "Caveats"). The run's
outcome is therefore not a verdict: `accepted` means the stand-in stopped
giving feedback, and the judge and the hidden tests carry the score. A
stronger stand-in model is the design's next step; a weaker model grading a
stronger one loses what the stronger one hides [18].

**Answer keys are partly inferred.** #872 and #633 are Tom's words. #646's
answers and half of #191's are inferred from the merged code. #893 has no
key; the stand-in answers "Your call." #188's decision was recorded by the
agent account, author of the call unknown. An inferred key labels the
reference's choices as Tom's intent, and the judge, which also sees the
reference diff, then favors the reference's shape twice. Each item marks
which lines of its key are recorded and which inferred, and a score on an
inferred line carries that caveat. Tom confirming an inferred key turns it
into a recorded one.

**Public repositories leak through the model, not only the network.** popoto
is public; the others are private but on GitHub. The transcript scan shows a
run did not fetch the answer. It cannot show the model did not already know
it from training. This is a gap: no reference in REFERENCES.md covers
training-data contamination of replayed tasks, and no check in the emulator
detects it. Private repositories and requests newer than the model's
training data are the mitigation available today.

**`/tmp` is shared between runs.** Common file names recurred across items.
The leak check lists `/tmp` paths a run shares with other runs, and files
from earlier runs of the same item are deleted before each run. A per-run
temporary directory inside the sandbox closes this; it is not built.

**n = 1.** One run per item and arm. A difference of one judge point is
noise. The baseline's aggregate (fidelity 3.67 bare, 3.83 clarify, summed
over six items) carries no significance; the per-item differences on the
one-liners (+2 on #191, +1 on #188) are the signal, and they are each one
run. Repeated runs per item and arm are the design; their cost is below.

**The judge is one Sonnet call that sees the reference.** It is blind to the
arm, not to the reference's shape. Calibration against Tom is the design
(see "Judge calibration"); today the judge's scores have never been compared
with his.

## Mobile

Mobile support for Ionic and Flutter apps is a stated direction, so the
emulator needs mobile items. None of Tom's mobile repositories gives a usable
replay:

- The most active Ionic React and Capacitor app has no tests, its tickets
  carry prescriptive solutions (little ambiguity to measure), its best
  candidate PR is contractor-authored with unrelated scope mixed in, and
  running it needs third-party service secrets.
- The Flutter repositories are either agent-authored with plan docs (the
  same leak as above), too large per PR, or have no merged PRs.

A public stand-in exists: `localsend/localsend` PR #2765 (issue #2735,
"show '3h 20m' or '1d 4h 30m'" for long transfers), +115/-9 across five
files, with a pure-Dart unit test the item can run as its hidden reference
test, no secrets, and no need for the app's Rust workspace. It needs Flutter
3.41.9 through fvm, and Flutter is not installed on this machine. Its answer
key needs the issue's comment thread read first, and the repository's
`AGENTS.md` and `CLAUDE.md` removed from the clean base. It is public, so it
carries the contamination gap above.

A mobile item needs its toolchain readable from the sandbox the way `uv` is
(a shared `bin/`), and Flutter's SDK and caches are large; where they live on
the 16 GB machine is `docs/machine.md`'s concern.

## Cost

Measured, gateway-metered for Valor's turns:

| | Runs | Valor (Opus 5.5) | Stand-in and judge (Sonnet) | Total |
|---|---|---|---|---|
| First demonstration | 1 | $2.97 | none (Tom and a role-play by hand) | $2.97 |
| Replay baseline | 13 | $18.37 | $4.08 | $22.46 |

A baseline run cost $0.48 to $2.13 of Valor's turns (mean $1.42) and $0.10 to
$0.92 of stand-in and judge. Wall time was 3 to 36 minutes per run with three
in parallel; on one slot the thirteen runs take about two and a half hours.
A run costs about $1.73 all in, so a sweep of the six baseline items in two
arms, three repetitions each, is about $62. Spend is metered and reported,
and stops nothing.

**Metered spending.** Valor's turns are metered by the kernel's gateway and
recorded on the item task. The stand-in's and judge's calls are metered by
the same gateway onto the run's emulator task (Metering, above), so a run's
whole model spend is in the ledger, apart from any check verdict recorded by
hand. The baseline's stand-in and judge figures above were logged outside
the ledger.

## When it runs

The design runs the emulator on every change to a skill and on every change
to the text a turn is rendered with (the persona and the Brief), because
those changes alter behavior without changing kernel code and nothing else
measures them. It also runs on any change to the request-underspecification
classifier (below). A sweep is a routine, its result is a report to Tom
comparing the change against the last sweep on the same items, and the
decision to keep the change is his. Today it runs by hand, one item at a
time.

The emulator refuses no merge. Milestone 1.5's takeover gate (three items
reaching a held merge at the baseline's bars) is a one-time Done item Tom
set for the rebuild, read from emulator runs by the build; it is not a
standing rule, and no merge is refused on a score.

## Measuring the request-underspecification classifier

The judgement layer's classifier reads each request and routes the
underspecified ones to a clarify turn. It is a granted guard, ledgered with
its incident (the first demonstration and the replay baseline's #894, #191,
and #188 runs), Mission items 3 and 6, and a ninety-day expiry
(`docs/judgement-layer.md`). The emulator is how that guard is judged before
its expiry:

- **Its labels.** The seed set already says which requests should be routed.
  Route: #894 (leans on an example), #191 and #188 (one-line asks). Straight
  to build: #872, #646, #893 (precise). #633 is the hard case: the request was
  precise and clarify hurt.
- **Its arm.** The `routed` arm replays each item with the classifier
  deciding. It should match clarify on the one-liners and bare on the
  precise items, at less attention than clarify spends on every item.
- **Its saving.** The first demonstration estimated the classifier at well
  under $0.05 a request and the saving at $1.40 to $1.80 of $2.97, plus both
  review rounds. Replaying #894 with and without it measures that estimate
  (rebuild-demonstration.md, "Money").

## Judge calibration

The judge is a cheap verifier of a stronger model's work, and its value
depends on how often it agrees with Tom. The design scores it the way the
blind verifier is scored: a human audit sample [4] in which Tom gives his own
accept or reject on a judged run, with the judge's scores turned into a
probability of acceptance and reported as a Brier score with its sample size
[12]. Human judgement with a model's help can be worse calibrated than either
alone [17], so Tom audits without seeing the judge's scores first. No run has
been audited yet.

## Growing the item set

An item qualifies when:

- the request is human-originated and recoverable verbatim (a Notion card, an
  issue Tom wrote, a Telegram message), not an agent's write-up of it;
- a human decision labels it: a merge, a decline, a review, or recorded
  answers;
- a clean base exists or can be rebuilt;
- its verification runs locally without production secrets;
- it is a size Valor can finish in one task (the baseline's references were
  +149 to +683 lines).

Agent-authored requests, items whose only verification needs a browser or a
device the workspace lacks, and items whose answers live nowhere are set
aside, not scored with a guess. Tom set the scope on 2026-10-01: items
from psyoptimal, popoto, and cuttlefish, requests he wrote in the last 12
months. Spend is metered and reported only, and stops nothing.

## Boundary with the kernel's tests

The kernel's integration tests under `tests/` check that metering, stop, the
ledger, and the broker hold. The emulator measures outcomes, on Tom's other
codebases, against a human's decision. The model stays fixed across arms, so
the arm is the variable.
