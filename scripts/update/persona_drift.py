"""Persona overlay drift check.

Compares an in-repo persona template against its private per-machine vault
overlay and returns a list of human-readable warnings. The check is purely a
surface — it never auto-merges, never mutates files, and never raises (any
unexpected error becomes a warning).

Covers two persona pairs (see ``PERSONA_OVERLAY_PAIRS``): ``engineer``
(the original check) and ``teammate`` (added for issue #2733, after a
private ``teammate.md`` stub was found silently shadowing the
repo-maintained overlay — this is the fleet-wide mechanism that surfaces
that shadow on any other machine instead of it loading silently).

Used by `scripts/update/run.py` Step 4.10 and exercised end-to-end by
`tests/unit/test_update_persona_drift.py`.
"""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import NamedTuple

DEFAULT_TEMPLATE_REL = Path("config/personas/engineer.md")
DEFAULT_OVERLAY_PATH = Path.home() / "Desktop" / "Valor" / "personas" / "engineer.md"

TEAMMATE_TEMPLATE_REL = Path("config/personas/teammate.md")
TEAMMATE_OVERLAY_PATH = Path.home() / "Desktop" / "Valor" / "personas" / "teammate.md"


class PersonaOverlayPair(NamedTuple):
    """One persona's (repo template, private overlay) pair to drift-check."""

    name: str
    template_rel: Path
    overlay_path: Path


# Every persona pair `/update` Step 4.10 checks. Add a row here to cover a
# new persona; `check_all_persona_drift` loops over this list, so covering a
# second persona is a loop, not a rewrite of the check itself.
PERSONA_OVERLAY_PAIRS: list[PersonaOverlayPair] = [
    PersonaOverlayPair("engineer", DEFAULT_TEMPLATE_REL, DEFAULT_OVERLAY_PATH),
    PersonaOverlayPair("teammate", TEAMMATE_TEMPLATE_REL, TEAMMATE_OVERLAY_PATH),
]


def check_persona_drift(
    project_dir: Path,
    *,
    template_rel: Path = DEFAULT_TEMPLATE_REL,
    overlay_path: Path | None = None,
    persona_name: str = "engineer",
) -> list[str]:
    """Return warnings produced by a persona overlay drift check.

    Parameters
    ----------
    project_dir:
        The repo root. The template path is resolved relative to this so the
        check is independent of the caller's current working directory.
    template_rel:
        Repo-relative path to the in-repo template. Defaults to
        ``config/personas/engineer.md``.
    overlay_path:
        Absolute path to the private vault overlay. Defaults to
        ``~/Desktop/Valor/personas/engineer.md``.
    persona_name:
        Human-readable label used in the warning text (e.g. ``"engineer"`` or
        ``"teammate"``), so a warning about one persona is never mistaken
        for another. Defaults to ``"engineer"`` to match this function's own
        default ``template_rel``/``overlay_path`` pair, which is the
        engineer overlay -- the same label ``PERSONA_OVERLAY_PAIRS`` uses for
        that pair.

    Returns
    -------
    list[str]
        Empty list when files are in sync or either file is absent.
        A single warning string when drift is detected or an error is
        encountered. Never raises.
    """

    warnings: list[str] = []
    overlay = overlay_path if overlay_path is not None else DEFAULT_OVERLAY_PATH
    repo_template = project_dir / template_rel

    try:
        if not repo_template.exists():
            return warnings
        if not overlay.exists():
            return warnings

        template_lines = repo_template.read_text().splitlines(keepends=True)
        overlay_lines = overlay.read_text().splitlines(keepends=True)
        diff = list(
            difflib.unified_diff(
                template_lines,
                overlay_lines,
                fromfile=str(repo_template),
                tofile=str(overlay),
            )
        )
        if diff:
            diff_lines = len(
                [
                    line
                    for line in diff
                    if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
                ]
            )
            warnings.append(
                f"{persona_name} persona overlay drift: {diff_lines} lines differ. "
                f"Run 'diff {repo_template} {overlay}' to review."
            )
    except Exception as exc:  # noqa: BLE001 - drift check must never crash /update
        warnings.append(f"{persona_name} persona overlay drift check failed (WARNING): {exc}")
    return warnings


def check_all_persona_drift(project_dir: Path) -> list[str]:
    """Run the drift check for every pair in ``PERSONA_OVERLAY_PAIRS``.

    Aggregates warnings across all covered personas into a single list so
    `/update` Step 4.10 can emit one combined report, matching the
    single-key behavior it had before a second persona was added. Never
    raises — each pair's check already contains its own failures.
    """
    warnings: list[str] = []
    for pair in PERSONA_OVERLAY_PAIRS:
        warnings.extend(
            check_persona_drift(
                project_dir,
                template_rel=pair.template_rel,
                overlay_path=pair.overlay_path,
                persona_name=pair.name,
            )
        )
    return warnings
