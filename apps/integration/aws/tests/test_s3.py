"""
Unit tests for the AWS S3 integration.
"""

import os
import tempfile
from datetime import datetime
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError
from django.test import TestCase, override_settings

from apps.integration.aws.s3 import (
    S3Client,
    generate_unique_filename,
    get_file_upload_presigned_post,
)


@override_settings(
    AWS_ACCESS_KEY_ID="test_key",
    AWS_SECRET_ACCESS_KEY="test_secret",
    AWS_S3_BUCKET_NAME="test-bucket",
    AWS_REGION="us-east-1",
)
class S3ClientTestCase(TestCase):
    """Test cases for the S3 client."""

    def setUp(self):
        """Set up test data."""
        # Patch boto3 for testing
        self.boto3_client_patcher = patch("apps.integration.aws.s3.boto3.client")
        self.mock_boto3_client = self.boto3_client_patcher.start()

        self.boto3_resource_patcher = patch("apps.integration.aws.s3.boto3.resource")
        self.mock_boto3_resource = self.boto3_resource_patcher.start()

        # Mock S3 clients
        self.mock_s3_client = MagicMock()
        self.mock_boto3_client.return_value = self.mock_s3_client

        self.mock_s3_resource = MagicMock()
        self.mock_bucket = MagicMock()
        self.mock_s3_resource.Bucket.return_value = self.mock_bucket
        self.mock_boto3_resource.return_value = self.mock_s3_resource

        # Create S3 client
        self.client = S3Client()

    def tearDown(self):
        """Clean up after tests."""
        self.boto3_client_patcher.stop()
        self.boto3_resource_patcher.stop()

    def test_init_with_credentials(self):
        """Test initialization with credentials."""
        # Test with default credentials from settings
        client = S3Client()
        self.assertTrue(client.enabled)
        self.assertEqual(client.aws_access_key_id, "test_key")
        self.assertEqual(client.aws_secret_access_key, "test_secret")
        self.assertEqual(client.bucket_name, "test-bucket")
        self.assertEqual(client.region_name, "us-east-1")

        # Test with custom credentials
        client = S3Client(
            aws_access_key_id="custom_key",
            aws_secret_access_key="custom_secret",
            aws_s3_bucket_name="custom-bucket",
            region_name="eu-west-1",
        )
        self.assertTrue(client.enabled)
        self.assertEqual(client.aws_access_key_id, "custom_key")
        self.assertEqual(client.aws_secret_access_key, "custom_secret")
        self.assertEqual(client.bucket_name, "custom-bucket")
        self.assertEqual(client.region_name, "eu-west-1")

    def test_init_with_missing_credentials(self):
        """Test initialization with missing credentials."""
        with patch("apps.integration.aws.s3.getattr") as mock_getattr:
            # Simulate missing credentials
            mock_getattr.return_value = ""

            client = S3Client()
            self.assertFalse(client.enabled)

    def test_validate_client(self):
        """Test client validation."""
        # Valid client
        self.assertTrue(self.client._validate_client())

        # Invalid client
        self.client.enabled = False
        self.assertFalse(self.client._validate_client())

    def test_upload_file(self):
        """Test uploading a file."""
        # Create a temporary file
        with tempfile.NamedTemporaryFile(delete=False) as temp_file:
            temp_file.write(b"test content")
            temp_file_path = temp_file.name

        try:
            # Test successful upload
            result = self.client.upload_file(
                file_path=temp_file_path, object_key="test/file.txt"
            )

            # Check result
            self.assertTrue(result["success"])
            self.assertEqual(
                result["url"], "https://test-bucket.s3.amazonaws.com/test/file.txt"
            )
            self.assertEqual(result["bucket"], "test-bucket")
            self.assertEqual(result["key"], "test/file.txt")

            # Verify S3 client was called correctly
            self.mock_s3_client.upload_file.assert_called_once()
            call_args = self.mock_s3_client.upload_file.call_args[1]
            self.assertEqual(call_args["Filename"], temp_file_path)
            self.assertEqual(call_args["Bucket"], "test-bucket")
            self.assertEqual(call_args["Key"], "test/file.txt")
            self.assertEqual(call_args["ExtraArgs"]["ACL"], "public-read")

            # Test upload with additional arguments
            self.mock_s3_client.upload_file.reset_mock()
            result = self.client.upload_file(
                file_path=temp_file_path,
                object_key="test/file2.txt",
                public=False,
                extra_args={"Metadata": {"custom": "value"}},
            )

            # Verify extra args were passed correctly
            call_args = self.mock_s3_client.upload_file.call_args[1]
            self.assertEqual(call_args["ExtraArgs"]["Metadata"], {"custom": "value"})
            self.assertNotIn("ACL", call_args["ExtraArgs"])

            # Test upload with file not found
            result = self.client.upload_file(
                file_path="/nonexistent/file.txt", object_key="test/nonexistent.txt"
            )

            self.assertFalse(result["success"])
            self.assertIn("File not found", result["error"])

            # Test upload with client error
            self.mock_s3_client.upload_file.side_effect = Exception("Test error")

            result = self.client.upload_file(
                file_path=temp_file_path, object_key="test/error.txt"
            )

            self.assertFalse(result["success"])
            self.assertEqual(result["error"], "Test error")

        finally:
            # Clean up
            os.unlink(temp_file_path)

    def test_upload_fileobj(self):
        """Test uploading a file-like object."""
        # Create a file-like object
        file_obj = MagicMock()

        # Test successful upload
        result = self.client.upload_fileobj(
            file_obj=file_obj, object_key="test/fileobj.txt"
        )

        # Check result
        self.assertTrue(result["success"])
        self.assertEqual(
            result["url"], "https://test-bucket.s3.amazonaws.com/test/fileobj.txt"
        )

        # Verify S3 client was called correctly
        self.mock_s3_client.upload_fileobj.assert_called_once()
        call_args = self.mock_s3_client.upload_fileobj.call_args[1]
        self.assertEqual(call_args["Fileobj"], file_obj)
        self.assertEqual(call_args["Bucket"], "test-bucket")
        self.assertEqual(call_args["Key"], "test/fileobj.txt")
        self.assertEqual(call_args["ExtraArgs"]["ACL"], "public-read")

        # Test upload with error
        self.mock_s3_client.upload_fileobj.side_effect = Exception("Test error")

        result = self.client.upload_fileobj(
            file_obj=file_obj, object_key="test/error.txt"
        )

        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "Test error")

    def test_download_file(self):
        """Test downloading a file."""
        # Test successful download
        with patch("os.makedirs") as mock_makedirs:
            result = self.client.download_file(
                object_key="test/file.txt", destination="/tmp/downloaded.txt"
            )

            # Check result
            self.assertTrue(result["success"])
            self.assertEqual(result["destination"], "/tmp/downloaded.txt")
            self.assertEqual(result["key"], "test/file.txt")

            # Verify S3 client was called correctly
            self.mock_s3_client.download_file.assert_called_once_with(
                Bucket="test-bucket",
                Key="test/file.txt",
                Filename="/tmp/downloaded.txt",
            )

            # Verify that the directory was created
            mock_makedirs.assert_called_once()

            # Test download with error
            self.mock_s3_client.download_file.side_effect = Exception("Test error")

            result = self.client.download_file(
                object_key="test/error.txt", destination="/tmp/error.txt"
            )

            self.assertFalse(result["success"])
            self.assertEqual(result["error"], "Test error")

    def test_get_object_metadata(self):
        """Test getting object metadata."""
        # Mock head_object response
        mock_head_response = {
            "ContentType": "text/plain",
            "ContentLength": 100,
            "LastModified": datetime.now(),
            "ETag": '"abcdef123456"',
            "Metadata": {"custom": "value"},
        }
        self.mock_s3_client.head_object.return_value = mock_head_response

        # Test successful metadata retrieval
        result = self.client.get_object_metadata(object_key="test/file.txt")

        # Check result
        self.assertTrue(result["success"])
        self.assertEqual(result["metadata"]["content_type"], "text/plain")
        self.assertEqual(result["metadata"]["content_length"], 100)
        self.assertEqual(result["metadata"]["e_tag"], '"abcdef123456"')
        self.assertEqual(result["metadata"]["metadata"], {"custom": "value"})

        # Verify S3 client was called correctly
        self.mock_s3_client.head_object.assert_called_once_with(
            Bucket="test-bucket", Key="test/file.txt"
        )

        # Test metadata retrieval for non-existent object
        self.mock_s3_client.head_object.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject"
        )

        result = self.client.get_object_metadata(object_key="test/nonexistent.txt")

        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "Object not found")

    def test_delete_object(self):
        """Test deleting an object."""
        # Test successful deletion
        result = self.client.delete_object(object_key="test/file.txt")

        # Check result
        self.assertTrue(result["success"])
        self.assertEqual(result["key"], "test/file.txt")

        # Verify S3 client was called correctly
        self.mock_s3_client.delete_object.assert_called_once_with(
            Bucket="test-bucket", Key="test/file.txt"
        )

        # Test deletion with error
        self.mock_s3_client.delete_object.side_effect = Exception("Test error")

        result = self.client.delete_object(object_key="test/error.txt")

        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "Test error")

    def test_list_objects(self):
        """Test listing objects."""
        # Mock list_objects_v2 response
        mock_list_response = {
            "Contents": [
                {"Key": "test/file1.txt", "Size": 100, "LastModified": datetime.now()},
                {"Key": "test/file2.txt", "Size": 200, "LastModified": datetime.now()},
            ],
            "IsTruncated": False,
        }
        self.mock_s3_client.list_objects_v2.return_value = mock_list_response

        # Test successful listing
        result = self.client.list_objects(prefix="test/")

        # Check result
        self.assertTrue(result["success"])
        self.assertEqual(result["count"], 2)
        self.assertFalse(result["is_truncated"])
        self.assertEqual(result["objects"][0]["key"], "test/file1.txt")
        self.assertEqual(result["objects"][0]["size"], 100)
        self.assertEqual(result["objects"][1]["key"], "test/file2.txt")
        self.assertEqual(result["objects"][1]["size"], 200)

        # Verify S3 client was called correctly
        self.mock_s3_client.list_objects_v2.assert_called_once_with(
            Bucket="test-bucket", Prefix="test/", MaxKeys=1000
        )

        # Test listing with error
        self.mock_s3_client.list_objects_v2.side_effect = Exception("Test error")

        result = self.client.list_objects(prefix="test/error/")

        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "Test error")

    def test_generate_presigned_url(self):
        """Test generating a pre-signed URL."""
        # Mock generate_presigned_url response
        self.mock_s3_client.generate_presigned_url.return_value = (
            "https://test-bucket.s3.amazonaws.com/test/file.txt?AWSAccessKeyId=..."
        )

        # Test successful URL generation
        result = self.client.generate_presigned_url(object_key="test/file.txt")

        # Check result
        self.assertTrue(result["success"])
        self.assertEqual(result["key"], "test/file.txt")
        self.assertEqual(result["expires_in"], 3600)
        self.assertTrue(
            result["url"].startswith(
                "https://test-bucket.s3.amazonaws.com/test/file.txt"
            )
        )

        # Verify S3 client was called correctly
        self.mock_s3_client.generate_presigned_url.assert_called_once_with(
            ClientMethod="get_object",
            Params={"Bucket": "test-bucket", "Key": "test/file.txt"},
            ExpiresIn=3600,
            HttpMethod="GET",
        )

        # Test URL generation with custom expiration and method
        self.mock_s3_client.generate_presigned_url.reset_mock()

        result = self.client.generate_presigned_url(
            object_key="test/file.txt", expiration=7200, http_method="PUT"
        )

        self.mock_s3_client.generate_presigned_url.assert_called_once_with(
            ClientMethod="get_object",
            Params={"Bucket": "test-bucket", "Key": "test/file.txt"},
            ExpiresIn=7200,
            HttpMethod="PUT",
        )

        # Test URL generation with error
        self.mock_s3_client.generate_presigned_url.side_effect = Exception("Test error")

        result = self.client.generate_presigned_url(object_key="test/error.txt")

        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "Test error")

    def test_generate_presigned_post(self):
        """Test generating a pre-signed POST."""
        # Mock generate_presigned_post response
        mock_post_response = {
            "url": "https://test-bucket.s3.amazonaws.com",
            "fields": {
                "key": "test/file.txt",
                "AWSAccessKeyId": "test_key",
                "policy": "base64_policy",
                "signature": "signature",
            },
        }
        self.mock_s3_client.generate_presigned_post.return_value = mock_post_response

        # Test successful POST generation
        result = self.client.generate_presigned_post(object_key="test/file.txt")

        # Check result
        self.assertTrue(result["success"])
        self.assertEqual(result["key"], "test/file.txt")
        self.assertEqual(result["expires_in"], 3600)
        self.assertEqual(result["post_url"], "https://test-bucket.s3.amazonaws.com")
        self.assertEqual(result["form_fields"]["key"], "test/file.txt")
        self.assertEqual(
            result["file_url"], "https://test-bucket.s3.amazonaws.com/test/file.txt"
        )

        # Verify S3 client was called correctly
        call_args = self.mock_s3_client.generate_presigned_post.call_args[1]
        self.assertEqual(call_args["Bucket"], "test-bucket")
        self.assertEqual(call_args["Key"], "test/file.txt")
        self.assertEqual(call_args["ExpiresIn"], 3600)
        self.assertEqual(call_args["Fields"]["acl"], "public-read")

        # Test POST generation with redirect URL
        self.mock_s3_client.generate_presigned_post.reset_mock()

        result = self.client.generate_presigned_post(
            object_key="test/file.txt",
            success_action_redirect="https://example.com/success",
        )

        call_args = self.mock_s3_client.generate_presigned_post.call_args[1]
        self.assertEqual(
            call_args["Fields"]["success_action_redirect"],
            "https://example.com/success",
        )

        # Test POST generation with error
        self.mock_s3_client.generate_presigned_post.side_effect = Exception(
            "Test error"
        )

        result = self.client.generate_presigned_post(object_key="test/error.txt")

        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "Test error")

    def test_get_public_url(self):
        """Test getting a public URL."""
        url = self.client.get_public_url("test/file.txt")
        self.assertEqual(url, "https://test-bucket.s3.amazonaws.com/test/file.txt")

    def test_parse_s3_url(self):
        """Test parsing an S3 URL."""
        # Test standard format
        bucket, key = self.client.parse_s3_url(
            "https://test-bucket.s3.amazonaws.com/test/file.txt"
        )
        self.assertEqual(bucket, "test-bucket")
        self.assertEqual(key, "test/file.txt")

        # Test alternate format
        bucket, key = self.client.parse_s3_url(
            "https://s3.amazonaws.com/test-bucket/test/file.txt"
        )
        self.assertEqual(bucket, "test-bucket")
        self.assertEqual(key, "test/file.txt")

        # Test invalid URL
        bucket, key = self.client.parse_s3_url("https://example.com/test/file.txt")
        self.assertEqual(bucket, "")
        self.assertEqual(key, "")


