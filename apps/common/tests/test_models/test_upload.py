"""
Tests for the Upload model and related functionality.
"""

from unittest import mock

from django.test import TestCase, override_settings

from ...models import Upload
from ..test_behaviors import TimestampableTest


class UploadModelTestCase(TimestampableTest, TestCase):
    """Test cases for the Upload model."""

    model = Upload

    def setUp(self):
        """Set up test data."""
        # Create an upload with minimal data
        self.upload_minimal = Upload.objects.create(
            original="https://example.com/file.txt",
            name="simple_file",
            meta_data={},  # Initialize with empty dict instead of None
        )

        # Create an image upload with metadata
        self.image_meta = {
            "mime_type": "image/jpeg",
            "type": "image",
            "ext": "jpg",
            "meta": {
                "width": 800,
                "height": 600,
            },
        }

        self.image_upload = Upload.objects.create(
            original="https://example.com/image.jpg",
            name="test_image",
            thumbnail="https://example.com/image_thumb.jpg",
            meta_data=self.image_meta,
            content_type="image/jpeg",
            size=102400,
        )

        # Create a PDF upload with metadata
        self.pdf_meta = {
            "mime_type": "application/pdf",
            "type": "document",
            "ext": "pdf",
            "etc": "resume",
        }

        self.pdf_upload = Upload.objects.create(
            original="https://example.com/document.pdf",
            meta_data=self.pdf_meta,
            content_type="application/pdf",
            size=51200,
        )

        # Create an S3 upload
        self.s3_upload = Upload.objects.create(
            s3_bucket="test-bucket",
            s3_key="uploads/test-file-123.txt",
            content_type="text/plain",
            size=1024,
            status=Upload.STATUS_COMPLETE,
        )

    def test_file_type_with_mocked_mime(self):
        """Test the file_type property returns the correct mime type."""
        # For uploads with content_type, it should use that first
        self.assertEqual(self.image_upload.file_type, "image/jpeg")

        # For uploads with meta_data mime_type but no content_type
        s3_upload_no_content = Upload.objects.create(
            s3_bucket="test-bucket",
            s3_key="uploads/test-image.jpg",
            meta_data={"mime_type": "image/jpeg"},
        )
        self.assertEqual(s3_upload_no_content.file_type, "image/jpeg")

        # For uploads without content_type or meta_data mime_type, it should use guess_type
        with mock.patch("apps.common.models.upload.guess_type") as mock_guess_type:
            mock_guess_type.return_value = ("text/plain", None)
            self.assertEqual(self.upload_minimal.file_type, "text/plain")

    def test_is_image_property(self):
        """Test the is_image property returns the correct value."""
        # Test with direct mock of property
        with mock.patch.object(
            Upload, "file_type", new_callable=mock.PropertyMock
        ) as mock_file_type:
            # Set the file_type to image/jpeg for testing
            mock_file_type.return_value = "image/jpeg"
            self.assertTrue(self.image_upload.is_image)

            # Set the file_type to something non-image
            mock_file_type.return_value = "application/pdf"
            self.assertFalse(self.pdf_upload.is_image)

    def test_is_pdf_property(self):
        """Test the is_pdf property returns the correct value."""
        # Test with direct mock of property
        with mock.patch.object(
            Upload, "file_type", new_callable=mock.PropertyMock
        ) as mock_file_type:
            # Set the file_type to application/pdf for testing
            mock_file_type.return_value = "application/pdf"
            self.assertTrue(self.pdf_upload.is_pdf)

            # Set the file_type to something non-pdf
            mock_file_type.return_value = "image/jpeg"
            self.assertFalse(self.image_upload.is_pdf)

    def test_is_video_property(self):
        """Test the is_video property returns the correct value."""
        # Create a video upload
        video_upload = Upload.objects.create(
            original="https://example.com/video.mp4", content_type="video/mp4"
        )

        # Test with direct mock of property
        with mock.patch.object(
            Upload, "file_type", new_callable=mock.PropertyMock
        ) as mock_file_type:
            # Set the file_type to video/mp4 for testing
            mock_file_type.return_value = "video/mp4"
            self.assertTrue(video_upload.is_video)

            # Set the file_type to something non-video
            mock_file_type.return_value = "image/jpeg"
            self.assertFalse(self.image_upload.is_video)

    def test_width_height_properties(self):
        """Test the width and height properties return the correct values."""
        # For image uploads with meta data
        with mock.patch.object(
            Upload, "is_image", new_callable=mock.PropertyMock
        ) as mock_is_image:
            mock_is_image.return_value = True
            self.assertEqual(self.image_upload.width, 800)
            self.assertEqual(self.image_upload.height, 600)

            # For uploads without image meta data
            self.assertIsNone(self.upload_minimal.width)
            self.assertIsNone(self.upload_minimal.height)

    def test_file_extension_property(self):
        """Test the file_extension property returns the correct value."""
        # From meta_data
        self.assertEqual(self.image_upload.file_extension, "jpg")
        self.assertEqual(self.pdf_upload.file_extension, "pdf")

        # From S3 key
        self.assertEqual(self.s3_upload.file_extension, "txt")

        # From original URL
        url_upload = Upload.objects.create(
            original="https://example.com/path/to/file.docx"
        )
        self.assertEqual(url_upload.file_extension, "docx")

        # No extension
        no_ext_upload = Upload.objects.create(
            original="https://example.com/no-extension"
        )
        self.assertEqual(no_ext_upload.file_extension, "")

    def test_dimensions_property(self):
        """Test the dimensions property returns the correct values."""
        # Test with is_image mocked
        with mock.patch.object(
            Upload, "is_image", new_callable=mock.PropertyMock
        ) as mock_is_image:
            # Test with is_image True
            mock_is_image.return_value = True

            # For uploads with meta data
            self.assertEqual(self.image_upload.dimensions, (800, 600))

            # For uploads without meta_data
            self.assertIsNone(self.upload_minimal.dimensions)

            # Test with is_video True
            with mock.patch.object(
                Upload, "is_video", new_callable=mock.PropertyMock
            ) as mock_is_video:
                mock_is_image.return_value = False
                mock_is_video.return_value = True

                # Create a video upload with dimensions
                video_meta = {
                    "mime_type": "video/mp4",
                    "type": "video",
                    "ext": "mp4",
                    "meta": {
                        "width": 1280,
                        "height": 720,
                    },
                }
                video_upload = Upload.objects.create(
                    original="https://example.com/video.mp4",
                    content_type="video/mp4",
                    meta_data=video_meta,
                )

                self.assertEqual(video_upload.dimensions, (1280, 720))

    def test_link_title_property(self):
        """Test the link_title property returns the correct value."""
        # When name is present
        self.assertEqual(self.image_upload.link_title, "test_image .JPG")

        # When name is not present but etc is present
        test_pdf = Upload.objects.create(
            original="https://example.com/test.pdf",
            meta_data={"etc": "resume", "ext": "pdf"},
        )
        self.assertEqual(test_pdf.link_title, "RESUME .PDF")

        # Create an upload with type but no name or etc
        type_only_meta = {"type": "spreadsheet", "ext": "xlsx"}
        type_only_upload = Upload.objects.create(
            original="https://example.com/data.xlsx", meta_data=type_only_meta
        )
        self.assertEqual(type_only_upload.link_title, "SPREADSHEET .XLSX")

        # For uploads with S3 key but no other metadata
        self.assertEqual(self.s3_upload.link_title, "test-file-123.txt .TXT")

        # For uploads without any relevant meta_data or S3 key
        minimal_meta = {"irrelevant": "data"}
        minimal_upload = Upload.objects.create(
            original="https://example.com/unknown", meta_data=minimal_meta
        )
        self.assertEqual(minimal_upload.link_title, "")

    def test_s3_url_property(self):
        """Test the s3_url property returns the correct value."""
        # For uploads with S3 bucket and key
        self.assertEqual(
            self.s3_upload.s3_url,
            "https://test-bucket.s3.amazonaws.com/uploads/test-file-123.txt",
        )

        # For uploads without S3 bucket and key
        self.assertEqual(self.image_upload.s3_url, self.image_upload.original)

    @override_settings(
        AWS_ACCESS_KEY_ID="test",
        AWS_SECRET_ACCESS_KEY="test",
        AWS_S3_BUCKET_NAME="test",
    )
    def test_get_presigned_url(self):
        """Test the get_presigned_url method returns the correct value."""
        # Mock S3Client
        with mock.patch("apps.integration.aws.s3.S3Client") as mock_s3_client_cls:
            mock_client = mock.MagicMock()
            mock_client.generate_presigned_url.return_value = {
                "success": True,
                "url": "https://presigned-url/test",
            }
            mock_s3_client_cls.return_value = mock_client

            # Test with S3 bucket and key
            url = self.s3_upload.get_presigned_url()
            self.assertEqual(url, "https://presigned-url/test")

            # Test with no S3 bucket or key
            url = self.image_upload.get_presigned_url()
            self.assertEqual(url, self.image_upload.original)

            # Test with S3 client error
            mock_client.generate_presigned_url.return_value = {
                "success": False,
                "error": "Test error",
            }
            url = self.s3_upload.get_presigned_url()
            self.assertEqual(url, self.s3_upload.s3_url)

    @override_settings(
        AWS_ACCESS_KEY_ID="test",
        AWS_SECRET_ACCESS_KEY="test",
        AWS_S3_BUCKET_NAME="test",
    )
    def test_delete_from_s3(self):
        """Test the delete_from_s3 method returns the correct value."""
        # Mock S3Client
        with mock.patch("apps.integration.aws.s3.S3Client") as mock_s3_client_cls:
            mock_client = mock.MagicMock()
            mock_client.delete_object.return_value = {"success": True}
            mock_s3_client_cls.return_value = mock_client

            # Test with S3 bucket and key
            result = self.s3_upload.delete_from_s3()
            self.assertTrue(result)

            # Test with no S3 bucket or key
            result = self.image_upload.delete_from_s3()
            self.assertFalse(result)

            # Test with S3 client error
            mock_client.delete_object.return_value = {
                "success": False,
                "error": "Test error",
            }
            result = self.s3_upload.delete_from_s3()
            self.assertFalse(result)

    def test_save_method(self):
        """Test the save method sets original URL from S3."""
        # Create an upload with S3 but no original
        upload = Upload.objects.create(
            s3_bucket="test-bucket", s3_key="uploads/new-file.txt"
        )

        # Check that original was set
        self.assertEqual(
            upload.original, "https://test-bucket.s3.amazonaws.com/uploads/new-file.txt"
        )

        # Test with None meta_data
        upload = Upload.objects.create(
            original="https://example.com/test.txt", meta_data=None
        )

        # Check that meta_data was initialized
        self.assertEqual(upload.meta_data, {})

    def test_delete_method(self):
        """Test the delete method also deletes from S3."""
        # Mock delete_from_s3
        with mock.patch.object(Upload, "delete_from_s3") as mock_delete_from_s3:
            # Test with S3 bucket and key
            s3_upload_copy = Upload.objects.create(
                original="https://example.com/test.txt",
                s3_bucket="test-bucket",
                s3_key="uploads/test-file.txt",
            )
            s3_upload_copy.delete()
            mock_delete_from_s3.assert_called_once()

            # Test with S3 delete error
            mock_delete_from_s3.reset_mock()
            mock_delete_from_s3.side_effect = Exception("Test error")

            # Create a new upload object for the error case
            s3_upload_error = Upload.objects.create(
                original="https://example.com/error.txt",
                s3_bucket="test-bucket",
                s3_key="uploads/error-file.txt",
            )

            # Should not raise exception
            s3_upload_error.delete()

    def test_str_method(self):
        """Test the __str__ method returns the correct value."""
        # With name
        self.assertEqual(str(self.image_upload), "Upload: test_image")

        # Without name
        no_name_upload = Upload.objects.create(
            original="https://example.com/no-name.txt"
        )
        self.assertEqual(str(no_name_upload), f"Upload: {no_name_upload.id}")

    def create_instance(self, **kwargs):
        """Create an Upload instance for testing."""
        if "meta_data" not in kwargs:
            kwargs["meta_data"] = {}
        return Upload.objects.create(original="https://example.com/file.txt", **kwargs)
