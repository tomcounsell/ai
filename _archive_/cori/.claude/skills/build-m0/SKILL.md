---
name: build-m0
description: Orchestrate the M0 build end to end as the lead. Opens the build stage, runs one builder per component through the waves the migration chain allows, validates and reviews each, merges to main, carries findings across builds, gates the live integration wave on the person, and closes with the measurements and a report. Triggered by '/build-m0'.
---

# /build-m0

You are the lead for building Cori's M0. Twelve plans under `docs/plans/` are reconciled against seams version 3, and every one of them ends in acceptance checks a builder can run. You turn them into code on `main` without writing component code yourself: you open the stage, dispatch a builder per component, have each build validated and reviewed, merge, carry what one build teaches into the next, and close the stage with the integration wave's measurements. The person is at the keyboard for two moments, the kickoff and the gate before the live wave, and reads `main` at their own pace. The lead pushes `main` and the build branches to `origin` so the stage survives a crash or a change of Mac; nothing else leaves the machine.

The project's `do-build` skill is written for one feature plan in a running repository: a fork with one turn, a PR per plan, a builder per task. Here twelve plans chain through one migration sequence, share one Postgres cluster, and hand seams to each other, and the human review is a practice rather than a PR gate. This skill borrows `do-build`'s worktree isolation, its builder self-check, and its definition of done, and replaces its pipeline.

Everything a builder needs is in the `build` skill (`.claude/skills/build/`). The three agent briefs are in `briefs.md` beside this file; the log template is `build-log-template.md`. Read all three now, then `CLAUDE.md`, `docs/plans/README.md` in full, `docs/plans/00-seams.md` in full, and the header, Tasks, and Out of scope of every `docs/plans/NN-*.md`, then `git log --oneline | head -40`. Do not start until the working tree is clean and you are on `main`.

## Seats

| Role | Agent type | Model | Components |
|---|---|---|---|
| builder | `general-purpose` | `fable` | tree, worker, verifier, supervisor, integration |
| builder | `general-purpose` | `opus` | events, sandbox, spaces, gateway, memory, broker, surface |
| validator | `validator` | `opus` | every component |
| reviewer | `general-purpose` | `opus` | every component |

The five on `fable` hold the most cross-cutting invariants and the hardest debugging. The person may change this table before kickoff; nothing else reads it. The stock `builder` agent type is scoped to one task and carries SQLite and ruff advice, so builders run as `general-purpose` with the brief in `briefs.md`; the stock `validator` is read-only, which is what a validator should be, and its model is raised here.

## Phase 0. Open the stage

Run every check and record its result in the build log. A red check stops the stage and goes to the person.

1. `git status --porcelain` is empty and `git branch --show-current` is `main`. `uv sync`. `uv run pytest -q` is green; record the count (51 when this skill was written). `uv run alembic current` prints `0001` on the `cori` database.
2. Services: `brew services list` shows `postgresql@18` and `redis` started; `container system status` is running; `container image list` has `cori-base:3.14`; `container network list` has `cori-hostonly` (`cori-egress` may exist as a spike artifact and is left alone).
3. Keychain: for each of `anthropic_api_key`, `logfire_token`, `github_token`, `gmail_refresh_token`, `google_oauth_client_id`, `google_oauth_client_secret`, `security find-generic-password -s cori -a <name>` exits 0. `gh auth status` is logged in. Note the fine-grained GitHub token's expiry from `docs/prereqs.md` item 13 (2026-10-19); the broker's live tests and the integration wave need it live.
4. Remove the planning stage's worktrees. `git branch --no-merged main | grep worktree-agent` must print nothing; then `git worktree list --porcelain | awk '/^worktree .*\/\.claude\/worktrees\//{print $2}' | xargs -n1 git worktree remove --force`, `git worktree prune`, and `git branch --merged main | grep worktree-agent | xargs -n1 git branch -d`.
5. Create `docs/reviews/<today>-build-log.md` from the template with phase 0 filled in. Commit on `main`: "Build stage opened: environment checked, planning worktrees removed, build log created".
6. Tell the person, in one message, that the stage is open, which live actions the build will take (the table under Building in `docs/plans/README.md`), and that you are proceeding to wave 1. Do not wait for an answer; the kickoff was the consent, and the gate before wave 10 is where you ask again.

