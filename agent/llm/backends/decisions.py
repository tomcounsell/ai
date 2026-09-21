"""The decisions (TypeSafe Jev) leg of :func:`agent.llm.run_typed` (#3421).

Runs one structured-decision call against TypeSafe's native endpoint
(:data:`config.models.TYPESAFE_DECISIONS_URL`) for a task the router resolved
to ``Backend.DECISIONS``. It differs from the Anthropic and Ollama legs in
what it sends and how it pays:

* **The wire shape** is ``POST {model, state, questions}`` with
  ``Authorization: Bearer <settings.api.typesafe_api_key>``. ``state`` is the
  prompt (``f"{system}\\n\\n{prompt}"`` when a system prompt is given; an
  empty one is absent). ``questions`` is built by :func:`questions_for` from
  the output type's fields: a ``Literal[...]`` (or ``Literal[...] | None``,
  or a string ``Enum``) field is a ``choice`` question whose criteria keys
  are the options; a ``bool`` field is a ``noul`` question with
  ``true``/``false`` criteria. Rubric text and instructions come from the
  field's :class:`agent.llm.tasks.Decision` marker, else the field's
  description, else ``"What is the {name}?"``. The answer is
  ``{answers: {id: {type, choice, probabilities, confidence} |
  {type, noul}}, usage: {input_tokens, output_tokens}}``; :func:`decode_answers`
  is the inverse: a ``choice`` is the option, a ``noul`` is ``True`` at or
  above the marker's ``threshold``, a ``float`` field named ``confidence`` is
  the minimum ``choice`` confidence (else the chosen ``noul`` side's
  probability), and every other field takes its default (``""`` for a
  required ``str``, ``None`` for a required optional). A ``choice`` under the
  marker's ``min_confidence`` is a ``validation`` failure: the leg abstains
  and the wrapper's Ollama fallback answers on the same inputs.
* **Nine failure classes plus the missing key**, each an
  :class:`~agent.llm.LLMCallError` with a ``reason`` and one ERROR line
  (``[agent.llm] decisions leg <reason> for model=<model>: <detail>``): a
  non-200 status (``transport``, with the status and the first 200 characters
  of the body's ``detail.message``), an unparseable body (``transport``), a
  question id missing from ``answers``, a ``choice`` outside the options, a
  ``choice`` under ``min_confidence``, and a ``model_validate`` failure (all
  ``validation``), ``httpx.TimeoutException`` (``timeout``), any other
  ``httpx.HTTPError`` such as a ``ConnectError`` (``transport``), a meter
  ``Refusal`` (``transport``, before any request), and a missing key
  (``transport``, before any I/O). The bearer value is scrubbed from every
  message. The endpoint's error bodies are untrusted data: truncated, never
  evaluated. One POST is one attempt: no retry, no semaphore (``slot_timeout``
  and ``max_retries`` are accepted for the leg protocol and unused), so a
  429 or 529 is a fast fall to granite inside the caller's budget.
* **One SDK-level timer**: ``AsyncHTTPClient(timeout=sdk_timeout)`` is the
  only timer around the request (hotfix #1055: never a coroutine-level
  timer around an LLM call); the deadline re-check (fallback calls only)
  runs once before the client is built. ``CancelledError`` passes through:
  the ``async with`` closes the client and the envelope is untouched.
* **The metering envelope**: the meter (``tools.paid_inference_meter``)
  counts whole cents and writes one ``spend_receipt`` row per settlement, so
  a per-call reservation at Jev's 32k-state bound (0.001344 USD) would
  reserve nothing and write a row per inbound message. :class:`SpendEnvelope`
  reserves one cent per process under purpose ``structured_decision`` on the
  RSI case, accumulates ``usage.input_tokens × JEV_PRICE_USD_PER_MTOKEN /
  1e6`` per 200 (output tokens are free), and settles exactly once and
  re-reserves when the next call's bound would not fit or the UTC day rolls
  over. A 200 without ``input_tokens``, or a ``None`` price constant, marks
  the envelope ``unknown``, never zero (charter §8). The roll is synchronous
  from the headroom check to the new reservation id, so concurrent callers
  in one event loop roll once. A meter error while settling is logged at
  WARNING and never fails the answered call; the reconcile sweep receipts an
  open reservation as ``unknown``. The runner's decisions arm passes its own
  per-run envelope through ``envelope=``.

Import-safety contract (#3001): module scope here is stdlib and our own code
only; ``httpx.AsyncClient`` comes from the ``stack`` the wrapper resolved, so
``dataclasses.replace(real_stack, AsyncHTTPClient=...)`` on the wrapper's
``_load_stack`` seam is the test seam for this leg, and httpx exceptions are
classified by class name along the ``__mro__`` rather than by import.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from enum import Enum
from time import monotonic
from types import NoneType, UnionType
from typing import TYPE_CHECKING, Any, Literal, Union, get_args, get_origin

from agent.llm.backends import bound_to_deadline
from agent.llm.errors import LLMCallError, Reason
from agent.llm.tasks import Decision
from config.models import JEV_PRICE_USD_PER_MTOKEN, TYPESAFE_DECISIONS_URL
from config.settings import settings
from tools import paid_inference_meter as meter

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pydantic import BaseModel
    from pydantic.fields import FieldInfo

    from agent.anthropic_client import LLMStack
    from agent.llm.router import Route

logger = logging.getLogger(__name__)

#: The RSI improvement case every comparison record and receipt attaches to;
#: mirrors ``tools.classification_eval.CASE_ID`` (this package never imports
#: ``tools.classification_eval``). Pinned equal by the leg's tests.
CASE_ID = "1ec40086ca1d422e90ef747775ff7f64"
PROJECT_KEY = "valor"
METER_PURPOSE = "structured_decision"
ENVELOPE_USD = 0.01
"""One cent: the amount the process singleton reserves at a time."""
CALL_BOUND_USD = 32_000 * 0.000000042
"""The most one call can cost: the 32k-token ``state`` ceiling at the pinned price."""
MAX_CHOICES = 255
"""The endpoint's cap on ``choice`` options."""
DETAIL_CHARS = 200
"""How much of an error body's ``detail.message`` reaches a log line."""

