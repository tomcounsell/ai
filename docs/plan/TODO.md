# Development TODO

## Tool Audit TODO

This section tracks the comprehensive audit of all PydanticAI tools in the system. Each tool will be audited according to the standards defined in [tool_auditing.md](./tool_auditing.md).

**✅ UPDATED FINDING**: After detailed architecture analysis, the "duplication crisis" is **partially resolved** through proper understanding of architectural patterns.

### Overview

**Total Tools Identified**: 58 (was 59, deleted unused `minimal_judge.py`)
- **Agent Tools** (integrated with valor_agent): 11
- **Standalone Tools** (in /tools/ directory): 13  
- **MCP Tools** (across 4 MCP servers): 35

**Architecture Clarification**: **Two distinct patterns identified**:
1. **✅ Good Architecture**: MCP tools as **wrappers** calling standalone implementations
2. **🔴 True Duplications**: Agent + MCP both implementing same logic without using standalone

**Audit Status Legend**:
- 🔴 **Not Started** - No audit performed
- 🟡 **In Progress** - Audit partially completed
- 🟢 **Completed** - Full audit completed and approved
- ⚠️ **Issues Found** - Audit completed, issues need resolution
- ✅ **Approved** - Audit completed, tool approved for production

### Agent Tools (PydanticAI @valor_agent.tool)

**🔴 DUPLICATION ALERT**: All these tools are FULLY DUPLICATED in MCP layer. Immediate consolidation required.

- [x] **search_current_info** ✅ 🔴 DUPLICATE (MCP social_tools) - Web search using Perplexity AI 
- [x] **create_image** ✅ 🔴 DUPLICATE (MCP social_tools) - DALL-E 3 image generation
- [x] **analyze_shared_image** ✅ 🔴 DUPLICATE (MCP social_tools) - AI vision analysis
- [x] **delegate_coding_task** ✅ 🟡 SIMILAR (MCP social_tools.technical_analysis) - Development delegation
- [x] **save_link_for_later** ✅ 🔴 DUPLICATE (MCP social_tools.save_link) - URL analysis/storage
- [x] **search_saved_links** ✅ 🔴 DUPLICATE (MCP social_tools.search_links) - Search saved links
- [x] **query_notion_projects** ✅ 🔴 DUPLICATE (MCP pm_tools) - Notion workspace queries
- [x] **search_conversation_history** ✅ 🔴 DUPLICATE (MCP telegram_tools) - Search Telegram history
- [x] **get_conversation_context** ✅ 🔴 DUPLICATE (MCP telegram_tools) - Extended conversation context
- [x] **read_project_documentation** ✅ 🔴 DUPLICATE (MCP development_tools) - Read project docs
- [x] **list_project_documentation** ✅ 🔴 DUPLICATE (MCP development_tools) - List documentation

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
- [ ] **image_tagging_tool.py** 🔴 ✅ **GOOD PATTERN** - MCP development_tools imports and calls this
- [x] **minimal_judge.py** ❌ **DELETED** - Was unused, removed from codebase

#### Infrastructure & Support
- [ ] **models.py** 🔴 🟢 **INFRASTRUCTURE** - Tool configuration models and base classes

### MCP Tools (35 tools across 4 servers) - **INTERFACE LAYER**

**✅ ARCHITECTURE STATUS**: MCP layer should be **interface wrappers** that call standalone implementations + add MCP-specific concerns.

#### Social Tools MCP (6 tools) - Core User Features
- [ ] **social_tools.py** 🔴 **REFACTOR NEEDED - BAD PATTERN**
  - search_current_info - 🔴 Should import from tools/search_tool.py
  - create_image - 🔴 Should import from tools/image_generation_tool.py 
  - analyze_shared_image - 🔴 Should import from tools/image_analysis_tool.py
  - save_link - 🔴 Should import from tools/link_analysis_tool.py
  - search_links - 🔴 Should import from tools/link_analysis_tool.py
  - technical_analysis - 🟡 Unique approach (Claude Code delegation)

