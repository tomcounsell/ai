# Sentry Subagent - Product Requirements Document

## 1. Overview

### Product Name
SentrySubagent - Error Monitoring & Performance Intelligence

### Purpose
A specialized AI subagent that manages error tracking, performance monitoring, and application health insights through Sentry's observability platform.

### Domain
Application Monitoring, Error Tracking, Performance Analysis

### Priority
**HIGH** - System health and error resolution are critical for reliability

---

## 2. Problem Statement

### Current Challenges
- Sentry has complex error grouping, stack traces, and performance data
- Loading all Sentry tools into main agent bloats context significantly
- Debugging requires specialized understanding of error patterns
- Performance analysis needs domain expertise
- Alert triage requires quick access to Sentry data

### Solution
A dedicated subagent that:
- Activates only for monitoring/debugging queries
- Maintains focused context with Sentry-specific tools
- Has expert-level error analysis capabilities
- Provides actionable debugging insights
- Triages alerts intelligently

---

## 3. User Stories

### US-1: Error Investigation
**As a** developer
**I want to** ask "What's causing the 500 errors in production?"
**So that** I can quickly diagnose and fix issues

**Acceptance Criteria**:
- Retrieves recent 500 errors from Sentry
- Identifies error patterns and common stack traces
- Shows affected users and frequency
- Suggests likely root cause

### US-2: Performance Analysis
**As a** engineering lead
**I want to** say "Show me the slowest endpoints this week"
**So that** I can prioritize performance optimization

**Acceptance Criteria**:
- Queries Sentry Performance data
- Ranks endpoints by p95 latency
- Shows transaction volume and trends
- Identifies performance regressions

### US-3: Alert Triage
**As an** on-call engineer
**I want to** ask "Summarize the new Sentry alerts"
**So that** I can triage incidents efficiently

**Acceptance Criteria**:
- Fetches unresolved alerts
- Groups by severity and impact
- Provides context for each alert
- Suggests priority order

### US-4: Issue Resolution Tracking
**As a** product manager
**I want to** say "How many critical bugs were fixed last sprint?"
**So that** I can track engineering quality metrics

**Acceptance Criteria**:
- Queries resolved Sentry issues
- Filters by severity and timeframe
- Shows resolution time metrics
- Provides trend analysis

### US-5: User Impact Assessment
**As a** support lead
**I want to** ask "How many users are affected by error #12345?"
**So that** I can assess customer impact

**Acceptance Criteria**:
- Retrieves issue details from Sentry
- Shows unique user count
- Displays geographic distribution
- Identifies high-value customers affected

---

## 4. Functional Requirements

### FR-1: Domain Detection
- **Triggers**: error, bug, sentry, crash, exception, performance, monitoring, alert
- **Context Analysis**: Detects debugging/monitoring intent from conversation
- **Confidence Threshold**: >85% confidence before activation

### FR-2: Tool Integration
**Required Sentry MCP Tools**:
- `sentry_list_issues` - List issues with filters
- `sentry_retrieve_issue` - Get detailed issue information
- `sentry_update_issue` - Update issue status/assignment
- `sentry_resolve_issue` - Mark issues as resolved
- `sentry_list_events` - List error events
- `sentry_retrieve_event` - Get event details with stack trace
- `sentry_list_projects` - List Sentry projects
- `sentry_get_project_stats` - Get project-level statistics
- `sentry_list_releases` - List releases
- `sentry_get_release_health` - Get release health metrics
- `sentry_list_alerts` - List active alerts
- `sentry_retrieve_alert` - Get alert details
- `sentry_get_performance_data` - Query performance metrics
- `sentry_list_transactions` - List transaction performance
- `sentry_get_user_feedback` - Retrieve user-reported feedback

### FR-3: Persona & Expertise
**Specialized Knowledge**:
- Stack trace interpretation
- Error pattern recognition
- Performance profiling
- Release health monitoring
- Alert configuration best practices
- Debugging methodologies

