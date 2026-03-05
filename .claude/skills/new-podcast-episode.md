# New Podcast Episode Workflow

## Quick Reference: Episode Workflow Progress

**Track your progress through each phase. The workflow is complete when all phases are checked off.**

- [ ] **Phase 1: Setup** → Episode directory and initial files created
- [ ] **Phase 2: Research - Academic Foundation** → Perplexity research complete (30-120s)
- [ ] **Phase 3: Research - Question Discovery** → Phase 2 analysis complete, targeted prompts ready
- [ ] **Phase 4: Research - Targeted Followup** → Grok, GPT-Researcher, Gemini, Claude, Together research complete
- [ ] **Phase 5: Cross-Validation** → Sources verified, contradictions identified
- [ ] **Phase 6: Master Briefing** → research/p3-briefing.md created with organized findings
- [ ] **Phase 7: Synthesis** → report.md created by podcast-synthesis-writer
- [ ] **Phase 8: Episode Planning** → content_plan.md created by podcast-episode-planner
- [ ] **Phase 9: Audio Generation** → NotebookLM (API or manual)
- [ ] **Phase 10: Audio Processing** → Transcription and chapters
- [ ] **Phase 11: Publishing** → Cover art, metadata, feed.xml updated
- [ ] **Phase 12: Commit & Push** → Changes committed and pushed to GitHub

**Verification:** After Phase 12, check https://research.yuda.me/podcast/feed.xml refreshes with new episode in 2-3 minutes.

---

You are helping create a new podcast episode following a structured research and production workflow with sequential deep research and multi-source verification.

## Episode Directory Structure

Each episode follows an organized structure with files grouped by purpose:

```
apps/podcast/pending-episodes/YYYY-MM-DD-topic-slug/
├── research/                           # Research files organized by phase
│   ├── p1-brief.md                    # Research brief (topic/questions)
│   ├── p2-perplexity.md               # Perplexity academic research
│   ├── p2-grok.md                     # Grok real-time/regional research
│   ├── p2-chatgpt.md                  # GPT-Researcher industry/technical
│   ├── p2-gemini.md                   # Gemini policy/strategic research
│   ├── p2-manual.md                   # Manual research, user sources
│   ├── p3-briefing.md                 # Cross-validated synthesis for synthesis agent
│   └── documents/                     # PDFs, papers, supporting files
├── logs/                               # Process logs
│   ├── prompts.md                     # All prompts used during creation
│   └── metadata.md                    # Publishing metadata scratch
├── tmp/                                # Temporary files (optional to commit)
│   └── *_transcript.json              # Full Whisper output (large file)
├── cover.png                           # Episode cover art with branding (~500KB)
├── report.md                           # Final narrative report from synthesis agent
├── content_plan.md                     # Episode structure guide for NotebookLM
├── sources.md                          # Source documentation
├── YYYY-MM-DD-topic-slug.mp3          # Final audio file with chapters (~30MB)
├── YYYY-MM-DD-topic-slug_chapters.json # Podcasting 2.0 chapter metadata
└── transcript.txt                      # Plain text transcript from Whisper
```

**Key organizational principles:**
- **Research files use phase prefixes** (p1, p2, p3) for chronological sorting
- **Each research tool saves to its own file** (prevents race conditions, enables parallel execution)
- **Root directory contains only final outputs** (published files linked in feed.xml)
- **Logs separated from research** (prompts.md moved to logs/)
- **Temporary files isolated** (tmp/ for large transcripts, can be optionally committed)

**File naming rationale:**
- **p1-brief.md** - "Brief" describes research topic/question (not "prompt" which is tool-specific)
- **p2-[tool].md** - Individual tool outputs enable parallel execution without conflicts
- **p3-briefing.md** - Cross-validated synthesis ready for narrative creation
- **No redundant prefixes** - Files in research/ don't need "research-" prefix

## Complete Workflow

═══════════════════════════════════════════════════════════════
                    PHASE 1: SETUP
═══════════════════════════════════════════════════════════════

**ENTRY REQUIREMENTS:**
✓ User has provided episode topic or research question
✓ Episode details known or easily inferred (date, slug, title, series info if applicable)

**IMPORTANT: Always use today's actual date for all timestamps. Never use placeholder dates like "YYYY-MM-DD" in created files.**

**Create a task list** to track progress through the workflow using TaskCreate:

1. "Setup episode structure and files"
2. "Conduct deep research (Perplexity, then targeted followup)"
3. "Cross-validate research findings"
4. "Create master research briefing"
5. "Synthesize narrative report"
6. "Create episode content plan"
7. "Generate audio via NotebookLM"
8. "Process audio (transcribe, chapters, embed)"
9. "Publish episode (cover art, metadata, feed.xml)"
10. "Commit and push to GitHub"

Then use TaskUpdate to mark task 1 as in_progress. Use TaskList to review progress at any time.

**Determine episode details:**

Use today's date (YYYY-MM-DD format) unless user specifies otherwise.

**Only ask the user if missing or unclear:**
1. **Series information** (if not provided or unclear from context)
   - Series name and episode number for series episodes
   - Or confirm it's a standalone episode
2. **Episode slug** (if not provided or easily inferred from topic)
   - e.g., "lifestyle", "vo2-max", "supplementation"
3. **Episode title** (if not provided)
   - **For series:** "Series Name: Ep. X, Topic" (e.g., "Cardiovascular Health: Ep. 1, Lifestyle Foundations")
   - **For standalone:** Descriptive title (e.g., "Stablecoin Market: Strategies and Pitfalls")

**Check for existing episode directory:**

If the episode directory already exists, check for a `research-prompt.md` file. If present:
- Read it to understand the episode context and research objectives
- Use it to inform the deep research prompts you'll create
- DO NOT copy it as the deep research prompts - you'll create new ones in logs/prompts.md

**Create the episode directory and files using setup_episode.py:**

```bash
# For public episodes (default yudame-research podcast)
uv run python ~/src/cuttlefish/apps/podcast/tools/setup_episode.py --slug "topic-slug" --title "Episode Title"

# For private podcast episodes (loads config from database)
uv run python ~/src/cuttlefish/apps/podcast/tools/setup_episode.py --podcast stablecoin --slug "overview" --title "Stablecoin Overview"

# For series episodes (legacy)
uv run python ~/src/cuttlefish/apps/podcast/tools/setup_episode.py --slug "topic-slug" --title "Series: Ep. X, Topic" \
  --series "series-name" --episode-num X

# With research context pre-filled
uv run python ~/src/cuttlefish/apps/podcast/tools/setup_episode.py --slug "topic-slug" --title "Episode Title" \
  --context "Research focus and key questions"
```

The `--podcast` parameter loads configuration from the database (PodcastConfig model) and saves it as `episode_config.json` in the episode directory. This config is used by downstream tools to adapt branding, CTAs, depth level, and sponsor breaks for public vs private feeds.

**What setup_episode.py creates:**
```
apps/podcast/pending-episodes/{path}/
├── research/
│   ├── documents/
│   └── p1-brief.md      # Research brief with date/title
├── logs/
│   └── prompts.md       # Prompt tracking with date/title
├── tmp/
└── sources.md           # Source template
```

The script automatically uses today's date and fills in all templates.

**logs/prompts.md (created by script):**
```markdown
# Prompts Used for Episode: [Episode Title]

This document tracks all prompts used during the creation of this episode for reproducibility and learning.

**Note:** If a `research-prompt.md` exists in this directory, it contains the seed research ideas and objectives. The prompts below are the actual copy-paste-ready prompts used with deep research tools.

---

## Setup Phase

**Episode Details:**
- Date: [Today's date in YYYY-MM-DD format]
- Slug: topic-slug
- Title: [Episode Title]

---

## Deep Research Phase

### Tool Configuration

**Automated tools:**
- **Perplexity:** Academic & Official Sources (Phase 1 - always used, API-based)
- **GPT-Researcher:** Industry & Technical Sources (Phase 3 - API-based, uses OpenAI GPT-5.2)
- **Gemini Deep Research:** Strategic & Policy Sources (Phase 3 - API-based)
- **Together:** Exploratory Multi-Hop Research (Phase 3 - API-based, uses DeepSeek-R1)
- **Claude:** Multi-agent deep research (Phase 3 - automated via Anthropic API)

**Manual tools (user runs these):**
- **Grok:** Real-Time & Regional Sources (Phase 3 - user pastes from https://x.com/i/grok)

**🚨 DEFAULT APPROACH: USE ALL 6 TOOLS FOR EVERY EPISODE**

All episodes should use all 6 research sources by default:
1. ✅ **Perplexity** - Academic foundation (always runs first, automated)
2. ✅ **GPT-Researcher** - Industry/technical analysis (automated)
3. ✅ **Gemini** - Policy/regulatory frameworks (automated)
4. ✅ **Together** - Exploratory multi-hop research (automated)
5. ✅ **Claude** - Multi-agent deep research (automated)
6. ✅ **Grok** - Real-time developments and practitioner perspectives (manual)

**Omitting a tool should be rare** and only for a specific reason (e.g., "This topic has zero policy/regulatory angle, skipping Gemini"). When in doubt, use all 6 tools.

### Deep Research Prompts (Copy-Paste Ready)

**IMPORTANT:** These prompts use single newlines only to prevent accidental partial submissions when pasting into Chrome-based tools.

---

<!-- Research prompts will be added as they are used -->
```

