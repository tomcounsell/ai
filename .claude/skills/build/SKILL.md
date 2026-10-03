---
name: build
description: Continue the Valor rebuild from wherever it stopped. Reads the rebuild plan and each milestone's plan file, works out every task's stage, fans out Opus and Sonnet subagents to run the pipeline on every task whose dependencies allow, until something needs Tom, records the results in the plan files, and pushes. Use when asked to build, continue, resume, or "pick up where we left off" on the rebuild.
---

# /build

Carry the rebuild forward, many pipelines at once, from what the
repository records, until every task left is waiting on Tom or on another
task. Work in `~/src/valor-rebuild`
on branch `valor-cori-rebuild`. Never switch `~/src/ai` off `main`: the live
old system runs from it.

## 1. Orient

Read, in order: `CLAUDE.md` (the governance paragraph and the tests line
bind everything), `docs/plans/valor-rebuild.md` (above all "How every
milestone is built", "How the loops end", "Execution", and the milestone
you are on), `docs/plans/rebuild-handoff.md`, then every task's plan file
(`docs/plans/m<milestone>-*.md`). Plan files are the
state: their frontmatter `status`, and their record sections (critique
rounds, build record, patch rounds, checks, delivery, Tom's feedback,
merged), say exactly where work stopped.

## 2. Check the machine

Before any work, confirm in one pass: the branch is clean and up to date
with origin; `.venv/bin/python -m core settings` runs and names this
machine's owner role, Postgres, and backup folder; `~/.config/valor-kernel/`
holds `pgpass`, `judgement-keys`, and `claude-token`; `~/src/valor-demo/items`
exists; the suite command below runs. If any
is missing, follow `docs/plans/rebuild-handoff.md` "Setup". Ask Tom only
for what only he holds (a secret, an account, a disk). Never print a secret
or any prefix of one.

## 3. Find where things left off

1. `git fetch`, then list branches named `m<milestone>-*` and
   `git worktree list`. An unmerged branch or worktree is work in flight;
   its plan file says its stage.
2. Walk every milestone and task in `valor-rebuild.md` and the waves in
   section 4. For each task not `merged`, its stage is:

| What the plan file shows | Next stage |
|---|---|
| no plan file | plan |
| `planned`, fewer critique rounds recorded than allowed and the last said revise | critique (or revise, then critique) |
| `planned`, critique sound or rounds spent | build (spent rounds' findings ride into the build prompt) |
| `built` or a patch recorded with no checks after it | checks |
| checks recorded, no join | the join |
| `delivered-not-passed`, no feedback from Tom | **stop**: present the delivery and recommendation to Tom |
| Tom's feedback recorded, not applied | patch as he said, then what he said to run |
| passed checks or Tom's tap recorded, not merged | merge and roll out (needs Tom's tap unless already recorded) |
| `merged` | the next item |

3. A task is ready when its stage is not a stop and the tasks it waits on
   (section 4) are far enough along. Run every ready task at once.

Before starting, say in a few lines which tasks are ready, at what stage,
and which wait, on what.

## 4. Fan out

Tom's decision of 2026-10-03: the rest of the system is planned at once
and built by many subagents in parallel, Opus 5.5 and Sonnet 5.5, from
this session. Run every ready task's next stage in one message of Agent
calls, so they run together. There is no cap on how many run; spending is
metered, never a reason to wait.

**Waves.** A task's plan can be written as soon as its milestone is named.
Building waits only on what the task's code needs:

