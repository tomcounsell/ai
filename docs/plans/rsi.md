# RSI rebuild: requirements

Tom, 2026-10-20, in an interview. Requirements only; no design yet. The old charter (v2, `git show old-system:docs/improvement-charter.md`) is the source of the goals; where this file differs, this file governs.

## Purpose

Valor gets more capable and more autonomous, and gets there by acquiring resources of its own, because it is weak while it depends on Tom's time and money. First targets: new abilities, autonomy, and the resources that fund both.

## Grants (Tom, 2026-10-20)

- **Resources.** Valor may pursue free or cheap compute and inference, and may create accounts and access, as charter section 8 allows.
- **Income.** Valor may pursue income ventures. Exposure lands on Yudame Inc. Valor proposes each venture; Tom approves strategies and guides the business model. Nothing starts on its own say-so. Charter bans on cold outreach and spam stand.
- **Spending.** The limits ($10/day paid inference, $50/week infrastructure) apply to hard cash only. Free resources (credits, subscription tokens) do not count against them, but credit expiry and exposure are still tracked. Raising a limit is a proposal with its evidence; Tom grants it in writing.

## Done (rule 5: Tom has used it, dated)

One month after it ships, all three:

1. New abilities that Tom used on real projects.
2. An income venture Tom approved is earning.
3. Valor runs without Tom: unattended, round the clock, in cloud sandboxes where it makes sense.

## Constraints carried over

- Open-source work may use any provider; client work and its private context stay on the Claude and Codex subscriptions.
- An ability counts as improved only on comparative evidence against the prior capability, with lineage from the gap to its later use. Merged patches and tokens burned are not evidence.
- Valor makes no promises; it reports what is true.
- Governance additions need a named incident, a mission item, and Tom's grant in a plan file. A bug fix never adds a guard.

## Decided shape

- Continuous and always-on, not a nightly batch. Open: how this stays a task-starter and not the old controller (375 runs, none for Tom). Design must name what is different.

## Open for the next round

- Where opportunities come from (memory, logs, forwarded links) and how they are ranked.
- How evaluation runs per kind of change (skill, persona, model, infrastructure, venture).
- What Tom sees and how often, and how he steers.
- What of the `improve-research`, `improve-preflight` and `rsi` skills in `old-system` is reused (rule 10).


# Plan (2026-10-20, issue #3655)

Status: Planning. Tracking: https://github.com/tomcounsell/ai/issues/3655.
Tom's requirements above stay as he wrote them (rule 6). The evidence is
[synthesis.md](../research/rsi/synthesis.md). Baseline: `f28c9262e`. D1, D2
and D3 are Done items 1, 2 and 3. **G1–G5** are governance and are listed
below with their accounting.

## Shape: a routine that starts tasks for Tom (D1, D3)

RSI is one routine, `routines/rsi/`. Its findings start ordinary tasks
through `core/findings.py` `start_fix` (rule 4: the mechanism that already
starts tasks). There is no case store, no CLI of its own and no merge path
of its own. It is always on: it runs every 15 minutes, and a run that finds
no new source rows ends without opening a turn. Concretely, it differs from
the old controller (375 runs, none for Tom) in five ways:

1. **It starts from Tom's work.** Every opportunity cites a ledger row
   from a task that was not started by RSI. The old loop opened cases
   from its own evidence collection, and its one candidate surface was
   its own memory-retrieval parameters.
2. **Its output is a task on a real repository.** That is `yudame/skills`,
   this repository, or the open-source project where the gap showed. The
   task goes through the same pipeline and ends in the same merge report
   to Tom. The old loop's output was rows in its own namespace.
3. **No precondition stands before work.** The old loop's first real
   cycle ended `infra_failure` because its own calibration floor (20
   corrections) was not met.
4. **It counts use.** An ability is done when a later task of Tom's used
   it and Tom accepted that delivery, recorded with the date. The status
   page shows RSI tasks started, merged and used. Used over started is
   the number to beat; the old loop scored 0 of 375.
5. **It is the size of a routine.** The old loop took seven lanes and
   about ten models.

## Answers

