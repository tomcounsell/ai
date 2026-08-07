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

import re
import subprocess
from pathlib import Path

import pytest

from scripts.update.git import get_upstream_ref, pull_ff_only

REPO_ROOT = Path(__file__).resolve().parents[2]

# Roots holding things that actually execute: scripts, and the command/skill
# bodies an agent runs. Deliberately excludes docs/ — prose describing a pull
# is not a pull.
_EXECUTABLE_ROOTS = (
    REPO_ROOT / "scripts",
    REPO_ROOT / ".githooks",
    REPO_ROOT / ".claude" / "commands",
    REPO_ROOT / ".claude" / "hooks",
    REPO_ROOT / ".claude" / "agents",
    REPO_ROOT / ".claude" / "skills",
    REPO_ROOT / ".claude" / "skills-global",
)
_EXECUTABLE_SUFFIXES = (".sh", ".py", ".md")

# `git pull` anywhere in a command chain. The leading alternation is the fix
# for the miss that this file's first version shipped: the old pattern anchored
# on `^`, so `cd ~/src/ai && git checkout main && git pull` was invisible.
#
# Backtick is deliberately NOT a separator here. It means command substitution
# in shell but code-span in markdown, and prose saying "never use `git pull`"
# is everywhere in this codebase — including in the comments explaining this
# very fix. The inline-bash `` !`…` `` wrapper is stripped in
# :func:`_executable_regions` instead, so that form is still reached.
_SHELL_PULL = re.compile(
    r"(?:^|[;&|(]|&&|\|\||\bif\s+|\bthen\s+|\bdo\s+)\s*git\s+(?:-C\s+\S+\s+)?pull\b"
)
# Python argv forms: an explicit ["git", "pull", ...] and the `_run_git`
# wrapper style that supplies the `git` itself — ["pull", "--rebase", ...].
_ARGV_PULL = re.compile(r"""["']git["']\s*,\s*["']pull["']""")
_ARGV_PULL_WRAPPED = re.compile(r"""\[\s*["']pull["']\s*,""")


def _has_shell_shebang(path: Path) -> bool:
    """True for an extensionless file whose first line is a shell shebang.

    `.githooks/` (`commit-msg`, `pre-commit`, `pre-push`) and `scripts/sdlc-tool`
    are executable shell with no suffix, so a suffix-only filter would let a
    future `git pull` hide in exactly the kind of file this sweep is about.
    """
    try:
        with path.open("r", errors="ignore") as fh:
            first = fh.readline()
    except OSError:
        return False
    return first.startswith("#!") and ("sh" in first or "bash" in first or "zsh" in first)


def _executable_files() -> list[Path]:
    files: list[Path] = []
    for root in _EXECUTABLE_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            if path.suffix in _EXECUTABLE_SUFFIXES or (
                not path.suffix and _has_shell_shebang(path)
            ):
                files.append(path)
    return sorted(files)


def _executable_regions(path: Path, text: str) -> list[tuple[int, str]]:
    """Return (line_number, text) for the parts of a file that actually run.

    Markdown is mostly prose, and prose naming a command is not a command, so
    only three regions count: fenced shell blocks, Claude Code's inline-bash
    ``!`…` `` form, and ``allowed-tools:`` permission patterns (which encode a
    literal command and must track it).

    Comments are stripped everywhere they appear, including inside fenced
    blocks — a comment explaining why `git pull` is wrong must not read as a
    `git pull`. Python is returned whole and matched only by the argv
    patterns, which cannot match English.
    """
    if path.suffix == ".py":
        return [(1, text)]

    if path.suffix == ".sh" or not path.suffix:
        return [(i, line.split("#", 1)[0]) for i, line in enumerate(text.splitlines(), 1)]

    regions: list[tuple[int, str]] = []
    in_shell_fence = False
    for i, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("```"):
            lang = stripped[3:].strip().lower()
            in_shell_fence = lang in {"bash", "sh", "shell", "zsh", "console"}
            continue
        if stripped.startswith("!`"):
            # Strip the inline-bash wrapper so the command inside is scanned
            # as a command, without making backtick a shell separator globally.
            regions.append((i, stripped[2:].rstrip("`")))
        elif in_shell_fence or stripped.startswith("allowed-tools:"):
            regions.append((i, line.split("#", 1)[0]))
    return regions


