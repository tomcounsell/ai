"""Tests for the file storage service."""

from unittest.mock import MagicMock, patch

import pytest

from apps.common.services.storage import (
    LocalFileStorage,
    SupabaseStorage,
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

    def test_store_public_false(self, local_storage):
        """Test storing with public=False still works for local backend."""
        url = store_file("test/private.txt", b"secret", public=False)
        assert "test/private.txt" in url
        content = get_file_content("test/private.txt", public=False)
        assert content == b"secret"

    def test_get_url_public_false(self, local_storage):
        """Test get_url with public=False returns same URL for local backend."""
        store_file("test/private.txt", b"secret")
        url = get_file_url("test/private.txt", public=False)
        assert url == "/media/test/private.txt"

    def test_delete_public_false(self, local_storage):
        """Test delete with public=False works for local backend."""
        store_file("test/private.txt", b"secret")
        delete_file("test/private.txt", public=False)
        with pytest.raises(FileNotFoundError):
            get_file_content("test/private.txt")


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
        settings.SUPABASE_PUBLIC_BUCKET_NAME = ""
        status = check_storage_config()
        assert status["ok"] is False
        assert "SUPABASE_PROJECT_URL" in status["missing_keys"]
        assert "SUPABASE_PUBLIC_BUCKET_NAME" in status["missing_keys"]

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
        settings.SUPABASE_PUBLIC_BUCKET_NAME = ""
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

    def test_public_parameter_ignored(self, tmp_path, settings):
        """Test that public parameter is accepted but ignored for local storage."""
        settings.MEDIA_ROOT = str(tmp_path)
        settings.MEDIA_URL = "/media/"
        backend = LocalFileStorage()

        # Store with public=False
        url = backend.store("test/file.txt", b"content", public=False)
        assert "test/file.txt" in url

        # Get URL with public=False
        url = backend.get_url("test/file.txt", public=False)
        assert url == "/media/test/file.txt"

        # Get content with public=False
        content = backend.get_content("test/file.txt", public=False)
        assert content == b"content"

        # Delete with public=False
        backend.delete("test/file.txt", public=False)
        assert not (tmp_path / "test/file.txt").exists()


class TestSupabaseStorageDualBucket:
    """Tests for SupabaseStorage dual-bucket routing."""

    @pytest.fixture
    def mock_managers(self, settings):
        """Create a SupabaseStorage with mocked public and private managers."""
        settings.SUPABASE_PROJECT_URL = "https://test.supabase.co"
        settings.SUPABASE_SERVICE_ROLE_KEY = "test-key"
        settings.SUPABASE_PUBLIC_BUCKET_NAME = "public-bucket"
        settings.SUPABASE_PRIVATE_BUCKET_NAME = "private-bucket"

        public_mgr = MagicMock()
        private_mgr = MagicMock()
        call_count = 0

        def fake_manager(bucket_name):
            nonlocal call_count
            call_count += 1
            return public_mgr if call_count == 1 else private_mgr

        with patch(
            "apps.common.services.storage.SupabaseStorage.__init__",
            lambda self: None,
        ):
            storage = SupabaseStorage()
            storage._public_manager = public_mgr
            storage._private_manager = private_mgr
            yield storage, public_mgr, private_mgr

    def test_store_public_routes_to_public_bucket(self, mock_managers):
        """Storing with public=True routes to the public manager."""
        storage, public_mgr, private_mgr = mock_managers
        public_mgr.upload.return_value = "https://public.url/file.mp3"

        result = storage.store(
            "podcast/test/audio.mp3", b"audio", "audio/mpeg", public=True
        )

        public_mgr.upload.assert_called_once()
        private_mgr.upload.assert_not_called()
        assert result == "https://public.url/file.mp3"

    def test_store_private_routes_to_private_bucket(self, mock_managers):
        """Storing with public=False routes to the private manager."""
        storage, public_mgr, private_mgr = mock_managers
        private_mgr.upload.return_value = "https://private.url/file.mp3"

        result = storage.store(
            "podcast/test/audio.mp3", b"audio", "audio/mpeg", public=False
        )

        private_mgr.upload.assert_called_once()
        public_mgr.upload.assert_not_called()
        assert result == "https://private.url/file.mp3"

    def test_store_default_is_public(self, mock_managers):
        """Calling store() without public parameter defaults to public=True."""
        storage, public_mgr, private_mgr = mock_managers
        public_mgr.upload.return_value = "https://public.url/file.txt"

        storage.store("test/file.txt", b"content", "text/plain")

        public_mgr.upload.assert_called_once()
        private_mgr.upload.assert_not_called()

    def test_store_passes_correct_args(self, mock_managers):
        """Store parses key into path_prefixes and filename correctly."""
        storage, public_mgr, private_mgr = mock_managers
        public_mgr.upload.return_value = "https://public.url/audio.mp3"

        storage.store(
            "podcast/my-show/ep1/audio.mp3", b"data", "audio/mpeg", public=True
        )

        public_mgr.upload.assert_called_once_with(
            file_content=b"data",
            path_prefixes=["podcast", "my-show", "ep1"],
            custom_filename="audio.mp3",
            file_type="audio/mpeg",
        )

    def test_store_single_filename_no_prefix(self, mock_managers):
        """Store with a simple filename (no path separators)."""
        storage, public_mgr, private_mgr = mock_managers
        public_mgr.upload.return_value = "https://public.url/file.txt"

        storage.store("file.txt", b"data", "text/plain")

        public_mgr.upload.assert_called_once_with(
            file_content=b"data",
            path_prefixes=[],
            custom_filename="file.txt",
            file_type="text/plain",
        )

    def test_get_url_public_returns_permanent_url(self, mock_managers):
        """get_url with public=True returns a permanent public URL."""
        storage, public_mgr, private_mgr = mock_managers
        public_mgr.get_public_url.return_value = (
            "https://test.supabase.co/storage/v1/object/public/"
            "public-bucket/podcast/test/audio.mp3"
        )

        result = storage.get_url("podcast/test/audio.mp3", public=True)

        public_mgr.get_public_url.assert_called_once_with("podcast/test/audio.mp3")
        assert "public-bucket" in result

    def test_get_url_private_returns_signed_url(self, mock_managers):
        """get_url with public=False returns a signed URL with 24h expiry."""
        storage, public_mgr, private_mgr = mock_managers
        private_mgr.create_signed_url.return_value = (
            "https://test.supabase.co/storage/v1/object/sign/"
            "private-bucket/audio.mp3?token=abc123"
        )

        result = storage.get_url("podcast/test/audio.mp3", public=False)

        private_mgr.create_signed_url.assert_called_once_with(
            "podcast/test/audio.mp3", expires_in=86400
        )
        assert "token" in result

    def test_get_url_default_is_public(self, mock_managers):
        """get_url without public parameter defaults to public=True."""
        storage, public_mgr, private_mgr = mock_managers
        public_mgr.get_public_url.return_value = "https://public.url/file.mp3"

        storage.get_url("test/file.mp3")

        public_mgr.get_public_url.assert_called_once_with("test/file.mp3")
        private_mgr.create_signed_url.assert_not_called()

    def test_get_content_public(self, mock_managers):
        """get_content with public=True reads from the public manager."""
        storage, public_mgr, private_mgr = mock_managers
        public_mgr.download.return_value = b"public-content"

        result = storage.get_content("test/file.txt", public=True)

        public_mgr.download.assert_called_once_with("test/file.txt")
        assert result == b"public-content"

    def test_get_content_private(self, mock_managers):
        """get_content with public=False reads from the private manager."""
        storage, public_mgr, private_mgr = mock_managers
        private_mgr.download.return_value = b"private-content"

        result = storage.get_content("test/file.txt", public=False)

        private_mgr.download.assert_called_once_with("test/file.txt")
        assert result == b"private-content"

    def test_delete_public(self, mock_managers):
        """delete with public=True deletes from the public manager."""
        storage, public_mgr, private_mgr = mock_managers

        storage.delete("test/file.txt", public=True)

        public_mgr.delete.assert_called_once_with("test/file.txt")
        private_mgr.delete.assert_not_called()

    def test_delete_private(self, mock_managers):
        """delete with public=False deletes from the private manager."""
        storage, public_mgr, private_mgr = mock_managers

        storage.delete("test/file.txt", public=False)

        private_mgr.delete.assert_called_once_with("test/file.txt")
        public_mgr.delete.assert_not_called()

    def test_no_private_bucket_falls_back_to_public(self, settings):
        """When SUPABASE_PRIVATE_BUCKET_NAME is empty, private ops use public."""
        settings.SUPABASE_PROJECT_URL = "https://test.supabase.co"
        settings.SUPABASE_SERVICE_ROLE_KEY = "test-key"
        settings.SUPABASE_PUBLIC_BUCKET_NAME = "public-bucket"
        settings.SUPABASE_PRIVATE_BUCKET_NAME = ""

        with patch(
            "apps.common.services.storage.SupabaseStorage.__init__",
            lambda self: None,
        ):
            public_mgr = MagicMock()
            storage = SupabaseStorage()
            storage._public_manager = public_mgr
            storage._private_manager = None

            assert storage._private_manager is None

            public_mgr.upload.return_value = "https://public.url/file.mp3"
            storage.store("test/file.txt", b"data", "text/plain", public=False)
            public_mgr.upload.assert_called_once()

    def test_check_config_validates_public_bucket_name(self, settings):
        """check_config reports missing SUPABASE_PUBLIC_BUCKET_NAME."""
        settings.SUPABASE_PROJECT_URL = "https://test.supabase.co"
        settings.SUPABASE_SERVICE_ROLE_KEY = "test-key"
        settings.SUPABASE_PUBLIC_BUCKET_NAME = ""

        missing = SupabaseStorage.check_config()
        assert "SUPABASE_PUBLIC_BUCKET_NAME" in missing

    def test_check_config_all_present(self, settings):
        """check_config returns empty list when all required keys are set."""
        settings.SUPABASE_PROJECT_URL = "https://test.supabase.co"
        settings.SUPABASE_SERVICE_ROLE_KEY = "test-key"
        settings.SUPABASE_PUBLIC_BUCKET_NAME = "public-bucket"

        missing = SupabaseStorage.check_config()
        assert missing == []

    def test_store_empty_content_type_defaults(self, mock_managers):
        """Store with empty content_type sends application/octet-stream."""
        storage, public_mgr, private_mgr = mock_managers
        public_mgr.upload.return_value = "https://public.url/file.bin"

        storage.store("test/file.bin", b"binary", "")

        call_kwargs = public_mgr.upload.call_args.kwargs
        assert call_kwargs["file_type"] == "application/octet-stream"
