"""Unit tests for tools.sdlc_decompose.

Covers Task 1.7 of docs/plans/sdlc-1393.md:
  (a) multi-unit plan returns valid JSON
  (b) single-task plan returns one unit
  (c) empty Implementation Plan section returns one fallback unit
  (d) Claude returns malformed JSON -> exits 1 with validation error
  (e) over-cap decomposition exits 1
  (f) plan file not found -> exits 1

Tests stub out the Claude API call by monkeypatching ``_call_claude``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import tools.sdlc_decompose as sd

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_plan(tmp_path: Path, section_body: str = "") -> Path:
    """Write a minimal plan doc with an optional Implementation Plan section."""
    plan = tmp_path / "test_plan.md"
    body = "# Test Plan\n\n## Problem\n\nSome problem.\n\n"
    if section_body:
        body += f"## Implementation Plan\n\n{section_body}\n"
    body += "\n## Other Section\n\nstuff\n"
    plan.write_text(body, encoding="utf-8")
    return plan


# ---------------------------------------------------------------------------
# extract_implementation_plan
# ---------------------------------------------------------------------------


def test_extract_implementation_plan_present(tmp_path: Path) -> None:
    plan = _make_plan(tmp_path, "- [ ] Task A\n- [ ] Task B\n")
    text = plan.read_text(encoding="utf-8")
    section = sd.extract_implementation_plan(text)
    assert "Task A" in section
    assert "Task B" in section


def test_extract_implementation_plan_missing(tmp_path: Path) -> None:
    plan = tmp_path / "no_section.md"
    plan.write_text("# Plan\n\n## Problem\n\nx\n", encoding="utf-8")
    text = plan.read_text(encoding="utf-8")
    assert sd.extract_implementation_plan(text) == ""


# ---------------------------------------------------------------------------
# _validate_units
# ---------------------------------------------------------------------------


def test_validate_units_accepts_valid() -> None:
    units = [
        {"unit_id": "u1", "description": "first", "tasks": ["Task 1"]},
        {"unit_id": "u2", "description": "second", "tasks": ["Task 2", "Task 3"]},
    ]
    assert sd._validate_units(units, max_units=3) == units


def test_validate_units_rejects_non_list() -> None:
    with pytest.raises(ValueError, match="must be a list"):
        sd._validate_units({"u1": "x"}, max_units=3)


def test_validate_units_rejects_empty_list() -> None:
    with pytest.raises(ValueError, match="zero units"):
        sd._validate_units([], max_units=3)


def test_validate_units_rejects_missing_unit_id() -> None:
    with pytest.raises(ValueError, match="unit_id"):
        sd._validate_units([{"description": "d", "tasks": ["t"]}], max_units=3)


def test_validate_units_rejects_non_snake_case_unit_id() -> None:
    units = [{"unit_id": "BadUnit", "description": "d", "tasks": ["t"]}]
    with pytest.raises(ValueError, match="snake_case"):
        sd._validate_units(units, max_units=3)


def test_validate_units_rejects_duplicate_unit_id() -> None:
    units = [
        {"unit_id": "u1", "description": "d", "tasks": ["t"]},
        {"unit_id": "u1", "description": "d2", "tasks": ["t2"]},
    ]
    with pytest.raises(ValueError, match="duplicate"):
        sd._validate_units(units, max_units=3)


def test_validate_units_rejects_empty_description() -> None:
    units = [{"unit_id": "u1", "description": "", "tasks": ["t"]}]
    with pytest.raises(ValueError, match="description"):
        sd._validate_units(units, max_units=3)


def test_validate_units_rejects_empty_tasks() -> None:
    units = [{"unit_id": "u1", "description": "d", "tasks": []}]
    with pytest.raises(ValueError, match="tasks"):
        sd._validate_units(units, max_units=3)


def test_validate_units_rejects_over_cap() -> None:
    units = [{"unit_id": f"u{i}", "description": "d", "tasks": ["t"]} for i in range(5)]
    with pytest.raises(ValueError, match="cap is 3"):
        sd._validate_units(units, max_units=3)


# ---------------------------------------------------------------------------
# decompose() end-to-end with stubbed Claude
# ---------------------------------------------------------------------------


def test_decompose_multi_unit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _make_plan(tmp_path, "### Phase 1\n- Task 1.1\n### Phase 2\n- Task 2.1\n")
    fake_units = [
        {"unit_id": "phase1", "description": "phase 1 work", "tasks": ["Task 1.1"]},
        {"unit_id": "phase2", "description": "phase 2 work", "tasks": ["Task 2.1"]},
    ]
    monkeypatch.setattr(sd, "_call_claude", lambda prompt: json.dumps(fake_units))

    result = sd.decompose(plan, max_units=3)
    assert len(result) == 2
    assert result[0]["unit_id"] == "phase1"
    assert result[1]["unit_id"] == "phase2"


def test_decompose_single_task_returns_one_unit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _make_plan(tmp_path, "- Task: do one thing\n")
    fake_units = [{"unit_id": "u1", "description": "one thing", "tasks": ["do one thing"]}]
    monkeypatch.setattr(sd, "_call_claude", lambda prompt: json.dumps(fake_units))

    result = sd.decompose(plan, max_units=3)
    assert len(result) == 1


def test_decompose_empty_section_returns_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = tmp_path / "empty.md"
    plan.write_text("# Plan\n\n## Problem\n\nx\n", encoding="utf-8")
    # Claude shouldn't be called when section is missing.
    monkeypatch.setattr(
        sd, "_call_claude", lambda prompt: (_ for _ in ()).throw(AssertionError("should not call"))
    )

    result = sd.decompose(plan, max_units=3)
    assert len(result) == 1
    assert result[0]["unit_id"] == "u1"


def test_decompose_malformed_json_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _make_plan(tmp_path, "- Task A\n")
    monkeypatch.setattr(sd, "_call_claude", lambda prompt: "not json {{{")

    with pytest.raises(ValueError, match="malformed JSON"):
        sd.decompose(plan, max_units=3)


def test_decompose_over_cap_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _make_plan(tmp_path, "- Task A\n- Task B\n- Task C\n- Task D\n")
    fake = [{"unit_id": f"u{i}", "description": "d", "tasks": ["t"]} for i in range(5)]
    monkeypatch.setattr(sd, "_call_claude", lambda prompt: json.dumps(fake))

    with pytest.raises(ValueError, match="cap is 3"):
        sd.decompose(plan, max_units=3)


def test_decompose_strips_code_fences(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _make_plan(tmp_path, "- Task A\n")
    fenced = '```json\n[{"unit_id":"u1","description":"d","tasks":["t"]}]\n```'
    monkeypatch.setattr(sd, "_call_claude", lambda prompt: fenced)

    result = sd.decompose(plan, max_units=3)
    assert result[0]["unit_id"] == "u1"


# ---------------------------------------------------------------------------
# main() exit codes
# ---------------------------------------------------------------------------


def test_main_returns_0_on_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    plan = _make_plan(tmp_path, "- Task A\n")
    monkeypatch.setattr(
        sd,
        "_call_claude",
        lambda p: json.dumps([{"unit_id": "u1", "description": "d", "tasks": ["t"]}]),
    )

    rc = sd.main([str(plan)])
    assert rc == 0
    captured = capsys.readouterr()
    parsed = json.loads(captured.out)
    assert isinstance(parsed, list)
    assert parsed[0]["unit_id"] == "u1"


def test_main_returns_1_on_missing_plan(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    missing = tmp_path / "nope.md"
    rc = sd.main([str(missing)])
    assert rc == 1
    captured = capsys.readouterr()
    parsed = json.loads(captured.err)
    assert "not found" in parsed["error"]


def test_main_returns_1_on_over_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    plan = _make_plan(tmp_path, "- Task\n")
    fake = [{"unit_id": f"u{i}", "description": "d", "tasks": ["t"]} for i in range(5)]
    monkeypatch.setattr(sd, "_call_claude", lambda p: json.dumps(fake))

    rc = sd.main([str(plan), "--max-units", "3"])
    assert rc == 1
    captured = capsys.readouterr()
    parsed = json.loads(captured.err)
    assert "cap is 3" in parsed["error"]


def test_main_returns_1_on_malformed_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    plan = _make_plan(tmp_path, "- Task\n")
    monkeypatch.setattr(sd, "_call_claude", lambda p: "garbage")

    rc = sd.main([str(plan)])
    assert rc == 1
    captured = capsys.readouterr()
    parsed = json.loads(captured.err)
    assert "malformed JSON" in parsed["error"]


def test_main_returns_2_on_bad_max_units(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    plan = _make_plan(tmp_path, "- Task\n")
    rc = sd.main([str(plan), "--max-units", "0"])
    assert rc == 2
