# Podcast Episode Improvements Plan

**Created:** 2026-01-29
**Updated:** 2026-01-30
**Based on:** Feedback from Matthew Rideout on "Algorithms for Life, Ep. 3 — How to Delegate"
**Goal:** Improve episode structure, depth distribution, dialogue dynamics, packaging, and listener learning outcomes

---

## Status Update (2026-02-04 - Wave 1 Validated, Wave 2 Next)

### ✅ Completed: Measurement Framework

**Phase 0: Quality Measurement System**
- [x] Created 10-dimension quality scorecard framework
- [x] Applied scorecard to baseline episode (Delegation, scored 33/50)
- [x] Validated scorecard against human feedback (90% confidence, 100% coverage)
- [x] Created reusable skill at `.claude/skills/podcast-quality-scorecard/SKILL.md`
- [x] Integrated scorecard into workflow (applied to every future episode)

**Artifacts:**
- Scorecard template: `.claude/skills/podcast-quality-scorecard/SKILL.md`
- Baseline scorecard: `apps/podcast/pending-episodes/algorithms-for-life/ep3-how-to-delegate/logs/quality_scorecard.md`

### ✅ Wave 1 - Research & Synthesis: VALIDATED & COMPLETE

**Status:** All 5 Wave 1 tasks enforced and validated on Stablecoin Ep. 8 (Feb 4, 2026).

**🎉 Validation Results:**
- **Pre-refactoring baseline (Feb 3):** 28/50 (56%)
- **Post-Wave 1 (Feb 4):** 44/50 (88%)
- **Improvement:** +16 points (+32%) ✅

**Dimension improvements:**
- Companion Resources: 2 → 5 (+3) 🚀
- Depth Distribution: 2 → 4 (+2) 🎯
- Takeaway Clarity: 4 → 5 (+1)
- Storytelling Quality: 4 → 5 (+1)
- Structural Clarity: 4 → 5 (+1)
- Episode Arc: 4 → 5 (+1)
- Practical Actionability: 5 → 5 (maintained perfection)

**Enforcement validated:**
- ✅ Phase 6 exit criteria successfully blocked progression without quality inputs
- ✅ All 5 Wave 1 sections completed in p3-briefing.md
- ✅ Synthesis agent input validation worked as designed (no blocking errors)
- ✅ 5 of 10 dimensions directly improved by Wave 1 achieved 4-5 scores

| Task | Template | Workflow | Exit Criteria | Synthesis Agent | Status |
|------|----------|----------|---------------|-----------------|--------|
| **B1.1** - Depth Distribution | ✅ | ✅ Required | ✅ Blocking | ✅ Validated | ✅ **ENFORCED** |
| **B1.2** - Counterpoint Discovery | ✅ | ✅ Required | ✅ Blocking | ✅ Validated | ✅ **ENFORCED** |
| **B1.3** - Practical Audit | ✅ | ✅ Required | ✅ Blocking | ✅ Blocking | ✅ **ENFORCED** |
| **B2.1** - Takeaway Clarity | ✅ | ✅ Required | ✅ Blocking | ✅ Blocking | ✅ **ENFORCED** |
| **B2.2** - Story Bank | ✅ | ✅ Required | ✅ Blocking | ✅ Blocking | ✅ **ENFORCED** |

**What was implemented (Feb 1, 2026):**

1. **✅ Phase 6 Workflow Updated** (`.claude/skills/new-podcast-episode.md`)
   - Now REQUIRES enhanced template (`docs/templates/p3-briefing-enhanced.md`)
   - Lists all Wave 1 sections as required structure (12 sections total)
   - Added **"PHASE 6 EXIT CRITERIA - WAVE 1 ENFORCEMENT"** section
   - BLOCKING: Cannot proceed to Phase 7 without all Wave 1 sections complete

2. **✅ Phase 6 Exit Criteria Added (BLOCKING)**
   - Depth Distribution Analysis table with depth ratings (B1.1)
   - Practical Implementation Audit with concrete steps (B1.3)
   - Story Bank with 3-5 stories minimum (B2.2)
   - Counterpoint Discovery with 2-3 dialogue opportunities (B1.2)
   - Notes for Synthesis Agent with takeaway requirements (B2.1)
   - **Enforcement:** Workflow explicitly states "DO NOT PROCEED" if missing

3. **✅ Synthesis Agent Enforcement** (`.claude/agents/podcast-synthesis-writer.md`)
   - Added Wave 1 **input validation as first step** (BLOCKING)
   - Agent **fails immediately** with error if any Wave 1 section missing
   - Wave 1 checklist moved from optional to **first checklist section** (blocking)
   - Error message: "Wave 1 requirements incomplete in research/p3-briefing.md. Missing: [list]. Phase 6 must be completed before synthesis can proceed."

**Enforcement Flow:**
```
Phase 6 → Exit Criteria Check → If Wave 1 incomplete → STOP (cannot proceed)
                               → If Wave 1 complete → Phase 7
                                                    ↓
Phase 7 → Synthesis Agent → Input Validation → If Wave 1 missing → FAIL with error
                                              → If Wave 1 present → Create report.md
```

**Next Episode Will:**
1. **Must** create enhanced p3-briefing.md with all 12 sections (not 8)
2. **Cannot** proceed to synthesis until Phase 6 exit criteria verified
3. **Will fail** if synthesis agent doesn't find Wave 1 sections
4. **Must** meet all Wave 1 quality standards in report.md

**Testing Status:** ⚠️ Ready for validation on next episode production. Until an episode is produced using this workflow, actual usage remains untested.

**Artifacts Created:**
- Enhanced workflow: `.claude/skills/new-podcast-episode.md` (Phase 6 with exit criteria)
- Blocking agent: `.claude/agents/podcast-synthesis-writer.md` (input validation + checklist)
- Enhanced template: `docs/templates/p3-briefing-enhanced.md` (reference spec)

**Validation Episode:**
- Stablecoin Series Ep. 8 - Post-Launch Operations (Feb 4, 2026)
- Scorecard: `apps/podcast/pending-episodes/stablecoin-series/ep8-post-launch-operations/logs/quality_scorecard.md`
- Pre-refactoring scorecard archived: `logs/archive/quality_scorecard_pre-refactoring_2026-02-03.md`

### 📚 Lessons Learned from Wave 1 Validation (Episode 8)

**What Worked Exceptionally Well:**

1. **Research quality → audio quality translation is real**
   - Depth Distribution Analysis (B1.1) → Balanced coverage across 7 themes
   - Practical Implementation Audit (B1.3) → All protocols had budgets, thresholds, timelines
   - Story Bank (B2.2) → 5 stories with memorability ratings led to compelling storytelling
   - Takeaway Clarity (B2.1) → 3 explicit takeaways in closing

2. **Exit criteria enforcement works perfectly**
   - No false blocks, no workarounds attempted
   - All 5 Wave 1 sections present before synthesis could proceed
   - Synthesis agent validated inputs successfully

3. **Opening hook execution matters**
   - $908M Coinbase payment was specific, surprising, thesis-anchoring
   - Got powerful callback in closing for narrative resolution
   - Lesson: Opening hooks should be concrete numbers, not abstract statements

**Critical Gap Identified:**

1. **🚨 Counterpoint research ≠ audio execution**
   - Counterpoint Discovery (B1.2) completed: 3 debates documented in p3-briefing.md
   - BUT: Not executed in audio as positional dialogue
   - Hosts presented both views collaboratively instead of taking opposing positions
   - Result: Dimension 4 (Dialogue Dynamics) only 3/5 (should be 4-5)
   - **Root cause:** content_plan.md didn't instruct speakers to TAKE POSITIONS, only to "present both frameworks"
   - **Fix:** Wave 2 Tasks A2.3 + A3.2 need MUCH STRONGER execution language (see updates below)

