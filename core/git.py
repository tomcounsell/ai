"""Git, read and pushed by the kernel, in a workspace a turn can write.

The kernel runs outside the turn's sandbox and holds the database
credential, so no git call here may run a program the turn chose. A turn
owns its workspace's `.git/config` (and any file it includes), `.git/hooks`,
`.git/info/attributes`, and the committed `.gitattributes`. Attributes only
name drivers; a driver's program comes from config. So every call:

- runs a root-owned git install (`settings.git_bin`, checked by
  `core.binaries.require_git` before every call: the Command Line Tools'
  git by default, never Apple's `/usr/bin/git` shim, which finds the real
  git through a per-user cache a turn can poison) with a PATH of system
  directories only and a time limit (`settings.git_timeout_s`);
- reads no global or system config (`GIT_CONFIG_GLOBAL=/dev/null`,
  `GIT_CONFIG_NOSYSTEM=1`) and inherits no other `GIT_*` variable;
- ignores replace refs and grafts (`GIT_NO_REPLACE_OBJECTS=1`,
  `GIT_GRAFT_FILE=/dev/null`), and the commit-graph and multi-pack-index
  files (`core.commitGraph=false`, `core.multiPackIndex=false`), all of
  which a turn can write and none of which is config, so none can change
  what history the kernel reads;
- drops every `DYLD_*` variable, so no library is injected into git;
- runs git in its own process group, killed whole when the call outlives
  its time: the smaller of `git_timeout_s` and what is left of the
  `deadline` the caller set (a performer sets one for its whole perform,
  so a push and the calls around it share one limit);
- pins hooks, the fsmonitor, the credential helper, the SSH command, the
  proxy command, the askpass program, the global attributes file, automatic
  gc, the `ext::` transport, and push's tag following, submodule recursion,
  and signing off on its command line, which overrides the repository's
  config; a push also passes `--no-follow-tags --no-recurse-submodules
  --no-signed`, so it sends exactly the one commit Tom's approval binds;
- passes `--no-textconv` and `--no-ext-diff` to every diff, and ignores
  submodules in `status`;
- and first refuses the workspace outright (`GitError`) when its local or
  worktree config holds any key that can name a program, redirect where a
  push lands, or pull other config in (`HOSTILE`, checked by `hostile`):
  filter drivers, diff and merge drivers, the fsmonitor, hooks path,
  pager, editor, askpass, SSH and proxy commands, credential helpers,
  transport and upload or receive programs, URL rewrites and push URLs,
  any `push.*` or `hook.*` setting, the alternate refs command, the
  interactive diff filter, partial clone, any `*.cmd`, aliases, submodule
  settings, `core.worktree`, and every include. Config that cannot be read
  is refused too.

Reading config (`git config --list`) runs nothing: it only reads files. A
workspace whose config the kernel refuses gets no candidate, no instance,
no git facts, and no push until the turn removes the key.

Imports the standard library and `core.settings`.
"""

import contextlib
import contextvars
import hashlib
import json
import os
import re
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from core import binaries
from core.settings import settings

# The PATH every kernel git call runs with: system directories only, none a
# turn can write (a turn can write ~/.local/bin, which Tom's own PATH puts
# first).
PATH = "/usr/bin:/bin:/usr/sbin:/sbin"


def env() -> dict[str, str]:
    """The environment of a kernel git call, built when the call is made:
    the kernel's own, less every inherited `GIT_*` variable (`GIT_DIR`,
    `GIT_CONFIG_PARAMETERS`, `GIT_EXTERNAL_DIFF`, ...), with a system-only
    PATH and git's prompts, global config, and system config off."""
    return {
        **{
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("GIT_", "DYLD_")) and k not in ("DEVELOPER_DIR", "xcrun_db", "SDKROOT")
        },
        "PATH": PATH,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_SSH_COMMAND": "false",
        "GIT_ASKPASS": "/usr/bin/false",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_PAGER": "cat",
        # A turn owns `.git/refs/replace/` and `.git/info/grafts`, which are
        # not config: without these a replace ref or a graft could make a
        # code commit read as docs only, or a merge read as landed.
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_GRAFT_FILE": "/dev/null",
    }


