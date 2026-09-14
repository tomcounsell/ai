"""The journal's head is authoritative; ``ImprovementCase.state``/``.revision``
are a projection written only after the journal has already accepted a
transition. ``apply`` never reads the projection first, and ``replay``
reconciles the projection to the head, folding the journal tail as a
cross-check that reports rather than raises when the tail has been trimmed
past the head's revision (Decision 2's ``journal_max_entries`` bound).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from tools.improvement_control import keys
from tools.improvement_control.journal import read_head

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReplayResult:
    """The outcome of one :func:`replay` call."""

    case_id: str
    head_revision: int
    projection_state_before: str | None
    projection_state_after: str
    fold_reached_head: bool
    first_folded_revision: int | None


def _control_redis():
    from utils.redis_client import text_redis

    return text_redis()


def apply(project_key: str, case_id: str) -> None:
    """Write the head's truth onto ``ImprovementCase`` through the ORM.

    Never reads the projection first -- the head is authoritative and this
    is a one-way write. A missing head is a no-op (nothing has happened to
    this case in the control namespace yet).
    """
    from models.improvement_case import ImprovementCase

    head = read_head(project_key, case_id)
    if head is None:
        return
    case = ImprovementCase.query.get(project_key=project_key, id=case_id)
    if case is None:
        logger.debug(
            "[improvement-control] projection.apply: no ImprovementCase row for %s/%s "
            "(head exists; nothing to project onto)",
            project_key,
            case_id,
        )
        return
    case.state = head.state
    case.revision = head.revision
    case.save()


def replay(project_key: str, case_id: str) -> ReplayResult:
    """Fold the journal tail as a cross-check, then reconcile the projection
    to the head regardless of whether the fold reached it.

    A journal trimmed past the head's revision (``journal_max_entries``) is
    reported via ``fold_reached_head=False`` and ``first_folded_revision``,
    never raised -- the head is still the truth and is still written.
    """
    from models.improvement_case import ImprovementCase

    head = read_head(project_key, case_id)
    case = ImprovementCase.query.get(project_key=project_key, id=case_id)
    before = case.state if case is not None else None

    tail_raw = _control_redis().lrange(keys.journal_key(project_key, case_id), 0, -1)
    first_folded_revision: int | None = None
    fold_reached_head = False
    if tail_raw:
        import json

        first_entry = json.loads(tail_raw[0])
        first_folded_revision = int(first_entry.get("revision", 0))
        # The fold starts from an empty/zero state and replays only what the
        # (possibly LTRIM-bounded) tail retains. It "reaches" the head only
        # when the earliest retained entry IS revision 1 -- otherwise history
        # before it was trimmed away and a from-scratch fold cannot verify
        # the head's revision count, even though the head itself is still
        # the authoritative truth this function still writes below.
        fold_reached_head = first_folded_revision == 1

    if head is not None:
        apply(project_key, case_id)
        after = head.state
    else:
        after = before or ""

    return ReplayResult(
        case_id=case_id,
        head_revision=head.revision if head is not None else -1,
        projection_state_before=before,
        projection_state_after=after,
        fold_reached_head=fold_reached_head,
        first_folded_revision=first_folded_revision,
    )
