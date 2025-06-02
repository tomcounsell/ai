# PydanticAI Tool Auditing Guide

This document outlines the comprehensive process for auditing PydanticAI tools to ensure they meet production quality standards, maintain clear separation of concerns, and provide reliable functionality within the agent ecosystem.

## Overview

A tool audit validates that a PydanticAI tool is well-designed, thoroughly tested, properly documented, and follows established architectural patterns. The audit process ensures tools are maintainable, reliable, and provide clear value to both the agent and human developers.

## Prerequisites & Design Principles

### Tom's Minimal Design Requirements

1. **Clear Separation of Concerns**
   - Tool function handles only the core logic for its specific purpose
   - External service integration is abstracted through dedicated modules
   - Business logic is separated from I/O operations
   - Error handling is consistent and appropriate for the tool's scope

2. **Single Responsibility**
   - Each tool does one thing well
   - Tool purpose is clearly defined and limited in scope
   - Dependencies are minimal and well-justified

3. **Predictable Interface**
   - Input parameters are typed and validated
   - Output format is consistent and documented
   - Error conditions are well-defined and handled gracefully

## Audit Process Overview

The audit follows a structured approach across four main phases:

1. **Design Review** - Architecture and separation of concerns
2. **Implementation Review** - Code quality and best practices
3. **Testing Validation** - End-to-end functionality coverage
4. **Documentation Review** - Clarity for both agents and developers

## Phase 1: Design Review

### 1.1 Architectural Assessment

**Objective**: Validate the tool follows proper separation of concerns and architectural patterns.

**Checklist**:
- [ ] Tool has a single, well-defined responsibility
- [ ] External service calls are abstracted through dedicated modules (e.g., `tools/search_tool.py` uses `search_web()`)
- [ ] Business logic is separated from I/O operations
- [ ] Dependencies are minimal and justified
- [ ] Tool doesn't duplicate functionality of existing tools

**Questions to Answer**:
- What specific problem does this tool solve for the agent?
- How does this tool fit into the overall agent capability matrix?
- Are there any architectural red flags (tight coupling, circular dependencies, etc.)?

### 1.2 Interface Design

**Objective**: Ensure the tool interface is clean, typed, and predictable.

**Checklist**:
- [ ] Function signature uses proper Python type hints
- [ ] Parameters are logically organized and named
- [ ] Optional parameters have sensible defaults
- [ ] Return type is consistent and documented
- [ ] Context parameter (`RunContext[ValorContext]`) is used appropriately

**Example of Good Interface Design**:
```python
@valor_agent.tool
def search_current_info(ctx: RunContext[ValorContext], query: str) -> str:
    """Search for current information on the web using Perplexity AI."""
```

## Phase 2: Implementation Review

### 2.1 Code Quality Assessment

**Objective**: Validate implementation follows Python best practices and PydanticAI patterns.

**Checklist**:
- [ ] Code follows PEP 8 style guidelines
- [ ] Error handling is comprehensive and graceful
- [ ] Edge cases are properly handled
- [ ] Performance considerations are addressed
- [ ] Security considerations are implemented (input validation, safe API calls)
- [ ] Logging is appropriate and helpful for debugging

### 2.2 PydanticAI Integration

**Objective**: Ensure proper integration with the PydanticAI framework.

**Checklist**:
- [ ] Tool is properly decorated with `@agent.tool`
- [ ] Context is used appropriately (accessing chat_id, username, etc.)
- [ ] Tool doesn't cause hanging or blocking issues
- [ ] Tool integrates well with other agent tools
- [ ] Return values are agent-friendly (formatted for conversation)

