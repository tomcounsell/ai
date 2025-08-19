"""
Tests for the City model and related functionality.
"""

import uuid

from django.test import TestCase

from ...models import City, Country, Currency


class CityTest(TestCase):
    """Test cases for the City model."""

    def setUp(self):
        """Set up test data."""
        # Create a currency for the country
        self.currency = Currency.objects.create(code="USD", name="US Dollar")

        # Create a country for the city
        self.country = Country.objects.create(
            name="United States", code="US", currency=self.currency
        )

    def test_city_creation(self):
        """Test basic city creation with required fields."""
        city = City.objects.create(name="New York", code="NYC", country=self.country)

        # Test that fields are saved correctly
        self.assertEqual(city.name, "New York")
        self.assertEqual(city.code, "nyc")  # should be lowercase due to signal
        self.assertEqual(city.country, self.country)
        self.assertIsInstance(city.id, uuid.UUID)

    def test_string_representation(self):
        """Test the string representation of a city."""
        city = City.objects.create(
            name="San Francisco", code="SFO", country=self.country
        )

        expected_str = "San Francisco, United States (sfo)"
        self.assertEqual(str(city), expected_str)

    def test_currency_property(self):
        """Test that the currency property returns the country's currency."""
        city = City.objects.create(name="Los Angeles", code="LAX", country=self.country)

        # The currency property should return the country's currency
        self.assertEqual(city.currency, self.currency)

    def test_lowercase_code_signal(self):
        """Test that the pre_save signal converts the code to lowercase."""
        # Create a city with uppercase code
        city = City.objects.create(name="Chicago", code="ORD", country=self.country)

        # Code should be converted to lowercase on save
        self.assertEqual(city.code, "ord")

        # Test updating the code
        city.code = "CHI"
        city.save()

        # Refresh from database and check code is lowercase
        city.refresh_from_db()
        self.assertEqual(city.code, "chi")

    def test_country_relationship(self):
        """Test the relationship between City and Country."""
        # Create multiple cities for the same country
        city1 = City.objects.create(name="Boston", code="BOS", country=self.country)

        city2 = City.objects.create(name="Miami", code="MIA", country=self.country)

        # The country should have these cities in its cities relation
        self.assertIn(city1, self.country.cities.all())
        self.assertIn(city2, self.country.cities.all())
        self.assertEqual(self.country.cities.count(), 2)

    def test_blank_fields(self):
        """Test that blank fields can be empty but still exist."""
        # Create a city with blank name and code
        city = City.objects.create(name="", code="", country=self.country)

        # The fields should be blank strings, not None
        self.assertEqual(city.name, "")
        self.assertEqual(city.code, "")

    def test_verbose_name_plural(self):
        """Test that the verbose_name_plural is set correctly."""
        self.assertEqual(City._meta.verbose_name_plural, "cities")
