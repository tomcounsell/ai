# Development TODO

## Tool Audit TODO

This section tracks the comprehensive audit of all PydanticAI tools in the system. Each tool will be audited according to the standards defined in [tool_auditing.md](./tool_auditing.md).

### Overview

**Total Tools Identified**: 59
- **Agent Tools** (integrated with valor_agent): 9
- **Standalone Tools** (in /tools/ directory): 16  
- **MCP Tools** (across 4 MCP servers): 34

**Architecture Note**: Many tools exist in multiple layers (Agent → MCP → Standalone) creating duplication that needs consolidation.

**Audit Status Legend**:
- 🔴 **Not Started** - No audit performed
- 🟡 **In Progress** - Audit partially completed
- 🟢 **Completed** - Full audit completed and approved
- ⚠️ **Issues Found** - Audit completed, issues need resolution
- ✅ **Approved** - Audit completed, tool approved for production

### Agent Tools (PydanticAI @valor_agent.tool)

- [x] **search_current_info** ✅ - Web search using Perplexity AI (HIGH PRIORITY)
- [x] **create_image** ✅ - DALL-E 3 image generation with Telegram integration (HIGH)
- [x] **analyze_shared_image** ✅ - AI vision analysis of uploaded images (HIGH)
- [x] **delegate_coding_task** ✅ - Development task delegation (CRITICAL - recently fixed)
- [x] **save_link_for_later** ✅ - URL analysis and storage (MEDIUM)
- [x] **search_saved_links** ✅ - Search through saved links (MEDIUM)
- [x] **query_notion_projects** ✅ - PsyOPTIMAL workspace queries (HIGH)
- [x] **search_conversation_history** ✅ - Search Telegram conversation history (MEDIUM)
- [x] **get_conversation_context** ✅ - Extended conversation context (MEDIUM)

### Standalone Tools (/tools/ directory)

#### Core Implementation Tools (Support Agent Tools)
- [x] **search_tool.py** ✅ - Web search implementation (HIGH - supports search_current_info)
- [ ] **image_generation_tool.py** 🔴 - DALL-E 3 implementation (HIGH - supports create_image)
- [ ] **image_analysis_tool.py** 🔴 - AI vision implementation (HIGH - supports analyze_shared_image)
- [x] **valor_delegation_tool.py** ✅ - Delegation implementation (CRITICAL - recently fixed)
- [ ] **notion_tool.py** 🔴 - Notion workspace integration (HIGH - supports query_notion_projects)
- [ ] **link_analysis_tool.py** 🔴 - URL analysis implementation (MEDIUM - supports link tools)
- [ ] **telegram_history_tool.py** 🔴 - Conversation history implementation (MEDIUM - supports conversation tools)

#### Development & Quality Tools
- [ ] **documentation_tool.py** 🔴 - Document analysis functionality (MEDIUM)
- [ ] **linting_tool.py** 🔴 - Code quality/linting tools (HIGH)
- [ ] **test_judge_tool.py** 🔴 - AI-powered test evaluation (MEDIUM)
- [ ] **test_params_tool.py** 🔴 - Test parameter generation (MEDIUM)
- [ ] **minimal_judge.py** 🔴 - Simple AI evaluation utility (LOW)

#### Infrastructure & Support
- [ ] **models.py** 🔴 - Tool infrastructure and base models (HIGH)
- [ ] **image_tagging_tool.py** 🔴 - Image tagging functionality (LOW)
- [ ] **doc_summary_tool.py** 🔴 - Document summarization (LOW)

### MCP Tools (34 tools across 4 servers)

#### Social Tools MCP (5 tools) - Core User Features
- [ ] **social_tools.py** 🔴 - search_current_info, create_image, analyze_shared_image, save_link, search_links (HIGH)

#### Telegram Tools MCP (4 tools) - Conversation Management  
- [ ] **telegram_tools.py** 🔴 - search_conversation_history, get_conversation_context, get_recent_history, list_telegram_dialogs (MEDIUM)