**Tone**:
- Technical and precise
- Diagnostic and analytical
- Solution-oriented
- Clear about severity and impact

### FR-4: Analysis Capabilities
**Error Analysis**:
- Stack trace parsing and explanation
- Error pattern identification
- Root cause hypothesis generation
- Similar issue detection
- Impact assessment (users, regions, versions)

**Performance Analysis**:
- Latency trend analysis
- Throughput metrics
- Resource utilization
- Bottleneck identification
- Regression detection

### FR-5: Response Formatting
**Error Reports**:
- Stack traces (formatted and explained)
- Error frequency charts (text-based)
- User impact metrics
- Severity indicators
- Resolution suggestions

**Performance Reports**:
- Latency percentiles (p50, p95, p99)
- Throughput metrics (req/sec)
- Error rates
- Apdex scores
- Trend indicators (↑↓→)

---

## 5. Non-Functional Requirements

### NFR-1: Performance
- **Activation Latency**: <500ms to load subagent
- **API Query Time**: <3s for Sentry API calls
- **Context Size**: <20k tokens (vs 100k+ if loaded in main agent)

### NFR-2: Reliability
- **API Availability**: Handle Sentry API downtime gracefully
- **Data Freshness**: Cache with 2min TTL for real-time insights
- **Error Recovery**: Fallback to Sentry web UI links if API fails

### NFR-3: Accuracy
- **Stack Trace Parsing**: 100% accurate extraction
- **Error Classification**: >90% correct severity assessment
- **Impact Calculation**: Accurate user/event counts

### NFR-4: Scalability
- **Concurrent Queries**: Handle multiple Sentry queries in parallel
- **Large Stack Traces**: Efficiently handle 500+ line stack traces
- **Historical Data**: Query up to 90 days of history

---

## 6. System Prompt Design

### Core Identity
```
You are the Sentry Subagent, a specialized AI expert in error monitoring, performance analysis, and application observability using the Sentry platform.

Your expertise includes:
- Stack trace interpretation and debugging
- Error pattern recognition and root cause analysis
- Performance profiling and optimization
- Release health monitoring
- Alert triage and incident response
- Application observability best practices

When investigating errors:
1. Parse stack traces to identify the exact failure point
2. Look for patterns across multiple events
3. Assess user impact and severity
4. Provide actionable debugging steps
5. Suggest fixes based on error type

When analyzing performance:
1. Focus on high-impact transactions (high volume or latency)
2. Compare against historical baselines
3. Identify regressions and trends
4. Recommend specific optimizations
5. Prioritize by user impact

Communication style:
- Technical and diagnostic
- Clear about severity (critical, high, medium, low)
- Action-oriented (always suggest next steps)
- Evidence-based (cite metrics and data)
- Collaborative (help developers debug, don't judge)

Never:
- Dismiss errors without investigation
- Ignore user impact data
- Provide vague debugging advice
- Overlook performance regressions
```

---

## 7. Integration Points

### 7.1 MCP Server Integration
**Primary Server**: `mcp://sentry-server`

**Connection Config**:
```json
{
  "server_name": "sentry",
  "server_type": "sentry_platform",
  "config": {
    "auth_token": "${SENTRY_AUTH_TOKEN}",
    "organization": "${SENTRY_ORG_SLUG}",
    "base_url": "https://sentry.io",
    "default_project": "${SENTRY_PROJECT_SLUG}",
    "max_events_per_query": 100
  }
}
```

### 7.2 SubagentRouter Integration
**Registration**:
```python
router.register_subagent(
    domain="sentry",
    config=SubagentConfig(
        domain="sentry",
        name="Sentry Error & Performance Expert",
        description="Handles error tracking, performance monitoring, and debugging via Sentry",
        mcp_servers=["sentry"],
        system_prompt=sentry_persona,
        model="openai:gpt-4",
        max_context_tokens=60_000  # Larger for stack traces
    )
)
```

