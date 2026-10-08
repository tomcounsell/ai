---
tracking: none
slug: valor-rebuild-feedback
type: plan
status: draft
---

# Valor rebuild: Tom's feedback

Tom's rulings on [the rebuild plan](valor-rebuild.md) (2026-10-03) and on the governance classifier (2026-10-07), and what each changes.

## Tom's feedback (2026-10-03)

On the "Stop means stop" amendment, in Tom's words: "I think this meant for
the orchestration between Valor and his subagents. Valor decides who stops
and who gets more budget. Valor's communication with the user is as a
project manager speaks to a CEO, so this kind of think would never come
up."

And: "Yes, AND the human also doesn't even get merge taps. Valor also
decides that based on his high level objective. The guiding principle is a
CEO x project manager relationship. If a CEO wants to get technical he can,
but it'd be on the CEO's lead. by defualt, Valor should never raise
technical decisions to a CEO, only ask about vision, business priorities,
and cost benefits of tradeoffs that need to be made in the context of the
how the company works"

What this changes:

- **Stop means stop** governs Valor's subagents. When a task's rounds are
  spent, Valor (and, until takeover, the /build lead) decides whether it
  gets another patch round or stops. It does not go to Tom.
- **Merges are Valor's call**, against the task's objective. "A merge held
  for Tom's tap" in the rebuild plan, in each task plan, and in
  `.claude/skills/build/SKILL.md` no longer holds; the skill text gets the
  matching edit at the next /build.
- **Tom's queue** holds questions about vision, business priorities, and
  the cost and benefit of tradeoffs in how the company works, plus things
  only Tom holds (a password, an account). Technical decisions are never
  raised unless Tom asks to get technical.
- Unchanged: the governance paragraph in `CLAUDE.md`. Adding a check,
  gate, hook, review step, or guard still needs an incident, a mission
  item, and Tom's tap, one per instance.
- **The Macs' admin password** (the same on all of Valor's machines) is in
  1Password, vault `m-valor`, item `bgqjftkvj4witkswgdoxegdjsy`. A step
  that needs `sudo` reads it from there and never prints it.

Decided the same day, recorded in each task's plan: the five passed
deliveries merge (3a, 3c, 4.2, 1.4d, 1.4c part one); one more patch round
for 1.4v, 2.1, 1.4b, 1.4s, 2.2, 2.3, 3b, 1.4u, and 1.5, in that order; no
timers on email commands, a stop ends a hung one; open question 17's DMARC
check parked; `idle_turns` removed.

## Tom's ruling on governance.adds (2026-10-07)

Count as a new checkpoint only a step that looks at work and can stop it or
send it back. An ordinary fix that makes code correct, like a lock that
makes two runs take turns, is no checkpoint, the way tests already are.

The incident: review row 1436 on task 09975a1c2e52, the expiry-lock fix for
review N4. `governance.adds` answered true on two hunks, the advisory lock
in `core/routines.py` and the `docs/routines.md` sentence describing it. The
Opus reviewer wrote "This is not governance", and since a reviewer cannot
remove an instance, a confirmed fix waited on a tap.

What this changes ([the plan](80e49c02-governance-adds.md)):

- The question's gloss names a step that judges work, a request, or an
  action and holds, redirects, or refuses it on that judgement, or a step
  someone must pass; code that makes the work itself correct and prose that
  only describes what code does are none of these, beside tests.
- A settled governance row is reused, and a failed one counts as a spent
  rerun, only under the question that answered it, so a reworded question
  is asked fresh.
- A reviewer's note that contests an instance is an incident against
  `governance.adds`, answered by a classifier change, never by a grant
  (`docs/judgement-layer.md`, note 6).
- Unchanged: the governance paragraph in `CLAUDE.md`.
