"""Tests for the Step 2b DOCS-stage merge gate (issue #1944).

The Step 2b gate logic lives as a shell snippet inside this repo's merge-gate
addendum ``docs/sdlc/do-merge.md`` (the portable ``/do-merge`` skill defers
repo-specific gates to it). We can't execute the full gate here without a live
PR and a populated Redis session, so these tests exercise the extracted snippet
directly: we parse the markdown, pull out the fenced ``bash`` block that contains
``DOCS_GATE:``, substitute the ``{PR}`` / ``{issue_number}`` placeholders, and run
it under ``bash`` with a temporary PATH shim that provides fake ``gh``,
``sdlc-tool``, and ``git`` executables the test controls.

Extracting the *live* snippet (rather than hardcoding a copy) is what pins the
test to the markdown: any drift in the gate's decision logic breaks the test.

The shim lets each test control:

- the PR head-ref (``gh pr view ... headRefName``) — hence the derived slug,
- the ``stages.DOCS`` value (``sdlc-tool stage-query`` JSON), and
- the current-branch fallback (``git rev-parse --abbrev-ref HEAD``).

Bash runs from a temp working directory where ``docs/features/{slug}.md`` fixture
files are created as needed, so the ``test -f`` degraded fallback is exercised
against a real filesystem. ``python3`` is expected on PATH (not shimmed).
"""

from __future__ import annotations

import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DO_MERGE_MD = REPO_ROOT / "docs" / "sdlc" / "do-merge.md"


def _extract_docs_gate_snippet() -> str:
    """Pull the fenced ``bash`` block containing ``DOCS_GATE:`` from do-merge.md.

    The block is embedded (indented) under a markdown list item, so we dedent it
    after extraction. Returns the raw shell source.
    """
    md = DO_MERGE_MD.read_text()
    # Match every ```bash ... ``` fenced block (fence may be indented under a
    # list item, so allow leading whitespace on the fence lines).
    pattern = re.compile(
        r"^[ \t]*```bash[ \t]*\n(.*?)^[ \t]*```[ \t]*$",
        re.DOTALL | re.MULTILINE,
    )
    for match in pattern.finditer(md):
        body = match.group(1)
        if "DOCS_GATE:" in body:
            return textwrap.dedent(body)
    raise AssertionError("No ```bash block containing DOCS_GATE: found in do-merge.md")


def _write_shim(shim_dir: Path, name: str, body: str) -> None:
    path = shim_dir / name
    path.write_text("#!/usr/bin/env bash\n" + body)
    path.chmod(0o755)


