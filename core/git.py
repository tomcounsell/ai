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
  directories only;
- reads no global or system config (`GIT_CONFIG_GLOBAL=/dev/null`,
  `GIT_CONFIG_NOSYSTEM=1`) and inherits no other `GIT_*` variable;
- ignores replace refs and grafts (`GIT_NO_REPLACE_OBJECTS=1`,
  `GIT_GRAFT_FILE=/dev/null`), and the commit-graph and multi-pack-index
  files (`core.commitGraph=false`, `core.multiPackIndex=false`), all of
  which a turn can write and none of which is config, so none can change
  what history the kernel reads;
- drops every `DYLD_*` variable, so no library is injected into git;
- runs git in its own process group with no time limit: git runs until
  it exits. Started under a watch (`interruptible()`: provisioning, a run
  starting its services, and every `threaded` caller, such as a perform),
  an interrupt of the watch kills the group, git and everything it
  started; a cancelled `threaded` caller (a stop, or an interrupt of the
  kernel's loop) interrupts its watch. Git writes its output to files
  the kernel holds, not pipes, so the kernel waits on git itself and
  never on a program git started. Inside a perform git holds the
  effect's lock (`core.performing`), which every process git starts
  inherits, so `broker.reconcile` waits until the last of them has exited;
- pins hooks, the fsmonitor, the credential helper, the SSH command, the
  proxy command, the askpass program, the global attributes file, automatic
  gc, the `ext::` transport, and push's tag following, submodule recursion,
  and signing off on its command line, which overrides the repository's
  config; a push also passes `--no-follow-tags --no-recurse-submodules
  --no-signed`, so it sends exactly the one commit the request names;
- on a call carrying the GitHub credential, also pins
  `http.followRedirects=false` and an empty `http.proxy`, at the general
  scope and the URL's own (`credential_pins`), so the header reaches the
  granted URL and nothing else whatever the repository's config says;
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
no git facts beyond its origin URL and branch (`origin`, which names whose
a failure is), and no push until the turn removes the key.

Imports the standard library, `core.binaries`, `core.performing`, and
`core.settings`.
"""

import asyncio
import contextlib
import contextvars
import functools
import hashlib
import io
import json
import os
import re
import signal
import stat
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core import binaries, performing
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


def credential_pins(url: str) -> list[str]:
    """Pinned on every call that carries the GitHub credential to `url`, on
    the command line, which outranks the repository's config and the header
    file, at both the general and the URL's own scope (a key scoped to the
    URL beats a general one wherever it is set): no redirect is followed and
    no proxy is used, so the header reaches the granted URL and nothing
    else. `url` is one `targets.url_ok` accepted, so it holds no `=`."""
    pins = []
    for key, value in (("followRedirects", "false"), ("proxy", "")):
        pins += ["-c", f"http.{key}={value}", "-c", f"http.{url}.{key}={value}"]
    return pins


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


class Interrupted(GitError):
    def __init__(self):
        super().__init__("interrupted")


class Interruptible:
    """Work whose holder can end it: every process started through `start`
    runs in its own process group with no time limit, and `interrupt()`
    ends them all (TERM, `reap_grace_s`, then KILL; git removes its lock
    files on TERM) and refuses any later start. One lock spans the flag,
    the start, and the record, so no process starts unseen."""

    def __init__(self):
        self._lock = threading.Lock()
        self._groups: set[int] = set()
        self.interrupted = False

    def start(self, argv: list[str], **kwargs) -> subprocess.Popen:
        with self._lock:
            if self.interrupted:
                raise Interrupted()
            proc = subprocess.Popen(argv, start_new_session=True, **kwargs)
            self._groups.add(proc.pid)
            return proc

    def finished(self, proc: subprocess.Popen) -> None:
        with self._lock:
            self._groups.discard(proc.pid)

    def interrupt(self) -> None:
        with self._lock:
            self.interrupted = True
            groups = set(self._groups)
        for sig in (signal.SIGTERM, signal.SIGKILL):
            for group in groups:
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.killpg(group, sig)
            if sig == signal.SIGTERM and groups:
                time.sleep(settings.reap_grace_s)
                with self._lock:
                    groups &= self._groups


_WATCH: contextvars.ContextVar[Interruptible | None] = contextvars.ContextVar("git_watch", default=None)


@contextlib.contextmanager
def interruptible():
    """Run the block's git calls (and whatever else starts through the
    watch, `watch()`) under one `Interruptible`. `asyncio.to_thread` copies
    the context, so a block that starts a thread covers it."""
    held = Interruptible()
    token = _WATCH.set(held)
    try:
        yield held
    finally:
        _WATCH.reset(token)


@contextlib.contextmanager
def uninterrupted():
    """A call that must run even after an interrupt (removing a ref the
    interrupted work added, stopping the services it started)."""
    token = _WATCH.set(None)
    try:
        yield
    finally:
        _WATCH.reset(token)


def watch() -> Interruptible | None:
    return _WATCH.get()


_MARK: contextvars.ContextVar[str | None] = contextvars.ContextVar("git_mark", default=None)


@contextlib.contextmanager
def marked(mark: str):
    """Give the block's trusted git calls the turn mark `mark`
    (`VALOR_TURN=<mark>` in git's environment, under any explicit
    `extra_env`), which every program git starts inherits, so `runs.reap`
    finds them after the kernel that started them died."""
    token = _MARK.set(mark)
    try:
        yield
    finally:
        _MARK.reset(token)


def start(argv: list[str], **kwargs) -> subprocess.Popen:
    """A process in its own process group, recorded on the current watch
    when there is one (`interruptible()`)."""
    held = _WATCH.get()
    if held is not None:
        return held.start(argv, **kwargs)
    return subprocess.Popen(argv, start_new_session=True, **kwargs)


def binary() -> str:
    """The trusted git's path, checked now."""
    try:
        return binaries.require_git(settings.git_bin)
    except binaries.Untrusted as exc:
        raise GitError(str(exc)) from None


