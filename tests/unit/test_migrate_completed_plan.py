"""Tests for scripts/migrate_completed_plan.py.

Covers Bug 1 fix: README-based display name extraction replacing .title() mangling.
Also covers the path-independent migrate_plan_to_completed() primitive (issue
#1900, Tier 0): guarded git-mv of a root plan into docs/plans/completed/.
"""

import contextlib
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

# Import the functions under test directly
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.migrate_completed_plan import (  # noqa: E402
    extract_feature_doc_path,
    extract_feature_name_from_index,
    migrate_plan_to_completed,
    validate_feature_doc,
    validate_feature_index,
)

# --- Fixtures ---

SAMPLE_README = textwrap.dedent("""\
    # Feature Documentation Index

    | Feature | Description | Status |
    |---------|-------------|--------|
    | [PM/Dev Session Architecture](pm-dev-session-architecture.md) | PM/Dev split | Shipped |
    | [SDLC Critique Stage](sdlc-critique-stage.md) | Automated plan validation | Shipped |
    | [AI Evaluator](ai-evaluator.md) | Semantic build evaluation | Shipped |
    | [Bridge Self-Healing](bridge-self-healing.md) | Crash recovery | Shipped |
    | [Do-Build AI Evaluator](do-build-ai-evaluator.md) | AI evaluator step | Shipped |
""")


# --- Tests for extract_feature_name_from_index ---


class TestExtractFeatureNameFromIndex:
    """Test README-based display name extraction using the real function."""

    @pytest.fixture(autouse=True)
    def _setup_readme(self, tmp_path):
        """Create a docs/features/README.md and chdir so the real function finds it."""
        readme_path = tmp_path / "docs" / "features" / "README.md"
        readme_path.parent.mkdir(parents=True, exist_ok=True)
        readme_path.write_text(SAMPLE_README)
        self._tmp = tmp_path

    def _extract(self, filename: str) -> str | None:
        with _chdir(self._tmp):
            return extract_feature_name_from_index(filename)

    def test_acronym_heavy_filename_pm(self):
        """PM in filename should resolve to PM/Dev Session Architecture, not Pm/Dev..."""
        result = self._extract("pm-dev-session-architecture.md")
        assert result == "PM/Dev Session Architecture"

    def test_acronym_heavy_filename_sdlc(self):
        """SDLC in filename should resolve correctly."""
        result = self._extract("sdlc-critique-stage.md")
        assert result == "SDLC Critique Stage"

    def test_acronym_heavy_filename_ai(self):
        """AI in filename should resolve correctly."""
        result = self._extract("ai-evaluator.md")
        assert result == "AI Evaluator"

    def test_display_text_differs_from_filename(self):
        """Display text can contain characters not in filename (e.g., slashes)."""
        result = self._extract("pm-dev-session-architecture.md")
        assert result == "PM/Dev Session Architecture"
        # Verify .title() would have mangled this
        mangled = "pm-dev-session-architecture".replace("-", " ").title()
        assert mangled == "Pm Dev Session Architecture"  # Wrong!
        assert result != mangled

    def test_hyphenated_compound_name(self):
        """Compound names with hyphens (do-build) should resolve correctly."""
        result = self._extract("do-build-ai-evaluator.md")
        assert result == "Do-Build AI Evaluator"

    def test_missing_readme_entry(self):
        """Filename with no matching README row returns None."""
        result = self._extract("nonexistent-feature.md")
        assert result is None

    def test_simple_filename(self):
        """Simple filename without acronyms works fine."""
        result = self._extract("bridge-self-healing.md")
        assert result == "Bridge Self-Healing"


