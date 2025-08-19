"""
Tests for the example pages (landing, pricing, blog).

These tests verify that:
- The example pages render correctly
- The pages have the correct content and structure
- The pages are accessible without authentication
"""

from django.test import Client, TestCase
from django.urls import reverse


class ExamplePagesTestCase(TestCase):
    """Test rendering and functionality of example pages."""

    def setUp(self):
        """Set up test data."""
        self.client = Client()

    def test_landing_page_renders(self):
        """Test that the landing page renders correctly with expected content."""
        response = self.client.get(reverse("public:landing"))
        self.assertEqual(response.status_code, 200)

        # Check template
        self.assertTemplateUsed(response, "pages/landing.html")

        # Check content
        content = response.content.decode("utf-8")
        self.assertIn("Welcome to Our Platform", content)
        self.assertIn("Get Started", content)

        # Check blocks are rendered
        self.assertIn("How it works", content)
        self.assertIn("Features", content)

    def test_pricing_page_renders(self):
        """Test that the pricing page renders correctly with expected content."""
        response = self.client.get(reverse("public:pricing"))
        self.assertEqual(response.status_code, 200)

        # Check template
        self.assertTemplateUsed(response, "pages/pricing.html")

        # Check content
        content = response.content.decode("utf-8")
        self.assertIn("Pricing Plans", content)
        self.assertIn("Free", content)
        self.assertIn("Premium", content)
        self.assertIn("Enterprise", content)

    def test_blog_page_renders(self):
        """Test that the blog page renders correctly with expected content."""
        response = self.client.get(reverse("public:blog"))
        self.assertEqual(response.status_code, 200)

        # Check template
        self.assertTemplateUsed(response, "pages/blog.html")

        # Check content
        content = response.content.decode("utf-8")
        self.assertIn("Blog", content)
        self.assertIn("Latest Articles", content)

    def test_blog_post_page_renders(self):
        """Test that a blog post page renders correctly with expected content."""
        response = self.client.get(reverse("public:blog-post", args=["sample-post"]))
        self.assertEqual(response.status_code, 200)

        # Check template
        self.assertTemplateUsed(response, "pages/blog_post.html")

        # Check content
        content = response.content.decode("utf-8")
        self.assertIn("Sample Blog Post", content)
        self.assertIn("Published", content)
