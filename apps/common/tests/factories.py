"""
Factory classes for creating test models.

These factories allow for easy creation of model instances for testing.
Follows the Factory pattern to create models with sensible defaults
that can be overridden as needed.
"""

import random
from typing import Any, Dict, Type, TypeVar

from django.contrib.auth import get_user_model
from django.db import models
from django.utils.text import slugify

from apps.common.models import (
    SMS,
    Address,
    BlogPost,
    City,
    Country,
    Currency,
    Document,
    Email,
    Image,
    Note,
    Team,
    TeamMember,
    Upload,
)

User = get_user_model()
T = TypeVar("T", bound=models.Model)


class ModelFactory:
    """Base factory class for creating model instances."""

    model_class: type[models.Model]
    default_data: dict[str, Any] = {}

    @classmethod
    def create(cls, **kwargs) -> T:
        """Create and save a model instance with given attributes."""
        data = cls.default_data.copy()
        data.update(kwargs)
        instance = cls.model_class(**data)
        instance.save()
        return instance

    @classmethod
    def build(cls, **kwargs) -> T:
        """Build a model instance without saving to database."""
        data = cls.default_data.copy()
        data.update(kwargs)
        return cls.model_class(**data)


class UserFactory(ModelFactory):
    """Factory for User model."""

    model_class = User
    default_data = {
        "username": lambda: f"user_{random.randint(1000, 9999)}",
        "email": lambda: f"user_{random.randint(1000, 9999)}@example.com",
        "first_name": "Test",
        "last_name": "User",
        "is_active": True,
    }

    @classmethod
    def create(cls, **kwargs) -> User:
        """Create a user with processed default values."""
        data = cls.default_data.copy()

        # Process any callable defaults
        for key, value in data.items():
            if callable(value):
                data[key] = value()

        data.update(kwargs)

        # Special handling for User model creation
        password = data.pop("password", "testpassword123")
        user = User.objects.create_user(**data)
        user.set_password(password)
        user.save()
        return user


class CountryFactory(ModelFactory):
    """Factory for Country model."""

    model_class = Country
    default_data = {
        "name": "United States",
        "code": "US",
        "calling_code": "1",
    }


class AddressFactory(ModelFactory):
    """Factory for Address model."""

    model_class = Address
    default_data = {
        "line_1": "123 Test Street",
        "line_2": "Apt 4B",
        "city": "Test City",
        "region": "TS",  # Changed from 'state' to match Address model fields
        "postal_code": "12345",
        "country": None,  # Will be set in create method
    }

    @classmethod
    def create(cls, **kwargs) -> Address:
        """Create an address with a valid country if not specified."""
        data = cls.default_data.copy()
        data.update(kwargs)

        # Create a country if one isn't provided
        if not data.get("country"):
            data["country"] = CountryFactory.create()

        return super().create(**data)


class CurrencyFactory(ModelFactory):
    """Factory for Currency model."""

    model_class = Currency
    default_data = {
        "code": "USD",
        "name": "US Dollar",
    }


class NoteFactory(ModelFactory):
    """Factory for Note model."""

    model_class = Note
    default_data = {
        "text": "This is a test note.",
        "author": None,  # Will be set in create method
    }

    @classmethod
    def create(cls, **kwargs) -> Note:
        """Create a note with a valid author if not specified."""
        data = cls.default_data.copy()
        data.update(kwargs)

        # Create an author if one isn't provided
        if not data.get("author"):
            data["author"] = UserFactory.create()

        return super().create(**data)


class UploadFactory(ModelFactory):
    """Factory for Upload model."""

    model_class = Upload
    default_data = {
        "original": "https://example.com/test.jpg",
        "name": "test.jpg",
        "meta_data": {
            "mime_type": "image/jpeg",
            "type": "image",
            "ext": "jpg",
            "meta": {"width": 800, "height": 600},
        },
    }


class ImageFactory(ModelFactory):
    """Factory for Image model."""

    model_class = Image
    default_data = {
        "original": "https://example.com/test.jpg",
        "name": "test.jpg",
        "thumbnail_url": "https://example.com/test_thumbnail.jpg",
        "meta_data": {
            "mime_type": "image/jpeg",
            "type": "image",
            "ext": "jpg",
            "meta": {"width": 800, "height": 600},
        },
    }


