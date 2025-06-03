# Development TODO

## Tool Audit TODO

This section tracks the comprehensive audit of all PydanticAI tools in the system. Each tool will be audited according to the standards defined in [tool_auditing.md](./tool_auditing.md).

**🚨 CRITICAL FINDING**: [Comprehensive Tool Analysis](../../comprehensive_tool_analysis.md) reveals massive duplication across all three layers.

### Overview

**Total Tools Identified**: 59
- **Agent Tools** (integrated with valor_agent): 11
- **Standalone Tools** (in /tools/ directory): 14  
- **MCP Tools** (across 4 MCP servers): 35

**Architecture Crisis**: 8 major tool categories are FULLY DUPLICATED across all 3 layers, creating massive maintenance overhead and resource waste.

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

### Standalone Tools (/tools/ directory)

**🔴 REDUNDANCY CRISIS**: Most standalone tools are now FULLY SUPERSEDED by MCP equivalents.

#### Core Implementation Tools (FULLY DUPLICATED)
- [x] **search_tool.py** ✅ 🔴 SUPERSEDED by MCP social_tools.search_current_info
- [x] **image_generation_tool.py** ✅ 🔴 SUPERSEDED by MCP social_tools.create_image
- [x] **image_analysis_tool.py** ✅ 🔴 SUPERSEDED by MCP social_tools.analyze_shared_image
- [x] **notion_tool.py** ✅ 🔴 SUPERSEDED by MCP pm_tools.query_notion_projects
- [x] **link_analysis_tool.py** ✅ 🔴 SUPERSEDED by MCP social_tools.save_link/search_links
- [x] **telegram_history_tool.py** ✅ 🔴 SUPERSEDED by MCP telegram_tools
- [x] **documentation_tool.py** ✅ 🔴 SUPERSEDED by MCP development_tools.read_documentation

#### Development & Quality Tools (PARTIALLY SUPERSEDED)
- [x] **valor_delegation_tool.py** ✅ 🟡 SIMILAR to MCP social_tools.technical_analysis (different approaches)
- [x] **linting_tool.py** ✅ 🟡 BASIC version of MCP development_tools comprehensive linting suite
- [x] **test_judge_tool.py** ✅ 🟡 BASIC version of MCP development_tools judge_ai_response
- [x] **test_params_tool.py** ✅ 🟡 BASIC version of MCP development_tools test parameter tools
- [ ] **doc_summary_tool.py** 🔴 🟡 BASIC version of MCP development_tools document summarization
- [ ] **image_tagging_tool.py** 🔴 🟡 BASIC version of MCP development_tools image analysis
- [ ] **minimal_judge.py** 🔴 🟡 BASIC version of MCP development_tools judging

#### Infrastructure & Support (POTENTIALLY UNIQUE)
- [ ] **models.py** 🔴 🟢 UNIQUE - Tool infrastructure and base models (keep for now)

### MCP Tools (35 tools across 4 servers) - PRIMARY IMPLEMENTATION LAYER

**🟢 ARCHITECTURE STATUS**: MCP layer is the DEFINITIVE tool implementation with proper Claude Code integration.

#### Social Tools MCP (6 tools) - Core User Features
- [ ] **social_tools.py** 🔴 **CRITICAL AUDIT NEEDED**
  - search_current_info - Duplicates Agent + Standalone
  - create_image - Duplicates Agent + Standalone  
  - analyze_shared_image - Duplicates Agent + Standalone
  - save_link - Duplicates Agent + Standalone
  - search_links - Duplicates Agent + Standalone
  - technical_analysis - Similar to valor_delegation_tool.py

#### PM Tools MCP (3 tools) - Project Management (formerly notion-tools)
- [ ] **pm_tools.py** 🔴 **HIGH PRIORITY**
  - query_notion_projects - Duplicates Agent + Standalone
  - list_notion_workspaces - UNIQUE functionality
  - validate_workspace_access - UNIQUE functionality

#### Telegram Tools MCP (4 tools) - Conversation Management  
- [ ] **telegram_tools.py** 🔴 **MEDIUM PRIORITY**
  - search_conversation_history - Duplicates Agent + Standalone
  - get_conversation_context - Duplicates Agent + Standalone
  - get_recent_history - UNIQUE functionality
  - list_telegram_dialogs - UNIQUE functionality

