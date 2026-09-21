"""The decisions (TypeSafe Jev) leg of ``run_typed`` (#3421).

``agent/llm/backends/decisions.py::call`` is the body ``run_typed`` runs for
a task the router resolves to ``Backend.DECISIONS``. These tests drive it
directly with a stack whose ``AsyncHTTPClient`` is a real ``httpx.AsyncClient``
over an ``httpx.MockTransport`` (``dataclasses.replace(real_stack,
AsyncHTTPClient=...)`` on the wrapper's ``_load_stack`` seam, #3001), so the
leg's own request building, response decoding and client lifecycle run
end to end and no socket opens. The meter is faked at the functions the
envelope calls, so nothing here touches Redis.

Recorded fixtures are the spike-1 bodies from the plan, retrieved 2026-09-21
against ``https://api.typesafe.ai/v1/systemone`` with the pinned model.

What is pinned:

* The success path returns the output type with ``Literal`` fields from
  ``choice`` answers, ``bool`` fields from ``noul`` at the field's threshold,
  ``confidence`` from the answer, and every other field at its default.
* Each of the nine failure classes plus the missing key raises
  ``LLMCallError`` with its ``reason``, logs exactly one ERROR line, and
  neither the log nor the message carries any fragment of the bearer key.
* The per-call client is closed on success and on every failure.
* ``questions_for`` is total over every classification output type on
  ``main`` and raises ``ValueError`` naming the field for the three shapes
  the leg cannot ask or fill; ``decode_answers`` is its inverse.
* The spend envelope: one reservation per cent, token-priced accumulation,
  ``unknown`` on a missing ``input_tokens`` or a ``None`` price, exactly one
  settle per reservation across a cancelled call, a failed call, a headroom
  roll, a day rollover, and four concurrent callers.
"""

from __future__ import annotations

import ast
import asyncio
import dataclasses
import importlib
import json
import logging
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

import httpx
import pytest
from pydantic import BaseModel, Field

from agent.anthropic_client import _load_stack
from agent.llm import LLMCallError
from agent.llm.backends import decisions as leg
from agent.llm.backends import default_sdk_timeout
from agent.llm.router import Route
from agent.llm.tasks import Backend, Decision, TaskKind, declared_sites
from config.models import JEV, JEV_PRICE_USD_PER_MTOKEN, MODEL_INFO, TYPESAFE_DECISIONS_URL
from config.settings import settings
from tools import paid_inference_meter as meter

REPO_ROOT = Path(__file__).resolve().parents[2]
ROUTE = Route(Backend.DECISIONS, JEV)
FAKE_KEY = "ts-unit-test-key-9f3a7c1e5b2d8046"
_FRAGMENTS = (FAKE_KEY, FAKE_KEY[:12], FAKE_KEY[-12:], FAKE_KEY[8:24])


# --------------------------------------------------------------------------
# Output types and recorded bodies
# --------------------------------------------------------------------------


class Terminus(BaseModel):
    """C2-shaped: one ``choice`` question, nothing else."""

    verdict: Annotated[
        Literal["RESPOND", "REACT", "SILENT"],
        Decision(
            "How should the bridge act on this message?",
            criteria={"RESPOND": "a reply is needed", "SILENT": "nothing is needed"},
            min_confidence=0.6,
        ),
    ]


class Needs(BaseModel):
    """C1-shaped: one ``noul`` question at a raised threshold."""

    needs_response: Annotated[
        bool,
        Decision(
            "Does this message need a reply or action?",
            criteria={"true": "asks or reports something", "false": "closes the loop"},
            threshold=0.7,
        ),
    ]


class Mixed(BaseModel):
    """A choice, a noul, a confidence and every fillable non-question shape."""

    intent: Literal["interjection", "new_work"] = Field(description="What kind of message?")
    recall: bool = False
    confidence: float
    reason: str
    job_id: str | None
    note: str = "n/a"


# Spike-1 bodies (2026-09-21): a 4-way choice at confidence 1.0 with a full
# probability vector, a noul at 0.30, usage with tokens and no cost.
OK_TERMINUS = {
    "model": "jev-1.13.0",
    "answers": {
        "verdict": {
            "type": "choice",
            "choice": "RESPOND",
            "probabilities": {"RESPOND": 0.91, "REACT": 0.06, "SILENT": 0.03},
            "confidence": 0.91,
        }
    },
    "usage": {"input_tokens": 417, "output_tokens": 64},
}
OK_NEEDS = {
    "model": "jev-1.13.0",
    "answers": {"needs_response": {"type": "noul", "noul": 0.30}},
    "usage": {"input_tokens": 356, "output_tokens": 58},
}
OK_MIXED = {
    "model": "jev-1.13.0",
    "answers": {
        "intent": {
            "type": "choice",
            "choice": "new_work",
            "probabilities": {"interjection": 0.2, "new_work": 0.8},
            "confidence": 0.8,
        },
        "recall": {"type": "noul", "noul": 0.65},
    },
    "usage": {"input_tokens": 512, "output_tokens": 70},
}
ERR_401 = {"detail": {"error_type": "authentication_error", "message": "Invalid API key."}}
ERR_400 = {"detail": {"error_type": "api_usage_error", "message": "Invalid request."}}


