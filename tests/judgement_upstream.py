"""A real local HTTP upstream speaking Jev's and Google's Decisions API wire
formats for the scripted judge and checks, its bodies shaped like the live
ones in `tests/fixtures/judgement_*.json`, on its own thread and loop.

`/fixed/<answer>/<leg>` (leg `jev` or `gemini`) answers every question
0.95 on one label: `precise`/`thin` (`one_line_ask`), `true`/`false`, or a
comma list where each question takes the first it has, else its last label;
`even` splits a question evenly (an abstain), as the whole answer or in the list. The query may add
`delay` (seconds before answering), `status` (an error status instead), and
`refuse=1` (the leg's own over-the-limit refusal).
"""

import asyncio
import json
import math
import threading
from pathlib import Path

from aiohttp import web

from core.judgement import LOOPBACK_KEY, JudgementPort
from core.settings import JEV_MODEL
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
GEMINI_TEMPLATE = _template("judgement_gemini.json", {"answers": {}, "usage": {}})


def questions_of(leg: str, body: dict) -> dict[str, list[str]]:
    """Each question's labels, read from the request."""
    qs = body["questions"]
    return {k: list(q["criteria"]) if q["type"] == "choice" else ["true", "false"] for k, q in qs.items()}


def fixed_probs(answer: str, labels: list[str]) -> dict[str, float]:
    pick = labels[-1]
    for a in answer.split(","):
        if a == "even":
            return {label: 1 / len(labels) for label in labels}
        if FIXED.get(a, a) in labels:
            pick = FIXED.get(a, a)
            break
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

    def port(self, *, fixed: str, gemini: str | None = None, gemini_timeout_s: float = 5) -> JudgementPort:
        """Both legs answering `fixed`; Gemini at `gemini` instead when given
        (an answer and a query, `even?delay=1`). The shared fixtures' port
        drains after every judgement, so a test reads the shadow row with
        the rest."""
        from tools.gemini_decisions import GeminiDecisions
        from tools.jev import Jev

        jev = Jev(f"{self.url}/fixed/{fixed}/jev", LOOPBACK_KEY, timeout_s=5)
        g_answer, _, query = (gemini or fixed).partition("?")
        g_url = f"{self.url}/fixed/{g_answer}/gemini" + (f"?{query}" if query else "")
        legs = {"jev": jev, "gemini": GeminiDecisions(g_url, LOOPBACK_KEY, timeout_s=gemini_timeout_s)}
        return DrainingPort(legs) if gemini is None else JudgementPort(legs)

    async def handle(self, request: web.Request) -> web.Response:
        leg, q = request.match_info["leg"], request.query
        raw = await request.read()
        await asyncio.sleep(float(q.get("delay", 0)))
        if "status" in q:
            return web.json_response({"error": {"message": "scripted"}}, status=int(q["status"]))
        if q.get("refuse"):
            if leg == "jev":
                return web.json_response({"detail": {"error_type": "max_tokens_exceeded"}}, status=400)
            message = "The input token count exceeds the maximum number of tokens allowed 262144."
            return web.json_response({"error": {"message": message, "code": "invalid_request"}}, status=400)
        body = json.loads(raw)
        labels = questions_of(leg, body)
        answers = {qid: fixed_probs(request.match_info["answer"], ls) for qid, ls in labels.items()}
        tokens = math.ceil(len(raw) / 3)
        out = json.loads(json.dumps(JEV_TEMPLATE if leg == "jev" else GEMINI_TEMPLATE))
        out["answers"] = {}
        for qid, p in answers.items():
            if set(labels[qid]) == {"true", "false"}:
                out["answers"][qid] = {"type": "noul", "noul": p.get("true"), "confidence": 0.5}
            else:
                choice = {"choice": max(p, key=p.get), "probabilities": p, "confidence": 0.5}
                out["answers"][qid] = {"type": "choice", **choice}
        if leg == "jev":
            out["model"] = JEV_MODEL
            out["usage"] = {"input_tokens": tokens, "output_tokens": 12}
        else:
            out["usage"] = {"total_tokens": tokens, "total_input_tokens": tokens, "total_output_tokens": 0}
        return web.json_response(out)


class DrainingPort(JudgementPort):
    """The port the shared fixtures use: every judgement waits for its
    shadowed fallback call before it returns."""

    async def judge(self, *args, **kwargs):
        j = await super().judge(*args, **kwargs)
        await self.drain()
        return j


_SHARED: Upstream | None = None


def shared() -> Upstream:
    global _SHARED
    if _SHARED is None:
        _SHARED = Upstream()
    return _SHARED