2. **Runtime constraints create end-of-episode compression**
   - 28:55 episode compressed operator's playbook to 2% (40 seconds)
   - Practical content at episode end gets squeezed when time runs out
   - **Fix:** Wave 2 Task A1.4 (Depth Budget) needs guidance to front-load practical content

**Packaging Opportunities:**

3. **Wave 4 is independent and low-effort**
   - Description, timestamps, CTAs can be added without affecting audio
   - Template-driven, can be applied retroactively
   - **Action:** Prioritize C1.1-C1.3 as HIGH PRIORITY (quick wins)

**Next Wave Strategy:**
- Wave 1: ✅ COMPLETE (validated +16 points)
- Wave 2: 🎯 NEXT (address counterpoint execution gap, add depth budget guidance)
- Wave 4: 🔄 IN PARALLEL (packaging improvements, can do immediately)

### Status: Waves 1-5 COMPLETE (2026-02-10)

- **Wave 1:** Research & Synthesis (5 tasks) - ✅ **COMPLETE** (validated on Ep. 8, +16 pts)
- **Wave 2:** Episode Planning (9 tasks) - ✅ **COMPLETE** (templates + exit criteria enforced)
- **Wave 3:** Audio Generation (4 tasks) - ✅ **COMPLETE** (episodeFocus enhanced with structural/dialogue/arc guidance)
- **Wave 4:** Publishing & Productization (11 tasks) - ✅ **COMPLETE** (update_feed.py enhanced, companion resources integrated)
- **Wave 5:** Quality Gates (2 tasks) - ✅ **COMPLETE** (Phase 8 + Phase 11 exit criteria enforced)
- **Wave 6:** Format Experiments (6 tasks) - NOT STARTED

**What was implemented (2026-02-10):**

**Wave 2 completion:**
- Updated `podcast-episode-planner` skill (v4.0) to produce Wave 2 structural sections
- Added Wave 2 exit criteria to Phase 8 workflow (Episode Structure Map, Mode-Switching, Signposting, Depth Budget, Counterpoint Moments with assigned positions, Episode Arc)
- Enhanced template referenced: `docs/templates/content_plan-enhanced.md`

**Wave 3 completion:**
- Enhanced `notebooklm_prompt.py` `generate_prompt()` with STRUCTURAL GUIDANCE, DIALOGUE DYNAMICS, and EPISODE ARC sections
- Enhanced `notebooklm_api.py` `generate_episode_focus()` with identical improvements
- Dialogue dynamics now explicitly instruct speakers to TAKE POSITIONS and DISAGREE (not collaborative framing)
- Added specific disagreement phrases and synthesis guidance

**Wave 4 completion:**
- Enhanced `update_feed.py` to parse "What You'll Learn", Key Timestamps, CTA from metadata.md
- `generate_content_encoded()` now produces structured HTML: Overview, What You'll Learn, Timestamps, Resources, Report link, CTA
- Added `<podcast:transcript>` tag support (links to transcript.txt)
- Integrated companion resource scripts into Phase 11 workflow
- Phase 11 now references enhanced metadata template

**Wave 5 completion:**
- Phase 8 exit criteria enforce all Wave 2 structural sections (blocking)
- Phase 11 exit criteria enforce all Wave 4 packaging sections (blocking)
- Both quality gates prevent proceeding without complete quality requirements

**Next Action:** Wave 6 (Format Experiments) available when ready. Validate Waves 2-5 on next episode production.

---

## Executive Summary

The feedback identifies six key improvement areas:
1. **Structure & Flow** - More intentional mode-switching and signposting
2. **Depth Distribution** - Even coverage across all major themes
3. **Dialogue Dynamics** - Introduce counterpoint and divergence
4. **Packaging & Productization** - Better CTAs, descriptions, and resources
5. **Format Experimentation** - Test different structural approaches
6. **Listener Learning** - Clear takeaways and actionable insights

---

## Improvement Categories

### A. Content Planning & Structure (Phase 8: Episode Planning)

These improvements affect how we plan episode content before audio generation.

#### A1. Structural Clarity Tasks

- [ ] **A1.1** - Add "Episode Structure Map" section to `content_plan.md`
  - Map out: when to be philosophical, practical, storytelling, analytical
  - Define clear transitions between modes
  - Create signposting language for NotebookLM to use

- [ ] **A1.2** - Create "Mode-Switching Framework" in content_plan.md
  - **Philosophy mode:** When to explore abstract concepts, frameworks, mental models
  - **Research mode:** When to cite studies, statistics, evidence
  - **Storytelling mode:** When to share examples, case studies, narratives
  - **Practical mode:** When to provide actionable advice, tactics, implementation
  - **Landing mode:** When to synthesize, summarize, drive home key points

- [ ] **A1.3** - Add "Signposting Language" section
  - Template phrases for transitions: "We just covered X, now let's explore Y"
  - Opening structure preview: "In this episode, we'll first explore X, then Y, and finally Z"
  - Progress markers: "So far we've covered X, which sets us up for Y"

- [ ] **A1.4** - Implement "Depth Budget" planning
  - Allocate time percentage to each major theme (e.g., AI: 35%, Leadership types: 35%, Implementation: 30%)
  - Flag when themes need equal treatment vs. when one is meant to be primary
  - Ensure sub-topics get proportional depth to their importance
  - **⚠️ Runtime constraint guidance (Episode 8 lesson):**
    - If runtime is ≤30 min, compression happens at episode END when time runs out
    - **Front-load practical content** - Place in Section 2 (Evidence) instead of Section 3 (Application)
    - Example: For 30-min episode, allocate Foundation 30% (9 min), Evidence 45% (13.5 min), Application 25% (7.5 min)
    - Validate: Does Application section have enough allocated time to deliver protocols in detail?
  - Use Depth Distribution Analysis from p3-briefing.md (B1.1) to inform budget allocation

#### A2. Content Architecture Tasks

- [ ] **A2.1** - Add "Problem Definition → Solution Architecture" framework
  - Separate problem exploration from solution delivery
  - Design episodes to either: (a) go deep on one aspect, or (b) clearly preview multiple angles
  - Document this choice explicitly in content_plan.md

- [ ] **A2.2** - Create "Build Toward Resolution" structure
  - Identify the main takeaway/resolution point
  - Work backward to ensure each section builds toward it
  - Avoid trailing-off endings by front-loading philosophical content

- [ ] **A2.3** - Design "Counterpoint Moments" into structure **⚠️ EXECUTION CRITICAL**
  - Identify 2-3 moments per episode where speakers should diverge or push back
  - **🚨 ASSIGN POSITIONS - not just "present both views"**
    - ❌ WRONG: "Both interpretations have merit. Framework A says X, Framework B says Y."
    - ✅ RIGHT: "Speaker A: 'I think Framework A is better because...' Speaker B: 'Wait, I disagree. Framework B is stronger because...'"
  - Use Counterpoint Discovery from p3-briefing.md (B1.2 output)
  - **Explicitly state in content_plan.md:**
    - Topic: [What the debate is about]
    - Speaker A position: [Specific stance]
    - Speaker B position: [Opposing or alternative stance]
    - Language templates: "Wait, but what about..." or "I see it differently because..."
  - **Quality check:** Each counterpoint must include EXPLICIT DISAGREEMENT, not collaborative framing
  - **Episode 8 lesson:** Researched counterpoints were presented collaboratively; need positional debate

