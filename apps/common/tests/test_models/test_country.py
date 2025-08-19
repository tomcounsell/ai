"""
Tests for the Country model and related functionality.
"""

import uuid

from django.test import TestCase

from ...models import Country, Currency


class CountryModelTestCase(TestCase):
    """Test cases for the Country model."""

    def setUp(self):
        """Set up test data."""
        self.currency = Currency.objects.create(name="US Dollar", code="USD")

        self.country = Country.objects.create(
            name="United States", code="US", calling_code="1", currency=self.currency
        )

        self.country_no_currency = Country.objects.create(
            name="Test Country", code="TC", calling_code="99"
        )

    def test_country_creation(self):
        """Test that a country can be created with the expected field values."""
        self.assertEqual(self.country.name, "United States")
        self.assertEqual(self.country.code, "us")  # Should be lowercase
        self.assertEqual(self.country.calling_code, "1")
        self.assertEqual(self.country.currency, self.currency)
        self.assertIsInstance(self.country.id, uuid.UUID)

    def test_country_without_currency(self):
        """Test that a country can be created without a currency."""
        self.assertEqual(self.country_no_currency.name, "Test Country")
        self.assertEqual(self.country_no_currency.code, "tc")  # Should be lowercase
        self.assertEqual(self.country_no_currency.calling_code, "99")
        self.assertIsNone(self.country_no_currency.currency)

    def test_string_representation(self):
        """Test the string representation of a country."""
        self.assertEqual(str(self.country), "United States")

    def test_lowercase_code_signal(self):
        """Test that the pre_save signal converts the code to lowercase."""
        # Test creating with uppercase
        country = Country.objects.create(name="Canada", code="CA", calling_code="1")
        self.assertEqual(country.code, "ca")

        # Test updating with uppercase
        country.code = "CAN"
        country.save()
        country.refresh_from_db()
        self.assertEqual(country.code, "can")

    def test_blank_fields(self):
        """Test that blank fields are handled properly."""
        country = Country.objects.create(name="", code="", calling_code="")
        self.assertEqual(country.name, "")
        self.assertEqual(country.code, "")
        self.assertEqual(country.calling_code, "")

    def test_verbose_name_plural(self):
        """Test that the verbose_name_plural is set correctly."""
        self.assertEqual(Country._meta.verbose_name_plural, "countries")

    def test_currency_relationship(self):
        """Test the relationship between Country and Currency."""
        # The currency should have this country in its countries relation
        self.assertIn(self.country, self.currency.countries.all())

        # Create another country with the same currency
        another_country = Country.objects.create(
            name="Another Country", code="AC", currency=self.currency
        )

        # Check that currency.countries includes both countries
        self.assertEqual(self.currency.countries.count(), 2)
        self.assertIn(self.country, self.currency.countries.all())
        self.assertIn(another_country, self.currency.countries.all())
