# AI System Rebuild - Agent Team

This directory contains specialized sub-agents designed to handle specific aspects of the AI system rebuild. Each agent has focused expertise and can be delegated specific tasks during the implementation process.

## Agent Overview

### 🏗️ Infrastructure & Foundation

**[database-architect](./database-architect.md)**
- SQLite schema design and optimization
- Data migration strategies
- Performance tuning and indexing
- Connection pooling and transaction management

**[infrastructure-engineer](./infrastructure-engineer.md)**
- Core infrastructure setup
- Monitoring and alerting systems
- Production deployment procedures
- Resource management and auto-restart

### 🧠 Core System Development

**[agent-architect](./agent-architect.md)**
- PydanticAI agent implementation
- Context management systems
- Conversational development patterns
- Tool orchestration framework

**[tool-developer](./tool-developer.md)**
- 9.8/10 gold standard tool implementation
- MCP server development
- Stateless tool design
- Error categorization systems

### 🔌 Integration & Communication

**[integration-specialist](./integration-specialist.md)**
- Telegram client implementation
- 5-step message processing pipeline
- External API integrations
- Communication architecture

### 🧪 Quality & Testing

**[test-engineer](./test-engineer.md)**
- Real integration testing (no mocks)
- AI judge implementation
- Performance benchmarking
- Test infrastructure setup

**[quality-auditor](./quality-auditor.md)**
- Code quality enforcement
- Architectural compliance
- Documentation review
- Performance validation

**[code-reviewer](./code-reviewer.md)**
- Code correctness and logic review
- Security vulnerability detection
- Standards compliance checking
- Constructive feedback

### 🎨 Design & Documentation

**[designer](./designer.md)**
- UI/UX implementation
- Design system adherence
- Accessibility compliance
- Component architecture

**[documentarian](./documentarian.md)**
- Documentation maintenance
- API reference writing
- Cross-referencing and discovery
- Keeping docs in sync with code

### 📋 Planning & Orchestration

**[plan-maker](./plan-maker.md)**
- Shape Up plan document creation
- Team orchestration design
- Task dependency mapping
- Agent type assignment for execution

### 🔄 Operations & Migration

**[migration-specialist](./migration-specialist.md)**
- Data migration planning
- Service transition strategies
- Configuration management
- Rollback procedures

## Delegation Strategy

### Phase-Based Delegation

**Phase 1: Core Infrastructure (Weeks 1-2)**
- Primary: `infrastructure-engineer`, `database-architect`
- Support: `quality-auditor`

**Phase 2: Agent Foundation (Weeks 2-3)**
- Primary: `agent-architect`
- Support: `quality-auditor`

**Phase 3: Tool Orchestration (Weeks 3-4)**
- Primary: `tool-developer`
- Support: `test-engineer`, `quality-auditor`

**Phase 4: MCP Integration (Weeks 4-5)**
- Primary: `tool-developer`
- Support: `integration-specialist`

**Phase 5: Communication Layer (Weeks 5-6)**
- Primary: `integration-specialist`
- Support: `test-engineer`

**Phase 6: Integration & Testing (Weeks 6-7)**
- Primary: `test-engineer`
- Support: All agents for their components

**Phase 7: Production Readiness (Weeks 7-8)**
- Primary: `infrastructure-engineer`, `migration-specialist`
- Support: `quality-auditor`

### Task-Based Delegation

When working on specific tasks, delegate to the appropriate specialist:

- **Database operations** → `database-architect`
- **API integrations** → `integration-specialist`
- **Tool creation** → `tool-developer`
- **Test implementation** → `test-engineer`
- **Code quality audit** → `quality-auditor`
- **Code review (PR)** → `code-reviewer`
- **Deployment setup** → `infrastructure-engineer`
- **Data migration** → `migration-specialist`
- **Agent design** → `agent-architect`
- **UI/UX work** → `designer`
- **Documentation** → `documentarian`
- **Plan creation** → `plan-maker` (NOT built-in `Plan` agent)

## Usage Examples

### Delegating to a Specific Agent

```
"Have the database-architect design the schema for the promises table with proper indexes and constraints"

"Ask the tool-developer to implement the web search tool following the 9.8/10 gold standard"

"Get the test-engineer to create E2E tests for the message processing pipeline"
```

### Multi-Agent Coordination

```
"Have the integration-specialist work with the test-engineer to create integration tests for the Telegram client"

"Get the infrastructure-engineer and migration-specialist to plan the production deployment strategy"
```

### Quality Gates

Before moving between phases, have the `quality-auditor` review:
- Code quality metrics
- Test coverage
- Documentation completeness
- Performance benchmarks

## Agent Communication

Agents share information through:
1. Documentation updates in `docs-rebuild/`
2. Code comments and docstrings
3. Test specifications
4. Architecture decision records

## Best Practices

1. **Single Responsibility**: Each agent focuses on their domain
2. **Clear Handoffs**: Document when transitioning between agents
3. **Quality First**: Always involve quality-auditor for reviews
4. **Test Everything**: test-engineer validates all components
5. **Document Decisions**: Update relevant documentation

## Success Metrics

Track agent effectiveness by:
- Task completion time
- Code quality scores
- Test coverage achieved
- Documentation completeness
- Performance benchmarks met

---

*Remember: These agents embody the "living codebase" philosophy - they ARE the system, not just builders of it.*