"""Knowledge document indexer pipeline.

Processes file changes from the work-vault:
- Reads file content
- Resolves project scope
- Upserts KnowledgeDocument records
- Syncs DocumentChunk records for fine-grained search
- Creates/refreshes companion Memory records with reference pointers

Companion memories use source="knowledge" and importance=3.0, positioned
between agent observations (1.0) and human messages (6.0).
"""

import hashlib
import json
import logging
import os
from pathlib import Path

from config.models import HAIKU

logger = logging.getLogger(__name__)

# Importance level for knowledge-sourced memories
KNOWLEDGE_IMPORTANCE = 3.0

# Supported file extensions for v1 (markdown and plain text only)
SUPPORTED_EXTENSIONS = {".md", ".txt", ".markdown", ".text"}

# Word count threshold for splitting by headings
LARGE_DOC_WORD_THRESHOLD = 2000

# Max chars for summary fallback when Haiku is unavailable
SUMMARY_FALLBACK_MAX_CHARS = 500


def _is_supported_file(file_path: str) -> bool:
    """Check if a file is supported for indexing (markdown/text only)."""
    ext = os.path.splitext(file_path)[1].lower()
    return ext in SUPPORTED_EXTENSIONS


def _is_hidden_or_archived(file_path: str) -> bool:
    """Check if path contains hidden dirs/files or _archive_ dirs."""
    parts = Path(file_path).parts
    for part in parts:
        if part.startswith(".") and part != ".":
            return True
        if part.startswith("_") and part.endswith("_"):
            return True
    return False


def _make_reference(file_path: str) -> str:
    """Create a JSON reference pointer for a file."""
    return json.dumps({"tool": "read_file", "params": {"file_path": file_path}})


def _split_by_headings(content: str) -> list[tuple[str, str]]:
    """Split content by top-level (h1/h2) headings.

    Delegates to the canonical implementation in tools.knowledge.chunking.
    """
    from tools.knowledge.chunking import _split_by_headings as _chunking_split_by_headings

    return _chunking_split_by_headings(content)


def _summarize_content(content: str, file_path: str) -> str:
    """Summarize document content using Haiku, with fallback to truncation.

    Uses anthropic API directly for cheap/fast summarization.
    Falls back to first-N-chars if API call fails.
    """
    try:
        import anthropic

        client = anthropic.Anthropic()
        filename = os.path.basename(file_path)

        response = client.messages.create(
            model=HAIKU,
            max_tokens=300,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Summarize this document in 1-2 sentences. "
                        f"Focus on what it contains and why someone working on "
                        f"this project would want to read it.\n\n"
                        f"File: {filename}\n\n{content[:4000]}"
                    ),
                }
            ],
        )
        summary = response.content[0].text.strip()
        if summary:
            return summary
    except Exception as e:
        logger.debug(f"Haiku summarization failed, using fallback: {e}")

    # Fallback: first N chars
    truncated = content[:SUMMARY_FALLBACK_MAX_CHARS].strip()
    if len(content) > SUMMARY_FALLBACK_MAX_CHARS:
        truncated += "..."
    return truncated


def _sync_chunks(doc, content: str, project_key: str) -> None:
    """Sync DocumentChunk records for a KnowledgeDocument.

    Deletes all existing chunks for the document, then creates new chunks
    from the content. Each chunk gets its own embedding via EmbeddingField.

    Wrapped in try/except to maintain crash isolation -- chunk failures
    must not break document indexing.

    Args:
        doc: The parent KnowledgeDocument instance.
        content: The full document content to chunk.
        project_key: Project key for the chunks.
    """
    try:
        from models.document_chunk import DocumentChunk
        from tools.knowledge.chunking import chunk_document

        doc_id = doc.doc_id

        # Delete all existing chunks for this document
        DocumentChunk.delete_by_parent(doc_id)

        # Split content into chunks
        chunks = chunk_document(content)
        if not chunks:
            logger.debug(f"No chunks produced for document {doc_id}")
            return

        # Create new chunks
        created = 0
        for chunk_data in chunks:
            try:
                chunk = DocumentChunk(
                    document_doc_id=doc_id,
                    chunk_index=chunk_data["chunk_index"],
                    content=chunk_data["text"],
                    file_path=doc.file_path or "",
                    project_key=project_key,
                )
                chunk.save()
                created += 1
            except Exception as e:
                logger.warning(
                    f"Failed to create chunk {chunk_data['chunk_index']} for document {doc_id}: {e}"
                )

        if created > 0:
            logger.info(f"Created {created} chunks for document {doc_id}")

    except Exception as e:
        logger.warning(f"Chunk sync failed for document (non-fatal): {e}")