def _bare_pull_hits(path: Path) -> list[tuple[int, str]]:
    text = path.read_text()
    # Python invokes git through argv lists, never a shell string, so the
    # shell pattern would only ever match its prose (docstrings, comments).
    patterns = (
        (_ARGV_PULL, _ARGV_PULL_WRAPPED)
        if path.suffix == ".py"
        else (_SHELL_PULL, _ARGV_PULL, _ARGV_PULL_WRAPPED)
    )
    hits: list[tuple[int, str]] = []
    for start_line, region in _executable_regions(path, text):
        for pattern in patterns:
            for match in pattern.finditer(region):
                line_no = start_line + region[: match.start()].count("\n")
                snippet = region.splitlines()[region[: match.start()].count("\n")].strip()
                hits.append((line_no, snippet[:100]))
    return hits


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

    def test_the_sweep_discovers_the_known_execution_surfaces(self):
        """Guard the guard. The first version of this sweep was a three-entry
        list that called itself a sweep, and it missed `/update`'s own
        slash-command — the most-used entry point of the path it was named
        after. Discovery replaced the list; this asserts discovery works."""
        found = {p.relative_to(REPO_ROOT).as_posix() for p in _executable_files()}
        for required in (
            "scripts/remote-update.sh",
            "scripts/update/git.py",
            "scripts/update/run.py",
            "scripts/migrate_completed_plan.py",
            ".claude/commands/update.md",
            ".claude/skills/update/SKILL.md",
            ".claude/skills/do-deploy/SKILL.md",
            ".claude/skills-global/weekly-review/SKILL.md",
            # Extensionless shell -- a suffix-only filter would drop these,
            # and they are as executable as anything with a .sh on it.
            "scripts/sdlc-tool",
            ".githooks/pre-push",
        ):
            assert required in found, f"sweep stopped discovering {required}"
        assert len(found) > 250, len(found)

    def test_no_bare_git_pull_survives_on_any_executable_surface(self):
        """A real sweep: every script and every agent-executable command body.

        `git pull` resolves its merge (or rebase) target through
        `.git/FETCH_HEAD`, which is per-repository. Any of these surfaces
        reintroducing it restores the race, and they all run against the same
        shared `~/src/ai` checkout during exactly the multi-lane conditions
        #2650 describes.

        Caught regardless of shape: a pull chained after `&&` (which the
        original pattern's line-start anchor missed), an inline-bash
        `` !`…` `` command body, a `Bash(...)` permission pattern, and both
        argv forms — `["git", "pull", …]` and a `_run_git(["pull", …])`
        wrapper that supplies the `git` itself.

        `git pull --rebase origin main` is caught too: naming the remote and
        branch does not help, because the rebase still takes its onto-target
        from FETCH_HEAD.
        """
        offenders = []
        for path in _executable_files():
            for line_no, snippet in _bare_pull_hits(path):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{line_no}  {snippet}")

        assert not offenders, (
            "bare `git pull` resolves its merge/rebase target through the "
            "repo-shared .git/FETCH_HEAD and races concurrent lanes (#2650); "
            "use `git fetch <remote> <branch>` + "
            "`git merge --ff-only <remote>/<branch>`:\n  " + "\n  ".join(offenders)
        )

    def test_no_upstream_is_reported_not_crashed_local(self, tmp_path: Path):
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


