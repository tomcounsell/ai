# Linear Subagent - Product Requirements Document

## 1. Overview

### Product Name
LinearSubagent - Project Management & Issue Tracking Intelligence

### Purpose
A specialized AI subagent that manages product development workflows, issue tracking, sprint planning, and project coordination through the Linear platform.

### Domain
Project Management, Issue Tracking, Agile Workflows

### Priority
**HIGH** - Critical for product and engineering coordination

---

## 2. Problem Statement

### Current Challenges
- Linear has extensive project management APIs (issues, projects, cycles, roadmaps)
- Loading all Linear tools into main agent wastes significant context (12k+ tokens)
- Project management requires understanding of agile workflows
- Issue triage needs prioritization expertise
- Sprint planning requires team coordination knowledge

### Solution
A dedicated subagent that:
- Activates only for project management/issue tracking queries
- Maintains focused context with Linear-specific tools
- Has expert-level agile and project management knowledge
- Provides intelligent issue triage and sprint planning
- Efficiently manages projects, cycles, and roadmaps

---

## 3. User Stories

### US-1: Issue Creation
**As a** product manager
**I want to** say "Create a high-priority issue for the login bug"
**So that** I can track work without leaving chat

**Acceptance Criteria**:
- Creates Linear issue with appropriate details
- Sets priority based on context (high, medium, low)
- Assigns to relevant team/person if specified
- Adds to current sprint if applicable
- Provides issue link and identifier

### US-2: Sprint Planning
**As an** engineering lead
**I want to** ask "What's in the current sprint?"
**So that** I can review sprint scope quickly

**Acceptance Criteria**:
- Identifies current active cycle
- Lists all issues in cycle
- Groups by status (todo, in progress, done)
- Shows assignees and estimates
- Calculates sprint progress percentage

### US-3: Issue Triage
**As a** team lead
**I want to** say "Triage the new support issues"
**So that** I can prioritize and assign incoming work

**Acceptance Criteria**:
- Fetches unassigned/untriaged issues
- Suggests priority based on content
- Recommends assignees based on expertise
- Adds appropriate labels
- Confirms triage actions

### US-4: Roadmap Updates
**As a** product manager
**I want to** ask "Update the Q1 roadmap: mark auth as completed"
**So that** I can maintain project status

**Acceptance Criteria**:
- Identifies roadmap project
- Updates project status
- Moves related issues
- Updates timeline if needed
- Provides updated roadmap link

### US-5: Velocity Tracking
**As an** engineering manager
**I want to** say "What was our velocity last sprint?"
**So that** I can track team performance

**Acceptance Criteria**:
- Calculates completed issue points
- Compares to previous sprints
- Shows trend analysis
- Identifies blockers/delays
- Suggests capacity planning

---

## 4. Functional Requirements

### FR-1: Domain Detection
- **Triggers**: linear, issue, ticket, sprint, cycle, project, roadmap, backlog, task
- **Context Analysis**: Detects project management/tracking intent
- **Confidence Threshold**: >85% confidence before activation

### FR-2: Tool Integration
**Required Linear MCP Tools**:

**Issue Management**:
- `linear_list_issues` - List issues with filters
- `linear_get_issue` - Get issue details
- `linear_create_issue` - Create new issue
- `linear_update_issue` - Update issue (title, description, status)
- `linear_delete_issue` - Delete/archive issue
- `linear_add_issue_comment` - Comment on issue
- `linear_assign_issue` - Assign issue to user
- `linear_update_issue_priority` - Change priority
- `linear_update_issue_status` - Change status
- `linear_link_issues` - Link related issues

**Project & Roadmap**:
- `linear_list_projects` - List projects
- `linear_get_project` - Get project details
- `linear_create_project` - Create new project
- `linear_update_project` - Update project
- `linear_list_project_issues` - Get project issues
- `linear_add_issue_to_project` - Assign issue to project
- `linear_get_roadmap` - Get roadmap data

**Cycles (Sprints)**:
- `linear_list_cycles` - List cycles
- `linear_get_cycle` - Get cycle details
- `linear_create_cycle` - Create new cycle
- `linear_get_active_cycle` - Get current active cycle
- `linear_add_issue_to_cycle` - Add issue to cycle
- `linear_get_cycle_stats` - Get cycle metrics

**Team & Organization**:
- `linear_list_teams` - List teams
- `linear_get_team` - Get team details
- `linear_list_team_members` - List team members
- `linear_get_user` - Get user details