#### PM Tools MCP (3 tools) - Project Management (formerly notion-tools)
- [ ] **pm_tools.py** 🟡 **INTEGRATION PATTERN - ACCEPTABLE**
  - query_notion_projects - ✅ Uses shared integrations.notion.query_engine
  - list_notion_workspaces - ✅ UNIQUE functionality
  - validate_workspace_access - ✅ UNIQUE functionality

#### Telegram Tools MCP (4 tools) - Conversation Management  
- [ ] **telegram_tools.py** 🔴 **REFACTOR NEEDED - BAD PATTERN**
  - search_conversation_history - 🔴 Should import from tools/telegram_history_tool.py
  - get_conversation_context - 🔴 Should import from tools/telegram_history_tool.py
  - get_recent_history - ✅ UNIQUE functionality
  - list_telegram_dialogs - ✅ UNIQUE functionality

#### Development Tools MCP (22 tools) - Development Workflow
- [ ] **development_tools.py** ✅ **EXCELLENT PATTERN - GOLD STANDARD**
  - ✅ **Perfect wrapper pattern** - imports all functions from standalone tools
  - ✅ **Adds MCP-specific concerns** - directory access validation, chat_id context
  - ✅ **Proper separation** - MCP handles interface, standalone handles logic
  - 🟡 **Oversized server** - consider splitting into focused servers

### REVISED PRIORITY: Architecture Pattern Cleanup (Week 1)
**✅ CLARITY ACHIEVED**: Focus on fixing **bad wrapper patterns** rather than "duplications"

#### Phase 1A: Remove Agent Tool Duplications (Day 1-2) - **UNCHANGED**
1. **Remove Agent @valor_agent.tool duplicates** - All 11 tools are true duplicates
   - All agent tools should be removed as they duplicate MCP functionality
   - Agent layer should focus on conversation flow, not tool implementation
   - **Target**: Remove all `@valor_agent.tool` decorators from `agents/valor/agent.py`

#### Phase 1B: Fix Bad MCP Wrapper Patterns (Day 3-5) - **REVISED**
1. **social_tools.py** - 🔴 **REFACTOR** - Make it import from standalone tools instead of reimplementing
2. **telegram_tools.py** - 🔴 **REFACTOR** - Make it import from standalone tools instead of reimplementing  
3. **development_tools.py** - ✅ **GOLD STANDARD** - Keep as reference for good wrapper pattern