_CONFIDENCE_FIELD = "confidence"


class AnswerError(ValueError):
    """The endpoint's answers cannot fill the output type (``reason="validation"``)."""


class _ResponseError(Exception):
    """A non-200 status or an unparseable body (``reason="transport"``)."""


# --------------------------------------------------------------------------
# Question construction and answer decoding (pure, transport-free)
# --------------------------------------------------------------------------


def _marker(field: FieldInfo) -> Decision | None:
    return next((m for m in field.metadata if isinstance(m, Decision)), None)


def _unwrap_optional(annotation: Any) -> tuple[Any, bool]:
    """``X | None`` to ``(X, True)``; anything else to ``(annotation, False)``."""
    if get_origin(annotation) in (Union, UnionType):
        args = [a for a in get_args(annotation) if a is not NoneType]
        if len(args) == 1 and len(args) != len(get_args(annotation)):
            return args[0], True
    return annotation, False


def _choice_options(annotation: Any) -> list[Any] | None:
    """The closed option set of a ``Literal`` or ``Enum`` field, else ``None``."""
    inner, _ = _unwrap_optional(annotation)
    if get_origin(inner) is Literal:
        return list(get_args(inner))
    if isinstance(inner, type) and issubclass(inner, Enum):
        return [member.value for member in inner]
    return None


def _is_confidence(name: str, field: FieldInfo) -> bool:
    return name == _CONFIDENCE_FIELD and field.annotation is float


