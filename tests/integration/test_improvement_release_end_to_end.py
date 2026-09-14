"""``valor-improve-release`` end to end, as a subprocess (#3218, lane 6).

The operator path from the plan, driven through the real CLI in a child
process against the claimed test db and a real temporary git repository:
seed experiment and ``accept`` evaluation (lane 4's exact string shapes) →
``propose`` → ``drill`` → ``approve`` → ``open-pr`` → ``expose`` →
``close-window`` → ``accepted``; then ``report`` shows level 2 supported and
level 3 not. ``gh`` is a fake executable on ``PATH`` that answers
``gh pr create`` with a URL and ``gh pr view`` with the real object shape.
Refusals are asserted on the exit code and the JSON the CLI prints.

Uses the autouse ``redis_test_db`` fixture (tests/conftest.py); the child
inherits the claimed db through ``REDIS_URL``.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from models.improvement_charter import _CHARTER_PATH, ImprovementCharter
from models.improvement_evaluation import ImprovementEvaluation
from models.improvement_evidence import ImprovementEvidence
from models.improvement_experiment import ImprovementExperiment
from models.improvement_release import ImprovementRelease
from tests.db_claim import subprocess_env
from tools.improvement_eval.runner import freeze_protocol

PK = "test-3218-e2e"
NOW = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
MERGED_AT = NOW - timedelta(days=7)
MERGE_SHA = "37dc10b33f6c33d18559d4c338d23653a21dbb49"
INTERVAL = {"lower": 0.02, "upper": 0.22, "n": 12, "raw_p_value": 0.01, "adjusted_p_value": 0.02}
PROJECT_ROOT = str(Path(__file__).resolve().parents[2])

GIT_ENV = {
    "GIT_AUTHOR_NAME": "e2e-test",
    "GIT_AUTHOR_EMAIL": "e2e@test.invalid",
    "GIT_COMMITTER_NAME": "e2e-test",
    "GIT_COMMITTER_EMAIL": "e2e@test.invalid",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
}

FAKE_GH = """#!/bin/sh
if [ "$1" = "pr" ] && [ "$2" = "create" ]; then
  echo "https://github.com/tomcounsell/ai/pull/4242"
  exit 0
fi
if [ "$1" = "pr" ] && [ "$2" = "view" ]; then
  cat "$GH_FAKE_VIEW"
  exit 0
