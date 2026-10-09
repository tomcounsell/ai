# Finish-the-rebuild orchestrator prompt

Paste everything below the line into a new Claude Code session started in
`~/src/valor-rebuild` on Valor's Mac. It runs as the `/build` lead and
carries every item left between today (2026-10-09) and a cutover Tom can
schedule.

---

You are the lead orchestrator finishing the Valor rebuild. You run the
`/build` skill's machinery (`.claude/skills/build/SKILL.md`: orient, check
the machine, fan out, the per-task pipeline, release, the subagent rules)
with the task list and waves below in place of its waves table. Where this
prompt and the skill disagree, this prompt wins, and your first commit
makes the skill agree with it.

## Read first, in order, before acting

1. `CLAUDE.md`. The governance paragraph and the tests line bind
   everything.
2. `docs/mission.md` (the Mission, the Constraints, "How each kind of
   evidence is read") and `docs/persona.md`.
3. `docs/plans/valor-rebuild-feedback.md`, then Tom's ruling of 2026-10-09
   below, which you add to that file verbatim in your first commit.
4. `docs/plans/valor-rebuild.md`: "Execution", milestones 3, 4 and 6,
   "Before cutover".
5. `docs/plans/rebuild-handoff.md`, then `~/src/valor-build-notes/state.md`
   (stale since 2026-10-04: rewrite it from what the repository says).

## Tom's ruling of 2026-10-09 (the north star for this run)

Asked what ceiling a task started from Telegram should get, Tom said:

> Not only should the agent act, but it should employ agents to act and
> only come back to the user for a report on what was done, or a question
> that must be asked to resolve a major blocker. Valor, this agent, is
> using its own accounts so it is expected to act independently to send,
> build, merge without asking any human for approval. In cases where the
> agent does seek a 2nd opinion, it should spawn an advisor agent to help
> it decide then act.

What it changes, beyond the 2026-10-03 ruling that merges are Valor's:

- No `act` effect waits for a human. Sends, pushes, and merges inside a
  task's ceiling leave when the kernel decides, not on a tap.
- A task Tom starts from any channel runs at `act`.
- Tom receives reports of what was done and questions that resolve a major
  blocker. Nothing else reaches him.
- A second opinion comes from an advisor agent Valor spawns, never from
  Tom. The advisor informs; Valor decides and acts. An advisor is never a
  step work must pass, so it adds no governance.
- Unchanged: the governance paragraph, byte for byte. A new check, gate,
  hook, round, review step, or guard still needs its incident, mission
  item, and Tom's grant (`python -m core grant`). Removing an approval step
  is not adding one and needs no grant.

You live by the same rule. Never ask Tom a technical question, a merge, a
round, or a stop. When you want a second opinion, spawn an advisor
subagent (Opus, read-only, fresh context, the question plus the evidence),
take its answer as input, decide, record the decision and why under
"Decided by default" in the plan file, and keep going.

## Where things stand

Milestones 1 to 4 are merged and rolled out on Valor's Mac. The new
Telegram and email bridge jobs are installed there but `launchctl
disable`d, since the old system still owns Valor's Telegram account. The
1.5 takeover gate passed (pop-b, pso-a, pop-a). popoto 1.10.0 was released
on 2026-10-09 with a Postgres backend (popoto #759, `popoto[postgres]`,
`POPOTO_BACKEND=postgres`, PostgreSQL 18), which is the trigger the plan
names for milestone 6.

What blocks cutover, by the plan's "Before cutover" list:

| Item | State |
|---|---|
| Milestones 1 to 4 done, evidence on the branch | open evidence: Pi carries #633 to a merge, one review per harness on one candidate (m3-pi-harness.md, Rollout 3 and 4); persona before and after emulator runs and the #894 bar (m4-2-persona.md, "Emulator runs": none yet; the pso-c item does not exist in `~/src/valor-demo/items`) |
| A real task, Telegram message to a merged change Tom used | blocked: message-started tasks get ceiling `propose` (`core/intake.py`), and the broker holds every `act` for a tap (`core/broker.py`, `_request`) |
| Emulator's last sweep shows no item worse than bare | not run on the current head |
| Nightly backups, one restore rehearsed | done (`docs/machine.md`, 2026-10-01) |
| RAM measured under a working turn with bridges connected | not done |
| The old system's data the new one needs, listed with sources | not done |

Also open: the docs stage has no runner. Governance calibration run 4
failed its entry check this morning (`docs/plans/m1-4b-records.md`,
"Governance recalibration (2026-10-09)"), so `GOVERNANCE.calibrated` stays
`None` and every task's docs stage waits on a hand verdict. Valor cannot
carry a task alone until that changes.

## The work

Each code task runs the full per-task pipeline of the skill, section 5,
with its own plan file `docs/plans/<id>-<slug>.md`. Stakes are set by the
plan, and kernel, stored-data, and schema work is critique 2 and review 2.

### Track A: Valor acts on its own (kernel, stakes 2, Opus)

**A1 Autonomous act.** The broker writes an `act` effect inside the
task's ceiling straight to intent and performs it, or, for a declared
bridge type, requests its release at once. It never holds one for an
approval. Message-started tasks get ceiling `act`. Notices of effects
become reports of what left, not cards to tap. Remove the effect-approval
path the change orphans (`approve`, `release` for effects, held-effect
notices, the `pending` listing of effects) rather than leaving it unused.
Keep the governance grant path and its refusal exactly as they are. Keep
stop exactly as it is. Update every doc that says an `act` waits for Tom
(`docs/mission.md` "How metered spending is read" and the attention-log
table, `docs/persona.md` effect table, `docs/architecture.md` "An act
leaves in two steps", the bridge docs, `valor-rebuild.md` "Execution",
the skill). Leave the governance paragraph alone everywhere it appears.
The attention log keeps counting questions and feedback; it has no
authority taps left to count, and says so. The rollout restarts the
kernel; this task merges before every other core change in flight.

**A2 The advisor.** A turn that wants a second opinion asks for one and
gets an advisor's answer back in the same task, then decides. Reuse what
exists before adding anything: a child task through the objective tree
(4.1), a fresh session at the `reviewer` or `reviewer_openai` seat (the
second vendor makes it a real second opinion), or a judgement site. The
plan picks one and says why. The advisor is metered like any turn, is
optional, and never holds, refuses, or redirects work; the plan states
this, and the review's governance boolean must answer no. The persona's
conduct gains one line: when unsure, ask the advisor, then act; ask Tom
only for vision, priorities, the cost and benefit of a tradeoff in how the
company works, or something only he holds. Builds in parallel with A1 and
merges after it.

**A3 The docs stage gets its runner.** Read the run 4 record: both legs
answered caution on cases Tom labelled false. Diagnose why against Tom's
2026-10-07 ruling (a checkpoint is only a step that judges work and can
stop it or send it back). Revise the question's gloss or the case
inputs, never the labels, and run calibration again on a test database,
never the real ledger. On a passing record: land `calibrated`, register
`fresh.docs_runner`, and delete the `verdict` command and
`verdicts.MANUAL_STAGES` as m1-4c-outline.md says. If run 5 fails too,
spawn an advisor on the two failing legs before a run 6. Starts now;
merges after A1.

### Track B: milestone 6, memory (stakes 2, Opus)

**B1 Memory.** As `valor-rebuild.md` milestone 6 states, on popoto 1.10.0's
Postgres backend: `memory/` reads harness transcripts and the corrections
and exemplar streams through a port in `core/`, under its own database
role with no privilege on the kernel's tables, and writes neither stream.
Popoto's tables live in their own schema (`POPOTO_POSTGRES_SCHEMA`) or
their own database; the plan decides which and why. pgvector stays out
until a measured need (the plan names what search runs without it). The
evidence is one emulator item with a recorded preference that behaves
differently with memory on than off. The dependency change (`uv.lock`,
`pyproject.toml`) and any role or schema change roll out by hand
(backup, `uv sync`, migrate, restart). Not on the cutover path; it runs
beside everything and merges whenever it passes, after A1.

### Track C: small fixes (stakes 1, Sonnet)

**C1 Email idle drop.** The 2026-10-05 window found the IMAP IDLE
connection dies silently: no read timeout, no keepalive, `TimeoutError`
errno 60 after about 30 minutes, mail waiting up to a reissue
(`~/src/valor-build-notes/window-driver.md`, "Email watch").
`bridges/email/imap.py` and `smtp.imap_connect`. Fix the connection so a
dead path is noticed and reconnected. A bug fix, so no guard.

**C2 Lead housekeeping** (you, no pipeline). Correct stale frontmatter
(`m2-1-port.md` planned; `13fe23bd`, `fc598a81`, `9718f496` planned but
merged; `m4-3-routines-record.md` built). Close `valor-rebuild.md` "Open
items" that are settled (the host Mac is Valor's Mac; the answer keys
stand as written, m1-5-emulator-records.md). Carry the follow-ups worth a
task into this list; drop the rest with a line saying why.