class TestStashIsRestoredByIdentity:
    """`refs/stash` is per-repository too (#2650, shape 1).

    `/update` stashes a dirty tree before fast-forwarding and restores it
    after. It used to restore with a bare `git stash pop`, which means
    `stash@{0}` — whatever anyone pushed most recently. Between our push and
    our pop, a concurrent lane's `git stash` becomes `stash@{0}`, so `/update`
    would restore that lane's uncommitted work into this checkout and leave
    ours buried. Same family as the autostash collision in #2650, inside the
    function that PR hardens.
    """

    @pytest.fixture
    def repo(self, tmp_path: Path) -> Path:
        r = tmp_path / "repo"
        _git(tmp_path, "init", "-b", "main", str(r))
        _git(r, "config", "user.email", "t@example.com")
        _git(r, "config", "user.name", "T")
        (r / "tracked.txt").write_text("committed\n")
        _git(r, "add", "tracked.txt")
        _git(r, "commit", "-m", "base")
        return r

    def test_restores_our_stash_not_a_peer_stash_pushed_after_ours(self, repo: Path):
        """The bug, directly. Ours goes on the stack first, a peer's lands on
        top, and the restore must still bring back ours."""
        from scripts.update.git import stash_changes, stash_pop

        (repo / "tracked.txt").write_text("OURS\n")
        result = stash_changes(repo)
        assert result.ok and result.sha, "stash_changes must return our stash commit sha"
        our_sha = result.sha

        # A peer lane in another worktree of the same repo stashes its own work.
        (repo / "tracked.txt").write_text("PEER\n")
        _git(repo, "stash", "push", "-m", "peer lane auto-stash")
        assert "PEER" not in (repo / "tracked.txt").read_text()

        assert stash_pop(repo, our_sha) is True
        assert (repo / "tracked.txt").read_text() == "OURS\n"

        # The peer's stash is untouched and still restorable by them.
        remaining = _git(repo, "stash", "list").stdout
        assert "peer lane auto-stash" in remaining
        assert "remote-update auto-stash" not in remaining  # ours was dropped

    def test_dropped_entry_still_restores_ours_and_never_a_strangers(self, repo: Path):
        """Our entry is off the stack, a peer's is on it.

        Applying by raw sha succeeds here — a dropped stash commit stays
        reachable until gc — so this restores OUR work rather than declining.
        That is the correct outcome and a deliberate contract change from the
        position-based version, which returned False. The property that
        matters is unchanged and asserted directly: a stranger's work never
        enters this tree, and their entry is never dropped.
        """
        from scripts.update.git import stash_changes, stash_pop

        (repo / "tracked.txt").write_text("OURS\n")
        our_sha = stash_changes(repo).sha
        assert our_sha

        # Ours is dropped (e.g. already restored elsewhere); a peer's remains.
        ref = _git(repo, "stash", "list", "--format=%H %gd").stdout.split()[1]
        _git(repo, "stash", "drop", ref)
        (repo / "tracked.txt").write_text("PEER\n")
        _git(repo, "stash", "push", "-m", "peer lane auto-stash")

        stash_pop(repo, our_sha)

        assert (repo / "tracked.txt").read_text() == "OURS\n"  # ours, not PEER
        assert "peer lane auto-stash" in _git(repo, "stash", "list").stdout

    @pytest.mark.parametrize(
        "bad_sha",
        [
            pytest.param("0" * 40, id="null-sha"),
            pytest.param("deadbeef" * 5, id="nonexistent"),
            pytest.param("stash@{0}", id="stack-position"),
            pytest.param("HEAD", id="non-stash-rev"),
            pytest.param("", id="empty"),
            pytest.param(None, id="none"),
        ],
    )
    def test_a_sha_that_is_not_ours_restores_nothing(self, repo: Path, bad_sha):
        """Nothing but a real object id gets through to git.

        The null sha is the dangerous one: `git stash apply 0000...0` is read
        by git as "no argument" and silently applies `stash@{0}`, exiting 0.
        In the shared checkout that restores a peer lane's work — the very
        harm this function exists to prevent, reached through an argument
        rather than a position. Verified against real git: it returned exit 0
        with the peer's content in the tree.
        """
        from scripts.update.git import stash_pop

        (repo / "tracked.txt").write_text("PEER\n")
        _git(repo, "stash", "push", "-m", "peer lane auto-stash")

        assert stash_pop(repo, bad_sha) is False
        assert (repo / "tracked.txt").read_text() == "committed\n"
        assert "peer lane auto-stash" in _git(repo, "stash", "list").stdout

    def test_round_trip_with_no_peers(self, repo: Path):
        from scripts.update.git import stash_changes, stash_pop

        (repo / "tracked.txt").write_text("OURS\n")
        our_sha = stash_changes(repo).sha
        assert our_sha
        assert (repo / "tracked.txt").read_text() == "committed\n"
        assert stash_pop(repo, our_sha) is True
        assert (repo / "tracked.txt").read_text() == "OURS\n"
        assert _git(repo, "stash", "list").stdout.strip() == ""

    def test_no_sha_is_a_refusal_not_a_bare_pop(self, repo: Path):
        """Without an identity there is nothing safe to restore."""
        from scripts.update.git import stash_pop

        (repo / "tracked.txt").write_text("PEER\n")
        _git(repo, "stash", "push", "-m", "peer lane auto-stash")

        assert stash_pop(repo, None) is False
        assert "peer lane auto-stash" in _git(repo, "stash", "list").stdout


