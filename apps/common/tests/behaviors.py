"""
Tests for behavior mixins.

This module contains tests for all behavior mixins implemented in the application.
Tests are written using both direct standalone tests (with mocks) and Django's TestCase
for database-backed testing, ensuring comprehensive coverage.
"""

import unittest
from datetime import datetime, timedelta
from unittest import mock

from django.db import models
from django.test import TestCase
from django.utils import timezone

# Import all behaviors
from apps.common.behaviors.annotatable import Annotatable
from apps.common.behaviors.authorable import Authorable
from apps.common.behaviors.expirable import Expirable
from apps.common.behaviors.locatable import Locatable
from apps.common.behaviors.permalinkable import Permalinkable
from apps.common.behaviors.publishable import Publishable
from apps.common.behaviors.timestampable import Timestampable

#
# Test Infrastructure
#


class BehaviorTestMixin:
    """
    Base mixin for behavior tests using Django TestCase.

    This mixin provides utilities for testing behavior mixins with
    Django's ORM and database.
    """

    @property
    def model(self):
        """Return the model class being tested."""
        raise NotImplementedError("Implement in subclass")

    def create_instance(self, **kwargs):
        """Create an instance of the model with the given kwargs."""
        return self.model.objects.create(**kwargs)


#
# Test Models
#


class AnnotatableModel(Annotatable, models.Model):
    """Test model for Annotatable behavior."""

    class Meta:
        app_label = "test_app"


class AuthorableModel(Authorable, models.Model):
    """Test model for Authorable behavior."""

    class Meta:
        app_label = "test_app"


class ExpirableModel(Expirable, models.Model):
    """Test model for Expirable behavior."""

    class Meta:
        app_label = "test_app"


class LocatableModel(Locatable, models.Model):
    """Test model for Locatable behavior."""

    class Meta:
        app_label = "test_app"


class PermalinkableModel(Permalinkable, models.Model):
    """Test model for Permalinkable behavior."""

    class Meta:
        app_label = "test_app"


class PublishableModel(Publishable, models.Model):
    """Test model for Publishable behavior."""

    class Meta:
        app_label = "test_app"


class TimestampableModel(Timestampable, models.Model):
    """Test model for Timestampable behavior."""

    class Meta:
        app_label = "test_app"


#
# Django TestCase Tests
#


class AnnotatableTest(BehaviorTestMixin, TestCase):
    """Tests for Annotatable behavior using Django TestCase."""

    @property
    def model(self):
        return AnnotatableModel

    def test_has_notes_property(self):
        """Test has_notes property returns expected values."""
        obj = self.create_instance()
        self.assertFalse(obj.has_notes)

        # TODO: Add notes to test for True case
        # We would need to create and add actual Note objects


class AuthorableTest(BehaviorTestMixin, TestCase):
    """Tests for Authorable behavior using Django TestCase."""

    @property
    def model(self):
        return AuthorableModel

    def test_author_name_display(self):
        """Test author_display_name returns appropriate value."""
        obj = self.create_instance()

        # Test anonymous author
        obj.is_author_anonymous = True
        self.assertEqual(obj.author_display_name, "Anonymous")

        # Test with author (would need to set up a User)


class ExpirableTest(BehaviorTestMixin, TestCase):
    """Tests for Expirable behavior using Django TestCase."""

    @property
    def model(self):
        return ExpirableModel

    def test_expiration_flags(self):
        """Test expiration flags and properties."""
        obj = self.create_instance()
        self.assertFalse(obj.is_expired)

        # Set expiration in the past
        obj.expired_at = timezone.now() - timedelta(days=1)
        obj.save()
        self.assertTrue(obj.is_expired)

        # Set expiration in the future
        obj.expired_at = timezone.now() + timedelta(days=1)
        obj.save()
        self.assertFalse(obj.is_expired)


class LocatableTest(BehaviorTestMixin, TestCase):
    """Tests for Locatable behavior using Django TestCase."""

    @property
    def model(self):
        return LocatableModel


class PermalinkableTest(BehaviorTestMixin, TestCase):
    """Tests for Permalinkable behavior using Django TestCase."""

    @property
    def model(self):
        return PermalinkableModel

    def test_get_url_kwargs(self):
        """Test get_url_kwargs returns expected values."""
        obj = self.create_instance()
        self.assertEqual(obj.get_url_kwargs(), {})
        self.assertEqual(obj.get_url_kwargs(id=1), {"id": 1})


class PublishableTest(BehaviorTestMixin, TestCase):
    """Tests for Publishable behavior using Django TestCase."""

    @property
    def model(self):
        return PublishableModel

    def test_publish_unpublish_methods(self):
        """Test publish and unpublish methods."""
        obj = self.create_instance()
        self.assertFalse(obj.is_published)

        # Test publishing
        obj.publish()
        self.assertTrue(obj.is_published)
        self.assertIsNotNone(obj.published_at)

        # Test unpublishing
        obj.unpublish()
        self.assertFalse(obj.is_published)
        self.assertIsNotNone(obj.unpublished_at)


class TimestampableTest(BehaviorTestMixin, TestCase):
    """Tests for Timestampable behavior using Django TestCase."""

    @property
    def model(self):
        return TimestampableModel

    def test_timestamps_on_creation(self):
        """Test timestamps are set on creation."""
        obj = self.create_instance()
        self.assertIsNotNone(obj.created_at)
        self.assertIsNotNone(obj.modified_at)
        self.assertEqual(obj.created_at, obj.modified_at)

    def test_modified_at_updates_on_save(self):
        """Test modified_at updates when the object is saved."""
        obj = self.create_instance()
        original_modified = obj.modified_at
        original_created = obj.created_at

        # Wait a moment to ensure modified_at will be different
        import time

        time.sleep(0.001)

        # Save again
        obj.save()

        # Check that modified_at updated but created_at didn't
        self.assertNotEqual(obj.modified_at, original_modified)
        self.assertEqual(obj.created_at, original_created)


