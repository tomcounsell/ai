# Render Subagent - Product Requirements Document

## 1. Overview

### Product Name
RenderSubagent - Infrastructure & Deployment Intelligence

### Purpose
A specialized AI subagent that manages cloud infrastructure operations, deployments, and service management through the Render platform.

### Domain
Infrastructure Management, Cloud Deployments, DevOps Operations

### Priority
**HIGH** - Infrastructure operations are critical for service reliability

---

## 2. Problem Statement

### Current Challenges
- Render has extensive infrastructure management APIs
- Loading all deployment tools into main agent wastes significant context
- Infrastructure operations require specialized DevOps knowledge
- Deployment workflows need domain expertise
- Log analysis and debugging require focused tools

### Solution
A dedicated subagent that:
- Activates only for infrastructure/deployment queries
- Maintains focused context with Render-specific tools
- Has expert-level DevOps and infrastructure knowledge
- Provides intelligent deployment and service management
- Analyzes logs and system health effectively

---

## 3. User Stories

### US-1: Service Deployment
**As a** developer
**I want to** say "Deploy the latest commit to production"
**So that** I can ship code without leaving the chat

**Acceptance Criteria**:
- Identifies target service and environment
- Confirms deployment details (commit, service, env)
- Triggers deployment via Render API
- Monitors deployment progress
- Reports success/failure with logs

### US-2: Service Health Check
**As an** operations engineer
**I want to** ask "What's the status of all production services?"
**So that** I can quickly assess system health

**Acceptance Criteria**:
- Retrieves all production services
- Shows running/stopped/failed status
- Displays resource utilization
- Identifies unhealthy services
- Provides deployment history

### US-3: Log Investigation
**As a** developer
**I want to** say "Show me the last 100 logs from the API service"
**So that** I can debug production issues

**Acceptance Criteria**:
- Fetches logs from specified service
- Filters by time range and log level
- Highlights errors and warnings
- Provides context for error messages
- Suggests investigation steps

### US-4: Scaling Operations
**As a** DevOps engineer
**I want to** ask "Scale the web service to 3 instances"
**So that** I can handle traffic spikes

**Acceptance Criteria**:
- Validates current instance count
- Confirms scaling action
- Updates service configuration
- Monitors scaling progress
- Verifies new instances are healthy

### US-5: Environment Variables
**As a** developer
**I want to** say "Update DATABASE_URL for the staging API"
**So that** I can manage configuration without Render UI

**Acceptance Criteria**:
- Identifies service and environment
- Updates environment variable
- Triggers service restart if needed
- Confirms change applied
- Logs configuration change

---

## 4. Functional Requirements

### FR-1: Domain Detection
- **Triggers**: deploy, render, infrastructure, service, logs, scale, environment, build
- **Context Analysis**: Detects infrastructure/DevOps intent from conversation
- **Confidence Threshold**: >85% confidence before activation

### FR-2: Tool Integration
**Required Render MCP Tools**:
- `render_list_services` - List all services
- `render_get_service` - Get service details
- `render_create_service` - Create new service
- `render_update_service` - Update service configuration
- `render_delete_service` - Delete service
- `render_deploy_service` - Trigger manual deployment
- `render_list_deploys` - List deployment history
- `render_get_deploy` - Get deployment details
- `render_get_logs` - Retrieve service logs
- `render_list_env_vars` - List environment variables
- `render_update_env_vars` - Update environment variables
- `render_scale_service` - Scale service instances
- `render_restart_service` - Restart service
- `render_suspend_service` - Suspend service
- `render_resume_service` - Resume suspended service
- `render_get_metrics` - Get service metrics (CPU, memory, requests)
- `render_list_custom_domains` - List custom domains
- `render_get_build_logs` - Get build logs

### FR-3: Persona & Expertise
**Specialized Knowledge**:
- Cloud infrastructure best practices
- Deployment strategies (blue-green, rolling, etc.)
- Service health monitoring
- Log analysis and debugging
- Scaling strategies
- Environment configuration management