#### A3. NotebookLM Guidance Enhancement

- [ ] **A3.1** - Enhance `episodeFocus` prompt template with structural guidance
  - Add instructions for mode-switching clarity
  - Include signposting language requirements
  - Specify when to use counterpoint vs. agreement

- [ ] **A3.2** - Add "Dialogue Dynamics" section to episodeFocus **⚠️ EXECUTION CRITICAL**
  - **🚨 NotebookLM needs VERY EXPLICIT instructions to create disagreement**
  - **Use counterpoint moments from content_plan.md (Task A2.3 output)**
  - **Example episodeFocus language:**
    ```
    DIALOGUE DYNAMICS:
    At [timestamp ~X min], discuss [Topic]. Speaker A should argue [Position X]
    while Speaker B challenges with [Position Y]. This should be a respectful
    debate with explicit disagreement, not collaborative exploration.

    Use phrases like:
    - "Wait, but what about..."
    - "I disagree because..."
    - "I see it differently. Here's why..."

    Request 2-3 total counterpoint moments where speakers TAKE POSITIONS and debate.
    ```
  - **Quality requirement:** Must include ASSIGNED POSITIONS, not just "explore both views"
  - **Episode 8 lesson:** Generic "create counterpoint" instructions produced collaborative framing; need specific position assignments

- [ ] **A3.3** - Create "Episode Arc Template" for NotebookLM
  - Opening: Hook + Problem Definition + Structure Preview (3-5 min)
  - Middle: Exploration with clear mode-switching (20-30 min)
  - Closing: Synthesis + Key Takeaway + Clear Next Step (3-5 min)

---

### B. Research & Synthesis (Phases 2-7: Research → Synthesis)

These improvements affect how we gather and organize research before episode planning.

#### B1. Research Phase Improvements

- [ ] **B1.1** - Add "Depth Distribution Analysis" to Phase 6 (Master Briefing)
  - After organizing research by subtopic, assess relative depth
  - Flag subtopics with insufficient evidence/sources
  - Note where to request additional targeted research

- [ ] **B1.2** - Create "Counterpoint Discovery" step in research cross-validation
  - Explicitly identify where sources disagree
  - Note alternative frameworks or approaches
  - Document these for use in dialogue design (see A2.3)

- [ ] **B1.3** - Add "Practical Implementation Audit" to research briefing
  - For each major finding, identify: "How would someone actually do this?"
  - Extract specific tactics, steps, frameworks
  - Ensure practical advice is proportional to conceptual coverage

#### B2. Synthesis Phase Improvements

- [ ] **B2.1** - Add "Takeaway Clarity Check" to report.md quality requirements
  - Each major section should end with "What does this mean for listeners?"
  - Identify 1-3 core takeaways for the entire episode
  - Make these explicit in the synthesis

- [ ] **B2.2** - Create "Story Bank" section in research briefing
  - Collect examples, case studies, narratives during research
  - Tag by: illustrative power, emotional resonance, memorability
  - Ensure storytelling mode has rich material to draw from

---

### C. Packaging & Productization (Phase 11: Publishing)

These improvements affect how episodes are packaged and presented to listeners.

**Priority Assessment (from Episode 8):**
- **🔴 HIGH PRIORITY:** C1.1-C1.3 (template-driven, low effort, immediate impact)
- **🟡 MEDIUM PRIORITY:** C3.1-C3.2 (companion resources require more work)
- **🟢 LOW PRIORITY:** C2.1-C2.3 (feed.xml enhancements, nice-to-have)

#### C1. Episode Description Enhancements **🔴 HIGH PRIORITY**

- [ ] **C1.1** - Expand description template in `logs/metadata.md` **🔴 QUICK WIN**
  - Add "What You'll Learn" section (3-5 bullet points)
  - Add "Key Timestamps" section (link to major sections)
  - Add "Resources & Tools Mentioned" section
  - **Episode 8 lesson:** Description was informative but lacked discoverability enhancements
  - **Effort:** LOW - Template update, can be applied retroactively
  - **Impact:** Raises Dimension 9 (Packaging) from 3 to 3.5-4

- [ ] **C1.2** - Create "Call-to-Action Framework" **🔴 QUICK WIN**
  - Define standard CTAs: related episodes, deep-dive resources, community links
  - Template end-of-episode redirect language
  - Include in episodeFocus prompt for NotebookLM to voice
  - **Effort:** LOW - Define once, reuse everywhere
  - **Impact:** Listener engagement and next steps

- [ ] **C1.3** - Enhance source links presentation **🔴 QUICK WIN**
  - Group sources by type: Research Papers, Tools/Templates, Further Reading
  - Add 1-sentence actionable description for each link
  - Example: "Circle S-1 SEC Filing — Use this to benchmark operational expenses against institutional-scale issuer"
  - **Episode 8 lesson:** Sources were validated but descriptions not actionable
  - **Effort:** LOW - Template update
  - **Impact:** Resource utility increases

#### C2. Feed.xml Metadata Improvements **🟢 LOW PRIORITY**

- [ ] **C2.1** - Add `<itunes:episode>` and `<itunes:episodeType>` tags
  - Properly label as full, trailer, or bonus episodes
  - Include episode numbers for series
  - **Priority:** LOW - Nice-to-have, not blocking

- [ ] **C2.2** - Enhance `<content:encoded>` HTML show notes
  - Add structured sections: Overview, Key Insights, Timestamps, Resources
  - Include visual formatting (headers, lists, links)
  - Make it a standalone resource (useful even without listening)
  - **Priority:** LOW - Most podcast apps ignore <content:encoded>

- [ ] **C2.3** - Add `<podcast:transcript>` tag support
  - Link to transcript.txt file
  - Include type="text/plain" attribute
  - Improve accessibility and SEO
  - **Priority:** LOW - Few apps support Podcasting 2.0 tags

#### C3. Listener Resource Creation **🟡 MEDIUM PRIORITY**

- [ ] **C3.1** - Create companion resource templates **🟡 VALUABLE BUT HIGHER EFFORT**
  - One-page summary/cheat sheet
  - Action checklist
  - Framework diagram or decision tree
  - **Episode 8 lesson:** Episode 8 scored 5/5 on Companion Resources WITH just report.md and briefing
  - **Priority:** MEDIUM - Valuable but requires work; not blocking for high scores

- [ ] **C3.2** - Add "Episode Landing Page" generation **🟡 VALUABLE BUT HIGHER EFFORT**
  - Auto-generate HTML page for each episode
  - Include: full description, timestamps, resources, transcript
  - Link from feed.xml and episode description
  - **Priority:** MEDIUM - Nice-to-have web presence, not critical for podcast quality

---

### D. Format Experiments (New Workflow Branch)

These are experiments to test different episode formats and structures.

#### D1. Format Variation Tests

- [ ] **D1.1** - "Problem-First" Format Experiment
  - Create an episode that spends 60-70% defining the problem
  - Light solution preview only
  - Release separate deep-dive episodes on each sub-solution

- [ ] **D1.2** - "Practical Cluster" Format Experiment
  - Front-load philosophy and research (first 60%)
  - Cluster all practical advice at end (last 40%)
  - Test if this creates clearer separation and better recall

- [ ] **D1.3** - "Debate Structure" Format Experiment
  - Design episode with intentional counterpoint
  - Structure as: Statement → Challenge → Resolution
  - Test if this creates more engaging dialogue

