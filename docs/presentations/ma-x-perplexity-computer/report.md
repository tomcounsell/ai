# Claude Managed Agents vs Perplexity Computer: Strategic Analysis

*April 13, 2026*

---

## Executive Summary

Claude Managed Agents (MA) and Perplexity Computer represent two fundamentally different approaches to AI agent infrastructure. MA is an API-first, developer-oriented execution backend -- containerized, programmable, and designed to be embedded in custom systems. Perplexity Computer is a UI-first, end-user-oriented digital worker -- multi-model, web-native, and designed to replace human workflows at a computer.

**Governing thought:** MA is a programmable execution engine. Perplexity Computer is an autonomous digital worker. For Valor's use case -- a system that needs to orchestrate agent execution programmatically -- MA is the clear choice. Perplexity Computer solves a different problem.

---

## 1. What Is Claude Managed Agents?

Claude Managed Agents is Anthropic's fully hosted agent infrastructure, launched in public beta April 2026. It provides a pre-built agent harness running in managed cloud containers with:

- **Pre-built agent loop** -- Claude autonomously decides when and how to use tools
- **Managed containers** -- secure cloud execution with pre-installed packages
- **Session persistence** -- conversation history and filesystem state across interactions
- **Real-time streaming** -- Server-sent events (SSE) for live agent responses
- **Built-in optimizations** -- prompt caching, compaction, efficient tool execution

### Core Resources

