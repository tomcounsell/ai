---
name: rsi
description: "Interactive RSI session with Tom present: show what the rsi routine has not yet started, hear what he came in with, and ask what only he can answer one question at a time through ask-me. Runs only on an explicit /rsi."
disable-model-invocation: true
argument-hint: "[rsi:<event id>]"
---

# /rsi: prioritization with Tom

Get Tom's judgment into the ledger before the `rsi` routine spends turns on a guess. Ports the old system's `/rsi` (#3641) onto the rebuilt loop (`docs/plans/rsi.md`).

## Ground first

```bash
python -m core rsi
```

It prints used over started, the rows the routine has not started (`rsi:<event id>`, source, text), and the tasks that ended or stopped unused. With an argument, start from that row. Read the rows in full before saying anything.

## Open the floor

Give Tom one line of state (used over started, how many rows are unseen) and ask what he came in with. Listen to all of it. Hold your own questions until he says he is ready.

## Ask

Use the `ask-me` skill: one question at a time, each wait for the answer before the next. Ask only what he knows and the ledger cannot say: which unseen row matters, what a stopped task got wrong, what he has already tried. Each question names the row it is about and the assumption it rests on, offers two to four options with your recommendation first, and asks for direction before detail. Never ask what a read of the code or the ledger answers.

## Write each answer down at once

A session has no predictable end, so nothing waits for a closing step.

- **A correction** ("that is wrong, do this"): `python -m core correct "<text>"`. The routine starts a task for it.
- **Work to do now**: start it, `python -m core start "<instruction>" --project <valor|popoto>`, or tell Tom the plan skill is the path for anything larger.
- **A row not worth a task**: say so. It is no obligation, but the routine starts a task for every unseen row within 15 minutes, so a row not worth a task needs a ruling from Tom, not silence.
- **A change to authority, a spending limit or a venture**: a proposal with its evidence, which Tom grants in writing in a plan file. This session grants nothing.

Read `python -m core rsi` again after each write and tell Tom what moved in a line. If nothing moved, say that.
