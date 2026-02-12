# Podcast Skill Workflow: Sub-Agent Optimization

**Issue**: [yudame/cuttlefish#32](https://github.com/yudame/cuttlefish/issues/32)
**Status**: Planning
**Migrated from**: yudame/research

---

## Core Problem

The orchestrator accumulates approximately 440KB of content (~110K tokens) across 12 workflow phases, causing context overflow and degraded quality. The bottlenecks occur when the orchestrator directly reads raw research files during question discovery, cross-validation, and briefing creation.

## Proposed Solution

Restructure the workflow so the orchestrator becomes a dispatcher rather than a content processor. Key changes:

### Research Digests (NEW)
After each p2-*.md file is collected, an Opus agent generates a ~3-5KB structured digest saved as `p2-*-digest.md` containing:
- Table of contents
- Key findings
- Sources
- Searchable topics

### Sub-Agent Delegation

Replace five phases with dedicated Opus agents:

| Phase | Current | Proposed |
|-------|---------|----------|
| **Phase 3** (Question Discovery) | Orchestrator reads all files | Delegates to specialized agent returning gap analysis summaries |
| **Phase 5** (Cross-Validation) | Orchestrator reads all files | Returns verification matrix without orchestrator reading all files |
| **Phase 6** (Master Briefing) | Orchestrator writes p3-briefing.md | Agent writes p3-briefing.md directly; orchestrator receives only summary |
| **Phase 8** (Episode Planning) | Orchestrator creates plan | Delegates to agent with full methodology access |
| **Phase 11** (Metadata) | Orchestrator writes metadata | Delegates to metadata-writing agent |

### Token Savings

**Estimated reduction**: ~596KB accumulated orchestrator context → ~16KB

That's approximately **97% reduction** in orchestrator context.

## Technical Implementation

### New Agent Definitions

Create nine new agent definitions in `.claude/agents/`:

```
.claude/agents/
├── podcast-question-discovery.md     # Phase 3
├── podcast-cross-validator.md        # Phase 5
├── podcast-briefing-writer.md        # Phase 6
├── podcast-episode-planner.md        # Phase 8
├── podcast-metadata-writer.md        # Phase 11
├── podcast-research-digest.md        # Post-p2 digest generation
├── podcast-research-query.md         # Haiku agent for answering questions about research
└── ...
```

### Workflow Rewrite

Comprehensive rewrite of `new-podcast-episode.md` to:
1. Use `Task` tool to spawn sub-agents instead of direct file reads
2. Pass only summaries/digests between phases
3. Maintain phase coordination without context accumulation

### Sub-Agent Pattern

Each sub-agent:
- Uses Opus model for quality
- Receives focused context (only what it needs)
- Returns structured summary to orchestrator
- Writes detailed output to files directly

### Research Query Agent (Haiku)

For ad-hoc questions about research files:
- Spawns multiple instances in parallel (one per p2-*.md file)
- Returns targeted answers without loading full files into orchestrator
- Used when orchestrator needs specific facts, not full content

Example usage:
```
User: Does the research mention any statistics about X?
Orchestrator: [Spawns 4 Haiku agents, one per p2 file]
Agents return: "Yes, p2-perplexity mentions 47% stat on line 234" / "No mention" / etc.
```

## Success Criteria

The orchestrator must **never** directly read:
- `p2-*.md` files (raw research results)
- `p3-briefing.md` (master briefing)
- `report.md` (final report)
- `content_plan.md` (episode plan)

Validation delegated to Opus validators instead.

**Context accumulation stays under ~30KB** throughout all phases.

## Implementation Phases

### Phase 1: Research Digest Agent
- Create `podcast-research-digest.md` agent
- Modify Phase 2 to generate digests after each p2 file
- Test digest quality and size

### Phase 2: Question Discovery Delegation
- Create `podcast-question-discovery.md` agent
- Modify Phase 3 to delegate instead of direct read
- Verify gap analysis quality

### Phase 3: Cross-Validation Delegation
- Create `podcast-cross-validator.md` agent
- Modify Phase 5 to receive verification matrix only
- Test cross-reference accuracy

### Phase 4: Briefing Writer Delegation
- Create `podcast-briefing-writer.md` agent
- Modify Phase 6 to receive summary only
- Verify briefing quality matches previous approach

### Phase 5: Episode Planning Delegation
- Create `podcast-episode-planner.md` agent
- Modify Phase 8 for delegation
- Test plan coherence

### Phase 6: Metadata Writer Delegation
- Create `podcast-metadata-writer.md` agent
- Modify Phase 11 for delegation
- Verify metadata accuracy

### Phase 7: Research Query Agent
- Create `podcast-research-query.md` (Haiku)
- Add utility for parallel spawning
- Test query accuracy and speed

### Phase 8: Integration Testing
- Run full episode workflow with new architecture
- Measure actual token usage
- Compare output quality to baseline

## Notes

- The `podcast-synthesis-writer` (Phase 7) intentionally retains full research file access since it runs in isolated context
- All sub-agents use Opus unless explicitly noted (Haiku for lightweight queries)
- Digests serve as searchable index, not replacement for full content when depth is needed
