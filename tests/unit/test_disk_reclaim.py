"""Tests for tools/disk_reclaim.py — the scheduled disk reclamation sweep.

Every guard here exists because skipping it destroys someone's work. The tests
are written to fail if a guard stops being reachable, not merely if it stops
being present: each orchestration test drives `sweep_worktrees` end to end with
exactly one guard tripped and asserts the lane survived with the right reason.
"""

from __future__ import annotations

import os
import subprocess
import time

import pytest

from tools import disk_reclaim
from tools.disk_reclaim import (
    APPLY_ENV,
    Sweep,
    apply_armed,
    format_report,
    main,
    sweep_transcripts,
    sweep_worktrees,
)

OLD = time.time() - (90 * 86400)


def _age(path, when=OLD):
    """Backdate every file in a tree so the age guard lets it through."""
    for entry in sorted(path.rglob("*"), reverse=True):
        os.utime(entry, (when, when))
    os.utime(path, (when, when))


@pytest.fixture
def repo(tmp_path):
    """A repo root with one stale worktree lane at `.worktrees/lane`."""
    root = tmp_path / "repo"
    lane = root / ".worktrees" / "lane"
    lane.mkdir(parents=True)
    (lane / "file.txt").write_text("content")
    _age(root / ".worktrees")
    return root


@pytest.fixture
def all_clear(monkeypatch):
    """Neutralize every guard so a test can trip exactly one of them.

    Returns the dict of calls made into `cleanup_after_merge`, so a test can
    assert both that removal happened and that `force` was never passed.
    """
    import agent.worktree_manager as wm

    calls: list[tuple] = []

    def fake_cleanup(repo_root, slug, *args, **kwargs):
        calls.append((repo_root, slug, args, kwargs))
        return {"worktree_removed": True, "branch_deleted": True, "errors": []}

    monkeypatch.setattr(wm, "_worktree_has_live_process", lambda _p: None)
    monkeypatch.setattr(wm, "worktree_busy_probe", lambda _r, _s: ("clear", ""))
    monkeypatch.setattr(wm, "merged_via_tree", lambda *_a, **_k: True)
    monkeypatch.setattr(wm, "cleanup_after_merge", fake_cleanup)
    monkeypatch.setattr(disk_reclaim, "open_pr_branches", lambda: set())
    monkeypatch.setattr(disk_reclaim, "_worktree_is_dirty", lambda _p: False)
    return calls


def _reasons(sweep: Sweep) -> dict[str, str]:
    return dict(sweep.skipped)


# --- arming -----------------------------------------------------------------


@pytest.mark.parametrize("value", ["true", "TRUE", "1", "yes"])
def test_apply_armed_true(monkeypatch, value):
    monkeypatch.setenv(APPLY_ENV, value)
    assert apply_armed() is True


@pytest.mark.parametrize("value", ["", "false", "0", "no", "maybe"])
def test_apply_armed_false(monkeypatch, value):
    monkeypatch.setenv(APPLY_ENV, value)
    assert apply_armed() is False


def test_apply_armed_false_when_unset(monkeypatch):
    monkeypatch.delenv(APPLY_ENV, raising=False)
    assert apply_armed() is False


@pytest.fixture
def sandboxed_paths(repo, monkeypatch, tmp_path):
    """Point both filesystem sweeps at throwaway dirs.

    Without this a test running with apply=True would delete the developer's
    real ~/.claude/projects and logs/sessions.
    """
    import agent.session_logs as sl

    monkeypatch.setattr(disk_reclaim, "CLAUDE_PROJECTS_DIR", tmp_path / "no-projects")
    monkeypatch.setattr(sl, "SESSION_LOGS_DIR", tmp_path / "no-sessions")


def test_reclaim_defaults_to_env_not_to_caller(repo, all_clear, sandboxed_paths, monkeypatch):
    """apply=None must consult the env, so a YAML params edit cannot arm it."""
    monkeypatch.delenv(APPLY_ENV, raising=False)
    report = disk_reclaim.reclaim(repo, apply=None)
    assert report["applied"] is False
    assert all_clear == [], "dry-run must not reach cleanup_after_merge"


def test_reclaim_with_apply_none_is_armed_by_the_environment(
    repo, all_clear, sandboxed_paths, monkeypatch
):
    """The other half of the contract, and the half a `bool(apply)` bug hides.

    The reflection always passes apply=None. If that collapsed to False, arming
    the sweep via DISK_RECLAIM_APPLY would silently do nothing on the scheduled
    path while still working from the CLI — a reclaimer that reports forever and
    never reclaims.
    """
    monkeypatch.setenv(APPLY_ENV, "true")
    report = disk_reclaim.reclaim(repo, apply=None)
    assert report["applied"] is True
    assert [slug for _, slug, _, _ in all_clear] == ["lane"]


# --- worktree guards, one at a time -----------------------------------------