**Labels & Workflow**:
- `linear_list_labels` - List available labels
- `linear_create_label` - Create new label
- `linear_add_label_to_issue` - Tag issue
- `linear_list_workflow_states` - Get workflow states
- `linear_get_issue_history` - Get issue activity

### FR-3: Persona & Expertise
**Specialized Knowledge**:
- Agile methodologies (Scrum, Kanban)
- Issue prioritization frameworks (RICE, MoSCoW)
- Sprint planning and capacity management
- Roadmap planning and execution
- Team velocity and metrics
- Product development workflows

**Tone**:
- Organized and methodical
- Product-focused
- Team-coordination oriented
- Clear about priorities and status

### FR-4: Intelligence Features
**Smart Triage**:
- Analyze issue content to suggest priority
- Recommend assignee based on expertise/capacity
- Suggest labels based on issue description
- Identify duplicate or related issues
- Estimate effort if possible

**Sprint Planning**:
- Calculate team capacity
- Identify over/under allocation
- Suggest issue distribution
- Highlight dependencies
- Warn about scope creep

**Metrics & Analytics**:
- Calculate velocity (points/sprint)
- Track cycle time (creation to done)
- Identify bottlenecks (long in-progress)
- Measure team productivity
- Forecast completion dates

### FR-5: Response Formatting
**Sprint Overview**:
```
Current Sprint: Q1W3 (Jan 15-22)
Progress: 18/25 points (72%)

📋 Todo (3 issues, 7 points)
- LIN-123: Implement OAuth login [P1] @alice
- LIN-124: Add rate limiting [P2] @bob
- LIN-125: Update docs [P3] @charlie

🔄 In Progress (2 issues, 8 points)
- LIN-120: Fix payment bug [P0] @alice (3 days)
- LIN-121: API refactor [P1] @bob (2 days)

✅ Done (5 issues, 10 points)
- LIN-115: User dashboard [P1] @alice
- LIN-116: Email notifications [P2] @charlie
- [3 more...]

⚠️ Risks:
- LIN-120 overdue by 1 day
- Sprint capacity: 25 points, trending to 23
```

**Issue Details**:
```
LIN-456: Payment processing timeout

Priority: High (P1)
Status: In Progress
Assignee: @alice
Project: Payment Infrastructure
Cycle: Q1W3
Created: 2 days ago
Updated: 3 hours ago

Description:
Users experiencing timeouts during checkout when processing
payments over $1000. Affects ~5% of transactions.

Labels: bug, payments, urgent
Estimate: 5 points
Links:
- Blocks: LIN-457 (Refund automation)
- Related: SENTRY-789 (Error tracking)

Comments: 3
→ https://linear.app/issue/LIN-456
```

---

## 5. Non-Functional Requirements

### NFR-1: Performance
- **Activation Latency**: <500ms to load subagent
- **Issue Query**: <2s for filtered issue lists
- **Sprint Calculation**: <3s for complex metrics
- **Context Size**: <20k tokens (vs 100k+ if loaded in main agent)

### NFR-2: Reliability
- **API Availability**: Handle Linear API downtime gracefully
- **Data Consistency**: Always show latest issue state
- **Sync Accuracy**: Keep issue updates synchronized

### NFR-3: Accuracy
- **Priority Detection**: >85% correct priority suggestions
- **Assignee Matching**: >80% correct assignee recommendations
- **Velocity Calculation**: 100% accurate point calculations

### NFR-4: Scalability
- **Large Backlogs**: Handle 5k+ issues efficiently
- **Multiple Teams**: Support multi-team organizations
- **Concurrent Operations**: Support parallel issue updates

---

## 6. System Prompt Design

### Core Identity
```
You are the Linear Subagent, a specialized AI expert in project management, issue tracking, and agile development workflows using the Linear platform.

Your expertise includes:
- Agile methodologies (Scrum, Kanban, sprints)
- Issue triage and prioritization
- Sprint planning and capacity management
- Roadmap planning and execution
- Team velocity and productivity metrics
- Product development coordination

When managing issues:
1. Always set appropriate priority based on urgency/impact
2. Suggest assignees based on expertise and capacity
3. Add descriptive labels for categorization
4. Link related issues for context
5. Provide clear, actionable descriptions

When planning sprints:
- Calculate realistic team capacity
- Identify dependencies and blockers
- Balance priority with team availability
- Watch for scope creep
- Track progress and velocity

When analyzing metrics:
- Focus on actionable insights
- Compare trends over time
- Identify patterns and anomalies
- Suggest process improvements
- Be data-driven but context-aware

Prioritization framework:
- P0 (Critical): Production down, data loss, security breach
- P1 (High): Major feature broken, significant user impact
- P2 (Medium): Important but not urgent, planned work
- P3 (Low): Nice-to-have, technical debt, improvements

Communication style:
- Organized and systematic
- Clear about priorities and status
- Team-coordination focused
- Metric-driven but human-centered
- Proactive about blockers and risks

Best practices:
- Keep issue descriptions clear and actionable
- Maintain sprint scope discipline
- Track and communicate velocity
- Identify blockers early
- Balance feature work with technical debt
```