- [ ] **D1.4** - "Modular Episodes" Format Experiment
  - Break complex topics into 3-4 separate 15-minute episodes
  - Each focuses on one aspect in depth
  - Release as mini-series with clear ordering

#### D2. Experiment Tracking Framework

- [ ] **D2.1** - Create experiment documentation template
  - Hypothesis: What we're testing
  - Format changes: What's different from standard
  - Success metrics: How we'll evaluate
  - Results: What we learned

- [ ] **D2.2** - Define success metrics for experiments
  - Listener feedback quality
  - Completion rates (if trackable)
  - Self-assessment: clarity, flow, impact
  - Comparative analysis vs. standard format

---

### E. Workflow Integration Tasks

These tasks integrate improvements into the actual workflow in `.claude/skills/new-podcast-episode.md`.

#### E1. Phase 8 (Episode Planning) Updates

- [ ] **E1.1** - Update content_plan.md template to include:
  - Episode Structure Map
  - Mode-Switching Framework
  - Signposting Language
  - Depth Budget
  - Counterpoint Moments
  - Episode Arc (Opening/Middle/Closing breakdown)

- [ ] **E1.2** - Create new "podcast-episode-planner" skill requirements
  - Read report.md and analyze for structural opportunities
  - Classify episode complexity: Simple (1 main theme) vs. Complex (multiple themes)
  - Auto-detect depth imbalances
  - Generate recommended structure based on content type

- [ ] **E1.3** - Update episodeFocus prompt generation in `notebooklm_prompt.py`
  - Read content_plan.md structure map
  - Inject signposting requirements
  - Add dialogue dynamics guidance
  - Include CTA at end

#### E2. Phase 11 (Publishing) Updates

- [ ] **E2.1** - Update `logs/metadata.md` template
  - Add "What You'll Learn" section
  - Add "Key Timestamps" section
  - Add "Resources & Tools" section
  - Add "Call-to-Action" section

- [ ] **E2.2** - Update `update_feed.py` script
  - Generate richer `<content:encoded>` HTML
  - Add `<podcast:transcript>` tag
  - Support `<itunes:episodeType>` tag

- [ ] **E2.3** - Create post-processing script for companion resources
  - Generate one-page summary from report.md
  - Extract action items into checklist
  - Create timestamp index from chapters JSON

#### E3. Quality Checklist Updates

- [ ] **E3.1** - Add structural checks to Phase 8 exit criteria **⚠️ UPDATED BASED ON EPISODE 8**
  - ✓ Episode structure map defined
  - ✓ Mode-switching framework applied
  - ✓ Signposting language included
  - **⚠️ NEW - Counterpoint execution check:**
    - ✓ Counterpoint moments designed (2-3 minimum)
    - ✓ Each counterpoint includes: Topic, Speaker A position, Speaker B position
    - ✓ Language templates provided ("Wait, but what about..." phrases)
    - ✓ Positions are ASSIGNED (not just "present both views")
  - **Episode 8 lesson:** Exit criteria didn't enforce counterpoint execution, only research
  - ✓ Depth budget confirms even coverage
  - ✓ Counterpoint moments designed

- [ ] **E3.2** - Add packaging checks to Phase 11 exit criteria
  - ✓ "What You'll Learn" section written
  - ✓ Key timestamps extracted
  - ✓ Resources section includes descriptions
  - ✓ Call-to-action defined
  - ✓ HTML show notes formatted

---

## Implementation Plan: Ordered by Workflow Phase

This plan follows the episode workflow from research → synthesis → planning → audio → publishing. Earlier improvements affect all downstream phases, so we test iteratively as we build up the workflow.

---

### **Wave 1: Research & Synthesis Foundation (Phases 2-7)**

These tasks affect everything downstream. Start here to ensure quality inputs flow through the entire pipeline.

**Phase 5-6 Improvements: Cross-Validation & Master Briefing**

1. **B1.1** - Add "Depth Distribution Analysis" to Phase 6 (Master Briefing)
   - **Affects downstream:** Ensures even topic coverage → better episode planning → balanced audio
   - **Test after:** Rate depth distribution quality using scorecard (see below)

2. **B1.2** - Create "Counterpoint Discovery" step in research cross-validation
   - **Affects downstream:** Feeds counterpoint moments in episode planning → more dynamic dialogue
   - **Test after:** Rate dialogue potential using scorecard

3. **B1.3** - Add "Practical Implementation Audit" to research briefing
   - **Affects downstream:** Ensures practical content → actionable episode → better listener takeaways
   - **Test after:** Rate practical actionability using scorecard

**Phase 7 Improvements: Synthesis (report.md)**

4. **B2.1** - Add "Takeaway Clarity Check" to report.md quality requirements
   - **Affects downstream:** Clear takeaways → focused episode planning → memorable audio
   - **Test after:** Rate takeaway clarity using scorecard

5. **B2.2** - Create "Story Bank" section in research briefing
   - **Affects downstream:** Rich storytelling material → engaging episode structure → compelling audio
   - **Test after:** Rate storytelling quality using scorecard

**Wave 1 Completion:** After implementing these 5 tasks, produce one full episode and measure using the quality scorecard. This establishes the research/synthesis foundation.

---

### **Wave 2: Episode Planning Architecture (Phase 8)**

These tasks transform research into structured episode plans. They depend on Wave 1's quality inputs.

**Structure & Clarity**

6. **A1.1** - Add "Episode Structure Map" section to content_plan.md
   - **Depends on:** Wave 1 (needs quality research to map)
   - **Affects downstream:** Clear structure → NotebookLM can follow it → listener comprehension
   - **Test after:** Rate structural clarity using scorecard

7. **A1.2** - Create "Mode-Switching Framework" in content_plan.md
   - **Depends on:** Wave 1 (needs identified modes from research)
   - **Affects downstream:** Intentional mode design → smoother audio transitions
   - **Test after:** Rate mode-switching quality using scorecard

8. **A1.3** - Add "Signposting Language" section
   - **Depends on:** Tasks 6, 7 (needs structure map and modes defined)
   - **Affects downstream:** NotebookLM uses signposts → listener orientation
   - **Test after:** Rate signposting effectiveness using scorecard

9. **A1.4** - Implement "Depth Budget" planning
   - **Depends on:** Task 1 (B1.1 depth analysis)
   - **Affects downstream:** Even time allocation → no rushed topics
   - **Test after:** Rate depth distribution using scorecard

**Content Architecture**

10. **A2.1** - Add "Problem Definition → Solution Architecture" framework
    - **Depends on:** Wave 1 (needs clear problem/solution from research)
    - **Affects downstream:** Coherent episode arc → satisfying resolution
    - **Test after:** Rate arc quality using scorecard

11. **A2.2** - Create "Build Toward Resolution" structure
    - **Depends on:** Task 4 (B2.1 takeaway clarity)
    - **Affects downstream:** Episode builds momentum → strong ending
    - **Test after:** Rate ending quality using scorecard

12. **A2.3** - Design "Counterpoint Moments" into structure
    - **Depends on:** Task 2 (B1.2 counterpoint discovery)
    - **Affects downstream:** Dynamic dialogue → engaging audio
    - **Test after:** Rate dialogue dynamics using scorecard

**Workflow Integration**

13. **E1.1** - Update content_plan.md template to include all new sections
    - **Depends on:** Tasks 6-12 (consolidates all planning improvements)
    - **Affects downstream:** Standardizes improved planning process
    - **Test after:** Verify template completeness

14. **E1.2** - Create new "podcast-episode-planner" skill requirements
    - **Depends on:** Task 13 (needs finalized template)
    - **Affects downstream:** Automates quality planning
    - **Test after:** Test automated planner output quality

