---
name: ask-me
description: "Interview the user to get unblocked after deep work, one open question at a time. Triggered by 'ask me', 'I need your input', 'interview me to unblock this', or when an agent has surfaced questions it needs the human to answer after research/investigation/planning."
allowed-tools: Read, Grep, Glob, Bash, AskUserQuestion
---

# Skill: /ask-me

## Purpose
You have just done deep work — research, investigation, a planning pass — and surfaced a pile of context and open questions. The user was NOT there for that work and does not share your context. This skill interviews them to get you unblocked: **one open question at a time, carrying only the context that actually matters into each question.**

The user's stated preference, in order:
1. **North-star alignment beats detail.** They prefer philosophical, directional questions — "which way are we pointing?" over "which of these 6 flags?" If a high-level steer from an advisor is enough to unblock you, ask for exactly that and nothing more.
2. **But the devil is in the details.** When the decision genuinely turns on specifics — an irreversible tradeoff, a fork where the two paths diverge sharply, a number/name/path that changes the outcome — then the details are load-bearing and MUST be in the question. Hiding them to sound high-level is a failure.

Your job is to correctly judge, per question, which case you're in.

## When to Use
- After a research / investigation / planning / audit pass that produced decisions only the human can make
- When you're blocked and the blocker is judgment or direction, not information you can go find yourself
- When the user says "ask me", "interview me", "I need to unblock you", "what do you need from me"

## When NOT to Use
- The answer is discoverable — read the code, the docs, the git history, the issue first. Never ask what you can find out.
- There's a sensible default and the choice is low-stakes/reversible — just pick it, note it, and move on. Don't interview for permission you don't need.
- It's a single yes/no you could resolve with a one-line inline question — just ask it directly.

## Steps

1. **Reload your own context first.** Silently re-read the artifacts your deep work touched (plan doc, issue, diff, files, logs). The user shouldn't have to supply anything you can recover yourself.

2. **Draft the full blocker list privately.** Write out every open question the work surfaced. Do NOT show this list to the user — it's your working set.

3. **Rank by leverage, then merge and cut.**
   - Drop anything you can decide yourself or look up (see *When NOT to Use*).
   - Collapse questions that are really one decision wearing three hats.
   - Order what remains so the most decision-shaping question comes first — later questions often dissolve once the north-star is set.

4. **For each remaining question, run the altitude test.** Ask yourself: *Would a one-line directional steer from a trusted advisor unblock me here?*
   - **Yes → ask high.** Pose the philosophical/directional question. Strip the implementation detail. Example: "Are we optimizing this for fewest surprises to existing users, or for the cleanest long-term architecture?"
   - **No, the paths truly diverge on specifics → ask low, with the details.** Put the load-bearing specifics *in the question text* — the concrete fork, the actual tradeoff, the real names/numbers. Example: "Resume-on-crash can either replay the last turn (risk: double-send a Telegram message) or skip it (risk: silently drop the user's reply). Which failure is more acceptable?"
   - The detail that matters is the detail that changes the answer. Include exactly that; exclude everything else.
   - **Ask for the principle, not the rule, when an intelligent actor will execute it.** If the thing acting on the answer is itself a capable model (an Opus-class agent), don't ask the user to author a decision rule or a tie-break heuristic — they'll tell you to trust the actor's judgment. Ask for the *principle* that should guide it ("what's this surface fundamentally for?") and let the actor discern the rest. A request for a hard rule where discernment belongs is a mis-altitude'd question.

   **State your key assumption inside the question.** Whatever model you built the question on — an architecture, a cost, a constraint — name it in one clause so a wrong assumption gets corrected instead of silently answered. You did deep work the user didn't; your framing can be off, and a compact "under X, ..." lets them catch it in one reply rather than answering the wrong question. Cheap insurance; never skip it.

5. **Ask one question at a time via `AskUserQuestion`.** One question per call. Wait for the answer before composing the next — later questions should adapt to what you just learned.
   - Frame it as an **open** question. Offer 2–4 options only as concrete illustrations of the space, put your recommendation first labeled `(Recommended)` when you have one, and rely on the user's "Other" to capture the answer you didn't anticipate. You are seeking their direction, not railroading them into your menu.
   - Keep the question text tight: the minimum context needed to answer well, and no dump of everything you know. If a fact isn't needed to choose, leave it out.
   - Never batch your whole blocker list into one multi-question call. The point is a conversation that adapts, not a form.

6. **Adapt as you go.** After each answer, re-check your remaining list. A north-star answer often makes two downstream detail-questions moot — drop them. If an answer opens a new fork, add it.

7. **Stop when unblocked, not when the list is empty.** The moment you have enough direction to proceed confidently, stop asking. Then give a short readback: what you now understand the direction to be, and what you'll do next with it. Confirm before acting on anything irreversible.

## Output
A short synthesis of the direction you extracted (the north-star + any load-bearing specifics the user pinned down) and the concrete next action you'll take now that you're unblocked.

## Anti-Patterns
- **Context dump in the question.** Pasting everything you learned instead of the one fact that changes the answer. Ask, don't brief.
- **False altitude.** Asking a vague directional question when the decision actually hinges on a specific the user can't see — they'll answer the wrong question. If the devil's in the details, show the details.
- **False precision.** Dragging the user into an implementation choice when a one-line steer would do. Prefer the north-star.
- **Unstated assumption.** Building the question on your own model (an architecture, a cost, a constraint) without naming it — so a wrong premise gets answered instead of corrected. State the "under X, ..." in the question.
- **Asking for a rule an intelligent actor should form itself.** Requesting a hard classification/tie-break heuristic when the executor is a capable agent. Ask for the guiding principle and trust its discernment.
- **Questionnaire mode.** Firing all questions at once, or not adapting later questions to earlier answers.
- **Asking what you could find.** Any question answerable by reading the repo, docs, or history is a bug in your prep, not a question for the user.
- **Interviewing for reversible trivia.** If it's low-stakes and undoable, decide it yourself and note it.