def _cleanup_orphan_chunks() -> int:
    """Delete DocumentChunk records whose parent KnowledgeDocument no longer exists.

    Called at the end of full_scan() to clean up orphans from partial failures.

    Returns:
        Number of orphan chunks deleted.
    """
    try:
        from models.document_chunk import DocumentChunk
        from models.knowledge_document import KnowledgeDocument

        all_chunks = DocumentChunk.query.all()
        if not all_chunks:
            return 0

        # Group chunks by parent doc_id
        chunks_by_parent: dict[str, list] = {}
        for chunk in all_chunks:
            parent_id = chunk.document_doc_id
            if parent_id not in chunks_by_parent:
                chunks_by_parent[parent_id] = []
            chunks_by_parent[parent_id].append(chunk)

        orphan_count = 0
        for parent_id, chunks in chunks_by_parent.items():
            try:
                parent = KnowledgeDocument.query.get(doc_id=parent_id)
                if parent is None:
                    # Parent doesn't exist -- these are orphans
                    for chunk in chunks:
                        try:
                            chunk.delete()
                            orphan_count += 1
                        except Exception:
                            pass
            except Exception:
                pass

        if orphan_count > 0:
            logger.info(f"Cleaned up {orphan_count} orphan chunks")
        return orphan_count

    except Exception as e:
        logger.warning(f"Orphan chunk cleanup failed (non-fatal): {e}")
        return 0


def index_file(file_path: str) -> bool:
    """Index a single file from the work-vault.

    Reads the file, resolves scope, upserts KnowledgeDocument,
    syncs DocumentChunk records, and creates/refreshes companion Memory records.

    Returns True if indexing succeeded, False otherwise.
    """
    try:
        abs_path = os.path.normpath(os.path.expanduser(file_path))

        # Skip unsupported files
        if not _is_supported_file(abs_path):
            logger.debug(f"Skipping unsupported file type: {abs_path}")
            return False

        # Skip hidden/archived files
        if _is_hidden_or_archived(abs_path):
            logger.debug(f"Skipping hidden/archived file: {abs_path}")
            return False

        if not os.path.isfile(abs_path):
            logger.debug(f"File not found: {abs_path}")
            return False

        # Read file content for hash comparison
        try:
            with open(abs_path, encoding="utf-8", errors="replace") as f:
                raw_content = f.read()
        except Exception as e:
            logger.warning(f"Failed to read file {abs_path}: {e}")
            return False

        if not raw_content.strip():
            logger.debug(f"Skipping empty file: {abs_path}")
            return False

        # Compute content hash BEFORE safe_upsert to detect changes
        content_hash = hashlib.sha256(raw_content.encode("utf-8")).hexdigest()

        # Resolve scope
        from tools.knowledge.scope_resolver import resolve_scope

        scope_result = resolve_scope(abs_path)
        if scope_result is None:
            logger.debug(f"File outside known scope, skipping: {abs_path}")
            return False

        project_key, scope = scope_result

        # Check if content has changed (for chunk sync decision)
        from models.knowledge_document import KnowledgeDocument

        existing_docs = KnowledgeDocument.query.filter(file_path=abs_path)
        content_changed = True
        if existing_docs:
            existing_doc = existing_docs[0]
            if existing_doc.content_hash == content_hash:
                content_changed = False

        # Upsert KnowledgeDocument
        doc = KnowledgeDocument.safe_upsert(abs_path, project_key, scope)
        if doc is None:
            return False

        # Sync chunks only if content changed
        if content_changed:
            _sync_chunks(doc, raw_content, project_key)

        # Create companion memories
        _create_companion_memories(abs_path, project_key, scope, doc.content or "")
        return True

    except Exception as e:
        logger.warning(f"Index failed for {file_path}: {e}")
        return False