class TestStashIdentityIsNeverInferredFromTheStackTop:
    """The identity must be OURS, not whatever sits on the shared stack.

    `refs/stash` is a per-repository stack. Reading its top after our own
    `git stash push` looks like it identifies our entry, and does not:

    1. `git stash push` exits **0** when there is nothing to stash. A tree
       whose only dirt is an untracked file is dirty by `git status
       --porcelain` (what `is_dirty` uses) but declined by the push (no `-u`),
       so the top read back is a PEER's entry. Restoring it writes their
       uncommitted work into this checkout and drops it from the stack — the
       original #2650 shape-1 harm, reached through the code written to
       prevent it.
    2. Even after a real push, a peer pushing before we read leaves their sha
       on top.

    So the entry is found by a token minted before the push. These tests drive
    the real functions against real git repos.
    """

    @pytest.fixture
    def repo(self, tmp_path: Path) -> Path:
        r = tmp_path / "repo"
        _git(tmp_path, "init", "-b", "main", str(r))
        _git(r, "config", "user.email", "t@example.com")
        _git(r, "config", "user.name", "T")
        (r / "tracked.txt").write_text("committed\n")
        _git(r, "add", "tracked.txt")
        _git(r, "commit", "-m", "base")
        return r

    def test_untracked_only_tree_never_adopts_a_peers_stash(self, repo: Path):
        """The blocker, directly. Our tree is dirty only by an untracked file
        and a peer's stash is on the stack: we must report nothing-to-stash,
        leave the peer's entry alone, and leave their work out of our tree."""
        from scripts.update.git import is_dirty, stash_changes

        (repo / "tracked.txt").write_text("PEERWORK\n")
        _git(repo, "stash", "push", "-m", "peer lane auto-stash")
        peer_sha = _git(repo, "rev-parse", "refs/stash").stdout.strip()

        (repo / "untracked.txt").write_text("ours, untracked\n")
        assert is_dirty(repo) is True  # porcelain counts the untracked file

        result = stash_changes(repo)

        assert result.ok is True, "nothing-to-stash is not a failure"
        assert result.sha is None, f"must not adopt the peer's sha ({peer_sha})"
        assert result.sha != peer_sha
        # The peer's stash is untouched and their work stayed out of our tree.
        assert "peer lane auto-stash" in _git(repo, "stash", "list").stdout
        assert (repo / "tracked.txt").read_text() == "committed\n"

    def test_a_peer_pushing_right_after_us_does_not_steal_our_identity(self, repo: Path):
        """Our push lands, then a peer's push tops the stack before anyone
        reads it. Token search still returns ours."""
        from scripts.update.git import stash_changes, stash_pop

        (repo / "tracked.txt").write_text("OURS\n")
        result = stash_changes(repo)
        assert result.ok and result.sha

        (repo / "tracked.txt").write_text("PEER\n")
        _git(repo, "stash", "push", "-m", "peer lane auto-stash")
        assert _git(repo, "rev-parse", "refs/stash").stdout.strip() != result.sha

        assert stash_pop(repo, result.sha) is True
        assert (repo / "tracked.txt").read_text() == "OURS\n"
        assert "peer lane auto-stash" in _git(repo, "stash", "list").stdout

    def test_git_pull_treats_nothing_to_stash_as_success_not_failure(
        self, clone_with_divergent_remote_branches: Path
    ):
        """`is_dirty` counts untracked files but the push declines them. If
        that read as a stash failure, /update would break every time a stray
        untracked file sat in the shared checkout."""
        from scripts.update.git import git_pull

        clone = clone_with_divergent_remote_branches
        (clone / "scratch-note.txt").write_text("someone's scratch file\n")

        result = git_pull(clone)

        assert result.success is True, result.error
        assert result.stashed is False  # nothing was stashed, so nothing to restore
        assert _sha(clone, "HEAD") == _sha(clone, "origin/main")
        assert (clone / "scratch-note.txt").exists()  # left alone

    def test_token_is_unique_per_call(self, repo: Path):
        """The token IS the identity, so two stashes in the same second must
        not collide."""
        from scripts.update.git import stash_changes

        (repo / "tracked.txt").write_text("first\n")
        first = stash_changes(repo)
        (repo / "tracked.txt").write_text("second\n")
        second = stash_changes(repo)

        assert first.sha and second.sha and first.sha != second.sha
        subjects = _git(repo, "stash", "list", "--format=%gs").stdout
        assert subjects.count("remote-update auto-stash") == 2


