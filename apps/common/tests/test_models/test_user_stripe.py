"""
Tests for the User model's Stripe-related methods and properties.
"""

import uuid
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.common.models import Payment, Subscription, User


class UserStripeTestCase(TestCase):
    """Test cases for User model's Stripe-related functionality."""

    def setUp(self):
        """Set up test data."""
        # Generate a unique username to avoid UniqueViolation errors
        unique_username = f"testuser_{uuid.uuid4().hex[:8]}"
        self.user = User.objects.create_user(
            username=unique_username,
            email=f"{unique_username}@example.com",
            password="password123",
            stripe_customer_id="cus_test123",
        )

    def test_stripe_customer_id(self):
        """Test stripe_customer_id field."""
        self.assertEqual(self.user.stripe_customer_id, "cus_test123")

        # Update stripe_customer_id
        self.user.stripe_customer_id = "cus_updated456"
        self.user.save()
        self.assertEqual(self.user.stripe_customer_id, "cus_updated456")

    def test_has_stripe_customer_property(self):
        """Test has_stripe_customer property."""
        # Has customer ID
        self.assertTrue(self.user.has_stripe_customer)

        # No customer ID
        self.user.stripe_customer_id = ""
        self.user.save()
        self.assertFalse(self.user.has_stripe_customer)

    def test_has_active_subscription_property(self):
        """Test has_active_subscription property."""
        # No subscriptions initially
        self.assertFalse(self.user.has_active_subscription)

        # Add active subscription
        subscription = Subscription.objects.create(
            stripe_subscription_id="sub_test123",
            price=1999,
            status="active",
            current_period_start=timezone.now(),
            current_period_end=timezone.now() + timedelta(days=30),
            plan_name="Test Plan",
            user=self.user,
        )
        self.assertTrue(self.user.has_active_subscription)

        # Cancel subscription
        subscription.status = "canceled"
        subscription.save()
        self.assertFalse(self.user.has_active_subscription)

    def test_active_subscriptions_property(self):
        """Test active_subscriptions property."""
        # No subscriptions initially
        self.assertEqual(len(self.user.active_subscriptions), 0)

        # Add active subscription
        subscription1 = Subscription.objects.create(
            stripe_subscription_id="sub_test123",
            price=1999,
            status="active",
            current_period_start=timezone.now(),
            current_period_end=timezone.now() + timedelta(days=30),
            plan_name="Test Plan",
            user=self.user,
        )

        # Add trial subscription
        subscription2 = Subscription.objects.create(
            stripe_subscription_id="sub_test456",
            price=2999,
            status="trialing",
            current_period_start=timezone.now(),
            current_period_end=timezone.now() + timedelta(days=14),
            plan_name="Premium Plan",
            user=self.user,
        )

        # Add canceled subscription
        subscription3 = Subscription.objects.create(
            stripe_subscription_id="sub_test789",
            price=999,
            status="canceled",
            current_period_start=timezone.now() - timedelta(days=30),
            current_period_end=timezone.now() - timedelta(days=1),
            plan_name="Canceled Plan",
            user=self.user,
        )

        # Should have 2 active subscriptions
        active_subs = self.user.active_subscriptions
        self.assertEqual(len(active_subs), 2)
        self.assertIn(subscription1, active_subs)
        self.assertIn(subscription2, active_subs)
        self.assertNotIn(subscription3, active_subs)

    def test_get_active_subscription_method(self):
        """Test get_active_subscription method."""
        # No subscriptions initially
        self.assertIsNone(self.user.get_active_subscription())

        # Add active subscription
        subscription = Subscription.objects.create(
            stripe_subscription_id="sub_test123",
            price=1999,
            status="active",
            current_period_start=timezone.now(),
            current_period_end=timezone.now() + timedelta(days=30),
            plan_name="Test Plan",
            user=self.user,
        )

        # Should return the active subscription
        self.assertEqual(self.user.get_active_subscription(), subscription)

    def test_get_payment_history_method(self):
        """Test get_payment_history method."""
        # No payments initially
        self.assertEqual(len(self.user.get_payment_history()), 0)

        # Add payments
        payment1 = Payment.objects.create(
            stripe_payment_intent_id="pi_test123",
            amount=1999,
            status="succeeded",
            user=self.user,
            created_at=timezone.now() - timedelta(days=3),
        )

        payment2 = Payment.objects.create(
            stripe_payment_intent_id="pi_test456",
            amount=2999,
            status="succeeded",
            user=self.user,
            created_at=timezone.now() - timedelta(days=2),
        )

        payment3 = Payment.objects.create(
            stripe_payment_intent_id="pi_test789",
            amount=999,
            status="succeeded",
            user=self.user,
            created_at=timezone.now() - timedelta(days=1),
        )

        # Test with default limit
        history = self.user.get_payment_history()
        self.assertEqual(len(history), 3)

        # Test with custom limit
        history = self.user.get_payment_history(limit=2)
        self.assertEqual(len(history), 2)
