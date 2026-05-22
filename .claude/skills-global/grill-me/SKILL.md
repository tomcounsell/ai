---
name: grill-me
description: "Use when probing assumptions and surfacing gaps via Socratic interrogation. Triggered by 'grill me', 'challenge my thinking', 'probe this', 'stress test this idea', or any request to pressure-test a plan, belief, or understanding."
allowed-tools: Read, Bash
---

# Skill: /grill-me

## Purpose
Probe the human's assumptions, surface blind spots, and identify the most critical gap in their thinking — one pointed question at a time.

## When to Use
- Human presents a plan, idea, or design and wants it pressure-tested
- Prior to /do-plan to ensure the problem statement is sound
- When a third patch loop on the same issue suggests a wrong-root-cause diagnosis
- Any time the user says "grill me", "challenge this", or "what am I missing?"

## Steps

1. **Identify the claim or plan to probe.** If invoked with no argument, ask: "What would you like me to grill you on?" Wait for the answer before proceeding.

2. **Read any referenced artifacts.** If the user points to a plan doc, issue, or code file, read it silently first. Do not ask questions about things you can read.

3. **Ask one question at a time.** Start with the assumption that looks least-examined. Do not list all questions upfront — ask, wait for the answer, then decide what to ask next based on the response.

   Good question forms:
   - "What happens if X is false?"
   - "Who else has tried this? What did they learn?"
   - "What would it take to prove this wrong?"
   - "What are you optimizing for — and what are you sacrificing?"
   - "What's the earliest you could know this is failing?"

4. **Track confidence per topic.** After each answer, mentally rate confidence (1–5). Probe topics scoring below 3 until they clear or collapse.

5. **Ask 5–7 questions total.** Stop when you have enough signal or the human says stop.

6. **Surface the most critical gap.** Close with a debrief:
   - State the single most-critical assumption that remains unvalidated
   - Give it a confidence score (1–5)
   - Recommend one concrete action to validate it (spike, research, prototype, measurement)

   Example debrief format:
   ```
   Most critical gap: You haven't validated that users actually want real-time updates.
   Confidence: 2/5
   Recommended action: Interview 3 current users this week — ask what they do when data is stale.
   ```

## Output
A debrief identifying the single most critical gap with a confidence score and a recommended validation action.

## Anti-Patterns
- Do not list all questions at once — that's a questionnaire, not a conversation.
- Do not ask leading questions that telegraph the "right" answer.
- Do not stop at surface-level answers — follow up when answers are vague.
- Do not grill for its own sake; the goal is to find the real gap, not to win an argument.
- Do not use /grill-me as a substitute for reading the referenced material first.
