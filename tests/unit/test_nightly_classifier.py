"""Tests for the nightly baseline classifier (issue #2334, shadow tier).

The centre of this file is the **falsifiability suite**: a non-stubbed
two-commit fixture repo driven through ``classify_against_baseline``'s
keyword-only injection seam, with **no monkeypatching of module globals**.
Every other classifier test stubs the spawn and therefore cannot tell a working
classifier from an inert one — one that returns all-``pre_existing`` (because it
ran against HEAD's source) or all-``inconclusive`` (because the wrapper aborted)
would pass them identically. Only the fixture tests discriminate.

The fixture's own properties are asserted rather than assumed (the
``fixture_preconditions`` guards), because each of them silently degrades the
falsifiability suite into something weaker:

- ``.git`` must be a **file** (a linked worktree), or ``pytest-clean.sh``'s
  #3033 ``.venv`` guard never fires and the fixture proves less than it appears.
- ``pyproject.toml`` must carry a literal ``[tool.pytest.ini_options]`` section,
  or the wrapper falls back to ``SCRIPT_ROOT`` and silently runs the **real**
  repo's suite.
- ``.venv/bin/pytest`` must exist, answer ``--version``, and accept both
  ``-n0`` (``pytest-xdist``) and ``--json-report`` (``pytest-json-report``), or
  every bucket is forced to ``inconclusive`` — the exact value the suite exists
  to rule out.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))

import nightly_regression_tests as nrt  # noqa: E402

from tests.unit.nightly_shadow_helpers import make_run_flags as _flags  # noqa: E402

NODE = "tests/test_thing.py::test_thing"

FIXTURE_PYPROJECT = """\
[project]
name = "nightly-classifier-fixture"
version = "0.0.0"
requires-python = ">=3.10"