def _git(
    workspace: str | Path,
    *args: str,
    text: bool = True,
    extra_env: dict[str, str] | None = None,
    prefix: list[str] | None = None,
) -> subprocess.CompletedProcess:
    """The trusted git (`settings.git_bin`, checked each call), never
    whichever `git` comes first on a PATH, in its own process group, run
    until it exits, started through `start` so an interrupt of the current
    watch (`interruptible()`, `threaded`) kills the group. Inside a perform,
    git gets the effect's lock descriptor (`core.performing`), which every
    process it starts inherits. Its output goes to files the kernel holds
    (`output_file`), not pipes, so the call ends when git exits, even while
    a program git started still holds them. `prefix` runs it under a
    sandbox (`sandbox-exec ... -f <profile>`). Inside `marked(mark)` its
    environment carries the mark."""
    git_bin = binary()
    held = _WATCH.get()
    mark = {} if _MARK.get() is None else {"VALOR_TURN": _MARK.get()}
    lock = performing.held()
    argv = [*(prefix or []), git_bin, "-C", str(workspace), *PINNED, *args]
    with output_file() as out, output_file() as err:
        proc = start(
            argv,
            stdout=out,
            stderr=err,
            env={**env(), **mark, **(extra_env or {})},
            pass_fds=() if lock is None else (lock,),
        )
        try:
            proc.wait()
        except BaseException:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, signal.SIGKILL)  # git and everything it started
            proc.wait()
            raise
        finally:
            if held is not None:
                held.finished(proc)
        if held is not None and held.interrupted:
            raise Interrupted()
        stdout, stderr = read_output(out, text), read_output(err, text)
    return subprocess.CompletedProcess(proc.args, proc.returncode, stdout, stderr)


