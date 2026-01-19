# [Workflow Name] SOP

**Version**: 1.0.0
**Last Updated**: YYYY-MM-DD
**Owner**: [Team/Person]
**Status**: [Draft|Active|Deprecated]

## Overview

Brief description of what this SOP accomplishes and when to use it.

## Prerequisites

- Required setup or conditions
- Tools that must be available
- Permissions needed

## Parameters

### Required
- **param_name** (type): Description and constraints
  - Example: `customer_id`
  - Type: `string`
  - Format: `cus_[a-zA-Z0-9]+`

### Optional
- **param_name** (type): Description with default value
  - Default: `value`

## Steps

### 1. [Step Name]
**Purpose**: Why this step is necessary

**Actions**:
- MUST perform required action
- SHOULD perform recommended action
- MAY perform optional action

**Validation**:
- How to verify this step succeeded

**Error Handling**:
- What to do if this step fails

### 2. [Next Step]
[Continue pattern...]

## Success Criteria

How to know the workflow completed successfully.

## Error Recovery

Common errors and how to recover:
- **Error Type**: Recovery procedure
- **Error Type**: Recovery procedure

## Examples

### Example 1: [Use Case]
```
Input:
  param1: value1
  param2: value2

Expected Output:
  result: success
  data: {...}
```

## Related SOPs

- [Related SOP](path/to/sop.md)
- [Parent SOP](path/to/parent.md)

## Version History

- v1.0.0 (YYYY-MM-DD): Initial version