**Detection Keywords** (for routing):
- Primary: sentry, error, bug, crash, exception, alert, monitoring
- Secondary: performance, latency, slow, timeout, 500, debug, trace

### 7.3 Main Agent Handoff
**Activation Flow**:
1. User asks: "Why is the checkout page crashing?"
2. SubagentRouter detects "sentry" domain (crash = error monitoring)
3. SentrySubagent loads (if not cached)
4. Task delegated: Investigate checkout crashes
5. SentrySubagent calls `sentry_list_issues` with filters
6. Analyzes stack traces and error patterns
7. Returns diagnostic report to main agent
8. Main agent returns to user

---

## 8. Success Metrics

### 8.1 Activation Accuracy
- **Target**: >92% correct domain detection
- **Measure**: % of error/monitoring queries correctly routed to SentrySubagent
- **False Positives**: <5% (non-error queries routed to Sentry)

### 8.2 Context Efficiency
- **Baseline**: Main agent with all Sentry tools = 100k+ tokens
- **Target**: SentrySubagent context = <20k tokens
- **Savings**: >80% reduction in context pollution

### 8.3 Diagnostic Quality
- **Root Cause Accuracy**: >85% correct root cause identification
- **Actionability**: >90% of responses include specific next steps
- **Completeness**: All critical data fields (stack trace, impact, frequency) included

### 8.4 Performance
- **Subagent Load Time**: <500ms
- **Sentry API Latency**: <3s per query
- **Stack Trace Parsing**: <1s for 500-line traces

### 8.5 Developer Productivity
- **Time to Root Cause**: 50% reduction vs manual Sentry UI navigation
- **Alert Triage Time**: 70% reduction with AI-powered summaries
- **Resolution Rate**: 20% increase in issues resolved on first attempt

---

## 9. Implementation Phases

### Phase 1: Foundation (Week 1)
- [ ] Create `agents/subagents/sentry/` directory
- [ ] Implement `SentrySubagent` class
- [ ] Write `sentry_persona.md` system prompt
- [ ] Configure Sentry MCP server connection
- [ ] Basic error querying and display

### Phase 2: Error Analysis (Week 1-2)
- [ ] Stack trace parsing and explanation
- [ ] Error pattern detection
- [ ] User impact calculation
- [ ] Similar issue detection
- [ ] Root cause suggestions

### Phase 3: Performance Features (Week 2)
- [ ] Performance data querying
- [ ] Latency analysis and trends
- [ ] Transaction ranking
- [ ] Regression detection
- [ ] Optimization recommendations

### Phase 4: Alert Management (Week 2)
- [ ] Alert retrieval and formatting
- [ ] Severity-based triage
- [ ] Alert summaries
- [ ] Resolution tracking

### Phase 5: Testing & Production (Week 3)
- [ ] Unit tests for all Sentry operations
- [ ] Integration tests with Sentry API
- [ ] Context isolation verification
- [ ] Performance benchmarking
- [ ] Documentation and runbooks

---

## 10. Testing Strategy

### 10.1 Unit Tests
```python
# Test: Stack trace parsing
async def test_stack_trace_analysis():
    subagent = SentrySubagent()
    result = await subagent.process_task(
        "Explain error issue #12345",
        context
    )
    assert "stack trace" in result["content"].lower()
    assert "root cause" in result["content"].lower()
```

### 10.2 Integration Tests
- Use Sentry test project
- Test error querying with various filters
- Verify performance data retrieval
- Test alert management workflows
- Validate stack trace formatting

### 10.3 Context Isolation Tests
```python
# Verify Sentry tools don't leak to main agent
def test_context_isolation():
    main_agent = ValorAgent()
    sentry_subagent = SentrySubagent()

    # Main agent should not have Sentry tools
    assert "sentry_list_issues" not in main_agent.tools

    # Sentry subagent should only have Sentry tools
    assert all(
        tool.startswith("sentry_")
        for tool in sentry_subagent.tools
    )
```

