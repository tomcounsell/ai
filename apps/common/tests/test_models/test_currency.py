"""
Tests for the Currency model and related functionality.
"""

import uuid

from django.test import TestCase

from ...models import Currency
from ..test_behaviors import TimestampableTest


class CurrencyModelTestCase(TimestampableTest, TestCase):
    """Test cases for the Currency model."""

    model = Currency

    def setUp(self):
        """Set up test data."""
        self.currency = Currency.objects.create(name="US Dollar", code="USD")

    def test_currency_creation(self):
        """Test that a currency can be created with the expected field values."""
        self.assertEqual(self.currency.name, "US Dollar")
        self.assertEqual(self.currency.code, "usd")  # Should be lowercase
        self.assertIsInstance(self.currency.id, uuid.UUID)

    def test_string_representation(self):
        """Test the string representation of a currency."""
        self.assertEqual(str(self.currency), "USD")

    def test_lowercase_code_signal(self):
        """Test that the pre_save signal converts the code to lowercase."""
        # Test creating with uppercase
        currency = Currency.objects.create(name="Euro", code="EUR")
        self.assertEqual(currency.code, "eur")

        # Test updating with uppercase
        currency.code = "EUR"
        currency.save()
        currency.refresh_from_db()
        self.assertEqual(currency.code, "eur")

    def test_verbose_name_plural(self):
        """Test that the verbose_name_plural is set correctly."""
        self.assertEqual(Currency._meta.verbose_name_plural, "currencies")

    def create_instance(self, **kwargs):
        """Create a Currency instance for testing."""
        return Currency.objects.create(name="Test Currency", code="TST", **kwargs)
