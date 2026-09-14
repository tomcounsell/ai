"""Candidate-surface denylist (#3218, lane 6).

A release proposal names the repo-relative paths a candidate changes. Some
paths are never a candidate's to change: the charter and its model (charter
§12 makes Tom its only author), the identity file, this package (the loop
cannot rewrite its own release gate), the secrets files, and the git hooks
that enforce the pipeline. Identity and persona files beyond
``config/identity.json`` live in the private vault outside the repo, which a
repo-relative surface cannot name.

Every surface is normalized with :func:`posixpath.normpath` before it is
compared, and a surface that is absolute, escapes the repo, or carries a glob
character is refused outright: a denylist that
``./docs/../docs/improvement-charter.md`` walks around is not a denylist.

Pure functions, no Redis, no filesystem.
"""

from __future__ import annotations

import posixpath
from collections.abc import Iterable

#: Normalized repo-relative prefixes. An entry ending in ``/`` denies the
#: directory and everything under it; any other entry denies that one path.
CANDIDATE_SURFACE_DENYLIST: tuple[str, ...] = (
    "docs/improvement-charter.md",
    "models/improvement_charter.py",
    "config/identity.json",
    "tools/improvement_release/",
    ".env",
    ".env.example",
    ".githooks/",
)

_GLOB_CHARACTERS = frozenset("*?[")


class SurfaceDenied(ValueError):  # noqa: N818 -- plan-mandated name (#3218)
    """At least one proposed surface is on the denylist.

    ``surfaces`` carries the offenders as given, in input order.
    """

    def __init__(self, surfaces: list[str]):
        self.surfaces = list(surfaces)
        super().__init__(f"surface denied: {', '.join(self.surfaces)}")


class InvalidSurface(SurfaceDenied):
    """A surface that cannot be checked is refused as if it were denied.

    Raised for an absolute path, a path that escapes the repo (``..`` after
    normalization), a glob pattern, or an empty surface. A subclass of
    :class:`SurfaceDenied` so a caller that refuses denied surfaces refuses
    these with the same handling; the ``reason`` says which rule fired.
    """

    def __init__(self, surface: str, reason: str):
        self.surfaces = [surface]
        self.reason = reason
        ValueError.__init__(self, f"invalid surface {surface!r}: {reason}")


def normalize_surface(surface: str) -> str:
    """Return the normalized repo-relative form of ``surface``.

    Raises :class:`InvalidSurface` when the surface is absolute, escapes the
    repo, contains a glob character, or names nothing.
    """
    if not isinstance(surface, str) or not surface:
        raise InvalidSurface(str(surface), "empty surface")
    if _GLOB_CHARACTERS & set(surface):
        raise InvalidSurface(surface, "glob characters are not a surface")
    if posixpath.isabs(surface):
        raise InvalidSurface(surface, "absolute path; surfaces are repo-relative")
    normalized = posixpath.normpath(surface)
    if normalized == "." or not normalized:
        raise InvalidSurface(surface, "names the repo root, not a surface")
    if normalized == ".." or normalized.startswith("../"):
        raise InvalidSurface(surface, "escapes the repo")
    return normalized


def _is_denied(normalized: str) -> bool:
    for entry in CANDIDATE_SURFACE_DENYLIST:
        if entry.endswith("/"):
            if normalized == entry.rstrip("/") or normalized.startswith(entry):
                return True
        elif normalized == entry:
            return True
    return False


def denied_surfaces(surfaces: Iterable[str]) -> list[str]:
    """Return the surfaces on the denylist, as given, in input order.

    Raises :class:`InvalidSurface` on the first surface that cannot be
    normalized; an uncheckable surface is refused before any answer is given.
    """
    return [surface for surface in surfaces if _is_denied(normalize_surface(surface))]


def refuse_denied(surfaces: Iterable[str]) -> None:
    """Raise :class:`SurfaceDenied` when any surface is on the denylist."""
    offenders = denied_surfaces(list(surfaces))
    if offenders:
        raise SurfaceDenied(offenders)
