# Audit Next Tool

Automatically picks up the next tool from the audit TODO list in `docs/plan/TODO.md`, performs a comprehensive audit according to `docs/plan/tool_auditing.md`, and then implements the resulting recommendations.

## Command Overview

This command will:

1. **Identify Next Tool**: Read `docs/plan/TODO.md` and find the first tool marked with 🔴 (Not Started)
2. **Perform Comprehensive Audit**: Follow the 4-phase audit process from `docs/plan/tool_auditing.md`
3. **Generate Deliverables**: Create audit report and recommendations TODO list
4. **Implement Improvements**: Work through the recommendations systematically
5. **Update Status**: Mark tool as ✅ Approved in the TODO list

## Execution Steps

### Phase 1: Tool Selection and Setup (5-10 minutes)

1. **Use TodoWrite tool** to create audit tracking tasks
2. **Read** `docs/plan/TODO.md` to identify the next tool marked with 🔴
3. **Create audit workspace**: `mkdir -p docs/audits/` (if not exists)
4. **Update TODO status** to 🟡 In Progress in TODO.md
5. **Identify tool files**: Locate both agent tool (if applicable) and implementation files
6. **Check git history**: Look for recent commits related to the tool (especially for "recently fixed" tools)

### Phase 2: Comprehensive Audit (60-90 minutes)

Follow the structured audit process from `docs/plan/tool_auditing.md`:

#### 2.1 Design Review (15-20 minutes)
- **Read tool source code** - Both agent wrapper and implementation
- **Analyze architecture** - Separation of concerns, single responsibility  
- **Validate interface design** - Parameters, return types, context usage
- **Check dependencies** - External services, imports, coupling
- **Review recent changes** - Git commits, architectural decisions

#### 2.2 Implementation Review (20-30 minutes)
- **Code quality assessment** - Style, error handling, performance
- **PydanticAI integration** - Decoration, context usage, return formatting
- **Security validation** - Input sanitization, safe API calls
- **Dependency management** - API keys, timeouts, rate limiting
- **Performance considerations** - Response times, hanging prevention

#### 2.3 Testing Validation (15-25 minutes)
- **Locate existing tests** - Find test files for the tool
- **Run existing tests** - Validate current functionality works
- **Identify testing gaps** - Happy path, errors, edge cases, integration
- **Check agent integration** - Tool selection, conversation formatting
- **Performance validation** - Execution times, hanging prevention

#### 2.4 Documentation Review (10-15 minutes)
- **Agent documentation** - Docstring clarity, usage examples, parameters
- **Developer documentation** - Architecture notes, maintenance guidance
- **Integration documentation** - Dependencies, configuration, errors
- **Historical context** - Document any recent fixes or architectural changes

### Phase 3: Generate Deliverables (15 minutes)

#### 3.1 Create Audit Report
- **Generate** `docs/audits/[tool_name]_audit_report.md`
- **Include** executive summary with Pass/Conditional Pass/Fail assessment
- **Document** detailed findings from all 4 phases
- **List** priority action items

#### 3.2 Create Recommendations TODO
- **Generate** `docs/audits/[tool_name]_recommendations.md`
- **Break down findings** into actionable tasks with effort estimates
- **Prioritize items**: Critical/High/Medium/Low
- **Include implementation notes**: Dependencies, risks, success criteria
- **Provide timeline estimates** for each priority level

### Phase 4: Implement Recommendations (60-180 minutes)

Work through the recommendations in priority order:

#### 4.1 Critical and High Priority Items
- **Code quality fixes**: Address immediate issues with specific file:line locations
- **Documentation updates**: Enhance agent docstrings with examples and usage guidance
- **Critical testing gaps**: Add tests for basic functionality and error conditions
- **Architecture improvements**: Fix separation of concerns or interface issues

#### 4.2 Medium Priority Items (if time permits)
- **Enhancement opportunities**: Improve performance or add helpful features
- **Additional documentation**: Troubleshooting guides, more examples
- **Extended testing**: Edge cases, load testing, additional integration tests

#### 4.3 Validation
- **Test all changes**: Ensure no regressions introduced
- **Validate agent integration**: Confirm tool selection and response formatting work
- **Performance check**: Ensure reasonable execution times maintained
- **Documentation review**: Verify all updates are clear and helpful

