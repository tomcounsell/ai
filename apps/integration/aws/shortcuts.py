"""
Shortcuts for AWS S3 integration.

This module provides easy-to-use functions for common S3 operations like generating
pre-signed URLs for direct browser uploads, creating Upload model instances, and
retrieving uploaded files.
"""

import logging
import mimetypes
import os
from typing import Any, Dict, Optional

from apps.common.models.upload import Upload
from apps.integration.aws.s3 import S3Client, generate_unique_filename

logger = logging.getLogger(__name__)


def get_direct_upload_form_data(
    original_filename: str,
    content_type: str | None = None,
    max_file_size: int = 10485760,  # 10MB
    prefix: str = "uploads",
    success_redirect_url: str | None = None,
    metadata: dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    Generate form data for direct browser upload to S3.

    Args:
        original_filename: Original filename
        content_type: Content type (MIME type) of the file
        max_file_size: Maximum file size in bytes (default: 10MB)
        prefix: Prefix to add to the filename in S3
        success_redirect_url: URL to redirect to after successful upload
        metadata: Additional metadata to store with the file

    Returns:
        Dict with form data for direct upload and Upload model instance
    """
    # Generate a unique filename
    object_key = generate_unique_filename(
        original_filename=original_filename, prefix=prefix
    )

    # Determine content type if not provided
    if not content_type:
        content_type, _ = mimetypes.guess_type(original_filename)

    # Create conditions for upload
    conditions = [
        # Content length restrictions
        ["content-length-range", 1, max_file_size]
    ]

    # Create fields
    fields = {}
    if content_type:
        fields["Content-Type"] = content_type

    # Add metadata if provided
    if metadata:
        for key, value in metadata.items():
            fields[f"x-amz-meta-{key}"] = value

    # Generate the pre-signed POST
    client = S3Client()
    result = client.generate_presigned_post(
        object_key=object_key,
        conditions=conditions,
        fields=fields,
        success_action_redirect=success_redirect_url,
    )

    if not result.get("success"):
        return {
            "success": False,
            "error": result.get("error", "Failed to generate pre-signed POST"),
        }

    # Create an Upload model instance
    upload = Upload.objects.create(
        s3_bucket=client.bucket_name,
        s3_key=object_key,
        content_type=content_type,
        name=os.path.basename(original_filename),
        meta_data={"original_filename": original_filename},
        status=Upload.STATUS_PENDING,
    )

    # Add the Upload instance to the result
    return {
        "success": True,
        "form_url": result["post_url"],
        "form_fields": result["form_fields"],
        "file_url": result["file_url"],
        "upload_id": upload.id,
        "expires_in": result["expires_in"],
    }


def complete_upload(
    upload_id: int,
    file_size: int | None = None,
    status: str = Upload.STATUS_COMPLETE,
    error: str | None = None,
) -> dict[str, Any]:
    """
    Complete an Upload by updating its status and metadata.

    Args:
        upload_id: ID of the Upload model instance
        file_size: Size of the uploaded file in bytes
        status: New status for the Upload
        error: Error message if upload failed

    Returns:
        Dict with updated Upload information
    """
    try:
        # Get the Upload instance
        upload = Upload.objects.get(id=upload_id)

        # Update fields
        upload.status = status
        if file_size is not None:
            upload.size = file_size
        if error:
            upload.error = error

        # Update metadata if status is complete
        if status == Upload.STATUS_COMPLETE and upload.s3_bucket and upload.s3_key:
            try:
                # Get metadata from S3
                client = S3Client(aws_s3_bucket_name=upload.s3_bucket)
                result = client.get_object_metadata(upload.s3_key)

                if result.get("success"):
                    metadata = result["metadata"]

                    # Update content type if not set
                    if metadata.get("content_type") and not upload.content_type:
                        upload.content_type = metadata["content_type"]

                    # Update size if not set
                    if metadata.get("content_length") and not upload.size:
                        upload.size = metadata["content_length"]

                    # Update metadata
                    if not upload.meta_data:
                        upload.meta_data = {}

                    upload.meta_data.update(
                        {
                            "last_modified": str(metadata.get("last_modified", "")),
                            "e_tag": metadata.get("e_tag", ""),
                            "s3_metadata": metadata.get("metadata", {}),
                        }
                    )

                    logger.info(f"Updated metadata for upload {upload_id} from S3")
            except Exception as e:
                logger.error(
                    f"Error updating metadata for upload {upload_id}: {str(e)}"
                )

        # Save changes
        upload.save()

        return {
            "success": True,
            "upload": {
                "id": upload.id,
                "url": upload.original or upload.s3_url,
                "name": upload.name,
                "content_type": upload.content_type,
                "size": upload.size,
                "status": upload.status,
            },
        }

    except Upload.DoesNotExist:
        return {"success": False, "error": f"Upload with ID {upload_id} not found"}
    except Exception as e:
        logger.error(f"Error completing upload {upload_id}: {str(e)}")
        return {"success": False, "error": str(e)}


def get_upload_file_url(
    upload_id: int, presigned: bool = False, expiration: int = 3600
) -> dict[str, Any]:
    """
    Get the URL for an uploaded file.

    Args:
        upload_id: ID of the Upload model instance
        presigned: Whether to generate a pre-signed URL
        expiration: Expiration time for pre-signed URL in seconds

    Returns:
        Dict with file URL information
    """
    try:
        # Get the Upload instance
        upload = Upload.objects.get(id=upload_id)

        # Get URL based on whether presigned is requested
        url = (
            upload.get_presigned_url(expiration=expiration)
            if presigned
            else upload.s3_url
        )

        return {
            "success": True,
            "url": url,
            "presigned": presigned,
            "content_type": upload.content_type,
            "name": upload.name,
            "size": upload.size,
        }

    except Upload.DoesNotExist:
        return {"success": False, "error": f"Upload with ID {upload_id} not found"}
    except Exception as e:
        logger.error(f"Error getting URL for upload {upload_id}: {str(e)}")
        return {"success": False, "error": str(e)}


def delete_upload(upload_id: int) -> dict[str, Any]:
    """
    Delete an uploaded file.

    Args:
        upload_id: ID of the Upload model instance

    Returns:
        Dict with deletion result
    """
    try:
        # Get the Upload instance
        upload = Upload.objects.get(id=upload_id)

        # Delete from S3 and database
        s3_deleted = (
            upload.delete_from_s3() if upload.s3_bucket and upload.s3_key else False
        )
        upload.delete()

        return {"success": True, "s3_deleted": s3_deleted}

    except Upload.DoesNotExist:
        return {"success": False, "error": f"Upload with ID {upload_id} not found"}
    except Exception as e:
        logger.error(f"Error deleting upload {upload_id}: {str(e)}")
        return {"success": False, "error": str(e)}