def output_dir() -> Path:
    """Where `output_file` makes its files, and `dirty` its index: `output/` in the effect lock
    directory (`settings.performing_dir`), made by the kernel with mode
    0700. A file has a path there between its creation and its unlink, and
    every task profile denies this directory (`workspace.kernel_paths`),
    so no turn, setup command, or service can open, list, or truncate one;
    the temp directory is open to all of them. It moves with
    `VALOR_PERFORMING_DIR`, so the test suite writes nothing under the
    kernel key directory."""
    d = Path(settings.performing_dir)
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    (d / "output").mkdir(mode=0o700, exist_ok=True)
    return d / "output"


def output_file():
    """An unlinked file the kernel holds for a child's output, in place of a
    pipe: waiting on the child returns when it exits, whatever its
    descendants (one that left its process group with `setsid`, say) still
    hold, and the kernel then reads the file (`read_output`). It is made in
    `output_dir`, which no task profile can reach."""
    return tempfile.TemporaryFile(prefix="valor-output-", dir=output_dir())


def read_output(f, text: bool = False) -> str | bytes:
    """What `f` (from `output_file`) holds now, read by position so a
    descendant still writing to it keeps its own offset; decoded as
    `subprocess` decodes text output when `text`."""
    size = os.fstat(f.fileno()).st_size
    parts, at = [], 0
    while at < size:
        part = os.pread(f.fileno(), size - at, at)
        if not part:
            break
        parts.append(part)
        at += len(part)
    data = b"".join(parts)
    return io.TextIOWrapper(io.BytesIO(data)).read() if text else data


async def threaded(fn, *args, **kwargs):
    """`fn(*args, **kwargs)` in a worker thread under its own watch
    (`interruptible()`) and, inside a perform, holding the effect's lock
    (`performing.in_thread`). Cancelling the caller (a stop, or an
    interrupt of the kernel's loop) interrupts the watch, which kills every
    process the thread started and refuses any it would start next; the
    cancel is raised once the thread has returned."""
    with interruptible() as held:
        job = asyncio.ensure_future(performing.in_thread(functools.partial(fn, *args, **kwargs)))
        try:
            await asyncio.wait({job})  # waits without cancelling the job
        except asyncio.CancelledError:
            await interrupt(held, job)
            raise
        return job.result()


async def interrupt(held: Interruptible, job: asyncio.Future) -> None:
    """Interrupt `held` and wait for both the interrupt and `job` (the
    thread doing the work) to finish; a further cancel meanwhile waits for
    the same. The job's own error is read and dropped: the interrupt is
    what the caller raises."""
    stop = asyncio.ensure_future(asyncio.to_thread(held.interrupt))
    while not (stop.done() and job.done()):
        try:
            await asyncio.wait({stop, job})
        except asyncio.CancelledError:
            asyncio.current_task().uncancel()
    if not job.cancelled():
        job.exception()


def hostile(
    workspace: str | Path, profile: str | Path | None = None, mark: str = "valor-git-config"
) -> list[str]:
    """Every key in the workspace's local or worktree config (includes
    followed) that the kernel will not run git under, named without its
    value. With `profile`, the
    read runs under that sandbox profile, marked `mark`, so an include
    naming a file the profile denies fails the read."""
    prefix = (
        [binaries.require(binaries.SANDBOX_EXEC), "-D", "GATEWAY_PORT=1", "-D", f"VALOR_TURN={mark}",
         "-f", str(profile)]
        if profile is not None
        else None
    )  # fmt: skip
    listed = _git(workspace, "config", "--list", "--show-scope", "--includes", prefix=prefix)
    if listed.returncode != 0:  # unreadable config is refused, never assumed clean
        return [f"the config could not be read: {listed.stderr.strip()}"]
    found = []
    for line in listed.stdout.splitlines():
        scope, _, entry = line.partition("\t")
        if scope not in ("local", "worktree"):
            continue
        name = entry.split("=", 1)[0]
        key = name.lower()
        if key.startswith(HOSTILE_PREFIXES) or key.endswith(HOSTILE_SUFFIXES):
            found.append(f"{scope}: {name}")  # the key, never the value the turn set
    return found