### Phase 5: Completion and Cleanup (10-15 minutes)

1. **Update audit report** with final "Approved" status
2. **Update TodoWrite** to mark all audit tasks as completed
3. **Delete** temporary `docs/audits/[tool_name]_recommendations.md` file
4. **Update** `docs/plan/TODO.md` to mark tool as ✅ Approved
5. **Commit and push changes**: Create comprehensive commit with all improvements
6. **Optional**: Archive results to `docs/audits/completed/` for organization

## Command Execution Notes

### Tool Priority Order
Follow the priority order defined in `docs/plan/TODO.md`:
1. **Critical Priority**: `delegate_coding_task`, `valor_delegation_tool.py`
2. **High Priority**: `search_current_info`, `create_image`, `analyze_shared_image`, etc.
3. **Medium Priority**: Link tools, conversation history tools
4. **Low Priority**: Support tools and utilities

### Special Considerations

#### For Recently Fixed Tools (delegate_coding_task, valor_delegation_tool.py)
- **Extra validation** of the recursive spawning fix
- **Thorough testing** of the new guidance response format
- **Performance verification** that hanging issues are resolved

#### For Agent Tools vs Implementation Tools
- **Agent tools**: Focus on PydanticAI integration, conversation formatting, tool selection
- **Implementation tools**: Focus on core logic, external API integration, error handling
- **Tool pairs**: Consider both together when they're closely related

#### For External API Tools (search, image generation, notion)
- **API key validation**: Proper environment variable handling
- **Rate limiting**: Respect service limits and implement appropriate delays
- **Fallback behavior**: Graceful handling when services are unavailable
- **Timeout handling**: Prevent hanging on slow/unresponsive APIs

### Success Criteria
- Tool passes all 4 audit phases
- Critical and high-priority recommendations implemented
- Documentation enhanced for both agent and developer use
- Tests added covering key functionality and error conditions
- No regressions in existing functionality
- Tool marked as ✅ Approved in TODO list

### Automation Commands

To execute this audit process:

```bash
# This command will automatically:
# 1. Find the next 🔴 tool in TODO.md
# 2. Perform the complete audit
# 3. Implement all recommendations
# 4. Update status to ✅ Approved

# Run in the project root directory
cd /Users/valorengels/src/ai
```

## Expected Time Investment

Based on actual audit experience:

- **Critical Priority Tools**: 3-4 hours (recently fixed tools needing thorough validation)
  - Example: delegate_coding_task took ~3 hours with comprehensive documentation updates and testing
- **High Priority Tools**: 2-3 hours (core user-facing functionality)
- **Medium Priority Tools**: 1.5-2 hours (supporting functionality)  
- **Low Priority Tools**: 1-1.5 hours (utilities and support tools)

**Time Breakdown** (for critical tools):
- Audit phases: 60-90 minutes
- Generate deliverables: 15-20 minutes
- Implement improvements: 60-120 minutes
- Completion and cleanup: 10-15 minutes

## Quality Standards

Each audited tool must meet:
- ✅ Clear separation of concerns
- ✅ Proper PydanticAI integration
- ✅ Comprehensive error handling
- ✅ Agent-friendly documentation with examples
- ✅ Developer maintenance documentation
- ✅ Test coverage for core scenarios
- ✅ Performance within reasonable limits
- ✅ Security best practices

## Integration with Development Workflow

This command integrates with the existing development workflow by:
- **Following established patterns** from `docs/plan/tool_auditing.md`
- **Using TodoWrite tool** to track audit progress and implementation tasks
- **Using existing file structure** in `docs/audits/`
- **Updating central TODO tracking** in `docs/plan/TODO.md`
- **Creating clean commits** with comprehensive improvement descriptions
- **Preserving audit work** by committing and pushing all changes
- **Building audit history** for future reference and pattern identification

## Lessons Learned from First Audit

**delegate_coding_task audit (December 2025)**:
- **Git history is crucial** - Understanding recent fixes saves significant time
- **TodoWrite integration works well** - Helps track complex audit progress
- **Mock RunContext testing pattern** - Effective for agent tool testing
- **Documentation updates are critical** - Outdated docs were main blocker
- **Commit and push immediately** - Preserves audit work for future reference
- **Test hanging prevention specifically** - Critical for subprocess-related tools