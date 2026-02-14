import uuid
from typing import List, Optional

from supabase import Client, create_client

from django.conf import settings


class SupabaseStorageManager:
    def __init__(self, bucket_name: Optional[str] = None):
        self.bucket_name = bucket_name or settings.SUPABASE_BUCKET_NAME
        self.client: Client = create_client(
            settings.SUPABASE_PROJECT_URL, settings.SUPABASE_SERVICE_ROLE_KEY
        )
        self.storage_client = self.client.storage.from_(self.bucket_name)

    def bucket_exists(self) -> bool:
        try:
            buckets = self.client.storage.list_buckets()
            return any(bucket["name"] == self.bucket_name for bucket in buckets)
        except Exception:
            return False

    def upload(
        self,
        file_content: bytes,
        path_prefixes: List[str] | None = None,
        custom_filename: str | None = None,
        file_type: str = "application/pdf",
    ) -> str:
        """
        Upload a file to cloud storage.

        :param file_content: File content as bytes to upload.
        :param path_prefixes: List of path prefixes to prepend to the filename, or None.
        :param custom_filename: Custom filename to use instead of generating a UUID, or None.
        :param file_type: MIME type of the file being uploaded.
        :return: Public URL of the uploaded file.
        :raises ValueError: If file upload fails.
        """
        path_prefixes = path_prefixes or []

        # Determine default extension based on file type
        extension = ".pdf"
        if file_type == "application/zip":
            extension = ".zip"
        elif file_type.startswith("audio/"):
            if file_type == "audio/wav":
                extension = ".wav"
            elif file_type == "audio/mpeg":
                extension = ".mp3"
        elif file_type.startswith("image/"):
            if file_type == "image/png":
                extension = ".png"
            elif file_type == "image/jpeg":
                extension = ".jpg"
            elif file_type == "image/webp":
                extension = ".webp"

        # Always include a UUID in the filename to avoid duplicates
        if custom_filename:
            name_parts = custom_filename.rsplit(".", 1)
            base_name = name_parts[0]
            filename = f"{base_name}_{str(uuid.uuid4())}{extension}"
        else:
            filename = f"{str(uuid.uuid4())}{extension}"

        file_path = "/".join(path_prefixes + [filename])

        try:
            self.storage_client.upload(
                file_path, file=file_content, file_options={"content-type": file_type}
            )
            return self.get_public_url(file_path)
        except Exception as e:
            raise ValueError(f"Error uploading file: {str(e)}")

    def get_public_url(self, file_path: str) -> str:
        """
        Get the public URL for a file in the storage.

        :param file_path: Path of the file in the storage.
        :return: Public URL of the file.
        """
        try:
            public_url = self.storage_client.get_public_url(file_path)
            return public_url
        except Exception as e:
            raise ValueError(f"Error getting public URL: {str(e)}")

    def download(self, file_path: str) -> bytes:
        """
        Download a file from cloud storage.

        :param file_path: Path of the file in the storage.
        :return: File content as bytes.
        """
        try:
            return self.storage_client.download(file_path)
        except Exception as e:
            raise ValueError(f"Error downloading file: {str(e)}")

    def delete(self, file_path: str) -> bool:
        """
        Delete a file from cloud storage.

        :param file_path: Path of the file to delete.
        :return: True if deletion was successful, False otherwise.
        """
        try:
            self.storage_client.remove([file_path])
            return True
        except Exception:
            return False

    def list_files(self, path_prefix: str = "", limit: int = 100) -> list:
        """
        List files in a path.

        :param path_prefix: Path prefix to list.
        :param limit: Maximum files to return.
        :return: List of file metadata.
        """
        try:
            files = self.storage_client.list(
                path_prefix, options={"limit": limit}
            )
            return files or []
        except Exception:
            return []
