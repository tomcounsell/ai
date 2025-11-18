# Subagent-Based MCP Architecture

## Overview

A lazy-loading subagent system that prevents context pollution by only loading MCP tools when needed.

## Problem Statement

**Context Pollution**: Loading all MCP servers (Stripe, Sentry, Render, GitHub, Notion, Linear, etc.) into the main agent's context would bloat the prompt with hundreds of unused tool schemas, wasting tokens and degrading performance.

**Solution**: Specialized subagents that load on-demand with intelligent routing.

## Architecture

### 1. SubagentRouter (New Component)

**Location**: `agents/subagent_router.py`

**Responsibilities**:
- Analyze incoming messages to detect MCP domain needs
- Route requests to appropriate subagents
- Lazy-load subagents only when needed
- Cache loaded subagents for reuse
- Maintain context isolation between subagents

**Intelligence**:
- Uses LLM-based intent classification (not keyword matching)
- Analyzes conversation context for domain signals
- Multi-domain detection (some requests need multiple subagents)

### 2. Specialized Subagents

Each subagent is a lightweight `ValorAgent` instance with:
- **Focused tool set**: Only MCP tools for its domain
- **Specialized prompt**: Domain-specific instructions
- **Independent context**: No pollution of main context
- **Lazy instantiation**: Created on first use, cached thereafter

#### Proposed Subagents:

**Payment & Billing** (`StripeSubagent`)
- Stripe MCP tools
- Payment processing, subscriptions, invoices
- Prompt: Expert in financial operations

**Monitoring & Errors** (`SentrySubagent`)
- Sentry MCP tools
- Error tracking, performance monitoring, alerts
- Prompt: Expert in system health and debugging

**Infrastructure** (`RenderSubagent`, `VercelSubagent`, etc.)
- Render, Vercel, Railway MCP tools
- Deployments, service management, logs
- Prompt: Expert in cloud infrastructure

**Code Management** (`GitHubSubagent`)
- GitHub MCP tools
- Repositories, PRs, issues, CI/CD
- Prompt: Expert in version control and collaboration

**Knowledge & Docs** (`NotionSubagent`)
- Notion MCP tools
- Documentation, wikis, knowledge bases
- Prompt: Expert in information organization

**Project Management** (`LinearSubagent`)
- Linear MCP tools
- Issues, sprints, roadmaps
- Prompt: Expert in project tracking

**Social & Content** (`SocialSubagent`)
- Twitter, Reddit, content APIs
- Social media management, content creation
- Prompt: Expert in community engagement

### 3. Integration with ValorAgent

**Modified Flow**:

```
User Message
    ↓
ValorAgent.process_message()
    ↓
SubagentRouter.analyze_message()
    ↓
[Domain Detection: "payment issue" → StripeSubagent needed]
    ↓
SubagentRouter.get_or_create_subagent("stripe")
    ↓
StripeSubagent.process_task(task_context)
    ↓
[Only Stripe tools loaded here]
    ↓
Result returned to ValorAgent
    ↓
ValorAgent returns response to user
```

**Main Agent Context**: Clean, only core tools
**Subagent Context**: Focused, only domain tools

### 4. Implementation Details

#### SubagentRouter Class

```python
class SubagentRouter:
    """Intelligent routing to specialized MCP subagents."""

    def __init__(self, main_agent: ValorAgent):
        self.main_agent = main_agent
        self._subagents: Dict[str, ValorAgent] = {}  # Lazy cache
        self._subagent_registry: Dict[str, SubagentConfig] = {}

    async def analyze_and_route(
        self,
        message: str,
        context: ValorContext
    ) -> Optional[SubagentResponse]:
        """
        Analyze message to detect MCP domain needs.
        Returns None if no subagent needed (use main agent).
        """
        # Use LLM to detect domain (intelligent, not keywords)
        domains = await self._detect_domains(message, context)

        if not domains:
            return None  # No subagent needed

        # Get or create subagents for detected domains
        results = []
        for domain in domains:
            subagent = await self._get_or_create_subagent(domain)
            result = await subagent.process_task(message, context)
            results.append(result)

        # Combine results if multiple subagents used
        return self._combine_results(results)

    async def _detect_domains(
        self,
        message: str,
        context: ValorContext
    ) -> List[str]:
        """
        Use LLM to intelligently detect which MCP domains are needed.

        Examples:
        - "Check our Stripe revenue" → ["stripe"]
        - "Deploy to Render and check Sentry" → ["render", "sentry"]
        - "Create GitHub PR and update Linear" → ["github", "linear"]
        """
        # Fast LLM call for domain classification
        # Returns list of domain identifiers
        pass

    async def _get_or_create_subagent(
        self,
        domain: str
    ) -> ValorAgent:
        """Lazy load subagent, cache for reuse."""
        if domain not in self._subagents:
            config = self._subagent_registry[domain]
            self._subagents[domain] = await self._create_subagent(config)

        return self._subagents[domain]

    def register_subagent(
        self,
        domain: str,
        config: SubagentConfig
    ):
        """Register a new subagent type."""
        self._subagent_registry[domain] = config
```

