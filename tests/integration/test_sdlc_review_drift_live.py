"""Live known-bad / known-good proof for the #3228 docs-only drift tolerance.

The unit tests stub the classifier. These drive the REAL GitHub compare API
against immutable commit pairs on ``tomcounsell/ai`` main, through the merge
gate's own freshness leg, so the tolerance is proven RED on a code change after
review and not only GREEN on the happy path. Only the verdict read and the PR
head read are stubbed; the drift classification is real.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

import tools.merge_predicate as mp
from tools.sdlc_review_drift import classify_head_drift

REPO = "tomcounsell/ai"
REPO_ROOT = Path(__file__).resolve().parents[2]

# 56a36836: "Docs: drop dangling plan link" touches one docs/features/*.md file.
DOCS_BASE = "8c8f9abb58f501f50cdc05b27ebceb205a8737c6"
DOCS_HEAD = "56a3683699af00e6d86a7db02e251b33e01f8cd4"
# c4853db5: "Settle the release verify past a process's boot window" touches
# bridge/update.py, config/settings.py, ... alongside docs.
CODE_BASE = "5fe7034ca1bc79fe832e034f6c8ce5499df2e34a"
CODE_HEAD = "c4853db57bb997eb2f99d9f7b2e9d92eae58de15"


def _gh_authenticated() -> bool:
    if not shutil.which("gh"):
        return False
    proc = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True, timeout=20)
    return proc.returncode == 0


pytestmark = pytest.mark.skipif(not _gh_authenticated(), reason="gh CLI not authenticated")


def test_docs_only_descendant_is_tolerated():
    assert classify_head_drift(DOCS_BASE, DOCS_HEAD, REPO) == "docs_only"


def test_code_change_after_review_is_code():
    """Known-bad: a commit that touches source after the reviewed SHA."""
    assert classify_head_drift(CODE_BASE, CODE_HEAD, REPO) == "code"


def test_head_behind_reviewed_sha_is_code():
    """Known-bad: the head no longer contains the reviewed commit."""
    assert classify_head_drift(CODE_HEAD, CODE_BASE, REPO) == "code"


def test_code_somewhere_in_a_multi_commit_range_is_code():
    """Known-bad: the docs commit on top does not launder the code commit below."""
    assert classify_head_drift(CODE_BASE, DOCS_HEAD, REPO) == "code"


def _freshness(monkeypatch, reviewed: str, head: str) -> tuple[list[str], list[str]]:
    monkeypatch.setattr(
        mp,
        "_run_verdict_get",
        lambda issue, root: {"verdict": "APPROVED", "head_sha": reviewed},
    )
    monkeypatch.setattr(
        mp, "_gh_latest_commit", lambda pr, root: {"sha": head, "date": "2026-09-29T00:00:00Z"}
    )
    failed: list[str] = []
    notes: list[str] = []
    mp._check_verdict_freshness(3576, 3228, REPO_ROOT, failed, notes)
    return failed, notes


def test_merge_gate_refuses_code_change_after_review(monkeypatch):
    failed, notes = _freshness(monkeypatch, CODE_BASE, CODE_HEAD)
    assert any("post-review drift classified 'code'" in f for f in failed), failed
    assert notes == []


def test_merge_gate_accepts_docs_only_drift(monkeypatch):
    failed, notes = _freshness(monkeypatch, DOCS_BASE, DOCS_HEAD)
    assert failed == []
    assert any("docs-only drift" in n for n in notes)
