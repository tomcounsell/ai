"""Unit tests for the persona overlay drift check (engineer + teammate).

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
  - Default repo template path resolves to the real engineer persona file
  - The teammate pair (added for issue #2733) gets the same coverage, plus
    ``check_all_persona_drift`` aggregation across both pairs
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts.update import run as run_module
from scripts.update.persona_drift import (
    DEFAULT_TEMPLATE_REL,
    PERSONA_OVERLAY_PAIRS,
    TEAMMATE_TEMPLATE_REL,
    PersonaOverlayPair,
    check_all_persona_drift,
    check_persona_drift,
)


def _setup(
    tmp_path: Path,
    template_text: str | None,
    overlay_text: str | None,
    *,
    template_rel: Path = DEFAULT_TEMPLATE_REL,
) -> tuple[Path, Path]:
    """Create a fake project_dir with the template at the given relative path
    and an overlay file alongside. Returns (project_dir, overlay_path).
    """
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    template_path = project_dir / template_rel
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

    warnings = check_persona_drift(project_dir, overlay_path=overlay)

    assert warnings == []


def test_one_line_difference_produces_warning(tmp_path):
    """A single line difference should append a warning with a line count."""
    project_dir, overlay = _setup(
        tmp_path,
        "# PM Persona\n\nYou are a PM.\n",
        "# PM Persona\n\nYou are a senior PM.\n",
    )

    warnings = check_persona_drift(project_dir, overlay_path=overlay)

    assert len(warnings) == 1
    assert "engineer persona overlay drift" in warnings[0]
    # Unified diff counts the removed line AND the added line → 2 diff_lines
    assert "2 lines differ" in warnings[0]


def test_line_count_reflects_actual_diff(tmp_path):
    """Diff line count should match the number of +/- lines in the unified diff."""
    project_dir, overlay = _setup(
        tmp_path,
        "line1\nline2\nline3\n",
        "line1\nchanged2\nchanged3\n",
    )

    warnings = check_persona_drift(project_dir, overlay_path=overlay)

    assert len(warnings) == 1
    # 2 removed + 2 added = 4 diff lines
    assert "4 lines differ" in warnings[0]


def test_private_overlay_absent_no_warning(tmp_path):
    """When the private overlay does not exist, no warning is emitted (fresh machine)."""
    project_dir, _ = _setup(tmp_path, "# PM Persona\n", None)

    warnings = check_persona_drift(project_dir, overlay_path=tmp_path / "nonexistent.md")

    assert warnings == []


def test_template_absent_no_warning(tmp_path):
    """When the repo template does not exist, no warning is emitted."""
    project_dir, overlay = _setup(tmp_path, None, "# PM Persona\n")

    warnings = check_persona_drift(project_dir, overlay_path=overlay)

    assert warnings == []


def test_both_absent_no_warning(tmp_path):
    """When neither file exists, no warning is emitted."""
    project_dir = tmp_path / "repo"
    project_dir.mkdir()

    warnings = check_persona_drift(project_dir, overlay_path=tmp_path / "nonexistent-overlay.md")

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
        warnings = check_persona_drift(project_dir, overlay_path=overlay)

    assert len(warnings) == 1
    assert "WARNING" in warnings[0] or "drift check failed" in warnings[0]


def test_warning_contains_diff_command(tmp_path):
    """Warning message should include a diff command operators can run."""
    project_dir, overlay = _setup(tmp_path, "original content\n", "changed content\n")

    warnings = check_persona_drift(project_dir, overlay_path=overlay)

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


# === Teammate pair (issue #2733) ================================================
#
# Added after a private ~/Desktop/Valor/personas/teammate.md stub was found
# silently shadowing the repo-maintained overlay. This is the fleet-wide
# mechanism: it surfaces that shadow on any other machine that still has one.


def test_teammate_template_path_points_at_real_file():
    """The teammate template path resolves to a real file, same regression
    class as test_default_template_path_points_at_real_file above."""
    repo_root = Path(__file__).resolve().parents[2]
    template_path = repo_root / TEAMMATE_TEMPLATE_REL
    assert template_path.exists(), (
        f"TEAMMATE_TEMPLATE_REL ({TEAMMATE_TEMPLATE_REL}) does not resolve to a real "
        f"file at {template_path}. The drift check would silently no-op."
    )


def test_teammate_overlay_absent_no_warning(tmp_path):
    """Absent-overlay case: a fresh machine with no private teammate.md gets
    no warning (not "the overlay drifted to nothing")."""
    project_dir, _ = _setup(
        tmp_path, "# Teammate Persona\n", None, template_rel=TEAMMATE_TEMPLATE_REL
    )

    warnings = check_persona_drift(
        project_dir,
        template_rel=TEAMMATE_TEMPLATE_REL,
        overlay_path=tmp_path / "nonexistent-teammate.md",
        persona_name="teammate",
    )

    assert warnings == []


def test_teammate_overlay_drift_produces_labeled_warning(tmp_path):
    """Drifted-overlay case: a shadowing private teammate.md that differs
    from the repo copy produces exactly one warning, labeled 'teammate' so
    it is never mistaken for the engineer/PM warning."""
    project_dir, overlay = _setup(
        tmp_path,
        "# Teammate Persona\n\nCasual and friendly.\n",
        "# Teammate Persona\n\nCasual and friendly, but a 9-line stub.\n",
        template_rel=TEAMMATE_TEMPLATE_REL,
    )

    warnings = check_persona_drift(
        project_dir,
        template_rel=TEAMMATE_TEMPLATE_REL,
        overlay_path=overlay,
        persona_name="teammate",
    )

    assert len(warnings) == 1
    assert "teammate persona overlay drift" in warnings[0]
    assert "PM persona" not in warnings[0]


def test_teammate_template_unreadable_warns_not_raises(tmp_path):
    """An unreadable teammate template produces a warning, never an
    exception -- the never-raise contract must hold per-pair."""
    project_dir, overlay = _setup(
        tmp_path,
        "# Teammate Persona\n",
        "# Teammate Persona\n",
        template_rel=TEAMMATE_TEMPLATE_REL,
    )
    template_path = project_dir / TEAMMATE_TEMPLATE_REL

    original_read_text = Path.read_text

    def failing_read_text(self, *args, **kwargs):
        if self == template_path:
            raise OSError("Permission denied")
        return original_read_text(self, *args, **kwargs)

    with patch.object(Path, "read_text", failing_read_text):
        warnings = check_persona_drift(
            project_dir,
            template_rel=TEAMMATE_TEMPLATE_REL,
            overlay_path=overlay,
            persona_name="teammate",
        )

    assert len(warnings) == 1
    assert "WARNING" in warnings[0] or "drift check failed" in warnings[0]


def test_persona_overlay_pairs_covers_engineer_and_teammate():
    """PERSONA_OVERLAY_PAIRS is the single source `/update` Step 4.10 loops
    over -- pin its membership so a future pair addition is deliberate."""
    names = {pair.name for pair in PERSONA_OVERLAY_PAIRS}
    assert names == {"engineer", "teammate"}


def test_check_all_persona_drift_aggregates_both_pairs(tmp_path):
    """check_all_persona_drift runs every pair and aggregates warnings --
    both an engineer-side and a teammate-side drift produce two labeled
    warnings, not one clobbering the other."""
    project_dir = tmp_path / "repo"
    project_dir.mkdir()

    engineer_template = project_dir / DEFAULT_TEMPLATE_REL
    engineer_template.parent.mkdir(parents=True, exist_ok=True)
    engineer_template.write_text("# Engineer\noriginal\n")
    engineer_overlay = tmp_path / "overlay-engineer.md"
    engineer_overlay.write_text("# Engineer\nchanged\n")

    teammate_template = project_dir / TEAMMATE_TEMPLATE_REL
    teammate_template.write_text("# Teammate\noriginal\n")
    teammate_overlay = tmp_path / "overlay-teammate.md"
    teammate_overlay.write_text("# Teammate\nchanged\n")

    with patch(
        "scripts.update.persona_drift.PERSONA_OVERLAY_PAIRS",
        [
            PersonaOverlayPair("engineer", DEFAULT_TEMPLATE_REL, engineer_overlay),
            PersonaOverlayPair("teammate", TEAMMATE_TEMPLATE_REL, teammate_overlay),
        ],
    ):
        warnings = check_all_persona_drift(project_dir)

    assert len(warnings) == 2
    assert any("engineer persona overlay drift" in w for w in warnings)
    assert any("teammate persona overlay drift" in w for w in warnings)


# === Wiring: does run_update actually call check_all_persona_drift? ============
#
# do-pr-review Tech Debt 2 (#2733 PATCH round): every test above exercises the
# real persona_drift functions directly, but none of them observes what
# scripts/update/run.py:2187 actually calls. Mutation-proven by the reviewer:
# reverting that line to `persona_drift.check_pm_persona_drift(project_dir)`
# (the pre-#2733 single-persona call) left every test in this file green,
# because none of them import or execute run.py's Step 4.10 statement.
#
# The block is lifted out of run_update by AST -- following
# test_update_human_gated_routing.py's idiom -- and executed against a mock
# persona_drift module, so a caller that reverts to the old function is
# observed directly rather than inferred from a string match in the source.


def _persona_wiring_statement() -> ast.stmt:
    """The `_persona_warnings = persona_drift.check_all_persona_drift(...)`
    statement inside `run_update`.

    Located by its assignment target (the name `_persona_warnings`), not by
    line number or by matching the literal call text -- so this test still
    finds the statement, and fails, if a mutation swaps which function on
    `persona_drift` gets called.
    """
    source = Path(run_module.__file__).read_text()
    tree = ast.parse(source)
    fn = next(
        (n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "run_update"),
        None,
    )
    assert fn is not None, "run_update not found as a top-level sync def in run.py"
    for node in ast.walk(fn):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "_persona_warnings"
        ):
            return node
    raise AssertionError("`_persona_warnings = ...` assignment not found in run_update")


class TestPersonaDriftWiring:
    def test_run_update_calls_check_all_persona_drift(self, tmp_path):
        """The Step 4.10 wiring line calls the fleet-wide aggregator, not the
        single-pair function directly.

        Mutation check performed by hand while writing this test: reverting
        `scripts/update/run.py`'s statement to
        `persona_drift.check_pm_persona_drift(project_dir)` (the call this
        function replaced) or to `persona_drift.check_persona_drift(project_dir)`
        (today's single-pair function, called directly instead of through the
        aggregator) both make `check_all_persona_drift.assert_called_once_with`
        below fail -- this test does not pass for the wrong reason.
        """
        stmt = _persona_wiring_statement()

        mock_persona_drift = MagicMock()
        mock_persona_drift.check_all_persona_drift.return_value = []
        # Also stubbed so a mutated call site (calling the single-pair
        # function directly) executes without AttributeError instead of
        # masking itself as an unrelated failure.
        mock_persona_drift.check_persona_drift.return_value = []

        namespace = dict(vars(run_module))
        namespace["persona_drift"] = mock_persona_drift
        namespace["project_dir"] = tmp_path

        exec(compile(ast.Module(body=[stmt], type_ignores=[]), "<block>", "exec"), namespace)

        mock_persona_drift.check_all_persona_drift.assert_called_once_with(tmp_path)
        mock_persona_drift.check_persona_drift.assert_not_called()