#### SubagentConfig Class

```python
class SubagentConfig(BaseModel):
    """Configuration for a specialized subagent."""

    domain: str  # "stripe", "sentry", etc.
    name: str  # Display name
    description: str  # What it handles
    mcp_servers: List[str]  # Which MCP servers to load
    system_prompt: str  # Domain-specific instructions
    model: str = "openai:gpt-4"  # Can use cheaper models for simple domains
    max_context_tokens: int = 50_000  # Smaller than main agent
```

#### Subagent Implementation

Each subagent is just a focused `ValorAgent`:

```python
class StripeSubagent(ValorAgent):
    """Specialized agent for Stripe payment operations."""

    def __init__(self):
        super().__init__(
            model="openai:gpt-4",
            persona_path=Path("agents/subagents/stripe_persona.md"),
            max_context_tokens=50_000  # Smaller context
        )

        # Only load Stripe MCP tools
        self._load_mcp_tools(["stripe"])

    async def process_task(
        self,
        message: str,
        context: ValorContext
    ) -> Dict[str, Any]:
        """Process a Stripe-specific task."""
        # This context only has Stripe tools loaded
        return await self.process_message(message, context.chat_id)
```

### 5. Benefits

1. **Context Efficiency**: Main agent context stays clean with only core tools
2. **Scalability**: Add unlimited MCP servers without context bloat
3. **Performance**: Smaller contexts = faster inference, lower costs
4. **Specialization**: Each subagent has domain expertise in its prompt
5. **Flexibility**: Can use cheaper models for simple domains (Stripe CRUD vs. complex reasoning)
6. **Isolation**: Subagent errors don't crash main agent

### 6. Example Usage

```python
# User asks: "What's our Stripe revenue for Q4?"

# 1. Message goes to ValorAgent
valor_agent = ValorAgent()
response = await valor_agent.process_message(
    message="What's our Stripe revenue for Q4?",
    chat_id="user123"
)

# 2. ValorAgent checks SubagentRouter
router = valor_agent.subagent_router
subagent_result = await router.analyze_and_route(message, context)

# 3. Router detects "stripe" domain
# 4. Router lazy-loads StripeSubagent (if not cached)
# 5. StripeSubagent processes with only Stripe tools in context
# 6. Result bubbles back to ValorAgent
# 7. ValorAgent returns final response

# Main agent context: CLEAN (no Stripe tools polluting it)
# StripeSubagent context: FOCUSED (only Stripe tools)
```

### 7. Directory Structure

```
agents/
├── valor/
│   ├── agent.py              # Main ValorAgent
│   └── persona.md            # Main persona
├── subagents/
│   ├── base.py               # BaseSubagent class
│   ├── stripe/
│   │   ├── agent.py          # StripeSubagent
│   │   └── persona.md        # Stripe-focused prompt
│   ├── sentry/
│   │   ├── agent.py
│   │   └── persona.md
│   ├── render/
│   ├── github/
│   ├── notion/
│   ├── linear/
│   └── social/
├── subagent_router.py        # SubagentRouter
└── context_manager.py        # Existing
```

### 8. Migration Path

**Phase 1**: Build infrastructure
- Create `SubagentRouter` class
- Create `BaseSubagent` class
- Add domain detection logic

**Phase 2**: First subagent (proof of concept)
- Implement `StripeSubagent`
- Test lazy loading
- Verify context isolation

**Phase 3**: Scale out
- Add remaining subagents (Sentry, Render, GitHub, etc.)
- Write domain-specific personas
- Configure MCP server mappings

**Phase 4**: Integrate with ValorAgent
- Add router to ValorAgent initialization
- Modify `process_message()` to check router first
- Add fallback to main agent if no subagent needed

**Phase 5**: Production deployment
- Monitor context sizes
- Optimize domain detection
- Add metrics and monitoring

### 9. Future Enhancements

1. **Multi-Agent Collaboration**: Some tasks need multiple subagents working together
2. **Subagent Chaining**: Output of one subagent feeds into another
3. **Context Sharing**: Smart sharing of relevant context between subagents
4. **Auto-Registration**: MCP servers auto-register their recommended subagent config
5. **Cost Optimization**: Use cheaper models (GPT-3.5) for simple CRUD subagents

### 10. Success Metrics

- **Context Size**: Main agent context should stay under 10k tokens
- **Response Time**: No significant latency from routing overhead
- **Accuracy**: Domain detection >95% accurate
- **Coverage**: Support for 10+ MCP domains without context pollution

---

**Status**: Design phase - ready for implementation
**Next Step**: Implement SubagentRouter and BaseSubagent classes
