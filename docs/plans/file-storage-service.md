---
status: Ready
type: feature
appetite: Small
owner: Valor
created: 2026-02-14
tracking: https://github.com/yudame/cuttlefish/issues/61
---

# File Storage Service: Abstract Interface for Binary File Storage

## Problem

The podcast workflow (and future features) need to store and retrieve binary files (audio MP3s, cover art images, PDFs). Currently, file handling would be tightly coupled to a specific storage provider. The workflow code should not know or care whether files are on S3, Cloudflare R2, local disk, or a Render persistent disk.

**Current behavior:**
No abstraction exists. Any file storage would require direct calls to a specific provider's API.

**Desired outcome:**
A thin storage abstraction that lets callers store/retrieve files by logical key, with the underlying provider configured via Django settings.

## Appetite

**Size:** Small

**Team:** Solo dev. Straightforward abstraction layer with two backends.

**Interactions:**
- PM check-ins: 0-1 (confirm S3-compatible provider choice for prod)
- Review rounds: 1

## Prerequisites

None - this is a foundational service with no dependencies on other work.

## Solution

### Key Elements

- **Abstract storage interface**: `store_file()`, `get_file_url()`, `get_file_content()`, `delete_file()`
- **Backend registry**: Select backend via `STORAGE_BACKEND` Django setting
- **Two backends**: `LocalFileStorage` (dev) and `S3Storage` (prod)
- **Logical keys**: Callers use paths like `podcast/algorithms-for-life/ep10/audio.mp3`

### Technical Approach

1. **Create storage service module at `apps/common/services/storage.py`:**

   ```python
   from django.conf import settings
   from abc import ABC, abstractmethod

   class StorageBackend(ABC):
       @abstractmethod
       def store(self, key: str, content: bytes, content_type: str = "") -> str:
           """Store content, return public URL."""
           pass

       @abstractmethod
       def get_url(self, key: str) -> str:
           """Get public URL for key."""
           pass

       @abstractmethod
       def get_content(self, key: str) -> bytes:
           """Retrieve content by key."""
           pass

       @abstractmethod
       def delete(self, key: str) -> None:
           """Delete file by key."""
           pass

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

   class S3Storage(StorageBackend):
       """S3-compatible storage for production (S3, R2, etc.)."""

       def __init__(self):
           import boto3
           self.client = boto3.client(
               's3',
               endpoint_url=settings.S3_ENDPOINT_URL,
               aws_access_key_id=settings.S3_ACCESS_KEY,
               aws_secret_access_key=settings.S3_SECRET_KEY,
           )
           self.bucket = settings.S3_BUCKET
           self.public_url = settings.S3_PUBLIC_URL

       def store(self, key: str, content: bytes, content_type: str = "") -> str:
           extra_args = {}
           if content_type:
               extra_args['ContentType'] = content_type
           self.client.put_object(
               Bucket=self.bucket, Key=key, Body=content, **extra_args
           )
           return f"{self.public_url}/{key}"

       def get_url(self, key: str) -> str:
           return f"{self.public_url}/{key}"

       def get_content(self, key: str) -> bytes:
           response = self.client.get_object(Bucket=self.bucket, Key=key)
           return response['Body'].read()

       def delete(self, key: str) -> None:
           self.client.delete_object(Bucket=self.bucket, Key=key)

   # Backend registry
   _backends = {
       'local': LocalFileStorage,
       's3': S3Storage,
   }

   def _get_backend() -> StorageBackend:
       backend_name = getattr(settings, 'STORAGE_BACKEND', 'local')
       return _backends[backend_name]()

   # Public API
   def store_file(key: str, content: bytes, content_type: str = "") -> str:
       """Store binary content, return a public URL."""
       return _get_backend().store(key, content, content_type)

   def get_file_url(key: str) -> str:
       """Get the public URL for a stored file."""
       return _get_backend().get_url(key)

   def get_file_content(key: str) -> bytes:
       """Retrieve file content by key."""
       return _get_backend().get_content(key)

   def delete_file(key: str) -> None:
       """Remove a stored file."""
       return _get_backend().delete(key)
   ```

2. **Add settings configuration:**

   ```python
   # settings/base.py
   STORAGE_BACKEND = env.str("STORAGE_BACKEND", default="local")

   # settings/local.py
   STORAGE_BACKEND = "local"

   # settings/production.py
   STORAGE_BACKEND = "s3"
   S3_ENDPOINT_URL = env.str("S3_ENDPOINT_URL")
   S3_ACCESS_KEY = env.str("S3_ACCESS_KEY")
   S3_SECRET_KEY = env.str("S3_SECRET_KEY")
   S3_BUCKET = env.str("S3_BUCKET")
   S3_PUBLIC_URL = env.str("S3_PUBLIC_URL")
   ```

3. **Write tests in `apps/common/tests/test_storage.py`:**

   ```python
   @pytest.fixture
   def storage_backend(tmp_path, settings):
       settings.MEDIA_ROOT = tmp_path
       settings.MEDIA_URL = "/media/"
       settings.STORAGE_BACKEND = "local"
       return LocalFileStorage()

   def test_store_and_retrieve(storage_backend):
       url = storage_backend.store("test/file.txt", b"hello", "text/plain")
       assert "test/file.txt" in url
       content = storage_backend.get_content("test/file.txt")
       assert content == b"hello"

   def test_delete(storage_backend):
       storage_backend.store("test/delete.txt", b"bye")
       storage_backend.delete("test/delete.txt")
       with pytest.raises(FileNotFoundError):
           storage_backend.get_content("test/delete.txt")
   ```

### File Changes

| File | Action | Description |
|------|--------|-------------|
| `apps/common/services/__init__.py` | Create | Empty init |
| `apps/common/services/storage.py` | Create | Storage abstraction + backends |
| `apps/common/tests/test_storage.py` | Create | Unit tests for storage service |
| `settings/base.py` | Modify | Add STORAGE_BACKEND setting |
| `settings/production.py` | Modify | Add S3 configuration |
| `pyproject.toml` | Modify | Add boto3 dependency |

## Rabbit Holes

- **Don't build CloudFlare R2 backend separately** - R2 is S3-compatible, use S3Storage
- **Don't add signed URL support yet** - All files are public for now (podcast audio needs public URLs for RSS)
- **Don't integrate with Django's FileField** - This is a service layer, not a model field replacement

## No-Gos

- No caching layer (premature optimization)
- No multipart upload (not needed for podcast files)
- No file metadata storage in DB (keys are self-describing)

## Acceptance Criteria

- [ ] Module importable: `from apps.common.services.storage import store_file, get_file_url`
- [ ] At least two backends: local filesystem (dev) and S3-compatible (prod)
- [ ] Backend selected by Django settings (`STORAGE_BACKEND`)
- [ ] Tests pass with local backend
- [ ] No podcast-specific code — general-purpose utility