**research/p1-brief.md:**
```markdown
# Research Brief: [Episode Title]

**Date:** [Today's date in YYYY-MM-DD format]
**Episode:** [Episode Title]

---

## Research Topic

[High-level description of what this episode will research]

## Key Questions

- [Question 1]
- [Question 2]
- [Question 3]

## Context

[Any relevant context or background for the research]

---

**Next Steps:**
1. Create Phase 1 academic research prompt for Perplexity
2. Run Perplexity research → save to research/p2-perplexity.md
3. Analyze results for question discovery
4. Create targeted Phase 3 prompts for other tools
```

**research/p2-perplexity.md (template - created after Phase 1 research):**
```markdown
# Perplexity Research: [Episode Title]

**Date:** [Today's date in YYYY-MM-DD format]
**Focus:** Academic & Official Sources
**Duration:** 30-120 seconds

---

## Research Output

[Paste Perplexity results here]

---

## Sources

[List key sources cited in the research]
```

**research/p2-grok.md, p2-chatgpt.md, p2-gemini.md (created as needed for Phase 3 tools)**

Each follows the same pattern:
```markdown
# [Tool Name] Research: [Episode Title]

**Date:** [Today's date in YYYY-MM-DD format]
**Focus:** [Tool's focus area]

---

## Research Output

[Paste results here]

---

## Sources

[List key sources]
```

**research/p3-briefing.md (template - created after cross-validation):**
```markdown
# Master Research Briefing: [Episode Title]

Date: [Today's date in YYYY-MM-DD format]
For: podcast-synthesis-writer agent

---

## VERIFIED KEY FINDINGS

### [Subtopic 1]
**Main finding:** [One sentence summary]

**Evidence:**
- [Stat/Finding] — Source: [Citation] — Quality: [Meta-analysis/RCT/etc] — N=[sample]
- [Stat/Finding] — Source: [Citation] — Quality: [Study type] — N=[sample]

**Contradictions/Nuances:**
- [If sources disagree, note here]

**Source quality notes:**
- [Methodological limitations to be aware of]

---

<!-- More subtopics as research reveals them -->

---

## RESEARCH GAPS & UNCERTAINTIES

- **Well-established:** [What we know with confidence]
- **Preliminary/Limited evidence:** [What has some support but needs more]
- **Unknown/Unstudied:** [What we don't know]

---

## SOURCE INVENTORY

### Tier 1 Sources (Meta-analyses, Systematic Reviews, Official Statistics)
1. [Full citation] — [Key contribution] — [URL]

### Tier 2 Sources (RCTs, Large Studies, Government Reports)
1. [Full citation] — [Key contribution] — [URL]

### Tier 3 Sources (Case Studies, Industry Reports, News)
1. [Full citation] — [Key contribution] — [URL]

---

## COMPARISON TABLES
[Tables comparing similar markets/programs/implementations]

---

## TIMELINE OF DEVELOPMENTS
[Chronological key events for topics with recent changes]

---

## PRACTITIONER PERSPECTIVES
[Direct quotes from credentialed experts - doctors, researchers, industry leaders]
[These carry weight as informed opinion, but are NOT peer-reviewed evidence]

---

## PUBLIC DISCOURSE (Opinion - NOT Evidence)

⚠️ **For podcast context only** - Use to contrast "what people believe" vs "what research shows"

### What X/Twitter Is Saying
- [Notable voice]: "[Quote]" — [@handle, credential, date, engagement]
- [Notable voice]: "[Quote]" — [@handle, credential, date, engagement]

### Active Debates/Controversies
- **Debate:** [Topic of disagreement]
  - **Pro position:** [Who's arguing this, their case]
  - **Con position:** [Who's arguing this, their case]

### Popular Misconceptions to Address
- **Belief:** [What many people think]
- **Reality:** [What evidence actually shows]
- **Podcast angle:** [How to bridge this gap for listeners]

---

## NOTES FOR OPUS 4.6

**Strongest evidence for:**
- [Topic areas with robust sources]

**Weaker evidence for:**
- [Topic areas with limited or conflicting sources]

**Interesting tensions/contradictions:**
- [Where sources disagree - worth exploring why]

**Missing context:**
- [Gaps that should be acknowledged]
```

**sources.md:**
```markdown
# Sources for [Episode Title]

## Research Tools Used
- Perplexity (Academic & Official - automated) → Evidence
- GPT-Researcher (Industry & Technical - automated) → Evidence + Case Studies
- Gemini Deep Research (Strategic & Policy - automated) → Evidence + Policy
- Together Open Deep Research (Exploratory Multi-Hop - automated) → Evidence + Emerging Angles
- Claude (Multi-agent deep research - automated) → Evidence synthesis
- Grok (X/Twitter Discourse - manual) → **Opinion/Sentiment ONLY**

## Evidence Sources (For factual claims)

### Tier 1: Meta-analyses, Systematic Reviews, Official Statistics
<!-- Add after cross-validation -->

### Tier 2: RCTs, Large Studies, Government Reports
<!-- Add after cross-validation -->

### Tier 3: Case Studies, Industry Reports, News
<!-- Add after cross-validation -->

---

## Opinion/Discourse Sources (For "what people think" context)

⚠️ **These are NOT evidence** - Use only for podcast segments contrasting belief vs. research

### Expert Opinion (credentialed but not peer-reviewed)
<!-- Industry leaders, researchers speaking informally, etc. -->

### Public Discourse (X/Twitter, forums)
<!-- Notable voices, debates, sentiment - cite with handle + date -->
<!-- Add after cross-validation -->

---

## Notes
- Research compiled: [Today's date in YYYY-MM-DD format]
- Sources cross-validated across multiple tools
- Conflicting sources noted in research/p3-briefing.md
```

**VERIFY SETUP COMPLETE - File State Check:**

Use Glob to verify directory structure: `apps/podcast/pending-episodes/YYYY-MM-DD-slug/**/*`
Expected: research/, logs/, tmp/ subdirectories present with initial files.

**Expected directory structure:**
```
apps/podcast/pending-episodes/YYYY-MM-DD-slug/
├── research/
│   └── documents/
├── logs/
│   ├── prompts.md (exists, ~500 bytes)
│   └── [p1-brief.md will be created if user provides research prompt]
├── tmp/
└── sources.md (exists, ~300 bytes)
```

**File State - AFTER Phase 1:**
- ✅ Directory structure created (research/, logs/, tmp/)
- ✅ logs/prompts.md exists with episode details
- ✅ sources.md template created
- ✅ research/p1-brief.md created if user provided research context

---

**EXIT CRITERIA (all must be true to proceed):**
✓ Episode directory created with correct naming
✓ Subdirectories created: research/, research/documents/, logs/, tmp/
✓ logs/prompts.md exists with episode details logged
✓ sources.md template exists
✓ Today's actual date used (not placeholder YYYY-MM-DD)
✓ All file templates use correct paths (research/, logs/)

**Update tasks:** Use TaskUpdate to mark "Setup episode structure and files" as completed, then mark "Conduct deep research" as in_progress.

═══════════════════════════════════════════════════════════════

---

═══════════════════════════════════════════════════════════════
                    PHASES 2-6: RESEARCH & BRIEFING
═══════════════════════════════════════════════════════════════

**PydanticAI Service Alternatives:** Phases 3, 5, 6, 7, and 8 have PydanticAI-powered services in `apps/podcast/services/` that can replace Claude Code sub-agent delegation. These return typed Pydantic models and are testable without API calls. The sub-agent approach (described below) and the service approach are interchangeable — both produce equivalent outputs. See `apps/podcast/services/` for: `digest_research`, `discover_questions`, `cross_validate`, `write_briefing`, `write_synthesis`, `plan_episode`, `write_metadata`, `generate_chapters`.

This section covers:
- **Phase 2:** Academic Foundation (Perplexity)
- **Phase 3:** Question Discovery
- **Phase 4:** Targeted Followup Research
- **Phase 5:** Cross-Validation
- **Phase 6:** Master Briefing Creation