# --------------------------------------------------------------------------
# Seams: the HTTP client and the meter
# --------------------------------------------------------------------------


class Transport:
    """A ``MockTransport`` handler that records requests and checks their shape."""

    def __init__(self, respond, *, output_type=None):
        self._respond = respond
        self.output_type = output_type
        self.requests: list[httpx.Request] = []
        self.bodies: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = json.loads(request.content)
        self.bodies.append(body)
        assert request.headers["Authorization"] == f"Bearer {FAKE_KEY}"
        assert str(request.url) == TYPESAFE_DECISIONS_URL
        assert body["model"] == JEV
        if self.output_type is not None:
            assert body["questions"] == leg.questions_for(self.output_type)
        return self._respond(request)


def _json(status: int, payload: dict):
    return lambda request: httpx.Response(status, json=payload)


def _raising(exc_type, message: str):
    def handler(request):
        raise exc_type(message, request=request)

    return handler


@pytest.fixture
def clients():
    """Every client the leg built, so a test can assert none outlived the call."""
    return []


@pytest.fixture
def stack_for(monkeypatch, clients):
    """A stack whose ``AsyncHTTPClient`` is httpx over the given handler; also
    pins the fake key on ``settings.api`` and gives the leg a fresh envelope."""

    def build(handler):
        def AsyncHTTPClient(**kwargs):  # noqa: N802 - mirrors the stack field name
            client = httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)
            clients.append(client)
            return client

        return dataclasses.replace(_load_stack(), AsyncHTTPClient=AsyncHTTPClient)

    monkeypatch.setattr(settings.api, "typesafe_api_key", FAKE_KEY)
    monkeypatch.setattr(leg, "_ENVELOPE", leg.SpendEnvelope())
    return build


class FakeMeter:
    """Stands in for ``paid_inference_meter.reserve`` / ``settle``."""

    def __init__(self, *, refuse: str | None = None, day_key: str = "2026-09-21"):
        self.refuse = refuse
        self.day_key = day_key
        self.reserves: list[tuple[float, str, str | None]] = []
        self.settles: list[tuple[str, float, str]] = []
        self.settle_raises: Exception | None = None

    def reserve(self, project_key, usd, *, purpose, case_id=None, **_):
        if self.refuse:
            return meter.Refusal(self.refuse)
        self.reserves.append((usd, purpose, case_id))
        return meter.Reservation(
            f"res-{len(self.reserves)}",
            project_key,
            round(usd * 100),
            purpose,
            case_id,
            self.day_key,
        )

    def settle(self, project_key, reservation_id, usd, *, metering):
        if self.settle_raises is not None:
            raise self.settle_raises
        self.settles.append((reservation_id, usd, metering))


@pytest.fixture
def fake_meter(monkeypatch):
    fake = FakeMeter()
    monkeypatch.setattr(meter, "reserve", fake.reserve)
    monkeypatch.setattr(meter, "settle", fake.settle)
    monkeypatch.setattr(leg, "_utc_day_key", lambda: fake.day_key)
    return fake


async def _call(stack, output_type=Terminus, *, prompt="user: thanks, that looks great", **kw):
    kwargs = dict(system=None, sdk_timeout=3.0, slot_timeout=None, max_retries=None)
    kwargs.update(kw)
    return await leg.call(prompt, output_type, ROUTE, stack=stack, **kwargs)


def _error_records(caplog):
    return [r for r in caplog.records if r.levelno == logging.ERROR]


# --------------------------------------------------------------------------
# Constants, timer, stack seam (Task 1)
# --------------------------------------------------------------------------


class TestConstants:
    def test_jev_is_a_pinned_version_never_an_alias(self):
        assert JEV == "jev-1.13.0"
        assert "latest" not in JEV and "preview" not in JEV

    def test_endpoint_is_typesafe_native(self):
        assert TYPESAFE_DECISIONS_URL == "https://api.typesafe.ai/v1/systemone"

    def test_price_and_model_info_agree(self):
        """Input tokens cost 0.042 USD per million, output tokens are free
        (https://docs.typesafe.ai/models, retrieved 2026-09-21)."""
        assert JEV_PRICE_USD_PER_MTOKEN == 0.042
        info = MODEL_INFO[JEV]
        assert info["provider"] == "typesafe"
        assert info["endpoint"] == "systemone"
        assert info["context_window"] == 32_000
        assert info["input_cost_per_mtoken"] == JEV_PRICE_USD_PER_MTOKEN
        assert info["output_cost_per_mtoken"] == 0.0
        assert info["price_retrieved_at"] == "2026-09-21"

    def test_call_bound_is_the_32k_state_at_the_pinned_price(self):
        assert leg.CALL_BOUND_USD == pytest.approx(32_000 * 0.042 / 1e6)
        assert leg.ENVELOPE_USD == 0.01

    def test_case_id_mirrors_the_runner(self):
        from tools.classification_eval import CASE_ID

        assert leg.CASE_ID == CASE_ID


