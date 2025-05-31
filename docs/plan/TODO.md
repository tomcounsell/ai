# Development TODO

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