class TestStashListFailureFailsClosed:
    """A failed `git stash list` must not read as "nothing was stashed".

    `_stash_entries` returning `[]` for both an empty stack and a failed read
    would run downhill: no token match, `StashResult(ok=True, sha=None)`,
    `git_pull` sets `stashed=False`, skips the restore, and reports success.
    If the push *did* create an entry, the user's work is stranded in the stack
    under a token nobody will look up, and the pull proceeds over the tree it
    came from.

    This module exists because that class of fail-open guess loses other lanes'
    work, so the read failure fails closed and `git_pull` aborts through its
    existing error path.
    """

    @pytest.fixture
    def repo(self, tmp_path: Path) -> Path:
        r = tmp_path / "repo"
        _git(tmp_path, "init", "-b", "main", str(r))
        _git(r, "config", "user.email", "t@example.com")
        _git(r, "config", "user.name", "T")
        (r / "tracked.txt").write_text("committed\n")
        _git(r, "add", "tracked.txt")
        _git(r, "commit", "-m", "base")
        return r

    @staticmethod
    def _break_stash_list(monkeypatch):
        """Fail only `git stash list`; every other git call runs for real."""
        import scripts.update.git as gitmod

        real = gitmod.run_cmd

        def failing(cmd, cwd=None, check=True):
            if len(cmd) >= 3 and cmd[:3] == ["git", "stash", "list"]:
                return subprocess.CompletedProcess(cmd, 128, "", "fatal: cannot read stash\n")
            return real(cmd, cwd=cwd, check=check)

        monkeypatch.setattr(gitmod, "run_cmd", failing)

    def test_empty_stack_and_failed_read_are_distinguishable(self, repo: Path, monkeypatch):
        """The distinction the fix rests on, asserted directly."""
        from scripts.update.git import _stash_entries

        assert _stash_entries(repo) == []  # empty stack: a real answer
        self._break_stash_list(monkeypatch)
        assert _stash_entries(repo) is None  # failed read: not an answer

    def test_stash_changes_reports_failure_not_nothing_to_stash(self, repo: Path, monkeypatch):
        from scripts.update.git import stash_changes

        (repo / "tracked.txt").write_text("OURS\n")
        self._break_stash_list(monkeypatch)

        result = stash_changes(repo)

        assert result.ok is False, "a failed listing must not read as ok"
        assert result.sha is None

    def test_git_pull_aborts_rather_than_pulling_over_stranded_work(
        self, clone_with_divergent_remote_branches: Path, monkeypatch
    ):
        """The consequence that matters: the pull must not proceed."""
        from scripts.update.git import git_pull

        clone = clone_with_divergent_remote_branches
        (clone / "a.txt").write_text("uncommitted local work\n")
        before = _sha(clone, "HEAD")
        self._break_stash_list(monkeypatch)

        result = git_pull(clone)

        assert result.success is False
        assert result.error and "stash" in result.error.lower()
        assert _sha(clone, "HEAD") == before  # did not move