class TestValidateFeatureIndex:
    """Test feature index validation."""

    def test_feature_found_case_insensitive(self, tmp_path):
        """validate_feature_index finds features case-insensitively."""
        readme = tmp_path / "docs" / "features" / "README.md"
        readme.parent.mkdir(parents=True, exist_ok=True)
        readme.write_text(SAMPLE_README)

        with _chdir(tmp_path):
            valid, error = validate_feature_index("PM/Dev Session Architecture")
            assert valid is True
            assert error == ""

    def test_feature_not_found(self, tmp_path):
        """validate_feature_index returns error for missing feature."""
        readme = tmp_path / "docs" / "features" / "README.md"
        readme.parent.mkdir(parents=True, exist_ok=True)
        readme.write_text(SAMPLE_README)

        with _chdir(tmp_path):
            valid, error = validate_feature_index("Nonexistent Feature XYZ")
            assert valid is False
            assert "Nonexistent Feature XYZ" in error

    def test_no_readme_file(self, tmp_path):
        """validate_feature_index handles missing README gracefully."""
        with _chdir(tmp_path):
            valid, error = validate_feature_index("Any Feature")
            assert valid is False
            assert "not found" in error


class TestValidateFeatureDoc:
    """Test feature doc validation."""

    def test_valid_doc(self, tmp_path):
        """Valid doc with title and content passes."""
        doc = tmp_path / "feature.md"
        doc.write_text("# My Feature\n\nThis is a substantial description of the feature.")
        valid, error = validate_feature_doc(doc)
        assert valid is True

    def test_missing_doc(self, tmp_path):
        """Missing doc fails gracefully."""
        doc = tmp_path / "nonexistent.md"
        valid, error = validate_feature_doc(doc)
        assert valid is False
        assert "not found" in error

    def test_doc_without_title(self, tmp_path):
        """Doc without title heading fails."""
        doc = tmp_path / "feature.md"
        doc.write_text("Just some text without a heading.")
        valid, error = validate_feature_doc(doc)
        assert valid is False
        assert "missing title" in error

    def test_doc_too_short(self, tmp_path):
        """Doc with only title and no content fails."""
        doc = tmp_path / "feature.md"
        doc.write_text("# Title\n\nShort")
        valid, error = validate_feature_doc(doc)
        assert valid is False
        assert "too short" in error


class TestExtractFeatureDocPath:
    """Test feature doc path extraction from plan text."""

    def test_extracts_create_path(self):
        plan = textwrap.dedent("""\
            ## Documentation
            - [ ] Create `docs/features/my-feature.md` describing the feature
            - [ ] Update README index
        """)
        result = extract_feature_doc_path(plan)
        assert result == "docs/features/my-feature.md"

    def test_extracts_update_path(self):
        plan = textwrap.dedent("""\
            ## Documentation
            - [ ] Update `docs/features/existing.md` with new section
        """)
        result = extract_feature_doc_path(plan)
        assert result == "docs/features/existing.md"

    def test_no_documentation_section(self):
        plan = "## Other Section\nSome content"
        result = extract_feature_doc_path(plan)
        assert result is None


class TestEndToEndMigrationChain:
    """Integration test: full migration validation chain.

    Exercises the specific scenario that triggered the original bug:
    a feature named pm-dev-session-architecture with a README entry that
    says PM/Dev Session Architecture (not Pm Dev Session Architecture).
    """

    def test_full_chain_with_acronym_feature(self, tmp_path):
        """The migration chain works end-to-end with acronym-heavy filenames."""
        # Set up docs/features/ directory
        features_dir = tmp_path / "docs" / "features"
        features_dir.mkdir(parents=True)

        # Create the README index
        readme = features_dir / "README.md"
        readme.write_text(SAMPLE_README)

        # Create the feature doc
        feature_doc = features_dir / "pm-dev-session-architecture.md"
        feature_doc.write_text(
            "# PM/Teammate/Dev Session Architecture\n\n"
            "Session type discriminator splitting orchestration from execution.\n\n"
            "## Overview\nDetailed description of the architecture."
        )

        with _chdir(tmp_path):
            # Step 1: validate feature doc exists
            valid, error = validate_feature_doc(feature_doc)
            assert valid is True, f"Feature doc validation failed: {error}"

            # Step 2: extract name from index (the new way - Bug 1 fix)
            feature_name = extract_feature_name_from_index("pm-dev-session-architecture.md")
            assert feature_name is not None, "Failed to extract feature name"
            assert feature_name == "PM/Dev Session Architecture"

            # Step 3: validate the extracted name is in the index
            valid, error = validate_feature_index(feature_name)
            assert valid is True, f"Feature index validation failed: {error}"

            # Step 4: verify the OLD way (.title()) would have failed
            mangled_name = "pm-dev-session-architecture".replace("-", " ").title()
            assert mangled_name == "Pm Dev Session Architecture"
            # This would have failed because "Pm" != "PM"
            valid_old, _ = validate_feature_index(mangled_name)
            # The old approach fails: "Pm Dev Session Architecture"
            # won't match "PM/Dev Session Architecture" (missing "/")
            assert valid_old is False, "Old .title() approach should fail due to missing /"