fi
echo "fake gh: unexpected $*" >&2
exit 64
"""


def git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, **GIT_ENV},
        timeout=30,
    )
    return completed.stdout.strip()


def commit(repo: Path, message: str, **files: str) -> str:
    for relative, content in files.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        git(repo, "add", "--", relative)
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def repo(tmp_path):
    """A clone with a bare ``origin``: base on ``main``, candidate on ``cand``."""
    origin = tmp_path / "origin.git"
    origin.mkdir()
    git(origin, "init", "-q", "--bare", "-b", "main")
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    git(path, "remote", "add", "origin", str(origin))
    base = commit(path, "base", **{"README.md": "readme\n", "src/thing.py": "v0\n"})
    git(path, "switch", "-q", "-c", "cand")
    commit(path, "candidate", **{"src/thing.py": "v1\n"})
    git(path, "switch", "-q", "main")
    git(path, "push", "-q", "origin", "main", "cand")
    return {"path": path, "origin": origin, "base": base}


def merge_candidate(repo: dict) -> str:
    """Merge ``cand`` into ``main`` with a merge commit and push; return its SHA."""
    git(repo["path"], "merge", "-q", "--no-ff", "-m", "merge cand", "cand")
    git(repo["path"], "push", "-q", "origin", "main")
    return git(repo["path"], "rev-parse", "HEAD")


def write_pr_view(view: Path, merge_sha: str) -> None:
    view.write_text(
        json.dumps(
            {
                "mergeCommit": {"oid": merge_sha},
                "mergedAt": MERGED_AT.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "state": "MERGED",
            }
        )
    )


@pytest.fixture
def fake_gh(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "gh"
    script.write_text(FAKE_GH)
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    view = tmp_path / "pr_view.json"
    write_pr_view(view, MERGE_SHA)
    return {"bin": bin_dir, "view": view}


@pytest.fixture
def charter(tmp_path):
    path = tmp_path / "improvement-charter.md"
    path.write_bytes(_CHARTER_PATH.read_bytes().replace(b"\r\n", b"\n"))
    row = ImprovementCharter.load_from_file(path, project_key=PK)
    assert row is not None
    return row


def _experiment(base_revision: str) -> ImprovementExperiment:
    ref = freeze_protocol({"primary_endpoint": "primary", "endpoints": ["primary"]})
    row = ImprovementExperiment(
        project_key=PK,
        created_at=NOW,
        state="complete",
        case_id=None,
        hypothesis="a wider candidate finds the gold memory",
        contract_digest="sha256:" + "c" * 64,
        candidate_surfaces=json.dumps(["src/thing.py"]),
        manifest=json.dumps({"protocol_ref": ref, "base_revision": base_revision}),
    )
    assert row.save() is not False
    return row


def _evaluation(charter_row, experiment, *, verdict="accept") -> ImprovementEvaluation:
    row = ImprovementEvaluation(
        project_key=PK,
        created_at=NOW,
        state="complete",
        verdict=verdict,
        experiment_id=str(experiment.id),
        contract_digest=experiment.contract_digest,
        charter_digest=charter_row.digest,
        # runner.py:499-508's exact expressions
        effect=json.dumps({"primary": 0.12}, sort_keys=True),
        confidence_interval=json.dumps({"primary": INTERVAL}, sort_keys=True),
        correction="holm; fixed-batch(n=2, endpoints=1)",
        notes="\n".join([verdict]),
        judge_records="",
    )
    assert row.save() is not False
    return row


@pytest.fixture
def evaluation(charter, repo):
    return _evaluation(charter, _experiment(repo["base"]))


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    for model in (ImprovementRelease, ImprovementEvaluation, ImprovementExperiment):
        for row in model.query.filter(project_key=PK):
            row.delete()


@pytest.fixture
def plans(tmp_path):
    rollback = tmp_path / "rollback.json"
    rollback.write_text(
        json.dumps({"kind": "git_revert", "verify": ["true"], "propagation": "/update"})
    )
    observation = tmp_path / "observation.json"
    observation.write_text(
        json.dumps(
            {
                "window_days": 7,
                "baseline_window_days": 7,
                "metrics": ["architectural_correction_rate", "coverage_ticks"],
            }
        )
    )
    return {"rollback": rollback, "observation": observation}


def _seed_evidence(at: datetime, *, architectural: int, ticks: int):
    for i in range(architectural):
        ImprovementEvidence.create(
            project_key=PK,
            created_at=at + timedelta(seconds=i),
            kind="correction",
            classification="architectural",
            source_ref=f"memory:{at.isoformat()}:{i}",
        )
    for i in range(ticks):
        ImprovementEvidence.create(
            project_key=PK,
            created_at=at + timedelta(seconds=100 + i),
            kind="other",
            classification="unknown",
            source_ref=f"coverage:{at.isoformat()}:{i}",
        )


# ---------------------------------------------------------------------------
# The CLI as a subprocess
# ---------------------------------------------------------------------------


class Cli:
    def __init__(self, repo: Path, extra_path: Path | None, extra_env: dict[str, str]):
        env = subprocess_env(project_root=PROJECT_ROOT, **extra_env)
        assert env["REDIS_URL"].rsplit("/", 1)[1] != "0", env["REDIS_URL"]
        if extra_path is not None:
            env["PATH"] = f"{extra_path}{os.pathsep}{env.get('PATH', '')}"
        self.env = {**env, **GIT_ENV}
        self.repo = repo

    def __call__(self, *args: str, expect: int = 0) -> dict:
        completed = subprocess.run(
            [sys.executable, "-m", "tools.improvement_release.cli", *args, "--project-key", PK],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            env=self.env,
            timeout=120,
        )
        assert completed.returncode == expect, (
            f"exit {completed.returncode}, expected {expect}\n"
            f"stdout: {completed.stdout}\nstderr: {completed.stderr}"
        )
        lines = [line for line in completed.stdout.splitlines() if line.strip()]
        assert len(lines) == 1, f"expected one JSON line on stdout, got: {completed.stdout!r}"
        return json.loads(lines[0])


@pytest.fixture
def cli(repo, fake_gh):
    return Cli(repo["path"], fake_gh["bin"], {"GH_FAKE_VIEW": str(fake_gh["view"])})


def _propose(cli, evaluation, plans, repo, *, expect: int = 0) -> dict:
    return cli(
        "propose",
        "--evaluation",
        str(evaluation.id),
        "--kind",
        "core_workflow",
        "--candidate-ref",
        "cand",
        "--surfaces",
        "src/thing.py",
        "--rollback-plan",
        str(plans["rollback"]),
        "--observation",
        str(plans["observation"]),
        "--repo",
        str(repo["path"]),
        "--now",
        NOW.isoformat(),
        expect=expect,
    )


class TestOperatorPath:
    def test_propose_to_accepted_report_then_rollback(
        self, cli, fake_gh, evaluation, plans, repo, tmp_path
    ):
        proposed = _propose(cli, evaluation, plans, repo)
        assert proposed["state"] == "proposed"
        release_id = proposed["id"]

        drilled = cli(
            "drill",
            "--release",
            release_id,
            "--repo",
            str(repo["path"]),
            "--root",
            str(tmp_path / "retention"),
            "--runner-log",
            str(tmp_path / "drill.jsonl"),
        )
        assert drilled["rollback_drill"]["result"] == "pass", drilled
        assert drilled["rollback_drill"]["restored"] is True
        log_lines = (tmp_path / "drill.jsonl").read_text().splitlines()
        assert any('"revert"' in line for line in log_lines), log_lines

        approved = cli(
            "approve",
            "--release",
            release_id,
            "--approved-by",
            "Tom Counsell",
            "--now",
            (NOW + timedelta(hours=1)).isoformat(),
        )
        assert approved["state"] == "approved"
        assert approved["observation_window_ends_at"] is not None
        assert approved["exposed_at"] is None

        opened = cli(
            "open-pr",
            "--release",
            release_id,
            "--repo",
            str(repo["path"]),
            "--runner-log",
            str(tmp_path / "pr.jsonl"),
        )
        assert opened["exposure"]["pr_number"] == 4242
        pr_log = (tmp_path / "pr.jsonl").read_text()
        assert '"gh", "pr", "create"' in pr_log

        # The pipeline merges the PR; the canned ``gh pr view`` answers with the
        # real merge commit so the later rollback reverts something that exists.
        merge_sha = merge_candidate(repo)
        write_pr_view(fake_gh["view"], merge_sha)
        _seed_evidence(MERGED_AT - timedelta(days=3), architectural=2, ticks=20)
        _seed_evidence(MERGED_AT + timedelta(days=3), architectural=2, ticks=20)

        exposed = cli(
            "expose",
            "--release",
            release_id,
            "--repo",
            str(repo["path"]),
            "--now",
            NOW.isoformat(),
        )
        assert exposed["state"] == "observing"
        assert exposed["exposure"]["merge_sha"] == merge_sha
        assert isinstance(exposed["exposure"]["merge_sha"], str)
        assert len(exposed["exposure"]["merge_sha"]) == 40
        assert exposed["exposed_at"] == MERGED_AT.isoformat()
        assert exposed["outcome"]["baseline"]["coverage_ticks"] == 20

        due = cli("close-window", "--due", "--now", (MERGED_AT + timedelta(days=7)).isoformat())
        assert [row["id"] for row in due["due"]] == [release_id]

        closed = cli(
            "close-window",
            "--release",
            release_id,
            "--now",
            (MERGED_AT + timedelta(days=7)).isoformat(),
        )
        assert closed["state"] == "accepted"
        assert closed["outcome"]["verdict"] == "held"
        assert closed["outcome"]["claim_level_2_supported"] is True
        assert closed["outcome"]["deltas"]["window"]["coverage_ticks"] == 20

        report = cli("report")
        level_2 = report["levels"]["2"]
        assert level_2["supported"] is True
        assert level_2["confidence_interval"] == INTERVAL
        assert level_2["correction"] == "holm; fixed-batch(n=2, endpoints=1)"
        assert "baseline band" in level_2["falsifier"]
        level_3 = report["levels"]["3"]
        assert level_3["supported"] is False
        assert level_3["why_not"] == "no comparison recorded"

        shown = cli("show", "--release", release_id)
        assert shown["state"] == "accepted"
        assert shown["rollback_drill"]["result"] == "pass"
        assert "revert" in shown["drill_log"]

        stored = ImprovementRelease.query.filter(project_key=PK, id=release_id).first()
        assert stored.state == "accepted"

        # The incident surface: revert the real merge on a freshly fetched
        # origin/main and push the revert to main.
        rolled = cli(
            "rollback",
            "--release",
            release_id,
            "--reason",
            "regression seen after the window",
            "--repo",
            str(repo["path"]),
            "--root",
            str(tmp_path / "retention"),
            "--runner-log",
            str(tmp_path / "rollback.jsonl"),
        )
        assert rolled["state"] == "rolled_back"
        record = rolled["outcome"]["rollback"]
        assert record["merge_sha"] == merge_sha
        assert record["merge_commit_parents"] == 2
        assert record["pushed_to"] == "main"
        assert "pr_command" not in rolled
        remote_head = git(repo["origin"], "rev-parse", "refs/heads/main")
        assert remote_head == record["revert_sha"]
        reverted = subprocess.run(
            ["git", "show", f"{remote_head}:src/thing.py"],
            cwd=repo["origin"],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, **GIT_ENV},
        ).stdout
        assert reverted == "v0\n"
        rollback_log = (tmp_path / "rollback.jsonl").read_text()
        assert rollback_log.index('"fetch"') < rollback_log.index('"revert"')

    def test_report_renders_as_text(self, cli):
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.improvement_release.cli",
                "report",
                "--render",
                "--project-key",
                PK,
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            env=cli.env,
            timeout=120,
        )
        assert completed.returncode == 0, completed.stderr
        assert "Level 2:" in completed.stdout
        assert "supported: no" in completed.stdout


class TestRefusals:
    def test_propose_refuses_a_rejected_evaluation(self, cli, charter, repo, plans):
        rejected = _evaluation(charter, _experiment(repo["base"]), verdict="reject")
        refused = _propose(cli, rejected, plans, repo, expect=2)
        assert refused["refused"] is True
        assert refused["code"] == "EVALUATION_NOT_ACCEPT"
        assert list(ImprovementRelease.query.filter(project_key=PK)) == []

    def test_approve_refuses_without_a_drill(self, cli, evaluation, plans, repo):
        proposed = _propose(cli, evaluation, plans, repo)
        refused = cli(
            "approve",
            "--release",
            proposed["id"],
            "--approved-by",
            "Tom Counsell",
            expect=2,
        )
        assert refused["refused"] is True
        assert refused["code"] == "DRILL_REQUIRED"
        assert "no passing rollback drill" in refused["detail"]

    def test_show_unknown_release_is_not_found(self, cli):
        refused = cli("show", "--release", "nope", expect=2)
        assert refused["code"] == "NOT_FOUND"

    def test_gate_is_disabled_with_both_preconditions(self, cli):
        gate = cli("gate")
        assert gate["automated"] is False
        assert set(gate["unmet"]) == {"credential_separation", "charter_names_reversible_surfaces"}

    def test_compare_run_refusal_is_exit_2_json(self, cli):
        """The experiment lookup runs first, so the arm-runner spec is never imported."""
        refused = cli(
            "compare",
            "run",
            "--experiment",
            "nope",
            "--arm-runner",
            "nosuch.module:Runner",
            expect=2,
        )
        assert refused["refused"] is True
        assert refused["code"] == "NOT_FOUND"
