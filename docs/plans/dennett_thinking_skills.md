---
status: Planning
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-02-15
tracking: https://github.com/tomcounsell/ai/issues/112
---

# Integrate Dennett Thinking Skills into Agent System

## Problem

We have 77 thinking tools from Dennett's "Intuition Pumps," mapped to 8 proposed Claude skills across a 4-phase build plan. Draft skill files exist on PR #111. The question isn't whether these tools are valuable — it's how to integrate them without:

- **Context bloat**: 804 lines of skill definitions compete with code context in the agent's attention window. Every line of thinking-tool guidance is a line of codebase context displaced.
- **Tool confusion**: 8 standalone skills create routing complexity. When does the agent invoke `dennett-decomposition` vs just doing good architecture review?
- **Always-on overhead**: Skills marked "always-on" (reasoning, steelman) would load into every session. Is the cost worth it?
- **Measurement gap**: No way to know if the skills actually improve output quality.

**Current behavior:**
- Valor has no structured thinking methodology beyond what's in SOUL.md and CLAUDE.md
- Code reviews, plans, and analysis rely on Claude's baseline reasoning
- No systematic approach to steelmanning, decomposition, or detecting rhetorical tricks

**Desired outcome:**
- The most impactful Dennett tools are woven into Valor's reasoning at the right integration points
- Context budget impact is minimized (tens of lines, not hundreds)
- Tools are activated contextually, not loaded universally
- Quality improvement is measurable through before/after comparison

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1-2 (alignment on which tools matter most, integration strategy)
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **Tiered integration**: Not all 8 skills get the same treatment. Three tiers based on leverage and context cost.
- **Prompt injection over standalone skills**: High-value tools get woven into existing prompts rather than creating new skill files.
- **Contextual activation**: On-demand skills are loaded only when the task type matches, not in every session.
- **Quality measurement**: Before/after comparison on real tasks.

### Integration Strategy

#### Tier 1: Embed in Existing Prompts (~30 lines total)

The highest-leverage, lowest-cost tools get distilled into existing agent prompts. Not standalone skills — surgical additions to prompts that already load.

| Tool | Where to Embed | Why |
|---|---|---|
| Rapoport's Rules (steelman) | `code-reviewer.md` | Every code review should steelman before critiquing |
| Occam's Broom + "Surely" operator | `code-reviewer.md`, `validator.md` | Detect hidden assumptions in code and claims |
| Cranes vs Skyhooks | `builder.md` | Builder should demand mechanistic solutions, not hand-wave |
| Chmess check | `make-plan/SKILL.md` | Plans should pass "does this matter?" before deep investment |
| Sphexishness | `builder.md` | Builder should notice when it's repeating a routine that doesn't fit |

**Cost:** ~5-6 lines per prompt × 4-5 prompts = ~30 lines. Negligible context impact.

**Format:** A concise "Thinking Discipline" section in each prompt:
```markdown
## Thinking Discipline
- **Steelman first**: Before critiquing, restate the approach so the author would agree you captured it. Then critique.
- **Check for smuggled assumptions**: Flag "obviously," "surely," "clearly" — each hides an undefended claim.
- **Demand cranes, not skyhooks**: If an explanation invokes magic ("the AI handles it"), demand the mechanism.
```

#### Tier 2: Condensed On-Demand Skills (~50 lines each)

Skills that are valuable but situational get condensed to ~50 lines each (down from 80-115) and stored as skill files invoked on-demand:

| Skill | When Invoked | Trigger |
|---|---|---|
| `dennett-decomposition` | Architecture reviews, system design | User asks for decomposition or "how does X work" |
| `dennett-clarity` | Documentation writing, explanations | User asks for docs, explanations for non-technical audience |
| `dennett-creativity` | Brainstorming, problem-solving when stuck | User asks for creative solutions or is explicitly stuck |

These 3 skills are the most distinct from baseline Claude behavior. The others either overlap with existing capability or have narrow use cases.

**Cost:** ~150 lines across 3 skill files. Only loaded when relevant.

#### Tier 3: Reference Material (Not Skills)

The remaining tools are reference material, not skills. They inform the system but don't need to be in the agent's active context:

