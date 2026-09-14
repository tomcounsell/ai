"""Rollback drill (#3218, lane 6).

Every drill here runs against a real temporary git repository the fixture
builds (``git init``, commits, branches), through the real
``SubprocessRunner``. The retention root is a second temporary directory, so
the worktree the drill creates never lands inside a checkout. The release is
a stand-in object carrying the fields the drill reads and writes; the Redis
round trip is ``tests/integration/test_improvement_release_drill.py``.

No Redis access: the stand-in's ``save()`` records the call.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tools.improvement_release import drill
from tools.improvement_release.drill import (
    DrillRefused,
    assert_restored,
    drill_root,
    refuse_checkout_path,
    sweep,
)
from tools.improvement_release.runner import (
    TAIL_BYTES,
    TIMEOUT_RETURNCODE,
    CommandResult,
    RecordingRunner,
    SubprocessRunner,
    run_step,
    tail,
)

GIT_ENV = {
    "GIT_AUTHOR_NAME": "drill-test",
    "GIT_AUTHOR_EMAIL": "drill@test.invalid",
    "GIT_COMMITTER_NAME": "drill-test",
    "GIT_COMMITTER_EMAIL": "drill@test.invalid",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
}


def git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, **GIT_ENV},
        timeout=30,
    )
    return completed.stdout.strip()


def commit(repo: Path, message: str, **files: str) -> str:
    for relative, content in files.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        git(repo, "add", "--", relative)
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


class FakeRelease:
    """The fields the drill reads and writes, with a recording ``save``."""

    def __init__(self, **fields):
        self.id = fields.pop("id", "rel-test")
        self.state = fields.pop("state", "proposed")
        self.surfaces = fields.pop("surfaces", ["tools/thing.py"])
        self.rollback_plan = fields.pop("rollback_plan", {"verify": []})
        self.candidate_ref = fields.pop("candidate_ref", "cand")
        self.base_revision = fields.pop("base_revision", None)
        self.rollback_drill = None
        self.drill_log = None
        self.saves = 0
        for key, value in fields.items():
            setattr(self, key, value)

    def save(self):
        self.saves += 1
        return True


@pytest.fixture
def repo(tmp_path):
    """A linear repo: base on ``main``, one candidate commit on ``cand``."""
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    base = commit(
        path,
        "base",
        **{"README.md": "readme\n", "tools/thing.py": "v0\n", "docs/other.md": "other\n"},
    )
    git(path, "switch", "-q", "-c", "cand")
    commit(path, "candidate", **{"tools/thing.py": "v1\n"})
    git(path, "switch", "-q", "main")
    return {"path": path, "base": base}


@pytest.fixture
def root(tmp_path):
    return tmp_path / "retention"


def release_for(repo, **overrides) -> FakeRelease:
    fields = {"base_revision": repo["base"], "candidate_ref": "cand"}
    fields.update(overrides)
    return FakeRelease(**fields)


def drill_dirs(root: Path) -> list[Path]:
    drills = root / "drills"
    if not drills.exists():
        return []
    return [p for p in drills.rglob("*") if p.is_dir() and len(p.relative_to(drills).parts) == 2]


def assert_no_worktree_left(repo, root):
    assert drill_dirs(root) == []
    listing = git(repo["path"], "worktree", "list", "--porcelain")
    assert "drills" not in listing


def step_names(record: dict) -> list[str]:
    return [step["name"] for step in record["steps"]]


class TestDrillPass:
    def test_drill_passes_on_linear_candidate(self, repo, root):
        release = release_for(repo, rollback_plan={"verify": ["cat tools/thing.py"]})

        record = drill.run(release, root=root, repo=repo["path"])

        assert record["result"] == "pass", record
        assert record["restored"] is True
        assert record["reason"] is None
        assert record["base_revision"] == repo["base"]
        assert record["candidate_ref"] == git(repo["path"], "rev-parse", "cand")
        assert "revert" in step_names(record)
        assert record["exercised"] == ["range_checks", "revert", "tree_restoration", "verify"]
        assert record["not_exercised"] == [
            "fleet_update",
            "production_traffic",
            "merge_commit_revert",
        ]
        verify_steps = [s for s in record["steps"] if s["name"].startswith("verify")]
        assert verify_steps[0]["returncode"] == 0
        assert verify_steps[0]["stdout_tail"] == "v0\n", "verify ran on the reverted tree"
        assert json.loads(release.rollback_drill) == record
        assert drill.drill_record(release) == record
        assert release.saves == 1
        assert release.state == "proposed"
        assert "$ git revert --no-commit" in release.drill_log
        assert_no_worktree_left(repo, root)

    def test_empty_verify_is_recorded_as_not_exercised(self, repo, root):
        release = release_for(repo, rollback_plan={"verify": []})

        record = drill.run(release, root=root, repo=repo["path"])

        assert record["result"] == "pass"
        assert "verify" not in record["exercised"]
        assert record["not_exercised"][-1] == "verify"

    def test_drill_records_diff_stat_and_timing(self, repo, root):
        release = release_for(repo)
        started = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)

        record = drill.run(release, root=root, repo=repo["path"], now=started)

        assert record["drilled_at"] == started.isoformat()
        assert "tools/thing.py" in record["diff_stat"]
        assert record["seconds"] >= 0
        assert record["worktree"].startswith(str(root / "drills" / release.id))


class TestPreRevertChecks:
    def test_drill_fails_when_undeclared_surface_differs(self, repo, root):
        git(repo["path"], "switch", "-q", "cand")
        commit(repo["path"], "also touches docs", **{"docs/other.md": "changed\n"})
        git(repo["path"], "switch", "-q", "main")
        release = release_for(repo, surfaces=["tools/thing.py"])

        record = drill.run(release, root=root, repo=repo["path"])

        assert record["result"] == "fail"
        assert record["reason"] == "UNDECLARED_SURFACE_CHANGED"
        assert record["paths"] == ["docs/other.md"]
        assert "revert" not in step_names(record), "no revert may run after a pre-check failure"
        assert record["restored"] is None
        assert record["exercised"] == ["range_checks"]
        assert_no_worktree_left(repo, root)

    def test_directory_surface_covers_files_beneath_it(self, repo, root):
        release = release_for(repo, surfaces=["tools/"])

        record = drill.run(release, root=root, repo=repo["path"])

        assert record["result"] == "pass", record

    def test_undeclared_check_reads_the_whole_listing_past_tail_bytes(self, repo, root):
        """Git sorts ``diff --name-only``; the one undeclared path sorts first.

        The declared surface changes enough files that the listing exceeds
        ``TAIL_BYTES``, so a check that reads the step's 2 KB tail never sees
        ``.githooks/x`` and passes the drill.
        """
        git(repo["path"], "switch", "-q", "cand")
        files = {
            f"tools/generated/module_{i:03d}_with_a_long_name.py": f"# {i}\n" for i in range(80)
        }
        files[".githooks/x"] = "#!/bin/sh\n"
        commit(repo["path"], "wide candidate", **files)
        listing = git(repo["path"], "diff", "--name-only", repo["base"], "cand")
        git(repo["path"], "switch", "-q", "main")
        assert len(listing.encode("utf-8")) > TAIL_BYTES, "the fixture must exceed the tail"
        assert listing.splitlines()[0] == ".githooks/x", "the undeclared path sorts first"
        release = release_for(repo, surfaces=["tools/"])

        record = drill.run(release, root=root, repo=repo["path"])

        assert record["result"] == "fail"
        assert record["reason"] == "UNDECLARED_SURFACE_CHANGED"
        assert record["paths"] == [".githooks/x"]
        assert "revert" not in step_names(record)
        changed = next(s for s in record["steps"] if s["name"] == "changed_paths")
        assert len(changed["stdout_tail"].encode("utf-8")) <= TAIL_BYTES, "the record keeps a tail"
        assert_no_worktree_left(repo, root)

    def test_drill_fails_when_a_changed_path_is_denied(self, repo, root):
        """A ``docs`` surface encloses the charter; the changed path itself is refused."""
        git(repo["path"], "switch", "-q", "cand")
        commit(repo["path"], "charter edit", **{"docs/improvement-charter.md": "mine now\n"})
        git(repo["path"], "switch", "-q", "main")
        release = release_for(repo, surfaces=["tools/thing.py", "docs"])

        record = drill.run(release, root=root, repo=repo["path"])

        assert record["result"] == "fail"
        assert record["reason"] == "DENIED_SURFACE_CHANGED"
        assert record["paths"] == ["docs/improvement-charter.md"]
        assert "revert" not in step_names(record)
        assert record["exercised"] == ["range_checks"]
        assert_no_worktree_left(repo, root)

    def test_drill_fails_when_base_not_ancestor(self, repo, root):
        advanced = commit(repo["path"], "main moved on", **{"README.md": "readme 2\n"})
        release = release_for(repo, base_revision=advanced)

        record = drill.run(release, root=root, repo=repo["path"])

        assert record["result"] == "fail"
        assert record["reason"] == "BASE_NOT_ANCESTOR"
        assert "revert" not in step_names(record)
        assert_no_worktree_left(repo, root)

    def test_drill_fails_when_merge_commits_in_range(self, repo, root):
        git(repo["path"], "switch", "-q", "-c", "side", repo["base"])
        commit(repo["path"], "side work", **{"tools/side.py": "side\n"})
        git(repo["path"], "switch", "-q", "cand")
        git(repo["path"], "merge", "-q", "--no-ff", "-m", "merge side", "side")
        merge_sha = git(repo["path"], "rev-parse", "HEAD")
        git(repo["path"], "switch", "-q", "main")
        release = release_for(repo, surfaces=["tools/"])

        record = drill.run(release, root=root, repo=repo["path"])

        assert record["result"] == "fail"
        assert record["reason"] == "MERGE_COMMITS_IN_RANGE"
        assert record["merges"] == [merge_sha]
        assert "revert" not in step_names(record)
        assert_no_worktree_left(repo, root)

    def test_pre_checks_run_in_plan_order(self, repo, root):
        """Ancestry is checked first: a rebased candidate is refused before its diff is read."""
        advanced = commit(repo["path"], "main moved on", **{"docs/other.md": "moved\n"})
        release = release_for(repo, base_revision=advanced)

        record = drill.run(release, root=root, repo=repo["path"])

        assert record["reason"] == "BASE_NOT_ANCESTOR"
        assert "changed_paths" not in step_names(record)


class TestRevertAndRestoration:
    def test_drill_fails_on_revert_conflict(self, repo, root):
        """A conflicted revert is recorded with its unmerged paths and never verified.

        A linear range reverted from its own tip cannot conflict: each revert's
        3-way merge sees ``ours == base``. The conflict is therefore seeded from
        a base-side commit that rewrites the candidate's lines, and the runner
        substitutes that commit for the range so the revert genuinely
        conflicts inside the drill worktree. Everything else is real git.
        """
        git(repo["path"], "switch", "-q", "-c", "base-side", repo["base"])
        conflicting = commit(repo["path"], "base-side rewrite", **{"tools/thing.py": "v-base\n"})
        git(repo["path"], "switch", "-q", "main")
        real = SubprocessRunner()

        def runner(argv, *, cwd=None, timeout=None):
            if argv[:3] == ["git", "revert", "--no-commit"]:
                argv = ["git", "revert", "--no-commit", conflicting]
            return real(argv, cwd=cwd, timeout=timeout)

        release = release_for(repo, rollback_plan={"verify": ["true"]})

        record = drill.run(release, runner=runner, root=root, repo=repo["path"])

        assert record["result"] == "fail"
        assert record["reason"] == "revert_conflict"
        assert record["paths"] == ["tools/thing.py"]
        assert record["restored"] is None
        assert not any(name.startswith("verify") for name in step_names(record))
        assert "revert" in record["exercised"]
        assert "tree_restoration" not in record["exercised"]
        assert_no_worktree_left(repo, root)

    def test_drill_fails_when_revert_leaves_residue(self, repo, root):
        """A verify command that rewrites an undeclared tracked file is caught.

        The whole-tree diff runs both before and after the verify commands;
        the post-verify pass is the final invariant, so a step that mutates
        the tree on a path outside the declared surfaces fails the drill.
        """
        release = release_for(
            repo, rollback_plan={"verify": ["sh -c 'printf residue > README.md'"]}
        )

        record = drill.run(release, root=root, repo=repo["path"])

        assert record["result"] == "fail"
        assert record["reason"] == "residue"
        assert record["paths"] == ["README.md"]
        assert record["restored"] is False
        assert_no_worktree_left(repo, root)

    def test_assert_restored_lists_differing_paths(self, repo, root):
        worktree = root / "wt"
        git(repo["path"], "worktree", "add", "-q", "--detach", str(worktree), "cand")
        try:
            transcript: list[str] = []
            result = assert_restored(
                SubprocessRunner(),
                worktree=str(worktree),
                base_revision=repo["base"],
                surfaces=["tools/thing.py"],
                transcript=transcript,
            )
            assert result == {"restored": False, "differing": ["tools/thing.py"]}
            assert any("git diff --quiet" in line for line in transcript)
        finally:
            git(repo["path"], "worktree", "remove", "--force", str(worktree))


class TestVerify:
    def test_drill_fails_on_verify_timeout(self, repo, root, monkeypatch):
        from config.settings import settings

        monkeypatch.setattr(settings.timeouts, "improvement_drill_verify_seconds", 1.0)
        release = release_for(repo, rollback_plan={"verify": ["sleep 5"]})

        record = drill.run(release, root=root, repo=repo["path"])

        assert record["result"] == "fail"
        assert record["reason"] == "verify_timeout"
        assert record["timeout"] == 1.0
        verify = [s for s in record["steps"] if s["name"].startswith("verify")][0]
        assert verify["timed_out"] is True
        assert verify["returncode"] == TIMEOUT_RETURNCODE
        assert verify["seconds"] < 4
        assert_no_worktree_left(repo, root)

    def test_verify_commands_run_in_the_worktree_and_stop_on_failure(self, repo, root):
        release = release_for(repo, rollback_plan={"verify": ["pwd", "false", "echo never"]})

        record = drill.run(release, root=root, repo=repo["path"])

        assert record["result"] == "fail"
        assert record["reason"] == "verify_failed"
        verify = [s for s in record["steps"] if s["name"].startswith("verify")]
        assert [s["returncode"] for s in verify] == [0, 1]
        assert verify[0]["stdout_tail"].strip().endswith(Path(record["worktree"]).name)


class TestSafety:
    @pytest.mark.parametrize(
        "scenario",
        ["pass", "undeclared", "not_ancestor", "conflict_free_failed_verify", "timeout"],
    )
    def test_drill_removes_worktree_on_every_path(self, repo, root, monkeypatch, scenario):
        from config.settings import settings

        overrides: dict = {}
        if scenario == "undeclared":
            git(repo["path"], "switch", "-q", "cand")
            commit(repo["path"], "extra", **{"docs/other.md": "x\n"})
            git(repo["path"], "switch", "-q", "main")
        elif scenario == "not_ancestor":
            overrides["base_revision"] = commit(repo["path"], "moved", **{"README.md": "m\n"})
        elif scenario == "conflict_free_failed_verify":
            overrides["rollback_plan"] = {"verify": ["false"]}
        elif scenario == "timeout":
            monkeypatch.setattr(settings.timeouts, "improvement_drill_verify_seconds", 1.0)
            overrides["rollback_plan"] = {"verify": ["sleep 3"]}
        release = release_for(repo, **overrides)

        record = drill.run(release, root=root, repo=repo["path"])

        assert record["result"] == ("pass" if scenario == "pass" else "fail")
        assert not Path(record["worktree"]).exists()
        assert_no_worktree_left(repo, root)

    def test_drill_removes_worktree_when_a_step_raises(self, repo, root):
        real = SubprocessRunner()

        def runner(argv, *, cwd=None, timeout=None):
            if argv[:2] == ["git", "revert"]:
                raise RuntimeError("boom")
            return real(argv, cwd=cwd, timeout=timeout)

        release = release_for(repo)

        with pytest.raises(RuntimeError, match="boom"):
            drill.run(release, runner=runner, root=root, repo=repo["path"])

        assert_no_worktree_left(repo, root)

    def test_drill_refuses_checkout_path(self, repo, root):
        release = release_for(repo)

        with pytest.raises(DrillRefused) as excinfo:
            drill.run(release, root=repo["path"], repo=repo["path"])
        assert excinfo.value.code == "CHECKOUT_PATH"
        assert not (repo["path"] / "drills").exists()
        assert release.rollback_drill is None

        with pytest.raises(DrillRefused) as excinfo:
            refuse_checkout_path(repo["path"], root=root)
        assert excinfo.value.code == "CHECKOUT_PATH"

        with pytest.raises(DrillRefused):
            refuse_checkout_path(root / "elsewhere" / "x", root=root)

        refuse_checkout_path(drill_root("rel-1", root=root), root=root)

    def test_drill_refuses_release_not_proposed(self, repo, root):
        release = release_for(repo, state="approved")

        with pytest.raises(DrillRefused) as excinfo:
            drill.run(release, root=root, repo=repo["path"])
        assert excinfo.value.code == "NOT_PROPOSED"
        assert release.rollback_drill is None
        assert_no_worktree_left(repo, root)

    @pytest.mark.parametrize("field", ["candidate_ref", "base_revision"])
    def test_drill_refuses_option_shaped_refs(self, repo, root, field):
        release = release_for(repo, **{field: "--output=/tmp/x"})

        with pytest.raises(DrillRefused) as excinfo:
            drill.run(release, root=root, repo=repo["path"])
        assert excinfo.value.code == "BAD_REF"
        assert_no_worktree_left(repo, root)

    @pytest.mark.parametrize(
        ("field", "value", "code"),
        [
            ("surfaces", ["../escape"], "BAD_SURFACE"),
            ("surfaces", "not json", "BAD_SURFACE"),
            ("surfaces", "[]", "BAD_SURFACE"),
            ("rollback_plan", "[1, 2]", "BAD_PLAN"),
            ("rollback_plan", "{not json", "BAD_PLAN"),
        ],
    )
    def test_drill_refuses_malformed_row_fields(self, repo, root, field, value, code):
        release = release_for(repo, **{field: value})

        with pytest.raises(DrillRefused) as excinfo:
            drill.run(release, root=root, repo=repo["path"])
        assert excinfo.value.code == code
        assert release.rollback_drill is None
        assert_no_worktree_left(repo, root)

    def test_drill_reads_json_string_row_fields(self, repo, root):
        """Popoto stores list and dict fields as JSON strings; the drill reads that shape."""
        release = release_for(
            repo,
            surfaces=json.dumps(["tools/thing.py"]),
            rollback_plan=json.dumps({"verify": ["cat tools/thing.py"]}),
        )

        record = drill.run(release, root=root, repo=repo["path"])

        assert record["result"] == "pass", record
        assert record["exercised"][-1] == "verify"

    def test_drill_root_is_timestamped_under_retention_root(self, root):
        now = datetime(2026, 9, 14, 1, 2, 3, tzinfo=UTC)

        path = drill_root("rel-9", root=root, now=now)

        assert path == root / "drills" / "rel-9" / "20260914T010203Z"
        assert drill_root("rel-9", root=root, now=now + timedelta(seconds=1)) != path


class TestSweep:
    def test_sweep_removes_stale_worktrees(self, repo, root):
        now = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
        stale = drill_root("rel-old", root=root, now=now - timedelta(days=2))
        fresh = drill_root("rel-new", root=root, now=now - timedelta(hours=1))
        for path in (stale, fresh):
            path.parent.mkdir(parents=True, exist_ok=True)
            git(repo["path"], "worktree", "add", "-q", "--detach", str(path), "cand")
        orphan = root / "drills" / "rel-orphan" / "20200101T000000Z"
        orphan.mkdir(parents=True)
        (orphan / "leftover").write_text("x")

        removed = sweep(root=root, repo=repo["path"], now=now)

        assert sorted(removed) == sorted([str(stale), str(orphan)])
        assert not stale.exists()
        assert not orphan.exists()
        assert fresh.exists()
        listing = git(repo["path"], "worktree", "list", "--porcelain")
        assert str(stale) not in listing
        assert str(fresh) in listing
        git(repo["path"], "worktree", "remove", "--force", str(fresh))

    def test_sweep_on_missing_root_is_empty(self, root):
        assert sweep(root=root, repo=None) == []

    def test_sweep_continues_past_a_refused_slot(self, repo, root, monkeypatch, caplog):
        """A slot the checkout guard refuses is logged and left; later slots are still swept."""
        now = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
        first = root / "drills" / "rel-a" / "20200101T000000Z"
        second = root / "drills" / "rel-b" / "20200101T000000Z"
        for path in (first, second):
            path.mkdir(parents=True)
            (path / "leftover").write_text("x")
        real = drill.refuse_checkout_path

        def refusing(path, *, root=None):
            if Path(path).resolve() == first.resolve():
                raise DrillRefused("CHECKOUT_PATH", "seeded refusal")
            return real(path, root=root)

        monkeypatch.setattr(drill, "refuse_checkout_path", refusing)

        with caplog.at_level("WARNING", logger="tools.improvement_release.drill"):
            removed = sweep(root=root, repo=None, now=now)

        assert removed == [str(second)]
        assert first.exists() and not second.exists()
        assert any("seeded refusal" in r.getMessage() for r in caplog.records)


class TestWorktreeVenv:
    def test_add_detached_worktree_links_the_repo_venv(self, repo, root):
        venv = repo["path"] / ".venv"
        (venv / "bin").mkdir(parents=True)
        (venv / "bin" / "python").write_text("#!/bin/sh\n")
        slot = drill_root("rel-venv", root=root)
        transcript: list[str] = []

        add = drill.add_detached_worktree(
            SubprocessRunner(), repo=repo["path"], ref="cand", path=slot, transcript=transcript
        )
        try:
            assert add["returncode"] == 0
            link = slot / ".venv"
            assert link.is_symlink()
            assert link.resolve() == venv.resolve()
            assert (link / "bin" / "python").exists()
            assert any("[linked" in line for line in transcript)
        finally:
            drill.remove_worktree(
                SubprocessRunner(), repo=repo["path"], path=slot, transcript=transcript, root=root
            )
        assert not slot.exists()
        assert venv.exists(), "removing the worktree never follows the link into the venv"
        assert (venv / "bin" / "python").exists()

    def test_add_detached_worktree_skips_the_link_without_a_venv(self, repo, root):
        slot = drill_root("rel-novenv", root=root)
        transcript: list[str] = []

        add = drill.add_detached_worktree(
            SubprocessRunner(), repo=repo["path"], ref="cand", path=slot, transcript=transcript
        )
        try:
            assert add["returncode"] == 0
            assert not (slot / ".venv").exists()
            assert not (slot / ".venv").is_symlink()
            assert not any("[linked" in line for line in transcript)
        finally:
            drill.remove_worktree(
                SubprocessRunner(), repo=repo["path"], path=slot, transcript=transcript, root=root
            )

    def test_drill_passes_with_a_linked_venv(self, repo, root):
        """The verify command runs the linked interpreter; the link leaves the tree restored."""
        venv = repo["path"] / ".venv"
        (venv / "bin").mkdir(parents=True)
        (venv / "bin" / "python").write_text("#!/bin/sh\necho linked\n")
        (venv / "bin" / "python").chmod(0o755)
        release = release_for(repo, rollback_plan={"verify": [".venv/bin/python"]})

        record = drill.run(release, root=root, repo=repo["path"])

        assert record["result"] == "pass", record
        verify = next(s for s in record["steps"] if s["name"] == "verify[0]")
        assert verify["stdout_tail"].strip() == "linked"
        assert_no_worktree_left(repo, root)


class TestRunnerSeam:
    def test_run_step_record_shape(self, tmp_path):
        runner = RecordingRunner(
            [
                (["git", "push"], (1, "", "denied")),
                (lambda argv: argv[-1] == "big", CommandResult([], 0, "x" * 5000, "", 2.5)),
            ]
        )
        transcript: list[str] = []

        pushed = run_step(
            runner, ["git", "push", "origin"], cwd=str(tmp_path), transcript=transcript
        )
        big = run_step(runner, ["cat", "big"], cwd=None, transcript=transcript, name="verify[0]")
        other = run_step(runner, ["git", "fetch"], cwd=None, timeout=7, transcript=transcript)

        assert pushed == {
            "name": "git push",
            "argv": ["git", "push", "origin"],
            "returncode": 1,
            "seconds": 0.0,
            "timed_out": False,
            "stdout_tail": "",
            "stderr_tail": "denied",
        }
        assert big["name"] == "verify[0]"
        assert len(big["stdout_tail"]) == 2048
        assert big["seconds"] == 2.5
        assert other["returncode"] == 0
        assert runner.calls[2] == {"argv": ["git", "fetch"], "cwd": None, "timeout": 7}
        assert transcript[0] == "$ git push origin\ndenied\n[exit 1 in 0.00 s]"

    def test_subprocess_runner_reports_timeout_and_logs(self, tmp_path):
        log = tmp_path / "runner.jsonl"
        runner = SubprocessRunner(log_path=log)

        result = runner(["sleep", "5"], timeout=0.2)
        missing = runner(["no-such-binary-3218"])

        assert result.timed_out is True
        assert result.returncode == TIMEOUT_RETURNCODE
        assert missing.returncode == 127
        lines = log.read_text().splitlines()
        assert len(lines) == 2
        assert '"timed_out": true' in lines[0]

    def test_tail_keeps_last_bytes(self):
        assert tail("abc", 2) == "bc"
        assert tail("abc") == "abc"
        assert tail("é" * 3, 3) == "é"

    def test_verify_commands_are_split_without_a_shell(self, repo, root):
        runner = RecordingRunner([(["git", "rev-parse"], (0, "0" * 40 + "\n", ""))])
        release = release_for(repo, rollback_plan={"verify": ["echo 'a b' && rm -rf /"]})

        drill.run(release, runner=runner, root=root, repo=repo["path"])

        verify_calls = [c for c in runner.calls if c["argv"][:1] == ["echo"]]
        assert verify_calls[0]["argv"] == shlex.split("echo 'a b' && rm -rf /")
        assert verify_calls[0]["cwd"] == drill.drill_record(release)["worktree"]
