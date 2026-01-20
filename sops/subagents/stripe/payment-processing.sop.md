# Payment Processing SOP

**Version**: 1.0.0
**Last Updated**: 2026-01-20
**Owner**: Valor AI System
**Status**: Active

## Overview

This SOP defines the standard procedure for processing payments through the Stripe integration. It covers charges, refunds, and subscription operations with appropriate security and validation measures.

## Prerequisites

- STRIPE_API_KEY environment variable configured
- Valid Stripe account with appropriate permissions
- Customer ID or payment method available

## Parameters

### Required
- **operation** (string): Type of payment operation
  - Values: `charge` | `refund` | `subscription`
  - Description: The payment operation to perform

- **customer_id** (string): Stripe customer identifier
  - Format: `cus_[a-zA-Z0-9]+`
  - Description: The customer to process payment for

### Optional
- **amount** (integer): Amount in cents
  - Default: Required for charge operations
  - Constraints: Must be positive, max 99999999

- **currency** (string): Three-letter ISO currency code
  - Default: `usd`
  - Example: `usd`, `eur`, `gbp`

- **idempotency_key** (string): Key for duplicate prevention
  - Default: Auto-generated UUID
  - Description: Prevents duplicate charges on retry

- **description** (string): Payment description
  - Default: None
  - Max length: 500 characters

## Steps

### 1. Validate Input

**Purpose**: Ensure all required parameters are valid before processing.

**Actions**:
- MUST verify customer_id exists in Stripe
- MUST validate amount is within acceptable limits
- MUST check payment method is attached to customer
- SHOULD verify customer is not blocked or flagged
- MAY check fraud detection score if available

**Validation**:
- Customer lookup returns valid customer object
- Amount is positive and within account limits

**Error Handling**:
- If customer not found: Return error with "Customer not found" message
- If no payment method: Return error with "No payment method available"

### 2. Prepare Payment Request

**Purpose**: Build the payment request with all necessary parameters.

**Actions**:
- MUST include idempotency_key for all operations
- MUST set appropriate metadata for tracking
- SHOULD include description for record keeping
- MAY add statement descriptor if configured

**Validation**:
- Request object contains all required fields

### 3. Execute Operation

**Purpose**: Process the payment through Stripe API.

**Actions**:
- MUST use appropriate Stripe API endpoint for operation type
- MUST handle API rate limits with exponential backoff
- MUST log transaction details for audit trail
- SHOULD set appropriate timeout (30 seconds)

**For Charges**:
```python
stripe.PaymentIntent.create(
    amount=amount,
    currency=currency,
    customer=customer_id,
    idempotency_key=idempotency_key,
)
```

**For Refunds**:
```python
stripe.Refund.create(
    payment_intent=payment_intent_id,
    amount=refund_amount,  # Optional for partial refund
    idempotency_key=idempotency_key,
)
```

**Validation**:
- API returns success status
- Transaction ID is received

### 4. Handle Response

**Purpose**: Process the API response and determine next steps.

**Actions**:
- MUST verify payment status is successful
- MUST store transaction ID for reference
- SHOULD send confirmation notification
- MAY trigger webhook handlers

**Validation**:
- Payment status is `succeeded` or `processing`

**Error Handling**:
- If payment fails: Log error, return failure details
- If network error: Retry with exponential backoff (max 3 retries)
- MUST NOT retry payment failures (only network/timeout errors)

### 5. Post-Processing

**Purpose**: Complete any follow-up actions after successful payment.

**Actions**:
- MUST update internal records with transaction details
- SHOULD send receipt to customer if configured
- MAY update subscription status for subscription operations
- MAY trigger analytics tracking

## Success Criteria

- Payment processed successfully with transaction ID
- Transaction logged to audit trail
- Customer notified (if configured)
- Internal records updated

## Error Recovery

| Error Type | Recovery Procedure |
|------------|-------------------|
| Customer not found | Verify customer ID, check if deleted |
| Card declined | Notify user, suggest alternative payment |
| Rate limited | Wait and retry with exponential backoff |
| Network timeout | Retry up to 3 times with backoff |
| Insufficient funds | Notify user, do not retry |
| Fraud detected | Block transaction, escalate to supervisor |

## Security Considerations

- MUST NOT log full card numbers or CVV
- MUST use idempotency keys to prevent duplicate charges
- MUST validate all input parameters
- SHOULD require confirmation for refunds > $100
- SHOULD log all operations for audit purposes

## Examples

### Example 1: Simple Charge

```
Input:
  operation: charge
  customer_id: cus_ABC123
  amount: 2500
  currency: usd
  description: "Monthly subscription"

Expected Output:
  success: true
  transaction_id: pi_XYZ789
  amount: 2500
  status: succeeded
```

### Example 2: Refund with Confirmation

```
Input:
  operation: refund
  customer_id: cus_ABC123
  payment_intent: pi_XYZ789
  amount: 15000  # $150 - requires confirmation

Expected Output:
  requires_confirmation: true
  message: "Refund amount exceeds $100. Please confirm."
  pending_refund:
    amount: 15000
    payment_intent: pi_XYZ789
```

## Related SOPs

- [Subscription Management](subscription-management.sop.md)
- [Refund Handling](refund-handling.sop.md)

## Version History

- v1.0.0 (2026-01-20): Initial version
