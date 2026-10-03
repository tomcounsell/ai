---
tracking: none
slug: m1-4c-review
type: build
status: planned; revised after critique round 1, awaiting round 2
critique_rounds: 2
review_rounds: 2
---

# 1.4c, part one: the review runner

Task 1.4c of [m1-4-checks.md](m1-4-checks.md), milestone 1.4 of
[valor-rebuild.md](valor-rebuild.md), in two parts. This part lands the
runner for `checks.review`: governance per hunk, then the kernel's own
rerun of the candidate's suite and lint on the host in a fresh sandboxed
checkout (1.4b's machinery), then a blind Opus session whose verdict the
runner turns into the recorded one. Once every check has a registered
runner, `python -m core verdict` is deleted. Part two,
[m1-4c-verifier.md](m1-4c-verifier.md), moves the rerun into an Apple
container VM.

It builds on the interface 1.4b leaves (m1-4b-runners.md): judgement sites
reading the mirror, `read_turn_file`, `check_harness`, `check_services`
(fresh Postgres and Redis on the task's ports), `run_setup`, the
environment digest, `suite.ran` and its `cause`, `compare`, stops raced
through `runs._stop_heard` and `os.killpg`, and the docs runner's
signature `(fresh_for, port, model=None)`. The shared design (the task
directory, the kernel mirror, fresh sessions, blind checkouts, project
specs) is m1-4-checks.md's and is not repeated here.

It merges after 1.4b and before the 1.5 gate, so the gate measures a real
review runner.

## Stakes

`critique_rounds: 2`, `review_rounds: 2`. The task changes the kernel (the
router's runner mapping, `record_check`'s review path, `record_critique`'s
leg), stored data (a `verify.ran` event, new fields on `review.decided`),
and the command line (one command deleted). A mistake either passes a
candidate on its own claim, gives the reviewer the builder's narration or
live data, or leaves a task stuck in checks with no way to Tom.

## The Done items it closes

From valor-rebuild.md, 1.4:

| Done item | What closes it here |
|---|---|
| The blind verifier: Opus in a fresh session, rerunning the tests in a fresh sandboxed checkout like the test branch's; `review.decided` carries the governance boolean | `fresh.review_runner`: seat `reviewer`, a blind checkout, the kernel's rerun at head in a fresh checkout under the check profile with fresh services, before the session reads anything |
| Fresh sessions for critique, review, and docs registered in `RUNNERS`; each stage removed from `verdict` as its runner lands, and the command deleted | review registered when governance's calibration record passes; `verdict` deleted in the commit where every check has a registered runner |
| The review and docs runners always pass `governance_from` | the runner asks governance first and passes its ids; `record_check` already refuses a session-leg review without them (1.4b) |

The Done line stands as written.

## Threat model

What the turns control:

- **The candidate's tree**: code, `conftest.py`, lockfiles, the plan file
  as committed at the candidate, test ids, exit codes, the JUnit file, and
  everything it writes in its rerun checkout.
- **The reviewer's turn**: its blind checkout, its `tmp/`, its own fresh
  service instances, and `.valor/verdict.json`.
- **The builder's session**: its clone, caches, `TMPDIR`, Claude Code
  config, and the task's live Postgres and Redis (1.4b).
- **Effect payloads** the builder's turns requested.

What the kernel must never do with any of it:

- Run candidate code outside the check profile, or past a stop. The rerun
  is 1.4b's suite run: marked, time-limited, its group killed on a stop.
- Give the reviewer the task's live services. Its turn runs inside
  `check_services`, so its `DATABASE_URL`, `PGPASSFILE`, and `REDIS_*`
  name fresh instances; nothing the builder left in a table is there.
- Give the reviewer a plan the builder edited after critique. `plan.md`
  is the plan file's bytes at `f.plan["commit"]`, read in the mirror; the
  builder's edits to it show in `diff.patch`.
- Read builder narration into the reviewer: no `done.md`, no builder
  `.valor/` file, no transcript, no commit message, no `turn.collected`
  text, no test-branch result, no docs work. The profile makes these
  unreachable. `verify.json` holds ids, counts, codes, and lint
  locations, never a message the candidate's tests printed. Effect
  payloads are quoted with `_quoted`.
- Treat the rerun's JUnit file or exit code as more than the candidate's
  claim, made in a checkout the builder never touched. The reviewer reads
  the diff, `conftest.py` included.
- Let the reviewer's verdict alone decide governance. The runner computes
  the recorded verdict from the kernel's instances and the grants.

## Design

### The review runner (`core/fresh.py`)

`review_runner(fresh_for, port, model=None) -> Runner` for
`Check.REVIEW`, the docs runner's signature:

1. Fold; `b.mirror` is required. **Governance first**:
   `ids = await judgement_sites.governance(port, ctx.dsn, ctx.task_id,
   b.base_sha, candidate)`, so a judge outage costs no suite run and no
   Opus turn. `judgement_sites.governance_outcome` over the mirror's
   hunks gives the kernel's instances, abstentions, and unjudged hunks.
2. **The rerun**, by the kernel, before any session reads anything:
   1.4b's `checks.suite(lay, b, sha=candidate, role="review")` in its own
   fresh checkout, then the spec's `lint` in the same checkout under the
   same profile and mark, with `settings.suite_timeout_s`. The base run
   is the task's usable `suite.ran` at base (kernel-run, reused as 1.4b
   reuses it); the head run is always the review's own, never the test
   branch's. `compare(base, head, removed)` gives failures,
   `failing_at_base`, and `deleted_at_head`. One `verify.ran` event:
   `where: "host"`, candidate, base, the `suite.ran` ids it read, lint
   command, lint exit, lint findings by rule, path, and line, counts by
   outcome, the three lists, duration, and `cause` (1.4b's classes). A
   `verify.ran` for the same candidate and environment digest with
   `cause` other than `kernel` is reused, so a review rerun after Tom's
   governance grant reruns nothing. A `kernel` cause records no verdict
   and the runner returns `failed`. A `commit` cause goes to the reviewer
   and never sends the branch round again.
3. **The blind session**: `workspace.fresh_dir(lay.checks /
   f"review-{candidate[:12]}")`, `blind_checkout` from the mirror at the
   candidate, and the turn run inside `check_services(lay, check_dir,
   project, task_id)` with `check_harness(lay, check_dir, ports, env,
   services=True)` built from that fresh environment. `write_inputs`:
   - `request.md`; `answers.md` (Tom's answers and feedback, quoted);
   - `plan.md`: the stakes header `critique_inputs` writes, then the plan
     file's bytes from `git show <f.plan["commit"]>:<path>` in the mirror;
   - `diff.patch` (base to candidate, from the mirror);
   - `verify.json` (the `verify.ran` fields, no free text);
   - `governance.json`: each kernel instance (id, path, start and end
     line, the hunk's added lines, granted or not), the abstentions, and
     the unjudged hunks;
   - `effects.md`: the task's held, released, and refused effects, each
     with its action kind, effect class, target, state, and payload
     fields, every value through `_quoted`.
   `runs.run_turn(..., model=SEATS["review"], state=..., fresh=...)`.
4. `read_verdict` gives: `verdict` (`pass` or `changes`, the reviewer's
   judgement of the work); `findings`, each with a kind; `governance`,
   instances the reviewer adds by path and line with summary, incident,
   and mission item; `notes` by the ids in `governance.json`;
   `predicted_failure` (0 to 1); `requirements`, one result per
   requirement. Any other `verdict` value, or a malformed file, is
   `Malformed`: no verdict, the branch reruns.
5. **The runner normalizes**, so nothing the reviewer writes can refuse
   the record:
   - a reviewer instance with no added line at its path and line (checked
     with `git.hunk_at` in the mirror) becomes a finding of kind
     `governance`, naming the path, line, and summary;
   - a note keyed by an id not in `governance.json` becomes a finding of
     kind `governance` with its text.
6. `record_check(conn, task_id, Check.REVIEW, reviewer_verdict,
   governance_from=ids, governance=specs, notes=..., findings=...,
   predicted_failure=..., requirements=..., verify=verify_event_id,
   leg="session", turn_id=..., model=..., usd_micros=...)`.

`ctx.alive()` is checked before the rerun, before the turn, and before the
write. A stop during the rerun kills its group; a stop during the turn is
`run_turn`'s. Either returns `stopped` with nothing recorded.

### The recorded verdict (`core/verdicts.py`)

For a session-leg review, `record_check` computes the verdict, as it does
for a kernel-leg test with `breadth`:

- the instances are the kernel's from `governance_from`, `_union`ed with
  the reviewer's, plus the unjudged-hunk instance when there is one;
- **`governance_refused`** whenever an instance not yet granted remains,
  keeping every finding the reviewer gave;
- otherwise the reviewer's `pass` or `changes`.

The payload keeps the reviewer's own verdict as `reviewer_verdict`, with
`predicted_failure`, `requirements`, and `verify` (the `verify.ran` event
id). The refusals for `pass` with an ungranted instance and for
`governance_refused` with none go, since no caller can produce either. A
judgement both legs failed with reruns left still refuses, as
"unanswered", so the branch has no verdict and the next run asks again.

### Registration and deleting `verdict` (`core/__main__.py`, `core/verdicts.py`)

Review routes on governance as docs does. So `runners(judgement_port)`
adds `Check.REVIEW: fresh.review_runner(_fresh_for, port)` only in a
commit where `GOVERNANCE.calibrated` is set (1.4b's landing rule). If it
is not set when this part builds, the runner code lands unregistered,
review stays on `verdict`, the build record says so, and the build does
not stop or ask Tom; a later commit registers it once governance's record
passes.

`verdict` is deleted in the commit where every check has a registered
runner: test (breadth calibrated), docs and review (governance
calibrated). In that commit:

- `verdicts.MANUAL_STAGES`, `manual_allowed`, and `_manual` are deleted;
  `leg` loses its `"manual"` default on both `record_check` and
  `record_critique` and is required, one of `session` or `kernel`;
  `_session_leg` loses its manual branch;
- the `verdict` subcommand, its parser, its usage text, `_verdict`, and
  `_instance` are deleted (1.4b already removed `--behavior` and the
  test and docs options);
- `_status_line`'s "no runner" text names the stage and says no runner
  is registered for it;
- `tests/scripted.py`'s `**MANUAL` payloads become session-leg payloads
  with a scripted turn id and model;
- `leg: manual` rows in the ledger fold as before and stay in the
  attention log.

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
- `docs/data.md`: `verify.ran` and the new `review.decided` fields.
- `core/README.md`, `tests/README.md`.

## Failure modes

| Failure | What happens |
|---|---|
| The governance judge is down | step 1 fails before the rerun; `failed`, no verdict, retried |
| Both governance legs failed, reruns left | `record_check` refuses as unanswered; `failed`, retried |
| A fresh service will not start, or a stop | `cause: kernel`; `failed` or `stopped`, nothing recorded |
| The candidate's suite hangs or its setup fails | `cause: commit`; the reviewer sees it in `verify.json` |
| A forged JUnit file or exit 0 from `conftest.py` | recorded as the candidate's claim; the reviewer reads the diff |
| `verdict.json` missing, malformed, or with another verdict value | `Malformed`, no verdict, the branch reruns |
| The reviewer names a line with no added code, or notes an unknown id | a `governance` finding; the record is written |
| The reviewer passes a diff with an ungranted kernel instance | recorded `governance_refused`, the reviewer's `pass` kept as `reviewer_verdict`; the task goes to Tom for a grant |

## Tests

Unit and router tests run with `VALOR_TEST_DB` and the scripted session.

**The recorded verdict.**

- A reviewer `pass` on a diff whose kernel instance is ungranted records
  `governance_refused` with `reviewer_verdict: "pass"`; nothing raises.
- After Tom's grant of that instance, a rerun records `pass`.
- A reviewer `changes` with an ungranted instance records
  `governance_refused` and keeps the reviewer's findings.
- A reviewer instance at a line with no added code becomes a
  `governance` finding and the record is written.
- A note keyed by an id not in `governance.json` becomes a finding.
- A reviewer line inside a kernel hunk merges into that instance (one id,
  the reviewer's summary filling an empty one); a reviewer instance
  outside every kernel hunk is added; none is removed.
- An unjudged-hunk instance makes the verdict `governance_refused`.
- `verdict: "governance_refused"` in `verdict.json` is `Malformed`.

**The runner.**

- Governance runs first: with the judge stubbed down, no suite starts and
  no turn starts.
- The rerun is the review's own: with a usable test-branch head run in
  `suite.ran`, the review still runs head once, and reuses the base.
- A review rerun after a grant reuses `verify.ran` (one head run across
  both); a `kernel` cause is not reused.
- The reviewer's database is fresh: a table the builder created in the
  task's live Postgres is absent from the reviewer's `DATABASE_URL`, and
  the live instance's port answers nothing during the turn.
- `plan.md` is the critiqued plan: a candidate that rewrites the plan
  file gives `plan.md` the bytes at `f.plan["commit"]`, and the rewrite
  shows in `diff.patch`.
- The reviewer's checkout holds no builder `.valor/`, and its profile
  refuses reading the builder clone's `.valor/done.md`, the builder's
  `TMPDIR`, `~/.claude`, and the test branch's check directory.
- `verify.json` carries no free text: a failure message the candidate's
  test prints does not appear in it.
- An effect payload holding a backtick fence and a line starting `#`
  appears quoted in `effects.md`.
- A stop during the rerun and a stop during the turn each record nothing
  and return `stopped`, with the rerun's group gone.

**Registration and deletion.**

- With `GOVERNANCE.calibrated` unset, `runners()` has no review runner
  and `verdict review` still records.
- In the deletion commit: `python -m core verdict` exits with the parser's
  unknown-command error; `record_check` and `record_critique` without
  `leg`, or with `leg="manual"`, raise; a ledger holding `leg: manual`
  review and critique rows folds to the same state.

**Live** (`VALOR_LIVE=1`, metered): one real blind Opus review of a toy
candidate through the router; and one of a toy candidate whose diff adds a
validator, which records `governance_refused` with an instance on that
hunk and reaches `pass` after a grant without a second rerun.

## Files it changes

Other tasks change `core/` too; these are the files this part touches.

| File | Change |
|---|---|
| `core/fresh.py` | `review_runner`, `review_inputs` |
| `core/verdicts.py` | the computed review verdict; new payload fields; `MANUAL_STAGES`, `manual_allowed`, `_manual`, the manual leg deleted; `record_critique`'s `leg` required; docstring |
| `core/__main__.py` | register `review_runner`; delete `verdict`, `_verdict`, `_instance`, usage text; `_status_line` |
| `core/checks.py` | the lint run after the suite; `role="review"` |
| `core/README.md` | the runner |
| `skills/sdlc/review.md`, `skills/sdlc/verdict.md` | inputs, verdict shape; no manual channel |
| `tests/test_review.py` | new |
| `tests/test_pipeline.py`, `tests/test_fresh.py`, `tests/test_attention.py`, `tests/test_judgement.py`, `tests/test_judgement_sites.py`, `tests/test_session.py`, `tests/test_workspace.py`, `tests/test_machine.py`, `tests/test_migrate_history.py`, `tests/scripted.py` | review through the router; callers of `verdict` and the manual leg moved to session or kernel legs |
| `tests/test_live_fresh.py` | the two live reviews |
| `tests/README.md` | the tests |
| `docs/sdlc-state-machine.md`, `docs/architecture.md`, `docs/data.md` | as in Docs fixed |

No migration: `verify.ran` is appended with `ledger.append` to the existing
ledger table.

## Tech debt absorbed

- The manual `verdict` command and every path that serves it, including
  `record_critique`'s manual default.
- The refusals a session-leg review could hit with no way out.

## Left out

- The rerun in a container VM: part two.
- An Opus-class reviewer from another vendor: milestone 3's route.
- Calibration and the audit sample of review verdicts: they need real
  verdicts first (architecture.md, Calibration and autonomy).

## Expected spend, as information

The two live reviews: about $3 each. A real task's review: about $2 to $4
per round. All metered; nothing refuses or pauses on money.

## Rollout

1. At merge, in the kernel checkout: `uv sync`, then restart the kernel so
   `RUNNERS` holds `review_runner` (when governance is calibrated). No
   migration.
2. The first real task after the merge runs review through the runner;
   its `verify.ran` and `review.decided` are read by hand once.

## Decided by default

Reversible calls made by the build session, not questions for Tom.

1. **The rerun's head run is the review's own, the base is shared.** The
   base is kernel-run at a commit the candidate does not control; the
   head is where independence matters.
2. **The runner computes the recorded verdict.** A reviewer cannot see
   grants or decide governance, and a refusal it cannot see would loop.
3. **`governance_refused` outranks the reviewer's `changes`**, keeping
   its findings, so Tom sees the governance instance in the same round.
4. **`verify.json` carries no free text.** Failure messages are
   candidate-written and could carry narration aimed at the reviewer; the
   reviewer can rerun any test in its checkout to read one.
5. **The reviewer gets fresh services**, not the task's live ones.
6. **Registration follows governance's calibration, and `verdict` goes
   only when every check has a runner**, so no kernel build has a stage
   with neither.
7. **The rerun needs no separate governance grant.** The review stage and
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
2. Deleting `verdict` could strand docs, and review ignored governance's
   calibration: review registers only with `GOVERNANCE.calibrated`, and
   `verdict` goes only when every check has a runner (Registration).
3. The gate would measure a hand-played review: this part reruns on the
   host with 1.4b's machinery and merges before the 1.5 gate; the Done
   line is not amended.
5. The reviewer got the builder's live database: its turn runs inside
   `check_services` (Design, step 3; Threat model; Tests).
6. The candidate controlled the plan the reviewer reads: `plan.md` is the
   bytes at `f.plan["commit"]`; effect payloads are quoted and their
   fields listed (Design, step 3).
11. "Both counts" contradicted "no test-branch result": the reviewer gets
    only the review's own rerun (Design, step 2); part two adds the
    `macos`-skipped count.
12. The runner signature lacked the judgement port: it is
    `(fresh_for, port, model=None)`.
13. The `core/verdicts.py` row was incomplete: `record_critique`'s manual
    default, `tests/scripted.py`'s `**MANUAL`, and the new `record_check`
    fields are listed; `--behavior` is 1.4b's (Files, Registration).
