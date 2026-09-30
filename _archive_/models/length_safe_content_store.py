"""Length-safe filesystem content store for popoto ``ContentField``.

Popoto's ``FilesystemStore`` (a pip dependency, ``.venv/.../popoto/stores/
filesystem.py`` -- never edited in place) derives the on-disk filename for a
model's content from the model's sorted key fields joined by ``:`` and
sanitized into a single filename component via ``_sanitize_filename``. Both
``save()`` (via ``_live_path``) and ``_live_path`` itself route through that
one method, so it is the single seam needed to cap filename length.

For ``DocumentChunk`` the key is
``chunk_id:document_doc_id:file_path:project_key``; for ``KnowledgeDocument``
it is ``doc_id:file_path:project_key``. A long vault ``file_path`` can push
the sanitized filename past the 255-byte POSIX/HFS+/APFS ``NAME_MAX``, which
makes ``chunk.save()`` fail with ``OSError: [Errno 63] File name too long``
(issue #2085). Because ``DocumentChunk.save()`` is called from a per-chunk
``try/except`` in ``tools/knowledge/indexer.py::_sync_chunks``, the failure
is swallowed and the chunk is silently dropped.

``LengthSafeFilesystemStore`` overrides ``_sanitize_filename`` to cap the
result at ``MAX_CONTENT_FILENAME_BYTES`` (UTF-8 byte length): names at or
under the budget are returned byte-identical to the parent's sanitization
(no churn for already-short keys), and names over budget are truncated to a
readable prefix plus a stable ``sha256`` digest suffix. The digest is
computed over the *full, unsanitized* key -- which always includes the
model's unique auto-key field (``chunk_id`` / ``doc_id``) -- so two distinct
records can never collide even if their long path prefixes are identical.
The function is a pure, deterministic function of the key, so re-saving the
same record overwrites its own live file rather than orphaning a new one.

Read compatibility is structural, not something this override needs to
preserve by convention: ``FilesystemStore.load()`` / ``_parse_reference``
read the relative path embedded in the ``$CF:{hash}:{relative_path}``
reference string stored in Redis -- they never re-derive the filename from
the model's key fields. So old (short-key, non-truncated) references keep
resolving unchanged, and new (long-key, truncated) references resolve via
the same mechanism.
"""

import hashlib
import os

from popoto.stores.filesystem import FilesystemStore


def _load_default_budget() -> int:
    """Resolve the filename byte budget, checked fresh on every call.

    Checks the flat ``POPOTO_MAX_CONTENT_FILENAME_BYTES`` env var first --
    this is read directly (not via the cached ``config.settings.settings``
    singleton) so tests can monkeypatch the env var *after* settings has
    already been imported/constructed elsewhere in the process and still see
    the override take effect on the next ``_sanitize_filename`` call. In
    normal operation this yields the same value the settings singleton
    already carries, since ``PerformanceSettings.model_post_init`` applies
    the same env var at process startup.

    Falls back to ``config.settings.settings.performance.
    max_content_filename_bytes`` (the centrally managed default of 200) if
    the env var is absent, and to a bare literal 200 if settings
    import/initialization fails for any reason -- this module must never
    make popoto model saves depend on the full settings stack being
    importable.
    """
    env_override = os.environ.get("POPOTO_MAX_CONTENT_FILENAME_BYTES")
    if env_override is not None:
        try:
            return int(env_override)
        except ValueError:
            pass

    try:
        from config.settings import settings as _settings

        return int(_settings.performance.max_content_filename_bytes)
    except Exception:
        return 200


# Provisional/tunable -- see config/settings.py::PerformanceSettings.
# max_content_filename_bytes for the grain-of-salt rationale (200 leaves
# headroom under the 255-byte POSIX NAME_MAX for the ".txt" extension and
# any tempfile suffix appended during atomic writes).
MAX_CONTENT_FILENAME_BYTES = _load_default_budget()


class LengthSafeFilesystemStore(FilesystemStore):
    """``FilesystemStore`` subclass that caps derived filenames to a byte budget.

    Overrides only ``_sanitize_filename`` -- the single seam both ``save()``
    and ``_live_path()`` use to turn a model's key into an on-disk filename.
    Short keys sanitize identically to the parent (byte-for-byte); keys whose
    sanitized form would exceed the budget are truncated to a readable
    prefix plus a 16-hex-char ``sha256`` digest suffix computed over the
    full original key.
    """

    def _sanitize_filename(self, name: str) -> str:
        """Sanitize ``name`` via the parent, then cap it to the byte budget.

        Args:
            name: The raw key value (e.g. ``"chunk123:doc456:/long/path:proj"``).

        Returns:
            A filesystem-safe name, UTF-8 byte length <= the configured
            budget. Names at or under budget are returned unchanged (parent
            behavior preserved exactly). Names over budget become
            ``f"{prefix}_{digest}"`` where ``digest`` is a stable 16-hex-char
            ``sha256`` prefix of the full original key (uniqueness is
            guaranteed by the key's unique auto-key component) and ``prefix``
            is a byte-safe truncation of the parent-sanitized name.

        Note:
            The parent's ``_sanitize_filename`` is a ``@staticmethod``,
            called explicitly here (not via ``super()``) as the pinning
            contract for a unit test that verifies short keys still
            round-trip identically if popoto's sanitization logic changes
            upstream.

            Truncation decodes with ``errors="ignore"``: ``str.isalnum()``
            is Unicode-aware, so a sanitized name may contain multi-byte
            characters, and a byte-slice can land mid-character. A bare
            ``.decode("utf-8")`` would raise ``UnicodeDecodeError`` in that
            case; ``errors="ignore"`` drops the partial trailing bytes
            instead.
        """
        sanitized = FilesystemStore._sanitize_filename(name)

        budget = _load_default_budget()
        sanitized_bytes = sanitized.encode("utf-8")
        if len(sanitized_bytes) <= budget:
            return sanitized

        digest = hashlib.sha256(str(name).encode("utf-8")).hexdigest()[:16]
        prefix_budget = budget - len(digest) - 1
        prefix_budget = max(prefix_budget, 0)
        prefix = sanitized_bytes[:prefix_budget].decode("utf-8", errors="ignore")
        return f"{prefix}_{digest}"


# Module-level singleton, shared by DocumentChunk.content and
# KnowledgeDocument.content. No base_path argument -- resolves the same
# default (POPOTO_CONTENT_PATH env var or ~/.popoto/content/) as popoto's
# plain "filesystem" store string, so already-saved short-key content stays
# reachable under the same content directory.
length_safe_content_store = LengthSafeFilesystemStore()
