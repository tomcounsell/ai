# Notion PM Integration Rebuild Plan

## Overview

Rebuild the Notion integration from the ground up to focus on Project Management (PM) tasks for the Valor agent. The goal is for Valor to intelligently check PM tasks in Notion to know what's assigned to him and see what the rest of the dev team is working on.

## Current State Analysis

### Problems with Current System
- **Outdated "scout" terminology** - Not aligned with PM-focused purpose
- **Keyword triggers** - Hardcoded pattern matching instead of intelligent decision-making
- **Redundant workspace resolution** - Manual alias checking when chats are already mapped to workspaces
- **Generic querying** - Not focused on PM tasks and assignments

### Current Architecture Issues
- `notion_scout.py` with heavy "scout" terminology throughout
- Keyword trigger functions in `/integrations/telegram/utils.py`:
  - `is_notion_question()` with hardcoded project names
  - `is_user_priority_question()` with rigid pattern matching
- Manual workspace resolution in Telegram handlers
- Complex alias system for CLI that's not needed for PM workflows

## New PM-Focused Design

### Core Concept
- **Purpose**: Valor agent checks PM tasks in Notion to know what's assigned to him and see what the dev team is working on
- **Trigger**: Agent intelligence determines when to check PM tasks (no keyword triggers)
- **Scope**: Each workspace maps to a specific Notion database (PM todo list)
- **Focus**: Task assignment visibility and team status

### Key Principles
1. **Intelligent Decision Making**: Valor agent determines PM task relevance based on conversation context
2. **Workspace Isolation**: Each chat is already tied to its specific workspace via `config/workspace_config.json`
3. **PM-Focused Interface**: Functions specifically designed for assignment status and team visibility
4. **Simplified Architecture**: Remove redundant complexity and focus on core PM needs

## New Architecture

### 1. File Restructuring
```
/integrations/notion/
├── pm_engine.py          # Renamed from query_engine.py, PM-focused
├── task_manager.py       # New: Core PM task management functions
└── utils.py              # Simplified workspace utilities

/agents/
├── pm_task_manager.py    # Renamed from notion_scout.py, PM CLI tool
└── valor/                # Valor agent integration

/mcp_servers/
└── pm_tools.py           # Updated MCP tools for PM workflows
```

### 2. New PM-Focused Interface
```python
# Core PM functions
def get_my_assigned_tasks(workspace_name: str) -> str:
    """Get tasks assigned to Valor in this workspace"""
    
def get_team_status(workspace_name: str) -> str:
    """Get what the dev team is working on"""
    
def check_pm_dashboard(workspace_name: str, context: str) -> str:
    """Intelligent PM task analysis based on conversation context"""

def get_task_assignments(workspace_name: str, assignee: str = None) -> str:
    """Get task assignments for specific team member or all"""

def check_sprint_status(workspace_name: str) -> str:
    """Get current sprint/milestone status"""
```

### 3. MCP Tool Updates
```python
@mcp.tool()
def check_pm_tasks(workspace_name: str, focus: str = "assignments", chat_id: str = "") -> str:
    """Check PM tasks with focus on assignments, team status, or sprint progress.
    
    Focus options:
    - "assignments" - Tasks assigned to Valor
    - "team" - What the dev team is working on  
    - "sprint" - Current sprint/milestone status
    - "all" - Comprehensive PM dashboard
    """
```

### 4. Agent Integration Pattern
```python
# Valor agent integration
class ValorAgent:
    def should_check_pm_tasks(self, message_context: str) -> bool:
        """Intelligent determination of when to check PM tasks"""
        # LLM-based decision making, no keyword triggers
        
    def get_pm_context(self, workspace_name: str, conversation_context: str) -> str:
        """Get relevant PM context based on conversation"""
        # Contextual PM task retrieval
```

## Implementation Steps

### Phase 1: Remove Legacy System
- [x] Remove keyword trigger functions (`is_notion_question`, `is_user_priority_question`)
- [x] Remove hardcoded project name checking in Telegram handlers
- [ ] Rename `notion_scout.py` to `pm_task_manager.py`
- [ ] Remove "scout" terminology throughout codebase
- [ ] Clean up alias system (keep only for CLI convenience)

### Phase 2: Create PM-Focused Core
- [ ] Create `task_manager.py` with PM-specific functions
- [ ] Refactor `query_engine.py` to `pm_engine.py` with PM focus
- [ ] Implement assignment-focused querying
- [ ] Add team status visibility functions

### Phase 3: Update Integration Points
- [ ] Update MCP tools in `pm_tools.py` for PM workflows
- [ ] Integrate PM functions with Valor agent
- [ ] Update workspace configuration for PM-specific metadata
- [ ] Remove redundant workspace resolution logic

### Phase 4: Testing & Validation
- [ ] Test PM task assignment queries
- [ ] Validate team status visibility
- [ ] Test Valor agent's intelligent PM task checking
- [ ] Verify workspace isolation in PM context

## Expected Outcomes

### User Experience
1. **Natural Interaction**: Valor intelligently knows when to check PM tasks
2. **Assignment Awareness**: Clear visibility into what's assigned to Valor
3. **Team Coordination**: Understanding of what teammates are working on
4. **Context-Aware**: PM task checking based on conversation relevance

### Technical Benefits
1. **Simplified Architecture**: Remove keyword triggers and redundant logic
2. **Focused Interface**: PM-specific functions instead of generic querying
3. **Intelligent Decision Making**: LLM-driven relevance detection
4. **Workspace Isolation**: Proper chat-to-workspace mapping usage

## Migration Notes

### Backward Compatibility
- CLI tools maintain workspace aliases for user convenience
- MCP tools provide clear migration path from old to new functions
- Workspace configuration remains unchanged (already consolidated)

### Configuration Changes
- No changes to `workspace_config.json` required
- Environment variables remain the same
- Workspace-to-database mapping stays intact

## Success Criteria

1. **Valor Agent Intelligence**: Agent correctly determines when to check PM tasks without keyword triggers
2. **Assignment Clarity**: Clear visibility into Valor's assigned tasks
3. **Team Awareness**: Understanding of team member work status
4. **Simplified Codebase**: Removal of redundant logic and keyword triggers
5. **Maintained Functionality**: All current PM query capabilities preserved with improved focus

This rebuild transforms the Notion integration from a generic "scouting" system into a focused PM task management tool that aligns with Valor's role and the team's workflow needs.