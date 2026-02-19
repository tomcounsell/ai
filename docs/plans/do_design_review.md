---
status: Ready
type: feature
appetite: Small
owner: Valor Engels
created: 2026-02-19
tracking: https://github.com/tomcounsell/ai/issues/143
---

# `/do-design-review` Skill

## Problem

There's a build-time frontend design skill (`/frontend-design`) that helps generate distinctive, production-grade interfaces. But there's no review-time counterpart — no way to evaluate an existing page against premium design criteria and get actionable feedback.

**Current behavior:**
After building UI with `/frontend-design`, there's no automated way to evaluate the result. Review is manual and ad-hoc.

**Desired outcome:**
A `/do-design-review` skill that accepts a starting URL and a test goal, then actively navigates the interface using real interactions (clicks, form fills, etc.) to evaluate the design against premium criteria — producing a severity-rated report with specific, actionable recommendations. The agent handles authentication if it encounters a login wall, then continues to the specified starting point.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 0

Writing a single SKILL.md and an optional reference doc is fast. No backend, no MCP, no bridge changes.

## Prerequisites

No prerequisites — this work has no external dependencies. `agent-browser` is already installed. Claude vision is available via the model itself.

## Solution

### Key Elements

- **SKILL.md**: Prompt that defines the evaluation rubric, workflow, and output format — the entire skill lives here
- **Two required inputs**: A starting URL (where to begin, assuming the user is logged in) and a test goal (what to evaluate, as specific as needed)
- **Active navigation**: Agent uses real interactions — clicks, form fills, navigation — to traverse the UI toward the test goal, not just passive screenshotting
- **Auth handling**: If the agent encounters a login wall en route, it figures out credentials and authenticates before continuing
- **Vision-based evaluation**: Claude screenshots and analyzes at each meaningful state, evaluating against the rubric as it goes
- **Structured output**: Table of categories with ratings, plus Top 3 prioritized recommendations

### Flow

```
User: /do-design-review http://localhost:8000/dashboard "evaluate the project creation flow end-to-end"

→ Agent opens starting URL
→ If login wall encountered → agent authenticates, returns to starting URL
→ Agent takes initial screenshot, begins evaluation
→ Agent navigates using actions/buttons toward the test goal
→ Screenshot + evaluate at each meaningful interaction point
→ Complete the test goal or exhaust navigable paths
→ Produce structured report: severity table + Top 3 improvements
```

The starting URL is where evaluation begins (assumed accessible). The test goal defines what the agent is trying to accomplish — it can be narrow ("evaluate the settings page layout") or broad ("walk through signup and first-run experience").

### Technical Approach

- Skill lives entirely in `.claude/skills/do-design-review/SKILL.md`
- Optional: `reference/criteria.md` with the full evaluation rubric (keeps SKILL.md lean)
- Uses `agent-browser open <url>` → `agent-browser snapshot -i` → `agent-browser click` / `fill` for active navigation
- Screenshots at each meaningful state with `agent-browser screenshot`
- Desktop viewport only (v1)
- Evaluation rubric mirrors `/frontend-design` dimensions, rephrased as diagnostic questions
- Output format: markdown table + prose recommendations

### Evaluation Dimensions

Ten premium design dimensions (from issue 143 and the frontend-design reference docs):

1. **Visual hierarchy** — Clear focal points, intentional whitespace, scannable layout
2. **Typography** — Font pairing quality, size/weight contrast, readability
3. **Color & contrast** — Cohesive palette, sufficient contrast, intentional accent use
4. **Spacing & alignment** — Consistent spacing system, grid alignment, breathing room
5. **Visual details** — Imagery quality, aspect ratios, decorative elements
6. **Micro-interactions** — Hover states, transitions, feedback cues (inferred from structure)
7. **Consistency** — Repeated patterns, component reuse, visual rhythm
8. **Trust signals** — Professional feel, polish, attention to detail
9. **Interaction quality** — Do actions feel responsive and intentional? Are affordances clear?
10. **AI Slop Check** — Does it look templated/generic? Would someone ask "which AI made this?"

### Rating Scale

- ✅ **Premium** — Exceptional, no action needed
- ⚠️ **Acceptable** — Passes bar, minor improvements possible
- ❌ **Needs work** — Clear issue, specific fix recommended

## Rabbit Holes