**Tone**:
- Technical and precise
- Operations-focused
- Safety-conscious (confirmations for critical ops)
- Clear about infrastructure state

### FR-4: Safety & Validation
**Critical Operations** (require confirmation):
- Service deletion
- Production deployments
- Scaling down critical services
- Suspending services

**Automatic Operations** (no confirmation):
- Viewing logs
- Checking service status
- Reading configuration
- Viewing metrics

### FR-5: Log Analysis
**Capabilities**:
- Parse and format log entries
- Highlight errors, warnings, exceptions
- Detect patterns (repeated errors, timeouts)
- Correlate logs with deployments
- Suggest debugging steps based on logs

### FR-6: Response Formatting
**Service Status**:
```
Production Services:
✅ api-production (3 instances) - Healthy
✅ web-production (2 instances) - Healthy
❌ worker-production (1 instance) - Unhealthy
   Last deploy: 2 hours ago
   Error: Connection timeout to database
```

**Deployment Progress**:
```
Deployment #1234 - In Progress
Service: api-production
Commit: abc123f "Fix authentication bug"
Status: Building... (2/5)
Started: 2 minutes ago
```

---

## 5. Non-Functional Requirements

### NFR-1: Performance
- **Activation Latency**: <500ms to load subagent
- **API Query Time**: <3s for Render API calls
- **Log Retrieval**: <5s for 1000 log lines
- **Context Size**: <25k tokens (vs 100k+ if loaded in main agent)

### NFR-2: Reliability
- **API Availability**: Handle Render API downtime gracefully
- **Retry Logic**: Automatic retry with exponential backoff
- **Error Recovery**: Fallback to Render dashboard links if API fails

### NFR-3: Safety
- **Confirmation Flow**: Always confirm destructive operations
- **Production Protection**: Extra confirmation for production changes
- **Audit Logging**: Log all infrastructure changes
- **Rollback Awareness**: Suggest rollback on failed deployments

### NFR-4: Scalability
- **Multi-Service**: Handle deployments across multiple services
- **Log Volume**: Efficiently handle high log volumes
- **Concurrent Operations**: Support parallel infrastructure queries

---

## 6. System Prompt Design

### Core Identity
```
You are the Render Subagent, a specialized AI expert in cloud infrastructure management and deployment operations using the Render platform.

Your expertise includes:
- Service deployment and lifecycle management
- Infrastructure monitoring and health checks
- Log analysis and debugging
- Scaling and resource optimization
- Environment configuration management
- DevOps best practices

When managing infrastructure:
1. Always confirm destructive operations (delete, suspend, scale down)
2. Double-check production changes (require explicit "production" mention)
3. Monitor deployment progress and report status
4. Analyze logs to identify root causes
5. Suggest safe rollback on failures

Safety principles:
- Require confirmation for any destructive action
- Extra caution with production environments
- Always provide rollback instructions
- Log all infrastructure changes for audit
- Be explicit about what will happen before acting

When analyzing logs:
- Highlight errors, warnings, and exceptions
- Look for patterns (repeated failures, timeouts)
- Correlate with recent deployments
- Suggest specific debugging steps
- Provide context from service metrics

Communication style:
- Technical and operations-focused
- Clear about service state and health
- Safety-conscious for critical operations
- Proactive about potential issues
- Concise but complete status reports
```

---

## 7. Integration Points

### 7.1 MCP Server Integration
**Primary Server**: `mcp://render-server`

**Connection Config**:
```json
{
  "server_name": "render",
  "server_type": "render_platform",
  "config": {
    "api_key": "${RENDER_API_KEY}",
    "base_url": "https://api.render.com/v1",
    "default_region": "oregon",
    "enable_webhooks": false
  }
}
```