def questions_for(output_type: type[BaseModel]) -> dict[str, dict[str, Any]]:
    """The ``questions`` body for ``output_type``, keyed by field name.

    Raises ``ValueError`` naming the field when the type has no ``Literal``
    or ``bool`` field to ask about, when a ``Literal`` member is not a
    string, when it has more than :data:`MAX_CHOICES` options, or when a
    required field is of a type the decoder cannot fill; a site whose type
    the leg cannot serve therefore fails at its first call, never silently.
    """
    questions: dict[str, dict[str, Any]] = {}
    type_name = output_type.__name__
    for name, field in output_type.model_fields.items():
        marker = _marker(field)
        options = _choice_options(field.annotation)
        if marker is not None:
            instructions = marker.question
        else:
            instructions = field.description or f"What is the {name}?"
        rubrics = dict(marker.criteria) if marker is not None and marker.criteria else {}
        if options is not None:
            bad = [o for o in options if not isinstance(o, str)]
            if bad:
                raise ValueError(
                    f"{type_name}.{name}: Literal members must be strings, got {bad!r}"
                )
            if len(options) > MAX_CHOICES:
                raise ValueError(
                    f"{type_name}.{name}: {len(options)} options exceed "
                    f"the endpoint's cap of {MAX_CHOICES}"
                )
            questions[name] = {
                "type": "choice",
                "instructions": instructions,
                "criteria": {option: rubrics.get(option) for option in options},
            }
        elif field.annotation is bool:
            questions[name] = {
                "type": "noul",
                "instructions": instructions,
                "criteria": {
                    "true": rubrics.get("true", "yes"),
                    "false": rubrics.get("false", "no"),
                },
            }
        elif _is_confidence(name, field) or not field.is_required():
            continue
        elif field.annotation is str or _unwrap_optional(field.annotation)[1]:
            continue
        else:
            raise ValueError(
                f"{type_name}.{name}: the decisions leg cannot fill a required "
                f"{field.annotation!r}; ask it as a Literal or bool, or give it a default"
            )
    if not questions:
        raise ValueError(f"{type_name} has no Literal or bool field for the decisions leg to ask")
    return questions


def _answer(answers: dict[str, Any], name: str) -> dict[str, Any]:
    answer = answers.get(name)
    if not isinstance(answer, dict):
        raise AnswerError(f"{name}: no answer in the response")
    return answer


