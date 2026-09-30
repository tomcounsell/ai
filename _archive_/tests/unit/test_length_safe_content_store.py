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


# ===================================================================
# VerifyingArtifactStore (#3177)
# ===================================================================


@pytest.fixture
def verifying_store(tmp_path):
    from models.verifying_artifact_store import VerifyingArtifactStore

    return VerifyingArtifactStore(base_path=str(tmp_path / "artifacts"))


@pytest.mark.unit
class TestVerifyingArtifactStore:
    """Every load path re-hashes, archive fallback included.

    Popoto's FilesystemStore.load() verifies the live file and then, on a
    mismatch or a missing live file, returns the archived bytes WITHOUT
    re-hashing them. For a document chunk that is harmless. For evaluation
    evidence it turns a corrupted file into a scored result, which is exactly
    what docs/plans/recursive-self-improvement.md exists to prevent.
    """

    def test_round_trip(self, verifying_store):
        ref = verifying_store.save(b"manifest bytes", key="exp-1", model_class_name="Exp")
        assert verifying_store.load(ref) == b"manifest bytes"

    def test_corrupted_live_file_with_no_archive_raises(self, verifying_store):
        from models.verifying_artifact_store import ArtifactIntegrityError

        ref = verifying_store.save(b"original", key="exp-2", model_class_name="Exp")
        _, relative = verifying_store._parse_reference(ref)
        live = os.path.join(verifying_store.base_path, relative)
        with open(live, "wb") as f:
            f.write(b"tampered")

        with pytest.raises(ArtifactIntegrityError):
            verifying_store.load(ref)

    def test_corrupted_archive_raises_instead_of_returning_bytes(self, verifying_store):
        """The behavior this subclass exists for.

        Save twice so the first version lands in .versions/, then corrupt BOTH
        the archived copy and the live file. The parent would fall through to
        the archive and hand back the tampered bytes unverified; this store
        refuses.
        """
        from models.verifying_artifact_store import ArtifactIntegrityError

        first_ref = verifying_store.save(b"version one", key="exp-3", model_class_name="Exp")
        verifying_store.save(b"version two", key="exp-3", model_class_name="Exp")

        content_hash, relative = verifying_store._parse_reference(first_ref)
        archive = verifying_store._version_path(content_hash)
        assert os.path.exists(archive), "precondition: the first version was archived"

        with open(archive, "wb") as f:
            f.write(b"tampered archive")

        with pytest.raises(ArtifactIntegrityError):
            verifying_store.load(first_ref)

    def test_parent_would_have_returned_the_tampered_archive(self, tmp_path):
        """Mutation guard: prove the parent really is unsafe here.

        Without this, the test above could pass against a parent that already
        verified, and the subclass would be guarding nothing.
        """
        parent = FilesystemStore(base_path=str(tmp_path / "parent"))
        first_ref = parent.save(b"version one", key="exp-4", model_class_name="Exp")
        parent.save(b"version two", key="exp-4", model_class_name="Exp")

        content_hash, _ = parent._parse_reference(first_ref)
        archive = parent._version_path(content_hash)
        with open(archive, "wb") as f:
            f.write(b"tampered archive")

        # The parent hands back bytes that do not hash to the reference.
        assert parent.load(first_ref) == b"tampered archive"

    def test_intact_archive_still_loads(self, verifying_store):
        """Verification must not break the legitimate archive fallback."""
        first_ref = verifying_store.save(b"version one", key="exp-5", model_class_name="Exp")
        verifying_store.save(b"version two", key="exp-5", model_class_name="Exp")

        assert verifying_store.load(first_ref) == b"version one"

    def test_missing_artifact_is_not_found_not_an_integrity_error(self, verifying_store):
        """Absence and corruption are different problems and read differently."""
        ref = verifying_store.save(b"gone soon", key="exp-6", model_class_name="Exp")
        _, relative = verifying_store._parse_reference(ref)
        os.unlink(os.path.join(verifying_store.base_path, relative))

        with pytest.raises(FileNotFoundError):
            verifying_store.load(ref)

    def test_exists_is_false_for_a_corrupted_artifact(self, verifying_store):
        """exists() True must still imply load() succeeds."""
        ref = verifying_store.save(b"original", key="exp-7", model_class_name="Exp")
        assert verifying_store.exists(ref) is True

        _, relative = verifying_store._parse_reference(ref)
        with open(os.path.join(verifying_store.base_path, relative), "wb") as f:
            f.write(b"tampered")

        assert verifying_store.exists(ref) is False

    def test_retention_root_is_separate_from_the_shared_content_path(self, monkeypatch, tmp_path):
        import models.verifying_artifact_store as artifact_store_module
        from models.verifying_artifact_store import VerifyingArtifactStore, _default_base_path

        monkeypatch.setenv("POPOTO_IMPROVEMENT_CONTENT_PATH", str(tmp_path / "custom"))
        assert _default_base_path() == str(tmp_path / "custom")
        assert VerifyingArtifactStore().base_path == str(tmp_path / "custom")

        monkeypatch.delenv("POPOTO_IMPROVEMENT_CONTENT_PATH")
        default = _default_base_path()
        assert VerifyingArtifactStore().base_path == default
        # Superseded off the repo checkout (#3274): an image rebuild destroys
        # the checkout, so the default must not live under it.
        checkout = os.path.dirname(os.path.dirname(os.path.abspath(artifact_store_module.__file__)))
        assert not default.startswith(checkout + os.sep)
        # Still its own root, not the shared popoto content directory.
        shared = os.environ.get(
            "POPOTO_CONTENT_PATH", os.path.join(os.path.expanduser("~"), ".popoto", "content")
        )
        assert os.path.abspath(default) != os.path.abspath(shared)
        assert os.path.basename(default) == "improvement_content"
