---
status: Planning
type: feature
tracking: https://github.com/tomcounsell/ai/issues/3655
revision_applied: true
revision_applied_at: 2026-10-20T08:50:36Z
---

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


# Plan (2026-10-20, #3655; revised after critique round 1)

Tom's requirements above stand (rule 6). Reasoning:
[synthesis.md](../research/rsi/synthesis.md#reasoning-behind-the-plan);
sources: [spikes.md](../research/rsi/spikes.md). Baseline `34c4a1d9c`.

## Shape

One routine, `routines/rsi/`, `[schedule] interval = 900`, `ceiling =
"act"`, `model = "frontier"`, `project = "valor"`, `branch = "main"`. It
starts ordinary tasks through `findings.start_fix` (rule 4). Unlike the old
controller (375 runs, 0 used), it starts from Tom's rows and counts what
Tom used. Tom halts it with `python -m core stop <objective>`.

**Class.** Rule 2: Tom asked for RSI on 2026-10-20, and it acts on what Tom
said. Scanning failed steps is a check (`routines.md`), so that source is
G5. No two hand-done task ids exist for `need` (Needs Tom 1).

## Stage 1 (Build 1)

- **Sources:** `correction.recorded` rows from Tom and messages carrying a
  link Tom forwarded; failed steps after G5. Ranked by
  recurrence, then age; RSI's own tasks are skipped.
- **Keys:** `rsi:<ledger event id>` in the `finding.keys` marker. A row is
  *unseen* when no `task.started` carries its key; the runner starts only
  unseen rows, so a merged, ended or stopped task never restarts its row.
  No cursor: `task.started` is the record.
- **`start_fix`:** `Finding` gains optional `project` and `branch`,
  defaulting to the routine's; the refusal for no project applies to the
  resolved value. `verify` passes neither.
- **Use:** "used over started" joins `task.started` rows with an `rsi:` key
  to `delivery.used` rows on the same task that `outcomes.used_count`
  counts (not role-played) and whose `by` is Tom. Only Tom writes them
  (`python -m core used TASK --by tom`); no agent judges use.

Tests: a merged, a stopped, and a crashed-before-`routine.ran` row are not
restarted on rescan; own tasks skipped; idle run opens no turn; a finding's
project is used; `verify` still starts on its own; the count ignores
role-played and non-Tom marks.

## Governance (none built without its grant; each ledgered, 90-day expiry)

| | What | Mission item | Incident | Grant |
|---|---|---|---|---|
| G1 | Blind replay before "improved"; under 10 paired trials per arm the verdict is "inconclusive", which neither blocks nor promotes | 5 | #3177 (375 runs, none used; judge saw the skill's name; 2 and 4 trials inconclusive) | **Needs Tom** |
| G2 | Tom approves each venture | Business 8 (conflicts) | none | Tom, 2026-10-20; **incident needed** |
| G3 | $10/day inference, $50/week infrastructure, as the card's issuer limit | Bounded authority (conflicts) | none | Tom, 2026-10-20; **incident needed** |
| G4 | Client work stays on Claude/Codex (specs gain `open_source`) | Generic across businesses | none | Tom, 2026-10-20; **incident needed** |
| G5 | RSI reads failed steps as a source | 1 | none named | **Needs Tom** |

## Needs Tom

1. Accept his 2026-10-20 ask as `need` (as `verify` cites his ask), or name two hand-done tasks.
2. Grant or refuse G1–G5 and name each incident; resolve G2 and G3 against `mission.md`.
3. Counsel on incorporation, per-venture LLCs, insurance, agent terms, consumers, tax.
4. Which providers and cloud sandbox host to open, and who creates each account.
5. Persona edits by RSI (charter §12)? A weekly message?
6. A read-only Stripe or bank adapter for the conditions routine.

## Build (Large)

1. **Stage 1** above. Serves Done 1; waits on nothing.
2. **`/rsi`**, its own task: the #3641 port, grounded on unseen rows,
   questioning through `ask-me`.
3. **Replay runner** (G1). Serves Done 1; waits on G1.
4. **Venture template:** owner, accounts, cap, kill date, disclosure,
   markets, insurance; money read by the conditions routine. Serves Done 2; waits on G2, G3, Needs Tom 3 and 6.
5. **Unattended:** the routine and kernel run on the chosen cloud host,
   with resource-expiry conditions. Serves Done 3; waits on Needs Tom 4.

Rule 10: `improve-preflight` is dropped; four `improve-research` rules go
into the instruction. No write path is added. Tom sees merge reports with
an `rsi:` lineage line. Docs: `routines.md`, `architecture.md`.
