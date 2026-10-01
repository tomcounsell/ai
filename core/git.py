"""Git, read and pushed by the kernel, in a workspace a turn can write.

A turn owns its workspace's `.git/config` and hooks, so nothing they name may
run or authenticate here: every call pins hooks, the fsmonitor, the
credential helper, and the SSH command off, and carries no terminal prompt.
`rewrites` names the config a turn could use to send a push somewhere other
than the URL the kernel recorded at start.

Imports only the standard library.
"""

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_SSH_COMMAND": "false"}
PINNED = [
    "-c", "core.hooksPath=/dev/null",
    "-c", "core.fsmonitor=false",
    "-c", "credential.helper=",
    "-c", "core.sshCommand=false",
]  # fmt: skip


class GitError(RuntimeError):
    pass


def run(workspace: str | Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(workspace), *PINNED, *args],
        capture_output=True,
        text=True,
        check=False,
        env=ENV,
    )


def out(workspace: str | Path, *args: str) -> str:
    done = run(workspace, *args)
    if done.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {done.stderr.strip()}")
    return done.stdout.strip()


def is_repo(workspace: str | Path | None) -> bool:
    return bool(workspace) and run(workspace, "rev-parse", "--git-dir").returncode == 0


def head(workspace: str | Path) -> str | None:
    done = run(workspace, "rev-parse", "--verify", "--quiet", "HEAD^{commit}")
    return done.stdout.strip() or None


def branch(workspace: str | Path) -> str | None:
    """The checked-out branch, or None on a detached HEAD."""
    done = run(workspace, "symbolic-ref", "--quiet", "--short", "HEAD")
    return done.stdout.strip() or None


def show(workspace: str | Path, rev: str, path: str) -> bytes | None:
    done = subprocess.run(
        ["git", "-C", str(workspace), *PINNED, "show", f"{rev}:{path}"],
        capture_output=True,
        check=False,
        env=ENV,
    )
    return done.stdout if done.returncode == 0 else None


def is_ancestor(workspace: str | Path, older: str, newer: str) -> bool:
    return run(workspace, "merge-base", "--is-ancestor", older, newer).returncode == 0


def merges_between(workspace: str | Path, older: str, newer: str) -> list[str]:
    return out(workspace, "rev-list", "--merges", f"{older}..{newer}").split()


def diff_paths(workspace: str | Path, older: str, newer: str) -> list[str]:
    """Every path changed between two commits. Renames count as a delete and
    an add, so the old path is listed too."""
    return out(workspace, "diff", "--no-ext-diff", "--no-renames", "--name-only", older, newer).splitlines()


def dirty(workspace: str | Path) -> list[str]:
    """Uncommitted and untracked paths (`.valor/` is excluded at setup)."""
    return out(workspace, "status", "--porcelain", "--untracked-files=all").splitlines()


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


def rewrites(workspace: str | Path) -> list[str]:
    """Config in the workspace's own scopes (local, worktree) that could send
    a push elsewhere or pull other config in: any `include.*` or
    `includeIf.*`, any `url.*` rule, any `remote.*.pushurl`. Rewrites in
    Tom's global config are his and are not listed."""
    listed = out(workspace, "config", "--list", "--show-scope", "--includes")
    found = []
    for line in listed.splitlines():
        scope, _, entry = line.partition("\t")
        key = entry.split("=", 1)[0].lower()
        if scope not in ("local", "worktree"):
            continue
        if key.startswith(("include.", "includeif.", "url.")) or (
            key.startswith("remote.") and key.endswith(".pushurl")
        ):
            found.append(f"{scope}: {entry}")
    return found


def push(workspace: str | Path, url: str, sha: str, branch_name: str) -> None:
    """Push one commit to one branch at an explicit URL, never with force,
    refusing when the workspace's config could rewrite where it lands."""
    found = rewrites(workspace)
    if found:
        raise GitError(f"the workspace's config could redirect the push: {'; '.join(found)}")
    if run(workspace, "check-ref-format", "--branch", branch_name).returncode != 0:
        raise ValueError(f"branch name {branch_name!r} is malformed")
    if run(workspace, "cat-file", "-e", f"{sha}^{{commit}}").returncode != 0:
        raise ValueError(f"{sha} is not a commit in {workspace}")
    out(
        workspace,
        "push",
        "--no-verify",
        "--receive-pack=git-receive-pack",
        url,
        f"{sha}:refs/heads/{branch_name}",
    )


def remote_sha(workspace: str | Path, url: str, branch_name: str) -> str | None:
    listed = run(workspace, "ls-remote", "--upload-pack=git-upload-pack", url, f"refs/heads/{branch_name}")
    fields = listed.stdout.split()
    return fields[0] if listed.returncode == 0 and fields else None


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
    text = out(workspace, "diff", "--no-ext-diff", "--no-renames", "--no-color", older, newer, "--", path)
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