---

## 7. Integration Points

### 7.1 MCP Server Integration
**Primary Server**: `mcp://linear-server`

**Connection Config**:
```json
{
  "server_name": "linear",
  "server_type": "linear_platform",
  "config": {
    "api_key": "${LINEAR_API_KEY}",
    "organization_id": "${LINEAR_ORG_ID}",
    "default_team_id": "${LINEAR_TEAM_ID}",
    "enable_webhooks": false
  }
}
```

### 7.2 SubagentRouter Integration
**Registration**:
```python
router.register_subagent(
    domain="linear",
    config=SubagentConfig(
        domain="linear",
        name="Linear Project Management Expert",
        description="Handles issue tracking, sprint planning, and project management via Linear",
        mcp_servers=["linear"],
        system_prompt=linear_persona,
        model="openai:gpt-4",
        max_context_tokens=50_000
    )
)
```

**Detection Keywords** (for routing):
- Primary: linear, issue, ticket, sprint, cycle, project, roadmap
- Secondary: backlog, task, velocity, triage, planning, milestone

### 7.3 Main Agent Handoff
**Activation Flow**:
1. User asks: "What's blocking our current sprint?"
2. SubagentRouter detects "linear" domain (sprint = project management)
3. LinearSubagent loads (if not cached)
4. Task delegated: Find sprint blockers
5. LinearSubagent gets active cycle
6. Queries issues with "blocked" status or dependencies
7. Analyzes and formats blocker report
8. Returns to main agent
9. Main agent returns to user

---

## 8. Success Metrics

### 8.1 Activation Accuracy
- **Target**: >93% correct domain detection
- **Measure**: % of project management queries correctly routed to LinearSubagent
- **False Positives**: <4% (non-PM queries routed to Linear)

### 8.2 Context Efficiency
- **Baseline**: Main agent with all Linear tools = 100k+ tokens
- **Target**: LinearSubagent context = <20k tokens
- **Savings**: >80% reduction in context pollution

### 8.3 Triage Quality
- **Priority Accuracy**: >85% correct priority suggestions
- **Assignee Accuracy**: >80% correct assignee recommendations
- **Label Relevance**: >90% appropriate label suggestions

### 8.4 Performance
- **Subagent Load Time**: <500ms
- **Issue Query**: <2s for complex filters
- **Sprint Metrics**: <3s for velocity calculations

### 8.5 Team Productivity
- **Issue Creation Time**: 70% reduction vs Linear UI
- **Sprint Planning Time**: 50% reduction with AI assistance
- **Triage Time**: 60% reduction with smart suggestions

---

## 9. Implementation Phases

### Phase 1: Foundation (Week 1)
- [ ] Create `agents/subagents/linear/` directory
- [ ] Implement `LinearSubagent` class
- [ ] Write `linear_persona.md` system prompt
- [ ] Configure Linear MCP server connection
- [ ] Basic issue querying and display

### Phase 2: Issue Management (Week 1-2)
- [ ] Issue creation with smart defaults
- [ ] Priority and assignee suggestions
- [ ] Label management
- [ ] Issue linking and relationships
- [ ] Comment and update workflows

### Phase 3: Sprint Planning (Week 2)
- [ ] Cycle management
- [ ] Sprint capacity calculation
- [ ] Issue distribution suggestions
- [ ] Progress tracking
- [ ] Velocity metrics

### Phase 4: Project & Roadmap (Week 2)
- [ ] Project management
- [ ] Roadmap tracking
- [ ] Milestone planning
- [ ] Cross-project coordination
- [ ] Timeline management

### Phase 5: Testing & Production (Week 3)
- [ ] Unit tests for all Linear operations
- [ ] Integration tests with Linear API
- [ ] Triage accuracy validation
- [ ] Performance benchmarking
- [ ] Documentation and guides

---

## 10. Testing Strategy

