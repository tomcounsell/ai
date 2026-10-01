"""`push_branch` (`act`): push one commit of a task's workspace to one branch
of the workspace's `origin`.

The request names the branch as its target and the commit as `head_sha` in
its payload, so the digest Tom approves binds both. The push carries
`<head_sha>:refs/heads/<branch>` and never `--force`: a branch that moved
under the broker is Tom's to read, not the broker's to overwrite. The
performer runs in the kernel's process, outside the turn's sandbox, which is
what lets it write a remote the turn cannot. `lookup` asks the remote what
the branch holds, which is how a dangling intent is reconciled.

The workspace's git config and hooks are the turn's to write, so nothing
they name runs or authenticates here: hooks, the fsmonitor, and the remote's
receive and upload programs are pinned, and the push carries no credential
helper, no SSH command, and no terminal prompt.
"""

import os
import subprocess
from pathlib import Path

ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_SSH_COMMAND": "false"}


class PushBranch:
    action_type = "push_branch"
    effect_class = "act"

    def __init__(self, workspace: str | Path, remote: str = "origin"):
        self.workspace = Path(workspace)
        self.remote = remote

    def _git(self, *args: str) -> subprocess.CompletedProcess:
        pinned = [
            "-c", "core.hooksPath=/dev/null",
            "-c", "core.fsmonitor=false",
            "-c", "credential.helper=",
            "-c", "core.sshCommand=false",
        ]  # fmt: skip
        return subprocess.run(
            ["git", "-C", str(self.workspace), *pinned, *args],
            capture_output=True,
            text=True,
            check=False,
            env=ENV,
        )

    def perform(self, action, key: str) -> dict:
        branch, sha = action.target, action.payload["head_sha"]
        if self._git("check-ref-format", "--branch", branch).returncode != 0:
            raise ValueError(f"branch name {branch!r} is malformed")
        if self._git("cat-file", "-e", f"{sha}^{{commit}}").returncode != 0:
            raise ValueError(f"{sha} is not a commit in {self.workspace}")
        pushed = self._git(
            "push",
            "--no-verify",
            "--receive-pack=git-receive-pack",
            self.remote,
            f"{sha}:refs/heads/{branch}",
        )
        if pushed.returncode != 0:
            raise RuntimeError(pushed.stderr.strip())
        return self._where(branch, sha)

    def lookup(self, action, key: str) -> dict | None:
        branch, sha = action.target, action.payload.get("head_sha")
        listed = self._git("ls-remote", "--upload-pack=git-upload-pack", self.remote, f"refs/heads/{branch}")
        if listed.returncode == 0 and listed.stdout.split()[:1] == [sha]:
            return self._where(branch, sha)
        return None

    def _where(self, branch: str, sha: str) -> dict:
        url = self._git("remote", "get-url", "--push", self.remote).stdout.strip()
        return {"remote": url, "branch": branch, "sha": sha}
