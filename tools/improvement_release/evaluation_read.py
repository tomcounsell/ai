"""The one reader of an evaluation's ``effect``, ``confidence_interval``, and ``notes``.

Lane 4's single writer (``tools/improvement_eval/runner.py::_write_evaluation``)
stores ``effect`` and ``confidence_interval`` as JSON strings of dicts keyed
by endpoint name (``json.dumps(ctx.effect, sort_keys=True)``), and ``notes``
as one newline-joined string. Every read in this lane goes through these
four functions, so lane 4's ``frozen-holdout/1`` rows and the comparison's
``recursive-comparison/1`` rows parse through one path, and a shape change
in lane 4 is a red test here rather than a silent misread.

Nothing here raises: ``None``, an empty string, malformed JSON, a JSON value
that is not an object, a missing endpoint, and a row whose field already
holds a parsed ``dict`` all answer ``None`` (or ``[]`` for notes).
"""

from __future__ import annotations

import json
from typing import Any

#: The notes line the comparison writes its budget accounting on.
BUDGET_PREFIX = "budget="


def _load(raw: Any) -> dict:
    """Parse ``raw`` to a dict; anything that is not a JSON object is ``{}``."""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        value = json.loads(raw)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def effect_of(evaluation: Any, endpoint: str) -> float | None:
    """The point estimate for ``endpoint``, or ``None`` when the row has none."""
    return _load(getattr(evaluation, "effect", None)).get(endpoint)


def interval_of(evaluation: Any, endpoint: str) -> dict | None:
    """The ``{lower, upper, n, raw_p_value, adjusted_p_value}`` dict for ``endpoint``."""
    value = _load(getattr(evaluation, "confidence_interval", None)).get(endpoint)
    return value if isinstance(value, dict) else None


def notes_of(evaluation: Any) -> list[str]:
    """The evaluator's notes as lines; ``[]`` when there are none."""
    notes = getattr(evaluation, "notes", None)
    return notes.split("\n") if isinstance(notes, str) and notes else []


def budget_of(evaluation: Any) -> dict | None:
    """The comparison's ``budget=<json>`` notes line parsed, or ``None``."""
    for line in notes_of(evaluation):
        if line.startswith(BUDGET_PREFIX):
            value = _load(line[len(BUDGET_PREFIX) :])
            return value or None
    return None


__all__ = ["BUDGET_PREFIX", "budget_of", "effect_of", "interval_of", "notes_of"]
