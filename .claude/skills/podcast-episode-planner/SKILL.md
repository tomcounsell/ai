---
name: podcast-episode-planner
description: Transform research materials into structured episode plans that guide NotebookLM audio generation. Creates content_plan.md with three-section structure, Wave 2 structural design, and NotebookLM guidance.
user-invocable: false
---

# Podcast Episode Planner Skill

**Purpose:** Transform research materials into a structured episode plan that guides NotebookLM's two-host audio generation.

**When to Use:** After research is complete (Phase 7 report.md exists) and before audio generation. This skill creates `content_plan.md` - the structural blueprint that NotebookLM uses to create coherent, well-organized podcast audio.

**⭐ REQUIRED: Use the enhanced Wave 2 template from `docs/templates/podcast/content_plan-enhanced.md`**

This template includes all Wave 2 structural improvements. DO NOT use a basic template.

---

## Skill Overview

This skill produces a structured episode plan that:

1. Classifies the episode type and selects appropriate toolkit elements
2. Structures content across three sections (Foundation, Evidence, Application)
3. Identifies key terms, studies, and narrative elements to emphasize
4. Provides guidance for NotebookLM's two-host conversation format

**Key Principles:**
- Plans provide structural guidance for NotebookLM, not verbatim scripts
- Section 1 introduces concepts and methodology — conclusions come later
- Acronyms must be spelled out on first use ("polyunsaturated fatty acids, or PUFA")
- Use metric units or intuitive measures ("handful", "palm-sized") — not imperial

---

## Inputs Required

Before invoking this skill, gather:

### 1. Episode Metadata
```yaml
topic: "[Specific topic/angle]"
core_question: "[The single question this episode answers]"
title: "[Episode title]"
```

### 2. Series Context (if applicable)
```yaml
series_name: "[Series name]"
series_question: "[Core question the series answers]"
position: "[opener | middle | closer | standalone]"
episode_number: [N]
```

### 3. Source Material (from report.md and p3-briefing.md)
```yaml
key_studies:
  - name: "[Study name/author/year]"
    finding: "[Key finding]"
    strength: "[Meta-analysis | RCT | Observational | etc]"

surprising_findings:
  - "[Finding that challenges assumptions]"

contradictions:
  - topic: "[Where sources disagree]"
    resolution: "[How to present this]"

practical_protocols:
  - "[Actionable insight with specific parameters]"
```

---

## Planning Process

### Step 1: Episode Classification

```markdown
## Episode Classification

- **Series Position:** [opener / middle / closer / standalone]
- **Evidence Status:** [consensus / minor conflict / major conflict]
- **Content Density:** [concept-heavy / protocol-heavy / balanced]
```

### Step 2: Toolkit Selection

**Opening Hook** (choose ONE):
| Hook Type | Use When... |
|-----------|-------------|
| Provocative Question | Research contradicts conventional wisdom |
| Surprising Statistic | You have a striking number that reframes the topic |
| Bold Claim | Listeners will gain clear actionable knowledge |
| Counterintuitive Claim | Experts have been wrong and you can show why |
| Stakes Establishment | Health/business risk, time-sensitive topics |

**Takeaway Structure** (choose ONE):
| Structure | Best For |
|-----------|----------|
| Numbered Protocol | Sequential actions where order matters |
| Prioritized Single Action | One intervention dominates |
| Tiered Recommendations | Optimal action depends on baseline |
| Conditional Protocol | Context determines best action |

**Contradiction Handling** (if evidence contested):
| Status | Approach |
|--------|----------|
| Consensus | Standard presentation |
| Minor conflict | Brief acknowledgment |
| Substantive conflict | Present both perspectives with context |

### Step 3: Section Planning

#### Section 1: Foundation
**Focus:** WHY - Introduce concepts and research methodology. Save conclusions for later.

Plan:
- Opening hook content
- Key concepts to introduce (2-3 max)
- Terms that MUST be defined (with pronunciation if unusual)
- Analogies to anchor abstract concepts
- Transition to Section 2

#### Section 2: Evidence
**Focus:** WHAT - Present the research findings.

Plan:
- Key studies to highlight (with institution, year, sample size)
- Evidence clusters (group related findings)
- Where evidence agrees vs. conflicts
- Callbacks to Section 1 concepts
- Transition to Section 3

#### Section 3: Application
**Focus:** HOW - Translate findings to action.

Plan:
- Specific protocols with parameters (timing, frequency, dosage)
- Who this applies to / caveats
- Callback to opening hook (complete the arc)
- Final synthesis

### Step 4: State Tracking

Track what's been established to enable callbacks:

- **Terms defined:** [list - can use freely after definition]
- **Concepts established:** [list - can callback without re-explaining]
- **Open loops:** [questions raised that must be answered by end]

### Step 5: Wave 2 Structural Design

**After classification and toolkit selection, design the structural elements:**

