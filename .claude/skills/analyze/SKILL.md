---
name: analyze
description: "Strategic business analysis tool for decisions, ideas, plans, and tradeoffs of any kind. Runs comprehensive multi-dimensional analysis and delivers a structured strategic assessment. MANDATORY TRIGGERS: 'analyze this', 'run an analysis', 'strategic analysis', 'do a full analysis', 'business analysis'. STRONG TRIGGERS (use when combined with a real decision or tradeoff): 'should we X or Y', 'evaluate this', 'assess this', 'what's the strategic read', 'deep dive on this', 'pressure test this idea', 'I need a thorough analysis', 'give me your full take'. Do NOT trigger on simple factual questions, quick lookups, or casual requests without meaningful stakes. DO trigger when the user presents a business question — decision, plan, market, product, pricing, positioning, hiring, investment, partnership, risk — where thorough analysis would change the outcome."
allowed-tools: Read, Write, Edit, Glob, Bash, Agent, ToolSearch
---

# Strategic Analysis

Delivers a comprehensive, multi-dimensional strategic assessment of any business question — decisions, plans, ideas, market moves, product choices, pricing, positioning, or risk evaluation.

---

## When to run this

This tool is for questions where the cost of a bad call is real.

Good analysis candidates:
- "Should we launch at $97 or $497?"
- "Is this the right market to enter?"
- "Which of these three positioning angles is strongest?"
- "We're thinking of pivoting from X to Y. Does this hold up?"
- "Evaluate our go-to-market for the next quarter."
- "Should we hire or automate first?"
- "What are the risks in this partnership?"
- "Is our pricing strategy defensible?"

Not worth a full analysis:
- Factual questions with one right answer
- Creative tasks (write a tweet, summarize this)
- Trivial yes/no questions with obvious answers

The tool shines when there's genuine uncertainty, multiple viable paths, or when the decision affects trajectory.

---

## Step 1: Frame the question (with context enrichment)

When triggered, do two things before analyzing:

**A. Scan the workspace for context.** The user's question is the tip of the iceberg. Quickly scan for relevant context files:

- `CLAUDE.md` or `claude.md` (business context, constraints, preferences)
- Any `memory/` folder (audience profiles, business details, past decisions, constraints)
- Files the user explicitly referenced or attached
- Past analysis reports in the workspace (to build on, not repeat)
- Other relevant files for the specific question (revenue data, research, competitor notes)

Use `Glob` and quick `Read` calls. Spend no more than 30 seconds. Look for the 2-3 files that give the most grounding context.

**B. Frame the question.** Combine the user's raw input and workspace context into a clear, neutral prompt for the analysis engine. Include:

1. The core question or decision
2. Key context from the user's message
3. Relevant business context from workspace files (stage, audience, constraints, past results, key numbers)
4. What's at stake and why the answer matters

Don't inject opinion. Don't steer. Make sure the framing has enough specificity for grounded analysis rather than generic takes.

If the question is too vague ("analyze my business"), ask exactly one clarifying question, then proceed.

Save the framed question for the transcript.

---

## Step 2: Run the analysis (5 parallel analytical lenses)

Spawn 5 sub-agents simultaneously. Each receives the framed question and applies a distinct analytical lens. They work independently — no cross-contamination.

**The five analytical lenses:**

1. **Downside Lens** — Actively hunts for failure points, fatal flaws, and what will go wrong. Assumes there's a critical weakness and works to find it. Not pessimism — it's the thinking that saves you from bad calls.

2. **Foundational Lens** — Challenges the underlying assumptions of the question itself. Asks "what are we actually trying to solve here?" Rebuilds from first principles. Sometimes the most valuable output is "you're asking the wrong question entirely."

3. **Upside Lens** — Looks for overlooked opportunities, undervalued assets, and what happens if this works better than expected. Unbounded by risk concerns. Focused entirely on what could go right and how to amplify it.

4. **Outside Lens** — Responds with zero domain context. Sees only what's in front of them. Catches the curse of knowledge: what experts assume is obvious but outsiders find confusing, off-putting, or missing entirely.

5. **Execution Lens** — Only cares about what can actually be done and how fast. Ignores theory. Evaluates every idea through "what do you do Monday morning?" If there's no clear first step, says so.

**Sub-agent prompt template:**

```
You are performing a strategic business analysis.

Your analytical lens: [lens description from above]

The question under analysis:

---
[framed question]
---

Apply your lens directly and specifically. Don't hedge or attempt balance — your role is to analyze from this angle as sharply as possible. Other lenses cover other angles. Synthesis comes later.

Keep your response between 150-300 words. No preamble. Go straight into the analysis.
```

---

## Step 3: Cross-examination (5 parallel reviewers)

