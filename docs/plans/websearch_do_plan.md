---
status: Ready
type: feature
appetite: Small
owner: Valor Engels
created: 2026-04-15
tracking: https://github.com/tomcounsell/ai/issues/971
last_comment_id:
revision_applied: true
---

# WebSearch Research Step for /do-plan

## Problem

The `/do-plan` skill writes plans from codebase context and memory alone. When planning features that involve external libraries, APIs, or ecosystem patterns, the planner has no way to pull in current documentation or community knowledge. This leads to plans that assume stale patterns or miss better alternatives that a quick web search would surface.

**Current behavior:**
Phase 1 of `/do-plan` jumps from recon validation directly to high-level analysis (blast radius, prior art, data flow) without any external research. The planner relies entirely on training data and codebase context.

**Desired outcome:**
A new research phase between Phase 0.5 (Freshness Check) and Phase 1 that uses WebSearch to gather relevant external context, saves valuable findings as memories, and feeds them into the plan document via a `## Research` section.

## Freshness Check

**Baseline commit:** `2aa04c408adad6ceea80070ff7408fce13329917`
**Issue filed at:** 2026-04-15T00:26:59Z
**Disposition:** Unchanged

**File:line references re-verified:**
- No specific file:line references in the issue body; the issue references directories (`.claude/skills/do-plan/`) which were confirmed via recon.

**Cited sibling issues/PRs re-checked:**
- #620 — Closed 2026-04-15T00:27:53Z (parent roadmap, decomposed into sub-issues including this one). Closure is expected; it was a meta-roadmap.

**Commits on main since issue was filed (touching referenced files):**
- `2aa04c40` "chore(plans): remove harness-session-continuity plan post-merge (#976)" — touches `docs/plans/`, irrelevant to this work
- No commits touching `.claude/skills/do-plan/` or `tools/web/` since the issue was filed.

**Active plans in `docs/plans/` overlapping this area:** None. No active plans modify `/do-plan` or `tools/web/`.

**Notes:** All claims from the issue still hold. Proceeding with planning.

## Prior Art

- **Issue #620**: Claude Code feature integration roadmap — identified WebSearch-augmented planning as a Phase 1 priority item. Now closed (decomposed into sub-issues).
- No prior PRs or closed issues attempted to add WebSearch to `/do-plan`.

## Research

This is the feature that adds this section to future plans — no research findings to include here. The implementation details are well-understood from the recon: `tools/web/search.py` provides the search API, `tools/memory_search` provides memory persistence, and the skill template + SKILL.md are the files to modify.

## Architectural Impact

- **New dependencies**: None. `tools/web/` and `tools/memory_search/` already exist and are production-ready.
- **Interface changes**: The `/do-plan` skill gains a new phase (Phase 0.7) and the plan template gains a new `## Research` section. The `allowed-tools` frontmatter in `SKILL.md` adds `ToolSearch, WebSearch`.
- **Coupling**: Minimal. The research phase is self-contained — if WebSearch fails or returns nothing, the plan proceeds without it. Memory saves are fire-and-forget.
- **Data ownership**: Research findings are owned by the plan document (as a section) and optionally persisted as memories.
- **Reversibility**: Trivially reversible — remove the phase from SKILL.md and the section from the template.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — `tools/web/` requires `PERPLEXITY_API_KEY` or `TAVILY_API_KEY` in `.env`, but these are already configured on all machines. The built-in Claude Code `WebSearch` tool requires no additional configuration.

## Solution

### Key Elements

- **Research Phase (Phase 0.7)**: A new phase in SKILL.md between Freshness Check (0.5) and Phase 1 that generates search queries from the issue context and runs WebSearch
- **Research Section in Template**: A new `## Research` section in PLAN_TEMPLATE.md placed after Prior Art
- **Memory Persistence**: Valuable findings saved as memories at importance ~5.0 for future plan reuse
- **Graceful Degradation**: If no useful results, the section states "No relevant external findings" and planning continues

### Flow

**Issue context** → Generate 1-3 search queries → WebSearch each query → Filter useful results → Save as memories → Write `## Research` section → Continue to Phase 1

### Technical Approach

