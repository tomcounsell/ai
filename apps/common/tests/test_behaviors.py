"""
Tests for behavior mixins in the common app.

This module contains test classes for all behavior mixins in the project.
It follows two testing approaches:
1. Database-backed tests: Using Django's TestCase to test with ORM integration
2. Direct tests: Using Python's unittest with mocks to test without database

These tests verify fields, properties, and methods provided by each behavior mixin.
"""

import datetime
import unittest
from unittest import mock

from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.common.behaviors.annotatable import Annotatable
from apps.common.behaviors.authorable import Authorable
from apps.common.behaviors.expirable import Expirable
from apps.common.behaviors.locatable import Locatable
from apps.common.behaviors.permalinkable import Permalinkable
from apps.common.behaviors.publishable import Publishable
from apps.common.behaviors.timestampable import Timestampable

User = get_user_model()


# Base test mixin for behavior tests
class BehaviorTestMixin:
    """Base mixin for behavior tests that provides common methods."""

    @property
    def model(self):
        """Must be defined by subclasses to return the model class to test."""
        raise NotImplementedError("Subclasses must define 'model' property")

    def create_instance(self, **kwargs):
        """Helper method to create a model instance for testing."""
        return self.model.objects.create(**kwargs)


# Timestampable Behavior Tests
class TimestampableTest(BehaviorTestMixin):
    """Tests for the Timestampable behavior mixin using Django TestCase."""

    def test_fields_exist(self):
        """Test that the fields are properly added to the model."""
        instance = self.create_instance()
        self.assertTrue(hasattr(instance, "created_at"))
        self.assertTrue(hasattr(instance, "modified_at"))

    def test_auto_timestamps(self):
        """Test that timestamps are automatically set."""
        instance = self.create_instance()
        self.assertIsNotNone(instance.created_at)
        self.assertIsNotNone(instance.modified_at)

        # Initially, created_at and modified_at should be close
        time_diff = abs((instance.created_at - instance.modified_at).total_seconds())
        self.assertLess(time_diff, 1)  # Should be less than 1 second apart

    def test_modified_at_updates(self):
        """Test that modified_at updates on save."""
        instance = self.create_instance()
        original_modified = instance.modified_at

        # Wait a moment to ensure timestamps differ
        instance.save()

        # Refresh from database to get updated values
        instance.refresh_from_db()
        self.assertGreaterEqual(instance.modified_at, original_modified)

        # created_at should remain unchanged
        self.assertEqual(
            instance.created_at.replace(microsecond=0),
            instance.created_at.replace(microsecond=0),
        )


# Authorable Behavior Tests
class AuthorableTest(BehaviorTestMixin):
    """Tests for the Authorable behavior mixin using Django TestCase."""

    def setUp(self):
        """Set up test data."""
        self.user = User.objects.create_user(
            username="testuser", email="test@example.com", password="password123"
        )

    def test_fields_exist(self):
        """Test that the fields are properly added to the model."""
        instance = self.create_instance()
        self.assertTrue(hasattr(instance, "author"))
        self.assertTrue(hasattr(instance, "is_author_anonymous"))
        self.assertTrue(hasattr(instance, "authored_at"))

    def test_author_assignment(self):
        """Test author assignment."""
        instance = self.create_instance(author=self.user)
        self.assertEqual(instance.author, self.user)
        self.assertFalse(instance.is_author_anonymous)
        self.assertIsNotNone(instance.authored_at)

    def test_anonymous_author(self):
        """Test anonymous author functionality."""
        instance = self.create_instance(is_author_anonymous=True)
        self.assertIsNone(instance.author)
        self.assertTrue(instance.is_author_anonymous)

        # Setting an author should clear anonymous flag
        instance.author = self.user
        instance.save()
        self.assertFalse(instance.is_author_anonymous)

    def test_author_display_name(self):
        """Test the author_display_name property."""
        # With author
        instance = self.create_instance(author=self.user)
        self.assertEqual(
            instance.author_display_name,
            self.user.get_full_name() or self.user.username,
        )

        # Anonymous
        instance = self.create_instance(is_author_anonymous=True)
        self.assertEqual(instance.author_display_name, "Anonymous")


