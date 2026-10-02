---
name: build
description: Continue the Valor rebuild from wherever it stopped. Reads the rebuild plan and each milestone's plan file, works out the next task and stage, runs the pipeline with subagents until something needs Tom, records the result in the plan files, and pushes. Use when asked to build, continue, resume, or "pick up where we left off" on the rebuild.
---

# /build

Carry the rebuild forward one pipeline at a time, from what the repository
records, until the next point that needs Tom. Work in `~/src/valor-rebuild`
on branch `valor-cori-rebuild`. Never switch `~/src/ai` off `main`: the live
old system runs from it.

## Where the last session left off (2026-10-02)

The popoto #191 trial run (milestone 1.4, before 1.4b) is task
`75c0902b6e25`, in `build` at $6.09 of an $8 budget, with no candidate.
Each Opus call reserves about $1.75, so the $1.91 left buys nothing.
The record and the next steps are in `docs/plans/m1-4-checks.md`, "The
popoto #191 trial run". Next: ask Tom for about $2 more (past the $8 he
set), then `python -m core run 75c0902b6e25`; at `NO RUNNER`, fresh
subagents play test, review, and docs through `python -m core verdict`,
and the run stops at the held merge, never releasing it. On Valor's Mac
every command needs `PATH=/opt/homebrew/opt/postgresql@18/bin:$PATH`,
`VALOR_BACKUP_DIR=/Volumes/PINK/valor_temp`, and
`PGPASSFILE=~/.config/valor-kernel/pgpass`. Remove this section when the
trial is recorded as done.

## 1. Orient

Read, in order: `CLAUDE.md` (the governance paragraph and the tests line
bind everything), `docs/plans/valor-rebuild.md` (above all "How every
milestone is built", "How the loops end", "Execution", and the milestone
you are on), `docs/plans/rebuild-handoff.md`, then the plan file of the
current milestone (`docs/plans/m<milestone>-*.md`). Plan files are the
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
2. Walk the milestones and tasks in the order of `valor-rebuild.md`. The
   current item is the first one not `merged`. Within it:

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

3. If milestone 1.5's takeover gate is merged, Valor builds itself:
   switch to phase B below instead of driving subagents.

Say in two lines what the current item and stage are before starting.

## 4. Run the pipeline (phase A)

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

## 5. Phase B: Valor builds itself

After takeover, start each task through the kernel:
`.venv/bin/python -m core start "<instruction>" --project valor --branch valor-cori-rebuild --budget-usd <N>`,
then `.venv/bin/python -m core run <task>` until it stops on a question, a
held merge, the budget, or a stop. Relay questions and held merges to Tom;
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
