from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.common.behaviors import Timestampable


class Subscription(Timestampable, models.Model):
    """
    Model for storing subscription information.
    """

    # User who owns the subscription
    user = models.ForeignKey(
        "common.User",
        related_name="subscriptions",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text=_("User who owns the subscription"),
    )

    # Stripe fields
    stripe_id = models.CharField(
        max_length=255, blank=True, default="", help_text=_("Stripe subscription ID")
    )

    stripe_customer_id = models.CharField(
        max_length=255, blank=True, default="", help_text=_("Stripe customer ID")
    )

    stripe_price_id = models.CharField(
        max_length=255, blank=True, default="", help_text=_("Stripe price ID")
    )

    # For backward compatibility
    stripe_subscription_id = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text=_("Stripe subscription ID (legacy)"),
    )

    # Subscription plan name
    plan_name = models.CharField(max_length=100, help_text=_("Subscription plan name"))

    # Plan description
    plan_description = models.TextField(
        blank=True, default="", help_text=_("Description of the subscription plan")
    )
    # Subscription price in cents
    price = models.PositiveIntegerField(help_text=_("Subscription price in cents"))

    # Billing interval
    INTERVAL_CHOICES = (
        ("monthly", _("Monthly")),
        ("yearly", _("Yearly")),
        ("weekly", _("Weekly")),
        ("daily", _("Daily")),
    )
    interval = models.CharField(
        max_length=20,
        choices=INTERVAL_CHOICES,
        default="monthly",
        help_text=_("Billing interval"),
    )

    # Subscription status
    STATUS_ACTIVE = "active"
    STATUS_CANCELED = "canceled"
    STATUS_PAST_DUE = "past_due"
    STATUS_TRIALING = "trialing"
    STATUS_UNPAID = "unpaid"
    STATUS_INCOMPLETE = "incomplete"
    STATUS_INCOMPLETE_EXPIRED = "incomplete_expired"

    STATUS_CHOICES = (
        (STATUS_ACTIVE, _("Active")),
        (STATUS_CANCELED, _("Canceled")),
        (STATUS_PAST_DUE, _("Past Due")),
        (STATUS_TRIALING, _("Trialing")),
        (STATUS_UNPAID, _("Unpaid")),
        (STATUS_INCOMPLETE, _("Incomplete")),
        (STATUS_INCOMPLETE_EXPIRED, _("Incomplete Expired")),
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="active",
        help_text=_("Subscription status"),
    )

    # Start date
    start_date = models.DateTimeField(
        default=timezone.now, help_text=_("When the subscription started")
    )

    # End date (null for active subscriptions)
    end_date = models.DateTimeField(
        null=True, blank=True, help_text=_("When the subscription ended or will end")
    )

    # Canceled at datetime
    canceled_at = models.DateTimeField(
        null=True, blank=True, help_text=_("When the subscription was canceled")
    )

    # Trial end date
    trial_end = models.DateTimeField(
        null=True, blank=True, help_text=_("When the trial period ends")
    )

    # Current period start
    current_period_start = models.DateTimeField(
        null=True, blank=True, help_text=_("Start of the current billing period")
    )

    # Current period end
    current_period_end = models.DateTimeField(
        null=True, blank=True, help_text=_("End of the current billing period")
    )

    # Cancel at period end
    cancel_at_period_end = models.BooleanField(
        default=False,
        help_text=_(
            "Whether the subscription will be canceled at the end of the current period"
        ),
    )

    class Meta:
        verbose_name = _("Subscription")
        verbose_name_plural = _("Subscriptions")
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.user} - {self.plan_name} ({self.status})"

    @property
    def active(self):
        """
        Check if the subscription is active.
        """
        if self.status not in [self.STATUS_ACTIVE, self.STATUS_TRIALING]:
            return False

        # Also check if the current period has ended
        if self.current_period_end and self.current_period_end < timezone.now():
            return False

        return True

    @property
    def is_active(self):
        """Alias for active property for backward compatibility."""
        return self.active

    @property
    def is_trialing(self):
        """
        Check if the subscription is in trial period.
        """
        return self.status == self.STATUS_TRIALING

    @property
    def is_canceled(self):
        """
        Check if the subscription is canceled or set to cancel.
        """
        return self.status == self.STATUS_CANCELED or self.cancel_at_period_end

    @property
    def price_display(self):
        """
        Display the price in dollars rather than cents.
        """
        return f"${self.price / 100:.2f}/{self.interval}"

    @property
    def days_until_expiration(self):
        """
        Calculate days until the subscription expires.
        """
        if not self.end_date:
            return None

        delta = self.end_date - timezone.now()
        return max(0, delta.days)

    @property
    def days_until_renewal(self):
        """
        Calculate days until the subscription renews.
        """
        if not self.current_period_end:
            return 0

        delta = self.current_period_end - timezone.now()
        return max(0, delta.days)

    @property
    def is_expiring_soon(self):
        """
        Check if the subscription is expiring within 7 days.
        """
        days = self.days_until_expiration
        return days is not None and days <= 7 and self.is_active

    @property
    def owner_display(self):
        """
        Get a display string for the subscription owner.
        """
        if self.user:
            return f"User: {self.user.email}"
        return "Unknown"
