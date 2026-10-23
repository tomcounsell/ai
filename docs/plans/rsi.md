---
status: Ready
type: feature
tracking: https://github.com/tomcounsell/ai/issues/3655
revision_applied: true
revision_applied_at: 2026-10-23T10:17:20Z
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


# Plan (2026-10-21, #3655; revised after critique round 3)

Tom, 2026-10-23: fix and build, no further critique.

Requirements above stand (rule 6). Reasoning:
[synthesis.md](../research/rsi/synthesis.md#reasoning-behind-the-plan);
[spikes.md](../research/rsi/spikes.md). Baseline `412f6d9cd`. Unlike the
old controller, it starts from what Tom said and counts what Tom used.

## Build 1: the routine

- **`routines/rsi/routine.toml`:** `name = "rsi"`, `created = 2026-10-23`
  (a TOML date), a one-line `instruction`, `runner = "rsi"`, `[schedule] interval = 900`,
  `ceiling = "act"`, `model = "frontier"`, `project = "valor"`,
  `branch = "main"`, `mission_item = "5"`, `need` = Tom's ask of 2026-10-20
  (one suffices, Tom, 2026-10-21).
- **`routines/rsi/runner.py`:** `run(ctx) -> Ran`, added to
  `core/__main__.py:_routine_runners`. Plain SQL, no turn.
  - `rows(conn)` reads three sources:
    - `correction.recorded` rows;
    - `message.received` rows from Tom's verified DM: `verified`,
      `chat_kind == "dm"`, `intake._from_operator(payload)`, and a
      GitHub URL of a repo in the allowed set below (intake already
      starts a task for any other Tom message, `core/intake.py:637-650`);
    - `task.ended` rows with `kind == "step"` (retries exhausted), one
      per task, on tasks not started by `rsi` and whose project is in the
      allowed set (G5). Not the per-retry `step.failed` rows
      (`core/router.py:214, 282-294`).
  - **Unseen:** a row is unseen when its id is above the routine's first
    `routine.registered` id (`routines.registrations`) and no
    `task.started` carries `rsi:<event id>` (`findings._started`): a
    definition, not a guard. An ended or stopped task never restarts its row.
  - **Starts:** each unseen row, oldest first, through
    `findings.start_fix(parent=ctx.objective)`. None unseen:
    `Ran(None, "nothing_due", ...)`, no turn.
- **Project:** `project_of(url)` matches only the allowed set, `valor`
  and `popoto` (Tom's open-source decision; no new flag), by their specs'
  `repo`, loading those two specs directly, not `Spec.here()`. Any other
  URL falls back to the routine's project. A DM's URL, a failed step's
  task `origin_url`; a correction takes the routine's.
  - `Finding` gains `project: str | None = None`. `start_fix` uses
    `finding.project or routine.project` (the routine's branch only on its
    own project) and records a differing project in the marker.
- **Used:** `used_over_started(conn)` over `rsi:` tasks; used means
  `outcomes.used_count` counts a mark with
  `provenance.by.strip().lower() == "tom"`.
- **Lineage:** the merge report (`notices.py`, `kind == "merge"`) adds the
  line `rsi: <keys>`.
- **Tests (`tests/test_rsi.py`):**
  - 50 corrections recorded before registration start nothing;
  - merged, stopped and crashed rows are not restarted;
  - five `step.failed` retries start nothing; their `task.ended kind=step`
    starts exactly one;
  - a failed step on a client (e.g. cuttlefish) task starts nothing;
  - group, unverified and URL-less messages start nothing; DMs linking
    vm.tiktok.com, claude.ai or a client repo start nothing;
  - a DM linking `github.com/tomcounsell/popoto` starts on popoto;
  - `routines.load("rsi")` succeeds;
  - an idle run starts no task;
  - `verify` is unchanged;
  - "Tom" equals "tom"; role-played and non-Tom marks do not count;
  - the merge report carries the lineage line.
- **Expect idle:** one correction row exists, so Build 1 mostly reports
  `nothing_due` until failed steps arrive; an idle week is not a fault.
- **Check:** `settings.machine` gives `Valor-the-Cowboy`, project specs say
  "Valor the Cowboy". If the mismatch is real, it is a separate bug commit
  with a test (rule 9).
- **Docs:** `routines.md` (the runner; Existence: one need suffices, Tom's
  grant 2026-10-21), `data.md` (marker `project`), `architecture.md`.

## Build 2: `/rsi` and the weekly message

- `skills/valor/skills/rsi/SKILL.md` ports `old-system:.claude/skills/rsi`
  (#3641). It reads unseen rows and asks through `ask-me`.
- `python -m core rsi` prints used over started, unseen rows, and rows
  whose task ended or stopped unused.
- The runner's first run each ISO week sends it to Tom
  (`notices.request(kind="report", about_key="rsi-week:<week>")`); Build 4
  adds ventures and money.
- Tests and docs as in Build 1.

## Builds 3–5 (each its own plan)

3. Blind replay runner (G1).
4. Venture template, with income read from Valor's Stripe; waits on
   Needs Tom 1–2.
5. Unattended on a cloud host Valor chooses.

## Decisions (Tom, 2026-10-21)

- **Access, not governance:** venture sign-off (Valor cannot bind Yudame
  Inc) and the cash caps (the card issuer's limit). No incident needed.
- **No client split:** RSI works only on open-source repos (ai, maybe
  popoto); no `open_source` flag.
- **Persona:** RSI may propose edits as a PR; Tom merges.
- **Stripe:** admin access on Valor's own brand account.
- **Accounts:** Tom's browsers stay logged in; Valor may create accounts
  and lists every login, and its chosen cloud host with why, in
  `~/Desktop/Valor/accounts.toml`.

## Governance (ledgered, 90-day expiry)

| | What | Mission item | Incident | Grant |
|---|---|---|---|---|
| G1 | Blind replay before "improved"; under 10 paired trials per arm is "inconclusive", which neither blocks nor promotes | 5 | #3177 | Tom, 2026-10-21 |
| G5 | Failed steps as a source | 1 | none named by Tom | Tom, 2026-10-21 |

## Needs Tom

1. Counsel on incorporation, per-venture LLCs, insurance, agent terms,
   consumers and tax (Build 4).
2. Who opens Valor's Stripe account, since it needs Yudame Inc's details.
