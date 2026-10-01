"""Pushing a task's commits: `push_branch` for a turn's own branch, and
`merge`, the kernel's push of a passed candidate onto the target branch.

Both are `act` and push to the origin URL the kernel recorded when the task
started (`Brief.origin_url`), never to whatever the workspace's config names
now: the turn owns that config. A push is refused when the workspace's own
config holds any include, URL rewrite, push URL, or anything else
`core.git.hostile` names, since git would apply a rewrite even to an
explicit URL and the kernel runs no program the turn chose. The push carries
`<head_sha>:refs/heads/<branch>` and never `--force`: a branch that moved
under the broker is Tom's to read, not the broker's to overwrite. The
performers run in the kernel's process, outside the turn's sandbox, which is
what lets them write a remote the turn cannot. `lookup` asks the remote what
the branch holds, which is how a dangling intent is reconciled.

`push_branch` names its branch as its target and its commit as `head_sha`,
so the digest Tom approves binds both, and it refuses the task's target
branch: the only way onto that branch is the merge and its predicate.
`merge` is offered to no turn; its payload carries the URL, the target
branch, the head, and the candidate, so Tom's approval binds all four.
"""

from pathlib import Path

from core import broker, git


class PushBranch:
    action_type = "push_branch"
    effect_class = "act"
    usage = (
        '`push_branch`: target the branch name, payload `{"head_sha": "<full sha>"}`; pushes that '
        "commit to that branch of the task's origin once Tom approves. Not the branch merges land on."
    )

    def __init__(self, workspace: str | Path, url: str | None = None, protected: str | None = None):
        self.workspace = Path(workspace)
        self.url = url  # None for a task started before origin URLs were recorded: read at push time
        self.protected = protected

    def refuse(self, action) -> str | None:
        """Checked at request and again at release, before the intent."""
        if self.protected and action.target == self.protected:
            return f"{self.protected} is the task's target branch; only the merge lands there"
        return self._rewrites()

    def _rewrites(self) -> str | None:
        try:
            found = git.hostile(self.workspace)
        except git.GitError as exc:
            return str(exc)
        return (
            f"the workspace's git config could redirect the push or run a program: {'; '.join(found)}"
            if found
            else None
        )

    def destination(self, action) -> tuple[str, str]:
        return self.url or git.push_url(self.workspace), action.target

    def perform(self, action, key: str) -> dict:
        said = self.refuse(action)
        if said:
            raise ValueError(said)
        url, branch = self.destination(action)
        git.push(self.workspace, url, action.payload["head_sha"], branch)
        return {"remote": url, "branch": branch, "sha": action.payload["head_sha"]}

    def lookup(self, action, key: str) -> dict | None:
        """Present (the remote branch holds the commit, at its tip or below
        it): the result. Absent: None. Unknown (the workspace is refused or
        the remote cannot be read): `broker.Unknown`."""
        try:
            url, branch = self.destination(action)
            sha = action.payload.get("head_sha")
            if sha and git.holds(self.workspace, url, branch, sha):
                return {"remote": url, "branch": branch, "sha": sha}
            return None
        except git.GitError as exc:
            raise broker.Unknown(str(exc)) from None


class Merge(PushBranch):
    action_type = "merge"
    usage = None

    def __init__(self, workspace: str | Path):
        self.workspace = Path(workspace)
        self.protected = None

    def refuse(self, action) -> str | None:
        p = action.payload
        if not p.get("url") or p.get("target_branch") != action.target or not p.get("head_sha"):
            return "a merge names its URL, target branch, and head"
        return self._rewrites()

    def destination(self, action) -> tuple[str, str]:
        return action.payload["url"], action.payload["target_branch"]
