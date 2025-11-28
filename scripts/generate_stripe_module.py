#!/usr/bin/env python3
"""
Generate Stripe Payment Module

Validates the Module Builder framework by generating a complete
Stripe payment processing module.
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from modules.builder.agent import ModuleBuilderAgent, ModuleRequirements


async def main():
    """Generate the Stripe payment module."""
    print("=" * 60)
    print("Module Builder - Stripe Payment Module Generation")
    print("=" * 60)

    # Define Stripe module requirements
    requirements = ModuleRequirements(
        name="Stripe Payment Processor",
        module_id="stripe_payment",
        version="1.0.0",
        module_type="mcp-server",
        category="payment",
        description_short="Process payments, refunds, and subscriptions via Stripe",
        description_long="""
A comprehensive Stripe payment processing module that provides:
- Customer charging with amount validation
- Refund processing with eligibility checks
- Subscription management (create, update, cancel)
- Payment method validation before charging
- Webhook event handling

All operations include comprehensive error handling, validation,
and side effect tracking for auditability.
        """.strip(),
        capabilities=[
            "payment-processing",
            "refund-handling",
            "subscription-management",
            "payment-validation",
            "webhook-handling",
        ],
        operations=[
            {
                "name": "charge-customer",
                "description": "Charge a customer for a one-time payment",
                "parameters": {
                    "customer_id": {
                        "type": "string",
                        "required": True,
                        "description": "Stripe customer ID (cus_xxx)",
                    },
                    "amount": {
                        "type": "integer",
                        "required": True,
                        "description": "Amount in cents (e.g., 1000 for $10.00)",
                    },
                    "currency": {
                        "type": "string",
                        "required": False,
                        "description": "Currency code (default: usd)",
                    },
                    "description": {
                        "type": "string",
                        "required": False,
                        "description": "Charge description",
                    },
                    "metadata": {
                        "type": "object",
                        "required": False,
                        "description": "Additional metadata for the charge",
                    },
                },
            },
            {
                "name": "process-refund",
                "description": "Process a refund for a previous charge",
                "parameters": {
                    "charge_id": {
                        "type": "string",
                        "required": True,
                        "description": "Stripe charge ID to refund (ch_xxx)",
                    },
                    "amount": {
                        "type": "integer",
                        "required": False,
                        "description": "Amount to refund in cents (default: full refund)",
                    },
                    "reason": {
                        "type": "string",
                        "required": False,
                        "description": "Refund reason: duplicate, fraudulent, requested_by_customer",
                    },
                },
            },
            {
                "name": "create-subscription",
                "description": "Create a new subscription for a customer",
                "parameters": {
                    "customer_id": {
                        "type": "string",
                        "required": True,
                        "description": "Stripe customer ID",
                    },
                    "price_id": {
                        "type": "string",
                        "required": True,
                        "description": "Stripe price ID for the subscription plan",
                    },
                    "trial_period_days": {
                        "type": "integer",
                        "required": False,
                        "description": "Number of trial days",
                    },
                    "metadata": {
                        "type": "object",
                        "required": False,
                        "description": "Additional subscription metadata",
                    },
                },
            },
            {
                "name": "cancel-subscription",
                "description": "Cancel an existing subscription",
                "parameters": {
                    "subscription_id": {
                        "type": "string",
                        "required": True,
                        "description": "Stripe subscription ID (sub_xxx)",
                    },
                    "immediately": {
                        "type": "boolean",
                        "required": False,
                        "description": "Cancel immediately or at period end (default: period end)",
                    },
                },
            },
            {
                "name": "validate-payment-method",
                "description": "Validate a payment method is ready for charging",
                "parameters": {
                    "payment_method_id": {
                        "type": "string",
                        "required": True,
                        "description": "Stripe payment method ID (pm_xxx)",
                    },
                },
            },
        ],
        external_services=[
            {
                "name": "stripe",
                "auth_type": "api_key",
                "env_var": "STRIPE_API_KEY",
                "package": "stripe>=5.0.0",
                "required": "true",
            }
        ],
        tags=["payments", "stripe", "financial", "subscriptions", "refunds"],
        search_keywords=[
            "payment",
            "charge",
            "refund",
            "subscription",
            "billing",
            "stripe",
            "credit card",
        ],
        use_cases=[
            "Process customer payments",
            "Handle subscription billing",
            "Issue refunds to customers",
            "Validate payment methods before charging",
        ],
        quality_standard="9.8/10",
        test_coverage=90,
    )

    # Create builder and generate module
    builder = ModuleBuilderAgent(output_dir="generated_modules")
    result = await builder.build_module(requirements, register=True)

    # Report results
    print("\n" + "=" * 60)
    print("Generation Results")
    print("=" * 60)
    print(f"Module ID: {result.module_id}")
    print(f"Name: {result.name}")
    print(f"Path: {result.path}")
    print(f"Success: {result.success}")
    print(f"Generation Time: {result.generation_time_ms}ms")
    print(f"Files Created: {len(result.files_created)}")

    if result.errors:
        print("\nErrors:")
        for error in result.errors:
            print(f"  - {error}")

    print("\nFiles:")
    for f in result.files_created:
        # Show simplified path
        print(f"  - {f}")

    # Verify module can be registered
    print("\n" + "=" * 60)
    print("Registry Verification")
    print("=" * 60)

    from modules.registry.registry import ModuleRegistry

    registry = ModuleRegistry()
    stats = registry.get_stats()
    print(f"Total modules in registry: {stats['total_modules']}")

    entry = registry.get(result.module_id)
    if entry:
        print(f"Module found: {entry.name}")
        print(f"  Type: {entry.type.value}")
        print(f"  Category: {entry.category}")
        print(f"  Capabilities: {', '.join(entry.capabilities)}")
        print(f"  Auth Status: {entry.auth_status.value}")
        print(f"  Quality Score: {entry.quality_score}")
    else:
        print("ERROR: Module not found in registry!")

    return result.success


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
