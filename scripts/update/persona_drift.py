"""Engineer persona overlay drift check.

Compares the in-repo engineer persona template against the private per-machine
vault overlay and returns a list of human-readable warnings. The check is
purely a surface — it never auto-merges, never mutates files, and never
raises (any unexpected error becomes a warning).

The default template path matches the actual file in the repo
(`config/personas/engineer.md`); the `config/personas/segments/`
directory holds universal segments only and has no `engineer.md`.

Used by `scripts/update/run.py` Step 4.10 and exercised end-to-end by
`tests/unit/test_update_persona_drift.py`.
"""

from __future__ import annotations

import difflib
from pathlib import Path

DEFAULT_TEMPLATE_REL = Path("config/personas/engineer.md")
DEFAULT_OVERLAY_PATH = Path.home() / "Desktop" / "Valor" / "personas" / "engineer.md"


def check_pm_persona_drift(
    project_dir: Path,
    *,
    template_rel: Path = DEFAULT_TEMPLATE_REL,
    overlay_path: Path | None = None,
) -> list[str]:
    """Return warnings produced by the engineer persona drift check.

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
                f"PM persona overlay drift: {diff_lines} lines differ. "
                f"Run 'diff {repo_template} {overlay}' to review."
            )
    except Exception as exc:  # noqa: BLE001 - drift check must never crash /update
        warnings.append(f"PM persona overlay drift check failed (WARNING): {exc}")
    return warnings