def _probability(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _choice_confidence(answer: dict[str, Any], choice: str) -> float:
    """``probabilities[choice]``, else the answer's ``confidence``, else 0.0."""
    probabilities = answer.get("probabilities")
    if isinstance(probabilities, dict):
        from_vector = _probability(probabilities.get(choice))
        if from_vector is not None:
            return from_vector
    return _probability(answer.get("confidence")) or 0.0


def decode_answers(output_type: type[BaseModel], answers: dict[str, Any]) -> dict[str, Any]:
    """The values to ``model_validate`` from the endpoint's ``answers``.

    The inverse of :func:`questions_for`. Raises :class:`AnswerError` (the
    ``validation`` reason) for a missing question id, a ``choice`` outside
    the options, a ``choice`` under the marker's ``min_confidence``, or a
    ``noul`` with no probability.
    """
    values: dict[str, Any] = {}
    choice_confidences: list[float] = []
    noul_confidences: list[float] = []
    confidence_field: str | None = None
    for name, field in output_type.model_fields.items():
        marker = _marker(field)
        options = _choice_options(field.annotation)
        if options is not None:
            answer = _answer(answers, name)
            choice = answer.get("choice")
            if choice not in options:
                raise AnswerError(f"{name}: choice {choice!r} is not one of {options}")
            confidence = _choice_confidence(answer, choice)
            floor = marker.min_confidence if marker is not None else 0.0
            if confidence < floor:
                raise AnswerError(
                    f"{name}: choice {choice!r} at confidence {confidence:.3f} is under "
                    f"min_confidence {floor}; abstaining to the fallback leg"
                )
            choice_confidences.append(confidence)
            values[name] = choice
        elif field.annotation is bool:
            answer = _answer(answers, name)
            noul = _probability(answer.get("noul"))
            if noul is None:
                raise AnswerError(f"{name}: noul answer carries no probability")
            threshold = marker.threshold if marker is not None else 0.5
            chosen = noul >= threshold
            values[name] = chosen
            noul_confidences.append(noul if chosen else 1.0 - noul)
        elif _is_confidence(name, field):
            confidence_field = name
        elif not field.is_required():
            values[name] = field.get_default(call_default_factory=True)
        elif field.annotation is str:
            values[name] = ""
        else:
            values[name] = None
    if confidence_field is not None:
        if choice_confidences:
            values[confidence_field] = min(choice_confidences)
        elif noul_confidences:
            values[confidence_field] = min(noul_confidences)
        else:
            values[confidence_field] = 0.0
    return values


# --------------------------------------------------------------------------
# The spend envelope
# --------------------------------------------------------------------------


def _utc_day_key() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


class SpendEnvelope:
    """One meter reservation amortised over many sub-cent calls.

    ``reserve(usd)`` opens a reservation; ``fits(bound)`` says whether one
    more call at ``bound`` fits inside it today; ``add_tokens`` prices a
    response; ``mark_unknown`` records that a response could not be priced;
    ``settle()`` closes the reservation exactly once; ``ensure_headroom()``
    is the per-call check that settles and re-reserves when needed. Every
    meter call is a blocking Redis round trip with no ``await`` between the
    check and the new reservation id (Race 1).
    """

    def __init__(
        self,
        *,
        project_key: str = PROJECT_KEY,
        purpose: str = METER_PURPOSE,
        case_id: str | None = CASE_ID,
        envelope_usd: float = ENVELOPE_USD,
    ) -> None:
        self.project_key = project_key
        self.purpose = purpose
        self.case_id = case_id
        self.envelope_usd = envelope_usd
        self.reservation_id: str | None = None
        self.day_key: str | None = None
        self.accumulated_usd = 0.0
        self.metering = "exact"

    def reserve(self, usd: float) -> str | None:
        """Open a reservation for ``usd``; the refusal reason when the meter refuses."""
        outcome = meter.reserve(self.project_key, usd, purpose=self.purpose, case_id=self.case_id)
        if isinstance(outcome, meter.Refusal):
            return outcome.reason
        self.reservation_id = outcome.reservation_id
        self.day_key = outcome.day_key
        self.envelope_usd = usd
        self.accumulated_usd = 0.0
        self.metering = "exact"
        return None

    def fits(self, bound: float) -> bool:
        return (
            self.reservation_id is not None
            and self.day_key == _utc_day_key()
            and self.accumulated_usd + bound <= self.envelope_usd
        )

    def add_tokens(self, input_tokens: int) -> None:
        price = JEV_PRICE_USD_PER_MTOKEN
        if price is None:
            self.mark_unknown()
            return
        self.accumulated_usd += input_tokens * price / 1e6

    def mark_unknown(self) -> None:
        self.metering = "unknown"

    def settle(self) -> None:
        """Settle the open reservation once; a no-op with nothing reserved.

        A meter error is logged at WARNING and never raised: the answer was
        already produced, and the reconcile sweep receipts an open
        reservation as ``unknown``. State is cleared either way so the same
        reservation is never settled twice from here.
        """
        reservation_id = self.reservation_id
        if reservation_id is None:
            return
        usd, metering = self.accumulated_usd, self.metering
        self.reservation_id = None
        self.day_key = None
        self.accumulated_usd = 0.0
        self.metering = "exact"
        try:
            meter.settle(self.project_key, reservation_id, usd, metering=metering)
        except Exception as e:
            logger.warning(
                "[agent.llm] decisions envelope settle failed for reservation=%s "
                "(usd=%.6f metering=%s); the reconcile sweep will receipt it: %s",
                reservation_id,
                usd,
                metering,
                e,
            )

    def ensure_headroom(self, bound: float = CALL_BOUND_USD) -> str | None:
        """Settle and re-reserve when ``bound`` no longer fits; the refusal reason if any."""
        if self.fits(bound):
            return None
        self.settle()
        return self.reserve(self.envelope_usd)


_ENVELOPE = SpendEnvelope()
"""The process singleton every wrapper-routed call meters through."""


# --------------------------------------------------------------------------
# The leg
# --------------------------------------------------------------------------


def _reason_for(exc: BaseException) -> Reason:
    names = {cls.__name__ for cls in type(exc).__mro__}
    if "TimeoutException" in names:
        return "timeout"
    if isinstance(exc, AnswerError) or "ValidationError" in names:
        return "validation"
    return "transport"


def _scrub(text: str, key: str) -> str:
    return text.replace(key, "***") if key else text


def _detail(response: Any, key: str) -> str:
    """The first :data:`DETAIL_CHARS` of the body's ``detail.message``, else ``""``.

    Scrubbed here, before any exception carries it, so a body that quotes the
    bearer back never reaches a traceback either.
    """
    try:
        message = response.json()["detail"]["message"]
    except (ValueError, KeyError, TypeError):
        return ""
    return _scrub(message[:DETAIL_CHARS], key) if isinstance(message, str) else ""


def _record_usage(envelope: SpendEnvelope, payload: Any) -> None:
    usage = payload.get("usage") if isinstance(payload, dict) else None
    tokens = usage.get("input_tokens") if isinstance(usage, dict) else None
    if isinstance(tokens, int) and not isinstance(tokens, bool) and tokens >= 0:
        envelope.add_tokens(tokens)
    else:
        envelope.mark_unknown()


def _failure(reason: Reason, model: str, detail: str) -> LLMCallError:
    logger.error("[agent.llm] decisions leg %s for model=%s: %s", reason, model, detail)
    return LLMCallError(
        f"decisions leg failed ({reason}) for model={model}: {detail}", reason=reason
    )


async def call(
    prompt: str,
    output_type: type[BaseModel],
    route: Route,
    *,
    system: str | None,
    sdk_timeout: float,
    slot_timeout: float | None,
    max_retries: int | None,
    deadline: float | None = None,
    stack: LLMStack,
    envelope: SpendEnvelope | None = None,
) -> BaseModel:
    """Run one structured-decision call on ``route.model`` against TypeSafe.

    ``slot_timeout`` and ``max_retries`` are accepted for the leg protocol
    and unused. ``envelope`` defaults to the process singleton; the runner's
    decisions arm passes its own per-run envelope.
    """
    sdk_timeout = bound_to_deadline(sdk_timeout, deadline, monotonic(), leg="decisions")
    key = settings.api.typesafe_api_key
    if not key:
        raise _failure(
            "transport",
            route.model,
            "settings.api.typesafe_api_key is None (the TypeSafe key is not configured); "
            "no request made",
        )
    questions = questions_for(output_type)
    state = prompt if not system else f"{system}\n\n{prompt}"
    envelope = _ENVELOPE if envelope is None else envelope
    refused = envelope.ensure_headroom()
    if refused is not None:
        raise _failure(
            "transport", route.model, f"paid-inference meter refused the envelope: {refused}"
        )
    body = {"model": route.model, "state": state, "questions": questions}
    try:
        async with stack.AsyncHTTPClient(timeout=sdk_timeout) as client:
            response = await client.post(
                TYPESAFE_DECISIONS_URL, headers={"Authorization": f"Bearer {key}"}, json=body
            )
        if response.status_code != 200:
            raise _ResponseError(f"HTTP {response.status_code}: {_detail(response, key)}")
        try:
            payload = response.json()
        except ValueError as e:
            envelope.mark_unknown()
            raise _ResponseError(f"HTTP 200 with an unparseable JSON body: {e}") from e
        _record_usage(envelope, payload)
        answers = payload.get("answers") if isinstance(payload, dict) else None
        values = decode_answers(output_type, answers if isinstance(answers, dict) else {})
        result = output_type.model_validate(values)
    except Exception as e:
        reason = _reason_for(e)
        detail = _scrub(str(e), key)
        logger.error(
            "[agent.llm] decisions leg %s for model=%s: %s",
            reason,
            route.model,
            detail,
            exc_info=reason != "timeout",
        )
        raise LLMCallError(
            f"decisions leg failed ({reason}) for model={route.model}: {detail}", reason=reason
        ) from e
    return result
