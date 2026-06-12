"""Unit tests for PM persona overlay drift check.

Exercises the real implementation in ``scripts.update.persona_drift`` so the
production code (Step 4.10 in ``scripts/update/run.py``) is covered by these
tests, not a parallel re-implementation.

Verifies:
  - Identical files → no warning
  - One-line difference → warning appended with line count
  - Private overlay absent → no warning, no error (fresh machine)
  - Template absent → no warning, no error
  - Both absent → no warning, no error
  - IOError reading file → warning appended, no crash
  - Default repo template path resolves to the real PM persona file
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from scripts.update.persona_drift import (
    DEFAULT_TEMPLATE_REL,
    check_pm_persona_drift,
)


def _setup(
    tmp_path: Path,
    template_text: str | None,
    overlay_text: str | None,
) -> tuple[Path, Path]:
    """Create a fake project_dir with the template at the real relative path
    and an overlay file alongside. Returns (project_dir, overlay_path).
    """
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    template_path = project_dir / DEFAULT_TEMPLATE_REL
    if template_text is not None:
        template_path.parent.mkdir(parents=True, exist_ok=True)
        template_path.write_text(template_text)
    overlay_path = tmp_path / "overlay-engineer.md"
    if overlay_text is not None:
        overlay_path.write_text(overlay_text)
    return project_dir, overlay_path


def test_identical_files_no_warning(tmp_path):
    """Identical template and overlay should produce no warning."""
    content = "# PM Persona\n\nYou are a PM.\n"
    project_dir, overlay = _setup(tmp_path, content, content)

    warnings = check_pm_persona_drift(project_dir, overlay_path=overlay)

    assert warnings == []


def test_one_line_difference_produces_warning(tmp_path):
    """A single line difference should append a warning with a line count."""
    project_dir, overlay = _setup(
        tmp_path,
        "# PM Persona\n\nYou are a PM.\n",
        "# PM Persona\n\nYou are a senior PM.\n",
    )

    warnings = check_pm_persona_drift(project_dir, overlay_path=overlay)

    assert len(warnings) == 1
    assert "PM persona overlay drift" in warnings[0]
    # Unified diff counts the removed line AND the added line → 2 diff_lines
    assert "2 lines differ" in warnings[0]


def test_line_count_reflects_actual_diff(tmp_path):
    """Diff line count should match the number of +/- lines in the unified diff."""
    project_dir, overlay = _setup(
        tmp_path,
        "line1\nline2\nline3\n",
        "line1\nchanged2\nchanged3\n",
    )

    warnings = check_pm_persona_drift(project_dir, overlay_path=overlay)

    assert len(warnings) == 1
    # 2 removed + 2 added = 4 diff lines
    assert "4 lines differ" in warnings[0]


def test_private_overlay_absent_no_warning(tmp_path):
    """When the private overlay does not exist, no warning is emitted (fresh machine)."""
    project_dir, _ = _setup(tmp_path, "# PM Persona\n", None)

    warnings = check_pm_persona_drift(project_dir, overlay_path=tmp_path / "nonexistent.md")

    assert warnings == []


def test_template_absent_no_warning(tmp_path):
    """When the repo template does not exist, no warning is emitted."""
    project_dir, overlay = _setup(tmp_path, None, "# PM Persona\n")

    warnings = check_pm_persona_drift(project_dir, overlay_path=overlay)

    assert warnings == []


def test_both_absent_no_warning(tmp_path):
    """When neither file exists, no warning is emitted."""
    project_dir = tmp_path / "repo"
    project_dir.mkdir()

    warnings = check_pm_persona_drift(project_dir, overlay_path=tmp_path / "nonexistent-overlay.md")

    assert warnings == []


def test_ioerror_reading_file_appends_warning_no_crash(tmp_path):
    """An IOError while reading files should append a warning but not crash."""
    project_dir, overlay = _setup(tmp_path, "# PM Persona\n", "# PM Persona\n")

    original_read_text = Path.read_text

    def failing_read_text(self, *args, **kwargs):
        if self == overlay:
            raise OSError("Permission denied")
        return original_read_text(self, *args, **kwargs)

    with patch.object(Path, "read_text", failing_read_text):
        warnings = check_pm_persona_drift(project_dir, overlay_path=overlay)

    assert len(warnings) == 1
    assert "WARNING" in warnings[0] or "drift check failed" in warnings[0]


def test_warning_contains_diff_command(tmp_path):
    """Warning message should include a diff command operators can run."""
    project_dir, overlay = _setup(tmp_path, "original content\n", "changed content\n")

    warnings = check_pm_persona_drift(project_dir, overlay_path=overlay)

    assert len(warnings) == 1
    assert "diff" in warnings[0]


def test_default_template_path_points_at_real_file():
    """Regression test for the path bug in the original PR: the default repo
    template path must resolve to an existing file at the repo root, not a
    nonexistent `segments/engineer.md`.
    """
    repo_root = Path(__file__).resolve().parents[2]
    template_path = repo_root / DEFAULT_TEMPLATE_REL
    assert template_path.exists(), (
        f"DEFAULT_TEMPLATE_REL ({DEFAULT_TEMPLATE_REL}) does not resolve to a real file "
        f"at {template_path}. The drift check would silently no-op on every machine."
    )
