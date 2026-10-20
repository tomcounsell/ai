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

Tom's requirements above stand (rule 6). Reasoning:
[synthesis.md](../research/rsi/synthesis.md#reasoning-behind-the-plan);
sources: [spikes.md](../research/rsi/spikes.md). Baseline `f28c9262e`.

## Shape (D1, D3)

One routine, `routines/rsi/`, every 15 minutes. It starts ordinary tasks
through `core/findings.py` `start_fix` (rule 4). Unlike the old controller
(375 runs, 0 used), it starts from Tom's work, delivers a task on a real
repository, needs no precondition, and counts tasks Tom used over tasks
started.

## Answers

1. **Sources and ranking (D1):** corrections, feedback, avoidable questions,
   failed steps, refused effects, gateway spend, and links Tom forwards.
   Ranked by Tom's attention, recurrence and the Done item served; keyed
   `rsi:<row>`.
2. **Evaluator per kind (D1–D3):** Tom's use, preceded by a blind replay
   (G1) whose answer keys stay in the kernel's checkout. Skill: verifier
   plus replay. Persona: chat replay. Model route: open-source replay plus
   spend. Infrastructure: the ledger (D3). Venture: processor money (D2).
3. **Write paths:** nothing added. There is no runtime skill-write tool,
   recall is quoted as data, and every diff is reviewed.
4. **Accounts (D2, D3):** a human at Yudame creates one account per
   provider, then Valor runs it. Pooling free tiers is banned.
5. **Venture liability (D2):** the liability falls on Yudame Inc. Each
   venture is a committed record that names its owner, accounts, cap, kill
   date, disclosure, markets and insurance.
6. **Unlike the old controller:** see Shape.
7. **What Tom sees and how he steers:** merge reports with a lineage line,
   the strategy agenda, and the routine's status row. He steers by reply,
   `core correct` or `exemplar`, `/rsi`, or by stopping the objective.
8. **Old skills (rule 10):** `improve-preflight` is dropped.
   `improve-research` is dropped, with four rules ported into the routine's
   instruction. `rsi` becomes #3641's `/rsi`, which grounds itself on the
   open opportunities and hands the questioning to `ask-me`.

## Governance (none is built without its grant; each is ledgered with a 90-day expiry)

| | What | Mission item | Incident | Grant |
|---|---|---|---|---|
| G1 | Blind replay before "improved" is claimed | 5 | #3177 (375 runs, none used; the judge saw the skill's name) | **Needs Tom** |
| G2 | Tom approves each venture | Business 8 (conflicts) | none in Valor | Tom, 2026-10-20; **incident needed** |
| G3 | $10/day inference, $50/week infrastructure, held as the card's issuer limit | Bounded authority (conflicts: "no spend ceiling") | none in Valor | Tom, 2026-10-20; **incident needed** |
| G4 | Client work stays on Claude/Codex (specs gain `open_source`) | Generic across businesses | none in Valor | Tom, 2026-10-20; **incident needed** |

## Needs Tom

1. Grant or refuse G1–G4, and name each one's incident.
2. Resolve the `mission.md` conflicts: G2 against Business item 8, and G3
   against "no spend ceiling".
3. Counsel: state of incorporation and an LLC per venture, insurance AI
   exclusions, agent terms at Stripe and the marketplaces, EU or California
   consumers, tax.
4. Which providers to open, who creates each account, and whether to apply
   for AWS Activate or Azure.
5. Whether RSI may edit the persona (charter §12).
6. Whether a weekly RSI message is wanted.
7. A read-only Stripe or bank adapter for the conditions routine (D2).

## Build (Large; stage 1 serves D1 and waits on none of the above)

1. **The `routines/rsi/` runner.** It skips RSI's own tasks and opens no
   turn when nothing is new. `start_fix` takes each finding's project.
2. **Lineage.** Each task carries its source row; a later delivery that
   used the ability writes `rsi.used`.
3. **`/rsi`.** This closes #3641's `rsi` item.
4. **After the grants:** the replay runner, a venture template, and
   resource-expiry conditions.

Tests cover dedupe, self-exclusion, the idle run, the finding's project,
`rsi.used`, and the `/rsi` write-backs. Docs: `routines.md` and
`architecture.md`. Done is D1–D3, each recorded with its date (rule 5).