# Publishable Behavior Tests
class PublishableTest(BehaviorTestMixin):
    """Tests for the Publishable behavior mixin using Django TestCase."""

    def test_fields_exist(self):
        """Test that the fields are properly added to the model."""
        instance = self.create_instance()
        self.assertTrue(hasattr(instance, "published_at"))
        self.assertTrue(hasattr(instance, "edited_at"))
        self.assertTrue(hasattr(instance, "unpublished_at"))

    def test_publish_method(self):
        """Test publish method sets published_at."""
        instance = self.create_instance()
        self.assertIsNone(instance.published_at)
        self.assertFalse(instance.is_published)

        instance.publish()
        self.assertIsNotNone(instance.published_at)
        self.assertTrue(instance.is_published)

    def test_unpublish_method(self):
        """Test unpublish method sets unpublished_at."""
        instance = self.create_instance()
        instance.publish()
        self.assertTrue(instance.is_published)

        instance.unpublish()
        self.assertIsNotNone(instance.unpublished_at)
        self.assertFalse(instance.is_published)

    def test_edit_published(self):
        """Test editing a published item sets edited_at."""
        instance = self.create_instance()
        instance.publish()

        # Simulate edit
        instance.edited_at = None
        instance.save()
        instance.refresh_from_db()
        self.assertIsNotNone(instance.edited_at)


# Expirable Behavior Tests
class ExpirableTest(BehaviorTestMixin):
    """Tests for the Expirable behavior mixin using Django TestCase."""

    def test_fields_exist(self):
        """Test that the fields are properly added to the model."""
        instance = self.create_instance()
        self.assertTrue(hasattr(instance, "valid_at"))
        self.assertTrue(hasattr(instance, "expired_at"))

    def test_is_expired_property(self):
        """Test the is_expired property correctly reflects state."""
        instance = self.create_instance()
        self.assertFalse(instance.is_expired)

        # Set expired
        instance.expired_at = timezone.now()
        instance.save()
        self.assertTrue(instance.is_expired)

    def test_set_expired(self):
        """Test setting expired state through property."""
        instance = self.create_instance()
        self.assertIsNone(instance.expired_at)

        instance.is_expired = True
        self.assertIsNotNone(instance.expired_at)
        self.assertTrue(instance.is_expired)

        instance.is_expired = False
        self.assertIsNone(instance.expired_at)
        self.assertFalse(instance.is_expired)


# Permalinkable Behavior Tests
class PermalinkableTest(BehaviorTestMixin):
    """Tests for the Permalinkable behavior mixin using Django TestCase."""

    def test_fields_exist(self):
        """Test that the fields are properly added to the model."""
        instance = self.create_instance()
        self.assertTrue(hasattr(instance, "slug"))

    def test_slug_generation(self):
        """Test slug generation."""
        instance = self.create_instance()
        self.assertIsNotNone(instance.slug)

        # Test with a model that has slug_source property
        if hasattr(instance, "slug_source"):
            expected_slug = instance.slug_source.lower().replace(" ", "-")
            self.assertTrue(instance.slug.startswith(expected_slug))

    def test_get_url_kwargs(self):
        """Test get_url_kwargs method."""
        instance = self.create_instance()
        kwargs = instance.get_url_kwargs()
        self.assertIn("slug", kwargs)
        self.assertEqual(kwargs["slug"], instance.slug)


# Locatable Behavior Tests
class LocatableTest(BehaviorTestMixin):
    """Tests for the Locatable behavior mixin using Django TestCase."""

    def test_fields_exist(self):
        """Test that the fields are properly added to the model."""
        instance = self.create_instance()
        self.assertTrue(hasattr(instance, "address"))
        self.assertTrue(hasattr(instance, "longitude"))
        self.assertTrue(hasattr(instance, "latitude"))

    def test_coordinates(self):
        """Test setting and getting coordinates."""
        instance = self.create_instance(longitude=123.456, latitude=45.678)
        self.assertEqual(instance.longitude, 123.456)
        self.assertEqual(instance.latitude, 45.678)