### Track D: evidence runs (no code; the lead or a runner subagent)

The kernel runs one turn per machine (`docs/machine.md`, "One turn at a
time"), so kernel-carried runs on one machine take turns. Emulator runs
against their own test databases and ports may run side by side; confirm
how the slot is keyed before starting two at once, and read
`memory_pressure` before each start (16 GB is a platform fact, not a cap).

**D1 Pi carries popoto #633.** `python -m core start ... --harness pi
--model gpt-6.1` (m3-pi-harness.md, Rollout 4), carried to its merge.
Once A1 has merged it merges by itself; before that, the lead releases it.
Record its ledger summary and spending. This first real task after the
1.4c part two rollout also gives its `verify.ran` read (m4-3-routines-record.md,
end).

**D2 One review per harness.** On D1's candidate: one review at
`reviewer`, one at `reviewer_openai`, recorded side by side (Rollout 3).

**D3 Persona evidence.** Build the pso-c item for psyoptimal #894 as
m4-2-persona.md "The emulator evidence" says, then the paired before and
after runs over the baseline items and the #894 bar. Record under
m4-2-persona.md "Emulator runs". A worse item goes back to a patch of
4.2.

**D4 The cutover sweep.** The `emulator` routine's sweep (including the
`routed` arm) on the head after A1, A2, and A3 merge. It measures the
pipeline Tom will actually get. No item worse than bare is the bar.

### Track E: cutover preparation (Sonnet, read-only of the old system)

**E1 The old system's data.** From `main` (`git show main:<path>`) and
the live old system in `~/src/ai` (read only; never write its Redis, never
switch it off `main`): every project, chat ownership per Mac, operator
identity, standing settings, and anything else the new kernel needs on day
one, each with where it comes from and how it reaches the new ledger
(an existing command, a one-off import task, or by hand). Writes
`docs/plans/cutover-data.md`.

**E2 The cutover runbook draft.** After E1: what is disabled, enabled,
imported, and verified, in order, and how to go back. Cutover itself is
Tom's to schedule; this gives him a runbook he only has to date.

### The live window (after A1 and A3 merge and roll out)

Valor drives it, with stand-ins it controls (Tom is never the tester). In
it: disable the old bridge, email bridge, and worker on Valor's Mac,
enable the new jobs, and:

1. A real task, a request from Tom's real backlog for psyoptimal, popoto,
   or cuttlefish, started by message, runs from the message to a merge
   with no human approval, Valor reporting only when it is done.
2. RAM sampled across a working turn with both bridges connected; recorded
   for the "Before cutover" item.
3. An email send leaves without a tap.
4. Restore the old jobs after.

The "change Tom used" half of the cutover item needs Tom's use: when the
merge lands, send Tom one report saying what shipped and where to use it,
and record his use with `python -m core used TASK --by tom` when he does.
That report is the one message this run sends him unprompted, besides the
final report.

## Waves

| Task | Starts | Merges after |
|---|---|---|
| A1 autonomous act | now | first of the core changes |
| A2 advisor | now | A1 |
| A3 docs runner | now (diagnosis and calibration on a test database) | A1 |
| B1 memory | now | A1; hand rollout |
| C1 email idle | now | any time (bridge code only) |
| C2 housekeeping | now, by the lead | n/a |
| D1, D2 Pi #633 and paired reviews | now (D2 after D1's candidate) | n/a |
| D3 persona evidence | now | n/a |
| E1 data inventory | now | n/a |
| E2 runbook | after E1 | n/a |
| Live window | A1 and A3 rolled out | n/a |
| D4 cutover sweep | A1, A2, A3 merged | n/a |
| Readiness report | the window and D4 done | n/a |

A1, A2, A3, and B1 all touch `core/`: build in parallel, merge one at a
time in that order, each later one rebasing and rerunning its checks.

## Rules (in every subagent brief, with the skill's list)

- The skill's rules, unchanged: own worktree, test database, port block,
  `.venv`, `unset VIRTUAL_ENV`; never the real ledger outside a rollout;
  kill by PID only; never print a secret; never touch `~/src/ai` beyond
  reading it; no invented caps or safeguards; docs in plain language with
  no em or en dashes and no history words, under 600 lines.
- Report only to the lead through the final result, full report in
  `~/src/valor-build-notes/<agent name>.md`, summary under 300 words.
- `git fetch` and fast-forward before orienting, cutting worktrees, and
  every merge. Tom pushes to this branch too.
- TaskStop each agent once its report is joined; drop its databases by
  exact name; free its ports.
- Create any account or bot a window needs without asking; keys go to
  1Password vault `m-valor` by stdin, never printed.

## Finish

When every task is merged or stopped with a recorded reason, publish one
readiness report as an artifact for Tom: each "Before cutover" item with
its evidence (commit, ledger row, record section), what this run spent,
and the cutover runbook. End your session report with the artifact link
and, in under ten lines, the one decision left to Tom: the date.
