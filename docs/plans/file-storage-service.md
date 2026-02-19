---
status: Done
type: feature
appetite: Small
owner: Valor
created: 2026-02-14
completed: 2026-02-19
tracking: https://github.com/yudame/cuttlefish/issues/61
implementation: https://github.com/yudame/cuttlefish/pull/87
---

# File Storage Service: Abstract Interface for Binary File Storage

## Problem

The podcast workflow (and future features) need to store and retrieve binary files (audio MP3s, cover art images, PDFs). Currently, file handling would be tightly coupled to a specific storage provider. The workflow code should not know or care whether files are on Supabase, S3, local disk, or elsewhere.

**Prior state:**
No abstraction existed. Any file storage would require direct calls to a specific provider's API.

**Implemented outcome:**
A thin storage abstraction that lets callers store/retrieve files by logical key, with the underlying provider configured via Django settings. Ships with Supabase dual-bucket support for public/private files, local filesystem for dev, and S3-compatible backend for alternative production deployments.

## Implementation Notes

**Dual-bucket support added in PR #87:**
- Public files: permanent URLs from `SUPABASE_PUBLIC_BUCKET_NAME`
- Private files: signed URLs (24h TTL) from `SUPABASE_PRIVATE_BUCKET_NAME`
- `public` parameter added to all storage API functions (defaults to `True`)
- Podcast audio/cover art routes to correct bucket based on `Podcast.is_public`
- Feed cache invalidation via Django signals

See `docs/features/file-storage-service.md` for complete implementation details.

## Appetite

**Size:** Small

**Team:** Solo dev. Straightforward abstraction layer with three backends.

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

None - this is a foundational service with no dependencies on other work.

**Reference implementation:** `apps/integration/supabase/` — the Supabase client is already copied into the repo and ready to use as the default production backend.

## Solution

### Key Elements

- **Abstract storage interface**: `store_file()`, `get_file_url()`, `get_file_content()`, `delete_file()`
- **Backend registry**: Select backend via `STORAGE_BACKEND` Django setting
- **Three backends**: `LocalFileStorage` (dev), `SupabaseStorage` (prod default), `S3Storage` (alt prod)
- **Logical keys**: Callers use paths like `podcast/algorithms-for-life/ep10/audio.mp3`
- **Health check on startup**: Validates required API keys are present for the configured backend
- **`check_storage` management command**: Verify connectivity locally or on production

### Technical Approach

#### Task 1: Create storage service module at `apps/common/services/storage.py`

```python
import logging
from abc import ABC, abstractmethod
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)


class StorageBackend(ABC):
    @abstractmethod
    def store(self, key: str, content: bytes, content_type: str = "", public: bool = True) -> str:
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

    def store(self, key: str, content: bytes, content_type: str = "") -> str:
        path = Path(settings.MEDIA_ROOT) / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return f"{settings.MEDIA_URL}{key}"

    def get_url(self, key: str) -> str:
        return f"{settings.MEDIA_URL}{key}"

    def get_content(self, key: str) -> bytes:
        path = Path(settings.MEDIA_ROOT) / key
        return path.read_bytes()

    def delete(self, key: str) -> None:
        path = Path(settings.MEDIA_ROOT) / key
        path.unlink(missing_ok=True)


class SupabaseStorage(StorageBackend):
    """Supabase Storage backend with dual-bucket support. Default for production."""

    def __init__(self):
        from apps.integration.supabase.storage_manager import SupabaseStorageManager
        self._public_manager = SupabaseStorageManager(settings.SUPABASE_PUBLIC_BUCKET_NAME)
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

    def store(self, key: str, content: bytes, content_type: str = "", public: bool = True) -> str:
        # Split key into path parts and filename
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
        for key in ("SUPABASE_PROJECT_URL", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_PUBLIC_BUCKET_NAME"):
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

    def store(self, key: str, content: bytes, content_type: str = "") -> str:
        extra_args = {}
        if content_type:
            extra_args["ContentType"] = content_type
        self.client.put_object(
            Bucket=self.bucket, Key=key, Body=content, **extra_args
        )
        return f"{self.public_url}/{key}"

    def get_url(self, key: str) -> str:
        return f"{self.public_url}/{key}"

    def get_content(self, key: str) -> bytes:
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        return response["Body"].read()

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    @classmethod
    def check_config(cls) -> list[str]:
        missing = []
        for key in ("S3_ENDPOINT_URL", "S3_ACCESS_KEY", "S3_SECRET_KEY", "S3_BUCKET", "S3_PUBLIC_URL"):
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
        backend_cls = _backends[backend_name]
        missing = backend_cls.check_config()
        if missing:
            logger.warning(
                f"Storage backend '{backend_name}' is missing config: {missing}. "
                f"Falling back to local storage."
            )
            backend_cls = LocalFileStorage
        _backend_instance = backend_cls()
    return _backend_instance


# Public API
def store_file(key: str, content: bytes, content_type: str = "", public: bool = True) -> str:
    """Store binary content, return a URL. public=True for permanent URLs, False for signed."""
    return _get_backend().store(key, content, content_type, public=public)


def get_file_url(key: str, public: bool = True) -> str:
    """Get URL for a stored file. public=True for permanent URLs, False for signed (24h TTL)."""
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
        return {"backend": backend_name, "ok": False, "error": f"Unknown backend: {backend_name}"}
    missing = backend_cls.check_config()
    return {
        "backend": backend_name,
        "ok": len(missing) == 0,
        "missing_keys": missing,
    }
```

