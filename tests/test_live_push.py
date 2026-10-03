"""The merge's GitHub credential against GitHub itself: `Merge`, from a
kernel-owned bare repository, pushes a commit to `valor/push-check` of
`https://github.com/tomcounsell/ai.git` with the real key file, and
`holds` confirms it. The commit's parent is that branch's head, or, when
the branch does not exist, the head of the branch this checkout is on (the
rebuild branch, in the kernel checkout).

Tom grants `valor/push-check` with `python -m core merge-target add`
before it runs; the branch stays on GitHub afterwards.

Live spend: none (no model call). Runs only when `VALOR_LIVE=1` and
`VALOR_LIVE_GITHUB=1`.
"""

import asyncio
import os
import subprocess
from pathlib import Path

import pytest

from core import broker, git
from core.settings import settings
from tools.push_branch import Merge

pytestmark = [
    pytest.mark.spend(usd=0),
    pytest.mark.skipif(
        os.environ.get("VALOR_LIVE") != "1" or os.environ.get("VALOR_LIVE_GITHUB") != "1",
        reason="the live push needs VALOR_LIVE=1 and VALOR_LIVE_GITHUB=1",
    ),
]

ROOT = Path(__file__).resolve().parent.parent
URL = "https://github.com/tomcounsell/ai.git"
BRANCH = "valor/push-check"


def sh(cwd, *args) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def test_a_merge_lands_on_github_with_the_kernel_s_credential(tmp_path):
    parent_branch = BRANCH
    if not sh(ROOT, "ls-remote", URL, f"refs/heads/{BRANCH}"):
        parent_branch = sh(ROOT, "branch", "--show-current")
    repo = tmp_path / "mirror.git"
    sh(tmp_path, "init", "-q", "--bare", str(repo))
    sh(
        repo,
        "fetch",
        "-q",
        "--no-tags",
        "--depth",
        "1",
        URL,
        f"+refs/heads/{parent_branch}:refs/heads/parent",
    )
    parent = sh(repo, "rev-parse", "parent")
    sha = sh(repo, "-c", "user.name=Valor", "-c", "user.email=valor@example.com", "commit-tree",
             f"{parent}^{{tree}}", "-p", parent, "-m", "Live push check")  # fmt: skip
    merge = Merge(repo, url=URL, branch=BRANCH, credential=settings.github_keyfile)
    action = broker.Action("merge", BRANCH, {"url": URL, "target_branch": BRANCH, "head_sha": sha})
    landed = asyncio.run(merge.perform(action, "live"))
    assert landed == {"remote": URL, "branch": BRANCH, "sha": sha}
    assert asyncio.run(merge.lookup(action, "live")) is not None
    assert git.holds(repo, URL, BRANCH, sha) is True
    assert list(Path(settings.github_keyfile).parent.glob("github-push-*.gitconfig")) == []