class UtilityFunctionsTestCase(TestCase):
    """Test cases for S3 utility functions."""

    def test_generate_unique_filename(self):
        """Test generating a unique filename."""
        # Test with default options
        filename = generate_unique_filename("test.txt")
        self.assertTrue(filename.endswith(".txt"))
        self.assertTrue("_" in filename)  # Has timestamp

        # Test with prefix
        filename = generate_unique_filename("test.txt", prefix="uploads")
        self.assertTrue(filename.startswith("uploads/"))

        # Test without timestamp
        filename = generate_unique_filename("test.txt", include_timestamp=False)
        self.assertFalse("_" in filename)

    @patch("apps.integration.aws.s3.S3Client")
    def test_get_file_upload_presigned_post(self, mock_s3_client_cls):
        """Test getting a pre-signed POST for file upload."""
        # Mock S3Client
        mock_client = MagicMock()
        mock_client.generate_presigned_post.return_value = {
            "success": True,
            "post_url": "https://test-bucket.s3.amazonaws.com",
            "form_fields": {"key": "uploads/test-uuid.jpg"},
            "file_url": "https://test-bucket.s3.amazonaws.com/uploads/test-uuid.jpg",
        }
        mock_s3_client_cls.return_value = mock_client

        # Test with default options
        with patch("apps.integration.aws.s3.generate_unique_filename") as mock_generate:
            mock_generate.return_value = "uploads/test-uuid.jpg"

            result = get_file_upload_presigned_post("test.jpg")
            self.assertEqual(result["success"], True)

            # Verify S3Client was called correctly
            mock_generate.assert_called_once_with("test.jpg", prefix="uploads")

            call_args = mock_client.generate_presigned_post.call_args[1]
            self.assertEqual(call_args["object_key"], "uploads/test-uuid.jpg")
            self.assertEqual(call_args["fields"]["Content-Type"], "image/jpeg")
            self.assertIn(
                ["content-length-range", 1, 10485760], call_args["conditions"]
            )