#### Task 2: Create `check_storage` management command

`apps/common/management/commands/check_storage.py`:

```python
from django.core.management.base import BaseCommand
from apps.common.services.storage import check_storage_config, store_file, get_file_content, delete_file


class Command(BaseCommand):
    help = "Verify storage backend configuration and connectivity"

    def handle(self, *args, **options):
        # Check config
        status = check_storage_config()
        self.stdout.write(f"Backend: {status['backend']}")

        if not status["ok"]:
            self.stderr.write(self.style.ERROR(
                f"Missing settings: {', '.join(status['missing_keys'])}"
            ))
            return

        self.stdout.write(self.style.SUCCESS("Configuration: OK"))

        # Connectivity test: write, read, delete a probe file
        probe_key = "_storage_probe/health_check.txt"
        probe_data = b"storage-health-check"
        try:
            url = store_file(probe_key, probe_data, "text/plain")
            self.stdout.write(f"  store → {url}")

            content = get_file_content(probe_key)
            assert content == probe_data, "Round-trip mismatch"
            self.stdout.write("  read  → OK")

            delete_file(probe_key)
            self.stdout.write("  delete → OK")

            self.stdout.write(self.style.SUCCESS("Connectivity: OK"))
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"Connectivity FAILED: {e}"))
```

This lets you run `uv run python manage.py check_storage` locally **and** via Render's shell to verify production keys.

#### Task 3: Add settings configuration

```python
# settings/base.py — add near bottom
STORAGE_BACKEND = "local"  # Options: "local", "supabase", "s3"

# Supabase Storage (dual-bucket support for public/private files)
SUPABASE_PROJECT_URL = os.environ.get("SUPABASE_PROJECT_URL", "")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
SUPABASE_PUBLIC_BUCKET_NAME = os.environ.get("SUPABASE_PUBLIC_BUCKET_NAME", "") or os.environ.get("SUPABASE_BUCKET_NAME", "")
SUPABASE_PRIVATE_BUCKET_NAME = os.environ.get("SUPABASE_PRIVATE_BUCKET_NAME", "")
SUPABASE_USER_ACCESS_TOKEN = os.environ.get("SUPABASE_USER_ACCESS_TOKEN", "")

# settings/production.py — add
STORAGE_BACKEND = "supabase"
```

#### Task 4: Add `supabase` dependency

```bash
uv add supabase
```

`boto3` is NOT added by default — only needed if someone switches to the S3 backend.

#### Task 5: Write tests in `apps/common/tests/test_storage.py`

```python
import pytest
from apps.common.services.storage import (
    LocalFileStorage, check_storage_config,
    store_file, get_file_url, get_file_content, delete_file,
)


@pytest.fixture
def local_storage(tmp_path, settings):
    settings.MEDIA_ROOT = str(tmp_path)
    settings.MEDIA_URL = "/media/"
    settings.STORAGE_BACKEND = "local"
    # Reset singleton
    import apps.common.services.storage as mod
    mod._backend_instance = None
    yield
    mod._backend_instance = None


def test_store_and_retrieve(local_storage):
    url = store_file("test/file.txt", b"hello", "text/plain")
    assert "test/file.txt" in url
    content = get_file_content("test/file.txt")
    assert content == b"hello"


def test_get_url(local_storage):
    store_file("test/file.txt", b"hello")
    url = get_file_url("test/file.txt")
    assert url == "/media/test/file.txt"


def test_delete(local_storage):
    store_file("test/delete.txt", b"bye")
    delete_file("test/delete.txt")
    with pytest.raises(FileNotFoundError):
        get_file_content("test/delete.txt")


def test_check_config_local(local_storage):
    status = check_storage_config()
    assert status["ok"] is True
    assert status["backend"] == "local"


def test_check_config_missing_supabase(settings):
    settings.STORAGE_BACKEND = "supabase"
    settings.SUPABASE_PROJECT_URL = ""
    settings.SUPABASE_SERVICE_ROLE_KEY = ""
    settings.SUPABASE_BUCKET_NAME = ""
    status = check_storage_config()
    assert status["ok"] is False
    assert "SUPABASE_PROJECT_URL" in status["missing_keys"]


def test_fallback_on_missing_config(tmp_path, settings):
    """When supabase keys are missing, falls back to local storage."""
    settings.STORAGE_BACKEND = "supabase"
    settings.SUPABASE_PROJECT_URL = ""
    settings.MEDIA_ROOT = str(tmp_path)
    settings.MEDIA_URL = "/media/"
    import apps.common.services.storage as mod
    mod._backend_instance = None
    url = store_file("fallback/test.txt", b"works")
    assert "/media/" in url
    mod._backend_instance = None
```