def run(
    workspace: str | Path,
    *args: str,
    text: bool = True,
    extra_env: dict[str, str] | None = None,
    credential: Path | None = None,
    url: str | None = None,
) -> subprocess.CompletedProcess:
    """One git call in the workspace, refused before it runs when the
    workspace's config is hostile. With `credential`, the per-call config
    file `core.credentials.header_file` wrote, git reads it as its global
    config, which carries the merge's pinned header for `url`, and the call
    also passes `credential_pins(url)`."""
    found = hostile(workspace)
    if found:
        raise GitError(
            "the workspace's git config names what the kernel will not run (a program, an include, "
            "a push destination or push option, or a transport setting; every `push.*` and `http.*` "
            "key is refused): " + "; ".join(found)
        )
    pins = []
    if credential is not None:
        if url is None:
            raise ValueError("a call carrying the credential names its URL")
        pins = credential_pins(url)
        extra_env = {**(extra_env or {}), "GIT_CONFIG_GLOBAL": str(credential)}
    return _git(workspace, *pins, *args, text=text, extra_env=extra_env)


def trusted(
    cwd: str | Path,
    *args: str,
    extra_env: dict[str, str] | None = None,
    strip: bool = True,
    text: bool = True,
) -> Any:
    """One git call in a repository only the kernel writes (its cache, a
    task's mirror and bare origin, a checkout it is making), with no hostile
    check: no turn can have written its config. Raises `GitError` on a
    non-zero exit. `strip=False` keeps the output whole, as a file's text
    whose line numbers count; `text=False` returns bytes, for names git
    does not require to be UTF-8."""
    done = _git(cwd, *args, text=text, extra_env=extra_env)
    if done.returncode != 0:
        raise GitError(f"git {' '.join(args[:3])}: {_text(done.stderr).strip()}")
    return done.stdout.strip() if strip else done.stdout


def out(
    workspace: str | Path,
    *args: str,
    text: bool = True,
    extra_env: dict[str, str] | None = None,
    credential: Path | None = None,
    url: str | None = None,
) -> Any:
    done = run(workspace, *args, text=text, extra_env=extra_env, credential=credential, url=url)
    if done.returncode != 0:
        named = args[: args.index("--")] if "--" in args else args  # never the pathspecs
        raise GitError(f"git {' '.join(named)}: {_text(done.stderr).strip()}")
    return done.stdout.strip()


def _text(output: str | bytes) -> str:
    return output if isinstance(output, str) else output.decode(errors="replace")


def _plain_dir(path: str | Path) -> bool:
    """A directory, not a link to one."""
    try:
        return stat.S_ISDIR(os.lstat(path).st_mode)
    except OSError:
        return False