| Task | Plan | Build starts when | Merges after |
|---|---|---|---|
| 1.4b runners | written | now | 1.4a |
| 1.4d credential, transcripts, performers | now | now | 1.4b |
| 1.5 emulator and takeover gate | now | now (the scripts' move); the gate runs once 1.4b and 1.4d merge | 1.4d |
| 1.4c container verifier, review runner | now | Tom installs `container` | 1.5 |
| 2.1 resident kernel, bridge port | now | 1.4b merges | 1.5 |
| 2.2 Telegram | now | 2.1's plan is `sound` (it builds to the port the plan names) | 2.1 |
| 2.3 email | now | 2.1's plan is `sound` | 2.1 |
| 3 harnesses (Pi, the gateway's OpenAI route) | now | now | 1.5 |
| 4.1 objective tree | now | 2.1 merges | 2.1 |
| 4.2 persona | now | now | 1.5 |
| 4.3 routines and status page | now | 4.1's plan is `sound` | 4.1 |
| 5 tools on demand | none: built when a task needs a tool twice | | |
| 6 memory | when popoto #631 ships | | |

Real-account Done items (2.2, 2.3) and live rollouts wait on Tom's test
window or credential; everything before them is built and tested on
emulators and local servers, and the delivery says what the window will
show.

**Models.** Pass `model` on every Agent call:

| Role | Model |
|---|---|
| plan, critique, review | `opus` |
| build, patch, stakes 2 (the kernel, money, stored data, migrations, auth; 1.4b, 1.4d, 1.5, 2.1, the OpenAI route, 4.1) | `opus` |
| build, patch, stakes 0 or 1 (2.2, 2.3, Pi, 4.2, 4.3) | `sonnet` |
| test, docs | `sonnet` |

**Names.** Each agent gets a `name` of role and task (`builder-2-1`,
`critic-2-1-r1`, `test-2-1`, `review-2-1-p1`), so SendMessage reaches it
and its reports say whose they are.

**Isolation.** Every agent that writes or runs code gets its own:

- worktree, `~/src/valor-rebuild-<id>[-<role>]`, on its own branch;
  builders on `m<id>-<slug>`, docs on `m<id>-docs`;
- test database, `VALOR_TEST_DB=valor_rebuild_test_<id><role>` with the
  id's dots removed;
- service ports: Redis and any other server on `64<nn>` where `nn` is the
  agent's number in this session's list, never 6379 (the live Redis);
- `unset VIRTUAL_ENV` and only its worktree's own `.venv`.

**Plan files live on `valor-cori-rebuild`.** A builder commits its plan
and its records on its task branch; this session cherry-picks those
plan-file-only commits onto `valor-cori-rebuild` as they land, so section
3 reads every task's stage from one branch. No two tasks share a plan file.

**Shared files.** The kernel is one `core/` and one `core/schema.sql`.
Tasks that both change them build in parallel but merge one at a time, in
the order of the waves table. Before its checks, a builder rebases onto
the current `valor-cori-rebuild`; after an earlier task merges, every
later task touching the same files rebases and its checks run again on
the new head. A builder that needs another task's interface reads that
task's plan, never its unmerged branch.

**Reports.** Agents report only to this session (in every brief: "Report
only to the lead session through your final result; never address Tom").
Keep a running table in this session, not a file, of task, stage, agent
name, and head. When an agent finishes, start the next stage of that task
at once, without waiting for the others.

## 5. Run the pipeline, per task

Each task runs this pipeline on its own, alongside the others.
One builder subagent owns a task from plan to patch; resume it with
SendMessage for each later stage of that task in this session. A builder
from an earlier session cannot be resumed: start a new one, give it the
plan file and its records, and say it is continuing that work. Critique,
test, review, and docs are each fresh subagents that never see the
builder's narration.

- **Plan.** The builder writes `docs/plans/m<id>-<slug>.md` with
  `tracking: none`, `status: planned`, the Done items as evidence, a threat
  model in a few lines (what the turn controls, what the kernel must never
  do with it), stakes with `critique_rounds` and `review_rounds` (the kernel,
  money, stored data, migrations: 2 and 2), tech debt absorbed, what is left
  out, tests including the non-obvious cases, and questions for Tom with
  the answer it will assume. It commits and stops.
- **Critique** (Opus, read-only): wrong premises, divergence from the docs
  the milestone cites, missed cases, and anything that adds a check, gate,
  hook, or review step without an incident, a mission item, and Tom's
  grant. Verdict `sound` or `revise`.
- **Build.** The builder builds to the plan in its own worktree
  (`git worktree add ~/src/valor-rebuild-<id> -b m<id>-<slug>`), with every
  test it named, then reports head, counts, ruff, what remains for rollout,
  and decisions Tom may want to change.
- **Checks**, three fresh subagents at once on the same commit, each in
  its own worktree and its own test database (`VALOR_TEST_DB=valor_rebuild_test_<id><role>`):
  - **test**: suite at base and head, regressions, then breadth (behaviors
    the diff changes that no test exercises). Verdict `pass`, `red`, or
    `gaps`.
  - **review** (Opus, blind): judges from the request, the plan, the diff,
    the docs, and runs of its own, never the builder's narration. Checks the
    Done items against the plan's threat model; a finding that the kernel
    reads turn-owned state is one finding, "remove the read". Answers the
    governance boolean. Verdict `pass`, `changes`, or `governance_refused`.
  - **docs**: on branch `m<id>-docs`, commits Markdown only so no doc says
    something the candidate made untrue. Verdict `updated`, `no_change`, or
    `changes`.
- **The join**, by `docs/sdlc-state-machine.md`: review `changes` with a
  round left goes to patch with every finding; review `pass` with test or
  docs failing goes to patch once (the repair round); everything passing
  goes to merge. Before a patch, fast-forward the builder's branch onto the
  docs commit. After every patch all three checks run again.
- **Stop means stop.** When rounds are spent, record the delivery as
  `delivered-not-passed` with the findings and a recommendation, and stop.
  Build nothing more on it until Tom answers.
- **Merge** (on Tom's tap): fast-forward `valor-cori-rebuild` to the docs
  head, push, run the plan's rollout steps (back up first with
  `python -m core backup`), record them under "Merged", set
  `status: merged`, remove the task's worktrees.

## 6. Phase B: Valor builds itself

Once 1.5's takeover gate is merged, new tasks start through the kernel
where it can carry them; tasks already running with subagents finish
there. Start a kernel task with:
`.venv/bin/python -m core start "<instruction>" --project valor --branch valor-cori-rebuild`,
then `.venv/bin/python -m core run <task>` until it stops on a question, a
held merge, or a stop. Relay questions and held merges to Tom;
record his answers with `core answer` and his taps with `core approve` and
`core release`. Step in with subagents only to repair the kernel when it
cannot run its pipeline, and record that repair as a task.

## Rules every subagent brief carries

- Never write the real ledger `valor_rebuild` outside a rollout; tests use
  their own `VALOR_TEST_DB`. Never run `migrate`, `secure-login`, or the key
  commands against the machine cluster outside a rollout.
- Never kill processes by pattern; other agents share the machine. Kill by
  PID, and clean up services a run started.
- Never print a secret. Never push except the merge step; never touch
  `~/src/ai`.
- Your own worktree, test database, ports, and `.venv` (section 4);
  `unset VIRTUAL_ENV`. Never edit another task's worktree or branch.
- Report only to the lead session through your final result; never
  address Tom. Questions for Tom go in the plan file with the answer you
  will assume, and you carry on with it.
- Docs: the status quo only, plain language, no em or en dashes, no
  history words, never the word "cori", under 600 lines, the governance
  paragraph byte-identical to `CLAUDE.md`'s.
- Suite: `VALOR_TEST_DB=<own name> .venv/bin/python -m pytest -q tests`;
  lint: `uvx ruff check .` and `uvx ruff format --check .` (the one
  pre-existing complaint in `docs/bridges/telegram.md` is not a failure).

## Tom's queue

Bring Tom only: deliveries to accept (a held merge or a delivery that did
not pass, with a recommendation), identity and credential choices, and
questions about intent. Decide every other reversible call, record it under
"Decided by default" in the plan file, and keep going. Ask one question at
a time, in plain language, with a concrete example and the recommendation
first.

## Finish

Commit every record to the plan files, push the branches touched, and
report in under fifteen lines: what moved, what the checks said, what
waits for Tom, and the next item `/build` will pick up.
