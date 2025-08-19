import hashlib
from datetime import datetime
from typing import Any, Dict, Optional

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models.query import QuerySet
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.common.behaviors import Timestampable


class User(AbstractUser, Timestampable):
    """
    Enhanced User model that extends Django's AbstractUser.

    This model adds additional fields and functionality for the application,
    including phone number, biography, verification flags

    Inherits from Timestampable to automatically track created_at and modified_at timestamps.

    Attributes:
        phone_number (str): User's phone number
        biography (str): User's biographical information
        is_email_verified (bool): Whether the user's email has been verified
        is_beta_tester (bool): Whether the user is part of the beta program
        agreed_to_terms_at (datetime): When the user agreed to the terms of service
    """

    phone_number = models.CharField(max_length=15, default="", blank=True)
    biography = models.TextField(_("Biography"), blank=True, default="")
    # birthdate = models.DateField(null=True, blank=True)
    is_email_verified = models.BooleanField(default=False)
    is_beta_tester = models.BooleanField(default=False)
    agreed_to_terms_at = models.DateTimeField(null=True, blank=True)

    # Add these back to fix migration issues
    has_payment_method = models.BooleanField(default=False)
    stripe_customer_id = models.CharField(max_length=255, default="", blank=True)

    @property
    def has_stripe_customer(self) -> bool:
        """
        Check if the user has a Stripe customer ID.

        Returns:
            bool: True if the user has a non-empty stripe_customer_id, False otherwise
        """
        return bool(self.stripe_customer_id and self.stripe_customer_id.strip())

    @property
    def has_active_subscription(self) -> bool:
        """
        Check if the user has any active subscription.

        Returns:
            bool: True if the user has at least one active subscription, False otherwise
        """
        return len(self.active_subscriptions) > 0

    @property
    def active_subscriptions(self) -> QuerySet:
        """
        Get all active subscriptions for the user.

        Returns:
            QuerySet: A queryset of all active or trialing subscriptions
        """

        active_statuses = ["active", "trialing"]
        return self.subscriptions.filter(status__in=active_statuses)

    def get_active_subscription(self) -> Any | None:
        """
        Get the first active subscription for the user.

        Returns:
            Optional[Subscription]: The first active subscription, or None if there are none
        """
        subs = self.active_subscriptions
        return subs.first() if subs.exists() else None

    def get_payment_history(self, limit: int = 10) -> QuerySet:
        """
        Get the payment history for the user.

        Args:
            limit (int): Maximum number of payments to return

        Returns:
            QuerySet: A queryset of payments, ordered by creation date (descending)
        """
        return self.payments.all().order_by("-created_at")[:limit]

    # MODEL PROPERTIES
    @property
    def serialized(self) -> dict[str, Any]:
        """
        Serializes basic user information into a dictionary.

        Provides a simplified representation of user data suitable for API responses
        or session storage.

        Returns:
            Dict[str, Any]: Dictionary containing serialized user data
        """
        return {
            "username": self.username,
            "email": self.email,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "is_staff": self.is_staff,
            "is_active": self.is_active,
        }

    @property
    def four_digit_login_code(self) -> str:
        """
        Generates a deterministic 4-digit login code for the user.

        This creates a unique code based on the user's ID, email, and last login time.
        For test accounts (email ending with @example.com), it returns a fixed code.

        Returns:
            str: A 4-digit login verification code

        Note:
            This is useful for implementing email-based login verification or two-factor authentication.
        """
        if self.email.endswith("@example.com"):
            return "1234"  # for test accounts
        hash_object = hashlib.md5(
            bytes(f"{self.id}{self.email}{self.last_login}", encoding="utf-8")
        )
        return str(int(hash_object.hexdigest(), 16))[-4:]

    @property
    def is_agreed_to_terms(self) -> bool:
        if self.agreed_to_terms_at and self.agreed_to_terms_at > timezone.make_aware(
            datetime(2019, 11, 1)
        ):
            return True
        return False

    @is_agreed_to_terms.setter
    def is_agreed_to_terms(self, value: bool):
        if value is True:
            self.agreed_to_terms_at = timezone.now()
        elif value is False and self.is_agreed_to_terms:
            self.agreed_to_terms_at = None

    @property
    def initials(self) -> str:
        """
        Get the user's initials for display in the UI.

        Returns the first letter of each word in the user's string representation,
        up to 2 characters. Handles edge cases like parentheses and empty names.

        Returns:
            str: User's initials (1-2 characters, uppercase)
        """
        user_str = str(self)

        # If the name contains parentheses (like "email@example.com (unverified)")
        # only use the part before the parentheses
        if "(" in user_str:
            user_str = user_str.split("(")[0].strip()

        # Split by spaces and get first letter of each part
        parts = [x for x in user_str.split(" ") if x]
        if not parts:
            return "U"  # Default to "U" for User if nothing else available

        # Get first letter of each word, uppercase it, join, and limit to 2 chars
        initials = "".join([p[0].upper() for p in parts if p])[:2]
        return initials if initials else "U"

    def get_login_url(self, next_url=None) -> str:
        """
        Generate a magic login URL for this user.

        This URL contains the login code needed for passwordless login.

        Args:
            next_url (str, optional): URL to redirect to after login

        Returns:
            str: Full login URL with code
        """
        from django.conf import settings
        from django.urls import reverse

        # Construct the base URL (with https:// if not already present)
        hostname = settings.HOSTNAME
        if not hostname.startswith(("http://", "https://")):
            hostname = f"https://{hostname}"

        # Get the login URL with code parameter
        login_path = reverse("public:account-login")
        url = f"{hostname}{login_path}?code={self.four_digit_login_code}"

        # Add next parameter if provided
        if next_url:
            url = f"{url}&next={next_url}"

        return url

    # MODEL FUNCTIONS
    def __str__(self) -> str:
        """
        Returns a string representation of the User.

        The string representation follows this priority:
        1. Full name (first name + last name if available)
        2. Username (if it's not an email)
        3. Email username part (if the email is verified)
        4. Full email with unverified indicator
        5. User ID as fallback for error cases

        Returns:
            str: A human-readable representation of the user
        """
        try:
            if self.first_name:
                return self.first_name + (
                    f" {self.last_name}" if self.last_name else ""
                )
            if self.username and "@" not in self.username:
                return self.username
            if self.is_email_verified:
                return self.email.split("@")[0]
            else:
                return f"{self.email} (unverified)"
        except Exception:  # Catching specific exceptions is better
            return f"User {self.id}"
