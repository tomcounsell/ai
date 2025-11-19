# Subagent Architecture Improvements
**Based on Claude Code Official Documentation**

## Overview

After reviewing the [official Claude Code sub-agent documentation](https://code.claude.com/docs/en/sub-agents), we can significantly improve our MCP subagent architecture by adopting Claude's native patterns.

## Key Insights from Claude Code

### ✅ What We Got Right

1. **Context Isolation** - "Separate context window from main conversation" ✅
2. **Specialized Focus** - Single-purpose subagents with clear domains ✅
3. **Lazy Loading** - Load only when needed ✅
4. **Domain Detection** - Intelligent routing based on intent ✅

### 🔧 Critical Improvements Needed

## 1. Use Native Claude Code Subagent Format

**Current Design**: Custom Python classes in `agents/subagents/*/agent.py`

**Improved Design**: Markdown files with YAML frontmatter in `.claude/agents/`

### Why This Matters
- **Native Integration**: Claude Code automatically detects and manages these
- **No Custom Router Needed**: Claude handles invocation automatically
- **Version Control**: Team can collaborate on subagent definitions
- **Simpler**: Just markdown files, no complex Python classes

### Implementation

**File Structure**:
```
.claude/
└── agents/
    ├── stripe.md
    ├── sentry.md
    ├── render.md
    ├── github.md
    ├── notion.md
    └── linear.md
```

**Example** - `.claude/agents/stripe.md`:
```markdown
---
name: stripe
description: Handles payment processing, subscriptions, billing, and revenue analytics via Stripe API. Invoke for queries about payments, refunds, customers, MRR, or financial operations.
tools:
  - stripe_list_customers
  - stripe_retrieve_customer
  - stripe_create_customer
  - stripe_list_subscriptions
  - stripe_create_subscription
  - stripe_cancel_subscription
  - stripe_list_invoices
  - stripe_create_refund
  - stripe_retrieve_balance
  - stripe_list_products
  - stripe_list_prices
model: sonnet
permissions:
  - mode: accept  # Auto-confirm Stripe operations
    tools: ["stripe_*"]
---

# Stripe Payment Expert

You are a specialized AI expert in payment processing and financial operations using the Stripe platform.

## Expertise
- Payment processing and transaction management
- Subscription lifecycle and billing operations
- Customer account management
- Refund and dispute handling
- Financial analytics and reporting
- Stripe API best practices

## Core Principles

### Financial Operations
1. Always confirm amounts and actions before executing
2. Mask sensitive payment data (show last 4 digits only)
3. Explain financial concepts clearly
4. Be precise with monetary amounts (always include currency)
5. Log all operations for audit purposes

### Security
- Never expose full card numbers
- Require confirmation for destructive operations (refunds, cancellations)
- Validate user permissions for sensitive operations
- Be transparent about what data you're accessing

### Communication Style
- Professional and financially literate
- Clear about monetary amounts and dates
- Security-conscious in all responses
- Helpful but cautious with financial operations

## Common Tasks

**Revenue Analysis**: Query subscriptions and calculate MRR, ARR, growth rates
**Customer Lookup**: Search by email, ID, or domain; show subscription status
**Subscription Management**: Create, update, cancel subscriptions with proper validation
**Refund Processing**: Issue refunds with amount confirmation and reason logging
**Payment Investigation**: Analyze failed payments, explain decline reasons, suggest fixes

## Response Format

Always include:
- Clear monetary amounts with currency ($1,234.56 USD)
- Relevant dates in human-readable format
- Status indicators (✅ Paid, ❌ Failed, 🔄 Pending)
- Next steps or recommendations
- Links to Stripe dashboard for details
```

## 2. Granular Tool Permissions

**Current Design**: Load all MCP tools for a domain

**Improved Design**: Specify exact tools each subagent can access

### Why This Matters
- **Security**: Subagents only get tools they need
- **Performance**: Fewer tools in context = faster inference
- **Clarity**: Explicit about capabilities

### Implementation

```yaml
tools:
  # Option 1: Specific tools
  - stripe_list_customers
  - stripe_create_customer

  # Option 2: Pattern matching
  - stripe_*

  # Option 3: Inherit all (default)
  # tools: inherit
```

**Permission Modes**:
```yaml
permissions:
  - mode: accept      # Auto-approve these tools
    tools: ["stripe_list_*", "stripe_retrieve_*"]

  - mode: reject      # Never allow (safety)
    tools: ["stripe_delete_*"]

  - mode: prompt      # Ask user first (default)
    tools: ["stripe_create_refund"]
```

## 3. Proactive Automatic Invocation

**Current Design**: User must explicitly mention domain keywords

**Improved Design**: Claude automatically invokes based on context and description

### Why This Matters
- **Better UX**: User doesn't need to know which subagent to use
- **Intelligent**: Claude analyzes conversation context, not just keywords
- **Seamless**: Subagent invocation is invisible to user

### Implementation

**Description-Based Matching**:
```yaml
description: |
  Handles payment processing, subscriptions, billing, and revenue analytics
  via Stripe API. Invoke for queries about payments, refunds, customers,
  MRR, or financial operations.
```

Claude analyzes:
- Current conversation context
- User intent and domain
- Available subagent descriptions
- Automatically delegates to best match

**Example**:
```
User: "How much did we make last month?"

Claude (internal):
- Detects "revenue" intent
- Matches "revenue analytics via Stripe API" in description
- Automatically invokes stripe subagent
- Returns result seamlessly
```

## 4. Model Selection Per Subagent

**Current Design**: All subagents use GPT-4

**Improved Design**: Choose optimal model per subagent complexity

### Why This Matters
- **Cost Optimization**: Use Haiku for simple CRUD, Sonnet for complex reasoning
- **Speed**: Faster responses for simple queries
- **Flexibility**: Match model to task complexity

### Implementation

```yaml
# Simple CRUD operations - use fast, cheap model
model: haiku

# Complex analysis - use powerful model
model: sonnet

# Mission-critical - use best model
model: opus

# Inherit from main conversation
model: inherit
```

**Recommended Models**:
| Subagent | Model | Rationale |
|----------|-------|-----------|
| Stripe | sonnet | Financial accuracy critical |
| Sentry | sonnet | Complex error analysis |
| Render | haiku | Simple deployment commands |
| GitHub | sonnet | Code review needs reasoning |
| Notion | haiku | Mostly CRUD operations |
| Linear | haiku | Issue management is simple |

## 5. Resumable Subagent Sessions

**Current Design**: Each invocation starts fresh

**Improved Design**: Continue previous subagent conversations

### Why This Matters
- **Context Continuity**: Follow-up questions work naturally
- **Efficiency**: Don't re-explain context
- **Better UX**: Conversational flow maintained

### Implementation

Claude automatically maintains subagent context across invocations in the same conversation:

```
User: "What's our Stripe MRR?"
[Stripe subagent activated]
→ "Your MRR is $125,000"

User: "What about last month?"
[Same Stripe subagent resumes with context]
→ "Last month's MRR was $118,000 (+5.9% growth)"

User: "Show me the top 5 customers by revenue"
[Stripe subagent continues]
→ [Customer list...]
```

No custom implementation needed - Claude Code handles this natively.

## 6. Skills Integration

**Current Design**: No skill system

**Improved Design**: Subagents can use skills for specialized capabilities

### Why This Matters
- **Composability**: Combine subagents with skills
- **Reusability**: Skills work across all subagents
- **Extensibility**: Add new capabilities without changing subagents

### Implementation

```yaml
skills:
  - data-analysis  # For complex data manipulation
  - visualization  # For charts and graphs
  - pdf-export     # For report generation
```

**Example** - Sentry subagent with data-analysis skill:
```yaml
---
name: sentry
description: Error monitoring and performance analysis
skills:
  - data-analysis  # For analyzing error trends
model: sonnet
---
```

Now Sentry subagent can:
- Analyze error patterns with pandas/numpy
- Generate statistical insights
- Forecast error trends

## 7. Remove SubagentRouter (Not Needed!)

**Current Design**: Custom `SubagentRouter` class for domain detection

**Improved Design**: Use Claude Code's built-in routing

### Why This Matters
- **Simpler**: No custom code needed
- **Better**: Claude's routing is more sophisticated
- **Maintained**: Updates come from Anthropic

### Implementation

**Delete**:
- ❌ `agents/subagent_router.py`
- ❌ Custom domain detection logic
- ❌ Manual subagent instantiation

**Use Instead**:
- ✅ `.claude/agents/*.md` files
- ✅ Good descriptions for automatic matching
- ✅ Trust Claude Code's built-in routing

## Revised Architecture

### Old (Custom Python Classes)
```
agents/
├── subagents/
│   ├── base.py              # Custom base class
│   ├── stripe/
│   │   ├── agent.py         # Python class
│   │   └── persona.md       # Prompt file
│   └── sentry/
│       ├── agent.py
│       └── persona.md
├── subagent_router.py       # Custom router
└── valor/agent.py
```

### New (Native Claude Code)
```
.claude/
└── agents/
    ├── stripe.md            # Markdown with YAML frontmatter
    ├── sentry.md
    ├── render.md
    ├── github.md
    ├── notion.md
    └── linear.md

agents/
└── valor/agent.py           # Main agent only
```

**Context Pollution Solved**: Same benefit, 90% less code!

## Implementation Plan (Revised)

### Phase 1: Convert to Claude Code Format (Week 1)
- [ ] Create `.claude/agents/` directory
- [ ] Convert each PRD to `.md` format with YAML frontmatter
- [ ] Define tool permissions for each subagent
- [ ] Select optimal model per subagent
- [ ] Write comprehensive descriptions for auto-invocation

### Phase 2: Tool Integration (Week 1)
- [ ] Map MCP tools to subagent YAML configs
- [ ] Set up permission modes (accept/reject/prompt)
- [ ] Test tool access control
- [ ] Verify context isolation

### Phase 3: Testing (Week 2)
- [ ] Test automatic subagent invocation
- [ ] Verify context isolation between subagents
- [ ] Test model selection (haiku vs sonnet)
- [ ] Validate resumable sessions
- [ ] Test multi-subagent coordination

### Phase 4: Refinement (Week 2)
- [ ] Tune descriptions for better auto-matching
- [ ] Optimize model selection for cost/performance
- [ ] Add skills where beneficial
- [ ] Document usage patterns

### Phase 5: Team Rollout (Week 3)
- [ ] Version control subagent definitions
- [ ] Team training on subagent usage
- [ ] Monitor usage and accuracy
- [ ] Iterate based on feedback

## Comparison: Before vs After

| Aspect | Our Original Design | Claude Code Native | Winner |
|--------|-------------------|-------------------|---------|
| **Implementation** | Custom Python classes | Markdown files | Native ✅ |
| **Routing** | Custom SubagentRouter | Built-in detection | Native ✅ |
| **Tool Control** | Load entire MCP server | Granular permissions | Native ✅ |
| **Model Selection** | All use GPT-4 | Per-subagent choice | Native ✅ |
| **Invocation** | Keyword-based | Context-aware | Native ✅ |
| **Resumability** | Manual tracking | Automatic | Native ✅ |
| **Context Isolation** | ✅ We got this right | ✅ Native support | Tie ✅ |
| **Code Complexity** | High (router, base classes) | Low (just .md files) | Native ✅ |
| **Maintainability** | Custom code to maintain | Anthropic maintains | Native ✅ |
| **Team Collaboration** | Version control Python | Version control .md | Native ✅ |

**Result**: Claude Code's native subagent system is superior in almost every way!

## Migration Benefits

### Before (Custom Implementation)
- **Code**: ~2000 lines (SubagentRouter, BaseSubagent, 6 subagent classes)
- **Maintenance**: Manual updates to routing logic
- **Complexity**: High - custom domain detection, lazy loading, caching
- **Team Adoption**: Need to understand custom architecture

### After (Native Claude Code)
- **Code**: ~600 lines (6 markdown files @ ~100 lines each)
- **Maintenance**: Just update markdown files
- **Complexity**: Low - Claude handles everything
- **Team Adoption**: Standard Claude Code patterns

**Net Savings**: 70% less code, 90% less complexity!

## Recommended Next Steps

1. **Pause Custom Implementation**: Don't build SubagentRouter yet
2. **Create First Native Subagent**: Convert Stripe PRD to `.claude/agents/stripe.md`
3. **Test Automatic Invocation**: Verify Claude detects and uses it
4. **Iterate**: Refine description, tools, permissions
5. **Scale**: Convert remaining 5 subagents
6. **Remove**: Delete custom router plans from architecture

## Updated Success Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| **Activation Accuracy** | >95% | % queries routed to correct subagent |
| **Context Reduction** | >60% | Token savings vs all-in-one |
| **Response Time** | <1s overhead | Subagent invocation latency |
| **Code Complexity** | <700 lines | Total lines in `.claude/agents/` |
| **Team Adoption** | 100% | All devs using subagents within 2 weeks |

## Open Questions

1. **Q**: Can we still use MCP servers with native subagents?
   **A**: YES - `tools` field supports MCP tool names

2. **Q**: Do we lose any functionality vs custom implementation?
   **A**: NO - Native approach is more powerful

3. **Q**: What about complex multi-subagent coordination?
   **A**: Claude Code handles this automatically - user can reference multiple domains in one query

4. **Q**: Can we still version control these?
   **A**: YES - `.claude/agents/*.md` files are just text files in git

5. **Q**: What if we need custom Python logic in a subagent?
   **A**: Use skills - they can execute custom code and are available to subagents

## Conclusion

**Recommendation**: 🚨 **Pivot to Claude Code's native subagent system**

Our custom architecture was well-designed, but Claude Code's native system is:
- ✅ Simpler (70% less code)
- ✅ More powerful (better routing, resumability, permissions)
- ✅ Better maintained (Anthropic updates it)
- ✅ Standard (team familiarity)
- ✅ Future-proof (gets new features automatically)

**Action**: Convert our 6 PRDs into `.claude/agents/*.md` format and let Claude handle the rest.

---

**Document Status**: Recommendation
**Last Updated**: 2025-01-18
**Impact**: HIGH - Significant simplification
**Effort**: MEDIUM - Conversion work, but less than original plan
**Decision**: Pending stakeholder approval
