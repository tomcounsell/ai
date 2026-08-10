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
    monkeypatch.setattr(disk_reclaim, "open_pr_branches", lambda _root: set())
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
    monkeypatch.setattr(disk_reclaim, "open_pr_branches", lambda _root: {"session/lane"})
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
    monkeypatch.setattr(disk_reclaim, "open_pr_branches", lambda _root: None)
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


def _fake_run_with_origin(origin_url: str, gh_response: subprocess.CompletedProcess):
    """A `subprocess.run` fake that answers both the origin lookup and `gh`.

    `open_pr_branches` now makes two subprocess calls: `git remote get-url
    origin` (to derive the `--repo` slug) and `gh pr list --repo <slug>`.
    Dispatch on argv[0] so both are answered realistically.
    """

    def fake_run(argv, **_kwargs):
        if argv[:2] == ["git", "remote"]:
            return subprocess.CompletedProcess(argv, 0, origin_url + "\n", "")
        return gh_response

    return fake_run


def test_open_pr_branches_returns_none_on_nonzero_exit(monkeypatch, tmp_path):
    monkeypatch.setattr(
        disk_reclaim.subprocess,
        "run",
        _fake_run_with_origin(
            "git@github.com:tomcounsell/ai.git",
            subprocess.CompletedProcess([], 1, "", "gh: auth required"),
        ),
    )
    assert disk_reclaim.open_pr_branches(tmp_path) is None


def test_open_pr_branches_returns_none_when_gh_missing(monkeypatch, tmp_path):
    def boom(argv, **_kwargs):
        if argv[:2] == ["git", "remote"]:
            return subprocess.CompletedProcess(argv, 0, "git@github.com:tomcounsell/ai.git\n", "")
        raise FileNotFoundError("gh")

    monkeypatch.setattr(disk_reclaim.subprocess, "run", boom)
    assert disk_reclaim.open_pr_branches(tmp_path) is None


def test_open_pr_branches_parses_names(monkeypatch, tmp_path):
    payload = '[{"headRefName": "session/a"}, {"headRefName": "session/b"}]'
    monkeypatch.setattr(
        disk_reclaim.subprocess,
        "run",
        _fake_run_with_origin(
            "git@github.com:tomcounsell/ai.git",
            subprocess.CompletedProcess([], 0, payload, ""),
        ),
    )
    assert disk_reclaim.open_pr_branches(tmp_path) == {"session/a", "session/b"}


