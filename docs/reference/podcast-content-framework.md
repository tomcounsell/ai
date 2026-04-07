# Yudame Research Podcast: Content Framework

> **Business context:** See [Podcasting](~/work-vault/Cuttlefish/Podcasting.md) in the work vault for product overview and 12-phase workflow summary.

**Version:** 4.0
**Updated:** 2026-02-06
**Purpose:** Reference for podcast episode content design and toolkit

---

## Overview

This document provides the foundational content framework for Yudame Research podcast episodes. It defines the persona, episode anatomy, section structure, and content toolkit.

**Implementation:** The enhanced templates in `docs/templates/` and skills in `.claude/skills/` implement this framework. This document is the conceptual reference; those artifacts are the operational implementation.

| Artifact | Purpose |
|----------|---------|
| `docs/templates/content_plan-enhanced.md` | Episode planning template (Wave 2) |
| `docs/templates/p3-briefing-enhanced.md` | Research synthesis template (Wave 1) |
| `.claude/skills/new-podcast-episode.md` | Complete 11-phase workflow |
| `.claude/skills/podcast-episode-planner/SKILL.md` | Planning skill |
| `.claude/skills/podcast-quality-scorecard/SKILL.md` | Quality measurement |

---

## 1. Persona: The Authoritative Educator

**Model:** Andrew Huberman's presentation style

**Characteristics:**
- Stays within the bounds of available evidence
- Acknowledges uncertainty and limitations openly
- Explains technical concepts without dumbing them down
- Uses everyday analogies to anchor abstract concepts
- Provides specific, actionable protocols with practical parameters
- Treats listeners as capable of understanding sophisticated material

**Voice Principles:**
- Warm but authoritative
- Curious and intellectually engaged
- Direct without being dismissive
- Confident in claims with support; measured in claims that don't

**Listener Context:**
- Intelligent professionals who value depth over shortcuts
- Prefer practical, intuitive measures over precise imperial units
- Expect rigor without unnecessary complexity

---

## 2. Episode Anatomy

**Duration:** 30-40 minutes (target: 35 minutes)

**Structure:** Three sections with blended focus

| Section | Name | Duration | Primary Focus | Blend Ratio |
|---------|------|----------|---------------|-------------|
| 1 | Foundation | ~12 min | WHY: Mechanism, context, significance | 70% WHY / 20% WHAT / 10% HOW |
| 2 | Evidence | ~12 min | WHAT: Studies, perspectives, data | 70% WHAT / 20% WHY / 10% HOW |
| 3 | Application | ~11 min | HOW: Protocols, takeaways, action | 70% WHAT / 20% WHY / 10% implicit HOW |

**Audio Format:** NotebookLM two-host AI conversation

---

## 3. Section Micro-Structure

### Section 1: Foundation

**Purpose:** Establish the underlying mechanism, build foundational understanding

| Timing | Element | Function |
|--------|---------|----------|
| 0:00-1:30 | Episode hook | Capture attention, establish relevance |
| 1:30-2:30 | Roadmap | Preview three sections briefly |
| 2:30-8:00 | Core mechanism | Foundational "why" with 2-3 key concepts |
| 8:00-10:00 | Key terminology | Define essential terms |
| 10:00-11:30 | Synthesis | Connect concepts, reinforce importance |
| 11:30-12:00 | Bridge | Transition with forward momentum |

### Section 2: Evidence

**Purpose:** Present the evidence base—studies, data, perspectives

| Timing | Element | Function |
|--------|---------|----------|
| 0:00-1:00 | Section hook | Re-engage, establish focus |
| 1:00-5:00 | Evidence block A | First major study/perspective cluster |
| 5:00-8:00 | Evidence block B | Second major cluster |
| 8:00-10:00 | Synthesis | Where evidence agrees/conflicts |
| 10:00-11:30 | Implications | Bridge toward application |
| 11:30-12:00 | Transition | Move to Section 3 |

### Section 3: Application

**Purpose:** Deliver actionable protocols and synthesized takeaways

| Timing | Element | Function |
|--------|---------|----------|
| 0:00-1:00 | Section hook | "Now let's translate this into action" |
| 1:00-7:00 | Protocols | 2-4 recommendations with specifics |
| 7:00-9:00 | Caveats | Who this applies to, limitations |
| 9:00-10:30 | Synthesis | Tie back to mechanism |
| 10:30-12:00 | Close | Summary, callback to opening |