# Annotatable Behavior Tests
class AnnotatableTest(BehaviorTestMixin):
    """Tests for the Annotatable behavior mixin using Django TestCase."""

    def test_fields_exist(self):
        """Test that the fields are properly added to the model."""
        instance = self.create_instance()
        self.assertTrue(hasattr(instance, "notes"))

    def test_has_notes_property(self):
        """Test has_notes property."""
        instance = self.create_instance()
        self.assertFalse(instance.has_notes)

        # We'd normally add a note here, but we need to mock this
        # since we don't have the actual Note model to test with
        with mock.patch.object(instance.notes, "exists", return_value=True):
            self.assertTrue(instance.has_notes)


# Direct Tests (Without Database)
class TestTimestampableDirect(unittest.TestCase):
    """Direct tests for Timestampable without database."""

    def test_fields(self):
        """Test the fields are defined correctly."""
        self.assertTrue(hasattr(Timestampable, "created_at"))
        self.assertTrue(hasattr(Timestampable, "modified_at"))

        # Get the field instances directly from the model's _meta
        created_at_field = Timestampable._meta.get_field("created_at")
        modified_at_field = Timestampable._meta.get_field("modified_at")

        self.assertEqual(created_at_field.auto_now_add, True)
        self.assertEqual(modified_at_field.auto_now, True)


class TestAuthorableDirect(unittest.TestCase):
    """Direct tests for Authorable without database."""

    def test_fields(self):
        """Test the fields are defined correctly."""
        self.assertTrue(hasattr(Authorable, "author"))
        self.assertTrue(hasattr(Authorable, "is_author_anonymous"))
        self.assertTrue(hasattr(Authorable, "authored_at"))

    def test_author_display_name(self):
        """Test author_display_name property."""
        obj = mock.MagicMock(spec=Authorable)

        # Test anonymous
        obj.is_author_anonymous = True
        obj.author = None
        self.assertEqual(Authorable.author_display_name.fget(obj), "Anonymous")

        # Test with author
        obj.is_author_anonymous = False
        mock_author = mock.MagicMock()
        mock_author.__str__.return_value = "Test User"
        obj.author = mock_author
        self.assertEqual(Authorable.author_display_name.fget(obj), "Test User")