class TestTimer:
    def test_default_sdk_timeout_reads_the_settings_field(self, monkeypatch):
        assert default_sdk_timeout(Backend.DECISIONS) == settings.timeouts.decisions_sdk_s == 3.0
        monkeypatch.setattr(settings.timeouts, "decisions_sdk_s", 1.25)
        assert default_sdk_timeout(Backend.DECISIONS) == 1.25


class TestStackSeam:
    def test_stack_carries_httpx_async_client(self):
        assert _load_stack().AsyncHTTPClient is httpx.AsyncClient


# --------------------------------------------------------------------------
# questions_for
# --------------------------------------------------------------------------


class TestQuestionsFor:
    def test_choice_from_a_marked_literal(self):
        questions = leg.questions_for(Terminus)
        assert questions == {
            "verdict": {
                "type": "choice",
                "instructions": "How should the bridge act on this message?",
                "criteria": {
                    "RESPOND": "a reply is needed",
                    "REACT": None,
                    "SILENT": "nothing is needed",
                },
            }
        }

    def test_noul_from_a_marked_bool(self):
        assert leg.questions_for(Needs) == {
            "needs_response": {
                "type": "noul",
                "instructions": "Does this message need a reply or action?",
                "criteria": {
                    "true": "asks or reports something",
                    "false": "closes the loop",
                },
            }
        }

    def test_unmarked_fields_get_description_or_generated_instructions(self):
        questions = leg.questions_for(Mixed)
        assert set(questions) == {"intent", "recall"}
        assert questions["intent"]["instructions"] == "What kind of message?"
        assert questions["intent"]["criteria"] == {"interjection": None, "new_work": None}
        assert questions["recall"] == {
            "type": "noul",
            "instructions": "What is the recall?",
            "criteria": {"true": "yes", "false": "no"},
        }

    def test_optional_literal_is_a_choice(self):
        class Out(BaseModel):
            job_id: Literal["a1", "b2"] | None

        assert leg.questions_for(Out)["job_id"]["criteria"] == {"a1": None, "b2": None}

    def test_string_enum_is_a_choice(self):
        class Bucket(StrEnum):
            ONE = "one"
            TWO = "two"

        class Out(BaseModel):
            bucket: Bucket

        assert leg.questions_for(Out)["bucket"]["criteria"] == {"one": None, "two": None}

    @pytest.mark.parametrize(
        ("annotations", "field"),
        [
            ({"reason": (str, ...), "confidence": (float, ...)}, None),
            ({"verdict": (Literal[1, 2], ...)}, "verdict"),
            ({"verdict": (Literal["a", "b"], ...), "count": (int, ...)}, "count"),
            ({"verdict": (Literal[tuple(str(i) for i in range(256))], ...)}, "verdict"),
        ],
        ids=["nothing-to-ask", "non-string-literal", "unfillable-required", "over-255"],
    )
    def test_value_error_names_the_field(self, annotations, field):
        from pydantic import create_model

        output_type = create_model("Out", **annotations)
        with pytest.raises(ValueError) as exc_info:
            leg.questions_for(output_type)
        assert (field or "Out") in str(exc_info.value)

    @pytest.mark.parametrize(
        "site",
        [d for d in declared_sites() if d.task.kind is TaskKind.CLASSIFICATION],
        ids=lambda d: d.task.site,
    )
    def test_every_classification_output_type_on_main_is_askable(self, site):
        """Each site's output type, located by AST from its ``run_typed`` call's
        second positional argument, builds at least one question."""
        tree = ast.parse((REPO_ROOT / site.path).read_text(encoding="utf-8"))
        names: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = getattr(node.func, "id", getattr(node.func, "attr", ""))
            if func != "run_typed" or len(node.args) < 2:
                continue
            task = next((k.value for k in node.keywords if k.arg == "task"), None)
            if task is None or ast.unparse(task) != site.name:
                continue
            arg = node.args[1]
            if isinstance(arg, ast.Name):
                names.add(arg.id)
        assert names, f"{site.task.site}: no run_typed call with a positional output type"
        module = importlib.import_module(site.path[:-3].replace("/", "."))
        types = _output_types(module, tree, names)
        assert types, f"{site.task.site}: {names} resolve to no BaseModel"
        for output_type in types:
            questions = leg.questions_for(output_type)
            assert questions, f"{output_type.__name__} asks nothing"
            assert all(q["type"] in {"choice", "noul"} for q in questions.values())