---

### Sequential Deep Research Phase

**CRITICAL PRINCIPLE:** Research tools gather and organize source material. They DO NOT write the final narrative. The podcast-synthesis-writer agent creates the actual report.

**Goal:** Build research progressively - start with academic foundation, identify questions, then gather targeted perspectives.

**Sequential Workflow:**
1. **Phase 1:** Perplexity academic research (comprehensive foundation)
2. **Phase 2:** Analyze results and identify questions to investigate
3. **Phase 3:** Targeted followup research with other tools based on Phase 2 questions

**Note on seed research prompts:** If a `research-prompt.md` file exists in the episode directory, treat it as context and input material - but do NOT use it directly as the deep research prompts. You must create NEW, distinct prompts optimized for the sequential workflow below.

#### **Phase 1: Perplexity - Academic Foundation**

Create a comprehensive academic research prompt with full methodology:

```
Research [TOPIC].

**Research methodology:**
- Prioritize peer-reviewed studies, meta-analyses, systematic reviews, and authoritative sources
- Distinguish between correlation and causation in findings
- Report effect sizes and practical significance, not just statistical significance
- Note the study populations and whether findings generalize to relevant demographics
- Compare individual studies against meta-analyses and systematic reviews
- Identify preliminary research vs. well-replicated findings
- Note funding sources and potential conflicts of interest when relevant
- Include contradictory findings and areas of scientific uncertainty
- Cite specific studies, researchers, and sources throughout
- Provide full source URLs for all citations

**Output:** Comprehensive research report with extensive citations, sample sizes, methodological details, and source links.
```

**Example for "early childhood educator burnout interventions":**
```
Research early childhood educator burnout interventions and their effectiveness.

**Research methodology:**
- Prioritize peer-reviewed studies, meta-analyses, systematic reviews, and authoritative sources
- Distinguish between correlation and causation in findings
- Report effect sizes and practical significance, not just statistical significance
- Note the study populations and whether findings generalize to relevant demographics
- Compare individual studies against meta-analyses and systematic reviews
- Identify preliminary research vs. well-replicated findings
- Note funding sources and potential conflicts of interest when relevant
- Include contradictory findings and areas of scientific uncertainty
- Cite specific studies, researchers, and sources throughout
- Provide full source URLs for all citations

**Output:** Comprehensive research report with extensive citations, sample sizes, methodological details, and source links.
```

---

#### **Phase 2: Question Discovery & Gap Analysis**

**After Perplexity research completes, analyze the results to identify questions we should investigate.**

**Goal:** Think creatively about what questions we should be asking - don't assume we know the right questions or their answers.

**Create a structured analysis in prompts.md:**

```markdown
## Phase 2: Question Discovery

**After analyzing Perplexity's academic research, here are the questions we should investigate:**

### What subtopics and themes emerged?
- [List the major subtopics found in the research]
- [Note which got extensive coverage vs. brief mentions]

### What gaps exist in the academic literature?
- [What hasn't been studied?]
- [What populations or contexts are missing?]
- [What time periods lack coverage?]

### What recent developments aren't covered?
- [What's happened in the last 12 months that academic research hasn't caught up with?]
- [What emerging trends or events need investigation?]

### What contradictions or uncertainties need more sources?
- [Where did sources disagree?]
- [What areas showed high uncertainty?]
- [What requires additional perspectives to understand?]

### What industry/implementation questions arose?
- [How is this actually implemented in practice?]
- [What do case studies and real-world examples show?]
- [What are the business/economic considerations?]

### What policy/regulatory angles need investigation?
- [What regulations or policies apply?]
- [How do different jurisdictions approach this?]
- [What's the strategic/policy context?]

### What practitioner perspectives are missing?
- [What would people actually doing this work say?]
- [What regional or local perspectives matter?]
- [What's being discussed in professional communities?]
```

**Use this analysis to create targeted, specific prompts for Phase 3 tools.**

---

#### **Phase 3: Targeted Followup Research**

Based on Phase 2 question discovery, create specific prompts for each tool.

**Important:** Default to using all four Phase 3 tools (Grok, ChatGPT, Gemini, Claude). Each tool provides a unique perspective that strengthens the research. Only omit a tool if its focus area is genuinely not applicable to the topic - this should be rare.

**Grok - X/Twitter Discourse & Real-Time Sentiment (OPINION, not evidence)**

⚠️ **Note:** Grok output is PUBLIC OPINION, not scientific evidence. Use for "what people are saying" segments in the podcast, contrasting popular belief with research findings.

Template:
```
Search X/Twitter and recent news for [TOPIC].

**Active X/Twitter Debates (last 30 days):**
- Who are the loudest voices on this topic? (Names, handles, credentials)
- What positions are they arguing? Quote specific posts.
- What's the sentiment split? (e.g., "60% skeptical, 40% supportive")
- Include engagement metrics for notable posts

**Practitioner Complaints & Frustrations:**
- What are people *doing this work* complaining about on X?
- What do they say "the research gets wrong" or "academics don't understand"?

**News from the Last 30 Days:**
- What happened THIS MONTH? (announcements, launches, controversies)
- Link to specific articles with dates

**Contrarian Takes:**
- Who's arguing against the mainstream view? What's their case?
- Who's defending the mainstream view against critics?

**Output format:**
- Name every source (person + handle + credential + date)
- Tag credibility: [HIGH] industry leader, [MED] informed practitioner, [LOW] random account
- Include X post URLs where possible
```

**Example based on "early childhood educator burnout" Phase 2 analysis:**
```
Search X/Twitter and recent news for early childhood educator burnout.

**Active X/Twitter Debates (last 30 days):**
- Who are the loudest voices discussing ECE burnout? (Names, handles, credentials)
- What positions are they arguing? Quote specific posts.
- What's the sentiment - are educators optimistic or pessimistic about solutions?

**Practitioner Complaints & Frustrations:**
- What are ECE teachers complaining about on X that isn't captured in research?
- What do they say policymakers or researchers "don't get"?

**News from the Last 30 Days:**
- Any new programs, policies, or controversies announced this month?

**Contrarian Takes:**
- Is anyone arguing burnout is overstated? What's their case?
- Is anyone defending current support systems?

**Output format:**
- Name every source (person + handle + credential + date)
- Tag credibility: [HIGH] industry leader, [MED] informed practitioner, [LOW] random account
- Include X post URLs where possible
```

---

**GPT-Researcher - Industry & Case Studies**

Template:
```
Research [TOPIC], focusing on these specific questions:

**Industry Analysis:**
- [Specific question from Phase 2 about market dynamics]
- [Specific question about business models or economics]

**Case Studies & Implementation:**
- [Specific question about real-world implementations]
- [Specific question about what worked/didn't work in practice]

**Technical Details:**
- [Specific question about technical implementation if relevant]
- [Specific question about comparative analysis]

Focus on: Industry analyst reports, market research, case studies, technical documentation, financial/business analysis.
Provide comprehensive findings with citations, data sources, and comparative analysis where relevant.
```

**Example based on "early childhood educator burnout" Phase 2 analysis:**
```
Research early childhood educator burnout, focusing on these specific questions:

**Industry Analysis:**
- What are the economic costs of educator turnover in early childhood education?
- What business models or organizational structures correlate with lower burnout?

**Case Studies & Implementation:**
- What specific burnout intervention programs have been implemented and evaluated?
- What does the data show about effectiveness of different intervention types (workload reduction vs. wellness programs vs. compensation)?

**Comparative Analysis:**
- How do burnout rates and interventions differ between private vs. public early childhood settings?
- What can we learn from other helping professions (nursing, social work) that reduced burnout?

Focus on: Industry analyst reports, market research, case studies, technical documentation, financial/business analysis.
Provide comprehensive findings with citations, data sources, and comparative analysis where relevant.
```

---

**Gemini Deep Research - Policy & Strategic Context**

Template:
```
Research [TOPIC], focusing on these specific questions:

**Regulatory & Policy Frameworks:**
- [Specific question from Phase 2 about regulations]
- [Specific question about policy approaches]

**Comparative Policy Analysis:**
- [Specific question about how different jurisdictions handle this]
- [Specific question about policy effectiveness]

**Strategic Context:**
- [Specific question about strategic considerations]
- [Specific question about policy debates or reforms]

Focus on: Regulatory frameworks, legislation, government policy documents, strategic plans, comparative policy analysis.
Provide findings with official source citations, effective dates, and policy context.
```

