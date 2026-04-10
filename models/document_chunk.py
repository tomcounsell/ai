"""DocumentChunk model for chunked document retrieval.

Stores individual chunks of a KnowledgeDocument with per-chunk embeddings.
Each chunk is a segment of the parent document, enabling fine-grained
semantic search over long documents.

Chunks are managed entirely by the indexer pipeline -- when a document
is re-indexed, all old chunks are deleted and regenerated.
"""

import logging

import numpy as np
from popoto import AutoKeyField, IntField, KeyField, Model
from popoto.fields.content_field import ContentField
from popoto.fields.embedding_field import EmbeddingField

logger = logging.getLogger(__name__)


class DocumentChunk(Model):
    """Individual chunk of a KnowledgeDocument with its own embedding.

    Fields:
        chunk_id: Auto-generated unique key.
        document_doc_id: FK to parent KnowledgeDocument.doc_id.
        chunk_index: Ordering index within the parent document (0-based).
        content: Chunk text stored on filesystem via ContentField.
        embedding: Auto-generated embedding from content via OpenAI provider.
        file_path: Denormalized parent document file path (for search results).
        project_key: Denormalized project key (for filtering).
    """

    chunk_id = AutoKeyField()
    document_doc_id = KeyField()
    chunk_index = IntField(default=0)
    content = ContentField(store="filesystem")
    embedding = EmbeddingField(source="content")
    file_path = KeyField()
    project_key = KeyField()

    @classmethod
    def delete_by_parent(cls, doc_id: str) -> int:
        """Delete all chunks belonging to a parent document.

        Args:
            doc_id: The parent KnowledgeDocument.doc_id.

        Returns:
            Number of chunks deleted.
        """
        try:
            chunks = cls.query.filter(document_doc_id=doc_id)
            count = 0
            for chunk in chunks:
                try:
                    chunk.delete()
                    count += 1
                except Exception as e:
                    logger.warning(f"Failed to delete chunk {chunk.chunk_id}: {e}")
            if count > 0:
                logger.info(f"Deleted {count} chunks for document {doc_id}")
            return count
        except Exception as e:
            logger.warning(f"DocumentChunk.delete_by_parent failed for {doc_id}: {e}")
            return 0

    @classmethod
    def search(
        cls, query_text: str, project_key: str | None = None, top_k: int = 5
    ) -> list[dict]:
        """Search chunks by semantic similarity to query text.

        Embeds the query via the configured OpenAI provider, loads all chunk
        embeddings, computes cosine similarity, and returns the top-K matches.

        Args:
            query_text: The search query string.
            project_key: Optional project key filter.
            top_k: Maximum number of results to return.

        Returns:
            List of dicts with keys: chunk_text, file_path, chunk_index, score, project_key.
        """
        try:
            # Embed the query
            from popoto.fields.embedding_field import OpenAIProvider

            provider = OpenAIProvider()
            query_embedding = provider.embed(query_text)
            if query_embedding is None:
                logger.warning("DocumentChunk.search: failed to embed query")
                return []

            query_vec = np.array(query_embedding, dtype=np.float32)

            # Load all chunk embeddings
            embeddings_dict = EmbeddingField.load_embeddings(cls)
            if not embeddings_dict:
                return []

            # Score each chunk
            results = []
            for chunk_id, embedding_vec in embeddings_dict.items():
                try:
                    chunk = cls.query.get(chunk_id=chunk_id)
                    if chunk is None:
                        continue

                    # Filter by project_key if specified
                    if project_key and chunk.project_key != project_key:
                        continue

                    # Cosine similarity
                    emb = np.array(embedding_vec, dtype=np.float32)
                    dot = np.dot(query_vec, emb)
                    norm_q = np.linalg.norm(query_vec)
                    norm_e = np.linalg.norm(emb)
                    if norm_q == 0 or norm_e == 0:
                        continue
                    score = float(dot / (norm_q * norm_e))

                    results.append(
                        {
                            "chunk_text": chunk.content or "",
                            "file_path": chunk.file_path or "",
                            "chunk_index": chunk.chunk_index or 0,
                            "score": score,
                            "project_key": chunk.project_key or "",
                        }
                    )
                except Exception as e:
                    logger.debug(f"Error scoring chunk {chunk_id}: {e}")
                    continue

            # Sort by score descending, return top-K
            results.sort(key=lambda x: x["score"], reverse=True)
            return results[:top_k]

        except Exception as e:
            logger.warning(f"DocumentChunk.search failed: {e}")
            return []
