"""Sandbox schemas. Seams §1.9, tech stack §6, spikes 06, 07, 08.

The profile is the capability (tech stack §6), so the profile table is pinned
by a validator and a profile that does not match the table fails at
construction rather than at boot. `env` maps an environment variable name to a
Keychain item name; a secret value never enters a profile, and so never enters
a Brief, an event, or a row.
"""

from datetime import datetime
from typing import Literal

from pydantic import field_validator, model_validator

from schemas.ids import SnapshotId, SpaceId
from schemas.space import Strict

SandboxProfileName = Literal["scratch", "verify", "worktree"]

MOUNT_TARGET = "/work"

# Architect, 2026-09-19 (seams §1.9): host-only for every profile at M0.
# "none" is reserved for the day the runtime offers a no-network mode.
NETWORK_NONE_REFUSED = (
    'network "none" is refused: apple/container offers no no-network mode, so '
    'every profile is host-only at M0 and "none" is admitted on the day a '
    "runtime release offers one (seams §1.9)"
)

# The table of tech stack §6, pinned: readonly, and whether secrets may ride.
_READONLY: dict[str, bool] = {"scratch": True, "verify": True, "worktree": False}


class SandboxProfile(Strict, frozen=True):
    name: SandboxProfileName
    space: SpaceId
    mount_source: str
    mount_target: str = MOUNT_TARGET
    readonly: bool
    network: Literal["hostonly", "none"]
    key: str
    env: dict[str, str]  # env var name -> Keychain item name, worktree only
    image: str = "cori-base:3.14"

    @field_validator("network")
    @classmethod
    def network_is_hostonly(cls, v: str) -> str:
        if v == "none":
            raise ValueError(NETWORK_NONE_REFUSED)
        return v

    @field_validator("mount_target")
    @classmethod
    def mount_target_is_work(cls, v: str) -> str:
        if v != MOUNT_TARGET:
            raise ValueError(f"mount_target is {MOUNT_TARGET!r}, got {v!r}")
        return v

    @field_validator("mount_source")
    @classmethod
    def mount_source_is_absolute(cls, v: str) -> str:
        if not v.startswith("/"):
            raise ValueError(f"mount_source must be an absolute host path, got {v!r}")
        return v

    @model_validator(mode="after")
    def profile_table_is_pinned(self) -> "SandboxProfile":
        expected = _READONLY[self.name]
        if self.readonly is not expected:
            raise ValueError(
                f"profile {self.name!r} is readonly={expected} (tech stack §6), "
                f"got readonly={self.readonly}"
            )
        if self.readonly and self.env:
            raise ValueError(
                f"profile {self.name!r} is read-only and carries no secrets; "
                "env is the worktree profile's alone (tech stack §6)"
            )
        return self


class SandboxHandle(Strict, frozen=True):
    id: str  # container name
    profile: SandboxProfile
    created_at: datetime


class ExecResult(Strict, frozen=True):
    exit_status: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool


class StopReceipt(Strict, frozen=True):
    handle_id: str
    killed_at: datetime
    confirmed_dead_at: datetime  # by a probe from outside the VM (spike 07)
    probe: str

    @model_validator(mode="after")
    def confirmed_after_killed(self) -> "StopReceipt":
        if self.confirmed_dead_at < self.killed_at:
            raise ValueError(
                "confirmed_dead_at is the probe's time and is never before killed_at"
            )
        return self


class SnapshotRef(Strict, frozen=True):
    id: SnapshotId
    handle_id: str
    path: str  # host path of the archive of the mount, never the rootfs
    sha256: str  # of the archive
    files: dict[str, str]  # relative path -> sha256, hashed on the host side
    taken_at: datetime
