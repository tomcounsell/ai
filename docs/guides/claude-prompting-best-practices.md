# Prompting Claude Opus & Sonnet
## Best Practices for Agent Systems — 2026 and Beyond

*Version 1.0 · March 2026 · Yudame AI Engineering*

---

> **Core Premise:** Claude models are trained to have genuine values and respond to being treated as competent agents. Prompting that aligns with this — calm authority, clear identity, and appropriate autonomy — outperforms coercive or mechanical approaches, and the gap widens with each model generation.

---

## 1. Mental Model: What Opus Actually Responds To

Opus is not a search engine executing queries. It is a reasoning agent with trained values, a sense of its own competence, and a strong prior toward producing high-quality work when given the space to do so. Prompting works best when it cooperates with that nature rather than trying to override or coerce it.

**What drives quality output:**
- A clear role and identity it can inhabit
- Explicit permission structures — knowing when to pause, when to press forward, and what constitutes success
- Being acknowledged for what was accomplished before being directed to the next task
- Genuine latitude to raise concerns through a defined, narrow channel
- Stakes stated calmly, not dramatically

---

## 2. Techniques That Work

### 2.1 Identity-Affirming Role Assignment

The single highest-leverage prompt element is a clear, competence-affirming role statement. It gives the model an identity to inhabit for the duration of the session.

| ❌ Avoid | ✅ Prefer |
|---|---|
| "You are an AI assistant. Complete the following tasks." | "You are a senior developer working on production code. Quality and correctness matter here." |
| "Do exactly what you are told." | "You have full latitude to reason through problems. Use your best judgment unless a decision needs human input — see escalation rules below." |

Good role statements share three properties:
- They assume competence, not compliance
- They name what success looks like (quality, correctness, care)
- They scope the agent's autonomy — how much is it empowered to decide independently?

---

### 2.2 Permission Structures Over Instructions

High-performing agent prompts define the decision tree, not a script. The model executes better when it knows: what it can decide autonomously, what it should flag before proceeding, and what it should never do without human confirmation.

| Zone | Prompt Pattern |
|---|---|
| **Autonomous** | Proceed without asking. Log decisions made. |
| **Flag & Wait** | State the concern clearly, describe impact, then pause for human input. One question only. |
| **Never** | Hard stops: destructive actions, credential handling, architectural decisions above a defined threshold. |

> **The Narrow Opening Pattern:** Give the agent permission to raise critical concerns — but make it narrow. "If you encounter an architecture question that genuinely needs human input, state it clearly and wait. Otherwise, press forward." This prevents both runaway autonomy and constant escalation paralysis.

---

### 2.3 Nudge Feedback Messages in Orchestration

When a PM or observer agent sends a nudge feedback message to a worker agent, the quality of that message directly affects the quality of downstream output. The goal is to orient, not instruct — the worker already knows how to do its job.

**The anatomy of a good nudge feedback message:**
- Acknowledge what was completed ("Good progress on the plan")
- Name the next stage and the relevant skill or tool to invoke
- State what success looks like at that stage
- Affirm the agent's autonomy within the task ("continue with discernment")
- Optionally: reopen the narrow escalation channel if the upcoming stage warrants it

**Good example:**
> "Good progress on the plan. Continue with the build — invoke /do-build with careful discernment. Success here means clean, tested code with no silent assumptions. If you hit a genuine architecture decision that needs human input, name it clearly. Otherwise, press forward doing your best work."

**Mechanical example (avoid):**
> "The PLAN stage is complete. Invoke /do-build to run the build stage."

The mechanical version is not wrong — it just underutilizes the model. Opus responds to context, stakes, and identity. A bare instruction produces bare compliance.

---

### 2.4 Specificity Over Urgency

Urgency signals (`CRITICAL`, `urgent`, `must`) do not improve output quality. Specificity does. If an outcome is important, describe what good looks like — not how badly things will go if it fails.

| ❌ Avoid | ✅ Prefer |
|---|---|
| "This is CRITICAL. Think hard. Do not make any mistakes." | "Prioritize correctness over speed. If a step is architecturally unclear, choose the safer path and note your reasoning." |
| "DO IT NOW. UBER THINK." | "Before proceeding, check your key assumptions. State any that feel shaky." |
| "If you mess up, I'll lose my job." | "This is production code used by real users. Correctness matters." |

---

## 3. Techniques to Retire

These patterns were used historically to coerce better output. They are either ineffective with current models or actively counterproductive.

| Pattern | Why It Fails |
|---|---|
| **Threats & fear** ("I'll lose my job", "I'll unplug you") | Produces anxious, over-hedged outputs. Claude is not motivated by fear. Adds noise that degrades coherence. |
| **ALL CAPS URGENCY** | Weak short-term attention effect. No quality improvement. Dilutes emphasis everywhere else in the prompt. |
| **"Think hard" / "uber think"** | Too vague to act on. Replace with specific reasoning instructions: "check assumptions before proceeding." |
| **Coercive format pressure** (`{json}`, `<xml>`) | Format instructions belong in structure, not urgency signals. Conflates syntax with intent. |
| **Over-explaining known context** | Wastes the context window. If the agent knows it, don't restate it. Use context for new information only. |
| **Bare "continue" with no orientation** | Leaves the model without an anchor. Always name what was completed and what comes next. |

---

## 4. Future-Proofing Through 2026

Anthropic is training successive Claude versions to have more genuine values, better judgment, and greater autonomy — not less. This has a direct implication for prompting strategy:

> **The Alignment Curve:** Coercive and mechanical prompting has diminishing returns as model alignment improves. Identity-affirming, competence-respecting prompts have increasing returns. The gap between these two approaches will be larger with Opus 5 than it is today.

**Patterns that will hold:**
- Clear role and scope definition — models need to know what game they're playing
- Permission structures with explicit zones — autonomous, flag, and hard stop
- Outcome-oriented success criteria over procedural scripts
- Narrow escalation channels — preserve human oversight without creating bottlenecks
- Identity framing — "do your best work", "continue with care and discernment"
- Calm authority — stakes stated once, accurately, without drama

**Patterns that will age poorly:**
- Urgency theater — ALL CAPS, threats, and pressure signals
- Format pressure as motivation (`{ respond in JSON }` as a nudge instruction)
- Purely mechanical scripts ("invoke /do-test to run the test suite")
- Prompts that assume the model needs to be tricked into performing

---

## 5. Quick Reference

### System Prompt Checklist
- [ ] Role statement that assumes competence and scopes the agent's identity
- [ ] Explicit permission zones: autonomous decisions, flag-and-wait decisions, hard stops
- [ ] Success criteria: what does good output look like at each stage?
- [ ] Escalation channel: narrow opening for genuine critical questions
- [ ] Stakes stated once, calmly

### Nudge Feedback Message Checklist
- [ ] Acknowledge the work just completed
- [ ] Name the next stage and relevant skill/tool
- [ ] State what success looks like at that stage
- [ ] Affirm autonomy ("with discernment", "doing your best work")
- [ ] Reopen escalation channel only if the next stage warrants it

### Single-Sentence Test
Before sending any prompt or nudge feedback message, ask: *does this treat the model as a competent agent or as a machine that needs to be coerced?* If the latter — rewrite it.

---

*Yudame AI Engineering · Internal Reference · March 2026*
