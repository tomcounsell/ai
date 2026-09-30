"""The smallest apple/container adapter for the Sandbox port (tech stack §6):
create, exec, read, write, destroy, driven through the CLI. Snapshot is
absent because this spike does not need it. Names start with s08- so they
never collide with other spikes running at the same time."""

from __future__ import annotations

import secrets
import subprocess
from dataclasses import dataclass
from pathlib import Path

IMAGE = "cori-base:3.14"
NETWORK = "cori-hostonly"


@dataclass
class ExecResult:
    exit_status: int
    stdout: str
    stderr: str


@dataclass
class Sandbox:
    name: str
    mount: Path  # host side of the bind mount at /work, the retained disk

    @classmethod
    def create(cls, mount: Path) -> "Sandbox":
        name = "s08-" + secrets.token_hex(3)
        subprocess.run(
            [
                "container",
                "run",
                "-d",
                "--name",
                name,
                "--network",
                NETWORK,
                "--mount",
                f"type=bind,source={mount},target=/work",
                IMAGE,
            ],
            check=True,
            capture_output=True,
        )
        sb = cls(name, mount)
        for _ in range(100):
            if sb.exec("true").exit_status == 0:
                return sb
        raise RuntimeError("sandbox never answered exec")

    def exec(
        self, cmd: str, *, timeout: float = 60, stdin: str | None = None
    ) -> ExecResult:
        args = ["container", "exec"]
        if stdin is not None:
            args.append("-i")
        args += [self.name, "sh", "-c", cmd]
        r = subprocess.run(
            args, capture_output=True, text=True, input=stdin, timeout=timeout
        )
        return ExecResult(r.returncode, r.stdout, r.stderr)

    def read(self, path: str) -> str:
        r = self.exec(f"cat {path!r}")
        if r.exit_status:
            raise FileNotFoundError(r.stderr.strip())
        return r.stdout

    def write(self, path: str, data: str) -> None:
        r = self.exec(f"cat > {path!r}", stdin=data)
        if r.exit_status:
            raise OSError(r.stderr.strip())

    def destroy(self) -> None:
        subprocess.run(["container", "rm", "-f", self.name], capture_output=True)