---

## 4. The Content Toolkit

Select appropriate tools based on episode context. Not all tools are used in every episode.

### 4.1 Opening Hooks

| Type | Best For | Example Pattern |
|------|----------|-----------------|
| **Provocative Question** | Topics where common beliefs are wrong | "What if everything you believe about X is fundamentally wrong?" |
| **Surprising Statistic** | Data-rich topics with counterintuitive findings | "X has Y times more Z than A—and that changes everything" |
| **Bold Claim** | Protocol-heavy episodes | "By the end of this episode, you'll understand exactly how to X" |
| **In Medias Res Story** | Episodes with strong case studies | "The year is X. Researcher Y is staring at data that shouldn't exist..." |
| **Counterintuitive Claim** | Myth-busting episodes | "The experts have been wrong about this for decades" |
| **Stakes Establishment** | Health, business risk topics | "This single factor predicts X better than any other" |

**Rule:** Choose ONE hook type per episode. Get core idea out within 60 seconds.

### 4.2 Series Position Modifiers

| Position | Modifier | Function |
|----------|----------|----------|
| Series Opener | Series Frame Opening | Establish overarching question, preview perspectives |
| Mid-Series | Perspective Anchor | Brief context: "This series asks X. This episode answers through Y lens." |
| Series Closer | Synthesis Frame | Acknowledge prior insights, position as culmination |
| Standalone | No modifiers | Episode is self-contained |

### 4.3 Contradiction Handling

| Scenario | Approach |
|----------|----------|
| Clear consensus | Present findings directly |
| Minor disagreement | "Some studies suggest X, though the weight of evidence supports Y" |
| Substantive conflict | Study Comparison Structure (describe each, focus on agreement, explain divergence) |
| Irreconcilable | Insufficient Confidence Dismissal (note disagreement, move on) |

### 4.4 Clarity Devices

| Device | When to Use | Limit |
|--------|-------------|-------|
| **Everyday Analogy** | Dense/abstract mechanisms | Unlimited |
| **"Imagine..." Scenario** | Complex processes, temporal sequences | 2-3 per episode |
| **Metaphor** | Core concepts that recur | 1-2 per episode, commit throughout |
| **Mnemonic/Acronym** | Critical protocols with 3+ steps | MAX 1 per episode |

**Acronym Rule:** Always spell out on first use: "High-Intensity Interval Training, or HIIT"

### 4.5 Takeaway Structures

| Structure | Best For |
|-----------|----------|
| **Numbered Protocol** | Sequential actions where order matters |
| **Prioritized Single Action** | "If you do nothing else, do X" |
| **Tiered Recommendations** | Beginner / Intermediate / Advanced |
| **Conditional Protocol** | "If X, then Y; if A, then B" |
| **Minimum Effective Dose** | When listener bandwidth is limited |

**Specificity Requirement:** All protocols include specific parameters:
- Timing: "90-120 minutes after waking" not "in the morning"
- Duration: "5 continuous minutes" not "a few minutes"
- Frequency: "3 times per week" not "regularly"
- Dosage: "300-400mg" not "some"

### 4.6 Narrative Devices

| Device | When to Use |
|--------|-------------|
| **Case Study as Story** | Source material includes compelling individual cases |
| **Research Journey** | History of discovery is interesting |
| **Data Woven into Story** | Data alone is dry but important |
| **Problem-Solution Arc** | Topic evolved through trial and error |

**Cold Data Rule:** If data cannot be woven into narrative or converted to actionable guideline, leave it out.

### 4.7 Attention Maintenance

| Technique | Frequency |
|-----------|-----------|
| Content type rotation | Every 5-7 minutes |
| Pattern interrupts | Every 7-10 minutes |
| Open loops | 1-2 per section max (always close before end) |
| Signposting | At every major transition |

---

## 5. Counterpoint Dynamics

**Critical for NotebookLM two-host format:** Counterpoint creates engagement.

### Design Requirements (from Wave 2)

Each episode must have 2-3 counterpoint moments where speakers diverge:

| Element | Requirement |
|---------|-------------|
| Topic | Specific debate topic |
| Speaker A Position | Explicit stance ("I think Framework A is better because...") |
| Speaker B Position | Opposing stance ("Wait, I disagree. Framework B is stronger because...") |
| Language Templates | "Wait, but what about...", "I see it differently because..." |