| Original Skill | Disposition | Rationale |
|---|---|---|
| `dennett-reasoning` | Tier 1 extraction — best tools embedded in existing prompts | Too broad as standalone; the 3-4 best tools are more effective when embedded |
| `dennett-steelman` | Tier 1 extraction — Rapoport's Rules embedded in code-reviewer | More effective as a code review behavior than a standalone skill |
| `dennett-stances` | Reference doc only | Claude already navigates abstraction levels well; a reference doc for edge cases suffices |
| `dennett-agency` | Reference doc only | Narrow use case (coaching/delegation); rarely needed in dev work |
| `dennett-meta` | Tier 1 extraction — Chmess check embedded in make-plan | The one useful tool (Chmess) is better as a plan-maker gate |

Reference material goes into `docs/reference/dennett-thinking-tools.md` — available if an agent needs it, but never auto-loaded.

### Flow

**Session starts** → [Existing prompts include Tier 1 tools natively] → [Task arrives] → [If architecture/docs/brainstorming task → load relevant Tier 2 skill] → [Agent uses embedded + loaded tools naturally]

### Technical Approach

1. **Distill Tier 1 additions**: Extract the 5-6 most impactful tools into 5-6 line "Thinking Discipline" sections for code-reviewer, validator, builder, and make-plan prompts.

2. **Condense Tier 2 skills**: Take the 3 on-demand skill files from PR #111 (decomposition, clarity, creativity), cut each to ~50 lines by removing examples and keeping only the method and anti-patterns.

3. **Create reference doc**: Consolidate the full 77-tool catalog into `docs/reference/dennett-thinking-tools.md` for human reference and occasional agent lookup.

4. **Remove unused skill files**: Delete the 5 skill directories that were absorbed into Tier 1 or demoted to reference.

5. **Update PR #111**: Revise the branch to reflect the tiered strategy instead of 8 standalone skills.

## Rabbit Holes

- **Trying to use all 77 tools**: Most are philosophical curiosities. Focus on the ~10 that actually improve engineering work.
- **Building measurement infrastructure**: Don't build a scoring system for thinking quality. Just do before/after comparison on a few real tasks and use judgment.
- **Skill routing logic**: Don't build automatic skill selection. Let the agent (or user) invoke on-demand skills explicitly. Routing intelligence is a separate problem.
- **A/B testing framework**: Interesting but overkill. Manual comparison of output quality is sufficient for v1.

## Risks

### Risk 1: Embedded tools get ignored
**Impact:** Agent reads the "Thinking Discipline" section but doesn't change behavior
**Mitigation:** Use direct, imperative language. Position the section near the action point (e.g., before the review checklist in code-reviewer). Rationalization tables from #102 reinforce discipline.

### Risk 2: On-demand skills never get invoked
**Impact:** Tier 2 skills rot unused because nobody remembers they exist
**Mitigation:** Document when to invoke them in CLAUDE.md. Consider adding hints in the make-plan and build skills ("For architecture work, consider invoking dennett-decomposition").

### Risk 3: Context budget still too high
**Impact:** Even 30 lines of Tier 1 additions + occasional Tier 2 loading degrades code context
**Mitigation:** Monitor. If agents start losing track of code context, trim Tier 1 to the top 3 tools only. The tiered approach makes this easy — just remove lines.

## No-Gos (Out of Scope)

- Automatic skill routing based on task type
- Quality measurement infrastructure (A/B testing, scoring)
- Integrating all 77 tools (most are reference-only)
- Changes to the bridge or SDK for skill loading
- New MCP servers

## Update System

No update system changes required — skill files and prompt changes propagate via git pull.

## Agent Integration

No new MCP integration needed. Tier 1 changes are inline in existing agent prompts. Tier 2 skills are standard `.claude/skills/` files loaded natively by Claude Code.

## Documentation

- [ ] Create `docs/reference/dennett-thinking-tools.md` — full 77-tool reference catalog
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Update `CLAUDE.md` with brief mention of Dennett thinking tools availability
- [ ] Inline documentation in each modified prompt file

## Success Criteria

- [ ] 5-6 highest-leverage Dennett tools embedded in existing agent prompts (code-reviewer, validator, builder, make-plan)
- [ ] 3 condensed on-demand skills: decomposition, clarity, creativity (~50 lines each)
- [ ] Full 77-tool reference doc in `docs/reference/`
- [ ] Unused standalone skill directories removed
- [ ] PR #111 updated to reflect tiered strategy
- [ ] Total context budget impact: <30 lines always-on, <150 lines on-demand
- [ ] At least one before/after comparison showing improved output quality
- [ ] All existing tests pass

## Team Orchestration

### Team Members

