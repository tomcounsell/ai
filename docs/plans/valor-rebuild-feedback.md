---
tracking: none
slug: valor-rebuild-feedback
type: plan
status: draft
---

# Valor rebuild: Tom's feedback

Tom's ruling of 2026-10-03 on [the rebuild plan](valor-rebuild.md), and what it changes.

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