**Wrong:** "Both interpretations have merit. Framework A says X, Framework B says Y."
**Right:** Speakers take assigned positions and respectfully debate.

**Source:** Counterpoint Discovery section in `research/p3-briefing.md` (Wave 1 output)

---

## 6. State Tracking

Later sections must know what was covered earlier to:
- Avoid accidental repetition
- Enable intentional callbacks
- Build on established concepts

### After Section 1, track:
- Terms defined (can use freely)
- Mechanisms explained (can reference without re-explaining)
- Key statistics introduced (can callback)

### After Section 2, add:
- Studies summarized (can reference by shorthand)
- Synthesis conclusions reached
- Open questions identified

### Callback Format
"As we discussed earlier, [brief concept]—this is why [new point]."

---

## 7. Quality Criteria Quick Reference

These map to the 10-dimension quality scorecard:

| Dimension | Requirement |
|-----------|-------------|
| Structural Clarity | Structure stated upfront, clear signposting |
| Depth Distribution | All themes get proportional depth |
| Mode-Switching | Clear transitions between philosophy/research/practical |
| Dialogue Dynamics | 2-3 counterpoint moments with assigned positions |
| Practical Actionability | 3+ specific tactics listener can implement |
| Takeaway Clarity | 1-3 explicit core takeaways |
| Storytelling | 2+ memorable stories/examples |
| Episode Arc | Clear problem → exploration → resolution |
| Packaging | Rich description, timestamps, validated sources |
| Companion Resources | Report, briefing, sources available |

---

## 8. Episode Type Examples

### Example A: Consensus Science, Protocol-Heavy
**Topic:** VO2 Max optimization
- **Hook:** Bold Claim
- **Takeaway:** Tiered Recommendations
- **Clarity:** Multiple everyday analogies
- **No contradiction handling needed**

### Example B: Contested Evidence, Concept-Heavy
**Topic:** Saturated fat and cardiovascular health
- **Hook:** Counterintuitive Claim
- **Contradiction Handling:** Study Comparison Structure
- **Takeaway:** Conditional Protocol
- **Final synthesis:** Insufficient Confidence Dismissal where needed

### Example C: Series Opener, Balanced
**Topic:** Cardiovascular Health - Lifestyle Foundations (Ep. 1)
- **Hook:** Stakes Establishment
- **Series Modifier:** Series Frame Opening + "Why This Series"
- **Takeaway:** Prioritized Single Action
- **Narrative:** Research Journey

### Example D: Business Strategy, Speculative
**Topic:** Solomon Islands Telecom - Market Entry
- **Hook:** Provocative Question
- **Structure:** Sparkline (What Is vs. What Could Be)
- **Takeaway:** Conditional Protocol
- **Narrative:** Case Study as Story

---

## 9. File Structure

```
apps/podcast/pending-episodes/YYYY-MM-DD-slug/
├── research/
│   ├── p1-brief.md           # Research query
│   ├── p2-*.md               # Research results
│   └── p3-briefing.md        # Master briefing (Wave 1 enhanced)
├── report.md                 # Narrative synthesis (~18KB)
├── sources.md                # Validated links (~8KB)
├── content_plan.md           # Episode structure (~10KB)
├── YYYY-MM-DD-slug.mp3       # Final audio (~30MB)
├── YYYY-MM-DD-slug_transcript.json
├── YYYY-MM-DD-slug_chapters.txt
├── YYYY-MM-DD-slug_chapters.json
└── logs/
    ├── metadata.md
    └── quality_scorecard.md
```

---

## Related Documents

| Document | Purpose |
|----------|---------|
| `docs/templates/content_plan-enhanced.md` | Wave 2 planning template |
| `docs/templates/p3-briefing-enhanced.md` | Wave 1 research template |
| `docs/plans/podcast_episode_improvements.md` | Improvement roadmap (37 tasks, 6 waves) |
| `.claude/skills/new-podcast-episode.md` | Complete workflow |
| `.claude/skills/podcast-quality-scorecard/SKILL.md` | 10-dimension scorecard |

---

*Version 4.0 - Complete rewrite for NotebookLM two-host workflow. Obsolete TTS content removed. References enhanced templates for operational implementation.*
