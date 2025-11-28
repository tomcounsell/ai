"""
Module Templates for Code Generation

Provides template strings for generating module components:
- module.yaml specification
- Python implementation
- Tests
- Documentation
"""

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class ModuleTemplate:
    """Container for module templates."""

    # Module specification template
    MODULE_YAML = '''metadata:
  id: "{module_id}"
  name: "{name}"
  version: "{version}"
  type: "{module_type}"
  category: "{category}"

  author:
    name: "Module Builder Agent"
    email: "noreply@example.com"

  created: "{created_at}"
  updated: "{created_at}"

description:
  short: "{description_short}"
  long: |
    {description_long}

capabilities:
{capabilities_yaml}

operations:
{operations_yaml}

dependencies:
  runtime:
    - "python>=3.9"
    - "pydantic>=2.0.0"
{runtime_deps_yaml}

  external_services:
{external_services_yaml}

  internal_modules:
    - "modules.framework"

interface:
  input_schema: "schemas/input.json"
  output_schema: "schemas/output.json"
  error_schema: "schemas/error.json"

configuration:
  auth_requirements:
{auth_requirements_yaml}

  settings:
    - name: "timeout"
      type: "integer"
      default: 30
      required: false
    - name: "retry_attempts"
      type: "integer"
      default: 3
      required: false

testing:
  has_unit_tests: true
  has_integration_tests: true
  test_coverage_target: 90
  real_api_tests: true

quality:
  standard: "9.8/10"
  linting: "ruff"
  formatting: "black"
  type_checking: "mypy --strict"

documentation:
  readme: "README.md"
  api_reference: "docs/API.md"
  examples: "examples/"

discovery:
  tags:
{tags_yaml}

  search_keywords:
{keywords_yaml}

  use_cases:
{use_cases_yaml}

health:
  validation_command: "pytest tests/"
'''

    # Python module implementation template
    MODULE_IMPL = '''"""
{name} Module Implementation

{description_long}

Operations:
{operations_list}
"""

import logging
from typing import Any, Dict, Optional, Set

from modules.framework.base import BaseModule, ModuleCapabilities


class {class_name}(BaseModule):
    """
    {description_short}

    Capabilities: {capabilities_list}
    """

    def __init__(
        self,
        logger: Optional[logging.Logger] = None,
    ):
        super().__init__(
            module_id="{module_id}",
            name="{name}",
            version="{version}",
            description="{description_short}",
            logger=logger,
        )

        # Initialize any clients or connections here
        self._initialized = False

    def get_supported_operations(self) -> Set[str]:
        """Return the set of operations this module supports."""
        return {{{operations_set}}}

    def get_capabilities(self) -> ModuleCapabilities:
        """Return module capabilities for discovery."""
        return ModuleCapabilities(
            operations=list(self.get_supported_operations()),
            capabilities={capabilities_list_python},
            tags={tags_list_python},
            category="{category}",
        )

    async def _execute_operation(
        self,
        operation: str,
        parameters: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Execute the core operation logic.

        Args:
            operation: The operation to perform
            parameters: Operation-specific parameters
            context: Execution context

        Returns:
            Dict with operation results
        """
{operation_handlers}

        raise ValueError(f"Unknown operation: {{operation}}")

{operation_methods}
'''

    # Test file template
    TEST_UNIT = '''"""
Unit Tests for {name}

Tests operation validation and module behavior without external API calls.
"""

import pytest
from modules.framework.contracts import ModuleInput, ExecutionStatus
from {module_import} import {class_name}


@pytest.fixture
def module():
    """Create module instance for testing."""
    return {class_name}()


class TestModuleBasics:
    """Test basic module functionality."""

    def test_module_id(self, module):
        """Test module has correct ID."""
        assert module.module_id == "{module_id}"

    def test_module_name(self, module):
        """Test module has correct name."""
        assert module.name == "{name}"

    def test_supported_operations(self, module):
        """Test module reports correct operations."""
        ops = module.get_supported_operations()
        expected = {{{operations_set}}}
        assert ops == expected

    def test_capabilities(self, module):
        """Test module reports correct capabilities."""
        caps = module.get_capabilities()
        assert "{category}" == caps.category
        assert len(caps.capabilities) > 0


class TestInputValidation:
    """Test input validation."""

    def test_invalid_operation_rejected(self, module):
        """Test that invalid operations are rejected."""
        error = module.validate_operation("invalid_operation")
        assert error is not None
        assert "Unsupported operation" in error

{operation_tests}


class TestDryRun:
    """Test dry run functionality."""

    @pytest.mark.asyncio
    async def test_dry_run_does_not_execute(self, module):
        """Test dry run validates but doesn't execute."""
        input_data = ModuleInput(
            operation="{first_operation}",
            parameters={{}},
            dry_run=True,
        )
        result = await module.execute(input_data)
        assert result.status == ExecutionStatus.SUCCESS
        assert result.data.get("dry_run") is True
'''

    # Integration test template
    TEST_INTEGRATION = '''"""
Integration Tests for {name}

Tests real API interactions. Requires valid API keys in environment.
NO MOCKS - Tests real service calls.
"""

import os
import pytest
from modules.framework.contracts import ModuleInput, ExecutionStatus
from {module_import} import {class_name}


# Skip if API key not available
pytestmark = pytest.mark.skipif(
    not os.environ.get("{api_key_env}"),
    reason="{api_key_env} not set"
)


@pytest.fixture
def module():
    """Create module instance for testing."""
    return {class_name}()


{integration_tests}
'''

    # README template
    README = '''# {name}

{description_short}

## Overview

{description_long}

## Installation

This module is part of the ai system. No separate installation required.

## Configuration

### Required Environment Variables

{env_vars_section}

### Settings

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| timeout | integer | 30 | Maximum operation time in seconds |
| retry_attempts | integer | 3 | Number of retry attempts |

## Usage

```python
from modules.framework.contracts import ModuleInput
from {module_import} import {class_name}

# Create module instance
module = {class_name}()

# Execute an operation
input_data = ModuleInput(
    operation="{first_operation}",
    parameters={{
        # Operation-specific parameters
    }},
)

result = await module.execute(input_data)

if result.status == "success":
    print(result.data)
else:
    print(f"Error: {{result.error.message}}")
```

## Operations

{operations_docs}

## Error Handling

All operations return a `ModuleOutput` with:
- `status`: success | partial_success | failure | error
- `data`: Result data (on success)
- `error`: ErrorDetail (on failure)
- `side_effects`: List of side effects
- `warnings`: Non-fatal warnings

## Testing

```bash
# Run all tests
pytest {test_path}

# Run only unit tests
pytest {test_path}/unit/

# Run integration tests (requires API key)
pytest {test_path}/integration/
```

## Quality

- Quality Standard: 9.8/10
- Test Coverage Target: >90%
- Real API Tests: Yes (no mocks)
'''

    # JSON Schema template for input
    INPUT_SCHEMA = '''{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "{name} Input Schema",
  "type": "object",
  "required": ["operation"],
  "properties": {
    "operation": {
      "type": "string",
      "enum": {operations_json},
      "description": "Operation to perform"
    },
    "parameters": {
      "type": "object",
      "description": "Operation-specific parameters",
      "oneOf": [
{operation_schemas}
      ]
    },
    "dry_run": {
      "type": "boolean",
      "default": false,
      "description": "Validate without executing"
    }
  }
}
'''

    # JSON Schema template for output
    OUTPUT_SCHEMA = '''{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "{name} Output Schema",
  "type": "object",
  "required": ["status", "request_id", "execution_time_ms"],
  "properties": {
    "status": {
      "type": "string",
      "enum": ["success", "partial_success", "failure", "error"]
    },
    "data": {
      "type": "object",
      "description": "Operation result data"
    },
    "error": {
      "$ref": "#/definitions/ErrorDetail"
    },
    "request_id": {
      "type": "string"
    },
    "execution_time_ms": {
      "type": "integer"
    },
    "side_effects": {
      "type": "array",
      "items": {
        "$ref": "#/definitions/SideEffect"
      }
    },
    "warnings": {
      "type": "array",
      "items": {"type": "string"}
    },
    "recommendations": {
      "type": "array",
      "items": {"type": "string"}
    }
  },
  "definitions": {
    "ErrorDetail": {
      "type": "object",
      "required": ["code", "message", "category"],
      "properties": {
        "code": {"type": "string"},
        "message": {"type": "string"},
        "category": {"type": "string"},
        "recoverable": {"type": "boolean"},
        "recovery_suggestion": {"type": "string"}
      }
    },
    "SideEffect": {
      "type": "object",
      "required": ["type", "description", "target"],
      "properties": {
        "type": {"type": "string"},
        "description": {"type": "string"},
        "target": {"type": "string"},
        "reversible": {"type": "boolean"}
      }
    }
  }
}
'''

    # Error schema template
    ERROR_SCHEMA = '''{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "{name} Error Schema",
  "type": "object",
  "required": ["code", "message", "category"],
  "properties": {
    "code": {
      "type": "string",
      "description": "Error code (e.g., '{MODULE_ID}_API_ERROR')"
    },
    "message": {
      "type": "string",
      "description": "Human-readable error message"
    },
    "category": {
      "type": "string",
      "enum": ["validation", "auth", "api", "internal", "timeout"],
      "description": "Error category for handling"
    },
    "recoverable": {
      "type": "boolean",
      "description": "Whether this error can be retried"
    },
    "recovery_suggestion": {
      "type": "string",
      "description": "Suggested action to recover"
    },
    "details": {
      "type": "object",
      "description": "Additional error context"
    }
  }
}
'''


# Helper functions for template rendering
def to_yaml_list(items: List[str], indent: int = 2) -> str:
    """Convert list to YAML format."""
    prefix = " " * indent
    if not items:
        return f"{prefix}[]"
    return "\n".join(f'{prefix}- "{item}"' for item in items)


def to_python_list(items: List[str]) -> str:
    """Convert list to Python list literal."""
    if not items:
        return "[]"
    quoted = [f'"{item}"' for item in items]
    return "[" + ", ".join(quoted) + "]"


def to_python_set(items: List[str]) -> str:
    """Convert list to Python set literal contents."""
    if not items:
        return ""
    quoted = [f'"{item}"' for item in items]
    return ", ".join(quoted)
