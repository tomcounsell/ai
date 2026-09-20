"""Live arms and input loaders for the comparison runner (#3410).

Reference arms (the site's backend on ``main`` before the taxonomy):

* :func:`anthropic_arm`: Haiku with the prompt verbatim through the Anthropic
  leg, via ``run_typed`` with a task pinned ``backend=ANTHROPIC`` (no
  fallback exists on that route, so every error is the arm's own).
* :func:`openrouter_gemma_arm`: C15's ``main`` backend, gemma on OpenRouter's
  free tier through chat/completions, metered under the existing
  ``promise_detector`` purpose (``tools/paid_inference_meter``: one
  reservation per run, settled from the accumulated ``usage.cost``).

Candidate arms:

* :func:`ollama_arm`: granite through the Ollama leg, called directly rather
  than through ``run_typed``, because the router gives an ``OLLAMA`` task an
  Anthropic fallback and a fallback answer would count as granite agreement.
* :func:`anthropic_arm` again, for a site whose landing backend is Haiku and
  needs a measured number (C15).

Every arm has the :data:`~tools.classification_eval.ArmCall` shape and is
built lazily so importing this module touches no network client.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections.abc import Awaitable, Callable
from time import perf_counter
from typing import Any

from pydantic import BaseModel

from agent.llm.tasks import Backend, LLMTask, TaskKind
from config.models import MODEL_FAST, OLLAMA_CLASSIFIER_MODEL, OPENROUTER_GEMMA4_FREE
from tools.classification_eval import CASE_ID, PROJECT_KEY, Arm, Input, Price, Site

logger = logging.getLogger(__name__)

METER_PURPOSE = "promise_detector"
OPENROUTER_TIMEOUT_S = 60
OPENROUTER_FREE_MIN_INTERVAL_S = 3.2
"""Spacing between gemma requests: OpenRouter's free models allow about twenty
requests a minute, and a burst past that answers 429 on nearly every call."""
OPENROUTER_429_RETRIES = 4

HAIKU_PRICE = Price(
    model=MODEL_FAST,
    usd_per_mtoken_in=1.0,
    usd_per_mtoken_out=5.0,
    retrieved_at="2026-06-24",
    note="Anthropic list price for Claude Haiku 4.5",
)
GEMMA_FREE_PRICE = Price(
    model=OPENROUTER_GEMMA4_FREE,
    usd_per_mtoken_in=0.0,
    usd_per_mtoken_out=0.0,
    retrieved_at="2026-09-17",
    note="OpenRouter free tier (#3338); usage.cost on each response is the metered truth",
)
OPENROUTER_GEMMA4_PAID = OPENROUTER_GEMMA4_FREE.removesuffix(":free")
GEMMA_PAID_PRICE = Price(
    model=OPENROUTER_GEMMA4_PAID,
    usd_per_mtoken_in=0.09,
    usd_per_mtoken_out=0.30,
    retrieved_at="2026-09-19",
    note="OpenRouter list price for the same weights on the paid route; usage.cost is metered",
)
"""The paid route of C15's reference model, for a run made while the free
route is throttled upstream (Google AI Studio answers 429 on every call for
a while); the same weights, the same provider, a metered price."""
GRANITE_PRICE = Price(
    model=OLLAMA_CLASSIFIER_MODEL,
    usd_per_mtoken_in=0.0,
    usd_per_mtoken_out=0.0,
    retrieved_at="2026-09-19",
    note="local Ollama; no per-token price",
)


def _pinned_task(site_id: str, backend: Backend) -> LLMTask:
    return LLMTask(
        site=f"classification_eval.{site_id}", kind=TaskKind.CLASSIFICATION, backend=backend
    )


def anthropic_arm(site_id: str, *, model: str | None = None, name: str = "anthropic") -> Arm:
    """Haiku (or ``model``) through the Anthropic leg with no fallback."""
    from agent.llm import run_typed

    task = _pinned_task(site_id, Backend.ANTHROPIC)
    model_id = model or MODEL_FAST

    async def call(prompt: str, system: str | None, output_type: type[BaseModel]):
        output = await run_typed(
            prompt, output_type, task=task, project_key=PROJECT_KEY, model=model_id, system=system
        )
        return output, None

    price = HAIKU_PRICE if model_id == MODEL_FAST else Price(model_id, 0.0, 0.0, "unknown")
    return Arm(name=name, backend=Backend.ANTHROPIC.value, model=model_id, price=price, call=call)


def ollama_arm(site_id: str, *, name: str = "ollama") -> Arm:
    """Granite through the Ollama leg, directly, so a failure is a failure."""
    from agent.anthropic_client import _load_stack
    from agent.llm.backends import default_sdk_timeout
    from agent.llm.backends import ollama as ollama_leg
    from agent.llm.router import Route

    route = Route(Backend.OLLAMA, OLLAMA_CLASSIFIER_MODEL)

    async def call(prompt: str, system: str | None, output_type: type[BaseModel]):
        output = await ollama_leg.call(
            prompt,
            output_type,
            route,
            system=system,
            sdk_timeout=default_sdk_timeout(Backend.OLLAMA),
            slot_timeout=None,
            max_retries=0,
            deadline=None,
            stack=_load_stack(),
        )
        return output, 0.0

    return Arm(
        name=name,
        backend=Backend.OLLAMA.value,
        model=OLLAMA_CLASSIFIER_MODEL,
        price=GRANITE_PRICE,
        call=call,
    )


_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


def parse_json_output(text: str, output_type: type[BaseModel]) -> BaseModel:
    """Validate the first JSON object in a free-text reply against ``output_type``."""
    match = _JSON_OBJECT.search(text)
    if match is None:
        raise ValueError(f"no JSON object in reply: {text[:120]!r}")
    return output_type.model_validate_json(match.group(0))


class OpenRouterGemmaArm:
    """The chat/completions transport for the gemma reference arm, metered.

    ``reserve()`` before the run and ``settle()`` after; ``metering`` drops
    to ``"unknown"`` the moment a response carries no ``usage.cost``
    (charter §8: uncertain metering is never zero). Requests are spaced
    ``min_interval_s`` apart under one lock (the latency pass at concurrency
    4 shares it) and a 429 waits out ``Retry-After`` (else the interval) and
    retries up to :data:`OPENROUTER_429_RETRIES` times, so a rate limit is a
    pause rather than a reference error. ``transport`` and ``sleep`` are the
    test seams.
    """

    def __init__(
        self,
        api_key: str,
        *,
        model: str = OPENROUTER_GEMMA4_FREE,
        transport: Any | None = None,
        min_interval_s: float = OPENROUTER_FREE_MIN_INTERVAL_S,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._api_key = api_key
        self.model = model
        self.cost_usd = 0.0
        self.metering = "exact"
        self.reservation_id: str | None = None
        self._transport = transport
        self._min_interval_s = min_interval_s
        self._sleep = sleep or asyncio.sleep
        self._lock: asyncio.Lock | None = None
        self._last_sent = 0.0

    def reserve(self, calls: int, *, project_key: str = PROJECT_KEY) -> None:
        from tools.paid_inference_meter import Refusal, reserve

        outcome = reserve(
            project_key, max(0.01, 0.002 * calls), purpose=METER_PURPOSE, case_id=CASE_ID
        )
        if isinstance(outcome, Refusal):
            raise RuntimeError(f"paid-inference meter refused the gemma arm: {outcome.reason}")
        self.reservation_id = outcome.reservation_id

    def settle(self, *, project_key: str = PROJECT_KEY) -> None:
        from tools.paid_inference_meter import settle

        if self.reservation_id is None:
            return
        settle(project_key, self.reservation_id, self.cost_usd, metering=self.metering)
        self.reservation_id = None

    async def _post(self, client: Any, body: dict[str, Any]) -> tuple[Any, float]:
        """One paced request and the seconds spent waiting for it: on the
        limiter's lock and interval, and on a 429's retry pause."""
        from config.models import OPENROUTER_URL

        if self._lock is None:
            self._lock = asyncio.Lock()
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        waited = 0.0
        for attempt in range(OPENROUTER_429_RETRIES + 1):
            queued = perf_counter()
            async with self._lock:
                wait = self._last_sent + self._min_interval_s - perf_counter()
                if wait > 0:
                    await self._sleep(wait)
                waited += perf_counter() - queued
                self._last_sent = perf_counter()
                response = await client.post(OPENROUTER_URL, headers=headers, json=body)
            if response.status_code != 429 or attempt == OPENROUTER_429_RETRIES:
                return response, waited
            retry_after = response.headers.get("retry-after")
            try:
                pause = float(retry_after) if retry_after else self._min_interval_s
            except ValueError:
                pause = self._min_interval_s
            paused = perf_counter()
            await self._sleep(max(pause, self._min_interval_s))
            waited += perf_counter() - paused
        return response, waited

    async def __call__(self, prompt: str, system: str | None, output_type: type[BaseModel]):
        import httpx

        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        body = {
            "model": self.model,
            "messages": messages,
            "max_tokens": 200,
            "usage": {"include": True},
        }
        async with httpx.AsyncClient(
            timeout=OPENROUTER_TIMEOUT_S, transport=self._transport
        ) as client:
            response, waited = await self._post(client, body)
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        cost = (payload.get("usage") or {}).get("cost")
        if isinstance(cost, (int, float)):
            self.cost_usd += float(cost)
        else:
            self.metering = "unknown"
            cost = None
        content = str((payload.get("choices") or [{}])[0].get("message", {}).get("content") or "")
        return parse_json_output(content, output_type), cost, waited


def openrouter_gemma_arm(
    *, name: str = "openrouter_gemma", paid: bool = False
) -> tuple[Arm, OpenRouterGemmaArm]:
    """The metered gemma reference arm; the caller reserves and settles the
    transport. ``paid`` selects :data:`OPENROUTER_GEMMA4_PAID` for a run made
    while the free route is throttled upstream."""
    import os

    from config.settings import settings

    api_key = settings.api.openrouter_api_key or os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not configured; the gemma reference arm needs it")
    price = GEMMA_PAID_PRICE if paid else GEMMA_FREE_PRICE
    transport = OpenRouterGemmaArm(api_key, model=price.model)
    arm = Arm(name=name, backend="openrouter", model=transport.model, price=price, call=transport)
    return arm, transport


# --- inputs -----------------------------------------------------------------------------


def real_messages(
    limit: int, *, project_key: str = PROJECT_KEY, records: list[Any] | None = None
) -> list[Input]:
    """Real inbound human messages for ``project_key`` from the subconscious
    memory store: the bridge's per-message save (``source="human"``, keyed
    by the sender), excluding the TUI interaction summaries
    ``agent/tui_interaction_capture.py`` files under the same source with a
    ``tui-`` agent id. Deduped on content and ordered by a content digest so
    a re-run draws the same sample. ``records`` is the test seam for the
    ORM read.

    Refuses any project the blocking ``is_open_source`` cannot prove public
    (charter §7; ``valor`` is pinned there), so client messages never reach a
    candidate arm.
    """
    from tools.improvement_eligibility import is_open_source

    if project_key != PROJECT_KEY and not is_open_source(project_key):
        raise RuntimeError(f"project {project_key!r} is not provably open source; refusing (§7)")
    if records is None:
        from tools.memory_search import fetch_all_records

        records = fetch_all_records(project_key)
    seen: set[str] = set()
    picked: list[tuple[str, str]] = []
    for row in records:
        if getattr(row, "source", None) != "human":
            continue
        if str(getattr(row, "agent_id", "") or "").startswith("tui-"):
            continue
        text = (getattr(row, "content", "") or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        picked.append((hashlib.sha256(text.encode()).hexdigest(), text))
    picked.sort()
    return [Input(text, "real") for _, text in picked[:limit]]


def site_inputs(
    site: Site,
    real_limit: int,
    *,
    project_key: str = PROJECT_KEY,
    default: Callable[..., list[Input]] = real_messages,
) -> list[Input]:
    """The row's fixtures followed by its real inputs: the row's own loader
    when it names one (``Site.real_inputs``), else real inbound messages
    through ``default`` (:func:`real_messages`; the test seam)."""
    if site.real_inputs is not None:
        real = site.real_inputs(real_limit)
    else:
        real = default(real_limit, project_key=project_key)
    return site.fixtures() + real


def dump_inputs(inputs: list[Input]) -> str:
    """JSON lines for a saved sample, so a landing loop can replay one draw."""
    return "\n".join(
        json.dumps({"text": i.text, "source": i.source, "context": dict(i.context)}) for i in inputs
    )


def load_inputs(text: str) -> list[Input]:
    """Inverse of :func:`dump_inputs`."""
    out = []
    for line in text.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        out.append(Input(row["text"], row["source"], row.get("context") or {}))
    return out
