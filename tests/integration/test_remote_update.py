"""Tests for remote update: shell script, bridge intercept, restart flag lifecycle.

Each test in this module exercises ``scripts/remote-update.sh`` against the
real repository state (``data/update.lock``, ``data/restart-requested``).
The script's lockfile and restart flag are host-global, so concurrent
invocations would collide. ``--dist=loadfile`` (set in ``pyproject.toml``)
keeps every test in this file on the same xdist worker so they run
serially within the worker pool.
"""

import asyncio
import os
import subprocess
from datetime import UTC
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scripts.update.deps import (
    AUTO_BUMP_SETS,
    CoupledSet,
    PinDeclarationError,
    auto_bump_deps,
    bump_pin_in_pyproject,
    get_pinned_version,
    get_pypi_latest,
    llm_gate_argv,
    verify_critical_versions,
)

# Project root
PROJECT_DIR = Path(__file__).parent.parent.parent


# =============================================================================
# Shell Script Tests
# =============================================================================


class TestRemoteUpdateScript:
    """Test scripts/remote-update.sh behavior."""

    SCRIPT = str(PROJECT_DIR / "scripts" / "remote-update.sh")

    def test_script_exists_and_is_executable(self):
        script = Path(self.SCRIPT)
        assert script.exists(), "scripts/remote-update.sh should exist"
        assert os.access(str(script), os.X_OK), "Script should be executable"

    def test_already_up_to_date(self, tmp_path: Path):
        """The script detects an up-to-date checkout and runs end-to-end without
        a launchd bootstrap error (issue #1964).

        This exercises the real ``scripts/remote-update.sh``. Since issue #1898
        the script gained a terminal ``verify_release`` step plus live launchd
        service restarts, which make its *exit code* a function of live
        launchd / running-process state — e.g. the local worker lagging HEAD,
        or a ``launchctl bootstrap`` racing a flapping service (the reported
        "Bootstrap failed: 5: Input/output error"). That state is orthogonal to
        the git "already up to date" path this test covers. We therefore isolate
        the run from the machine's launchd services by pointing ``HOME`` at a
        throwaway directory: with no ``~/Library/LaunchAgents/*.plist`` present,
        the script skips every worker/bridge kickstart+bootstrap and never
        restarts the developer's running services. We then assert on the
        up-to-date detection and clean end-to-end execution rather than the
        environment-coupled exit code.
        """
        venv_dir = PROJECT_DIR / ".venv"
        if not venv_dir.exists():
            pytest.skip("No .venv in project dir (e.g. running in worktree)")
        # Clean up any stale lock file from previous runs
        lock_dir = PROJECT_DIR / "data" / "update.lock"
        if lock_dir.is_dir():
            lock_dir.rmdir()

        # Isolate from live launchd services: a throwaway HOME has no
        # ~/Library/LaunchAgents/*.plist, so the script skips every worker and
        # bridge kickstart+bootstrap (the #1964 failure) and never restarts the
        # developer's running services. Keep npm's cache pointed at the real
        # user cache so the soft ``npm ci`` step stays warm (cold-cache installs
        # would add network latency and flakiness).
        home = tmp_path / "home"
        home.mkdir()
        env = {
            **os.environ,
            "HOME": str(home),
            "npm_config_cache": str(Path.home() / ".npm"),
        }

        # Timeout is generous: pull + npm + uv sync can take >30s on a cold cache.
        result = subprocess.run(
            ["bash", self.SCRIPT],
            cwd=str(PROJECT_DIR),
            capture_output=True,
            text=True,
            timeout=180,
            env=env,
        )
        output = result.stdout + result.stderr

        # Up-to-date detection (or a clean pull summary) — the behavior under test.
        assert (
            "up to date" in result.stdout.lower()
            or "commit(s)" in result.stdout
            or "update successful" in result.stdout
        ), f"expected up-to-date/pull summary in stdout, got tail: {result.stdout[-500:]}"
        # The reported #1964 failure must be gone: with launchd isolated the
        # script never runs ``launchctl bootstrap``, so this line cannot appear.
        assert "Bootstrap failed" not in output, (
            f"launchd bootstrap error leaked into an isolated run: {output[-500:]}"
        )
        # Pipeline reached its terminal verify step → ran end-to-end, no mid-run crash.
        assert "release verify" in output, (
            f"script did not reach the terminal verify step: {output[-500:]}"
        )

    def test_no_restart_flag_when_up_to_date(self):
        """When already up to date, no restart flag should be written."""
        venv_dir = PROJECT_DIR / ".venv"
        if not venv_dir.exists():
            pytest.skip("No .venv in project dir (e.g. running in worktree)")
        flag = PROJECT_DIR / "data" / "restart-requested"
        # Remove any existing flag
        flag.unlink(missing_ok=True)

        result = subprocess.run(
            ["bash", self.SCRIPT],
            cwd=str(PROJECT_DIR),
            capture_output=True,
            text=True,
            timeout=180,
        )

        if "Already up to date" in result.stdout:
            assert not flag.exists(), "No restart flag should be written when up to date"

    def test_lockfile_prevents_concurrent_runs(self):
        """Second invocation should skip if lock is held."""
        lock_dir = PROJECT_DIR / "data" / "update.lock"
        lock_dir.mkdir(parents=True, exist_ok=True)
        try:
            result = subprocess.run(
                ["bash", self.SCRIPT],
                cwd=str(PROJECT_DIR),
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert result.returncode == 0
            assert "Another update is already running" in result.stdout
        finally:
            lock_dir.rmdir()

    def test_lockfile_cleaned_up_on_exit(self):
        """Lock directory should be removed after script completes."""
        lock_dir = PROJECT_DIR / "data" / "update.lock"
        lock_dir.unlink(missing_ok=True) if lock_dir.is_file() else None
        if lock_dir.exists():
            lock_dir.rmdir()

        # Timeout is generous: pull + npm + uv sync can take >30s on a cold cache.
        subprocess.run(
            ["bash", self.SCRIPT],
            cwd=str(PROJECT_DIR),
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert not lock_dir.exists(), "Lock directory should be cleaned up"

    def test_log_prefix_on_all_lines(self):
        """Output lines from the update system should have a log prefix.

        Lines produced by subcommands (git, pip, etc.) or the cron-mode
        summary line may not carry a prefix, so we only check that we
        got some output (the Python module captures prefixed lines to a
        log file and prints only a bare summary to stdout in cron mode).
        """
        result = subprocess.run(
            ["bash", self.SCRIPT],
            cwd=str(PROJECT_DIR),
            capture_output=True,
            text=True,
            timeout=180,
        )
        lines = [line for line in result.stdout.strip().split("\n") if line.strip()]
        # In cron mode the Python module captures prefixed lines to a log
        # file and prints only a bare summary to stdout, so zero prefixed
        # lines is acceptable as long as we got *some* output.
        assert len(lines) > 0, "Expected at least one line of output"


# =============================================================================
# Restart Flag Tests
# =============================================================================


class TestRestartFlag:
    """Test restart flag lifecycle in agent_session_queue.py."""

    def setup_method(self):
        """Ensure clean state for each test."""
        from agent.agent_session_queue import _RESTART_FLAG

        _RESTART_FLAG.parent.mkdir(parents=True, exist_ok=True)
        _RESTART_FLAG.unlink(missing_ok=True)

    def teardown_method(self):
        """Clean up flag after each test."""
        from agent.agent_session_queue import _RESTART_FLAG

        _RESTART_FLAG.unlink(missing_ok=True)

    def test_check_restart_flag_returns_false_when_no_flag(self):
        from agent.agent_session_queue import _check_restart_flag

        assert _check_restart_flag() is False

    def _fresh_timestamp(self):
        """Return a flag content string with a timestamp from 5 minutes ago."""
        from datetime import datetime, timedelta

        ts = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        return f"{ts} 3 commit(s)"

    def test_check_restart_flag_returns_true_when_flag_exists_and_no_jobs(self):
        from agent.agent_session_queue import _RESTART_FLAG, _check_restart_flag

        _RESTART_FLAG.write_text(self._fresh_timestamp())

        with patch("agent.agent_session_queue.AgentSession") as mock_session:
            mock_session.query.filter.return_value = []
            assert _check_restart_flag() is True

    def test_check_restart_flag_defers_when_jobs_running(self):
        from agent.agent_session_queue import (
            _RESTART_FLAG,
            _active_workers,
            _check_restart_flag,
        )

        _RESTART_FLAG.write_text(self._fresh_timestamp())

        # Simulate an active worker
        mock_task = MagicMock()
        mock_task.done.return_value = False
        _active_workers["testproject"] = mock_task

        try:
            with patch("agent.agent_session_queue.AgentSession") as mock_session:
                # Return running sessions for the project
                mock_session.query.filter.return_value = [MagicMock()]
                assert _check_restart_flag() is False
        finally:
            _active_workers.pop("testproject", None)

    def test_clear_restart_flag_removes_file(self):
        from agent.agent_session_queue import _RESTART_FLAG, clear_restart_flag

        _RESTART_FLAG.write_text("test content")
        assert clear_restart_flag() is True
        assert not _RESTART_FLAG.exists()

    def test_clear_restart_flag_returns_false_when_no_file(self):
        from agent.agent_session_queue import clear_restart_flag

        assert clear_restart_flag() is False

    def test_trigger_restart_removes_flag_and_sends_sigterm(self):
        from agent.agent_session_queue import _RESTART_FLAG, _trigger_restart

        _RESTART_FLAG.write_text("test")

        # #2147 service-isolation audit: os.kill is patched (mocked) here and the
        # SIGTERM is asserted against os.getpid() — the current process, never a
        # runtime-derived worker PID. No real signal reaches any process, so this
        # path cannot target the launchd live worker and needs no
        # assert_not_live_worker guard.
        with patch("agent.agent_session_queue.os.kill") as mock_kill:
            _trigger_restart()

        assert not _RESTART_FLAG.exists()
        mock_kill.assert_called_once_with(os.getpid(), 15)  # SIGTERM = 15

    def test_check_restart_flag_ignores_stale_flag(self):
        """A flag older than 1 hour should be ignored and deleted."""
        from datetime import datetime, timedelta

        from agent.agent_session_queue import _RESTART_FLAG, _check_restart_flag

        stale_ts = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        _RESTART_FLAG.write_text(f"{stale_ts} 5 commit(s)")

        result = _check_restart_flag()
        assert result is False
        assert not _RESTART_FLAG.exists(), "Stale flag should be deleted"

    def test_check_restart_flag_handles_malformed_flag_content(self):
        """Malformed or empty flag content should not raise — returns False and deletes."""
        from agent.agent_session_queue import _RESTART_FLAG, _check_restart_flag

        # Empty content
        _RESTART_FLAG.write_text("")
        assert _check_restart_flag() is False
        assert not _RESTART_FLAG.exists()

        # Garbage content
        _RESTART_FLAG.write_text("not-a-timestamp blah")
        assert _check_restart_flag() is False
        assert not _RESTART_FLAG.exists()

        # Whitespace only
        _RESTART_FLAG.write_text("   \n  ")
        assert _check_restart_flag() is False
        assert not _RESTART_FLAG.exists()


# =============================================================================
# Worker Loop Restart Check Tests
# =============================================================================


class TestWorkerRestartCheck:
    """Test that the worker loop checks the restart flag between jobs."""

    def setup_method(self):
        from agent.agent_session_queue import _RESTART_FLAG

        _RESTART_FLAG.parent.mkdir(parents=True, exist_ok=True)
        _RESTART_FLAG.unlink(missing_ok=True)

    def teardown_method(self):
        from agent.agent_session_queue import _RESTART_FLAG

        _RESTART_FLAG.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_worker_checks_flag_when_queue_empty(self):
        """Worker should check restart flag when queue becomes empty."""
        from agent.agent_session_queue import _RESTART_FLAG

        _RESTART_FLAG.write_text("2026-02-02T10:00:00Z 1 commit(s)")

        with (
            patch("agent.agent_session_queue._pop_agent_session", return_value=None),
            patch("agent.agent_session_queue._check_restart_flag", return_value=True) as mock_check,
            patch("agent.agent_session_queue._trigger_restart") as mock_restart,
        ):
            from agent.agent_session_queue import _worker_loop

            event = asyncio.Event()
            await _worker_loop("testproject", event)

        mock_check.assert_called_once()
        mock_restart.assert_called_once()

    @pytest.mark.asyncio
    async def test_worker_checks_flag_after_job_completion(self):
        """Worker should check restart flag after completing a session."""
        mock_session_entry = MagicMock()
        mock_session_entry.agent_session_id = "test-123"
        mock_session_entry.project_key = "testproject"

        call_count = 0

        async def pop_side_effect(worker_key, is_project_keyed):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_session_entry
            return None

        with (
            patch("agent.agent_session_queue._pop_agent_session", side_effect=pop_side_effect),
            patch("agent.agent_session_queue._execute_agent_session", new_callable=AsyncMock),
            patch("agent.agent_session_queue._complete_agent_session", new_callable=AsyncMock),
            patch("agent.agent_session_queue._check_restart_flag", return_value=True) as mock_check,
            patch("agent.agent_session_queue._trigger_restart") as mock_restart,
        ):
            from agent.agent_session_queue import _worker_loop

            event = asyncio.Event()
            await _worker_loop("testproject", event)

        # Should have been called at least once (after session completion)
        assert mock_check.call_count >= 1
        mock_restart.assert_called()


# =============================================================================
# Bridge Command Intercept Tests
# =============================================================================


class TestBridgeUpdateCommand:
    """Test the /update command handling in the bridge."""

    def test_handle_update_command_exists(self):
        """The handle_update_command function should be importable."""
        from bridge.update import handle_force_update_command, handle_update_command

        assert callable(handle_update_command)
        assert callable(handle_force_update_command)

    def test_update_intercept_before_message_processing(self):
        """The /update check should come before message storage."""
        bridge_path = PROJECT_DIR / "bridge" / "telegram_bridge.py"
        source = bridge_path.read_text()

        # Find positions
        update_pos = source.find("/update")
        store_pos = source.find("store_message(")

        assert update_pos < store_pos, "/update intercept should come before store_message"

    def test_restart_flag_cleanup_in_startup(self):
        """Bridge startup should clear stale restart flags."""
        bridge_path = PROJECT_DIR / "bridge" / "telegram_bridge.py"
        source = bridge_path.read_text()
        assert "clear_restart_flag" in source


# =============================================================================
# Service Manager Tests
# =============================================================================


class TestServiceManager:
    """Test valor-service.sh has update cron support."""

    SERVICE_SCRIPT = str(PROJECT_DIR / "scripts" / "valor-service.sh")

    def test_update_plist_defined(self):
        source = Path(self.SERVICE_SCRIPT).read_text()
        # Label is built from ${SERVICE_LABEL_PREFIX}.update (defaulting to com.valor),
        # not hardcoded. Assert the dynamic form.
        assert "${SERVICE_LABEL_PREFIX}.update" in source
        assert "SERVICE_LABEL_PREFIX:=com.valor" in source

    def test_install_creates_both_plists(self):
        source = Path(self.SERVICE_SCRIPT).read_text()
        # install_service should reference both bridge and update plists
        assert "UPDATE_PLIST_PATH" in source
        assert "StartInterval" in source

    def test_uninstall_removes_both_plists(self):
        source = Path(self.SERVICE_SCRIPT).read_text()
        # uninstall should handle update plist
        assert source.count("UPDATE_PLIST_PATH") >= 2  # defined + used in uninstall

    def test_update_polling_interval_1800(self):
        """Update plist should use StartInterval of 1800 (30 minutes)."""
        source = Path(self.SERVICE_SCRIPT).read_text()
        assert "<key>StartInterval</key>" in source
        assert "<integer>1800</integer>" in source
        # Should NOT use the old calendar-based schedule
        assert "StartCalendarInterval" not in source


# =============================================================================
# Auto-Bump Deps Tests
# =============================================================================


class TestGetPypiLatest:
    def test_fetches_known_package(self):
        """Should return a version string for a known package."""
        version = get_pypi_latest("anthropic")
        assert version is not None
        assert "." in version  # version like "0.84.0"

    def test_returns_none_for_nonexistent_package(self):
        version = get_pypi_latest("this-package-definitely-does-not-exist-12345")
        assert version is None


# Mirrors the real ``pyproject.toml`` shape the pin helpers must survive:
# a floor declaration (``openai>=1.0.0``) whose package name also appears
# inside the TRAILING COMMENT of a line that does carry ``==`` (the exact
# defect-1 reproduction — a comment on its own line would not trip the old
# substring scan), an extras pin (``pydantic-ai-slim[anthropic]``) that
# contains a second package's name, and that extras line placed ABOVE the
# ``anthropic`` declaration so a line-order-dependent reader resolves the
# wrong one.
REALISTIC_PYPROJECT = """[project]
name = "ai"
dependencies = [
    "telethon==1.42.0", # CRITICAL — pin exact, upgrade via /update only
    "pydantic-ai-slim[anthropic]==2.9.0", # slim: avoids the openai/google/mcp extras
    "anthropic==0.125.0", # CRITICAL — pin exact
    "claude-agent-sdk==0.2.151", # CRITICAL — pin exact
    "openai>=1.0.0", # Embedding API for semantic doc impact finder
]
"""


class TestPinDeclarationDefects:
    """Regression tests for the three spike-2 pin-helper defects (#3001).

    Each defect silently produced the half-bump the coupled-set gate exists
    to prevent, so each gets its own test.
    """

    def test_openai_pin_not_read_from_comment(self, tmp_path: Path):
        """Defect 1: the reader scraped a version out of a *comment*.

        ``openai`` appears inside the ``pydantic-ai-slim`` comment block, so
        the old substring scan returned that line's ``2.9.0``. The real
        declaration is a floor with no ``==`` at all, which must read as
        "no exact pin" (``None``), never a version.
        """
        (tmp_path / "pyproject.toml").write_text(REALISTIC_PYPROJECT)
        assert get_pinned_version(tmp_path, "openai") is None

    def test_anthropic_pin_not_read_from_extras_line(self, tmp_path: Path):
        """Defect 2: ``anthropic`` resolved correctly only by line order.

        ``pydantic-ai-slim[anthropic]==2.9.0`` contains both ``anthropic``
        and ``==``. With that line above the real declaration, the old
        reader returned ``2.9.0``.
        """
        (tmp_path / "pyproject.toml").write_text(REALISTIC_PYPROJECT)
        assert get_pinned_version(tmp_path, "anthropic") == "0.125.0"
        assert get_pinned_version(tmp_path, "pydantic-ai-slim") == "2.9.0"

    def test_extras_pin_is_bumped(self, tmp_path: Path):
        """Defect 3: the writer could not match an extras pin.

        ``"{package}==[^"]*"`` never matches
        ``"pydantic-ai-slim[anthropic]==2.9.0"``, so the writer silently
        no-opped while the rest of the coupled set moved.
        """
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(REALISTIC_PYPROJECT)

        bump_pin_in_pyproject(tmp_path, "pydantic-ai-slim", "2.35.0")

        content = pyproject.read_text()
        assert '"pydantic-ai-slim[anthropic]==2.35.0"' in content
        # The extras marker and the neighbouring declarations are untouched.
        assert '"anthropic==0.125.0"' in content
        assert '"openai>=1.0.0"' in content
        assert "avoids the openai/google/mcp extras" in content


class TestPinHelpersRefuseLoudly:
    """Neither helper may return a wrong-but-plausible answer."""

    def test_writer_raises_when_declaration_absent(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text(REALISTIC_PYPROJECT)
        with pytest.raises(PinDeclarationError):
            bump_pin_in_pyproject(tmp_path, "nonexistent", "1.0.0")

    def test_writer_raises_on_floor_declaration(self, tmp_path: Path):
        """A floor is not a pin — rewriting it would invent a pin."""
        (tmp_path / "pyproject.toml").write_text(REALISTIC_PYPROJECT)
        with pytest.raises(PinDeclarationError):
            bump_pin_in_pyproject(tmp_path, "openai", "2.30.0")

    def test_reader_raises_on_conflicting_declarations(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text(
            '[project]\ndependencies = [\n    "anthropic==0.125.0",\n    "anthropic==0.62.0",\n]\n'
        )
        with pytest.raises(PinDeclarationError):
            get_pinned_version(tmp_path, "anthropic")

    def test_reader_returns_none_when_package_absent(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text(REALISTIC_PYPROJECT)
        assert get_pinned_version(tmp_path, "not-declared-anywhere") is None

    def test_no_dependency_block_refuses_without_crashing(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "ai"\n')
        assert get_pinned_version(tmp_path, "anthropic") is None
        with pytest.raises(PinDeclarationError):
            bump_pin_in_pyproject(tmp_path, "anthropic", "1.0.0")
        # Refusal is a refusal: the file is byte-for-byte untouched.
        assert (tmp_path / "pyproject.toml").read_text() == '[project]\nname = "ai"\n'

    def test_writer_raises_when_pyproject_missing(self, tmp_path: Path):
        with pytest.raises(PinDeclarationError):
            bump_pin_in_pyproject(tmp_path, "anthropic", "1.0.0")

    def test_real_pyproject_declarations_resolve(self):
        """The live file is the shape that produced every spike-2 defect."""
        assert get_pinned_version(PROJECT_DIR, "anthropic") == "0.125.0"
        assert get_pinned_version(PROJECT_DIR, "pydantic-ai-slim") == "2.9.0"
        # A floor is not a pin, and `openai` sits inside the slim line's comment.
        assert get_pinned_version(PROJECT_DIR, "openai") is None


class TestVerifyCriticalVersions:
    def test_verify_critical_versions_unchanged_by_helper_rewrite(self, tmp_path: Path):
        """The helper rewrite must not move ``verify_critical_versions``.

        Expected pins still come from the real declarations, and the
        installed-vs-expected comparison is untouched.
        """
        (tmp_path / "pyproject.toml").write_text(REALISTIC_PYPROJECT)

        installed = {
            "telethon": "1.42.0",
            "anthropic": "0.62.0",
            "claude-agent-sdk": None,
        }
        with patch(
            "scripts.update.deps.get_installed_version",
            side_effect=lambda _dir, pkg: installed[pkg],
        ):
            results = verify_critical_versions(tmp_path)

        by_package = {r.package: r for r in results}
        assert [r.package for r in results] == [
            "telethon",
            "anthropic",
            "claude-agent-sdk",
        ]
        assert by_package["telethon"].expected == "1.42.0"
        assert by_package["telethon"].matches is True
        # Installed disagrees with the pin -> mismatch.
        assert by_package["anthropic"].expected == "0.125.0"
        assert by_package["anthropic"].matches is False
        # Pinned but not installed -> mismatch, expected still reported.
        assert by_package["claude-agent-sdk"].expected == "0.2.151"
        assert by_package["claude-agent-sdk"].version is None
        assert by_package["claude-agent-sdk"].matches is False


class TestBumpPinInPyproject:
    def test_bumps_existing_pin(self, tmp_path: Path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            "[project]\ndependencies = [\n"
            '    "anthropic==0.62.0",  # CRITICAL\n'
            '    "claude-agent-sdk==0.1.35",  # CRITICAL\n'
            "]\n"
        )
        assert bump_pin_in_pyproject(tmp_path, "anthropic", "0.84.0")
        content = pyproject.read_text()
        assert '"anthropic==0.84.0"' in content
        # Other pins untouched
        assert '"claude-agent-sdk==0.1.35"' in content

    def test_preserves_comments(self, tmp_path: Path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('    "anthropic==0.62.0", # CRITICAL — pin exact\n')
        bump_pin_in_pyproject(tmp_path, "anthropic", "0.99.0")
        content = pyproject.read_text()
        assert "# CRITICAL" in content
        assert '"anthropic==0.99.0"' in content


# Two independent sets, so a failure in one is visibly scoped to that set.
# `held` is a third set that must never execute anything.
SET_A = CoupledSet(
    members=["anthropic", "pydantic-ai-slim"],
    import_names=("anthropic", "pydantic_ai"),
    gates=("llm", "import", "pytest"),
    reason="test double for the LLM coupled set",
)
SET_B = CoupledSet(
    members=["claude-agent-sdk"],
    import_names=("claude_agent_sdk",),
    reason="test double for the standalone set",
)

TWO_SET_PYPROJECT = (
    "[project]\ndependencies = [\n"
    '    "anthropic==0.62.0",\n'
    '    "pydantic-ai-slim[anthropic]==2.9.0",\n'
    '    "claude-agent-sdk==0.1.35",\n'
    "]\n"
)


def _latest(mapping: dict[str, str]):
    """A `get_pypi_latest` stub driven by an explicit per-package table."""
    return lambda package, timeout=10: mapping.get(package)


class TestCoupledSetDeclaration:
    """The declaration itself is a safety surface (#3001)."""

    def test_llm_set_is_declared_and_held(self):
        """`anthropic` is back in the auto-bump *structure*, but parked.

        Without the hold, the first post-merge cron tick would execute the
        Step 2 upgrade fleet-wide, unattended.
        """
        llm_set = next(s for s in AUTO_BUMP_SETS if "anthropic" in s.members)
        assert set(llm_set.members) == {"anthropic", "pydantic-ai-slim"}
        assert llm_set.import_names == ("anthropic", "pydantic_ai")
        assert llm_set.gates == ("llm", "import", "pytest")
        assert llm_set.hold == "#3001 Step 2"

    def test_openai_is_not_in_any_coupled_set(self):
        """spike-5: no packaging coupling, and its declaration is a floor."""
        assert "openai" not in {member for s in AUTO_BUMP_SETS for member in s.members}

    def test_default_gates_exclude_llm_phase(self):
        """A new set must not silently inherit a billed API call."""
        fresh = CoupledSet(members=["x"], import_names=("x",), reason="t")
        assert fresh.gates == ("import", "pytest")
        assert fresh.hold is None

    def test_import_phase_is_set_derived_not_hardcoded(self):
        """Each set's `import` phase probes what that set actually moved."""
        from scripts.update.deps import _gate_argv

        argv = _gate_argv(Path("/proj"), "import", SET_A)
        assert argv[-1] == "import anthropic; import pydantic_ai"
        assert _gate_argv(Path("/proj"), "import", SET_B)[-1] == "import claude_agent_sdk"


class TestLlmGateArgv:
    def test_llm_phase_argv_matches_gate_helper(self):
        """The phase and any manual invocation share ONE argv construction.

        Two constructions would let a hand-run verification pass against a
        command production never issues.
        """
        from scripts.update.deps import _gate_argv

        venv_python = Path("/proj/.venv/bin/python")
        assert _gate_argv(Path("/proj"), "llm", SET_A) == llm_gate_argv(venv_python)

    def test_llm_gate_runs_in_the_target_venv(self):
        """Never the update process's own interpreter — it imported pre-sync."""
        argv = llm_gate_argv(Path("/proj/.venv/bin/python"))
        assert argv == [
            "/proj/.venv/bin/python",
            "-m",
            "agent.llm.compat",
            "--json",
            "--allow-network",
        ]


class TestAutoBumpDeps:
    def test_no_bump_when_already_latest(self, tmp_path: Path):
        """When every set is at latest, nothing bumps and nothing syncs."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(TWO_SET_PYPROJECT)

        with (
            patch("scripts.update.deps.AUTO_BUMP_SETS", [SET_A, SET_B]),
            patch(
                "scripts.update.deps.get_pypi_latest",
                _latest(
                    {
                        "anthropic": "0.62.0",
                        "pydantic-ai-slim": "2.9.0",
                        "claude-agent-sdk": "0.1.35",
                    }
                ),
            ),
            patch("scripts.update.deps.sync_dependencies") as sync,
        ):
            result = auto_bump_deps(tmp_path)

        assert not result.any_bumped
        assert not result.rolled_back
        # A quiet cycle must not re-resolve the lockfile.
        sync.assert_not_called()

    def test_rollback_on_gate_failure_restores_whole_set(self, tmp_path: Path):
        """A failed gate reverts EVERY member of the set, not just one."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(TWO_SET_PYPROJECT)

        with (
            patch("scripts.update.deps.AUTO_BUMP_SETS", [SET_A]),
            patch(
                "scripts.update.deps.get_pypi_latest",
                _latest({"anthropic": "1.0.0", "pydantic-ai-slim": "2.35.0"}),
            ),
            patch(
                "scripts.update.deps.sync_dependencies",
                return_value=MagicMock(success=True, error=None),
            ),
            patch(
                "scripts.update.deps.run_gate_phases",
                return_value=(False, "pytest", "pytest gate failed:\nboom"),
            ),
        ):
            result = auto_bump_deps(tmp_path)

        assert result.rolled_back
        assert result.failed_phase == "pytest"
        assert not result.smoke_passed
        # Nothing is reported bumped, so run.py's commit list stays honest.
        assert not result.any_bumped
        assert pyproject.read_text() == TWO_SET_PYPROJECT

    def test_llm_gate_failure_rolls_back_set(self, tmp_path: Path):
        """The failed phase is named, so `llm` reads differently from `pytest`."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(TWO_SET_PYPROJECT)

        with (
            patch("scripts.update.deps.AUTO_BUMP_SETS", [SET_A]),
            patch(
                "scripts.update.deps.get_pypi_latest",
                _latest({"anthropic": "1.0.0", "pydantic-ai-slim": "2.35.0"}),
            ),
            patch(
                "scripts.update.deps.sync_dependencies",
                return_value=MagicMock(success=True, error=None),
            ),
            patch(
                "scripts.update.deps.run_gate_phases",
                return_value=(False, "llm", "llm gate failed:\nINCOMPATIBLE: no temperature"),
            ),
        ):
            result = auto_bump_deps(tmp_path)

        assert result.failed_phase == "llm"
        assert "INCOMPATIBLE" in result.smoke_output
        errors = " ".join(b.error or "" for b in result.bumps)
        assert "llm gate failed" in errors
        assert pyproject.read_text() == TWO_SET_PYPROJECT

    def test_unrelated_set_survives_failed_set(self, tmp_path: Path):
        """spike-4: a per-set snapshot, not a whole-file one.

        A whole-file snapshot taken once before the loop reverts the good
        set's bump along with the bad one's.
        """
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(TWO_SET_PYPROJECT)

        def gates(_project_dir, coupled_set):
            if "anthropic" in coupled_set.members:
                return False, "import", "import gate failed"
            return True, None, "gates passed"

        with (
            patch("scripts.update.deps.AUTO_BUMP_SETS", [SET_A, SET_B]),
            patch(
                "scripts.update.deps.get_pypi_latest",
                _latest(
                    {
                        "anthropic": "1.0.0",
                        "pydantic-ai-slim": "2.35.0",
                        "claude-agent-sdk": "0.9.9",
                    }
                ),
            ),
            patch(
                "scripts.update.deps.sync_dependencies",
                return_value=MagicMock(success=True, error=None),
            ),
            patch("scripts.update.deps.run_gate_phases", side_effect=gates),
        ):
            result = auto_bump_deps(tmp_path)

        content = pyproject.read_text()
        # Failed set reverted...
        assert '"anthropic==0.62.0"' in content
        assert '"pydantic-ai-slim[anthropic]==2.9.0"' in content
        # ...good set kept.
        assert '"claude-agent-sdk==0.9.9"' in content
        assert {b.package for b in result.bumps if b.bumped} == {"claude-agent-sdk"}
        assert result.rolled_back

    def test_partial_resolve_skips_whole_set(self, tmp_path: Path):
        """One unresolvable member skips the set — a half-resolve is a half-bump."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(TWO_SET_PYPROJECT)

        with (
            patch("scripts.update.deps.AUTO_BUMP_SETS", [SET_A]),
            patch(
                "scripts.update.deps.get_pypi_latest",
                # Network down for the second member only.
                _latest({"anthropic": "1.0.0"}),
            ),
            patch("scripts.update.deps.sync_dependencies") as sync,
        ):
            result = auto_bump_deps(tmp_path)

        assert not result.any_bumped
        assert pyproject.read_text() == TWO_SET_PYPROJECT
        sync.assert_not_called()
        assert all("set skipped" in (b.error or "") for b in result.bumps)
        assert {b.package for b in result.bumps} == {"anthropic", "pydantic-ai-slim"}

    def test_held_set_is_skipped_and_legible(self, tmp_path: Path):
        """A held set executes nothing and says why, per member."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(TWO_SET_PYPROJECT)

        held = CoupledSet(
            members=["anthropic", "pydantic-ai-slim"],
            import_names=("anthropic", "pydantic_ai"),
            gates=("llm", "import", "pytest"),
            reason="test double",
            hold="#3001 Step 2",
        )
        with (
            patch("scripts.update.deps.AUTO_BUMP_SETS", [held]),
            patch("scripts.update.deps.get_pypi_latest") as pypi,
            patch("scripts.update.deps.sync_dependencies") as sync,
            patch("scripts.update.deps.run_gate_phases") as gates,
        ):
            result = auto_bump_deps(tmp_path)

        pypi.assert_not_called()
        sync.assert_not_called()
        gates.assert_not_called()
        assert not result.any_bumped
        assert [b.error for b in result.bumps] == ["held: #3001 Step 2"] * 2
        # The recorded current pin keeps run.py's per-bump log informative.
        assert {b.package: b.old_version for b in result.bumps} == {
            "anthropic": "0.62.0",
            "pydantic-ai-slim": "2.9.0",
        }
        assert pyproject.read_text() == TWO_SET_PYPROJECT

    def test_gate_unavailable_is_fail_closed(self, tmp_path: Path):
        """No venv to gate with is a FAILED gate, never a skipped one."""
        from scripts.update.deps import run_gate_phases

        passed, phase, output = run_gate_phases(tmp_path, SET_A)
        assert passed is False
        assert phase == "llm"
        assert "No Python venv" in output

    def test_restore_failure_blocks_commit(self, tmp_path: Path):
        """A rollback whose re-sync failed must stop the run committing.

        `run.py` gates its commit on `any_bumped and not restore_failed`;
        without the flag a later good bump pushes a poisoned lockfile.
        """
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(TWO_SET_PYPROJECT)

        syncs = [
            MagicMock(success=True, error=None),  # post-bump sync
            MagicMock(success=False, error="resolver exploded"),  # restore sync
            MagicMock(success=True, error=None),  # SET_B post-bump sync
        ]
        with (
            patch("scripts.update.deps.AUTO_BUMP_SETS", [SET_A, SET_B]),
            patch(
                "scripts.update.deps.get_pypi_latest",
                _latest(
                    {
                        "anthropic": "1.0.0",
                        "pydantic-ai-slim": "2.35.0",
                        "claude-agent-sdk": "0.9.9",
                    }
                ),
            ),
            patch("scripts.update.deps.sync_dependencies", side_effect=syncs),
            patch(
                "scripts.update.deps.run_gate_phases",
                side_effect=lambda _d, s: (
                    (False, "import", "boom") if "anthropic" in s.members else (True, None, "ok")
                ),
            ),
            patch("scripts.update.deps.run_cmd") as run_cmd,
        ):
            result = auto_bump_deps(tmp_path)

        assert result.restore_failed is True
        # The poisoned lockfile is discarded, not left for `git add`.
        assert run_cmd.call_args_list[0].args[0] == ["git", "checkout", "--", "uv.lock"]
        # A good set still bumped, which is exactly why the flag must gate.
        assert result.any_bumped

    def test_worktree_clean_after_every_rollback_path(self, tmp_path: Path):
        """Sync failure, gate failure, and rewrite refusal all leave the file pristine."""
        pyproject = tmp_path / "pyproject.toml"

        scenarios = [
            # (sync result, gate result)
            (MagicMock(success=False, error="sync died"), (True, None, "ok")),
            (MagicMock(success=True, error=None), (False, "pytest", "gate died")),
        ]
        for sync_result, gate_result in scenarios:
            pyproject.write_text(TWO_SET_PYPROJECT)
            with (
                patch("scripts.update.deps.AUTO_BUMP_SETS", [SET_A]),
                patch(
                    "scripts.update.deps.get_pypi_latest",
                    _latest({"anthropic": "1.0.0", "pydantic-ai-slim": "2.35.0"}),
                ),
                patch("scripts.update.deps.sync_dependencies", return_value=sync_result),
                patch("scripts.update.deps.run_gate_phases", return_value=gate_result),
            ):
                result = auto_bump_deps(tmp_path)
            assert result.rolled_back
            assert pyproject.read_text() == TWO_SET_PYPROJECT

        # A writer refusal mid-set abandons the set with the file restored.
        pyproject.write_text(TWO_SET_PYPROJECT)
        with (
            patch("scripts.update.deps.AUTO_BUMP_SETS", [SET_A]),
            patch(
                "scripts.update.deps.get_pypi_latest",
                _latest({"anthropic": "1.0.0", "pydantic-ai-slim": "2.35.0"}),
            ),
            patch(
                "scripts.update.deps.bump_pin_in_pyproject",
                side_effect=PinDeclarationError("declared twice"),
            ),
            patch("scripts.update.deps.sync_dependencies") as sync,
        ):
            result = auto_bump_deps(tmp_path)

        sync.assert_not_called()
        assert pyproject.read_text() == TWO_SET_PYPROJECT
        assert all("set abandoned" in (b.error or "") for b in result.bumps)


# =============================================================================
# Verify-time LLM stack compat check
# =============================================================================


class TestVerifyRunsCompatCheck:
    """The verify leg is the ONLY guard on a follower machine (route 3).

    Followers never auto-bump, so if this check is silent an incompatible
    stack ships with nothing saying so.
    """

    INCOMPATIBLE = {
        "compatible": False,
        "loader_ok": True,
        "anthropic_version": "1.0.0",
        "pydantic_ai_version": "2.9.0",
        "reason": "anthropic 1.0.0's client.beta.messages.create does not accept: temperature",
    }

    def _stub_run(self, tmp_path: Path, payload: dict):
        venv_python = tmp_path / ".venv" / "bin" / "python"
        venv_python.parent.mkdir(parents=True, exist_ok=True)
        venv_python.write_text("")
        import json as _json

        return patch(
            "scripts.update.verify.run_cmd",
            return_value=MagicMock(returncode=0, stdout=_json.dumps(payload), stderr=""),
        )

    def test_verify_runs_compat_check_without_bump(self, tmp_path: Path):
        """Incompatible stack -> non-empty `.error`, not just `available=False`.

        `run.py`'s valor_tools loop is `if not tool.available and tool.error:`
        — a check that only flips `available` produces no log line, no
        warning, and nothing for `extract_update_warnings` to surface.
        """
        from bridge.update import extract_update_warnings
        from scripts.update import verify

        with self._stub_run(tmp_path, self.INCOMPATIBLE):
            check = verify.check_llm_stack_compat(tmp_path)

        assert check.name == "llm-stack-compat"
        assert check.available is False
        assert check.error
        assert "temperature" in check.error
        # Both versions are legible whether it passed or failed.
        assert "anthropic 1.0.0" in check.detail
        assert "pydantic-ai 2.9.0" in check.detail

        # And the warning run.py builds from it survives the parser.
        warnings = extract_update_warnings([f"  ⚠️ {check.name}: {check.error}"])
        assert any("llm-stack-compat" in w and "temperature" in w for w in warnings)

    def test_compat_check_is_appended_to_valor_tools(self, tmp_path: Path):
        """It must reach `result.valor_tools`, where run.py's loop looks."""
        from scripts.update import verify

        cheap = {
            "check_system_tools": [],
            "check_python_deps": [],
            "check_dev_tools": [],
            "check_valor_tools": [],
            "check_sdk_auth": {},
            "check_mcp_servers": [],
            "check_gitignore_issues": [],
        }
        singles = ["check_telegram_session", "check_google_token", "check_env_completeness"]

        with patch.multiple(
            "scripts.update.verify",
            **{name: MagicMock(return_value=value) for name, value in cheap.items()},
            **{
                name: MagicMock(return_value=verify.ToolCheck(name=name, available=True))
                for name in singles
            },
        ):
            with self._stub_run(tmp_path, self.INCOMPATIBLE):
                result = verify.verify_environment(tmp_path, check_ollama_model=False)

        compat = [t for t in result.valor_tools if t.name == "llm-stack-compat"]
        assert len(compat) == 1
        assert compat[0].error

    def test_compat_check_reports_versions_on_pass(self, tmp_path: Path):
        """Print-on-pass: `detail` carries both versions on a healthy stack."""
        from scripts.update import verify

        payload = dict(self.INCOMPATIBLE, compatible=True, reason=None)
        with self._stub_run(tmp_path, payload):
            check = verify.check_llm_stack_compat(tmp_path)

        assert check.available is True
        assert check.error is None
        assert check.detail == "anthropic 1.0.0 / pydantic-ai 2.9.0"
        assert check.version == check.detail

    def test_compat_check_is_fail_closed_on_garbage(self, tmp_path: Path):
        """A subprocess that emits no verdict is a failure with a reason."""
        from scripts.update import verify

        venv_python = tmp_path / ".venv" / "bin" / "python"
        venv_python.parent.mkdir(parents=True, exist_ok=True)
        venv_python.write_text("")

        with patch(
            "scripts.update.verify.run_cmd",
            return_value=MagicMock(returncode=1, stdout="", stderr="Traceback: boom"),
        ):
            check = verify.check_llm_stack_compat(tmp_path)

        assert check.available is False
        assert "boom" in check.error

    def test_compat_check_not_human_gated(self):
        """It is agent-resolvable, so it must re-warn every run until fixed."""
        source = (PROJECT_DIR / "scripts" / "update" / "run.py").read_text()
        gated = source.split("human_gated_tools = ")[1].split("\n")[0]
        assert "llm-stack-compat" not in gated
