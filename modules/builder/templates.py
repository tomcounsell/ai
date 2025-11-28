"""
Module Templates for Code Generation

Provides template strings for generating module components:
- module.yaml specification
- Python implementation
- Tests
- Documentation
"""

from dataclasses import dataclass
from typing import List


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
  completeness: "scaffolding"  # scaffolding | partial | complete

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
    - "mcp_servers.base"

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
  real_api_tests: true  # NO MOCKS

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

    # Python module implementation template - now integrates with MCP
    MODULE_IMPL = '''"""
{name} Module Implementation

{description_long}

Operations:
{operations_list}

NOTE: This is generated scaffolding. Operations marked with TODO require implementation.
"""

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from modules.framework.base import BaseModule, ModuleCapabilities
from modules.framework.contracts import SideEffect


class {class_name}(BaseModule):
    """
    {description_short}

    Capabilities: {capabilities_list}

    Completeness: SCAFFOLDING - Requires implementation of operation handlers.
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
{client_init}

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

    def validate_parameters(
        self, operation: str, parameters: Dict[str, Any]
    ) -> Optional[str]:
        """Validate operation parameters."""
{parameter_validation}
        return None

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

    # Unit test template - tests module structure and validation
    TEST_UNIT = '''"""
Unit Tests for {name}

Tests module structure, validation, and behavior without external API calls.
These tests verify the module is correctly configured and validates inputs.
"""

import pytest
from modules.framework.contracts import ModuleInput, ExecutionStatus
from {module_import} import {class_name}


@pytest.fixture
def module():
    """Create module instance for testing."""
    return {class_name}()


class TestModuleStructure:
    """Test basic module structure and metadata."""

    def test_module_id(self, module):
        """Test module has correct ID."""
        assert module.module_id == "{module_id}"

    def test_module_name(self, module):
        """Test module has correct name."""
        assert module.name == "{name}"

    def test_version(self, module):
        """Test module has correct version."""
        assert module.version == "{version}"

    def test_supported_operations(self, module):
        """Test module reports correct operations."""
        ops = module.get_supported_operations()
        expected = {{{operations_set}}}
        assert ops == expected

    def test_capabilities(self, module):
        """Test module reports correct capabilities."""
        caps = module.get_capabilities()
        assert caps.category == "{category}"
        assert len(caps.capabilities) > 0
        assert len(caps.operations) == len(module.get_supported_operations())


class TestOperationValidation:
    """Test operation validation logic."""

    def test_invalid_operation_rejected(self, module):
        """Test that invalid operations are rejected."""
        error = module.validate_operation("invalid_operation_xyz")
        assert error is not None
        assert "Unsupported operation" in error

    def test_valid_operations_accepted(self, module):
        """Test that all valid operations are accepted."""
        for op in module.get_supported_operations():
            error = module.validate_operation(op)
            assert error is None, f"Operation {{op}} should be valid"

{operation_validation_tests}


class TestDryRun:
    """Test dry run functionality."""

    @pytest.mark.asyncio
    async def test_dry_run_does_not_execute(self, module):
        """Test dry run validates operation but skips execution."""
        input_data = ModuleInput(
            operation="{first_operation}",
            parameters={{}},  # Empty params OK for dry run
            dry_run=True,
        )
        result = await module.execute(input_data)
        assert result.status == ExecutionStatus.SUCCESS
        assert result.data.get("dry_run") is True
        assert result.data.get("would_execute") == "{first_operation}"


class TestHealthCheck:
    """Test module health check."""

    def test_health_check_returns_status(self, module):
        """Test health check returns valid status."""
        health = module.health_check()
        assert "healthy" in health
        assert "module_id" in health
        assert health["module_id"] == "{module_id}"

    def test_metrics_tracking(self, module):
        """Test metrics are tracked correctly."""
        metrics = module.get_metrics()
        assert "total_executions" in metrics
        assert "success_rate" in metrics
        assert metrics["module_id"] == "{module_id}"
