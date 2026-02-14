"""Mock classes for testing Supabase integration."""

import uuid
from unittest.mock import MagicMock


class MockSupabaseStorageManager:
    """Mock implementation of SupabaseStorageManager for testing."""

    def __init__(self):
        """Initialize with default behaviors."""
        self.mock = MagicMock()
        self.uploaded_files = {}

    def upload(
        self,
        file_content=None,
        path_prefixes=None,
        custom_filename=None,
        file_type=None,
    ):
        """Mock the upload method."""
        file_id = str(uuid.uuid4())
        path = "/".join(path_prefixes) if path_prefixes else ""
        filename = custom_filename or f"file-{file_id}"
        url = f"https://mock-supabase-storage.com/{path}/{filename}"

        self.uploaded_files[file_id] = {
            "path": path,
            "filename": filename,
            "content_type": file_type,
            "url": url,
        }

        self.mock.upload(
            file_content=file_content,
            path_prefixes=path_prefixes,
            custom_filename=custom_filename,
            file_type=file_type,
        )

        return url

    def download(self, file_path):
        """Mock the download method."""
        self.mock.download(file_path=file_path)
        return b"mock file content"

    def delete(self, file_path):
        """Mock the delete method."""
        self.mock.delete(file_path=file_path)
        for file_id, info in list(self.uploaded_files.items()):
            if info["url"].endswith(file_path):
                del self.uploaded_files[file_id]
                return True
        return False


def mock_supabase_storage(func):
    """
    Decorator to patch SupabaseStorageManager in tests.

    Usage:
    @mock_supabase_storage
    def test_something(self, mock_storage_manager):
        # mock_storage_manager is an instance of MockSupabaseStorageManager
        ...
    """
    from unittest.mock import patch

    return patch(
        "apps.integration.supabase.storage_manager.SupabaseStorageManager",
        return_value=MockSupabaseStorageManager(),
    )(func)
