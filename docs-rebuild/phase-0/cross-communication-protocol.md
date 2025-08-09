# Cross-Agent Communication Protocol

## Overview
This protocol ensures seamless coordination between all agents during the 8-week AI system rebuild.

## Communication Hierarchy

```
                    Sarah (Master PM)
                           |
        ┌─────────────────┼─────────────────┐
        |                 |                 |
    Phase Leads      Support Agents    Specialist Agents
        |
   Task Teams
```

## Communication Channels

### 1. Daily Standup Format
```yaml
DAILY STANDUP - [Date]
Agent: [Name]
Phase: [Current Phase]
Yesterday:
  - Completed: [List of completed tasks]
  - Blockers: [Any blockers encountered]
Today:
  - Planned: [Today's task list]
  - Dependencies: [Waiting on other agents]
Help Needed:
  - [Specific assistance required]
```

### 2. Task Handoff Protocol
```yaml
TASK HANDOFF
From: [Agent Name]
To: [Agent Name]
Task: [Description]
Deliverables:
  - [List of completed items]
  - [Location of artifacts]
Next Steps:
  - [What receiving agent needs to do]
Context:
  - [Important information to know]
```

### 3. Blocker Escalation
```yaml
BLOCKER ALERT
Agent: [Name]
Severity: [CRITICAL/HIGH/MEDIUM]
Blocked Task: [Task ID and description]
Blocking Issue: [Description of problem]
Attempted Solutions: [What was tried]
Help Required: [Specific assistance needed]
Impact: [Tasks/timeline affected]
```

### 4. Quality Gate Checkpoint
```yaml
QUALITY GATE - Phase [X]
Reviewing Agent: [Quinn/Quality Auditor]
Phase Lead: [Agent Name]
Criteria Met:
  - ✅ [Criterion 1]
  - ✅ [Criterion 2]
  - ❌ [Failed criterion]
Issues Found:
  - [List of issues]
Resolution Required:
  - [Actions needed to pass]
```

## Inter-Agent Dependencies

### Phase 1-2 Dependencies
```
Developer ←→ Infrastructure Engineer
    ├── Environment setup
    ├── Configuration management
    └── Database initialization

Developer ←→ Database Architect
    ├── Schema design
    └── Migration planning
```

### Phase 3-4 Dependencies
```
Tool Developer ←→ MCP Specialist
    ├── Tool registration
    ├── Context injection
    └── Stateless patterns

Tool Developer ←→ Quality Auditor
    ├── 9.8/10 standard validation
    └── Test coverage verification
```

### Phase 5-6 Dependencies
```
Integration Specialist ←→ Test Engineer
    ├── Component integration
    ├── E2E test scenarios
    └── Performance testing

UI/UX Specialist ←→ Integration Specialist
    ├── Response formatting
    └── User experience validation
```

### Phase 7-8 Dependencies
```
Infrastructure Engineer ←→ Migration Specialist
    ├── Deployment planning
    ├── Data migration
    └── Service transition

Security Reviewer ←→ All Agents
    ├── Security validation
    └── Compliance verification
```

## Collaboration Rules

### 1. Parallel Work Coordination
- Agents working on independent tasks should proceed without waiting
- Daily sync ensures alignment without blocking progress
- Shared resources must be coordinated through PM

### 2. Code Review Protocol
- All code requires review before phase completion
- Primary: Phase lead reviews
- Secondary: Quality auditor validates standards
- Critical: Security reviewer for sensitive components

### 3. Documentation Updates
- Each agent maintains documentation for their components
- Documentation Specialist reviews for consistency
- Updates committed daily with clear descriptions

### 4. Testing Coordination
- Developers write initial tests
- Test Engineer enhances test coverage
- QA Agent validates quality gates
- Performance Optimizer validates benchmarks

## Conflict Resolution

### Priority Conflicts
1. Refer to RICE-scored feature prioritization
2. PM makes final determination
3. Document decision rationale

### Technical Disagreements
1. Both parties present approach with pros/cons
2. Consult relevant specialist agent
3. PM facilitates decision based on project principles

### Resource Conflicts
1. Infrastructure Engineer assesses capacity
2. PM reallocates based on priority
3. Adjust timeline if necessary

## Progress Tracking

### Daily Metrics
- Tasks completed vs planned
- Blockers identified and resolved
- Test coverage percentage
- Quality gate status

### Weekly Reporting
```yaml
WEEKLY REPORT - Week [X]
Phase: [Current Phase]
Progress: [X]% complete
Completed:
  - [Major milestones]
Upcoming:
  - [Next week's goals]
Risks:
  - [Identified risks]
Metrics:
  - Tasks: [Completed]/[Total]
  - Quality: [Pass rate]
  - Timeline: [On track/At risk/Behind]
```

## Emergency Procedures

### Critical Blocker
1. Immediate escalation to PM
2. All hands meeting within 2 hours
3. Resource reallocation as needed
4. Timeline impact assessment

### Quality Gate Failure
1. Stop progression to next phase
2. Root cause analysis
3. Remediation plan
4. Re-test before proceeding

### Security Issue
1. Immediate stop work order
2. Security Reviewer assessment
3. Patch and validate
4. Document incident

## Success Criteria

### Communication Effectiveness
- Zero miscommunication incidents
- All handoffs documented
- Daily standups completed
- Blockers resolved within 24 hours

### Team Alignment
- All agents aware of current phase status
- Dependencies identified proactively
- Parallel work maximized
- Quality gates understood by all

### Delivery Excellence
- 8-week timeline maintained
- 350+ tasks completed
- 9.8/10 quality achieved
- Zero critical issues in production