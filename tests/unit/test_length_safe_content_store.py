"""Unit tests for LengthSafeFilesystemStore (issue #2085).

No network, no Redis -- instantiates LengthSafeFilesystemStore directly
against a tmp_path base_path, mirroring the popoto FilesystemStore test
surface it subclasses.
"""

import os

import pytest
from popoto.stores.filesystem import FilesystemStore

from models.length_safe_content_store import LengthSafeFilesystemStore


@pytest.fixture
def store(tmp_path):
    return LengthSafeFilesystemStore(base_path=str(tmp_path))


@pytest.mark.unit
class TestLengthSafeFilenameSanitize:
    """_sanitize_filename budget-capping behavior."""

    def test_short_key_byte_identical_to_parent(self, store):
        """Keys under budget sanitize identically to the parent (no churn)."""
        key = "chunk123:doc456:short/path.md:test-project"
        expected = FilesystemStore._sanitize_filename(key)
        assert store._sanitize_filename(key) == expected
        assert len(expected.encode("utf-8")) <= 200

    def test_empty_and_whitespace_keys_do_not_raise(self, store):
        """Empty/whitespace-only keys return a value without raising."""
        assert store._sanitize_filename("") == FilesystemStore._sanitize_filename("")
        assert store._sanitize_filename("   ") == FilesystemStore._sanitize_filename("   ")

    def test_long_ascii_key_capped_deterministic_unique(self, store):
        """Long ASCII keys are capped, deterministic, and unique per key."""
        long_path = "/".join(["a-very-long-directory-segment"] * 15)
        key_a = f"chunk_aaa:doc_common:{long_path}:test-project"
        key_b = f"chunk_bbb:doc_common:{long_path}:test-project"

        name_a = store._sanitize_filename(key_a)
        name_b = store._sanitize_filename(key_b)

        assert len(name_a.encode("utf-8")) <= 200
        assert len(name_b.encode("utf-8")) <= 200

        # Deterministic: same key twice -> same name.
        assert store._sanitize_filename(key_a) == name_a

        # Unique: keys differing only in the unique chunk_id prefix diverge.
        assert name_a != name_b

        # Shape: truncated names end with an underscore + 16 hex chars.
        digest_a = name_a.rsplit("_", 1)[-1]
        assert len(digest_a) == 16
        assert all(c in "0123456789abcdef" for c in digest_a)

    def test_long_non_ascii_key_no_unicode_decode_error(self, store):
        """Long non-ASCII keys truncate safely without UnicodeDecodeError."""
        long_path = "/".join(["café/naïve/日本語"] * 15)
        key = f"chunk_unicode:doc_common:{long_path}:test-project"

        # Must not raise UnicodeDecodeError (or any exception).
        name = store._sanitize_filename(key)

        assert len(name.encode("utf-8")) <= 200

    def test_knowledge_document_shaped_long_key_also_capped(self, store):
        """A 3-component KnowledgeDocument-shaped key overflows and is capped too."""
        long_path = "/".join(["deeply-nested-vault-segment"] * 12)
        key = f"doc_abc123:{long_path}:psyoptimal"

        uncapped = FilesystemStore._sanitize_filename(key)
        assert len(uncapped.encode("utf-8")) > 200  # sanity: this key does overflow

        name = store._sanitize_filename(key)
        assert len(name.encode("utf-8")) <= 200
        assert name != uncapped

    def test_save_and_load_round_trip_long_key(self, store):
        """save() + load() round-trips original bytes for a long key."""
        long_path = "/".join(["round-trip-segment"] * 15)
        key = f"chunk_rt:doc_rt:{long_path}:test-project"
        content = b"hello from a very long content-store key"

        ref = store.save(content, key=key, model_class_name="DocumentChunk")
        loaded = store.load(ref)

        assert loaded == content

    def test_budget_override_forces_deterministic_truncation(self, store, monkeypatch):
        """Overriding the budget (env) forces truncation even for short keys."""
        monkeypatch.setenv("POPOTO_MAX_CONTENT_FILENAME_BYTES", "20")

        key = "chunk_small:doc_small:short/path.md:proj"
        name = store._sanitize_filename(key)

        assert len(name.encode("utf-8")) <= 20
        digest = name.rsplit("_", 1)[-1]
        assert len(digest) == 16

    def test_budget_env_override_read_at_call_time(self, store, monkeypatch):
        """The budget is read per-call, so env changes after store construction apply."""
        key = "chunk_x:doc_x:" + ("segment/" * 40) + ":proj"

        # Default budget (200) may or may not cap this key; force a tiny
        # budget to prove the override is observed without recreating the store.
        os.environ.pop("POPOTO_MAX_CONTENT_FILENAME_BYTES", None)
        monkeypatch.setenv("POPOTO_MAX_CONTENT_FILENAME_BYTES", "50")

        name = store._sanitize_filename(key)
        assert len(name.encode("utf-8")) <= 50