- Use the built-in Claude Code `WebSearch` tool (deferred tool, available in the environment) rather than the Python `tools/web/` module. The built-in tool is simpler — it's a direct tool call, no subprocess or import needed. The Python module would require `Bash` calls to invoke `valor-search`, adding unnecessary indirection. Note: WebSearch is a deferred tool that requires `ToolSearch("select:WebSearch")` to load its schema before first use. The google-workspace skill uses the related `WebFetch` tool (for URL fetching, not searching) — WebSearch follows the same deferred-tool pattern.
- Add `ToolSearch, WebSearch` to the `allowed-tools` frontmatter in `SKILL.md`. WebSearch is a deferred tool — its schema must be loaded via `ToolSearch("select:WebSearch")` before it can be called. Both tools must be listed in `allowed-tools`.
- The research phase generates search queries by extracting key technical terms from the issue title, problem statement, and desired outcome. Queries should target: (a) library/API documentation, (b) ecosystem best practices, (c) known pitfalls.
- Memory saves use `python -m tools.memory_search save "finding" --importance 5.0 --source agent` via Bash. The `--source` flag only accepts `human`, `agent`, or `system` — agent-initiated saves during planning use `agent`.
- The `## Research` section in the template includes a skip-if annotation: "Skip if the work is purely internal (no external libraries, APIs, or patterns involved)."
- Query generation is LLM-native — the skill instructions tell the planner how to derive queries from context. No code is needed for query generation.

## Failure Path Test Strategy

### Exception Handling Coverage
- No exception handlers in scope — WebSearch failures are handled by the "skip gracefully" instruction in the skill text.

### Empty/Invalid Input Handling
- [ ] If WebSearch returns no results or empty content, the plan proceeds with "No relevant external findings" in the Research section
- [ ] If memory save fails (Redis down, bad input), it fails silently — memory saves are never blocking

### Error State Rendering
- Not applicable — no user-visible UI; the plan document is the output.

## Test Impact

No existing tests affected — this is a greenfield addition to skill markdown files (SKILL.md and PLAN_TEMPLATE.md). No Python code is being modified or created, so no existing test files are impacted.

## Rabbit Holes

- **Building a query generation engine in Python**: The LLM already knows how to generate good search queries from context. Writing Python code to extract keywords would be over-engineering. The skill instructions should describe the query strategy; the LLM executes it.
- **Caching search results**: Memories already serve as a cache. No need for a separate caching layer.
- **Multiple search rounds or iterative refinement**: One pass of 1-3 queries is sufficient for planning context. Iterative search is a different feature.
- **Using the Python `tools/web/` module instead of the built-in WebSearch**: The built-in tool is simpler and already available. The Python module would require subprocess calls.

## Risks

### Risk 1: WebSearch returns low-quality or irrelevant results
**Impact:** Research section adds noise rather than signal to the plan.
**Mitigation:** The skill instructions include filtering guidance — only include findings that are directly relevant to the technical approach. The skip-if clause handles cases where nothing useful is found.

### Risk 2: WebSearch rate limits or API failures during planning
**Impact:** Research phase blocks or delays plan creation.
**Mitigation:** The phase is explicitly optional — if WebSearch fails, planning continues without it. The skill text includes "skip gracefully" instructions.

## Race Conditions

No race conditions identified — the research phase is synchronous within the planning flow, runs before any other phase reads its output, and memory saves are fire-and-forget.

## No-Gos (Out of Scope)

- Automated query generation code (Python/scripts) — the LLM generates queries from context
- Search result caching beyond memory saves
- Iterative/multi-round research
- Modifying the Python `tools/web/` module
- Adding WebSearch to other skills (this plan is scoped to `/do-plan` only)

## Update System

No update system changes required — this feature modifies only skill markdown files (`.claude/skills/do-plan/SKILL.md` and `.claude/skills/do-plan/PLAN_TEMPLATE.md`). These are synced automatically by `git pull` during updates.

## Agent Integration

No agent integration required — this modifies the `/do-plan` skill's instructions and template, which are read directly by Claude Code when the skill is invoked. No MCP server changes, no `.mcp.json` changes, no bridge changes needed.

## Documentation

- [ ] Update `docs/features/README.md` index table with a note about WebSearch in /do-plan
- [ ] Add inline documentation in SKILL.md describing the research phase behavior and skip conditions

## Success Criteria

- [ ] `/do-plan` SKILL.md contains a Phase 0.7 research step with WebSearch instructions (including ToolSearch load step)
- [ ] `allowed-tools` in SKILL.md frontmatter includes `ToolSearch, WebSearch`
- [ ] PLAN_TEMPLATE.md contains a `## Research` section with skip-if guidance
- [ ] Research phase instructions include memory save commands (`python -m tools.memory_search save ... --source agent`)
- [ ] Research phase includes graceful skip when results are empty or irrelevant
- [ ] Existing plan structure and required sections are preserved
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (skill-updater)**
  - Name: skill-updater
  - Role: Modify SKILL.md and PLAN_TEMPLATE.md
  - Agent Type: builder
  - Resume: true

