"""
AWS S3 integration for file uploads and storage.

This module provides functions for uploading, downloading, and managing files in S3.
It also provides utilities for generating pre-signed URLs for direct browser uploads.
"""

import logging
import mimetypes
import os
import uuid
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import boto3
from botocore.exceptions import ClientError
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


class S3Client:
    """Client for AWS S3 operations."""

    def __init__(
        self,
        aws_access_key_id: str | None = None,
        aws_secret_access_key: str | None = None,
        aws_s3_bucket_name: str | None = None,
        region_name: str | None = None,
    ):
        """
        Initialize S3 client with credentials.

        Args:
            aws_access_key_id: AWS access key ID (defaults to settings.AWS_ACCESS_KEY_ID)
            aws_secret_access_key: AWS secret access key (defaults to settings.AWS_SECRET_ACCESS_KEY)
            aws_s3_bucket_name: S3 bucket name (defaults to settings.AWS_S3_BUCKET_NAME)
            region_name: AWS region name (defaults to settings.AWS_REGION)
        """
        self.aws_access_key_id = aws_access_key_id or getattr(
            settings, "AWS_ACCESS_KEY_ID", ""
        )
        self.aws_secret_access_key = aws_secret_access_key or getattr(
            settings, "AWS_SECRET_ACCESS_KEY", ""
        )
        self.bucket_name = aws_s3_bucket_name or getattr(
            settings, "AWS_S3_BUCKET_NAME", ""
        )
        self.region_name = region_name or getattr(settings, "AWS_REGION", "us-east-1")
        self.enabled = bool(
            self.aws_access_key_id and self.aws_secret_access_key and self.bucket_name
        )

        # Initialize S3 client if credentials are available
        self.s3_client = None
        self.s3_resource = None
        if self.enabled:
            try:
                self.s3_client = boto3.client(
                    "s3",
                    aws_access_key_id=self.aws_access_key_id,
                    aws_secret_access_key=self.aws_secret_access_key,
                    region_name=self.region_name,
                )

                self.s3_resource = boto3.resource(
                    "s3",
                    aws_access_key_id=self.aws_access_key_id,
                    aws_secret_access_key=self.aws_secret_access_key,
                    region_name=self.region_name,
                )

                self.bucket = self.s3_resource.Bucket(self.bucket_name)
            except Exception as e:
                logger.error(f"Failed to initialize S3 client: {str(e)}")
                self.enabled = False

    def _validate_client(self) -> bool:
        """
        Check if the S3 client is properly configured.

        Returns:
            bool: True if the client is configured, False otherwise
        """
        if not self.enabled or not self.s3_client:
            logger.error("S3 client is not properly configured")
            return False
        return True

    def upload_file(
        self,
        file_path: str,
        object_key: str | None = None,
        public: bool = True,
        extra_args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Upload a file to S3.

        Args:
            file_path: Path to the file to upload
            object_key: S3 object key to use (defaults to the file name)
            public: Whether the file should be publicly accessible
            extra_args: Additional arguments to pass to boto3's upload_file

        Returns:
            dict: Response data including success flag and URL
        """
        if not self._validate_client():
            return {"success": False, "error": "S3 client not configured"}

        if not os.path.exists(file_path):
            return {"success": False, "error": f"File not found: {file_path}"}

        try:
            # Default to file name if object_key not provided
            if not object_key:
                object_key = os.path.basename(file_path)

            # Prepare extra arguments
            if extra_args is None:
                extra_args = {}

            # Set content type based on file extension if not specified
            if "ContentType" not in extra_args:
                content_type, _ = mimetypes.guess_type(file_path)
                if content_type:
                    extra_args["ContentType"] = content_type

            # Set ACL if public
            if public and "ACL" not in extra_args:
                extra_args["ACL"] = "public-read"

            # Upload file
            self.s3_client.upload_file(
                Filename=file_path,
                Bucket=self.bucket_name,
                Key=object_key,
                ExtraArgs=extra_args,
            )

            # Generate the URL
            url = f"https://{self.bucket_name}.s3.amazonaws.com/{object_key}"

            return {
                "success": True,
                "url": url,
                "bucket": self.bucket_name,
                "key": object_key,
            }

        except Exception as e:
            logger.error(f"Failed to upload file to S3: {str(e)}")
            return {"success": False, "error": str(e)}

    def upload_fileobj(
        self,
        file_obj,
        object_key: str,
        public: bool = True,
        extra_args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Upload a file-like object to S3.

        Args:
            file_obj: File-like object to upload
            object_key: S3 object key to use
            public: Whether the file should be publicly accessible
            extra_args: Additional arguments to pass to boto3's upload_fileobj

        Returns:
            dict: Response data including success flag and URL
        """
        if not self._validate_client():
            return {"success": False, "error": "S3 client not configured"}

        try:
            # Prepare extra arguments
            if extra_args is None:
                extra_args = {}

            # Set content type based on file extension if not specified
            if "ContentType" not in extra_args:
                content_type, _ = mimetypes.guess_type(object_key)
                if content_type:
                    extra_args["ContentType"] = content_type

            # Set ACL if public
            if public and "ACL" not in extra_args:
                extra_args["ACL"] = "public-read"

            # Upload file object
            self.s3_client.upload_fileobj(
                Fileobj=file_obj,
                Bucket=self.bucket_name,
                Key=object_key,
                ExtraArgs=extra_args,
            )

            # Generate the URL
            url = f"https://{self.bucket_name}.s3.amazonaws.com/{object_key}"

            return {
                "success": True,
                "url": url,
                "bucket": self.bucket_name,
                "key": object_key,
            }

        except Exception as e:
            logger.error(f"Failed to upload file object to S3: {str(e)}")
            return {"success": False, "error": str(e)}

    def download_file(self, object_key: str, destination: str) -> dict[str, Any]:
        """
        Download a file from S3.

        Args:
            object_key: S3 object key to download
            destination: Local file path to save the file

        Returns:
            dict: Response data including success flag
        """
        if not self._validate_client():
            return {"success": False, "error": "S3 client not configured"}

        try:
            # Ensure the directory exists
            os.makedirs(os.path.dirname(os.path.abspath(destination)), exist_ok=True)

            # Download the file
            self.s3_client.download_file(
                Bucket=self.bucket_name, Key=object_key, Filename=destination
            )

            return {"success": True, "destination": destination, "key": object_key}

        except Exception as e:
            logger.error(f"Failed to download file from S3: {str(e)}")
            return {"success": False, "error": str(e)}

    def get_object_metadata(self, object_key: str) -> dict[str, Any]:
        """
        Get metadata for an S3 object.

        Args:
            object_key: S3 object key

        Returns:
            dict: Object metadata
        """
        if not self._validate_client():
            return {"success": False, "error": "S3 client not configured"}

        try:
            # Get object metadata
            response = self.s3_client.head_object(
                Bucket=self.bucket_name, Key=object_key
            )

            metadata = {
                "content_type": response.get("ContentType"),
                "content_length": response.get("ContentLength"),
                "last_modified": response.get("LastModified"),
                "e_tag": response.get("ETag"),
                "metadata": response.get("Metadata", {}),
            }

            return {"success": True, "metadata": metadata}

        except ClientError as e:
            if e.response["Error"]["Code"] == "404":
                return {"success": False, "error": "Object not found"}
            logger.error(f"Failed to get object metadata: {str(e)}")
            return {"success": False, "error": str(e)}

    def delete_object(self, object_key: str) -> dict[str, Any]:
        """
        Delete an object from S3.

        Args:
            object_key: S3 object key to delete

        Returns:
            dict: Response data including success flag
        """
        if not self._validate_client():
            return {"success": False, "error": "S3 client not configured"}

        try:
            # Delete the object
            self.s3_client.delete_object(Bucket=self.bucket_name, Key=object_key)

            return {"success": True, "key": object_key}

        except Exception as e:
            logger.error(f"Failed to delete object from S3: {str(e)}")
            return {"success": False, "error": str(e)}

    def list_objects(self, prefix: str = "", max_keys: int = 1000) -> dict[str, Any]:
        """
        List objects in the S3 bucket.

        Args:
            prefix: Prefix to filter objects by
            max_keys: Maximum number of keys to return

        Returns:
            dict: Response data including success flag and objects
        """
        if not self._validate_client():
            return {"success": False, "error": "S3 client not configured"}

        try:
            # List objects
            response = self.s3_client.list_objects_v2(
                Bucket=self.bucket_name, Prefix=prefix, MaxKeys=max_keys
            )

            objects = []
            if "Contents" in response:
                for obj in response["Contents"]:
                    objects.append(
                        {
                            "key": obj.get("Key"),
                            "size": obj.get("Size"),
                            "last_modified": obj.get("LastModified"),
                            "url": f"https://{self.bucket_name}.s3.amazonaws.com/{obj.get('Key')}",
                        }
                    )

            return {
                "success": True,
                "objects": objects,
                "count": len(objects),
                "is_truncated": response.get("IsTruncated", False),
            }

        except Exception as e:
            logger.error(f"Failed to list objects in S3: {str(e)}")
            return {"success": False, "error": str(e)}

    def generate_presigned_url(
        self, object_key: str, expiration: int = 3600, http_method: str = "GET"
    ) -> dict[str, Any]:
        """
        Generate a pre-signed URL for an S3 object.

        Args:
            object_key: S3 object key
            expiration: URL expiration time in seconds (default: 1 hour)
            http_method: HTTP method for the URL (default: GET)

        Returns:
            dict: Response data including success flag and URL
        """
        if not self._validate_client():
            return {"success": False, "error": "S3 client not configured"}

        try:
            # Generate pre-signed URL
            url = self.s3_client.generate_presigned_url(
                ClientMethod="get_object",
                Params={"Bucket": self.bucket_name, "Key": object_key},
                ExpiresIn=expiration,
                HttpMethod=http_method,
            )

            return {
                "success": True,
                "url": url,
                "key": object_key,
                "expires_in": expiration,
            }

        except Exception as e:
            logger.error(f"Failed to generate pre-signed URL: {str(e)}")
            return {"success": False, "error": str(e)}

    def generate_presigned_post(
        self,
        object_key: str,
        expiration: int = 3600,
        conditions: list[Any] | None = None,
        fields: dict[str, Any] | None = None,
        success_action_redirect: str | None = None,
    ) -> dict[str, Any]:
        """
        Generate a pre-signed POST for direct browser upload to S3.

        Args:
            object_key: S3 object key for the uploaded file
            expiration: URL expiration time in seconds (default: 1 hour)
            conditions: Conditions for the upload (optional)
            fields: Fields to include in the form (optional)
            success_action_redirect: URL to redirect to after successful upload (optional)

        Returns:
            dict: Response data including success flag, URL, fields, and form data
        """
        if not self._validate_client():
            return {"success": False, "error": "S3 client not configured"}

        try:
            # Prepare fields
            if fields is None:
                fields = {}

            if success_action_redirect:
                fields["success_action_redirect"] = success_action_redirect
            else:
                # Return JSON when no redirect URL is provided
                fields["success_action_status"] = "201"

            # Set content type based on file extension if not specified
            if "Content-Type" not in fields:
                content_type, _ = mimetypes.guess_type(object_key)
                if content_type:
                    fields["Content-Type"] = content_type

            # Allow public read by default
            if "acl" not in fields:
                fields["acl"] = "public-read"

            # Prepare conditions
            if conditions is None:
                conditions = []

            # Generate pre-signed POST
            response = self.s3_client.generate_presigned_post(
                Bucket=self.bucket_name,
                Key=object_key,
                Fields=fields,
                Conditions=conditions,
                ExpiresIn=expiration,
            )

            # Calculate file URL
            file_url = f"https://{self.bucket_name}.s3.amazonaws.com/{object_key}"

            # Extract and format form fields for easy use
            form_fields = response.get("fields", {})
            form_url = response.get("url")

            return {
                "success": True,
                "post_url": form_url,
                "form_fields": form_fields,
                "file_url": file_url,
                "key": object_key,
                "expires_in": expiration,
            }

        except Exception as e:
            logger.error(f"Failed to generate pre-signed POST: {str(e)}")
            return {"success": False, "error": str(e)}

    def get_public_url(self, object_key: str) -> str:
        """
        Get the public URL for an S3 object.

        Args:
            object_key: S3 object key

        Returns:
            str: Public URL for the object
        """
        return f"https://{self.bucket_name}.s3.amazonaws.com/{object_key}"

    def parse_s3_url(self, url: str) -> tuple[str, str]:
        """
        Parse an S3 URL into bucket name and object key.

        Args:
            url: S3 URL

        Returns:
            tuple: (bucket_name, object_key)
        """
        parsed_url = urlparse(url)

        if parsed_url.netloc.endswith("s3.amazonaws.com"):
            # URL format: https://bucket-name.s3.amazonaws.com/key
            bucket_name = parsed_url.netloc.replace(".s3.amazonaws.com", "")
            object_key = parsed_url.path.lstrip("/")
        elif parsed_url.netloc == "s3.amazonaws.com":
            # URL format: https://s3.amazonaws.com/bucket-name/key
            path_parts = parsed_url.path.strip("/").split("/", 1)
            bucket_name = path_parts[0] if path_parts else ""
            object_key = path_parts[1] if len(path_parts) > 1 else ""
        else:
            # Not an S3 URL
            bucket_name = ""
            object_key = ""

        return bucket_name, object_key


def generate_unique_filename(
    original_filename: str, prefix: str = "", include_timestamp: bool = True
) -> str:
    """
    Generate a unique filename for uploading to S3.

    Args:
        original_filename: Original filename
        prefix: Prefix to add to the filename
        include_timestamp: Whether to include a timestamp in the filename

    Returns:
        str: Unique filename
    """
    # Get file extension
    _, ext = os.path.splitext(original_filename)

    # Generate UUID
    file_uuid = str(uuid.uuid4())

    # Add timestamp if requested
    timestamp = ""
    if include_timestamp:
        timestamp = f"_{int(timezone.now().timestamp())}"

    # Build and return filename
    if prefix:
        return f"{prefix}/{file_uuid}{timestamp}{ext}"
    else:
        return f"{file_uuid}{timestamp}{ext}"


def get_file_upload_presigned_post(
    original_filename: str,
    prefix: str = "uploads",
    max_file_size: int = 10485760,  # 10MB
) -> dict[str, Any]:
    """
    Generate a pre-signed POST for uploading a file directly to S3.

    Args:
        original_filename: Original filename
        prefix: Prefix to add to the filename
        max_file_size: Maximum file size in bytes

    Returns:
        dict: Pre-signed POST data
    """
    # Generate a unique filename
    object_key = generate_unique_filename(original_filename, prefix=prefix)

    # Get content type
    content_type, _ = mimetypes.guess_type(original_filename)

    # Create conditions for the upload
    conditions = [
        # Content length restrictions
        ["content-length-range", 1, max_file_size]
    ]

    # Create fields
    fields = {}
    if content_type:
        fields["Content-Type"] = content_type

    # Generate the pre-signed POST
    client = S3Client()
    return client.generate_presigned_post(
        object_key=object_key, conditions=conditions, fields=fields
    )
