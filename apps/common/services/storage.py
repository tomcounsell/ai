"""
File Storage Service: Abstract interface for binary file storage.

Provides a unified API for storing and retrieving files, with pluggable backends
for local filesystem (dev), Supabase (prod default), and S3-compatible services.

Supports dual-bucket storage: public bucket for permanent URLs, private bucket
for signed URLs with time-limited access.

Usage:
    from apps.common.services.storage import store_file, get_file_url

    # Public file (default) - permanent URL
    url = store_file("podcast/my-show/ep1/audio.mp3", audio_bytes, "audio/mpeg")
    url = get_file_url("podcast/my-show/ep1/audio.mp3")

    # Private file - signed URL with 24h expiration
    url = store_file("drafts/ep1/notes.pdf", pdf_bytes, "application/pdf", public=False)
    url = get_file_url("drafts/ep1/notes.pdf", public=False)
"""

import logging
from abc import ABC, abstractmethod
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)


class StorageBackend(ABC):
    """Abstract base class for storage backends."""

    @abstractmethod
    def store(
        self, key: str, content: bytes, content_type: str = "", public: bool = True
    ) -> str:
        """Store content, return URL."""

    @abstractmethod
    def get_url(self, key: str, public: bool = True) -> str:
        """Get URL for key. Public=permanent URL, private=signed URL."""

    @abstractmethod
    def get_content(self, key: str, public: bool = True) -> bytes:
        """Retrieve content by key."""

    @abstractmethod
    def delete(self, key: str, public: bool = True) -> None:
        """Delete file by key."""

    @classmethod
    def check_config(cls) -> list[str]:
        """Return list of missing config keys. Empty list = ready."""
        return []


class LocalFileStorage(StorageBackend):
    """Filesystem storage for development. Writes to MEDIA_ROOT."""

    def store(
        self, key: str, content: bytes, content_type: str = "", public: bool = True
    ) -> str:
        path = Path(settings.MEDIA_ROOT) / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return f"{settings.MEDIA_URL}{key}"

    def get_url(self, key: str, public: bool = True) -> str:
        return f"{settings.MEDIA_URL}{key}"

    def get_content(self, key: str, public: bool = True) -> bytes:
        path = Path(settings.MEDIA_ROOT) / key
        return path.read_bytes()

    def delete(self, key: str, public: bool = True) -> None:
        path = Path(settings.MEDIA_ROOT) / key
        path.unlink(missing_ok=True)


class SupabaseStorage(StorageBackend):
    """Supabase Storage backend with dual-bucket support.

    Uses a public bucket for permanent URLs and an optional private bucket
    for signed URLs with time-limited access.
    """

    def __init__(self):
        from apps.integration.supabase.storage_manager import SupabaseStorageManager

        self._public_manager = SupabaseStorageManager(
            settings.SUPABASE_PUBLIC_BUCKET_NAME
        )
        self._private_manager = (
            SupabaseStorageManager(settings.SUPABASE_PRIVATE_BUCKET_NAME)
            if settings.SUPABASE_PRIVATE_BUCKET_NAME
            else None
        )

    def _manager(self, public: bool = True):
        """Return the appropriate storage manager based on access level."""
        if public or self._private_manager is None:
            return self._public_manager
        return self._private_manager

    def store(
        self, key: str, content: bytes, content_type: str = "", public: bool = True
    ) -> str:
        parts = key.rsplit("/", 1)
        if len(parts) == 2:
            path_prefixes = parts[0].split("/")
            filename = parts[1]
        else:
            path_prefixes = []
            filename = parts[0]
        return self._manager(public).upload(
            file_content=content,
            path_prefixes=path_prefixes,
            custom_filename=filename,
            file_type=content_type or "application/octet-stream",
        )

    def get_url(self, key: str, public: bool = True) -> str:
        if public:
            return self._manager(True).get_public_url(key)
        return self._manager(False).create_signed_url(key, expires_in=86400)

    def get_content(self, key: str, public: bool = True) -> bytes:
        return self._manager(public).download(key)

    def delete(self, key: str, public: bool = True) -> None:
        self._manager(public).delete(key)

    @classmethod
    def check_config(cls) -> list[str]:
        missing = []
        for key in (
            "SUPABASE_PROJECT_URL",
            "SUPABASE_SERVICE_ROLE_KEY",
            "SUPABASE_PUBLIC_BUCKET_NAME",
        ):
            if not getattr(settings, key, None):
                missing.append(key)
        return missing


