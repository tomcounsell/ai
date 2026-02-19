"""Tests for daydream multi-repo support.

Tests cover:
- load_local_projects() filters to directories that exist on this machine
- step_review_logs() iterates per-project logs dirs
- step_clean_tasks() runs gh issue list per-project
- step_create_github_issue() creates issues per-project with cwd support
- step_post_to_telegram() posts per-project (skips gracefully when unconfigured)
- step_clean_legacy() bug fix (cache_dirs and pyc_files variables)
- DaydreamRunner.__init__ loads self.projects
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# --- load_local_projects() tests ---


class TestLoadLocalProjects:
    """Tests for load_local_projects() filtering."""

    def test_returns_projects_whose_directory_exists(self, tmp_path):
        """Only projects with existing working_directory are returned."""
        from scripts.daydream import load_local_projects

        existing_dir = tmp_path / "project_a"
        existing_dir.mkdir()
        missing_dir = tmp_path / "project_b_does_not_exist"

        config = {
            "projects": {
                "proj-a": {
                    "name": "Project A",
                    "working_directory": str(existing_dir),
                },
                "proj-b": {
                    "name": "Project B",
                    "working_directory": str(missing_dir),
                },
            }
        }
        config_path = tmp_path / "projects.json"
        config_path.write_text(json.dumps(config))

        with patch("scripts.daydream.AI_ROOT", tmp_path):
            # Patch config path resolution
            with patch("scripts.daydream.load_local_projects.__wrapped__", create=True):
                pass

        # Call directly with monkeypatched config file
        import scripts.daydream as dmod

        orig_ai_root = dmod.AI_ROOT
        dmod.AI_ROOT = tmp_path
        try:
            # Also need to create a projects.json in the patched root
            config_dir = tmp_path / "config"
            config_dir.mkdir(exist_ok=True)
            (config_dir / "projects.json").write_text(json.dumps(config))
            projects = load_local_projects()
        finally:
            dmod.AI_ROOT = orig_ai_root

        slugs = [p["slug"] for p in projects]
        assert "proj-a" in slugs
        assert "proj-b" not in slugs

    def test_includes_slug_in_project_dict(self, tmp_path):
        """Each project dict includes 'slug' key from config key."""
        from scripts.daydream import load_local_projects

        existing_dir = tmp_path / "my_project"
        existing_dir.mkdir()

        config = {
            "projects": {
                "my-slug": {
                    "name": "My Project",
                    "working_directory": str(existing_dir),
                }
            }
        }

        import scripts.daydream as dmod

        orig_ai_root = dmod.AI_ROOT
        dmod.AI_ROOT = tmp_path
        try:
            config_dir = tmp_path / "config"
            config_dir.mkdir(exist_ok=True)
            (config_dir / "projects.json").write_text(json.dumps(config))
            projects = load_local_projects()
        finally:
            dmod.AI_ROOT = orig_ai_root

        assert len(projects) == 1
        assert projects[0]["slug"] == "my-slug"
        assert projects[0]["name"] == "My Project"

    def test_returns_empty_list_when_no_projects_exist(self, tmp_path):
        """Returns empty list when no configured projects have existing dirs."""
        from scripts.daydream import load_local_projects

        config = {
            "projects": {
                "ghost": {
                    "name": "Ghost",
                    "working_directory": str(tmp_path / "does_not_exist"),
                }
            }
        }

        import scripts.daydream as dmod

        orig_ai_root = dmod.AI_ROOT
        dmod.AI_ROOT = tmp_path
        try:
            config_dir = tmp_path / "config"
            config_dir.mkdir(exist_ok=True)
            (config_dir / "projects.json").write_text(json.dumps(config))
            projects = load_local_projects()
        finally:
            dmod.AI_ROOT = orig_ai_root

        assert projects == []


# --- DaydreamRunner.projects attribute ---


class TestDaydreamRunnerProjects:
    """Tests that DaydreamRunner loads self.projects on init."""

    def test_runner_has_projects_attribute(self):
        """DaydreamRunner has self.projects populated on init."""
        from scripts.daydream import DaydreamRunner

        runner = DaydreamRunner()
        assert hasattr(runner, "projects")
        assert isinstance(runner.projects, list)

    def test_runner_projects_are_dicts(self):
        """Each project in self.projects is a dict with at least 'slug' and 'working_directory'."""
        from scripts.daydream import DaydreamRunner

        runner = DaydreamRunner()
        for project in runner.projects:
            assert isinstance(project, dict)
            assert "slug" in project
            assert "working_directory" in project


# --- step_clean_legacy bug fix ---


class TestStepCleanLegacyBugFix:
    """Tests that step_clean_legacy no longer crashes with undefined cache_dirs/pyc_files."""

    @pytest.mark.asyncio
    @patch("scripts.daydream.subprocess.run")
    async def test_step_clean_legacy_runs_without_error(self, mock_run):
        """step_clean_legacy completes without NameError."""
        from scripts.daydream import DaydreamRunner

        mock_run.return_value = MagicMock(returncode=0, stdout="")

        runner = DaydreamRunner()
        runner.state.findings = {}
        runner.state.step_progress = {}

        # This should not raise NameError
        await runner.step_clean_legacy()

        assert "clean_legacy" in runner.state.step_progress
        assert "cache_dirs" in runner.state.step_progress["clean_legacy"]
        assert "pyc_files" in runner.state.step_progress["clean_legacy"]

    @pytest.mark.asyncio
    @patch("scripts.daydream.subprocess.run")
    async def test_step_clean_legacy_records_counts(self, mock_run):
        """step_clean_legacy records cache_dirs and pyc_files as 0."""
        from scripts.daydream import DaydreamRunner

        mock_run.return_value = MagicMock(returncode=0, stdout="")

        runner = DaydreamRunner()
        runner.state.step_progress = {}
        await runner.step_clean_legacy()

        progress = runner.state.step_progress["clean_legacy"]
        assert progress["cache_dirs"] == 0
        assert progress["pyc_files"] == 0


# --- step_review_logs multi-repo ---


class TestStepReviewLogsMultiRepo:
    """Tests for per-project log review."""

    @pytest.mark.asyncio
    async def test_review_logs_iterates_per_project(self, tmp_path):
        """step_review_logs checks logs dir for each project."""
        from scripts.daydream import DaydreamRunner

        # Create two project dirs with logs
        proj_a = tmp_path / "proj_a"
        proj_a_logs = proj_a / "logs"
        proj_a_logs.mkdir(parents=True)
        (proj_a_logs / "app.log").write_text(
            "2026-02-16 10:00:00 - mod - ERROR - Something broke\n"
        )

        proj_b = tmp_path / "proj_b"
        proj_b_logs = proj_b / "logs"
        proj_b_logs.mkdir(parents=True)
        (proj_b_logs / "server.log").write_text("INFO: all good\n")

        projects = [
            {"slug": "proj-a", "working_directory": str(proj_a)},
            {"slug": "proj-b", "working_directory": str(proj_b)},
        ]

        runner = DaydreamRunner()
        runner.projects = projects
        runner.state.findings = {}
        runner.state.step_progress = {}

        await runner.step_review_logs()

        # Should have findings namespaced per project
        finding_keys = list(runner.state.findings.keys())
        assert any("proj-a" in k for k in finding_keys)

    @pytest.mark.asyncio
    async def test_review_logs_skips_project_without_logs_dir(self, tmp_path):
        """Projects without a logs directory are noted and skipped."""
        from scripts.daydream import DaydreamRunner

        proj = tmp_path / "proj_no_logs"
        proj.mkdir()  # No logs subdir

        runner = DaydreamRunner()
        runner.projects = [{"slug": "proj-no-logs", "working_directory": str(proj)}]
        runner.state.findings = {}
        runner.state.step_progress = {}

        # Should not raise
        await runner.step_review_logs()

    @pytest.mark.asyncio
    async def test_review_logs_namespaces_findings(self, tmp_path):
        """Findings are namespaced with '{slug}:log_review'."""
        from scripts.daydream import DaydreamRunner

        proj = tmp_path / "proj_ns"
        logs = proj / "logs"
        logs.mkdir(parents=True)
        (logs / "test.log").write_text(
            "2026-02-16 10:00:00 - mod - ERROR - Test error\n"
        )

        runner = DaydreamRunner()
        runner.projects = [{"slug": "my-proj", "working_directory": str(proj)}]
        runner.state.findings = {}
        runner.state.step_progress = {}

        await runner.step_review_logs()

        assert "my-proj:log_review" in runner.state.findings


# --- step_clean_tasks multi-repo ---


class TestStepCleanTasksMultiRepo:
    """Tests for per-project task cleanup."""

    @pytest.mark.asyncio
    @patch("scripts.daydream.subprocess.run")
    async def test_clean_tasks_runs_gh_per_project(self, mock_run, tmp_path):
        """step_clean_tasks calls gh issue list for each project with github config."""
        from scripts.daydream import DaydreamRunner

        proj_dir = tmp_path / "proj"
        proj_dir.mkdir()

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="42\tsome bug\topen\tbug\n",
        )

        projects = [
            {
                "slug": "my-proj",
                "working_directory": str(proj_dir),
                "github": {"org": "testorg", "repo": "testrepo"},
            }
        ]

        runner = DaydreamRunner()
        runner.projects = projects
        runner.state.findings = {}
        runner.state.step_progress = {}

        await runner.step_clean_tasks()

        assert mock_run.called
        # Verify cwd was set to project working directory
        call_kwargs = mock_run.call_args[1]
        assert call_kwargs.get("cwd") == str(proj_dir)

    @pytest.mark.asyncio
    @patch("scripts.daydream.subprocess.run")
    async def test_clean_tasks_skips_project_without_github(self, mock_run, tmp_path):
        """Projects without github config are skipped for gh CLI calls."""
        from scripts.daydream import DaydreamRunner

        proj_dir = tmp_path / "proj_no_gh"
        proj_dir.mkdir()

        projects = [
            {
                "slug": "no-github-proj",
                "working_directory": str(proj_dir),
                # No "github" key
            }
        ]

        runner = DaydreamRunner()
        runner.projects = projects
        runner.state.findings = {}
        runner.state.step_progress = {}

        await runner.step_clean_tasks()

        # gh should not have been called for this project
        for call in mock_run.call_args_list:
            args = call[0][0]
            # If gh was called, the cwd should not be our project dir
            if "gh" in args:
                assert call[1].get("cwd") != str(proj_dir)

    @pytest.mark.asyncio
    @patch("scripts.daydream.subprocess.run")
    async def test_clean_tasks_namespaces_findings_per_project(
        self, mock_run, tmp_path
    ):
        """Findings are namespaced with '{slug}:tasks'."""
        from scripts.daydream import DaydreamRunner

        proj_dir = tmp_path / "proj_tasks"
        proj_dir.mkdir()

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="1\tbug title\topen\tbug\n",
        )

        projects = [
            {
                "slug": "task-proj",
                "working_directory": str(proj_dir),
                "github": {"org": "org", "repo": "repo"},
            }
        ]

        runner = DaydreamRunner()
        runner.projects = projects
        runner.state.findings = {}
        runner.state.step_progress = {}

        await runner.step_clean_tasks()

        assert "task-proj:tasks" in runner.state.findings


# --- step_create_github_issue multi-repo ---


class TestStepCreateGithubIssueMultiRepo:
    """Tests for per-project GitHub issue creation."""

    @pytest.mark.asyncio
    @patch("scripts.daydream.create_daydream_issue")
    async def test_creates_issue_per_project_with_github(self, mock_create, tmp_path):
        """Creates an issue for each project that has github config."""
        from scripts.daydream import DaydreamRunner

        proj_dir = tmp_path / "proj_gh"
        proj_dir.mkdir()

        mock_create.return_value = "https://github.com/org/repo/issues/1"

        projects = [
            {
                "slug": "gh-proj",
                "working_directory": str(proj_dir),
                "github": {"org": "org", "repo": "repo"},
            }
        ]

        runner = DaydreamRunner()
        runner.projects = projects
        runner.state.findings = {"gh-proj:log_review": ["some finding"]}
        runner.state.step_progress = {}

        with patch.object(runner, "step_post_to_telegram", new=AsyncMock()):
            await runner.step_create_github_issue()

        mock_create.assert_called_once()
        # cwd should be passed to create_daydream_issue
        call_kwargs = mock_create.call_args[1]
        assert "cwd" in call_kwargs

    @pytest.mark.asyncio
    @patch("scripts.daydream.create_daydream_issue")
    async def test_skips_project_without_github_config(self, mock_create, tmp_path):
        """Skips issue creation for projects without github config."""
        from scripts.daydream import DaydreamRunner

        proj_dir = tmp_path / "proj_no_gh"
        proj_dir.mkdir()

        projects = [
            {
                "slug": "no-gh",
                "working_directory": str(proj_dir),
                # No github key
            }
        ]

        runner = DaydreamRunner()
        runner.projects = projects
        runner.state.findings = {"no-gh:log_review": ["something"]}
        runner.state.step_progress = {}

        await runner.step_create_github_issue()

        mock_create.assert_not_called()

    @pytest.mark.asyncio
    @patch("scripts.daydream.create_daydream_issue")
    async def test_skips_when_no_per_project_findings(self, mock_create, tmp_path):
        """Skips issue creation when project has no findings."""
        from scripts.daydream import DaydreamRunner

        proj_dir = tmp_path / "proj_empty"
        proj_dir.mkdir()

        projects = [
            {
                "slug": "empty-proj",
                "working_directory": str(proj_dir),
                "github": {"org": "org", "repo": "repo"},
            }
        ]

        runner = DaydreamRunner()
        runner.projects = projects
        runner.state.findings = {}  # No findings
        runner.state.step_progress = {}

        await runner.step_create_github_issue()

        mock_create.assert_not_called()


# --- step_post_to_telegram ---


class TestStepPostToTelegram:
    """Tests for per-project Telegram posting."""

    @pytest.mark.asyncio
    async def test_skips_when_no_telegram_groups(self):
        """Skips posting when project has no telegram.groups configured."""
        from scripts.daydream import DaydreamRunner

        runner = DaydreamRunner()
        project = {
            "slug": "no-tg",
            "working_directory": "/tmp",
            # No telegram key
        }
        # Should not raise
        await runner.step_post_to_telegram(project, "")

    @pytest.mark.asyncio
    async def test_skips_when_no_session_file(self, tmp_path):
        """Skips when valor.session file does not exist."""
        import scripts.daydream as dmod

        # Set up a minimal config dir so DaydreamRunner() can be constructed
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "projects.json").write_text(json.dumps({"projects": {}}))

        orig_ai_root = dmod.AI_ROOT
        dmod.AI_ROOT = tmp_path  # No data/valor.session here
        try:
            runner = dmod.DaydreamRunner()
            project = {
                "slug": "tg-proj",
                "working_directory": "/tmp",
                "telegram": {"groups": ["Dev: Test"]},
            }
            # Should not raise, just log and return
            await runner.step_post_to_telegram(project, "")
        finally:
            dmod.AI_ROOT = orig_ai_root

    @pytest.mark.asyncio
    async def test_skips_when_no_telegram_credentials(self, tmp_path):
        """Skips when TELEGRAM_API_ID or TELEGRAM_API_HASH is missing."""
        import os

        import scripts.daydream as dmod

        # Set up config dir and fake session file
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "projects.json").write_text(json.dumps({"projects": {}}))
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        session_file = data_dir / "valor.session"
        session_file.write_text("fake_session_data")

        orig_ai_root = dmod.AI_ROOT
        dmod.AI_ROOT = tmp_path
        try:
            runner = dmod.DaydreamRunner()
            project = {
                "slug": "tg-proj",
                "working_directory": "/tmp",
                "telegram": {"groups": ["Dev: Test"]},
            }
            # No TELEGRAM_API_ID/HASH in env — should skip gracefully
            env_backup = {}
            for key in ["TELEGRAM_API_ID", "TELEGRAM_API_HASH"]:
                env_backup[key] = os.environ.pop(key, None)

            try:
                await runner.step_post_to_telegram(
                    project, "https://github.com/issue/1"
                )
            finally:
                for key, val in env_backup.items():
                    if val is not None:
                        os.environ[key] = val
        finally:
            dmod.AI_ROOT = orig_ai_root


# --- daydream_report.py cwd parameter ---


class TestCreateDaydreamIssueCwd:
    """Tests that create_daydream_issue accepts and uses cwd parameter."""

    @patch("scripts.daydream_report.subprocess.run")
    @patch("scripts.daydream_report.issue_exists_for_date", return_value=False)
    def test_create_issue_passes_cwd_to_subprocess(self, mock_exists, mock_run):
        """create_daydream_issue passes cwd to subprocess.run calls."""
        from scripts.daydream_report import create_daydream_issue

        mock_run.return_value = MagicMock(
            returncode=0, stdout="https://github.com/org/repo/issues/1\n"
        )
        findings = {"test": ["finding"]}
        result = create_daydream_issue(findings, "2026-02-16", cwd="/tmp/myproject")

        assert result is not None  # URL or True
        # The subprocess call for issue create should use the given cwd
        create_call = None
        for call in mock_run.call_args_list:
            args = call[0][0]
            if "create" in args:
                create_call = call
                break
        assert create_call is not None
        assert create_call[1].get("cwd") == "/tmp/myproject"

    @patch("scripts.daydream_report.subprocess.run")
    @patch("scripts.daydream_report.issue_exists_for_date", return_value=False)
    def test_create_issue_without_cwd_still_works(self, mock_exists, mock_run):
        """create_daydream_issue works without cwd (backward compatible)."""
        from scripts.daydream_report import create_daydream_issue

        mock_run.return_value = MagicMock(
            returncode=0, stdout="https://github.com/org/repo/issues/2\n"
        )
        findings = {"test": ["finding"]}
        result = create_daydream_issue(findings, "2026-02-16")
        assert result is not None