class DocumentFactory(ModelFactory):
    """Factory for Document model."""

    model_class = Document
    default_data = {
        "original": "https://example.com/test.pdf",
        "name": "test.pdf",
        "meta_data": {
            "mime_type": "application/pdf",
            "type": "document",
            "ext": "pdf",
        },
    }

    @classmethod
    def create(cls, **kwargs) -> Document:
        """Create a document with valid defaults."""
        data = cls.default_data.copy()
        data.update(kwargs)
        return super().create(**data)


class BlogPostFactory(ModelFactory):
    """Factory for BlogPost model."""

    model_class = BlogPost
    default_data = {
        "title": "Test Blog Post",
        "content": "This is a test blog post with sample content.",
        "reading_time_minutes": 3,
        "tags": "test, sample, factory",
        "featured_image": None,  # Will be set conditionally in create method
        "author": None,  # Will be set in create method
    }

    @classmethod
    def create(cls, **kwargs) -> BlogPost:
        """Create a blog post with valid relationships if not specified."""
        data = cls.default_data.copy()
        data.update(kwargs)

        # Create an author if one isn't provided (from Authorable mixin)
        if not data.get("author"):
            data["author"] = UserFactory.create()

        # Optionally create a featured image (50% chance)
        if not data.get("featured_image") and random.choice([True, False]):
            data["featured_image"] = ImageFactory.create()

        instance = cls.model_class(**data)
        instance.save()  # Special handling for BlogPost with behaviors
        return instance


class CityFactory(ModelFactory):
    """Factory for City model."""

    model_class = City
    default_data = {
        "name": "Test City",
        "code": "TST",
        "country": None,  # Will be set in create method
    }

    @classmethod
    def create(cls, **kwargs) -> City:
        """Create a city with a valid country if not specified."""
        data = cls.default_data.copy()
        data.update(kwargs)

        # Create a country if one isn't provided
        if not data.get("country"):
            data["country"] = CountryFactory.create()

        # Ensure code is uppercase (the model might be enforcing this)
        if "code" in data:
            data["code"] = data["code"].upper()

        return super().create(**data)


class TeamFactory(ModelFactory):
    """Factory for Team model."""

    model_class = Team
    default_data = {
        "name": lambda: f"Team {random.randint(1000, 9999)}",
        "slug": None,  # Will be set based on name
        "description": "This is a test team.",
        "is_active": True,
    }

    @classmethod
    def create(cls, **kwargs) -> Team:
        """Create a team with processed default values."""
        data = cls.default_data.copy()

        # Process any callable defaults
        for key, value in data.items():
            if callable(value):
                data[key] = value()

        data.update(kwargs)

        # Generate slug from name if not provided
        if not data.get("slug"):
            data["slug"] = slugify(data["name"])

        return super().create(**data)


class TeamMemberFactory(ModelFactory):
    """Factory for TeamMember model."""

    model_class = TeamMember
    default_data = {
        "team": None,  # Will be set in create method
        "user": None,  # Will be set in create method
        "role": "MEMBER",  # Default role
    }

    @classmethod
    def create(cls, **kwargs) -> TeamMember:
        """Create a team member with valid relationships if not specified."""
        data = cls.default_data.copy()
        data.update(kwargs)

        # Create a team if one isn't provided
        if not data.get("team"):
            data["team"] = TeamFactory.create()

        # Create a user if one isn't provided
        if not data.get("user"):
            data["user"] = UserFactory.create()

        return super().create(**data)


class EmailFactory(ModelFactory):
    """Factory for Email model."""

    model_class = Email
    default_data = {
        "to_address": lambda: f"recipient_{random.randint(1000, 9999)}@example.com",
        "from_address": "sender@example.com",
        "subject": "Test Email Subject",
        "body": "This is a test email body.",
        "type": 1,  # NOTIFICATION type
    }

    @classmethod
    def create(cls, **kwargs) -> Email:
        """Create an email with processed default values."""
        data = cls.default_data.copy()

        # Process any callable defaults
        for key, value in data.items():
            if callable(value):
                data[key] = value()

        data.update(kwargs)
        return super().create(**data)


class SMSFactory(ModelFactory):
    """Factory for SMS model."""

    model_class = SMS
    default_data = {
        "to_number": lambda: f"+1555{random.randint(1000000, 9999999)}",
        "from_number": "+15551234567",
        "body": "This is a test SMS message.",
    }

    @classmethod
    def create(cls, **kwargs) -> SMS:
        """Create an SMS with processed default values."""
        data = cls.default_data.copy()

        # Process any callable defaults
        for key, value in data.items():
            if callable(value):
                data[key] = value()

        data.update(kwargs)
        return super().create(**data)