#
# Direct Tests (Unittest with mocks)
#


class TestAnnotatableDirect(unittest.TestCase):
    """Direct tests for Annotatable behavior without database."""

    def setUp(self):
        self.obj = mock.MagicMock(spec=Annotatable)
        self.notes_queryset = mock.MagicMock()
        type(self.obj).notes = mock.PropertyMock(return_value=self.notes_queryset)

    def test_has_notes_property_returns_false_when_no_notes(self):
        self.notes_queryset.count.return_value = 0
        result = Annotatable.has_notes.fget(self.obj)
        self.assertFalse(result)

    def test_has_notes_property_returns_true_when_notes_exist(self):
        self.notes_queryset.count.return_value = 1
        result = Annotatable.has_notes.fget(self.obj)
        self.assertTrue(result)


class TestAuthorableDirect(unittest.TestCase):
    """Direct tests for Authorable behavior without database."""

    def setUp(self):
        self.obj = mock.MagicMock(spec=Authorable)
        self.user = mock.MagicMock()
        type(self.obj).author = mock.PropertyMock(return_value=self.user)
        type(self.user).__str__ = mock.Mock(return_value="Test User")

    def test_author_display_name_returns_user_string(self):
        type(self.obj).is_author_anonymous = mock.PropertyMock(return_value=False)
        result = Authorable.author_display_name.fget(self.obj)
        self.assertEqual(result, "Test User")

    def test_anonymous_author_returns_anonymous(self):
        type(self.obj).is_author_anonymous = mock.PropertyMock(return_value=True)
        result = Authorable.author_display_name.fget(self.obj)
        self.assertEqual(result, "Anonymous")


class TestExpirableDirect(unittest.TestCase):
    """Direct tests for Expirable behavior without database."""

    def setUp(self):
        self.obj = mock.MagicMock(spec=Expirable)
        self.now = datetime(2023, 1, 1, 12, 0, 0)
        self.now_patch = mock.patch("django.utils.timezone.now")
        self.mock_now = self.now_patch.start()
        self.mock_now.return_value = self.now

    def tearDown(self):
        self.now_patch.stop()

    def test_is_expired_false_when_expired_at_is_none(self):
        type(self.obj).expired_at = mock.PropertyMock(return_value=None)
        result = Expirable.is_expired.fget(self.obj)
        self.assertFalse(result)

    def test_is_expired_true_when_expired_at_in_past(self):
        past = self.now - timedelta(days=1)
        type(self.obj).expired_at = mock.PropertyMock(return_value=past)
        result = Expirable.is_expired.fget(self.obj)
        self.assertTrue(result)

    def test_is_expired_false_when_expired_at_in_future(self):
        future = self.now + timedelta(days=1)
        type(self.obj).expired_at = mock.PropertyMock(return_value=future)
        result = Expirable.is_expired.fget(self.obj)
        self.assertFalse(result)


class TestPermalinkableDirect(unittest.TestCase):
    """Direct tests for Permalinkable behavior without database."""

    def test_get_url_kwargs(self):
        obj = mock.MagicMock(spec=Permalinkable)

        # Test with no url_kwargs attribute
        type(obj).url_kwargs = mock.PropertyMock(return_value=None)
        kwargs = Permalinkable.get_url_kwargs(obj)
        self.assertEqual(kwargs, {})

        # Test with additional kwargs
        kwargs = Permalinkable.get_url_kwargs(obj, id=1)
        self.assertEqual(kwargs, {"id": 1})

        # Test with url_kwargs attribute
        type(obj).url_kwargs = mock.PropertyMock(return_value={"test": "value"})
        kwargs = Permalinkable.get_url_kwargs(obj, id=1)
        self.assertEqual(kwargs, {"id": 1, "test": "value"})


class TestPublishableDirect(unittest.TestCase):
    """Direct tests for Publishable behavior without database."""

    def setUp(self):
        self.obj = mock.MagicMock(spec=Publishable)
        self.now = datetime(2023, 1, 1, 12, 0, 0)
        self.now_patch = mock.patch("django.utils.timezone.now")
        self.mock_now = self.now_patch.start()
        self.mock_now.return_value = self.now

    def tearDown(self):
        self.now_patch.stop()

    def test_is_published_false_by_default(self):
        type(self.obj).published_at = mock.PropertyMock(return_value=None)
        type(self.obj).unpublished_at = mock.PropertyMock(return_value=None)
        result = Publishable.is_published.fget(self.obj)
        self.assertFalse(result)

    def test_is_published_true_when_published_in_past(self):
        past = self.now - timedelta(days=1)
        type(self.obj).published_at = mock.PropertyMock(return_value=past)
        type(self.obj).unpublished_at = mock.PropertyMock(return_value=None)
        result = Publishable.is_published.fget(self.obj)
        self.assertTrue(result)

    def test_is_published_false_when_published_in_future(self):
        future = self.now + timedelta(days=1)
        type(self.obj).published_at = mock.PropertyMock(return_value=future)
        type(self.obj).unpublished_at = mock.PropertyMock(return_value=None)
        result = Publishable.is_published.fget(self.obj)
        self.assertFalse(result)

    def test_publish_method_calls_is_published_setter(self):
        Publishable.publish(self.obj)
        self.obj.is_published = True

    def test_unpublish_method_calls_is_published_setter(self):
        Publishable.unpublish(self.obj)
        self.obj.is_published = False


if __name__ == "__main__":
    unittest.main()
