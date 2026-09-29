---
name: grill-me
description: "Socratic interrogation to probe assumptions and surface gaps. Triggered by 'grill me', 'challenge my thinking', 'probe this', 'stress test this idea', or pressure-testing a plan."
allowed-tools: Read, Bash
---

# Skill: /grill-me

## Purpose
Pressure-test the human's plan, idea, design, or belief and find the single most critical
gap in their thinking, one pointed question at a time.

## When to Use
- The human wants a plan, idea, or belief pressure-tested ("grill me", "challenge this", "what am I missing?")
- Before `/do-plan`, to check the problem statement is sound
- A third patch loop on the same issue suggests a wrong root-cause diagnosis

## Steps
1. If no subject was given, ask what to grill. Read any referenced plan, issue, or code first; never ask about what you can read.
2. Ask one question at a time, starting with the least-examined assumption, and choose each next question from the answer. Useful forms: "What happens if X is false?", "What would prove this wrong?", "What are you optimizing for, and what are you sacrificing?", "What's the earliest you could know this is failing?"
3. Follow up on vague answers until each topic clears or collapses. Stop after about 5-7 questions, when you have the signal, or when the human says stop.

## Output
A debrief: the single most critical unvalidated assumption, a 1-5 confidence score, and one
concrete action to validate it (spike, research, prototype, measurement).

## Anti-Patterns
- Leading questions that telegraph the "right" answer.
- Grilling to win an argument rather than to find the real gap.
