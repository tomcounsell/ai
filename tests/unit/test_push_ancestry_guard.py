"""Tests for the push-ancestry merge-bypass guard (WS-E, issue #2026 / #2124).

The guard refuses a push to ``refs/heads/main`` whose HEAD is descended from an
OPEN PR branch head, unless a break-glass authorization is present. It fails
CLOSED on an ancestry match and fails OPEN on a ``gh`` outage (but a local
detached-HEAD-at-PR-tip is refused without ``gh``).

These are stdlib-only unit tests over the pure functions plus the ``check``/
``main`` orchestration with ``gh``/``git`` mocked — no network, no real repo state.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tools import push_ancestry_guard as g


class TestPushTargetParsing:
    def test_line_pushing_to_main_returns_local_sha(self):
        stdin = "refs/heads/feature abc123 refs/heads/main def456\n"
        assert g._pushes_to_main([], stdin) == "abc123"

    def test_line_not_pushing_to_main_returns_none(self):
        stdin = "refs/heads/feature abc123 refs/heads/feature def456\n"
        assert g._pushes_to_main([], stdin) is None

    def test_branch_deletion_all_zero_sha_is_noop(self):
        stdin = f"refs/heads/x {'0' * 40} refs/heads/main def456\n"
        assert g._pushes_to_main([], stdin) is None

    def test_empty_stdin_falls_back_to_head(self):
        with patch.object(g, "_head_sha", return_value="headsha"):
            assert g._pushes_to_main([], "") == "headsha"

    def test_multiple_lines_only_main_line_matters(self):
        stdin = "refs/heads/a s1 refs/heads/feature r1\nrefs/heads/b s2 refs/heads/main r2\n"
        assert g._pushes_to_main([], stdin) == "s2"


class TestCheckWithOpenPRs:
    def test_ancestry_match_refuses_fail_closed(self, capsys):
        prs = [{"number": 42, "headRefName": "feat", "headRefOid": "prhead"}]
        with (
            patch.object(g, "_open_prs", return_value=prs),
            patch.object(g, "_is_ancestor", return_value=True),
            patch.object(g, "_pr_authorized", return_value=False),
        ):
            rc = g.check("pushedsha")
        assert rc == 1
        err = capsys.readouterr().err
        assert g.ERR_ANCESTRY in err
        assert "#42" in err

    def test_ancestry_match_but_authorized_allows(self):
        prs = [{"number": 42, "headRefName": "feat", "headRefOid": "prhead"}]
        with (
            patch.object(g, "_open_prs", return_value=prs),
            patch.object(g, "_is_ancestor", return_value=True),
            patch.object(g, "_pr_authorized", return_value=True),
        ):
            assert g.check("pushedsha") == 0

    def test_no_ancestry_match_allows(self):
        prs = [{"number": 42, "headRefName": "feat", "headRefOid": "prhead"}]
        with (
            patch.object(g, "_open_prs", return_value=prs),
            patch.object(g, "_is_ancestor", return_value=False),
        ):
            assert g.check("pushedsha") == 0

    def test_no_open_prs_allows(self):
        with patch.object(g, "_open_prs", return_value=[]):
            assert g.check("pushedsha") == 0


class TestCheckGhOutageFailOpen:
    def test_gh_unreachable_non_detached_allows(self, capsys):
        with (
            patch.object(g, "_open_prs", return_value=None),
            patch.object(g, "_head_is_detached", return_value=False),
        ):
            assert g.check("pushedsha") == 0
        assert "gh unreachable" in capsys.readouterr().err

    def test_gh_unreachable_detached_at_branch_tip_refuses_locally(self, capsys):
        with (
            patch.object(g, "_open_prs", return_value=None),
            patch.object(g, "_head_is_detached", return_value=True),
            patch.object(g, "_head_sha", return_value="headsha"),
            patch.object(g, "_local_branch_tips", return_value={"feat": "headsha"}),
        ):
            rc = g.check("headsha")
        assert rc == 1
        err = capsys.readouterr().err
        assert g.ERR_DETACHED in err
        assert "feat" in err

    def test_gh_unreachable_detached_not_at_any_tip_allows(self):
        with (
            patch.object(g, "_open_prs", return_value=None),
            patch.object(g, "_head_is_detached", return_value=True),
            patch.object(g, "_head_sha", return_value="headsha"),
            patch.object(g, "_local_branch_tips", return_value={"feat": "otherx"}),
        ):
            assert g.check("headsha") == 0


class TestMain:
    def test_not_pushing_to_main_is_noop(self):
        with patch.object(g.sys.stdin, "isatty", return_value=True):
            # No argv, tty stdin -> _pushes_to_main gets "" -> HEAD; but we short out
            # by making _pushes_to_main return None.
            with patch.object(g, "_pushes_to_main", return_value=None):
                assert g.main([]) == 0

    def test_pushing_to_main_delegates_to_check(self):
        with (
            patch.object(g.sys.stdin, "isatty", return_value=True),
            patch.object(g, "_pushes_to_main", return_value="sha"),
            patch.object(g, "check", return_value=1) as chk,
        ):
            assert g.main([]) == 1
            chk.assert_called_once_with("sha")


class TestPrAuthorized:
    def test_absent_override_file_is_unauthorized(self, tmp_path):
        with patch.object(g, "_DATA_DIR", tmp_path):
            assert g._pr_authorized(99) is False

    def test_override_line_authorizes(self, tmp_path):
        (tmp_path / "merge_authorized_99").write_text("override: intentional squash merge\n")
        with patch.object(g, "_DATA_DIR", tmp_path):
            assert g._pr_authorized(99) is True

    def test_file_without_override_line_is_unauthorized(self, tmp_path):
        (tmp_path / "merge_authorized_99").write_text("some note but no override marker\n")
        with patch.object(g, "_DATA_DIR", tmp_path):
            assert g._pr_authorized(99) is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
