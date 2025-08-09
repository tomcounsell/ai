# AI System Rebuild - Complete Kickoff Process

## Overview
This document defines the complete kickoff process using the BMad Method to orchestrate the 8-week AI system rebuild.

## Available Command Structure

### BMad Team Fullstack Agents
- **bmad-orchestrator**: Master orchestrator for workflow coordination
- **analyst** (Mary): Business analysis and documentation review
- **pm** (Sarah): Project management and sprint planning
- **ux-expert**: User experience and interface design
- **architect**: Technical architecture and system design
- **po**: Product ownership and backlog management

### Workflow Options
Since this is a **brownfield project** (rebuilding existing system):
- `brownfield-fullstack.yaml`: Full-stack enhancement workflow
- `brownfield-service.yaml`: Service-only workflow
- `brownfield-ui.yaml`: UI-only workflow

## 🚀 COMPLETE KICKOFF SEQUENCE

### Step 1: Initialize BMad Orchestrator
```bash
# Activate the BMad orchestrator
/BMad:agents:bmad-orchestrator
```

Expected Response: BMad Orchestrator introduces itself and offers help command.

### Step 2: Start Brownfield Fullstack Workflow
```bash
# Since we're rebuilding an existing system with comprehensive docs
*workflow brownfield-fullstack
```

The orchestrator will:
1. Classify enhancement scope (Major Enhancement - 8 week rebuild)
2. Check documentation (Already complete in docs-rebuild/)
3. Route to comprehensive planning workflow

### Step 3: Activate PM as Master Coordinator
```bash
# Transform into PM agent for master coordination
*agent pm

# Once Sarah (PM) is active, provide the Phase 0 brief:
"Sarah, you are the Master PM for the AI system rebuild. 
Your brief is at: docs-rebuild/phase-0/master-pm-brief.md
The complete 350+ task checklist is at: docs-rebuild/README.md (lines 133-1040)
Begin Phase 1 implementation coordination."
```

### Step 4: Brief All Agents in Sequence

#### 4.1 Technical Lead Brief
```bash
*agent architect
"Review docs-rebuild/phase-0/agent-briefs/developer-brief.md
You're co-leading Phase 1-2 with focus on system architecture.
Coordinate with PM (Sarah) for task assignments."
```

#### 4.2 Analyst Brief
```bash
*agent analyst
"Review docs-rebuild/ documentation suite.
Support requirements clarification and documentation updates.
Report to PM (Sarah) for coordination."
```

#### 4.3 UX Expert Brief (for Phase 5)
```bash
*agent ux-expert
"Review Phase 5 requirements in docs-rebuild/README.md
Focus on message formatting and Telegram UI optimization.
Standby for Phase 5 activation."
```

### Step 5: Execute Phase 1 Tasks

#### 5.1 Create Sprint Plan
```bash
*agent pm
*task create-sprint-plan

# Provide context:
"Create Week 1 sprint from Phase 1 tasks in docs-rebuild/README.md:
- Phase 1.1: Project Structure & Configuration (lines 137-149)
- Phase 1.2: Core Dependencies Installation (lines 151-170)
- Phase 1.3: Configuration Management (lines 172-187)
Target: Complete Phase 1.1-1.3 by end of Week 1"
```

#### 5.2 Begin Implementation
```bash
# Switch to development mode
*agent architect
*task setup-project-structure

# Or use orchestrator to coordinate multiple agents:
*party-mode
"All agents: Review your Phase 1 assignments from docs-rebuild/README.md
Architect: Setup project structure (1.1)
PM: Track progress and dependencies
Analyst: Document decisions and rationale"
```

## 📋 Alternative: Direct Command Sequence

For rapid execution without workflow guidance:

```bash
# 1. Activate orchestrator
/BMad:agents:bmad-orchestrator

# 2. Activate PM directly
*agent pm

# 3. Load project context
"Load context from docs-rebuild/ - we're rebuilding the AI system.
Review phase-0/master-pm-brief.md for your role as Master PM.
The implementation checklist has 350+ tasks across 8 phases."

# 4. Create implementation plan
*plan
"Create 8-week implementation plan from docs-rebuild/README.md checklist.
Break down into weekly sprints with clear deliverables."

# 5. Start Phase 1
*task create-sprint-plan
"Focus on Phase 1: Core Infrastructure (Week 1-2)"

# 6. Coordinate agents
*party-mode
"Team meeting: Beginning AI system rebuild Phase 1.
Each agent review your assignments and confirm readiness."
```

## 🎯 Success Criteria for Kickoff

- [ ] BMad Orchestrator activated
- [ ] PM (Sarah) established as Master Coordinator
- [ ] All agents briefed on their roles
- [ ] Phase 1 sprint plan created
- [ ] Development environment initialized
- [ ] First daily standup completed
- [ ] Progress tracking started

## 📊 Monitoring Progress

### Using BMad Commands
```bash
# Check current status
*status

# View plan progress
*plan-status

# Update completed tasks
*plan-update

# Switch between agents as needed
*agent [name]

# Return to orchestrator
*exit
```

### Daily Workflow
```bash
# Morning standup
*agent pm
*task daily-standup

# Check blockers
*status

# Assign tasks
*task assign-tasks

# Evening report
*task daily-report
```

## 🚨 Important Notes

1. **Commands require * prefix**: All BMad commands must start with asterisk
2. **Agent persistence**: Agents maintain context within session
3. **Documentation access**: All agents can read docs-rebuild/ directory
4. **Task tracking**: Use *plan-status to monitor progress
5. **Workflow flexibility**: Can switch between agents anytime with *agent

## 🎬 Quick Start Command

To begin immediately, run this single compound command:

```bash
/BMad:agents:bmad-orchestrator && *agent pm && echo "Load docs-rebuild/phase-0/master-pm-brief.md. You're Master PM for 8-week AI rebuild. Begin Phase 1 from docs-rebuild/README.md checklist."
```

This will:
1. Activate BMad Orchestrator
2. Transform to PM agent (Sarah)  
3. Provide context for immediate action

## Next Steps After Kickoff

1. **Phase 1 Execution**: Follow checklist items 137-240
2. **Daily Standups**: 9 AM coordination meetings
3. **Quality Gates**: Enforce before phase progression
4. **Documentation Updates**: Maintain as you build
5. **Progress Reporting**: Daily updates to stakeholders