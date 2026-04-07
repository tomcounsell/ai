"""Knowledge document indexer pipeline.

Processes file changes from the work-vault:
- Reads file content
- Resolves project scope
- Upserts KnowledgeDocument records
- Creates/refreshes companion Memory records with reference pointers

Companion memories use source="knowledge" and importance=3.0, positioned
between agent observations (1.0) and human messages (6.0).
"""

import json
import logging
import os
import re
from pathlib import Path

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

    Returns list of (heading, section_content) tuples.
    If no headings found, returns single entry with empty heading.
    """
    # Split on lines starting with # or ##
    sections = []
    current_heading = ""
    current_lines = []

    for line in content.split("\n"):
        if re.match(r"^#{1,2}\s+", line):
            # Save previous section if it has content
            if current_lines:
                section_text = "\n".join(current_lines).strip()
                if section_text:
                    sections.append((current_heading, section_text))
            current_heading = line.strip()
            current_lines = []
        else:
            current_lines.append(line)

    # Save last section
    if current_lines:
        section_text = "\n".join(current_lines).strip()
        if section_text:
            sections.append((current_heading, section_text))

    if not sections:
        return [("", content.strip())]

    return sections


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
            model="claude-haiku-4-20250414",
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


def index_file(file_path: str) -> bool:
    """Index a single file from the work-vault.

    Reads the file, resolves scope, upserts KnowledgeDocument,
    and creates/refreshes companion Memory records.

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

        # Resolve scope
        from tools.knowledge.scope_resolver import resolve_scope

        scope_result = resolve_scope(abs_path)
        if scope_result is None:
            logger.debug(f"File outside known scope, skipping: {abs_path}")
            return False

        project_key, scope = scope_result

        # Upsert KnowledgeDocument
        from models.knowledge_document import KnowledgeDocument

        doc = KnowledgeDocument.safe_upsert(abs_path, project_key, scope)
        if doc is None:
            return False

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
    """Delete a KnowledgeDocument and its companion memories.

    Returns True if cleanup succeeded, False otherwise.
    """
    try:
        abs_path = os.path.normpath(os.path.expanduser(file_path))

        from models.knowledge_document import KnowledgeDocument

        doc_deleted = KnowledgeDocument.delete_by_path(abs_path)
        _delete_companion_memories(abs_path)

        if doc_deleted:
            logger.info(f"Deleted knowledge document and companions: {abs_path}")
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

    logger.info(
        f"Full scan complete: {stats['indexed']} indexed, "
        f"{stats['skipped']} skipped, {stats['errors']} errors"
    )
    return stats
