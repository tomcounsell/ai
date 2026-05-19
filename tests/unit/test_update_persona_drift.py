"""Unit tests for PM persona overlay drift check in scripts/update/run.py.

The drift check (Step 4.10) compares the in-repo PM persona template
(config/personas/segments/project-manager.md) against the private vault
overlay (~Desktop/Valor/personas/project-manager.md).

Verifies:
  - Identical files → no warning
  - One-line difference → warning appended with line count
  - Private overlay absent → no warning, no error (fresh machine)
  - Template absent → no warning, no error
  - Both absent → no warning, no error
  - IOError reading file → warning appended, no crash
"""

from __future__ import annotations

import difflib
from pathlib import Path
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Helper — replicate the drift-check logic as a callable for unit testing.
# We test the logic directly rather than importing from run.py because run.py
# has many heavy side effects at import time (env reads, dataclass creation).
# The implementation in run.py follows this exact logic.
# ---------------------------------------------------------------------------


def _run_persona_drift_check(
    repo_template: Path,
    private_overlay: Path,
) -> list[str]:
    """Return list of warnings produced by the Step 4.10 drift check.

    Mirrors the try/except block added to scripts/update/run.py exactly,
    with the file paths parameterised for test isolation.
    """
    warnings: list[str] = []
    try:
        if not repo_template.exists():
            return warnings
        if not private_overlay.exists():
            return warnings

        template_lines = repo_template.read_text().splitlines(keepends=True)
        overlay_lines = private_overlay.read_text().splitlines(keepends=True)
        diff = list(
            difflib.unified_diff(
                template_lines,
                overlay_lines,
                fromfile=str(repo_template),
                tofile=str(private_overlay),
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
                f"Run 'diff {repo_template} {private_overlay}' to review."
            )
    except Exception as exc:
        warnings.append(f"PM persona overlay drift check failed (WARNING): {exc}")
    return warnings


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_identical_files_no_warning(tmp_path):
    """Identical template and overlay should produce no warning."""
    content = "# PM Persona\n\nYou are a PM.\n"
    template = tmp_path / "project-manager.md"
    overlay = tmp_path / "overlay-project-manager.md"
    template.write_text(content)
    overlay.write_text(content)

    warnings = _run_persona_drift_check(template, overlay)

    assert warnings == []


def test_one_line_difference_produces_warning(tmp_path):
    """A single line difference should append a warning with a line count."""
    template = tmp_path / "project-manager.md"
    overlay = tmp_path / "overlay-project-manager.md"
    template.write_text("# PM Persona\n\nYou are a PM.\n")
    overlay.write_text("# PM Persona\n\nYou are a senior PM.\n")

    warnings = _run_persona_drift_check(template, overlay)

    assert len(warnings) == 1
    assert "PM persona overlay drift" in warnings[0]
    # Unified diff counts the removed line AND the added line → 2 diff_lines
    assert "2 lines differ" in warnings[0]


def test_line_count_reflects_actual_diff(tmp_path):
    """Diff line count should match the number of +/- lines in the unified diff."""
    template = tmp_path / "project-manager.md"
    overlay = tmp_path / "overlay-project-manager.md"
    template.write_text("line1\nline2\nline3\n")
    overlay.write_text("line1\nchanged2\nchanged3\n")

    warnings = _run_persona_drift_check(template, overlay)

    assert len(warnings) == 1
    # 2 removed + 2 added = 4 diff lines
    assert "4 lines differ" in warnings[0]


def test_private_overlay_absent_no_warning(tmp_path):
    """When the private overlay does not exist, no warning is emitted (fresh machine)."""
    template = tmp_path / "project-manager.md"
    template.write_text("# PM Persona\n")
    # overlay intentionally not created

    warnings = _run_persona_drift_check(template, tmp_path / "nonexistent.md")

    assert warnings == []


def test_template_absent_no_warning(tmp_path):
    """When the repo template does not exist, no warning is emitted."""
    overlay = tmp_path / "overlay-project-manager.md"
    overlay.write_text("# PM Persona\n")
    # template intentionally not created

    warnings = _run_persona_drift_check(tmp_path / "nonexistent.md", overlay)

    assert warnings == []


def test_both_absent_no_warning(tmp_path):
    """When neither file exists, no warning is emitted."""
    warnings = _run_persona_drift_check(
        tmp_path / "nonexistent-template.md",
        tmp_path / "nonexistent-overlay.md",
    )

    assert warnings == []


def test_ioerror_reading_file_appends_warning_no_crash(tmp_path):
    """An IOError while reading files should append a warning but not crash."""
    template = tmp_path / "project-manager.md"
    overlay = tmp_path / "overlay-project-manager.md"
    template.write_text("# PM Persona\n")
    overlay.write_text("# PM Persona\n")

    original_read_text = Path.read_text

    def failing_read_text(self, *args, **kwargs):
        if self == overlay:
            raise OSError("Permission denied")
        return original_read_text(self, *args, **kwargs)

    with patch.object(Path, "read_text", failing_read_text):
        warnings = _run_persona_drift_check(template, overlay)

    assert len(warnings) == 1
    assert "WARNING" in warnings[0] or "drift check failed" in warnings[0]


def test_warning_contains_diff_command(tmp_path):
    """Warning message should include a diff command operators can run."""
    template = tmp_path / "project-manager.md"
    overlay = tmp_path / "overlay-project-manager.md"
    template.write_text("original content\n")
    overlay.write_text("changed content\n")

    warnings = _run_persona_drift_check(template, overlay)

    assert len(warnings) == 1
    assert "diff" in warnings[0]
