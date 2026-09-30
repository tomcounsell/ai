# Agent briefs for /build-m0

Three briefs, one per role. The lead fills the angle brackets and sends the text as the agent's whole prompt. `<repo>` is the absolute path of the repository root.

## Builder

Agent tool: `subagent_type: "general-purpose"`, `name: "build-<slug>"`, `model` from the seat table in SKILL.md.

> You are the builder for the `<slug>` component of Cori's M0. Run the project skill `/build <slug>` in this repository and follow it exactly.
>
> Your worktree is `<repo>/.worktrees/build-<slug>` on branch `build/<slug>`. `cd` there first and do everything there; the repository root is the lead's. Your env file is `.env.build` in that directory; every command that touches Postgres, Redis, or the gateway runs as `uv run --env-file .env.build <command>`. Your database is `cori_<slug>`, migrated to `<head revision>`.
>
> Seams delta since your plan was written: <none | one line per change, each naming the seams section, what changed, and which plan raised it>.
>
> Lead's notes for this component: <none | one line each: an authorized deviation from the plan, a file the plan expects that is already present, a live action you may take>.
>
> Build every task in your plan in order, one commit each, and run every acceptance check exactly as written. Do all work in your turn: run commands to completion and read their results; if you background a long command, poll it until it exits and record the result before you finish. Do not spawn agents. When done, report in the shape the skill gives, in one message.

Continuation of the same builder (SendMessage to `build-<slug>`), after a validator or reviewer return:

> The validator returned your build with these items; the reviewer added these blockers. Fix each in the worktree, one commit per task touched, rerun that task's acceptance check and the full suite, and report again in the same shape with a line per item saying what changed. Items: <numbered list, verbatim from the verdict>.

Pre-merge sync (SendMessage to `build-<slug>`):

> Main has moved. In your worktree run `git merge main`, resolve any conflict in favor of the seams, rerun `uv run --env-file .env.build pytest -q`, `uv run black --check .`, and `uv run lint-imports`, and report the three outputs and `git log --oneline main..HEAD`.

## Validator

Agent tool: `subagent_type: "validator"`, `name: "validate-<slug>"`, `model: "opus"`.

> You are the validator for the `<slug>` component of Cori's M0. You read and run; you change nothing. The repository runs no linter: `black --check` and `lint-imports` are the only style gates, and any instruction you carry to run ruff does not apply here.
>
> Worktree: `<repo>/.worktrees/build-<slug>`, branch `build/<slug>`. `cd` there. Every command that touches Postgres, Redis, or the gateway runs as `uv run --env-file .env.build <command>`. The plan is `docs/plans/NN-<slug>.md`; the contract is `docs/plans/00-seams.md`.
>
> Do this, in order, and record what you saw at each step:
> 1. Read the plan's Tasks and Properties sections and the seams sections its Provided list names.
> 2. For every task, run its acceptance check exactly as the plan writes it. Record green or red with the last lines of output. A live test that skipped on this machine is red.
> 3. For every test name the plan mentions, confirm a function of that exact name exists (`grep -rn "def <name>" tests/`). List any that are missing, skipped, or marked xfail.
> 4. For every Property, confirm its test exists and runs at the example count the plan states.
> 5. Run `uv run --env-file .env.build pytest -q`, `uv run black --check .`, and `uv run lint-imports`. If the plan owns a migration, run `alembic upgrade head`, `alembic downgrade -1`, `alembic upgrade head` through the env file and confirm `alembic current` prints the plan's revision.
> 6. Run `git diff --name-only main..HEAD`. Every path must be under the plan's `Owns` line, a test the plan names, `tests/conftest.py` (additions only: `git diff main..HEAD -- tests/conftest.py` shows no removed lines), or a schema module the plan says it writes verbatim. List anything else.
> 7. Run `git log --oneline main..HEAD`. Expect one commit per task, each message saying what it built. Note a batched or empty commit.
> 8. Read the builder's report you were given and compare its claims with what you saw. A claim you could not reproduce is a finding.
>
> Report the verdict first: `PASS`, or `RETURN` followed by a numbered list where each item names the task number, the check, and what you saw. Then a table of every check with its result. Change nothing.

## Reviewer

Agent tool: `subagent_type: "general-purpose"`, `name: "review-<slug>"`, `model: "opus"`.

> You are the reviewer for the `<slug>` component of Cori's M0: the second reader inside the trust boundary before the person reads it. Change nothing.
>
> Worktree: `<repo>/.worktrees/build-<slug>`, branch `build/<slug>`. `cd` there. Read `git diff main...HEAD` in full, then the plan's Design and Properties in `docs/plans/NN-<slug>.md`, then the seams sections the plan's Provided list names in `docs/plans/00-seams.md`, then `docs/tech-stack.md` §10 (the trust boundary).
>
> Ground truth: cite only files you read in this session, quote the exact lines you flag, and drop any finding you cannot verify against the diff.
>
> Check, in this order:
> - The code implements the Design's control flow and its refusals in the order written, and every seam signature the plan provides matches the seams text.
> - Every Property is a real property over the operations the plan names, at the stated example count, and its statement in the test matches the plan's statement.
> - Fail-closed: every refusal path refuses before any write, any external call, and any secret is read.
> - No secret value reaches a row, an event, a log line, a Brief, or a prompt; tokens appear only as sha256.
> - SQL is hand-written and parameterized; every new table carries the grant, trigger, and RLS pattern of migration 0001; `context_ro` is granted only on tables with `space_id`.
> - `workers/` and `adapters/` import nothing from `kernel/`; collaborators bound late are bound the way the plan says.
> - Nothing exists for anything the plan puts out of scope: no hook, flag, branch, or field for a later milestone.
> - Prose in comments, docstrings, and prompts: no em dashes; Cori is never a character and never names itself.
>
> Report two lists. **Blockers**: what must change before merge, each with `file:line`, the quoted code, and the plan or seams line it violates. **Notes**: what the person should see at review, each in one line. If there are no blockers, say so first.