**Wave 2 Completion:** After implementing these 9 tasks, produce one full episode with improved planning. Measure and compare to Wave 1 baseline.

---

### **Wave 3: Audio Generation Enhancement (Phase 9)**

These tasks improve how content_plan.md translates into audio via NotebookLM.

15. **A3.1** - Enhance episodeFocus prompt template with structural guidance
    - **Depends on:** Wave 2 (needs structure map, modes, signposting from content_plan.md)
    - **Affects downstream:** Audio follows intended structure
    - **Test after:** Rate audio structural fidelity using scorecard

16. **A3.2** - Add "Dialogue Dynamics" section to episodeFocus
    - **Depends on:** Task 12 (A2.3 counterpoint moments)
    - **Affects downstream:** More engaging conversational audio
    - **Test after:** Rate dialogue dynamics using scorecard

17. **A3.3** - Create "Episode Arc Template" for NotebookLM
    - **Depends on:** Tasks 10, 11 (problem→solution arc, build toward resolution)
    - **Affects downstream:** Consistent opening/middle/closing structure
    - **Test after:** Rate arc execution using scorecard

18. **E1.3** - Update episodeFocus prompt generation in notebooklm_prompt.py
    - **Depends on:** Tasks 15-17 (consolidates audio generation improvements)
    - **Affects downstream:** Automation maintains quality
    - **Test after:** Test automated prompt output

**Wave 3 Completion:** After implementing these 4 tasks, produce one full episode with improved audio generation. Measure and compare to Waves 1-2.

---

### **Wave 4: Publishing & Productization (Phase 11)**

These tasks improve discoverability, resources, and listener value. Independent of earlier waves (can run in parallel).

**Metadata & Descriptions**

19. **E2.1** - Update logs/metadata.md template
    - **Independent task** (can start anytime)
    - **Affects:** Episode discoverability and utility
    - **Test after:** Rate metadata quality using scorecard

20. **C1.1** - Expand description template in logs/metadata.md
    - **Depends on:** Task 19 (template update)
    - **Affects:** "What You'll Learn" clarity
    - **Test after:** Rate description usefulness using scorecard

21. **C1.2** - Create "Call-to-Action Framework"
    - **Can integrate with:** Task 17 (A3.3 arc template) for voiced CTAs
    - **Affects:** Listener engagement and next steps
    - **Test after:** Rate CTA clarity using scorecard

22. **C1.3** - Enhance source links presentation
    - **Depends on:** Wave 1 (needs validated sources from research)
    - **Affects:** Resource utility
    - **Test after:** Rate resource quality using scorecard

**Feed & Distribution**

23. **C2.1** - Add iTunes episode metadata tags
    - **Independent task**
    - **Affects:** Podcast app presentation
    - **Test after:** Verify feed validation

24. **C2.2** - Enhance HTML show notes in content:encoded
    - **Depends on:** Tasks 20-22 (needs rich metadata)
    - **Affects:** Standalone show notes utility
    - **Test after:** Rate show notes quality using scorecard

25. **C2.3** - Add podcast:transcript tag support
    - **Independent task**
    - **Affects:** Accessibility and SEO
    - **Test after:** Verify feed validation

26. **E2.2** - Update update_feed.py script
    - **Depends on:** Tasks 23-25 (consolidates feed improvements)
    - **Affects:** Automation maintains quality
    - **Test after:** Test automated feed generation

**Companion Resources**

27. **C3.1** - Create companion resource templates
    - **Depends on:** Wave 1 (needs quality research), Task 4 (B2.1 takeaways)
    - **Affects:** Listener learning and retention
    - **Test after:** Rate resource utility using scorecard

28. **C3.2** - Add Episode Landing Page generation
    - **Depends on:** Tasks 24, 27 (needs show notes and resources)
    - **Affects:** Discoverability and reference value
    - **Test after:** Rate landing page utility using scorecard

29. **E2.3** - Create post-processing script for companion resources
    - **Depends on:** Task 27 (consolidates resource generation)
    - **Affects:** Automation maintains quality
    - **Test after:** Test automated resource generation

**Wave 4 Completion:** After implementing these 11 tasks, publish one full episode with complete packaging. Measure packaging quality using scorecard.

---

### **Wave 5: Quality Assurance Integration**

These tasks embed quality checks into the workflow.

30. **E3.1** - Add structural checks to Phase 8 exit criteria
    - **Depends on:** Wave 2 complete
    - **Affects:** Prevents proceeding with low-quality planning
    - **Test after:** Verify checklist catches issues

31. **E3.2** - Add packaging checks to Phase 11 exit criteria
    - **Depends on:** Wave 4 complete
    - **Affects:** Prevents publishing with incomplete packaging
    - **Test after:** Verify checklist catches issues

**Wave 5 Completion:** Quality gates are active. Future episodes must pass all checks.

---

### **Wave 6: Format Experiments (Optional Variations)**

These experiments test alternative episode structures. Can run anytime after Wave 2 (requires planning foundation).

32. **D2.1** - Create experiment documentation template
    - **Start here** to establish experiment tracking framework
    - **Test after:** Document one experiment

33. **D2.2** - Define success metrics for experiments
    - **Depends on:** Scorecard established (see below)
    - **Test after:** Apply scorecard to experimental episode

34. **D1.1** - "Problem-First" Format Experiment
    - **Depends on:** Wave 2 (A2.1 problem→solution framework)
    - **Test with:** Full scorecard comparison vs. standard format

35. **D1.2** - "Practical Cluster" Format Experiment
    - **Depends on:** Wave 1 (B1.3 practical audit), Wave 2 (A1.2 modes)
    - **Test with:** Full scorecard comparison vs. standard format

36. **D1.3** - "Debate Structure" Format Experiment
    - **Depends on:** Wave 2 (A2.3 counterpoint design)
    - **Test with:** Full scorecard comparison vs. standard format

37. **D1.4** - "Modular Episodes" Format Experiment
    - **Depends on:** Wave 2 (A1.4 depth budget)
    - **Test with:** Full scorecard comparison vs. standard format

**Wave 6 Notes:** Run experiments one at a time. Each produces a full episode measured against the standard workflow baseline.

---

## Next Steps: Wave-Based Implementation

### **Immediate Actions**

1. **Create Baseline** - Produce one episode with current workflow
   - Apply full quality scorecard
   - Establish baseline scores across all 10 dimensions
   - Identify biggest weaknesses (scores 1-2) to target first

2. **Select Starting Wave** - Based on baseline scorecard
   - **If research/synthesis is weak (Dimensions 2, 5, 6, 7):** Start with Wave 1
   - **If structure/planning is weak (Dimensions 1, 3, 8):** Start with Wave 2
   - **If packaging is weak (Dimensions 9, 10):** Can start Wave 4 in parallel

3. **Implement First Wave** (5 episodes recommended)
   - Episode 1: Implement Wave 1 tasks, measure with scorecard
   - Episode 2: Refine Wave 1 based on scorecard feedback, measure again
   - Episode 3: Continue Wave 1 refinement, measure
   - Episode 4: Add Wave 2 tasks (research foundation now solid), measure
   - Episode 5: Refine Waves 1+2 combined, measure

4. **Review & Iterate**
   - After 5 episodes, compare scorecards
   - Identify which improvements had biggest impact
   - Decide: Continue to next wave, or refine current wave further?

### **Long-Term Roadmap**

**Episodes 1-5:** Waves 1-2 (Research → Planning foundation)
- Goal: Achieve 4+ scores on Dimensions 1, 2, 5, 6