def _output_types(module, tree, names: set[str]) -> list[type[BaseModel]]:
    """Resolve each name to a model class: directly, or through a module-level
    ``name = A if ... else B`` assignment (``tools/classifier.py``'s
    ``output_model``)."""
    found: list[type[BaseModel]] = []
    for name in sorted(names):
        candidate = getattr(module, name, None)
        if isinstance(candidate, type) and issubclass(candidate, BaseModel):
            found.append(candidate)
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == name for t in node.targets)
                and isinstance(node.value, ast.IfExp)
            ):
                for branch in (node.value.body, node.value.orelse):
                    cls = getattr(module, ast.unparse(branch), None)
                    if isinstance(cls, type) and issubclass(cls, BaseModel):
                        found.append(cls)
    return found


# --------------------------------------------------------------------------
# decode_answers
# --------------------------------------------------------------------------


class TestDecodeAnswers:
    def test_choice_noul_confidence_and_fillers(self):
        values = leg.decode_answers(Mixed, OK_MIXED["answers"])
        assert values == {
            "intent": "new_work",
            "recall": True,
            "confidence": 0.8,
            "reason": "",
            "job_id": None,
            "note": "n/a",
        }
        assert Mixed.model_validate(values).intent == "new_work"

    def test_noul_below_threshold_is_false(self):
        assert leg.decode_answers(Needs, OK_NEEDS["answers"]) == {"needs_response": False}

    def test_noul_exactly_at_threshold_is_true(self):
        answers = {"needs_response": {"type": "noul", "noul": 0.7}}
        assert leg.decode_answers(Needs, answers) == {"needs_response": True}

    def test_confidence_from_a_noul_is_the_chosen_side_s_probability(self):
        class Out(BaseModel):
            flag: bool
            confidence: float

        assert leg.decode_answers(Out, {"flag": {"type": "noul", "noul": 0.3}}) == {
            "flag": False,
            "confidence": pytest.approx(0.7),
        }
        assert leg.decode_answers(Out, {"flag": {"type": "noul", "noul": 0.9}}) == {
            "flag": True,
            "confidence": 0.9,
        }

    def test_confidence_is_the_minimum_over_choice_answers(self):
        class Out(BaseModel):
            a: Literal["x", "y"]
            b: Literal["p", "q"]
            confidence: float

        answers = {
            "a": {"type": "choice", "choice": "x", "probabilities": {"x": 0.9, "y": 0.1}},
            "b": {"type": "choice", "choice": "q", "probabilities": {"p": 0.4, "q": 0.6}},
        }
        assert leg.decode_answers(Out, answers)["confidence"] == 0.6

    def test_choice_without_probabilities_falls_back_to_confidence_then_zero(self):
        class Out(BaseModel):
            a: Literal["x", "y"]
            confidence: float

        with_conf = {"a": {"type": "choice", "choice": "x", "confidence": 0.55}}
        assert leg.decode_answers(Out, with_conf)["confidence"] == 0.55
        bare = {"a": {"type": "choice", "choice": "x"}}
        assert leg.decode_answers(Out, bare)["confidence"] == 0.0

    def test_choice_exactly_at_min_confidence_passes(self):
        answers = {"verdict": {"type": "choice", "choice": "SILENT", "confidence": 0.6}}
        assert leg.decode_answers(Terminus, answers) == {"verdict": "SILENT"}

    @pytest.mark.parametrize(
        ("output_type", "answers", "match"),
        [
            (Terminus, {}, "verdict"),
            (
                Terminus,
                {"verdict": {"type": "choice", "choice": "MAYBE", "confidence": 0.9}},
                "MAYBE",
            ),
            (
                Terminus,
                {"verdict": {"type": "choice", "choice": "REACT", "confidence": 0.59}},
                "min_confidence",
            ),
            (Needs, {"needs_response": {"type": "noul"}}, "noul"),
        ],
        ids=["missing-id", "unknown-choice", "under-min-confidence", "malformed-noul"],
    )
    def test_answer_errors(self, output_type, answers, match):
        with pytest.raises(leg.AnswerError, match=match):
            leg.decode_answers(output_type, answers)


# --------------------------------------------------------------------------
# call: success
# --------------------------------------------------------------------------


