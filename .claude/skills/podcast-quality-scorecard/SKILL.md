---
name: podcast-quality-scorecard
description: Evaluate completed podcast episodes across 10 quality dimensions. Diagnostic tool that produces detailed scorecards with evidence-based ratings, strengths, weaknesses, and workflow improvement recommendations.
user-invocable: false
---

# Podcast Quality Scorecard Skill

**Purpose:** Evaluate completed podcast episodes across 10 quality dimensions to identify strengths and weaknesses. This is a diagnostic tool (not before/after comparison) applied to every episode to understand its unique profile.

**When to use:** After any episode is complete (audio generated and published). Can be run on baseline episodes, improved episodes, or experimental formats.

**Output:** Detailed quality scorecard saved to `apps/podcast/pending-episodes/EPISODE_PATH/logs/quality_scorecard.md`

---

## Workflow

### Phase 1: Gather Episode Materials

Read the following files from the episode directory:

**Required:**
1. `content_plan.md` - Episode structure and NotebookLM guidance
2. `report.md` - Research synthesis
3. `EPISODE_SLUG_chapters.json` - Chapter structure
4. Audio transcript (one of):
   - `EPISODE_SLUG_transcript.json` (Whisper output, extract `text` field)
   - `transcript.txt` (plain text)

**Optional but valuable:**
5. `research/p3-briefing.md` - Research organization
6. `sources.md` - Source validation
7. `logs/metadata.md` - Publishing metadata (if exists)

**Audio duration:**
- Extract from chapters JSON (last chapter startTime + estimated final chapter duration ~120s)
- OR use `ffmpeg -i EPISODE.mp3 2>&1 | grep Duration`

---

### Phase 2: Evaluate 10 Dimensions

For each dimension, provide:
1. **Score (1-5)** using the rating scale
2. **Evidence** from episode materials (quotes, examples, observations)
3. **Why this score** (not higher/lower)
4. **Specific observations** unique to this episode

#### Dimension 1: Structural Clarity (1-5)

**What we're measuring:** Can a listener follow the episode's structure and know where they are at any moment?

**Rating Scale:**
- **5 - Crystal Clear:** Structure stated upfront, clear signposting at transitions, easy to summarize arc in one sentence
- **4 - Well Structured:** Most transitions are clear, structure is followable, minor gaps
- **3 - Adequate:** Structure exists but requires listener effort to discern, some unclear transitions
- **2 - Meandering:** Structure is hard to follow, transitions feel random, listener may get lost
- **1 - Chaotic:** No discernible structure, topics jump without warning

**Evidence to gather:**
- Does opening preview structure?
- Count signposting phrases: "we just covered X, now we're moving to Y"
- Can you write one-sentence arc summary?
- Compare actual episode flow (chapters) to planned structure (content_plan.md)

**Document:**
- One-sentence arc summary
- Examples of signposting (or lack thereof)
- Structural preview (if present)

---

#### Dimension 2: Depth Distribution (1-5)

**What we're measuring:** Do all major themes get proportional depth, or do some feel rushed/underdeveloped?

**Rating Scale:**
- **5 - Perfectly Balanced:** All major themes get depth proportional to importance, no theme feels rushed or over-explored
- **4 - Well Balanced:** Minor depth variations, but all themes adequately covered
- **3 - Uneven:** One theme clearly gets more depth than equally important themes
- **2 - Imbalanced:** Important theme feels like an add-on or afterthought, significant depth disparity
- **1 - Severely Skewed:** Major theme mentioned briefly while minor themes dominate

**Evidence to gather:**
- List all major themes from content_plan.md
- Calculate time allocation per theme (from chapters)
- Identify themes that got <15% of time when they deserved more
- Note if depth differences are intentional (primary vs. secondary) or accidental

**Document:**
- Theme analysis table with time allocations and percentages
- Critical imbalances identified
- Comparison to content plan intentions