**Example based on "early childhood educator burnout" Phase 2 analysis:**
```
Research early childhood educator burnout, focusing on these specific questions:

**Regulatory & Policy Frameworks:**
- What regulations exist around educator-to-child ratios and how do they impact workload?
- What policies have governments implemented specifically to address educator burnout?

**Comparative Policy Analysis:**
- How do different countries approach educator compensation, working conditions, and support?
- What can we learn from jurisdictions that successfully reduced burnout rates?

**Strategic Context:**
- What policy debates are ongoing about early childhood workforce sustainability?
- What systemic reforms are being proposed or tested?

Focus on: Regulatory frameworks, legislation, government policy documents, strategic plans, comparative policy analysis.
Provide findings with official source citations, effective dates, and policy context.
```

---

**Claude Research - Comprehensive Synthesis**

Template:
```
Research [TOPIC], focusing on these specific questions:

[List 3-5 specific questions from Phase 2 that require multi-dimensional analysis across academic, industry, policy, and recent sources]

**Research methodology:**
- Conduct comprehensive research across academic, industry, policy, and recent sources
- Prioritize authoritative sources and distinguish correlation from causation
- Note methodological limitations and conflicts of interest
- Include contradictory findings and areas of uncertainty
- Cite specific studies, reports, and sources extensively with URLs
```

**Example based on "early childhood educator burnout" Phase 2 analysis:**
```
Research early childhood educator burnout, focusing on these specific questions:

- What is the relationship between educator burnout and child outcomes (development, safety, learning)?
- How do systemic factors (compensation, ratios, administrative burden) interact to create burnout?
- What does the evidence show about the long-term sustainability of the early childhood workforce?

**Research methodology:**
- Conduct comprehensive research across academic, industry, policy, and recent sources
- Prioritize authoritative sources and distinguish correlation from causation
- Note methodological limitations and conflicts of interest
- Include contradictory findings and areas of uncertainty
- Cite specific studies, reports, and sources extensively with URLs
```

**Display the Phase 1 Perplexity prompt to user, then save to logs/prompts.md:**

"I've created the Phase 1 Perplexity academic research prompt with comprehensive methodology.

**📋 PERPLEXITY PROMPT (Phase 1 - Academic Foundation):**

```
[DISPLAY THE FULL PROMPT HERE - user needs to see exactly what will be researched]
```

This prompt will now be saved to logs/prompts.md and used for Phase 1 research.

---

**Sequential Research Workflow:**

**Phase 1: Academic Foundation (Start Here)**
- Run Perplexity first with the comprehensive academic prompt
- This builds the foundation from peer-reviewed research
- When complete, paste results into research/p2-perplexity.md

**Phase 2: Question Discovery (After Perplexity)**
- I'll analyze Perplexity's results
- Identify what questions we should be asking
- Discover gaps, contradictions, recent developments, implementation questions
- Create Phase 2 analysis in prompts.md
- Generate targeted Phase 3 prompts based on these questions

**Phase 3: Targeted Followup (Based on Phase 2)**
- Run Grok, ChatGPT, Gemini, and/or Claude with specific questions from Phase 2
- Each tool focuses on questions that match its strengths
- Much more targeted and valuable than parallel generic research

---

**I'll now attempt to automate Phase 1 Perplexity submission using the Perplexity API.**

**Using the `perplexity-deep-research` skill:**
- API-based automation with sonar-deep-research model
- Supports sync (default, 30-120s blocking) and async (fire-and-poll, no timeout) modes
- Use `--async` for submit-and-poll, or `--no-wait` to fire off research and continue other work
- Automatically formatted output saved to research/p2-perplexity.md with metadata sidecar (.meta.json)

**Fallback:** If API automation fails, manually run at https://www.perplexity.ai/ with Pro Search enabled.

**After Phase 1 completes:** Let me know and I'll begin Phase 2 question discovery analysis."

---

### Phase 1 Automation: Perplexity Academic Research

#### **Perplexity API (sonar-deep-research)**

**Invoke the perplexity-deep-research skill:**

Use the Skill tool: `perplexity-deep-research`

The skill will:
1. Check for PERPLEXITY_API_KEY in .env file
2. Submit to sonar-deep-research model with reasoning_effort=high
3. **Sync mode (default):** Wait 30-120 seconds for completion with automatic retries
4. **Async mode (`--async`):** Submit, poll for results, no client-side timeout
5. **Fire-and-forget (`--no-wait`):** Submit and return job ID; poll later with `--job-id`
6. Extract and format research report with citations
7. Save results to research/p2-perplexity.md + metadata to .meta.json

**Expected time:** 30-120 seconds (sync) or fire-and-poll (async, no blocking)

**Fallback if skill unavailable or API fails:**
- Go to https://www.perplexity.ai/
- Enable Pro Search
- Paste prompt from prompts.md
- Copy output to research/p2-perplexity.md

**Note:** API requires PERPLEXITY_API_KEY in .env. Get key at https://www.perplexity.ai/settings/api

**Update tasks:** "Conduct deep research" remains in_progress (Phase 1 running).

---

### Phase 2: Question Discovery Analysis

**When Perplexity research completes (Phase 1 complete):**

**⚠️ CONTEXT OPTIMIZATION: Use sub-agent instead of reading research directly**

1. **Generate digest of Perplexity research:**

   Use the Task tool to spawn `podcast-research-digest` agent:
   ```
   Generate a compact digest of the Perplexity research.

   Episode directory: apps/podcast/pending-episodes/YYYY-MM-DD-slug/
   Research file: research/p2-perplexity.md

   Create research/p2-perplexity-digest.md with:
   - Table of contents
   - Key findings (priority order)
   - Statistics & data points
   - Sources (tiered)
   - Searchable topics and keywords
   - Questions answered and NOT answered
   - Contradictions & nuances
   ```

2. **Delegate question discovery to sub-agent:**

   Use the Task tool to spawn `podcast-question-discovery` agent:
   ```
   Analyze Perplexity research to discover questions for targeted followup.

   Episode directory: apps/podcast/pending-episodes/YYYY-MM-DD-slug/
   Episode topic: [Topic]

   Read research/p2-perplexity-digest.md (or p2-perplexity.md if no digest).
   Create research/question-discovery.md with gap analysis.
   Return ONLY the summary to orchestrator.
   ```

   The agent will return a summary with:
   - Key findings from Perplexity
   - Critical gaps to address
   - Recommended tool allocation for Phase 3

3. **Generate targeted Phase 3 prompts for ALL 5 TOOLS** based on the agent's gap analysis:
   - **GPT-Researcher** - Industry analysis, case studies, implementation details, technical documentation, market dynamics (automated)
   - **Gemini** - Policy analysis, regulatory frameworks, comparative policy analysis, strategic context, official documents (automated)
   - **Together** - Exploratory multi-hop, adjacent topics, contrarian views (automated)
   - **Claude** - Multi-agent deep research across academic, industry, policy, and recent sources (automated)
   - **Grok** - Recent developments (last 12 months), practitioner perspectives, regional insights, real-time discussions (manual)

   **🚨 DEFAULT: CREATE PROMPTS FOR ALL 5 PHASE 3 TOOLS**

   Omitting a tool should be rare and only for a specific reason. Examples of valid reasons to skip:
   - Skip Gemini if topic truly has zero policy/regulatory/strategic angles
   - Skip GPT-Researcher if topic has no industry/technical implementation aspects
   - Skip Together if topic has no exploratory or adjacent angles worth investigating
   - Skip Claude if other tools provide sufficient cross-dimensional coverage (Claude is now automated)
   - Skip Grok if topic has no recent developments or practitioner perspectives

   **In practice:** Most topics benefit from all perspectives. Use all 5 tools unless you have a specific reason not to.

4. **Display MANUAL prompts FIRST so user can start while automation runs**

   **IMPORTANT:** Show manual prompts before launching automated tools. This allows the user to submit Grok research in parallel with GPT-Researcher/Gemini/Claude automation.

   ```
   ═══════════════════════════════════════════════════════════════
   📋 MANUAL RESEARCH PROMPTS - Submit these now while automation runs
   ═══════════════════════════════════════════════════════════════

   **GROK PROMPT (paste at https://x.com/i/grok):**
   ```
   [Full Grok prompt here]
   ```

   ⏳ Submit this now - automation will run in parallel below.

   ═══════════════════════════════════════════════════════════════
   🤖 AUTOMATED RESEARCH - Launching now
   ═══════════════════════════════════════════════════════════════

   **GPT-RESEARCHER (6-20 min):** [Brief description of focus]
   **GEMINI (3-10 min):** [Brief description of focus]
   **CLAUDE (5-15 min):** [Brief description of focus]

   Launching automated research agents...
   ```

   After displaying prompts, save all to logs/prompts.md

