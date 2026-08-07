"""`/update`'s pull must not resolve its merge target through FETCH_HEAD (#2650).

`.git/FETCH_HEAD` is per-*repository*, not per-worktree. This machine runs
concurrent SDLC lanes across many worktrees of one repo, so a bare
``git pull --ff-only`` races: a peer lane's fetch rewrites FETCH_HEAD between
our fetch and our merge, and the merge resolves the peer's refs instead of
ours. That killed three consecutive ``/update`` attempts on 2026-08-07 with
``fatal: Cannot fast-forward to multiple branches``.

These tests build a real remote + clone on disk (no mocks, no network).

**On the control.** ``git pull`` cannot itself be the control: it is fetch +
merge, and its own fetch overwrites any FETCH_HEAD poison planted beforehand,
so it passes and proves nothing. (Confirmed the hard way — the first version of
this file asserted exactly that and the control came back green.) The race
lands the peer's write *between* those two internal steps, which is not
reachable from outside the process. So the control targets the merge step
directly: ``git merge --ff-only FETCH_HEAD`` is what ``git pull --ff-only``
runs after fetching, and it is where the wrong target is read.

The failure is asserted as a non-zero exit rather than a message match. Git
words this differently across versions ("Cannot fast-forward to multiple
branches" vs "Not possible to fast-forward"), and the mechanism, not the
phrasing, is what these tests are about.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.update.git import get_upstream_ref, pull_ff_only


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


def _sha(cwd: Path, ref: str) -> str:
    return _git(cwd, "rev-parse", ref).stdout.strip()


def _poison(clone: Path) -> None:
    """Write the FETCH_HEAD a peer lane's concurrent fetch leaves behind.

    Two real, mutually divergent heads, each individually fast-forwardable
    from the clone's HEAD — the shape that has no single ff target.
    """
    (clone / ".git" / "FETCH_HEAD").write_text(
        f"{_sha(clone, 'origin/main')}\t\tbranch 'main' of remote\n"
        f"{_sha(clone, 'origin/other')}\t\tbranch 'other' of remote\n"
    )


@pytest.fixture
def clone_with_divergent_remote_branches(tmp_path: Path) -> Path:
    """A clone one commit behind origin/main, with a divergent origin/other.

    Layout — the clone sits at `one`, and `two` and `other` are siblings, so
    neither remote head is an ancestor of the other:

        one ── two      (origin/main)
          └─── other    (origin/other)
    """
    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    clone = tmp_path / "clone"

    _git(tmp_path, "init", "--bare", "-b", "main", str(remote))
    _git(tmp_path, "clone", str(remote), str(seed))
    _git(seed, "config", "user.email", "t@example.com")
    _git(seed, "config", "user.name", "T")
    (seed / "a.txt").write_text("one\n")
    _git(seed, "add", "a.txt")
    _git(seed, "commit", "-m", "one")
    _git(seed, "push", "-u", "origin", "main")

    # Clone now, so it stays pinned at `one`.
    _git(tmp_path, "clone", str(remote), str(clone))
    _git(clone, "config", "user.email", "t@example.com")
    _git(clone, "config", "user.name", "T")

    _git(seed, "checkout", "-b", "other")
    (seed / "b.txt").write_text("other\n")
    _git(seed, "add", "b.txt")
    _git(seed, "commit", "-m", "other")
    _git(seed, "push", "origin", "other")

    _git(seed, "checkout", "main")
    (seed / "a.txt").write_text("two\n")
    _git(seed, "add", "a.txt")
    _git(seed, "commit", "-m", "two")
    _git(seed, "push", "origin", "main")

    _git(clone, "fetch", "origin", "main")
    _git(clone, "fetch", "origin", "other")
    return clone


class TestFetchHeadRace:
    def test_control_merging_fetch_head_fails_under_a_peer_fetch(
        self, clone_with_divergent_remote_branches: Path
    ):
        """The control: resolving the merge target through FETCH_HEAD really
        does break once a peer has written it. Without this, a green treatment
        test would only prove the fixture is harmless."""
        clone = clone_with_divergent_remote_branches
        _poison(clone)

        result = subprocess.run(
            ["git", "merge", "--ff-only", "FETCH_HEAD"],
            cwd=clone,
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode != 0, result.stdout + result.stderr
        assert "fast-forward" in (result.stdout + result.stderr).lower()
        # And the clone did not move.
        assert _sha(clone, "HEAD") != _sha(clone, "origin/main")

    def test_treatment_merging_the_named_ref_succeeds_in_the_same_state(
        self, clone_with_divergent_remote_branches: Path
    ):
        """Same poisoned FETCH_HEAD, named remote-tracking ref: immune."""
        clone = clone_with_divergent_remote_branches
        _poison(clone)

        result = subprocess.run(
            ["git", "merge", "--ff-only", "origin/main"],
            cwd=clone,
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 0, result.stdout + result.stderr
        assert _sha(clone, "HEAD") == _sha(clone, "origin/main")

    def test_pull_ff_only_survives_a_peer_fetch_landing_mid_pull(
        self, clone_with_divergent_remote_branches: Path, monkeypatch
    ):
        """End to end: the peer's write lands right after our fetch, exactly
        where the real race puts it, and `pull_ff_only` still lands correctly."""
        import scripts.update.git as gitmod

        clone = clone_with_divergent_remote_branches
        real_run_cmd = gitmod.run_cmd

        def racing_run_cmd(cmd, cwd=None, check=True):
            result = real_run_cmd(cmd, cwd=cwd, check=check)
            if len(cmd) >= 2 and cmd[0] == "git" and cmd[1] == "fetch":
                _poison(clone)  # the peer lane, in miniature
            return result

        monkeypatch.setattr(gitmod, "run_cmd", racing_run_cmd)

        before = _sha(clone, "HEAD")
        success, output = pull_ff_only(clone)

        assert success is True, output
        assert _sha(clone, "HEAD") != before
        assert _sha(clone, "HEAD") == _sha(clone, "origin/main")

    def test_pull_ff_only_never_shells_out_to_bare_git_pull(
        self, clone_with_divergent_remote_branches: Path, monkeypatch
    ):
        """The load-bearing property, asserted on the command trail.

        A future edit reintroducing `git pull` would still pass the behavioral
        test above — its own fetch clears the poison — so the command shape is
        asserted directly rather than inferred from an outcome.
        """
        import scripts.update.git as gitmod

        real_run_cmd = gitmod.run_cmd
        seen: list[list[str]] = []

        def recording_run_cmd(cmd, cwd=None, check=True):
            seen.append(list(cmd))
            return real_run_cmd(cmd, cwd=cwd, check=check)

        monkeypatch.setattr(gitmod, "run_cmd", recording_run_cmd)
        success, output = pull_ff_only(clone_with_divergent_remote_branches)

        assert success is True, output
        git_cmds = [c for c in seen if c and c[0] == "git"]
        assert not any(c[1] == "pull" for c in git_cmds), git_cmds
        assert ["git", "fetch", "origin", "main"] in git_cmds
        assert ["git", "merge", "--ff-only", "origin/main"] in git_cmds

    def test_already_up_to_date_is_success(self, clone_with_divergent_remote_branches: Path):
        clone = clone_with_divergent_remote_branches
        assert pull_ff_only(clone)[0] is True
        # Nothing left to do; still a success, not a spurious failure.
        assert pull_ff_only(clone)[0] is True

    def test_diverged_branch_rebases_onto_the_named_ref(
        self, clone_with_divergent_remote_branches: Path
    ):
        """Divergence falls back to rebase — onto `origin/main`, never
        onto FETCH_HEAD."""
        clone = clone_with_divergent_remote_branches
        (clone / "local.txt").write_text("local\n")
        _git(clone, "add", "local.txt")
        _git(clone, "commit", "-m", "local work")

        success, output = pull_ff_only(clone)

        assert success is True, output
        log = _git(clone, "log", "--oneline", "-3").stdout
        assert "local work" in log
        assert "two" in log

    def test_no_bare_git_pull_survives_anywhere_in_the_update_path(self):
        """Sweep, not a checklist: `/update` runs from a shell wrapper and two
        Python modules, and the incident came from the shell one. Any of them
        reintroducing `git pull` restores the race, so all are swept.

        `git pull --rebase origin main` is caught too: naming the remote and
        branch does not help, because the rebase still takes its onto-target
        from FETCH_HEAD.
        """
        import re

        repo_root = Path(__file__).resolve().parents[2]
        swept = [
            repo_root / "scripts" / "remote-update.sh",
            repo_root / "scripts" / "update" / "git.py",
            repo_root / "scripts" / "update" / "run.py",
        ]

        # A `git pull` invocation in shell form or argv-list form. Prose
        # mentions ("used after git pull pulls new code") are not invocations,
        # so the shell pattern requires the command to start a statement and
        # the argv pattern requires the quoted-list shape.
        shell_pull = re.compile(r"^\s*(?:if\s+)?git\s+(?:-C\s+\S+\s+)?pull\b", re.MULTILINE)
        argv_pull = re.compile(r"""["']git["']\s*,\s*["']pull["']""")

        offenders = []
        for path in swept:
            assert path.exists(), f"swept file missing -- update the sweep: {path}"
            text = path.read_text()
            for pattern in (shell_pull, argv_pull):
                for match in pattern.finditer(text):
                    line_no = text[: match.start()].count("\n") + 1
                    offenders.append(f"{path.relative_to(repo_root)}:{line_no}")

        assert not offenders, (
            "bare `git pull` resolves its merge/rebase target through the "
            "repo-shared .git/FETCH_HEAD and races concurrent lanes (#2650); "
            "use `git fetch <remote> <branch>` + "
            f"`git merge --ff-only <remote>/<branch>`: {offenders}"
        )

    def test_no_upstream_is_reported_not_crashed(self, tmp_path: Path):
        repo = tmp_path / "solo"
        _git(tmp_path, "init", "-b", "main", str(repo))
        _git(repo, "config", "user.email", "t@example.com")
        _git(repo, "config", "user.name", "T")
        (repo / "f.txt").write_text("x\n")
        _git(repo, "add", "f.txt")
        _git(repo, "commit", "-m", "c")

        assert get_upstream_ref(repo) is None
        success, output = pull_ff_only(repo)
        assert success is False
        assert "upstream" in output.lower()