- **Validator (skill-validator)**
  - Name: skill-validator
  - Role: Verify skill changes preserve structure and add research correctly
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add Research Phase to SKILL.md
- **Task ID**: build-skill
- **Depends On**: none
- **Validates**: manual review — skill files are markdown, not testable via pytest
- **Assigned To**: skill-updater
- **Agent Type**: builder
- **Parallel**: true
- Add `ToolSearch, WebSearch` to the `allowed-tools` frontmatter in `.claude/skills/do-plan/SKILL.md`
- Insert Phase 0.7 (Research) between Phase 0.5 (Freshness Check) and Phase 1 (Flesh Out)
- Phase 0.7 instructions should cover: (a) loading WebSearch via `ToolSearch("select:WebSearch")`, (b) generating 1-3 search queries from issue context, (c) calling WebSearch for each query, (d) filtering results for relevance, (e) saving valuable findings as memories via `python -m tools.memory_search save "finding" --importance 5.0 --source agent`, (f) skip-if guidance for purely internal work
- Ensure the phase references the `## Research` section in the template

### 2. Add Research Section to PLAN_TEMPLATE.md
- **Task ID**: build-template
- **Depends On**: none
- **Validates**: manual review
- **Assigned To**: skill-updater
- **Agent Type**: builder
- **Parallel**: true
- Add `## Research` section to PLAN_TEMPLATE.md after `## Prior Art` and before `## Spike Results`
- Include skip-if annotation: "Skip if purely internal work with no external libraries, APIs, or ecosystem patterns"
- Include placeholder structure: query used, key findings with source URLs, relevance assessment
- Include a note that findings are saved as memories for future reference

### 3. Validate Changes
- **Task ID**: validate-skill
- **Depends On**: build-skill, build-template
- **Assigned To**: skill-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify SKILL.md `allowed-tools` includes `WebSearch`
- Verify Phase 0.7 appears in correct position (after 0.5, before 1)
- Verify PLAN_TEMPLATE.md `## Research` appears after `## Prior Art` and before `## Spike Results`
- Verify all existing required sections in PLAN_TEMPLATE.md are preserved
- Verify no Python code was created (this is a markdown-only change)

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-skill
- **Assigned To**: skill-updater
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/README.md` if it has a planning-related entry
- Ensure inline docs in SKILL.md are clear about skip conditions

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: skill-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite to verify no regressions
- Verify all success criteria met
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| WebSearch in allowed-tools | `grep -c 'ToolSearch, WebSearch' .claude/skills/do-plan/SKILL.md` | output > 0 |
| Research section in template | `grep -c '## Research' .claude/skills/do-plan/PLAN_TEMPLATE.md` | output > 0 |
| Phase 0.7 in skill | `grep -c 'Phase 0.7' .claude/skills/do-plan/SKILL.md` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Skeptic | `--source research` is not a valid choice for `python -m tools.memory_search save`; CLI only accepts `human`, `agent`, or `system` (cli.py:330) | Task 1 must use `--source agent` instead | The argparse choices are `["human", "agent", "system"]` at cli.py:330. Using `--source research` will raise a SystemExit with argparse validation error. Use `--source agent` since the save is agent-initiated. |
| CONCERN | Skeptic | Plan assumes adding `WebSearch` to `allowed-tools` is sufficient, but `WebSearch` is a deferred tool requiring `ToolSearch` to load its schema before invocation | Task 1 skill instructions must include a ToolSearch step before WebSearch calls | Deferred tools appear by name only until fetched via `ToolSearch`. Without `ToolSearch("select:WebSearch")` first, calling WebSearch will fail with `InputValidationError`. The Phase 0.7 instructions must tell the planner to call ToolSearch before WebSearch. Also add `ToolSearch` to `allowed-tools`. |
| CONCERN | Archaeologist | Issue recon claims WebSearch is "already used by google-workspace skill" but google-workspace actually uses `WebFetch` (a different tool). Plan should verify WebSearch tool availability independently. | Builder should verify WebSearch works via ToolSearch during implementation | `google-workspace/SKILL.md` line 4 shows `allowed-tools: Read, Write, Edit, Bash, WebFetch`. The recon confused WebFetch with WebSearch. The plan's approach is still valid since WebSearch does exist as a deferred tool, but this should be verified. |

---
