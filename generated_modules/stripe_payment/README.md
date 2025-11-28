# Stripe Payment Processor

Process payments, refunds, and subscriptions via Stripe

## Overview

A comprehensive Stripe payment processing module that provides:
- Customer charging with amount validation
- Refund processing with eligibility checks
- Subscription management (create, update, cancel)
- Payment method validation before charging
- Webhook event handling

All operations include comprehensive error handling, validation,
and side effect tracking for auditability.

## Installation

This module is part of the ai system. No separate installation required.

## Configuration

### Required Environment Variables

- `STRIPE_API_KEY`: stripe API key

### Settings

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| timeout | integer | 30 | Maximum operation time in seconds |
| retry_attempts | integer | 3 | Number of retry attempts |

## Usage

```python
from modules.framework.contracts import ModuleInput
from generated_modules.stripe_payment.src.processor import StripePaymentProcessorModule

# Create module instance
module = StripePaymentProcessorModule()

# Execute an operation
input_data = ModuleInput(
    operation="charge-customer",
    parameters={
        # Operation-specific parameters
    },
)

result = await module.execute(input_data)

if result.status == "success":
    print(result.data)
else:
    print(f"Error: {result.error.message}")
```

## Operations

### charge-customer

Charge a customer for a one-time payment

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| customer_id | string | Yes | Stripe customer ID (cus_xxx) |
| amount | integer | Yes | Amount in cents (e.g., 1000 for $10.00) |
| currency | string | No | Currency code (default: usd) |
| description | string | No | Charge description |
| metadata | object | No | Additional metadata for the charge |


### process-refund

Process a refund for a previous charge

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| charge_id | string | Yes | Stripe charge ID to refund (ch_xxx) |
| amount | integer | No | Amount to refund in cents (default: full refund) |
| reason | string | No | Refund reason: duplicate, fraudulent, requested_by_customer |


### create-subscription

Create a new subscription for a customer

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| customer_id | string | Yes | Stripe customer ID |
| price_id | string | Yes | Stripe price ID for the subscription plan |
| trial_period_days | integer | No | Number of trial days |
| metadata | object | No | Additional subscription metadata |


### cancel-subscription

Cancel an existing subscription

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| subscription_id | string | Yes | Stripe subscription ID (sub_xxx) |
| immediately | boolean | No | Cancel immediately or at period end (default: period end) |


### validate-payment-method

Validate a payment method is ready for charging

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| payment_method_id | string | Yes | Stripe payment method ID (pm_xxx) |


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
pytest generated_modules/stripe_payment/tests

# Run only unit tests
pytest generated_modules/stripe_payment/tests/unit/

# Run integration tests (requires API key)
pytest generated_modules/stripe_payment/tests/integration/
```

## Quality

- Quality Standard: 9.8/10
- Test Coverage Target: >90%
- Real API Tests: Yes (no mocks)
