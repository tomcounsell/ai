"""A real local HTTP upstream speaking Jev's and OpenRouter's wire formats
for the scripted judge and checks, its bodies shaped like the live ones in
`tests/fixtures/judgement_*.json`, on its own thread and loop.

`/fixed/<answer>/<leg>` (leg `jev` or `open_weight`) answers every question
0.95 on one label: `precise`/`thin` (`one_line_ask`), `true`/`false`, or a
comma list where each question takes the first it has, else its last label.
"""

import asyncio
import json
import math
import threading
from pathlib import Path

from aiohttp import web

from core.judgement import LOOPBACK_KEY, JudgementPort
from core.settings import JEV_MODEL, OPEN_WEIGHT_MODEL, OPEN_WEIGHT_PROVIDER_NAME
from tests import ports

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FIXED = {"precise": "precise", "thin": "one_line_ask", "true": "true", "false": "false"}


def _template(name: str, fallback: dict) -> dict:
    path = FIXTURES / name
    return json.loads(path.read_text()) if path.exists() else fallback


JEV_TEMPLATE = _template(
    "judgement_jev.json",
    {"model": JEV_MODEL, "answers": {}, "usage": {"input_tokens": 0, "output_tokens": 0}},
)
OW_TEMPLATE = _template(
    "judgement_open_weight.json",
    {
        "model": OPEN_WEIGHT_MODEL,
        "provider": OPEN_WEIGHT_PROVIDER_NAME,
        "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "{}"}}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "cost": 0},
    },
)


def questions_of(leg: str, body: dict) -> dict[str, list[str]]:
    """Each question's labels, read from the request."""
    if leg == "jev":
        qs = body["questions"]
        return {k: list(q["criteria"]) if q["type"] == "choice" else ["true", "false"] for k, q in qs.items()}
    schema = body["response_format"]["json_schema"]["schema"]
    return {qid: list(s["properties"]) for qid, s in schema["properties"].items() if s["type"] == "object"}


def fixed_probs(answer: str, labels: list[str]) -> dict[str, float]:
    pick = next((p for p in (FIXED.get(a, a) for a in answer.split(",")) if p in labels), labels[-1])
    rest = [label for label in labels if label != pick]
    return {label: (0.95 if label == pick else round(0.05 / len(rest), 6)) for label in labels}


class Upstream:
    def __init__(self):
        self._ready = threading.Event()
        self.loop = asyncio.new_event_loop()
        self._failed: BaseException | None = None
        threading.Thread(target=self._serve, daemon=True).start()
        self._ready.wait()
        if self._failed is not None:
            raise self._failed

    def _serve(self) -> None:
        """Listen, then serve; a listen that fails is raised by `__init__`."""
        asyncio.set_event_loop(self.loop)
        try:
            app = web.Application()
            app.router.add_post("/fixed/{answer}/{leg}", self.handle)
            runner = web.AppRunner(app, handler_cancellation=True)
            self.loop.run_until_complete(runner.setup())
            site = web.TCPSite(runner, "127.0.0.1", ports.listen())
            self.loop.run_until_complete(site.start())
            self.url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        except BaseException as exc:  # noqa: BLE001  raised again by `__init__`
            self._failed = exc
            return
        finally:
            self._ready.set()
        self.loop.run_forever()

    def port(self, *, fixed: str) -> JudgementPort:
        from tools.jev import Jev
        from tools.open_weight import OpenWeight

        base = f"{self.url}/fixed/{fixed}"
        jev = Jev(f"{base}/jev", LOOPBACK_KEY, timeout_s=5)
        ow = OpenWeight(f"{base}/open_weight", LOOPBACK_KEY, timeout_s=5)
        return JudgementPort({"jev": jev, "open_weight": ow})

    async def handle(self, request: web.Request) -> web.Response:
        leg = request.match_info["leg"]
        raw = await request.read()
        body = json.loads(raw)
        labels = questions_of(leg, body)
        answers = {qid: fixed_probs(request.match_info["answer"], ls) for qid, ls in labels.items()}
        tokens = math.ceil(len(raw) / 3)
        if leg == "jev":
            out = json.loads(json.dumps(JEV_TEMPLATE))
            out["model"] = JEV_MODEL
            out["answers"] = {}
            for qid, p in answers.items():
                if set(labels[qid]) == {"true", "false"}:
                    out["answers"][qid] = {"type": "noul", "noul": p.get("true")}
                else:
                    choice = {"choice": max(p, key=p.get), "probabilities": p, "confidence": 0.5}
                    out["answers"][qid] = {"type": "choice", **choice}
            out["usage"] = {"input_tokens": tokens, "output_tokens": 12}
        else:
            out = json.loads(json.dumps(OW_TEMPLATE))
            out["model"] = OPEN_WEIGHT_MODEL
            out["provider"] = OPEN_WEIGHT_PROVIDER_NAME
            out["choices"][0]["message"]["content"] = json.dumps({"notes": "compared", **answers})
            cost = round(tokens * 0.14e-6 + 30 * 0.8e-6, 10)
            out["usage"] = {"prompt_tokens": tokens, "completion_tokens": 30, "cost": cost}
        return web.json_response(out)


_SHARED: Upstream | None = None


def shared() -> Upstream:
    global _SHARED
    if _SHARED is None:
        _SHARED = Upstream()
    return _SHARED
