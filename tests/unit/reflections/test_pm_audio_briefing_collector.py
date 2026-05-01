"""Unit tests for reflections/pm_audio_briefing/collector.py.

Covers:
- The 3 v1 collectors only fire for known categories (unknown logs+skips)
- angles.include filters at the collection layer (only listed categories run)
- angles.exclude filters at the post-collection layer (substring suppression)
- include + exclude combined: both layers run
- subprocess errors are handled gracefully (return [])
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from reflections.pm_audio_briefing import collector

pytestmark = [pytest.mark.unit]


# --- Fixtures ----------------------------------------------------------------


@pytest.fixture
def project_dict():
    return {
        "slug": "test-proj",
        "working_directory": "/tmp",
        "github": {"org": "tomcounsell", "repo": "ai"},
    }


# --- Helpers -----------------------------------------------------------------


def _gh_issue_payload(rows):
    """Render a `gh issue list --json ...` style stdout."""
    return json.dumps(rows)


# --- _collect_merges ---------------------------------------------------------


class TestCollectMerges:
    def test_parses_merge_commits(self, project_dict):
        out = (
            "abc123def456|Merge pull request #42 from foo/bar\n"
            "beef00012345|Merge branch 'fix' (#99)"
        )
        with patch.object(collector, "_run", return_value=(0, out, "")):
            items = collector._collect_merges(project_dict)
        assert len(items) == 2
        assert items[0]["sha"].startswith("abc123")
        assert items[0]["pr_number"] == 42
        assert items[1]["pr_number"] == 99

    def test_handles_empty_log(self, project_dict):
        with patch.object(collector, "_run", return_value=(0, "", "")):
            items = collector._collect_merges(project_dict)
        assert items == []

    def test_handles_git_error_gracefully(self, project_dict):
        with patch.object(collector, "_run", return_value=(128, "", "fatal: not a git repo")):
            items = collector._collect_merges(project_dict)
        assert items == []


# --- _collect_open_bugs / _collect_upvote_queue ------------------------------


class TestCollectIssues:
    def test_collect_open_bugs_uses_repo_slug(self, project_dict):
        payload = _gh_issue_payload(
            [{"number": 7, "title": "thing broken", "url": "https://gh/x/7", "labels": []}]
        )
        with patch.object(collector, "_run", return_value=(0, payload, "")) as m:
            items = collector._collect_open_bugs(project_dict)
        cmd = m.call_args[0][0]
        assert "gh" in cmd
        assert "tomcounsell/ai" in cmd
        assert "--label" in cmd and "bug" in cmd
        assert items[0]["number"] == 7

    def test_collect_returns_empty_when_repo_missing(self, project_dict):
        project_dict["github"] = {}  # no org/repo
        with patch.object(collector, "_run") as m:
            items = collector._collect_open_bugs(project_dict)
        assert items == []
        assert m.call_count == 0  # short-circuited; no subprocess

    def test_collect_handles_gh_failure(self, project_dict):
        with patch.object(collector, "_run", return_value=(1, "", "rate limited")):
            items = collector._collect_upvote_queue(project_dict)
        assert items == []

    def test_collect_handles_invalid_json(self, project_dict):
        with patch.object(collector, "_run", return_value=(0, "not json", "")):
            items = collector._collect_open_bugs(project_dict)
        assert items == []


# --- collect() public API: angles filtering ---------------------------------


class TestCollectAnglesFilter:
    def _patched_collectors(self, **overrides):
        """Build a temporary _COLLECTORS dict with the requested overrides."""
        base = {k: v for k, v in collector._COLLECTORS.items()}
        base.update(overrides)
        return base

    def test_include_only_merges_excludes_other_categories(self, project_dict):
        m_merges = MagicMock(return_value=[{"subject": "x"}])
        m_bugs = MagicMock(return_value=[{"title": "y"}])
        patched = self._patched_collectors(**{"merges": m_merges, "open-bugs": m_bugs})
        with patch.object(collector, "_COLLECTORS", patched):
            out = collector.collect(project_dict, ["merges"])
        assert "merges" in out
        assert "open-bugs" not in out
        assert m_merges.called
        assert not m_bugs.called

    def test_unknown_category_logs_warning_and_skips(self, project_dict, caplog):
        out = collector.collect(project_dict, ["totally-fake-category"])
        assert out == {}
        # Confirm the warning made it to logs
        assert any("Unknown angle category" in r.message for r in caplog.records)

    def test_exclude_only_filters_collected_subjects(self, project_dict):
        signals = [
            {"subject": "Bump foo lockfile-bumps in Cargo.lock", "pr_number": 1},
            {"subject": "Fix the auth flow", "pr_number": 2},
        ]
        patched = self._patched_collectors(merges=MagicMock(return_value=signals))
        with patch.object(collector, "_COLLECTORS", patched):
            out = collector.collect(project_dict, ["merges"], ["lockfile-bumps"])
        kept = out["merges"]
        assert len(kept) == 1
        assert kept[0]["pr_number"] == 2

    def test_include_and_exclude_both_run(self, project_dict):
        merges = [
            {"subject": "Add feature A"},
            {"subject": "Lockfile-bumps update"},
        ]
        bugs = [
            {"title": "Lockfile-bumps thingy"},
            {"title": "Real bug"},
        ]
        patched = self._patched_collectors(
            **{"merges": MagicMock(return_value=merges), "open-bugs": MagicMock(return_value=bugs)}
        )
        with patch.object(collector, "_COLLECTORS", patched):
            out = collector.collect(
                project_dict,
                ["merges", "open-bugs"],
                ["lockfile-bumps"],
            )
        assert len(out["merges"]) == 1
        assert "Add feature A" in out["merges"][0]["subject"]
        assert len(out["open-bugs"]) == 1
        assert "Real bug" in out["open-bugs"][0]["title"]

    def test_v1_only_three_categories_wired(self):
        """Ensure exactly the 3 v1 categories are registered (no more)."""
        assert set(collector._COLLECTORS.keys()) == {
            "merges",
            "open-bugs",
            "upvote-queue",
        }

    def test_is_empty_helper(self):
        assert collector.is_empty({})
        assert collector.is_empty({"merges": []})
        assert not collector.is_empty({"merges": [{"a": 1}]})