- **Builder (prompts)**
  - Name: prompt-integrator
  - Role: Embed Tier 1 tools in existing prompts, condense Tier 2 skills, create reference doc
  - Agent Type: builder
  - Resume: true

- **Validator**
  - Name: integration-validator
  - Role: Verify tools are correctly embedded, context budget within limits, no existing test breakage
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Embed Tier 1 tools in existing prompts
- **Task ID**: build-tier1
- **Depends On**: none
- **Assigned To**: prompt-integrator
- **Agent Type**: builder
- **Parallel**: true
- Add "Thinking Discipline" section to `.claude/agents/code-reviewer.md`:
  - Rapoport's Rules (steelman before critiquing)
  - Occam's Broom (what evidence is being swept aside?)
  - "Surely" operator (flag smuggled assumptions)
- Add "Thinking Discipline" section to `.claude/agents/builder.md`:
  - Cranes not skyhooks (demand mechanism, not magic)
  - Sphexishness check (am I repeating a routine that doesn't fit?)
- Add "Thinking Discipline" section to `.claude/agents/validator.md`:
  - Occam's Broom (what's being ignored in this validation?)
- Add Chmess check to `.claude/skills/make-plan/SKILL.md`:
  - "Before deep investment, ask: who cares about this? What decision does it change?"
- Keep each addition to 5-6 lines maximum

### 2. Condense Tier 2 on-demand skills
- **Task ID**: build-tier2
- **Depends On**: none
- **Assigned To**: prompt-integrator
- **Agent Type**: builder
- **Parallel**: true
- Condense `dennett-decomposition` to ~50 lines (keep: method, protocol, anti-patterns; cut: extended examples)
- Condense `dennett-clarity` to ~50 lines (keep: lay audience method, deepity detection; cut: detailed examples)
- Condense `dennett-creativity` to ~50 lines (keep: jootsing method, knob-turning; cut: extended scenarios)
- Remove the other 5 skill directories (reasoning, steelman, stances, agency, meta)

### 3. Create reference doc
- **Task ID**: build-reference
- **Depends On**: none
- **Assigned To**: prompt-integrator
- **Agent Type**: builder
- **Parallel**: true
- Create `docs/reference/dennett-thinking-tools.md` from the spreadsheet data
- Include all 77 tools with number, name, core concept, and skill potential
- Include the 8 proposed skills table for context
- This is a reference document, not an active prompt

### 4. Validate integration
- **Task ID**: validate-integration
- **Depends On**: build-tier1, build-tier2, build-reference
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify each modified prompt has a "Thinking Discipline" section of <=6 lines
- Verify 3 condensed skills exist and are each <=60 lines
- Verify 5 unused skill directories are removed
- Verify reference doc exists with all 77 tools
- Count total always-on context addition (<30 lines)
- Run `ruff check . && black --check .`
- Run `pytest tests/ -v`

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-integration
- **Assigned To**: documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Add entry to `docs/features/README.md`
- Update CLAUDE.md with brief mention of available Dennett tools
- Ensure inline docs in modified prompt files

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify all success criteria met
- Run full test suite
- Confirm documentation indexed

## Validation Commands

- `wc -l .claude/agents/code-reviewer.md .claude/agents/builder.md .claude/agents/validator.md` — Check prompt sizes haven't bloated
- `wc -l .claude/skills/dennett-decomposition/SKILL.md .claude/skills/dennett-clarity/SKILL.md .claude/skills/dennett-creativity/SKILL.md` — Tier 2 skills <=60 lines each
- `test -f docs/reference/dennett-thinking-tools.md` — Reference doc exists
- `test ! -d .claude/skills/dennett-reasoning` — Unused skills removed
- `pytest tests/ -v` — Tests pass
- `ruff check .` — Linting
- `black --check .` — Formatting

## Open Questions

1. **Which 5-6 tools make the Tier 1 cut?** The plan proposes Rapoport's Rules, Occam's Broom, "surely" operator, cranes vs skyhooks, Chmess check, and sphexishness. Are these the right ones, or should others be swapped in?
2. **Should Tier 2 skills have hints in other prompts?** E.g., should the build skill mention "for architecture work, consider loading dennett-decomposition"? This increases context but improves discoverability.
3. **What happens to PR #111?** Options: (a) close it and create a new PR from this plan, (b) update it in-place to reflect the tiered strategy, (c) merge as-is and then modify. Plan currently assumes (b).
