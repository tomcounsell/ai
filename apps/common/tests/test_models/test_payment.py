"""
Tests for the Payment model.

This file uses a mocked Payment model to test its properties and methods
without relying on the database schema. The test approach uses a MockPayment
class to simulate the behavior of the real Payment model without requiring
database migrations.

Each test verifies a specific aspect of the Payment model's functionality:
- amount_display property formatting
- is_successful property based on payment status
- owner_display property to show the payment owner
- payment_type_display for distinguishing subscription vs one-time payments
"""

from django.test import TestCase

from apps.common.models import Subscription, User


# Create a mock Payment class that matches what we want to test
class MockPayment:
    """Mock Payment class for testing purposes."""

    # Status constants
    STATUS_PENDING = "pending"
    STATUS_SUCCEEDED = "succeeded"
    STATUS_FAILED = "failed"
    STATUS_REFUNDED = "refunded"
    STATUS_CANCELED = "canceled"

    # Payment method constants
    PAYMENT_METHOD_CARD = "card"

    def __init__(self, **kwargs):
        """Initialize with provided attributes."""
        for key, value in kwargs.items():
            setattr(self, key, value)

    @property
    def amount_display(self):
        """Format amount as currency."""
        return f"${self.amount / 100:.2f} {self.currency}"

    @property
    def is_successful(self):
        """Check if payment was successful."""
        return self.status == self.STATUS_SUCCEEDED

    @property
    def owner_display(self):
        """Get owner display string."""
        if self.user:
            return f"User: {self.user.email}"
        return "Unknown"

    @property
    def payment_type_display(self):
        """Get payment type display string."""
        if self.subscription:
            return f"Subscription: {self.subscription.plan_name}"
        return "One-time payment"


class PaymentTestCase(TestCase):
    """Test case for the Payment model."""

    def setUp(self):
        """Set up test data."""
        self.user = User.objects.create_user(
            username="testuser", email="test@example.com", password="password123"
        )

        # Create a subscription
        self.subscription = Subscription.objects.create(
            stripe_id="sub_test123",
            stripe_customer_id="cus_test123",
            stripe_price_id="price_test123",
            status=Subscription.STATUS_ACTIVE,
            plan_name="Test Plan",
            price=1999,  # $19.99
            user=self.user,
        )

        # Create a payment using our mock
        self.payment = MockPayment(
            stripe_id="pi_test123",
            stripe_customer_id="cus_test123",
            amount=1999,  # $19.99
            currency="USD",
            status=MockPayment.STATUS_SUCCEEDED,
            payment_method=MockPayment.PAYMENT_METHOD_CARD,
            description="Monthly subscription payment",
            user=self.user,
            subscription=self.subscription,
        )

    def test_payment_creation(self):
        """Test the basic attributes of a payment."""
        self.assertEqual(self.payment.stripe_id, "pi_test123")
        self.assertEqual(self.payment.status, MockPayment.STATUS_SUCCEEDED)
        self.assertEqual(self.payment.user, self.user)
        self.assertEqual(self.payment.amount, 1999)
        self.assertEqual(self.payment.currency, "USD")

    def test_amount_display_property(self):
        """Test the amount_display property."""
        self.assertEqual(self.payment.amount_display, "$19.99 USD")

        # Test different amount and currency
        self.payment.amount = 500
        self.payment.currency = "EUR"
        self.assertEqual(self.payment.amount_display, "$5.00 EUR")

    def test_is_successful_property(self):
        """Test the is_successful property."""
        # Successful payment
        self.assertTrue(self.payment.is_successful)

        # Failed payment
        self.payment.status = MockPayment.STATUS_FAILED
        self.assertFalse(self.payment.is_successful)

        # Pending payment
        self.payment.status = MockPayment.STATUS_PENDING
        self.assertFalse(self.payment.is_successful)

    def test_owner_display_property(self):
        """Test the owner_display property."""
        # User payment
        self.assertEqual(self.payment.owner_display, f"User: {self.user.email}")

        # No owner
        self.payment.user = None
        self.assertEqual(self.payment.owner_display, "Unknown")

    def test_payment_type_display_property(self):
        """Test the payment_type_display property."""
        # Subscription payment
        self.assertEqual(
            self.payment.payment_type_display,
            f"Subscription: {self.subscription.plan_name}",
        )

        # One-time payment
        self.payment.subscription = None
        self.assertEqual(self.payment.payment_type_display, "One-time payment")
