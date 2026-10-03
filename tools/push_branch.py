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
branch, the head, and the candidate, so Tom's approval binds all four, and
`Merge` is built from the task's Brief, so it refuses a payload naming any
other URL or branch. A merge to a remote (not a local path) lands only on a
pair in the merge-target list (`core.targets`), never on the branch the
remote's `HEAD` names, read just before the push. A task with a kernel
mirror pushes from the mirror with the GitHub credential, a per-call config
file (`core.credentials.header_file`); no other push carries it.

Every method is a coroutine; git runs in a worker thread
(`core.git.threaded`) until it exits, and cancelling the coroutine (a stop,
or an interrupt of the kernel) kills the git it runs. A perform's thread
holds the effect's lock file and hands it to every git it runs
(`core.performing.in_thread`), so `broker.reconcile` reads the remote only
after the push was reaped.
"""

from pathlib import Path

from core import broker, credentials, git, targets


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

    async def refuse(self, conn, action) -> str | None:
        """Checked at request and again at release, before the intent."""
        if self.protected and action.target == self.protected:
            return f"{self.protected} is the task's target branch; only the merge lands there"
        return await git.threaded(self._rewrites)

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

    async def perform(self, action, key: str) -> dict:
        return await git.threaded(self._perform, action)

    async def lookup(self, action, key: str) -> dict | None:
        """Present (the remote branch holds the commit, at its tip or below
        it): the result. Absent: None. Unknown (the workspace is refused or
        the remote cannot be read): `broker.Unknown`."""
        return await git.threaded(self._lookup, action)

    def _perform(self, action) -> dict:
        if self.protected and action.target == self.protected:
            raise ValueError(f"{self.protected} is the task's target branch; only the merge lands there")
        said = self._rewrites()
        if said:
            raise ValueError(said)
        url, branch = self.destination(action)
        git.push(self.workspace, url, action.payload["head_sha"], branch)
        return {"remote": url, "branch": branch, "sha": action.payload["head_sha"]}

    def _lookup(self, action) -> dict | None:
        try:
            url, branch = self.destination(action)
            sha = action.payload.get("head_sha")
            if sha and git.holds(self.workspace, url, branch, sha):
                return {"remote": url, "branch": branch, "sha": sha}
            return None
        except git.GitError as exc:
            raise broker.Unknown(str(exc)) from None


ROTATE = "GitHub refused the credential; rotate it"
# Git's own phrases for a refused credential, never a bare status code: a
# rejected push prints full SHAs, and a SHA can hold "401" or "403".
_REFUSED = (
    "The requested URL returned error: 401",
    "The requested URL returned error: 403",
    "could not read Username",
    "Authentication failed",
    "Invalid username or password",
)


class Merge(PushBranch):
    """The kernel's merge for one task, built from its Brief: `url` and
    `branch` are the Brief's origin URL and target branch, `repo` the
    kernel mirror (or the workspace, for a `--workspace` task), and
    `credential` the GitHub key file, given only with a mirror. `loopback`
    lets the header file name a loopback http URL, for tests only."""

    action_type = "merge"
    usage = None

    def __init__(
        self,
        repo: str | Path,
        *,
        url: str | None,
        branch: str | None,
        credential: str | Path | None = None,
        loopback: bool = False,
    ):
        self.workspace = Path(repo)
        self.url = url
        self.branch = branch
        self.credential = credential
        self.loopback = loopback
        self.protected = None

    async def refuse(self, conn, action) -> str | None:
        p = action.payload
        if not p.get("url") or p.get("target_branch") != action.target or not p.get("head_sha"):
            return "a merge names its URL, target branch, and head"
        if p["url"] != self.url or p["target_branch"] != self.branch:
            return (
                f"the merge names {p['url']} {p['target_branch']}; the task's Brief names "
                f"{self.url} {self.branch}"
            )
        said = await targets.check(conn, p["url"], p["target_branch"])
        if said:
            return said
        return await git.threaded(self._rewrites)

    def destination(self, action) -> tuple[str, str]:
        return action.payload["url"], action.payload["target_branch"]

    def _with_credential(self, url: str, call):
        """Run `call(credential_path_or_None)`: with a fresh header file for
        a remote target of a task with a mirror, else with none."""
        if self.credential is None or targets.local(url):
            return call(None)
        with credentials.header_file(self.credential, url, loopback=self.loopback) as path:
            return call(path)

    def _perform(self, action) -> dict:
        said = self._rewrites()
        if said:
            raise ValueError(said)
        url, branch = self.destination(action)
        if url != self.url or branch != self.branch:
            raise ValueError(
                f"the merge names {url} {branch}; the task's Brief names {self.url} {self.branch}"
            )
        sha = action.payload["head_sha"]
        try:
            if not targets.local(url):
                self._with_credential(url, lambda c: self._not_default(url, branch, c))
            self._with_credential(url, lambda c: git.push(self.workspace, url, sha, branch, c))
        except git.GitError as exc:
            if self.credential is not None and any(m in str(exc) for m in _REFUSED):
                raise git.GitError(f"{ROTATE}: {exc}") from None
            raise
        return {"remote": url, "branch": branch, "sha": sha}

    def _not_default(self, url: str, branch: str, credential: Path | None) -> None:
        """Refuse the branch the remote's HEAD names, read now."""
        try:
            named = git.remote_head(self.workspace, url, credential)
        except git.GitError as exc:
            raise ValueError(f"cannot read the remote's HEAD: {exc}") from None
        if named is None:
            raise ValueError("the remote's HEAD names no branch")
        if named == branch:
            raise ValueError(f"{branch} is the default branch of {url}; a merge never lands there")

    def _lookup(self, action) -> dict | None:
        try:
            url, branch = self.destination(action)
            sha = action.payload.get("head_sha")
            if sha and self._with_credential(url, lambda c: git.holds(self.workspace, url, branch, sha, c)):
                return {"remote": url, "branch": branch, "sha": sha}
            return None
        except (git.GitError, credentials.CredentialError) as exc:
            raise broker.Unknown(str(exc)) from None