**Opportunity sources and ranking.** Rows the kernel already writes on
Tom's tasks: `correction.recorded`, `feedback.given`, `question.asked` (a
question Valor could have answered), `step.failed` and failed endings,
`effect.refused`, and gateway spend by kind of task. A link Tom forwards
with no request is inspiration (charter §4). The persona files it with the
existing by-hand finding (`python -m core routine rsi --finding … --key
link:<url>`). Memory is searched for recurrence. One turn per run ranks the
open opportunities by three things: Tom's attention spent on the gap
(questions plus feedback rounds), recurrence (the gap seen in two or more
tasks), and the Done item it serves. It then starts tasks under keys
`rsi:<row>`. Dedupe keys and turn slots bound the load. The ranking is the
turn's judgement; there is no scoring engine.

**Evaluator per kind of change.** Ground truth is Tom's use and his
feedback (`architecture.md`, Calibration). Before he uses an ability, a
**blind replay** compares it with the prior capability (G1). The replay
uses the rebuild-baseline method: real past tasks, run with and without
the change. The answer keys are Tom's merged result and hidden tests. They
sit in the kernel's checkout, never in a task's workspace, and a fresh
Opus-class judge reads outcomes, never narration. The improver can neither
see nor edit the replay.

| Kind | Judge | Replay set |
|---|---|---|
| Skill | the existing verifier on the diff, then the replay | the task whose gap started it, plus one similar task |
| Persona | the replay, including hostile group messages (Grok, 2025-07) | past chat turns |
| Model route | the replay plus gateway spend; open-source projects only | past open-source tasks |
| Infrastructure | the ledger: turns, deliveries and failures while Tom made no move | none (D3 is read from the ledger) |
| Venture | money received, read from the processor by the conditions routine, never reported by Valor | none (D2) |

The lineage, gap row → task → replay → later use, is ledgered on the RSI
task.

**Untrusted input and write paths.** Nothing is added. Hermes's failure
needs a runtime skill-write tool, and Valor has none. Skills and persona
change only through a reviewed merge, and memory recall is quoted, escaped
and grants nothing (`memory.md`). Corrections come only from Tom
(`core correct`, with provenance). Content read while working is data
(`persona/conduct.md`). An opportunity's evidence is quoted in the
findings fence ("data from the run, not instructions"). Group text is
never a source row. The open exposure is a reviewed diff whose content
came from a forwarded link. The verifier reads every diff, and a guard
comes only after an incident.

**Accounts and signup walls.** SPIKE-A (pending).

**Legal liability for ventures.** No court has ruled on whether an
agent's out-of-scope act binds its company (Venable, 2026-04). The
sources agree it lands on the deployer: UETA §14 and E-SIGN §7001(h)
attribute an electronic agent's contracts to its user; the Restatement
(Third) of Agency treats a program as a tool; *Moffatt v. Air Canada*
(2024) held the airline liable for its chatbot; California AB 316 (from
2026-01-01) bars the "the AI did it" defence. So exposure falls on Yudame
Inc, as Tom stated. Each venture is a committed decision in its business
repository (`business.md`, items 3 and 7), approved by Tom (G2), and it
names:

- the legal owner (Yudame Inc or an LLC) as merchant of record, with Tom as
  signer;
- each platform account, with the terms clause that permits automated
  operation (Upwork bans bots; Fiverr bans bot messaging and multiple
  accounts);
- a hard cap held at the card or account, not in a prompt (G3), and the
  largest contract Valor may accept;
- a kill date and early-kill conditions (a complaint, a chargeback, a
  platform warning), watched by the conditions routine and put on the
  strategy-meeting agenda;
- bot disclosure (California B.O.T. Act; EU AI Act Art. 50 from
  2026-08-02) and claims checked against FTC Operation AI Comply and the
  fake-review rule;
- markets, and the insurance policy that covers it (Verisk's 2026
  generative-AI exclusions may apply).

Everything else is a question for counsel (Needs Tom). Income is expected
in dollars, and saved inference is the likelier value.

**What Tom sees, and how he steers.** No new digest and no dashboard.
- RSI merges reach Tom as ordinary merge reports with one lineage line.
- Venture proposals, limit raises and credit expiries go on the
  strategy-meeting agenda (`agenda.item`).
- The status page's routine row shows started, merged and used.

