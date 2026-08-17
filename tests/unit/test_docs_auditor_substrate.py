"""Tests for the new unified docs auditor substrate (reflections/docs_auditor.py).

Covers the public ``audit()`` callable, rotation reflection, branch sweeper,
SETNX lock contention, neighborhood cap, zero-diff gate, auth probe
degradation, ``refresh_docs_in_memory`` hook, and the ``/do-docs`` thin-caller
contract (pr-changed-files mode).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime, timedelta
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


@pytest.fixture()
def git_repo(repo: Path) -> Path:
    """A ``repo`` that is also a real git checkout.

    The bare-name existence oracle (#2759) resolves a filename with no ``/``
    against a ``git ls-files --cached --others --exclude-standard`` basename
    index. That index only exists inside a git checkout, so ambiguity and
    index-backed resolution have to be exercised here rather than on the plain
    ``tmp_path`` ``repo`` fixture.
    """
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    return repo


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


# ---------------------------------------------------------------------------
# TestStaleTermDictionary
# ---------------------------------------------------------------------------


def _stale_pairs(content: str) -> list[tuple[str, str]]:
    """Flatten the regex fix channel to (pattern_source, replacement) pairs."""
    return [
        (pattern.pattern, new) for pattern, new in docs_auditor._detect_stale_term_fixes(content)
    ]


class TestStaleTermDictionary:
    def test_stale_term_dict_seeded(self):
        assert "SessionLog" in docs_auditor.STALE_TERMS
        assert docs_auditor.STALE_TERMS["SessionLog"] == "AgentSession"
        assert "RedisJob" in docs_auditor.STALE_TERMS

    @pytest.mark.parametrize(
        "content",
        [
            # Bare form — the only shape the pre-#2744 hatch could ever see.
            "The SessionLog has been renamed to AgentSession.",
            # Backticked: the corpus writes every identifier this way, which is
            # why `formerly SessionLog` never matched a single live document.
            "The `SessionLog` has been renamed to `AgentSession`.",
            "`RedisJob` was replaced by `AgentSession` in the same pass.",
            # Mixed case at the start of a sentence.
            "Formerly `RedisJob`, this model is now `AgentSession`.",
            # Alias form — agent-session-migration-audit.md's actual prose.
            "The compat shim declares `SessionLog = AgentSession` for old imports.",
            # Arrow forms — summarizer-output-audit.md's actual prose.
            "Rename map: `SessionLog` -> `AgentSession` across all call sites.",
            "Rename map: `SessionLog` → `AgentSession` across all call sites.",
            # "replacing both the earlier ..." — popoto-redis-expansion.md:7.
            "`AgentSession` lands, replacing both the earlier `SessionLog` and `RedisJob` models.",
        ],
    )
    def test_migration_context_skips_fix(self, content):
        """The hatch must fire on the phrasings the corpus actually uses (#2744).

        Every case here is a document that *correctly* records a completed
        rename. Rewriting any occurrence in it produces a sentence saying the
        new name was formerly itself — a false statement that ships with
        ``fixes_withheld == 0`` and auto-merges unread.
        """
        assert docs_auditor._detect_stale_term_fixes(content) == []

    @pytest.mark.parametrize(
        "content",
        [
            "The SessionLog has methods to track session state.",
            "The SessionLog holds per-turn state and is queried directly.",
            "Worker turns append to the session_log field on exit.",
        ],
    )
    def test_no_migration_context_queues_fix(self, content):
        """The widened hatch must not over-exempt (plan Risk 1).

        Prose that merely *mentions* a stale term without recording a migration
        still has to queue a fix, or the detector becomes decorative.
        """
        assert docs_auditor._detect_stale_term_fixes(content) != []

    def test_fixes_travel_on_the_regex_channel(self):
        fixes = docs_auditor._detect_stale_term_fixes("The SessionLog tracks state.")
        assert fixes
        for pattern, new in fixes:
            assert isinstance(pattern, re.Pattern)
            assert isinstance(new, str)

    def test_cue_word_inside_a_larger_word_does_not_exempt(self):
        """Tier-2 cue words are word-anchored, not substring tests (plan Risk 1).

        ``old`` is a substring of ``threshold``, ``placeholder``, ``holds``,
        ``bold`` — all common in this corpus. With an unanchored test, tier 2
        degenerates into "the document mentions the new term somewhere", which is
        exactly the over-exemption the anti-over-exemption guard claims to
        prevent. Measured live: ``docs/guides/summarizer-output-audit.md`` was
        exempted for ``RedisJob`` solely because it contains ``threshold``.
        """
        content = (
            "The summarize_threshold controls how many turns are batched.\n"
            "`AgentSession` rows are written once per turn.\n"
            "The SessionLog is queried directly by the dashboard.\n"
        )
        assert docs_auditor._detect_stale_term_fixes(content) != []

    @pytest.mark.parametrize("content", ["", "   \n\n  ", "Nothing stale here at all."])
    def test_absent_key_emits_no_fix(self, content):
        """Empty, whitespace-only, and stale-term-free content all yield no fix."""
        assert docs_auditor._detect_stale_term_fixes(content) == []


# ---------------------------------------------------------------------------
# TestStaleTermWordBoundary — the #2711 regression
# ---------------------------------------------------------------------------


class TestStaleTermWordBoundary:
    @pytest.mark.parametrize(
        "content",
        [
            "| `save_session_snapshot()` | `agent/session_logs.py` | (none) |",
            "See `bridge/session_logs.py` for the re-export shim.",
            "The SessionLogs collection is plural.",
            "A `_RedisJob` wrapper carries the payload.",
        ],
    )
    def test_compound_identifiers_are_not_rewritten(self, content):
        """`session_log` must not match inside `session_logs` (issue #2711)."""
        assert docs_auditor._detect_stale_term_fixes(content) == []

    def test_standalone_term_still_matches(self):
        assert _stale_pairs("The session_log field is written on exit.") == [
            (r"\bsession_log\b", "agent_session")
        ]

    def test_apply_leaves_session_logs_path_untouched(self, repo):
        """Path tokens are never rewritten, whether or not the target exists.

        ``agent/session_logs.py`` is saved by word-anchoring (#2711).
        ``models/session_log.py`` is not: ``/`` and ``.`` are word boundaries, so
        ``\\bsession_log\\b`` matches the whole path segment and rewrites it to
        ``models/agent_session.py``. Both files exist on disk here on purpose —
        that is the point. The #2728 existence invariant cannot catch a rewrite
        whose target exists, so only path-token suppression stands between a
        correct doc and a silently false one (#2744).
        """
        doc = repo / "docs" / "features" / "snap.md"
        original = (
            "Snapshots live in `agent/session_logs.py`.\n"
            "The backward-compat shim lives in `models/session_log.py`.\n"
        )
        doc.write_text(original)
        (repo / "agent").mkdir()
        (repo / "agent" / "session_logs.py").write_text("")
        (repo / "models").mkdir()
        (repo / "models" / "session_log.py").write_text("")
        # The rewrite target exists too — the existence invariant will pass it.
        (repo / "models" / "agent_session.py").write_text("")

        regex_fixes = docs_auditor._detect_stale_term_fixes(original)
        applied, withheld = docs_auditor._apply_fixes_to_file(
            Path("docs/features/snap.md"), repo, regex_fixes
        )
        assert applied == 0
        assert withheld == []
        assert doc.read_text() == original

    def test_stale_term_inside_fenced_block_is_not_rewritten(self, repo):
        """A fenced code block is illustrative, not prose to modernize.

        Suppression is an *apply-time* property (the detector keeps returning
        ``(re.Pattern, str)``), so the assertion is on the resulting file
        content, not on the detector's return value.
        """
        doc = repo / "docs" / "features" / "fence.md"
        original = (
            "# Notes\n"
            "\n"
            "Historical example, kept verbatim:\n"
            "\n"
            "```python\n"
            "rows = SessionLog.objects.all()\n"
            "```\n"
        )
        doc.write_text(original)

        regex_fixes = docs_auditor._detect_stale_term_fixes(original)
        applied, withheld = docs_auditor._apply_fixes_to_file(
            Path("docs/features/fence.md"), repo, regex_fixes
        )
        assert applied == 0
        assert withheld == []
        assert doc.read_text() == original

    def test_suppression_survives_an_earlier_shortening_fix(self, repo):
        """Apply-time suppression must survive an earlier fix shortening the text.

        The regex loop rewrites ``new_text`` across iterations, so any line index
        computed against the pre-loop content is stale for every fix after the
        first. Here the first fix deletes three lines that sit *ahead* of a
        fenced code block, shifting the fence up by three: a detection-time index
        would read line 6 as the ordinary prose line ``Kept prose line.`` instead
        of the fenced ``SessionLog`` line, conclude "not in a fence", and rewrite
        inside the illustrative block. It would do so silently, never raising —
        hence the assertion is on the resulting file content.

        The shortening regex here is a *synthetic* stand-in for a ``STALE_TERMS``
        shape that does not exist today: every current replacement is newline-free
        and strictly longer than its key, so no production fix can shift a later
        fix's line index. This test guards the forward-looking case, for operator
        entries that shorten text or span newlines.
        """
        doc = repo / "docs" / "features" / "ctx.md"
        original = (
            "# Notes\n"
            "\n"
            "Filler one.\n"
            "Filler two.\n"
            "Filler three.\n"
            "\n"
            "Kept prose line.\n"
            "\n"
            "```python\n"
            "rows = SessionLog.objects.all()\n"
            "```\n"
        )
        doc.write_text(original)

        # Fix 1 shortens the document by exactly three lines...
        shortening = (re.compile(r"Filler one\.\nFiller two\.\nFiller three\.\n"), "")
        # ...ahead of fix 2's match, which must stay suppressed inside the fence.
        stale = docs_auditor._detect_stale_term_fixes(original)
        assert stale, "fixture must queue a stale-term fix for the fenced line"

        applied, withheld = docs_auditor._apply_fixes_to_file(
            Path("docs/features/ctx.md"), repo, [shortening, *stale]
        )

        text = doc.read_text()
        assert (applied, withheld) == (1, [])
        # The earlier lines are gone...
        assert "Filler one." not in text
        assert "Kept prose line." in text
        # ...and the later stale term is still correctly suppressed.
        assert "rows = SessionLog.objects.all()" in text
        assert "AgentSession" not in text


# ---------------------------------------------------------------------------
# TestExistenceInvariant — no fix may introduce an absent repo path
# ---------------------------------------------------------------------------


class TestExistenceInvariant:
    @pytest.fixture()
    def doc(self, repo):
        p = repo / "docs" / "features" / "inv.md"
        # Every match anchor below is an ordinary PROSE word, never a path token.
        # Path-token suppression (#2744) refuses an in-token rewrite *before* the
        # existence invariant ever runs, so a fixture matching inside
        # `agent/real.py` would yield `applied == 0` / `withheld == []` and prove
        # nothing. The path-shaped string always travels as the *replacement*.
        # Do not "simplify" these back into path-shaped patterns.
        p.write_text(
            "The real handler drives the loop.\n"
            "The other helper wraps it.\n"
            "A ghost adapter is planned.\n"
            "The renamed shim is still referenced.\n"
        )
        (repo / "agent").mkdir()
        (repo / "agent" / "real.py").write_text("")
        (repo / "agent" / "other.py").write_text("")
        return Path("docs/features/inv.md")

    def test_fix_introducing_absent_path_is_rejected_and_reported(self, repo, doc):
        applied, withheld = docs_auditor._apply_fixes_to_file(
            doc, repo, [(re.compile(r"\breal\b"), "agent/ghost.py")]
        )
        assert applied == 0
        assert len(withheld) == 1
        assert withheld[0] == {
            "doc": str(doc),
            "old": r"\breal\b",
            "new": "agent/ghost.py",
            "reason": "target-absent",
        }
        assert "agent/ghost.py" not in (repo / doc).read_text()

    def test_rejection_is_logged_with_offending_path(self, repo, doc, caplog):
        with caplog.at_level("WARNING", logger="reflections.docs_auditor"):
            docs_auditor._apply_fixes_to_file(
                doc, repo, [(re.compile(r"\breal\b"), "agent/ghost.py")]
            )
        assert any("agent/ghost.py" in r.getMessage() for r in caplog.records)

    def test_sibling_valid_fix_still_applies(self, repo, doc):
        (repo / "agent" / "renamed.py").write_text("")
        applied, withheld = docs_auditor._apply_fixes_to_file(
            doc,
            repo,
            [
                (re.compile(r"\breal\b"), "agent/ghost.py"),
                (re.compile(r"\bother\b"), "agent/renamed.py"),
            ],
        )
        assert applied == 1
        assert len(withheld) == 1
        text = (repo / doc).read_text()
        assert "agent/renamed.py" in text
        assert "The real handler" in text
        assert "agent/ghost.py" not in text

    def test_all_fixes_rejected_writes_nothing(self, repo, doc):
        original = (repo / doc).read_text()
        mtime = (repo / doc).stat().st_mtime_ns
        applied, withheld = docs_auditor._apply_fixes_to_file(
            doc,
            repo,
            [
                (re.compile(r"\breal\b"), "agent/ghost.py"),
                (re.compile(r"\bother\b"), "agent/phantom.py"),
            ],
        )
        assert applied == 0
        assert len(withheld) == 2
        assert (repo / doc).read_text() == original
        assert (repo / doc).stat().st_mtime_ns == mtime

    def test_preexisting_absent_path_is_never_revalidated(self, repo):
        p = repo / "docs" / "features" / "pre.md"
        p.write_text("Legacy `agent/vanished.py` and the real handler.\n")
        (repo / "agent").mkdir()
        (repo / "agent" / "kept.py").write_text("")
        applied, withheld = docs_auditor._apply_fixes_to_file(
            Path("docs/features/pre.md"), repo, [(re.compile(r"\breal\b"), "agent/kept.py")]
        )
        assert applied == 1
        assert withheld == []
        assert "agent/vanished.py" in p.read_text()

    # -- bare (unprefixed) filename refs, #2759 ------------------------------
    # These sit ALONGSIDE the dir-prefixed cases above, which stay untouched:
    # widening `_PATH_REF_RE` from `+` to `*` must not change any of them.

    @pytest.fixture()
    def bare_doc(self, repo):
        # Prose anchor again: ``\brunner\b`` matches the standalone word only —
        # never inside a ``*.py`` token, where #2744 would suppress it first.
        p = repo / "docs" / "features" / "bare.md"
        p.write_text("The runtime lives in the runner module.\n")
        return Path("docs/features/bare.md")

    def test_fix_introducing_absent_bare_name_is_withheld(self, repo, bare_doc):
        """The #2711 corruption shape, minus the directory prefix (#2759 AC1).

        ``_PATH_REF_RE`` requires at least one ``dir/`` segment, so a bare
        filename is invisible to the existence invariant and passes
        unconditionally — exactly the class that shipped as ``d7bf3ad99``.
        """
        applied, withheld = docs_auditor._apply_fixes_to_file(
            bare_doc, repo, [(re.compile(r"\brunner\b"), "ghost_module.py")]
        )
        assert applied == 0
        assert withheld == [
            {
                "doc": str(bare_doc),
                "old": r"\brunner\b",
                "new": "ghost_module.py",
                "reason": "target-absent",
            }
        ]
        assert "ghost_module.py" not in (repo / bare_doc).read_text()

    def test_bare_name_resolvable_in_doc_directory_passes(self, repo, bare_doc):
        """Resolution order step 1: the doc's own directory.

        A bare ref in a doc is most often a sibling. Resolving it against the
        repo root instead would read as absent and over-withhold.
        """
        (repo / "docs" / "features" / "headless_runner.py").write_text("")
        applied, withheld = docs_auditor._apply_fixes_to_file(
            bare_doc, repo, [(re.compile(r"\brunner\b"), "headless_runner.py")]
        )
        assert applied == 1
        assert withheld == []
        assert "headless_runner.py" in (repo / bare_doc).read_text()

    def test_ambiguous_bare_name_passes_and_debug_logs(self, git_repo, caplog):
        """Resolution order step 2, ambiguity ruling: >=1 match passes (#2759 AC2).

        The invariant asks "does this name denote something real", not "is it
        unambiguous". Ambiguity never produced the #2711 corruption, so it is
        logged at DEBUG and allowed through rather than withheld.
        """
        repo = git_repo
        doc = repo / "docs" / "features" / "amb.md"
        doc.write_text("The runtime lives in the runner module.\n")
        for pkg in ("alpha", "beta"):
            (repo / pkg).mkdir()
            (repo / pkg / "shared_helper.py").write_text("")

        with caplog.at_level("DEBUG", logger="reflections.docs_auditor"):
            applied, withheld = docs_auditor._apply_fixes_to_file(
                Path("docs/features/amb.md"),
                repo,
                [(re.compile(r"\brunner\b"), "shared_helper.py")],
            )

        assert applied == 1
        assert withheld == []
        assert "shared_helper.py" in doc.read_text()
        assert any(
            r.levelname == "DEBUG" and "shared_helper.py" in r.getMessage() for r in caplog.records
        ), "ambiguous bare-name resolution must be DEBUG-logged"

    def test_preexisting_absent_bare_name_is_never_revalidated(self, repo):
        """Additive-only twin of ``test_preexisting_absent_path_is_never_revalidated``.

        This property is what makes the widening free (spike-4): ``original_refs``
        is computed with the same pattern, so widening widens both sides
        symmetrically and the 1557 newly-visible corpus refs cost zero withholds.
        """
        p = repo / "docs" / "features" / "pre_bare.md"
        p.write_text("Legacy `vanished_module.py` and the runner module.\n")
        (repo / "docs" / "features" / "kept_module.py").write_text("")
        applied, withheld = docs_auditor._apply_fixes_to_file(
            Path("docs/features/pre_bare.md"),
            repo,
            [(re.compile(r"\brunner\b"), "kept_module.py")],
        )
        assert applied == 1
        assert withheld == []
        assert "vanished_module.py" in p.read_text()

    @pytest.mark.parametrize("body", ["", "   \n\t\n"])
    def test_empty_or_whitespace_doc_with_no_fixes_writes_nothing(self, repo, body):
        """With the literal channel gone, an empty ``regex_fixes`` list is the only
        fix-emptiness early-out.
        """
        p = repo / "docs" / "features" / "empty.md"
        p.write_text(body)
        applied, withheld = docs_auditor._apply_fixes_to_file(
            Path("docs/features/empty.md"), repo, []
        )
        assert (applied, withheld) == (0, [])
        assert p.read_text() == body

    # -- degraded `git ls-files`, the only new failure surface ---------------

    @pytest.fixture()
    def clear_basename_cache(self):
        """The index is memoized per repo root; assert against a live call, not a hit."""
        docs_auditor._BASENAME_INDEX_CACHE.clear()
        yield
        docs_auditor._BASENAME_INDEX_CACHE.clear()

    @pytest.mark.parametrize(
        "failure",
        [
            pytest.param(
                lambda: MagicMock(returncode=1, stdout="", stderr="fatal: not a git repository"),
                id="nonzero-rc",
            ),
            pytest.param(
                lambda: (_ for _ in ()).throw(OSError("git: command not found")),
                id="oserror",
            ),
        ],
    )
    def test_ls_files_failure_warns_and_yields_empty_index(
        self, repo, caplog, clear_basename_cache, failure
    ):
        """Plan Risk 3 / Failure Path Test Strategy: the forced-failure case.

        A failed index must be loud (``logger.warning``) and empty, never a
        partially-populated dict that silently answers "absent" for real names.
        """
        with (
            patch.object(docs_auditor.subprocess, "run", side_effect=lambda *a, **k: failure()),
            caplog.at_level("WARNING", logger="reflections.docs_auditor"),
        ):
            index = docs_auditor._repo_basename_index(repo)

        assert index == {}
        assert any(
            r.levelname == "WARNING" and "git ls-files" in r.getMessage() for r in caplog.records
        ), "a degraded basename index must warn"

    @pytest.mark.parametrize(
        "failure",
        [
            pytest.param(
                lambda: MagicMock(returncode=1, stdout="", stderr="fatal: not a git repository"),
                id="nonzero-rc",
            ),
            pytest.param(
                lambda: (_ for _ in ()).throw(OSError("git: command not found")),
                id="oserror",
            ),
        ],
    )
    def test_dir_prefixed_decisions_unaffected_by_degraded_index(
        self, repo, doc, clear_basename_cache, failure
    ):
        """Degradation is scoped to bare names; ``dir/file.py`` never consults the index.

        The fallback must not leak into the path class that already worked, in
        either direction: an absent dir-prefixed target still withholds and a
        present one still applies.
        """
        (repo / "agent" / "renamed.py").write_text("")
        with patch.object(docs_auditor.subprocess, "run", side_effect=lambda *a, **k: failure()):
            applied, withheld = docs_auditor._apply_fixes_to_file(
                doc,
                repo,
                [
                    (re.compile(r"\bghost\b"), "agent/ghost.py"),
                    (re.compile(r"\brenamed\b"), "agent/renamed.py"),
                ],
            )

        assert applied == 1
        assert withheld == [
            {
                "doc": str(doc),
                "old": r"\bghost\b",
                "new": "agent/ghost.py",
                "reason": "target-absent",
            }
        ]
        text = (repo / doc).read_text()
        assert "agent/renamed.py" in text
        assert "agent/ghost.py" not in text

    def test_ok_result_carries_withheld_keys(self):
        res = docs_auditor._ok_result("ok")
        assert res["fixes_withheld"] == 0
        assert res["withheld"] == []

    def test_audit_surfaces_withheld_without_writing(self, repo, auth_ok, patch_redis):
        # The surviving producer is the regex channel, and its match must sit in
        # prose: a path-token match is suppressed by #2744 before the existence
        # invariant runs, and this test would then assert nothing.
        p = repo / "docs" / "features" / "aud.md"
        p.write_text("The SessionRunner drives each turn.\n")

        with (
            patch.object(
                docs_auditor,
                "_detect_stale_term_fixes",
                return_value=[(re.compile(r"\bSessionRunner\b"), "ghost_module.py")],
            ),
            patch.object(
                docs_auditor,
                "_resolve_pr_changed_files",
                return_value=[Path("docs/features/aud.md")],
            ),
            patch.object(docs_auditor, "_commit_current_branch") as commit,
        ):
            result = docs_auditor.audit(
                primary_path=None,
                scope_mode="pr-changed-files",
                apply_mode="apply",
                project_key="test",
                repo_root=repo,
            )

        assert result["status"] == "ok"
        assert result["fixes_withheld"] == 1
        assert result["withheld"][0]["new"] == "ghost_module.py"
        assert result["files_touched"] == []
        commit.assert_not_called()
        assert "ghost_module.py" not in p.read_text()


# ---------------------------------------------------------------------------
# TestWithheldBlocksAutoMerge — the rotation path's only review gate
# ---------------------------------------------------------------------------


class TestWithheldBlocksAutoMerge:
    """A run that withheld a fix must not produce an auto-mergeable PR."""

    @staticmethod
    def _meta(body: str) -> dict:
        return {
            "files": [{"path": "docs/features/foo.md"}],
            "reviews": [],
            "reviewRequests": [],
            "comments": [],
            "additions": 1,
            "deletions": 1,
            "createdAt": (datetime.now(UTC) - timedelta(days=3)).isoformat().replace("+00:00", "Z"),
            "body": body,
        }

    def _eligible(self, body: str) -> bool:
        with patch("reflections.docs_auditor.subprocess.run") as run:
            run.return_value = MagicMock(returncode=0, stdout=json.dumps(self._meta(body)))
            return docs_auditor._pr_is_auto_merge_eligible(123)

    def test_clean_pr_is_eligible(self):
        assert self._eligible("Automated docs auditor pass.") is True

    def test_withheld_marker_disqualifies(self):
        body = f"Automated docs auditor pass.\n\n{docs_auditor.WITHHELD_PR_MARKER}\n1 withheld"
        assert self._eligible(body) is False

    def test_pr_body_carries_marker_when_fixes_withheld(self, repo):
        calls: list[list[str]] = []

        def fake_run(cmd, *a, **kw):
            calls.append(cmd)
            return MagicMock(returncode=0, stdout="https://github.com/o/r/pull/1", stderr="")

        withheld = [
            {
                "doc": "docs/features/x.md",
                "old": "a/b.py",
                "new": "a/c.py",
                "reason": "target-absent",
            }
        ]
        with (
            patch("reflections.docs_auditor.subprocess.run", side_effect=fake_run),
            patch("reflections.docs_auditor._daily_pr_cap_reached", return_value=False),
            patch("reflections.docs_auditor._has_open_pr_for_slug", return_value=False),
            patch("reflections.docs_auditor._record_daily_pr"),
        ):
            docs_auditor._push_branch_and_pr("slug", repo, withheld=withheld)

        create = next(c for c in calls if c[:3] == ["gh", "pr", "create"])
        body = create[create.index("--body") + 1]
        assert docs_auditor.WITHHELD_PR_MARKER in body
        assert "a/c.py" in body

    def test_bare_name_withhold_propagates_to_pr_body_telegram_and_liveness(
        self, repo, auth_ok, patch_redis
    ):
        """The new bare-name withhold class must reach every operator surface.

        Computing the withhold is not enough — #2759's whole value is that the
        rotation PR becomes auto-merge-ineligible and a human hears about it.
        The withheld record here is produced by a real ``audit()`` run, not
        hand-written, so the first assertion is the one that fails on ``main``.
        """
        # Prose anchor, not a path token — see the #2744 note on the sibling
        # ``test_audit_surfaces_withheld_without_writing``.
        p = repo / "docs" / "features" / "foo.md"
        p.write_text("The SessionRunner drives each turn.\n" + "Padding line.\n" * 6)

        with (
            patch.object(
                docs_auditor,
                "_detect_stale_term_fixes",
                return_value=[(re.compile(r"\bSessionRunner\b"), "ghost_module.py")],
            ),
            patch.object(
                docs_auditor,
                "_resolve_pr_changed_files",
                return_value=[Path("docs/features/foo.md")],
            ),
            patch.object(docs_auditor, "_commit_current_branch"),
        ):
            audit_result = docs_auditor.audit(
                primary_path=None,
                scope_mode="pr-changed-files",
                apply_mode="apply",
                project_key="test",
                repo_root=repo,
            )

        assert audit_result["fixes_withheld"] == 1
        assert audit_result["withheld"][0]["new"] == "ghost_module.py"
        assert "ghost_module.py" not in p.read_text()

        # Surface 1 — the PR body carries the marker and the offending name.
        calls: list[list[str]] = []

        def fake_run(cmd, *a, **kw):
            calls.append(cmd)
            return MagicMock(returncode=0, stdout="https://github.com/o/r/pull/1", stderr="")

        with (
            patch("reflections.docs_auditor.subprocess.run", side_effect=fake_run),
            patch("reflections.docs_auditor._daily_pr_cap_reached", return_value=False),
            patch("reflections.docs_auditor._has_open_pr_for_slug", return_value=False),
            patch("reflections.docs_auditor._record_daily_pr"),
        ):
            docs_auditor._push_branch_and_pr("slug", repo, withheld=audit_result["withheld"])

        create = next(c for c in calls if c[:3] == ["gh", "pr", "create"])
        body = create[create.index("--body") + 1]
        assert docs_auditor.WITHHELD_PR_MARKER in body
        assert "ghost_module.py" in body
        # ...and that marker is what makes the PR auto-merge-ineligible.
        assert self._eligible(body) is False

        # Surfaces 2 and 3 — the Telegram notification and Redis liveness.
        with (
            patch("reflections.docs_auditor.PROJECT_ROOT", repo),
            patch("reflections.docs_auditor._git_dirty", return_value=False),
            patch("reflections.docs_auditor._run_vault_drift_detection", return_value=0),
            patch("reflections.docs_auditor.audit", return_value=audit_result),
            patch("reflections.docs_auditor._send_telegram_notification") as notify,
            patch("reflections.docs_auditor._update_rotation_hash"),
            patch("reflections.docs_auditor._write_liveness") as liveness,
        ):
            result = docs_auditor.run_docs_auditor()

        assert result["status"] == "ok"
        assert "1 fix(es) withheld" in notify.call_args.args[0]
        assert liveness.call_args.kwargs["fixes_withheld"] == 1

    def test_rotation_result_surfaces_withheld_count(self, repo, auth_ok, patch_redis):
        primary = repo / "docs" / "features" / "foo.md"
        primary.write_text("# Foo\n" + "Padding line.\n" * 6)
        audit_result = docs_auditor._ok_result(
            "ok",
            files_touched=["docs/features/foo.md"],
            fixes_applied=1,
            fixes_withheld=2,
            withheld=[
                {"doc": "docs/features/foo.md", "old": "a/b.py", "new": "a/c.py", "reason": "x"},
                {"doc": "docs/features/foo.md", "old": "a/d.py", "new": "a/e.py", "reason": "x"},
            ],
        )
        with (
            patch("reflections.docs_auditor.PROJECT_ROOT", repo),
            patch("reflections.docs_auditor._git_dirty", return_value=False),
            patch("reflections.docs_auditor._git_diff_quiet", return_value=False),
            patch("reflections.docs_auditor._run_vault_drift_detection", return_value=0),
            patch("reflections.docs_auditor.audit", return_value=audit_result),
            patch("reflections.docs_auditor._push_branch_and_pr", return_value=None) as push,
            patch("reflections.docs_auditor._send_telegram_notification") as notify,
            patch("reflections.docs_auditor._update_rotation_hash"),
            patch("reflections.docs_auditor._write_liveness"),
        ):
            result = docs_auditor.run_docs_auditor()

        assert result["status"] == "ok"
        assert any("2 fix(es) withheld" in f for f in result["findings"])
        assert "withheld" in result["summary"]
        assert push.call_args.kwargs["withheld"] == audit_result["withheld"]
        assert "withheld" in notify.call_args.args[0]

    def test_all_withheld_zero_diff_run_still_notifies(self, repo, auth_ok, patch_redis):
        """Every fix rejected => no files touched => the step-9 notify is unreachable.

        That is the loudest case (the auditor tried to invent paths), so the
        zero-diff early return must send its own Telegram alert.
        """
        primary = repo / "docs" / "features" / "foo.md"
        primary.write_text("# Foo\n" + "Padding line.\n" * 6)
        audit_result = docs_auditor._ok_result(
            "ok",
            files_touched=[],
            fixes_applied=0,
            fixes_withheld=2,
            withheld=[
                {"doc": "docs/features/foo.md", "old": "a/b.py", "new": "a/c.py", "reason": "x"},
                {"doc": "docs/features/foo.md", "old": "a/d.py", "new": "a/e.py", "reason": "x"},
            ],
        )
        with (
            patch("reflections.docs_auditor.PROJECT_ROOT", repo),
            patch("reflections.docs_auditor._git_dirty", return_value=False),
            patch("reflections.docs_auditor._run_vault_drift_detection", return_value=0),
            patch("reflections.docs_auditor.audit", return_value=audit_result),
            patch("reflections.docs_auditor._send_telegram_notification") as notify,
            patch("reflections.docs_auditor._update_rotation_hash"),
            patch("reflections.docs_auditor._write_liveness") as liveness,
        ):
            result = docs_auditor.run_docs_auditor()

        assert result["status"] == "ok"
        assert "zero-diff" in result["summary"]
        assert notify.call_count == 1
        msg = notify.call_args.args[0]
        assert "2 fix(es) withheld" in msg
        assert "docs_features_foo_md" in msg  # the rotation slug
        assert liveness.call_args.kwargs["fixes_withheld"] == 2

    def test_clean_zero_diff_run_does_not_notify(self, repo, auth_ok, patch_redis):
        """No withholding => a zero-diff pass stays silent, as before."""
        primary = repo / "docs" / "features" / "foo.md"
        primary.write_text("# Foo\n" + "Padding line.\n" * 6)
        audit_result = docs_auditor._ok_result("ok", files_touched=[], fixes_applied=0)
        with (
            patch("reflections.docs_auditor.PROJECT_ROOT", repo),
            patch("reflections.docs_auditor._git_dirty", return_value=False),
            patch("reflections.docs_auditor._run_vault_drift_detection", return_value=0),
            patch("reflections.docs_auditor.audit", return_value=audit_result),
            patch("reflections.docs_auditor._send_telegram_notification") as notify,
            patch("reflections.docs_auditor._update_rotation_hash"),
            patch("reflections.docs_auditor._write_liveness"),
        ):
            docs_auditor.run_docs_auditor()

        assert notify.call_count == 0


# ---------------------------------------------------------------------------
# TestWithheldRateNonRegression — #2759 AC4
# ---------------------------------------------------------------------------


# The pre-#2759 pattern: `+` requires at least one `dir/` segment, so bare
# filenames were invisible to the existence invariant. This is the "before" arm.
_NARROW_PATH_REF_RE = re.compile(r"(?:[\w.-]+/)+[\w.-]+\.(?:py|md)")


class TestWithheldRateNonRegression:
    """Widening ``_PATH_REF_RE`` adds no withholds on the real corpus (#2759 AC4).

    This is the mechanical proof behind the plan's ruling that #2759 does **not**
    block on #2729 (withheld PRs are auto-merge-ineligible forever and stale-close
    at day 14). Two measurement paths report a reassuring zero regardless of the
    change's real effect and are therefore not used:

    1. ``_detect_stale_term_fixes`` alone never reaches ``_absent_new_path_refs``;
       ``fixes_withheld`` is populated only by ``_apply_fixes_to_file``.
    2. ``audit(apply_mode="dry-run")`` is *also* wrong — ``_apply_fixes_to_file``
       is gated on ``apply_mode == "apply"``, so dry-run leaves ``withheld`` empty
       unconditionally.

    So ``_apply_fixes_to_file`` is driven directly, against a **real git checkout**
    (both the existence oracle and the ``git ls-files`` basename index need one; a
    ``tmp_path`` mirror makes every reference read as absent and turns the
    measurement into noise) — specifically a **disposable detached worktree**, torn
    down in a ``finally``. Never the live checkout: ``_apply_fixes_to_file`` writes
    with ``full.write_text``, several agents test on this machine concurrently, and
    ``pytest-clean.sh`` runs under ``--timeout=420 --timeout-method=thread``, so a
    restore that does not run is a realistic outcome with another lane's checkout as
    its blast radius.

    **Self-baselining, no pinned constant.** Both arms are measured in the same run
    over the same corpus snapshot — narrow ``+`` vs. the shipped widened ``*`` —
    asserting ``after <= before``. A hard-coded baseline would rot against a corpus
    the auditor rewrites daily.

    **The fix set deliberately bypasses the migration-context hatch.** After #2744
    the hatch exempts every doc in the live corpus that mentions a ``STALE_TERMS``
    key, so ``_detect_stale_term_fixes`` proposes nothing there and a hatch-filtered
    measurement would be ``0 == 0`` with the invariant never invoked — the third way
    to report a vacuous zero. Building the fix set straight from ``STALE_TERMS``
    keeps real corpus text flowing through ``_absent_new_path_refs``, which is the
    guard AC4 is actually about. The non-vacuity assertions below fail loudly if a
    future corpus or detector change ever hollows this out.
    """

    CORPUS_GLOB = "docs/features/*.md"

    @staticmethod
    def _stale_term_fixes(content: str) -> list[tuple[re.Pattern[str], str]]:
        """Every ``STALE_TERMS`` rewrite the corpus text could possibly attract."""
        fixes = []
        for old_term, new_term in docs_auditor.STALE_TERMS.items():
            pattern = re.compile(rf"\b{re.escape(old_term)}\b")
            if pattern.search(content):
                fixes.append((pattern, new_term))
        return fixes

    @classmethod
    def _measure(cls, worktree: Path, *, narrow: bool) -> dict[str, int]:
        """Run the corpus through ``_apply_fixes_to_file`` under one regex arm.

        Restores the worktree afterwards so the second arm sees the identical
        snapshot, and clears the basename-index cache (memoized on
        ``repo_root.resolve()``, i.e. shared by both arms) so neither measurement
        answers from the other's snapshot.
        """
        saved_re = docs_auditor._PATH_REF_RE
        saved_invariant = docs_auditor._absent_new_path_refs
        invariant_calls = 0

        def _spy(*args, **kwargs):
            nonlocal invariant_calls
            invariant_calls += 1
            return saved_invariant(*args, **kwargs)

        docs_auditor._absent_new_path_refs = _spy
        if narrow:
            docs_auditor._PATH_REF_RE = _NARROW_PATH_REF_RE
        docs_auditor._BASENAME_INDEX_CACHE.clear()

        withheld_total = 0
        visible_refs: set[str] = set()
        try:
            for full in sorted(worktree.glob(cls.CORPUS_GLOB)):
                content = full.read_text(encoding="utf-8", errors="replace")
                visible_refs |= set(docs_auditor._PATH_REF_RE.findall(content))
                fixes = cls._stale_term_fixes(content)
                if not fixes:
                    continue
                _, withheld = docs_auditor._apply_fixes_to_file(
                    full.relative_to(worktree), worktree, fixes
                )
                withheld_total += len(withheld)
        finally:
            docs_auditor._PATH_REF_RE = saved_re
            docs_auditor._absent_new_path_refs = saved_invariant
            docs_auditor._BASENAME_INDEX_CACHE.clear()
            subprocess.run(
                ["git", "checkout", "--", "."], cwd=worktree, check=True, capture_output=True
            )

        return {
            "withheld": withheld_total,
            "invariant_calls": invariant_calls,
            "visible_refs": len(visible_refs),
        }

    def test_widening_path_ref_re_adds_no_withholds(self):
        repo_root = Path(docs_auditor.__file__).resolve().parents[1]
        if not (repo_root / ".git").exists():
            pytest.skip("not a git checkout — the existence oracle needs a real index")

        holder = Path(tempfile.mkdtemp(prefix="docs-auditor-withheld-rate-"))
        worktree = holder / "corpus"
        subprocess.run(
            ["git", "worktree", "add", "--detach", str(worktree), "HEAD"],
            cwd=repo_root,
            check=True,
            capture_output=True,
        )
        try:
            before = self._measure(worktree, narrow=True)
            after = self._measure(worktree, narrow=False)
        finally:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(worktree)],
                cwd=repo_root,
                check=False,
                capture_output=True,
            )
            shutil.rmtree(holder, ignore_errors=True)
            docs_auditor._BASENAME_INDEX_CACHE.clear()

        # The two arms must genuinely differ, or "after <= before" is a tautology
        # over one pattern measured twice.
        assert after["visible_refs"] > before["visible_refs"], (
            f"widening made no new refs visible ({after} vs {before}) — "
            "the narrow arm is not being applied"
        )
        # ...and the guard being measured must actually have run.
        assert before["invariant_calls"] > 0 and after["invariant_calls"] > 0, (
            f"the existence invariant was never invoked ({before}, {after}) — "
            "the measurement is vacuous, not clean"
        )
        # #2759 AC4: widening `original_refs` and the candidate scan symmetrically
        # costs no new withholds, because `_absent_new_path_refs` is additive-only.
        assert after["withheld"] <= before["withheld"], (
            f"widening _PATH_REF_RE raised the withheld count: {before} -> {after}"
        )


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
        findings = _mk_finding(content, repo)
        assert len(findings) == 1

    def test_rename_destination_reference_is_reported(self, git_repo: Path):
        """A deleted rename destination must now reach the human-facing report.

        Before #2741 the reporter called ``git log --follow`` and dropped any
        reference whose path had a rename anywhere in history. This fixture is
        exactly that shape — ``pkgdir/old_module.py`` renamed to
        ``pkgdir/new_module.py``, then deleted — so on the pre-change code the
        finding was silently suppressed. The ``--follow`` assertion below pins
        the suppression condition down against a real checkout, which is what
        keeps this a regression guard rather than a tautology.
        """
        repo = git_repo
        env = ["-c", "user.email=t@e.st", "-c", "user.name=Test"]

        def git(*args: str) -> subprocess.CompletedProcess:
            return subprocess.run(
                ["git", *env, *args], cwd=repo, check=True, capture_output=True, text=True
            )

        (repo / "pkgdir").mkdir()
        (repo / "pkgdir" / "old_module.py").write_text("x = 1\n")
        git("add", "pkgdir/old_module.py")
        git("commit", "-qm", "add old_module")
        git("mv", "pkgdir/old_module.py", "pkgdir/new_module.py")
        git("commit", "-qm", "rename to new_module")
        git("rm", "-q", "pkgdir/new_module.py")
        git("commit", "-qm", "delete new_module")

        # The pre-change suppression condition, asserted directly: the deleted
        # path really is a rename destination in this checkout's history.
        follow = git(
            "log",
            "--follow",
            "--diff-filter=R",
            "--name-status",
            "--format=",
            "--",
            "pkgdir/new_module.py",
        )
        assert "pkgdir/old_module.py" in follow.stdout, (
            "fixture must reproduce the rename-destination history the old "
            "`git log --follow` suppression keyed on"
        )

        content = (
            "## Architecture\n\nThe handler lives in `pkgdir/new_module.py` and runs the loop.\n"
        )
        findings = _mk_finding(content, repo)
        assert len(findings) == 1
        assert "pkgdir/new_module.py" in findings[0]["title"]
        assert findings[0]["category"] == "deleted-target"


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


# ---------------------------------------------------------------------------
# TestVaultSiteDrift — curated VAULT_SITE_MAPPING drift detector + secrets guard
# ---------------------------------------------------------------------------


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    """A populated fake vault root."""
    root = tmp_path / "vault"
    root.mkdir()
    return root


class TestIsSecretsPath:
    def test_mixed_case_secrets_component_excluded(self, vault: Path):
        assert docs_auditor._is_secrets_path("Secrets/API Keys.md", vault) is True
        assert docs_auditor._is_secrets_path("secrets/creds.md", vault) is True

    def test_near_miss_not_excluded(self, vault: Path):
        # Component equality, not substring: siblings are NOT over-matched.
        (vault / "secrets-analysis.md").write_text("x")
        assert docs_auditor._is_secrets_path("secrets-analysis.md", vault) is False
        (vault / "Secretsandbox").mkdir()
        (vault / "Secretsandbox" / "n.md").write_text("x")
        assert docs_auditor._is_secrets_path("Secretsandbox/n.md", vault) is False

    def test_symlink_into_secrets_excluded(self, vault: Path):
        # A symlink whose own name does NOT say "secrets" but points INTO a real
        # secrets/ tree must be caught by the resolved-path check.
        real_secrets = vault / "secrets"
        real_secrets.mkdir()
        (real_secrets / "keys.md").write_text("secret")
        link = vault / "innocent.md"
        link.symlink_to(real_secrets / "keys.md")
        assert docs_auditor._is_secrets_path("innocent.md", vault) is True

    def test_out_of_vault_value_error_excluded(self, vault: Path):
        # An entry that resolves OUTSIDE the vault -> ValueError -> fail-closed.
        assert docs_auditor._is_secrets_path("../outside.md", vault) is True

    def test_mapping_has_no_secrets_entry(self, vault: Path):
        # Build/test-time invariant: no shipped mapping entry is a secrets/ path.
        for rel_path in docs_auditor.VAULT_SITE_MAPPING:
            assert not any(part.lower() == "secrets" for part in Path(rel_path).parts), (
                f"mapping entry '{rel_path}' is a secrets/ path"
            )


class TestVaultSiteDrift:
    def _populate(self, vault: Path, rel_paths: list[str]) -> None:
        for rel in rel_paths:
            p = vault / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f"# {rel}\n\nnarrative body\n")

    def test_compared_nonzero_on_populated_vault(self, vault: Path, repo: Path):
        mapping = {
            "Overview.md": ("site/index.html", None),
            "Deck.md": ("site/research.html", None),
        }
        self._populate(vault, list(mapping))
        with (
            patch.object(docs_auditor, "VAULT_SITE_MAPPING", mapping),
            # Every target has no commit history -> ts 0 -> everything drifts.
            patch.object(docs_auditor, "_git_commit_ts", return_value=0),
        ):
            findings, compared = docs_auditor._detect_vault_site_drift(vault, repo)
        assert compared == 2
        assert len(findings) == 2
        assert all(f["category"] == "vault-drift" for f in findings)
        # Title encodes both vault path and site page (composite dedup key).
        assert any(
            "Overview.md" in f["title"] and "site/index.html" in f["title"] for f in findings
        )

    def test_no_drift_when_site_newer(self, vault: Path, repo: Path):
        mapping = {"Overview.md": ("site/index.html", None)}
        self._populate(vault, list(mapping))
        with (
            patch.object(docs_auditor, "VAULT_SITE_MAPPING", mapping),
            # Site committed far in the future -> vault mtime older -> no drift.
            patch.object(docs_auditor, "_git_commit_ts", return_value=9_999_999_999),
        ):
            findings, compared = docs_auditor._detect_vault_site_drift(vault, repo)
        assert compared == 1
        assert findings == []

    def test_repo_doc_counterpart_adds_second_finding(self, vault: Path, repo: Path):
        mapping = {"Overview.md": ("site/index.html", "docs/features/overview.md")}
        self._populate(vault, list(mapping))
        with (
            patch.object(docs_auditor, "VAULT_SITE_MAPPING", mapping),
            patch.object(docs_auditor, "_git_commit_ts", return_value=0),
        ):
            findings, compared = docs_auditor._detect_vault_site_drift(vault, repo)
        assert compared == 1
        # One finding for the site page, one for the repo doc.
        assert len(findings) == 2
        assert any("docs/features/overview.md" in f["title"] for f in findings)

    def test_missing_vault_file_not_counted(self, vault: Path, repo: Path):
        mapping = {"Present.md": ("site/index.html", None), "Absent.md": ("site/x.html", None)}
        self._populate(vault, ["Present.md"])  # Absent.md intentionally not created
        with (
            patch.object(docs_auditor, "VAULT_SITE_MAPPING", mapping),
            patch.object(docs_auditor, "_git_commit_ts", return_value=0),
        ):
            findings, compared = docs_auditor._detect_vault_site_drift(vault, repo)
        assert compared == 1  # only the present narrative counts

    def test_markitdown_sidecar_skipped(self, vault: Path, repo: Path):
        mapping = {"Sidecar.md": ("site/index.html", None)}
        (vault / "Sidecar.md").write_text("---\ngenerated_by: markitdown\n---\n\nbody\n")
        with (
            patch.object(docs_auditor, "VAULT_SITE_MAPPING", mapping),
            patch.object(docs_auditor, "_git_commit_ts", return_value=0),
        ):
            findings, compared = docs_auditor._detect_vault_site_drift(vault, repo)
        assert compared == 0
        assert findings == []

    def test_secrets_entry_never_read(self, vault: Path, repo: Path):
        # A secrets-guarded mapping entry is skipped before any read/compare.
        mapping = {"secrets/keys.md": ("site/index.html", None)}
        (vault / "secrets").mkdir()
        (vault / "secrets" / "keys.md").write_text("SECRET")
        with (
            patch.object(docs_auditor, "VAULT_SITE_MAPPING", mapping),
            patch.object(docs_auditor, "_git_commit_ts", return_value=0),
        ):
            findings, compared = docs_auditor._detect_vault_site_drift(vault, repo)
        assert compared == 0
        assert findings == []

    def test_issue_cap_enforced(self, vault: Path, patch_redis):
        # More drift findings than the cap -> at most VAULT_DRIFT_ISSUE_CAP filed.
        many = [
            {
                "title": f"docs-auditor: vault narrative 'n{i}.md' has drifted from site/x.html",
                "body": "b",
                "category": "vault-drift",
            }
            for i in range(docs_auditor.VAULT_DRIFT_ISSUE_CAP + 3)
        ]
        with (
            patch.object(docs_auditor, "PROJECT_ROOT", vault),
            patch.object(docs_auditor, "_resolve_vault_root", return_value=vault),
            patch.object(docs_auditor, "_detect_vault_site_drift", return_value=(many, len(many))),
            patch.object(docs_auditor, "_file_issue_if_new", return_value=True) as filer,
        ):
            compared = docs_auditor._run_vault_drift_detection("valor")
        assert compared == len(many)
        assert filer.call_count == docs_auditor.VAULT_DRIFT_ISSUE_CAP

    def test_unresolvable_vault_returns_zero_no_crash(self, vault: Path):
        with (
            patch.object(docs_auditor, "PROJECT_ROOT", vault),
            patch.object(docs_auditor, "_resolve_vault_root", return_value=None),
        ):
            compared = docs_auditor._run_vault_drift_detection("valor")
        assert compared == 0

    def test_resolve_vault_root_missing_mapping_returns_none(self):
        with patch(
            "tools.knowledge.scope_resolver._load_project_mappings",
            return_value=[("/some/other", "psyoptimal")],
        ):
            assert docs_auditor._resolve_vault_root("valor") is None

    def test_resolve_vault_root_found(self):
        with patch(
            "tools.knowledge.scope_resolver._load_project_mappings",
            return_value=[("/vault/valor", "valor")],
        ):
            assert docs_auditor._resolve_vault_root("valor") == Path("/vault/valor")


