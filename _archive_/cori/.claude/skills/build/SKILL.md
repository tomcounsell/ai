---
name: build
description: Build one M0 component from its plan in a dedicated worktree, one task per commit, every acceptance check run. Written for builder agents dispatched by /build-m0 with strict file ownership. Triggered by '/build <slug>'.
---

# /build

The M0 plans are written, critiqued, and reconciled against the seams. Building turns one plan into code without re-deciding what the plan and the seams decided. Several builders may run at once, so this skill is mostly rules about what you may touch and how you prove a task is done.

`$ARGUMENTS` is a component slug from `docs/plans/README.md`. The lead has created your worktree at `.worktrees/build-<slug>` on branch `build/<slug>`, and inside it an env file `.env.build` that names your own database. Work there and nowhere else.

## Read first, in this order

1. `CLAUDE.md`.
2. `docs/plans/README.md`: your row, the build order, the requirements carried from the spikes, and the Building section.
3. `docs/plans/NN-<slug>.md`, all of it. The Cascade note at the top and the Critique at the bottom are the record of how the plan got here; Design, Tasks, and Properties are what you build. Where a Findings, Seam amendments, or Critique entry quotes an older shape, the body wins.
4. `docs/plans/00-seams.md`, all of it. It is the contract your plan cites by section. The Round two and Version 3 sections at the end override anything above them.
5. The seams delta in your brief, if the lead put one there. It names what changed on main since your plan was written.
6. The sections of `docs/architecture.md` and `docs/tech-stack.md` that your plan's "What the documents say" cites. `VOICE.md` if you write a prompt.
7. The code: everything your plan lists under "What exists", the spike files it lifts, and the modules of every plan you depend on, as they are on your branch. Read that code rather than those plans; the seams and the code are the contract, the plan is the reasoning.

## Environment

- Run anything that touches Postgres, Redis, or the gateway as `uv run --env-file .env.build <command>`. The env file points at `cori_<slug>`, created by the lead and migrated to the head your branch starts from. Your migration, if the plan owns one, is applied there by your own `alembic upgrade head`. Never run `alembic` or a test against any other database.
- Postgres, Redis, the container runtime, and the six Keychain items are all on this machine. A test that skips for an absent service here is a failure to investigate, never a pass.
- Live tests (a model call, a Gmail read, a push to `yudame/cori-sandbox`) are part of your plan's acceptance and run here. The report says which ran, what they touched, and roughly what they cost.
- The gateway listens on `127.0.0.1:8788`. When a task needs it, start `uv run --env-file .env.build python -m gateway` in the background, poll until it answers, and stop it when the task is done. No other builder uses the port while you do.
- `container` names carry a random suffix and `running()` filters by prefix, so another session's containers are noise, never a failure.

## Rules

- **Ownership.** You write the files your plan's `Owns` line names, the tests it names, and nothing else, with three exceptions the build order allows: a schema module another plan owns, written verbatim from the seams when your plan says so; additions to `tests/conftest.py` (a new fixture, never a change to an existing one's behavior); and `PROGRESS.md` at the worktree root, which stays uncommitted. Design documents, the seams, the plans index, other plans, `pyproject.toml`, `uv.lock`, CI, and CODEOWNERS are the lead's. A dependency you need and do not have is a stop-and-report, never an edit.
- **Tasks in order, one commit each.** Before committing, run the task's acceptance check exactly as the plan writes it, then `uv run black .`. The commit message says what the task built and decided, in the style of `git log`. Never batch two tasks into one commit, and never commit a task whose check is red.
- **Build the plan, not your idea of it.** Where the plan and the seams disagree, the seams win and the difference goes under Deviations. Where both are silent, decide the smallest thing that works and record it as "Decided in build:" with the reason. Where the plan is wrong in a way that blocks (a signature that cannot work, a test that cannot pass as named), keep the seam's signature, build the nearest thing that does work, and record it. Never widen scope, never leave a hook or a flag for something the plan puts out of scope, never change a seam signature.
- **Tests.** Every test the plan names exists under that exact name and is green. Anything under `kernel/`, `gateway/`, or `broker/` has its invariants as the Hypothesis properties the plan states, at the example counts it names. No `xfail`. A `skip` is allowed only for an absent service, and every skip is in the report.
- **Kernel style.** Hand-written SQL, parameterized, under the role the seams name. Every insert-only table copies the grant, trigger, and RLS pattern of migration 0001. `workers/` and `adapters/` import nothing from `kernel/`. Ids are minted by the kernel. No clock value inside a cached prefix. No secret value in any row, event, log line, or Brief.
- **Writing.** No em dashes anywhere, code comments and prompts included. Cori is the system, never a character, and never names itself. Prompts you own sit on top of VOICE.md.
- **Git.** Stay on `build/<slug>`. No `git checkout` of another branch, no rebase, no push, no co-author, no force. When the lead asks for the pre-merge sync, `git merge main` into your branch, resolve what conflicts, rerun the full suite, and report.
- **Scratch.** `PROGRESS.md` holds your task list and what each task's check said. Temporary files go in your scratchpad directory, never in the repository.

## When done

Run these in the worktree and paste the output at the end of the report: `uv run --env-file .env.build pytest -q`, `uv run black --check .`, `uv run lint-imports`, `git status`, `git log --oneline main..HEAD`, `git diff --name-only main..HEAD`.

Report in this shape, under a page:

- **Built.** One line per task: what it built and what its acceptance check said.
- **Live tests.** Which ran, what they touched, what they cost.
- **Deviations.** Every place the code differs from the plan, with the plan's line and the reason.
- **Decided in build.** Every decision the plan and the seams left open.
- **Findings for the lead.** Seam drift, a document that contradicts the code, anything a later plan will hit. One line each.
- **Skips and risks.** Every skipped test with its reason, and what you could not prove.
- The six command outputs.
