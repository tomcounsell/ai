"""Tests for the file storage service."""

import pytest

from apps.common.services.storage import (
    LocalFileStorage,
    check_storage_config,
    delete_file,
    get_file_content,
    get_file_url,
    reset_backend,
    store_file,
)


@pytest.fixture
def local_storage(tmp_path, settings):
    """Configure local storage backend for testing."""
    settings.MEDIA_ROOT = str(tmp_path)
    settings.MEDIA_URL = "/media/"
    settings.STORAGE_BACKEND = "local"
    # Reset singleton
    reset_backend()
    yield
    reset_backend()


class TestLocalFileStorage:
    """Tests for LocalFileStorage backend."""

    def test_store_and_retrieve(self, local_storage):
        """Test storing and retrieving a file."""
        url = store_file("test/file.txt", b"hello", "text/plain")
        assert "test/file.txt" in url
        content = get_file_content("test/file.txt")
        assert content == b"hello"

    def test_get_url(self, local_storage):
        """Test getting URL for a stored file."""
        store_file("test/file.txt", b"hello")
        url = get_file_url("test/file.txt")
        assert url == "/media/test/file.txt"

    def test_delete(self, local_storage):
        """Test deleting a file."""
        store_file("test/delete.txt", b"bye")
        delete_file("test/delete.txt")
        with pytest.raises(FileNotFoundError):
            get_file_content("test/delete.txt")

    def test_nested_paths(self, local_storage):
        """Test storing files in nested directories."""
        url = store_file("podcast/my-show/ep1/audio.mp3", b"audio data", "audio/mpeg")
        assert "podcast/my-show/ep1/audio.mp3" in url
        content = get_file_content("podcast/my-show/ep1/audio.mp3")
        assert content == b"audio data"

    def test_overwrite_file(self, local_storage):
        """Test overwriting an existing file."""
        store_file("test/overwrite.txt", b"original")
        store_file("test/overwrite.txt", b"updated")
        content = get_file_content("test/overwrite.txt")
        assert content == b"updated"


class TestCheckStorageConfig:
    """Tests for check_storage_config function."""

    def test_check_config_local(self, local_storage):
        """Test config check for local backend."""
        status = check_storage_config()
        assert status["ok"] is True
        assert status["backend"] == "local"

    def test_check_config_missing_supabase(self, settings):
        """Test config check for Supabase backend with missing keys."""
        settings.STORAGE_BACKEND = "supabase"
        settings.SUPABASE_PROJECT_URL = ""
        settings.SUPABASE_SERVICE_ROLE_KEY = ""
        settings.SUPABASE_BUCKET_NAME = ""
        status = check_storage_config()
        assert status["ok"] is False
        assert "SUPABASE_PROJECT_URL" in status["missing_keys"]

    def test_check_config_missing_s3(self, settings):
        """Test config check for S3 backend with missing keys."""
        settings.STORAGE_BACKEND = "s3"
        settings.S3_ENDPOINT_URL = ""
        settings.S3_ACCESS_KEY = ""
        settings.S3_SECRET_KEY = ""
        settings.S3_BUCKET = ""
        settings.S3_PUBLIC_URL = ""
        status = check_storage_config()
        assert status["ok"] is False
        assert "S3_BUCKET" in status["missing_keys"]


class TestFallbackBehavior:
    """Tests for fallback behavior when config is missing."""

    def test_fallback_on_missing_supabase_config(self, tmp_path, settings):
        """When supabase keys are missing, falls back to local storage."""
        settings.STORAGE_BACKEND = "supabase"
        settings.SUPABASE_PROJECT_URL = ""
        settings.SUPABASE_SERVICE_ROLE_KEY = ""
        settings.SUPABASE_BUCKET_NAME = ""
        settings.MEDIA_ROOT = str(tmp_path)
        settings.MEDIA_URL = "/media/"
        reset_backend()
        url = store_file("fallback/test.txt", b"works")
        assert "/media/" in url
        reset_backend()

    def test_fallback_on_unknown_backend(self, tmp_path, settings):
        """When an unknown backend is configured, falls back to local."""
        settings.STORAGE_BACKEND = "unknown_backend"
        settings.MEDIA_ROOT = str(tmp_path)
        settings.MEDIA_URL = "/media/"
        reset_backend()
        url = store_file("unknown/test.txt", b"works")
        assert "/media/" in url
        reset_backend()


class TestLocalFileStorageClass:
    """Direct tests for LocalFileStorage class."""

    def test_store_creates_directories(self, tmp_path, settings):
        """Test that store creates parent directories."""
        settings.MEDIA_ROOT = str(tmp_path)
        settings.MEDIA_URL = "/media/"
        backend = LocalFileStorage()
        url = backend.store("deep/nested/path/file.txt", b"content")
        assert "deep/nested/path/file.txt" in url
        assert (tmp_path / "deep/nested/path/file.txt").exists()

    def test_delete_missing_file_ok(self, tmp_path, settings):
        """Test that delete doesn't error on missing file."""
        settings.MEDIA_ROOT = str(tmp_path)
        settings.MEDIA_URL = "/media/"
        backend = LocalFileStorage()
        # Should not raise
        backend.delete("nonexistent/file.txt")