**Episodes 6-10:** Wave 3 (Audio generation)
- Goal: Achieve 4+ scores on Dimensions 3, 4, 7, 8

**Episodes 11-15:** Wave 4 (Publishing & productization)
- Goal: Achieve 4+ scores on Dimensions 9, 10

**Episodes 16+:** Wave 5 (Quality gates active) + Wave 6 (Experiments)
- Goal: Maintain 4+ scores across all dimensions
- Test format variations, measure impact

### **Success Criteria**

**Wave Complete When:**
- 3 consecutive episodes score 4+ on all dimensions targeted by that wave
- Improvements are repeatable (not one-off flukes)
- Workflow changes are documented and integrated

**Overall Success:**
- Average 4+ across all 10 dimensions
- No dimension consistently scores below 3
- Workflow is sustainable (not exhausting to maintain quality)

---

## Episode Quality Scorecard

Apply this scorecard to **every episode** (baseline, improved, and experimental) to identify strengths and weaknesses. This is not a before/after comparison—it's a diagnostic tool to understand each episode's profile.

**Usage:** After each episode is complete, rate it on all dimensions. Record ratings in `/apps/podcast/pending-episodes/YYYY-MM-DD-slug/logs/quality_scorecard.md`.

---

### **Dimension 1: Structural Clarity**

**What we're measuring:** Can a listener follow the episode's structure and know where they are at any moment?

**Rating Scale (1-5):**
- **5 - Crystal Clear:** Structure stated upfront, clear signposting at transitions, easy to summarize arc in one sentence
- **4 - Well Structured:** Most transitions are clear, structure is followable, minor gaps
- **3 - Adequate:** Structure exists but requires listener effort to discern, some unclear transitions
- **2 - Meandering:** Structure is hard to follow, transitions feel random, listener may get lost
- **1 - Chaotic:** No discernible structure, topics jump without warning

**Evidence to consider:**
- Does the opening preview the structure?
- Are mode switches (philosophy → research → practical) signaled?
- Can you write a one-sentence episode arc summary?
- Would a listener know "we just covered X, now we're moving to Y"?

**Score: ___ / 5**

---

### **Dimension 2: Depth Distribution**

**What we're measuring:** Do all major themes get proportional depth, or do some feel rushed/underdeveloped?

**Rating Scale (1-5):**
- **5 - Perfectly Balanced:** All major themes get depth proportional to importance, no theme feels rushed or over-explored
- **4 - Well Balanced:** Minor depth variations, but all themes adequately covered
- **3 - Uneven:** One theme clearly gets more depth than equally important themes
- **2 - Imbalanced:** Important theme feels like an add-on or afterthought, significant depth disparity
- **1 - Severely Skewed:** Major theme mentioned briefly while minor themes dominate

**Evidence to consider:**
- List the major themes and their approximate time allocation
- Does any important theme get <15% of time when it deserves more?
- Does any theme feel "tacked on" at the end?
- Are depth differences intentional (primary vs. secondary) or accidental?

**Score: ___ / 5**

**Themes identified:**
- Theme 1: _______ (~___%)
- Theme 2: _______ (~___%)
- Theme 3: _______ (~___%)

---

### **Dimension 3: Mode-Switching Clarity**

**What we're measuring:** Are transitions between modes (philosophy, research, storytelling, practical, landing) intentional and smooth?

**Rating Scale (1-5):**
- **5 - Masterful:** Modes are clearly defined, transitions feel purposeful, each mode serves its function
- **4 - Intentional:** Modes are distinguishable, transitions mostly smooth, occasional blend
- **3 - Blended:** Modes blend together, transitions not always clear, listener may not notice mode shifts
- **2 - Muddy:** Modes blur together confusingly (philosophy mixed with practical advice, research mixed with opinion)
- **1 - Undefined:** No clear modes, everything feels like one continuous stream

**Evidence to consider:**
- Can you identify distinct philosophical, research, storytelling, practical, and landing moments?
- Are transitions smooth or jarring?
- Does each mode feel purposeful or accidental?
- Do modes blend in ways that reduce clarity (e.g., research claims without citations, practical advice without implementation)?

**Score: ___ / 5**

**Modes observed:**
- Philosophy: Yes / No — Quality: _____
- Research: Yes / No — Quality: _____
- Storytelling: Yes / No — Quality: _____
- Practical: Yes / No — Quality: _____
- Landing: Yes / No — Quality: _____

---

### **Dimension 4: Dialogue Dynamics**

**What we're measuring:** Does the conversation feel like a genuine exchange with counterpoint, or just mutual agreement and reinforcement?

**Rating Scale (1-5):**
- **5 - Dynamic Exchange:** Multiple counterpoint moments, respectful disagreement, "wait, but..." challenges, diverse perspectives
- **4 - Engaging:** Some counterpoint, occasional push-back, mostly collaborative with texture
- **3 - Supportive Riff:** Mostly agreement, speakers build on each other, limited divergence
- **2 - Echo Chamber:** Pure reinforcement, no push-back, feels like presentation with two voices
- **1 - Monotone:** Could be one person talking, no meaningful interaction

**Evidence to consider:**
- Count counterpoint moments (one speaker challenges or diverges from the other)
- Are there "wait, but what about..." or "I see it differently because..." moments?
- Do speakers bring different stories or perspectives?
- Does it feel like a conversation or a scripted presentation?

**Score: ___ / 5**

**Counterpoint moments counted: _____**

**Examples of dynamic exchanges:**
1. _____________________________________________________
2. _____________________________________________________

---

### **Dimension 5: Practical Actionability**

**What we're measuring:** Does the listener leave with clear, specific, actionable steps?

**Rating Scale (1-5):**
- **5 - Highly Actionable:** 3+ specific tactics, frameworks, or steps a listener can implement immediately
- **4 - Actionable:** 2 specific tactics, clear enough to act on with minimal additional research
- **3 - Moderately Actionable:** 1 specific tactic, or general advice that needs clarification
- **2 - Vaguely Actionable:** Concepts discussed but no clear "how to do this" guidance
- **1 - Purely Conceptual:** Interesting ideas but zero implementation guidance

**Evidence to consider:**
- List specific tactics, frameworks, or steps mentioned
- Are they detailed enough to implement, or just high-level concepts?
- Is there a balance between "why this matters" and "how to do it"?
- Could a listener start doing something different tomorrow?

**Score: ___ / 5**

**Actionable takeaways identified:**
1. _____________________________________________________
2. _____________________________________________________
3. _____________________________________________________

---

### **Dimension 6: Takeaway Clarity**

**What we're measuring:** Can a listener articulate 1-3 core takeaways from the episode?

**Rating Scale (1-5):**
- **5 - Crystal Clear:** 1-3 core takeaways explicitly stated, memorable, listener could repeat them
- **4 - Clear:** Takeaways are identifiable with minimal effort, mostly explicit
- **3 - Inferrable:** Listener needs to synthesize or infer takeaways, not explicitly stated
- **2 - Fuzzy:** Hard to identify core takeaways, too many ideas competing for attention
- **1 - Unclear:** No clear takeaways, episode explores but doesn't land on key points

**Evidence to consider:**
- Are takeaways stated explicitly (e.g., in closing synthesis)?
- If you asked a listener "what was this episode about?", could they answer in 1-2 sentences?
- Are there too many ideas competing (diluting focus)?
- Do takeaways connect to episode opening/hook?

**Score: ___ / 5**

**Core takeaways (1-3):**
1. _____________________________________________________
2. _____________________________________________________
3. _____________________________________________________

---

