"""Tests for docs_auditor.py — DocsAuditor class and helpers."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts.docs_auditor import (
    AuditSummary,
    DocsAuditor,
    Verdict,
    _extract_references,
    _verify_references,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """Create a minimal fake repo with docs/ and scripts/."""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "plans").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "data").mkdir()
    # Minimal pyproject.toml
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "test"\ndependencies = ["anthropic"]\n'
    )
    return tmp_path


@pytest.fixture()
def auditor(repo: Path) -> DocsAuditor:
    return DocsAuditor(repo_root=repo, dry_run=True)


# ---------------------------------------------------------------------------
# enumerate_docs
# ---------------------------------------------------------------------------


class TestEnumerateDocs:
    def test_returns_md_files_under_docs(self, repo: Path, auditor: DocsAuditor) -> None:
        (repo / "docs" / "foo.md").write_text("# Foo")
        (repo / "docs" / "bar.md").write_text("# Bar")
        docs = auditor.enumerate_docs()
        names = [d.name for d in docs]
        assert "foo.md" in names
        assert "bar.md" in names

    def test_excludes_plans_dir(self, repo: Path, auditor: DocsAuditor) -> None:
        (repo / "docs" / "plans" / "my-plan.md").write_text("# Plan")
        (repo / "docs" / "real.md").write_text("# Real")
        docs = auditor.enumerate_docs()
        paths_str = [str(d) for d in docs]
        assert all("plans" not in p for p in paths_str)
        assert any("real.md" in p for p in paths_str)

    def test_empty_docs_dir(self, repo: Path, auditor: DocsAuditor) -> None:
        docs = auditor.enumerate_docs()
        assert docs == []

    def test_returns_relative_paths(self, repo: Path, auditor: DocsAuditor) -> None:
        (repo / "docs" / "foo.md").write_text("# Foo")
        docs = auditor.enumerate_docs()
        assert all(not d.is_absolute() for d in docs)


# ---------------------------------------------------------------------------
# _extract_references
# ---------------------------------------------------------------------------


class TestExtractReferences:
    def test_extracts_file_paths(self) -> None:
        content = "See `scripts/foo.py` for details and `bridge/bar.py`."
        refs = _extract_references(content)
        assert "scripts/foo.py" in refs["backtick_tokens"]
        assert "bridge/bar.py" in refs["backtick_tokens"]

    def test_extracts_env_vars(self) -> None:
        content = "Set `ANTHROPIC_API_KEY` and `TELEGRAM_API_ID` in your `.env`."
        refs = _extract_references(content)
        assert "ANTHROPIC_API_KEY" in refs["env_vars"]
        assert "TELEGRAM_API_ID" in refs["env_vars"]

    def test_extracts_python_imports_from_code_block(self) -> None:
        content = "```python\nfrom anthropic import Anthropic\nimport json\n```"
        refs = _extract_references(content)
        assert "anthropic" in refs["python_imports"]

    def test_returns_deduped_lists(self) -> None:
        content = "`scripts/foo.py` and `scripts/foo.py` again"
        refs = _extract_references(content)
        assert refs["backtick_tokens"].count("scripts/foo.py") == 1


# ---------------------------------------------------------------------------
# _verify_references
# ---------------------------------------------------------------------------


class TestVerifyReferences:
    def test_existing_file_path_returns_true(self, repo: Path) -> None:
        (repo / "scripts" / "run.sh").write_text("#!/bin/bash")
        refs: dict = {"file_paths": ["scripts/run.sh"], "env_vars": [], "backtick_tokens": [], "python_imports": []}
        result = _verify_references(refs, repo)
        assert result["file_paths"]["scripts/run.sh"] is True

    def test_missing_file_path_returns_false(self, repo: Path) -> None:
        refs: dict = {"file_paths": ["scripts/missing.sh"], "env_vars": [], "backtick_tokens": [], "python_imports": []}
        result = _verify_references(refs, repo)
        assert result["file_paths"]["scripts/missing.sh"] is False

    def test_env_var_found_in_pyproject(self, repo: Path) -> None:
        (repo / ".env.example").write_text("SOME_VAR=example\n")
        refs: dict = {"file_paths": [], "env_vars": ["SOME_VAR"], "backtick_tokens": [], "python_imports": []}
        result = _verify_references(refs, repo)
        assert result["env_vars"]["SOME_VAR"] is True

    def test_package_in_pyproject_returns_true(self, repo: Path) -> None:
        refs: dict = {"file_paths": [], "env_vars": [], "backtick_tokens": [], "python_imports": ["anthropic"]}
        result = _verify_references(refs, repo)
        assert result["python_imports"]["anthropic"] is True


# ---------------------------------------------------------------------------
# _should_skip (frequency gate)
# ---------------------------------------------------------------------------


class TestFrequencyGate:
    def test_no_state_file_does_not_skip(self, repo: Path, auditor: DocsAuditor) -> None:
        assert auditor._should_skip() is False

    def test_recent_audit_skips(self, repo: Path, auditor: DocsAuditor) -> None:
        state = {"last_audit_date": datetime.now().isoformat()}
        (repo / "data" / "daydream_state.json").write_text(json.dumps(state))
        assert auditor._should_skip() is True

    def test_old_audit_does_not_skip(self, repo: Path, auditor: DocsAuditor) -> None:
        old_date = (datetime.now() - timedelta(days=8)).isoformat()
        state = {"last_audit_date": old_date}
        (repo / "data" / "daydream_state.json").write_text(json.dumps(state))
        assert auditor._should_skip() is False

    def test_boundary_exactly_7_days_skips(self, repo: Path, auditor: DocsAuditor) -> None:
        # 6.9 days ago — still within window
        recent = (datetime.now() - timedelta(days=6, hours=23)).isoformat()
        state = {"last_audit_date": recent}
        (repo / "data" / "daydream_state.json").write_text(json.dumps(state))
        assert auditor._should_skip() is True


# ---------------------------------------------------------------------------
# _parse_verdict
# ---------------------------------------------------------------------------


class TestParseVerdict:
    def test_parses_keep(self, auditor: DocsAuditor) -> None:
        text = "VERDICT: KEEP\nCONFIDENCE: HIGH\nRATIONALE: All references verified\nCORRECTIONS:\n- none"
        v = auditor._parse_verdict(text)
        assert v.action == "KEEP"
        assert v.low_confidence is False
        assert "verified" in v.rationale

    def test_parses_update_with_corrections(self, auditor: DocsAuditor) -> None:
        text = (
            "VERDICT: UPDATE\nCONFIDENCE: HIGH\n"
            "RATIONALE: Script was renamed\n"
            "CORRECTIONS:\n- scripts/old.sh → scripts/new.sh\n- fix env var name"
        )
        v = auditor._parse_verdict(text)
        assert v.action == "UPDATE"
        assert len(v.corrections) == 2
        assert "scripts/old.sh" in v.corrections[0]

    def test_parses_delete(self, auditor: DocsAuditor) -> None:
        text = "VERDICT: DELETE\nCONFIDENCE: HIGH\nRATIONALE: Feature removed\nCORRECTIONS:\n- none"
        v = auditor._parse_verdict(text)
        assert v.action == "DELETE"

    def test_low_confidence_flag(self, auditor: DocsAuditor) -> None:
        text = "VERDICT: UPDATE\nCONFIDENCE: LOW\nRATIONALE: uncertain about this\nCORRECTIONS:\n- none"
        v = auditor._parse_verdict(text)
        assert v.low_confidence is True

    def test_corrections_none_not_included(self, auditor: DocsAuditor) -> None:
        text = "VERDICT: KEEP\nCONFIDENCE: HIGH\nRATIONALE: All good\nCORRECTIONS:\n- none"
        v = auditor._parse_verdict(text)
        assert v.corrections == []


# ---------------------------------------------------------------------------
# execute_verdict
# ---------------------------------------------------------------------------


class TestExecuteVerdict:
    def test_dry_run_delete_does_not_remove_file(self, repo: Path, auditor: DocsAuditor) -> None:
        doc = repo / "docs" / "deleteme.md"
        doc.write_text("# Delete me")
        verdict = Verdict(action="DELETE", rationale="Feature gone")
        auditor.execute_verdict(Path("docs/deleteme.md"), verdict)
        assert doc.exists()  # dry_run=True, should not delete

    def test_live_delete_removes_file(self, repo: Path) -> None:
        live_auditor = DocsAuditor(repo_root=repo, dry_run=False)
        doc = repo / "docs" / "deleteme.md"
        doc.write_text("# Delete me")
        verdict = Verdict(action="DELETE", rationale="Feature gone")
        live_auditor.execute_verdict(Path("docs/deleteme.md"), verdict)
        assert not doc.exists()

    def test_live_update_applies_arrow_correction(self, repo: Path) -> None:
        live_auditor = DocsAuditor(repo_root=repo, dry_run=False)
        doc = repo / "docs" / "update.md"
        doc.write_text("Run `scripts/old_name.sh` to start.")
        verdict = Verdict(
            action="UPDATE",
            rationale="Script renamed",
            corrections=["scripts/old_name.sh → scripts/new_name.sh"],
        )
        live_auditor.execute_verdict(Path("docs/update.md"), verdict)
        updated = doc.read_text()
        assert "scripts/new_name.sh" in updated
        assert "scripts/old_name.sh" not in updated


# ---------------------------------------------------------------------------
# sweep_index_files
# ---------------------------------------------------------------------------


class TestSweepIndexFiles:
    def test_removes_deleted_doc_from_index(self, repo: Path) -> None:
        live_auditor = DocsAuditor(repo_root=repo, dry_run=False)
        index = repo / "docs" / "README.md"
        index.write_text(
            "# Docs\n\n"
            "| [Gone Feature](gone-feature.md) | It was great | Shipped |\n"
            "| [Alive Feature](alive-feature.md) | Still here | Shipped |\n"
        )
        live_auditor.sweep_index_files([Path("docs/gone-feature.md")])
        content = index.read_text()
        assert "gone-feature.md" not in content
        assert "alive-feature.md" in content

    def test_dry_run_does_not_modify_index(self, repo: Path, auditor: DocsAuditor) -> None:
        index = repo / "docs" / "README.md"
        original = "| [Gone](gone.md) | desc |\n"
        index.write_text(original)
        auditor.sweep_index_files([Path("docs/gone.md")])
        assert index.read_text() == original  # dry_run=True


# ---------------------------------------------------------------------------
# run() — skipped when recent audit
# ---------------------------------------------------------------------------


class TestRunFrequencyGate:
    def test_run_skips_if_recent(self, repo: Path) -> None:
        state = {"last_audit_date": datetime.now().isoformat()}
        (repo / "data" / "daydream_state.json").write_text(json.dumps(state))
        auditor = DocsAuditor(repo_root=repo, dry_run=True)
        summary = auditor.run()
        assert summary.skipped is True
        assert "last run" in summary.skip_reason

    def test_audit_summary_str_when_skipped(self) -> None:
        summary = AuditSummary(skipped=True, skip_reason="last run: 2026-02-17")
        assert "skipped" in str(summary).lower()
        assert "2026-02-17" in str(summary)

    def test_run_with_no_docs_returns_empty_summary(self, repo: Path) -> None:
        auditor = DocsAuditor(repo_root=repo, dry_run=True)
        summary = auditor.run()
        assert not summary.skipped
        assert summary.kept == []
        assert summary.deleted == []

    def test_run_calls_analyze_for_each_doc(self, repo: Path) -> None:
        (repo / "docs" / "a.md").write_text("# A")
        (repo / "docs" / "b.md").write_text("# B")

        auditor = DocsAuditor(repo_root=repo, dry_run=True)
        mock_verdict = Verdict(action="KEEP", rationale="All good")

        with patch.object(auditor, "analyze_doc", return_value=mock_verdict) as mock_analyze:
            summary = auditor.run()

        assert mock_analyze.call_count == 2
        assert len(summary.kept) == 2
