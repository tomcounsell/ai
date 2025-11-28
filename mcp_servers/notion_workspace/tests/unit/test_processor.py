"""
Unit Tests for Notion Workspace

Tests module structure, validation, and behavior without external API calls.
These tests verify the module is correctly configured and validates inputs.
"""

import pytest
from modules.framework.contracts import ModuleInput, ExecutionStatus
from mcp_servers.notion_workspace.src.processor import NotionWorkspaceModule


@pytest.fixture
def module():
    """Create module instance for testing."""
    return NotionWorkspaceModule()


class TestModuleStructure:
    """Test basic module structure and metadata."""

    def test_module_id(self, module):
        """Test module has correct ID."""
        assert module.module_id == "notion_workspace"

    def test_module_name(self, module):
        """Test module has correct name."""
        assert module.name == "Notion Workspace"

    def test_version(self, module):
        """Test module has correct version."""
        assert module.version == "1.0.0"

    def test_supported_operations(self, module):
        """Test module reports correct operations."""
        ops = module.get_supported_operations()
        expected = {"search", "get-page", "create-page", "query-database", "update-page"}
        assert ops == expected

    def test_capabilities(self, module):
        """Test module reports correct capabilities."""
        caps = module.get_capabilities()
        assert caps.category == "productivity"
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
            assert error is None, f"Operation {op} should be valid"

    @pytest.mark.asyncio
    async def test_search_missing_required_params(self, module):
        """Test search fails with missing required parameters."""
        input_data = ModuleInput(
            operation="search",
            parameters={},  # Missing required params
        )
        result = await module.execute(input_data)
        assert result.status == ExecutionStatus.FAILURE
        assert result.error is not None
        assert "missing" in result.error.message.lower() or "required" in result.error.message.lower()

    @pytest.mark.asyncio
    async def test_get_page_missing_required_params(self, module):
        """Test get-page fails with missing required parameters."""
        input_data = ModuleInput(
            operation="get-page",
            parameters={},  # Missing required params
        )
        result = await module.execute(input_data)
        assert result.status == ExecutionStatus.FAILURE
        assert result.error is not None
        assert "missing" in result.error.message.lower() or "required" in result.error.message.lower()

    @pytest.mark.asyncio
    async def test_create_page_missing_required_params(self, module):
        """Test create-page fails with missing required parameters."""
        input_data = ModuleInput(
            operation="create-page",
            parameters={},  # Missing required params
        )
        result = await module.execute(input_data)
        assert result.status == ExecutionStatus.FAILURE
        assert result.error is not None
        assert "missing" in result.error.message.lower() or "required" in result.error.message.lower()

    @pytest.mark.asyncio
    async def test_query_database_missing_required_params(self, module):
        """Test query-database fails with missing required parameters."""
        input_data = ModuleInput(
            operation="query-database",
            parameters={},  # Missing required params
        )
        result = await module.execute(input_data)
        assert result.status == ExecutionStatus.FAILURE
        assert result.error is not None
        assert "missing" in result.error.message.lower() or "required" in result.error.message.lower()

    @pytest.mark.asyncio
    async def test_update_page_missing_required_params(self, module):
        """Test update-page fails with missing required parameters."""
        input_data = ModuleInput(
            operation="update-page",
            parameters={},  # Missing required params
        )
        result = await module.execute(input_data)
        assert result.status == ExecutionStatus.FAILURE
        assert result.error is not None
        assert "missing" in result.error.message.lower() or "required" in result.error.message.lower()



class TestDryRun:
    """Test dry run functionality."""

    @pytest.mark.asyncio
    async def test_dry_run_does_not_execute(self, module):
        """Test dry run validates operation but skips execution."""
        input_data = ModuleInput(
            operation="search",
            parameters={},  # Empty params OK for dry run
            dry_run=True,
        )
        result = await module.execute(input_data)
        assert result.status == ExecutionStatus.SUCCESS
        assert result.data.get("dry_run") is True
        assert result.data.get("would_execute") == "search"


class TestHealthCheck:
    """Test module health check."""

    def test_health_check_returns_status(self, module):
        """Test health check returns valid status."""
        health = module.health_check()
        assert "healthy" in health
        assert "module_id" in health
        assert health["module_id"] == "notion_workspace"

    def test_metrics_tracking(self, module):
        """Test metrics are tracked correctly."""
        metrics = module.get_metrics()
        assert "total_executions" in metrics
        assert "success_rate" in metrics
        assert metrics["module_id"] == "notion_workspace"
