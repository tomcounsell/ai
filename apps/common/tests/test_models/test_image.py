"""
Tests for the Image model and related functionality.
"""

from unittest import mock

from django.test import TestCase

from ...models import Image, Upload
from ..test_behaviors import TimestampableTest


class ImageTest(TimestampableTest, TestCase):
    """Test cases for the Image model."""

    model = Image

    def create_instance(self, **kwargs):
        """Create an Image instance with default test values."""
        default_kwargs = {
            "original": "https://example.com/image.jpg",
            "meta_data": {
                "mime_type": "image/jpeg",
                "meta": {"width": 800, "height": 600},
            },
        }
        default_kwargs.update(kwargs)
        return Image.objects.create(**default_kwargs)

    def test_inheritance_from_upload(self):
        """Test that Image inherits from Upload."""
        image = self.create_instance()
        self.assertIsInstance(image, Upload)

    def test_thumbnail_url_field(self):
        """Test that thumbnail_url field is available."""
        image = self.create_instance(thumbnail_url="https://example.com/thumbnail.jpg")
        self.assertEqual(image.thumbnail_url, "https://example.com/thumbnail.jpg")

    def test_width_property(self):
        """Test the width property returns the correct value from meta_data."""
        image = self.create_instance()
        self.assertEqual(image.width, 800)

        # Test with no meta field
        image_no_meta = self.create_instance(meta_data={"mime_type": "image/jpeg"})
        self.assertIsNone(image_no_meta.width)

        # Test with meta but no width
        image_no_width = self.create_instance(
            meta_data={"mime_type": "image/jpeg", "meta": {}}
        )
        self.assertIsNone(image_no_width.width)

    def test_height_property(self):
        """Test the height property returns the correct value from meta_data."""
        image = self.create_instance()
        self.assertEqual(image.height, 600)

        # Test with no meta field
        image_no_meta = self.create_instance(meta_data={"mime_type": "image/jpeg"})
        self.assertIsNone(image_no_meta.height)

        # Test with meta but no height
        image_no_height = self.create_instance(
            meta_data={"mime_type": "image/jpeg", "meta": {}}
        )
        self.assertIsNone(image_no_height.height)

    def test_is_image_property(self):
        """Test is_image property returns True for image files."""
        # Create a mock image instance
        image = self.create_instance()

        # Mock the file_type property directly
        with mock.patch.object(
            Image, "file_type", new_callable=mock.PropertyMock
        ) as mock_file_type:
            # For image mimetype
            mock_file_type.return_value = "image/jpeg"
            self.assertTrue(image.is_image)

            # For non-image mimetype
            mock_file_type.return_value = "application/pdf"
            self.assertFalse(image.is_image)

    def test_is_pdf_property(self):
        """Test is_pdf property returns True for PDF files."""
        # Create a mock image instance
        image = self.create_instance()

        # Mock the file_type property directly
        with mock.patch.object(
            Image, "file_type", new_callable=mock.PropertyMock
        ) as mock_file_type:
            # For PDF mimetype
            mock_file_type.return_value = "application/pdf"
            self.assertTrue(image.is_pdf)

            # For non-PDF mimetype
            mock_file_type.return_value = "image/jpeg"
            self.assertFalse(image.is_pdf)

    def test_file_type_property(self):
        """Test file_type property returns the correct mimetype.

        Upload.file_type checks mime_type (content_type or meta_data['mime_type'])
        first, then falls back to guess_type from the URL.
        """
        # When meta_data has mime_type, it should be returned directly
        image = self.create_instance(
            original="https://example.com/image.png",
            meta_data={"mime_type": "image/png"},
        )
        self.assertEqual(image.file_type, "image/png")

        # When content_type is set, it takes precedence over meta_data
        image_with_ct = self.create_instance(
            original="https://example.com/image.png",
            content_type="image/webp",
            meta_data={"mime_type": "image/png"},
        )
        self.assertEqual(image_with_ct.file_type, "image/webp")

        # When neither content_type nor meta_data mime_type, falls back to guess_type
        with mock.patch(
            "apps.common.models.upload.guess_type", return_value=("image/gif", None)
        ):
            image_no_mime = self.create_instance(
                original="https://example.com/image.gif",
                meta_data={},
            )
            self.assertEqual(image_no_mime.file_type, "image/gif")

    def test_file_extension_property(self):
        """Test file_extension property returns the correct extension."""
        image = self.create_instance(
            meta_data={"ext": "jpg", "mime_type": "image/jpeg"}
        )
        self.assertEqual(image.file_extension, "jpg")

        # No extension in meta_data - falls back to URL extraction
        image_no_ext = self.create_instance(meta_data={"mime_type": "image/jpeg"})
        self.assertEqual(image_no_ext.file_extension, "jpg")  # extracted from .jpg URL

        # No extension anywhere
        image_no_ext_at_all = self.create_instance(
            original="https://example.com/image",
            meta_data={"mime_type": "image/jpeg"},
        )
        self.assertEqual(image_no_ext_at_all.file_extension, "")

    def test_dimensions_property(self):
        """Test dimensions property returns width and height tuple."""
        image = self.create_instance()
        self.assertEqual(image.dimensions, (800, 600))

        # No meta data - empty dict is falsy, so dimensions returns None
        image_no_meta = self.create_instance(meta_data={})
        self.assertIsNone(image_no_meta.dimensions)

    def test_link_title_property(self):
        """Test link_title property formats correctly.

        Upload.link_title appends the file extension to the name if not already
        present in the title string.
        """
        # With name - Upload appends extension from meta_data
        image_with_name = self.create_instance(name="test_image.jpg")
        # Extension is extracted from URL (.jpg), and ".JPG" is not in "test_image.jpg"
        # so Upload appends " .JPG"
        self.assertEqual(image_with_name.link_title, "test_image.jpg .JPG")

        # No name, but with etc
        image_with_etc = self.create_instance(
            name="", meta_data={"mime_type": "image/jpeg", "etc": "photo", "ext": "jpg"}
        )
        self.assertEqual(image_with_etc.link_title, "PHOTO .JPG")

        # No name or etc, but with type
        image_with_type = self.create_instance(
            name="",
            meta_data={"mime_type": "image/jpeg", "type": "image", "ext": "jpg"},
        )
        self.assertEqual(image_with_type.link_title, "IMAGE .JPG")

        # Only extension
        image_only_ext = self.create_instance(
            name="", meta_data={"mime_type": "image/jpeg", "ext": "jpg"}
        )
        self.assertEqual(image_only_ext.link_title, " .JPG")