class TestMigratePlanToCompleted:
    """Tests for the path-independent migrate_plan_to_completed() primitive.

    Uses a real temp git repo (matches the `docs/plans/{name}.md` layout the
    function derives its repo root from) rather than mocking git -- these
    guards (existence, clean-tree/HEAD==main, git mv) only mean something
    against a real repository.
    """

    def _init_repo(self, tmp_path: Path) -> Path:
        """Create a bare-bones git repo with docs/plans/ + docs/plans/completed/."""
        repo = tmp_path / "repo"
        (repo / "docs" / "plans" / "completed").mkdir(parents=True)
        _git(repo, "init", "-q", "-b", "main")
        _git(repo, "config", "user.email", "test@example.com")
        _git(repo, "config", "user.name", "Test")
        return repo

    def _write_plan(self, repo: Path, name: str, tracking_issue: int = 1900) -> Path:
        plan = repo / "docs" / "plans" / name
        plan.write_text(
            f"---\ntracking: https://github.com/tomcounsell/ai/issues/{tracking_issue}\n"
            f"---\n# {name}\n"
        )
        return plan

    def _commit_all(self, repo: Path, message: str = "init") -> None:
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", message)

    def test_closed_issue_plan_migrates(self, tmp_path):
        """A plan on a clean main branch is git-mv'd into completed/, not unlinked."""
        repo = self._init_repo(tmp_path)
        plan = self._write_plan(repo, "example-plan.md")
        self._commit_all(repo)

        verdict = migrate_plan_to_completed(plan, apply=True)

        assert verdict == "migrated"
        assert not plan.exists()
        completed = repo / "docs" / "plans" / "completed" / "example-plan.md"
        assert completed.exists()
        assert "example-plan.md" in completed.read_text()
        # Verify it was a tracked git mv, not a bare unlink: git status is clean
        # (the move + commit is fully recorded), and the file shows up under
        # completed/ in the git history for HEAD.
        status = _git(repo, "status", "--porcelain")
        assert status.stdout.strip() == ""
        log = _git(repo, "log", "--oneline", "-1")
        assert "Migrate completed plan" in log.stdout

    def test_already_migrated_is_idempotent(self, tmp_path):
        """Source absent + dest present -> 'already-migrated', not an error.

        git mv is NOT idempotent -- a second attempt on an already-moved plan
        must not look like a failure.
        """
        repo = self._init_repo(tmp_path)
        completed = repo / "docs" / "plans" / "completed" / "example-plan.md"
        completed.write_text("# already here\n")
        missing_plan = repo / "docs" / "plans" / "example-plan.md"

        verdict = migrate_plan_to_completed(missing_plan, apply=True)

        assert verdict == "already-migrated"
        assert completed.exists()
        assert completed.read_text() == "# already here\n"

    def test_dirty_tree_preserves_plan(self, tmp_path):
        """A dirty working tree blocks the git mv; the plan is never lost."""
        repo = self._init_repo(tmp_path)
        plan = self._write_plan(repo, "dirty-plan.md")
        self._commit_all(repo)
        # Make the tree dirty.
        plan.write_text(plan.read_text() + "\nuncommitted change\n")

        verdict = migrate_plan_to_completed(plan, apply=True)

        assert verdict == "dirty-tree-skip"
        assert plan.exists(), "plan must be preserved in place, never lost"
        completed = repo / "docs" / "plans" / "completed" / "dirty-plan.md"
        assert not completed.exists()

    def test_non_main_branch_preserves_plan(self, tmp_path):
        """Migration only runs on main; a feature branch is also a report-only skip."""
        repo = self._init_repo(tmp_path)
        plan = self._write_plan(repo, "branch-plan.md")
        self._commit_all(repo)
        _git(repo, "checkout", "-q", "-b", "session/some-feature")

        verdict = migrate_plan_to_completed(plan, apply=True)

        assert verdict == "dirty-tree-skip"
        assert plan.exists()

    def test_apply_false_reports_without_mutating(self, tmp_path):
        """apply=False (report-only) evaluates eligibility but moves nothing on disk."""
        repo = self._init_repo(tmp_path)
        plan = self._write_plan(repo, "dry-run-plan.md")
        self._commit_all(repo)

        verdict = migrate_plan_to_completed(plan, apply=False)

        assert verdict == "migrated"  # verdict describes what WOULD happen
        assert plan.exists(), "apply=False must not perform the git mv"
        completed = repo / "docs" / "plans" / "completed" / "dry-run-plan.md"
        assert not completed.exists()
        status = _git(repo, "status", "--porcelain")
        assert status.stdout.strip() == "", "apply=False must leave the tree untouched"