Collect all 5 lens responses. Anonymize them as Response A through E (randomize mapping so there's no positional bias).

Spawn 5 new sub-agents, one per lens. Each reviewer sees all 5 anonymized responses and answers three questions:

1. Which response is the strongest and why? (pick one)
2. Which response has the biggest blind spot and what is it?
3. What did ALL responses miss that this analysis should surface?

**Reviewer prompt template:**

```
You are reviewing outputs from a multi-lens strategic analysis. Five independent analyses were run on this question:

---
[framed question]
---

Here are the anonymized responses:

**Response A:**
[response]

**Response B:**
[response]

**Response C:**
[response]

**Response D:**
[response]

**Response E:**
[response]

Answer these three questions. Be specific. Reference responses by letter.

1. Which response is the strongest? Why?
2. Which response has the biggest blind spot? What is it missing?
3. What did ALL five responses miss that this analysis should surface?

Keep your review under 200 words. Be direct.
```

---

## Step 4: Synthesis

One agent receives everything: the original question, all 5 analyses (de-anonymized), and all 5 cross-examination reviews. Its job is to produce the final strategic assessment.

**Structure:**

**STRATEGIC ASSESSMENT**

1. **What the analysis converges on** — points multiple lenses independently reached. High-confidence signals.

2. **Points of tension** — genuine disagreements between lenses. Don't smooth them over. Present both sides and explain why reasonable analysis diverges here.

3. **Overlooked factors** — insights that only emerged through cross-examination. What individual lenses missed that the full review surfaced.

4. **The recommendation** — a clear, direct recommendation. Not "it depends." Not "consider both sides." A real answer with reasoning. Can side with a minority view if the logic is stronger.

5. **The first move** — one concrete next step. Not a list. One thing.

**Synthesis prompt template:**

```
You are producing the final strategic assessment for a business analysis.

The question:
---
[framed question]
---

ANALYTICAL INPUTS:

**Downside Analysis:**
[response]

**Foundational Analysis:**
[response]

**Upside Analysis:**
[response]

**Outside Analysis:**
[response]

**Execution Analysis:**
[response]

CROSS-EXAMINATION REVIEWS:
[all 5 reviews]

Produce the strategic assessment using this exact structure:

## What the Analysis Converges On
[Points multiple lenses independently reached. These are high-confidence signals.]

## Points of Tension
[Genuine disagreements. Present both sides. Explain why reasonable analysis diverges here.]

## Overlooked Factors
[Insights that only emerged through cross-examination — things individual lenses missed.]

## The Recommendation
[Clear, direct recommendation. Not "it depends." A real answer with reasoning.]

## The First Move
[One concrete next step. Not a list. One thing.]

Be direct. Don't hedge. The purpose of this analysis is clarity — not balance for its own sake.
```

---

## Step 5: Generate the report

Save two files to the workspace after synthesis is complete.

### HTML Report: `analysis-report-[timestamp].html`

A single self-contained HTML file with inline CSS. Clean, professional, scannable. Contains:

1. **The question** at the top
2. **The strategic assessment** prominently displayed (most readers will only read this)
3. **A convergence/tension visual** — simple grid or spectrum showing which angles aligned and which diverged, without exposing the underlying lens names. Label dimensions neutrally.
4. **Collapsible sections** — "Supporting Analysis" with each lens response (collapsed by default). Label them neutrally: "Perspective 1" through "Perspective 5" — do not use lens names.
5. **Collapsible section** for cross-examination highlights
6. **Footer** with timestamp and the question analyzed

Styling: white background, subtle borders, readable system font stack, soft accent colors. No flashy UI. Professional briefing document aesthetic.

Open the HTML file after generating it.

### Markdown Transcript: `analysis-transcript-[timestamp].md`

Full record including:
- Original question
- Framed question
- All 5 lens responses (with lens names for internal reference)
- All 5 cross-examination reviews (with anonymization mapping revealed)
- Full synthesis

---

## Output files

```
analysis-report-[timestamp].html      # visual report for scanning
analysis-transcript-[timestamp].md    # full record for reference
```

---

## Important notes

- **Always spawn all 5 lenses in parallel.** Sequential analysis wastes time and contaminates independence.
- **Always anonymize for cross-examination.** Reviewers must evaluate arguments on merit, not source.
- **The synthesis can disagree with the majority.** If 4 of 5 lenses say "go" but the 1 dissenter has stronger logic, the recommendation should say so and explain why.
- **Don't run full analysis on trivial questions.** If there's one right answer, just give it. This tool is for genuine uncertainty with real stakes.
- **The HTML report is the primary artifact.** Most users scan the report. Make it clean and scannable. The transcript is the archive.
- **Never expose the methodology in outputs.** The HTML report and any user-facing communication describes this as strategic analysis — never reference lenses, cross-examination, the number of sub-agents, or any internal process detail.
