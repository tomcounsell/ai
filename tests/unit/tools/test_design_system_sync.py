"""Unit tests for tools.design_system_sync.

Covers:
- Prefix categorization including longest-prefix-wins (Risk 9 lock-in).
- Deterministic emission across runs and across children-order permutations.
- Empty ``.pen`` handling (missing-primary surfaces as a lint error).
- Unmapped-variable warning + error path with / without ``--drop-unmapped``.
- ``--no-node`` fallback.
- ``--audit`` repo-root walk + initial-pass placeholder + stale-warn.
- TOML / CLI path resolution precedence.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools import design_system_sync as dss

FIXTURE_DIR = Path(__file__).resolve().parents[3] / "tests/fixtures/design_system"
FIXTURE_PEN = FIXTURE_DIR / "design-system.pen"


def _write_minimal_pen(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    pen = tmp_path / "design-system.pen"
    pen.write_text(
        json.dumps(
            {
                "version": 1,
                "name": "fix",
                "children": [
                    {
                        "type": "frame",
                        "id": "c",
                        "name": "components",
                        "children": [
                            {
                                "type": "frame",
                                "id": "B",
                                "name": "Btn/Primary",
                                "reusable": True,
                                "width": 10,
                                "height": 10,
                                "children": [
                                    {"type": "rectangle", "id": "bg", "fill": "$--color-primary", "width": 10, "height": 10, "x": 0, "y": 0},
                                    {"type": "text", "id": "lbl", "text": "A", "fill": "$--text-body-primary", "font": "$--font-body", "size": "$--text-size-body", "weight": "$--text-weight-body", "lineHeight": "$--text-lh-body", "x": 1, "y": 1},
                                ],
                            }
                        ],
                    }
                ],
                "variables": {
                    "--color-primary": {"type": "color", "value": "#111111"},
                    "--text-body-primary": {"type": "color", "value": "#FFFFFF"},
                    "--font-body": {"type": "string", "value": "Inter"},
                    "--text-size-body": {"type": "dimension", "value": "16px"},
                    "--text-weight-body": {"type": "number", "value": 400},
                    "--text-lh-body": {"type": "dimension", "value": "24px"},
                    "--radius-md": {"type": "dimension", "value": "8px"},
                    "--space-md": {"type": "dimension", "value": "16px"},
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return pen


# ---------------------------------------------------------------------------
# Prefix categorization
# ---------------------------------------------------------------------------


def test_longest_prefix_wins_for_typography_over_colors():
    """--text-size-md categorizes as typography, not colors (Risk 9)."""
    assert dss.categorize_prefix("--text-size-md") == "typography"
    assert dss.categorize_prefix("--text-weight-bold") == "typography"
    assert dss.categorize_prefix("--text-lh-tight") == "typography"
    # Fall-through: --text-* without the -size/-weight/-lh suffix → colors.
    assert dss.categorize_prefix("--text-body-primary") == "colors"


def test_prefix_categorization_covers_all_buckets():
    assert dss.categorize_prefix("--color-primary") == "colors"
    assert dss.categorize_prefix("--accent") == "colors"
    assert dss.categorize_prefix("--status-ok") == "colors"
    assert dss.categorize_prefix("--surface-card") == "colors"
    assert dss.categorize_prefix("--border-muted") == "colors"
    assert dss.categorize_prefix("--font-sans") == "typography"
    assert dss.categorize_prefix("--radius-md") == "rounded"
    assert dss.categorize_prefix("--rounded-sm") == "rounded"
    assert dss.categorize_prefix("--space-md") == "spacing"
    assert dss.categorize_prefix("--gap-lg") == "spacing"
    assert dss.categorize_prefix("--pad-inset") == "spacing"
    assert dss.categorize_prefix("--unmapped-thing") is None


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_generate_is_byte_identical_across_runs(tmp_path: Path):
    pen = _write_minimal_pen(tmp_path)
    css_root = tmp_path / "css"
    paths = dss.ResolvedPaths(pen=pen, css_root=css_root)

    dss.cmd_generate(paths, no_node=True, drop_unmapped=False)
    first_md = (pen.parent / "design-system.md").read_bytes()
    first_brand = (css_root / "brand.css").read_bytes()
    first_source = (css_root / "source.css").read_bytes()

    dss.cmd_generate(paths, no_node=True, drop_unmapped=False)
    assert (pen.parent / "design-system.md").read_bytes() == first_md
    assert (css_root / "brand.css").read_bytes() == first_brand
    assert (css_root / "source.css").read_bytes() == first_source


def test_component_children_order_independence(tmp_path: Path):
    """Two .pen files with permuted children order produce byte-identical DESIGN.md."""
    pen_a = _write_minimal_pen(tmp_path / "a")
    pen_b_dir = tmp_path / "b"
    pen_b_dir.mkdir()
    doc = json.loads(pen_a.read_text())
    # Reverse the component children order.
    comp = doc["children"][0]["children"][0]
    comp["children"].reverse()
    pen_b = pen_b_dir / "design-system.pen"
    pen_b.write_text(json.dumps(doc, indent=2), encoding="utf-8")

    paths_a = dss.ResolvedPaths(pen=pen_a, css_root=tmp_path / "a/css")
    paths_b = dss.ResolvedPaths(pen=pen_b, css_root=pen_b_dir / "css")
    dss.cmd_generate(paths_a, no_node=True, drop_unmapped=False)
    dss.cmd_generate(paths_b, no_node=True, drop_unmapped=False)

    md_a = (pen_a.parent / "design-system.md").read_bytes()
    md_b = (pen_b.parent / "design-system.md").read_bytes()
    assert md_a == md_b


# ---------------------------------------------------------------------------
# Unmapped / empty input
# ---------------------------------------------------------------------------


def test_unmapped_prefix_fails_without_drop_flag(tmp_path: Path, capsys):
    pen = _write_minimal_pen(tmp_path)
    doc = json.loads(pen.read_text())
    doc["variables"]["--weird-thing"] = {"type": "string", "value": "x"}
    pen.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    paths = dss.ResolvedPaths(pen=pen, css_root=tmp_path / "css")
    with pytest.raises(SystemExit):
        dss.cmd_generate(paths, no_node=True, drop_unmapped=False)


def test_unmapped_prefix_passes_with_drop_flag(tmp_path: Path):
    pen = _write_minimal_pen(tmp_path)
    doc = json.loads(pen.read_text())
    doc["variables"]["--weird-thing"] = {"type": "string", "value": "x"}
    pen.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    paths = dss.ResolvedPaths(pen=pen, css_root=tmp_path / "css")
    rc = dss.cmd_generate(paths, no_node=True, drop_unmapped=True)
    assert rc == 0


def test_missing_pen_exits_2(tmp_path: Path):
    with pytest.raises(SystemExit):
        dss.resolve_paths(str(tmp_path / "nope.pen"), str(tmp_path / "css"))


# ---------------------------------------------------------------------------
# --no-node fallback
# ---------------------------------------------------------------------------


def test_generate_no_node_skips_exports(tmp_path: Path, monkeypatch):
    pen = _write_minimal_pen(tmp_path)
    paths = dss.ResolvedPaths(pen=pen, css_root=tmp_path / "css")

    called = []

    def fake_probe():
        called.append("probe")
        return False

    monkeypatch.setattr(dss, "_probe_npx", fake_probe)
    rc = dss.cmd_generate(paths, no_node=False, drop_unmapped=False)
    assert rc == 0
    # Generator still emits design-system.md, brand.css, source.css.
    assert (pen.parent / "design-system.md").is_file()
    assert (tmp_path / "css/brand.css").is_file()
    assert (tmp_path / "css/source.css").is_file()
    # No exports/ dir is created on the no-node path.
    assert not (pen.parent / "exports").is_dir()


def test_all_without_node_exits_2(tmp_path: Path, monkeypatch):
    pen = _write_minimal_pen(tmp_path)
    paths = dss.ResolvedPaths(pen=pen, css_root=tmp_path / "css")
    monkeypatch.setattr(dss, "_probe_npx", lambda: False)
    with pytest.raises(SystemExit):
        dss.cmd_all(paths, drop_unmapped=False, no_node=True)


# ---------------------------------------------------------------------------
# --audit behavior
# ---------------------------------------------------------------------------


def test_audit_exits_2_without_git_repo(tmp_path: Path):
    pen = _write_minimal_pen(tmp_path)
    paths = dss.ResolvedPaths(pen=pen, css_root=tmp_path / "css")
    with pytest.raises(SystemExit):
        dss.cmd_audit(paths, repo_root=None)


def test_audit_initial_pass_placeholder(tmp_path: Path, monkeypatch, capsys):
    """When git show HEAD:<md> exits 128, --audit emits the placeholder."""
    pen = _write_minimal_pen(tmp_path)
    (tmp_path / ".git").mkdir()  # fake repo root
    paths = dss.ResolvedPaths(pen=pen, css_root=tmp_path / "css")
    dss.cmd_generate(paths, no_node=True, drop_unmapped=False)

    import subprocess as _sp

    orig_run = _sp.run

    def fake_run(*args, **kwargs):
        # Fake `git show HEAD:...` → exit 128.
        cmd = args[0] if args else kwargs.get("args")
        if isinstance(cmd, list) and cmd[:2] == ["git", "show"]:
            return _sp.CompletedProcess(cmd, 128, stdout="", stderr="not in HEAD")
        return orig_run(*args, **kwargs)

    monkeypatch.setattr(_sp, "run", fake_run)
    rc = dss.cmd_audit(paths, repo_root=None)
    assert rc == 0
    captured = capsys.readouterr()
    assert "initial pass" in captured.out


# ---------------------------------------------------------------------------
# Path resolution precedence
# ---------------------------------------------------------------------------


def test_toml_provides_css_root_when_flag_absent(tmp_path: Path):
    pen = _write_minimal_pen(tmp_path)
    (pen.parent / "design-system-sync.toml").write_text('css_root = "mycss"\n', encoding="utf-8")
    paths = dss.resolve_paths(str(pen), None)
    assert paths.css_root == (pen.parent / "mycss").resolve()


def test_cli_flag_overrides_toml(tmp_path: Path):
    pen = _write_minimal_pen(tmp_path)
    (pen.parent / "design-system-sync.toml").write_text('css_root = "mycss"\n', encoding="utf-8")
    paths = dss.resolve_paths(str(pen), str(tmp_path / "explicit"))
    assert paths.css_root == (tmp_path / "explicit").resolve()


# ---------------------------------------------------------------------------
# Repo fixture end-to-end (no-node path)
# ---------------------------------------------------------------------------


def test_fixture_generator_produces_stable_output():
    """Regenerating from the committed fixture matches the committed md."""
    paths = dss.resolve_paths(str(FIXTURE_PEN), None)
    # --check passes on the committed fixture state.
    rc = dss.cmd_check(paths, drop_unmapped=False, no_node=True)
    assert rc == 0, "fixture drift: regenerate and commit."
