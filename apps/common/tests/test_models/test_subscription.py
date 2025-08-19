"""
Tests for the Subscription model.
"""

from datetime import timedelta
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from apps.common.models import Subscription, User


class SubscriptionTestCase(TestCase):
    """Test case for the Subscription model."""

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
            current_period_start=timezone.now(),
            current_period_end=timezone.now() + timedelta(days=30),
            plan_name="Test Plan",
            plan_description="A test subscription plan",
            price=1995,  # $19.95
            user=self.user,
        )

    def test_subscription_creation(self):
        """Test that a subscription can be created."""
        self.assertEqual(self.subscription.stripe_id, "sub_test123")
        self.assertEqual(self.subscription.status, Subscription.STATUS_ACTIVE)
        self.assertEqual(self.subscription.user, self.user)
        self.assertEqual(self.subscription.plan_name, "Test Plan")

    def test_active_property(self):
        """Test the active property."""
        # Active subscription
        self.assertTrue(self.subscription.active)

        # Canceled subscription
        self.subscription.status = Subscription.STATUS_CANCELED
        self.subscription.save()
        self.assertFalse(self.subscription.active)

        # Past due but within period
        self.subscription.status = Subscription.STATUS_ACTIVE
        self.subscription.save()
        self.assertTrue(self.subscription.active)

        # Expired subscription
        self.subscription.current_period_end = timezone.now() - timedelta(days=1)
        self.subscription.save()
        self.assertFalse(self.subscription.active)

    def test_is_trialing_property(self):
        """Test the is_trialing property."""
        # Not trialing
        self.assertFalse(self.subscription.is_trialing)

        # Trialing
        self.subscription.status = Subscription.STATUS_TRIALING
        self.subscription.save()
        self.assertTrue(self.subscription.is_trialing)

    def test_is_canceled_property(self):
        """Test the is_canceled property."""
        # Not canceled
        self.assertFalse(self.subscription.is_canceled)

        # Canceled status
        self.subscription.status = Subscription.STATUS_CANCELED
        self.subscription.save()
        self.assertTrue(self.subscription.is_canceled)

        # Cancel at period end
        self.subscription.status = Subscription.STATUS_ACTIVE
        self.subscription.cancel_at_period_end = True
        self.subscription.save()
        self.assertTrue(self.subscription.is_canceled)

    def test_days_until_renewal_property(self):
        """Test the days_until_renewal property."""
        from datetime import datetime

        import pytz

        # Future renewal with fixed datetime (to avoid timing issues)
        now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=pytz.UTC)
        future = now + timedelta(days=15)

        with mock.patch("django.utils.timezone.now", return_value=now):
            self.subscription.current_period_end = future
            self.subscription.save()
            self.assertEqual(self.subscription.days_until_renewal, 15)

            # Past renewal
            past = now - timedelta(days=5)
            self.subscription.current_period_end = past
            self.subscription.save()
            self.assertEqual(self.subscription.days_until_renewal, 0)

            # No renewal date
            self.subscription.current_period_end = None
            self.subscription.save()
            self.assertEqual(self.subscription.days_until_renewal, 0)

    def test_owner_display_property(self):
        """Test the owner_display property."""
        # User subscription
        self.assertEqual(self.subscription.owner_display, f"User: {self.user.email}")

        # No owner
        self.subscription.user = None
        self.subscription.save()
        self.assertEqual(self.subscription.owner_display, "Unknown")
