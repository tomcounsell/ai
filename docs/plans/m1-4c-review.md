---
tracking: none
slug: m1-4c-review
type: build
status: planned; revised after critique round 2 (both rounds spent)
critique_rounds: 2
review_rounds: 2
---

# 1.4c, part one: the review runner

Task 1.4c of [m1-4-checks.md](m1-4-checks.md), milestone 1.4 of
[valor-rebuild.md](valor-rebuild.md), in two parts. This part lands the
runner for `checks.review`: governance per hunk, then the kernel's own
rerun of the candidate's suite and lint on the host in a fresh sandboxed
checkout (1.4b's machinery), then a blind Opus session in a checkout the
kernel has set up, whose verdict the runner turns into the recorded one.
`python -m core verdict` is deleted once docs has a registered runner too.
Part two, [m1-4c-verifier.md](m1-4c-verifier.md), moves the rerun into an
Apple container VM.

It builds on the interface 1.4b leaves (m1-4b-runners.md): judgement sites
reading the mirror, `read_turn_file`, `check_harness`, `check_services`
(fresh Postgres and Redis on the task's ports), `run_setup`, the seed
cache under `checks/seed/`, the environment digest, `suite.ran` and its
`cause`, `compare`, stops raced through `runs._stop_heard` and
`os.killpg`, and the docs runner's signature `(fresh_for, port,
model=None)`. The shared design (the task directory, the kernel mirror,
fresh sessions, blind checkouts, project specs) is m1-4-checks.md's and is
not repeated here.

It is built stacked on 1.4b's head, merges after 1.4b and before the 1.5
gate, so the gate measures a real review runner.

## Stakes

`critique_rounds: 2`, `review_rounds: 2`. The task changes the kernel (the
router's runner mapping, `record_check`'s review path, `record_critique`'s
leg, the `suite.ran` reuse key, `git.hunk_at`), stored data (a
`verify.ran` event, a `review.compared` event, new fields on
`review.decided`), and the command line (one command deleted). A mistake
either passes a candidate on its own claim, gives the reviewer the
builder's narration or live data, or leaves a task stuck in checks with no
way to Tom.

## The Done items it closes

From valor-rebuild.md, 1.4:

| Done item | What closes it here |
|---|---|
| The blind verifier: Opus in a fresh session, rerunning the tests in a fresh sandboxed checkout like the test branch's; `review.decided` carries the governance boolean | `fresh.review_runner`: seat `reviewer`, the kernel's own head run in a fresh checkout under the check profile with fresh services, before a blind session in its own set-up checkout reads anything |
| Fresh sessions for critique, review, and docs registered in `RUNNERS`; each stage removed from `verdict` as its runner lands, and the command deleted | review registered unconditionally; `verdict` deleted in the commit where docs is registered too |
| The review and docs runners always pass `governance_from` | the runner asks governance first and passes its ids; `record_check` already refuses a session-leg review without them (1.4b) |

The Done line stands as written.

## Threat model

What the turns control:

- **The candidate's tree**: code, `conftest.py`, lockfiles, setup inputs,
  the plan file as committed at the candidate, test ids, exit codes, the
  JUnit file, lint output, and everything it writes in its checkouts.
- **The reviewer's turn**: its checkout, its `tmp/` and cache copy, its
  own fresh service instances, and `.valor/verdict.json`, including every
  path and id it writes there.
- **The builder's session**: its clone, caches, `TMPDIR`, Claude Code
  config, and the task's live Postgres and Redis (1.4b).
- **Effect payloads** the builder's turns requested.

What the kernel must never do with any of it:

- Run candidate code outside the check profile, or past a stop. The head
  run, the reviewer checkout's setup, and the lint are 1.4b's runs:
  marked, time-limited, their group killed on a stop.
- Give the reviewer the task's live services. Its turn runs inside
  `check_services`, so its `DATABASE_URL`, `PGPASSFILE`, and `REDIS_*`
  name fresh instances; the live ones are stopped for the duration.
- Give the reviewer a plan the builder edited after critique. `plan.md`
  is the plan file's bytes at `f.plan["commit"]`, read in the mirror; the
  builder's edits show in `diff.patch`.
- Read builder narration into the reviewer: no `done.md`, no builder
  `.valor/` file, no transcript, no commit message, no `turn.collected`
  text, no test-branch result, no docs work. The profile makes these
  unreachable. `verify.json` holds ids, counts, codes, and lint
  locations, never a message the candidate's tests or lint printed.
  Effect payloads are quoted with `_quoted`.
- Treat the head run's JUnit file or exit code as more than the
  candidate's claim, made in a checkout the builder never touched.
- Pass a path the reviewer wrote to git as a pathspec. A reviewer path
  must equal one of the diff's paths, and `hunk_at` runs with
  `--literal-pathspecs`.
- Let the reviewer's verdict alone decide governance, or let a reviewer
  refusal it cannot see block the record. The runner computes the
  recorded verdict from the kernel's instances and the grants.

## Design

### The review runner (`core/fresh.py`)

`review_runner(fresh_for, port, model=None, seat="reviewer") -> Runner`.
The registered runner is `seat="reviewer"` (`SEATS["review"]`); the model
is `model or resolve_model(seat)`, and it reaches the turn through
`fresh_for`, as in `critique_runner`. `fresh_for` takes the seat as well
(`_fresh_for(prompt, checkout, model, harness, seat)`), so milestone 3's
second harness is chosen from the seat; the Claude Code harness ignores
it.

1. Fold; `b.mirror` is required. **Governance first**:
   `ids = await judgement_sites.governance(port, ctx.dsn, ctx.task_id,
   b.base_sha, candidate)`, so a judge outage costs no suite run and no
   Opus turn. `judgement_sites.governance_outcome` over the mirror's
   hunks gives the kernel's instances, abstentions, and unjudged hunks.
2. **The head run**, by the kernel, before any session reads anything:
   1.4b's `checks.suite(lay, b, sha=candidate, role="review")` in its own
   fresh checkout, with `run_setup` from a clone of the seed cache.
   **The `suite.ran` reuse key includes the role for head runs** (commit,
   role, command, environment digest; `cause` not `kernel`), so the
   review's head run and the test branch's never stand in for each
   other. Base runs keep 1.4b's key without the role and are shared. 1.4b
   is told to build the key this way; if it lands without the role, this
   part adds it in `core/checks.py`.
   Then **the lint**, when the spec has one, in the same checkout under
   the same profile and mark, with its own `settings.suite_timeout_s`:
   - `lint: null` when the spec's `lint` is `None`;
   - otherwise the exit code and duration, always;
   - for kind `python-uv`, locations parsed from ruff's concise lines
     (`path:line:col: CODE`): path, line, and rule, the message dropped;
     the command is run with `--output-format concise` appended when it
     is a `ruff check` command;
   - for any other kind, the exit code only.
   `compare(base, head, removed)` against the task's usable base run
   gives failures, `failing_at_base`, and `deleted_at_head`. One
   `verify.ran` event: `where: "host"`, candidate, base, the `suite.ran`
   ids it read, the lint record, counts by outcome, the three lists,
   duration, and `cause` (1.4b's classes). A `verify.ran` with the same
   candidate, environment digest, and `where`, and `cause` other than
   `kernel`, is reused, so a review rerun after Tom's grant reruns
   nothing, and a host result is never reused for a VM run. A `kernel`
   cause records no verdict and the runner returns `failed`; a `commit`
   cause goes to the reviewer and never sends the branch round again.
3. **The reviewer's checkout**: `workspace.fresh_dir(lay.checks /
   f"review-{candidate[:12]}")`, `blind_checkout` from the mirror at the
   candidate, a clone of `checks/seed/` into `<check_dir>/cache/`, then,
   inside `check_services(lay, check_dir, project, task_id)`, the kernel
   runs `run_setup` on the checkout under the check profile and mark, as
   1.4b's head runs do. Its exit goes on `verify.json`
   (`reviewer_setup_exit`); a failure does not stop the turn. The turn
   then runs inside the same `check_services`, with `check_harness(lay,
   check_dir, ports, env, services=True)` built from that fresh
   environment, so `uv run pytest <id>` in the checkout needs no network.
   `write_inputs`:
   - `request.md`; `answers.md` (Tom's answers and feedback, quoted);
   - `plan.md`: the stakes header `critique_inputs` writes, then the plan
     file's bytes from `git show <f.plan["commit"]>:<path>` in the mirror;
   - `diff.patch` (base to candidate, from the mirror);
   - `verify.json` (the `verify.ran` fields and `reviewer_setup_exit`, no
     free text);
   - `governance.json`: each kernel instance (id, path, start and end
     line, the hunk's added lines, granted or not), the abstentions, and
     the unjudged hunks;
   - `effects.md`: the task's held, released, and refused effects, each
     with its action kind, effect class, target, state, and payload
     fields, every value through `_quoted`.
   `runs.run_turn(ctx.gateway, ctx.task_id, fresh_for(prompt(files),
   checkout, model, harness, seat), dsn=ctx.dsn, state=..., fresh="review")`.
4. `read_verdict` gives: `verdict` (`pass` or `changes`, the reviewer's
   judgement of the work); `findings`, each with a kind; `governance`,
   instances the reviewer adds by path and line with summary, incident,
   and mission item; `notes` by the ids in `governance.json`;
   `predicted_failure` (0 to 1); `requirements`, one result per
   requirement. Any other `verdict` value, or a malformed file, is
   `Malformed`: no verdict, the branch reruns.
5. **The runner normalizes**, so nothing the reviewer writes can refuse
   the record:
   - a reviewer instance whose `path` is not exactly one of
     `git.diff_paths(mirror, base, candidate)`, or with no added line at
     that line, becomes a finding of kind `governance` naming the path,
     line, and summary;
   - a note keyed by an id not in `governance.json` becomes a finding of
     kind `governance` with its text.
6. At the registered seat: `record_check(conn, task_id, Check.REVIEW,
   reviewer_verdict, governance_from=ids, governance=specs, notes=...,
   findings=..., predicted_failure=..., requirements=...,
   verify=verify_event_id, leg="session", turn_id=..., model=...,
   usd_micros=...)`.
   **At any other seat**, the run reuses the candidate's governance
   judgements and `verify.ran` (asking and running nothing it has),
   computes the same verdict, and appends `review.compared` (seat, model,
   candidate, the computed verdict, `reviewer_verdict`, findings,
   instances, turn id, `usd_micros`) as information. It never writes
   `review.decided` and never moves the task. It runs on the latest
   candidate whatever the task's state. Milestone 3 adds the seat's
   harness and the call.

`ctx.alive()` is checked before the head run, before the setup, before
the turn, and before the write. A stop during a run kills its group; a
stop during the turn is `run_turn`'s. Either returns `stopped` with
nothing recorded.

### The recorded verdict (`core/verdicts.py`)

For a session-leg review, `record_check` computes the verdict, as it does
for a kernel-leg test with `breadth`:

- the instances are the kernel's from `governance_from`, `_union`ed with
  the reviewer's, plus the unjudged-hunk instance when there is one;
- on the reviewer's **`changes`**: the verdict is `changes`. The instances
  stay in `governance`, and each one not yet granted is added as a
  finding of kind `governance`, so the patch sees it. Tom is not asked to
  grant code about to change; the merge stays blocked anyway, because
  `ensure_merge` returns nothing while an instance is ungranted and
  delivery row 2 needs every instance granted;
- on the reviewer's **`pass`**: `governance_refused` when an instance not
  yet granted remains, else `pass`.

The payload keeps `reviewer_verdict`, `predicted_failure`,
`requirements`, and `verify` (the `verify.ran` event id). The refusals for
`pass` with an ungranted instance and for `governance_refused` with none
stay for `leg="manual"` until `verdict` is deleted. A judgement both legs
failed with reruns left still refuses, as "unanswered", so the branch has
no verdict and the next run asks again.

`git.hunk_at` runs git with `--literal-pathspecs`, so a path is only ever
a file name.

### Registration and deleting `verdict` (`core/__main__.py`, `core/verdicts.py`)

`runners(judgement_port)` adds `Check.REVIEW:
fresh.review_runner(_fresh_for, port)` unconditionally. Governance's
calibration record is information: kernel instances route as CLAUDE.md's
paragraph says (a yes with no grant is a refused merge), whatever the
entry check says. An uncalibrated judge on the review path can only add
caution, at the cost of a tap, and the reviewer still names its own
instances.

`verdict` is deleted in the commit where docs is registered too. If 1.4b
registers docs unconditionally, that is this part's registering commit.
In it:

- `verdicts.MANUAL_STAGES`, `manual_allowed`, `_manual`, and the
  manual-leg refusals are deleted; `leg` loses its `"manual"` default on
  both `record_check` and `record_critique` and is required, one of
  `session` or `kernel`; `_session_leg` loses its manual branch;
- the `verdict` subcommand, its parser, its usage text, `_verdict`, and
  `_instance` are deleted (1.4b removes `--behavior` and the test and
  docs options);
- `_status_line`'s "no runner" text names the stage and says no runner is
  registered for it;
- `tests/scripted.py`'s `**MANUAL` payloads and its
  `checks(..., review="governance_refused")` callers become session-leg
  payloads with a scripted turn id and model;
- `leg: manual` rows in the ledger fold as before and stay in the
  attention log.

If docs is still manual when this part lands, review is registered, the
manual path stays for docs alone, and the build record says so.

### Skills

`skills/sdlc/review.md` gains the inputs list and the `verdict.json` shape
(step 4), and says the recorded verdict is computed from governance.
`skills/sdlc/verdict.md` drops the manual channel when `verdict` goes.

### Docs fixed in the same build

- `docs/sdlc-state-machine.md`, checks.review and "What exists": the
  review runner and how the verdict is computed; no manual command once
  deleted.
- `docs/architecture.md`, Verification: the verifier as built (the
  kernel's rerun on the host, then the blind session); the container is
  part two's.
- `docs/data.md`: `verify.ran`, `review.compared`, and the new
  `review.decided` fields.
- `docs/plans/m1-4-checks.md`: the status line, the split row, the
  Done-items row ("rerunning the tests in an Apple container"), and the
  1.4c outline point at the two part files and say the review rerun is on
  the host until part two.
- `docs/plans/valor-rebuild.md`, line 257: "task 1.4c" becomes "task
  1.4c, part two".
- `core/README.md`, `tests/README.md`.

## Failure modes

| Failure | What happens |
|---|---|
| The governance judge is down | step 1 fails before any run; `failed`, no verdict, retried |
| Both governance legs failed, reruns left | `record_check` refuses as unanswered; `failed`, retried |
| A fresh service will not start, or a stop | `cause: kernel`; `failed` or `stopped`, nothing recorded |
| The candidate's suite hangs or its setup fails | `cause: commit`; the reviewer sees it in `verify.json` |
| The reviewer checkout's setup fails | `reviewer_setup_exit` says so; the turn runs |
| A forged JUnit file or exit 0 from `conftest.py` | recorded as the candidate's claim; the reviewer reads the diff |
| `verdict.json` missing, malformed, or with another verdict value | `Malformed`, no verdict, the branch reruns |
| The reviewer names a path outside the diff, a line with no added code, or an unknown id | a `governance` finding; the record is written |
| The reviewer passes a diff with an ungranted kernel instance | `governance_refused`, `reviewer_verdict: "pass"` kept; the task goes to Tom for a grant |
| The reviewer asks for changes on a diff with an ungranted instance | `changes`, the instance a finding; Tom is asked after the patch, on the code that stays |

## Tests

Unit and router tests run with `VALOR_TEST_DB` and the scripted session.

**The recorded verdict.**

- A reviewer `pass` on a diff whose kernel instance is ungranted records
  `governance_refused` with `reviewer_verdict: "pass"`; nothing raises.
  After Tom's grant of that instance, a rerun records `pass` and reuses
  `verify.ran`.
- A reviewer `changes` with an ungranted instance records `changes`, the
  instance in `governance` and as a `governance` finding; the task goes to
  patch, not to Tom.
- A reviewer instance at a line with no added code, and one with path
  `"."`, `":(top)"`, or `":(glob)**"`, each become a `governance` finding;
  `hunk_at` with `"."` finds no hunk.
- A note keyed by an id not in `governance.json` becomes a finding.
- A reviewer line inside a kernel hunk merges into that instance (one id,
  the reviewer's summary filling an empty one); a reviewer instance
  outside every kernel hunk is added; none is removed.
- An unjudged-hunk instance with a reviewer `pass` makes the verdict
  `governance_refused`.
- `verdict: "governance_refused"` in `verdict.json` is `Malformed`.
- Before the deletion commit, `leg="manual"` keeps both refusals.

**The runner.**

- Governance runs first: with the judge stubbed down, no run and no turn
  starts.
- The head run is the review's own: with a usable test-branch head run in
  `suite.ran`, the review runs head once and reuses the base; a later
  test branch on the same candidate runs its own head.
- A `verify.ran` with `where: "vm"` is not reused for a host run.
- The reviewer's database is fresh: during the turn, the task's live
  Postgres pid is gone and the reviewer's `DATABASE_URL` has no table
  the builder created; after the turn, the live instance is back with
  the table.
- The reviewer's checkout is set up: with `UV_OFFLINE=1` in its
  environment, `uv run pytest <id>` in the checkout passes.
- `plan.md` is the critiqued plan: a candidate that rewrites the plan
  file gives `plan.md` the bytes at `f.plan["commit"]`, and the rewrite
  shows in `diff.patch`.
- The reviewer's checkout holds no builder `.valor/`, and its profile
  refuses reading the builder clone's `.valor/done.md`, the builder's
  `TMPDIR`, `~/.claude`, and the test branch's check directory.
- `verify.json` carries no free text: a failure message the candidate's
  test prints, and a ruff message, do not appear in it.
- The lint record: a spec with no `lint` gives `lint: null`; ruff concise
  output gives path, line, and rule; a lint of another kind gives the
  exit code only.
- An effect payload holding a backtick fence and a line starting `#`
  appears quoted in `effects.md`.
- A run at seat `reviewer_openai` (with a scripted harness) appends
  `review.compared`, writes no `review.decided`, asks no governance and
  runs no suite already present, and leaves the task's state unchanged.
- A stop during the head run, the setup, and the turn each record nothing
  and return `stopped`, with the run's group gone.

**Registration and deletion.**

- With `GOVERNANCE.calibrated` unset, `runners()` holds the review runner.
- In the deletion commit: `python -m core verdict` exits with the parser's
  unknown-command error; `record_check` and `record_critique` without
  `leg`, or with `leg="manual"`, raise; a ledger holding `leg: manual`
  review and critique rows folds to the same state.

**Live** (`VALOR_LIVE=1`, metered): one real blind Opus review of a toy
candidate through the router; and one of a toy candidate whose diff adds a
validator, which comes back with an instance on that hunk (as
`governance_refused` on a pass, or as a finding on `changes`) and, after a
grant, reaches `pass` without a second head run.

## Files it changes

Other tasks change `core/` too; these are the files this part touches.

| File | Change |
|---|---|
| `core/fresh.py` | `review_runner` with `seat`, `review_inputs`, `review.compared` |
| `core/verdicts.py` | the computed session-leg review verdict; new payload fields; at deletion, `MANUAL_STAGES`, `manual_allowed`, `_manual`, the manual leg and its refusals; `record_critique`'s `leg` required; docstring |
| `core/__main__.py` | register `review_runner`; `_fresh_for` takes the seat; at deletion, `verdict`, `_verdict`, `_instance`, usage text; `_status_line` |
| `core/checks.py` | the head run's reuse key includes the role (if 1.4b lands without it); the lint run and its parser; `role="review"` |
| `core/git.py` | `hunk_at` with `--literal-pathspecs` |
| `core/README.md` | the runner |
| `skills/sdlc/review.md`, `skills/sdlc/verdict.md` | inputs, verdict shape; no manual channel |
| `tests/test_review.py` | new |
| `tests/test_pipeline.py`, `tests/test_fresh.py`, `tests/test_attention.py`, `tests/test_judgement.py`, `tests/test_judgement_sites.py`, `tests/test_session.py`, `tests/test_workspace.py`, `tests/test_machine.py`, `tests/test_migrate_history.py`, `tests/test_git_history.py`, `tests/scripted.py` | review through the router; callers of `verdict` and the manual leg moved to session or kernel legs; the literal pathspec |
| `tests/test_live_fresh.py` | the two live reviews |
| `tests/README.md` | the tests |
| `docs/sdlc-state-machine.md`, `docs/architecture.md`, `docs/data.md`, `docs/plans/m1-4-checks.md`, `docs/plans/valor-rebuild.md` | as in Docs fixed |

No migration: `verify.ran` and `review.compared` are appended with
`ledger.append` to the existing ledger table.

## Tech debt absorbed

- The manual `verdict` command and every path that serves it, including
  `record_critique`'s manual default.
- The refusals a session-leg review could hit with no way out.
- `hunk_at`'s pathspec reading of a path.

## Left out

- The rerun in a container VM: part two.
- The second-vendor reviewer's harness and the call that runs it:
  milestone 3 (this part leaves the seat and `review.compared`).
- Calibration and the audit sample of review verdicts: they need real
  verdicts first (architecture.md, Calibration and autonomy).

## Expected spend, as information

The two live reviews: about $3 each. A real task's review: about $2 to $4
per round. All metered; nothing refuses or pauses on money.

## Rollout

1. Built stacked on 1.4b's head once its build is reported.
2. At merge, in the kernel checkout: `uv sync`, then restart the kernel so
   `RUNNERS` holds `review_runner`. No migration.
3. The first real task after the merge runs review through the runner;
   its `verify.ran` and `review.decided` are read by hand once.

## Decided by default

Reversible calls made by the build session, not questions for Tom.

1. **The head run is the review's own, the base is shared.** The base is
   kernel-run at a commit the candidate does not control; the head is
   where independence matters.
2. **The runner computes the recorded verdict.** A reviewer cannot see
   grants, and a refusal it cannot see would loop.
3. **The reviewer's `changes` outranks `governance_refused`.** Tom's tap
   is not spent on code about to change; the merge stays blocked until
   every instance is granted.
4. **`verify.json` carries no free text.** Failure and lint messages are
   candidate-controlled and could carry narration aimed at the reviewer;
   the reviewer's checkout is set up, so it can rerun any test to read
   one.
5. **The reviewer gets fresh services and a set-up checkout.**
6. **Review is registered unconditionally.** Governance's calibration is
   information, under Tom's rule against invented safeguards, as for the
   test runner and breadth. `verdict` goes once docs is registered too.
7. **A run at another seat is information (`review.compared`)**, never a
   second `review.decided`, so the join reads one verdict per candidate.
8. **Lint locations only for a kind the kernel can parse**, with the
   message dropped; otherwise the exit code.
9. **The rerun needs no separate governance grant.** The review stage and
   its rerun are named by valor-rebuild.md's granted checkpoints
   ("critique and review loops"), the 1.4 Done line, and architecture.md's
   Verification. The rerun's result decides nothing alone: a `commit`
   cause is input to the reviewer and a `kernel` cause records nothing. If
   a rerun result ever forced `changes` on its own, that would be a new
   gate and would need a grant.

## Questions for Tom

None. No identity or credential choice is involved, and the intent
questions are answered by the docs above.

## Critique round 1 (of 2): revise

The report covered both parts. The lead split the task: findings 1, 2, 3,
5, 6, 11, 12, and 13 are handled here; 4, 7, 8, 9, 10, 14, 15, 16, and 17
in m1-4c-verifier.md.

1. A reviewer and the kernel disagreeing on governance left the task stuck:
   `governance.json` is an input, the runner normalizes reviewer instances
   and notes into findings, and `record_check` computes the verdict, with
   a test per case (Design, The recorded verdict; Tests).
2. Deleting `verdict` could strand docs: `verdict` goes only with docs
   registered (Registration; registration itself settled in round 2).
3. The gate would measure a hand-played review: this part reruns on the
   host with 1.4b's machinery and merges before the 1.5 gate; the Done
   line is not amended.
5. The reviewer got the builder's live database: its turn runs inside
   `check_services` (Design, step 3; Threat model; Tests).
6. The candidate controlled the plan the reviewer reads: `plan.md` is the
   bytes at `f.plan["commit"]`; effect payloads are quoted and their
   fields listed (Design, step 3).
11. "Both counts" contradicted "no test-branch result": the reviewer gets
    only the review's own run (Design, step 2); part two adds the
    `macos`-skipped count.
12. The runner signature lacked the judgement port: it is
    `(fresh_for, port, model=None, seat="reviewer")`.
13. The `core/verdicts.py` row was incomplete: `record_critique`'s manual
    default, `tests/scripted.py`'s `**MANUAL`, and the new `record_check`
    fields are listed; `--behavior` is 1.4b's (Files, Registration).

## Critique round 2 (of 2): revise

Both rounds are spent; every finding is folded in. Findings 1 to 11 are
this part's; 12 is part two's.

1. Review waited on governance's calibration, so the gate could still
   measure a hand-played review: review is registered unconditionally,
   calibration is information (the lead's call under Tom's rule against
   invented safeguards); `verdict` goes once docs is registered
   (Registration; Decided by default 6).
2. The advisory lock: part two's.
3. The head run's reuse key lacked the role: it includes the role, base
   runs stay shared, and 1.4b is told (Design, step 2; Files).
4. No seat and no path for a second seat's review: `seat="reviewer"`,
   `fresh_for` takes the seat, the model is `resolve_model(seat)` passed
   through `fresh_for`, and another seat appends `review.compared`
   (Design, step 6).
5. `governance_refused` outranked `changes`: `changes` is recorded with
   the ungranted instances as findings; `governance_refused` only on a
   `pass` (The recorded verdict; Decided by default 3).
6. The reviewer's checkout was never set up: seed cache cloned and
   `run_setup` run by the kernel before the turn, with an offline test
   (Design, step 3).
7. The live-port test was false: it asserts the live pid gone, no builder
   table, and the live instance back afterwards (Tests).
8. Docs wrong between the merges: m1-4-checks.md's rows and outline and
   valor-rebuild.md:257 are fixed in this part (Docs fixed).
9. A reviewer path reached git as a pathspec: it must equal a diff path,
   and `hunk_at` uses `--literal-pathspecs`, tested with `"."` (Design,
   step 5; The recorded verdict).
10. The lint record: `null` with no command, exit and duration always,
    ruff concise locations for `python-uv` without the message, exit only
    otherwise, under its own `suite_timeout_s` (Design, step 2).
11. The manual-leg refusals: the verdict is computed for the session leg
    only, and both refusals stay for `leg="manual"` until deletion (The
    recorded verdict).
