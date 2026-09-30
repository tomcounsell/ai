"""The sandbox port. Seams §2.2, tech stack §6.

Seven operations. `stop` is the seventh: tech stack §4 requires a compute stop
with the disk retained, and `destroy` cannot serve because it removes the
container. `stop` is `container kill` followed by a probe from outside the VM
and returns only when the probe confirms execution is dead (spike 07).

`read` and `write` of a path under the mount are served from the host side, so
the tool bridge hashes an artifact without an exec (spike 08); a path that
escapes the mount by symlink is refused. `snapshot` archives and hashes the
mount on the host, never the rootfs (spike 06), with the id the kernel passes.
`destroy` after `stop` removes the container and, for `verify`, the extracted
artifact directory.
"""

from typing import Protocol

from schemas.sandbox import (
    ExecResult,
    SandboxHandle,
    SandboxProfile,
    SnapshotId,
    SnapshotRef,
    StopReceipt,
)


class SandboxProvider(Protocol):
    async def create(self, profile: SandboxProfile) -> SandboxHandle: ...
    async def exec(
        self, h: SandboxHandle, cmd: str, *, timeout: float
    ) -> ExecResult: ...
    async def read(self, h: SandboxHandle, path: str) -> bytes: ...
    async def write(self, h: SandboxHandle, path: str, data: bytes) -> None: ...
    async def snapshot(
        self, h: SandboxHandle, *, snapshot_id: SnapshotId
    ) -> SnapshotRef: ...
    async def stop(self, h: SandboxHandle) -> StopReceipt: ...
    async def destroy(self, h: SandboxHandle) -> None: ...
