---
description: Systematically audit new or modified tools for quality, architecture compliance, and integration patterns
---

# Tool Audit System

Comprehensive tool audit system for validating and maintaining code quality across the tool ecosystem. All current tools have been audited, but this system remains ready for new tool additions.

## Current Status

**All Audits Complete**: ✅ 12/12 standalone tools audited and approved  
**System Health**: ✅ Production-ready and maintainable  
**Architecture**: ✅ GOLD STANDARD patterns throughout  

**Audit Registry**: See `docs/plan/AUDITS.md` for complete audit history and results

## Command Purpose

This command provides systematic audit capabilities for:

1. **New Tool Validation**: When new tools are added to the system
2. **Periodic Re-audits**: Quality validation for existing tools after major changes
3. **Architecture Compliance**: Ensuring new tools follow established patterns
4. **Quality Maintenance**: Ongoing validation of system health

## Audit Process Overview

When auditing new or modified tools, follow the 4-phase process:

### Phase 1: Analysis and Assessment
- **Tool Identification**: Locate tool files and related components
- **Architecture Review**: Validate separation of concerns and design patterns
- **Duplication Check**: Ensure no unnecessary code duplication
- **Git History**: Review recent changes and context

### Phase 2: Quality Validation
- **Code Quality**: Style, error handling, performance considerations
- **Integration Patterns**: MCP wrapper patterns, PydanticAI integration
- **Security Review**: Input validation, safe API calls, dependency management
- **Documentation**: Agent and developer documentation quality

### Phase 3: Testing Assessment
- **Test Coverage**: Existing tests and gap identification
- **Functionality Validation**: End-to-end testing and integration
- **Performance Testing**: Response times and resource usage
- **Regression Prevention**: Ensure changes don't break existing functionality

### Phase 4: Documentation and Completion
- **Update Audit Registry**: Add results to `docs/plan/AUDITS.md`
- **Create Audit Report**: Document findings and recommendations
- **Implement Fixes**: Address critical and high priority issues
- **Commit Changes**: Preserve audit work and improvements

## Established Quality Standards

All audited tools must meet these standards:

### Architecture Requirements
- ✅ **Clear separation of concerns**: Single responsibility principle
- ✅ **Proper integration patterns**: Follow GOLD STANDARD MCP wrapper pattern
- ✅ **Minimal dependencies**: Well-justified external dependencies
- ✅ **Clean interfaces**: Typed parameters and consistent return types

### Code Quality Standards  
- ✅ **Comprehensive error handling**: Graceful failure and recovery
- ✅ **Security best practices**: Input validation, safe API usage
- ✅ **Performance considerations**: Reasonable response times, timeout handling
- ✅ **Code style compliance**: PEP 8 and project conventions

### Documentation Requirements
- ✅ **Agent-friendly documentation**: Clear docstrings with examples
- ✅ **Developer documentation**: Architecture notes and maintenance guidance
- ✅ **Integration documentation**: Dependencies, configuration, error handling
- ✅ **Usage examples**: Practical examples for common use cases

### Testing Standards
- ✅ **Core functionality coverage**: Happy path and error conditions
- ✅ **Integration testing**: Tool works within the ecosystem
- ✅ **Performance validation**: Meets timing requirements
- ✅ **Regression prevention**: Changes don't break existing functionality

## Architecture Patterns

### GOLD STANDARD: MCP Wrapper Pattern
```python
# MCP tool imports and calls standalone implementation
from tools.standalone_tool import core_function

@mcp.tool()
def mcp_wrapper_function(params) -> str:
    # Add MCP-specific concerns (validation, context)
    # Call standalone implementation
    # Format for MCP protocol
    return core_function(params)
```

### Infrastructure Pattern
```python
# Infrastructure models and utilities
class ToolModel(BaseModel):
    # Well-designed Pydantic models
    # Comprehensive validation
    # Clear documentation
```

### Integration Pattern (Acceptable)
```python
# Both MCP and standalone use shared services
from integrations.shared_service import SharedService
# Acceptable when tools access shared services differently
```

## For New Tool Audits

When new tools are added to the system:

1. **Use the established 4-phase process** as outlined above
2. **Follow GOLD STANDARD patterns** from existing approved tools
3. **Update the audit registry** in `docs/plan/AUDITS.md`
4. **Ensure architectural consistency** with the established codebase patterns
5. **Document lessons learned** for future audit improvements

### Tool Categories to Audit
- **Standalone Tools**: In `/tools/` directory - core implementation layer
- **MCP Server Tools**: Wrapper functions in MCP servers
- **Infrastructure**: Models, utilities, and shared components
- **Integration Tools**: External service connectors

### Audit Documentation
- **Update** `docs/plan/AUDITS.md` with new audit results
- **Create** detailed audit reports for comprehensive audits
- **Document** any new patterns or architectural decisions
- **Preserve** audit methodology improvements

## System Health Maintenance

The audit system supports ongoing system health through:

- **Quality Standards**: Consistent standards across all tools
- **Architecture Compliance**: Ensuring patterns remain consistent
- **Technical Debt Prevention**: Catching issues before they accumulate
- **Documentation Currency**: Keeping documentation accurate and helpful

## Historical Context

**December 2024 Audit Program**:
- ✅ **12 tools audited**: Comprehensive validation completed
- ✅ **Architecture crisis resolved**: All duplications eliminated
- ✅ **GOLD STANDARD patterns**: Established throughout codebase
- ✅ **Quality improvements**: Enhanced testing, documentation, security
- ✅ **System consolidation**: Clean, maintainable architecture achieved

**Audit Registry**: Complete history available in `docs/plan/AUDITS.md`

---

**Last Updated**: December 30, 2024  
**All Current Tools**: ✅ Audited and approved  
**System Status**: ✅ Production-ready and maintainable