"""Programs the kernel runs: absolute paths for `git`, `claude`, and
`sandbox-exec`, found on `PATH` (Homebrew and user tools included) unless a
setting names one. Imports only the standard library."""

import shutil
from pathlib import Path

SANDBOX_EXEC = "/usr/bin/sandbox-exec"
PS = "/bin/ps"
SECURITY = "/usr/bin/security"
# Apple's `container` (core/container.py).
CONTAINER = "/usr/local/bin/container"
CONTAINER_LIBEXEC = "/usr/local/libexec/container"
CONTAINER_HELPERS = (
    "/usr/local/bin/container-apiserver",
    "/usr/local/libexec/container/plugins/container-core-images/bin/container-core-images",
    "/usr/local/libexec/container/plugins/container-network-vmnet/bin/container-network-vmnet",
    "/usr/local/libexec/container/plugins/container-runtime-linux/bin/container-runtime-linux",
    "/usr/local/libexec/container/plugins/machine-apiserver/bin/machine-apiserver",
)


class Untrusted(RuntimeError):
    pass


def require(path: str | Path) -> str:
    """The path, if it is an existing file; otherwise `Untrusted`."""
    if not Path(path).is_file():
        raise Untrusted(f"{path} does not exist")
    return str(path)


def which(name: str) -> str | None:
    """The absolute path of `name` on `PATH`, or None."""
    found = shutil.which(name)
    return str(Path(found).absolute()) if found else None


def git() -> str | None:
    return which("git")


def claude() -> str | None:
    return which("claude")


def require_git(path: str | Path | None) -> str:
    if not path:
        raise Untrusted("no git found: install git, or set VALOR_GIT")
    return require(path)
