---
tracking: none
slug: critique-issues-out
type: plan
status: done (posted as #3609, #3610, #3611, #3612 on 2026-10-07 by the lead; task 6fd4e0439ac1)
---

# Issues from Tom's SDLC autonomy critique

The output of [critique-issues.md](critique-issues.md). Tom's critique of
the old Valor (`valor-sdlc-autonomy-critique.md`, recommendations R1 to
R11, 2026-10-06) was written against `main`. Each recommendation is read
here against the rebuild. Four issues follow, ready to post, then every
point or part of a point that was dropped, with the reason.

Every citation is to branch `valor-cori-rebuild` at commit
`5c11e496d7b67ec00248750944aef6064730dc52` (written `5c11e496d:path:line`);
`main` is never cited bare. Permalink form:
`https://github.com/tomcounsell/ai/blob/5c11e496d7b67ec00248750944aef6064730dc52/<path>#L<line>`.

| Point | Outcome |
|---|---|
| R1 runtime verification | Split: issue 1 (live tests in the test check); the VERIFY stage, scenario library, and required trace dropped |
| R2 door-class merge gate | Dropped |
| R3 post-merge watch, auto revert | Split: issues 2 and 3 (designed but not built); the watch and auto revert dropped |
| R4 #3597, drain on status | Dropped, answered |
| R5 decide/record split | Dropped, answered |
| R6 outcome measurement | Split: issue 4 (record outcomes); the sampled re-review round dropped |
| R7 failure-to-guard miner | Dropped |
| R8 per-issue budget breaker | Dropped, Tom's ruling |
| R9 lint ratchets, god files | Dropped |
| R10 outer-loop clustering | Dropped |
| R11 context, not control | Dropped, answered |

---

## Issue 1

**Title:** The test check never runs the live tests, so a merged tip that broke a real turn passed every check

**Body:**

Citations are to `valor-cori-rebuild` at 5c11e496d.

*Problem.* The repository has tests that drive real turns: a real
harness, real model calls through the gateway, real fresh sessions. They
run only when `VALOR_LIVE=1` is set. The test check runs the project's
suite without it. The live tests therefore run only when the build lead
runs them by hand during a rollout, after the merge. A change that breaks
a real turn but leaves the suite green passes test, review, and docs, and
it merges.

*Evidence.*
- `5c11e496d:tests/README.md:18`: "Live tests run only with `VALOR_LIVE=1`."
  The live set covers a real turn, session, runners, fresh sessions,
  transcripts, routines, and judgement (`tests/test_live_*.py`).
- `5c11e496d:projects/valor.toml:15`: the suite is
  `uv run pytest -q -p no:cacheprovider --junitxml={junit}`, and the
  `[env]` table (lines 22 to 31) sets no `VALOR_LIVE`.
- The incident: `5c11e496d:docs/plans/m3-pi-harness.md:584-595`. Task 3b
  merged with "1007 passed, 19 skipped". The rollout's live cases then
  found that "Critique at `reviewer_openai`: failed. node aborts at start
  (signal 6)", because the turn's output files sat where the fresh profile
  denies reads. The same case had passed at 7b426b15a. The fix came after
  the merge, in 7cee5da32 ("the check profile lets a step fstat its output
  file").
- `5c11e496d:skills/sdlc/build.md:14`: web UIs already have a capability
  for seeing the result (`look`). The kernel's own behavior has no
  equivalent inside the checks.

*Mission item.* Mission item 1, "testing actual use".

*Proposed change.* Have the valor project's test check run the live tests
that need no human account: turn, session, runners, fresh, transcripts,
and judgement. The push test (it reaches GitHub) and the Telegram window tests stay out, since each also needs a variable of
its own (`VALOR_LIVE_GITHUB`, `VALOR_TELEGRAM_*`). Do this by setting `VALOR_LIVE=1` in
`projects/valor.toml`'s `[env]`, so it is not a new stage or a new
runner. The base and head runs then both execute them, and a live failure
at head that passes at base is an ordinary `red`. The spend each test
declares (`pytest.mark.spend`) is metered onto the task and shown; it
never stops it.

*Governance.* Running the repository's own tests in the existing check
asserts documented behavior. Under "Tests are not governance" in
`CLAUDE.md`, that needs no grant. If review answers the governance
boolean yes on this hunk, the incident is 3b above, the mission item is
1, and it waits for Tom's tap.

*Unverified.* Whether the check's sandbox profile lets a live turn reach
the gateway and the machine's Claude login. The first build step is to
run one live test inside a check checkout. If the profile refuses it,
that refusal goes in the issue before any profile is widened.

---

## Issue 2

**Title:** A merged kernel change reaches the running kernel only by hand; the plan says the merge does it

**Body:**

Citations are to `valor-cori-rebuild` at 5c11e496d.

*Problem.* The rebuild plan says that releasing a merge that touches
`core/` also pulls the running kernel's checkout, applies any schema
change, and restarts the kernel service, as part of the same effect.
Nothing in the code does this. Today a rollout is a list of steps the
build lead runs by hand after the merge. After takeover, Valor merges its
own kernel changes, and the running kernel then stays on the old code
until someone performs those steps. That is a gap between steps that Tom,
or a driving session, has to coordinate.

*Evidence.*
- `5c11e496d:docs/plans/valor-rebuild.md:98-101`: "Releasing a merge that
  touches `core/` also pulls that checkout, applies any schema change, and
  restarts the kernel service, as part of the same tapped effect."
- The code has no such step. `core/` and `tools/` hold no `kickstart` and
  no pull of the kernel checkout. `core/serve.py:86` names the launchd
  label only so it can write the plist.
- `5c11e496d:.claude/skills/build/SKILL.md:224-228`: a merge is to
  "fast-forward `valor-cori-rebuild` ... push, run the plan's rollout
  steps (back up first with `python -m core backup`)".
- Commit 5c11e496d itself is such a rollout, done by hand: "kernel
  restarted on the merged code, migrate, routines loaded".
- A restart is already safe for work in flight. `core/serve.py` `recover`
  ends a live turn as `interrupted`, and the scheduler starts it again
  (`5c11e496d:tests/test_serve.py:168-172`).

*Mission item.* Mission item 1 ("Tom never coordinates the gaps between
those steps"), and the constraint **Docs describe reality**.

*Proposed change.* Build the release step the plan describes. The step
applies to the kernel's own project (`projects/valor.toml`) and only when
the merged diff touches `core/`, `persona/`, `skills/`, or
`core/schema.sql`. It does four things:
1. a backup (`core/backup.py`);
2. a fast-forward of the kernel checkout;
3. `python -m core migrate`;
4. `launchctl kickstart -k` of `com.valor.kernel`.

It ledgers each step as part of the merge's outcome. It adds no new
approval, since it rides the merge effect that already exists. The
alternative is to strike the sentence from the plan and record rollout as
the lead's step. I recommend building it, because it is the last
hand-run step between a merge and the change taking effect.

---

## Issue 3

**Title:** "Autonomy shrinks automatically on evidence" is designed but not built: no audit sample, no verifier calibration

**Body:**

Citations are to `valor-cori-rebuild` at 5c11e496d.

*Problem.* The mission's constraint that autonomy shrinks automatically
on evidence has a design but no mechanism. The design calibrates the
blind verifier against a human audit sample. The verifier already records
a `predicted_failure` on every verdict, but nothing records a label to
score it against, and nothing computes false accepts, false rejects, or a
Brier score. Merges now rest on the verifier's pass: Tom ruled on
2026-10-03 that merges are Valor's call. So the leniency of that one
check is the number the constraint depends on, and nobody can see it.

*Evidence.*
- `5c11e496d:docs/architecture.md:402-409`: "A degrading series lowers the
  highest class the system may commit without Tom; raising it is only
  Tom's decision ... Serves: autonomy shrinks automatically on evidence."
- `5c11e496d:docs/architecture.md:509`: "Verifier too lenient |
  Opus-class blind reviewer, never cheaper (built); human audit sample
  (design)".
- `5c11e496d:docs/judgement-layer.md:273`: Calibration discipline. The
  judgement sites have calibration records (`python -m core calibrate`);
  the verifier does not.
- `5c11e496d:core/fresh.py:734-770`: `predicted_failure` is parsed and
  recorded. No module reads it back. A search of `core/*.py` for an audit
  label, `degrad`, or a highest class finds nothing.
- `5c11e496d:docs/mission.md:396`: "no audit sample exists."
- `5c11e496d:docs/plans/valor-rebuild-feedback.md:33`: "Merges are
  Valor's call".

*Mission item.* The constraint **Reliable stop, recovery, and correction**
("Autonomy shrinks automatically on evidence"), and Evidence "Independent
checks".

*Proposed change.* Build the evidence half only:
- An `audit.labelled` ledger row: Tom's right or wrong on one verdict, with
  provenance (`by`, `via`, `at`, `role_played`), recorded from the command
  line and the local page.
- A sample drawn as `docs/architecture.md:402-404` weights it: work that
  left the workspace and every `act` first. It is shown on the status page
  as a list Tom can work through when he chooses, never sent as a
  question.
- A calibration fold for the verifier (false accept, false reject, Brier
  with n) on the status page. Issue 4's outcomes count as labels beside
  Tom's.

*Governance.* The half that lowers the highest class without Tom is a
gate on merges. No incident exists for it yet: no merged work has been
found wrong in use. It is not proposed here. It becomes a proposal, with
Tom's grant, on the first merged task whose outcome contradicts a `pass`.

---

## Issue 4

**Title:** Nothing records what happened to merged work, so "working results in real use" stays empty

**Body:**

Citations are to `valor-cori-rebuild` at 5c11e496d.

*Problem.* A task's record ends at `merged`. Several later events go
unrecorded against it:
- whether the change was used;
- whether a later task reworked the same files;
- whether the merge was reverted;
- whether Tom gave feedback after it.

The mission names "working results in real use" as its first kind of
evidence, and it is recorded as empty. Without it, nobody can say whether
a merge went well, the verifier's calibration (issue 3) has no labels
except Tom's, and changes to the pipeline are judged by anecdote.

*Evidence.*
- `5c11e496d:docs/mission.md:146-150`: "No delivery so far has been used
  ... so this evidence is empty."
- `5c11e496d:docs/sdlc-state-machine.md:61`: `merged` takes only
  `feedback`, which goes to `patch`. Feedback after a merge is a row
  (`5c11e496d:docs/architecture.md:484-485`), but no fold reads it as an
  outcome of the merge.
- `5c11e496d:core/tasks.py:638-647`: status reports a task's latest
  delivery and its `outcome`, meaning the turn's, not what happened after
  the merge.

*Mission item.* Evidence "Working results in real use", serving Mission
item 4 (the same class of request reaching Tom less often needs a result
to read beside it).

*Proposed change.* Add a read-side fold with no new routine and no new
stage. For each merged task, `python -m core status` and the status page
show:
- feedback rows after the merge;
- later merged tasks whose diffs touch the same paths within 14 days,
  computed from the kernel mirror;
- whether the merge commit was reverted;
- a `used` mark Tom can set in one command (`python -m core used TASK`,
  with provenance).

All of it is shown beside spend and attention, and none of it refuses or
holds anything.

---

## Dropped

**R1, the VERIFY stage, the scenario library, and the trace REVIEW must
cite.** A new stage between test and review, plus a required artifact as
a merge input, is a check and an approval input. The incident in issue 1
supports running the existing live tests, not a new stage. The web half
is answered: `look` is built (`5c11e496d:docs/browser.md`,
`5c11e496d:tools/look`), and the build and patch stages tell the turn to
use it and name the screenshot in `done.md`
(`5c11e496d:skills/sdlc/build.md:14`, `5c11e496d:skills/sdlc/patch.md:13`).

**R2, a door-class merge gate.** It adds an approval step by diff class,
and nothing on this branch is an incident for it: no one-way change has
merged and harmed anything. The parts it protected are already covered:
- Stored data has a nightly `pg_dump` with a rehearsed restore
  (`5c11e496d:core/backup.py`).
- Every rollout backs up first
  (`5c11e496d:.claude/skills/build/SKILL.md:226`).
- A schema change applies without rewriting history
  (`5c11e496d:docs/plans/valor-rebuild.md`, 1.1 Done).
- Plans already raise stakes for migrations and stored data, at critique 2
  and review 2 (`5c11e496d:docs/plans/valor-rebuild.md:34-38`).

Tom also ruled that merges are Valor's call
(`5c11e496d:docs/plans/valor-rebuild-feedback.md:33`).

**R3, the post-merge watch window and automatic revert PR.** A watch that
compares signals after a merge and acts on a breach is a check with a
consequence. The old incidents (the disabled watchdog, cron deploys
everywhere) do not exist here: there is no cron deploy, and rollout is a
step (issue 2). The evidence half is issues 3 and 4. A revert is a
`patch` on the same task once evidence exists.

**R4, fix #3597 (a restart kills live work because the drain reads
status).** This is answered. There is no drain and no status field: the
kernel holds no state of its own, and on restart it ends a live turn as
`interrupted`, reaps its processes, and the scheduler starts the step
again from the ledger. The evidence is:
- `5c11e496d:docs/architecture.md:436-445`;
- `5c11e496d:core/serve.py:7-11`;
- `5c11e496d:tests/test_serve.py:168-172`, which asserts the
  `interrupted` outcome with reason "kernel restarted" and a second
  `turn.started` with the same Brief.

**R5, collapse decide and record; one state store.** This is answered.
The state is a fold over the append-only ledger, the only store
(`5c11e496d:core/machine.py:349`). The router reads verdicts and follows
a table in code, never writing a verdict
(`5c11e496d:docs/sdlc-state-machine.md:172-178`). There is no
dispatch-record step for a model to forget. Stage instructions total 196
lines across `5c11e496d:skills/sdlc/*.md` and state goals and exit
evidence, not a prose supervisor.

**R6, the weekly deep re-review of a random sample by a fresh verifier.**
That is a review round with no incident on this branch. The mission
assigns the check on the cheaper checks to Tom's audit sample
(`5c11e496d:docs/mission.md:171`), which issue 3 builds. The A/B
hill-climb is the emulator sweep, already a routine (milestone 4.3).

**R7, the failure-to-guardrail miner and a `guard` action type.** A miner
that turns clusters of mistakes into guard proposals produces governance
on a schedule, which the governance paragraph rules out: each guard needs
its own incident and Tom's tap, and "no restraint skill, no hook that
blocks hooks". The other parts are answered:
- Corrections are first-class rows rendered into every turn
  (`5c11e496d:core/corrections.py:109-110`).
- Steering is a ledger row (`message.steered`), not a destructive drain
  (`5c11e496d:docs/architecture.md:447-450`, `5c11e496d:core/intake.py:395`).
- A correction is tested by the next task behaving differently
  (`5c11e496d:docs/mission.md`, "Corrections and exemplars").

**R8, a per-issue budget and circuit breaker.** This contradicts Tom's
ruling of 2026-10-03: "spending never stops a task"
(`5c11e496d:docs/mission.md:62`), and "there is no cap"
(`5c11e496d:docs/mission.md:90-91`). Spending is metered per task and
shown. Stop (`python -m core stop`) is the brake.

**R9, ratchets on file length and lint classes, and splitting god
files.** Ratchets are gates, and no incident is named. The largest file
is `5c11e496d:core/workspace.py` at 2,261 lines, but no record shows that
size causing a mis-edit or a repeated re-read, and a line count alone is
not a defect. A lint command already runs
(`5c11e496d:projects/valor.toml:16`).

**R10, a chief-of-staff clustering reflection and auto-upvoted lanes.**
The intake it clusters (Sentry, bug labels, nightly failures, dead
letters) does not exist here. Work starts from Tom's messages through
`intake.receive` (`5c11e496d:core/intake.py:111`). That is no first need,
let alone the second one Mission item 5 requires. The critique itself
sequences this after its trust rungs.

**R11, context, not control, between PM and Dev.** This is answered.
There is no PM relaying to a Dev. Clarify, plan, build, and patch resume
one working session, and critique, review, and docs read the candidate
fresh (`5c11e496d:docs/architecture.md:223-226`). The Brief carries the
instruction, the plan path, and its commit, which are pointers, not a
hand-assembled context.