5. **Create research files for Phase 3 results:**

Use the Write tool to create placeholder files in the episode's research/ directory:
- `research/p2-chatgpt.md` — GPT-Researcher output (automated, will be populated by skill)
- `research/p2-gemini.md` — Gemini output (automated, will be populated by skill)
- `research/p2-claude.md` — Claude output (automated, will be populated by deep research orchestrator)
- `research/p2-grok.md` — Grok output (manual, user pastes from https://x.com/i/grok)

Each file follows the standard template: header with date/focus, Research Output section, and Sources section.

6. **Launch automated research (user should already be submitting manual prompts)**

**Update tasks:** "Conduct deep research" remains in_progress (Phase 2 analysis complete, Phase 3 ready).

---

### Phase 3 Automation: Targeted Followup Research

**Execution order:**
1. Manual prompt (Grok) already displayed above - user submits while automation runs
2. Launch GPT-Researcher, Gemini, Together, and Claude in parallel (automated)
3. All 5 tools complete roughly together

**Launch automated research skills in parallel:**

Use the Skill tool to invoke these (long-running, so launch via Task tool with `run_in_background: true`):
- **GPT-Researcher:** Invoke `gpt-researcher` skill with the industry/technical prompt from prompts.md. Save results to research/p2-chatgpt.md.
- **Gemini:** Invoke `gemini-deep-research` skill with the policy/strategic prompt from prompts.md. Save results to research/p2-gemini.md.
- **Together:** Invoke together research with the exploratory prompt from prompts.md. Save results to research/p2-together.md.
- **Claude:** Automated via deep research orchestrator (multi-agent pipeline). Save results to research/p2-claude.md.

**Expected timeline:**
- User submits Grok while reading this (~1-2 min)
- GPT-Researcher runs (6-20 min)
- Gemini runs (3-10 min)
- Claude runs (5-15 min)
- All research completes roughly together

**Fallback for automation failures:** Use prompts from logs/prompts.md manually

**Update tasks:** When all Phase 3 results are collected, use TaskUpdate to mark "Conduct deep research" as completed, then mark "Cross-validate research findings" as in_progress.

---

### 3. Cross-Validation Phase

**Immediately after all Phase 3 research is collected, proceed automatically to cross-validation.**

**⚠️ DO NOT STOP AND WAIT FOR USER - CONTINUE AUTOMATICALLY**

**⚠️ CONTEXT OPTIMIZATION: Use sub-agents instead of reading all research directly**

**Step 1: Generate digests for all research files (if not already done):**

For each p2-*.md file that doesn't have a digest, spawn `podcast-research-digest` agent:
```
Generate a compact digest of the [tool] research.

Episode directory: apps/podcast/pending-episodes/YYYY-MM-DD-slug/
Research file: research/p2-[tool].md

Create research/p2-[tool]-digest.md with key findings, sources, and topics.
```

**Step 2: Delegate cross-validation to sub-agent:**

Use the Task tool to spawn `podcast-cross-validator` agent:
```
Cross-validate research findings across all sources.

Episode directory: apps/podcast/pending-episodes/YYYY-MM-DD-slug/
Episode topic: [Topic]

Read all research/p2-*-digest.md files (or p2-*.md if no digests).
Create research/cross-validation.md with:
- Verification matrix (VERIFIED / SINGLE-SOURCE / CONFLICTING)
- Source quality assessment
- Coverage map
- Contradictions requiring attention

Return ONLY the summary to orchestrator.
```

The agent will return a summary with:
- Verification results (N verified, N single-source, N conflicting)
- Strongest and weakest evidence areas
- Key contradictions to resolve in briefing
- Recommendation for Phase 6

**Update tasks:** Use TaskUpdate to mark "Cross-validate research findings" as completed, then mark "Create master research briefing" as in_progress.

---

### 4. Master Research Briefing Creation

**After completing cross-validation, immediately proceed to create research/p3-briefing.md.**

**⚠️ DO NOT STOP AND WAIT FOR USER - CONTINUE AUTOMATICALLY**

**⚠️ CONTEXT OPTIMIZATION: Delegate briefing creation to sub-agent**

Use the Task tool to spawn `podcast-briefing-writer` agent:
```
Create the master research briefing from cross-validated research.

Episode directory: apps/podcast/pending-episodes/YYYY-MM-DD-slug/
Episode topic: [Topic]
Episode title: [Title]

Read:
- research/cross-validation.md (verification status)
- All research/p2-*-digest.md files (or p2-*.md if no digests)

Create research/p3-briefing.md using enhanced Wave 1 template with ALL required sections:
- Verified key findings (organized by TOPIC)
- Depth Distribution Analysis (B1.1)
- Practical Implementation Audit (B1.3)
- Story Bank with 3-5 stories (B2.2)
- Counterpoint Discovery (B1.2)
- Notes for Synthesis Agent with takeaway requirements (B2.1)
- Research gaps & uncertainties
- Source inventory (tiered by quality)

Return ONLY the summary to orchestrator.
```

The agent will return a summary with:
- Subtopics covered and verified findings count
- Wave 1 sections complete status
- Stories in bank and counterpoints identified
- Confirmation ready for Phase 7

**Key principles the agent follows:**
- Organize by TOPIC, not by which tool found it
- Include evidence hierarchy (what's well-established vs preliminary)
- Surface contradictions explicitly
- Keep opinion separate from evidence (Grok's X/Twitter in PUBLIC DISCOURSE only)
- Flag gaps that Opus should acknowledge

**Update sources.md with verified sources organized by tier.**

---

**⭐ PHASE 6 EXIT CRITERIA - WAVE 1 ENFORCEMENT:**

Before proceeding to Phase 7 (Synthesis), verify ALL of these requirements:

✓ research/p3-briefing.md created using enhanced template (docs/templates/podcast/p3-briefing-enhanced.md)
✓ **Depth Distribution Analysis table present** (B1.1)
  - Shows depth rating for all major subtopics
  - Flags shallow topics
  - Includes recommendations for synthesis
✓ **Practical Implementation Audit completed** (B1.3)
  - Each major finding includes "how to do this" steps
  - Steps include concrete parameters (timeframes, thresholds, criteria)
  - Actionability confirmed for each finding
✓ **Story Bank created with 3-5 stories minimum** (B2.2)
  - Each story tagged by memorability, emotional resonance
  - Integration opportunities documented
✓ **Counterpoint Discovery documented** (B1.2)
  - Where sources disagree or present alternatives
  - Dialogue opportunities identified (2-3 minimum)
✓ **Notes for Synthesis Agent include takeaway clarity requirements** (B2.1)
  - Requirement stated: "Each section ends with listener implications"
  - 1-3 core takeaways identified for entire episode
✓ All sources tiered by quality (Tier 1-3)
✓ Research gaps and uncertainties explicitly noted

**If ANY Wave 1 requirement is missing, DO NOT PROCEED to Phase 7.**
Return to Phase 6 and complete the missing sections.

**Update tasks:** Use TaskUpdate to mark "Create master research briefing" as completed, then mark "Synthesize narrative report" as in_progress.

---

═══════════════════════════════════════════════════════════════
                    PHASE 7: SYNTHESIS
═══════════════════════════════════════════════════════════════

**ENTRY REQUIREMENTS:**
✓ research/p3-briefing.md created with organized findings (Phase 6)
✓ All research/p2-*.md files present
✓ Sources cross-validated and verified
✓ Ready for narrative creation

**⚠️ DO NOT STOP AND WAIT FOR USER - INVOKE AGENT AUTOMATICALLY**

**WORK TO DO:** Invoke the podcast-synthesis-writer agent to create report.md:

Use the Task tool with subagent_type='podcast-synthesis-writer':

```
Transform the research materials into a narrative podcast report.

Episode directory: apps/podcast/pending-episodes/YYYY-MM-DD-slug/
Episode title: [Episode Title]

The podcast-synthesis-writer agent will:
1. Read research/p3-briefing.md and individual research/p2-*.md files
2. Transform organized research into engaging narrative report
3. Apply evidence standards and podcast storytelling principles
4. Create report.md with proper citations and source hierarchy
5. **Keep opinion separate from evidence:**
   - Factual claims cite only Tier 1-3 sources (peer-reviewed, studies, reports)
   - X/Twitter discourse (from Grok) used ONLY for "popular belief vs research" segments
   - Frame opinion content as: "While many on social media argue X... the research actually shows Y"
6. **⭐ ENFORCE Wave 1 quality requirements (BLOCKING):**
   - **Takeaway Clarity (B2.1):** Each major section MUST end with "What does this mean for listeners?"
   - **Core Takeaways:** 1-3 explicit takeaways stated clearly (not just implied)
   - **Story Integration (B2.2):** Use high-memorability stories from Story Bank strategically
   - **Practical Actionability (B1.3):** Findings MUST include specific implementation steps
   - **Depth Balance (B1.1):** Topics receive coverage proportional to depth ratings from briefing
   - If briefing is missing Wave 1 sections, FAIL and request Phase 6 completion
7. Verify all quality requirements are met

Required files must exist:
- research/p3-briefing.md (master briefing with Wave 1 sections complete)
- research/p2-*.md files (individual tool outputs for additional context)
```

**The agent handles all synthesis requirements:**
- Narrative architecture and storytelling
- Evidence standards and citation format
- Podcast-optimized writing
- Accessibility without oversimplification
- Source organization and verification
- Quality checklist validation

**VERIFY SYNTHESIS COMPLETE:**

Use Glob to confirm `apps/podcast/pending-episodes/YYYY-MM-DD-slug/report.md` exists, then Read it to verify content quality and length (expect 15-25KB, 5,000-8,000 words).

**Expected output:**
- ✅ report.md exists
- ✅ File size: 15-25KB
- ✅ Word count: 5,000-8,000 words (typical for 30-40 min episode)

---

**EXIT CRITERIA (all must be true to proceed):**
✓ report.md created in episode root directory
✓ File size 15-25KB (~5,000-8,000 words)
✓ Narrative structure (not bullet points)
✓ All claims have source citations
✓ Citations link to verified sources from research/p3-briefing.md

**Update tasks:** Use TaskUpdate to mark "Synthesize narrative report" as completed, then mark "Create episode content plan" as in_progress.

═══════════════════════════════════════════════════════════════

---

═══════════════════════════════════════════════════════════════
                    PHASE 8: EPISODE PLANNING
═══════════════════════════════════════════════════════════════

**ENTRY REQUIREMENTS:**
✓ report.md created (Phase 7)
✓ sources.md created with validated citations
✓ Ready to create episode structure guidelines for NotebookLM

**⚠️ DO NOT STOP AND WAIT FOR USER - INVOKE AGENT AUTOMATICALLY**

**⚠️ CONTEXT OPTIMIZATION: Delegate episode planning to sub-agent**

Use the Task tool to spawn `podcast-episode-planner` agent:
```
Create the episode content plan for NotebookLM audio generation.

Episode directory: apps/podcast/pending-episodes/YYYY-MM-DD-slug/
Episode title: [Title]
Series: [Series name or "Standalone"]

Read report.md, sources.md, and research/p3-briefing.md.
Create content_plan.md using enhanced Wave 2 template with ALL structural elements:
- Episode Structure Map
- Mode-Switching Framework
- Signposting Language
- Depth Budget
- Problem → Solution Architecture
- Counterpoint Moments (with ASSIGNED speaker positions)
- Episode Arc
- NotebookLM guidance

Return ONLY the summary to orchestrator.
```

The agent will return a summary with:
- Episode classification (series position, evidence status, content density)
- Toolkit selections (hook type, takeaway structure)
- Wave 2 checks confirmation
- Ready for Phase 9 confirmation

**The agent produces:**
- `content_plan.md` - Episode structure guide with Wave 2 structural design + NotebookLM instructions (10-15KB)

**What content_plan.md provides for NotebookLM:**
- **Wave 2 Structural Design:** Episode Structure Map, Mode-Switching Framework, Signposting Language, Depth Budget, Problem→Solution Architecture, Build Toward Resolution, Counterpoint Moments with assigned positions, Episode Arc
- Three-section structure (Foundation → Evidence → Application)
- Key terms that must be defined
- Specific studies/findings to emphasize
- Narrative arc and transitions
- Opening hook and closing callback guidance
- **Counterpoint execution instructions** with assigned speaker positions
- Call-to-action

**VERIFY EPISODE PLANNING COMPLETE:**

Use Glob to confirm `apps/podcast/pending-episodes/YYYY-MM-DD-slug/content_plan.md` exists, then Read to verify it contains all required sections.

**Expected output:**
- ✅ content_plan.md exists (8-12KB)

---

**⭐ PHASE 8 EXIT CRITERIA - WAVE 2 ENFORCEMENT:**

Before proceeding to Phase 9 (Audio Generation), verify ALL of these requirements:

✓ content_plan.md created using enhanced template (docs/templates/podcast/content_plan-enhanced.md)

**Structural Clarity (Wave 2, Tasks A1.1-A1.3):**
✓ Episode Structure Map defined (modes, durations, transitions for each section)
✓ Mode-Switching Framework applied (each mode has clear language markers)
✓ Signposting language included (structure preview, transitions, progress markers)

**Depth & Balance (Wave 2, Task A1.4):**
✓ Depth Budget validates even coverage (no primary theme <15% when it deserves more)
✓ Time allocation matches research depth from p3-briefing.md
✓ If runtime ≤30 min, practical content front-loaded in Section 2

**Content Architecture (Wave 2, Tasks A2.1-A2.2):**
✓ Problem → Solution architecture clear
✓ Episode builds toward clear resolution/takeaway (not trailing off)

**Dialogue Dynamics (Wave 2, Task A2.3) - EXECUTION CRITICAL:**
✓ Counterpoint moments designed (2-3 minimum)
✓ Each counterpoint includes: Topic, Speaker A position, Speaker B position
✓ Language templates provided ("Wait, but what about..." phrases)
✓ Positions are ASSIGNED (not just "present both views")
✓ **Quality check:** Each counterpoint has EXPLICIT DISAGREEMENT, not collaborative framing

**NotebookLM Guidance:**
✓ Key terms to define listed
✓ Studies/findings to emphasize identified
✓ Stories to feature selected from Story Bank
✓ Transition moments planned with signposting language
✓ Counterpoint execution instructions for NotebookLM
✓ Closing callback designed
✓ Call-to-action included

**If ANY Wave 2 requirement is missing, DO NOT PROCEED to Phase 9.**
Return to content_plan.md and complete the missing sections.

**Update tasks:** Use TaskUpdate to mark "Create episode content plan" as completed, then mark "Generate audio via NotebookLM" as in_progress.

═══════════════════════════════════════════════════════════════

---

═══════════════════════════════════════════════════════════════
                    PHASE 9: AUDIO GENERATION
═══════════════════════════════════════════════════════════════

**ENTRY REQUIREMENTS:**
✓ report.md created (Phase 7)
✓ content_plan.md created (Phase 8)
✓ research/p3-briefing.md exists
✓ sources.md exists

**Current Approach: Local Audio Worker**

The automated pipeline pauses at Phase 9 and waits for a local machine to run:

```bash
# On a local machine with notebooklm-py installed
uv run python manage.py local_audio_worker
```

The worker polls for episodes awaiting audio, generates audio via `notebooklm-py`, uploads to storage, and resumes the workflow.

**Fallback: Manual NotebookLM Workflow**

If the local audio worker is unavailable, use the `notebooklm-audio` skill for manual workflow via the NotebookLM web interface:

1. Go to https://notebooklm.google.com/
2. Create new notebook
3. Upload the 5 source files (p1-brief.md, report.md, p3-briefing.md, sources.md, content_plan.md)
4. Click "Audio Overview" -> "Customize"
5. Settings: **Deep Dive** format, **Long** length
6. Generate and download audio

After manual generation, process the audio with `podcast-audio-processing` skill for transcription.

---

**Update tasks when audio is ready:** Use TaskUpdate to mark "Generate audio via NotebookLM" as completed, then mark "Process audio (transcribe, chapters, embed)" as in_progress.

---

═══════════════════════════════════════════════════════════════
                    PHASE 10: AUDIO PROCESSING
═══════════════════════════════════════════════════════════════

**ENTRY REQUIREMENTS:**
✓ Audio generated (Phase 9) via NotebookLM API or manual
✓ Audio file is in episode directory (.mp3)

---

### Transcribe and Create Chapters

**NotebookLM output requires transcription:**

Use Glob to check for `*.mp3` and `transcript.txt` in the episode directory.
Then get file metadata with Bash (ffmpeg required):
```bash
ffmpeg -i apps/podcast/pending-episodes/EPISODE_PATH/EPISODE_SLUG.mp3 2>&1
```

**Create chapters from transcript/script:**
- Read script.md or transcript.txt and identify 10-15 natural topic transitions
- Use `[TRANSITION: new section]` markers as primary chapter boundaries
- Create `EPISODE_SLUG_chapters.txt` (FFmpeg format) and `EPISODE_SLUG_chapters.json` (Podcasting 2.0)
- See chapter format templates in podcast-audio-processing skill

**Embed chapters:**
```bash
ffmpeg -i EPISODE_SLUG.mp3 -i EPISODE_SLUG_chapters.txt -map_metadata 1 -codec copy temp.mp3 -y
mv temp.mp3 EPISODE_SLUG.mp3
```

---

### If NotebookLM Audio (Phase 9 Option B)

**Invoke audio processing:**

Use the Skill tool: `podcast-audio-processing`

The skill will:
1. Convert to mp3 if needed (m4a → mp3)
2. Get file metadata (size in bytes, duration)
3. Transcribe with local Whisper (base model) → save to tmp/
4. Analyze transcript and create 10-15 chapter markers
5. Embed chapters into mp3
6. Log to logs/prompts.md

CRITICAL: Note the file metadata when complete (duration in MM:SS, file size in bytes) — needed for publishing phase.

---

**VERIFY AUDIO PROCESSING SUCCEEDED:**

After processing completes, verify using dedicated tools:

1. **mp3 exists:** Use Glob for `apps/podcast/pending-episodes/YYYY-MM-DD-slug/*.mp3`
2. **File size and duration:** Use Bash: `ffmpeg -i apps/podcast/pending-episodes/YYYY-MM-DD-slug/YYYY-MM-DD-slug.mp3 2>&1`
3. **Transcript exists:** Use Glob for `apps/podcast/pending-episodes/YYYY-MM-DD-slug/transcript.txt` or `tmp/*_transcript.json`
4. **Chapters JSON exists:** Use Glob for `apps/podcast/pending-episodes/YYYY-MM-DD-slug/*_chapters.json`
5. **Chapters embedded:** Use Bash: `ffmpeg -i apps/podcast/pending-episodes/YYYY-MM-DD-slug/YYYY-MM-DD-slug.mp3 -f ffmetadata - 2>/dev/null`

**Expected outputs:**

| Source | mp3 | Duration | Transcript | Chapters |
|--------|-----|----------|------------|----------|
| Gemini | ~30MB | ~36:00 | transcript.txt (~15KB) | 10-15 |
| NotebookLM | ~30-40MB | 30-40 min | tmp/*_transcript.json (~400KB) | 10-15 |

**⚠️ Common issues:**

| Issue | Diagnosis | Solution |
|-------|-----------|----------|
| Gemini audio short (<20 min) | Context too small | Check 4 input files total 80KB+ |
| Conversion failed | Check ffmpeg installed | `brew install ffmpeg` |
| Transcription slow | Normal for base model | Wait 5-10 min for 30-40 min audio |
| No chapters found | Check transcript exists | Verify transcript file present |
| Chapters not embedded | FFmpeg metadata error | Re-run embed command manually |

---

**EXIT CRITERIA (all must be true to proceed):**
✓ Final mp3 file exists with correct naming (YYYY-MM-DD-slug.mp3)
✓ File size known (exact bytes)
✓ Duration known (MM:SS or HH:MM:SS format)
✓ Transcript exists (transcript.txt OR tmp/*_transcript.json)
✓ Chapters JSON created (*_chapters.json)
✓ Chapters embedded in mp3 (verified with ffmpeg)
✓ Chapter count: 10-15 chapters
✓ All steps logged to logs/prompts.md

**⚠️ DO NOT PROCEED TO PHASE 11 UNTIL FILE METADATA IS CONFIRMED**

**Update tasks:** Use TaskUpdate to mark "Process audio (transcribe, chapters, embed)" as completed, then mark "Publish episode (cover art, metadata, feed.xml)" as in_progress.

═══════════════════════════════════════════════════════════════

---

═══════════════════════════════════════════════════════════════
                    PHASE 11: PUBLISHING
═══════════════════════════════════════════════════════════════

**ENTRY REQUIREMENTS:**
✓ Audio processing complete (Phase 10)
✓ Duration known (MM:SS format)
✓ File size known (exact bytes)
✓ Transcript exists (transcript.txt)
✓ report.md and research/p3-briefing.md available

---

### Generate Cover Art (runs in parallel with metadata)

```bash
python ~/src/cuttlefish/apps/podcast/tools/cover_art.py apps/podcast/pending-episodes/YYYY-MM-DD-slug/
```

This auto-detects title/series and generates + brands cover art in one step.
Can run in background while creating metadata.

---

**⚠️ CONTEXT OPTIMIZATION: Delegate metadata creation to sub-agent**

Use the Task tool to spawn `podcast-metadata-writer` agent:
```
Generate episode publishing metadata.

Episode directory: apps/podcast/pending-episodes/YYYY-MM-DD-slug/
Episode title: [Title]
Series: [Series name or "Standalone"]
Audio duration: [HH:MM:SS]
Audio file size: [bytes]

Read report.md, transcript.txt, research/p3-briefing.md, and *_chapters.json.
Create logs/metadata.md using enhanced template with:
- Description (plain text + report link)
- "What You'll Learn" (3-5 bullets)
- Key Timestamps (5-7 from chapters)
- Keywords (5-10 episode-specific)
- Resources (5-10 sources with actionable descriptions)
- Call-to-Action (primary + voiced)
- Show Notes HTML
- Feed.xml technical metadata

Return ONLY the summary to orchestrator.
```

The agent will return a summary confirming:
- Description, What You'll Learn, Timestamps generated
- Resources validated
- Keywords generated
- Ready for feed.xml update

**Generate Companion Resources:**

```bash
# Generate summary, checklist, and frameworks
uv run python ~/src/cuttlefish/apps/podcast/tools/generate_companion_resources.py apps/podcast/pending-episodes/YYYY-MM-DD-slug/

# Generate HTML landing page
uv run python ~/src/cuttlefish/apps/podcast/tools/generate_landing_page.py apps/podcast/pending-episodes/YYYY-MM-DD-slug/
```

These scripts create:
- `companion/*-summary.md` - One-page episode summary
- `companion/*-checklist.md` - Action checklist
- `companion/*-frameworks.md` - Key frameworks reference
- `index.html` - Episode landing page

**Publish Episode to Database:**

The Django `publish_episode` management command reads local files and creates/updates the Episode record in the database. The dynamic Django feed views then serve the episode via RSS.

```bash
# Publish episode to database
uv run python manage.py publish_episode apps/podcast/pending-episodes/EPISODE_PATH/
```

**What publish_episode does:**
1. Reads episode files (report.md, metadata, audio, etc.)
2. Creates/updates Episode model in database
3. Creates EpisodeArtifact records for companion resources
4. Episode is immediately available in the dynamic RSS feed

**Feed is served dynamically** via Django views at `/podcast/{slug}/feed.xml`. No manual XML editing required.
- Duration matches file: MM:SS format
- File size matches: exact bytes
- pubDate in RFC 2822 format

**⚠️ Common issues:**
- Duration mismatch → Re-check with `ffmpeg -i file.mp3 2>&1 | grep Duration`
- File size wrong → Re-check with `ls -l file.mp3 | awk '{print $5}'`
- Invalid XML → Check for unclosed tags, improper escaping

---

**⭐ PHASE 11 EXIT CRITERIA - WAVE 4/5 ENFORCEMENT:**

Before proceeding to Phase 12 (Commit & Push), verify ALL of these requirements:

**Core Publishing (required):**
✓ cover.png exists and branded (~1MB)
✓ logs/metadata.md created using enhanced template (docs/templates/podcast/metadata-enhanced.md)
✓ feed.xml updated with new `<item>` entry
✓ `<lastBuildDate>` updated in feed.xml channel metadata
✓ All metadata accurate (duration matches file, size matches file, pubDate is RFC 2822)
✓ 🚨 **Feed validator reports VALID or VALID WITH WARNINGS** (not INVALID)
✓ All ❌ failed checks from validator have been fixed

**Description & Discovery (Wave 4, Task C1.1):**
✓ Plain text description written (1-2 sentences + report link)
✓ "What You'll Learn" section complete (3-5 compelling bullets, verb-led)
✓ Key timestamps extracted (5-7 major sections with enticing descriptions)
✓ Keywords generated (5-10 episode-specific terms, not generic)

**Resources (Wave 4, Task C1.3):**
✓ Resources & Tools section complete (5-10 sources)
✓ Sources grouped by type (Research / Tools / Reading)
✓ Each source has actionable 1-sentence description
✓ All URLs validated and working

**Call-to-Action (Wave 4, Task C1.2):**
✓ Primary CTA defined (clear next step for listener)
✓ Voiced CTA written (natural language for audio)

**Companion Resources (Wave 4, Task C3.1):**
✓ generate_companion_resources.py run (creates summary, checklist, frameworks)
✓ generate_landing_page.py run (creates index.html)
✓ At least one companion resource exists in companion/ directory

**Feed.xml Enhancements (Wave 4, Tasks C2.1-C2.3):**
✓ `<itunes:episodeType>` tag present
✓ `<itunes:episode>` tag present (if series episode)
✓ `<podcast:transcript>` tag present (links to transcript.txt)
✓ Enhanced `<content:encoded>` HTML with Overview, What You'll Learn, Timestamps, Resources sections

**If ANY packaging requirement is missing, complete it before proceeding.**

**⚠️ DO NOT PROCEED TO PHASE 12 UNTIL ALL EXIT CRITERIA MET**

**Update tasks:** Use TaskUpdate to mark "Publish episode (cover art, metadata, feed.xml)" as completed, then mark "Commit and push to GitHub" as in_progress.

═══════════════════════════════════════════════════════════════

---

═══════════════════════════════════════════════════════════════
                    PHASE 12: COMMIT & PUSH
═══════════════════════════════════════════════════════════════

**ENTRY REQUIREMENTS:**
✓ feed.xml updated with episode metadata (Phase 11)
✓ cover.png generated and branded (Phase 11)
✓ All episode files present in episode directory
✓ Publishing metadata complete (logs/metadata.md)

**CRITICAL:** This phase publishes your episode. Without completing BOTH commit AND push, the episode stays local and never goes live.

---

### Step 1: Review Changes

```bash
git status
git diff feed.xml
```

**VERIFY:**
- All episode files show as untracked or modified
- feed.xml shows new `<item>` entry
- No unexpected changes to other files

---

### Step 2: Stage All Files

```bash
git add podcast/feed.xml apps/podcast/pending-episodes/YYYY-MM-DD-slug/
```

**Files being added:**
- `research/p1-brief.md` - Research brief
- `research/p2-*.md` - Individual tool research outputs
- `research/p3-briefing.md` - Master briefing (organized by topic)
- `research/documents/` - Any PDFs or supporting files (if present)
- `logs/prompts.md` - All prompts used during creation
- `logs/metadata.md` - Publishing metadata
- `tmp/*_transcript.json` - Full Whisper transcript (optional - large file)
- `sources.md` - Source links organized by tier
- `report.md` - Final narrative report from synthesis agent
- `report.html` - HTML report (series only)
- `transcript.html` - HTML transcript (series only)
- `cover.png` - Episode cover art with branding
- `YYYY-MM-DD-slug.mp3` - Final audio with embedded chapters
- `YYYY-MM-DD-slug_chapters.json` - Podcasting 2.0 format
- Updated `feed.xml`

**Note:** .m4a source files are gitignored automatically (see .gitignore line 23)

**VERIFY FILES STAGED:**
```bash
git status
```

**Expected output:** All episode files should show in "Changes to be committed" (green)

---

### Step 3: Commit Changes

```bash
git commit -m "$(cat <<'EOF'
feat: Add episode on [topic]

- Add episode "[title]" covering [key topics]
- Conduct sequential deep research: Perplexity academic foundation → question discovery → targeted followup with [tools used]
- Create master research briefing organized by topic
- Synthesize final narrative report with podcast-synthesis-writer agent
- Generate AI cover art with Gemini via OpenRouter and apply podcast branding
- Generate full transcript using local Whisper (base model)
- Create [N] chapter markers covering key topics
- Embed chapters into mp3 for podcast app support
- Update feed.xml with episode metadata
- Episode duration: MM:SS, covers [key highlights]
EOF
)"
```

**VERIFY COMMIT SUCCEEDED:**
```bash
git log -1 --oneline
git status
```

**Expected output:**
- `git log` shows your commit message
- `git status` shows "nothing to commit, working tree clean"

**❌ If commit fails:** Check error message. Common issues:
- "nothing to commit" → Files weren't staged, run `git add` again
- Hook failures → Fix issues and retry commit

---

### Step 4: 🚨 **CRITICAL - Push to GitHub** 🚨

```bash
git push
```

**⚠️ WHY THIS MATTERS:**
Without push, the episode stays on your local machine and **NEVER goes live** on GitHub Pages. The workflow is NOT complete until this step succeeds.

**VERIFY PUSH SUCCEEDED:**
```bash
git log -1 --oneline
git ls-remote origin main | grep main
```

**Expected output:**
- Both commands show the SAME commit hash
- Example: `a1b2c3d feat: Add episode on topic`

**✅ If hashes match:** Push succeeded
**❌ If hashes don't match:** Push failed, run `git push` again

**Common push failures:**

| Error | Solution |
|-------|----------|
| "Updates were rejected (non-fast-forward)" | `git pull --rebase origin main` then `git push` |
| "Permission denied" | Check GitHub authentication |
| "Could not resolve host" | Check internet connection |

---

### Step 5: ✅ **FINAL VERIFICATION - Episode is Live**

Wait 2-3 minutes for GitHub Pages deployment, then verify:

Use WebFetch to verify the episode appears at `https://research.yuda.me/podcast/feed.xml`.

**Expected output:** Should return the episode title and enclosure URL

**Alternative verification:** Visit https://research.yuda.me/podcast/feed.xml in browser and search for episode title

**✅ Episode is live when:**
- feed.xml shows new episode
- Episode appears in podcast players (may take 30-60 min for refresh)

**❌ If not found after 5 minutes:**
- Check GitHub Actions: https://github.com/[user]/research/actions
- Look for failed workflows
- Check Pages settings: Settings → Pages → Source should be "main" branch

---

**EXIT CRITERIA (all must be true to complete workflow):**
✓ Commit created successfully
✓ Push completed successfully
✓ Commit hash matches on local and remote
✓ feed.xml updated on live site (after 2-3 min)
✓ Episode appears in feed.xml

**Update tasks:** Use TaskUpdate to mark "Commit and push to GitHub" as completed. Use TaskList to verify all tasks show completed.

═══════════════════════════════════════════════════════════════

## Role Division

**User handles:**
- Manual research submission for web-based tools (Grok)

**You handle:**
- File organization and directory setup
- Reading seed research-prompt.md if present
- **Phase 1:** Setup - Creating episode directory and initial files
- **Phase 2:** Perplexity API automation for academic research (30-120 seconds)
- **Phase 3:** Analyzing Perplexity results and conducting question discovery
- **Phase 4:** Generating targeted prompts and running GPT-Researcher, Gemini, Together, Claude research
- **Phase 5:** Cross-validation matrix creation across all research sources
- **Phase 6:** Master research briefing compilation (research/p3-briefing.md)
- **Phase 7:** **Invoking podcast-synthesis-writer agent** to create report.md
- **Phase 8:** **Invoking podcast-episode-planner** to create content_plan.md
- **Phase 9:** Audio generation via NotebookLM (API or manual)
- **Phase 10:** Transcription (Whisper), chapter creation and embedding
- **Phase 11:** Cover art, metadata, feed.xml update, validation
- **Phase 12:** Git commit and push (publishes episode)

**Audio Generation:**
- **Primary:** NotebookLM Enterprise API (`.claude/skills/notebooklm-enterprise-api/`) - Two-host conversational format, automated via Discovery Engine API
- **Manual fallback:** NotebookLM web interface (`.claude/skills/notebooklm-audio/`) - Use when API unavailable


## Getting Started

When user wants to create a new episode:

1. **Create task list** with TaskCreate
2. **Determine episode details** (use today's date; only ask about series/slug/title if not provided)
3. **Check for existing research-prompt.md** (seed document) and read if present
4. **Phase 1:** Create episode directory and initial files (research/, logs/, tmp/, sources.md)
5. **Phase 2:** Run Perplexity API for academic foundation (30-120 seconds)
6. **Phase 3:** Analyze Perplexity results, conduct question discovery
7. **Phase 4:** Run targeted research (GPT-Researcher, Gemini, Together, Claude automated; Grok manual)
8. **Phase 5:** Create cross-validation matrix across all sources
9. **Phase 6:** Compile master briefing (research/p3-briefing.md organized by topic)
10. **Phase 7:** Invoke podcast-synthesis-writer agent to create report.md
11. **Phase 8:** Invoke podcast-episode-planner to create content_plan.md
12. **Phase 9:** Generate audio via NotebookLM (API or manual, ~10-15 min)
13. **Phase 10:** Transcribe with Whisper, create chapters, and embed in mp3
14. **Phase 11:** Generate cover art, create metadata, update feed.xml, validate
15. **Phase 12:** Git commit and push to publish (EPISODE LIVE)

**Key:** Use TaskUpdate at every phase transition to track progress. The sequential workflow builds research progressively: academic foundation → question discovery → targeted followup, producing higher quality, better verified, non-redundant research.