def test_open_pr_branches_is_pinned_to_repo_root(monkeypatch, tmp_path):
    """The guard is void if `gh` answers about whatever repo `GH_REPO` names.

    `GH_REPO` outranks the working directory in `gh`'s repo-resolution chain,
    so a bare call under a forced cwd can *succeed* while describing a
    different repository: no `session/*` lane matches, every lane reads "no
    open PR", and the fail-closed None branch never fires because nothing
    failed. The remedy is an explicit `--repo <slug>` derived from
    `repo_root`'s own `origin` remote, plus scrubbing `GH_REPO` from the
    child's environment as defense in depth.
    """
    monkeypatch.setenv("GH_REPO", "cli/cli")
    calls: list[tuple] = []

    def capture(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv[:2] == ["git", "remote"]:
            return subprocess.CompletedProcess(argv, 0, "git@github.com:tomcounsell/ai.git\n", "")
        return subprocess.CompletedProcess(argv, 0, "[]", "")

    monkeypatch.setattr(disk_reclaim.subprocess, "run", capture)
    other = tmp_path / "some" / "other" / "checkout"
    other.mkdir(parents=True)

    assert disk_reclaim.open_pr_branches(other) == set()

    gh_argv, gh_kwargs = next(c for c in calls if c[0][0] == "gh")
    assert "--repo" in gh_argv
    assert gh_argv[gh_argv.index("--repo") + 1] == "tomcounsell/ai"
    assert "GH_REPO" not in gh_kwargs.get("env", {})


def test_open_pr_branches_fails_closed_when_origin_cannot_be_derived(monkeypatch, tmp_path):
    """No origin remote (or an unparseable one) must skip `gh` entirely."""

    def fake_run(argv, **_kwargs):
        if argv[:2] == ["git", "remote"]:
            return subprocess.CompletedProcess(argv, 1, "", "fatal: no such remote 'origin'")
        raise AssertionError(f"gh must never be invoked when origin cannot be derived: {argv}")

    monkeypatch.setattr(disk_reclaim.subprocess, "run", fake_run)
    assert disk_reclaim.open_pr_branches(tmp_path) is None


def test_sweep_worktrees_passes_repo_root_to_pr_query(repo, all_clear, monkeypatch):
    """And the caller must actually thread it through."""
    seen: list = []

    def spy(repo_root):
        seen.append(repo_root)
        return set()

    monkeypatch.setattr(disk_reclaim, "open_pr_branches", spy)
    sweep_worktrees(repo, apply=False)
    assert seen == [repo]


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


SESSION_A = "007b53a9-3481-495a-bf84-59aeb5c6bdb1"
SESSION_B = "01a48c67-ec38-4dbe-ae33-46bdcb7d9b63"


def _project(root, name):
    d = root / name
    d.mkdir(parents=True)
    return d


def test_transcripts_keeps_recent_and_reaps_old(tmp_path):
    root = tmp_path / "projects"
    old, new = _project(root, "old-proj"), _project(root, "new-proj")
    for d in (old, new):
        (d / f"{SESSION_A}.jsonl").write_text("{}")
    _age(old)

    sweep = sweep_transcripts(max_age_days=30, apply=True, projects_dir=root)
    assert sweep.removed == [f"old-proj/{SESSION_A}.jsonl"]
    assert _reasons(sweep)["new-proj"] == "too_young:1"
    assert not (old / f"{SESSION_A}.jsonl").exists()
    assert (new / f"{SESSION_A}.jsonl").exists()


def test_transcripts_never_remove_the_project_directory(tmp_path):
    """Blocker: the project dir is the container, not the garbage.

    rmtree'ing it takes memory/, .timelines/ and sessions-index.json with it.
    """
    root = tmp_path / "projects"
    proj = _project(root, "proj")
    (proj / f"{SESSION_A}.jsonl").write_text("{}")
    _age(proj)

    sweep_transcripts(max_age_days=30, apply=True, projects_dir=root)
    assert proj.is_dir(), "the project directory itself must survive"


def test_transcripts_preserve_the_memory_store(tmp_path):
    """The whole reason for file-granular sweeping.

    `memory/` is durable curated state whose own mtimes are older than the
    transcripts beside it, so a whole-directory age check selects precisely the
    projects whose memory is most valuable and deletes it.
    """
    root = tmp_path / "projects"
    proj = _project(root, "quiet-proj")
    memory = proj / "memory"
    memory.mkdir()
    (memory / "MEMORY.md").write_text("# Memory Index")
    (memory / "feedback_something.md").write_text("body")
    (proj / f"{SESSION_A}.jsonl").write_text("{}")
    _age(proj)

    sweep = sweep_transcripts(max_age_days=30, apply=True, projects_dir=root)

    assert sweep.removed == [f"quiet-proj/{SESSION_A}.jsonl"]
    assert (memory / "MEMORY.md").read_text() == "# Memory Index"
    assert (memory / "feedback_something.md").exists()
    assert "preserved:1" in _reasons(sweep)["quiet-proj"]


def test_transcripts_age_each_entry_on_its_own_mtime(tmp_path):
    """A live session must not keep its dead siblings alive, or vice versa."""
    root = tmp_path / "projects"
    proj = _project(root, "busy-proj")
    stale_file = proj / f"{SESSION_A}.jsonl"
    stale_dir = proj / SESSION_A
    stale_dir.mkdir()
    (stale_dir / "scratch.txt").write_text("x")
    stale_file.write_text("{}")
    _age(proj)
    fresh = proj / f"{SESSION_B}.jsonl"
    fresh.write_text("{}")  # written now, after the backdating

    sweep = sweep_transcripts(max_age_days=30, apply=True, projects_dir=root)

    assert sorted(sweep.removed) == [f"busy-proj/{SESSION_A}", f"busy-proj/{SESSION_A}.jsonl"]
    assert not stale_file.exists() and not stale_dir.exists()
    assert fresh.exists(), "a fresh transcript must survive"
    assert "too_young:1" in _reasons(sweep)["busy-proj"]


def test_transcripts_preserve_unrecognized_entries(tmp_path):
    """Fail closed on anything outside the `<uuid>[.jsonl]` shape."""
    root = tmp_path / "projects"
    proj = _project(root, "proj")
    timelines = proj / ".timelines"
    timelines.mkdir()
    (timelines / "t.json").write_text("{}")
    (proj / "sessions-index.json").write_text("{}")
    (proj / "notes.jsonl").write_text("{}")  # .jsonl but not a session id
    (proj / f"{SESSION_A}.jsonl").write_text("{}")
    _age(proj)

    sweep = sweep_transcripts(max_age_days=30, apply=True, projects_dir=root)

    assert sweep.removed == [f"proj/{SESSION_A}.jsonl"]
    assert timelines.is_dir()
    assert (proj / "sessions-index.json").exists()
    assert (proj / "notes.jsonl").exists()
    assert "preserved:3" in _reasons(sweep)["proj"]


def test_transcripts_freed_bytes_sums_removed_entries_only(tmp_path):
    root = tmp_path / "projects"
    proj = _project(root, "proj")
    (proj / f"{SESSION_A}.jsonl").write_text("x" * 500)
    memory = proj / "memory"
    memory.mkdir()
    (memory / "MEMORY.md").write_text("y" * 4000)
    _age(proj)

    sweep = sweep_transcripts(max_age_days=30, apply=True, projects_dir=root)
    assert sweep.freed_bytes == 500


def test_transcripts_dry_run_deletes_nothing(tmp_path):
    root = tmp_path / "projects"
    old = _project(root, "old-proj")
    (old / f"{SESSION_A}.jsonl").write_text("{}")
    _age(old)

    sweep = sweep_transcripts(max_age_days=30, apply=False, projects_dir=root)
    assert sweep.removed == [f"old-proj/{SESSION_A}.jsonl"]
    assert (old / f"{SESSION_A}.jsonl").exists(), "dry-run must not delete a transcript"


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
