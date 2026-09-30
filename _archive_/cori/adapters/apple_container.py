"""The one sandbox adapter: apple/container. Tech stack §6, seams §2.2.

Every tool call an agent makes lands here, and every call goes out through the
`container` CLI with `infra.sandbox.proc.run`, so nothing blocks the kernel's
event loop (tech stack §2). The adapter imports `schemas/`, `ports/`, and
`infra.secrets`, and never `kernel/`.

Three things this file is careful about:

- A path under the mount is read and written from the host side, so an
  artifact is hashed without an exec (spike 08). That is the one place the
  adapter reaches past the VM, so containment is checked on the resolved path:
  a symlink the sandbox plants inside the mount cannot pull a host file out.
- A stop is `container kill` and then a probe from outside the VM. The CLI's
  exit is never the confirmation (spike 07).
- Ids are minted by the kernel. `snapshot` receives its `snapshot_id` and this
  file mints nothing.
"""

import asyncio
import hashlib
import json
import math
import os
import posixpath
import secrets as _secrets
import shutil
import subprocess
import tarfile
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from infra.sandbox.mounts import default_sandbox_root
from infra.sandbox.proc import run
from infra.secrets import read_secret as _read_secret
from schemas.sandbox import (
    ExecResult,
    SandboxHandle,
    SandboxProfile,
    SnapshotId,
    SnapshotRef,
    StopReceipt,
)

CONTAINER = "container"
NAME_PREFIX = "cori-"
DATA_CLASS_PREFIX = "CORI_DATA_CLASS_"
BOOT_TIMEOUT_S = 30.0
PROBE_INTERVAL_S = 0.05
PROBE_WINDOW_S = 10.0

LABELS = (
    "cori.space",
    "cori.profile",
    "cori.key",
    "cori.mount_source",
    "cori.mount_target",
    "cori.readonly",
    "cori.image",
)


class SandboxError(RuntimeError):
    """The runtime refused or failed, and the caller cannot assume a state."""


class StopUnconfirmed(SandboxError):
    """A kill was issued and no probe from outside the VM confirmed the death.
    The kernel issues no replacement generation on this (tech stack §4)."""


def _now() -> datetime:
    return datetime.now(UTC)


