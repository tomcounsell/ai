# Development TODO

## 🚀 CURRENT FOCUS: Individual Tool Audits

**Status**: Architecture crisis resolved. Systematic audit process established. Focus on individual tool audits.

**Next Tool**: `models.py` (🔴 Not Started)

**Audit Command**: `project:audit-next-tool` (automatically selects and audits next tool)

**Progress**: 2/12 standalone tools audited (doc_summary_tool.py ✅ APPROVED, image_tagging_tool.py ✅ APPROVED)

---

## Tool Audit TODO

This section tracks the comprehensive audit of all PydanticAI tools in the system. Each tool will be audited according to the standards defined in [tool_auditing.md](./tool_auditing.md).

**✅ ARCHITECTURE CRISIS RESOLVED**: Systematic tool audit process established with clear consolidation strategy.

### Overview

**Total Tools Identified**: 57 (deleted unused `minimal_judge.py`)
- **Agent Tools** (integrated with valor_agent): 9 (reduced from 11 - removed doc tool duplicates)
- **Standalone Tools** (in /tools/ directory): 12  
- **MCP Tools** (across 4 MCP servers): 35

**Architecture Patterns Clarified**:
1. **✅ Gold Standard Pattern**: MCP tools as **wrappers** calling standalone implementations (development_tools.py)
2. **🔴 Bad Pattern**: MCP tools reimplementing standalone logic instead of importing
3. **🟡 Integration Pattern**: Both layers using shared services (acceptable)

**Audit Status Legend**:
- 🔴 **Not Started** - No audit performed
- 🟡 **In Progress** - Audit partially completed
- 🟢 **Completed** - Full audit completed and approved
- ⚠️ **Issues Found** - Audit completed, issues need resolution
- ✅ **Approved** - Audit completed, tool approved for production

### Agent Tools (PydanticAI @valor_agent.tool) - **CONSOLIDATION IN PROGRESS**

**✅ PARTIAL CONSOLIDATION COMPLETE**: Agent tool duplicates being systematically removed during individual tool audits.

- [x] **search_current_info** ✅ 🔴 DUPLICATE (MCP social_tools) - Web search using Perplexity AI 
- [x] **create_image** ✅ 🔴 DUPLICATE (MCP social_tools) - DALL-E 3 image generation
- [x] **analyze_shared_image** ✅ 🔴 DUPLICATE (MCP social_tools) - AI vision analysis
- [x] **delegate_coding_task** ✅ 🟡 SIMILAR (MCP social_tools.technical_analysis) - Development delegation
- [x] **save_link_for_later** ✅ 🔴 DUPLICATE (MCP social_tools.save_link) - URL analysis/storage
- [x] **search_saved_links** ✅ 🔴 DUPLICATE (MCP social_tools.search_links) - Search saved links
- [x] **query_notion_projects** ✅ 🔴 DUPLICATE (MCP pm_tools) - Notion workspace queries
- [x] **search_conversation_history** ✅ 🔴 DUPLICATE (MCP telegram_tools) - Search Telegram history
- [x] **get_conversation_context** ✅ 🔴 DUPLICATE (MCP telegram_tools) - Extended conversation context
- [x] **read_project_documentation** ✅ ❌ **REMOVED** - Consolidated to MCP development_tools during doc_summary_tool audit
- [x] **list_project_documentation** ✅ ❌ **REMOVED** - Consolidated to MCP development_tools during doc_summary_tool audit

### Standalone Tools (/tools/ directory) - **IMPLEMENTATION LAYER**

**✅ ARCHITECTURE CLARIFIED**: Standalone tools serve as the **implementation layer** that MCP tools should call.

#### Core Implementation Tools (TRUE DUPLICATIONS - Bad Pattern)
- [x] **search_tool.py** ✅ 🔴 TRUE DUPLICATE - MCP social_tools reimplements instead of calling
- [x] **image_generation_tool.py** ✅ 🔴 TRUE DUPLICATE - MCP social_tools reimplements instead of calling
- [x] **image_analysis_tool.py** ✅ 🔴 TRUE DUPLICATE - MCP social_tools reimplements instead of calling
- [x] **notion_tool.py** ✅ 🟡 INTEGRATION PATTERN - Both use shared integrations.notion.query_engine
- [x] **link_analysis_tool.py** ✅ 🔴 TRUE DUPLICATE - MCP social_tools reimplements instead of calling
- [x] **telegram_history_tool.py** ✅ 🔴 TRUE DUPLICATE - MCP telegram_tools reimplements instead of calling
- [x] **documentation_tool.py** ✅ 🟡 MIXED - Agent calls standalone, MCP has enhanced features