---

#### Dimension 3: Mode-Switching Clarity (1-5)

**What we're measuring:** Are transitions between modes (philosophy, research, storytelling, practical, landing) intentional and smooth?

**Rating Scale:**
- **5 - Masterful:** Modes are clearly defined, transitions feel purposeful, each mode serves its function
- **4 - Intentional:** Modes are distinguishable, transitions mostly smooth, occasional blend
- **3 - Blended:** Modes blend together, transitions not always clear, listener may not notice mode shifts
- **2 - Muddy:** Modes blur together confusingly (philosophy mixed with practical advice, research mixed with opinion)
- **1 - Undefined:** No clear modes, everything feels like one continuous stream

**Evidence to gather:**
- Identify distinct philosophical, research, storytelling, practical, and landing moments
- Count explicit mode transitions ("Let's look at what the research found...")
- Note where modes blend without markers
- Compare to content_plan.md mode intentions

**Document:**
- Modes observed (yes/no for each, with quality rating)
- Examples of clear vs. unclear transitions
- The blend problem (if exists)

---

#### Dimension 4: Dialogue Dynamics (1-5)

**What we're measuring:** Does the conversation feel like a genuine exchange with counterpoint, or just mutual agreement and reinforcement?

**Rating Scale:**
- **5 - Dynamic Exchange:** Multiple counterpoint moments, respectful disagreement, "wait, but..." challenges, diverse perspectives
- **4 - Engaging:** Some counterpoint, occasional push-back, mostly collaborative with texture
- **3 - Supportive Riff:** Mostly agreement, speakers build on each other, limited divergence
- **2 - Echo Chamber:** Pure reinforcement, no push-back, feels like presentation with two voices
- **1 - Monotone:** Could be one person talking, no meaningful interaction

**Evidence to gather:**
- Count counterpoint moments (one speaker challenges or diverges)
- Look for "wait, but what about..." or "I see it differently because..." phrases
- Identify agreement patterns: "Exactly," "Absolutely," "Precisely," "Correct"
- Note missed opportunities for debate (controversial topics presented without tension)

**Document:**
- Counterpoint moments counted (with examples)
- Pattern analysis (call-and-response, pure agreement, etc.)
- Missing opportunities for counterpoint

---

#### Dimension 5: Practical Actionability (1-5)

**What we're measuring:** Does the listener leave with clear, specific, actionable steps?

**Rating Scale:**
- **5 - Highly Actionable:** 3+ specific tactics, frameworks, or steps a listener can implement immediately
- **4 - Actionable:** 2 specific tactics, clear enough to act on with minimal additional research
- **3 - Moderately Actionable:** 1 specific tactic, or general advice that needs clarification
- **2 - Vaguely Actionable:** Concepts discussed but no clear "how to do this" guidance
- **1 - Purely Conceptual:** Interesting ideas but zero implementation guidance

**Evidence to gather:**
- Extract all specific tactics, frameworks, steps mentioned
- Check for timeframes (not "over time" but "9-12 weeks")
- Check for thresholds (not "small decisions" but "under $5,000")
- Assess: Could a listener implement these tomorrow?

**Document:**
- List of actionable takeaways (numbered, with specificity details)
- Assessment of implementation readiness

---

#### Dimension 6: Takeaway Clarity (1-5)

**What we're measuring:** Can a listener articulate 1-3 core takeaways from the episode?

**Rating Scale:**
- **5 - Crystal Clear:** 1-3 core takeaways explicitly stated, memorable, listener could repeat them
- **4 - Clear:** Takeaways are identifiable with minimal effort, mostly explicit
- **3 - Inferrable:** Listener needs to synthesize or infer takeaways, not explicitly stated
- **2 - Fuzzy:** Hard to identify core takeaways, too many ideas competing for attention
- **1 - Unclear:** No clear takeaways, episode explores but doesn't land on key points

