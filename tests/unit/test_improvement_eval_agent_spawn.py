"""Tests for the real session spawn in run_agent_trial (#3311, Task 4).

``run_agent_trial`` runs one bounded agent session per trial: the task prompt
shaped by the manifest (persona preamble plus the frozen skill text when the
manifest names a skill), executed through the injected transport, with the
``VERDICT:`` line parsed into the ``passed`` bit. A malformed session output
is returned with ``passed=False`` (the rubric scores it zero); only a
transport failure propagates, so the worker reports an error and the trial
becomes a harness error, never a scored zero.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tools.improvement_eval.errors import InfraFailure


@pytest.fixture(autouse=True)
def _arm_child_env(tmp_path, monkeypatch):
    """``run_agent_trial`` runs in the arm child env: scratch derives from it."""
    monkeypatch.setenv("POPOTO_CONTENT_PATH", str(tmp_path))


TASK = {
    "id": "t1",
    "prompt": "decide READY or NOT READY for the freeze described above",
}
BOUNDS = {"timeout_s": 30, "max_turns": 2, "spend_cap": 0.02}
PROJECT = "test3311agentspawn"


def _skill_text() -> str:
    root = Path(__file__).resolve().parents[2]
    for base in (".claude/skills", ".claude/skills-global"):
        candidate = root / base / "improve-preflight" / "SKILL.md"
        if candidate.exists():
            return candidate.read_text()
    raise AssertionError("improve-preflight SKILL.md missing from the checkout")


def _skill_hash() -> str:
    return "sha256:" + hashlib.sha256(_skill_text().encode("utf-8")).hexdigest()


class TestRunAgentTrialSpawn:
    def test_bare_prompt_without_skill(self):
        from tools.improvement_eval import arm_worker

        seen = {}

        def _fake(prompt, timeout_s):
            seen["prompt"] = prompt
            seen["timeout_s"] = timeout_s
            return "checks are green\nVERDICT: FREEZE"

        outcome = arm_worker.run_agent_trial(
            TASK, {"model": "claude-subscription"}, BOUNDS, PROJECT, _complete=_fake
        )
        assert outcome["task_id"] == "t1"
        assert outcome["passed"] is True
        assert "VERDICT: FREEZE" in outcome["output"]
        assert outcome["model"] == "claude-subscription"
        assert TASK["prompt"] in seen["prompt"]
        assert "improve-preflight" not in seen["prompt"]
        assert seen["timeout_s"] == 30

    def test_skill_text_pinned_by_prompt_hash(self):
        from tools.improvement_eval import arm_worker

        seen = {}

        def _fake(prompt, timeout_s):
            seen["prompt"] = prompt
            return "floor is short\nVERDICT: HOLD"

        manifest = {
            "model": "claude-subscription",
            "skill": "improve-preflight",
            "persona": "research-session",
            "prompt_hash": _skill_hash(),
        }
        outcome = arm_worker.run_agent_trial(TASK, manifest, BOUNDS, PROJECT, _complete=_fake)
        assert outcome["passed"] is True
        assert "Check 1" in seen["prompt"]
        assert "research-session" in seen["prompt"]
        assert TASK["prompt"] in seen["prompt"]

    @pytest.mark.parametrize("bad_hash", ["sha256:" + "0" * 64, None, ""])
    def test_wrong_or_missing_prompt_hash_refuses(self, bad_hash):
        from tools.improvement_eval import arm_worker

        manifest = {"model": "claude-subscription", "skill": "improve-preflight"}
        if bad_hash is not None:
            manifest["prompt_hash"] = bad_hash
        with pytest.raises(InfraFailure, match="prompt_hash"):
            arm_worker.run_agent_trial(
                TASK, manifest, BOUNDS, PROJECT, _complete=lambda p, t: "VERDICT: HOLD"
            )

    def test_missing_skill_file_refuses(self):
        from tools.improvement_eval import arm_worker

        manifest = {
            "model": "m",
            "skill": "no-such-skill-3311",
            "prompt_hash": "sha256:abc",
        }
        with pytest.raises(InfraFailure, match="no-such-skill-3311"):
            arm_worker.run_agent_trial(
                TASK, manifest, BOUNDS, PROJECT, _complete=lambda p, t: "VERDICT: HOLD"
            )

    def test_malformed_output_returns_unpassed_never_raises(self):
        from tools.improvement_eval import arm_worker

        outcome = arm_worker.run_agent_trial(
            TASK,
            {"model": "m"},
            BOUNDS,
            PROJECT,
            _complete=lambda prompt, timeout_s: "prose with no decision line",
        )
        assert outcome["passed"] is False
        assert outcome["output"] == "prose with no decision line"

    def test_transport_failure_propagates_for_a_harness_error(self):
        from tools.improvement_eval import arm_worker

        def _boom(prompt, timeout_s):
            raise RuntimeError("claude -p exited 1")

        with pytest.raises(RuntimeError, match="exited 1"):
            arm_worker.run_agent_trial(TASK, {"model": "m"}, BOUNDS, PROJECT, _complete=_boom)