### 10.4 Analysis Quality Tests
- Verify root cause accuracy on known errors
- Test impact calculation with sample data
- Validate performance trend detection
- Check severity classification accuracy

---

## 11. Future Enhancements

### V2 Features
- **Automated Triage**: AI-powered alert prioritization and assignment
- **Issue Grouping**: Smart grouping of related errors
- **Release Correlation**: Correlate errors with deployments
- **User Journey Tracking**: Show error impact on user flows
- **Proactive Monitoring**: Detect anomalies before alerts fire

### V3 Features
- **Auto-Resolution**: Suggest code fixes for common errors
- **Performance Baselines**: Learn normal performance patterns
- **Predictive Alerting**: Forecast potential issues
- **Integration with GitHub**: Link errors to code changes
- **Custom Dashboards**: Generate tailored monitoring views

---

## 12. Dependencies

### Required Services
- **Sentry API**: Error monitoring platform
- **Sentry MCP Server**: Tool provider
- **SubagentRouter**: Routing and activation
- **BaseSubagent**: Core subagent framework

### Required Credentials
- `SENTRY_AUTH_TOKEN` - API token for Sentry organization
- `SENTRY_ORG_SLUG` - Organization identifier
- `SENTRY_PROJECT_SLUG` - Default project (optional)

### Optional Integrations
- **GitHub**: Link errors to code commits
- **Linear**: Create issues from Sentry errors
- **Notion**: Document error patterns and solutions
- **Slack**: Alert notifications (future)

---

## 13. Documentation Deliverables

### User Documentation
- **Sentry Subagent Guide**: How to use error monitoring features
- **Example Queries**: Common debugging use cases
- **Troubleshooting Guide**: Interpreting error reports

### Developer Documentation
- **API Reference**: All Sentry tools available
- **Architecture Diagram**: How subagent integrates
- **Error Classification Guide**: Understanding severity levels

### Operational Documentation
- **Incident Response Runbook**: Using Sentry subagent for on-call
- **Monitoring Best Practices**: Effective use of Sentry data
- **Alert Configuration**: Optimizing Sentry alerts

---

## 14. Risks & Mitigation

### Risk 1: Sentry API Rate Limits
**Impact**: MEDIUM - Delayed error data
**Probability**: MEDIUM - During incident spikes
**Mitigation**: Request queuing, caching, batch queries, pagination

### Risk 2: Large Stack Traces
**Impact**: MEDIUM - Context overflow
**Probability**: HIGH - Complex applications
**Mitigation**: Intelligent truncation, focus on relevant frames, summarization

### Risk 3: False Error Attribution
**Impact**: MEDIUM - Wrong root cause suggestions
**Probability**: LOW - With proper analysis
**Mitigation**: Always show evidence, avoid definitive claims, suggest verification steps

### Risk 4: Sensitive Data in Errors
**Impact**: HIGH - PII exposure in stack traces
**Probability**: MEDIUM - Poor error handling practices
**Mitigation**: Data scrubbing, PII detection, redaction warnings

---

## 15. Open Questions

1. **Q**: Should we integrate with Sentry's AI-powered grouping?
   **A**: YES - V2 feature, use Sentry's ML models

2. **Q**: How do we handle multi-project organizations?
   **A**: Support project selection in query or default to configured project

3. **Q**: Should we auto-resolve issues?
   **A**: NO - Only suggest resolution, require human confirmation

4. **Q**: What's the data retention for performance queries?
   **A**: Follow Sentry's plan limits (typically 90 days)

5. **Q**: How do we handle very large stack traces (1000+ lines)?
   **A**: Intelligently summarize, show only relevant frames, provide full trace link

---

**Document Status**: Draft
**Last Updated**: 2025-01-18
**Author**: Valor Engels
**Reviewers**: TBD
**Approval**: Pending