class TestSuccess:
    async def test_returns_the_output_type(self, stack_for, fake_meter, clients):
        transport = Transport(_json(200, OK_TERMINUS), output_type=Terminus)

        result = await _call(stack_for(transport))

        assert isinstance(result, Terminus)
        assert result.verdict == "RESPOND"
        assert transport.bodies[0]["state"] == "user: thanks, that looks great"
        assert all(c.is_closed for c in clients) and len(clients) == 1

    async def test_client_carries_the_sdk_timer(self, stack_for, fake_meter, clients):
        await _call(stack_for(Transport(_json(200, OK_TERMINUS))), sdk_timeout=1.5)

        assert clients[0].timeout == httpx.Timeout(1.5)

    async def test_noul_answers_at_the_field_threshold(self, stack_for, fake_meter):
        result = await _call(stack_for(Transport(_json(200, OK_NEEDS), output_type=Needs)), Needs)

        assert result.needs_response is False

    async def test_mixed_type_fills_every_field(self, stack_for, fake_meter):
        result = await _call(stack_for(Transport(_json(200, OK_MIXED), output_type=Mixed)), Mixed)

        assert (result.intent, result.recall, result.confidence) == ("new_work", True, 0.8)
        assert (result.reason, result.job_id, result.note) == ("", None, "n/a")

    @pytest.mark.parametrize(
        ("system", "state"),
        [
            (None, "user: hi"),
            ("", "user: hi"),
            ("You are a router.", "You are a router.\n\nuser: hi"),
        ],
        ids=["none", "empty-is-absent", "joined"],
    )
    async def test_state_is_prompt_with_the_system_prefix(
        self, stack_for, fake_meter, system, state
    ):
        transport = Transport(_json(200, OK_TERMINUS))

        await _call(stack_for(transport), prompt="user: hi", system=system)

        assert transport.bodies[0]["state"] == state

    async def test_slot_timeout_and_max_retries_are_accepted_and_unused(
        self, stack_for, fake_meter
    ):
        transport = Transport(_json(200, OK_TERMINUS))

        await _call(stack_for(transport), slot_timeout=0.001, max_retries=5)

        assert len(transport.requests) == 1

    async def test_no_error_log_on_success(self, stack_for, fake_meter, caplog):
        with caplog.at_level(logging.ERROR, logger="agent.llm.backends.decisions"):
            await _call(stack_for(Transport(_json(200, OK_TERMINUS))))

        assert _error_records(caplog) == []


# --------------------------------------------------------------------------
# call: the nine failure classes plus the missing key
# --------------------------------------------------------------------------

_FAILURES = {
    "non-200-401": (_json(401, ERR_401), "transport", "401"),
    "non-200-400": (_json(400, ERR_400), "transport", "Invalid request"),
    "invalid-json": (lambda r: httpx.Response(200, content=b"<html>oops"), "transport", "JSON"),
    "missing-answer-id": (_json(200, {"answers": {}, "usage": {}}), "validation", "verdict"),
    "unknown-choice": (
        _json(
            200,
            {"answers": {"verdict": {"type": "choice", "choice": "MAYBE", "confidence": 0.9}}},
        ),
        "validation",
        "MAYBE",
    ),
    "under-min-confidence": (
        _json(
            200,
            {"answers": {"verdict": {"type": "choice", "choice": "REACT", "confidence": 0.2}}},
        ),
        "validation",
        "min_confidence",
    ),
    "read-timeout": (_raising(httpx.ReadTimeout, "read timed out"), "timeout", "timed out"),
    "connect-error": (
        _raising(httpx.ConnectError, "[Errno 8] nodename nor servname provided"),
        "transport",
        "Errno 8",
    ),
}


