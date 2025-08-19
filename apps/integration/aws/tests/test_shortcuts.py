"""
Tests for AWS S3 shortcuts.
"""

from unittest import mock

from django.test import TestCase, override_settings

from apps.common.models.upload import Upload
from apps.integration.aws.shortcuts import (
    complete_upload,
    delete_upload,
    get_direct_upload_form_data,
    get_upload_file_url,
)


@override_settings(
    AWS_ACCESS_KEY_ID="test_key",
    AWS_SECRET_ACCESS_KEY="test_secret",
    AWS_S3_BUCKET_NAME="test-bucket",
    AWS_REGION="us-east-1",
)
class AWSShortcutsTestCase(TestCase):
    """Test cases for AWS S3 shortcuts."""

    def setUp(self):
        """Set up test data."""
        # Create test uploads
        self.pending_upload = Upload.objects.create(
            s3_bucket="test-bucket",
            s3_key="uploads/pending-file.txt",
            content_type="text/plain",
            status=Upload.STATUS_PENDING,
        )

        self.complete_upload = Upload.objects.create(
            s3_bucket="test-bucket",
            s3_key="uploads/complete-file.txt",
            content_type="text/plain",
            size=1024,
            status=Upload.STATUS_COMPLETE,
        )

    @mock.patch("apps.integration.aws.shortcuts.S3Client")
    @mock.patch("apps.integration.aws.shortcuts.generate_unique_filename")
    def test_get_direct_upload_form_data(
        self, mock_generate_filename, mock_s3_client_cls
    ):
        """Test generating form data for direct uploads."""
        # Mock S3Client
        mock_client = mock.MagicMock()
        mock_client.bucket_name = "test-bucket"
        mock_client.generate_presigned_post.return_value = {
            "success": True,
            "post_url": "https://test-bucket.s3.amazonaws.com",
            "form_fields": {"key": "uploads/test-uuid.jpg"},
            "file_url": "https://test-bucket.s3.amazonaws.com/uploads/test-uuid.jpg",
            "expires_in": 3600,
        }
        mock_s3_client_cls.return_value = mock_client

        # Mock generate_unique_filename
        mock_generate_filename.return_value = "uploads/test-uuid.jpg"

        # Test generating form data
        result = get_direct_upload_form_data(
            original_filename="test.jpg",
            content_type="image/jpeg",
            max_file_size=5 * 1024 * 1024,
        )

        # Verify result
        self.assertTrue(result["success"])
        self.assertEqual(result["form_url"], "https://test-bucket.s3.amazonaws.com")
        self.assertEqual(result["form_fields"]["key"], "uploads/test-uuid.jpg")
        self.assertIn("upload_id", result)

        # Verify that an Upload instance was created
        upload_id = result["upload_id"]
        upload = Upload.objects.get(id=upload_id)

        self.assertEqual(upload.s3_bucket, "test-bucket")
        self.assertEqual(upload.s3_key, "uploads/test-uuid.jpg")
        self.assertEqual(upload.content_type, "image/jpeg")
        self.assertEqual(upload.name, "test.jpg")
        self.assertEqual(upload.status, Upload.STATUS_PENDING)

        # Test with error
        mock_client.generate_presigned_post.return_value = {
            "success": False,
            "error": "Test error",
        }

        result = get_direct_upload_form_data(original_filename="test.jpg")

        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "Test error")

    @mock.patch("apps.integration.aws.shortcuts.S3Client")
    def test_complete_upload(self, mock_s3_client_cls):
        """Test completing an upload."""
        # Mock S3Client
        mock_client = mock.MagicMock()
        mock_client.get_object_metadata.return_value = {
            "success": True,
            "metadata": {
                "content_type": "text/plain",
                "content_length": 2048,
                "last_modified": "2023-01-01T12:00:00Z",
                "e_tag": '"abcdef123456"',
                "metadata": {"custom": "value"},
            },
        }
        mock_s3_client_cls.return_value = mock_client

        # Test completing a pending upload
        result = complete_upload(
            upload_id=self.pending_upload.id,
            file_size=2048,
            status=Upload.STATUS_COMPLETE,
        )

        # Verify result
        self.assertTrue(result["success"])
        self.assertEqual(result["upload"]["id"], self.pending_upload.id)
        self.assertEqual(result["upload"]["status"], Upload.STATUS_COMPLETE)
        self.assertEqual(result["upload"]["size"], 2048)

        # Verify that the Upload instance was updated
        self.pending_upload.refresh_from_db()
        self.assertEqual(self.pending_upload.status, Upload.STATUS_COMPLETE)
        self.assertEqual(self.pending_upload.size, 2048)
        self.assertIn("last_modified", self.pending_upload.meta_data)
        self.assertIn("e_tag", self.pending_upload.meta_data)

        # Test with error status
        result = complete_upload(
            upload_id=self.complete_upload.id,
            status=Upload.STATUS_ERROR,
            error="Test error",
        )

        # Verify result
        self.assertTrue(result["success"])
        self.assertEqual(result["upload"]["status"], Upload.STATUS_ERROR)

        # Verify that the Upload instance was updated
        self.complete_upload.refresh_from_db()
        self.assertEqual(self.complete_upload.status, Upload.STATUS_ERROR)
        self.assertEqual(self.complete_upload.error, "Test error")

        # Test with non-existent upload ID
        result = complete_upload(upload_id=999999, status=Upload.STATUS_COMPLETE)

        self.assertFalse(result["success"])
        self.assertIn("not found", result["error"])

        # Test with S3 client error
        mock_client.get_object_metadata.side_effect = Exception("Test error")

        # Should still succeed but with warning in logs
        result = complete_upload(
            upload_id=self.pending_upload.id, status=Upload.STATUS_COMPLETE
        )

        self.assertTrue(result["success"])

    @mock.patch("apps.common.models.upload.Upload.get_presigned_url")
    def test_get_upload_file_url(self, mock_get_presigned_url):
        """Test getting a URL for an uploaded file."""
        # Mock the get_presigned_url method on the Upload model
        mock_get_presigned_url.return_value = "https://presigned-url/test"
        # Test getting a URL without pre-signing
        result = get_upload_file_url(upload_id=self.complete_upload.id, presigned=False)

        # Verify result
        self.assertTrue(result["success"])
        self.assertEqual(
            result["url"],
            "https://test-bucket.s3.amazonaws.com/uploads/complete-file.txt",
        )
        self.assertFalse(result["presigned"])
        self.assertEqual(result["content_type"], "text/plain")
        self.assertEqual(result["size"], 1024)

        # Test getting a pre-signed URL
        result = get_upload_file_url(upload_id=self.complete_upload.id, presigned=True)

        # Verify result
        self.assertTrue(result["success"])
        self.assertEqual(result["url"], "https://presigned-url/test")
        self.assertTrue(result["presigned"])

        # Test with non-existent upload ID
        result = get_upload_file_url(upload_id=999999)

        self.assertFalse(result["success"])
        self.assertIn("not found", result["error"])

        # Test with S3 client error - simulate an exception during presigned URL generation
        mock_get_presigned_url.side_effect = Exception("Test error")

        # Test with error in get_presigned_url
        # Test error handling in get_presigned_url
        result = get_upload_file_url(upload_id=self.complete_upload.id, presigned=True)

        # Should return an error response, not fallback to regular URL
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "Test error")

    def test_delete_upload(self):
        """Test deleting an uploaded file."""
        # Mock the delete_from_s3 method
        with mock.patch.object(Upload, "delete_from_s3") as mock_delete_from_s3:
            mock_delete_from_s3.return_value = True

            # Get initial count
            initial_count = Upload.objects.count()

            # Test deleting an upload
            result = delete_upload(upload_id=self.complete_upload.id)

            # Verify result
            self.assertTrue(result["success"])
            self.assertTrue(result["s3_deleted"])

            # Verify that the Upload instance was deleted
            self.assertEqual(Upload.objects.count(), initial_count - 1)

            # Test with non-existent upload ID
            result = delete_upload(upload_id=999999)

            self.assertFalse(result["success"])
            self.assertIn("not found", result["error"])

            # Test with S3 delete error
            mock_delete_from_s3.side_effect = Exception("Test error")

            # Should still delete the database record
            result = delete_upload(upload_id=self.pending_upload.id)

            self.assertFalse(result["success"])
            self.assertEqual(
                Upload.objects.filter(id=self.pending_upload.id).count(), 1
            )
