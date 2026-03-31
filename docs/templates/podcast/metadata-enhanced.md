# Episode Publishing Metadata

---

## Title
[Episode Title]

---

## Publication Date
[Day, DD Mon YYYY HH:MM:SS GMT - RFC 2822 format]

---

## Series Info (if applicable)
- **Series Name:** [Series Name]
- **Season Number:** [N]
- **Episode Number:** [N]
- **Episode Type:** [full/trailer/bonus]

---

## Audio
- **Duration:** [HH:MM:SS or MM:SS]
- **File Size:** [bytes]
- **Format:** audio/mpeg
- **Bitrate:** 128 kbps

---

## Description (Plain Text for RSS <description>)

[1-2 compelling sentences highlighting key topics, major stories/events covered, and main takeaways.]

Full research report: https://research.yuda.me/podcast/episodes/[YYYY-MM-DD-slug]/report.md

---

## What You'll Learn (Wave 4, Task C1.1)

**Purpose:** Entice and inform listeners about episode value

- [Bullet 1: Specific, compelling insight or myth-bust]
- [Bullet 2: Practical framework or actionable takeaway]
- [Bullet 3: Surprising statistic or research finding]
- [Bullet 4: Story or case study featured]
- [Bullet 5: Key question answered or problem solved]

**Format notes:**
- Start each bullet with verb or "Why/How/What" for clarity
- Be specific (not "delegation tips" but "One interview question that predicts delegation success better than resumes")
- Include numbers when impactful ("The $280,800 annual cost of not delegating")

---

## Key Timestamps (Wave 4, Task C1.1)

**Purpose:** Help listeners navigate to sections of interest

- **[00:00]** - Introduction: [Brief description]
- **[XX:XX]** - [Major section 1 title]: [1-sentence description]
- **[XX:XX]** - [Major section 2 title]: [1-sentence description]
- **[XX:XX]** - [Major section 3 title]: [1-sentence description]
- **[XX:XX]** - [Major section 4 title]: [1-sentence description]
- **[XX:XX]** - [Major section 5 title]: [1-sentence description]
- **[XX:XX]** - Closing: [Key takeaways and next steps]

**Selection criteria:**
- Include 5-7 major sections (not every chapter, just key transitions)
- Descriptions should entice ("The interview question that predicts success" not just "Hiring discussion")

---

## Resources & Tools Mentioned (Wave 4, Task C1.3)

**Purpose:** Make sources actionable and useful beyond just providing URLs

### Research Papers & Meta-Analyses
- **[Author Year - Title]**: [1-sentence actionable description of how to use this]
  - URL: [Full URL]
  - Use this to: [Specific application, e.g., "Justify prioritizing adaptability over experience in hiring"]

### Tools & Templates
- **[Tool/Framework Name]**: [1-sentence description of what it does]
  - URL: [Full URL] (if applicable)
  - Use this to: [Specific application, e.g., "Assess your team's delegation readiness"]

### Further Reading
- **[Resource Name]**: [1-sentence description of additional context it provides]
  - URL: [Full URL]
  - Use this to: [Specific application, e.g., "Explore the full research behind Founder Mode"]

**Format notes:**
- Group sources by type (Research / Tools / Reading) for clarity
- 1-sentence descriptions should be actionable ("Use this to...") not just descriptive
- Total: 5-10 key resources (prioritize quality over quantity)
- Sources come from research/p3-briefing.md Tier 1-2 primarily

---

## Call-to-Action (Wave 4, Task C1.2)

**Purpose:** Guide listeners to next steps

### Primary CTA
[Next logical step for listener - related episode, deep-dive resource, community link]

**Examples:**
- "For more on [related topic], check out Episode X: [Title]"
- "Download the [Framework Name] worksheet at [URL]"
- "Explore the full research report with citations at [report URL]"

### Secondary CTA (if applicable)
[Optional additional resource or action]

### Voiced CTA (for audio - included in episodeFocus prompt)
> "[Brief natural language CTA that hosts can voice at end of episode]"
>
> Example: "If you want to go deeper on this, we've linked the full research report in the show notes with all the studies and sources we mentioned. You can find it at research.yuda.me."

---

## Keywords (iTunes/Podcast Apps)

**Purpose:** Improve discoverability - episode-specific terms

[keyword1, keyword2, keyword3, specific-framework-name, specific-concept, person-name, study-name]

**Selection criteria:**
- 5-10 keywords maximum
- Prioritize: specific technical terms, proper nouns (people, studies), key frameworks, unique concepts
- Avoid generic terms ("leadership", "productivity") - be specific ("situational leadership", "OPPTY framework")
- Extract from: chapter titles, key frameworks mentioned, studies cited, concepts explored

---

## Companion Resources (Wave 4, Task C3.1)

**Purpose:** Provide value beyond audio - resources listeners can use