class TestFailureReasons:
    @pytest.mark.parametrize(
        ("handler", "reason", "detail"), list(_FAILURES.values()), ids=list(_FAILURES)
    )
    async def test_each_failure_carries_its_reason_and_logs_once(
        self, stack_for, fake_meter, clients, caplog, handler, reason, detail
    ):
        with caplog.at_level(logging.ERROR, logger="agent.llm.backends.decisions"):
            with pytest.raises(LLMCallError) as exc_info:
                await _call(stack_for(Transport(handler)))

        assert exc_info.value.reason == reason
        assert detail in str(exc_info.value)
        errors = _error_records(caplog)
        assert len(errors) == 1
        assert (
            errors[0]
            .getMessage()
            .startswith(f"[agent.llm] decisions leg {reason} for model={JEV}: ")
        )
        _assert_no_key_fragment(caplog.text, str(exc_info.value))
        assert all(c.is_closed for c in clients) and len(clients) == 1

    async def test_connect_error_is_transport(self, stack_for, fake_meter):
        """The ninth class: any ``httpx.HTTPError`` that is not a timeout."""
        handler = _raising(httpx.ConnectError, "connection refused")

        with pytest.raises(LLMCallError) as exc_info:
            await _call(stack_for(Transport(handler)))

        assert exc_info.value.reason == "transport"
        assert isinstance(exc_info.value.__cause__, httpx.ConnectError)

    async def test_model_validate_failure_is_validation(self, stack_for, fake_meter, caplog):
        class Strict(BaseModel):
            flag: bool
            confidence: float = Field(ge=0.0, le=0.5)

        body = {"answers": {"flag": {"type": "noul", "noul": 0.9}}, "usage": {"input_tokens": 9}}
        with caplog.at_level(logging.ERROR, logger="agent.llm.backends.decisions"):
            with pytest.raises(LLMCallError) as exc_info:
                await _call(stack_for(Transport(_json(200, body))), Strict)

        assert exc_info.value.reason == "validation"
        assert len(_error_records(caplog)) == 1

    async def test_meter_refusal_is_transport_before_any_request(
        self, stack_for, fake_meter, clients, caplog
    ):
        fake_meter.refuse = "day_exhausted"
        transport = Transport(_json(200, OK_TERMINUS))

        with caplog.at_level(logging.ERROR, logger="agent.llm.backends.decisions"):
            with pytest.raises(LLMCallError) as exc_info:
                await _call(stack_for(transport))

        assert exc_info.value.reason == "transport"
        assert "day_exhausted" in str(exc_info.value)
        assert transport.requests == [] and clients == []
        assert len(_error_records(caplog)) == 1
        _assert_no_key_fragment(caplog.text, str(exc_info.value))

    async def test_meter_error_at_reserve_is_transport_before_any_request(
        self, stack_for, fake_meter, clients, caplog, monkeypatch
    ):
        """A raw exception from ``meter.reserve`` (a Redis connection error
        under the meter's unguarded ``eval`` / ``hset``) is an
        ``LLMCallError(reason="transport")`` like a refusal, so the wrapper's
        ``except LLMCallError`` runs the granite fallback; escaping as itself
        would skip the fallback and leave the site on its fail-safe."""

        class RedisDownError(ConnectionError):
            pass

        def reserve(*a, **kw):
            raise RedisDownError("Error 61 connecting to localhost:6379. Connection refused.")

        monkeypatch.setattr(meter, "reserve", reserve)
        transport = Transport(_json(200, OK_TERMINUS))

        with caplog.at_level(logging.ERROR, logger="agent.llm.backends.decisions"):
            with pytest.raises(LLMCallError) as exc_info:
                await _call(stack_for(transport))

        assert exc_info.value.reason == "transport"
        assert "Error 61 connecting" in str(exc_info.value)
        assert isinstance(exc_info.value.__cause__, RedisDownError)
        assert transport.requests == [] and clients == []
        assert len(_error_records(caplog)) == 1

    async def test_key_echoed_in_a_choice_reaches_no_log_surface(
        self, stack_for, fake_meter, caplog
    ):
        """A 200 whose ``choice`` is the bearer: ``decode_answers`` echoes the
        value in its ``AnswerError``, and the ERROR line prints the traceback
        (``exc_info``), which renders every exception in the chain from its
        args. Neither the message, the traceback, ``str(exc)`` nor
        ``str(exc.__cause__)`` may carry a fragment of the key."""
        body = {"answers": {"verdict": {"type": "choice", "choice": FAKE_KEY, "confidence": 0.9}}}

        with caplog.at_level(logging.ERROR, logger="agent.llm.backends.decisions"):
            with pytest.raises(LLMCallError) as exc_info:
                await _call(stack_for(Transport(_json(200, body))))

        assert exc_info.value.reason == "validation"
        records = _error_records(caplog)
        assert len(records) == 1 and records[0].exc_info is not None
        assert "Traceback" in caplog.text and "AnswerError" in caplog.text
        assert "choice '***' is not one of" in str(exc_info.value)
        assert isinstance(exc_info.value.__cause__, leg.AnswerError)
        _assert_no_key_fragment(caplog.text, str(exc_info.value), str(exc_info.value.__cause__))

    async def test_missing_key_is_transport_before_any_io(
        self, stack_for, fake_meter, clients, caplog, monkeypatch
    ):
        monkeypatch.setattr(settings.api, "typesafe_api_key", None)
        transport = Transport(_json(200, OK_TERMINUS))

        with caplog.at_level(logging.ERROR, logger="agent.llm.backends.decisions"):
            with pytest.raises(LLMCallError) as exc_info:
                await _call(stack_for(transport))

        assert exc_info.value.reason == "transport"
        assert "typesafe_api_key is None" in str(exc_info.value)
        assert transport.requests == [] and clients == [] and fake_meter.reserves == []
        assert len(_error_records(caplog)) == 1

    async def test_unaskable_type_raises_value_error_before_any_io(
        self, stack_for, fake_meter, clients
    ):
        class Prose(BaseModel):
            summary: str

        with pytest.raises(ValueError, match="Prose"):
            await _call(stack_for(Transport(_json(200, OK_TERMINUS))), Prose)

        assert clients == [] and fake_meter.reserves == []

    async def test_key_echoed_by_the_endpoint_is_scrubbed(self, stack_for, fake_meter, caplog):
        """An untrusted error body may quote the bearer back; neither the log
        line nor the exception message may carry it."""
        echo = {"detail": {"message": f"bad key {FAKE_KEY} for this account"}}

        with caplog.at_level(logging.ERROR, logger="agent.llm.backends.decisions"):
            with pytest.raises(LLMCallError) as exc_info:
                await _call(stack_for(Transport(_json(401, echo))))

        assert "bad key *** for this account" in str(exc_info.value)
        _assert_no_key_fragment(caplog.text, str(exc_info.value))

    async def test_key_straddling_the_detail_boundary_is_scrubbed(
        self, stack_for, fake_meter, caplog
    ):
        """A key that crosses the DETAIL_CHARS cut must be scrubbed before the
        cut: truncating first leaves a prefix the whole-key scrub cannot match."""
        echo = {"detail": {"message": "x" * 180 + FAKE_KEY + " tail"}}

        with caplog.at_level(logging.ERROR, logger="agent.llm.backends.decisions"):
            with pytest.raises(LLMCallError) as exc_info:
                await _call(stack_for(Transport(_json(401, echo))))

        records = _error_records(caplog)
        assert len(records) == 1
        for text in (str(exc_info.value), records[0].getMessage()):
            for start in range(len(FAKE_KEY) - 7):
                window = FAKE_KEY[start : start + 8]
                assert window not in text, f"key window {window!r} leaked"

    async def test_error_body_detail_is_truncated(self, stack_for, fake_meter):
        long = {"detail": {"message": "x" * 1000}}

        with pytest.raises(LLMCallError) as exc_info:
            await _call(stack_for(Transport(_json(422, long))))

        assert "x" * 200 in str(exc_info.value)
        assert "x" * 201 not in str(exc_info.value)

    async def test_cancellation_passes_through_and_closes_the_client(
        self, stack_for, fake_meter, clients, caplog
    ):
        started = asyncio.Event()

        async def slow(request):
            started.set()
            await asyncio.sleep(30)
            return httpx.Response(200, json=OK_TERMINUS)

        def AsyncHTTPClient(**kwargs):  # noqa: N802 - mirrors the stack field name
            client = httpx.AsyncClient(transport=httpx.MockTransport(slow), **kwargs)
            clients.append(client)
            return client

        stack = dataclasses.replace(_load_stack(), AsyncHTTPClient=AsyncHTTPClient)
        task = asyncio.create_task(_call(stack))
        await started.wait()
        task.cancel()
        with caplog.at_level(logging.ERROR, logger="agent.llm.backends.decisions"):
            with pytest.raises(asyncio.CancelledError):
                await task

        assert clients[0].is_closed
        assert _error_records(caplog) == []


