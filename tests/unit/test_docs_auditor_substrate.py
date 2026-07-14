"""Tests for the new unified docs auditor substrate (reflections/docs_auditor.py).

Covers the public ``audit()`` callable, rotation reflection, branch sweeper,
SETNX lock contention, neighborhood cap, zero-diff gate, auth probe
degradation, ``refresh_docs_in_memory`` hook, and the ``/do-docs`` thin-caller
contract (pr-changed-files mode).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from reflections import docs_auditor

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    (tmp_path / "docs" / "features").mkdir(parents=True)
    (tmp_path / "docs" / "plans").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
    return tmp_path


@pytest.fixture(autouse=True)
def reset_global_state():
    """Reset module-level counters between tests."""
    docs_auditor._RENAME_QUERY_COUNT = 0
    yield
    docs_auditor._RENAME_QUERY_COUNT = 0


@pytest.fixture()
def fake_redis():
    """Stand-in for the Popoto Redis connection."""
    fake = MagicMock()
    fake.set.return_value = True
    fake.delete.return_value = 1
    fake.hgetall.return_value = {}
    fake.hset.return_value = 1
    return fake


@pytest.fixture()
def patch_redis(fake_redis):
    with patch("reflections.docs_auditor._get_redis", return_value=fake_redis):
        yield fake_redis


@pytest.fixture()
def auth_ok():
    with patch("reflections.docs_auditor._check_auth", return_value=(True, "")):
        yield


# ---------------------------------------------------------------------------
# TestAuditSubstrate — public audit() entrypoint
# ---------------------------------------------------------------------------


class TestAuditSubstrate:
    """Tests for the public ``audit()`` callable."""

    def test_returns_disabled_on_auth_failure(self, repo: Path):
        with patch(
            "reflections.docs_auditor._check_auth",
            return_value=(False, "ANTHROPIC_API_KEY not set"),
        ):
            result = docs_auditor.audit(
                primary_path="docs/features/foo.md",
                scope_mode="rotation",
                apply_mode="apply",
                project_key="test",
                repo_root=repo,
            )
        assert result["status"] == "disabled"
        assert "ANTHROPIC_API_KEY" in result.get("reason", "")

    def test_returns_skipped_when_primary_missing(self, repo: Path, auth_ok, patch_redis):
        result = docs_auditor.audit(
            primary_path="docs/features/missing.md",
            scope_mode="rotation",
            apply_mode="apply",
            project_key="test",
            repo_root=repo,
        )
        assert result["status"] == "skipped"
        assert result.get("reason") == "primary_not_found"

    def test_returns_skipped_when_no_primary_path(self, repo: Path, auth_ok, patch_redis):
        result = docs_auditor.audit(
            primary_path=None,
            scope_mode="rotation",
            apply_mode="apply",
            project_key="test",
            repo_root=repo,
        )
        assert result["status"] == "skipped"
        assert result.get("reason") == "no_primary_path"

    def test_unknown_scope_mode_returns_error(self, repo: Path, auth_ok, patch_redis):
        result = docs_auditor.audit(
            primary_path="docs/features/foo.md",
            scope_mode="bogus",
            apply_mode="apply",
            project_key="test",
            repo_root=repo,
        )
        assert result["status"] == "error"

    def test_pr_changed_files_empty_returns_ok(self, repo: Path, auth_ok, patch_redis):
        with patch("reflections.docs_auditor._resolve_pr_changed_files", return_value=[]):
            result = docs_auditor.audit(
                primary_path=None,
                scope_mode="pr-changed-files",
                apply_mode="apply",
                project_key="test",
                repo_root=repo,
            )
        assert result["status"] == "ok"
        assert result["files_touched"] == []

    def test_stale_term_fix_applied(self, repo: Path, auth_ok, patch_redis):
        primary = repo / "docs" / "features" / "foo.md"
        # Use enough content so it's not a stub
        primary.write_text("# Foo\n\nThe SessionLog tracks state.\n" + "Padding line.\n" * 6)
        with patch.object(docs_auditor, "_file_issue_if_new", return_value=False):
            result = docs_auditor.audit(
                primary_path="docs/features/foo.md",
                scope_mode="rotation",
                apply_mode="apply",
                project_key="test",
                repo_root=repo,
            )
        assert result["status"] == "ok"
        assert result["fixes_applied"] >= 1
        # File should be rewritten with AgentSession
        assert "AgentSession" in primary.read_text()

    def test_dry_run_does_not_apply(self, repo: Path, auth_ok, patch_redis):
        primary = repo / "docs" / "features" / "foo.md"
        primary.write_text("# Foo\n\nThe SessionLog tracks state.\n" + "Padding line.\n" * 6)
        original = primary.read_text()
        with patch.object(docs_auditor, "_file_issue_if_new", return_value=False):
            result = docs_auditor.audit(
                primary_path="docs/features/foo.md",
                scope_mode="rotation",
                apply_mode="dry-run",
                project_key="test",
                repo_root=repo,
            )
        assert result["status"] == "ok"
        assert primary.read_text() == original


# ---------------------------------------------------------------------------
# TestNeighborhoodCap
# ---------------------------------------------------------------------------


class TestNeighborhoodCap:
    def test_neighborhood_capped_at_20(self, repo: Path):
        primary = repo / "docs" / "features" / "primary.md"
        # Generate many outbound links
        links = []
        for i in range(50):
            target = repo / "docs" / "features" / f"linked_{i:03d}.md"
            target.write_text(f"# Doc {i}")
            links.append(f"- [doc {i}](linked_{i:03d}.md)")
        primary.write_text("# Primary\n" + "\n".join(links))

        result = docs_auditor._resolve_neighborhood(
            Path("docs/features/primary.md"), repo, cap=docs_auditor.NEIGHBORHOOD_CAP
        )
        assert len(result) <= docs_auditor.NEIGHBORHOOD_CAP


# ---------------------------------------------------------------------------
# TestSetnxLock — concurrent run protection
# ---------------------------------------------------------------------------


class TestSetnxLock:
    def test_lock_acquire_returns_true_when_unlocked(self, fake_redis, patch_redis):
        fake_redis.set.return_value = True
        assert docs_auditor._acquire_lock("test:lock") is True
        fake_redis.set.assert_called_with(
            "test:lock", "1", nx=True, ex=docs_auditor.LOCK_TTL_SECONDS
        )

    def test_lock_acquire_returns_false_when_locked(self, fake_redis, patch_redis):
        fake_redis.set.return_value = None
        assert docs_auditor._acquire_lock("test:lock") is False

    def test_concurrent_run_returns_skipped(self, repo, auth_ok, patch_redis, fake_redis):
        fake_redis.set.return_value = None  # already locked
        with patch("reflections.docs_auditor.PROJECT_ROOT", repo):
            result = docs_auditor.run_docs_auditor()
        assert result["status"] == "ok"
        assert "locked" in result["summary"].lower() or "locked" in str(result["findings"]).lower()


# ---------------------------------------------------------------------------
# TestZeroDiffGate
# ---------------------------------------------------------------------------


class TestZeroDiffGate:
    def test_zero_diff_skips_pr_creation(self, repo, auth_ok, patch_redis):
        primary = repo / "docs" / "features" / "foo.md"
        primary.write_text("# Foo\n\nThe AgentSession tracks state.\n" + "Padding line.\n" * 6)

        with (
            patch("reflections.docs_auditor.PROJECT_ROOT", repo),
            patch("reflections.docs_auditor._git_dirty", return_value=False),
            patch("reflections.docs_auditor._git_diff_quiet", return_value=True),
            patch("reflections.docs_auditor._push_branch_and_pr") as mock_push,
            patch("reflections.docs_auditor._send_telegram_notification"),
        ):
            result = docs_auditor.run_docs_auditor()

        assert result["status"] == "ok"
        # Push must NOT be called when zero-diff
        mock_push.assert_not_called()


# ---------------------------------------------------------------------------
# TestRefreshDocsInMemoryHook
# ---------------------------------------------------------------------------


class TestRefreshDocsInMemoryHook:
    def test_hook_is_a_no_op(self):
        # Just ensure it doesn't raise on any input
        docs_auditor.refresh_docs_in_memory([])
        docs_auditor.refresh_docs_in_memory(["docs/features/foo.md"])
        docs_auditor.refresh_docs_in_memory(["a", "b", "c"])

    def test_hook_invoked_once_per_non_empty_touched_paths(self, repo, auth_ok, patch_redis):
        primary = repo / "docs" / "features" / "foo.md"
        primary.write_text("# Foo\n\nThe SessionLog tracks state.\n" + "Padding line.\n" * 6)

        with (
            patch("reflections.docs_auditor.PROJECT_ROOT", repo),
            patch("reflections.docs_auditor._git_dirty", return_value=False),
            patch("reflections.docs_auditor._git_diff_quiet", return_value=False),
            patch(
                "reflections.docs_auditor._push_branch_and_pr",
                return_value="https://example.com/pr/1",
            ),
            patch("reflections.docs_auditor._send_telegram_notification"),
            patch("reflections.docs_auditor._file_issue_if_new", return_value=False),
            patch("reflections.docs_auditor.refresh_docs_in_memory") as mock_hook,
        ):
            result = docs_auditor.run_docs_auditor()

        assert result["status"] == "ok"
        assert mock_hook.call_count == 1

    def test_hook_skipped_on_zero_diff_path(self, repo, auth_ok, patch_redis):
        primary = repo / "docs" / "features" / "foo.md"
        primary.write_text("# Foo\n\nThe AgentSession tracks state.\n" + "Padding line.\n" * 6)

        with (
            patch("reflections.docs_auditor.PROJECT_ROOT", repo),
            patch("reflections.docs_auditor._git_dirty", return_value=False),
            patch("reflections.docs_auditor._git_diff_quiet", return_value=True),
            patch("reflections.docs_auditor._push_branch_and_pr"),
            patch("reflections.docs_auditor._send_telegram_notification"),
            patch("reflections.docs_auditor.refresh_docs_in_memory") as mock_hook,
        ):
            docs_auditor.run_docs_auditor()

        # Zero-diff path returns before the hook is reached
        mock_hook.assert_not_called()

    def test_hook_failure_does_not_propagate(self, repo, auth_ok, patch_redis):
        primary = repo / "docs" / "features" / "foo.md"
        primary.write_text("# Foo\n\nThe SessionLog tracks state.\n" + "Padding line.\n" * 6)

        with (
            patch("reflections.docs_auditor.PROJECT_ROOT", repo),
            patch("reflections.docs_auditor._git_dirty", return_value=False),
            patch("reflections.docs_auditor._git_diff_quiet", return_value=False),
            patch(
                "reflections.docs_auditor._push_branch_and_pr",
                return_value="https://example.com/pr/1",
            ),
            patch("reflections.docs_auditor._send_telegram_notification"),
            patch("reflections.docs_auditor._file_issue_if_new", return_value=False),
            patch(
                "reflections.docs_auditor.refresh_docs_in_memory",
                side_effect=RuntimeError("boom"),
            ),
        ):
            result = docs_auditor.run_docs_auditor()

        # Hook raised -> reflection still returns ok
        assert result["status"] == "ok"


# ---------------------------------------------------------------------------
# TestAuthProbeDegradation
# ---------------------------------------------------------------------------


class TestAuthProbeDegradation:
    def test_check_auth_missing_anthropic_module(self):
        with patch.dict("sys.modules", {"anthropic": None}):
            ok, reason = docs_auditor._check_auth()
        assert ok is False

    def test_check_auth_missing_key(self):
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}, clear=False):
            ok, reason = docs_auditor._check_auth()
        assert ok is False
        assert "ANTHROPIC_API_KEY" in reason

    def test_check_embedding_auth_returns_false_when_unset(self):
        with patch.dict("os.environ", {}, clear=True):
            assert docs_auditor._check_embedding_auth() is False

    def test_check_embedding_auth_returns_true_when_set(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}, clear=False):
            assert docs_auditor._check_embedding_auth() is True


# ---------------------------------------------------------------------------
# TestGitLogFollowCap
# ---------------------------------------------------------------------------


class TestGitLogFollowCap:
    def test_cap_enforced_after_n_calls(self, repo: Path):
        # Force the cap counter near the limit
        docs_auditor._RENAME_QUERY_COUNT = docs_auditor.GIT_LOG_FOLLOW_CAP
        result = docs_auditor._git_log_follow_renames("foo/bar.py", repo)
        assert result == []

    def test_subprocess_failure_returns_empty(self, repo: Path):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("git", 10)):
            result = docs_auditor._git_log_follow_renames("foo/bar.py", repo)
        assert result == []


# ---------------------------------------------------------------------------
# TestDoDocsContract — pr-changed-files mode contract for /do-docs
# ---------------------------------------------------------------------------


class TestDoDocsContract:
    def test_pr_mode_uses_changed_files_resolver(self, repo, auth_ok, patch_redis):
        with patch(
            "reflections.docs_auditor._resolve_pr_changed_files", return_value=[]
        ) as mock_resolver:
            docs_auditor.audit(
                primary_path=None,
                scope_mode="pr-changed-files",
                apply_mode="apply",
                project_key="test",
                repo_root=repo,
            )
        mock_resolver.assert_called_once()

    def test_pr_mode_does_not_create_branch(self, repo: Path, auth_ok, patch_redis):
        # The substrate itself never branches; only the rotation reflection does.
        primary = repo / "docs" / "features" / "foo.md"
        primary.write_text("# Foo\n\nThe SessionLog tracks state.\n" + "Padding line.\n" * 6)

        with (
            patch(
                "reflections.docs_auditor._resolve_pr_changed_files",
                return_value=[Path("docs/features/foo.md")],
            ),
            patch.object(docs_auditor, "_file_issue_if_new", return_value=False),
            patch("reflections.docs_auditor._push_branch_and_pr") as mock_push,
        ):
            result = docs_auditor.audit(
                primary_path=None,
                scope_mode="pr-changed-files",
                apply_mode="apply",
                project_key="test",
                repo_root=repo,
            )

        assert result["status"] == "ok"
        # Substrate must not push branches in any scope mode
        mock_push.assert_not_called()

    def test_hook_invocation_under_pr_mode(self, repo: Path, auth_ok, patch_redis):
        # In pr-changed-files mode the substrate fires the memory refresh hook
        # itself, so the /do-docs skill is a true thin caller (no skill-level
        # work needed per Task 4 acceptance criteria).
        primary = repo / "docs" / "features" / "foo.md"
        primary.write_text("# Foo\n\nThe SessionLog tracks state.\n" + "Padding line.\n" * 6)

        with (
            patch(
                "reflections.docs_auditor._resolve_pr_changed_files",
                return_value=[Path("docs/features/foo.md")],
            ),
            patch.object(docs_auditor, "_file_issue_if_new", return_value=False),
            patch.object(docs_auditor, "_commit_current_branch") as mock_commit,
            patch.object(docs_auditor, "refresh_docs_in_memory") as mock_hook,
        ):
            result = docs_auditor.audit(
                primary_path=None,
                scope_mode="pr-changed-files",
                apply_mode="apply",
                project_key="test",
                repo_root=repo,
            )

        assert "docs/features/foo.md" in result["files_touched"]
        mock_commit.assert_called_once()
        mock_hook.assert_called_once_with(["docs/features/foo.md"])

    def test_rotation_mode_does_not_fire_hook_inside_audit(self, repo: Path, auth_ok, patch_redis):
        # In rotation mode, the hook is fired by run_docs_auditor (Caller A),
        # not by audit() directly. Avoid double-firing.
        primary = repo / "docs" / "features" / "foo.md"
        primary.write_text("# Foo\n\nThe SessionLog tracks state.\n" + "Padding line.\n" * 6)

        with (
            patch.object(docs_auditor, "_file_issue_if_new", return_value=False),
            patch.object(docs_auditor, "refresh_docs_in_memory") as mock_hook,
        ):
            docs_auditor.audit(
                primary_path="docs/features/foo.md",
                scope_mode="rotation",
                apply_mode="apply",
                project_key="test",
                repo_root=repo,
            )

        mock_hook.assert_not_called()


# ---------------------------------------------------------------------------
# TestNonMarkdownApplyGuard — apply mode must never rewrite non-.md files (#2058)
# ---------------------------------------------------------------------------


class TestNonMarkdownApplyGuard:
    """Committed site/*.html must be byte-identical after pr-changed-files apply.

    The stale-term / link / symbol detectors are markdown-regex based and were
    never meant to rewrite HTML. Before #2058 the pr-changed-files apply path had
    no suffix guard on the write-back, so a stale term inside an HTML attribute
    (e.g. class="session_log") could be silently rewritten and shipped to the
    public docs site. The guard skips the write-back for any non-.md path.
    """

    def test_html_with_stale_term_in_attribute_left_untouched(
        self, repo: Path, auth_ok, patch_redis
    ):
        site = repo / "site"
        site.mkdir()
        page = site / "runtime.html"
        # `session_log` is a STALE_TERMS key (→ agent_session); here it lives
        # inside a class attribute — exactly the collateral-rewrite hazard.
        html = (
            "<!doctype html><html><body>\n"
            '<section class="session_log">\n'
            "  <h2>Runtime</h2>\n"
            "  <p>The worker executes sessions.</p>\n"
            "</section>\n"
            "</body></html>\n"
        )
        page.write_text(html)

        with (
            patch(
                "reflections.docs_auditor._resolve_pr_changed_files",
                return_value=[Path("site/runtime.html")],
            ),
            patch.object(docs_auditor, "_commit_current_branch") as mock_commit,
            patch.object(docs_auditor, "refresh_docs_in_memory") as mock_hook,
        ):
            result = docs_auditor.audit(
                primary_path=None,
                scope_mode="pr-changed-files",
                apply_mode="apply",
                project_key="test",
                repo_root=repo,
            )

        # The HTML file is byte-identical — the guard blocked the write-back.
        assert page.read_text() == html
        assert result["status"] == "ok"
        assert result["files_touched"] == []
        assert result["fixes_applied"] == 0
        # No commit / memory refresh fires when nothing was touched.
        mock_commit.assert_not_called()
        mock_hook.assert_not_called()

    def test_markdown_sibling_still_rewritten(self, repo: Path, auth_ok, patch_redis):
        """The guard only narrows non-.md; committed .md files still auto-fix."""
        md = repo / "docs" / "features" / "runtime.md"
        md.write_text("# Runtime\n\nThe session_log tracks state.\n" + "Pad.\n" * 6)

        with (
            patch(
                "reflections.docs_auditor._resolve_pr_changed_files",
                return_value=[Path("docs/features/runtime.md")],
            ),
            patch.object(docs_auditor, "_commit_current_branch"),
            patch.object(docs_auditor, "refresh_docs_in_memory"),
        ):
            result = docs_auditor.audit(
                primary_path=None,
                scope_mode="pr-changed-files",
                apply_mode="apply",
                project_key="test",
                repo_root=repo,
            )

        assert "agent_session" in md.read_text()
        assert result["fixes_applied"] >= 1
        assert "docs/features/runtime.md" in result["files_touched"]


# ---------------------------------------------------------------------------
# TestRotationKeyExplosion — single Redis hash, not per-file keys
# ---------------------------------------------------------------------------


class TestRotationKeyExplosion:
    def test_rotation_writes_to_single_hash(self, repo, fake_redis, patch_redis):
        docs_auditor._update_rotation_hash("test", ["docs/features/a.md", "docs/features/b.md"])
        # Should call hset with a single key, not multiple set() calls
        assert fake_redis.hset.called
        args, kwargs = fake_redis.hset.call_args
        assert args[0] == docs_auditor.REDIS_LAST_RUN_HASH
        mapping = kwargs.get("mapping") or args[1]
        assert isinstance(mapping, dict)
        assert len(mapping) == 2

    def test_vault_field_naming(self):
        f = docs_auditor._vault_field("psyoptimal", "vault/biz.md")
        assert f.startswith("vault:psyoptimal:")


# ---------------------------------------------------------------------------
# TestStaleTermDictionary
# ---------------------------------------------------------------------------


class TestStaleTermDictionary:
    def test_stale_term_dict_seeded(self):
        assert "SessionLog" in docs_auditor.STALE_TERMS
        assert docs_auditor.STALE_TERMS["SessionLog"] == "AgentSession"
        assert "RedisJob" in docs_auditor.STALE_TERMS

    def test_migration_context_skips_fix(self):
        content = "The SessionLog has been renamed to AgentSession."
        fixes = docs_auditor._detect_stale_term_fixes(content)
        # Migration context recognized -> no fix queued
        assert ("SessionLog", "AgentSession") not in fixes

    def test_no_migration_context_queues_fix(self):
        content = "The SessionLog has methods to track session state."
        fixes = docs_auditor._detect_stale_term_fixes(content)
        assert ("SessionLog", "AgentSession") in fixes


# ---------------------------------------------------------------------------
# TestDirtyTreeGuard
# ---------------------------------------------------------------------------


class TestDirtyTreeGuard:
    def test_dirty_tree_skips_rotation(self, repo, auth_ok, patch_redis):
        with (
            patch("reflections.docs_auditor.PROJECT_ROOT", repo),
            patch("reflections.docs_auditor._git_dirty", return_value=True),
        ):
            result = docs_auditor.run_docs_auditor()
        assert result["status"] == "ok"
        assert "dirty" in result["summary"].lower()


# ---------------------------------------------------------------------------
# TestPRCreationFailure
# ---------------------------------------------------------------------------


class TestPRCreationFailure:
    def test_push_failure_returns_finding_no_raise(self, repo, auth_ok, patch_redis):
        primary = repo / "docs" / "features" / "foo.md"
        primary.write_text("# Foo\n\nThe SessionLog tracks state.\n" + "Padding line.\n" * 6)

        with (
            patch("reflections.docs_auditor.PROJECT_ROOT", repo),
            patch("reflections.docs_auditor._git_dirty", return_value=False),
            patch("reflections.docs_auditor._git_diff_quiet", return_value=False),
            patch(
                "reflections.docs_auditor._push_branch_and_pr",
                return_value=None,
            ),
            patch("reflections.docs_auditor._send_telegram_notification"),
            patch("reflections.docs_auditor._file_issue_if_new", return_value=False),
        ):
            result = docs_auditor.run_docs_auditor()

        # Failure to create PR should not raise
        assert result["status"] in ("ok", "error")


# ---------------------------------------------------------------------------
# TestDraftModeAbsent — verifies no DRAFT_MODE constant exists
# ---------------------------------------------------------------------------


class TestDraftModeAbsent:
    def test_no_draft_mode_constant(self):
        assert not hasattr(docs_auditor, "DRAFT_MODE")
        # Also check the source file to be sure
        src = Path(docs_auditor.__file__).read_text()
        assert "DRAFT_MODE" not in src


# ---------------------------------------------------------------------------
# TestDeletedTargetFiltering — placeholder / fenced / deletion-heading suppression
# ---------------------------------------------------------------------------


def _mk_finding(content: str, repo: Path) -> list[dict]:
    """Run the detector against a doc with the given content."""
    return docs_auditor._detect_deleted_target_issues(Path("docs/features/x.md"), content, repo)


class TestDeletedTargetFiltering:
    def test_is_placeholder_path_stand_ins(self):
        assert docs_auditor._is_placeholder_path("foo/bar.py") is True
        assert docs_auditor._is_placeholder_path("agent/docs_handler/foo.py") is True
        assert docs_auditor._is_placeholder_path("pkg/example.py") is True
        assert docs_auditor._is_placeholder_path("a/thing.py") is True  # single-letter dir

    def test_is_placeholder_path_real_paths(self):
        assert docs_auditor._is_placeholder_path("reflections/docs_auditor.py") is False
        assert docs_auditor._is_placeholder_path("agent/output_router.py") is False

    def test_is_placeholder_path_empty_and_single_segment(self):
        assert docs_auditor._is_placeholder_path("") is False
        assert (
            docs_auditor._is_placeholder_path("foo.py") is False
        )  # no slash, not reached normally

    def test_placeholder_paths_suppressed(self, repo: Path):
        content = (
            "## Examples\n"
            "An illustrative path like `foo/bar.py` should not be flagged.\n"
            "Neither should `agent/docs_handler/foo.py` (path-matching example).\n"
        )
        assert _mk_finding(content, repo) == []

    def test_fenced_block_paths_suppressed(self, repo: Path):
        content = "Intro prose.\n```\nsee deleted/gone_module.py for context\n```\nOutro.\n"
        assert _mk_finding(content, repo) == []

    def test_deletion_heading_paths_suppressed(self, repo: Path):
        content = (
            "## Migration from Ollama Intent Classification\n\n"
            "The `intent/__init__.py` module is gone.\n"
        )
        assert _mk_finding(content, repo) == []

    def test_deprecated_heading_suppressed(self, repo: Path):
        content = "### Deprecated\n\nWe used to import `old/legacy_thing.py` here.\n"
        assert _mk_finding(content, repo) == []

    def test_deletion_prose_cue_suppressed(self, repo: Path):
        content = (
            "## Architecture\n\n"
            "The `some/removed_module.py` is no longer in the codebase as of the refactor.\n"
        )
        assert _mk_finding(content, repo) == []

    def test_genuine_dead_reference_not_suppressed(self, repo: Path):
        # Normal prose, normal heading, inline code, path does not exist on disk.
        content = (
            "## Architecture\n\n"
            "The handler lives in `agent/totally_made_up_handler_xyz.py` and runs the loop.\n"
        )
        with patch.object(docs_auditor, "_git_log_follow_renames", return_value=[]):
            findings = _mk_finding(content, repo)
        assert len(findings) == 1
        assert "agent/totally_made_up_handler_xyz.py" in findings[0]["title"]
        assert findings[0]["category"] == "deleted-target"

    def test_existing_path_not_flagged(self, repo: Path):
        # A path that exists on disk is skipped even if it survives the filters.
        (repo / "agent").mkdir()
        (repo / "agent" / "real_module.py").write_text("x = 1\n")
        content = "## Architecture\n\nSee `agent/real_module.py`.\n"
        assert _mk_finding(content, repo) == []

    def test_empty_content_returns_empty(self, repo: Path):
        assert _mk_finding("", repo) == []

    def test_inline_code_not_blanket_suppressed(self, repo: Path):
        # Inline single-backtick code is the normal way real refs are written —
        # it must NOT be suppressed merely for being inline code.
        content = "The module `agent/inline_ref_xyz.py` is referenced inline in prose.\n"
        with patch.object(docs_auditor, "_git_log_follow_renames", return_value=[]):
            findings = _mk_finding(content, repo)
        assert len(findings) == 1


# ---------------------------------------------------------------------------
# TestCrossMachineDedup — live-tracker gate + Redis fast-path
# ---------------------------------------------------------------------------


def _gh_list_result(stdout: str, returncode: int = 0):
    return subprocess.CompletedProcess(args=["gh"], returncode=returncode, stdout=stdout, stderr="")


class TestCrossMachineDedup:
    def test_open_issue_exists_exact_match(self, repo: Path):
        title = "Doc references deleted target: a/b.py (in docs/x.md)"
        out = f'[{{"number": 5, "title": "{title}"}}]'
        with patch("subprocess.run", return_value=_gh_list_result(out)):
            assert docs_auditor._open_issue_exists(title, repo) is True

    def test_open_issue_exists_whitespace_normalized(self, repo: Path):
        title = "Doc references deleted target: a/b.py (in docs/x.md)"
        # Tracker title has collapsed/extra whitespace — still an exact match.
        tracker_title = "Doc references deleted   target: a/b.py (in docs/x.md)"
        out = f'[{{"number": 5, "title": "{tracker_title}"}}]'
        with patch("subprocess.run", return_value=_gh_list_result(out)):
            assert docs_auditor._open_issue_exists(title, repo) is True

    def test_open_issue_exists_no_match(self, repo: Path):
        title = "Doc references deleted target: a/b.py (in docs/x.md)"
        out = '[{"number": 5, "title": "Some unrelated issue"}]'
        with patch("subprocess.run", return_value=_gh_list_result(out)):
            assert docs_auditor._open_issue_exists(title, repo) is False

    def test_open_issue_exists_empty_list(self, repo: Path):
        with patch("subprocess.run", return_value=_gh_list_result("[]")):
            assert docs_auditor._open_issue_exists("anything", repo) is False

    def test_open_issue_exists_nonzero_rc_fails_open(self, repo: Path, caplog):
        with patch("subprocess.run", return_value=_gh_list_result("", returncode=1)):
            assert docs_auditor._open_issue_exists("t", repo) is False
        assert any("dedup" in r.message.lower() for r in caplog.records)

    def test_open_issue_exists_malformed_json_fails_open(self, repo: Path, caplog):
        with patch("subprocess.run", return_value=_gh_list_result("not json{{")):
            assert docs_auditor._open_issue_exists("t", repo) is False
        assert any("dedup" in r.message.lower() for r in caplog.records)

    def test_open_issue_exists_subprocess_raises_fails_open(self, repo: Path, caplog):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("gh", 20)):
            assert docs_auditor._open_issue_exists("t", repo) is False
        assert any("dedup" in r.message.lower() for r in caplog.records)

    def test_open_match_skips_filing(self, repo: Path, patch_redis):
        patch_redis.exists.return_value = False
        finding = {"title": "Doc references deleted target: a/b.py (in docs/x.md)", "body": "b"}
        with (
            patch.object(docs_auditor, "_open_issue_exists", return_value=True),
            patch("subprocess.run") as run,
        ):
            filed = docs_auditor._file_issue_if_new(finding, repo)
        assert filed is False
        # gh issue create must NOT have been called.
        assert run.call_count == 0
        # The local fast-path key is recorded so later runs skip the tracker query.
        patch_redis.set.assert_called_once()

    def test_no_open_match_files(self, repo: Path, patch_redis):
        patch_redis.exists.return_value = False
        finding = {"title": "Doc references deleted target: a/b.py (in docs/x.md)", "body": "b"}
        with (
            patch.object(docs_auditor, "_open_issue_exists", return_value=False),
            patch("subprocess.run", return_value=_gh_list_result("https://gh/issues/9")) as run,
        ):
            filed = docs_auditor._file_issue_if_new(finding, repo)
        assert filed is True
        # gh issue create invoked exactly once (a scutil call for the machine
        # stamp in the issue body may also run — assert on the create call, not
        # the raw subprocess count).
        create_calls = [c for c in run.call_args_list if c.args[0][:3] == ["gh", "issue", "create"]]
        assert len(create_calls) == 1
        create_cmd = create_calls[0].args[0]
        assert create_cmd[:3] == ["gh", "issue", "create"]

    def test_redis_fast_path_skips_tracker_query(self, repo: Path, patch_redis):
        # If the local Redis key already exists, the tracker query is skipped.
        patch_redis.exists.return_value = True
        finding = {"title": "Doc references deleted target: a/b.py (in docs/x.md)", "body": "b"}
        with patch.object(docs_auditor, "_open_issue_exists") as gate:
            filed = docs_auditor._file_issue_if_new(finding, repo)
        assert filed is False
        gate.assert_not_called()

    def test_tracker_failure_falls_back_to_filing(self, repo: Path, patch_redis, caplog):
        # Simulate gh issue list failing inside _open_issue_exists (fail-open ->
        # _open_issue_exists returns False) so filing proceeds via gh create.
        patch_redis.exists.return_value = False
        finding = {"title": "Doc references deleted target: a/b.py (in docs/x.md)", "body": "b"}

        def fake_run(cmd, *a, **k):
            if cmd[:3] == ["gh", "issue", "list"]:
                return _gh_list_result("", returncode=1)  # tracker query fails
            return _gh_list_result("https://gh/issues/9")  # create succeeds

        with patch("subprocess.run", side_effect=fake_run):
            filed = docs_auditor._file_issue_if_new(finding, repo)
        assert filed is True
        # The fail-open warning was logged.
        assert any("dedup" in r.message.lower() for r in caplog.records)

    def test_empty_title_returns_false(self, repo: Path, patch_redis):
        assert docs_auditor._file_issue_if_new({"title": "", "body": "b"}, repo) is False