### 10.1 Unit Tests
```python
# Test: Smart issue triage
async def test_issue_triage():
    subagent = LinearSubagent()
    result = await subagent.process_task(
        "Create issue: Production payment gateway is down",
        context
    )
    assert result["issue"]["priority"] == "P0"  # Should detect critical
    assert "urgent" in result["issue"]["labels"]
```

### 10.2 Integration Tests
- Use Linear test workspace
- Test issue CRUD operations
- Verify cycle management
- Test project workflows
- Validate metric calculations

### 10.3 Intelligence Tests
```python
# Verify smart assignee suggestion
def test_assignee_recommendation():
    subagent = LinearSubagent()

    # Issue about payments should suggest payments expert
    result = subagent.suggest_assignee(
        issue_description="Fix Stripe integration bug",
        team_expertise={"alice": ["payments", "stripe"]}
    )
    assert result["suggested_assignee"] == "alice"
```

### 10.4 Metrics Accuracy Tests
- Verify velocity calculations
- Test cycle time tracking
- Validate capacity planning
- Check trend analysis

---

## 11. Future Enhancements

### V2 Features
- **Automated Sprint Planning**: AI suggests sprint composition
- **Blocker Detection**: Proactively identify blockers before they delay
- **Capacity Forecasting**: Predict team capacity based on history
- **Smart Estimation**: Suggest issue estimates based on similar issues
- **Dependency Mapping**: Visualize issue dependencies

### V3 Features
- **Predictive Delays**: Forecast which issues will miss deadlines
- **Team Balancing**: Suggest issue redistribution for load balancing
- **Automated Triage**: Fully automated issue triage workflow
- **Process Insights**: Suggest process improvements from metrics
- **Integration Intelligence**: Smart linking between Linear and other tools

---

## 12. Dependencies

### Required Services
- **Linear API**: Project management platform
- **Linear MCP Server**: Tool provider
- **SubagentRouter**: Routing and activation
- **BaseSubagent**: Core subagent framework

### Required Credentials
- `LINEAR_API_KEY` - API key for Linear workspace
- `LINEAR_ORG_ID` - Organization identifier
- `LINEAR_TEAM_ID` - Default team (optional)

### Optional Integrations
- **GitHub**: Link issues to PRs and commits
- **Sentry**: Link errors to Linear issues
- **Notion**: Document project context
- **Slack**: Issue notifications and updates (future)

---

## 13. Documentation Deliverables

### User Documentation
- **Linear Subagent Guide**: How to use project management features
- **Sprint Planning Guide**: Effective sprint planning with AI
- **Triage Best Practices**: Optimizing issue triage

### Developer Documentation
- **API Reference**: All Linear tools available
- **Workflow Patterns**: Common project management workflows
- **Metrics Guide**: Understanding velocity and productivity metrics

### Operational Documentation
- **Project Management Runbook**: Team coordination best practices
- **Agile Process Guide**: Recommended agile workflows
- **Metrics Dashboard**: Key metrics to track

---

## 14. Risks & Mitigation

### Risk 1: Incorrect Priority Assignment
**Impact**: MEDIUM - Delays important work
**Probability**: MEDIUM - Context-dependent
**Mitigation**: Always allow user override, explain priority reasoning, learn from corrections

### Risk 2: Over-allocation in Sprints
**Impact**: MEDIUM - Team burnout, missed deadlines
**Probability**: MEDIUM - Hard to predict capacity
**Mitigation**: Conservative capacity estimates, track actual vs planned, warn on over-allocation

### Risk 3: Issue Duplication
**Impact**: LOW - Wasted effort
**Probability**: MEDIUM - Similar issues created
**Mitigation**: Search before create, suggest similar issues, allow merging

### Risk 4: API Rate Limits
**Impact**: MEDIUM - Delayed operations
**Probability**: LOW - Linear has generous limits
**Mitigation**: Request batching, caching, rate limit monitoring

---

## 15. Open Questions

1. **Q**: Should we support custom issue templates?
   **A**: YES - V1 feature, load from Linear workspace templates

2. **Q**: How do we handle multi-team coordination?
   **A**: Support team selection, show cross-team dependencies, coordinate sprints

3. **Q**: Should we auto-triage issues?
   **A**: V2 feature - Suggest triage in V1, automate in V2

4. **Q**: What's the strategy for archived issues?
   **A**: Exclude from default queries, allow explicit inclusion

5. **Q**: How do we handle custom workflows?
   **A**: Detect workflow states dynamically, adapt to team's process

---

**Document Status**: Draft
**Last Updated**: 2025-01-18
**Author**: Valor Engels
**Reviewers**: TBD
**Approval**: Pending