#### Phase 1C: Validate Good Patterns (Day 5) - **NEW**
- **✅ Keep**: All standalone tools (they're the implementation layer)
- **✅ Validate**: development_tools.py wrapper pattern works correctly
- **✅ Document**: Good vs bad wrapper patterns for future development

### Sprint 2: Architecture Pattern Standardization (Week 2)
**Focus**: Standardize on **good wrapper patterns** across all MCP servers

1. **Apply development_tools.py pattern** to social_tools.py and telegram_tools.py
2. **Create architecture documentation** showing good vs bad wrapper patterns
3. **Update all tests** to validate the wrapper pattern works correctly
4. **Consider splitting oversized development_tools.py** (22 tools → 4-5 focused servers)

**Architecture Goal**: Agent Layer (conversation flow) → MCP Layer (interface wrappers) → Standalone Layer (implementations)

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

#### 🔴 True Duplications Identified (5 categories):
1. **Web Search**: Agent + bad MCP pattern (should import tools/search_tool.py)
2. **Image Generation**: Agent + bad MCP pattern (should import tools/image_generation_tool.py)
3. **Image Analysis**: Agent + bad MCP pattern (should import tools/image_analysis_tool.py)
4. **Link Management**: Agent + bad MCP pattern (should import tools/link_analysis_tool.py)
5. **Telegram History**: Agent + bad MCP pattern (should import tools/telegram_history_tool.py)

#### ✅ Good Patterns Identified:
- **Development Tools**: Perfect wrapper pattern with proper imports
- **PM Tools**: Acceptable integration pattern using shared services
- **Standalone Tools**: Proper implementation layer that MCP should call

### Corrected Action Plan

#### Phase 1: Remove Agent Layer Duplicates ⚡
- [x] **Architecture clarity**: Agent should focus on conversation, not tool implementation
- [ ] **Remove all Agent tools**: 11 @valor_agent.tool functions are true duplicates
- [ ] **Target**: Clean up `agents/valor/agent.py` to focus on conversation flow

#### Phase 2: Fix Bad MCP Patterns
- [ ] **Refactor social_tools.py**: Make it import from standalone tools (5 tools)
- [ ] **Refactor telegram_tools.py**: Make it import from standalone tools (2 tools)
- [ ] **Keep development_tools.py**: Use as gold standard reference

#### Phase 3: Pattern Standardization
- [ ] **Document patterns**: Create guide for good vs bad wrapper patterns
- [ ] **Update tests**: Validate wrapper pattern functionality
- [ ] **Consider server splitting**: development_tools.py may be too large

### Target Architecture (Corrected)
**Before**: Agent (11 duplicates) + MCP (mixed patterns) + Standalone (implementations) = 58 tools with pattern inconsistency
**After**: Agent (conversation flow) + MCP (consistent wrappers) + Standalone (implementations) = 58 tools with clear separation

## Test Coverage & Quality Improvements

### Critical Test Infrastructure Fixes
- [ ] **Fix module import paths in test_token_tracker.py** - ModuleNotFoundError for 'utilities' module
- [ ] **Resolve MCP test mocking issues**:
  - [ ] Fix context manager protocol for social_tools.search_links test
  - [ ] Add proper NotionQueryEngine import/mock in notion_tools tests
  - [ ] Add proper ChatHistoryManager import/mock in telegram_tools tests
- [ ] **Fix test assertion mismatches**:
  - [ ] Update Notion API key error message assertion to match actual output
  - [ ] Fix undefined function errors in test_context_injection.py (search_current_info)
  - [ ] Resolve KeyError for 'system_status' in integrated_system_validation

### Test Coverage Expansion
- [ ] **Environment-agnostic testing**:
  - [ ] Create test fixtures that work without Telegram API credentials
  - [ ] Add mock implementations for all external API dependencies
  - [ ] Separate unit tests from integration tests requiring live credentials
- [ ] **Core functionality validation**:
  - [ ] Add comprehensive tests for all MCP tools without external dependencies
  - [ ] Test context injection across all tool types with synthetic data
  - [ ] Add performance benchmarks for context optimization (97-99% compression)
- [ ] **Error handling coverage**:
  - [ ] Test graceful degradation when APIs are unavailable
  - [ ] Validate error recovery in multi-user scenarios
  - [ ] Test resource cleanup and monitoring edge cases

### Test Organization Improvements
- [ ] **Test categorization**:
  - [ ] Split tests into: unit/, integration/, e2e/ directories
  - [ ] Create fast test suite for CI/CD (no external APIs)
  - [ ] Create comprehensive test suite for full validation
- [ ] **Test utilities**:
  - [ ] Add shared test fixtures for common mock data
  - [ ] Create test database for isolated Notion testing
  - [ ] Add test chat history generator for Telegram testing

## Architecture Issues

## Planned Features

## Technical Debt

### Module Import Structure
- [ ] **Fix Python module paths**:
  - [ ] Ensure all tests can import utilities module correctly
  - [ ] Standardize import paths across test suite
  - [ ] Add __init__.py files where missing for proper package structure

### MCP Server Robustness
- [ ] **Improve error handling in MCP tools**:
  - [ ] Add better validation for missing API keys
  - [ ] Implement fallback responses when services unavailable
  - [ ] Add retry logic for transient failures

## Known Issues

### Test Suite Issues (Current)
- ❌ **token_tracker test**: ModuleNotFoundError for utilities module
- ❌ **MCP server tests**: 5/23 tests failing due to mocking issues
- ❌ **Context injection**: Undefined function references in workflow demo
- ❌ **System validation**: Missing system_status key in monitoring

### Performance Validation Needed
- [ ] **Benchmark actual vs. claimed performance**:
  - [ ] Validate 2.21s streaming intervals claim
  - [ ] Test 97-99% context compression efficiency
  - [ ] Measure tool execution latency (<1ms integration processing)
- [ ] **Load testing**:
  - [ ] Test 50+ simultaneous user support
  - [ ] Validate resource cleanup under load
  - [ ] Test memory efficiency during long conversations
