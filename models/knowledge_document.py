"""KnowledgeDocument model for the knowledge base indexing system.

Stores indexed documents from the work-vault with content and embeddings.
Each document is keyed by file path and scoped by project_key for NDA isolation.

ContentField stores document content on the filesystem (not in Redis).
EmbeddingField auto-generates embeddings via OpenAI text-embedding-3-small.
"""

import hashlib
import logging
import os

from popoto import (
    AutoKeyField,
    FloatField,
    KeyField,
    Model,
    StringField,
)
from popoto.fields.content_field import ContentField
from popoto.fields.embedding_field import EmbeddingField

logger = logging.getLogger(__name__)


class KnowledgeDocument(Model):
    """Indexed knowledge document from the work-vault.

    Fields:
        doc_id: Auto-generated unique key.
        file_path: Absolute path to the source file (unique key for upsert).
        project_key: Project partition key for NDA isolation.
        scope: "client" for project-specific docs, "company-wide" for shared docs.
        content: Document content stored on filesystem via ContentField.
        embedding: Auto-generated embedding from content via OpenAI provider.
        content_hash: SHA-256 hash of content for skip-if-unchanged optimization.
        last_modified: File mtime at time of indexing.
    """

    doc_id = AutoKeyField()
    file_path = KeyField()
    project_key = KeyField()
    scope = StringField(default="client")
    content = ContentField(store="filesystem")
    embedding = EmbeddingField(source="content")
    content_hash = StringField(default="")
    last_modified = FloatField(default=0.0)

    @classmethod
    def safe_upsert(
        cls, file_path: str, project_key: str, scope: str
    ) -> "KnowledgeDocument | None":
        """Read a file and create/update a KnowledgeDocument.

        Skips re-indexing if content hash is unchanged.
        Returns the KnowledgeDocument instance, or None on failure.
        """
        try:
            abs_path = os.path.expanduser(file_path)
            if not os.path.isfile(abs_path):
                logger.warning(f"KnowledgeDocument: file not found: {abs_path}")
                return None

            with open(abs_path, encoding="utf-8", errors="replace") as f:
                content = f.read()

            if not content.strip():
                logger.debug(f"KnowledgeDocument: skipping empty file: {abs_path}")
                return None

            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            mtime = os.path.getmtime(abs_path)

            # Truncate content to avoid exceeding OpenAI embedding token limit
            content = content[:30000]

            # Check for existing document with same content hash
            existing = cls.query.filter(file_path=abs_path)
            if existing:
                doc = existing[0]
                if doc.content_hash == content_hash:
                    logger.debug(f"KnowledgeDocument: unchanged, skipping: {abs_path}")
                    return doc
                # Update existing document
                doc.content = content
                doc.content_hash = content_hash
                doc.last_modified = mtime
                doc.project_key = project_key
                doc.scope = scope
                doc.save()
                logger.info(f"KnowledgeDocument: updated: {abs_path}")
                return doc

            # Create new document
            doc = cls(
                file_path=abs_path,
                project_key=project_key,
                scope=scope,
                content=content,
                content_hash=content_hash,
                last_modified=mtime,
            )
            doc.save()
            logger.info(f"KnowledgeDocument: created: {abs_path}")
            return doc

        except Exception as e:
            logger.warning(f"KnowledgeDocument upsert failed (non-fatal): {e}")
            return None

    @classmethod
    def delete_by_path(cls, file_path: str) -> bool:
        """Delete a KnowledgeDocument by file path.

        Returns True if a document was found and deleted, False otherwise.
        """
        try:
            abs_path = os.path.expanduser(file_path)
            existing = cls.query.filter(file_path=abs_path)
            if existing:
                for doc in existing:
                    doc.delete()
                logger.info(f"KnowledgeDocument: deleted: {abs_path}")
                return True
            return False
        except Exception as e:
            logger.warning(f"KnowledgeDocument delete failed (non-fatal): {e}")
            return False