#### Development & Quality Tools (GOOD WRAPPER PATTERN)
- [x] **valor_delegation_tool.py** ✅ 🟡 SIMILAR to MCP social_tools.technical_analysis (different approaches)
- [x] **linting_tool.py** ✅ ✅ **GOOD PATTERN** - MCP development_tools imports and calls this
- [x] **test_judge_tool.py** ✅ ✅ **GOOD PATTERN** - MCP development_tools imports and calls this
- [x] **test_params_tool.py** ✅ ✅ **GOOD PATTERN** - MCP development_tools imports and calls this
- [x] **doc_summary_tool.py** ✅ ✅ **GOOD PATTERN** - MCP development_tools imports and calls this
- [x] **image_tagging_tool.py** ✅ ✅ **GOOD PATTERN** - MCP development_tools imports and calls this
- [x] **minimal_judge.py** ❌ **DELETED** - Was unused, removed from codebase

#### Infrastructure & Support
- [ ] **models.py** 🔴 🟢 **INFRASTRUCTURE** - Tool configuration models and base classes

### MCP Tools (35 tools across 4 servers) - **INTERFACE LAYER**

**✅ ARCHITECTURE STATUS**: MCP layer should be **interface wrappers** that call standalone implementations + add MCP-specific concerns.

#### Social Tools MCP (6 tools) - Core User Features
- [x] **social_tools.py** ✅ **GOLD STANDARD PATTERN - FIXED**
  - search_current_info - ✅ Now imports from tools/search_tool.py
  - create_image - ✅ Now imports from tools/image_generation_tool.py 
  - analyze_shared_image - ✅ Now imports from tools/image_analysis_tool.py
  - save_link - ✅ Now imports from tools/link_analysis_tool.py
  - search_links - ✅ Now imports from tools/link_analysis_tool.py
  - technical_analysis - ✅ Unique approach (Claude Code delegation)

#### PM Tools MCP (3 tools) - Project Management (formerly notion-tools)
- [ ] **pm_tools.py** 🟡 **INTEGRATION PATTERN - ACCEPTABLE**
  - query_notion_projects - ✅ Uses shared integrations.notion.query_engine
  - list_notion_workspaces - ✅ UNIQUE functionality
  - validate_workspace_access - ✅ UNIQUE functionality

#### Telegram Tools MCP (4 tools) - Conversation Management  
- [x] **telegram_tools.py** ✅ **GOLD STANDARD PATTERN - FIXED**
  - search_conversation_history - ✅ Now imports from tools/telegram_history_tool.py
  - get_conversation_context - ✅ Now imports from tools/telegram_history_tool.py
  - get_recent_history - ✅ UNIQUE functionality
  - list_telegram_dialogs - ✅ UNIQUE functionality

#### Development Tools MCP (22 tools) - Development Workflow
- [ ] **development_tools.py** ✅ **EXCELLENT PATTERN - GOLD STANDARD**
  - ✅ **Perfect wrapper pattern** - imports all functions from standalone tools
  - ✅ **Adds MCP-specific concerns** - directory access validation, chat_id context
  - ✅ **Proper separation** - MCP handles interface, standalone handles logic
  - 🟡 **Oversized server** - consider splitting into focused servers

### ⚡ CURRENT PRIORITY: Continue Individual Tool Audits

**✅ APPROACH ESTABLISHED**: Each tool audit systematically addresses duplications and architectural issues.

#### ✅ Completed Tool Audits:
1. **doc_summary_tool.py** - ✅ APPROVED (agent duplicates removed, security fixed, tests improved)

#### 🔴 Next Tools for Audit:
1. **image_tagging_tool.py** - 🔴 Not Started (follows good pattern, needs validation)
2. **models.py** - 🔴 Not Started (infrastructure tool, needs review)

#### Architecture Pattern Consolidation Status:
- **✅ doc_summary_tool**: Agent duplicates removed, MCP wrapper validated as gold standard
- **🔴 social_tools.py**: Needs refactor to import from standalone tools instead of reimplementing
- **🔴 telegram_tools.py**: Needs refactor to import from standalone tools instead of reimplementing  
- **✅ development_tools.py**: GOLD STANDARD - reference implementation for wrapper pattern