def _assert_no_key_fragment(*texts: str) -> None:
    for text in texts:
        for fragment in _FRAGMENTS:
            assert fragment not in text, f"key fragment {fragment!r} leaked"


# --------------------------------------------------------------------------
# call: deadline
# --------------------------------------------------------------------------


class TestDeadline:
    async def test_under_half_a_second_raises_timeout_without_a_client(
        self, stack_for, fake_meter, clients, monkeypatch
    ):
        monkeypatch.setattr(leg, "monotonic", lambda: 100.0)

        with pytest.raises(LLMCallError) as exc_info:
            await _call(stack_for(Transport(_json(200, OK_TERMINUS))), deadline=100.4)

        assert exc_info.value.reason == "timeout"
        assert clients == [] and fake_meter.reserves == []

    async def test_remainder_bounds_the_sdk_timer(
        self, stack_for, fake_meter, clients, monkeypatch
    ):
        monkeypatch.setattr(leg, "monotonic", lambda: 100.0)

        await _call(stack_for(Transport(_json(200, OK_TERMINUS))), deadline=102.0)

        assert clients[0].timeout == httpx.Timeout(2.0)


# --------------------------------------------------------------------------
# The spend envelope
# --------------------------------------------------------------------------


class TestEnvelope:
    async def test_first_call_reserves_one_cent_under_the_purpose(self, stack_for, fake_meter):
        await _call(stack_for(Transport(_json(200, OK_TERMINUS))))

        assert fake_meter.reserves == [(0.01, "structured_decision", leg.CASE_ID)]
        assert fake_meter.settles == []

    async def test_token_priced_accumulation(self, stack_for, fake_meter):
        stack = stack_for(Transport(_json(200, OK_TERMINUS)))

        await _call(stack)
        await _call(stack)

        env = leg._ENVELOPE
        assert env.accumulated_usd == pytest.approx(2 * 417 * 0.042 / 1e6)
        assert env.metering == "exact"
        assert len(fake_meter.reserves) == 1

    async def test_usage_without_input_tokens_marks_unknown_and_still_returns(
        self, stack_for, fake_meter
    ):
        body = {**OK_TERMINUS, "usage": {"output_tokens": 64}}

        result = await _call(stack_for(Transport(_json(200, body))))

        assert result.verdict == "RESPOND"
        assert leg._ENVELOPE.metering == "unknown"
        leg._ENVELOPE.settle()
        assert fake_meter.settles == [("res-1", 0.0, "unknown")]

    async def test_none_price_marks_unknown(self, stack_for, fake_meter, monkeypatch):
        monkeypatch.setattr(leg, "JEV_PRICE_USD_PER_MTOKEN", None)

        await _call(stack_for(Transport(_json(200, OK_TERMINUS))))

        assert leg._ENVELOPE.metering == "unknown"
        assert leg._ENVELOPE.accumulated_usd == 0.0

    async def test_headroom_roll_settles_once_and_reserves_again(self, stack_for, fake_meter):
        stack = stack_for(Transport(_json(200, OK_TERMINUS)))
        await _call(stack)
        env = leg._ENVELOPE
        spent = leg.ENVELOPE_USD - leg.CALL_BOUND_USD + 1e-6
        env.accumulated_usd = spent

        await _call(stack)

        assert fake_meter.settles == [("res-1", pytest.approx(spent), "exact")]
        assert len(fake_meter.reserves) == 2
        assert env.reservation_id == "res-2"
        assert env.accumulated_usd == pytest.approx(417 * 0.042 / 1e6)

    async def test_day_rollover_settles_once_and_reserves_again(self, stack_for, fake_meter):
        stack = stack_for(Transport(_json(200, OK_TERMINUS)))
        await _call(stack)
        fake_meter.day_key = "2026-09-22"

        await _call(stack)

        assert [s[0] for s in fake_meter.settles] == ["res-1"]
        assert len(fake_meter.reserves) == 2

    async def test_failed_call_neither_settles_nor_accumulates(self, stack_for, fake_meter):
        with pytest.raises(LLMCallError):
            await _call(stack_for(Transport(_json(401, ERR_401))))

        assert fake_meter.settles == []
        assert leg._ENVELOPE.accumulated_usd == 0.0
        assert leg._ENVELOPE.metering == "exact"

    async def test_cancelled_call_leaves_the_envelope_untouched(self, stack_for, fake_meter):
        async def slow(request):
            await asyncio.sleep(30)
            return httpx.Response(200, json=OK_TERMINUS)

        task = asyncio.create_task(_call(stack_for(Transport(slow))))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert fake_meter.settles == []
        assert leg._ENVELOPE.accumulated_usd == 0.0
        assert len(fake_meter.reserves) == 1

    async def test_four_concurrent_callers_roll_once(self, stack_for, fake_meter):
        """Race 1: the roll is synchronous from the check to the new id."""
        stack = stack_for(Transport(_json(200, OK_TERMINUS)))
        await _call(stack)
        leg._ENVELOPE.accumulated_usd = leg.ENVELOPE_USD

        await asyncio.gather(*(_call(stack) for _ in range(4)))

        assert len(fake_meter.settles) == 1
        assert len(fake_meter.reserves) == 2

    async def test_settle_is_a_noop_with_nothing_reserved(self, fake_meter):
        leg.SpendEnvelope().settle()

        assert fake_meter.settles == []

    async def test_settle_twice_settles_once(self, stack_for, fake_meter):
        await _call(stack_for(Transport(_json(200, OK_TERMINUS))))

        leg._ENVELOPE.settle()
        leg._ENVELOPE.settle()

        assert len(fake_meter.settles) == 1

    async def test_settle_error_is_logged_and_never_fails_the_call(
        self, stack_for, fake_meter, caplog
    ):
        stack = stack_for(Transport(_json(200, OK_TERMINUS)))
        await _call(stack)
        fake_meter.settle_raises = ConnectionError("redis down")
        leg._ENVELOPE.accumulated_usd = leg.ENVELOPE_USD

        with caplog.at_level(logging.WARNING, logger="agent.llm.backends.decisions"):
            result = await _call(stack)

        assert result.verdict == "RESPOND"
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1 and "redis down" in warnings[0].getMessage()
        assert len(fake_meter.reserves) == 2

    async def test_a_caller_supplied_envelope_is_used_instead_of_the_singleton(
        self, stack_for, fake_meter
    ):
        own = leg.SpendEnvelope()
        assert own.reserve(0.5) is None

        await _call(stack_for(Transport(_json(200, OK_TERMINUS))), envelope=own)

        assert own.accumulated_usd == pytest.approx(417 * 0.042 / 1e6)
        assert leg._ENVELOPE.reservation_id is None
        assert fake_meter.reserves == [(0.5, "structured_decision", leg.CASE_ID)]

    def test_reserve_returns_the_refusal_reason(self, fake_meter):
        fake_meter.refuse = "INVALID_AMOUNT"

        assert leg.SpendEnvelope().reserve(0.01) == "INVALID_AMOUNT"


