"""Programs the kernel runs outside a turn's sandbox, held to one rule: the
file, and every directory above it, is owned by root and writable by no
group or other user, checked each time the program is about to run.

A turn runs as the machine's user, so anything that user can write a turn
can replace: `~/.local/bin`, Homebrew's prefix, `/Applications` (writable
by the admin group, which the user is in), and the caches under
`/var/folders` that Apple's `/usr/bin` shims (`git` among them, through
`xcrun`) read to find the real program. A program that passes this check
can be replaced only by root.

Imports only the standard library.
"""

import os
import stat
from pathlib import Path

# The real git: the Command Line Tools' copy, which sits under root-owned
# directories no one else can write. Xcode's copy sits under
# `/Applications`, which the admin group can write, and `/usr/bin/git` is
# the `xcrun` shim, which resolves the real one through a per-user cache a
# turn can poison; neither passes `require_git`. On Linux (the verification
# VM) the distribution's `/usr/bin/git` is a real install.
GIT_CANDIDATES = ("/Library/Developer/CommandLineTools/usr/bin/git", "/usr/bin/git")
SANDBOX_EXEC = "/usr/bin/sandbox-exec"
PS = "/bin/ps"
SECURITY = "/usr/bin/security"
# Apple's `container` (core/container.py), from its signed package: the CLI
# and the programs it starts as launchd services.
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


def untrusted(path: str | Path) -> str | None:
    """Why the program or directory at `path` may not be trusted outside the
    sandbox, or None when it is root's alone. Both the path as given and the
    path it resolves to are judged, each with every directory above it:
    every entry owned by root, and none but a symlink writable by its group
    or by others (a symlink's own mode bits mean nothing; the directory
    holding it decides who can replace it)."""
    path = Path(path)
    if not path.is_absolute():
        return f"{path} is not an absolute path"
    real = Path(os.path.realpath(path))
    for start in dict.fromkeys((path, real)):
        for p in (start, *start.parents):
            try:
                st = os.lstat(p)
            except OSError as exc:
                return f"{p}: {exc.strerror}"
            if st.st_uid != 0:
                return f"{p} is not owned by root"
            if not stat.S_ISLNK(st.st_mode) and st.st_mode & 0o022:
                return f"{p} is writable by its group or by others"
    return None


def require(path: str | Path) -> str:
    """The path, if `untrusted` finds nothing; otherwise `Untrusted`."""
    why = untrusted(path)
    if why is not None:
        raise Untrusted(f"will not run {path} outside the sandbox: {why}")
    return str(path)


def git() -> str | None:
    """The first git among `GIT_CANDIDATES` that `require_git` accepts, or
    None."""
    for c in GIT_CANDIDATES:
        try:
            return require_git(c)
        except Untrusted:
            continue
    return None


def require_git(path: str | Path | None) -> str:
    """A git the kernel may run: a real install, with its exec path
    (`../libexec/git-core`, where git finds its own subcommands) beside it,
    and both passing `untrusted`. Apple's `/usr/bin/git` has no exec path
    beside it, because it is the `xcrun` shim that finds the real git
    through a per-user cache, so it is refused however its file is owned."""
    if not path:
        raise Untrusted(
            "no trusted git: install the Command Line Tools, or set VALOR_GIT to a root-owned git"
        )
    require(path)
    libexec = Path(path).parent.parent / "libexec" / "git-core"
    if not libexec.is_dir():
        raise Untrusted(f"will not run {path}: no exec path at {libexec}, so it is not a real git install")
    require(libexec)
    return str(path)
