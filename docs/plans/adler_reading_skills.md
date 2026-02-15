---
status: Planning
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-02-15
tracking: https://github.com/tomcounsell/ai/issues/113
---

# Adler Reading Skills — Inspectional, Analytical, Syntopical

## Problem

Claude processes documents at one speed: either skimming superficially or reading everything exhaustively. There's no structured protocol for choosing the right depth, extracting structure before content, or systematically comparing multiple sources.

**Current behavior:**
- Given a PDF or document, Claude reads linearly with no structured first-pass
- No framework for deciding how deeply to engage with a document
- Multi-document comparison is ad-hoc — no systematic method for finding agreements, disagreements, and defining the issues
- Research tasks have no protocol for when to skim vs. deep-read vs. cross-reference

**Desired outcome:**
- Three reading skills matching Adler's hierarchy: inspectional (fast first-pass), analytical (deep structured read), syntopical (multi-source comparison)
- Each skill has a concrete, step-by-step protocol that Claude follows
- Skills can be invoked explicitly or recommended contextually (e.g., inspectional before analytical)
- Applicable to PDFs, web pages, codebases, docs, proposals, and research material

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1 (confirm skill scope and integration approach)
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **adler-inspectional**: Systematic skimming protocol — fast first-pass to understand structure, scope, and relevance before committing to a deep read
- **adler-analytical**: Deep-read protocol — classify, summarize, outline, define terms, extract arguments
- **adler-syntopical**: Multi-source comparison — find agreements/disagreements across documents, define issues, analyze the discussion

### Adler's Rules Translated to Claude

#### Inspectional Reading (Level 2)

Adler's rules for systematic skimming, translated:

