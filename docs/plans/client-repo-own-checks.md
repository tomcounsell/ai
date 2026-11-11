# A client repo's own checks run before Valor's pull request reaches its people

Request: Tom, valor team chat, 2026-11-11 07:38 UTC. A client's head of
engineering says Valor's pull requests to their backend arrive without the
repo's local checks run, so their team runs them. This file names no client:
the repo, its PRs and people are in the Brief and the private spec.

## What is wrong (checked 2026-11-11)

- **The spec runs nothing.** The private spec has `suite = "true"`.
- **The local pass is left to the client.** The repo's review skill ends in
  "Approved (pending local checks)" when the reviewer has no `.env`. Its gate
  stays red until a "local checks" follow-up posts `verdict=approved` at the
  same head. Valor's reviews of the three open PRs (2026-11-09) stopped at
  pending-local or changes-requested. The client's engineer ran the local
  pass on 2026-11-11. 78ce897a2 still allows this ("a check you cannot run
  (an `.env`...) is named in the report"). Its review.md also passes a
  pending-local verdict, which neither lacks a verdict nor asks for changes.
  Since 2026-11-11, the client's turns on its Mac get the client's own
  1Password token (task 309795bb), which reads the vault the repo's
  `.env.tpl` names.
- **No client-repo task has run since 78ce897a2.** Valor's last push or
  comment on those PRs is 2026-11-09 20:38 UTC, and 78ce897a2 landed
  2026-11-10 05:47 UTC. Its guidance was incomplete, not ignored, and never
  exercised.
- **Valor's GitHub token has only `repo` scope.** It cannot push
  `.github/workflows/*`, so one PR lacks its workflow file (that PR's
  blocker).

## What will be built

**This task** (a Valor-project task):

1. **Private spec**, edited as `docs/plans/private-projects.md` says. The
   edit is loaded with `Spec.load`, and the diff goes in the report. It runs
   CI's `tests` job:
   - `services = ["postgres"]`.
   - `setup`: a uv Python 3.11 venv with `requirements.txt` (it has
     pytest-xdist 3.6.1), plus CI's pinned import-linter and vulture.
   - `env`: every non-secret variable CI's job sets. That is `ENVIRONMENT =
     "DEV"`, `CI = "1"` (contract tests fail instead of skipping), and
     `CONTRACT_TEST_DB_URL` built from `{port}` with the password from the
     passfile. These placeholders work in `env` only (workspace.py:797).
     `CONTRACT_BASE_REF` is set to a ref that resolves in a check worktree.
   - `suite`: `pytest tests --no-cov -p no:cacheprovider -q -n auto
     --junitxml={junit}`. `{junit}` works in the suite only (checks.py:771).
   - `lint`: `lint-imports --no-cache` and CI's vulture step. Neither needs a
     placeholder.

   The builder runs it once at `dev`'s head on a local clone the way a
   check does. The bar: the only failure is the known `dev` one (the shared
   standard's map misses two importers), and no settings, import or database
   error. It also plants a layer break to show `lint` fails, and times the
   run.

   Not in the suite: `import src.main` and starting the app. Both need the
   stage secrets, and a suite step gets no client token (docs/machine.md).
   The turn's local pass runs them (item 2).
2. **Guidance**, with a test in `tests/test_kernel.py` as for converse.md:
   - `skills/sdlc/build.md`, and one sentence in `patch.md`: a
     pending-local verdict is not the end. The build runs the repo's local
     pass itself, in a fresh agent, with the client's credentials (e.g.
     `op run --env-file=.env.tpl`), and posts the follow-up its gate reads.
     It names a check as missing only after trying, with the error.
   - `skills/sdlc/review.md`, one sentence: the review of record is the
     repo's review plus the local-checks follow-up its gate reads at that
     head. A pending-local verdict alone is `changes`. This tightens
     existing guidance and adds no gate.
3. **Workflow scope.** Run `gh auth refresh -s workflow` on the client's Mac
   as Valor, completing the device code in Valor's browser session (BYOB).
   Tom is named as the blocker only if a step needs something only he holds,
   with the exact screen reached.
4. **Roll out** to the four Macs, and confirm each is on the commit.
5. **Hand-off.** The delivery asks the chat session to start one task under
   the client-repo spec (this task cannot start one) for the three PRs. That
   task is on the client's Mac, with the client's token in its turns, the
   new suite in its test stage, and the new guidance in build and review.

**That task** (its own Brief, written in this task's delivery):

- It brings the PRs to green in stack order: the base PR first, then the
  two stacked on it.
- For each PR: merge `dev`, fix the review's findings, commit the missing
  workflow file, then pass Valor's test stage.
- Then the repo's `do-review` runs in a fresh agent, and the local pass
  runs with the client token. Each is posted with its marker.
- The bar: CI `tests` and `review-gate` are green, and nothing is merged.
- The base PR carries a fix for `dev`'s broken test. The report to the
  client names it as a `dev`-wide fix their team can take on its own.
- A run over ssh is a fallback only, and the report names it if used.

## Out of scope

- No new check, gate or hook in Valor. The client's gate is theirs.
- No merge into the client's `dev` or `main`, and no stage or production
  writes.
- No change to the client's skills.

## Tests

- The prose test: build.md and patch.md name the local pass as the build's,
  with the client's credentials, and review.md treats pending-local as
  `changes`.
- The spec loads, and every spec still loads.
- The local run at `dev`'s head: pass and fail counts, only the known
  failure, and the JUnit file written. The planted layer break fails `lint`.
- Valor's suite is green.
- Done when the hand-off task's PRs show CI `tests`, `secrets` and
  `review-gate` green at their heads, reached through that task's own stages.