[tool.pytest.ini_options]
testpaths = ["tests"]
"""

# Hashed only — provision_baseline_worktree digests uv.lock to decide whether a
# `uv sync` is needed. The fixture pre-writes a matching marker so the real
# provisioning path short-circuits without ever invoking `uv sync`, which has
# nothing to sync here.
FIXTURE_UV_LOCK = "# nightly classifier fixture lock — digested, never parsed\n"


def _run(argv: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(  # timeout-guard: allow
        argv, cwd=str(cwd), capture_output=True, text=True, timeout=180
    )


# --- the two-commit fixture repo --------------------------------------------


class _Fixture:
    def __init__(self, repo: Path, worktree: Path, bare_worktree: Path, sha_a: str, sha_b: str):
        self.repo = repo
        self.worktree = worktree
        self.bare_worktree = bare_worktree
        self.sha_a = sha_a  # the test PASSES here
        self.sha_b = sha_b  # the test FAILS here (also HEAD)


@pytest.fixture(scope="module")
def fixture_repo(tmp_path_factory: pytest.TempPathFactory) -> _Fixture:
    """A temp git repo with commit A (test passes) and commit B (test fails).

    The classifiable tree is a **linked worktree** (``git worktree add
    --detach``) so ``.git`` is a file and the #3033 guard in
    ``scripts/pytest-clean.sh`` is live against it. A sibling worktree is left
    deliberately un-provisioned for the ``.venv``-less case.

    Skips rather than fails when ``uv`` is unavailable or any provisioning step
    exits non-zero: a harness problem must not become a newly-confirmed nightly
    failure that pages a human through the very alert path this feature
    reforms.
    """
    if shutil.which("uv") is None:
        pytest.skip("uv unavailable — cannot provision the fixture venv")
    if shutil.which("git") is None:
        pytest.skip("git unavailable")

    root = tmp_path_factory.mktemp("nightly_classifier_fixture")
    repo = root / "src"
    (repo / "tests").mkdir(parents=True)

    (repo / "pyproject.toml").write_text(FIXTURE_PYPROJECT)
    (repo / "uv.lock").write_text(FIXTURE_UV_LOCK)
    # Deliberately NO .python-version: check-interpreter-pin.sh is then a no-op
    # (`[ -f "$PIN_FILE" ] || exit 0`). Adding one "for realism" would pin the
    # fixture venv to this repo's interpreter and make the test
    # environment-coupled.

    for step in (
        ["git", "init", "-q", "."],
        ["git", "config", "user.email", "fixture@example.invalid"],
        ["git", "config", "user.name", "fixture"],
        ["git", "config", "commit.gpgsign", "false"],
    ):
        if _run(step, repo).returncode != 0:
            pytest.skip(f"fixture git setup failed: {step}")

    (repo / "tests" / "test_thing.py").write_text("def test_thing():\n    assert True\n")
    _run(["git", "add", "-A"], repo)
    if _run(["git", "commit", "-q", "-m", "A"], repo).returncode != 0:
        pytest.skip("fixture commit A failed")
    sha_a = _run(["git", "rev-parse", "HEAD"], repo).stdout.strip()

    (repo / "tests" / "test_thing.py").write_text("def test_thing():\n    assert False\n")
    _run(["git", "add", "-A"], repo)
    if _run(["git", "commit", "-q", "-m", "B"], repo).returncode != 0:
        pytest.skip("fixture commit B failed")
    sha_b = _run(["git", "rev-parse", "HEAD"], repo).stdout.strip()

    worktree = root / "wt"
    if _run(["git", "worktree", "add", "--detach", "-q", str(worktree), sha_a], repo).returncode:
        pytest.skip("fixture `git worktree add` failed")

    bare_worktree = root / "wt-no-venv"
    if _run(
        ["git", "worktree", "add", "--detach", "-q", str(bare_worktree), sha_a], repo
    ).returncode:
        pytest.skip("fixture bare `git worktree add` failed")

    # `uv venv` + `uv pip install`, NOT `uv sync`: there is no real lockfile to
    # sync. `--json-report` needs pytest-json-report and `-n0` needs
    # pytest-xdist; both are dev-extra deps of the MAIN pyproject that no other
    # venv inherits, and either one missing aborts the wrapper into
    # all-`inconclusive`.
    venv = worktree / ".venv"
    if _run(["uv", "venv", "-q", str(venv)], worktree).returncode != 0:
        pytest.skip("fixture `uv venv` failed")
    install = _run(
        [
            "uv",
            "pip",
            "install",
            "-q",
            "--python",
            str(venv / "bin" / "python"),
            "pytest",
            "pytest-json-report",
            "pytest-xdist",
        ],
        worktree,
    )
    if install.returncode != 0:
        pytest.skip(f"fixture `uv pip install` failed: {install.stderr.strip()}")

    # Pre-satisfy provision_baseline_worktree's uv.lock marker so the real
    # provisioning path re-points the checkout and skips `uv sync`.
    digest = hashlib.sha256((worktree / "uv.lock").read_bytes()).hexdigest()
    (worktree / nrt.BASELINE_PROVISION_MARKER).write_text(digest + "\n")

    return _Fixture(repo, worktree, bare_worktree, sha_a, sha_b)


# --- fixture preconditions (the three guards) --------------------------------


@pytest.mark.slow
@pytest.mark.timeout(120)
def test_fixture_preconditions_git_is_a_file(fixture_repo: _Fixture) -> None:
    """A linked worktree keeps `.git` as a FILE, which is what arms #3033.

    A plain `git init` fixture has `.git` as a directory, the wrapper's
    `[ -f "$REPO_ROOT/.git" ]` guard never fires, and the "provisioning failure
    is inconclusive, never a PROJECT_DIR fallback" claim stays untested on the
    real path.
    """
    assert (fixture_repo.worktree / ".git").is_file()
    assert (fixture_repo.bare_worktree / ".git").is_file()


@pytest.mark.slow
@pytest.mark.timeout(120)
def test_fixture_preconditions_pyproject_declares_pytest_section(
    fixture_repo: _Fixture,
) -> None:
    """Without `[tool.pytest`, pytest-clean.sh silently runs the REAL repo.

    L33-39 sets `REPO_ROOT="$(pwd)"` only when cwd's pyproject.toml matches
    `^\\[tool\\.pytest`; otherwise it falls back to SCRIPT_ROOT and cds there.
    """
    assert "[tool.pytest" in (fixture_repo.worktree / "pyproject.toml").read_text()


@pytest.mark.slow
@pytest.mark.timeout(120)
def test_fixture_preconditions_venv_pytest_accepts_the_classifier_argv(
    fixture_repo: _Fixture, tmp_path: Path
) -> None:
    """The fixture interpreter must answer --version and accept -n0 --json-report.

    pytest-clean.sh L155-168 aborts unless the resolved pytest answers
    `--version`; the classifier argv then needs pytest-xdist for `-n0` and
    pytest-json-report for `--json-report`. Any of the three missing forces
    every bucket to `inconclusive` — the value the falsifiability suite exists
    to rule out.
    """
    pytest_bin = fixture_repo.worktree / ".venv" / "bin" / "pytest"
    assert pytest_bin.exists()
    assert _run([str(pytest_bin), "--version"], fixture_repo.worktree).returncode == 0

    report = tmp_path / "probe.json"
    probe = _run(
        [
            str(pytest_bin),
            NODE,
            "-n0",
            "--tb=no",
            "-q",
            "--json-report",
            f"--json-report-file={report}",
        ],
        fixture_repo.worktree,
    )
    combined = probe.stdout + probe.stderr
    assert "unrecognized arguments" not in combined, combined
    assert report.exists(), combined


# --- the falsifiability suite ------------------------------------------------


@pytest.mark.slow
@pytest.mark.timeout(120)
def test_fixture_repo_node_passing_at_baseline_is_newly_broken(
    fixture_repo: _Fixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE falsifier: passes at the baseline SHA, fails at HEAD → newly_broken.

    The real `classify_against_baseline` is called through its keyword-only
    injection seam. No module global is monkeypatched (the autouse
    `_quiet_log` fixture redirects LOG_FILE, which is pure test hygiene and
    not on any code path under test).
    """
    result = nrt.classify_against_baseline(
        [NODE],
        fixture_repo.sha_a,
        repo_root=fixture_repo.repo,
        worktree_path=fixture_repo.worktree,
        wrapper=nrt.PYTEST_CLEAN_SH,
        report_path=str(tmp_path / "baseline_report.json"),
    )

    assert result["newly_broken"] == [NODE]
    assert result["pre_existing"] == []
    assert result["inconclusive"] == []