| Adler's Rule | Physical Book | Claude Equivalent |
|---|---|---|
| Read the title and preface | Cover, foreword | Read filename, first paragraph, metadata, abstract |
| Study the table of contents | ToC page | Extract headings/sections (## in markdown, ToC in PDF, file tree in codebase) |
| Check the index | Back of book | Search for key terms, scan for named entities, check references |
| Read the publisher's blurb | Back cover | Read summary, README, introduction, "About" section |
| Look at pivotal chapters | Chapters that seem crucial | Read the introduction, conclusion, and any section with the highest heading density |
| Thumb through, reading a paragraph here and there | Random sampling | Sample 3-5 sections from different parts, reading a paragraph from each |
| Read the last 2-3 pages | Conclusion | Read the conclusion or final section — authors often summarize their argument there |

**Output**: A structured "inspectional report" — what the document is about, how it's organized, what kind of document it is, and whether it deserves a deep read.

#### Analytical Reading (Level 3)

Adler's four stages of analytical reading:

**Stage 1 — Classify and outline:**
1. Classify the work: What kind of document is this? (proposal, tutorial, reference, argument, narrative, specification)
2. State the unity in one sentence: What is this document about, in a single sentence?
3. Outline the major parts: What are the main sections and how do they relate?
4. Define the problem the author is trying to solve

**Stage 2 — Interpret:**
5. Find and define the author's key terms (not just jargon — the specific concepts that carry the argument)
6. Identify the leading propositions (the claims the author is making)
7. Construct the author's arguments (how do they support their claims?)
8. Determine which problems the author solved and which they didn't

**Stage 3 — Evaluate:**
9. Where is the author uninformed? (missing relevant facts)
10. Where is the author misinformed? (asserting what isn't so)
11. Where is the author illogical? (conclusions don't follow from premises)
12. Where is the author's analysis incomplete?

**Output**: A structured analytical report with classification, one-sentence summary, outline, key terms, propositions, arguments, and critical evaluation.

#### Syntopical Reading (Level 4)

Adler's five steps for comparing multiple sources:

1. **Find the relevant passages**: Across all sources, identify the sections relevant to the research question (not the author's question — yours)
2. **Bring authors to terms**: Create a shared vocabulary — different authors use different words for the same concept. Establish neutral terminology.
3. **Define the questions**: Formulate the questions that the different sources address, even if no single author formulates them that way
4. **Define the issues**: Where do authors disagree? Map the disagreements to specific questions
5. **Analyze the discussion**: Present the truth of the matter as fairly as possible, showing all sides with their strongest arguments

**Output**: A syntopical analysis — shared vocabulary table, question-by-question comparison, issue map, and balanced synthesis.

### Flow

**Document arrives** → [Inspectional read: what is this?] → [Decision: worth a deep read?] → [If yes: Analytical read] → [If comparing sources: Syntopical read]

### Technical Approach

Three skill files in `.claude/skills/`:

1. **`adler-inspectional/SKILL.md`** (~60 lines)
   - Step-by-step skimming protocol
   - Output template for inspectional report
   - Applicable to: PDFs, web pages, documents, codebases, proposals

2. **`adler-analytical/SKILL.md`** (~80 lines)
   - Three-stage deep-read protocol (classify/interpret/evaluate)
   - Output template for analytical report
   - Assumes inspectional read already done (or does one first)

3. **`adler-syntopical/SKILL.md`** (~70 lines)
   - Five-step multi-source comparison protocol
   - Output template for syntopical analysis
   - Requires 2+ sources; can build on prior analytical reads

### Integration Points

- **Research tasks**: When the agent is asked to research something, inspectional reading is the natural first step — skim sources to identify which deserve deep analysis
- **Plan-making**: The analytical reading protocol maps well to understanding issue descriptions, prior art, and competing proposals
- **Document review**: Analytical reading gives a structured framework for evaluating any document
- **Multi-source comparison**: Syntopical reading is ideal for "compare these approaches" or "review this literature" tasks

## Rabbit Holes

- **Automating reading level selection**: Don't try to build a router that automatically picks inspectional vs. analytical vs. syntopical. Let the user or agent choose. The skills are protocols, not classifiers.
- **Building document parsing infrastructure**: Don't write a PDF parser or web scraper. Use existing tools (Read tool for files, WebFetch for URLs). The skills are thinking protocols, not data pipelines.
- **Adler's rules for specific genres**: The book has chapters on reading science, literature, philosophy, etc. These are interesting but too specialized for v1. The general protocols cover 90% of use cases.
- **Exhaustive evaluation framework**: The analytical reading evaluation stage (uninformed/misinformed/illogical/incomplete) is powerful but could expand indefinitely. Keep it to 4 specific questions, not a comprehensive rubric.

## Risks

### Risk 1: Skills are too rigid
**Impact:** The step-by-step protocols feel mechanical and produce cookie-cutter outputs
**Mitigation:** Write the protocols as thinking guides, not fill-in-the-blank templates. Include judgment points: "If this is a short document, skip steps 3-4 and go straight to..." Allow the agent to adapt.

### Risk 2: Inspectional reading is redundant
**Impact:** Claude already skims — the skill might not add value over baseline behavior
**Mitigation:** The value is in *structured* skimming with a specific output format. Baseline Claude reads; inspectional Claude reports back: type, unity, structure, and recommendation. The structure is the value.

### Risk 3: Syntopical reading is rarely invoked
**Impact:** The most powerful skill goes unused because multi-source comparison tasks are uncommon
**Mitigation:** Add hints in research-related prompts ("when comparing multiple sources, consider adler-syntopical"). Also useful for: comparing competing libraries, reviewing multiple approaches in GitHub issues, multi-article research.

## No-Gos (Out of Scope)

- Document parsing infrastructure (PDF, web scraping)
- Automatic reading level selection
- Genre-specific reading rules (science, literature, philosophy)
- Integration into the bridge or SDK (purely skill files)
- Changes to existing agent prompts in v1 (hints can come in v2)

## Update System

No update system changes required — skill files propagate via git pull.

## Agent Integration

No agent integration required — standard `.claude/skills/` files loaded natively by Claude Code. The skills are invoked explicitly by name or when the agent determines the task matches.

## Documentation

- [ ] Create `docs/features/adler-reading-skills.md` describing the three skills
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Inline documentation in each skill file (the skills are self-documenting)

## Success Criteria

- [ ] `adler-inspectional` skill produces structured inspectional report from any document
- [ ] `adler-analytical` skill produces classification, one-sentence unity, outline, key terms, propositions, arguments, and evaluation
- [ ] `adler-syntopical` skill produces shared vocabulary, question comparison, issue map, and balanced synthesis from 2+ sources
- [ ] Each skill file is <=80 lines
- [ ] Skills are usable on PDFs, markdown docs, web pages, and codebases
- [ ] At least one before/after comparison showing improved document analysis quality
- [ ] Documentation created and indexed

## Team Orchestration

### Team Members

- **Builder (skills)**
  - Name: skills-builder
  - Role: Create the three Adler reading skill files
  - Agent Type: builder
  - Resume: true

- **Validator**
  - Name: skills-validator
  - Role: Test each skill against a real document, verify output quality
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Create inspectional reading skill
- **Task ID**: build-inspectional
- **Depends On**: none
- **Assigned To**: skills-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `.claude/skills/adler-inspectional/SKILL.md`
- Include: 7-step skimming protocol, output template, applicability notes
- Keep to ~60 lines
- Protocol should produce: document type, one-sentence summary, structure map, relevance assessment, deep-read recommendation

### 2. Create analytical reading skill
- **Task ID**: build-analytical
- **Depends On**: none
- **Assigned To**: skills-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `.claude/skills/adler-analytical/SKILL.md`
- Include: 3-stage protocol (classify/interpret/evaluate), output template
- Keep to ~80 lines
- Protocol should produce: classification, unity statement, outline, key terms, propositions, arguments, critical evaluation

### 3. Create syntopical reading skill
- **Task ID**: build-syntopical
- **Depends On**: none
- **Assigned To**: skills-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `.claude/skills/adler-syntopical/SKILL.md`
- Include: 5-step comparison protocol, output template
- Keep to ~70 lines
- Protocol should produce: shared vocabulary, question-by-question comparison, issue map, balanced synthesis

### 4. Validate skills
- **Task ID**: validate-skills
- **Depends On**: build-inspectional, build-analytical, build-syntopical
- **Assigned To**: skills-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify all three skill files exist and are within line limits
- Verify each follows a clear step-by-step protocol with output template
- Verify skills are applicable to multiple document types
- Run `ruff check . && black --check .`

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-skills
- **Assigned To**: documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/adler-reading-skills.md`
- Add entry to `docs/features/README.md` index

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: skills-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify all success criteria
- Confirm documentation indexed

## Validation Commands

- `test -f .claude/skills/adler-inspectional/SKILL.md` — Inspectional skill exists
- `test -f .claude/skills/adler-analytical/SKILL.md` — Analytical skill exists
- `test -f .claude/skills/adler-syntopical/SKILL.md` — Syntopical skill exists
- `wc -l .claude/skills/adler-*/SKILL.md` — All within line limits
- `test -f docs/features/adler-reading-skills.md` — Feature doc exists
- `grep -q adler docs/features/README.md` — Indexed

## Open Questions

1. **Should inspectional reading be a default first step?** When the agent is asked to analyze a document, should it automatically do an inspectional pass first? Or only when explicitly invoked?
2. **How should syntopical reading handle web sources?** The skill needs access to multiple documents simultaneously. For web research, should it use WebFetch to gather sources first, or expect them to be provided?
3. **Should these integrate with the Dennett skills?** Analytical reading's evaluation stage (uninformed/misinformed/illogical/incomplete) overlaps with `dennett-reasoning`. Worth cross-referencing or keeping independent?