class TestWriteLivenessVaultParam:
    def _summary(self, fake_redis) -> dict:
        # Find the r.set call that persisted the summary JSON.
        for c in fake_redis.set.call_args_list:
            if c.args and c.args[0] == docs_auditor.REDIS_LAST_COMPLETED_SUMMARY_KEY:
                return json.loads(c.args[1])
        raise AssertionError("summary was not written")

    def test_four_arg_call_omits_vault_count(self, fake_redis, patch_redis):
        docs_auditor._write_liveness("slug", "ok", None, 3)
        summary = self._summary(fake_redis)
        assert "vault_narratives_compared" not in summary

    def test_five_arg_call_includes_vault_count(self, fake_redis, patch_redis):
        docs_auditor._write_liveness("slug", "ok", None, 3, 7)
        summary = self._summary(fake_redis)
        assert summary["vault_narratives_compared"] == 7

    def test_five_arg_zero_is_emitted(self, fake_redis, patch_redis):
        # 0 is distinct from None: a resolved-but-empty vault must be observable.
        docs_auditor._write_liveness("slug", "ok", None, 0, 0)
        summary = self._summary(fake_redis)
        assert summary["vault_narratives_compared"] == 0

    def test_withheld_count_absent_when_zero(self, fake_redis, patch_redis):
        # A clean run must not carry the key at all — same shape as before.
        docs_auditor._write_liveness("slug", "ok", None, 3)
        assert "fixes_withheld" not in self._summary(fake_redis)

    def test_withheld_count_emitted_when_nonzero(self, fake_redis, patch_redis):
        # Redis is the only durable, queryable surface; a withheld run must not
        # be byte-identical to a clean one there.
        docs_auditor._write_liveness("slug", "ok", None, 3, fixes_withheld=2)
        assert self._summary(fake_redis)["fixes_withheld"] == 2

    def test_withheld_is_trailing_and_preserves_positional_contract(self):
        import inspect

        params = list(inspect.signature(docs_auditor._write_liveness).parameters)
        # fixes_withheld must come last so existing 4-arg and 5-arg positional
        # call sites keep their meaning.
        assert params[-1] == "fixes_withheld"
        assert params[:5] == [
            "slug",
            "status",
            "pr_url",
            "files_touched",
            "vault_narratives_compared",
        ]


class TestVaultDeadCodeRemoved:
    def test_default_vault_weight_gone(self):
        assert not hasattr(docs_auditor, "DEFAULT_VAULT_WEIGHT")

    def test_vault_field_gone(self):
        assert not hasattr(docs_auditor, "_vault_field")

    def test_select_primary_doc_has_no_vault_weight_param(self):
        import inspect

        params = inspect.signature(docs_auditor._select_primary_doc).parameters
        assert "vault_weight" not in params

    def test_select_primary_doc_globs_only_docs_features(self, repo: Path, patch_redis):
        # Regression guard: the repo-doc rotation is unaffected by the vault work —
        # only docs/features/*.md are candidates, never vault paths.
        (repo / "docs" / "features" / "a.md").write_text("# A\n")
        (repo / "docs" / "features" / "b.md").write_text("# B\n")
        primary, _ = docs_auditor._select_primary_doc(repo, "valor")
        assert primary is not None
        assert str(primary).startswith("docs/features/")