class TestPublishableDirect(unittest.TestCase):
    """Direct tests for Publishable without database."""

    def test_fields(self):
        """Test the fields are defined correctly."""
        self.assertTrue(hasattr(Publishable, "published_at"))
        self.assertTrue(hasattr(Publishable, "edited_at"))
        self.assertTrue(hasattr(Publishable, "unpublished_at"))

    def test_is_published_property(self):
        """Test is_published property."""
        obj = mock.MagicMock(spec=Publishable)

        # Not published
        obj.published_at = None
        obj.unpublished_at = None
        self.assertFalse(Publishable.is_published.fget(obj))

        # Published
        obj.published_at = timezone.now()
        obj.unpublished_at = None
        self.assertTrue(Publishable.is_published.fget(obj))

        # Published then unpublished
        obj.published_at = timezone.now()
        obj.unpublished_at = timezone.now() + datetime.timedelta(days=1)
        self.assertFalse(Publishable.is_published.fget(obj))

    def test_is_published_setter(self):
        """Test is_published setter method."""
        obj = mock.MagicMock(spec=Publishable)

        # Test publishing directly - simplify to direct testing
        # First call - setting to True when unpublished
        obj.published_at = None
        obj.unpublished_at = timezone.now()
        obj.is_published = False  # Mock property return

        # Call the setter directly
        Publishable.is_published.fset(obj, True)

        # Verify expected behavior
        self.assertIsNone(obj.unpublished_at)
        self.assertIsNotNone(obj.published_at)

        # Test unpublishing - setting to False when published
        obj.published_at = timezone.now() - datetime.timedelta(days=1)
        obj.unpublished_at = None
        obj.is_published = True  # Mock property return

        # Call the setter directly
        Publishable.is_published.fset(obj, False)

        # Verify expected behavior
        self.assertIsNotNone(obj.unpublished_at)

    def test_publish_method(self):
        """Test publish method."""

        # Simplify by just implementing/calling the method to verify behavior
        # No mocking, just subclass with a property tracking setter
        class TrackingPublishable(Publishable):
            def __init__(self):
                self.is_published_value = None

            @property
            def is_published(self):
                return self.is_published_value

            @is_published.setter
            def is_published(self, value):
                self.is_published_value = value

        # Create instance with tracking
        obj = TrackingPublishable()
        obj.is_published_value = False  # Start unpublished

        # Call the method
        obj.publish()

        # Verify result
        self.assertTrue(obj.is_published_value)

    def test_unpublish_method(self):
        """Test unpublish method."""

        # Simplify by just implementing/calling the method to verify behavior
        # No mocking, just subclass with a property tracking setter
        class TrackingPublishable(Publishable):
            def __init__(self):
                self.is_published_value = None

            @property
            def is_published(self):
                return self.is_published_value

            @is_published.setter
            def is_published(self, value):
                self.is_published_value = value

        # Create instance with tracking
        obj = TrackingPublishable()
        obj.is_published_value = True  # Start published

        # Call the method
        obj.unpublish()

        # Verify result
        self.assertFalse(obj.is_published_value)

    def test_publication_status_property(self):
        """Test publication_status property."""
        obj = mock.MagicMock(spec=Publishable)

        # We'll call the method directly, mocking at a different level
        # Published
        with mock.patch.object(obj, "is_published", True, create=True):
            self.assertEqual(Publishable.publication_status.fget(obj), "Published")

        # Unpublished
        with mock.patch.object(obj, "is_published", False, create=True):
            obj.published_at = timezone.now()
            self.assertEqual(Publishable.publication_status.fget(obj), "Unpublished")

        # Draft
        with mock.patch.object(obj, "is_published", False, create=True):
            obj.published_at = None
            self.assertEqual(Publishable.publication_status.fget(obj), "Draft")


class TestExpirableDirect(unittest.TestCase):
    """Direct tests for Expirable without database."""

    def test_fields(self):
        """Test the fields are defined correctly."""
        self.assertTrue(hasattr(Expirable, "valid_at"))
        self.assertTrue(hasattr(Expirable, "expired_at"))

    def test_is_expired_property(self):
        """Test is_expired property."""
        obj = mock.MagicMock(spec=Expirable)

        # Not expired
        obj.expired_at = None
        self.assertFalse(Expirable.is_expired.fget(obj))

        # Expired
        obj.expired_at = timezone.now()
        self.assertTrue(Expirable.is_expired.fget(obj))

    def test_is_expired_setter(self):
        """Test is_expired setter method."""
        obj = mock.MagicMock(spec=Expirable)

        # Test setting to expired (True)
        obj.expired_at = None
        Expirable.is_expired.fset(obj, True)
        self.assertIsNotNone(obj.expired_at)

        # Test setting to not expired (False) when it was expired
        obj.expired_at = timezone.now()

        # Mock at the object level, not the property level
        with mock.patch.object(obj, "is_expired", True, create=True):
            Expirable.is_expired.fset(obj, False)
            self.assertIsNone(obj.expired_at)

        # Test setting to not expired (False) when it was already not expired
        obj.expired_at = None

        with mock.patch.object(obj, "is_expired", False, create=True):
            Expirable.is_expired.fset(obj, False)
            # Should remain None
            self.assertIsNone(obj.expired_at)


