"""The LLM task taxonomy (#3410): ``agent/llm/tasks.py`` and ``LLMCallError.reason``.

An ``LLMTask`` is the one per-site declaration the router reads. These
tests pin the shape lane B (#3420) and lane C (#3421) build against:
frozen, keyword-extensible with defaults, enum values that are the
lowercase strings the ``llm_route`` / ``llm_fallback`` log lines print.
"""

from __future__ import annotations

import dataclasses

import pytest
from agent.llm.tasks import Backend, ErrorCost, LLMTask, TaskKind

from agent.llm import LLMCallError, LLMStackIncompatible


class TestEnums:
    def test_kind_values_are_the_two_populations(self):
        assert {k.value for k in TaskKind} == {"classification", "thinking"}

    def test_lane_a_backends_are_anthropic_and_ollama(self):
        assert {b.value for b in Backend} == {"anthropic", "ollama"}

    def test_error_cost_tiers(self):
        assert {c.value for c in ErrorCost} == {"low", "medium", "high"}


class TestLLMTask:
    def test_defaults(self):
        task = LLMTask(
            site="routing.needs_response", kind=TaskKind.CLASSIFICATION, backend=Backend.OLLAMA
        )
        assert task.error_cost is ErrorCost.MEDIUM
        assert task.client_only is False

    def test_is_frozen(self):
        task = LLMTask(site="x.y", kind=TaskKind.THINKING, backend=Backend.ANTHROPIC)
        with pytest.raises(dataclasses.FrozenInstanceError):
            task.backend = Backend.OLLAMA  # type: ignore[misc]

    def test_is_hashable_for_use_as_a_dict_key(self):
        task = LLMTask(site="x.y", kind=TaskKind.THINKING, backend=Backend.ANTHROPIC)
        assert {task: 1}[task] == 1

    def test_replace_is_the_one_word_landing_diff(self):
        """Moving a site between backends is ``dataclasses.replace`` on one field."""
        task = LLMTask(site="x.y", kind=TaskKind.CLASSIFICATION, backend=Backend.ANTHROPIC)
        landed = dataclasses.replace(task, backend=Backend.OLLAMA)
        assert landed.backend is Backend.OLLAMA
        assert landed.site == task.site

    def test_no_question_builder_fields(self):
        """#3421 adds its own fields; lane A declares none of them."""
        names = {f.name for f in dataclasses.fields(LLMTask)}
        assert names == {"site", "kind", "backend", "error_cost", "client_only"}


class TestLLMCallErrorReason:
    @pytest.mark.parametrize("reason", ["timeout", "slot_timeout", "transport", "validation"])
    def test_reason_is_carried(self, reason):
        err = LLMCallError("boom", reason=reason)
        assert err.reason == reason
        assert str(err) == "boom"

    def test_reason_defaults_to_transport(self):
        """Existing ``LLMCallError("...")`` raise sites and test fakes keep working."""
        assert LLMCallError("ollama unreachable").reason == "transport"

    def test_stack_incompatible_is_still_a_call_error_with_a_reason(self):
        err = LLMStackIncompatible("degraded")
        assert isinstance(err, LLMCallError)
        assert err.reason == "transport"
