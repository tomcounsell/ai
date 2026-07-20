"""Unit tests for models/content_decode.py (issue #2112).

Covers the shared ``decoded_content`` seam that fixes popoto's lazy-field
bypass: query-loaded rows (``.get()`` / ``.filter()`` / ``.all()``) surface
the raw ``$CF:{hash}:{relpath}`` reference string instead of the decoded
ContentField text.

The round-trip test doubles as a CANARY: it asserts the raw ``.content`` of a
query-loaded row still starts with ``$CF:`` (pinning the upstream popoto
bypass). If a future popoto release routes lazy ContentField reads through
the store, that assertion fails loudly and the helper can be retired
deliberately.

Uses real Redis via the ORM. All test rows use a recognizable ``test-``
project_key and are cleaned up with ORM deletes only (never raw Redis).
"""

import logging

import pytest
from models.content_decode import decoded_content

TEST_PROJECT_KEY = "test-content-decode"


class _Plain:
    """Minimal stand-in exposing only a ``.content`` attribute.

    Used for passthrough cases where the helper must never touch
    ``_meta`` (no ``$CF:`` prefix means no store access).
    """

    def __init__(self, content):
        self.content = content


@pytest.fixture
def no_auto_embed(monkeypatch):
    """Disable EmbeddingField auto-embedding on save (no network in unit tests)."""
    from popoto.fields.embedding_field import EmbeddingField

    monkeypatch.setattr(
        EmbeddingField,
        "on_save",
        classmethod(
            lambda cls, model_instance, field_name, field_value, pipeline=None, **kw: pipeline
        ),
    )


@pytest.fixture
def saved_doc(no_auto_embed):
    """A real KnowledgeDocument saved to Redis, deleted after the test."""
    from models.knowledge_document import KnowledgeDocument

    doc = KnowledgeDocument(
        file_path=f"/tmp/{TEST_PROJECT_KEY}/canary.md",
        project_key=TEST_PROJECT_KEY,
        scope="client",
        content="The canary chunk text that must round-trip through the store.",
    )
    doc.save()
    try:
        yield doc
    finally:
        for row in KnowledgeDocument.query.filter(project_key=TEST_PROJECT_KEY):
            row.delete()


@pytest.mark.unit
@pytest.mark.models
class TestDecodedContentCanaryRoundTrip:
    """Real save + query-loaded reload against Redis."""

    def test_query_loaded_row_raw_is_reference_and_helper_decodes(self, saved_doc):
        """CANARY: raw .content on a query.get() row is a $CF: reference,
        and decoded_content() returns the original text.

        If the first assertion ever fails, popoto fixed the lazy bypass
        upstream — retire the helper deliberately (see plan #2112).
        """
        from models.knowledge_document import KnowledgeDocument

        reloaded = KnowledgeDocument.query.get(doc_id=saved_doc.doc_id)
        assert reloaded is not None

        raw = reloaded.content
        assert isinstance(raw, str) and raw.startswith("$CF:"), (
            "popoto lazy-field bypass no longer returns a $CF: reference — "
            "upstream may have fixed it; retire decoded_content deliberately"
        )
        assert (
            decoded_content(reloaded)
            == "The canary chunk text that must round-trip through the store."
        )


@pytest.mark.unit
@pytest.mark.models
class TestDecodedContentPassthrough:
    """Non-reference values pass through unchanged; empty inputs yield ''."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (None, ""),
            ("", ""),
            ("plain chunk text, not a reference", "plain chunk text, not a reference"),
        ],
    )
    def test_passthrough(self, value, expected):
        assert decoded_content(_Plain(value)) == expected


@pytest.mark.unit
@pytest.mark.models
class TestDecodedContentFailurePaths:
    """The helper is the isolation boundary: it never raises."""

    def test_missing_content_file_returns_empty_and_warns(self, saved_doc, caplog):
        """A dangling $CF: reference (content file deleted) -> '' + warning."""
        import os

        from models.knowledge_document import KnowledgeDocument

        reloaded = KnowledgeDocument.query.get(doc_id=saved_doc.doc_id)
        ref = reloaded.content
        assert ref.startswith("$CF:")

        # Delete the underlying content files (live + version archive) so
        # store.load raises FileNotFoundError.
        store = KnowledgeDocument._meta.fields["content"].store
        content_hash, relative_path = store._parse_reference(ref)
        for path in (
            os.path.join(store.base_path, relative_path),
            store._version_path(content_hash),
        ):
            if os.path.exists(path):
                os.remove(path)

        with caplog.at_level(logging.WARNING, logger="models.content_decode"):
            assert decoded_content(reloaded) == ""
        assert any("decoded_content" in r.message for r in caplog.records)

    def test_malformed_reference_returns_empty_and_warns(self, caplog):
        """A malformed $CF: reference (store raises non-FileNotFoundError) -> '' + warning.

        Proves a corrupted record cannot abort a caller's whole-scan loop
        (e.g. the doctor zero-chunk check has no per-doc try/except).
        """
        from models.knowledge_document import KnowledgeDocument

        class _MalformedRef:
            """Fake model carrying the REAL content-field store via _meta.

            store.load("$CF:not-a-valid-reference") raises ValueError from
            _parse_reference (missing the second ':' separator).
            """

            _meta = KnowledgeDocument._meta
            content = "$CF:not-a-valid-reference"

        with caplog.at_level(logging.WARNING, logger="models.content_decode"):
            assert decoded_content(_MalformedRef()) == ""
        assert any("decoded_content" in r.message for r in caplog.records)