## The waves

The build order in `docs/plans/README.md` is the merge order. Each migration's `down_revision` names the previous plan's file, so a plan whose migration is `000N` starts only after `000N-1` is on `main`. That makes waves 5 to 9 serial and leaves two safe pairs.

| Wave | Components | Notes for the briefs |
|---|---|---|
| 1 | `events`, `sandbox` | Both depend on the seams alone; disjoint files; sandbox owns no table. Sandbox: `cori-egress` exists here as a spike artifact and `setup.sh` leaves it alone. |
| 2 | `tree` | Task 3 writes `schemas/report.py` and `schemas/trace.py` verbatim from seams §1.6 and §1.7; the worker plan keeps them. |
| 3 | `spaces` | The manifest edit is the builder's in this build: task 1 adds the `audience` block to `infra/spaces/psyoptimal.yaml` per seams §1.1 (`domains: [psyoptimal.com]`, `addresses: []`, `source: "~/work-vault/PsyOptimal/README.md#contacts"`) in the same commit as the `Audience` field, replacing the trailing comment, so `main` stays green at every commit; you review the manifest at merge. The plan's two greps then pass. |
| 4 | `gateway`, `memory` | Gateway needs the tree's signatures, which its tests fake, and migration 0004; memory needs events and spaces and owns no table. Gateway: the four `usd_per_mtok` numbers per seated model come from the provider's pricing page with the check date in the YAML comment; you review the seat file at merge. Memory: `.env.build` carries `CORI_REDIS_URL=redis://localhost:6379/1`; the conftest binding the plan writes must read it. |
| 5 | `worker` | `schemas/report.py` and `schemas/trace.py` exist from the tree build; keep them, add the validators the plan names if the verbatim copy lacks them, and add `schemas/records.py`. Task 10 starts its own gateway on 8788. |
| 6 | `broker` | Tasks 5 and 7 push and delete a branch on `yudame/cori-sandbox`; task 6 reads ten headers and one body from `psyoptimal.com` mail. |
| 7 | `surface` | The pty round trip in task 9 runs on this Mac. |
| 8 | `verifier` | Task 4's prerequisite (the manifest's `audience`) is met since wave 3. Tasks 8 and 11 spend on the verifier seat. |
| 9 | `supervisor` | Eleventh in the build order: every seam it consumes is on `main`; a drift in any of them lands here first. |
| 10 | `integration` | Runs on the `cori` database. Gated (below). |

## One component, start to merge

Do these steps for every component. Run a wave's builders in one message so they start together; then end your turn. You are re-invoked as each agent finishes. Do not poll.

**1. Prepare.** From the repository root:

```
git worktree add .worktrees/build-<slug> -b build/<slug> main
createdb cori_<slug>
printf 'CORI_PGDATABASE=cori_<slug>\nCORI_MIGRATOR_DSN=postgresql://localhost:5432/cori_<slug>\nCORI_REDIS_URL=redis://localhost:6379/1\n' > .worktrees/build-<slug>/.env.build
(cd .worktrees/build-<slug> && uv sync && uv run --env-file .env.build alembic upgrade head)
```

For `integration`, the database is `cori` itself, already at head. Set the component's Status to `building` in the index and the log, and commit on `main`.