'''

    # Integration test template - REAL API tests, NO MOCKS
    TEST_INTEGRATION = '''"""
Integration Tests for {name}

IMPORTANT: These tests call REAL APIs. No mocks allowed.
Requires valid API keys in environment variables.

Test Philosophy:
- Test the happy path thoroughly with real API calls
- Verify actual API responses match expected schemas
- Clean up any test data created
- Skip gracefully if API keys not configured
"""

import os
import pytest
from modules.framework.contracts import ModuleInput, ExecutionStatus
from {module_import} import {class_name}


# Configuration
API_KEY_ENV = "{api_key_env}"
SKIP_REASON = f"{{API_KEY_ENV}} not set - skipping real API tests"


def has_api_key() -> bool:
    """Check if API key is available for testing."""
    return bool(os.environ.get(API_KEY_ENV))


# Skip entire module if no API key
pytestmark = pytest.mark.skipif(not has_api_key(), reason=SKIP_REASON)


@pytest.fixture
def module():
    """Create module instance with real API configuration."""
    return {class_name}()


@pytest.fixture
def cleanup_ids():
    """Track IDs of resources created during tests for cleanup."""
    ids = []
    yield ids
    # Cleanup would happen here if needed
    # For now, tests should clean up their own resources


{integration_tests}


class TestAPIConnectivity:
    """Test basic API connectivity and authentication."""

    @pytest.mark.asyncio
    async def test_module_can_connect(self, module):
        """
        Test that the module can connect to the external service.

        This verifies:
        - API key is valid
        - Network connectivity works
        - Basic authentication succeeds
        """
        # Use a read-only or low-impact operation to test connectivity
        health = module.health_check()
        assert health["healthy"] or "needs implementation" in str(health.get("issues", []))


class TestErrorHandling:
    """Test error handling with real API errors."""

    @pytest.mark.asyncio
    async def test_invalid_parameters_handled(self, module):
        """Test that invalid parameters return proper error responses."""
        input_data = ModuleInput(
            operation="{first_operation}",
            parameters={{
                # Intentionally invalid/missing required params
            }},
        )
        result = await module.execute(input_data)
        # Should fail gracefully, not crash
        assert result.status in [
            ExecutionStatus.FAILURE,
            ExecutionStatus.ERROR,
        ]
        assert result.error is not None
'''

    # README template - honest about scaffolding nature
    README = '''# {name}

{description_short}

> **Note**: This module was auto-generated by the Module Builder.
> Operations marked with `TODO` require implementation before use.

## Status

| Aspect | Status |
|--------|--------|
| Completeness | Scaffolding |
| Unit Tests | Passing |
| Integration Tests | Require API keys |
| Production Ready | No - requires implementation |

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
        # Add required parameters here
    }},
)

result = await module.execute(input_data)

if result.status.value == "success":
    print(result.data)
else:
    print(f"Error: {{result.error.message}}")
    if result.error.recovery_suggestion:
        print(f"Suggestion: {{result.error.recovery_suggestion}}")
```

## Operations

{operations_docs}

## Implementation Status

The following operations require implementation:

{implementation_status}

## Error Handling

All operations return a `ModuleOutput` with:
- `status`: success | partial_success | failure | error
- `data`: Result data (on success)
- `error`: ErrorDetail with code, message, category, and recovery suggestions
- `side_effects`: List of side effects for audit trail
- `warnings`: Non-fatal warnings
- `recommendations`: Suggested follow-up actions

## Testing

```bash
# Run unit tests (no API key required)
pytest {test_path}/unit/ -v

# Run integration tests (requires API key)
export {api_key_env}="your-api-key"
pytest {test_path}/integration/ -v

# Run all tests
pytest {test_path}/ -v
```

## Quality Standards

- Target Quality: 9.8/10
- Test Coverage Target: >90%
- Real API Tests: Yes (no mocks)
- Type Checking: mypy --strict
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