| Resource | Purpose | API |
|----------|---------|-----|
| **Agent** | Reusable config: model, system prompt, tools, skills | \`POST /v1/agents\` |
| **Environment** | Cloud container definition (packages, networking, mounts) | \`POST /v1/environments\` |
| **Session** | Running instance of an agent within an environment | \`POST /v1/sessions\` |
| **Vault** | Secret storage, injected as env vars at session start | Referenced via \`vault_ids\` |

### API Pattern

\`\`\`python
# Create agent (reusable)
agent = client.beta.agents.create(
    name="Build Agent",
    model="claude-sonnet-4-6",
    system="You are a coding assistant.",
    tools=[{"type": "agent_toolset_20260401"}],
)

# Create environment (persistent)
environment = client.beta.environments.create(
    name="fullstack-env",
    config={"type": "cloud", "networking": {"type": "unrestricted"}},
)

# Create session (per-task)
session = client.beta.sessions.create(
    agent=agent.id,
    environment_id=environment.id,
    vault_ids=[github_vault.id],
)

# Send events, stream responses
client.beta.sessions.events.send(session.id, events=[
    {"type": "user.message", "content": [{"type": "text", "text": "Build the feature"}]},
])

with client.beta.sessions.events.stream(session.id) as stream:
    for event in stream:
        match event.type:
            case "agent.message": print(event.content[0].text)
            case "agent.tool_use": print(f"[Tool: {event.name}]")
            case "session.status_idle": break
\`\`\`

### Built-in Tools (\`agent_toolset_20260401\`)

| Tool | Description |
|------|-------------|
| \`bash\` | Execute shell commands |
| \`read\` | Read files |
| \`write\` | Write files |
| \`edit\` | String replacement in files |
| \`glob\` | Pattern matching |
| \`grep\` | Regex search |
| \`web_fetch\` | Fetch URL content |
| \`web_search\` | Web search |

All individually toggleable. Custom tools and MCP servers also supported.

### Pricing

| Component | Cost |
|-----------|------|
| Input/output tokens | Standard Claude model pricing |
| Session runtime | \$0.08 per session-hour (billed per ms, idle is free) |
| Web search | \$10 per 1,000 searches |

### SDKs

Python, TypeScript, Go, Java, C#, Ruby, PHP, and the \`ant\` CLI.

### Research Preview Features

- **Outcomes** -- define success criteria for agent tasks
- **Multi-agent** -- orchestrate multiple agents in one session
- **Memory** -- persistent agent memory across sessions

---

## 2. What Is Perplexity Computer?

Perplexity Computer, launched February 25, 2026, is a multi-model AI agent that orchestrates 19+ AI models to complete complex, multi-step workflows entirely in the background. It is available exclusively to Perplexity Max subscribers (\$200/month).

### Core Architecture

Perplexity Computer is fundamentally a **browser-automation agent** that uses visual understanding (vision-language models) to interact with web interfaces the way a human would. Its execution flow:

1. User provides a natural language task description
2. System decomposes the task into subtasks
3. Routes each subtask to the optimal AI model (Claude Opus 4.6 for reasoning, Gemini for research, GPT-5.2 for long-context, Grok for lightweight tasks, specialized models for media generation)
4. Spins up a virtual browser environment in the cloud
5. Vision-language model interprets rendered page state and takes actions (click, type, scroll, navigate)
6. Process iterates until task completion or human intervention required
7. Results return to user

### Key Capabilities

- **Web browsing and automation** -- interacts with any web interface visually
- **Code execution** -- sandboxed Python, JavaScript, SQL in isolated containers
- **File system** -- persistent filesystem mounted via FUSE per session
- **400+ pre-built integrations** -- Slack, Google Workspace, various SaaS tools
- **MCP server support** -- local and remote MCP connections
- **Proactive monitoring** -- can watch email, calendar, flight status with triggers
- **Scheduled jobs** -- morning briefings, deadline reminders, recurring tasks

### Product Variants

| Variant | Description | Access |
|---------|-------------|--------|
| **Computer** (cloud) | Cloud-based agent, runs in Perplexity's infrastructure | Max subscription (\$200/mo) |
| **Personal Computer** | Mac mini hardware + always-on local agent | Separate hardware purchase |
| **Computer for Enterprise** | Org-level controls, SCIM, audit logs, Snowflake | Enterprise Max (\$325/seat/mo) |

### Session and Memory Model

- Each task runs in an isolated compute container with dedicated filesystem and browser
- Session context persists across conversations via a user-specific knowledge graph
- Vector embeddings in LanceDB enable semantic retrieval of historical context
- Projects can run for hours, days, or months with full context retention

### Credit System

- Max tier: 10,000 credits/month included
- Enterprise Max: 15,000 credits/month per seat
- Credit consumption varies by task complexity (no published conversion table)
- Simple tasks: ~30 credits; complex multi-hour tasks: 15,000-21,000+ credits
- Heavy professional use: expect \$300-500/month

### API Access

Perplexity offers a separate **Agent API** (\`POST https://api.perplexity.ai/v1/agent\`) that provides:

- Multi-provider model access (OpenAI, Anthropic, Google, xAI)
- Built-in tools: \`web_search\`, \`fetch_url\`, custom function calling
- Pay-as-you-go pricing with detailed cost breakdowns
- OpenAI SDK compatibility (\`POST /v1/responses\`)

**Critical distinction:** The Agent API is a **separate product** from Computer. It provides agentic search and tool-use capabilities but does **not** expose Computer's browser automation, visual interaction, or multi-model orchestration programmatically. There is no documented API to programmatically create and manage Computer tasks.

---

## 3. SWOT Analysis

### Strengths

| Dimension | Managed Agents | Perplexity Computer |
|-----------|----------------|---------------------|
| **API-first design** | Full REST API with SSE streaming | Agent API for search/tools; Computer itself is UI-driven |
| **Programmability** | Create agents, sessions, environments programmatically | Limited -- Agent API is separate from Computer product |
| **Code execution** | Full container with bash, file ops, package management | Sandboxed Python/JS/SQL with FUSE filesystem |
| **Tool ecosystem** | 8 built-in + custom tools + MCP servers | 400+ pre-built integrations + MCP support |
| **Web capabilities** | web_fetch + web_search (text-based) | Full visual browser automation (sees rendered pages) |
| **Model flexibility** | Claude models only | 19+ models routed per subtask |
| **Session management** | Explicit create/stream/archive lifecycle | Persistent knowledge graph across sessions |
| **Secret management** | Vault system, env var injection | Credential handling via integrations (limited docs) |
| **Pricing transparency** | Per-ms billing + per-search + per-token | Opaque credit system, no published per-task rates |

### Weaknesses

| Dimension | Managed Agents | Perplexity Computer |
|-----------|----------------|---------------------|
| **Web browsing** | Text-only fetch, no visual interaction | Full visual browser but failure-prone for complex UIs |
| **Multi-model** | Claude only | Routes to best model per task, but adds latency |
| **End-user UX** | API-only, no consumer UI | Polished consumer experience |
| **Memory** | Research preview only | Production knowledge graph with vector search |
| **Always-on** | Sessions are ephemeral | Personal Computer variant is 24/7 |
| **Orchestrability** | Fully orchestrable by external systems | Computer product cannot be orchestrated programmatically |
| **Cost predictability** | Predictable per-ms + per-token | Unpredictable credit consumption |
| **Maturity** | Beta (April 2026) | Production (February 2026) |

### Opportunities

| Opportunity | Managed Agents | Perplexity Computer |
|-------------|----------------|---------------------|
| **Backend for custom systems** | Natural fit -- designed for this | Not designed for programmatic orchestration |
| **Research and web scraping** | Basic (text fetch only) | Strong (visual browser, real-time search) |
| **Marketing/business automation** | Possible but requires custom tooling | Sweet spot -- 400+ integrations, visual browser |
| **Software development** | Strong -- bash, file ops, containers | Weak -- no native code tooling, browser-first |
| **Frontend testing** | Headless Chromium in containers | Visual browser could screenshot but not designed for test frameworks |

### Threats

| Threat | Impact |
|--------|--------|
| **Perplexity adds programmatic Computer API** | Would make Computer a viable execution backend for orchestrators |
| **Anthropic ships visual browser in MA** | Would close the web-browsing gap entirely |
| **Credit pricing opacity** | Could make Perplexity Computer uneconomical at scale |
| **MA memory GA** | Would eliminate one of Perplexity's current advantages (persistent memory) |
| **Multi-model in MA (research preview)** | Could reduce Perplexity's model-routing advantage |

---

## 4. Architecture Comparison

### Execution Model

| Aspect | Managed Agents | Perplexity Computer |
|--------|----------------|---------------------|
| **Runtime** | Linux container (cloud) | Cloud VM with virtual browser |
| **Primary interface** | CLI/programmatic (bash, file ops) | Visual (rendered web pages) |
| **How it "sees"** | Reads text, files, structured data | Vision-language model reads rendered pixels |
| **How it acts** | Executes commands, writes files | Clicks, types, scrolls, navigates |
| **Isolation** | Per-session container | Per-task isolated container + browser |
| **Persistence** | Environment persists; sessions are ephemeral | Knowledge graph persists; task containers are ephemeral |
| **Networking** | Configurable (restricted or unrestricted) | Unrestricted web access (sandboxed from internal network) |

### Fundamental Design Difference

MA is a **developer tool**: it exposes a container and lets code run inside it. The agent interacts with the filesystem and shell, the way a developer would.

Perplexity Computer is a **digital worker**: it exposes a virtual desktop and lets an agent interact with web applications. The agent interacts with rendered UI, the way a human end-user would.

This is not a spectrum -- it is a fork. Each approach excels at different workloads and fails at the other's.

---

## 5. API and Orchestration

### Managed Agents: Fully Orchestrable

MA is designed to be embedded in external systems. The full lifecycle is API-driven:

1. **Create** agent, environment, vault (one-time setup)
2. **Create** session per task (instant)
3. **Send** events (user messages, tool results)
4. **Stream** responses via SSE
5. **Steer** mid-execution with additional user events
6. **Archive/delete** when done

Key orchestration features:
- Custom tools that route through the orchestrator (Valor's "stealth injection" pattern)
- Mid-execution steering via \`user.message\` events
- Session status polling (\`idle\`, \`running\`, \`rescheduling\`, \`terminated\`)
- Full event history retrieval
- Rate limits: 60 creates/min, 600 reads/min

### Perplexity Computer: Not Orchestrable (Computer Product)

The Computer product is accessed via:
- Web interface at perplexity.ai
- Chrome extension
- Mobile apps (iOS/Android)
- Slack integration (Enterprise)

There is **no documented API** to programmatically create Computer tasks, stream their progress, inject steering messages, or retrieve structured results. The Agent API is a separate product that provides search and tool-use capabilities but does not control Computer.

### Perplexity Agent API: Partially Orchestrable

The Agent API (\`POST /v1/agent\`) is programmable and provides:
- Multi-provider model routing
- Web search and URL fetch tools
- Custom function calling
- Streaming responses
- OpenAI SDK compatibility

However, it does **not** provide:
- Browser automation
- Visual interaction with web pages
- The multi-model orchestration that makes Computer distinctive
- Persistent session management
- File system or code execution

**Assessment for Valor:** MA can be directly integrated as an execution backend. Perplexity Computer cannot. The Perplexity Agent API could be useful as a supplementary research tool but is not a substitute for either MA or Computer.

---

## 6. Tool Ecosystem

| Category | Managed Agents | Perplexity Computer |
|----------|----------------|---------------------|
| **Shell/CLI** | bash (full shell) | Not documented |
| **File operations** | read, write, edit, glob, grep | FUSE filesystem (read/write/list) |
| **Code execution** | Full container (any language) | Sandboxed Python, JS, SQL |
| **Web search** | web_search (text results) | 19-model research with citations |
| **Web fetch** | web_fetch (text extraction) | Full visual browser (rendered pages) |
| **Custom tools** | JSON schema definitions, client-executed | Custom function calling via Agent API |
| **MCP servers** | Native support (configured at agent level) | Local and remote MCP support |
| **Pre-built integrations** | None (bring your own) | 400+ (Slack, Google, SaaS tools) |
| **Package management** | Pre-install in environment | Runtime installation per session |

### Analysis

MA has a narrower but deeper tool set for development work. Its bash tool gives access to any CLI, any language runtime, and any package manager available in a Linux container. This means effectively unlimited tool coverage for technical tasks.

Perplexity Computer has broader coverage for business workflows through pre-built integrations but shallower control over technical execution. Its strength is interacting with web applications that do not have APIs -- the visual browser fills gaps that no tool-based approach can.

---

## 7. Web and Browser Capabilities

This is the largest capability gap between the two platforms.

### Managed Agents

- **web_search**: Returns text search results (like a search API)
- **web_fetch**: Fetches and processes URL content as text/markdown
- **No browser**: Cannot render JavaScript, interact with dynamic web apps, fill forms, or take screenshots of rendered pages
- **Headless browser**: Can be installed in the container environment (Chromium, Playwright) and used via bash, but requires explicit setup

### Perplexity Computer

- **Full visual browser**: Renders pages and interprets them with a vision-language model
- **Interaction**: Clicks, types, scrolls, navigates -- interacts with any web interface
- **Dynamic content**: Handles JavaScript-rendered apps, SPAs, authenticated sessions
- **Form filling**: Can fill complex multi-step forms
- **Screenshots**: Takes and analyzes screenshots as part of its workflow

### Implications for Valor

For **research tasks** (finding documentation, comparing products, reading changelogs), Perplexity Computer's visual browser is superior. It can handle paywalled sites, dynamic JavaScript apps, and complex navigation that web_fetch cannot.

For **development tasks** (building code, running tests, managing git), MA's container-based tools are superior. Code execution, file manipulation, and shell access are what matters -- not browser interaction.

For **frontend testing**, MA containers can run headless Chromium via Playwright (installed in the environment), giving equivalent capability to Perplexity's visual browser but with more control and determinism.

---

## 8. Code Execution

| Aspect | Managed Agents | Perplexity Computer |
|--------|----------------|---------------------|
| **Languages** | Any (install in environment) | Python, JavaScript, SQL |
| **Container** | Full Linux container | Sandboxed execution environment |
| **Bash/shell** | Full shell access | Not documented for Computer |
| **Package management** | Pre-install in environment or install at runtime | Runtime installation per session |
| **File persistence** | Within session (environment reused, sessions ephemeral) | FUSE-mounted filesystem per task |
| **Git support** | Full (install git, push/pull, manage branches) | Not a primary use case |
| **Test frameworks** | Any (pytest, jest, playwright, etc.) | Not documented |
| **Build tools** | Any (npm, pip, cargo, make, etc.) | Limited |

### Assessment

MA is purpose-built for code execution. Perplexity Computer can execute code but is not designed for software development workflows. The gap is most visible in:

1. **Shell access**: MA gives full bash. Perplexity Computer's code execution is language-specific sandboxes.
2. **Environment persistence**: MA environments pre-bake dependencies. Perplexity reinstalls per session.
3. **Git integration**: MA can push to GitHub via vault-injected tokens. Perplexity Computer struggles with git workflows (one user reported burning 2,000+ credits just trying to push to GitHub).
4. **Test execution**: MA can run any test framework natively. Perplexity Computer is not designed for this.

---

## 9. Session Management

| Aspect | Managed Agents | Perplexity Computer |
|--------|----------------|---------------------|
| **Creation** | Programmatic (\`POST /v1/sessions\`) | UI-initiated (web, mobile, Slack) |
| **Lifecycle** | \`idle\` -> \`running\` -> \`idle\` (multi-turn) | Task submitted -> running -> complete |
| **Streaming** | SSE stream of events | No programmatic streaming |
| **Mid-execution steering** | Send \`user.message\` during execution | Limited (can add context via UI) |
| **Resume** | Send new events to idle session | Persistent knowledge graph carries context |
| **Archive** | \`POST /v1/sessions/{id}/archive\` | Tasks complete and results persist |
| **History** | Full event history retrievable via API | Task history in UI |
| **Concurrent sessions** | Multiple (limited by rate limits) | Unclear documentation |

### Assessment

MA's session model is built for orchestration: explicit lifecycle, SSE streaming, programmatic steering. Perplexity Computer's session model is built for end users: submit a task, get results later. The gap matters most for Valor's use case, where the PM session needs to create, monitor, steer, and harvest results from execution sessions programmatically.

---

## 10. Pricing Analysis

### Cost Per Workload

**Scenario: 2-hour BUILD session (coding task)**

| Component | Managed Agents | Perplexity Computer |
|-----------|----------------|---------------------|
| Session runtime | \$0.16 (2 hrs x \$0.08) | ~500-2,000 credits (estimated) |
| Tokens (est. 500K in, 200K out) | ~\$3-8 (Sonnet pricing) | Included in credits |
| Web searches (est. 5) | \$0.05 | Included in credits |
| **Total** | **~\$3-8** | **~5-20% of monthly credit budget** |

**Scenario: 20 BUILD sessions per month**

| | Managed Agents | Perplexity Computer |
|---|----------------|---------------------|
| Runtime | \$3.20 | N/A (credit-based) |
| Tokens | \$60-160 | Included |
| Searches | \$1.00 | Included |
| **Monthly total** | **~\$65-165** | **\$200-500+** (likely exceeding included credits) |

### Cost Predictability

MA wins decisively on cost predictability:
- Per-millisecond billing with idle sessions costing nothing
- Token costs follow standard Claude pricing (published, well-understood)
- Web searches at a known rate (\$10/1K)

Perplexity Computer's credit system is opaque:
- No published per-task credit rates
- Complexity-based consumption makes budgeting difficult
- Overage costs unclear
- One documented case: 40-minute codebase scan consumed 21,000 credits (2x the monthly allotment)

### Recommendation

For Valor's workload profile (20-40 coding sessions/month, each 30 mins to 3 hours), MA is significantly more cost-effective and predictable. Perplexity Computer's pricing model is designed for occasional knowledge-work tasks, not sustained development execution.

---

## 11. Memory and Learning

| Aspect | Managed Agents | Perplexity Computer |
|--------|----------------|---------------------|
| **Cross-session memory** | Research preview (not GA) | Production knowledge graph |
| **Implementation** | Undocumented (preview) | Vector embeddings in LanceDB with semantic search |
| **Scope** | Per-agent (presumably) | Per-user across all tasks |
| **What it remembers** | TBD | Business context, preferences, project state, writing style |
| **External memory** | Custom tools can query external systems (Redis, etc.) | MCP connections to external data |
| **Observation extraction** | Not built-in (but achievable via post-session processing) | Unclear |

### Implications for Valor

Neither platform matches Valor's subconscious memory system (bloom filter + BM25+RRF recall + PostToolUse injection). However:

- **MA** allows custom tools that route through Valor for stealth memory injection. This is the "workflow tool" approach documented in the MA x Valor report.
- **Perplexity Computer** has no equivalent mechanism. Its memory is internal to the Perplexity platform and not accessible to external orchestrators.

MA's custom tool architecture makes it the only viable option for integrating Valor's memory system into a hosted execution backend.

---

## 12. Security Model

| Aspect | Managed Agents | Perplexity Computer |
|--------|----------------|---------------------|
| **Isolation** | Per-session container | Per-task isolated container + browser |
| **Secrets** | Vault system (env var injection, never in context) | Credential handling via integrations |
| **Network** | Configurable (restricted or unrestricted) | Sandboxed from org internal network |
| **Audit** | API logs | Enterprise audit logs (Enterprise tier) |
| **Data residency** | Anthropic cloud (region unclear) | Perplexity cloud (region unclear) |
| **Code visibility** | Full -- you see all events, tool calls, outputs | Limited -- results returned, internals opaque |
| **Human approval** | No built-in gate (orchestrator implements this) | Personal Computer has approval requirements |

### Assessment

MA provides more granular security controls through its vault system and network configuration. Perplexity Computer provides broader guardrails (sandboxing, approval gates) but with less programmatic control. For an orchestrator like Valor that needs to manage secrets and validate outputs, MA's explicit security model is preferable.

---

## 13. Valor Integration Assessment

### MA Integration (High Viability)

MA can be integrated as Valor's execution backend with minimal architectural changes:

1. **Session dispatch**: Worker creates MA sessions instead of spawning local Claude Code processes
2. **Environment pre-baking**: Create environments with project dependencies once
3. **Secret injection**: Vault replaces local \`.env\` for MA sessions (GITHUB_TOKEN, REDIS_URL)
4. **Stealth memory injection**: Custom tools (run_tests, git_commit, submit_work) route through Valor
5. **Pre-session memory**: Load relevant memories into the first user message
6. **Post-session extraction**: Pull transcript via API, run Haiku extraction locally
7. **SSE monitoring**: Stream events for real-time progress tracking
8. **Mid-execution steering**: Send user.message events for PM-to-Dev steering

**Integration effort estimate**: 2-3 weeks for a proof-of-concept; 4-6 weeks for production.

### Perplexity Computer Integration (Low Viability)

Perplexity Computer cannot be programmatically orchestrated by Valor:

- No API to create tasks programmatically
- No SSE streaming of task progress
- No custom tool injection for memory augmentation
- No vault-equivalent for secret management
- No mid-execution steering
- Credit-based pricing makes cost management difficult

The Perplexity Agent API is a separate product and does not control Computer.

### Perplexity Agent API Integration (Supplementary Only)

The Agent API could supplement Valor as a **research tool**:

- Real-time web search with citations
- Multi-provider model access
- Custom function calling
- Pay-as-you-go pricing

This would be useful for PLAN and CRITIQUE stages where current web information is valuable, but it is not an execution backend.

---

## 14. Recommendation

### Primary: Adopt Managed Agents as Valor's execution backend

MA is the clear choice for Valor's needs:

1. **API-first design** maps directly to Valor's orchestration model
2. **Custom tools** enable the stealth memory injection pattern
3. **Container isolation** replaces local worktree management
4. **SSE streaming** enables real-time monitoring and steering
5. **Predictable pricing** at ~\$65-165/month for Valor's workload
6. **Security model** (vaults, network config) meets requirements

### Secondary: Evaluate Perplexity Agent API for research tasks

The Agent API could improve the PLAN and CRITIQUE stages with real-time web search and multi-provider model access. This is a low-risk, low-effort addition that does not affect the core execution architecture.

### Do Not: Adopt Perplexity Computer as an execution backend

Perplexity Computer is not designed for programmatic orchestration. It solves a different problem (autonomous digital worker for end users) and lacks the API surface needed for Valor's architecture. Attempting to use it would require workarounds (Slack bot integration, screen scraping the UI) that are fragile and defeat the purpose.

### Decision Matrix

| Criterion | Weight | MA Score | Perplexity Computer Score |
|-----------|--------|----------|--------------------------|
| Programmatic orchestration | 30% | 10 | 2 |
| Code execution capability | 20% | 10 | 5 |
| Cost predictability | 15% | 9 | 4 |
| Memory integration | 15% | 8 | 2 |
| Web browsing capability | 10% | 5 | 10 |
| Session management | 10% | 9 | 4 |
| **Weighted total** | 100% | **8.9** | **3.7** |

---

## 15. What to Watch

| Signal | Impact | Urgency |
|--------|--------|---------|
| **Perplexity Computer API** | Would make Computer viable for orchestration | Watch |
| **MA visual browser tool** | Would close the web browsing gap | Medium |
| **MA memory GA** | Would simplify memory architecture | High |
| **Perplexity Agent API maturity** | Better research tool for PLAN/CRITIQUE | Low |
| **MA hook-equivalent API** | Makes subconscious memory fully portable | High -- the real unlock |
| **Perplexity Computer pricing transparency** | Better cost comparison | Low |
| **MA multi-agent GA** | Could subsume PM/Dev session split | Medium |

### Key Trigger Points

1. **If Perplexity ships a Computer API**: Re-evaluate for hybrid use (MA for code, Computer for web research)
2. **If MA ships visual browser**: Perplexity Computer becomes irrelevant for Valor
3. **If MA memory + hooks ship**: Full migration to MA becomes viable

---

## Appendix A: Product Comparison Table

| Feature | Claude Managed Agents | Perplexity Computer | Perplexity Agent API |
|---------|----------------------|---------------------|---------------------|
| **Launch** | April 2026 (beta) | February 2026 | March 2026 |
| **Pricing** | Per-ms + per-token + per-search | \$200/mo (Max) + credits | Pay-per-request |
| **API access** | Full REST API | UI only | Full REST API |
| **Models** | Claude family only | 19+ models (Claude, GPT, Gemini, Grok) | Multi-provider |
| **Execution** | Linux container | Cloud VM + visual browser | API responses only |
| **Shell** | Full bash | Not documented | None |
| **File ops** | read/write/edit/glob/grep | FUSE filesystem | None |
| **Code execution** | Any language | Python, JS, SQL | None |
| **Web search** | Text results | Multi-model research with citations | Text results with citations |
| **Browser** | None (install your own) | Full visual browser | None |
| **Custom tools** | JSON schema + client execution | Via Agent API | Function calling |
| **MCP** | Native support | Local + remote | Not documented |
| **Memory** | Research preview | Production knowledge graph | None |
| **Streaming** | SSE | None (programmatic) | Streaming supported |
| **Steering** | user.message mid-execution | UI only | N/A |
| **Secrets** | Vault (env var injection) | Integration credentials | API key |
| **SDKs** | Python, TS, Go, Java, C#, Ruby, PHP | None for Computer | Python, TypeScript |

## Appendix B: Perplexity Product Family

Perplexity has expanded beyond search into a family of AI products:

| Product | What It Is | Target |
|---------|-----------|--------|
| **Perplexity Search** | AI-powered search engine | Everyone |
| **Perplexity Pro** | Enhanced search with model selection | Power users (\$20/mo) |
| **Perplexity Max** | Pro + Computer access | Knowledge workers (\$200/mo) |
| **Computer** | Cloud-based multi-model agent | Business tasks |
| **Personal Computer** | Mac mini hardware + always-on agent | Always-on workflows |
| **Computer for Enterprise** | Computer + SCIM, audit, Snowflake | Organizations (\$325/seat/mo) |
| **Comet** | AI-native browser | Browsing with AI assistance |
| **Agent API** | Developer API for search + tools | Developers (pay-per-request) |
| **Sandbox API** | Isolated code execution for agents | Developers |

## Appendix C: Unconfirmed Details

The following details could not be verified from public documentation and should be confirmed through direct testing or vendor contact:

1. **Perplexity Computer credit consumption per task type** -- no published conversion table
2. **Perplexity Computer overage pricing** -- unclear what happens when credits are exhausted
3. **MA mid-execution steering behavior** -- docs say "send additional user events to guide the agent mid-execution" but exact behavior during \`running\` state needs testing
4. **Perplexity Computer programmatic API roadmap** -- no public timeline
5. **MA memory store implementation details** -- research preview, limited documentation
6. **Perplexity Personal Computer availability and pricing** -- pricing for the Mac mini bundle unclear
7. **MA container cold start time** -- not documented, needs benchmarking
8. **Perplexity Agent API relationship to Computer** -- whether the Agent API will eventually control Computer tasks
