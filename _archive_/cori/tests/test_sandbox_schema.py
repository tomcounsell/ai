"""Task 1: the profile is the capability, so a wrong profile fails at
construction. Seams §1.9, tech stack §6, plan 06 task 1."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from schemas.sandbox import (
    SandboxHandle,
    SandboxProfile,
    SnapshotRef,
    StopReceipt,
)


def profile(**over) -> SandboxProfile:
    fields = dict(
        name="worktree",
        space="s1",
        mount_source="/tmp/cori/s1/worktrees/o1",
        readonly=False,
        network="hostonly",
        key="o1",
        env={},
    )
    fields.update(over)
    return SandboxProfile(**fields)


def test_profile_table_is_pinned():
    # The table of tech stack §6: scratch and verify read-only, worktree not.
    assert profile(name="scratch", readonly=True).readonly is True
    assert profile(name="verify", readonly=True).readonly is True
    assert profile().readonly is False

    with pytest.raises(ValidationError, match="readonly=True"):
        profile(name="scratch", readonly=False)
    with pytest.raises(ValidationError, match="readonly=True"):
        profile(name="verify", readonly=False)
    with pytest.raises(ValidationError, match="readonly=False"):
        profile(name="worktree", readonly=True)

    # Secrets ride in a worktree and nowhere else.
    assert profile(env={"PGPASSWORD": "psyoptimal_db"}).env == {
        "PGPASSWORD": "psyoptimal_db"
    }
    with pytest.raises(ValidationError, match="read-only"):
        profile(name="scratch", readonly=True, env={"PGPASSWORD": "psyoptimal_db"})

    with pytest.raises(ValidationError):
        profile(name="repl")


def test_none_network_refused():
    assert profile(network="hostonly").network == "hostonly"
    with pytest.raises(ValidationError, match="no-network mode"):
        profile(network="none")
    with pytest.raises(ValidationError):
        profile(network="bridge")


def test_extra_field_refused():
    with pytest.raises(ValidationError, match="extra"):
        profile(cpus=4)
    with pytest.raises(ValidationError, match="extra"):
        SandboxHandle(
            id="cori-worktree-o1-abc123",
            profile=profile(),
            created_at=datetime.now(UTC),
            pid=1,
        )


def test_mount_target_is_work():
    assert profile().mount_target == "/work"
    with pytest.raises(ValidationError, match="mount_target"):
        profile(mount_target="/srv")
    with pytest.raises(ValidationError, match="absolute"):
        profile(mount_source="worktrees/o1")


def test_receipt_confirmed_not_before_killed():
    killed = datetime.now(UTC)
    ok = StopReceipt(
        handle_id="cori-worktree-o1-abc123",
        killed_at=killed,
        confirmed_dead_at=killed + timedelta(milliseconds=265),
        probe="inspect:stopped;exec:refused",
    )
    assert ok.confirmed_dead_at > ok.killed_at
    with pytest.raises(ValidationError, match="never before killed_at"):
        StopReceipt(
            handle_id="cori-worktree-o1-abc123",
            killed_at=killed,
            confirmed_dead_at=killed - timedelta(milliseconds=1),
            probe="inspect:stopped;exec:refused",
        )


def test_models_are_frozen():
    p = profile()
    with pytest.raises(ValidationError):
        p.readonly = True
    ref = SnapshotRef(
        id="01900000-0000-7000-8000-000000000000",
        handle_id="cori-worktree-o1-abc123",
        path="/tmp/cori/s1/snapshots/x.tar.gz",
        sha256="0" * 64,
        files={"a.txt": "1" * 64},
        taken_at=datetime.now(UTC),
    )
    with pytest.raises(ValidationError):
        ref.sha256 = "2" * 64
