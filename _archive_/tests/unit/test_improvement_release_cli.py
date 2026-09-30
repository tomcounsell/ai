"""``valor-improve-release`` argument handling (#3218, lane 6).

The subprocess path is ``tests/integration/test_improvement_release_end_to_end.py``.
This file pins the in-process boundary: a bad argument is an
``INVALID_ARGUMENT`` refusal (exit 2, one JSON line), and the production
parser carries no clock override. No Redis: every case refuses before a row
is touched.
"""

from __future__ import annotations

import json

import pytest

from tools.improvement_release import cli


def _main(capsys, *argv: str) -> tuple[int, dict]:
    code = cli.main(list(argv))
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1, out
    return code, json.loads(out[0])


class TestInvalidArguments:
    @pytest.mark.parametrize(
        "spec",
        [
            {"selection_rule": "rank", "no_such_key": 1},
            {"selection_rule": "rank"},
        ],
        ids=["unknown-key", "missing-keys"],
    )
    def test_bad_process_spec_file_is_invalid_argument_exit_2(self, tmp_path, capsys, spec):
        budget = tmp_path / "budget.json"
        budget.write_text(
            json.dumps(
                {"unit2_usd": 1.0, "unit3_usd": 1.0, "subscription_turns": 1, "wall_seconds": 1}
            )
        )
        bad = tmp_path / "spec.json"
        bad.write_text(json.dumps(spec))

        code, payload = _main(
            capsys,
            "compare",
            "freeze",
            "--arm-a",
            "sha256:" + "a" * 64,
            "--arm-b",
            str(bad),
            "--opportunities",
            "c1",
            "--budget",
            str(budget),
            "--project-key",
            "test-3218-cli",
        )

        assert code == cli.EXIT_REFUSED
        assert payload["refused"] is True
        assert payload["code"] == "INVALID_ARGUMENT"
        assert "process spec" in payload["detail"] and str(bad) in payload["detail"]

    def test_unreadable_plan_file_is_invalid_argument(self, tmp_path, capsys):
        code, payload = _main(
            capsys,
            "propose",
            "--evaluation",
            "e1",
            "--kind",
            "core_workflow",
            "--candidate-ref",
            "cand",
            "--surfaces",
            "tools/x.py",
            "--rollback-plan",
            str(tmp_path / "missing.json"),
            "--observation",
            str(tmp_path / "missing.json"),
        )
        assert code == cli.EXIT_REFUSED
        assert payload["code"] == "INVALID_ARGUMENT"


class TestNoClockOverride:
    @pytest.mark.parametrize(
        "argv",
        [
            ["approve", "--release", "r", "--approved-by", "Tom"],
            ["expose", "--release", "r"],
            ["close-window", "--release", "r"],
            ["close-window", "--due"],
            [
                "propose",
                "--evaluation",
                "e",
                "--kind",
                "k",
                "--candidate-ref",
                "c",
                "--surfaces",
                "s",
                "--rollback-plan",
                "p",
                "--observation",
                "o",
            ],
        ],
        ids=["approve", "expose", "close-window", "close-window-due", "propose"],
    )
    def test_now_is_not_a_production_flag(self, argv, capsys):
        parser = cli.build_parser()
        parser.parse_args(argv)
        with pytest.raises(SystemExit) as excinfo:
            parser.parse_args([*argv, "--now", "2026-01-01T00:00:00Z"])
        assert excinfo.value.code == 2
        assert "--now" in capsys.readouterr().err

    def test_no_subparser_declares_now(self):
        for action in cli.build_parser()._subparsers._group_actions:
            for sub in getattr(action, "choices", {}).values():
                assert "--now" not in sub.format_help(), sub.prog
