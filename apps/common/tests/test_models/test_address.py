"""
Tests for the Address model and related functionality.
"""

import uuid

from django.test import TestCase

from ...models import Address, Country
from ..test_behaviors import TimestampableTest


class AddressModelTestCase(TimestampableTest, TestCase):
    """Test cases for the Address model."""

    model = Address

    def setUp(self):
        """Set up test data."""
        self.country = Country.objects.create(
            name="United States", code="US", calling_code="1"
        )

        self.address = Address.objects.create(
            line_1="123 Main St",
            line_2="Apt 4B",
            line_3="Building A",
            city="New York",
            region="NY",
            postal_code="10001",
            country=self.country,
            google_map_link="https://maps.google.com/?q=customlink",
        )

        self.address_minimal = Address.objects.create(
            line_1="456 Side St", city="Boston"
        )

    def test_address_creation(self):
        """Test that an address can be created with the expected field values."""
        self.assertEqual(self.address.line_1, "123 Main St")
        self.assertEqual(self.address.line_2, "Apt 4B")
        self.assertEqual(self.address.line_3, "Building A")
        self.assertEqual(self.address.city, "New York")
        self.assertEqual(self.address.region, "NY")
        self.assertEqual(self.address.postal_code, "10001")
        self.assertEqual(self.address.country, self.country)
        self.assertEqual(
            self.address.google_map_link, "https://maps.google.com/?q=customlink"
        )
        self.assertIsInstance(self.address.id, uuid.UUID)

    def test_minimal_address_creation(self):
        """Test that an address can be created with minimal field values."""
        self.assertEqual(self.address_minimal.line_1, "456 Side St")
        self.assertEqual(self.address_minimal.city, "Boston")
        self.assertIsNone(self.address_minimal.line_2)
        self.assertIsNone(self.address_minimal.line_3)
        self.assertIsNone(self.address_minimal.region)
        self.assertIsNone(self.address_minimal.postal_code)
        self.assertIsNone(self.address_minimal.country)
        self.assertIsNone(self.address_minimal.google_map_link)

    def test_inline_string_property(self):
        """Test the inline_string property returns the expected formatted string."""
        expected_string = "123 Main St New York, NY "
        self.assertEqual(self.address.inline_string, expected_string)

        # Test with minimal data
        expected_minimal_string = "456 Side St Boston, None "
        self.assertEqual(self.address_minimal.inline_string, expected_minimal_string)

    def test_google_map_url_property(self):
        """Test the google_map_url property returns the expected URL."""
        # For address with custom map link
        self.assertEqual(
            self.address.google_map_url, "https://maps.google.com/?q=customlink"
        )

        # For address without custom map link
        expected_url = "http://maps.google.com/?q=456%20Side%20St%20Boston,%20None%20"
        self.assertEqual(self.address_minimal.google_map_url, expected_url)

    def test_string_representation(self):
        """Test the string representation of the address."""
        expected_string = "123 Main St New York, NY "
        self.assertEqual(str(self.address), expected_string)

    def test_verbose_name_plural(self):
        """Test that the verbose_name_plural is set correctly."""
        self.assertEqual(Address._meta.verbose_name_plural, "addresses")
