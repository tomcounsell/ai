from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.common.models import Address, BlogPost
from apps.common.tests.factories import UserFactory


class BlogPostModelTestCase(TestCase):
    """Test case for BlogPost model."""

    def setUp(self):
        self.user = UserFactory.create()
        self.blog_post = BlogPost.objects.create(
            title="Test Blog Post",
            subtitle="A subtitle for testing",
            content="This is the content of the test blog post.",
            author=self.user,
            reading_time_minutes=5,
            tags="test,django,example",
        )

    # Testing base fields
    def test_blog_post_creation(self):
        """Test that a blog post can be created."""
        self.assertIsNotNone(self.blog_post.id)
        self.assertEqual(self.blog_post.title, "Test Blog Post")
        self.assertEqual(
            self.blog_post.content, "This is the content of the test blog post."
        )
        self.assertEqual(self.blog_post.reading_time_minutes, 5)
        self.assertEqual(self.blog_post.tags, "test,django,example")

    def test_blog_post_str_method(self):
        """Test the __str__ method."""
        self.assertEqual(str(self.blog_post), "Test Blog Post")

    # Testing model properties
    def test_summary_property(self):
        """Test the summary property."""
        self.assertEqual(
            self.blog_post.summary, "This is the content of the test blog post."
        )

        long_content = "A" * 250
        self.blog_post.content = long_content
        self.assertEqual(self.blog_post.summary, f"{long_content[:197]}...")

    def test_is_featured_property(self):
        """Test the is_featured property."""
        self.assertFalse(self.blog_post.is_featured)

        self.blog_post.tags = "test,featured,example"
        self.assertTrue(self.blog_post.is_featured)

    def test_reading_time_display_property(self):
        """Test the reading_time_display property."""
        self.assertEqual(self.blog_post.reading_time_display, "5 minutes")

        self.blog_post.reading_time_minutes = 1
        self.assertEqual(self.blog_post.reading_time_display, "1 minute")

    def test_get_absolute_url(self):
        """Test the get_absolute_url method."""
        self.blog_post.slug = "test-blog-post"
        self.assertEqual(self.blog_post.get_absolute_url(), "/blog/test-blog-post/")

    def test_get_meta_description(self):
        """Test the get_meta_description method."""
        self.assertEqual(
            self.blog_post.get_meta_description(), "A subtitle for testing"
        )

        self.blog_post.subtitle = ""
        self.assertEqual(self.blog_post.get_meta_description(), self.blog_post.summary)

    def test_tag_management(self):
        """Test adding and removing tags."""
        self.blog_post.add_tag("python")
        self.assertIn("python", self.blog_post.tags)

        self.blog_post.remove_tag("test")
        self.assertNotIn("test", self.blog_post.tags)

        # Test adding duplicate tag
        original_tags = self.blog_post.tags
        self.blog_post.add_tag("python")
        self.assertEqual(self.blog_post.tags, original_tags)

    # Testing Timestampable behavior
    def test_timestampable_behavior(self):
        """Test the timestampable behavior."""
        self.assertIsNotNone(self.blog_post.created_at)
        self.assertIsNotNone(self.blog_post.modified_at)

        old_modified = self.blog_post.modified_at
        self.blog_post.title = "Updated Title"
        self.blog_post.save()
        self.assertGreater(self.blog_post.modified_at, old_modified)

    # Testing Authorable behavior
    def test_authorable_behavior(self):
        """Test the authorable behavior."""
        self.assertEqual(self.blog_post.author, self.user)
        self.assertIsNotNone(self.blog_post.authored_at)
        self.assertFalse(self.blog_post.is_author_anonymous)
        self.assertEqual(self.blog_post.author_display_name, str(self.user))

        self.blog_post.is_author_anonymous = True
        self.assertEqual(self.blog_post.author_display_name, "Anonymous")

    # Testing Publishable behavior
    def test_publishable_behavior(self):
        """Test the publishable behavior."""
        self.assertIsNone(self.blog_post.published_at)
        self.assertFalse(self.blog_post.is_published)

        # Test publishing
        self.blog_post.publish()
        self.assertIsNotNone(self.blog_post.published_at)
        self.assertTrue(self.blog_post.is_published)

        # Test unpublishing
        self.blog_post.unpublish()
        self.assertIsNotNone(self.blog_post.unpublished_at)
        self.assertFalse(self.blog_post.is_published)

    # Testing Expirable behavior
    def test_expirable_behavior(self):
        """Test the expirable behavior."""
        self.assertIsNone(self.blog_post.expired_at)
        self.assertFalse(self.blog_post.is_expired)

        # Test expiring
        self.blog_post.is_expired = True
        self.assertIsNotNone(self.blog_post.expired_at)
        self.assertTrue(self.blog_post.is_expired)

        # Test setting valid_at in future
        future_time = timezone.now() + timedelta(days=7)
        self.blog_post.valid_at = future_time
        self.blog_post.is_expired = False
        self.assertIsNone(self.blog_post.expired_at)

    # Testing Locatable behavior
    def test_locatable_behavior(self):
        """Test the locatable behavior."""
        self.assertIsNone(self.blog_post.address)
        self.assertIsNone(self.blog_post.latitude)
        self.assertIsNone(self.blog_post.longitude)

        # Add location info
        address = Address.objects.create(
            line_1="123 Test Street", city="Test City", region="Test Region"
        )
        self.blog_post.address = address
        self.blog_post.latitude = 37.7749
        self.blog_post.longitude = -122.4194
        self.blog_post.save()

        self.assertEqual(self.blog_post.address, address)
        self.assertEqual(self.blog_post.latitude, 37.7749)
        self.assertEqual(self.blog_post.longitude, -122.4194)

    # Testing Permalinkable behavior
    def test_permalinkable_behavior(self):
        """Test the permalinkable behavior."""
        # Automatic slug generation is tested
        self.assertEqual(self.blog_post.slug, "test-blog-post")

        # Test slug updating
        self.blog_post.title = "Updated Title"
        self.blog_post.slug = None
        self.blog_post.save()
        self.assertEqual(self.blog_post.slug, "updated-title")

    # Testing Annotatable behavior
    def test_annotatable_behavior(self):
        """Test the annotatable behavior."""
        self.assertFalse(self.blog_post.has_notes)

        # Add a note
        note = self.blog_post.notes.create(author=self.user, text="This is a test note")

        self.assertEqual(self.blog_post.notes.count(), 1)
        self.assertTrue(self.blog_post.has_notes)
        self.assertEqual(
            list(self.blog_post.notes.all())[0].text, "This is a test note"
        )
