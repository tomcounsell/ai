"""
Tests for the Note model and related functionality.
"""

import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase

from ...models import Note
from ..test_behaviors import AuthorableTest, TimestampableTest

User = get_user_model()


class NoteModelTestCase(AuthorableTest, TimestampableTest, TestCase):
    """Test cases for the Note model."""

    model = Note

    def setUp(self):
        """Set up test data."""
        self.user = User.objects.create_user(
            username="testuser", email="testuser@example.com", password="password"
        )

        self.note = Note.objects.create(author=self.user, text="This is a test note")

    def test_note_creation(self):
        """Test that a note can be created with the expected field values."""
        self.assertEqual(self.note.text, "This is a test note")
        self.assertEqual(self.note.author, self.user)
        self.assertIsInstance(self.note.id, uuid.UUID)
        self.assertFalse(self.note.is_author_anonymous)

    def test_empty_text(self):
        """Test that a note can be created with empty text."""
        note = Note.objects.create(author=self.user, text="")
        self.assertEqual(note.text, "")

    def test_anonymous_author(self):
        """Test that a note can be created with an anonymous author."""
        note = Note.objects.create(
            author=self.user, text="Anonymous note", is_author_anonymous=True
        )
        self.assertTrue(note.is_author_anonymous)

    def create_instance(self, **kwargs):
        """Create a Note instance for testing."""
        if "author" not in kwargs and "is_author_anonymous" not in kwargs:
            kwargs["author"] = self.user

        if "text" not in kwargs:
            kwargs["text"] = "Test note"

        return Note.objects.create(**kwargs)