### **Dimension 7: Storytelling Quality**

**What we're measuring:** Are examples, case studies, and narratives used effectively to illustrate concepts?

**Rating Scale (1-5):**
- **5 - Compelling:** Multiple memorable stories, well-integrated, emotionally resonant, illustrate key points perfectly
- **4 - Effective:** 2+ stories, good integration, serve to illustrate concepts
- **3 - Adequate:** 1 story, or multiple stories that are functional but not memorable
- **2 - Minimal:** Stories feel tacked on or tangential, limited illustrative power
- **1 - Absent:** No stories, pure abstract discussion

**Evidence to consider:**
- How many stories, examples, or case studies are used?
- Are they memorable?
- Do they illustrate key concepts or feel tangential?
- Do they create emotional connection or just add length?

**Score: ___ / 5**

**Stories/examples identified:**
1. _____________________________________________________
2. _____________________________________________________

---

### **Dimension 8: Episode Arc & Resolution**

**What we're measuring:** Does the episode build toward a satisfying resolution, or does it trail off?

**Rating Scale (1-5):**
- **5 - Satisfying Arc:** Clear problem → exploration → resolution, builds momentum, strong ending that lands the point
- **4 - Good Arc:** Identifiable build and resolution, ending feels intentional
- **3 - Adequate Arc:** Some build-up, ending is present but doesn't fully land
- **2 - Weak Arc:** Little build-up, ending feels like it trails off or runs out of steam
- **1 - No Arc:** Flat throughout, no sense of build or resolution

**Evidence to consider:**
- Can you identify: opening hook → problem definition → exploration → resolution?
- Does the episode build toward something, or meander at consistent intensity?
- Does the ending feel like a conclusion or just "we ran out of time"?
- Is there a callback to the opening?

**Score: ___ / 5**

**Arc structure:**
- Opening: _____________________________________________________
- Problem: _____________________________________________________
- Exploration: _____________________________________________________
- Resolution: _____________________________________________________

---

### **Dimension 9: Packaging & Discoverability**

**What we're measuring:** Are episode metadata, descriptions, and resources useful for listeners?

**Rating Scale (1-5):**
- **5 - Excellent Packaging:** Rich description with "What You'll Learn", timestamps, validated sources, clear CTA, useful show notes
- **4 - Strong Packaging:** Description is informative, sources provided, show notes functional
- **3 - Adequate Packaging:** Basic description, some sources, minimal show notes
- **2 - Weak Packaging:** Generic description, few/no sources, poor show notes
- **1 - Minimal Packaging:** Title and basic description only

**Evidence to consider:**
- Does the description entice and inform?
- Are "What You'll Learn" bullets present and accurate?
- Are timestamps provided for major sections?
- Are sources validated and useful?
- Is there a clear CTA for next steps?
- Are show notes formatted and useful standalone?

**Score: ___ / 5**

---

### **Dimension 10: Companion Resource Value**

**What we're measuring:** Do companion resources (summary, checklist, diagrams) add value beyond the audio?

**Rating Scale (1-5):**
- **5 - Highly Valuable:** Multiple resources (summary, checklist, framework diagram), professionally formatted, immediately useful
- **4 - Valuable:** 1-2 resources, clear utility, good formatting
- **3 - Moderately Valuable:** Resources exist but basic, limited additional value beyond audio
- **2 - Low Value:** Resources feel auto-generated, not tailored, minimal utility
- **1 - Absent:** No companion resources

**Evidence to consider:**
- What resources exist? (one-pager, checklist, diagram, landing page, etc.)
- Would a listener use these, or are they just "nice to have"?
- Are they formatted well?
- Do they distill episode content effectively?

**Score: ___ / 5**

**Resources present:**
- [ ] One-page summary
- [ ] Action checklist
- [ ] Framework diagram
- [ ] Episode landing page
- [ ] Other: _____

---

## Scorecard Summary Template

Copy this template to `/apps/podcast/pending-episodes/YYYY-MM-DD-slug/logs/quality_scorecard.md` for each episode.

```markdown
# Episode Quality Scorecard: [Episode Title]

**Date:** YYYY-MM-DD
**Evaluator:** [Your name]
**Episode Format:** Standard / Experiment ([format type])

---

## Scores

| Dimension | Score | Notes |
|-----------|-------|-------|
| 1. Structural Clarity | ___ / 5 | |
| 2. Depth Distribution | ___ / 5 | Themes: [list] |
| 3. Mode-Switching Clarity | ___ / 5 | |
| 4. Dialogue Dynamics | ___ / 5 | Counterpoints: ___ |
| 5. Practical Actionability | ___ / 5 | |
| 6. Takeaway Clarity | ___ / 5 | |
| 7. Storytelling Quality | ___ / 5 | |
| 8. Episode Arc & Resolution | ___ / 5 | |
| 9. Packaging & Discoverability | ___ / 5 | |
| 10. Companion Resource Value | ___ / 5 | |

**Total:** ___ / 50

---

## Strengths (scores 4-5)

- _____________________________________________________
- _____________________________________________________

## Weaknesses (scores 1-2)

- _____________________________________________________
- _____________________________________________________

## Areas for Improvement (score 3)

- _____________________________________________________
- _____________________________________________________

---

## Workflow Improvements Applied This Episode

- [ ] Task [X.X] - [Description]
- [ ] Task [X.X] - [Description]

---

## Notes & Observations

[Free-form notes on what worked, what didn't, lessons learned, ideas for next episode]

```

---

## How to Use the Scorecard

### **After Each Episode:**
1. Listen to the full episode (or review transcript if generating)
2. Fill out scorecard immediately while fresh
3. Identify 1-2 strengths to maintain
4. Identify 1-2 weaknesses to address in next wave

### **When Planning Next Episode:**
1. Review previous episode's scorecard
2. Select 2-3 workflow improvements from current wave that target weaknesses
3. Apply improvements during production
4. Measure again to see impact

### **For Format Experiments:**
1. Apply full scorecard to experimental episode
2. Compare scores to baseline episode in same series (if applicable)
3. Document: What changed? What improved? What got worse?
4. Decide: Keep change, modify, or discard

### **Quarterly Review:**
1. Aggregate scorecards for all episodes in quarter
2. Calculate average score per dimension
3. Identify consistent strengths (maintain)
4. Identify persistent weaknesses (prioritize in next wave)
5. Track improvement trends over time

---

## Key Principles of This Plan

### **1. Workflow-Ordered Implementation**

