"""The LLM task taxonomy (#3410): ``agent/llm/tasks.py`` and ``LLMCallError.reason``.

An ``LLMTask`` is the one per-site declaration the router reads. These
tests pin the shape lane B (#3420) and lane C (#3421) build against:
frozen, keyword-extensible with defaults, enum values that are the
lowercase strings the ``llm_route`` / ``llm_fallback`` log lines print.
"""

from __future__ import annotations

import dataclasses

import pytest

from agent.llm import LLMCallError, LLMStackIncompatible
from agent.llm.tasks import Backend, Decision, ErrorCost, LLMTask, TaskKind


class TestEnums:
    def test_kind_values_are_the_two_populations(self):
        assert {k.value for k in TaskKind} == {"classification", "thinking"}

    def test_backends_are_anthropic_ollama_and_decisions(self):
        """Lane A's two legs plus lane C's decisions leg (#3421)."""
        assert {b.value for b in Backend} == {"anthropic", "ollama", "decisions"}

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
        """#3421's question metadata is the per-field ``Decision`` marker, never
        a task field: the site walk keeps reading exactly these five."""
        names = {f.name for f in dataclasses.fields(LLMTask)}
        assert names == {"site", "kind", "backend", "error_cost", "client_only"}


class TestDecisionMarker:
    """``Decision`` rides on an output type's field as ``Annotated`` metadata
    (#3421): the decisions leg reads it from ``model_fields``; the other legs
    never see it because pydantic keeps it out of the JSON schema."""

    def test_defaults(self):
        marker = Decision("Does this need a reply?")
        assert marker.criteria is None
        assert marker.threshold == 0.5
        assert marker.min_confidence == 0.0

    def test_is_frozen(self):
        marker = Decision("q")
        with pytest.raises(dataclasses.FrozenInstanceError):
            marker.threshold = 0.9  # type: ignore[misc]

    def test_exported_from_the_package(self):
        import agent.llm

        assert agent.llm.Decision is Decision

    def test_retrievable_from_field_metadata_and_absent_from_the_schema(self):
        from typing import Annotated, Literal

        from pydantic import BaseModel

        marker = Decision("Which bucket?", criteria={"a": "first", "b": "second"})

        class Out(BaseModel):
            verdict: Annotated[Literal["a", "b"], marker]
            flag: Annotated[bool, Decision("Is it?", threshold=0.7)]
            reason: str = ""

        assert Out.model_fields["verdict"].metadata == [marker]
        assert Out.model_fields["flag"].metadata[0].threshold == 0.7
        schema = Out.model_json_schema()
        assert schema["properties"]["verdict"] == {
            "enum": ["a", "b"],
            "title": "Verdict",
            "type": "string",
        }
        assert "Decision" not in str(schema)


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