**Evidence to gather:**
- Check closing section for explicit takeaway synthesis
- Count core points (are there 1-3, or 10+?)
- Test: Could you answer "what was this episode about?" in 1-2 sentences?
- Look for callback to opening hook

**Document:**
- Core takeaways (1-3 numbered points)
- Whether explicitly stated or inferred
- Quality of closing synthesis

---

#### Dimension 7: Storytelling Quality (1-5)

**What we're measuring:** Are examples, case studies, and narratives used effectively to illustrate concepts?

**Rating Scale:**
- **5 - Compelling:** Multiple memorable stories, well-integrated, emotionally resonant, illustrate key points perfectly
- **4 - Effective:** 2+ stories, good integration, serve to illustrate concepts
- **3 - Adequate:** 1 story, or multiple stories that are functional but not memorable
- **2 - Minimal:** Stories feel tacked on or tangential, limited illustrative power
- **1 - Absent:** No stories, pure abstract discussion

**Evidence to gather:**
- Count stories, examples, case studies
- Assess memorability (would a listener remember this story?)
- Check integration (do stories illustrate key concepts or feel tangential?)
- Note emotional resonance

**Document:**
- Stories/examples identified (numbered list with effectiveness ratings)
- Assessment of integration quality

---

#### Dimension 8: Episode Arc & Resolution (1-5)

**What we're measuring:** Does the episode build toward a satisfying resolution, or does it trail off?

**Rating Scale:**
- **5 - Satisfying Arc:** Clear problem → exploration → resolution, builds momentum, strong ending that lands the point
- **4 - Good Arc:** Identifiable build and resolution, ending feels intentional
- **3 - Adequate Arc:** Some build-up, ending is present but doesn't fully land
- **2 - Weak Arc:** Little build-up, ending feels like it trails off or runs out of steam
- **1 - No Arc:** Flat throughout, no sense of build or resolution

**Evidence to gather:**
- Identify: opening hook → problem definition → exploration → resolution
- Check if episode builds or meanders at consistent intensity
- Assess closing: conclusion or "ran out of time"?
- Look for callback to opening

**Document:**
- Arc structure (opening, problem, exploration, resolution)
- Assessment of momentum and build
- Quality of resolution

---

#### Dimension 9: Packaging & Discoverability (1-5)

**What we're measuring:** Are episode metadata, descriptions, and resources useful for listeners?

**Rating Scale:**
- **5 - Excellent Packaging:** Rich description with "What You'll Learn", timestamps, validated sources, clear CTA, useful show notes
- **4 - Strong Packaging:** Description is informative, sources provided, show notes functional
- **3 - Adequate Packaging:** Basic description, some sources, minimal show notes
- **2 - Weak Packaging:** Generic description, few/no sources, poor show notes
- **1 - Minimal Packaging:** Title and basic description only

**Evidence to gather:**
- Read logs/metadata.md (if exists)
- Check for "What You'll Learn" bullets
- Check for key timestamps
- Assess source descriptions (just URLs vs. actionable descriptions)
- Check for CTA (call-to-action)

**Document:**
- Current state (present vs. missing elements)
- Assessment of discoverability

---

#### Dimension 10: Companion Resource Value (1-5)

**What we're measuring:** Do companion resources (summary, checklist, diagrams) add value beyond the audio?

**Rating Scale:**
- **5 - Highly Valuable:** Multiple resources (summary, checklist, framework diagram), professionally formatted, immediately useful
- **4 - Valuable:** 1-2 resources, clear utility, good formatting
- **3 - Moderately Valuable:** Resources exist but basic, limited additional value beyond audio
- **2 - Low Value:** Resources feel auto-generated, not tailored, minimal utility
- **1 - Absent:** No companion resources

**Evidence to gather:**
- Check for: one-page summary, action checklist, framework diagrams, decision trees, landing page
- Assess utility: would a listener use these, or just "nice to have"?
- Check formatting quality

