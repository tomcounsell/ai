"""
Tests for model factories in factories.py.

These tests ensure that each factory properly creates model instances
with sensible defaults and respects custom attributes.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

User = get_user_model()

from apps.common.tests.factories import (
    AddressFactory,
    BlogPostFactory,
    CityFactory,
    CountryFactory,
    CurrencyFactory,
    DocumentFactory,
    EmailFactory,
    ImageFactory,
    NoteFactory,
    SMSFactory,
    TeamFactory,
    TeamMemberFactory,
    UploadFactory,
    UserFactory,
)


class ExistingFactoriesTestCase(TestCase):
    """Test case for existing factories to ensure they work as expected."""

    def test_user_factory(self):
        """Test that UserFactory creates valid User instances."""
        user = UserFactory.create()
        self.assertIsNotNone(user.id)
        self.assertTrue(user.is_active)
        self.assertTrue(user.check_password("testpassword123"))

        # Test custom attributes
        custom_user = UserFactory.create(
            username="customuser",
            email="custom@example.com",
            first_name="Custom",
            last_name="Name",
        )
        self.assertEqual(custom_user.username, "customuser")
        self.assertEqual(custom_user.email, "custom@example.com")
        self.assertEqual(custom_user.first_name, "Custom")
        self.assertEqual(custom_user.last_name, "Name")

    def test_country_factory(self):
        """Test that CountryFactory creates valid Country instances."""
        country = CountryFactory.create()
        self.assertIsNotNone(country.id)
        self.assertEqual(country.name, "United States")
        # Note: Country.code is stored in lowercase
        self.assertEqual(country.code, "us")

        # Test custom attributes
        custom_country = CountryFactory.create(
            name="Canada", code="CA", calling_code="1"
        )
        self.assertEqual(custom_country.name, "Canada")
        # Note: Country.code is stored in lowercase
        self.assertEqual(custom_country.code, "ca")

    def test_address_factory(self):
        """Test that AddressFactory creates valid Address instances."""
        address = AddressFactory.create()
        self.assertIsNotNone(address.id)
        self.assertEqual(address.line_1, "123 Test Street")
        self.assertIsNotNone(address.country)

        # Test with custom country
        country = CountryFactory.create(name="France", code="FR")
        custom_address = AddressFactory.create(
            line_1="45 Rue de Paris",
            city="Paris",
            country=country,
            region=None,  # Explicitly set region to None to avoid issues
        )
        self.assertEqual(custom_address.line_1, "45 Rue de Paris")
        self.assertEqual(custom_address.city, "Paris")
        self.assertEqual(custom_address.country.name, "France")

    def test_currency_factory(self):
        """Test that CurrencyFactory creates valid Currency instances."""
        currency = CurrencyFactory.create()
        self.assertIsNotNone(currency.id)
        # Note: Currency.code is stored in lowercase
        self.assertEqual(currency.code, "usd")
        self.assertEqual(currency.name, "US Dollar")

    def test_note_factory(self):
        """Test that NoteFactory creates valid Note instances."""
        note = NoteFactory.create()
        self.assertIsNotNone(note.id)
        self.assertEqual(note.text, "This is a test note.")
        self.assertIsNotNone(note.author)

        # Test with custom author
        user = UserFactory.create()
        custom_note = NoteFactory.create(text="Custom note text", author=user)
        self.assertEqual(custom_note.text, "Custom note text")
        self.assertEqual(custom_note.author, user)

    def test_upload_factory(self):
        """Test that UploadFactory creates valid Upload instances."""
        upload = UploadFactory.create()
        self.assertIsNotNone(upload.id)
        self.assertEqual(upload.original, "https://example.com/test.jpg")
        self.assertEqual(upload.name, "test.jpg")
        self.assertEqual(upload.file_type, "image/jpeg")
        self.assertEqual(upload.width, 800)
        self.assertEqual(upload.height, 600)

    def test_image_factory(self):
        """Test that ImageFactory creates valid Image instances."""
        image = ImageFactory.create()
        self.assertIsNotNone(image.id)
        self.assertEqual(image.width, 800)
        self.assertEqual(image.height, 600)
        self.assertEqual(image.thumbnail_url, "https://example.com/test_thumbnail.jpg")

    def test_document_factory(self):
        """Test that DocumentFactory creates valid Document instances."""
        document = DocumentFactory.create()
        self.assertIsNotNone(document.id)
        self.assertEqual(document.original, "https://example.com/test.pdf")
        self.assertEqual(document.name, "test.pdf")
        self.assertEqual(document.meta_data["type"], "document")


class BlogPostFactoryTestCase(TestCase):
    """Test case for BlogPostFactory."""

    def test_blog_post_factory(self):
        """Test that BlogPostFactory creates valid BlogPost instances."""
        # This test will fail until we implement BlogPostFactory
        blog_post = BlogPostFactory.create()
        self.assertIsNotNone(blog_post.id)
        self.assertIsNotNone(blog_post.title)
        self.assertIsNotNone(blog_post.content)
        self.assertIsNotNone(blog_post.author)

        # Test with custom attributes
        user = UserFactory.create()
        image = ImageFactory.create()
        custom_post = BlogPostFactory.create(
            title="Custom Blog Title",
            content="Custom content for testing",
            author=user,
            featured_image=image,
            reading_time_minutes=5,
        )
        self.assertEqual(custom_post.title, "Custom Blog Title")
        self.assertEqual(custom_post.content, "Custom content for testing")
        self.assertEqual(custom_post.author, user)
        self.assertEqual(custom_post.featured_image, image)
        self.assertEqual(custom_post.reading_time_minutes, 5)


class CityFactoryTestCase(TestCase):
    """Test case for CityFactory."""

    def test_city_factory(self):
        """Test that CityFactory creates valid City instances."""
        # This test will fail until we implement CityFactory
        city = CityFactory.create()
        self.assertIsNotNone(city.id)
        self.assertIsNotNone(city.name)
        self.assertIsNotNone(city.country)

        # Test with custom attributes
        country = CountryFactory.create(name="Japan", code="JP")
        custom_city = CityFactory.create(name="Tokyo", code="TYO", country=country)
        self.assertEqual(custom_city.name, "Tokyo")
        # Note: city.code is lowercased by a pre_save signal
        self.assertEqual(custom_city.code, "tyo")
        self.assertEqual(custom_city.country.name, "Japan")


class TeamFactoryTestCase(TestCase):
    """Test case for TeamFactory."""

    def test_team_factory(self):
        """Test that TeamFactory creates valid Team instances."""
        # This test will fail until we implement TeamFactory
        team = TeamFactory.create()
        self.assertIsNotNone(team.id)
        self.assertIsNotNone(team.name)
        self.assertIsNotNone(team.slug)
        self.assertTrue(team.is_active)

        # Test with custom attributes
        custom_team = TeamFactory.create(
            name="Engineering",
            slug="engineering",
            description="The engineering team",
            is_active=False,
        )
        self.assertEqual(custom_team.name, "Engineering")
        self.assertEqual(custom_team.slug, "engineering")
        self.assertEqual(custom_team.description, "The engineering team")
        self.assertFalse(custom_team.is_active)


class TeamMemberFactoryTestCase(TestCase):
    """Test case for TeamMemberFactory."""

    def test_team_member_factory(self):
        """Test that TeamMemberFactory creates valid TeamMember instances."""
        # This test will fail until we implement TeamMemberFactory
        member = TeamMemberFactory.create()
        self.assertIsNotNone(member.id)
        self.assertIsNotNone(member.team)
        self.assertIsNotNone(member.user)

        # Test with custom attributes
        team = TeamFactory.create()
        user = UserFactory.create()
        custom_member = TeamMemberFactory.create(team=team, user=user, role="ADMIN")
        self.assertEqual(custom_member.team, team)
        self.assertEqual(custom_member.user, user)
        self.assertEqual(custom_member.role, "ADMIN")


class EmailFactoryTestCase(TestCase):
    """Test case for EmailFactory."""

    def test_email_factory(self):
        """Test that EmailFactory creates valid Email instances."""
        # This test will fail until we implement EmailFactory
        email = EmailFactory.create()
        self.assertIsNotNone(email.id)
        self.assertIsNotNone(email.to_address)
        self.assertIsNotNone(email.subject)

        # Test with custom attributes
        custom_email = EmailFactory.create(
            to_address="custom@example.com",
            from_address="sender@example.com",
            subject="Custom Subject",
            body="Custom email body",
        )
        self.assertEqual(custom_email.to_address, "custom@example.com")
        self.assertEqual(custom_email.from_address, "sender@example.com")
        self.assertEqual(custom_email.subject, "Custom Subject")
        self.assertEqual(custom_email.body, "Custom email body")


class SMSFactoryTestCase(TestCase):
    """Test case for SMSFactory."""

    def test_sms_factory(self):
        """Test that SMSFactory creates valid SMS instances."""
        # This test will fail until we implement SMSFactory
        sms = SMSFactory.create()
        self.assertIsNotNone(sms.id)
        self.assertIsNotNone(sms.to_number)

        # Test with custom attributes
        custom_sms = SMSFactory.create(
            to_number="+15551234567", from_number="+15557654321", body="Custom SMS body"
        )
        self.assertEqual(custom_sms.to_number, "+15551234567")
        self.assertEqual(custom_sms.from_number, "+15557654321")
        self.assertEqual(custom_sms.body, "Custom SMS body")
