"""Rollback drill against a real release row (#3218, lane 6).

The unit file drives the drill with a stand-in release. This file runs the
whole path: a real temporary git repository, a real ``ImprovementRelease``
row in state ``proposed`` in the claimed test db, the real
``SubprocessRunner``, and the ``drill_log`` transcript round-tripping through
the verifying artifact store, whose reference carries the transcript's
digest and whose ``load()`` re-hashes on every read.

Uses the autouse ``redis_test_db`` fixture (tests/conftest.py).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from models.improvement_release import ImprovementRelease
from models.verifying_artifact_store import verifying_artifact_store
from tools.improvement_release import drill
from tools.improvement_release.evaluation_read import json_field
from tools.improvement_release.lifecycle import withdraw
from tools.improvement_release.runner import SubprocessRunner

PK = "test-3218-drill"

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


@pytest.fixture
def repo(tmp_path):
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
def release(repo):
    row = ImprovementRelease.create(
        project_key=PK,
        created_at=datetime.now(UTC),
        state="proposed",
        kind="core_workflow",
        surfaces=json.dumps(["tools/thing.py"]),
        candidate_ref="cand",
        base_revision=repo["base"],
        rollback_plan=json.dumps(
            {
                "steps": ["git revert --no-commit <base_revision>..<candidate_ref>"],
                "verify": ["cat tools/thing.py"],
            }
        ),
    )
    yield row
    for stale in ImprovementRelease.query.filter(project_key=PK):
        stale.delete()


def reload(release_id: str) -> ImprovementRelease:
    row = ImprovementRelease.query.filter(project_key=PK, id=release_id).first()
    assert row is not None
    return row


class TestDrillOnARealRelease:
    def test_drill_passes_and_records_what_it_exercised(self, repo, release, tmp_path):
        root = tmp_path / "retention"

        record = drill.run(release, root=root, repo=repo["path"])

        assert record["result"] == "pass", record
        assert record["restored"] is True
        assert record["exercised"] == ["range_checks", "revert", "tree_restoration", "verify"]
        assert record["not_exercised"] == [
            "fleet_update",
            "production_traffic",
            "merge_commit_revert",
        ]
        assert record["base_revision"] == repo["base"]
        assert record["candidate_ref"] == git(repo["path"], "rev-parse", "cand")
        assert not Path(record["worktree"]).exists()
        assert "drills" not in git(repo["path"], "worktree", "list", "--porcelain")

        stored = reload(release.id)
        assert stored.state == "proposed", "a drill never changes state"
        stored_record = drill.drill_record(stored)
        assert stored_record == record
        assert stored_record["steps"][-1]["name"] == "restoration"

    def test_drill_log_round_trips_through_store(self, repo, release, tmp_path):
        record = drill.run(release, root=tmp_path / "retention", repo=repo["path"])

        stored = reload(release.id)
        reference = getattr(stored, "drill_log")
        assert isinstance(reference, str) and reference.startswith("$CF:"), reference
        content_hash, relative = verifying_artifact_store._parse_reference(reference)
        # the conftest fixture keeps these bytes out of the production retention root
        live = Path(os.environ["POPOTO_IMPROVEMENT_CONTENT_PATH"]) / relative
        assert live.is_file(), live
        assert str(tmp_path) in str(live)

        transcript = drill.read_drill_log(stored)
        assert hashlib.sha256(transcript.encode("utf-8")).hexdigest() == content_hash
        assert transcript == verifying_artifact_store.load(reference).decode("utf-8")
        assert transcript == "\n".join(block for block in release.drill_log.split("\n")), (
            "the in-memory transcript and the stored one agree"
        )
        assert "$ git worktree add --detach" in transcript
        assert "$ git revert --no-commit" in transcript
        assert "$ cat tools/thing.py\nv0" in transcript
        assert transcript.count("$ ") >= len(record["steps"])

    def test_failed_drill_is_recorded_without_a_state_change(self, repo, release, tmp_path):
        git(repo["path"], "switch", "-q", "cand")
        commit(repo["path"], "undeclared", **{"docs/other.md": "changed\n"})
        git(repo["path"], "switch", "-q", "main")

        record = drill.run(release, root=tmp_path / "retention", repo=repo["path"])

        assert record["result"] == "fail"
        assert record["reason"] == "UNDECLARED_SURFACE_CHANGED"
        stored = reload(release.id)
        assert stored.state == "proposed"
        assert drill.drill_record(stored)["paths"] == ["docs/other.md"]
        assert "[drill fail: UNDECLARED_SURFACE_CHANGED]" in drill.read_drill_log(stored)

    def test_drill_persist_keeps_a_withdraw_landing_during_worktree_add(
        self, repo, release, tmp_path
    ):
        """Race 1 on the drill's persist: a ``withdraw`` during ``git worktree add`` survives.

        ``run`` was handed the row before the git steps; by the time it
        writes ``rollback_drill`` and ``drill_log`` the row has moved. The
        persisted row keeps the withdrawn state, its reason, and the
        ``withdrawn`` event, and carries the drill record and transcript.
        """
        real = SubprocessRunner()
        at = datetime.now(UTC)

        def runner(argv, *, cwd=None, timeout=None):
            if [str(part) for part in argv[:3]] == ["git", "worktree", "add"]:
                withdraw(release.id, project_key=PK, reason="pulled", now=at)
            return real(argv, cwd=cwd, timeout=timeout)

        record = drill.run(release, runner=runner, root=tmp_path / "retention", repo=repo["path"])

        assert record["result"] == "pass", record
        stored = reload(release.id)
        assert stored.state == "withdrawn"
        outcome = json_field(stored.outcome)
        assert outcome["withdrawn"] == {"reason": "pulled", "at": at.isoformat()}
        assert [e["event"] for e in outcome["history"]] == ["withdrawn"]
        assert drill.drill_record(stored) == record
        assert "$ git worktree add --detach" in drill.read_drill_log(stored)

    def test_drill_refuses_a_release_past_proposal(self, repo, release, tmp_path):
        release.state = "approved"
        release.save()

        with pytest.raises(drill.DrillRefused) as excinfo:
            drill.run(release, root=tmp_path / "retention", repo=repo["path"])

        assert excinfo.value.code == "NOT_PROPOSED"
        assert drill.drill_record(reload(release.id)) is None
