"""A feedback round after a merge builds on the merged head.

A merge lands the docs head: the candidate plus the docs check's commits,
which the kernel keeps only in its mirror. Before a `patch` turn of a task
with a done merge, the kernel brings that head into the work branch, so the
round's next merge is a fast-forward of the first
(`docs/sdlc-state-machine.md`, patch; `docs/workspace.md`). On real git,
under the turn's own sandbox profile.
"""

import subprocess

import pytest

from core import git as kgit
from core import session, tasks
from core import workspace as kws

pytestmark = pytest.mark.macos

ID = ("-c", "user.name=t", "-c", "user.email=t@t")


def commit(repo, name: str, text: str) -> str:
    (repo / name).write_text(text)
    kgit.trusted(repo, "add", name)
    kgit.trusted(repo, *ID, "commit", "-qm", name)
    return kgit.trusted(repo, "rev-parse", "HEAD")


def docs_commit(mirror, parent: str) -> str:
    """A docs commit on `parent`, made in the mirror the way the docs check
    keeps its commits: there and nowhere else."""

    def g(*args, stdin=None):
        return subprocess.run(
            [kgit.binary(), "-C", str(mirror), *ID, *args],
            input=stdin,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    blob = g("hash-object", "-w", "--stdin", stdin="the docs\n")
    listing = g("ls-tree", parent) + f"\n100644 blob {blob}\tdocs.md\n"
    tree = g("mktree", stdin=listing)
    return g("commit-tree", tree, "-p", parent, "-m", "docs")


def layout(tmp_path, *, merged_to_origin: bool):
    """A task's layout after its first merge: base on the bare origin's
    `main`, candidate C in `repo/`, its docs head D in the mirror, and D
    merged to the bare origin (or, for a task with a `merge_url`, to a
    remote elsewhere, leaving the bare origin at the base)."""
    lay = kws.Layout(tmp_path / "work" / "abc123")
    lay.root.mkdir(parents=True)
    kgit.trusted(lay.root, "init", "-q", "-b", "valor/abc123", str(lay.repo))
    base = commit(lay.repo, "a.txt", "base\n")
    kgit.trusted(lay.root, "init", "-q", "--bare", str(lay.origin))
    kgit.trusted(lay.origin, "config", "receive.denyNonFastForwards", "true")
    kgit.trusted(lay.repo, "remote", "add", "origin", str(lay.origin))
    kgit.trusted(lay.repo, "push", "-q", str(lay.origin), f"{base}:refs/heads/main")
    kgit.trusted(lay.root, "init", "-q", "--bare", str(lay.mirror))
    cand = commit(lay.repo, "a.txt", "candidate\n")
    kgit.trusted(lay.mirror, "fetch", "-q", str(lay.repo), f"{cand}:refs/valor/candidate")
    head = docs_commit(lay.mirror, cand)
    kgit.trusted(lay.mirror, "update-ref", "refs/valor/docs/t1", head)
    if merged_to_origin:
        kgit.trusted(lay.mirror, "push", "-q", str(lay.origin), f"{head}:refs/heads/main")
    profile = tmp_path / "turn.sb"
    profile.write_text(kws.turn_profile(lay, [], home=tmp_path / "home"))
    return lay, profile, cand, head


@pytest.mark.parametrize("merged_to_origin", [True, False], ids=["own-origin", "merge-url"])
def test_the_next_round_merges_as_a_fast_forward_of_the_first(tmp_path, merged_to_origin):
    lay, profile, _cand, head = layout(tmp_path, merged_to_origin=merged_to_origin)
    kws.bring_merged(lay.repo, lay.mirror, lay.origin, head, "main", profile, "merged-abc123")
    assert kgit.trusted(lay.repo, "rev-parse", "HEAD") == head
    assert kgit.trusted(lay.origin, "rev-parse", "main") == head
    assert kgit.trusted(lay.repo, "symbolic-ref", "--short", "HEAD") == "valor/abc123"
    kws.bring_merged(
        lay.repo, lay.mirror, lay.origin, head, "main", profile, "merged-abc123"
    )  # again: no change
    patch = commit(lay.repo, "a.txt", "patched\n")
    kgit.trusted(lay.mirror, "fetch", "-q", str(lay.repo), f"{patch}:refs/valor/candidate2")
    kgit.trusted(lay.mirror, "push", "-q", str(lay.origin), f"{patch}:refs/heads/main")  # the second merge
    assert kgit.trusted(lay.origin, "rev-parse", "main") == patch


def brief(lay, profile) -> tasks.Brief:
    return tasks.Brief(
        instruction="x",
        workspace=str(lay.repo),
        mirror=str(lay.mirror),
        push_url=str(lay.origin),
        target_branch="main",
        harness={"sandbox_profile": str(profile)},
    )


def merged_rows(head: str) -> list[dict]:
    merge = {"head_sha": head, "url": "/elsewhere", "target_branch": "main"}
    return [
        {"id": 1, "task_id": "abc123", "type": "effect.intent",
         "payload": {"effect_id": "e1", "action_type": "merge", "payload": merge}},
        {"id": 2, "task_id": "abc123", "type": "effect.outcome", "payload": {"effect_id": "e1", "kind": "done"}},
    ]  # fmt: skip


def test_the_session_brings_the_merged_head_in_and_tells_the_turn_when_it_cannot(tmp_path):
    lay, profile, cand, head = layout(tmp_path, merged_to_origin=False)
    b = brief(lay, profile)
    assert session.merged_into_work(b, []) is None  # no merge yet: nothing to bring
    assert kgit.trusted(lay.repo, "rev-parse", "HEAD") == cand
    (lay.repo / "docs.md").write_text("an edit the head would overwrite\n")
    note = session.merged_into_work(b, merged_rows(head))
    assert (
        note.startswith("# The merged head")
        and head in note
        and "git merge --ff-only" in note
        and f"rebase the branch onto {head}" in note
    )
    assert kgit.trusted(lay.repo, "rev-parse", "HEAD") == cand
    (lay.repo / "docs.md").unlink()
    assert session.merged_into_work(b, merged_rows(head)) is None
    assert kgit.trusted(lay.repo, "rev-parse", "HEAD") == head
