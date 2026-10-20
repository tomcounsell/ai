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


# Plan (2026-10-20, #3655)

Tom's requirements above stand as written (rule 6). The evidence is in
[synthesis.md](../research/rsi/synthesis.md) and
[spikes.md](../research/rsi/spikes.md). D1, D2 and D3 are the three Done
items. G1–G4 are the governance rows below. Baseline commit: `f28c9262e`.

## Shape: one routine that starts tasks for Tom (D1, D3)

RSI is a single routine, `routines/rsi/`. It starts ordinary tasks through
`core/findings.py` `start_fix` (rule 4). There is no case store, no CLI and
no merge path. It runs every 15 minutes, all day, under launchd. A run that
finds no new source rows ends without a turn. It differs from the old
controller, which ran 375 times with nothing for Tom, in four ways:

1. **It starts from Tom's work.** Every opportunity cites a row on a task
   RSI did not start. The old loop's only candidate was its own
   memory-retrieval parameters.
2. **Its output is a task on a real repository:** `yudame/skills`, this
   one, or the open-source project where the gap showed. The task runs the
   normal pipeline and ends in a merge report to Tom. The old loop wrote
   rows to its own namespace.
3. **Nothing must be true before work starts.** The old loop's first cycle
   ended `infra_failure` on its own calibration floor.
4. **It counts use.** An ability is done when a later task of Tom's used it
   and Tom accepted that delivery, with the date. The number to beat is
   tasks used over tasks started; the old loop scored 0 of 375.

## Answers

**Sources and ranking (D1).** Rows the kernel already writes on Tom's
tasks: corrections, feedback rounds, questions Valor could have answered
itself, failed steps and tasks, refused effects, and gateway spend by kind
of task. Links Tom forwards count too; the persona files them as
inspiration with the existing `core routine rsi --finding … --key
link:<url>`. One turn per run ranks the open opportunities by three
things: Tom's attention spent on the gap, recurrence (two or more tasks,
checked in memory), and which Done item it serves. It then starts tasks
keyed `rsi:<row>`. Dedupe keys and turn slots bound the load.

**Evaluator per kind (D1–D3).** The ground truth is Tom's use and his
feedback. Before he uses a change, a **blind replay** (G1) runs real past
tasks with and without it, using the rebuild-baseline method. Its answer
keys are Tom's merged result and the hidden tests. They live in the
kernel's checkout, never in a task's workspace. A fresh Opus-class judge
reads outcomes, never narration, so the improver can neither see nor edit
the judge.

- **Skill:** the existing verifier on the diff, then a replay of the
  task that showed the gap plus one similar task.
- **Persona:** a replay of past chat turns, hostile group messages
  included (the Grok incident).
- **Model route:** a replay of past open-source tasks, plus gateway spend.
- **Infrastructure:** the ledger's turns and deliveries while Tom made no
  move (D3).
- **Venture:** money received, read from the processor by the conditions
  routine and never self-reported (D2).

Each RSI task records its lineage: source row, task, replay, later use.

**Untrusted input and write paths (D1). Nothing is added.** Hermes-style
poisoning needs a skill-write tool at runtime, and Valor has none. Skills
and persona change only by a reviewed merge. Memory recall is quoted and
escaped, and grants nothing. Corrections come only from Tom, with
provenance. Content read while working is data (`persona/conduct.md`).
Findings quote their evidence as data. Group text is never a source row.
What remains is a reviewed diff carrying content from a forwarded link.
The verifier reads every diff, and no guard is added until an incident
calls for one.

**Accounts (D2, D3).** Valor cannot open a provider account alone:

- GitHub requires a human.
- Cloudflare's agent path (Stripe Projects) still needs a human to accept
  the terms and add a card.
- OpenAI's ID-and-selfie check, Cerebras's card and the phone checks at
  Mistral and claude.ai need a person at Yudame.

Pooling free tiers is banned (OpenRouter, Cloudflare, Groq) or blocked
(OpenAI allows one organization per ID). So the rule is one account per
provider: a human creates it, then Valor runs it.

- **Usable with no program:** Workers AI, Modal ($30 a month), E2B ($100
  once), Daytona ($200), GitHub Actions.
- **Founder programs:** AWS Activate and Azure. Anthropic's cohort is
  closed, and OpenAI's credits come only through VCs.
- **Free tiers that train on inputs** (Gemini, Mistral, some OpenRouter
  `:free` models) take open-source work only (G4).

Each credit's expiry is a watched condition. A cloud sandbox for
open-source tasks is the first D3 opportunity, filed as a backlog finding
once an account exists.

**Venture liability (D2).** No court has ruled on whether an agent's
out-of-scope act binds its company. Every source puts the exposure on the
deployer, Yudame Inc:

- UETA §14 and E-SIGN §7001(h) bind the deployer to the agent's
  contracts.
- The Restatement treats a program as a tool, not an agent.
- *Moffatt v. Air Canada* held the airline liable for its chatbot.
- California AB 316 (2026) bars the "the AI did it" defence.

A venture is a committed decision in its business repository (Business
items 3 and 7), approved by Tom (G2). The record names:

- the legal owner and merchant of record, with Tom as signer;
- each platform account and the clause in its terms that permits an agent
  (Upwork bans bots);
- a cap held at the card (G3), and the largest contract Valor may accept;
- a kill date, plus early-kill conditions (complaint, chargeback,
  platform warning), which the conditions routine raises on the agenda;
- bot disclosure (California B.O.T. Act, EU AI Act Art. 50) and FTC-clean
  claims;
- its markets and its insurance.

The rest is for counsel. Expect income in dollars; saved inference is the
likelier value.

**What Tom sees and how he steers (all).** No digest and no dashboard.

- RSI merges arrive as ordinary merge reports, each with a lineage line.
- Venture proposals, limit raises and credit expiries go on the
  strategy-meeting agenda.
- The routine's row on the status page shows started, merged and used.

He steers by replying to a report, by `core correct` or `exemplar`, by
`/rsi`, or by stopping the routine's objective, which stops all of it.

**Old skills (rule 10, D1).**

- **`improve-preflight`: dropped.** Its three reads are controller state
  that no longer exists.
- **`improve-research`: dropped.** Its rules are ported as lines of the
  routine's instruction:
  - a claim carries a URL and a date;
  - an unreachable source is recorded as unreachable;
  - an assumption names what would overturn it;
  - Tom is asked no research questions.
- **`rsi`: ported.** This is #3641's `rsi`, one skill, not two. It
  becomes a short Valor-plugin skill: it grounds itself on the open
  opportunities, then hands the questioning to `ask-me`. Each answer is
  written back at once:
  - a fact goes to `core correct`;
  - an idea goes to `--finding`;
  - "plan this" goes to `do-issue`.

## Governance (none is built without its grant; each is ledgered with a 90-day expiry)

| | What | Mission item | Incident | Grant |
|---|---|---|---|---|
| G1 | Blind replay before "improved" is claimed | 5 | #3177: 375 runs, none used; lane 5b's judge saw the skill's name | **Needs Tom** |
| G2 | Tom approves each venture before it starts | Business 8 (says nothing waits on consent) | none in Valor (Project Vend, external) | Tom, 2026-10-20; **incident needed** |
| G3 | Hard cash: $10/day inference, $50/week infrastructure, as the card's issuer limit (no code) | Bounded authority (mission: "no spend ceiling") | none in Valor | Tom, 2026-10-20; **incident needed** |
| G4 | Client work stays on Claude/Codex; other models only on open-source projects (specs gain `open_source`) | Generic across businesses | none in Valor | Tom, 2026-10-20; **incident needed** |

## Needs Tom

1. Grant or refuse G1–G4, and name each one's incident.
2. Resolve the conflicts with `mission.md`: G2 against Business item 8, and
   G3 against "no spend ceiling".
3. Counsel:
   - Yudame's state of incorporation, and whether each venture should be
     its own LLC;
   - whether its insurance carries the 2026 AI exclusions;
   - whether Stripe or the marketplaces let an agent run an account;
   - whether to sell to EU or California consumers;
   - tax per venture.
4. Which providers to open, and who at Yudame creates each account (phone,
   card, terms). Whether to apply for AWS Activate and Azure as founder.
5. Whether RSI may edit the persona. Charter §12 keeps identity out of
   autonomous change.
6. Whether a weekly RSI message is wanted.
7. A read-only Stripe or bank adapter for the conditions routine, so D2 is
   read rather than reported.

## Build (Large; stage 1 serves D1 and waits on none of the above)

1. **The `routines/rsi/` runner.** It reads the source rows since the last
   run, skipping RSI's own tasks, and opens the ranking turn only when
   there is something new. `start_fix` is extended to take each finding's
   project.
2. **Lineage.** Each task carries its source row. A later delivery that
   used the ability writes `rsi.used`, and the routine's row counts it.
3. **`/rsi`,** as above. This closes #3641's `rsi` item.
4. **After the grants:** G1's replay runner, a venture record template,
   and resource-expiry conditions.

Tests:

- one source row starts one task, and seeing it again starts none;
- a row from an RSI task starts none;
- a run with no new rows opens no turn;
- `start_fix` uses the finding's project;
- `rsi.used` is counted;
- each `/rsi` write-back lands as a row.

Docs: `routines.md` and `architecture.md`. Done is D1–D3, one month after
stage 1 ships, each recorded with its date (rule 5).
