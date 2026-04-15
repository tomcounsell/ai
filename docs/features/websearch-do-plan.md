# WebSearch Research in Planning

Phase 0.7 in the `/do-plan` skill adds a WebSearch-powered external research step that gathers library documentation, ecosystem patterns, and known pitfalls before writing a plan.

## How It Works

1. **Query Generation**: The planner extracts key technical terms from the issue title, problem statement, and desired outcome to generate 1-3 targeted search queries
2. **WebSearch Execution**: Each query is run via the built-in Claude Code `WebSearch` tool (loaded via `ToolSearch` since it is a deferred tool)
3. **Filtering**: Results are evaluated for relevance to the planned work -- only directly applicable findings are retained
4. **Memory Persistence**: Valuable findings are saved as memories (`importance ~5.0, source=agent`) for future plan reuse
5. **Plan Integration**: Findings populate the `## Research` section in the plan document

## Skip Conditions

The research phase is skipped when:
- The work is purely internal (no external libraries, APIs, or ecosystem patterns)
- Small appetite with no external dependencies
- WebSearch returns no useful results (the section notes "No relevant external findings")

## Files Modified

- `.claude/skills/do-plan/SKILL.md` -- Phase 0.7 instructions between Freshness Check (0.5) and Phase 1
- `.claude/skills/do-plan/PLAN_TEMPLATE.md` -- `## Research` section after Prior Art, before Spike Results

## Configuration

The skill's `allowed-tools` frontmatter includes `ToolSearch, WebSearch` to enable the deferred tool pattern. No additional API keys are needed beyond what the Claude Code environment provides.

## Related

- [Enhanced Planning](enhanced-planning.md) -- Spike Resolution, RFC Review, and Infrastructure Documentation phases
- [Deep Plan Analysis](deep-plan-analysis.md) -- Prior Art, Data Flow, and Failure Analysis sections
- [Code Impact Finder](code-impact-finder.md) -- Semantic blast radius analysis during planning