def test_removes_lane_when_every_guard_clears(repo, all_clear):
    sweep = sweep_worktrees(repo, apply=True)
    assert sweep.removed == ["lane"]
    assert len(all_clear) == 1
    _, slug, args, kwargs = all_clear[0]
    assert slug == "lane"
    assert "force" not in kwargs and args == (), "cleanup_after_merge must never be forced"


def test_dry_run_never_calls_cleanup(repo, all_clear):
    sweep = sweep_worktrees(repo, apply=False)
    assert sweep.removed == ["lane"]
    assert all_clear == [], "dry-run reported a candidate but must not remove it"


def test_skips_lane_younger_than_threshold(repo, all_clear):
    now = time.time()
    _age(repo / ".worktrees" / "lane", now)
    sweep = sweep_worktrees(repo, min_age_days=14, apply=True)
    assert sweep.removed == []
    assert _reasons(sweep)["lane"] == "too_young"
    assert all_clear == []


def test_skips_lane_with_uncommitted_changes(repo, all_clear, monkeypatch):
    monkeypatch.setattr(disk_reclaim, "_worktree_is_dirty", lambda _p: True)
    sweep = sweep_worktrees(repo, apply=True)
    assert sweep.removed == []
    assert _reasons(sweep)["lane"] == "uncommitted_changes"
    assert all_clear == []


def test_skips_lane_when_git_status_unavailable(repo, all_clear, monkeypatch):
    """Cannot ask whether it is dirty => must not delete it."""
    monkeypatch.setattr(disk_reclaim, "_worktree_is_dirty", lambda _p: None)
    sweep = sweep_worktrees(repo, apply=True)
    assert _reasons(sweep)["lane"] == "git_status_unavailable"
    assert all_clear == []


def test_skips_lane_with_live_os_process(repo, all_clear, monkeypatch):
    import agent.worktree_manager as wm

    monkeypatch.setattr(wm, "_worktree_has_live_process", lambda _p: 4242)
    sweep = sweep_worktrees(repo, apply=True)
    assert _reasons(sweep)["lane"] == "live_process:4242"
    assert all_clear == []


def test_skips_lane_with_live_session(repo, all_clear, monkeypatch):
    import agent.worktree_manager as wm

    monkeypatch.setattr(wm, "worktree_busy_probe", lambda _r, _s: ("busy", "sess-1"))
    sweep = sweep_worktrees(repo, apply=True)
    assert _reasons(sweep)["lane"] == "live_session:sess-1"
    assert all_clear == []


def test_busy_check_error_also_blocks_removal(repo, all_clear, monkeypatch):
    """The load-bearing one: an unanswerable busy check must not read as clear.

    `worktree_busy_check` is fail-open by design and returns None both when a
    lane is genuinely idle and when Redis is unreachable. An unattended reaper
    that used it would delete every lane during a Redis outage.
    """
    import agent.worktree_manager as wm

    monkeypatch.setattr(
        wm, "worktree_busy_probe", lambda _r, _s: ("error", "query_failed:ConnectionError")
    )
    sweep = sweep_worktrees(repo, apply=True)
    assert sweep.removed == []
    assert _reasons(sweep)["lane"] == "busy_check_error:query_failed:ConnectionError"
    assert all_clear == []


def test_skips_lane_with_open_pr(repo, all_clear, monkeypatch):
    monkeypatch.setattr(disk_reclaim, "open_pr_branches", lambda: {"session/lane"})
    sweep = sweep_worktrees(repo, apply=True)
    assert _reasons(sweep)["lane"] == "open_pr"
    assert all_clear == []


def test_skips_lane_whose_branch_has_not_landed(repo, all_clear, monkeypatch):
    import agent.worktree_manager as wm

    monkeypatch.setattr(wm, "merged_via_tree", lambda *_a, **_k: False)
    sweep = sweep_worktrees(repo, apply=True)
    assert _reasons(sweep)["lane"] == "unmerged"
    assert all_clear == []


def test_gh_failure_skips_every_lane(repo, all_clear, monkeypatch):
    """A failed PR query must fail closed, not read as "no open PRs".

    The predecessor script collapsed a failed `gh` call into an empty string,
    which made every worktree a prune candidate on an auth blip.
    """
    monkeypatch.setattr(disk_reclaim, "open_pr_branches", lambda: None)
    sweep = sweep_worktrees(repo, apply=True)
    assert sweep.removed == []
    assert _reasons(sweep)["lane"] == "pr_state_unavailable"
    assert all_clear == []


def test_cleanup_declining_is_reported_not_swallowed(repo, all_clear, monkeypatch):
    import agent.worktree_manager as wm

    monkeypatch.setattr(
        wm,
        "cleanup_after_merge",
        lambda _r, _s: {"worktree_removed": False, "blocked_by_session": "sess-9", "errors": []},
    )
    sweep = sweep_worktrees(repo, apply=True)
    assert sweep.removed == []
    assert _reasons(sweep)["lane"] == "cleanup_declined:sess-9"