def is_repo(workspace: str | Path | None) -> bool:  # raises GitError when no trusted git exists
    """Whether the directory is a git repository; reads nothing the turn
    controls beyond what `git rev-parse` needs to find it."""
    return (
        bool(workspace)
        and _plain_dir(workspace)
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


def has_commit(repo: str | Path, sha: str) -> bool:
    """Whether a repository only the kernel writes holds `sha` as a commit."""
    return _git(repo, "cat-file", "-e", f"{sha}^{{commit}}").returncode == 0


def ancestry(repo: str | Path, older: str, newer: str) -> tuple[bool | None, str | None]:
    """Whether `older` is an ancestor of `newer` in a repository only the
    kernel writes: True, False, or None with git's message when git could
    not say (`merge-base --is-ancestor` exits 1 for "not an ancestor" and
    anything else for an error)."""
    done = _git(repo, "merge-base", "--is-ancestor", older, newer)
    if done.returncode in (0, 1):
        return done.returncode == 0, None
    return None, _text(done.stderr).strip()


def merges_between(workspace: str | Path, older: str, newer: str) -> list[str]:
    return out(workspace, "rev-list", "--merges", f"{older}..{newer}").split()


def diff_paths(workspace: str | Path, older: str, newer: str) -> list[str]:
    """Every path changed between two commits. Renames count as a delete and
    an add, so the old path is listed too."""
    return out(
        workspace, "diff", "--no-ext-diff", "--no-textconv", "--no-renames", "--name-only", older, newer
    ).splitlines()


def dirty(workspace: str | Path, path: str | None = None) -> list[str]:
    """Uncommitted and untracked paths (`.valor/` is excluded at setup), or
    only those at `path`, matched literally. Git quotes a path holding a space
    or a character outside ASCII, so a caller asking about one path asks git
    rather than matching the lines.

    The working tree is compared with HEAD through a fresh index the kernel
    reads from HEAD in a directory of its own under `output_dir`, which no
    task profile can reach, never the turn's index: there
    an assume-unchanged or skip-worktree bit, cached stat data that matches
    an edited file, or an untracked cache would each make `status` report
    nothing, and the turn can write all of them. The fresh index caches no
    stat data, so every tracked file's content is compared. `core.fileMode`
    is pinned on, so a mode change the turn's config would hide still
    counts.

    Git compares content after its filters, and the turn can set those
    from config, `.git/info/attributes`, or an untracked `.gitattributes`:
    `core.autocrlf` or the `text`/`eol` attributes make a CRLF-only edit
    compare equal, the `ident` attribute collapses text inside `$Id$`, and
    `working-tree-encoding` makes other bytes compare equal. `core.symlinks`
    set false in the turn's config hides a type change: a link replaced by a
    file holding its target's text compares equal. So working-tree bytes
    that differ can still read as clean. What the kernel records does not
    change: the plan's digest is the committed blob's and the candidate is
    the commit."""
    only = ["--", f":(literal){path}"] if path is not None else []
    commit = head(workspace)
    with tempfile.TemporaryDirectory(prefix="valor-index-", dir=output_dir()) as tmp:
        index = {"GIT_INDEX_FILE": os.path.join(tmp, "index")}
        out(workspace, "read-tree", commit or "--empty", extra_env=index)
        return out(
            workspace,
            "-c",
            "core.fileMode=true",
            "--no-optional-locks",
            "status",
            "--porcelain",
            "--untracked-files=all",
            "--ignore-submodules=all",
            *only,
            extra_env=index,
        ).splitlines()


def push_url(workspace: str | Path, remote: str = "origin") -> str:
    """The remote's push URL, a local path made absolute (git reports a
    relative path as it was written)."""
    return _absolute(workspace, out(workspace, "remote", "get-url", "--push", remote))


def _absolute(workspace: str | Path, url: str) -> str:
    if "://" not in url and not re.match(r"^[^/:]+@[^/:]+:", url):
        url = str((Path(workspace) / url).resolve())
    return url


def origin(workspace: str | Path) -> tuple[str, str | None]:
    """`remote.origin.url` as written (a local path made absolute) and the
    checked-out branch, read with no hostile check: `config --get` and
    `symbolic-ref` only read files and run nothing. For naming whose a
    refused workspace is, never for running git in it. Raises `GitError`
    when there is no origin URL."""
    done = _git(workspace, "config", "--get", "remote.origin.url")
    if done.returncode != 0 or not done.stdout.strip():
        raise GitError(f"no origin URL: {_text(done.stderr).strip()}")
    head = _git(workspace, "symbolic-ref", "--quiet", "--short", "HEAD")
    return _absolute(workspace, done.stdout.strip()), head.stdout.strip() or None


def remote_head(workspace: str | Path, url: str, credential: Path | None = None) -> str | None:
    """The branch a remote's HEAD names, or None when it names none (an
    unborn HEAD lists nothing). A remote that does not answer raises
    `GitError` carrying git's stderr."""
    listed = run(
        workspace,
        "ls-remote",
        "--symref",
        "--upload-pack=git-upload-pack",
        url,
        "HEAD",
        credential=credential,
        url=url,
    )
    if listed.returncode != 0:
        raise GitError(listed.stderr.strip())
    for line in listed.stdout.splitlines():
        if line.startswith("ref: refs/heads/") and line.endswith("\tHEAD"):
            return line[len("ref: refs/heads/") : -len("\tHEAD")]
    return None


def push(workspace: str | Path, url: str, sha: str, branch_name: str, credential: Path | None = None) -> None:
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
        credential=credential,
        url=url,
    )


