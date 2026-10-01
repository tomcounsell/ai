"""The primary judgement leg: TypeSafe's Jev structured-decision endpoint.

`POST {model, state, questions}` with a bearer key; the answer is
`{model, answers: {id: {type: "choice", choice, probabilities, confidence} |
{type: "noul", noul}}, usage: {input_tokens, output_tokens}}` (probed
2026-10-02; https://docs.typesafe.ai/api). Every question of a task goes in
one call. The inputs are the `state`, a JSON object of the task's fields;
the question text and rubrics are the only instructions.

Effect class `read`: it asks a question and changes nothing. One POST is one
attempt; the port's fallback leg is the retry. A response naming any model
but the pinned one is malformed, so a release on TypeSafe's side cannot
answer under the pinned name unnoticed.
"""

import json
import math
from collections.abc import Mapping

from core import judgement
from core.judgement import DATA_ONLY, JudgementTask, Kind, LegAnswer, LegError
from core.settings import JEV_MODEL, settings

# Jev's billed input over the request body's bytes / 3 (see `Jev.estimate`).
OVERHEAD_RATIO = 1.25
OVERHEAD_FIXED = 300
OVERHEAD_PER_QUESTION = 50


class Jev:
    name = "jev"
    effect_class = "read"
    fixed_text = DATA_ONLY

    def __init__(self, url: str, key: str, *, model: str = JEV_MODEL, timeout_s: float | None = None):
        self.endpoint = url
        self._key = key
        self.model = model
        self.timeout_s = timeout_s or settings.jev_timeout_s
        self.max_input_tokens = settings.jev_max_input_tokens
        self.max_output_tokens = 0  # Jev's output tokens are free

    def body(self, task: JudgementTask, inputs: Mapping[str, str]) -> dict:
        questions = {}
        for q in task.questions:
            if q.kind is Kind.CHOICE:
                questions[q.id] = {
                    "type": "choice",
                    "instructions": f"{q.text}\n\n{DATA_ONLY}",
                    "criteria": dict(q.labels),
                }
            else:
                questions[q.id] = {
                    "type": "noul",
                    "instructions": f"{q.text}\n\n{DATA_ONLY}",
                    "criteria": {"true": q.labels["true"], "false": q.labels["false"]},
                }
        return {"model": self.model, "state": dict(inputs), "questions": questions}

    def estimate(self, task: JudgementTask, inputs: Mapping[str, str]) -> int:
        """Input tokens, estimated high enough to reserve: Jev bills a prompt
        of its own around the request. Over its 35 calibration calls of
        2026-10-02 it billed 0.95 to 1.59 times the body's bytes / 3, and at
        most 189 tokens more (`docs/plans/m1-3-judgement.md`, Patch round 1),
        so the estimate is a quarter over bytes / 3 plus 300 tokens and 50
        per question. All 35 were single-question judge calls of at most 1,016
        estimated tokens (bytes / 3); breadth and governance re-check this from their own
        rows when 1.4 calibrates them."""
        body = judgement.estimate_tokens(json.dumps(self.body(task, inputs)))
        return math.ceil(body * OVERHEAD_RATIO) + OVERHEAD_FIXED + OVERHEAD_PER_QUESTION * len(task.questions)

    async def ask(self, task: JudgementTask, inputs: Mapping[str, str]) -> LegAnswer | LegError:
        got = await judgement.post(self.endpoint, self._key, self.body(task, inputs), self.timeout_s)
        if isinstance(got, LegError):
            return got
        status, raw = got
        if status != 200:
            return LegError("http_status", "none", status=status, what=f"HTTP {status}")
        return decode(task, raw, self.model)


def _usage(payload) -> dict | None:
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        return None
    tokens = usage.get("input_tokens")
    if isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 0:
        return None
    out = usage.get("output_tokens", 0)
    if isinstance(out, bool) or not isinstance(out, int) or out < 0:
        out = 0
    return {"input_tokens": tokens, "output_tokens": out, "reported_usd": None}


def decode(task: JudgementTask, raw: bytes, model: str) -> LegAnswer | LegError:
    """A 200 body to an answer; total: anything unreadable is `malformed`,
    charged by its usage when the body has one."""
    try:
        payload = json.loads(raw)
    except ValueError, UnicodeDecodeError:
        return LegError("malformed", "unknown", status=200, what="the body is not JSON")
    usage = _usage(payload)
    billed = "usage" if usage else "unknown"
    if not isinstance(payload, dict):
        return LegError("malformed", billed, status=200, usage=usage, what="the body is not an object")
    if payload.get("model") != model:
        return LegError(
            "malformed", billed, status=200, usage=usage, what="the answering model is not the pinned one"
        )
    answers = payload.get("answers")
    if not isinstance(answers, dict):
        return LegError("malformed", billed, status=200, usage=usage, what="no answers")
    probabilities: dict[str, dict] = {}
    choices: dict[str, str | None] = {}
    for q in task.questions:
        a = answers.get(q.id)
        if not isinstance(a, dict):
            return LegError("malformed", billed, status=200, usage=usage, what=f"no answer to {q.id}")
        if q.kind is Kind.CHOICE:
            probs = a.get("probabilities")
            if not isinstance(probs, dict):
                return LegError(
                    "malformed", billed, status=200, usage=usage, what=f"no probabilities for {q.id}"
                )
            probabilities[q.id] = dict(probs)
            choice = a.get("choice")
            choices[q.id] = choice if isinstance(choice, str) and choice in q.labels else None
        else:
            p = a.get("noul")
            if isinstance(p, bool) or not isinstance(p, int | float) or not 0 <= p <= 1:
                return LegError(
                    "malformed", billed, status=200, usage=usage, what=f"no probability for {q.id}"
                )
            probabilities[q.id] = {"true": float(p), "false": 1.0 - float(p)}
            choices[q.id] = None
    return LegAnswer(probabilities, choices, usage, model)