# --- Helpers ---


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    """Run a git subcommand rooted at `repo`, raising on unexpected failure."""
    result = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"git {' '.join(args)} failed: {result.stderr}"
    return result


@contextlib.contextmanager
def _chdir(path):
    """Context manager to temporarily change directory."""
    old = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


class TestRunIssueEvidenceGate:
    """The --issue CLI (Site D primary path) is evidence-gated like the sweep
    and the reflection: only a literally "closed" tracking issue migrates.

    PR #1903 review blocker: a multi-PR issue (PR 1 merged, issue open for
    PR 2) must keep its plan in root; a gh outage ("unknown") must defer.
    """

    def _setup(self, monkeypatch, tmp_path, state):
        import scripts.migrate_completed_plan as mcp

        plan = tmp_path / "docs" / "plans" / "some-plan.md"
        plan.parent.mkdir(parents=True)
        plan.write_text("---\ntracking: https://github.com/o/r/issues/42\n---\n# Plan\n")
        monkeypatch.setattr(mcp, "find_plan_by_issue", lambda n: plan)
        monkeypatch.setattr(mcp, "_gh_issue_state", lambda n: state)
        calls = []

        def fake_migrate(p, *, apply):
            calls.append((p, apply))
            return "migrated"

        monkeypatch.setattr(mcp, "migrate_plan_to_completed", fake_migrate)
        return mcp, calls

    def test_open_issue_skips_and_never_migrates(self, monkeypatch, tmp_path, capsys):
        mcp, calls = self._setup(monkeypatch, tmp_path, "open")
        rc = mcp.run_issue("42", apply=True)
        assert rc == 1
        assert calls == []
        assert "skipped-open" in capsys.readouterr().out

    def test_unknown_state_defers_never_migrates(self, monkeypatch, tmp_path, capsys):
        """A gh outage reads as "unknown" -- deferral, never a migration."""
        mcp, calls = self._setup(monkeypatch, tmp_path, "unknown")
        rc = mcp.run_issue("42", apply=True)
        assert rc == 1
        assert calls == []
        assert "skipped-open" in capsys.readouterr().out

    def test_closed_issue_migrates(self, monkeypatch, tmp_path):
        mcp, calls = self._setup(monkeypatch, tmp_path, "closed")
        rc = mcp.run_issue("42", apply=True)
        assert rc == 0
        assert len(calls) == 1
        assert calls[0][1] is True

    def test_no_plan_found_exits_2(self, monkeypatch, tmp_path):
        import scripts.migrate_completed_plan as mcp

        monkeypatch.setattr(mcp, "find_plan_by_issue", lambda n: None)
        gate_calls = []
        monkeypatch.setattr(mcp, "_gh_issue_state", lambda n: gate_calls.append(n) or "closed")
        assert mcp.run_issue("999", apply=True) == 2