class S3Storage(StorageBackend):
    """S3-compatible storage (S3, R2, etc.)."""

    def __init__(self):
        import boto3

        self.client = boto3.client(
            "s3",
            endpoint_url=settings.S3_ENDPOINT_URL,
            aws_access_key_id=settings.S3_ACCESS_KEY,
            aws_secret_access_key=settings.S3_SECRET_KEY,
        )
        self.bucket = settings.S3_BUCKET
        self.public_url = settings.S3_PUBLIC_URL

    def store(
        self, key: str, content: bytes, content_type: str = "", public: bool = True
    ) -> str:
        extra_args = {}
        if content_type:
            extra_args["ContentType"] = content_type
        self.client.put_object(Bucket=self.bucket, Key=key, Body=content, **extra_args)
        return f"{self.public_url}/{key}"

    def get_url(self, key: str, public: bool = True) -> str:
        return f"{self.public_url}/{key}"

    def get_content(self, key: str, public: bool = True) -> bytes:
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        return response["Body"].read()

    def delete(self, key: str, public: bool = True) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    @classmethod
    def check_config(cls) -> list[str]:
        missing = []
        for key in (
            "S3_ENDPOINT_URL",
            "S3_ACCESS_KEY",
            "S3_SECRET_KEY",
            "S3_BUCKET",
            "S3_PUBLIC_URL",
        ):
            if not getattr(settings, key, None):
                missing.append(key)
        return missing


# Backend registry
_backends = {
    "local": LocalFileStorage,
    "supabase": SupabaseStorage,
    "s3": S3Storage,
}

_backend_instance = None


def _get_backend() -> StorageBackend:
    global _backend_instance
    if _backend_instance is None:
        backend_name = getattr(settings, "STORAGE_BACKEND", "local")
        backend_cls = _backends.get(backend_name)
        if not backend_cls:
            logger.error(f"Unknown storage backend: {backend_name}. Using local.")
            backend_cls = LocalFileStorage
        else:
            missing = backend_cls.check_config()
            if missing:
                logger.warning(
                    f"Storage backend '{backend_name}' is missing config: {missing}. "
                    f"Falling back to local storage."
                )
                backend_cls = LocalFileStorage
        _backend_instance = backend_cls()
    return _backend_instance


def reset_backend() -> None:
    """Reset the backend singleton. Useful for testing."""
    global _backend_instance
    _backend_instance = None


# Public API
def store_file(
    key: str, content: bytes, content_type: str = "", public: bool = True
) -> str:
    """Store binary content, return a URL.

    Args:
        key: Storage key/path for the file.
        content: File content as bytes.
        content_type: MIME type of the file.
        public: If True, store in public bucket with permanent URL.
                If False, store in private bucket.
    """
    return _get_backend().store(key, content, content_type, public=public)


def get_file_url(key: str, public: bool = True) -> str:
    """Get URL for a stored file.

    Args:
        key: Storage key/path for the file.
        public: If True, return permanent public URL.
                If False, return signed URL with 24h expiration.
    """
    return _get_backend().get_url(key, public=public)


def get_file_content(key: str, public: bool = True) -> bytes:
    """Retrieve file content by key."""
    return _get_backend().get_content(key, public=public)


def delete_file(key: str, public: bool = True) -> None:
    """Remove a stored file."""
    return _get_backend().delete(key, public=public)


def check_storage_config() -> dict:
    """
    Check storage configuration and return status.
    Useful for health checks and the management command.
    """
    backend_name = getattr(settings, "STORAGE_BACKEND", "local")
    backend_cls = _backends.get(backend_name)
    if not backend_cls:
        return {
            "backend": backend_name,
            "ok": False,
            "error": f"Unknown backend: {backend_name}",
        }
    missing = backend_cls.check_config()
    return {
        "backend": backend_name,
        "ok": len(missing) == 0,
        "missing_keys": missing,
    }
