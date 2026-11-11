# A client repo's own checks run before Valor's pull request reaches its people

Request: Tom, valor team chat, 2026-11-11 07:38 UTC. A client's head of
engineering says Valor's pull requests to their backend arrive without the
repo's local checks run, so their team runs them. This file names no client:
the repo, its PRs and people are in the Brief and the private spec.

## What is wrong (checked 2026-11-11)

- **The spec runs nothing.** The private spec's `suite = "true"`, so Valor's
  test stage passes every candidate without running the repo's tests.
- **The local pass is left to the client.** The repo's review skill ends
  in "Approved (pending local checks)" when the reviewer had no `.env`. The
  repo's review gate then fails until a "local checks" follow-up comment
  posts `verdict=approved` for the same head. Valor's reviews on all three
  open PRs stopped at pending-local or changes-requested (2026-11-09). The
  client's engineer ran the local pass on 2026-11-11. 78ce897a2 still allows
  this: "a check you cannot run (an `.env`...) is named in the report."
  Since 2026-11-11, the client's turns on its Mac sign in to 1Password with the
  client's own token (task 309795bb), which reads the vault the repo's
  `.env.tpl` points at. So that excuse no longer holds.
- **No client-repo task has run since 78ce897a2.** Valor's last commit or
  comment on those PRs is 2026-11-09 20:38 UTC. 78ce897a2 landed
  2026-11-10 05:47 UTC. The guidance was never exercised. It is incomplete
  (above), not ignored.
- **Valor's GitHub token has only `repo` scope** (`X-OAuth-Scopes: repo`
  on this Mac). It cannot push `.github/workflows/*`, which is why one PR's
  workflow file is missing (the reviewer's blocker).

## What will be built

1. **Private spec** (`~/Desktop/Valor/projects/<repo>.toml`, edited as
   `docs/plans/private-projects.md` says, loaded with `Spec.load`, and the
   diff put in the report). The repo's CI `tests` job, run locally:
   `services = ["postgres"]`. `setup`: a uv Python 3.11 venv with
   `requirements.txt`, plus the import-linter and vulture pins CI uses.
   `env`: `ENVIRONMENT = "DEV"`, and `CONTRACT_TEST_DB_URL` through
   `{port}` and the passfile (asyncpg reads `PGPASSFILE`).
   `suite = "... pytest tests --no-cov -p no:cacheprovider -q -n auto --junitxml={junit}"`.
   `lint`: `lint-imports --no-cache` and CI's vulture step (schema and table
   files filtered out). The builder confirms the base ref the route-coverage
   test reads (`CONTRACT_BASE_REF`) resolves in a check worktree, else sets it
   to the check's base. The builder times one run at head on this Mac. Out of
   the suite, and why: `import src.main` and the app start need the stage
   secrets, and a suite step gets no client token (docs/machine.md). They
   run in the turn, item 2.
2. **Guidance** (`skills/sdlc/build.md`'s "repository's own process"
   paragraph, and one sentence in `skills/sdlc/patch.md`, since a patch also
   pushes a new head). An "Approved (pending local checks)" verdict is not
   the end. The build runs the repo's local pass itself, in a fresh agent,
   with the client's credentials (e.g. `op run --env-file=.env.tpl`), and
   posts the follow-up its gate reads. A check is named as missing only
   after that was tried, with the error. Test: `tests/test_kernel.py` asserts
   both files say the local pass is the build's, as the converse.md test
   does.
3. **The three open PRs**, in stack order: the base PR first, then the
   two stacked on it. For each PR:
   - merge `dev` and fix the review's findings;
   - fix the broken test inherited from `dev` (the shared standard's map
     misses two new importers), in the base PR;
   - commit the missing workflow file once the token can push it;
   - get the suite and lint green in a local clone;
   - run a full review with the repo's `do-review` in a fresh agent, then
     the local pass on the client's Mac over ssh (its token never leaves that
     Mac), each posted with its marker;
   - watch CI `tests` and `review-gate` go green.

   They are pushed with `gh`/git as Valor to Valor's own branches. None is
   merged. The workflow scope: `gh auth refresh -s workflow` as Valor through
   the browser. If that needs Tom, the report names it as the one blocker,
   and that PR stays red only for it.
4. **Roll out** the guidance commit to the four Macs. Confirm each is on
   it.

## Out of scope

No new check, gate or hook in Valor; the client's gate is theirs. No
review.md change. No merge into the client's `dev` or `main`. No stage or
production writes. No change to the client's review or build skills.

## Tests

- The prose test above: build.md and patch.md each name the local pass
  as the build's, run with the client's credentials.
- The spec loads (`Spec.load`); all specs still load.
- One real suite and lint run at the head of `dev` on a local clone:
  passed and failed counts, the known `dev` failure seen, and the JUnit file
  written. Plus a negative: a planted import-layer break fails `lint`.
- Each PR: CI `tests`, `secrets` and `review-gate` green at its head, links
  in the report.
- Valor's own suite green.