# --------------------------------------------------------------------------
# Module invariants
# --------------------------------------------------------------------------


def test_no_third_party_import_at_module_scope():
    """#3001: the leg takes its client from the stack."""
    tree = ast.parse(Path(leg.__file__).read_text())
    imported: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not imported & {"httpx", "openai", "pydantic_ai", "anthropic"}, imported


def test_no_wait_for_and_no_per_call_receipt_inside_the_leg():
    source = Path(leg.__file__).read_text()
    assert "wait_for" not in source
    assert "record_receipt" not in source and "settle_from_response" not in source
    assert "openrouter" not in source.lower()


def test_broad_excepts_are_the_post_block_and_the_settle_guard():
    """``call`` has exactly one ``except Exception`` (around the POST and the
    decode); ``SpendEnvelope.settle`` has the one that keeps a meter error
    from failing an answered call; nothing else catches broadly."""
    tree = ast.parse(Path(leg.__file__).read_text())

    def broad(fn: ast.AST) -> int:
        return sum(
            1
            for node in ast.walk(fn)
            if isinstance(node, ast.ExceptHandler)
            and isinstance(node.type, ast.Name)
            and node.type.id == "Exception"
        )

    functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert broad(functions["call"]) == 1
    assert broad(functions["settle"]) == 1
    assert broad(tree) == 2