def _run_gate(
    tmp_path: Path,
    *,
    head_ref: str,
    stages_json: str,
    git_branch: str = "main",
) -> str:
    """Run the extracted Step 2b snippet under a controlled PATH shim.

    Args:
        head_ref: what ``gh pr view ... headRefName`` echoes (empty = unavailable).
        stages_json: what ``sdlc-tool stage-query`` echoes on stdout.
        git_branch: what ``git rev-parse --abbrev-ref HEAD`` echoes (fallback path).

    Returns combined stdout+stderr of the snippet run.
    """
    if shutil.which("bash") is None:
        pytest.skip("bash not installed")

    snippet = _extract_docs_gate_snippet()
    snippet = snippet.replace("{PR}", "999").replace("{issue_number}", "1944")

    shim_dir = tmp_path / "bin"
    shim_dir.mkdir()

    # Fake `gh`: only handle `pr view ... headRefName`. Echo the controlled
    # head-ref (or nothing, to simulate an unavailable lookup).
    _write_shim(
        shim_dir,
        "gh",
        f'if [[ "$*" == *headRefName* ]]; then printf %s {head_ref!r}; fi\n',
    )
    # Fake `sdlc-tool`: emit the controlled stage-query JSON on stdout.
    _write_shim(
        shim_dir,
        "sdlc-tool",
        f"cat <<'EOF'\n{stages_json}\nEOF\n",
    )
    # Fake `git`: answer rev-parse --abbrev-ref HEAD with the controlled branch.
    _write_shim(
        shim_dir,
        "git",
        f'if [[ "$*" == *"rev-parse --abbrev-ref HEAD"* ]]; then echo {git_branch!r}; fi\n',
    )

    workdir = tmp_path / "work"
    (workdir / "docs" / "features").mkdir(parents=True, exist_ok=True)

    env = {
        "PATH": f"{shim_dir}:/usr/bin:/bin:/usr/local/bin",
    }
    result = subprocess.run(
        ["bash", "-c", snippet],
        cwd=workdir,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout + result.stderr


def _make_feature_doc(tmp_path: Path, slug: str) -> None:
    features_dir = tmp_path / "work" / "docs" / "features"
    features_dir.mkdir(parents=True, exist_ok=True)
    (features_dir / f"{slug}.md").write_text("# feature\n")


# ---------------------------------------------------------------------------
# completed → authoritative PASS
# ---------------------------------------------------------------------------


def test_completed_passes(tmp_path):
    out = _run_gate(
        tmp_path,
        head_ref="session/my-slug",
        stages_json='{"stages": {"DOCS": "completed"}, "_meta": {}}',
    )
    assert "DOCS_GATE: PASS" in out
    assert "GATES_FAILED" not in out


def test_skip_recorded_as_completed_passes(tmp_path):
    """A DOCS-skip (#1799) records the ``completed`` status, so it is admitted
    with no special skip-branch — identical to a real completion."""
    out = _run_gate(
        tmp_path,
        head_ref="session/trivial-docs-skip",
        stages_json='{"stages": {"DOCS": "completed"}, "_meta": {}}',
    )
    assert "DOCS_GATE: PASS" in out
    assert "GATES_FAILED" not in out


# ---------------------------------------------------------------------------
# in_progress → the only HARD fail
# ---------------------------------------------------------------------------


def test_in_progress_hard_fails(tmp_path):
    out = _run_gate(
        tmp_path,
        head_ref="session/my-slug",
        stages_json='{"stages": {"DOCS": "in_progress"}, "_meta": {}}',
    )
    assert "GATES_FAILED" in out
    assert "DOCS_GATE: FAIL" in out
    assert "in_progress" in out


# ---------------------------------------------------------------------------
# pending → degraded fallback to docs/features/{slug}.md existence
# ---------------------------------------------------------------------------


def test_pending_with_feature_doc_passes_degraded(tmp_path):
    _make_feature_doc(tmp_path, "my-slug")
    out = _run_gate(
        tmp_path,
        head_ref="session/my-slug",
        stages_json='{"stages": {"DOCS": "pending"}, "_meta": {}}',
    )
    assert "PASS (degraded)" in out
    assert "GATES_FAILED" not in out


def test_pending_without_feature_doc_fails(tmp_path):
    out = _run_gate(
        tmp_path,
        head_ref="session/my-slug",
        stages_json='{"stages": {"DOCS": "pending"}, "_meta": {}}',
    )
    assert "GATES_FAILED" in out


# ---------------------------------------------------------------------------
# empty stages (session reaped) → degraded fallback
# ---------------------------------------------------------------------------


def test_empty_stages_with_feature_doc_passes_degraded(tmp_path):
    _make_feature_doc(tmp_path, "my-slug")
    out = _run_gate(
        tmp_path,
        head_ref="session/my-slug",
        stages_json='{"stages": {}, "_meta": {}}',
    )
    assert "PASS (degraded)" in out
    assert "GATES_FAILED" not in out


def test_empty_stages_without_feature_doc_fails(tmp_path):
    out = _run_gate(
        tmp_path,
        head_ref="session/my-slug",
        stages_json='{"stages": {}, "_meta": {}}',
    )
    assert "GATES_FAILED" in out


# ---------------------------------------------------------------------------
# no usable slug (invoked from main / detached HEAD) → FAIL with <no-slug>
# ---------------------------------------------------------------------------


def test_no_usable_slug_fails_without_main_lookup(tmp_path):
    """When the PR head-ref resolves to ``main`` (and the git fallback is also
    ``main``), the slug normalizes to empty: the gate must FAIL naming
    ``<no-slug>`` and must NOT do a ``docs/features/main.md`` lookup."""
    out = _run_gate(
        tmp_path,
        head_ref="main",
        stages_json='{"stages": {"DOCS": "pending"}, "_meta": {}}',
        git_branch="main",
    )
    assert "GATES_FAILED" in out
    assert "<no-slug>" in out
    assert "docs/features/main.md" not in out