PINNED = [
    "-c", "core.hooksPath=/dev/null",
    "-c", "core.fsmonitor=false",
    "-c", "credential.helper=",
    "-c", "core.sshCommand=false",
    "-c", "core.gitProxy=",
    "-c", "core.askPass=/usr/bin/false",
    "-c", "core.attributesFile=/dev/null",
    "-c", "core.pager=cat",
    "-c", "gc.auto=0",
    "-c", "maintenance.auto=false",
    "-c", "protocol.ext.allow=never",
    "-c", "push.followTags=false",
    "-c", "push.recurseSubmodules=no",
    "-c", "push.gpgSign=false",
    "-c", "core.commitGraph=false",
    "-c", "core.multiPackIndex=false",
    "-c", "submodule.recurse=false",
    "-c", "advice.graftFileDeprecated=false",
]  # fmt: skip

# Local or worktree config keys (lowercased) the kernel will not run git
# under: by prefix, or by a `remote.<name>.` / `diff.<name>.` /
# `merge.<name>.` suffix.
HOSTILE_PREFIXES = (
    "include.", "includeif.", "filter.", "url.", "alias.", "submodule.", "credential",
    "core.fsmonitor", "core.hookspath", "core.pager", "core.editor", "core.askpass",
    "core.sshcommand", "core.gitproxy", "core.worktree", "core.attributesfile",
    "pager.", "sequence.editor", "protocol.", "uploadpack.", "receive.", "gpg.",
    "diff.external", "ssh.", "push.", "hook.", "http.", "core.alternaterefscommand",
    "interactive.difffilter", "extensions.partialclone",
)  # fmt: skip
HOSTILE_SUFFIXES = (
    ".textconv", ".command", ".driver", ".pushurl", ".uploadpack", ".receivepack", ".proxy",
    ".vcs", ".helper", ".cmd",
)  # fmt: skip


class GitError(RuntimeError):
    pass


def binary() -> str:
    """The trusted git's path, checked now."""
    try:
        return binaries.require_git(settings.git_bin)
    except binaries.Untrusted as exc:
        raise GitError(str(exc)) from None


