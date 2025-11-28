"""
Unit Tests for Stripe Payment Processor

Tests operation validation and module behavior without external API calls.
"""

import pytest
from modules.framework.contracts import ModuleInput, ExecutionStatus
from mcp_servers.stripe_payment.src.processor import StripePaymentProcessorModule


@pytest.fixture
def module():
    """Create module instance for testing."""
    return StripePaymentProcessorModule()


class TestModuleBasics:
    """Test basic module functionality."""

    def test_module_id(self, module):
        """Test module has correct ID."""
        assert module.module_id == "stripe_payment"

    def test_module_name(self, module):
        """Test module has correct name."""
        assert module.name == "Stripe Payment Processor"

    def test_supported_operations(self, module):
        """Test module reports correct operations."""
        ops = module.get_supported_operations()
        expected = {"charge-customer", "process-refund", "create-subscription", "cancel-subscription", "validate-payment-method"}
        assert ops == expected

    def test_capabilities(self, module):
        """Test module reports correct capabilities."""
        caps = module.get_capabilities()
        assert "payment" == caps.category
        assert len(caps.capabilities) > 0


class TestInputValidation:
    """Test input validation."""

    def test_invalid_operation_rejected(self, module):
        """Test that invalid operations are rejected."""
        error = module.validate_operation("invalid_operation")
        assert error is not None
        assert "Unsupported operation" in error

    def test_valid_charge_customer_operation(self, module):
        """Test charge-customer operation is valid."""
        error = module.validate_operation("charge-customer")
        assert error is None

    def test_valid_process_refund_operation(self, module):
        """Test process-refund operation is valid."""
        error = module.validate_operation("process-refund")
        assert error is None

    def test_valid_create_subscription_operation(self, module):
        """Test create-subscription operation is valid."""
        error = module.validate_operation("create-subscription")
        assert error is None

    def test_valid_cancel_subscription_operation(self, module):
        """Test cancel-subscription operation is valid."""
        error = module.validate_operation("cancel-subscription")
        assert error is None

    def test_valid_validate_payment_method_operation(self, module):
        """Test validate-payment-method operation is valid."""
        error = module.validate_operation("validate-payment-method")
        assert error is None



class TestDryRun:
    """Test dry run functionality."""

    @pytest.mark.asyncio
    async def test_dry_run_does_not_execute(self, module):
        """Test dry run validates but doesn't execute."""
        input_data = ModuleInput(
            operation="charge-customer",
            parameters={},
            dry_run=True,
        )
        result = await module.execute(input_data)
        assert result.status == ExecutionStatus.SUCCESS
        assert result.data.get("dry_run") is True