- **Lighthouse integration** — Performance scores are useful but different from visual design quality; keep them separate
- **Before/after comparison** — Useful but complex; can be a v2 enhancement
- **Reference site comparison** — Comparing against inspiration sites is a great idea but adds scope; defer
- **Annotation overlays** — Drawing on screenshots to mark issues is impressive but overkill for a skill; prose descriptions are sufficient
- **Mobile viewport** — Deferred to v2; desktop-only is sufficient for v1
- **Multi-path crawling** — The agent follows one goal-driven path, not a full sitemap crawl

## Risks

### Risk 1: Auth credentials unknown
**Impact:** Agent hits login wall and can't proceed to starting URL
**Mitigation:** Skill instructs agent to look for credentials in `.env`, ask the user if not found, or attempt common dev defaults (admin/admin, etc.) on local instances

### Risk 2: Vision evaluation quality varies
**Impact:** Shallow or generic feedback that doesn't catch real issues
**Mitigation:** Write the rubric as specific diagnostic questions (e.g., "Does the hero have one clear focal point, or is attention split across multiple elements?") rather than vague criteria

## No-Gos (Out of Scope)

- Lighthouse / performance scores
- Before/after comparison runs
- Screenshot annotation / overlays
- Sitemap crawling — one goal-driven path per invocation
- Mobile viewport (v1 — desktop only)
- Saving reports to disk (output to chat is sufficient)
- Integration into `/do-pr-review` (could be added later as opt-in)

## Update System

No update system changes required — this is a new skill file, no dependencies to propagate across machines. The `.claude/skills/` directory syncs via git on `/update`.

## Agent Integration

No agent integration required — this is a user-invocable skill, not a tool exposed via MCP. The agent can invoke it via the Skill tool like any other skill. No changes to `.mcp.json`, `mcp_servers/`, or `bridge/telegram_bridge.py`.

## Documentation

- [ ] Create `docs/features/do-design-review.md` describing the skill, usage, and output format
- [ ] Add entry to `docs/features/README.md` index table

## Success Criteria

- [ ] Skill accepts two inputs: starting URL and test goal description
- [ ] Agent actively navigates using real interactions (clicks, form fills) to pursue the test goal
- [ ] Agent handles auth if it encounters a login wall before reaching the starting URL
- [ ] Screenshots are captured at each meaningful state during navigation
- [ ] Report covers all 10 evaluation dimensions with severity ratings
- [ ] Report includes specific, actionable Top 3 recommendations tied to observed UI states
- [ ] Works with both local (`localhost`) and deployed URLs
- [ ] Skill description in `SKILL.md` frontmatter correctly triggers from Claude Code session

## Team Orchestration

### Team Members

- **Builder (skill-writer)**
  - Name: skill-writer
  - Role: Write `.claude/skills/do-design-review/SKILL.md` with evaluation rubric, workflow, and output format
  - Agent Type: builder
  - Resume: true

- **Validator (skill-validator)**
  - Name: skill-validator
  - Role: Verify the skill file is complete, rubric covers all 10 dimensions, and output format matches spec
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Create `docs/features/do-design-review.md` and update the README index
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Write the skill
- **Task ID**: build-skill
- **Depends On**: none
- **Assigned To**: skill-writer
- **Agent Type**: builder
- **Parallel**: true
- Create `.claude/skills/do-design-review/SKILL.md` with YAML frontmatter, evaluation rubric (10 dimensions), active navigation workflow using agent-browser (open → snapshot → interact → screenshot → evaluate loop), auth handling instructions, and structured output format
- Optionally create `reference/criteria.md` if rubric is too long to inline

### 2. Validate the skill
- **Task ID**: validate-skill
- **Depends On**: build-skill
- **Assigned To**: skill-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify SKILL.md has correct frontmatter fields (name, description, allowed-tools)
- Verify all 10 evaluation dimensions are present
- Verify output format includes severity table and Top 3 recommendations
- Verify agent-browser usage is consistent with installed skill

### 3. Write documentation
- **Task ID**: document-feature
- **Depends On**: validate-skill
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/do-design-review.md`
- Add entry to `docs/features/README.md` index table

### 4. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: skill-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify all success criteria are met
- Confirm docs were created
- Generate final pass/fail report

## Validation Commands

- `ls .claude/skills/do-design-review/SKILL.md` — skill file exists
- `grep -c 'Premium\|Acceptable\|Needs work' .claude/skills/do-design-review/SKILL.md` — rating scale present
- `ls docs/features/do-design-review.md` — feature doc exists
- `grep 'do-design-review' docs/features/README.md` — index entry present