def _git(
    workspace: str | Path, *args: str, text: bool = True, extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    """The trusted git (`settings.git_bin`, checked each call), never
    whichever `git` comes first on a PATH, in its own process group, killed
    whole when it outlives its limit (the smaller of `git_timeout_s` and
    what is left of the caller's `deadline`)."""
    git_bin = binary()
    limit = settings.git_timeout_s
    ends = _DEADLINE.get()
    if ends is not None:
        limit = min(limit, ends - time.monotonic())
        if limit <= 0:
            raise GitError(f"git {' '.join(args[:2])}: the deadline for this perform has passed")
    proc = subprocess.Popen(
        [git_bin, "-C", str(workspace), *PINNED, *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=text,
        env={**env(), **(extra_env or {})},
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=limit)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)  # git and everything it started
        proc.communicate()
        raise GitError(f"git {' '.join(args[:2])} did not finish in {limit:.0f}s") from None
    return subprocess.CompletedProcess(proc.args, proc.returncode, stdout, stderr)


_DEADLINE: contextvars.ContextVar[float | None] = contextvars.ContextVar("git_deadline", default=None)


def remaining(limit: float) -> float:
    """`limit`, or less when the caller's `deadline` ends sooner; a passed
    deadline raises `GitError`. For a git run outside `_git` (the kernel
    mirror's bounded fetch)."""
    ends = _DEADLINE.get()
    if ends is None:
        return limit
    left = ends - time.monotonic()
    if left <= 0:
        raise GitError("the deadline for this perform has passed")
    return min(limit, left)


@contextlib.contextmanager
def deadline(seconds: float):
    """One limit for every git call inside the block, together. Nested
    blocks keep the earlier deadline."""
    ends = time.monotonic() + seconds
    outer = _DEADLINE.get()
    token = _DEADLINE.set(ends if outer is None else min(outer, ends))
    try:
        yield
    finally:
        _DEADLINE.reset(token)


def hostile(workspace: str | Path) -> list[str]:
    """Every key in the workspace's local or worktree config (includes
    followed) that the kernel will not run git under."""
    listed = _git(workspace, "config", "--list", "--show-scope", "--includes")
    if listed.returncode != 0:  # unreadable config is refused, never assumed clean
        return [f"the config could not be read: {listed.stderr.strip()[:200]}"]
    found = []
    for line in listed.stdout.splitlines():
        scope, _, entry = line.partition("\t")
        if scope not in ("local", "worktree"):
            continue
        key = entry.split("=", 1)[0].lower()
        if key.startswith(HOSTILE_PREFIXES) or key.endswith(HOSTILE_SUFFIXES):
            found.append(f"{scope}: {entry}")
    return found


def run(workspace: str | Path, *args: str, text: bool = True) -> subprocess.CompletedProcess:
    """One git call in the workspace, refused before it runs when the
    workspace's config is hostile."""
    found = hostile(workspace)
    if found:
        raise GitError(
            "the workspace's git config names what the kernel will not run (a program, an include, "
            "a push destination or push option, or a transport setting; every `push.*` and `http.*` "
            "key is refused): " + "; ".join(found)
        )
    return _git(workspace, *args, text=text)


def trusted(cwd: str | Path, *args: str, extra_env: dict[str, str] | None = None) -> str:
    """One git call in a repository only the kernel writes (its cache, a
    task's mirror and bare origin, a checkout it is making), with no hostile
    check: no turn can have written its config. Raises `GitError` on a
    non-zero exit."""
    done = _git(cwd, *args, extra_env=extra_env)
    if done.returncode != 0:
        raise GitError(f"git {' '.join(args[:3])}: {done.stderr.strip()[:300]}")
    return done.stdout.strip()


def out(workspace: str | Path, *args: str) -> str:
    done = run(workspace, *args)
    if done.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {done.stderr.strip()}")
    return done.stdout.strip()


def is_repo(workspace: str | Path | None) -> bool:  # raises GitError when no trusted git exists
    """Whether the directory is a git repository; reads nothing the turn
    controls beyond what `git rev-parse` needs to find it."""
    return (
        bool(workspace)
        and Path(workspace).is_dir()
        and _git(workspace, "rev-parse", "--git-dir").returncode == 0
    )


def head(workspace: str | Path) -> str | None:
    done = run(workspace, "rev-parse", "--verify", "--quiet", "HEAD^{commit}")
    return done.stdout.strip() or None


def branch(workspace: str | Path) -> str | None:
    """The checked-out branch, or None on a detached HEAD."""
    done = run(workspace, "symbolic-ref", "--quiet", "--short", "HEAD")
    return done.stdout.strip() or None


def show(workspace: str | Path, rev: str, path: str) -> bytes | None:
    """A file's bytes at a commit (`cat-file blob`: no filter, no textconv)."""
    done = run(workspace, "cat-file", "blob", f"{rev}:{path}", text=False)
    return done.stdout if done.returncode == 0 else None


def is_ancestor(workspace: str | Path, older: str, newer: str) -> bool:
    return run(workspace, "merge-base", "--is-ancestor", older, newer).returncode == 0


def merges_between(workspace: str | Path, older: str, newer: str) -> list[str]:
    return out(workspace, "rev-list", "--merges", f"{older}..{newer}").split()


def diff_paths(workspace: str | Path, older: str, newer: str) -> list[str]:
    """Every path changed between two commits. Renames count as a delete and
    an add, so the old path is listed too."""
    return out(
        workspace, "diff", "--no-ext-diff", "--no-textconv", "--no-renames", "--name-only", older, newer
    ).splitlines()


def dirty(workspace: str | Path) -> list[str]:
    """Uncommitted and untracked paths (`.valor/` is excluded at setup)."""
    return out(
        workspace,
        "--no-optional-locks",
        "status",
        "--porcelain",
        "--untracked-files=all",
        "--ignore-submodules=all",
    ).splitlines()


def push_url(workspace: str | Path, remote: str = "origin") -> str:
    """The remote's push URL, a local path made absolute (git reports a
    relative path as it was written)."""
    url = out(workspace, "remote", "get-url", "--push", remote)
    if "://" not in url and not re.match(r"^[^/:]+@[^/:]+:", url):
        url = str((Path(workspace) / url).resolve())
    return url


def remote_head(workspace: str | Path, url: str) -> str | None:
    """The branch a remote's HEAD names, or None when it names none (an
    unborn HEAD lists nothing)."""
    listed = run(workspace, "ls-remote", "--symref", "--upload-pack=git-upload-pack", url, "HEAD")
    for line in listed.stdout.splitlines():
        if line.startswith("ref: refs/heads/") and line.endswith("\tHEAD"):
            return line[len("ref: refs/heads/") : -len("\tHEAD")]
    return None


def push(workspace: str | Path, url: str, sha: str, branch_name: str) -> None:
    """Push one commit to one branch at an explicit URL, never with force.
    `run` refuses a workspace whose config could rewrite where it lands."""
    if run(workspace, "check-ref-format", "--branch", branch_name).returncode != 0:
        raise ValueError(f"branch name {branch_name!r} is malformed")
    if run(workspace, "cat-file", "-e", f"{sha}^{{commit}}").returncode != 0:
        raise ValueError(f"{sha} is not a commit in {workspace}")
    out(
        workspace,
        "push",
        "--no-verify",
        "--no-follow-tags",
        "--no-recurse-submodules",
        "--no-signed",
        "--receive-pack=git-receive-pack",
        url,
        f"{sha}:refs/heads/{branch_name}",
    )


def remote_sha(workspace: str | Path, url: str, branch_name: str) -> str | None:
    """The branch's tip at the remote, or None when the remote answered and
    holds no such branch. A remote that does not answer raises `GitError`:
    unreachable is not the same as absent."""
    listed = run(workspace, "ls-remote", "--upload-pack=git-upload-pack", url, f"refs/heads/{branch_name}")
    if listed.returncode != 0:
        raise GitError(f"ls-remote {url}: {listed.stderr.strip()[:200]}")
    fields = listed.stdout.split()
    return fields[0] if fields else None


LOOKUP_REF = "refs/valor-kernel/lookup"


def holds(workspace: str | Path, url: str, branch_name: str, sha: str) -> bool | None:
    """Whether the remote branch holds `sha`: its tip, or a commit the tip
    descends from (the branch may have moved on since). None when the
    remote holds no such branch. The tip is fetched into the kernel's own
    ref (`LOOKUP_REF`), so ancestry is read from objects the remote sent
    now. Raises `GitError` when the remote cannot be read."""
    tip = remote_sha(workspace, url, branch_name)
    if tip is None:
        return None
    if tip == sha:
        return True
    out(
        workspace,
        "fetch", "--no-tags", "--no-recurse-submodules", "--no-write-fetch-head",
        "--upload-pack=git-upload-pack", url, f"+refs/heads/{branch_name}:{LOOKUP_REF}",
    )  # fmt: skip
    return is_ancestor(workspace, sha, LOOKUP_REF)


# -- governance instances --------------------------------------------------------

HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@ ?(.*)$")


@dataclass(frozen=True)
class Hunk:
    path: str
    start: int
    length: int
    context: str
    added: tuple[str, ...]

    def id(self) -> str:
        """The instance id: the path, the hunk header's function context, and
        the added lines, without line numbers, so the same hunk on a later
        candidate keeps its id and a changed one gets a new id."""
        body = json.dumps([self.path, self.context, list(self.added)], separators=(",", ":"))
        return hashlib.sha256(body.encode()).hexdigest()[:16]


def hunks(workspace: str | Path, older: str, newer: str, path: str) -> list[Hunk]:
    """The hunks of one path's diff between two commits, as git computes
    them (three lines of context, so the header carries function context)."""
    text = out(
        workspace,
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--no-renames",
        "--no-color",
        older,
        newer,
        "--",
        path,
    )
    found: list[Hunk] = []
    current = None
    for line in text.splitlines():
        m = HUNK.match(line)
        if m:
            current = {
                "start": int(m.group(1)),
                "length": int(m.group(2) if m.group(2) is not None else 1),
                "context": m.group(3).strip(),
                "added": [],
            }
            found.append(current)  # type: ignore[arg-type]
        elif current is not None and line.startswith("+") and not line.startswith("+++"):
            current["added"].append(line[1:])
    return [Hunk(path, h["start"], h["length"], h["context"], tuple(h["added"])) for h in found]  # type: ignore[index]


def hunk_at(workspace: str | Path, older: str, newer: str, path: str, line: int) -> Hunk | None:
    """The hunk with added lines whose new range holds `line`."""
    for h in hunks(workspace, older, newer, path):
        if h.added and h.start <= line < h.start + max(h.length, 1):
            return h
    return None