**Best Practices Reference**:
- Follow [PydanticAI Tool Documentation](https://ai.pydantic.dev/tools/)
- Use context for chat-specific functionality
- Format outputs for conversational use
- Handle async operations properly if needed

### 2.3 Dependency Management

**Objective**: Validate external dependencies are properly managed.

**Checklist**:
- [ ] External API keys are properly configured via environment variables
- [ ] API rate limits are respected
- [ ] Network timeouts are configured appropriately
- [ ] Fallback behavior is implemented for service failures
- [ ] Dependencies are documented in requirements

## Phase 3: Testing Validation

### 3.1 End-to-End Functionality Testing

**Objective**: Ensure the tool works correctly in all expected scenarios without prescribing specific testing methods.

**Core Testing Areas** (methodology flexible):
- **Happy Path Testing**: Tool performs its primary function correctly
- **Error Condition Testing**: Tool handles failures gracefully
- **Edge Case Testing**: Tool behaves correctly with unusual inputs
- **Integration Testing**: Tool works within the agent ecosystem
- **Performance Testing**: Tool performs within acceptable time limits

**Testing Approaches** (choose what fits):
- Unit tests for core logic
- Integration tests with live services
- Mock tests for external dependencies
- Manual testing scenarios
- Automated test suites

### 3.2 Agent Integration Testing

**Objective**: Validate the tool works correctly when called by the agent.

**Test Scenarios**:
- Agent correctly selects tool for appropriate queries
- Tool provides useful responses in conversational context
- Tool doesn't interfere with other agent capabilities
- Tool handles context information appropriately
- Error messages are user-friendly
- **No hanging or blocking issues** during tool execution
- **Context parameter handling** works with mock RunContext objects

**Testing Approach for Agent Tools**:
```python
# Create mock RunContext for testing agent tools
class MockRunContext:
    def __init__(self, deps):
        self.deps = deps

mock_context = MockRunContext(ValorContext(chat_id=12345, username="test"))
result = agent_tool_function(mock_context, "test input")
```

### 3.3 Regression Testing

**Objective**: Ensure tool changes don't break existing functionality.

**Checklist**:
- [ ] Existing test cases still pass
- [ ] Tool behavior remains consistent for known inputs
- [ ] Performance hasn't degraded significantly
- [ ] Integration with other tools remains stable

## Phase 4: Documentation Review

### 4.1 Agent-Facing Documentation

**Objective**: Ensure the agent can understand when and how to use the tool.

**Requirements**:
- **Clear Tool Description**: Explains what the tool does in plain language
- **Usage Examples**: Shows when the tool should be used
- **Parameter Descriptions**: Explains each parameter's purpose
- **Return Value Description**: Explains what the tool returns

**Example of Good Agent Documentation**:
```python
def search_current_info(ctx: RunContext[ValorContext], query: str) -> str:
    """Search for current information on the web using Perplexity AI.

    This tool enables the Valor agent to access up-to-date information from
    the web when answering questions about current events, trends, or recent
    developments that may not be in the agent's training data.

    Use this when you need up-to-date information about:
    - Current events, news, or recent developments
    - Latest technology trends or releases
    - Current market conditions or company information
    - Recent research or publications
    - Any information that might have changed recently

    Args:
        ctx: The runtime context containing conversation information.
        query: The search query to find current information about.

    Returns:
        str: Current information from web search formatted for conversation.

    Example:
        >>> search_current_info(ctx, "Python 3.12 new features")
        '🔍 **Python 3.12 new features**\n\nPython 3.12 includes...'
    """
```

### 4.2 Developer-Facing Documentation

**Objective**: Ensure developers can understand, maintain, and extend the tool.

**Requirements**:
- **Architecture Documentation**: Explains how the tool fits into the system
- **Implementation Details**: Covers important design decisions
- **Maintenance Notes**: Explains common maintenance tasks
- **Extension Guidelines**: Shows how to safely extend functionality
- **Troubleshooting Guide**: Common issues and solutions

### 4.3 Integration Documentation

**Objective**: Document how the tool integrates with external services and the broader system.

**Requirements**:
- **External Dependencies**: Document required API keys, services, etc.
- **Configuration Requirements**: Environment variables, settings, etc.
- **Service Limitations**: Rate limits, quotas, availability considerations
- **Error Scenarios**: Common failure modes and how they're handled

## Audit Execution Order

### Phase 1: Tool Selection and Setup (5-10 minutes)
1. **Identify target tool**: Read TODO.md and find next 🔴 tool
2. **Update tracking**: Change status to 🟡 In Progress in TODO.md  
3. **Create workspace**: `mkdir -p docs/audits/` (if not exists)
4. **Locate tool files**: Find both agent tool and implementation files
5. **Check git history**: Look for recent commits related to the tool (especially "recently fixed" tools)

### Phase 2: Comprehensive 4-Phase Audit (60-90 minutes)

#### 2.1 Design Review (15-20 minutes)
1. **Read tool source code** - Both agent wrapper and implementation
2. **Analyze architecture** - Separation of concerns, single responsibility
3. **Validate interface design** - Parameters, return types, context usage
4. **Check dependencies** - External services, imports, coupling
5. **Review recent changes** - Git commits, architectural decisions

#### 2.2 Implementation Review (20-30 minutes)
1. **Code quality assessment** - Style, error handling, performance
2. **PydanticAI integration** - Decoration, context usage, return formatting
3. **Security validation** - Input sanitization, safe API calls
4. **Dependency management** - API keys, timeouts, rate limiting
5. **Performance considerations** - Response times, resource usage

#### 2.3 Testing Validation (15-25 minutes)
1. **Locate existing tests** - Find test files for the tool
2. **Run existing tests** - Validate current functionality
3. **Identify testing gaps** - Happy path, errors, edge cases, integration
4. **Check agent integration** - Tool selection, conversation formatting
5. **Performance validation** - Execution times, hanging prevention

#### 2.4 Documentation Review (10-15 minutes)
1. **Agent documentation** - Docstring clarity, usage examples, parameters
2. **Developer documentation** - Architecture notes, maintenance guidance
3. **Integration documentation** - Dependencies, configuration, errors
4. **Historical context** - Document any recent fixes or architectural changes

### Phase 3: Generate Deliverables (15-20 minutes)
1. **Create audit report** - `docs/audits/[tool_name]_audit_report.md`
2. **Create recommendations TODO** - `docs/audits/[tool_name]_recommendations.md`
3. **Prioritize action items** - Critical/High/Medium/Low with effort estimates
4. **Plan implementation order** - Dependencies, risks, impact

### Phase 4: Implement Critical Improvements (30-120 minutes)
1. **Address critical items** - Blocking issues, documentation updates
2. **Implement high priority fixes** - Testing gaps, code quality issues
3. **Validate changes** - Run tests, check agent integration
4. **Update documentation** - Reflect any changes made

### Phase 5: Completion and Cleanup (10-15 minutes)
1. **Update audit report** - Mark as "Approved" with final status
2. **Clean up workspace** - Delete recommendations.md file
3. **Update TODO.md** - Mark tool as ✅ Approved
4. **Commit and push** - Comprehensive commit with all improvements

## Audit Deliverables

### 1. Audit Report

**Format**: `docs/audits/[tool_name]_audit_report.md`

```markdown
# Tool Audit Report: [Tool Name]

## Executive Summary
- Tool Purpose: [Brief description]
- Overall Assessment: [Pass/Conditional Pass/Fail]
- Key Findings: [3-5 bullet points]

## Detailed Findings

### Design Review
- Architecture: [Assessment]
- Interface: [Assessment]
- Recommendations: [List]

### Implementation Review
- Code Quality: [Assessment]
- PydanticAI Integration: [Assessment]
- Recommendations: [List]

### Testing Status
- Coverage Assessment: [Description]
- Key Gaps: [List]
- Recommendations: [List]

### Documentation Review
- Agent Documentation: [Assessment]
- Developer Documentation: [Assessment]
- Recommendations: [List]

## Priority Action Items
1. [High priority item]
2. [Medium priority item]
3. [Low priority item]

## Approval Status
- [ ] Approved for production use
- [ ] Approved with conditions: [List conditions]
- [ ] Requires rework before approval
```

### 2. Tool Improvement Recommendations

**Format**: `docs/audits/[tool_name]_recommendations.md` (temporary file, deleted after implementation)

This file contains actionable TODO items generated from the audit findings:

```markdown
# [Tool Name] - Audit Recommendations TODO

Generated from audit on: [Date]
Auditor: [Name]
Priority: [Critical/High/Medium/Low]

## Immediate Actions Required (Critical/High Priority)

### Code Quality Improvements
- [ ] [Specific code issue] - Location: [file:line] - Effort: [hours]
- [ ] [Performance optimization] - Impact: [description] - Effort: [hours]
- [ ] [Security fix] - Vulnerability: [description] - Effort: [hours]

### Documentation Updates
- [ ] [Agent docstring improvement] - Missing: [details] - Effort: [hours]
- [ ] [Developer documentation] - Add: [section] - Effort: [hours]
- [ ] [Usage examples] - Scenarios: [list] - Effort: [hours]

### Testing Gaps
- [ ] [Test scenario] - Coverage: [area] - Type: [unit/integration/e2e] - Effort: [hours]
- [ ] [Error condition test] - Scenario: [description] - Effort: [hours]
- [ ] [Performance test] - Metric: [what to measure] - Effort: [hours]

### Architecture/Design Changes
- [ ] [Refactoring task] - Concern: [separation issue] - Effort: [hours]
- [ ] [Interface improvement] - Change: [parameter/return type] - Effort: [hours]
- [ ] [Integration fix] - Issue: [agent integration problem] - Effort: [hours]

## Medium Priority Improvements

### Enhancement Opportunities
- [ ] [Feature addition] - Value: [description] - Effort: [hours]
- [ ] [Code cleanup] - Technical debt: [description] - Effort: [hours]
- [ ] [Performance optimization] - Gain: [expected improvement] - Effort: [hours]

### Documentation Enhancements
- [ ] [Additional examples] - Use case: [description] - Effort: [hours]
- [ ] [Troubleshooting guide] - Common issues: [list] - Effort: [hours]

## Low Priority (Nice to Have)

### Code Quality
- [ ] [Style improvements] - Standards: [specific guidelines] - Effort: [hours]
- [ ] [Additional validation] - Input: [parameter] - Effort: [hours]

### Testing Extensions
- [ ] [Edge case tests] - Scenario: [unusual input] - Effort: [hours]
- [ ] [Load testing] - Capacity: [concurrent usage] - Effort: [hours]

## Implementation Notes

### Dependencies
- [Tool] depends on: [other tools/services]
- Required before implementing: [prerequisite tasks]
- Affects: [other tools that depend on this one]

### Risk Assessment
- **High Risk Changes**: [list changes that could break functionality]
- **Testing Strategy**: [how to validate changes don't break existing functionality]
- **Rollback Plan**: [how to revert if issues occur]

## Success Criteria

- [ ] All Critical and High priority items completed
- [ ] Tool passes re-audit
- [ ] No regression in existing functionality
- [ ] Performance metrics maintained or improved
- [ ] Documentation updated and reviewed

## Timeline Estimate

- **Critical Items**: [X hours] - Target: [date]
- **High Priority**: [X hours] - Target: [date]  
- **Medium Priority**: [X hours] - Target: [date]
- **Total Effort**: [X hours] over [X weeks]

## Implementation Order

1. [Task] - [Reason for priority]
2. [Task] - [Dependencies or blockers]
3. [Task] - [Risk mitigation]
```

### 3. Updated Tool Documentation

**Result**: Improved tool files with enhanced docstrings and comments

### 4. New/Enhanced Tests

**Result**: Additional test files or enhanced existing tests covering identified gaps

### 5. Tool Implementation Improvements

**Result**: Code changes addressing critical and high-priority findings

## Best Practices Summary

### Tool Design
- Keep tools focused on a single responsibility
- Use clear, descriptive parameter names
- Provide sensible defaults for optional parameters
- Return conversation-friendly formatted strings
- **Avoid subprocess spawning** that can cause hanging issues

### Implementation
- Follow Python type hints throughout
- Implement comprehensive error handling
- Use appropriate logging for debugging
- Validate inputs and sanitize outputs
- **Test for hanging/blocking issues** especially with external processes

### Testing
- Test both happy path and error conditions
- Include integration testing with agent
- Test with realistic data and scenarios
- Validate performance under load
- **Add specific tests for recently fixed issues** to prevent regressions
- **Test agent integration with mock RunContext** when needed

### Documentation
- Write for both agent and human audiences
- Include clear examples and use cases
- Document all configuration requirements
- Explain error conditions and handling
- **Document architectural decisions** especially for recent fixes
- **Update docstrings immediately** when implementation changes

### Audit Process Lessons
- **Check git history first** for recently fixed tools to understand context
- **Update documentation immediately** when behavior changes
- **Create comprehensive tests** for new functionality
- **Use TodoWrite tool** to track audit progress
- **Commit and push improvements** to preserve audit work

## Deliverable Workflow

### During Audit
1. **Create audit workspace**: `mkdir docs/audits/[tool_name]/`
2. **Generate recommendations file**: `docs/audits/[tool_name]_recommendations.md`
3. **Document findings**: `docs/audits/[tool_name]_audit_report.md`
4. **Track progress**: Update TODO.md with specific action items

### Post-Audit Implementation
1. **Work through recommendations**: Use recommendations.md as working TODO list
2. **Update codebase**: Implement critical and high-priority improvements
3. **Enhance documentation**: Update tool docstrings and add developer notes
4. **Add/improve tests**: Address testing gaps identified in audit
5. **Validate changes**: Re-run audit phases to ensure improvements

### Completion
1. **Final report**: Update audit report with "Approved" status
2. **Cleanup**: Delete temporary recommendations.md file
3. **Update TODO.md**: Mark tool as ✅ Approved in main TODO list
4. **Commit and push**: Ensure all improvements are preserved in git
5. **Optional archiving**: Move audit report to `docs/audits/completed/` for organization

## Special Audit Considerations

### Recently Fixed Tools
Tools marked as "recently fixed" in TODO.md require extra attention:

1. **Validate the fix** - Understand what was broken and how it was fixed
2. **Test the fix thoroughly** - Ensure the issue is completely resolved
3. **Update documentation** - Reflect any behavioral changes
4. **Add regression tests** - Prevent the issue from recurring
5. **Document architectural decisions** - Explain why the fix was implemented that way

**Example**: delegate_coding_task was fixed to prevent hanging by returning guidance instead of spawning subprocesses. The audit must validate this approach works and update documentation accordingly.

### Tool Pairs (Agent + Implementation)
Some tools have both an agent wrapper and implementation file:
- **Agent tool**: Focus on PydanticAI integration, context usage, conversation formatting
- **Implementation**: Focus on core logic, external APIs, error handling
- **Audit both together** when they're closely related
- **Ensure consistency** between agent interface and implementation behavior

### External API Tools
Tools that integrate with external services need special consideration:
- **API key validation** - Proper environment variable handling
- **Rate limiting** - Respect service limits, implement delays
- **Timeout handling** - Prevent hanging on slow/unresponsive APIs
- **Fallback behavior** - Graceful degradation when services unavailable
- **Error mapping** - Convert API errors to user-friendly messages

## Integration with Development Workflow

### Before Major Tool Changes
- Review existing audit report for guidance
- Update recommendations if new issues identified
- Ensure changes align with architectural principles

### After Tool Improvements
- Run quick re-audit to validate improvements
- Update documentation to reflect changes
- Add regression tests for fixed issues

### Continuous Improvement
- Schedule periodic re-audits for critical tools
- Use audit findings to improve overall tool patterns
- Share best practices across tool development

### Audit Workflow Integration
- **Use TodoWrite tool** to track audit progress and implementation tasks
- **Commit frequently** during implementation to preserve incremental progress
- **Push final results** to ensure audit work is preserved
- **Update TODO.md immediately** when tools are approved

## Conclusion

A thorough tool audit ensures that PydanticAI tools are reliable, maintainable, and provide clear value to the agent ecosystem. By following this structured approach, teams can maintain high quality standards while enabling rapid development and deployment of new agent capabilities.

The audit process is designed to be comprehensive yet flexible, allowing teams to adapt the methodology to their specific needs while ensuring all critical quality dimensions are evaluated. The deliverable structure ensures that audit findings translate directly into actionable improvements that enhance tool quality and developer productivity.