def _create_companion_memories(file_path: str, project_key: str, scope: str, content: str) -> None:
    """Create or refresh companion Memory records for a knowledge document.

    For large documents (>2000 words), creates one memory per top-level heading.
    For small documents, creates a single memory.
    """
    from models.memory import SOURCE_KNOWLEDGE, Memory

    reference = _make_reference(file_path)
    filename = os.path.basename(file_path)

    # Clean up existing companion memories for this file
    _delete_companion_memories(file_path)

    word_count = len(content.split())

    if word_count > LARGE_DOC_WORD_THRESHOLD:
        # Split by headings and create one memory per section
        sections = _split_by_headings(content)
        for heading, section_content in sections:
            if not section_content.strip():
                continue
            section_label = f" ({heading})" if heading else ""
            summary = _summarize_content(section_content, file_path)
            memory_content = f"Knowledge doc: {filename}{section_label} - {summary}"

            Memory.safe_save(
                project_key=project_key,
                content=memory_content[:500],
                importance=KNOWLEDGE_IMPORTANCE,
                source=SOURCE_KNOWLEDGE,
                reference=reference,
                metadata={
                    "category": "pattern",
                    "tags": ["knowledge", scope],
                    "file_path": file_path,
                },
            )
    else:
        # Single memory for small documents
        summary = _summarize_content(content, file_path)
        memory_content = f"Knowledge doc: {filename} - {summary}"

        Memory.safe_save(
            project_key=project_key,
            content=memory_content[:500],
            importance=KNOWLEDGE_IMPORTANCE,
            source=SOURCE_KNOWLEDGE,
            reference=reference,
            metadata={
                "category": "pattern",
                "tags": ["knowledge", scope],
                "file_path": file_path,
            },
        )

    logger.debug(f"Created companion memories for: {file_path}")


def delete_file(file_path: str) -> bool:
    """Delete a KnowledgeDocument, its chunks, and companion memories.

    Returns True if cleanup succeeded, False otherwise.
    """
    try:
        abs_path = os.path.normpath(os.path.expanduser(file_path))

        from models.document_chunk import DocumentChunk
        from models.knowledge_document import KnowledgeDocument

        # Delete chunks before deleting the parent document
        existing = KnowledgeDocument.query.filter(file_path=abs_path)
        if existing:
            for doc in existing:
                try:
                    DocumentChunk.delete_by_parent(doc.doc_id)
                except Exception as e:
                    logger.warning(f"Chunk cleanup failed for {doc.doc_id}: {e}")

        doc_deleted = KnowledgeDocument.delete_by_path(abs_path)
        _delete_companion_memories(abs_path)

        if doc_deleted:
            logger.info(f"Deleted knowledge document, chunks, and companions: {abs_path}")
        return doc_deleted

    except Exception as e:
        logger.warning(f"Delete failed for {file_path}: {e}")
        return False


def _delete_companion_memories(file_path: str) -> None:
    """Delete companion memories that reference a specific file path."""
    try:
        from models.memory import SOURCE_KNOWLEDGE, Memory

        abs_path = os.path.normpath(os.path.expanduser(file_path))
        reference = _make_reference(abs_path)

        # Query memories with source="knowledge" and matching reference
        memories = Memory.query.filter(source=SOURCE_KNOWLEDGE)
        for mem in memories:
            if mem.reference == reference:
                try:
                    mem.delete()
                except Exception:
                    pass
    except Exception as e:
        logger.debug(f"Companion memory cleanup error (non-fatal): {e}")


def full_scan(vault_path: str | None = None) -> dict[str, int]:
    """Scan the work-vault and index all changed files.

    Compares file mtimes against KnowledgeDocument last_modified timestamps.
    Only re-indexes files that have changed since last indexing.

    Returns dict with counts: {"indexed": N, "skipped": N, "errors": N}
    """
    from tools.knowledge.scope_resolver import get_vault_path

    if vault_path is None:
        vault_path = get_vault_path()

    vault_path = os.path.expanduser(vault_path)
    if not os.path.isdir(vault_path):
        logger.warning(f"Work-vault not found: {vault_path}")
        return {"indexed": 0, "skipped": 0, "errors": 0}

    stats = {"indexed": 0, "skipped": 0, "errors": 0}

    for root, dirs, files in os.walk(vault_path):
        # Skip hidden directories
        dirs[:] = [
            d for d in dirs if not d.startswith(".") and not (d.startswith("_") and d.endswith("_"))
        ]

        for filename in files:
            file_path = os.path.join(root, filename)

            if not _is_supported_file(file_path):
                continue

            if _is_hidden_or_archived(file_path):
                continue

            try:
                # Check if file needs re-indexing
                mtime = os.path.getmtime(file_path)

                from models.knowledge_document import KnowledgeDocument

                existing = KnowledgeDocument.query.filter(file_path=file_path)
                if existing and existing[0].last_modified >= mtime:
                    stats["skipped"] += 1
                    continue

                if index_file(file_path):
                    stats["indexed"] += 1
                else:
                    stats["skipped"] += 1

            except Exception as e:
                logger.debug(f"Error scanning {file_path}: {e}")
                stats["errors"] += 1

    # Clean up orphan chunks from partial failures
    _cleanup_orphan_chunks()

    logger.info(
        f"Full scan complete: {stats['indexed']} indexed, "
        f"{stats['skipped']} skipped, {stats['errors']} errors"
    )
    return stats