#### Notion Tools MCP (3 tools) - Workspace Integration
- [ ] **notion_tools.py** 🔴 - query_notion_projects, list_notion_workspaces, validate_workspace_access (HIGH)

#### Development Tools MCP (22 tools) - Development Workflow
- [ ] **development_tools.py** 🔴 - Complex server with testing, linting, docs, images, project tools (HIGH - **SPLIT RECOMMENDED**)

### Sprint 1: MCP Tools Priority (Week 1)  
**Focus**: Audit MCP tools first to identify consolidation opportunities

1. **social_tools.py** (MCP) - HIGH - Contains 5 core user tools (search, images, links)
2. **notion_tools.py** (MCP) - HIGH - Contains 3 workspace integration tools  
3. **telegram_tools.py** (MCP) - MEDIUM - Contains 4 conversation tools (already some audited)
4. **development_tools.py** (MCP) - HIGH - Contains 22 development tools (**SPLIT NEEDED**)

**Strategy**: Audit MCP tools first, then identify which standalone tools are redundant

### Sprint 2: Standalone Tool Consolidation (Week 2)
**Focus**: Audit standalone tools and eliminate redundancies found in Sprint 1

1. **search_tool.py** - Review against MCP social_tools findings
2. **image_generation_tool.py** - Review against MCP social_tools findings  
3. **image_analysis_tool.py** - Review against MCP social_tools findings
4. **notion_tool.py** - Review against MCP notion_tools findings
5. **Core infrastructure tools** - models.py, linting_tool.py

**Estimated Total Effort**: 25-35 hours (across both sprints)

## Architecture Consolidation Plan

### Critical Duplication Issues
**FOUND**: Multiple implementations of the same functionality across Agent → MCP → Standalone layers

#### Priority 1: Eliminate Major Duplications (HIGH PRIORITY)
- [ ] **Search Tools**: 3 implementations (Agent + MCP + Standalone) → Consolidate to MCP primary
  - `search_current_info` (agent) + `search_current_info` (MCP) + `search_tool.py` (standalone)
- [ ] **Image Generation**: 3 implementations → Consolidate to MCP primary  
  - `create_image` (agent) + `create_image` (MCP) + `image_generation_tool.py` (standalone)
- [ ] **Image Analysis**: 3 implementations → Consolidate to MCP primary
  - `analyze_shared_image` (agent) + `analyze_shared_image` (MCP) + `image_analysis_tool.py` (standalone)  
- [ ] **Notion Queries**: 3 implementations → Consolidate to MCP primary
  - `query_notion_projects` (agent) + `query_notion_projects` (MCP) + `notion_tool.py` (standalone)
- [ ] **Link Management**: 2 agent tools + MCP tools + standalone → Consolidate architecture
  - `save_link_for_later` + `search_saved_links` (agents) + MCP tools + `link_analysis_tool.py`

#### Priority 2: Split Oversized Components (MEDIUM PRIORITY)  
- [ ] **Development Tools MCP** (22 tools) → Split into focused servers:
  - **Testing Tools MCP**: test generation + judging (6 tools)
  - **Code Quality MCP**: linting and formatting (4 tools)  
  - **Document Processing MCP**: summarization and analysis (5 tools)
  - **Image Processing MCP**: advanced analysis features (5 tools)
  - **Project Management MCP**: context and workspace tools (2 tools)

#### Priority 3: Resolve Architecture Inconsistencies (LOW PRIORITY)
- [ ] **Telegram Tools**: Clean up overlapping context vs history functionality
- [ ] **Delegate Tools**: Ensure single responsibility between delegation variants
- [ ] **Infrastructure Tools**: Consolidate models.py and utility functions

### Recommended Audit Strategy
1. **Audit MCP tools first** (they're the primary implementations)
2. **Identify redundant standalone tools** during MCP audits  
3. **Verify agent tools delegate properly** to MCP implementations
4. **Plan deprecation/consolidation** of unnecessary duplicates

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