## Architecture Pattern Analysis

**Status**: ✅ COMPLETED - Architecture analysis reveals **pattern clarity**, not "duplication crisis"

### ✅ Pattern Classification

#### **Good Wrapper Pattern** (✅ development_tools.py)
```python
# MCP tool imports and calls standalone implementation
from tools.linting_tool import run_linting
@mcp.tool()
def lint_python_code(...params...) -> str:
    # Add MCP-specific concerns (validation, context)
    # Call standalone implementation
    # Format for MCP protocol
```

#### **Bad Reimplementation Pattern** (🔴 social_tools.py, telegram_tools.py)
```python
# MCP tool duplicates standalone logic instead of calling it
@mcp.tool()
def search_current_info(...) -> str:
    # Reimplements same logic as tools/search_tool.py
    # Creates maintenance overhead
```

#### **Integration Pattern** (🟡 pm_tools.py)
```python
# Both MCP and standalone use shared integration layer
from integrations.notion.query_engine import NotionQueryEngine
# Acceptable when tools access shared services differently
```

#### ✅ True Duplications FIXED (5 categories):
1. **Web Search**: ✅ MCP now imports tools/search_tool.py (GOLD STANDARD pattern)
2. **Image Generation**: ✅ MCP now imports tools/image_generation_tool.py (GOLD STANDARD pattern)
3. **Image Analysis**: ✅ MCP now imports tools/image_analysis_tool.py (GOLD STANDARD pattern)
4. **Link Management**: ✅ MCP now imports tools/link_analysis_tool.py (GOLD STANDARD pattern)
5. **Telegram History**: ✅ MCP now imports tools/telegram_history_tool.py (GOLD STANDARD pattern)

#### ✅ Good Patterns Identified:
- **Development Tools**: Perfect wrapper pattern with proper imports
- **PM Tools**: Acceptable integration pattern using shared services
- **Standalone Tools**: Proper implementation layer that MCP should call

### Current Status Summary

#### ✅ Completed Actions:
- **Architecture analysis**: Pattern clarity achieved, crisis resolved
- **Tool audit process**: Established systematic audit methodology
- **doc_summary_tool.py**: First tool fully audited and approved
- **Agent duplicate removal**: Beginning consolidation during individual audits

#### 🔴 Remaining Work:
- **Continue tool audits**: Process remaining standalone tools individually
- **MCP server refactoring**: Fix bad wrapper patterns in social_tools.py and telegram_tools.py
- **Test infrastructure**: Address MCP test mocking issues as tools are audited

### Target Architecture Progress
**Current**: Agent (9 remaining duplicates) + MCP (mixed patterns) + Standalone (implementations)
**Target**: Agent (conversation flow) + MCP (consistent wrappers) + Standalone (implementations)
**Progress**: 18% complete (2/11 agent duplicates removed via audits)

## Test Coverage & Quality Improvements - **ADDRESSED DURING TOOL AUDITS**

**✅ NEW APPROACH**: Test issues are systematically addressed during individual tool audits rather than as separate tasks.

### ✅ Test Improvements Made:
- **doc_summary_tool.py**: Fixed pytest fixture architecture, added security tests, resolved major failures

### 🔴 Test Issues to Address During Upcoming Audits:
- **Module import paths**: Fix utilities module imports when auditing affected tools
- **MCP test mocking**: Address during social_tools.py and telegram_tools.py audits  
- **Context injection testing**: Fix during individual tool audits that use context injection
- **Performance benchmarking**: Add during tools that claim specific performance metrics

## Technical Debt - **ADDRESSED DURING TOOL AUDITS**

**✅ SYSTEMATIC APPROACH**: Technical debt is addressed during individual tool audits rather than as standalone tasks.

### Technical Debt Categories:
- **Import Structure**: Fixed during tool audits when test failures occur
- **MCP Server Robustness**: Improved during MCP wrapper pattern refactoring
- **Performance Validation**: Added during audits of tools with performance claims
- **Error Handling**: Enhanced during security and reliability improvements

### Known Issues Status:
- **Test failures**: Systematically resolved during tool audits (doc_summary_tool: 15 errors → 0 errors)
- **Performance claims**: Will be validated during relevant tool audits
- **Module imports**: Fixed as-needed during individual tool testing
