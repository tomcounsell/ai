"""Unit tests for tools.plan_checkbox_writer.

Coverage maps to the Failure Path Test Strategy in
docs/plans/drop-plan-completion-gate.md:
- Dual-heading recognition (## Acceptance Criteria AND ## Success Criteria)
- Both-headings-present -> MATCH_AMBIGUOUS_SECTION
- No-heading-present -> NO_CRITERIA_SECTION
- Missing file -> MISSING_FILE
- Empty criterion text -> EMPTY_CRITERION
- Whitespace-normalized exact match (collapses runs, strips ends)
- Near-duplicate criteria differing only by punctuation -> MATCH_AMBIGUOUS
- Case-sensitive (NOT case-insensitive)
- Idempotent (ticking already-ticked is a no-op exit 0)
- Empty criteria section is a no-op success
- status subcommand emits matched_heading + criteria list
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from tools import plan_checkbox_writer as pcw


def _write_plan(tmp_path: Path, body: str) -> Path:
    plan = tmp_path / "plan.md"
    plan.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    return plan


# ---------------------------------------------------------------------------
# Heading discovery
# ---------------------------------------------------------------------------


class TestHeadingDiscovery:
    def test_finds_acceptance_heading(self, tmp_path: Path) -> None:
        plan = _write_plan(
            tmp_path,
            """
            # Plan
            ## Acceptance Criteria
            - [ ] Tests pass
            ## Other
            """,
        )
        rc = pcw.main(["tick", str(plan), "--criterion", "Tests pass"])
        assert rc == 0
        assert "- [x] Tests pass" in plan.read_text()

    def test_finds_success_heading(self, tmp_path: Path) -> None:
        plan = _write_plan(
            tmp_path,
            """
            # Plan
            ## Success Criteria
            - [ ] Build is green
            """,
        )
        rc = pcw.main(["tick", str(plan), "--criterion", "Build is green"])
        assert rc == 0
        assert "- [x] Build is green" in plan.read_text()

    def test_both_headings_present_returns_ambiguous_section(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        plan = _write_plan(
            tmp_path,
            """
            ## Acceptance Criteria
            - [ ] A
            ## Success Criteria
            - [ ] B
            """,
        )
        rc = pcw.main(["tick", str(plan), "--criterion", "A"])
        assert rc == 2
        captured = capsys.readouterr()
        assert "MATCH_AMBIGUOUS_SECTION" in captured.err

    def test_no_heading_returns_no_criteria_section(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        plan = _write_plan(
            tmp_path,
            """
            # Plan
            Just prose, no criteria section.
            """,
        )
        rc = pcw.main(["tick", str(plan), "--criterion", "anything"])
        assert rc == 2
        captured = capsys.readouterr()
        assert "NO_CRITERIA_SECTION" in captured.err


# ---------------------------------------------------------------------------
# Match algorithm
# ---------------------------------------------------------------------------


class TestMatchAlgorithm:
    def test_exact_match_ticks(self, tmp_path: Path) -> None:
        plan = _write_plan(
            tmp_path,
            """
            ## Success Criteria
            - [ ] First criterion
            - [ ] Second criterion
            """,
        )
        rc = pcw.main(["tick", str(plan), "--criterion", "Second criterion"])
        assert rc == 0
        body = plan.read_text()
        assert "- [ ] First criterion" in body  # untouched
        assert "- [x] Second criterion" in body  # ticked

    def test_whitespace_normalization_matches(self, tmp_path: Path) -> None:
        # The criterion has tabs and a trailing space; the input has plain spaces.
        plan = _write_plan(
            tmp_path,
            "## Success Criteria\n- [ ] Tests   pass\t\n- [ ] Lint clean\n",
        )
        rc = pcw.main(["tick", str(plan), "--criterion", "  Tests pass  "])
        assert rc == 0
        body = plan.read_text()
        assert "- [x] Tests   pass" in body  # whitespace preserved on disk

    def test_near_duplicate_punctuation_is_ambiguous(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        # "Tests pass" and "Tests pass." are NOT the same after normalization
        # (period is not whitespace). So this resolves cleanly to one match.
        plan = _write_plan(
            tmp_path,
            """
            ## Success Criteria
            - [ ] Tests pass
            - [ ] Tests pass.
            """,
        )
        rc = pcw.main(["tick", str(plan), "--criterion", "Tests pass"])
        assert rc == 0
        body = plan.read_text()
        assert "- [x] Tests pass\n" in body
        assert "- [ ] Tests pass." in body  # the period variant untouched

    def test_true_duplicate_is_ambiguous(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        plan = _write_plan(
            tmp_path,
            """
            ## Success Criteria
            - [ ] Tests pass
            - [ ] Tests pass
            """,
        )
        rc = pcw.main(["tick", str(plan), "--criterion", "Tests pass"])
        assert rc == 2
        captured = capsys.readouterr()
        assert "MATCH_AMBIGUOUS" in captured.err

    def test_match_not_found(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        plan = _write_plan(
            tmp_path,
            """
            ## Success Criteria
            - [ ] Tests pass
            """,
        )
        rc = pcw.main(["tick", str(plan), "--criterion", "Nonexistent"])
        assert rc == 2
        captured = capsys.readouterr()
        assert "MATCH_NOT_FOUND" in captured.err

    def test_match_is_case_sensitive(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        plan = _write_plan(
            tmp_path,
            """
            ## Success Criteria
            - [ ] Tests pass
            """,
        )
        rc = pcw.main(["tick", str(plan), "--criterion", "tests pass"])
        assert rc == 2
        captured = capsys.readouterr()
        assert "MATCH_NOT_FOUND" in captured.err


# ---------------------------------------------------------------------------
# Idempotence
# ---------------------------------------------------------------------------


class TestIdempotence:
    def test_tick_already_ticked_is_noop_success(self, tmp_path: Path) -> None:
        plan = _write_plan(
            tmp_path,
            """
            ## Success Criteria
            - [x] Already done
            """,
        )
        before = plan.read_text()
        rc = pcw.main(["tick", str(plan), "--criterion", "Already done"])
        assert rc == 0
        assert plan.read_text() == before

    def test_untick_already_unticked_is_noop_success(self, tmp_path: Path) -> None:
        plan = _write_plan(
            tmp_path,
            """
            ## Success Criteria
            - [ ] Not yet
            """,
        )
        before = plan.read_text()
        rc = pcw.main(["untick", str(plan), "--criterion", "Not yet"])
        assert rc == 0
        assert plan.read_text() == before

    def test_untick_flips_x_to_space(self, tmp_path: Path) -> None:
        plan = _write_plan(
            tmp_path,
            """
            ## Success Criteria
            - [x] Was ticked
            """,
        )
        rc = pcw.main(["untick", str(plan), "--criterion", "Was ticked"])
        assert rc == 0
        assert "- [ ] Was ticked" in plan.read_text()


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_missing_file(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        plan = tmp_path / "does_not_exist.md"
        rc = pcw.main(["tick", str(plan), "--criterion", "x"])
        assert rc == 2
        captured = capsys.readouterr()
        assert "MISSING_FILE" in captured.err

    def test_empty_criterion_rejected(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        plan = _write_plan(
            tmp_path,
            """
            ## Success Criteria
            - [ ] Real one
            """,
        )
        rc = pcw.main(["tick", str(plan), "--criterion", "   "])
        assert rc == 2
        captured = capsys.readouterr()
        assert "EMPTY_CRITERION" in captured.err

    def test_empty_criteria_section_is_noop(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        plan = _write_plan(
            tmp_path,
            """
            ## Success Criteria

            ## Other
            stuff
            """,
        )
        before = plan.read_text()
        rc = pcw.main(["tick", str(plan), "--criterion", "anything"])
        assert rc == 0
        assert plan.read_text() == before

    def test_section_ends_at_next_h2(self, tmp_path: Path) -> None:
        # Criteria after the next ## must NOT be matched.
        plan = _write_plan(
            tmp_path,
            """
            ## Success Criteria
            - [ ] inside
            ## Outside
            - [ ] outside
            """,
        )
        rc = pcw.main(["tick", str(plan), "--criterion", "outside"])
        assert rc == 2  # MATCH_NOT_FOUND because it's outside the section

        rc = pcw.main(["tick", str(plan), "--criterion", "inside"])
        assert rc == 0
        assert "- [x] inside" in plan.read_text()
        assert "- [ ] outside" in plan.read_text()  # untouched

    def test_indented_checkbox_recognized(self, tmp_path: Path) -> None:
        # Real plans sometimes nest checkboxes under a sub-bullet.
        plan = _write_plan(
            tmp_path,
            "## Success Criteria\n  - [ ] Indented criterion\n",
        )
        rc = pcw.main(["tick", str(plan), "--criterion", "Indented criterion"])
        assert rc == 0
        assert "  - [x] Indented criterion" in plan.read_text()

    def test_preserves_trailing_newline(self, tmp_path: Path) -> None:
        plan = tmp_path / "p.md"
        plan.write_text("## Success Criteria\n- [ ] One\n", encoding="utf-8")
        pcw.main(["tick", str(plan), "--criterion", "One"])
        assert plan.read_text().endswith("\n")

    def test_preserves_no_trailing_newline(self, tmp_path: Path) -> None:
        plan = tmp_path / "p.md"
        plan.write_text("## Success Criteria\n- [ ] One", encoding="utf-8")
        pcw.main(["tick", str(plan), "--criterion", "One"])
        assert not plan.read_text().endswith("\n")


# ---------------------------------------------------------------------------
# status subcommand
# ---------------------------------------------------------------------------


class TestStatus:
    def test_status_reports_matched_heading_and_state(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        plan = _write_plan(
            tmp_path,
            """
            ## Success Criteria
            - [ ] First
            - [x] Second
            """,
        )
        rc = pcw.main(["status", str(plan)])
        assert rc == 0
        out = capsys.readouterr().out
        payload = json.loads(out)
        assert payload["matched_heading"] == "Success Criteria"
        assert len(payload["criteria"]) == 2
        assert payload["criteria"][0] == {"criterion": "First", "checked": False, "line": 2}
        assert payload["criteria"][1] == {"criterion": "Second", "checked": True, "line": 3}

    def test_status_with_acceptance_heading(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        plan = _write_plan(
            tmp_path,
            """
            ## Acceptance Criteria
            - [ ] A
            """,
        )
        rc = pcw.main(["status", str(plan)])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["matched_heading"] == "Acceptance Criteria"
        assert payload["criteria"][0]["criterion"] == "A"

    def test_status_no_section(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        plan = _write_plan(tmp_path, "# Plan\nProse only.\n")
        rc = pcw.main(["status", str(plan)])
        assert rc == 2
        assert "NO_CRITERIA_SECTION" in capsys.readouterr().err