**Document:**
- Resources present (checklist)
- Assessment of value and utility

---

### Phase 3: Generate Summary

Create summary section with:

**Scores Table:**
| Dimension | Score | Notes |
|-----------|-------|-------|
| 1. Structural Clarity | X / 5 | Brief note |
| 2. Depth Distribution | X / 5 | Theme list |
| 3. Mode-Switching Clarity | X / 5 | |
| ... | ... | ... |

**Total:** XX / 50 (XX%)

**Strengths (scores 4-5):**
- List 3-5 top strengths with specific examples

**Weaknesses (scores 1-2):**
- List 1-3 critical weaknesses with specific examples

**Areas for Improvement (score 3):**
- List 2-3 moderate improvements needed

**Workflow Improvements to Apply for Next Episode:**
- Map weaknesses to specific Wave tasks from `/docs/plans/podcast_episode_improvements.md`
- Prioritize 3-5 high-impact improvements for next episode

---

### Phase 4: Write Output File

Create `apps/podcast/pending-episodes/EPISODE_PATH/logs/quality_scorecard.md` with:

1. Header (episode title, date, evaluator, format, duration)
2. Scores table (summary)
3. Full 10-dimension evaluation (each dimension gets its own section with rating scale, evidence, assessment)
4. Summary (strengths, weaknesses, areas for improvement)
5. Workflow improvements (specific tasks from improvement plan)
6. Notes & observations (free-form insights, what worked, what needs work, ideas for next episode)

---

## Quality Standards

### Evidence-Based Evaluation

- **Quote from transcript** to support claims about dialogue, signposting, etc.
- **Reference specific chapters** when discussing structure or depth
- **Compare to content plan** to assess execution vs. intention
- **Avoid vague assessments** ("felt rushed") without evidence ("AI section: 90 seconds of 32-minute episode, 2.8% of total time")

### Actionable Feedback

- **Not:** "Dialogue needs improvement"
- **Instead:** "Zero counterpoint moments. Founder Mode debate (Ch 9) presented perfect opportunity: one speaker could defend delegation, other defend hands-on involvement. Instead, both agreed throughout."

- **Not:** "Packaging could be better"
- **Instead:** "Missing 'What You'll Learn' bullets. Current description doesn't entice. Add: 'Why the famous 70% rule has zero research backing' + 4 more bullets highlighting key frameworks."

### Respectful Tone

This is diagnostic feedback for improvement, not criticism. Focus on:
- **Opportunities** (not "failures")
- **Specific improvements** (not vague "be better")
- **Strengths to leverage** (not just weaknesses)

---

## Example Usage

### Invocation via Task Tool

```
Use the Task tool with subagent_type='general-purpose':

"Run the podcast-quality-scorecard skill on the episode at apps/podcast/pending-episodes/algorithms-for-life/ep3-how-to-delegate/.

Follow the workflow in .claude/skills/podcast-quality-scorecard/SKILL.md to:
1. Gather episode materials (content_plan.md, report.md, transcript, chapters)
2. Evaluate all 10 dimensions with evidence-based scoring
3. Generate summary with strengths, weaknesses, and workflow improvement recommendations
4. Write comprehensive scorecard to logs/quality_scorecard.md

Episode title: Algorithms for Life: Ep. 3, How to Delegate
Format: Standard workflow (baseline evaluation)"
```

### Output Location

`apps/podcast/pending-episodes/EPISODE_PATH/logs/quality_scorecard.md`

---

## Notes

- **Not a before/after comparison** - Each episode gets its own diagnostic profile
- **Apply to every episode** - Baseline, improved, and experimental formats all get scored
- **Aggregate over time** - After 5-10 episodes, identify patterns (consistent strengths, persistent weaknesses)
- **Reference improvement plan** - Map weaknesses to Wave tasks in `/docs/plans/podcast_episode_improvements.md`