a. **Episode Structure Map (A1.1):** Map when to be philosophical, practical, storytelling, analytical. Define primary mode, duration, purpose, and key elements for each section.

b. **Mode-Switching Framework (A1.2):** Define clear transitions between philosophy, research, storytelling, practical, and landing modes with specific language markers and duration allocation.

c. **Signposting Language (A1.3):** Create opening structure preview, transition phrases, progress markers, and mode-switch signals that listeners can follow.

d. **Depth Budget (A1.4):** Allocate time percentage to each theme using Depth Distribution Analysis from p3-briefing.md. Validate: primary themes get ≥25% each, no primary theme <15%. If runtime ≤30 min, front-load practical content in Section 2.

e. **Problem → Solution Architecture (A2.1):** Separate problem exploration from solution delivery. Choose single-focus or multi-dimensional approach.

f. **Build Toward Resolution (A2.2):** Work backward from main takeaway. Each section must raise stakes or deepen understanding. Closing must feel like a conclusion, not "we ran out of time."

g. **Counterpoint Moments (A2.3):** Design 2-3 moments using Counterpoint Discovery from p3-briefing.md. **CRITICAL: ASSIGN POSITIONS to speakers, not "present both views."** Each counterpoint must include: Topic, Speaker A position, Speaker B position, and language templates.

h. **Episode Arc Template (A3.3):** Opening (Hook + Problem + Structure Preview, 3-5 min), Middle (Escalating depth with mode-switching, 20-30 min), Closing (Synthesis + Takeaways + Callback + CTA, 3-5 min).

### Step 6: Quality Check

Before finalizing, verify:

- [ ] Three sections with clear focus (Foundation/Evidence/Application)
- [ ] Maximum 3-4 major concepts per section
- [ ] All key terms listed with definitions
- [ ] Protocols include specific parameters
- [ ] Opening hook connects to closing callback
- [ ] Episode answers its stated core question

**Wave 2 Structural Checks (REQUIRED):**
- [ ] Episode Structure Map defined (modes, durations, transitions)
- [ ] Mode-Switching Framework applied (each mode has language markers)
- [ ] Signposting language included (preview, transitions, progress markers)
- [ ] Depth Budget validates even coverage (no theme <15% when it deserves more)
- [ ] Problem → Solution architecture clear
- [ ] Episode builds toward clear resolution/takeaway
- [ ] Counterpoint moments designed (2-3 minimum with ASSIGNED POSITIONS)
- [ ] Episode Arc template followed (Opening/Middle/Closing)
- [ ] Call-to-action included

---

## Output Format

Produce `content_plan.md` following the enhanced template at `docs/templates/podcast/content_plan-enhanced.md`.

The output must include ALL of the following sections:

