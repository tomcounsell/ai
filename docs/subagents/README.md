# Subagent Product Requirements Documents

## Overview

This directory contains Product Requirements Documents (PRDs) for specialized AI subagents that handle different MCP (Model Context Protocol) domains. Each subagent prevents context pollution by lazy-loading only when needed.

## The Problem: Context Pollution

Loading all MCP tools (Stripe, Sentry, Render, GitHub, Notion, Linear, etc.) into the main agent would consume 60k+ tokens of context with tool schemas, leaving minimal space for actual conversation and degrading performance.

## The Solution: Specialized Subagents

Each subagent:
- **Activates on-demand** via intelligent routing
- **Maintains focused context** with only domain-specific tools
- **Has specialized expertise** through domain-focused prompts
- **Operates independently** without polluting main agent context
- **Caches for reuse** after first activation

## Architecture

```
User Query
    ↓
Main Agent (clean context, only core tools)
    ↓
SubagentRouter (intelligent domain detection)
    ↓
Specialized Subagent (lazy-loaded, domain-specific tools)
    ↓
Result → Main Agent → User
```

## Available Subagents

### 1. Stripe Subagent
**Domain**: Payment Processing & Financial Operations
**Priority**: HIGH
**Tools**: 18+ Stripe MCP tools
**Context**: <15k tokens

**Capabilities**:
- Payment processing and analytics
- Subscription management
- Customer billing
- Refund processing
- Revenue reporting

**Detection Keywords**: payment, stripe, revenue, subscription, invoice, refund, billing

📄 [Full PRD](./stripe-subagent-prd.md)

---

### 2. Sentry Subagent
**Domain**: Error Monitoring & Performance Analysis
**Priority**: HIGH
**Tools**: 14+ Sentry MCP tools
**Context**: <20k tokens

**Capabilities**:
- Error investigation and analysis
- Performance monitoring
- Alert triage
- Stack trace interpretation
- User impact assessment

**Detection Keywords**: error, bug, sentry, crash, exception, performance, monitoring, alert

📄 [Full PRD](./sentry-subagent-prd.md)

---

### 3. Render Subagent
**Domain**: Infrastructure & Deployment Operations
**Priority**: HIGH
**Tools**: 16+ Render MCP tools
**Context**: <25k tokens

**Capabilities**:
- Service deployment
- Infrastructure monitoring
- Log analysis
- Scaling operations
- Environment configuration

**Detection Keywords**: deploy, render, infrastructure, service, logs, scale, environment

📄 [Full PRD](./render-subagent-prd.md)

---

### 4. GitHub Subagent
**Domain**: Code Repository & Collaboration
**Priority**: CRITICAL
**Tools**: 30+ GitHub MCP tools
**Context**: <30k tokens

**Capabilities**:
- Pull request management
- Code review assistance
- Issue tracking
- Repository operations
- Branch management
- CI/CD workflows

**Detection Keywords**: github, PR, pull request, issue, repository, branch, commit, code review

📄 [Full PRD](./github-subagent-prd.md)

---

### 5. Notion Subagent
**Domain**: Knowledge Management & Documentation
**Priority**: MEDIUM-HIGH
**Tools**: 15+ Notion MCP tools
**Context**: <20k tokens

**Capabilities**:
- Documentation creation
- Knowledge search
- Database management
- Template usage
- Content organization

**Detection Keywords**: notion, documentation, docs, wiki, knowledge, page, database, note

📄 [Full PRD](./notion-subagent-prd.md)

---

### 6. Linear Subagent
**Domain**: Project Management & Issue Tracking
**Priority**: HIGH
**Tools**: 25+ Linear MCP tools
**Context**: <20k tokens

**Capabilities**:
- Issue creation and triage
- Sprint planning
- Roadmap management
- Velocity tracking
- Team coordination

**Detection Keywords**: linear, issue, ticket, sprint, cycle, project, roadmap, backlog

📄 [Full PRD](./linear-subagent-prd.md)

---

## Context Efficiency Comparison

| Configuration | Total Tools | Context Size | Notes |
|--------------|-------------|--------------|-------|
| **All-in-One** (current) | 118+ tools | 100k+ tokens | ❌ Context pollution |
| **Main Agent Only** | 6 core tools | <10k tokens | ✅ Clean baseline |
| **Main + All Subagents Loaded** | 118+ tools | 100k+ tokens | ❌ Same problem |
| **Main + On-Demand Subagents** | 6-36 tools | 10k-40k tokens | ✅ **Our approach** |