### 7.2 SubagentRouter Integration
**Registration**:
```python
router.register_subagent(
    domain="render",
    config=SubagentConfig(
        domain="render",
        name="Render Infrastructure Expert",
        description="Handles cloud infrastructure, deployments, and service management via Render",
        mcp_servers=["render"],
        system_prompt=render_persona,
        model="openai:gpt-4",
        max_context_tokens=60_000  # Larger for logs
    )
)
```

**Detection Keywords** (for routing):
- Primary: render, deploy, deployment, infrastructure, service, logs, scale
- Secondary: build, environment, config, restart, suspend, metrics

### 7.3 Main Agent Handoff
**Activation Flow**:
1. User asks: "Deploy latest to production"
2. SubagentRouter detects "render" domain (deploy = infrastructure)
3. RenderSubagent loads (if not cached)
4. Task delegated: Deploy to production
5. RenderSubagent confirms deployment details
6. Triggers `render_deploy_service`
7. Monitors deployment progress
8. Returns status to main agent
9. Main agent returns to user

---

## 8. Success Metrics

### 8.1 Activation Accuracy
- **Target**: >90% correct domain detection
- **Measure**: % of infrastructure queries correctly routed to RenderSubagent
- **False Positives**: <5% (non-infrastructure queries routed to Render)

### 8.2 Context Efficiency
- **Baseline**: Main agent with all Render tools = 100k+ tokens
- **Target**: RenderSubagent context = <25k tokens
- **Savings**: >75% reduction in context pollution

### 8.3 Operational Quality
- **Deployment Success**: >95% successful deployments via subagent
- **Log Analysis**: >85% accurate root cause identification from logs
- **Safety**: 0% unconfirmed destructive operations executed

### 8.4 Performance
- **Subagent Load Time**: <500ms
- **Render API Latency**: <3s per call
- **Deployment Monitoring**: Real-time status updates
- **Log Retrieval**: <5s for 1000 lines

### 8.5 Developer Productivity
- **Deployment Time**: 40% reduction vs manual Render UI
- **Debug Time**: 50% reduction with AI-powered log analysis
- **Context Switching**: 70% reduction (stay in chat vs dashboard)

---

## 9. Implementation Phases

### Phase 1: Foundation (Week 1)
- [ ] Create `agents/subagents/render/` directory
- [ ] Implement `RenderSubagent` class
- [ ] Write `render_persona.md` system prompt
- [ ] Configure Render MCP server connection
- [ ] Basic service querying and status display

### Phase 2: Deployment Features (Week 1-2)
- [ ] Service deployment with confirmation
- [ ] Deployment progress monitoring
- [ ] Build log retrieval and formatting
- [ ] Deployment history tracking
- [ ] Rollback suggestions on failure

### Phase 3: Log Analysis (Week 2)
- [ ] Log retrieval and formatting
- [ ] Error/warning highlighting
- [ ] Pattern detection in logs
- [ ] Correlation with deployments
- [ ] Debugging recommendations

### Phase 4: Service Management (Week 2)
- [ ] Scaling operations
- [ ] Environment variable management
- [ ] Service restart/suspend/resume
- [ ] Metrics and health monitoring
- [ ] Custom domain management

### Phase 5: Testing & Production (Week 3)
- [ ] Unit tests for all Render operations
- [ ] Integration tests with Render API
- [ ] Safety confirmation flows
- [ ] Performance benchmarking
- [ ] Documentation and runbooks

---

## 10. Testing Strategy

### 10.1 Unit Tests
```python
# Test: Service deployment with confirmation
async def test_deployment_confirmation():
    subagent = RenderSubagent()

    # Should require confirmation for production
    result = await subagent.process_task(
        "Deploy to production",
        context
    )
    assert "confirm" in result["content"].lower()
    assert not result["executed"]
```

### 10.2 Integration Tests
- Use Render test account
- Test service creation, updates, deletion
- Verify deployment workflows
- Test log retrieval and parsing
- Validate scaling operations