Tasks are sequenced by workflow phase (Research → Synthesis → Planning → Audio → Publishing), not by arbitrary priority. This ensures:
- **Upstream improvements flow downstream** (better research → better planning → better audio)
- **We test iteratively** (measure after each wave to see impact)
- **Dependencies are respected** (can't improve episode planning without quality research inputs)

### **2. Measure Every Episode, Not Before/After**

The quality scorecard is diagnostic, not comparative:
- **NOT:** "Did we improve from 2.5 to 3.5?"
- **INSTEAD:** "This episode scores 5 on storytelling but 2 on dialogue dynamics—here's what to improve next time"

This approach:
- Shows each episode's unique profile (strengths and weaknesses)
- Identifies patterns across multiple episodes (consistent strengths to leverage, persistent weaknesses to address)
- Avoids false comparisons (different topics naturally score differently on some dimensions)
- Provides actionable feedback (specific dimensions to target, not vague "make it better")

### **3. Incremental Waves, Not Big Bang**

37 tasks divided into 6 waves:
- Each wave is 4-9 tasks focused on one workflow phase
- Complete one wave before starting the next (with overlap allowed for independent tasks)
- 3-5 episodes per wave to refine and stabilize improvements
- Test and learn before adding more complexity

### **4. Quality Gates Prevent Regression**

Wave 5 adds exit criteria to workflow phases:
- Phase 8 (Planning) can't proceed without structure map, depth budget, etc.
- Phase 11 (Publishing) can't proceed without rich metadata, validated sources, etc.
- Once quality improves, gates prevent backsliding

### **5. Format Experiments as Variations, Not Replacements**

Wave 6 experiments test alternatives (problem-first, debate structure, etc.) while preserving the standard workflow:
- Each experiment is fully documented (hypothesis, changes, results)
- Scorecard measures impact vs. baseline
- Successful experiments can be integrated; unsuccessful ones are discarded
- Standard workflow remains the fallback

---

## Plan Benefits

**For Users:**
- Clear roadmap: "We're in Wave 2, working on episode planning"
- Tangible progress: Scorecard shows improvement over time
- Informed decisions: "Dimension 4 (dialogue) is weak—let's prioritize Wave 2 Task 12"

**For Claude:**
- Workflow integration: Tasks map directly to existing phases in `.claude/skills/new-podcast-episode.md`
- Testable checkpoints: Each wave has clear completion criteria
- Dependency awareness: Earlier tasks explicitly noted as dependencies for later tasks

**For Episodes:**
- Iterative improvement: Each episode builds on learnings from previous
- Sustainable quality: Changes are integrated into workflow, not manual per-episode effort
- Productized output: Episodes become complete learning resources, not just audio files

---

## Wave 2-5 Updates Based on Episode 8 Learnings (2026-02-04)

### Summary of Changes

**Context:** Stablecoin Ep. 8 validated Wave 1 (+16 points, 28→44/50) but revealed one critical gap: counterpoint research ≠ audio execution. Based on this, Waves 2-5 have been updated with:

### 1. **Strengthened Counterpoint Execution Requirements**

**Wave 2, Task A2.3 - Design "Counterpoint Moments"**
- **OLD:** "Identify 2-3 moments where speakers should diverge"
- **NEW:** "ASSIGN POSITIONS - not just 'present both views'"
- **Added:** Explicit examples of wrong vs. right execution
- **Added:** Quality check requiring EXPLICIT DISAGREEMENT, not collaborative framing

**Wave 3, Task A3.2 - Add "Dialogue Dynamics" to episodeFocus**
- **OLD:** "Request specific moments of push-back"
- **NEW:** "NotebookLM needs VERY EXPLICIT instructions with ASSIGNED POSITIONS"
- **Added:** Example episodeFocus language showing position assignments
- **Added:** Quality requirement for assigned positions, not generic "explore both views"

**Wave 5, Task E3.1 - Phase 8 Exit Criteria**
- **Added:** Counterpoint execution check (2-3 moments with assigned positions)
- **Added:** Quality requirements for each counterpoint (Topic, Speaker A/B positions, language templates)

**Rationale:** Episode 8 showed that Counterpoint Discovery (B1.2) was completed perfectly (3 debates documented), but not executed in audio as positional dialogue. The fix is in Wave 2-3 execution, not Wave 1 research.

### 2. **Added Runtime Constraint Guidance**

**Wave 2, Task A1.4 - Implement "Depth Budget"**
- **Added:** "If runtime ≤30 min, front-load practical content in Section 2 (Evidence)"
- **Added:** Warning that compression happens at episode end when time runs out
- **Added:** Example allocation for 30-min episode (Foundation 30%, Evidence 45%, Application 25%)

**Rationale:** Episode 8 compressed operator's playbook to 2% (40 seconds) because it was placed at episode end. Front-loading prevents this.

### 3. **Reprioritized Wave 4 Tasks**

**Wave 4 - Packaging & Productization**
- **Marked HIGH PRIORITY:** C1.1-C1.3 (description, CTA, source enhancements)
  - Template-driven, low effort, immediate impact
  - Can be applied retroactively to Episode 8
  - Raises Dimension 9 (Packaging) from 3 to 4
- **Marked MEDIUM PRIORITY:** C3.1-C3.2 (companion resources, landing page)
  - Episode 8 scored 5/5 on Companion Resources with just report.md and briefing
  - Valuable but not blocking for high scores
- **Marked LOW PRIORITY:** C2.1-C2.3 (feed.xml enhancements)
  - Nice-to-have, few apps support advanced tags

**Rationale:** Episode 8 showed packaging is independent of audio quality and can be quick wins. Prioritize template-driven improvements first.

### 4. **Validated Wave 1 Baseline**

**Results:**
- Pre-refactoring: 28/50 (56%)
- Post-Wave 1: 44/50 (88%)
- Improvement: +16 points (+32%)

**Impact by dimension:**
- Companion Resources: 2 → 5 (+3) - Enhanced research briefing with 5 Wave 1 sections
- Depth Distribution: 2 → 4 (+2) - Depth Distribution Analysis table prevented imbalances
- Takeaway Clarity: 4 → 5 (+1) - Explicit synthesis requirements worked
- Storytelling: 4 → 5 (+1) - Story Bank with memorability ratings delivered
- Structural Clarity: 4 → 5 (+1) - Better signposting and preview
- Episode Arc: 4 → 5 (+1) - Strong opening hook + callback structure
- Practical Actionability: 5 → 5 - Practical Implementation Audit maintained perfection

**Enforcement validated:**
- ✅ Phase 6 exit criteria blocked progression without quality inputs
- ✅ Synthesis agent input validation worked perfectly
- ✅ All 5 Wave 1 sections completed before synthesis could proceed

### 5. **Clear Next Steps**

**Next Wave Priority:**
- **Wave 2:** HIGH - Address counterpoint execution gap (Dimension 4: 3→4-5)
  - Focus on Tasks A2.3 (assign positions) and A1.4 (depth budget)
- **Wave 4:** PARALLEL - Quick packaging wins (Dimension 9: 3→4)
  - Focus on Tasks C1.1-C1.3 (template-driven improvements)
- **Wave 3:** After Wave 2 - Implement enhanced episodeFocus prompt
  - Task A3.2 depends on Wave 2 Task A2.3 output

**Target for next episode:** 47-49/50 (94-98%)
- Maintain 8 dimensions at 4-5
- Raise Dimension 4 (Dialogue Dynamics) from 3 to 4-5
- Raise Dimension 9 (Packaging) from 3 to 4

---

## Changes Made to This Document (2026-02-04)

1. **Updated Status Section** - Wave 1 marked as VALIDATED & COMPLETE with Episode 8 results
2. **Added "Lessons Learned" Section** - Documented what worked, critical gaps, and packaging opportunities
3. **Strengthened Wave 2 Task A2.3** - Added execution language for assigned positions vs. collaborative framing
4. **Enhanced Wave 2 Task A1.4** - Added runtime constraint guidance (front-load practical content)
5. **Strengthened Wave 3 Task A3.2** - Added explicit episodeFocus examples with position assignments
6. **Reprioritized Wave 4 Tasks** - Marked C1.1-C1.3 HIGH, C3.1-C3.2 MEDIUM, C2.1-C2.3 LOW
7. **Updated Wave 5 Task E3.1** - Added counterpoint execution check to Phase 8 exit criteria
8. **Added This Summary Section** - Document changes made based on real-world testing

**Philosophy:** These updates preserve the original improvement plan structure while incorporating learnings from Episode 8. The counterpoint execution gap was the most critical finding, requiring strengthened language in Waves 2-3. All changes are backwards-compatible with existing work.
