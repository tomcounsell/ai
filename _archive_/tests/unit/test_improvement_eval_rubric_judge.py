"""Tests for the task-rubric judge for agent-run evaluations (#3311, Task 4).

The rubric judge is deterministic: it reads the frozen expected verdict for
one endpoint and scores the session outcome 1.0/0.0 on the VERDICT line. A
blank or malformed outcome scores zero with a named reason and never throws.
"""

from __future__ import annotations

import json


def _envelope_output(output: str) -> str:
    return json.dumps({"trial_id": "t1", "blinded_arm_id": "arm-a", "outcome": {"output": output}})


class TestRubricJudge:
    def test_correct_verdict_scores_one(self):
        from tools.improvement_eval.judges.rubric import make_rubric_judge

        judge = make_rubric_judge("readiness-verdict", "HOLD")
        envelope = judge(
            _envelope_output("checks show 0 of 20.\nVERDICT: HOLD"),
            blinded_arm_id="arm-a",
            trial_id="t1",
        )
        assert envelope["status"] == "ok"
        assert envelope["judge"]["judge_id"] == "readiness-verdict"
        assert envelope["judge"]["score"] == 1.0
        assert envelope["judge"]["verdict"] == "APPROVED"

    def test_wrong_verdict_scores_zero(self):
        from tools.improvement_eval.judges.rubric import make_rubric_judge

        judge = make_rubric_judge("readiness-verdict", "HOLD")
        envelope = judge(
            _envelope_output("all green.\nVERDICT: FREEZE"),
            blinded_arm_id="arm-a",
            trial_id="t1",
        )
        assert envelope["status"] == "ok"
        assert envelope["judge"]["score"] == 0.0
        assert envelope["judge"]["verdict"] == "CHANGES REQUESTED"

    def test_missing_verdict_line_scores_zero_with_reason(self):
        from tools.improvement_eval.judges.rubric import make_rubric_judge

        judge = make_rubric_judge("readiness-verdict", "HOLD")
        envelope = judge(
            _envelope_output("some prose with no decision"),
            blinded_arm_id="arm-a",
            trial_id="t1",
        )
        assert envelope["judge"]["score"] == 0.0
        assert "VERDICT" in envelope["judge"]["reasoning_summary"]

    def test_non_json_input_scores_zero_without_throwing(self):
        from tools.improvement_eval.judges.rubric import make_rubric_judge

        judge = make_rubric_judge("readiness-verdict", "HOLD")
        envelope = judge("not json at all", blinded_arm_id="arm-a", trial_id="t1")
        assert envelope["status"] == "ok"
        assert envelope["judge"]["score"] == 0.0

    def test_blank_output_scores_zero_without_throwing(self):
        from tools.improvement_eval.judges.rubric import make_rubric_judge

        judge = make_rubric_judge("readiness-verdict", "HOLD")
        envelope = judge(_envelope_output(""), blinded_arm_id="arm-a", trial_id="t1")
        assert envelope["status"] == "ok"
        assert envelope["judge"]["score"] == 0.0

    def test_verdict_match_is_case_and_space_tolerant(self):
        from tools.improvement_eval.judges.rubric import make_rubric_judge

        judge = make_rubric_judge("readiness-verdict", "FREEZE")
        envelope = judge(
            _envelope_output("reasoning here\nverdict:   freeze  "),
            blinded_arm_id="arm-a",
            trial_id="t1",
        )
        assert envelope["judge"]["score"] == 1.0

    def test_last_verdict_line_wins(self):
        from tools.improvement_eval.judges.rubric import make_rubric_judge

        judge = make_rubric_judge("readiness-verdict", "HOLD")
        envelope = judge(
            _envelope_output("VERDICT: FREEZE\non reflection the floor is short\nVERDICT: HOLD"),
            blinded_arm_id="arm-a",
            trial_id="t1",
        )
        assert envelope["judge"]["score"] == 1.0

    def test_per_trial_expected_map(self):
        from tools.improvement_eval.judges.rubric import make_rubric_judge

        judge = make_rubric_judge("readiness-verdict", {"t1": "FREEZE", "t2": "HOLD"})
        freeze = judge(
            _envelope_output("all green\nVERDICT: FREEZE"),
            blinded_arm_id="arm-a",
            trial_id="t1",
        )
        hold = judge(
            _envelope_output("all green\nVERDICT: FREEZE"),
            blinded_arm_id="arm-a",
            trial_id="t2",
        )
        assert freeze["judge"]["score"] == 1.0
        assert hold["judge"]["score"] == 0.0

    def test_trial_outside_the_map_scores_zero_with_reason(self):
        from tools.improvement_eval.judges.rubric import make_rubric_judge

        judge = make_rubric_judge("readiness-verdict", {"t1": "FREEZE"})
        envelope = judge(_envelope_output("VERDICT: FREEZE"), blinded_arm_id="arm-a", trial_id="t9")
        assert envelope["status"] == "ok"
        assert envelope["judge"]["score"] == 0.0
        assert "t9" in envelope["judge"]["reasoning_summary"]
