"""Shared decode seam for ContentField values on query-loaded popoto rows.

Popoto's lazy-field path (``Model.__getattribute__`` for rows loaded via
``.get()`` / ``.filter()`` / ``.all()``) decodes the raw msgpack value from
Redis and returns it directly. For a ``ContentField`` that stored value IS
the ``$CF:{hash}:{relpath}`` reference string — the descriptor decode
(``ContentField.__get__`` → ``store.load()``) never runs, so consumers see
the reference instead of the text (issue #2112).

``decoded_content`` is the single repo-level seam that fixes this: it
detects a ``$CF:`` reference and loads the real bytes through the field's
own store. It is deliberately a plain function (NOT a model mixin, property,
or popoto monkeypatch — popoto's metaclass treats class attributes as
potential fields, and attribute interception on lazy instances is fragile).

A unit test in ``tests/unit/test_content_decode.py`` doubles as a canary:
it pins the upstream bypass by asserting a query-loaded row's raw
``.content`` still starts with ``$CF:``. If a future popoto release fixes
the lazy path, that assertion fails loudly and this helper can be retired
deliberately.
"""

import logging

logger = logging.getLogger(__name__)

_CONTENT_REFERENCE_PREFIX = "$CF:"


def decoded_content(instance) -> str:
    """Return the decoded ``content`` text for a popoto model instance.

    Safe to call unconditionally at every consumer — no "is this row lazy?"
    branching needed anywhere:

    - ``None`` or empty content → ``""``.
    - A ``$CF:{hash}:{relpath}`` reference string (the raw value surfaced by
      popoto's lazy-field bypass on query-loaded rows) → real bytes loaded
      via ``type(instance)._meta.fields["content"].store`` and decoded as
      UTF-8.
    - Any other value (fresh instances, plain strings) passes through
      unchanged.

    Exception policy: this helper is the isolation boundary. ANY exception
    during reference decode (``FileNotFoundError`` for a dangling reference,
    ``ValueError`` for a malformed one, ``UnicodeDecodeError`` for a
    corrupted content file, ...) is logged as a warning and swallowed,
    returning ``""``. Callers' per-record scan loops (doctor zero-chunk
    check, ``index_file``) have no inner try/except, so a single corrupted
    record must never abort a whole scan.

    Args:
        instance: A popoto model instance with a ``content`` ContentField
            (e.g. ``DocumentChunk``, ``KnowledgeDocument``).

    Returns:
        The decoded content text, or ``""`` on empty/undecodable content.
    """
    raw = instance.content
    if not raw:
        return ""
    if isinstance(raw, str) and raw.startswith(_CONTENT_REFERENCE_PREFIX):
        try:
            store = type(instance)._meta.fields["content"].store
            return store.load(raw).decode("utf-8")
        except Exception as e:
            logger.warning(
                f"decoded_content: failed to decode content reference "
                f"{raw[:64]!r} for {type(instance).__name__}: {e}"
            )
            return ""
    return raw
