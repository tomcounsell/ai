"""Tests for SDLC stub file creation via the migration system."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.update.migrations import (
    _SDLC_STUBS,
    MIGRATIONS,
    _migrate_create_sdlc_stubs,
    run_pending_migrations,
)

EXPECTED_STUBS = [
    "do-plan.md",
    "do-plan-critique.md",
    "do-build.md",
    "do-test.md",
    "do-patch.md",
    "do-pr-review.md",
    "do-docs.md",
    "do-merge.md",
]


class TestMigrateCreateSdlcStubs:
    """Tests for _migrate_create_sdlc_stubs migration function."""

    def test_creates_all_eight_stubs(self, tmp_path: Path) -> None:
        """Migration creates all 8 docs/sdlc/ stub files."""
        error = _migrate_create_sdlc_stubs(tmp_path)

        assert error is None
        sdlc_dir = tmp_path / "docs" / "sdlc"
        assert sdlc_dir.is_dir()
        for stub in EXPECTED_STUBS:
            assert (sdlc_dir / stub).exists(), f"Missing stub: {stub}"

    def test_stubs_have_comment_header(self, tmp_path: Path) -> None:
        """Each created stub contains the required comment header."""
        _migrate_create_sdlc_stubs(tmp_path)

        sdlc_dir = tmp_path / "docs" / "sdlc"
        for stub in EXPECTED_STUBS:
            content = (sdlc_dir / stub).read_text()
            assert "Do not duplicate content from the global skill" in content, (
                f"{stub} missing comment header"
            )

    def test_idempotent_does_not_overwrite_existing(self, tmp_path: Path) -> None:
        """Re-running migration does not overwrite files that already have content."""
        # First run
        _migrate_create_sdlc_stubs(tmp_path)

        # Modify one file
        do_plan = tmp_path / "docs" / "sdlc" / "do-plan.md"
        original_content = do_plan.read_text()
        custom_content = original_content + "\n## Custom Note\nThis was hand-authored.\n"
        do_plan.write_text(custom_content)

        # Second run should not overwrite
        error = _migrate_create_sdlc_stubs(tmp_path)
        assert error is None
        assert do_plan.read_text() == custom_content, "Migration overwrote existing file"

    def test_creates_docs_sdlc_directory(self, tmp_path: Path) -> None:
        """Migration creates docs/sdlc/ directory if it does not exist."""
        assert not (tmp_path / "docs" / "sdlc").exists()
        _migrate_create_sdlc_stubs(tmp_path)
        assert (tmp_path / "docs" / "sdlc").is_dir()

    def test_stub_list_matches_expected(self) -> None:
        """_SDLC_STUBS constant matches the expected 8 stage names."""
        expected_names = {s.removesuffix(".md") for s in EXPECTED_STUBS}
        assert set(_SDLC_STUBS) == expected_names

    def test_returns_none_on_success(self, tmp_path: Path) -> None:
        """Migration function returns None (no error) on success."""
        result = _migrate_create_sdlc_stubs(tmp_path)
        assert result is None


class TestMigrationRegistry:
    """Tests for create_sdlc_stubs in the MIGRATIONS registry."""

    def test_create_sdlc_stubs_registered(self) -> None:
        """create_sdlc_stubs is in the MIGRATIONS dict."""
        assert "create_sdlc_stubs" in MIGRATIONS

    def test_create_sdlc_stubs_has_description(self) -> None:
        """create_sdlc_stubs entry has a non-empty description."""
        _, description = MIGRATIONS["create_sdlc_stubs"]
        assert description and len(description) > 10

    def test_run_pending_migrations_creates_stubs(self, tmp_path: Path) -> None:
        """run_pending_migrations() runs the create_sdlc_stubs migration."""
        # Seed data dir so other migrations (e.g. keyfield_rename) are skipped
        completed_path = tmp_path / "data" / "migrations_completed.json"
        completed_path.parent.mkdir(parents=True)
        completed_path.write_text(json.dumps(["agent_session_keyfield_rename"]) + "\n")

        result = run_pending_migrations(tmp_path)

        assert "create_sdlc_stubs" in result.ran
        assert (tmp_path / "docs" / "sdlc" / "do-plan.md").exists()

    def test_run_pending_migrations_idempotent(self, tmp_path: Path) -> None:
        """Running migrations twice skips create_sdlc_stubs the second time."""
        completed_path = tmp_path / "data" / "migrations_completed.json"
        completed_path.parent.mkdir(parents=True)
        completed_path.write_text(json.dumps(["agent_session_keyfield_rename"]) + "\n")

        run_pending_migrations(tmp_path)
        result2 = run_pending_migrations(tmp_path)

        assert "create_sdlc_stubs" in result2.skipped
        assert "create_sdlc_stubs" not in result2.ran


class TestGracefulDegradation:
    """Tests that missing addendum files do not raise errors."""

    def test_missing_addendum_file_is_safe(self, tmp_path: Path) -> None:
        """Checking for a missing addendum file (e.g., in skill logic) does not raise."""
        addendum_path = tmp_path / "docs" / "sdlc" / "do-plan.md"

        # Simulate what a skill would do: check for existence before reading
        if addendum_path.exists():
            content = addendum_path.read_text()
        else:
            content = ""

        # Should reach here without exception
        assert content == ""

    def test_stub_files_in_real_repo(self) -> None:
        """All 8 stub files exist in the actual docs/sdlc/ directory."""
        repo_root = Path(__file__).parent.parent.parent
        sdlc_dir = repo_root / "docs" / "sdlc"

        if not sdlc_dir.exists():
            pytest.skip("docs/sdlc/ not yet created — run /update first")

        for stub in EXPECTED_STUBS:
            assert (sdlc_dir / stub).exists(), f"Missing in real repo: {stub}"