def remote_sha(
    workspace: str | Path, url: str, branch_name: str, credential: Path | None = None
) -> str | None:
    """The branch's tip at the remote, or None when the remote answered and
    holds no such branch. A remote that does not answer raises `GitError`:
    unreachable is not the same as absent."""
    listed = run(
        workspace,
        "ls-remote",
        "--upload-pack=git-upload-pack",
        url,
        f"refs/heads/{branch_name}",
        credential=credential,
        url=url,
    )
    if listed.returncode != 0:
        raise GitError(f"ls-remote {url}: {listed.stderr.strip()}")
    fields = listed.stdout.split()
    return fields[0] if fields else None


LOOKUP_REF = "refs/valor-kernel/lookup"


def holds(
    workspace: str | Path, url: str, branch_name: str, sha: str, credential: Path | None = None
) -> bool | None:
    """Whether the remote branch holds `sha`: its tip, or a commit the tip
    descends from (the branch may have moved on since). None when the
    remote holds no such branch. The tip is fetched into the kernel's own
    ref (`LOOKUP_REF`), so ancestry is read from objects the remote sent
    now. Raises `GitError` when the remote cannot be read."""
    tip = remote_sha(workspace, url, branch_name, credential)
    if tip is None:
        return None
    if tip == sha:
        return True
    out(
        workspace,
        "fetch", "--no-tags", "--no-recurse-submodules", "--no-write-fetch-head",
        "--upload-pack=git-upload-pack", url, f"+refs/heads/{branch_name}:{LOOKUP_REF}",
        credential=credential,
        url=url,
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
    removed: tuple[str, ...] = ()

    def id(self) -> str:
        """The instance id: the path, the hunk header's function context, and
        the added lines, without line numbers, so the same hunk on a later
        candidate keeps its id and a changed one gets a new id. A hunk that
        only removes lines digests its removed lines in their place."""
        lines = [list(self.added)] if self.added else [[], list(self.removed)]
        body = json.dumps([self.path, self.context, *lines], separators=(",", ":"))
        return hashlib.sha256(body.encode()).hexdigest()[:16]


def hunks(workspace: str | Path, older: str, newer: str, path: str) -> list[Hunk]:
    """The hunks of one path's diff between two commits, as git computes
    them (three lines of context, so the header carries function context).
    `path` is read as a file name only (`--literal-pathspecs`), never as a
    pathspec with magic or a glob, and a directory (`.`, `hooks`) is no
    file of the diff, so it has no hunks."""
    diff = ("--literal-pathspecs", "diff", "--no-ext-diff", "--no-textconv", "--no-renames")
    named = [n for n in out(workspace, *diff, "--name-only", "-z", older, newer, "--", path).split("\0") if n]
    if named != [path]:
        return []
    text = out(workspace, *diff, "--no-color", older, newer, "--", path)
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
                "removed": [],
            }
            found.append(current)  # type: ignore[arg-type]
        elif current is not None and line.startswith("+") and not line.startswith("+++"):
            current["added"].append(line[1:])
        elif current is not None and line.startswith("-"):
            current["removed"].append(line[1:])
    return [
        Hunk(path, h["start"], h["length"], h["context"], tuple(h["added"]), tuple(h["removed"]))  # type: ignore[index]
        for h in found
    ]


def hunk_at(workspace: str | Path, older: str, newer: str, path: str, line: int) -> Hunk | None:
    """The hunk whose new range holds `line`. A hunk that only removes lines
    has its context lines as its new range, and a deleted or emptied file's
    one hunk is read at line 0."""
    for h in hunks(workspace, older, newer, path):
        if h.start <= line < h.start + max(h.length, 1):
            return h
    return None
