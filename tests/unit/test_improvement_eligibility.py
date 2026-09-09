"""Tests for the open-source eligibility guard (#3255, lane 2b).

Charter §7 draws one line: any provider may see open-source work, and client
work stays on the subscriptions. ``is_open_source`` is the guard on that line,
so its failure paths are the product rather than an edge case. Every one of
them is tested separately: a single blanket "error returns False" case would
let a typo in the JSON key pass as a caught timeout.

Returning True by mistake is how private client context reaches a foreign
provider. Returning False by mistake costs an experiment. The tests are shaped
around that asymmetry.
"""

from __future__ import annotations

import json
import subprocess
from unittest.mock import patch

import pytest

from tools import improvement_eligibility
from tools.improvement_eligibility import _clear_cache, is_open_source

CONFIG = {
    "projects": {
        "open-thing": {"github": {"org": "tomcounsell", "repo": "ai"}},
        "client-thing": {"github": {"org": "acme", "repo": "private-app"}},
        "no-github": {"name": "a project with no github block"},
        "no-repo": {"github": {"org": "tomcounsell"}},
        "no-org": {"github": {"repo": "ai"}},
    }
}


@pytest.fixture(autouse=True)
def clear_cache():
    _clear_cache()
    yield
    _clear_cache()


@pytest.fixture(autouse=True)
def config():
    with patch.object(improvement_eligibility, "load_config", return_value=CONFIG):
        yield


def _completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["gh"], returncode=returncode, stdout=stdout, stderr="")


def _gh(**kwargs):
    """Patch the subprocess the guard shells out with."""
    return patch.object(improvement_eligibility.subprocess, "run", **kwargs)


class TestDeterminateAnswers:
    def test_a_public_repository_is_open_source(self):
        with _gh(return_value=_completed(json.dumps({"visibility": "PUBLIC"}))):
            assert is_open_source("open-thing") is True

    def test_a_private_repository_is_not(self):
        with _gh(return_value=_completed(json.dumps({"visibility": "PRIVATE"}))):
            assert is_open_source("client-thing") is False

    def test_an_internal_repository_is_not(self):
        with _gh(return_value=_completed(json.dumps({"visibility": "INTERNAL"}))):
            assert is_open_source("client-thing") is False

    def test_the_comparison_tolerates_case_and_whitespace(self):
        """`gh` answers uppercase today; the guard must not depend on that."""
        with _gh(return_value=_completed(json.dumps({"visibility": " public "}))):
            assert is_open_source("open-thing") is True


class TestConfigurationFailsClosed:
    @pytest.mark.parametrize(
        "project_key",
        ["", "unknown-project", "no-github", "no-repo", "no-org"],
        ids=["empty", "unknown", "no-github-block", "missing-repo", "missing-org"],
    )
    def test_an_unresolvable_project_is_not_open_source(self, project_key):
        with _gh(return_value=_completed(json.dumps({"visibility": "PUBLIC"}))) as run:
            assert is_open_source(project_key) is False
        assert run.call_count == 0, "an unresolvable project must not reach the network"


class TestSubprocessFailsClosed:
    def test_a_non_zero_exit_is_not_open_source(self):
        with _gh(return_value=_completed("", returncode=1)):
            assert is_open_source("open-thing") is False

    def test_a_timeout_is_not_open_source(self):
        with _gh(side_effect=subprocess.TimeoutExpired(cmd="gh", timeout=10)):
            assert is_open_source("open-thing") is False

    def test_a_missing_gh_binary_is_not_open_source(self):
        with _gh(side_effect=FileNotFoundError("gh")):
            assert is_open_source("open-thing") is False

    def test_unparseable_json_is_not_open_source(self):
        with _gh(return_value=_completed("not json at all")):
            assert is_open_source("open-thing") is False

    def test_empty_output_is_not_open_source(self):
        with _gh(return_value=_completed("")):
            assert is_open_source("open-thing") is False

    def test_an_absent_visibility_key_is_not_open_source(self):
        with _gh(return_value=_completed(json.dumps({"name": "ai"}))):
            assert is_open_source("open-thing") is False


class TestRepositoryIsPassedPositionally:
    """`GH_REPO` is set process-wide and `gh` reads it before cwd.

    A bare `gh repo view --json visibility` would answer about whatever
    `GH_REPO` names, exit 0, and look healthy. The correct and incorrect
    versions differ by one argument, so only a test can tell them apart.
    """

    def test_the_argv_carries_the_resolved_repository(self):
        with _gh(return_value=_completed(json.dumps({"visibility": "PRIVATE"}))) as run:
            is_open_source("client-thing")

        argv = run.call_args[0][0]
        assert "acme/private-app" in argv

    def test_a_decoy_gh_repo_does_not_make_a_private_project_open_source(self, monkeypatch):
        monkeypatch.setenv("GH_REPO", "tomcounsell/ai")

        with _gh(return_value=_completed(json.dumps({"visibility": "PRIVATE"}))) as run:
            assert is_open_source("client-thing") is False

        assert "acme/private-app" in run.call_args[0][0]


#: The four shapes `tools/improvement_eligibility.py` treats as indeterminate
#: rather than a determinate "private": a timeout, a non-zero exit (the most
#: common real-world outage: a `gh` auth failure), empty stdout, and stdout
#: that fails to parse as JSON. None of them may pin False for the cache TTL.
INDETERMINATE_OUTCOMES = [
    pytest.param({"side_effect": subprocess.TimeoutExpired(cmd="gh", timeout=10)}, id="timeout"),
    pytest.param({"return_value": _completed("", returncode=1)}, id="non-zero-exit"),
    pytest.param({"return_value": _completed("")}, id="empty-stdout"),
    pytest.param({"return_value": _completed("not json")}, id="unparseable-json"),
]


class TestCache:
    def test_a_second_call_issues_no_subprocess(self):
        with _gh(return_value=_completed(json.dumps({"visibility": "PUBLIC"}))) as run:
            assert is_open_source("open-thing") is True
            assert is_open_source("open-thing") is True

        assert run.call_count == 1

    def test_an_expired_entry_is_re_resolved(self):
        with _gh(return_value=_completed(json.dumps({"visibility": "PUBLIC"}))) as run:
            assert is_open_source("open-thing", ttl_seconds=0) is True
            assert is_open_source("open-thing", ttl_seconds=0) is True

        assert run.call_count == 2

    def test_the_cache_does_not_confuse_two_projects(self):
        answers = {
            "tomcounsell/ai": json.dumps({"visibility": "PUBLIC"}),
            "acme/private-app": json.dumps({"visibility": "PRIVATE"}),
        }

        def fake_run(argv, **kwargs):
            repo = next(a for a in argv if "/" in a)
            return _completed(answers[repo])

        with _gh(side_effect=fake_run):
            assert is_open_source("open-thing") is True
            assert is_open_source("client-thing") is False

    @pytest.mark.parametrize("outcome", INDETERMINATE_OUTCOMES)
    def test_an_indeterminate_failure_is_not_cached(self, outcome):
        """A transient `gh` outage must not pin False for the whole TTL.

        Each of the four indeterminate shapes must be followed by a fresh
        subprocess call, not a cached False. Caching False on the non-zero-exit
        branch specifically is the outage shape a `gh` auth failure produces,
        and it is the one the mutation check in the review bit on.
        """
        with _gh(**outcome):
            assert is_open_source("open-thing") is False

        with _gh(return_value=_completed(json.dumps({"visibility": "PUBLIC"}))) as run:
            assert is_open_source("open-thing") is True
        assert run.call_count == 1, "the indeterminate outcome must not have been cached"
