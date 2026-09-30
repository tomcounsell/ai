"""Row helpers shared across the release and recursion packages (#3218, lane 6).

Three small functions every module in ``tools/improvement_release`` and
``tools/improvement_recursion`` needs and none should define twice: a
timezone-aware reading of a stamp, the recency key a newest-first sort uses,
and the ``(project_key, id)`` lookup of one popoto row. No Redis is touched at
import; :func:`lookup` takes the model class from its caller.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def aware(stamp: Any) -> datetime | None:
    """``stamp`` as a timezone-aware datetime, or ``None``.

    Accepts a ``datetime`` (naive ones are read as UTC) or an ISO-8601 string
    (``Z`` accepted), the two shapes a stamp has on a row and in a JSON field.
    Anything else, including an unparsable string, is ``None``.
    """
    if isinstance(stamp, str):
        try:
            stamp = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(stamp, datetime):
        return None
    return stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=UTC)


def recency(row: Any) -> datetime:
    """Sort key on ``created_at``; a row without one sorts first."""
    return aware(getattr(row, "created_at", None)) or datetime.min.replace(tzinfo=UTC)


def lookup(model: Any, project_key: str, row_id: Any) -> Any:
    """The ``model`` row keyed ``(project_key, str(row_id))``, or ``None`` for a falsy id."""
    if not row_id:
        return None
    return model.query.filter(project_key=project_key, id=str(row_id)).first()


__all__ = ["aware", "lookup", "recency"]