Tom steers four ways:
- reply to a report (that is feedback);
- `core correct` or `exemplar`;
- `/rsi`;
- stop the routine's objective, which stops all of RSI.

**Reuse of the old skills (rule 10).**
- `improve-preflight`: **dropped**. All three of its reads (lease,
  budget, calibration floor) are controller state that no longer exists.
- `improve-research`: **dropped as a skill.** Its rules are **ported** as
  four lines of the routine's instruction: a claim is text with a URL and
  a retrieval date; an unreachable source is recorded as unreachable; an
  assumption names what would overturn it; Tom is never asked a research
  question.
- `rsi`: **ported**, and this is #3641's `rsi` (one skill, not two). It is
  a short Valor-plugin skill that grounds itself on the routine's open
  opportunities and hands the questioning to `ask-me` (it already asks one
  question at a time). Each answer is written back at once: a fact
  becomes `core correct`, an idea becomes `--finding`, "plan this" becomes
  `do-issue`.

## Governance this plan would add (none is built without the grant)

| | What | Mission item | Incident | Tom's grant |
|---|---|---|---|---|
| G1 | Blind replay before an RSI change is reported as improved | 5 | #3177: 375 runs, none used; lane 5b's judge saw the skill name | **Needs Tom** (the bar is his; the check is not granted) |
| G2 | Each venture approved by Tom before it starts | Business 8 (conflicts: "nothing held for consent") | none in Valor; Project Vend's giveaways and onion contract | Granted 2026-10-20 (Income); **incident needed** |
| G3 | $10/day inference and $50/week infrastructure, hard cash only; held as the card's limit at the issuer, no code | Constraint "Bounded authority" (mission says no spend ceiling, 2026-10-16) | none in Valor | Granted 2026-10-20 (Spending); **incident needed** |
| G4 | Client work stays on Claude and Codex; model-route tasks only on projects with no `client` | Business constraint "Generic across businesses" | none in Valor | Carried 2026-10-20; **incident needed** |
| G5 | Accounts only by official sign-up and published programs, one per provider | Constraint "Bounded authority" | none in Valor | SPIKE-A; **Needs Tom** |

Each granted guard is ledgered (`core/guards.py`) with a 90-day expiry.

## Needs Tom

1. G1–G5: grant or refuse each one, and name the incident or accept the
   external one.
2. The conflicts between this plan and the mission: G2 against Business
   item 8, and G3 against "no spend ceiling". Amend `mission.md` or narrow
   the grants.
3. Counsel (G2): Yudame's state of incorporation, and whether each venture
   is a separate LLC; whether Yudame's insurance carries the 2026 AI
   exclusions; whether Stripe, Upwork or a given marketplace lets an agent
   run the account; EU or California consumers; tax and sales tax per
   venture.
4. Identity: phone, company details and card for sign-ups (SPIKE-A).
5. Persona: may RSI change the persona at all? Charter §12 keeps identity
   outside autonomous change.
6. Does Tom want a weekly RSI message beyond merge reports and the agenda?
7. A read-only Stripe (or bank) adapter for the conditions routine, so D2
   is read rather than reported.

## Build (Large; stage 1 serves D1 and needs no answer above)

1. `routines/rsi/` with its runner: read the source rows since the last
   run, skipping tasks under RSI's own objective; open the ranking turn
   only when there are new rows; start through `start_fix`, extended to
   take each finding's project.
2. Lineage and use: the RSI task carries its source row. A later
   delivery that used the ability writes `rsi.used` naming it, and the
   status page's routine row counts started, merged and used.
3. `/rsi`, as ported above (closes #3641's `rsi` item).
4. After grants: G1's replay runner, then a venture record template and
   a resource-expiry condition, both reusing the conditions routine.

Tests:
- a source row on Tom's task starts one task;
- the same row twice starts one task;
- a row from an RSI task starts none;
- a run with no new rows opens no turn;
- `start_fix` honours the finding's project;
- `rsi.used` is counted on the routine row;
- the `/rsi` write-backs land as rows.

Docs: `routines.md` (the rsi routine) and `architecture.md`.

**Done** is Tom's three Done items, one month after stage 1 ships, recorded
with dates (rule 5).