class AppleContainer:
    """`SandboxProvider` over apple/container 1.4."""

    def __init__(
        self,
        *,
        network: str = "cori-hostonly",
        sandbox_root: Path | None = None,
        read_secret: Callable[[str], str] = _read_secret,
    ) -> None:
        self.network = network
        self.sandbox_root = Path(sandbox_root or default_sandbox_root())
        self.read_secret = read_secret

    # ---- the CLI -------------------------------------------------------

    async def _cli(self, *args: str, timeout: float | None = 120.0):
        return await run(CONTAINER, *args, timeout=timeout)

    async def _checked(self, *args: str, timeout: float | None = 120.0) -> str:
        r = await self._cli(*args, timeout=timeout)
        if r.returncode != 0:
            raise SandboxError(
                f"container {' '.join(args)} exited {r.returncode}: "
                f"{r.stderr.decode('utf-8', 'replace').strip()}"
            )
        return r.stdout.decode("utf-8", "replace")

    async def _inspect(self, name: str) -> dict | None:
        r = await self._cli("inspect", name, timeout=30.0)
        if r.returncode != 0:
            return None
        try:
            return json.loads(r.stdout)[0]
        except json.JSONDecodeError, IndexError, KeyError:
            return None

    async def _state(self, name: str) -> str | None:
        """The runtime's own word for the container, from outside the VM."""
        d = await self._inspect(name)
        return None if d is None else d.get("status", {}).get("state")

    # ---- names, labels, profiles ---------------------------------------

    def _name(self, profile: SandboxProfile) -> str:
        key = "".join(c for c in profile.key if c.isalnum() or c in "._-")[:24]
        return f"{NAME_PREFIX}{profile.name}-{key}-{_secrets.token_hex(3)}"

    @staticmethod
    def _labels(profile: SandboxProfile) -> list[str]:
        """Every profile field except `env`, so `running()` can rebuild a
        profile through the validator after a kernel crash."""
        values = {
            "cori.space": profile.space,
            "cori.profile": profile.name,
            "cori.key": profile.key,
            "cori.mount_source": profile.mount_source,
            "cori.mount_target": profile.mount_target,
            "cori.readonly": "true" if profile.readonly else "false",
            "cori.image": profile.image,
        }
        out = []
        for k in LABELS:
            out += ["--label", f"{k}={values[k]}"]
        return out

    def _check_mount_source(self, profile: SandboxProfile) -> None:
        source = Path(profile.mount_source)
        if not source.is_dir():
            raise SandboxError(
                f"mount source {profile.mount_source!r} is not a directory"
            )
        if profile.name == "scratch":
            return  # a scratch mount is a root of the space itself
        root = self.sandbox_root.resolve()
        try:
            source.resolve().relative_to(root)
        except ValueError:
            raise SandboxError(
                f"mount source {profile.mount_source!r} for a {profile.name!r} profile "
                f"is outside the sandbox root {root}"
            ) from None

    def _env_lines(self, profile: SandboxProfile) -> list[str]:
        """Keychain item names become values here and nowhere else. A key under
        CORI_DATA_CLASS_ is the class label of the secret it names and is passed
        through literally: a class is not a secret."""
        lines = []
        for var, item in sorted(profile.env.items()):
            value = (
                item if var.startswith(DATA_CLASS_PREFIX) else self.read_secret(item)
            )
            if "\n" in value or "\r" in value:
                raise SandboxError(
                    f"the value for {var!r} holds a newline, which --env-file cannot carry"
                )
            lines.append(f"{var}={value}")
        return lines

    # ---- create --------------------------------------------------------

    async def create(self, profile: SandboxProfile) -> SandboxHandle:
        profile = SandboxProfile.model_validate(profile.model_dump())
        self._check_mount_source(profile)
        name = self._name(profile)

        mount = (
            f"type=bind,source={profile.mount_source},target={profile.mount_target}"
            + (",readonly" if profile.readonly else "")
        )
        args = [
            "run",
            "-d",
            "--name",
            name,
            "--network",
            self.network,
            *self._labels(profile),
            "--mount",
            mount,
        ]

        env_file = None
        try:
            lines = self._env_lines(profile)
            if lines:
                fd, path = tempfile.mkstemp(prefix="cori-env-")
                env_file = Path(path)
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, "w") as fh:
                    fh.write("\n".join(lines) + "\n")
                args += ["--env-file", str(env_file)]
            args.append(profile.image)
            await self._checked(*args, timeout=180.0)
        finally:
            if env_file is not None:
                env_file.unlink(missing_ok=True)

        await self._wait_ready(name)
        return SandboxHandle(id=name, profile=profile, created_at=_now())

    async def _wait_ready(self, name: str) -> None:
        """`run -d` returns when the VM is up; the first exec answers a little
        later (spike 06), so readiness is an exec that exits 0."""
        t0 = time.perf_counter()
        while True:
            r = await self._cli("exec", name, "true", timeout=30.0)
            if r.returncode == 0:
                return
            if time.perf_counter() - t0 > BOOT_TIMEOUT_S:
                raise SandboxError(f"container {name} never answered exec")
            await asyncio.sleep(0.1)

    # ---- exec ----------------------------------------------------------

    async def exec(self, h: SandboxHandle, cmd: str, *, timeout: float) -> ExecResult:
        """The timeout is armed inside the VM with coreutils `timeout`, because
        a host-side timeout alone leaves the command running in the sandbox
        after the tool has returned. The host side is the backstop."""
        guest_timeout = max(1, math.ceil(timeout))
        r = await self._cli(
            "exec",
            "--cwd",
            h.profile.mount_target,
            h.id,
            "timeout",
            "-s",
            "KILL",
            str(guest_timeout),
            "sh",
            "-c",
            cmd,
            timeout=timeout + 10.0,
        )
        if r.timed_out:  # the host-side backstop fired: the CLI is gone
            return ExecResult(
                exit_status=-1,
                stdout="",
                stderr="the container CLI did not return within timeout + 10 s",
                duration_ms=r.ms,
                timed_out=True,
            )
        # 137 is SIGKILL. `timeout` sends it at the deadline, but so does the
        # OOM killer and so does a `kill -9` from the command itself, so the
        # elapsed time has to agree before this is called a timeout.
        timed_out = r.returncode == 137 and r.ms >= guest_timeout * 1000
        return ExecResult(
            exit_status=r.returncode,
            stdout=r.stdout.decode("utf-8", "replace"),
            stderr=r.stderr.decode("utf-8", "replace"),
            duration_ms=r.ms,
            timed_out=timed_out,
        )

    # ---- host-side paths -----------------------------------------------

    def _map(self, h: SandboxHandle, path: str) -> tuple[str, Path | None]:
        """("outside", None) for a path that is not under the mount,
        ("escape", None) for one that resolves out of it, ("inside", p)
        otherwise. The resolved path is what is checked, because a symlink the
        sandbox plants inside the mount can point anywhere on the host."""
        target = h.profile.mount_target
        guest = posixpath.normpath(posixpath.join(target, path))
        if guest != target and not guest.startswith(target.rstrip("/") + "/"):
            return "outside", None
        rel = posixpath.relpath(guest, target)
        source = Path(h.profile.mount_source)
        host = source if rel == "." else source / rel
        try:
            base = source.resolve()
            resolved = host.resolve()
        except OSError:
            return "escape", None
        if resolved != base and base not in resolved.parents:
            return "escape", None
        return "inside", host

    def host_path(self, h: SandboxHandle, path: str) -> Path | None:
        """The host file behind a guest path, or None when there is none the
        adapter will touch."""
        kind, host = self._map(h, path)
        return host if kind == "inside" else None

    async def read(self, h: SandboxHandle, path: str) -> bytes:
        kind, host = self._map(h, path)
        if kind == "escape":
            raise PermissionError(f"{path!r} resolves outside the mount and is refused")
        if kind == "inside":
            return await asyncio.to_thread(host.read_bytes)
        r = await self._cli("exec", h.id, "cat", path, timeout=60.0)
        if r.returncode != 0:
            raise FileNotFoundError(
                f"{path!r} could not be read inside {h.id}: "
                f"{r.stderr.decode('utf-8', 'replace').strip()}"
            )
        return r.stdout

    async def write(self, h: SandboxHandle, path: str, data: bytes) -> None:
        if h.profile.readonly:
            raise PermissionError(
                f"the {h.profile.name!r} profile is read-only; nothing writes to its mount"
            )
        kind, host = self._map(h, path)
        if kind != "inside":
            raise PermissionError(
                f"{path!r} is outside the mount at {h.profile.mount_target!r} "
                "and the adapter writes nowhere else"
            )
        await asyncio.to_thread(_atomic_write, host, data)

    # ---- stop ----------------------------------------------------------

    async def stop(self, h: SandboxHandle) -> StopReceipt:
        """Kill, then confirm from outside the VM. Spike 07 measured the CLI's
        return lagging execution death by 1.4 s at the median and 21 s once, so
        the receipt's `confirmed_dead_at` comes from the probe and never from
        the kill command. The disk is untouched: it is a bind mount on the
        host, and it survives (spike 07, 40 of 40)."""
        killed_at = _now()
        r = await self._cli("kill", h.id, timeout=60.0)
        if r.returncode != 0:
            # A nonzero exit is a runtime error, not a live sandbox. Read the
            # state and try once more if it still says running (spike 07).
            if await self._state(h.id) == "running":
                await self._cli("kill", h.id, timeout=60.0)

        confirmed = await self._probe_dead(h.id, PROBE_WINDOW_S)
        if confirmed is None:
            await self._cli("kill", h.id, timeout=60.0)
            confirmed = await self._probe_dead(h.id, PROBE_WINDOW_S)
        if confirmed is None:
            raise StopUnconfirmed(
                f"{h.id} was killed twice and no probe from outside the VM "
                f"confirmed it dead within {2 * PROBE_WINDOW_S:.0f} s"
            )
        probe, when = confirmed
        return StopReceipt(
            handle_id=h.id,
            killed_at=killed_at,
            confirmed_dead_at=max(when, killed_at),
            probe=probe,
        )

    async def _probe_dead(self, name: str, window: float):
        """Two observations from outside the VM: the runtime reports a state
        other than running, and an exec is refused. Both are later than
        execution death, never earlier, which is the safe side."""
        t0 = time.perf_counter()
        while True:
            state = await self._state(name)
            if state != "running":
                r = await self._cli("exec", name, "true", timeout=30.0)
                if r.returncode != 0:
                    return f"inspect:{state or 'absent'};exec:refused", _now()
            if time.perf_counter() - t0 > window:
                return None
            await asyncio.sleep(PROBE_INTERVAL_S)

    # ---- snapshot ------------------------------------------------------

    async def snapshot(
        self, h: SandboxHandle, *, snapshot_id: SnapshotId
    ) -> SnapshotRef:
        """The mount, never the 285 MB rootfs (spike 06). Taken after `stop`
        and `confirm_stop` (seams §3.5), so the archive is of a quiet disk, and
        every hash is computed here on the host, never by a command inside a
        container (spike 08, surprise 2). The id is the kernel's."""
        if await self._state(h.id) == "running":
            raise SandboxError(
                f"{h.id} is still running; an archive of a disk something is "
                "writing is not a record of anything"
            )
        source = Path(h.profile.mount_source)
        archive = self.sandbox_root / h.profile.space / "snapshots"
        archive = archive / f"{snapshot_id}.tar.gz"
        files, digest = await asyncio.to_thread(_archive_mount, source, archive)
        return SnapshotRef(
            id=snapshot_id,
            handle_id=h.id,
            path=str(archive),
            sha256=digest,
            files=files,
            taken_at=_now(),
        )

    # ---- destroy -------------------------------------------------------

    async def destroy(self, h: SandboxHandle) -> None:
        if await self._state(h.id) == "running":
            await self.stop(h)
        await self._cli("rm", "-f", h.id, timeout=60.0)
        if h.profile.name != "verify":
            return  # a scratch mount is a root; a worktree outlives its containers
        extraction = Path(h.profile.mount_source).resolve()
        verify_dir = (self.sandbox_root / h.profile.space / "verify").resolve()
        if verify_dir in extraction.parents:
            await asyncio.to_thread(shutil.rmtree, extraction, True)

    # ---- what is up -----------------------------------------------------

    async def running(self) -> list[SandboxHandle]:
        """Every sandbox this system has up, rebuilt from the labels `create`
        wrote. After a kernel restart this is the only record of what is still
        running, so the profile is reconstructed through the validator rather
        than trusted: a container whose labels do not make a profile is not
        one of ours to stop. `env` is empty, because a profile carries Keychain
        item names and a container carries only values, which never come back
        out of the runtime and into a row.

        Containers another session started are filtered out by the `cori-`
        prefix and by the label set, so a stranger on the same network is noise
        rather than a failure.
        """
        r = await self._cli("ls", "--format", "json", timeout=60.0)
        if r.returncode != 0:
            raise SandboxError(
                f"listing containers failed: "
                f"{r.stderr.decode('utf-8', 'replace').strip()}"
            )
        rows = json.loads(r.stdout.decode("utf-8", "replace") or "[]")
        handles = []
        for row in rows:
            name = row.get("id") or ""
            status = row.get("status") or {}
            if not name.startswith(NAME_PREFIX) or status.get("state") != "running":
                continue
            labels = (row.get("configuration") or {}).get("labels") or {}
            if not all(k in labels for k in LABELS):
                continue
            try:
                profile = SandboxProfile(
                    name=labels["cori.profile"],
                    space=labels["cori.space"],
                    mount_source=labels["cori.mount_source"],
                    mount_target=labels["cori.mount_target"],
                    readonly=labels["cori.readonly"] == "true",
                    network="hostonly",
                    key=labels["cori.key"],
                    env={},
                    image=labels["cori.image"],
                )
            except ValueError:
                continue
            handles.append(
                SandboxHandle(
                    id=name,
                    profile=profile,
                    created_at=_started_at(status),
                )
            )
        return handles

    # ---- the network ---------------------------------------------------

    @staticmethod
    def gateway_ip(network: str = "cori-hostonly") -> str:
        """The vmnet gateway address: the one host address a sandbox can reach.
        Nothing of this system listens there at M0 (the gateway binds
        127.0.0.1, seams §3.9). It serves the tests' probe server and the
        allowlisting proxy of tech stack §6 when that TODO is triggered."""
        r = subprocess.run(
            [CONTAINER, "network", "inspect", network],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            raise SandboxError(f"network {network!r}: {r.stderr.strip()}")
        return json.loads(r.stdout)[0]["status"]["ipv4Gateway"]


def _started_at(status: dict) -> datetime:
    """The runtime's own start time, so a handle rebuilt after a restart says
    when the sandbox came up rather than when the kernel noticed."""
    raw = status.get("startedDate") or ""
    try:
        return datetime.fromisoformat(raw).astimezone(UTC)
    except ValueError:
        return _now()


def _file_digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _archive_mount(source: Path, archive: Path) -> tuple[dict[str, str], str]:
    """A gzip tar of the mount with symlinks kept as links and never followed,
    plus the sha256 of every regular file by relative path. Both on the host."""
    members = sorted(source.rglob("*"), key=lambda p: str(p.relative_to(source)))
    files = {
        str(p.relative_to(source)): _file_digest(p)
        for p in members
        if p.is_file() and not p.is_symlink()
    }
    archive.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "w:gz", dereference=False) as tar:
        for path in members:
            tar.add(path, arcname=str(path.relative_to(source)), recursive=False)
    return files, _file_digest(archive)


def _atomic_write(host: Path, data: bytes) -> None:
    host.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(host.parent), prefix=".cori-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, host)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
