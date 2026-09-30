"""The push module refuses every branch but the objective's, and a HEAD that
moved under it, before touching the network. With a token in the Keychain it
pushes and removes a branch on the throwaway repository. Plan 07 task 5;
tech stack §7.
"""

import subprocess
import uuid
from pathlib import Path

import pytest

from broker import push_branch
from broker.credentials import credential_for
from broker.errors import EffectRefused
from infra.secrets import MissingSecret, read_secret
from schemas.effect import PushBranch

SANDBOX = "yudame/cori-sandbox"
SHA = "a" * 40


@pytest.fixture
def no_network(monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError(f"subprocess was called: {a[0]}")

    monkeypatch.setattr(subprocess, "run", forbidden)


def action(objective_id: str, *, branch: str | None = None, **over) -> PushBranch:
    fields = {
        "space": "psyoptimal",
        "objective_id": objective_id,
        "brief_id": uuid.uuid4().hex,
        "repo": SANDBOX,
        "branch": branch if branch is not None else f"cori/{objective_id}",
        "source_dir": "/tmp",
        "head_sha": SHA,
    }
    fields.update(over)
    return PushBranch(**fields)


# ---------------------------------------------------------------------------
# The branch is the objective's


@pytest.mark.parametrize(
    "branch",
    [
        "main",
        "master",
        "feature/x",
        "cori",
        "cori/",
        "../cori/x",
        "cori/..",
        "",
        "cori/other",
    ],
)
def test_branches_that_are_not_the_objectives_are_refused(no_network, branch):
    objective_id = uuid.uuid4().hex
    with pytest.raises(EffectRefused):
        push_branch.check(action(objective_id, branch=branch))


def test_a_path_under_the_objectives_branch_is_refused(no_network):
    objective_id = uuid.uuid4().hex
    with pytest.raises(EffectRefused):
        push_branch.check(action(objective_id, branch=f"cori/{objective_id}/x"))


def test_another_objectives_branch_is_refused(no_network):
    with pytest.raises(EffectRefused) as info:
        push_branch.check(action(uuid.uuid4().hex, branch=f"cori/{uuid.uuid4().hex}"))
    assert "owns" in str(info.value)


def test_the_repo_field_carries_the_owner_into_the_target():
    assert action(uuid.uuid4().hex).target() == "github.com/yudame"


# ---------------------------------------------------------------------------
# The source side


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def repo_with_one_commit(tmp_path: Path) -> str:
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "note.txt").write_text("pushed by the broker\n")
    git(tmp_path, "add", "note.txt")
    git(
        tmp_path,
        "-c",
        "user.name=cori",
        "-c",
        "user.email=cori@yuda.me",
        "commit",
        "-q",
        "-m",
        "broker push test",
    )
    return git(tmp_path, "rev-parse", "HEAD")


def test_head_moved_is_refused(tmp_path):
    """The action names the SHA it meant. A work tree that moved under the
    broker is the source-side form of tech stack §7's state binding."""
    head = repo_with_one_commit(tmp_path)
    objective_id = uuid.uuid4().hex
    push_branch.check(action(objective_id, source_dir=str(tmp_path), head_sha=head))
    with pytest.raises(EffectRefused) as info:
        push_branch.check(
            action(objective_id, source_dir=str(tmp_path), head_sha="b" * 40)
        )
    assert "head_moved" in str(info.value)


def test_a_source_dir_that_is_not_a_work_tree_is_refused(tmp_path):
    with pytest.raises(EffectRefused) as info:
        push_branch.check(action(uuid.uuid4().hex, source_dir=str(tmp_path)))
    assert "work tree" in str(info.value)


def test_a_missing_source_dir_is_refused(no_network):
    with pytest.raises(EffectRefused) as info:
        push_branch.check(action(uuid.uuid4().hex, source_dir="/tmp/not-there-at-all"))
    assert "not a directory" in str(info.value)


# ---------------------------------------------------------------------------
# Live: the throwaway repository


async def test_push_query_and_delete_on_the_sandbox_repo(tmp_path):
    try:
        read_secret("github_token")
    except MissingSecret:
        pytest.skip("github_token is not in the Keychain")
    credential = credential_for("psyoptimal", "github.com/yudame")
    head = repo_with_one_commit(tmp_path)
    objective_id = uuid.uuid4().hex
    pushed = action(objective_id, source_dir=str(tmp_path), head_sha=head)
    push_branch.check(pushed)
    try:
        assert await push_branch.query(None, pushed, credential) == "absent"
        assert await push_branch.run(None, pushed, credential) == {"sha": head}
        assert await push_branch.query(None, pushed, credential) == "present"
        assert credential.value("github_token") not in git(tmp_path, "config", "--list")
    finally:
        push_branch.delete_branch(pushed, credential.value("github_token"))
    assert await push_branch.query(None, pushed, credential) == "absent"


async def test_a_branch_at_another_sha_reads_as_differs(tmp_path):
    try:
        read_secret("github_token")
    except MissingSecret:
        pytest.skip("github_token is not in the Keychain")
    credential = credential_for("psyoptimal", "github.com/yudame")
    head = repo_with_one_commit(tmp_path)
    objective_id = uuid.uuid4().hex
    pushed = action(objective_id, source_dir=str(tmp_path), head_sha=head)
    try:
        await push_branch.run(None, pushed, credential)
        moved = pushed.model_copy(
            update={
                "head_sha": "b" * 40,
                "idempotency_key": f"{SANDBOX}#{pushed.branch}@{'b' * 40}",
            }
        )
        assert await push_branch.query(None, moved, credential) == "differs"
    finally:
        push_branch.delete_branch(pushed, credential.value("github_token"))
