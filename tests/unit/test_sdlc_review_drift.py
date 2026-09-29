"""Unit tests for tools.sdlc_review_drift (#3228).

The classifier's whole value is that it narrows a detected trailer mismatch in
exactly one direction — documentation-only drift — and fails closed on
everything else. So the tests that matter most are the refusals: code paths,
force-pushes, truncated file lists, and every error shape.

`gh` is mocked at the subprocess boundary; nothing here touches the network.
"""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from tools.sdlc_review_drift import (
    _COMPARE_FILE_CAP,
    classify_head_drift,
    is_docs_only_path,
)

_BASE = "a" * 40
_HEAD = "b" * 40


def _compare(status: str, filenames: list[str] | None = None, files: list | None = None):
    """Build a mocked `gh api .../compare/...` CompletedProcess."""
    if files is None:
        files = [{"filename": name} for name in (filenames or [])]
    proc = MagicMock()
    proc.returncode = 0
    proc.stdout = json.dumps({"status": status, "files": files})
    proc.stderr = ""
    return proc


class TestIsDocsOnlyPath:
    @pytest.mark.parametrize(
        "path",
        [
            "docs/plans/sdlc-642.md",
            "docs/features/merge-gate.md",
            "README.md",
            "CHANGELOG.md",
        ],
    )
    def test_documentation(self, path):
        assert is_docs_only_path(path) is True

    @pytest.mark.parametrize(
        "path",
        [
            "src/popoto/models/base.py",
            "tests/test_thing.py",
            "mkdocs.yml",
            ".github/workflows/tests.yml",
            # A skill body is executable instruction, not prose.
            ".claude/skills/do-pr-review/SKILL.md",
            ".claude/commands/foo.md",
            # Instruction surfaces that look like prose: loaded and followed
            # by agents at runtime, so a post-review edit must be re-reviewed.
            "docs/sdlc/do-pr-review.md",
            "CLAUDE.md",
            "AGENTS.md",
            # Nested *.md outside a docs directory is not waved through.
            "src/popoto/NOTES.md",
            "",
        ],
    )
    def test_not_documentation(self, path):
        assert is_docs_only_path(path) is False


class TestClassifyHeadDrift:
    def test_same_sha_is_identical_without_calling_gh(self):
        with patch("subprocess.run") as run:
            assert classify_head_drift(_BASE, _BASE.upper(), "o/r") == "identical"
        run.assert_not_called()

    def test_docs_only_range_is_tolerated(self):
        with patch(
            "subprocess.run",
            return_value=_compare("ahead", ["docs/plans/x.md", "README.md"]),
        ):
            assert classify_head_drift(_BASE, _HEAD, "o/r") == "docs_only"

    @pytest.mark.parametrize("path", ["docs/sdlc/do-merge.md", "CLAUDE.md"])
    def test_instruction_surface_edit_is_code(self, path):
        """A post-review edit to a file agents load as instructions changes
        behaviour, so it invalidates the verdict like a source change."""
        with patch("subprocess.run", return_value=_compare("ahead", ["docs/plans/x.md", path])):
            assert classify_head_drift(_BASE, _HEAD, "o/r") == "code"

    def test_one_source_file_makes_the_whole_range_code(self):
        """The rule is all-or-nothing: a docs cascade that also fixes a
        docstring in a source file is code drift, because that file was not in
        the reviewed diff."""
        with patch(
            "subprocess.run",
            return_value=_compare("ahead", ["docs/plans/x.md", "src/popoto/fields/base.py"]),
        ):
            assert classify_head_drift(_BASE, _HEAD, "o/r") == "code"

    @pytest.mark.parametrize("status", ["diverged", "behind", None, "weird"])
    def test_non_descending_head_is_refused(self, status):
        """Force-push / rebase: the reviewed commit is not in the head's
        history, so "only docs changed since" is not even well-formed."""
        with patch("subprocess.run", return_value=_compare(status, ["docs/x.md"])):
            assert classify_head_drift(_BASE, _HEAD, "o/r") == "code"

    def test_rename_out_of_docs_is_code(self):
        with patch(
            "subprocess.run",
            return_value=_compare(
                "ahead",
                files=[{"filename": "docs/x.md", "previous_filename": "src/x.py"}],
            ),
        ):
            assert classify_head_drift(_BASE, _HEAD, "o/r") == "code"

    def test_possibly_truncated_file_list_is_unknown(self):
        names = [f"docs/f{i}.md" for i in range(_COMPARE_FILE_CAP)]
        with patch("subprocess.run", return_value=_compare("ahead", names)):
            assert classify_head_drift(_BASE, _HEAD, "o/r") == "unknown"

    def test_empty_range_changes_nothing_to_review(self):
        with patch("subprocess.run", return_value=_compare("ahead", [])):
            assert classify_head_drift(_BASE, _HEAD, "o/r") == "docs_only"

    def test_gh_failure_is_unknown(self):
        proc = MagicMock()
        proc.returncode = 1
        proc.stdout = ""
        proc.stderr = "not found"
        with patch("subprocess.run", return_value=proc):
            assert classify_head_drift(_BASE, _HEAD, "o/r") == "unknown"

    def test_timeout_is_unknown(self):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("gh", 20)):
            assert classify_head_drift(_BASE, _HEAD, "o/r") == "unknown"

    def test_unparseable_payload_is_unknown(self):
        proc = MagicMock()
        proc.returncode = 0
        proc.stdout = "<html>rate limited</html>"
        proc.stderr = ""
        with patch("subprocess.run", return_value=proc):
            assert classify_head_drift(_BASE, _HEAD, "o/r") == "unknown"

    def test_missing_files_key_is_unknown(self):
        proc = MagicMock()
        proc.returncode = 0
        proc.stdout = json.dumps({"status": "ahead"})
        proc.stderr = ""
        with patch("subprocess.run", return_value=proc):
            assert classify_head_drift(_BASE, _HEAD, "o/r") == "unknown"

    def test_malformed_file_entry_is_unknown(self):
        with patch("subprocess.run", return_value=_compare("ahead", files=[{"status": "added"}])):
            assert classify_head_drift(_BASE, _HEAD, "o/r") == "unknown"

    @pytest.mark.parametrize(
        "base,head,repo",
        [("", _HEAD, "o/r"), (_BASE, "", "o/r"), (_BASE, _HEAD, ""), (None, _HEAD, "o/r")],
    )
    def test_missing_inputs_are_unknown(self, base, head, repo):
        with patch("subprocess.run") as run:
            assert classify_head_drift(base, head, repo) == "unknown"
        run.assert_not_called()

    def test_targets_the_compare_endpoint_for_the_right_repo_and_range(self):
        with patch("subprocess.run", return_value=_compare("ahead", ["docs/x.md"])) as run:
            classify_head_drift(_BASE, _HEAD, "tomcounsell/popoto", repo_root="/tmp")
        cmd = run.call_args.args[0]
        assert cmd[:2] == ["gh", "api"]
        assert cmd[2] == f"repos/tomcounsell/popoto/compare/{_BASE}...{_HEAD}"
        assert run.call_args.kwargs["cwd"] == "/tmp"