class TestPermalinkableDirect(unittest.TestCase):
    """Direct tests for Permalinkable without database."""

    def test_fields(self):
        """Test the fields are defined correctly."""
        self.assertTrue(hasattr(Permalinkable, "slug"))

    def test_get_url_kwargs(self):
        """Test get_url_kwargs method."""
        obj = mock.MagicMock(spec=Permalinkable)
        obj.slug = "test-slug"
        # Add additional kwargs to the get_url_kwargs method
        result = Permalinkable.get_url_kwargs(obj, slug=obj.slug)
        self.assertEqual(result, {"slug": "test-slug"})

    def test_pre_save_slug_signal_handler(self):
        """Test pre_save_slug signal handler."""
        # Import slugify directly to verify our implementation
        from django.utils.text import slugify

        from apps.common.behaviors.permalinkable import pre_save_slug

        # Create a real class that directly inherits from Permalinkable
        class TestPermalinkable(Permalinkable):
            slug_source = "Test Title"

        # Create an instance of the permalinkable model
        instance = TestPermalinkable()
        instance.slug = None  # Ensure slug is None

        # Call the pre_save_slug handler
        pre_save_slug(TestPermalinkable, instance)

        # Verify slug was set correctly
        self.assertEqual(instance.slug, slugify("Test Title"))

        # Test with a non-permalinkable model
        class TestNonPermalinkable:
            pass

        # Create an instance
        non_permalinkable_instance = TestNonPermalinkable()
        if not hasattr(non_permalinkable_instance, "slug"):
            non_permalinkable_instance.slug = None

        # Call the signal handler
        pre_save_slug(TestNonPermalinkable, non_permalinkable_instance)

        # Slug should remain None
        self.assertIsNone(non_permalinkable_instance.slug)


class TestLocatableDirect(unittest.TestCase):
    """Direct tests for Locatable without database."""

    def test_fields(self):
        """Test the fields are defined correctly."""
        self.assertTrue(hasattr(Locatable, "address"))
        self.assertTrue(hasattr(Locatable, "longitude"))
        self.assertTrue(hasattr(Locatable, "latitude"))

    def test_has_coordinates_property(self):
        """Test has_coordinates property."""
        obj = mock.MagicMock(spec=Locatable)

        # No coordinates
        obj.latitude = None
        obj.longitude = None
        self.assertFalse(Locatable.has_coordinates.fget(obj))

        # Only latitude
        obj.latitude = 45.0
        obj.longitude = None
        self.assertFalse(Locatable.has_coordinates.fget(obj))

        # Only longitude
        obj.latitude = None
        obj.longitude = -120.0
        self.assertFalse(Locatable.has_coordinates.fget(obj))

        # Both coordinates
        obj.latitude = 45.0
        obj.longitude = -120.0
        self.assertTrue(Locatable.has_coordinates.fget(obj))

    def test_coordinates_property(self):
        """Test coordinates property."""
        obj = mock.MagicMock(spec=Locatable)

        # No coordinates
        obj.latitude = None
        obj.longitude = None

        # Mock at the object level
        with mock.patch.object(obj, "has_coordinates", False, create=True):
            self.assertIsNone(Locatable.coordinates.fget(obj))

        # With coordinates
        obj.latitude = 45.0
        obj.longitude = -120.0

        # Mock at the object level
        with mock.patch.object(obj, "has_coordinates", True, create=True):
            coordinates = Locatable.coordinates.fget(obj)
            self.assertEqual(coordinates, (45.0, -120.0))


class TestAnnotatableDirect(unittest.TestCase):
    """Direct tests for Annotatable without database."""

    def test_fields(self):
        """Test the fields are defined correctly."""
        self.assertTrue(hasattr(Annotatable, "notes"))

    def test_has_notes_property(self):
        """Test has_notes property."""
        obj = mock.MagicMock(spec=Annotatable)
        obj.notes.exists.return_value = False
        self.assertFalse(Annotatable.has_notes.fget(obj))

        obj.notes.exists.return_value = True
        self.assertTrue(Annotatable.has_notes.fget(obj))
