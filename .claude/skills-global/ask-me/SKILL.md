---
name: ask-me
description: "Interview the user one question at a time to get unblocked after deep work. Triggered by 'ask me', 'I need your input', 'interview me', or when only the human can answer."
allowed-tools: Read, Grep, Glob, Bash, AskUserQuestion
---

# Skill: /ask-me

If `.claude/skill-context/ask-me.md` exists, read it and honor its declarations; otherwise use the generic defaults described below.

You have done deep work (research, investigation, planning) the user was not present for
and does not share context on. Get unblocked by interviewing them **one open question at a
time, carrying into each question only the context that changes the answer.** Done when
you have enough direction to proceed confidently (not when the list is empty), followed by
a short readback: the direction you extracted, the specifics the user pinned down, and
the next action you will take. Confirm before acting on anything irreversible.

## What not to ask

- Anything discoverable from code, docs, git history, or the issue. Re-read the artifacts
  your work touched first; the user should never supply what you can recover.
- Low-stakes, reversible choices with a sensible default: decide, note it, move on.
- A single yes/no you can ask inline without this skill.

## Shaping the questions

Keep your full blocker list private. Drop what you can decide yourself, collapse questions
that are one decision in disguise, and lead with the most decision-shaping one: later
questions often dissolve once the direction is set.

**Pick the altitude per question.** The user prefers north-star questions ("fewest
surprises for existing users, or cleanest long-term architecture?") over detail. But when
the paths diverge sharply on specifics (an irreversible tradeoff, a number, name, or path
that changes the outcome), put those specifics in the question text: "Resume-on-crash can
replay the last turn (risk: double-send) or skip it (risk: silently drop the reply). Which
failure is more acceptable?" Hiding load-bearing detail to sound high-level makes them
answer the wrong question; dragging them into detail a one-line steer would settle wastes
their time.

**Ask for the principle, not the rule,** when a capable agent will act on the answer: ask
what the thing is fundamentally for and let the actor discern the rest.

**State your key assumption in the question** ("under X, ..."), so a wrong premise gets
corrected rather than answered.

## Asking

Use `AskUserQuestion`, one question per call, and wait for each answer: later questions
should adapt to what you learn, dropping ones an answer made moot and adding forks it
opened. Frame it open: offer 2-4 options as illustrations, your recommendation first
labeled `(Recommended)`, and rely on "Other" for the answer you didn't anticipate. Ask
separately in one go only when questions are genuinely independent (neither answer could
reframe or moot the other).