@pytest.mark.slow
@pytest.mark.timeout(120)
def test_fixture_repo_node_failing_at_both_commits_is_pre_existing(
    fixture_repo: _Fixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The companion: failing at the baseline SHA too → pre_existing.

    Proves the classifier is not simply echoing HEAD's result — if it were,
    both this and the newly_broken case would land in the same bucket.
    """
    result = nrt.classify_against_baseline(
        [NODE],
        fixture_repo.sha_b,
        repo_root=fixture_repo.repo,
        worktree_path=fixture_repo.worktree,
        wrapper=nrt.PYTEST_CLEAN_SH,
        report_path=str(tmp_path / "baseline_report.json"),
    )

    assert result["pre_existing"] == [NODE]
    assert result["newly_broken"] == []
    assert result["inconclusive"] == []


@pytest.mark.slow
@pytest.mark.timeout(120)
def test_fixture_repo_without_venv_is_inconclusive(
    fixture_repo: _Fixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A worktree the wrapper would refuse must never yield a real bucket.

    Two independent guards make this inconclusive and both are live here: the
    fixture's `uv.lock` cannot be synced, so provisioning fails first, and even
    if it had not, pytest-clean.sh L136-146 refuses a linked worktree with no
    `.venv` of its own (asserted directly below).
    """
    assert not (fixture_repo.bare_worktree / ".venv").exists()

    wrapper_abort = _run([str(nrt.PYTEST_CLEAN_SH), NODE, "-n0", "-q"], fixture_repo.bare_worktree)
    assert wrapper_abort.returncode != 0
    assert "no .venv of its own" in wrapper_abort.stderr

    result = nrt.classify_against_baseline(
        [NODE],
        fixture_repo.sha_a,
        repo_root=fixture_repo.repo,
        worktree_path=fixture_repo.bare_worktree,
        wrapper=nrt.PYTEST_CLEAN_SH,
        report_path=str(tmp_path / "baseline_report.json"),
    )
    assert result["inconclusive"] == [NODE]
    assert result["newly_broken"] == [] and result["pre_existing"] == []


# --- stubbed provisioning ----------------------------------------------------


def _step_token(argv: list[str]) -> str:
    if argv[:2] == ["git", "worktree"]:
        return "worktree_prune" if "prune" in argv else "worktree_add"
    if "checkout" in argv:
        return "checkout"
    if argv[0] == "uv":
        return "uv_sync"
    return "other"


class _FakeProc:
    """A Popen stand-in whose ``communicate`` can time out like a hung child.

    ``pid`` is above macOS/Linux pid ceilings, so the timeout path's
    ``os.getpgid`` raises ``ProcessLookupError`` and the killpg cleanup is a
    no-op — the shape of a child that died between the timeout and the kill.
    """

    def __init__(self, argv: list[str], returncode: int, *, timeout: bool, fake=None):
        self.pid = 99999999
        self.returncode = returncode
        self._argv = argv
        self._timeout = timeout
        self._fake = fake

    def communicate(self, timeout=None):
        if self._fake is not None:
            self._fake.timeouts.append(timeout)
        if self._timeout:
            raise subprocess.TimeoutExpired(cmd=self._argv, timeout=timeout or 1)
        return ("", "provisioning step failed")

    def wait(self, timeout=None):
        return self.returncode


class _ProvisionFake:
    """A subprocess.Popen stand-in for the bounded provisioning steps."""

    def __init__(self, *, fail: set[str] | None = None, timeout: set[str] | None = None):
        self.fail = fail or set()
        self.timeout = timeout or set()
        self.calls: list[list[str]] = []
        self.kwargs: list[dict] = []
        self.timeouts: list = []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        self.kwargs.append(dict(kwargs))
        token = _step_token(list(argv))
        rc = 1 if token in self.fail else 0
        return _FakeProc(list(argv), rc, timeout=token in self.timeout, fake=self)

    @property
    def tokens(self) -> list[str]:
        return [_step_token(a) for a in self.calls]


def _ready_worktree(tmp_path: Path) -> Path:
    """A worktree directory that satisfies provision_baseline_worktree's venv check."""
    wt = tmp_path / "baseline-wt"
    (wt / ".venv" / "bin").mkdir(parents=True)
    (wt / ".venv" / "bin" / "pytest").write_text("#!/bin/sh\n")
    (wt / "uv.lock").write_text("lock\n")
    digest = hashlib.sha256((wt / "uv.lock").read_bytes()).hexdigest()
    (wt / nrt.BASELINE_PROVISION_MARKER).write_text(digest + "\n")
    # The classifier pre-filters node IDs whose test file is absent at the
    # baseline checkout; the stub tests use nodes "a::t1" / "b::t2".
    (wt / "a").write_text("")
    (wt / "b").write_text("")
    return wt


def _classify(
    tmp_path: Path,
    worktree: Path,
    *,
    nodes: list[str] | None = None,
    baseline_sha: str = "cafef00d",
    report_path: Path | None = None,
):
    return nrt.classify_against_baseline(
        nodes if nodes is not None else ["a::t1"],
        baseline_sha,
        repo_root=tmp_path / "repo-root",
        worktree_path=worktree,
        wrapper=tmp_path / "pytest-clean.sh",
        report_path=str(report_path or (tmp_path / "baseline.json")),
    )


@pytest.fixture(autouse=True)
def _quiet_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(nrt, "LOG_FILE", tmp_path / "nightly.log")


class TestProvisionFailures:
    """Every provisioning failure is `inconclusive`, never a PROJECT_DIR fallback."""

    @pytest.mark.parametrize("token", ["worktree_add", "uv_sync"])
    def test_provision_nonzero_exit_is_inconclusive(self, tmp_path: Path, token: str) -> None:
        wt = tmp_path / "absent-wt" if token == "worktree_add" else _ready_worktree(tmp_path)
        if token == "uv_sync":
            (wt / nrt.BASELINE_PROVISION_MARKER).write_text("stale-digest\n")
        fake = _ProvisionFake(fail={token})
        with (
            patch.object(nrt.subprocess, "Popen", fake),
            patch.object(nrt, "_spawn_pytest") as spawn,
        ):
            result = _classify(tmp_path, wt)
        assert result["inconclusive"] == ["a::t1"]
        assert result["newly_broken"] == [] and result["pre_existing"] == []
        spawn.assert_not_called()

    def test_provision_checkout_nonzero_exit_is_inconclusive(self, tmp_path: Path) -> None:
        """An unknown baseline SHA fails the detached checkout of an existing lane."""
        wt = _ready_worktree(tmp_path)
        fake = _ProvisionFake(fail={"checkout"})
        with (
            patch.object(nrt.subprocess, "Popen", fake),
            patch.object(nrt, "_spawn_pytest") as spawn,
        ):
            result = _classify(tmp_path, wt, baseline_sha="0badc0de")
        assert result["inconclusive"] == ["a::t1"]
        # An unknown SHA fails the checkout, and the prune-and-retry self-heal
        # (for desynced admin entries) runs before the classifier gives up.
        assert fake.tokens == ["checkout", "worktree_prune", "checkout"]
        spawn.assert_not_called()

    @pytest.mark.parametrize("token", ["worktree_add", "checkout", "uv_sync"])
    def test_provision_timeout_is_inconclusive(self, tmp_path: Path, token: str) -> None:
        """A hung provision must reach a bucket, exactly like a non-zero exit."""
        wt = tmp_path / "absent-wt" if token == "worktree_add" else _ready_worktree(tmp_path)
        if token == "uv_sync":
            (wt / nrt.BASELINE_PROVISION_MARKER).write_text("stale-digest\n")
        fake = _ProvisionFake(timeout={token})
        with (
            patch.object(nrt.subprocess, "Popen", fake),
            patch.object(nrt, "_spawn_pytest") as spawn,
        ):
            result = _classify(tmp_path, wt)
        assert result["inconclusive"] == ["a::t1"]
        spawn.assert_not_called()

    def test_provision_runs_uv_sync_in_the_worktree(self, tmp_path: Path) -> None:
        """`uv sync` must target the worktree, never the primary checkout."""
        wt = _ready_worktree(tmp_path)
        (wt / nrt.BASELINE_PROVISION_MARKER).write_text("stale-digest\n")
        fake = _ProvisionFake()

        with patch.object(nrt.subprocess, "Popen", fake):
            assert nrt.provision_baseline_worktree(
                "cafef00d", repo_root=tmp_path / "repo-root", worktree_path=wt
            )
        uv_idx = [i for i, c in enumerate(fake.calls) if c[0] == "uv"]
        assert uv_idx and fake.calls[uv_idx[0]] == ["uv", "sync"]
        assert fake.kwargs[uv_idx[0]]["cwd"] == str(wt)
        assert fake.timeouts[uv_idx[0]] == nrt.BASELINE_UV_SYNC_TIMEOUT_SECONDS

    def test_provision_skips_uv_sync_when_the_lock_has_not_moved(self, tmp_path: Path) -> None:
        """The amortization that keeps the common night near zero cost."""
        wt = _ready_worktree(tmp_path)
        fake = _ProvisionFake()
        with patch.object(nrt.subprocess, "Popen", fake):
            assert nrt.provision_baseline_worktree(
                "cafef00d", repo_root=tmp_path / "repo-root", worktree_path=wt
            )
        assert "uv_sync" not in fake.tokens

    def test_provision_unreadable_lockfile_is_inconclusive(self, tmp_path: Path) -> None:
        wt = _ready_worktree(tmp_path)
        (wt / "uv.lock").unlink()
        with (
            patch.object(nrt.subprocess, "Popen", _ProvisionFake()),
            patch.object(nrt, "_spawn_pytest") as spawn,
        ):
            result = _classify(tmp_path, wt)
        assert result["inconclusive"] == ["a::t1"]
        spawn.assert_not_called()


class TestProvisionHardening:
    """The provisioning tech-debt fixes from the #3082 review."""

    def test_provision_steps_own_a_process_group(self, tmp_path: Path) -> None:
        """start_new_session=True on every step, so killpg can reach a
        surviving uv build grandchild instead of hanging in communicate()."""
        wt = _ready_worktree(tmp_path)
        (wt / nrt.BASELINE_PROVISION_MARKER).write_text("stale-digest\n")
        fake = _ProvisionFake()
        with patch.object(nrt.subprocess, "Popen", fake):
            assert nrt.provision_baseline_worktree(
                "cafef00d", repo_root=tmp_path / "repo-root", worktree_path=wt
            )
        assert fake.calls, "at least one provisioning step must run"
        assert all(k.get("start_new_session") is True for k in fake.kwargs)

    def test_provision_prune_and_retry_recovers_a_desynced_admin_entry(
        self, tmp_path: Path
    ) -> None:
        """A registered-but-missing lane fails `git worktree add` forever until
        pruned; the classifier prunes and retries once, logging the recovery."""
        wt = tmp_path / "absent-wt"
        calls: list[list[str]] = []

        def _fake(argv, **kwargs):
            calls.append(list(argv))
            token = _step_token(list(argv))
            # First add fails (desynced admin entry); post-prune add succeeds.
            first_add_failing = token == "worktree_add" and calls.count(list(argv)) == 1
            return _FakeProc(list(argv), 1 if first_add_failing else 0, timeout=False)

        with patch.object(nrt.subprocess, "Popen", _fake):
            # The add "succeeds" without creating the directory, so the venv
            # check below still returns False — the assertion here is about
            # the recovery sequence, not end-to-end success.
            nrt.provision_baseline_worktree(
                "cafef00d", repo_root=tmp_path / "repo-root", worktree_path=wt
            )
        tokens = [_step_token(a) for a in calls]
        assert tokens[:3] == ["worktree_add", "worktree_prune", "worktree_add"]
        log_text = (tmp_path / "nightly.log").read_text()
        assert "git worktree prune" in log_text

    @pytest.mark.parametrize("bad_sha", ["-rf", "main", "HEAD~1", "", "cafe f00d"])
    def test_provision_refuses_a_non_hex_baseline_sha(self, tmp_path: Path, bad_sha: str) -> None:
        """last_run.json content never reaches git argv unvalidated. A `--`
        separator is not usable for `checkout --detach`, so shape validation is
        the guard."""
        wt = _ready_worktree(tmp_path)
        fake = _ProvisionFake()
        with patch.object(nrt.subprocess, "Popen", fake):
            assert not nrt.provision_baseline_worktree(
                bad_sha, repo_root=tmp_path / "repo-root", worktree_path=wt
            )
        assert fake.calls == []


class TestBaselineNodePrefilter:
    """Newly-ADDED failing tests must not poison the whole batch."""

    def _spawn_with_report(self, report: Path, payload: dict):
        def _spawn(argv, timeout=None, env=None, cwd=None):
            report.write_text(json.dumps(payload))
            return 1

        return _spawn

    def test_prefilter_absent_node_is_inconclusive_alone(self, tmp_path: Path) -> None:
        """A node whose test file does not exist at the baseline SHA buckets
        inconclusive by itself; the present nodes still classify."""
        wt = _ready_worktree(tmp_path)
        report = tmp_path / "baseline.json"
        payload = {"tests": [{"nodeid": "a::t1", "outcome": "passed"}]}
        captured: dict = {}

        def _spawn(argv, timeout=None, env=None, cwd=None):
            captured["argv"] = list(argv)
            report.write_text(json.dumps(payload))
            return 1

        with (
            patch.object(nrt.subprocess, "Popen", _ProvisionFake()),
            patch.object(nrt, "_spawn_pytest", side_effect=_spawn),
        ):
            result = _classify(
                tmp_path, wt, nodes=["a::t1", "tests/new_file.py::t9"], report_path=report
            )
        assert result["newly_broken"] == ["a::t1"]
        assert result["inconclusive"] == ["tests/new_file.py::t9"]
        assert "tests/new_file.py::t9" not in captured["argv"]

    def test_prefilter_all_nodes_absent_skips_the_baseline_run(self, tmp_path: Path) -> None:
        wt = _ready_worktree(tmp_path)
        with (
            patch.object(nrt.subprocess, "Popen", _ProvisionFake()),
            patch.object(nrt, "_spawn_pytest") as spawn,
        ):
            result = _classify(tmp_path, wt, nodes=["tests/new_file.py::t9"])
        assert result["inconclusive"] == ["tests/new_file.py::t9"]
        spawn.assert_not_called()

    def test_prefilter_logs_the_absent_nodes(self, tmp_path: Path) -> None:
        wt = _ready_worktree(tmp_path)
        with (
            patch.object(nrt.subprocess, "Popen", _ProvisionFake()),
            patch.object(nrt, "_spawn_pytest") as spawn,
        ):
            _classify(tmp_path, wt, nodes=["tests/new_file.py::t9"])
        spawn.assert_not_called()
        log_text = (tmp_path / "nightly.log").read_text()
        assert "no test file at baseline" in log_text
        assert "tests/new_file.py::t9" in log_text


class TestNoProjectDirFallback:
    """A failed provision must never silently classify against HEAD's source."""

    def test_no_project_dir_fallback_on_provision_failure(self, tmp_path: Path) -> None:
        wt = tmp_path / "absent-wt"
        fake = _ProvisionFake(fail={"worktree_add"})
        with (
            patch.object(nrt.subprocess, "Popen", fake),
            patch.object(nrt, "_spawn_pytest") as spawn,
        ):
            result = _classify(tmp_path, wt)
        assert result["inconclusive"] == ["a::t1"]
        # The tell of the fallback would be a spawn at all, or one at PROJECT_DIR.
        spawn.assert_not_called()
        assert not any(str(nrt.PROJECT_DIR) in str(arg) for call in fake.calls for arg in call)

    def test_no_project_dir_fallback_on_a_successful_run(self, tmp_path: Path) -> None:
        wt = _ready_worktree(tmp_path)
        report = tmp_path / "baseline.json"
        captured: dict = {}

        def _spawn(argv, timeout=None, env=None, cwd=None):
            captured["cwd"] = cwd
            report.write_text(json.dumps({"tests": [{"nodeid": "a::t1", "outcome": "passed"}]}))
            return 1

        with (
            patch.object(nrt.subprocess, "Popen", _ProvisionFake()),
            patch.object(nrt, "_spawn_pytest", side_effect=_spawn),
        ):
            result = _classify(tmp_path, wt, report_path=report)
        assert result["newly_broken"] == ["a::t1"]
        assert captured["cwd"] == wt
        assert captured["cwd"] != nrt.PROJECT_DIR


class TestReportPathIsolation:
    """The classifier owns its report file; main() re-reads the serial one AFTER."""

    def _serial_sentinel(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        serial = tmp_path / "serial.json"
        serial.write_text(json.dumps({"tests": [{"nodeid": "a::t1", "outcome": "failed"}]}))
        monkeypatch.setattr(nrt, "PYTEST_SERIAL_JSON_TMP", str(serial))
        return serial

    def test_report_path_isolation_on_a_successful_classification(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        serial = self._serial_sentinel(tmp_path, monkeypatch)
        before = serial.read_bytes()
        wt = _ready_worktree(tmp_path)
        report = tmp_path / "baseline.json"
        captured: dict = {}

        def _spawn(argv, timeout=None, env=None, cwd=None):
            captured["argv"] = list(argv)
            report.write_text(json.dumps({"tests": [{"nodeid": "a::t1", "outcome": "passed"}]}))
            return 1

        with (
            patch.object(nrt.subprocess, "Popen", _ProvisionFake()),
            patch.object(nrt, "_spawn_pytest", side_effect=_spawn),
        ):
            result = _classify(tmp_path, wt, report_path=report)

        assert result["newly_broken"] == ["a::t1"]
        assert f"--json-report-file={report}" in captured["argv"]
        assert not any(nrt.PYTEST_SERIAL_JSON_TMP in a for a in captured["argv"])
        assert not any(nrt.PYTEST_JSON_TMP in a for a in captured["argv"])
        assert serial.read_bytes() == before

    def test_report_path_isolation_on_a_failed_classification(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        serial = self._serial_sentinel(tmp_path, monkeypatch)
        before = serial.read_bytes()
        wt = _ready_worktree(tmp_path)

        def _spawn(argv, timeout=None, env=None, cwd=None):
            raise subprocess.TimeoutExpired(cmd=argv, timeout=1)

        with (
            patch.object(nrt.subprocess, "Popen", _ProvisionFake()),
            patch.object(nrt, "_spawn_pytest", side_effect=_spawn),
        ):
            result = _classify(tmp_path, wt)
        assert result["inconclusive"] == ["a::t1"]
        assert serial.read_bytes() == before

    def test_report_path_default_is_the_dedicated_baseline_path(self) -> None:
        """A distinct constant, and distinct from both existing report paths."""
        assert nrt.PYTEST_BASELINE_JSON_TMP not in (
            nrt.PYTEST_SERIAL_JSON_TMP,
            nrt.PYTEST_JSON_TMP,
        )


class TestClassifierSpawnEnv:
    def test_claim_wait_override_is_carried_verbatim(self, tmp_path: Path) -> None:
        """Without it the db claim aborts in pytest_configure and every node is
        inconclusive — a second, silent way to ship an inert classifier."""
        wt = _ready_worktree(tmp_path)
        report = tmp_path / "baseline.json"
        captured: dict = {}

        def _spawn(argv, timeout=None, env=None, cwd=None):
            captured["env"] = env
            captured["timeout"] = timeout
            report.write_text(json.dumps({"tests": [{"nodeid": "a::t1", "outcome": "passed"}]}))
            return 1

        with (
            patch.object(nrt.subprocess, "Popen", _ProvisionFake()),
            patch.object(nrt, "_spawn_pytest", side_effect=_spawn),
        ):
            _classify(tmp_path, wt, report_path=report)

        assert captured["env"]["TEST_DB_CLAIM_WAIT_S"] == "300"
        assert captured["env"]["PATH"] == os.environ["PATH"], "env must derive from os.environ"
        assert captured["timeout"] == nrt.PYTEST_BASELINE_TIMEOUT_SECONDS


class TestClassifierRunFailures:
    """Every run failure lands in `inconclusive`. Never a guess."""

    def _classify_with_report(self, tmp_path: Path, payload, *, rc: int = 1, nodes=None):
        wt = _ready_worktree(tmp_path)
        report = tmp_path / "baseline.json"

        def _spawn(argv, timeout=None, env=None, cwd=None):
            if payload is not None:
                report.write_text(payload)
            return rc

        with (
            patch.object(nrt.subprocess, "Popen", _ProvisionFake()),
            patch.object(nrt, "_spawn_pytest", side_effect=_spawn),
        ):
            return _classify(tmp_path, wt, nodes=nodes, report_path=report)

    def test_missing_report_is_inconclusive(self, tmp_path: Path) -> None:
        result = self._classify_with_report(tmp_path, None)
        assert result["inconclusive"] == ["a::t1"]

    def test_unparseable_report_is_inconclusive(self, tmp_path: Path) -> None:
        result = self._classify_with_report(tmp_path, "{not json")
        assert result["inconclusive"] == ["a::t1"]

    def test_collection_error_empty_report_is_inconclusive(self, tmp_path: Path) -> None:
        """A collection error writes a report with no test entries at all."""
        result = self._classify_with_report(tmp_path, json.dumps({"tests": []}), rc=2)
        assert result["inconclusive"] == ["a::t1"]

    def test_node_absent_from_the_report_is_inconclusive_not_assumed_passed(
        self, tmp_path: Path
    ) -> None:
        payload = json.dumps({"tests": [{"nodeid": "a::t1", "outcome": "passed"}]})
        result = self._classify_with_report(tmp_path, payload, nodes=["a::t1", "b::t2"])
        assert result["newly_broken"] == ["a::t1"]
        assert result["inconclusive"] == ["b::t2"]

    def test_timeout_is_inconclusive(self, tmp_path: Path) -> None:
        wt = _ready_worktree(tmp_path)

        def _spawn(argv, timeout=None, env=None, cwd=None):
            raise subprocess.TimeoutExpired(cmd=argv, timeout=1)

        with (
            patch.object(nrt.subprocess, "Popen", _ProvisionFake()),
            patch.object(nrt, "_spawn_pytest", side_effect=_spawn),
        ):
            result = _classify(tmp_path, wt)
        assert result["inconclusive"] == ["a::t1"]

    def test_arbitrary_exception_is_inconclusive(self, tmp_path: Path) -> None:
        """The db-claim abort shape: the spawn raises and nothing is guessed."""
        wt = _ready_worktree(tmp_path)
        with (
            patch.object(nrt.subprocess, "Popen", _ProvisionFake()),
            patch.object(nrt, "_spawn_pytest", side_effect=RuntimeError("db claim exhausted")),
        ):
            result = _classify(tmp_path, wt)
        assert result["inconclusive"] == ["a::t1"]

    def test_missing_baseline_sha_never_provisions(self, tmp_path: Path) -> None:
        wt = _ready_worktree(tmp_path)
        fake = _ProvisionFake()
        with (
            patch.object(nrt.subprocess, "Popen", fake),
            patch.object(nrt, "_spawn_pytest") as spawn,
        ):
            result = _classify(tmp_path, wt, baseline_sha="")
        assert result["inconclusive"] == ["a::t1"]
        assert fake.calls == []
        spawn.assert_not_called()

    def test_empty_node_list_does_no_work(self, tmp_path: Path) -> None:
        wt = _ready_worktree(tmp_path)
        fake = _ProvisionFake()
        with (
            patch.object(nrt.subprocess, "Popen", fake),
            patch.object(nrt, "_spawn_pytest") as spawn,
        ):
            result = _classify(tmp_path, wt, nodes=[])
        assert result == nrt.empty_classification()
        assert fake.calls == []
        spawn.assert_not_called()


# --- the five refusable classification preconditions -------------------------


PRECONDITION_CASES = [
    ("seed_run", {"is_seed_run": True}, ["a::t1"]),
    ("integrity_warnings", {"integrity_warnings": ["total shrank"]}, ["a::t1"]),
    ("dry_run", {"dry_run": True}, ["a::t1"]),
    ("no_baseline_sha", {"baseline_sha": ""}, ["a::t1"]),
    # The re-baseline shape: a population far above the cap must be refused
    # BEFORE the cost of classifying it is paid.
    (
        "over_max_failures",
        {},
        [f"a::t{i}" for i in range(nrt.NIGHTLY_FIX_MAX_FAILURES_DEFAULT + 40)],
    ),
]


class TestClassificationPreconditions:
    """Each refusal does NO git and NO pytest work, and still logs its reason."""

    @pytest.mark.parametrize(
        "reason,flag_kwargs,new_failures",
        PRECONDITION_CASES,
        ids=[c[0] for c in PRECONDITION_CASES],
    )
    def test_precondition_refuses_before_any_work(
        self, tmp_path: Path, reason: str, flag_kwargs: dict, new_failures: list[str]
    ) -> None:
        flags = _flags(**flag_kwargs)
        caps = nrt.GateCaps(max_failures=nrt.NIGHTLY_FIX_MAX_FAILURES_DEFAULT)

        assert nrt.classify_precondition_reason(new_failures, caps, flags) == reason

        fake = _ProvisionFake()
        with (
            patch.object(nrt.subprocess, "Popen", fake),
            patch.object(nrt, "_spawn_pytest") as spawn,
        ):
            nrt.log_shadow_verdict(new_failures, caps, flags)

        assert fake.calls == [], "a refused night must run no git subprocess"
        spawn.assert_not_called()

        log_text = (tmp_path / "nightly.log").read_text()
        assert (
            f"nightly-fix shadow-verdict: escalate reason={reason} "
            f"nodes={len(new_failures)}" in log_text
        )
        assert "shadow-buckets" not in log_text, "no classification ran, so no bucket line"

    @pytest.mark.parametrize(
        "reason,flag_kwargs,new_failures",
        PRECONDITION_CASES,
        ids=[c[0] for c in PRECONDITION_CASES],
    )
    def test_precondition_gate_reports_the_same_token(
        self, reason: str, flag_kwargs: dict, new_failures: list[str]
    ) -> None:
        """The call-site list and the gate's first five clauses cannot diverge."""
        caps = nrt.GateCaps(max_failures=nrt.NIGHTLY_FIX_MAX_FAILURES_DEFAULT)
        assert (
            nrt.gate_reason(nrt.empty_classification(), new_failures, caps, _flags(**flag_kwargs))
            == reason
        )


class TestSkippedNightStillLogs:
    def test_skip_still_logs_the_verdict_line(self, tmp_path: Path) -> None:
        """A silent skip would lose this tier's only deliverable on the noisiest nights."""
        nrt.log_shadow_verdict(
            ["a::t1", "b::t2"],
            nrt.GateCaps(max_failures=nrt.NIGHTLY_FIX_MAX_FAILURES_DEFAULT),
            _flags(is_seed_run=True),
        )
        log_text = (tmp_path / "nightly.log").read_text()
        assert "nightly-fix shadow-verdict: escalate reason=seed_run nodes=2" in log_text

    def test_skip_still_logs_when_classification_runs(self, tmp_path: Path) -> None:
        """The sibling bucket line appears only on the classified path."""
        classification = {
            "newly_broken": ["a::t1"],
            "pre_existing": ["b::t2"],
            "inconclusive": [],
        }
        with patch.object(nrt, "classify_against_baseline", return_value=classification):
            nrt.log_shadow_verdict(
                ["a::t1", "b::t2"],
                nrt.GateCaps(max_failures=nrt.NIGHTLY_FIX_MAX_FAILURES_DEFAULT),
                _flags(),
            )
        log_text = (tmp_path / "nightly.log").read_text()
        assert (
            "nightly-fix shadow-buckets: newly_broken=1 pre_existing=1 inconclusive=0 "
            "not_newly_broken=b::t2" in log_text
        )
        assert "nightly-fix shadow-verdict: escalate reason=pre_existing nodes=2" in log_text
