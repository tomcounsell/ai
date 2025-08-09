#!/bin/bash
# AI System Rebuild - Quick Start Script
# This script provides the exact commands to kickoff the rebuild

echo "🚀 AI SYSTEM REBUILD - KICKOFF SCRIPT"
echo "====================================="
echo ""
echo "This script provides the commands to initialize the rebuild."
echo "Copy and paste these commands into your Claude session."
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "STEP 1: Activate BMad Orchestrator"
echo "-----------------------------------"
echo "/BMad:agents:bmad-orchestrator"
echo ""
echo "STEP 2: Transform to PM Agent (Sarah)"
echo "--------------------------------------"
echo "*agent pm"
echo ""
echo "STEP 3: Load Master PM Context"
echo "-------------------------------"
cat << 'EOF'
You are now Sarah, the Master Project Manager for the AI system rebuild.

Your primary resources:
1. Master PM Brief: docs-rebuild/phase-0/master-pm-brief.md
2. Complete Checklist (350+ tasks): docs-rebuild/README.md (lines 133-1040)
3. Implementation Strategy: docs-rebuild/rebuilding/implementation-strategy.md
4. Cross-Communication Protocol: docs-rebuild/phase-0/cross-communication-protocol.md

Your immediate tasks:
1. Review the Phase 1 checklist items (lines 137-240 in README.md)
2. Create Week 1 sprint plan
3. Brief the development team
4. Begin Phase 1: Core Infrastructure

Start by creating the sprint plan for Week 1, focusing on:
- Phase 1.1: Project Structure & Configuration
- Phase 1.2: Core Dependencies Installation  
- Phase 1.3: Configuration Management
- Phase 1.4: Database Layer Implementation

Assign tasks to:
- Developer (Sonny): Technical implementation
- Infrastructure Engineer: Environment setup
- Database Architect: Schema design
- QA (Quinn): Test framework setup

Begin with: "I'll create the Week 1 sprint plan for Phase 1: Core Infrastructure"
EOF
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "ALTERNATIVE: One-Line Quick Start"
echo "---------------------------------"
echo "Copy this entire command:"
echo ""
cat << 'EOF'
/BMad:agents:bmad-orchestrator && sleep 1 && echo "*agent pm" && echo "Load docs-rebuild/phase-0/master-pm-brief.md. You're Master PM for the 8-week AI rebuild. Review the 350+ task checklist in docs-rebuild/README.md (lines 133-1040). Create Week 1 sprint plan and begin Phase 1: Core Infrastructure."
EOF
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "MONITORING COMMANDS"
echo "-------------------"
echo "*status          - Check current context"
echo "*plan-status     - View plan progress"
echo "*plan-update     - Update task completion"
echo "*agent [name]    - Switch to different agent"
echo "*party-mode      - Multi-agent collaboration"
echo "*help           - Show all available commands"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "Ready to begin the rebuild! 🎯"