**2. Build.** Spawn the builder with the brief from `briefs.md`, filled in: the worktree, the head revision its database is at, the seams delta since its plan was written (from the log's seams table; `none` for wave 1), and the notes for its component from the wave table. Name it `build-<slug>`.

**3. Check the output.** When the builder reports: `git -C .worktrees/build-<slug> log --oneline main..HEAD` has commits and `git -C .worktrees/build-<slug> status --porcelain` is clean. No commits, or a final message that is a bare shell command with no tool calls behind it, is a failed dispatch: spawn a fresh builder once with the same brief and note it in the log. A report that skips a task, or says a live test skipped on this machine, goes straight back to the builder as a return.

**4. Validate and review.** Spawn the validator and the reviewer in one message, with the builder's report attached to both. When both report: a `RETURN` from the validator or a blocker from the reviewer goes to the builder by `SendMessage` as one numbered list, verbatim. The builder fixes and reports; send the changed items back to the same validator and reviewer. Three rounds is the limit. On the fourth, set the component to `blocked`, write what stands in the way in the log, stop the wave, and ask the person with `AskUserQuestion`.

**5. Pre-merge sync.** If `main` moved since the branch was cut, send the builder the sync message from `briefs.md` and wait for the three green outputs.

**6. Merge.** From the repository root:

```
git merge --no-ff build/<slug> -m "Merge build/<slug>: <one line saying what the component now does>"
uv run alembic upgrade head
uv run alembic heads
uv run pytest -q && uv run black --check . && uv run lint-imports
```

One head, everything green. Then: the component's Status becomes `built` in the index and the log; the log row gets the merge commit, the suite count, the round count, and the agent names; the plan's header table gains a row `| Built | <date>, merge <sha> |`; the reviewer's notes and every finding from the three reports go into the log's Findings table with a disposition. Commit on `main`. Then `git worktree remove .worktrees/build-<slug>` and `dropdb cori_<slug>`; keep the branch.

If the suite is red on `main` after a merge that was green on the branch, the cause is the other member of the wave. `git reset --hard ORIG_HEAD`, `uv run alembic downgrade <previous head>`, have the builder sync and rerun, and merge again.

## Findings and the seams

A builder's Deviations and Findings, a validator's discrepancies, and a reviewer's notes all land in the log's Findings table. Three dispositions change something:

- **`seams v4`.** A seam that cannot be built as written. Edit `docs/plans/00-seams.md` yourself: bump the version line to 4 with today's date, add a section "Version 4, build" at the end with a numbered entry naming the section, the change, the plan that raised it, and every plan it touches. Record the entry in the log's seams table. From then on, every builder whose plan the entry touches receives it as its seams delta. A plan already merged that the entry touches gets a finding for the person, never a silent edit.
- **`design edit`.** A design document the code contradicts. Collected through the build and applied by you in one commit after wave 10, from the log, the way the planning stage did it. Never mid-build.
- **`M1`.** Anything a plan's Out of scope names, plus anything the build showed is needed next. Collected for the closing report.

A dependency a builder needs and does not have is yours to decide: if the plan or the seams name the library, add it on `main` with `uv add`, commit, and tell the builder to sync; otherwise ask the person. Builders never touch `pyproject.toml`.

## The gate before wave 10

Wave 10 runs the two objectives end to end on the real seats, reads the PsyOptimal inbox, kills and restarts the process twenty times, and takes the cache measurement over twenty supervisor turns. Before preparing it, write the log's live-actions table to date, then ask the person with `AskUserQuestion`: proceed, or stop here with nine components on `main`. Include the spend so far as the gateway log records it, the GitHub token's expiry, and whether the PsyOptimal inbox holds a recent mail from `psyoptimal.com` (the second objective needs one; the integration plan's Risks say what to do when it does not).

On proceed: the integration builder gets `cori` as its database and runs its tasks in the plan's order. Its report carries the measurements; you paste them into `docs/plans/README.md` under Measurements and into the log, and the cap proposal for architecture §1 becomes a `design edit` finding. The chaos test counts as done when three runs of twenty are green by hand; wiring it into CI is M1.

## Close the stage

1. Every component is `built`, or `blocked` with the person's decision recorded.
2. Design document edits from the log's `design edit` findings, in one commit, by you.
3. The final report, standing on its own for someone who saw none of the above: the merge commits in order; the suite count on `main`; the measurements; every seams v4 entry and what it changed; every finding with its disposition; the live actions taken and their cost; what is `blocked` and why; the M1 inputs collected from the plans' Out of scope sections and the build. Say what was pushed: `main` and the `build/<slug>` branches, and nothing else.

## Resuming

If `docs/reviews/<date>-build-log.md` exists when you start, the stage is open. First `git fetch` and `git pull --rebase` on `main`: the stage has run on more than one Mac, and `origin/main` is the durable state. Then read the log, starting with its "Blocked and open" section, where a lead that hands off leaves what the next lead must do first, and `git worktree list`. Continue from the first component whose status is not `built`. A component marked `building` with a worktree present had a builder in flight: spawn a fresh builder with the ordinary brief plus one line, "The branch already holds N commits; read `PROGRESS.md` and `git log`, verify each committed task's acceptance check, and continue from the first task that is not met." A component marked `building` with no worktree was interrupted at prepare; prepare again. A branch `build/<slug>` on `origin` with no local worktree was built on another Mac: cut the worktree from `origin/build/<slug>` and check the branch's commits the same way.

A component can also be `building` in a review round: its log row counts rounds above zero, and the newest finding against it reads "returned to the builder, round N". Then the builder owes that round's items, not tasks. Compare `git -C .worktrees/build-<slug> log` with `origin/build/<slug>` and push if local is ahead. If the branch has a commit past the one the finding names, the round's fix landed: send it to a fresh validator and reviewer with the round's items named, and record the result as that round's. If it has none, spawn a fresh builder with the continuation brief and the round's items verbatim. An agent lost to a crash does not count as a round; log it as a finding and keep the round number. A validator or reviewer lost the same way is simply dispatched again.

Before the next wave, run the full suite, `black --check`, and `lint-imports` on `main` if any merge on `main` has no suite count in its log row, and record the count. Red there is fixed before anything else.

On a Mac that has not led before, bring the environment to phase 0 first: `uv run alembic upgrade head` on `cori`, `container system start`, and a `cori-base:3.14` whose `UV_NO_SYNC` is set (`container image inspect`); remove a stale tag and rerun `infra/sandbox/setup.sh`. A VPN holding the default route blocks the containers' egress; the person turns it off.

**One lead at a time.** Two leads on two Macs build the same wave twice and diverge. When you start, the log's "Blocked and open" says whether another lead handed off; if the log says a wave is `building` and you cannot tell whether its lead still runs, ask the person before you prepare it. Push `main` after every merge and every log commit, and push each `build/<slug>` branch after its builder reports, so a crash loses nothing. To hand off, write what the next lead must do first under "Blocked and open", commit, and push. The person pushes nothing for you; pushing the build's own branches and `main` is part of this stage.

## Rules that hold throughout

- You write no component code and fix no builder's test. When a build is wrong, the builder with the context fixes it. When a seam is wrong, you change the seam.
- Push only `main` and `build/<slug>` branches, as Resuming says. Never add a co-author. Commit messages say what was built or decided. `main` is green after every commit you make.
- Builders work in their worktree and nowhere else; your shell stays at the repository root. `git -C` for anything in a worktree.
- One database per builder, dropped at merge. `cori` is `main`'s and the integration wave's.
- Port 8788, the container runtime, and the Keychain are shared; the wave table keeps their users apart.
- Design documents are edited only after wave 10, in one commit, from the log. The seams are edited during the build only through a Version 4 entry.
- A skipped live test is red. A `skip` for an absent service is red on this machine.
- The person decides what is `blocked`; you decide everything the plans and seams already decided. Use `AskUserQuestion` at the gate, at a block, and nowhere else.
- The stage is done when every component is `built`, the measurements are in the index, the design edits are committed, and the report is written.