### One-Page Summary/Cheat Sheet
- **Status:** [Created / Not created]
- **File:** [YYYY-MM-DD-slug-cheatsheet.pdf]
- **Contents:** Key frameworks, formulas, decision trees distilled to one page

### Action Checklist
- **Status:** [Created / Not created]
- **File:** [YYYY-MM-DD-slug-checklist.pdf]
- **Contents:** Step-by-step actionable items from episode

### Framework Diagram
- **Status:** [Created / Not created]
- **File:** [YYYY-MM-DD-slug-framework.png]
- **Contents:** Visual representation of key framework (e.g., OPPTY 4-phase progression)

### Decision Tree (if applicable)
- **Status:** [Created / Not created]
- **File:** [YYYY-MM-DD-slug-decision-tree.png]
- **Contents:** "Which approach to use?" flowchart

### Episode Landing Page
- **Status:** [Generated / Not generated]
- **File:** [YYYY-MM-DD-slug.html]
- **Contents:** Consolidated page with description, timestamps, resources, transcript, companion downloads

---

## Show Notes (HTML - for <content:encoded> in feed.xml)

**Purpose:** Standalone resource useful even without listening

### Structure (Wave 4, Task C2.2)

```html
<h2>Overview</h2>
<p>[Expanded 2-3 sentence description of episode]</p>

<h2>What You'll Learn</h2>
<ul>
  <li>[Bullet from "What You'll Learn" section]</li>
  <li>[...]</li>
</ul>

<h2>Key Timestamps</h2>
<ul>
  <li><strong>[00:00]</strong> - [Section]: [Description]</li>
  <li>[...]</li>
</ul>

<h2>Resources & Tools</h2>
<h3>Research Papers</h3>
<ul>
  <li><a href="[URL]">[Author Year - Title]</a> - [Actionable description]</li>
</ul>

<h3>Tools & Templates</h3>
<ul>
  <li><a href="[URL]">[Tool Name]</a> - [Actionable description]</li>
</ul>

<h2>Companion Resources</h2>
<ul>
  <li><a href="[URL]">One-Page Summary (PDF)</a></li>
  <li><a href="[URL]">Action Checklist (PDF)</a></li>
  <li><a href="[URL]">Framework Diagram (PNG)</a></li>
</ul>

<h2>Full Research Report</h2>
<p>Read the complete research synthesis with all citations at:
<a href="https://research.yuda.me/podcast/episodes/[slug]/report.md">research.yuda.me/podcast/episodes/[slug]/report.md</a></p>
```

**Format notes:**
- Use semantic HTML (h2, h3, ul, li, a, strong)
- All links must be absolute URLs
- Show notes should be useful standalone (not require listening to episode)
- Include all key information from metadata sections above

---

## Feed.xml Technical Metadata (Wave 4, Tasks C2.1, C2.3)

### iTunes Episode Tags
```xml
<itunes:episodeType>full</itunes:episodeType>  <!-- or trailer/bonus -->
<itunes:episode>[episode number]</itunes:episode>  <!-- if series -->
<itunes:season>[season number]</itunes:season>  <!-- if series with seasons -->
```

### Podcast Namespace Tags
```xml
<podcast:transcript url="https://research.yuda.me/podcast/episodes/[slug]/transcript.txt" type="text/plain" />
```

---

## Quality Checklist (Wave 5, Task E3.2)

Before proceeding to Phase 12 (Commit & Push), verify:

### Description & Discovery
✓ Plain text description written (1-2 sentences + report link)
✓ "What You'll Learn" section complete (3-5 compelling bullets)
✓ Key timestamps extracted (5-7 major sections with enticing descriptions)
✓ Keywords generated (5-10 episode-specific terms, not generic)

### Resources
✓ Resources & Tools section complete (5-10 sources)
✓ Sources grouped by type (Research / Tools / Reading)
✓ Each source has actionable 1-sentence description
✓ All URLs validated and working

### Call-to-Action
✓ Primary CTA defined (clear next step)
✓ Voiced CTA written (natural language for hosts)
✓ CTA included in episodeFocus prompt (for audio generation)

### Companion Resources
✓ At least one companion resource created:
  - [ ] One-page summary/cheat sheet
  - [ ] Action checklist
  - [ ] Framework diagram
✓ Resources referenced in show notes with download links

### Show Notes
✓ HTML show notes formatted (structured sections)
✓ All links are absolute URLs
✓ Show notes are useful standalone
✓ All key metadata sections represented

### Feed.xml Enhancements
✓ iTunes episode metadata included (episodeType, episode number if series)
✓ Podcast transcript tag added (links to transcript.txt)
✓ Enhanced <content:encoded> HTML
✓ All XML properly escaped

---

## Notes

[Any additional publishing notes, special considerations, or ideas for this episode]
