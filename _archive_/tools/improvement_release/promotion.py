"""Promotion gate (#3218, lane 6): automated promotion is disabled.

The parent plan (``docs/plans/recursive-self-improvement.md``) names two
preconditions for a release to be promoted without a human, and this module
is where they are checked rather than merely described. Every answer comes
from this module's constants and from the pinned charter row. Nothing else
reads into it: no ``config.settings`` field, no environment key, no file
flag. A builder who wants promotion enabled changes the world, not a switch.

**Precondition 1, ``credential_separation``: unmet by construction.** The
event that would meet it is a separate process identity for evaluation, with
candidate execution unable to read evaluator secrets or production
credentials. No module here can observe that property of the execution
environment, no attestation record exists for it, and a worktree is not a
security boundary. Until an attestation exists, this precondition is a
constant ``False`` and :func:`promotion_gate` reports it so.

**Precondition 2, ``charter_names_reversible_surfaces``: checkable.** A
human-amended charter (charter §12: Tom is its only author) has to name the
reversible surfaces. The gate reads the pinned charter's ``text`` through
the verifying store and looks for a section heading of depth one to three
matching ``reversible surfaces``, case-insensitive. Charter v2 carries no
such heading, so it is unmet against the current file.

``approve()`` in ``lifecycle.py`` stores the gate's answer on the release so
the record shows what was true when a human approved it.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from models.improvement_charter import ImprovementCharter
from tools.improvement_eval.runner import read_content

PRECONDITION_CREDENTIAL_SEPARATION = "credential_separation"
PRECONDITION_CHARTER_REVERSIBLE_SURFACES = "charter_names_reversible_surfaces"

#: Every precondition the gate reports on, in the order it reports them.
PRECONDITIONS: tuple[str, ...] = (
    PRECONDITION_CREDENTIAL_SEPARATION,
    PRECONDITION_CHARTER_REVERSIBLE_SURFACES,
)

_REVERSIBLE_SURFACES_HEADING = re.compile(
    r"^#{1,3}\s+.*reversible surfaces", re.IGNORECASE | re.MULTILINE
)

_CREDENTIAL_SEPARATION_REASON = (
    "no attestation exists that evaluator secrets and production credentials "
    "are separated from candidate execution; the event that would change this "
    "is a separate process identity for evaluation, with candidate execution "
    "unable to read evaluator secrets or production credentials. A worktree is "
    "not a security boundary."
)


@dataclass(frozen=True)
class PromotionGate:
    """The gate's answer for one project at one moment.

    ``automated`` is always ``False`` in this build because precondition 1 is
    unmet by construction. ``unmet`` names every unmet precondition in the
    order of :data:`PRECONDITIONS`; ``detail`` explains each one.
    """

    automated: bool
    unmet: tuple[str, ...]
    detail: dict

    def as_dict(self) -> dict:
        """A JSON-ready copy, the shape ``ImprovementRelease.promotion_gate`` stores."""
        payload = asdict(self)
        payload["unmet"] = list(self.unmet)
        return payload


class PromotionDisabled(RuntimeError):  # noqa: N818 -- plan-mandated name (#3218)
    """Raised by :func:`promote_automatically` on every call.

    ``gate`` carries the full answer; the message names the release and every
    unmet precondition.
    """

    def __init__(self, release_id: str, gate: PromotionGate):
        self.release_id = release_id
        self.gate = gate
        unmet = ", ".join(gate.unmet) or "none named"
        super().__init__(
            f"automated promotion is disabled for release {release_id}: "
            f"unmet preconditions: {unmet}"
        )


def charter_names_reversible_surfaces(text: str) -> bool:
    """Whether ``text`` carries a depth-1..3 heading naming reversible surfaces."""
    return _REVERSIBLE_SURFACES_HEADING.search(text or "") is not None


def _charter_detail(project_key: str) -> dict:
    charter = ImprovementCharter.pinned(project_key)
    if charter is None:
        return {
            "met": False,
            "charter_digest": None,
            "charter_version": None,
            "heading": None,
            "reason": f"no charter is pinned for project {project_key!r}",
        }
    text = read_content(charter, "text") or ""
    match = _REVERSIBLE_SURFACES_HEADING.search(text)
    return {
        "met": match is not None,
        "charter_digest": getattr(charter, "digest", None),
        "charter_version": getattr(charter, "version", None),
        "heading": match.group(0).strip() if match else None,
        "reason": (
            "the pinned charter names its reversible surfaces"
            if match
            else "the pinned charter has no section heading naming reversible surfaces"
        ),
    }


def promotion_gate(project_key: str = "valor") -> PromotionGate:
    """Answer whether automated promotion is permitted for ``project_key``.

    Reads the pinned charter row and nothing else. Never writes.
    """
    detail = {
        PRECONDITION_CREDENTIAL_SEPARATION: {
            "met": False,
            "reason": _CREDENTIAL_SEPARATION_REASON,
        },
        PRECONDITION_CHARTER_REVERSIBLE_SURFACES: _charter_detail(project_key),
    }
    unmet = tuple(name for name in PRECONDITIONS if not detail[name]["met"])
    return PromotionGate(automated=not unmet, unmet=unmet, detail=detail)


def promote_automatically(release_id: str, *, project_key: str = "valor") -> None:
    """Raise :class:`PromotionDisabled` naming every unmet precondition.

    Never writes. There is no promotion implementation behind this function;
    it exists so the refusal is a code path with a test rather than a sentence
    in a docstring, and so a caller reads which preconditions stand between
    the release and an automated promotion.
    """
    raise PromotionDisabled(release_id, promotion_gate(project_key))
