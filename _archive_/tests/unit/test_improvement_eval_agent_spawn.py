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


class TestProviderDispatch:
    """The manifest model selects the session transport (#3311, Task 5).

    ``claude-subscription`` runs one headless ``claude -p`` turn; an
    ``openrouter:``-prefixed model id reaches the cheap provider through the
    existing OpenRouter key over the OpenAI-compatible chat-completions
    route lane 5's investigation exercised.
    """

    def test_subscription_model_runs_the_subscription_transport(self, monkeypatch):
        from tools.improvement_eval import arm_worker

        seen = {}

        def _fake_subscription(prompt, timeout_s):
            seen["prompt"] = prompt
            seen["timeout_s"] = timeout_s
            return "checks are green\nVERDICT: FREEZE"

        monkeypatch.setattr(arm_worker, "_run_session_via_subscription", _fake_subscription)
        outcome = arm_worker.run_agent_trial(
            TASK, {"model": "claude-subscription"}, BOUNDS, PROJECT
        )
        assert outcome["passed"] is True
        assert outcome["model"] == "claude-subscription"
        assert TASK["prompt"] in seen["prompt"]
        assert seen["timeout_s"] == 30

    def test_openrouter_model_runs_the_openrouter_transport(self, monkeypatch):
        from tools.improvement_eval import arm_worker

        seen = {}

        def _fake_openrouter(prompt, timeout_s, model_id):
            seen["prompt"] = prompt
            seen["timeout_s"] = timeout_s
            seen["model_id"] = model_id
            return "checks are green\nVERDICT: FREEZE"

        monkeypatch.setattr(arm_worker, "_run_session_via_openrouter", _fake_openrouter)
        outcome = arm_worker.run_agent_trial(
            TASK, {"model": "openrouter:meta/muse-spark-1.3"}, BOUNDS, PROJECT
        )
        assert outcome["passed"] is True
        assert outcome["model"] == "openrouter:meta/muse-spark-1.3"
        assert seen["model_id"] == "meta/muse-spark-1.3"
        assert TASK["prompt"] in seen["prompt"]
        assert seen["timeout_s"] == 30

    def test_openrouter_transport_posts_the_chat_completions_shape(self, monkeypatch):

        from tools.improvement_eval import arm_worker

        seen = {}

        class _FakeResponse:
            def raise_for_status(self):
                seen["checked_status"] = True

            def json(self):
                return {"choices": [{"message": {"content": "floor is short\nVERDICT: HOLD"}}]}

        def _fake_post(url, *, headers, json, timeout):
            seen["url"] = url
            seen["headers"] = headers
            seen["json"] = json
            seen["timeout"] = timeout
            return _FakeResponse()

        import requests

        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-key")
        monkeypatch.setattr(requests, "post", _fake_post)
        text = arm_worker._run_session_via_openrouter(
            "decide the freeze", 30, "meta/muse-spark-1.3"
        )
        assert text == "floor is short\nVERDICT: HOLD"
        assert seen["url"] == "https://openrouter.ai/api/v1/chat/completions"
        assert seen["headers"]["Authorization"] == "Bearer sk-or-test-key"
        assert seen["json"]["model"] == "meta/muse-spark-1.3"
        assert seen["json"]["messages"] == [{"role": "user", "content": "decide the freeze"}]
        assert seen["checked_status"] is True

    def test_openrouter_transport_reads_list_content_parts(self, monkeypatch):
        from tools.improvement_eval import arm_worker

        class _FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "choices": [
                        {
                            "message": {
                                "content": [
                                    {"type": "text", "text": "reasons here"},
                                    {"type": "text", "text": "VERDICT: FREEZE"},
                                ]
                            }
                        }
                    ]
                }

        import requests

        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-key")
        monkeypatch.setattr(requests, "post", lambda *a, **k: _FakeResponse())
        text = arm_worker._run_session_via_openrouter("decide", 30, "meta/muse-spark-1.3")
        assert "VERDICT: FREEZE" in text

    def test_openrouter_null_content_returns_undecided_never_raises(self, monkeypatch):
        """A 200 with choices but no text is malformed output, not transport.

        Reasoning models can spend the whole turn thinking and emit no
        decision line; the trial comes back undecided and the rubric scores
        it zero, matching the subscription path. Only a transport failure
        propagates for a harness error.
        """
        from tools.improvement_eval import arm_worker

        class _FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "choices": [
                        {
                            "message": {"content": None, "reasoning": "thinking only"},
                            "finish_reason": "length",
                        }
                    ]
                }

        import requests

        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-key")
        monkeypatch.setattr(requests, "post", lambda *a, **k: _FakeResponse())
        outcome = arm_worker.run_agent_trial(
            TASK, {"model": "openrouter:meta/muse-spark-1.3"}, BOUNDS, PROJECT
        )
        assert outcome["output"] == ""
        assert outcome["passed"] is False

    def test_openrouter_transport_without_a_key_refuses_before_post(self, monkeypatch):
        from tools.improvement_eval import arm_worker

        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        with pytest.raises(InfraFailure, match="OPENROUTER_API_KEY"):
            arm_worker.run_agent_trial(
                TASK, {"model": "openrouter:meta/muse-spark-1.3"}, BOUNDS, PROJECT
            )

    def test_openrouter_transport_failure_propagates_for_a_harness_error(self, monkeypatch):
        import requests

        from tools.improvement_eval import arm_worker

        def _boom(*args, **kwargs):
            raise requests.ConnectionError("dns down")

        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-key")
        monkeypatch.setattr(requests, "post", _boom)
        with pytest.raises(RuntimeError, match="dns down"):
            arm_worker.run_agent_trial(
                TASK, {"model": "openrouter:meta/muse-spark-1.3"}, BOUNDS, PROJECT
            )