### File Changes

| File | Action | Description |
|------|--------|-------------|
| `apps/integration/supabase/__init__.py` | Create | Already done — empty init |
| `apps/integration/supabase/storage_manager.py` | Create | Already done — Supabase client |
| `apps/integration/supabase/test_mocks.py` | Create | Already done — test mocks |
| `apps/common/services/__init__.py` | Create | Empty init |
| `apps/common/services/storage.py` | Create | Storage abstraction + 3 backends |
| `apps/common/tests/test_storage.py` | Create | Unit tests |
| `apps/common/management/commands/check_storage.py` | Create | Health check command |
| `settings/base.py` | Modify | Add `STORAGE_BACKEND` + Supabase settings |
| `settings/production.py` | Modify | Set `STORAGE_BACKEND = "supabase"` |
| `pyproject.toml` | Modify | Add `supabase` dependency |

## Usage

### Quick Start (local dev)

Works out of the box with no configuration — uses local filesystem by default:

```python
from apps.common.services.storage import store_file, get_file_url, get_file_content, delete_file

# Store a file
url = store_file("podcast/my-show/ep1/audio.mp3", audio_bytes, "audio/mpeg")

# Get its public URL later
url = get_file_url("podcast/my-show/ep1/audio.mp3")

# Read it back
content = get_file_content("podcast/my-show/ep1/audio.mp3")

# Delete it
delete_file("podcast/my-show/ep1/audio.mp3")
```

### Production Setup (Supabase)

1. Create a Supabase project at [supabase.com](https://supabase.com)
2. Create two storage buckets:
   - Public bucket (for serving public podcast audio over RSS)
   - Private bucket (for private podcast audio with signed URLs)
3. Add environment variables on Render:

```
STORAGE_BACKEND=supabase
SUPABASE_PROJECT_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=eyJ...
SUPABASE_PUBLIC_BUCKET_NAME=public-podcasts
SUPABASE_PRIVATE_BUCKET_NAME=private-podcasts  # optional
SUPABASE_USER_ACCESS_TOKEN=your-secret-token   # for private feed auth
```

4. Verify it works:

```bash
# Locally (with .env.local set)
uv run python manage.py check_storage

# On Render (via shell)
python manage.py check_storage
```

See `docs/features/file-storage-service.md` for migration guide from single-bucket to dual-bucket setup.

### Graceful Fallback

If the configured backend's API keys are missing, the service logs a warning and falls back to local storage. This means:

- **Dev machines** never break even if someone sets `STORAGE_BACKEND=supabase` without keys
- **CI/CD** tests always work since they use the default `local` backend
- **Production** will log an obvious warning if Supabase keys are missing, but won't crash

### Using the S3 Backend (alternative)

If you prefer S3/R2 over Supabase, add `boto3` and set:

```
STORAGE_BACKEND=s3
S3_ENDPOINT_URL=https://your-endpoint
S3_ACCESS_KEY=...
S3_SECRET_KEY=...
S3_BUCKET=your-bucket
S3_PUBLIC_URL=https://your-cdn-or-bucket-url
```

Then run `uv add boto3` and `python manage.py check_storage`.

## Rabbit Holes

- **Don't build CloudFlare R2 backend separately** - R2 is S3-compatible, use S3Storage
- **Don't add signed URL support yet** - All files are public for now (podcast audio needs public URLs for RSS)
- **Don't integrate with Django's FileField** - This is a service layer, not a model field replacement

## No-Gos

- No caching layer (premature optimization)
- No multipart upload (not needed for podcast files)
- No file metadata storage in DB (keys are self-describing)

## Acceptance Criteria

- [x] Module importable: `from apps.common.services.storage import store_file, get_file_url`
- [x] Three backends: local filesystem (dev), Supabase (prod default), S3-compatible (alt)
- [x] Backend selected by Django settings (`STORAGE_BACKEND`)
- [x] Graceful fallback to local storage when configured backend's keys are missing
- [x] `check_storage` management command verifies config + connectivity
- [x] Tests pass with local backend (no external services needed in CI)
- [x] No podcast-specific code — general-purpose utility
- [x] Supabase integration client in `apps/integration/supabase/` (already committed)
- [x] **Dual-bucket support** for public/private files (added in PR #87)
- [x] **Signed URL generation** for private files with 24h TTL (added in PR #87)
- [x] **Feed cache invalidation** on episode publish/unpublish (added in PR #87)
