"""Operating report tests (lane 7, #3274).

The report's primary case is the zero-sandbox position: five real answers,
one of which is "none". The fifth answer is non-empty whenever its inputs
are, and each answer degrades independently when its source fails.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from tools import improvement_operating_report as report
from tools.improvement_operating_report import generate_report, main

PK = "test-3274-report"


def fake_settings(cap=50.0):
    return SimpleNamespace(
        improvement=SimpleNamespace(
            weekly_infrastructure_usd=cap,
            budget_week_start="monday",
            budget_day_boundary="UTC",
        )
    )


def seed_probe_row(states):
    from models.improvement_evidence import ImprovementEvidence

    detail = {name: {"state": state, "detail": f"{name} {state}"} for name, state in states.items()}
    return ImprovementEvidence.record_once(
        PK,
        "resource_probe",
        source_ref="lane7-report-probe-seed",
        text="; ".join(f"{k}={v}" for k, v in states.items()),
        detail=json.dumps(detail),
        observed_at=datetime.now(UTC),
    )


class TestFiveAnswers:
    def test_five_answers_from_zero_sandbox_position(self):
        answers = generate_report(project_key="test-3274-empty", settings=fake_settings())
        for key in ("sessions", "unattended", "resources", "cost", "prevents"):
            assert answers[key], f"answer {key!r} is empty"
        assert "No RSI session" in answers["sessions"]
        assert "not demonstrated" in answers["unattended"]
        assert "no probe record" in answers["resources"]
        assert "monday" in answers["cost"] and "UTC" in answers["cost"]
        assert answers["prevents"]

    def test_five_answers_with_probe_and_refusal(self):
        from tools.infrastructure_budget import ResourceDecl, admit

        seed_probe_row({"cloudflare_account": "unknown", "cloudflare_cli": "absent"})
        refused = admit(
            ResourceDecl(name="r-report-refuse", weekly_rate_usd=None),
            project_key=PK,
            settings=fake_settings(),
            now=datetime(2026, 9, 22, 12, 0, tzinfo=UTC),
        )
        assert refused.admitted is False
        answers = generate_report(project_key=PK, settings=fake_settings())
        assert "cloudflare_account" in answers["prevents"]
        assert "no forecastable rate" in answers["prevents"]

    def test_what_prevents_nonempty_when_inputs_nonempty(self):
        answers = generate_report(project_key="test-3274-empty", settings=fake_settings())
        # Even with no probe and no refusals, the recorded assumptions keep
        # the fifth answer non-empty.
        assert "What still prevents" in answers["prevents"]
        assert len(answers["prevents"]) > 100


class TestPerSourceDegradation:
    @pytest.mark.parametrize(
        "source,failed_answer",
        [
            ("read_probe_row", "resources"),
            ("read_reservations", "cost"),
            ("read_receipts", "cost"),
            ("read_trial_records", "sessions"),
            ("read_assumptions", "prevents"),
        ],
    )
    def test_degradation_one_source_fails_four_survive(self, monkeypatch, source, failed_answer):
        def boom(*args, **kwargs):
            raise RuntimeError(f"{source} down")

        monkeypatch.setattr(report, source, boom)
        answers = generate_report(project_key="test-3274-empty", settings=fake_settings())
        assert (
            answers[failed_answer]
            == "could not be determined: "
            + {
                "resources": "probe record unavailable",
                "cost": "ledger unavailable",
                "sessions": "trial records unavailable",
                "prevents": "assumptions unavailable",
            }[failed_answer]
        )
        survivors = {"sessions", "unattended", "resources", "cost", "prevents"} - {failed_answer}
        if failed_answer == "sessions":
            survivors.discard("unattended")
            assert answers["unattended"].startswith("could not be determined")
        for key in survivors:
            assert not answers[key].startswith("could not be determined"), key


class TestModuleEntryPointSubprocess:
    def test_report_runs_as_module_and_emits_five_answers(self):
        """The agent's entry point is `python -m`, not `main()` in-process.

        Runs the module the way the agent would and asserts all five answers
        appear. The subprocess inherits this process's REDIS_URL, which the
        test harness points at the claimed test db, so the run stays
        read-only against test records.
        """
        import subprocess
        import sys
        from pathlib import Path

        repo_root = Path(__file__).resolve().parents[2]
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.improvement_operating_report",
                "--project-key",
                "test-3274-empty",
            ],
            capture_output=True,
            text=True,
            cwd=repo_root,
            timeout=180,
        )
        assert proc.returncode == 0, proc.stderr
        answers = json.loads(proc.stdout)
        for key in ("sessions", "unattended", "resources", "cost", "prevents"):
            assert answers[key], f"answer {key!r} is empty"


class TestEntryPoint:
    def test_report_invocable_through_module_entry_point(self, capsys):
        assert main(["--project-key", "test-3274-empty"]) == 0
        out = capsys.readouterr().out
        answers = json.loads(out)
        for key in ("sessions", "unattended", "resources", "cost", "prevents"):
            assert answers[key]

    def test_report_help_is_informative(self):
        with pytest.raises(SystemExit) as exc:
            main(["--help"])
        assert exc.value.code == 0

    def test_report_has_no_non_measure_functions(self):
        import inspect
        import pathlib

        defined = {name for name, _ in inspect.getmembers(report, inspect.isfunction)}
        for banned in ("sandbox_count", "uptime", "token_volume", "token_count"):
            assert not any(banned in name for name in defined), banned
        text = pathlib.Path(report.__file__).read_text()
        for banned in ("def sandbox_count", "def uptime", "def token_volume", "def token_count"):
            assert banned not in text