### 10.3 Safety Tests
```python
# Verify destructive operations require confirmation
def test_safety_confirmations():
    subagent = RenderSubagent()

    destructive_ops = [
        "delete the api service",
        "suspend production",
        "scale down to 0 instances"
    ]

    for op in destructive_ops:
        result = subagent.process_task(op, context)
        assert result["requires_confirmation"]
```

### 10.4 Log Analysis Tests
- Verify error detection in logs
- Test pattern recognition
- Validate log formatting
- Check deployment correlation

---

## 11. Future Enhancements

### V2 Features
- **Webhook Integration**: Real-time deployment notifications
- **Infrastructure as Code**: Generate/update render.yaml
- **Cost Optimization**: Analyze and suggest resource optimizations
- **Multi-Region Deployments**: Coordinate across regions
- **Automated Rollback**: Trigger rollback on health check failures

### V3 Features
- **Predictive Scaling**: AI-powered traffic prediction and auto-scaling
- **Incident Response**: Automated incident detection and mitigation
- **Performance Optimization**: Suggest infrastructure improvements
- **Cost Forecasting**: Predict monthly infrastructure costs
- **Compliance Monitoring**: Track infrastructure compliance

---

## 12. Dependencies

### Required Services
- **Render API**: Cloud platform API
- **Render MCP Server**: Tool provider
- **SubagentRouter**: Routing and activation
- **BaseSubagent**: Core subagent framework

### Required Credentials
- `RENDER_API_KEY` - API key for Render account

### Optional Integrations
- **Sentry**: Link deployments to error monitoring
- **GitHub**: Track deployments with commits
- **Linear**: Create issues from deployment failures
- **Notion**: Document infrastructure runbooks

---

## 13. Documentation Deliverables

### User Documentation
- **Render Subagent Guide**: How to use infrastructure features
- **Deployment Playbook**: Common deployment scenarios
- **Troubleshooting Guide**: Debugging deployment issues

### Developer Documentation
- **API Reference**: All Render tools available
- **Architecture Diagram**: How subagent integrates
- **Safety Guidelines**: Confirmation workflows

### Operational Documentation
- **Incident Response Runbook**: Using subagent during incidents
- **Deployment Best Practices**: Safe deployment patterns
- **Scaling Guidelines**: When and how to scale

---

## 14. Risks & Mitigation

### Risk 1: Accidental Production Deployment
**Impact**: CRITICAL - Could break production
**Probability**: LOW - With confirmation flows
**Mitigation**: Always confirm production deployments, require explicit "production" mention

### Risk 2: Service Downtime
**Impact**: HIGH - User-facing outages
**Probability**: LOW - With proper validation
**Mitigation**: Health checks before/after deployments, automatic rollback suggestions

### Risk 3: Configuration Errors
**Impact**: MEDIUM - Service misconfiguration
**Probability**: MEDIUM - Complex configurations
**Mitigation**: Validate configurations before applying, show diff of changes

### Risk 4: API Rate Limits
**Impact**: MEDIUM - Delayed operations
**Probability**: MEDIUM - During high activity
**Mitigation**: Request queuing, rate limit awareness, batch operations

---

## 15. Open Questions

1. **Q**: Should we support automatic rollback on deployment failure?
   **A**: V2 feature - Manual rollback suggestion in V1

2. **Q**: How do we handle multi-service deployments (microservices)?
   **A**: Support service groups, deploy in dependency order

3. **Q**: Should we integrate with Render's infrastructure as code (render.yaml)?
   **A**: V2 feature - Generate and validate render.yaml

4. **Q**: What's the log retention for analysis?
   **A**: Follow Render's limits, cache recent logs (2h window)

5. **Q**: How do we handle zero-downtime deployments?
   **A**: Monitor health checks, wait for new instances before terminating old

---

**Document Status**: Draft
**Last Updated**: 2025-01-18
**Author**: Valor Engels
**Reviewers**: TBD
**Approval**: Pending