```markdown
# Episode Plan: [Episode Title]

## Episode Metadata
- **Series:** [Series name or "Standalone"]
- **Position:** [Opener / Middle / Closer / Standalone]
- **Core Question:** [The question this episode answers]
- **Episode Type:** [Evidence status] + [Content density]

## Toolkit Selections
- **Hook Type:** [Selected hook]
- **Takeaway Structure:** [Selected structure]
- **Contradiction Handling:** [Approach if applicable]

---

## Structural Design (Wave 2)

### Episode Structure Map
| Section | Primary Mode | Duration | Purpose | Key Elements |
|---------|-------------|----------|---------|--------------|
| Opening | Hook + Problem | 3-5 min | ... | ... |
| Part 1 | [Mode] | [X min] | ... | ... |
| Part 2 | [Mode] | [X min] | ... | ... |
| Part 3 | [Mode] | [X min] | ... | ... |
| Closing | Landing + Synthesis | 3-5 min | ... | ... |

### Mode-Switching Framework
[Define each mode: Philosophy, Research, Storytelling, Practical, Landing]
[Include language markers and duration allocation for each]

### Signposting Language
[Opening structure preview, transition phrases, progress markers, mode-switch signals]

### Depth Budget
| Theme | Importance | Duration | % | Research Depth | Notes |
|-------|-----------|----------|---|----------------|-------|
[Allocate time per theme, validate against p3-briefing.md depth analysis]

### Problem → Solution Architecture
[Problem definition, exploration approach, solution delivery plan]

### Build Toward Resolution
[Main takeaway, how each section builds toward it, momentum check]

### Counterpoint Moments
| Moment | Topic | Speaker A Position | Speaker B Position | Tension Type | Timing |
|--------|-------|-------------------|-------------------|-------------|--------|
[2-3 counterpoints with ASSIGNED POSITIONS, language templates]

### Episode Arc
[Opening (3-5 min), Middle (20-30 min), Closing (3-5 min) breakdown]

---

## NotebookLM Guidance

### Opening Instructions
[Specific guidance for how the hosts should open - the hook to use, tone to set]

### Key Terms to Define
| Term | Definition | Pronunciation (if needed) |
|------|------------|---------------------------|
| [Term 1] | [Clear definition] | [e.g., "poo-fah" for PUFA] |
| [Term 2] | [Clear definition] | |

### Studies to Emphasize
1. **[Study name, Institution, Year]** - [Key finding to highlight]
   - Sample size: [N]
   - Why it matters: [Context]

2. **[Study name]** - [Key finding]

### Stories to Feature (from Story Bank)
1. **[Story title]** - Use at [X min mark] to illustrate [concept]
2. **[Story title]** - Use at [X min mark] to illustrate [concept]

### Narrative Arc

**Section 1: Foundation**
- Primary focus: [What concept/mechanism to establish]
- Key analogy: "[Everyday comparison to anchor the concept]"
- Transition hook: [How to move to evidence]

**Section 2: Evidence**
- Evidence cluster A: [Studies supporting point 1]
- Evidence cluster B: [Studies supporting point 2]
- Conflict to address: [If any, how to present it]
- Callback opportunity: "[Reference to Section 1 concept]"

**Section 3: Application**
- Protocol 1: [Specific action with parameters]
  - Timing: [Specific]
  - Frequency: [Specific]
  - Who: [Target population]
- Protocol 2: [If applicable]
- Caveats: [Important limitations]

### Counterpoint Execution (for NotebookLM)
- At ~[X min]: [Topic] - Speaker A argues [position], Speaker B challenges with [position]
  - Use phrases: "Wait, but doesn't that contradict...", "I see it differently because..."
- At ~[X min]: [Topic] - Speaker A defends [view], Speaker B pushes back with [alternative]

### Closing Instructions
- Callback to opening: [How to reference the opening hook]
- Key takeaway: [Single most important point]
- Call-to-action: [Next step for listener - adapt based on episode_config.json is_public flag]
- Sign-off: [Use closing_script from episode_config.json if provided, otherwise default: "Find the full research and sources at research dot yuda dot me—that's Y-U-D-A dot M-E."]

**Config-Aware Closing:**
Read `episode_config.json` from the episode directory for feed-specific settings:
- `is_public` - Affects CTA messaging
- `closing_script` - Custom closing script if provided
- `sponsor_break` - Whether to include sponsor splice point in structure

---

## Specificity Standards

The hosts should use specific parameters throughout:

| Category | Vague (Avoid) | Specific (Use) |
|----------|---------------|----------------|
| Timing | "in the morning" | "90-120 minutes after waking" |
| Frequency | "regularly" | "3 times per week" |
| Citations | "some studies show" | "A 2023 meta-analysis of 47 trials found" |
| Effects | "significant improvement" | "17% reduction in all-cause mortality" |
| Dosage | "take some magnesium" | "300-400mg magnesium glycinate" |

---

## Attention Maintenance Notes

Remind hosts to:
- Rotate content types every 5-7 minutes (explanation → example → insight)
- Use pattern interrupts every 7-10 minutes
- Signpost major transitions ("Key point here...", "This brings us to...")
- Close any open loops before episode end
```

---

## Templates

### Opening Hook Templates

**Provocative Question:**
> "What if everything you believe about [topic] is fundamentally wrong?"

**Surprising Statistic:**
> "[Specific number] [unexpected comparison]. That's [X times more/less] than [common assumption]."

**Bold Claim:**
> "By the end of this episode, you'll understand exactly how to [specific outcome]."

**Counterintuitive Claim:**
> "The experts have been wrong about this for decades. And the data finally shows us why."

**Stakes Establishment:**
> "This single [factor] predicts [outcome] better than any other—and most people are getting it completely wrong."

### Series Modifier Templates

**Series Frame Opening:**
> "This is the [ordinal] episode in our series on [topic]. Today, we're looking at it through the lens of [perspective]."

**Series Wrap (closer):**
> "This concludes our series on [topic]. Together, these perspectives give you a complete framework for [core question]."

### Callback Templates

- "As we covered earlier, [concept]—this is exactly why [new point]."
- "Remember the mechanism we discussed? This study shows it in action."
- "This brings us back to [opening hook reference]. Now you understand why."

---

## Integration with NotebookLM

The `content_plan.md` file is uploaded to NotebookLM along with:
- `research/p1-brief.md` - Research brief
- `report.md` - Narrative synthesis
- `research/p3-briefing.md` - Master briefing
- `sources.md` - Validated citations

NotebookLM uses `content_plan.md` to:
1. Structure the conversation flow
2. Know which terms to define and when
3. Emphasize the right studies
4. Create coherent narrative arc with callbacks
5. Deliver specific, actionable protocols

The episodeFocus prompt in the NotebookLM API provides additional guidance on tone and style.

---

*Skill Version: 4.0 (Wave 2 structural improvements)*
*Output: content_plan.md with Wave 2 structural design + NotebookLM guidance*