**Key Insight**: With on-demand subagents, we only load 1-3 subagents per conversation (max 40k tokens), leaving 60k+ tokens for actual conversation and context.

## Implementation Phases

### Phase 0: Architecture ✅
- [x] Design subagent system architecture
- [x] Write PRDs for all 6 subagents
- [x] Define routing strategy

### Phase 1: Foundation (Week 1)
- [ ] Implement `SubagentRouter` with domain detection
- [ ] Create `BaseSubagent` framework
- [ ] Build lazy-loading infrastructure
- [ ] Set up caching system

### Phase 2: Proof of Concept (Week 1-2)
- [ ] Implement `StripeSubagent` (first full subagent)
- [ ] Test domain detection accuracy
- [ ] Verify context isolation
- [ ] Benchmark performance

### Phase 3: Full Deployment (Week 2-3)
- [ ] Implement remaining 5 subagents
- [ ] Integrate with `ValorAgent`
- [ ] Add subagent monitoring
- [ ] Production testing

### Phase 4: Optimization (Week 3-4)
- [ ] Tune domain detection
- [ ] Optimize caching strategy
- [ ] Add multi-subagent coordination
- [ ] Performance optimization

## Success Metrics

### Activation Accuracy
- **Target**: >90% correct subagent routing
- **Measure**: % of queries routed to correct subagent
- **Threshold**: <5% false positives

### Context Efficiency
- **Baseline**: 100k+ tokens with all tools
- **Target**: <40k tokens with on-demand loading
- **Improvement**: >60% context reduction

### Performance
- **Subagent Load**: <500ms for first activation
- **Cached Load**: <50ms for subsequent uses
- **Domain Detection**: <200ms for classification

### User Experience
- **Transparency**: User doesn't notice subagent activation
- **Accuracy**: Correct domain detection >90%
- **Latency**: No noticeable delay vs all-in-one

## Development Standards

All subagents must meet:

1. **9.8/10 Quality Standard**: Same as main system
2. **Context Isolation**: No tool leakage between subagents
3. **Lazy Loading**: Only instantiate when needed
4. **Caching**: Reuse loaded subagents within session
5. **Error Handling**: Graceful fallback to main agent
6. **Documentation**: Complete PRD and implementation docs
7. **Testing**: Unit, integration, and context isolation tests

## Future Subagents (Candidates)

### Additional MCP Domains
- **Vercel/Netlify**: Alternative deployment platforms
- **Slack**: Team communication and notifications
- **Jira**: Alternative to Linear (for teams using Jira)
- **Confluence**: Alternative to Notion (for teams using Confluence)
- **Datadog/New Relic**: Alternative monitoring platforms
- **AWS/GCP/Azure**: Cloud infrastructure management
- **Cloudflare**: CDN and edge computing
- **Postgres/MySQL**: Database management
- **Redis**: Cache management
- **Kubernetes**: Container orchestration

### Evaluation Criteria for New Subagents
1. **Tool Count**: >10 tools = good candidate
2. **Usage Frequency**: Used in <50% of conversations = good candidate
3. **Context Size**: Would add >5k tokens = good candidate
4. **Domain Coherence**: Clear functional boundary = good candidate
5. **User Demand**: Multiple requests for integration = good candidate

## Architecture Documents

- **System Overview**: [docs/architecture/subagent-mcp-system.md](../architecture/subagent-mcp-system.md)
- **MCP Integration**: [docs/architecture/mcp-integration.md](../architecture/mcp-integration.md)
- **Unified Agent Design**: [docs/architecture/unified-agent-design.md](../architecture/unified-agent-design.md)

## Related Components

- **SubagentRouter**: `agents/subagent_router.py` (to be implemented)
- **BaseSubagent**: `agents/subagents/base.py` (to be implemented)
- **ValorAgent**: `agents/valor/agent.py` (to be updated)
- **MCP Orchestrator**: `mcp_servers/orchestrator.py` (existing)

## Questions?

For questions about subagent architecture or PRDs, see:
- Architecture overview: [docs/architecture/subagent-mcp-system.md](../architecture/subagent-mcp-system.md)
- Individual PRDs in this directory
- CLAUDE.md for development guidelines

---

**Status**: Design Complete, Implementation Pending
**Last Updated**: 2025-01-18
**Next Step**: Implement SubagentRouter and BaseSubagent