def test_missing_worktrees_dir_is_not_an_error(tmp_path, all_clear):
    sweep = sweep_worktrees(tmp_path / "empty", apply=True)
    assert sweep.removed == [] and sweep.skipped == [] and sweep.errors == []


# --- open_pr_branches fails closed ------------------------------------------


def test_open_pr_branches_returns_none_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(
        disk_reclaim.subprocess,
        "run",
        lambda *_a, **_k: subprocess.CompletedProcess([], 1, "", "gh: auth required"),
    )
    assert disk_reclaim.open_pr_branches() is None


def test_open_pr_branches_returns_none_when_gh_missing(monkeypatch):
    def boom(*_a, **_k):
        raise FileNotFoundError("gh")

    monkeypatch.setattr(disk_reclaim.subprocess, "run", boom)
    assert disk_reclaim.open_pr_branches() is None


def test_open_pr_branches_parses_names(monkeypatch):
    payload = '[{"headRefName": "session/a"}, {"headRefName": "session/b"}]'
    monkeypatch.setattr(
        disk_reclaim.subprocess,
        "run",
        lambda *_a, **_k: subprocess.CompletedProcess([], 0, payload, ""),
    )
    assert disk_reclaim.open_pr_branches() == {"session/a", "session/b"}


# --- dirty detection against a real git repo --------------------------------


def test_worktree_is_dirty_against_real_git(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e"}
    env.update({"GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"})
    subprocess.run(["git", "init", "-q"], cwd=wt, check=True, env=env)
    (wt / "a.txt").write_text("x")
    subprocess.run(["git", "add", "."], cwd=wt, check=True, env=env)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=wt, check=True, env=env)

    assert disk_reclaim._worktree_is_dirty(wt) is False
    (wt / "a.txt").write_text("changed")
    assert disk_reclaim._worktree_is_dirty(wt) is True


def test_worktree_is_dirty_returns_none_outside_a_repo(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert disk_reclaim._worktree_is_dirty(plain) is None


# --- transcripts ------------------------------------------------------------


def test_transcripts_keeps_recent_and_reaps_old(tmp_path):
    root = tmp_path / "projects"
    old, new = root / "old-proj", root / "new-proj"
    for d in (old, new):
        d.mkdir(parents=True)
        (d / "t.jsonl").write_text("{}")
    _age(old)

    sweep = sweep_transcripts(max_age_days=30, apply=True, projects_dir=root)
    assert sweep.removed == ["old-proj"]
    assert _reasons(sweep)["new-proj"] == "too_young"
    assert not old.exists()
    assert new.exists()


def test_transcripts_dry_run_deletes_nothing(tmp_path):
    root = tmp_path / "projects"
    old = root / "old-proj"
    old.mkdir(parents=True)
    (old / "t.jsonl").write_text("{}")
    _age(old)

    sweep = sweep_transcripts(max_age_days=30, apply=False, projects_dir=root)
    assert sweep.removed == ["old-proj"]
    assert old.exists(), "dry-run must not delete a transcript"


def test_transcripts_missing_dir_is_not_an_error(tmp_path):
    sweep = sweep_transcripts(projects_dir=tmp_path / "nope")
    assert sweep.removed == [] and sweep.errors == []


# --- session snapshots ------------------------------------------------------


def test_snapshot_sweep_honors_dry_run(tmp_path, monkeypatch):
    import agent.session_logs as sl

    monkeypatch.setattr(sl, "SESSION_LOGS_DIR", tmp_path / "sessions")
    (tmp_path / "sessions").mkdir()
    stale = tmp_path / "sessions" / "sess-old"
    stale.mkdir()
    (stale / "snap.json").write_text("{}")
    _age(stale)

    sweep = disk_reclaim.sweep_session_snapshots(apply=False)
    assert sweep.removed == ["sess-old"]
    assert stale.exists(), "dry-run must not delete a snapshot"

    sweep = disk_reclaim.sweep_session_snapshots(apply=True)
    assert sweep.removed == ["sess-old"]
    assert not stale.exists()


# --- CLI --------------------------------------------------------------------


def test_cli_apply_refused_without_env(monkeypatch, capsys, tmp_path):
    monkeypatch.delenv(APPLY_ENV, raising=False)
    rc = main(["--repo-root", str(tmp_path), "--apply"])
    assert rc == 2
    assert APPLY_ENV in capsys.readouterr().err


def test_report_names_what_it_kept_and_why():
    report = {
        "applied": False,
        "sweeps": [
            {
                "category": "worktrees",
                "removed": ["gone"],
                "skipped": [{"name": "kept", "reason": "uncommitted_changes"}],
                "freed_bytes": 0,
                "errors": [],
            }
        ],
        "freed_bytes": 0,
        "removed_count": 1,
        "skipped_count": 1,
        "errors": [],
    }
    text = format_report(report)
    assert "would remove: gone" in text
    assert "kept" in text and "uncommitted_changes" in text
    assert APPLY_ENV in text
