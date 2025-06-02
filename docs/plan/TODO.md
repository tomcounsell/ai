# Development TODO

## Tool Audit TODO

This section tracks the comprehensive audit of all PydanticAI tools in the system. Each tool will be audited according to the standards defined in [tool_auditing.md](./tool_auditing.md).

### Overview

**Total Tools Identified**: 18
- **Agent Tools** (integrated with valor_agent): 9
- **Standalone Tools** (in /tools/ directory): 9

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
- [ ] **search_saved_links** 🔴 - Search through saved links (MEDIUM)
- [ ] **query_notion_projects** 🔴 - PsyOPTIMAL workspace queries (HIGH)
- [ ] **search_conversation_history** 🔴 - Search Telegram conversation history (MEDIUM)
- [ ] **get_conversation_context** 🔴 - Extended conversation context (MEDIUM)

### Standalone Tools (/tools/ directory)

- [ ] **search_tool.py** 🔴 - Web search implementation (HIGH - supports search_current_info)
- [ ] **image_generation_tool.py** 🔴 - DALL-E 3 implementation (HIGH - supports create_image)
- [ ] **image_analysis_tool.py** 🔴 - AI vision implementation (HIGH - supports analyze_shared_image)
- [x] **valor_delegation_tool.py** ✅ - Delegation implementation (CRITICAL - recently fixed)
- [ ] **notion_tool.py** 🔴 - Notion workspace integration (HIGH - supports query_notion_projects)
- [ ] **link_analysis_tool.py** 🔴 - URL analysis implementation (MEDIUM - supports link tools)
- [ ] **telegram_history_tool.py** 🔴 - Conversation history implementation (MEDIUM)
- [ ] **image_tagging_tool.py** 🔴 - Image tagging functionality (LOW)
- [ ] **doc_summary_tool.py** 🔴 - Document summarization (LOW)

### Sprint 1: Critical & High Priority (Week 1)
**Focus**: Recently fixed tools and core user-facing functionality

1. **delegate_coding_task** (agent tool) - CRITICAL
2. **valor_delegation_tool.py** - CRITICAL  
3. **search_current_info** (agent tool) - HIGH
4. **search_tool.py** - HIGH
5. **create_image** (agent tool) - HIGH
6. **image_generation_tool.py** - HIGH

**Estimated Effort**: 15-18 hours

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