#### Development Tools MCP (22 tools) - Development Workflow
- [ ] **development_tools.py** 🔴 **OVERSIZED SERVER - SPLIT RECOMMENDED**
  - Contains 22 tools across 5 categories (testing, linting, docs, images, project)
  - Many tools partially duplicate Standalone layer
  - Some tools are completely unique to MCP layer

### EMERGENCY SPRINT: Duplication Crisis Resolution (Week 1)
**🚨 CRITICAL**: Address architecture crisis immediately - 8 tool categories fully duplicated

#### Phase 1A: Remove Agent Tool Duplications (Day 1-2)
1. **Remove Agent @valor_agent.tool duplicates** - All except delegate_coding_task 
   - Keep delegate_coding_task (different from technical_analysis)
   - Remove: search_current_info, create_image, analyze_shared_image
   - Remove: save_link_for_later, search_saved_links, query_notion_projects  
   - Remove: search_conversation_history, get_conversation_context
   - Remove: read_project_documentation, list_project_documentation

#### Phase 1B: Audit Core MCP Servers (Day 3-5)
1. **social_tools.py** - CRITICAL - Contains all core duplicated functionality
2. **pm_tools.py** - HIGH - Notion integration + unique workspace tools
3. **telegram_tools.py** - MEDIUM - Conversation tools + unique dialog features

#### Phase 1C: Plan Standalone Deprecation (Day 5)
- **Mark for removal**: 7 fully superseded standalone tools
- **Evaluate**: 6 partially superseded tools (keep unique features)
- **Keep**: models.py + potentially unique functionality

### Sprint 2: Architecture Consolidation (Week 2)
**Focus**: Complete transition to MCP-primary architecture

1. **Split oversized development_tools.py** (22 tools → 4-5 focused servers)
2. **Remove superseded standalone tools** (after MCP validation)
3. **Update all tests** to use MCP tools instead of duplicates
4. **Update documentation** to reflect simplified architecture

**Architecture Goal**: Agent Layer (delegation only) → MCP Layer (primary) → Minimal Standalone (unique only)

## Architecture Consolidation Plan

**Status**: ✅ COMPLETED - Comprehensive analysis reveals massive duplication crisis. See [Comprehensive Tool Analysis](../../comprehensive_tool_analysis.md)

### Critical Findings

#### 🔴 Full Duplications Identified (8 categories):
1. **Web Search**: Agent + MCP + Standalone (3 implementations)
2. **Image Generation**: Agent + MCP + Standalone (3 implementations)  
3. **Image Analysis**: Agent + MCP + Standalone (3 implementations)
4. **Link Management**: Agent + MCP + Standalone (3 implementations)
5. **Notion Queries**: Agent + MCP + Standalone (3 implementations)
6. **Telegram History**: Agent + MCP + Standalone (3 implementations)
7. **Documentation Reading**: Agent + MCP + Standalone (3 implementations)
8. **Development Delegation**: Agent + MCP similar functionality (2 implementations)

#### 🟡 Partial Duplications Identified:
- Document Summarization (Standalone basic → MCP comprehensive)
- Code Linting (Standalone basic → MCP comprehensive)
- Image Tagging (Standalone basic → MCP comprehensive)
- AI Testing/Judging (Standalone basic → MCP comprehensive)

#### 🟢 Unique Tools Identified:
- MCP-only: get_recent_history, list_telegram_dialogs, list_notion_workspaces, validate_workspace_access, validate_directory_access_tool, plus 15+ specialized development tools
- Standalone-only: models.py (infrastructure)

### Immediate Action Plan

#### Phase 1: Emergency Duplication Removal ⚡
- [x] **Analysis Completed**: Full tool mapping across all layers
- [ ] **Remove Agent Duplicates**: 9 of 11 @valor_agent.tool functions marked for removal
- [ ] **Audit MCP Primaries**: Validate MCP tools before removing duplicates
- [ ] **Plan Standalone Deprecation**: Mark 7 standalone tools for removal

#### Phase 2: Architecture Simplification
- [ ] **Split development_tools.py**: 22 tools → 4-5 focused servers
- [ ] **Remove Superseded Standalone Tools**: After MCP validation
- [ ] **Update Test Suite**: Transition from duplicate tools to MCP primaries
- [ ] **Update Documentation**: Reflect new simplified architecture

### Target Architecture
**Before**: Agent (11) → Standalone (14) → MCP (35) = 60 total tools with massive duplication
**After**: Agent (1-2 delegation) → MCP (35+ organized) → Standalone (1-2 infrastructure) = ~40 tools, no duplication

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
