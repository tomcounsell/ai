"""The judgement port asking Jev and Gemini's Decisions API side by side
(docs/plans/gemini-decisions-fallback.md), the Gemini leg's wire format, and
the agreement count."""

import asyncio
import dataclasses
import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from core import db, judgement, ledger, router, tasks
from core.judgement import JudgementTask, Kind, LegAnswer, LegError, Question
from core.settings import GEMINI_DECISION_MODEL, JEV_MODEL, settings
from tests import judgement_upstream
from tests.bridges import rows
from tools import gemini_decisions
from tools.gemini_decisions import GeminiDecisions

pytestmark = pytest.mark.spend(usd=0)

FIXTURE = Path(__file__).parent / "fixtures" / "judgement_gemini.json"

# The live probe's two questions: q1 a yes/no, q2 a choice.
TWO = JudgementTask(
    site="test.two",
    questions=(
        Question(
            "q2",
            "How precise is the request?",
            Kind.CHOICE,
            {"a": "names what to build", "b": "one line"},
            frozenset({"a"}),
        ),
        Question(
            "q1",
            "Does the change add a public function?",
            Kind.BOOLEAN,
            {"true": "it does", "false": "it does not"},
            frozenset({"false"}),
        ),
    ),
    inputs={"diff": "the change"},
    error_cost="low",
    floor={"primary": 0.70, "fallback": 0.75},
    on_abstain="caution",
    on_failure="caution",
    consumer={"proceed": "on", "caution": "hold"},
    serves="tests",
    guard="none",
)
INPUTS = {"diff": "+def area(r):\n+    return 3.14*r*r\n"}
LIVE = JudgementTask(**{**TWO.__dict__, "questions": (
    Question("q1", "Does the change add a public function?", Kind.BOOLEAN,
             {"true": "it does", "false": "it does not"}, frozenset({"false"})),
    Question("q2", "How precise is the request?", Kind.CHOICE,
             {"precise": "names what to build", "one_line_ask": "one line"}, frozenset({"precise"})),
)})  # fmt: skip


def run(coro):
    return asyncio.run(coro)


async def a_task(dsn: str) -> str:
    async with await db.connect(dsn) as conn:
        return await tasks.start(conn, tasks.Brief(instruction="judge it"))


def kinds(found, *types):
    return [r for r in found if r["type"] in types]


# -- the leg ------------------------------------------------------------------


def test_the_gemini_body_puts_the_inputs_in_one_text_block_and_each_question_in_its_type():
    body = GeminiDecisions("http://127.0.0.1:1/g", "k").body(TWO, INPUTS)
    assert body["model"] == GEMINI_DECISION_MODEL
    assert body["input"] == [{"type": "text", "text": json.dumps(INPUTS)}]
    q1, q2 = body["questions"]["q1"], body["questions"]["q2"]
    assert q1["type"] == "noul" and q1["criteria"] == {
        "true_description": "it does",
        "false_description": "it does not",
    }
    assert q2["type"] == "choice" and q2["criteria"] == {"a": "names what to build", "b": "one line"}
    assert all(q["instructions"].endswith(judgement.DATA_ONLY) for q in (q1, q2))


def test_the_live_probes_body_decodes_with_its_usage():
    got = gemini_decisions.decode(LIVE, FIXTURE.read_bytes())
    assert isinstance(got, LegAnswer) and got.model == GEMINI_DECISION_MODEL
    assert got.probabilities["q1"]["true"] == pytest.approx(0.8706257256788913)
    assert got.probabilities["q2"]["precise"] == pytest.approx(0.6922593418927344)
    assert got.provider_choice == {"q1": None, "q2": "precise"}
    assert got.usage == {"input_tokens": 235, "output_tokens": 0, "reported_usd": None}


@pytest.mark.parametrize(
    "body, what",
    [
        (b"not json", "the body is not JSON"),
        (b"[]", "no answers"),
        (json.dumps({"usage": {"total_input_tokens": 5}}).encode(), "no answers"),
        (json.dumps({"answers": {"q1": {"noul": 0.5}}}).encode(), "no answer to q2"),
        (json.dumps({"answers": {"q1": {"noul": 1.5}, "q2": {"probabilities": {}}}}).encode(), "no probability for q1"),
        (json.dumps({"answers": {"q1": {"noul": 0.5}, "q2": {"choice": "precise"}}}).encode(), "no probabilities for q2"),
    ],
)  # fmt: skip
def test_an_unreadable_gemini_body_is_malformed_naming_its_check(body, what):
    got = gemini_decisions.decode(LIVE, body)
    assert isinstance(got, LegError) and got.reason == "malformed" and got.what == what


def test_the_key_goes_in_googles_header_and_an_oversize_refusal_is_input_too_large(monkeypatch):
    sent = []

    async def post(url, headers, body, timeout_s):
        sent.append(headers)
        return answers.pop(0)

    too_large = {"error": {"message": "The input token count exceeds the maximum number of tokens allowed 262144.",
                           "code": "invalid_request"}}  # fmt: skip
    answers = [(400, json.dumps(too_large).encode()), (400, b'{"error": {"message": "bad"}}'), (500, b"")]
    monkeypatch.setattr(judgement, "post", post)
    leg = GeminiDecisions("http://127.0.0.1:1/g", "the-key")
    got = [run(leg.ask(TWO, INPUTS)) for _ in range(3)]
    assert sent[0] == {"x-goog-api-key": "the-key"}
    assert [(g.reason, g.billed, g.status) for g in got] == [
        ("input_too_large", "none", 400), ("http_status", "none", 400), ("http_status", "none", 500)
    ]  # fmt: skip


# -- the port, side by side -------------------------------------------------------


def port(jev: str, gemini: str, timeout_s: float = 5) -> judgement.JudgementPort:
    return judgement_upstream.shared().port(fixed=jev, gemini=gemini, gemini_timeout_s=timeout_s)


def judge(p, dsn, task_id):
    return p.judge(TWO, INPUTS, task_id=task_id, ref={"case": "x"}, dsn=dsn)


def test_a_judgement_jev_answered_returns_before_gemini_and_its_shadow_row_and_charge_follow(dsn):
    async def go():
        t = await a_task(dsn)
        p = port("a", "b?delay=0.6")
        started = time.monotonic()
        j = await judge(p, dsn, t)
        took = time.monotonic() - started
        before = await rows(dsn, t)
        await p.drain()
        return t, j, took, before, await rows(dsn, t)

    _, j, took, before, after = run(go())
    assert took < 0.5 and j.leg == "primary" and j.model == JEV_MODEL
    attempts = {a["leg"]: a for a in j.payload["attempts"]}
    assert attempts["gemini"]["outcome"] == "shadowed" and attempts["jev"]["answers"]["q2"]["label"] == "a"
    assert not kinds(before, "judgement.shadowed")
    shadow = kinds(after, "judgement.shadowed")
    assert len(shadow) == 1 and shadow[0]["payload"]["judgement_id"] == j.judgement_id
    assert shadow[0]["payload"]["attempt"]["answers"]["q2"]["label"] == "b"
    charged = [
        r for r in kinds(after, "gateway.charged") if r["payload"]["call_id"] == attempts["gemini"]["call_id"]
    ]
    assert len(charged) == 1 and charged[0]["payload"]["usd_micros"] > 0


def test_where_jev_abstains_gemini_answers_naming_its_model_cost_and_latency(dsn):
    async def go():
        t = await a_task(dsn)
        fallback = await judge(port("even", "a"), dsn, t)
        both = await judge(port("a,even", "a"), dsn, t)
        refused = await judge(port("a?refuse=1", "a"), dsn, t)
        return fallback, both, refused

    fallback, both, refused = run(go())
    assert (fallback.leg, fallback.model) == ("fallback", GEMINI_DECISION_MODEL)
    g = next(a for a in fallback.payload["attempts"] if a["leg"] == "gemini")
    assert g["outcome"] == "answered" and g["usd_micros"] > 0 and g["latency_ms"] >= 0
    jev = next(a for a in fallback.payload["attempts"] if a["leg"] == "jev")
    assert jev["answers"]["q2"]["decision"] == "abstain"  # Jev's lean is kept beside Gemini's answer
    assert both.leg == "both" and both.model == f"{GEMINI_DECISION_MODEL}+{JEV_MODEL}"
    assert both.answers["q2"]["leg"] == "primary" and both.answers["q1"]["leg"] == "fallback"
    assert refused.leg == "fallback" and refused.answered


def test_a_gemini_failure_never_fails_or_delays_a_judgement_jev_answered(dsn):
    async def go():
        t = await a_task(dsn)
        p = port("a", "a?delay=3", timeout_s=0.3)
        started = time.monotonic()
        hung = await judge(p, dsn, t)
        took = time.monotonic() - started
        q = port("a", "a?status=500")
        broken = await judge(q, dsn, t)
        drained = time.monotonic()
        await p.drain()
        await q.drain()
        return t, hung, broken, took, time.monotonic() - drained, await rows(dsn, t)

    _, hung, broken, took, drain_took, found = run(go())
    assert hung.answered and broken.answered and took < 0.3 and drain_took < 1.5
    shadows = kinds(found, "judgement.shadowed")
    reasons = {s["payload"]["judgement_id"]: s["payload"]["attempt"].get("reason") for s in shadows}
    assert reasons.get(hung.judgement_id) == "timeout"
    # The 500 came back before Jev's answer did, or after it, shadowed.
    b = next(a for a in broken.payload["attempts"] if a["leg"] == "gemini")
    assert b.get("reason") == "http_status" or reasons.get(broken.judgement_id) == "http_status"


def test_both_legs_failing_is_a_failed_judgement_and_a_stopped_task_asks_no_provider(dsn):
    async def go():
        t = await a_task(dsn)
        failed = await judge(port("a?status=500", "a?status=503"), dsn, t)
        stopped = await a_task(dsn)
        async with await db.connect(dsn) as conn:
            await tasks.stop(conn, stopped, reason="test")
        with pytest.raises(tasks.TaskStopped):
            await judge(port("a", "a"), dsn, stopped)
        return failed, await rows(dsn, stopped)

    failed, stopped = run(go())
    assert not failed.answered and failed.payload["on_failure"] == "caution"
    assert [a["outcome"] for a in failed.payload["attempts"]] == ["failed", "failed"]
    assert not kinds(stopped, "gateway.opened", "judgement.answered", "judgement.shadowed")


def test_a_shadow_write_that_fails_is_reported_and_the_judgement_stands(dsn, monkeypatch):
    reported = []
    monkeypatch.setattr(judgement.sentry_sdk, "capture_exception", reported.append)
    append = ledger.append

    async def failing(conn, task_id, kind, payload):
        if kind == "judgement.shadowed":
            raise RuntimeError("the database went away")
        return await append(conn, task_id, kind, payload)

    monkeypatch.setattr(ledger, "append", failing)

    async def go():
        t = await a_task(dsn)
        p = port("a", "a?delay=0.3")
        j = await judge(p, dsn, t)
        await p.drain()
        await asyncio.sleep(0)  # the done callback
        return j, p

    j, p = run(go())
    assert j.answered and not p._held
    assert [str(e) for e in reported] == ["the database went away"]


def test_a_shadow_row_after_a_failed_step_leaves_the_step_to_be_retried():
    at = datetime.now(UTC) - timedelta(days=1)
    found = [
        {"id": 1, "type": "step.failed", "payload": {"state": "judge"}, "at": at},
        {"id": 2, "type": "judgement.shadowed", "payload": {"site": "checks.test.breadth"}, "at": at},
    ]
    assert router.retry_due(found) and tasks.failed_step(found) is not None


# -- the agreement count ------------------------------------------------------------


def _answer(label, p, decision):
    """An answer whose proceed label (`a`, or `false`) has probability `p`."""
    proceed, other = ("false", "true") if label in ("true", "false") else ("a", "b")
    return {"label": label, "p_proceed": p, "decision": decision, "probabilities": {proceed: p, other: 1 - p}}


def _row(jid, jev, gemini, kind="judgement.answered", site="test.two"):
    attempts = [{"leg": "jev", "outcome": "answered" if jev else "failed", "answers": jev}]
    if gemini is not None:
        attempts.append(gemini)
    return {"type": kind, "payload": {"judgement_id": jid, "site": site, "attempts": attempts}}


def test_agreement_counts_both_answers_per_question_and_gemini_at_the_primary_floor():
    confident = {"q2": _answer("a", 0.95, "proceed"), "q1": _answer("false", 0.9, "proceed")}
    found = [
        # Agree on both questions, Gemini's answer in the row.
        _row("j1", confident, {"leg": "gemini", "outcome": "answered", "answers": confident}),
        # Shadowed: Gemini at 0.72 abstains at its 0.75 floor, proceeds at Jev's 0.70.
        _row("j2", confident, {"leg": "gemini", "outcome": "shadowed", "answers": None}),
        {"type": "judgement.shadowed", "payload": {"judgement_id": "j2", "attempt": {
            "leg": "gemini", "outcome": "answered",
            "answers": {"q2": _answer("a", 0.72, "abstain"), "q1": _answer("true", 0.1, "caution")}}}},
        # Gemini failed in the shadow; another whose shadow never came.
        _row("j3", confident, {"leg": "gemini", "outcome": "shadowed", "answers": None}),
        {"type": "judgement.shadowed", "payload": {"judgement_id": "j3", "attempt": {
            "leg": "gemini", "outcome": "failed", "reason": "timeout", "answers": None}}},
        _row("j4", confident, {"leg": "gemini", "outcome": "shadowed", "answers": None}),
        # A calibration's one-leg row, and a row from before Gemini: not counted.
        _row("j5", confident, None),
        _row("j6", confident, {"leg": "open_weight", "outcome": "unused", "answers": None}),
        # Jev failed, Gemini answered.
        _row("j7", None, {"leg": "gemini", "outcome": "answered", "answers": confident}),
    ]  # fmt: skip
    got = judgement.agreement(found, {"test.two": TWO})["test.two"]
    assert (got["judgements"], got["unsettled"], got["failed"]) == (5, 1, {"jev": 1, "gemini": 1})
    assert got["questions"] == 4
    assert (got["action_agree"], got["action_agree_at_primary_floor"], got["label_agree"]) == (2, 3, 3)
    assert got["disagree"] == {"jev proceed / gemini abstain": 1, "jev proceed / gemini caution": 1}
    assert got["disagree_at_primary_floor"] == {"jev proceed / gemini caution": 1}


def test_agreement_reads_since_as_a_utc_day_whatever_the_sessions_timezone(dsn):
    """Two rows a second either side of the next UTC midnight: since that
    day counts only the later one, in a session seven hours ahead of UTC."""
    from core import judgement_sites
    from core.judgement_tasks import JUDGE

    q = JUDGE.questions[0]
    answer = {
        "label": "precise",
        "p_proceed": 0.9,
        "decision": "proceed",
        "probabilities": {
            label: (0.9 if label == "precise" else 0.1 / (len(q.labels) - 1)) for label in q.labels
        },
    }
    attempts = [{"leg": leg, "outcome": "answered", "answers": {q.id: answer}} for leg in ("jev", "gemini")]
    midnight = datetime.combine(datetime.now(UTC).date() + timedelta(days=1), datetime.min.time(), UTC)

    async def go():
        t = await a_task(dsn)
        async with await db.connect(dsn) as conn:
            for n, at in enumerate((midnight - timedelta(seconds=1), midnight + timedelta(seconds=1))):
                payload = {"judgement_id": f"utc-day-{n}-{t}", "site": JUDGE.site, "attempts": attempts}
                await conn.execute(
                    "INSERT INTO events (task_id, type, payload, at) VALUES (%s, 'judgement.answered', %s, %s)",
                    (t, json.dumps(payload), at),
                )
        os.environ["PGTZ"] = "Asia/Bangkok"
        try:
            return await judgement_sites.agreement(dsn), await judgement_sites.agreement(
                dsn, midnight.date().isoformat()
            )
        finally:
            os.environ.pop("PGTZ")

    everything, since = run(go())
    assert "test.two" not in everything and since[JUDGE.site]["judgements"] == 1


# -- the composition root -----------------------------------------------------------


def test_the_gemini_leg_reads_the_meet_key_and_a_missing_one_refuses(tmp_path, monkeypatch):
    from core import credentials
    from core.__main__ import port as build

    jev = tmp_path / "judgement-keys"
    jev.write_text("TYPESAFE_API_KEY=t\n")
    google = tmp_path / "google-key"
    with pytest.raises(credentials.MissingKey, match="GOOGLE_API_KEY.*google-key"):
        build(str(jev), str(google))
    google.write_text("GOOGLE_API_KEY=g\n")
    assert build(str(jev), str(google)).legs["gemini"]._key == "g"
    from core import __main__ as main

    monkeypatch.setattr(
        main, "settings", dataclasses.replace(settings, gemini_url="http://127.0.0.1:9/decisions")
    )
    assert build(str(jev), str(google)).legs["gemini"]._key == judgement.LOOPBACK_KEY
    monkeypatch.setattr(
        main, "settings", dataclasses.replace(settings, gemini_url="https://example.com/decisions")
    )
    with pytest.raises(ValueError, match="loopback"):
        build(str(jev), str(google))


GEMINI_FIRST = dataclasses.replace(TWO, site="test.gemini_first", primary="gemini")


def test_a_site_routed_gemini_first_takes_gemini_and_counts_agreement_with_jev(dsn):
    async def go():
        t = await a_task(dsn)
        p = port("b", "a")
        j = await p.judge(GEMINI_FIRST, INPUTS, task_id=t, ref={"case": "x"}, dsn=dsn)
        await p.drain()
        return j, await rows(dsn, t)

    j, found = run(go())
    assert judgement.route(GEMINI_FIRST) == ("gemini", "jev") and judgement.route(TWO) == ("jev", "gemini")
    assert j.leg == "primary" and j.model == GEMINI_DECISION_MODEL and j.answers["q2"]["label"] == "a"
    roles = {a["leg"]: a["role"] for a in j.payload["attempts"]}
    assert roles == {"gemini": "primary", "jev": "fallback"}
    got = judgement.agreement(found, {GEMINI_FIRST.site: GEMINI_FIRST})[GEMINI_FIRST.site]
    assert (got["judgements"], got["unsettled"], got["questions"]) == (1, 0, 2) and got["label_agree"] < 2
    legs = {"jev": {"model": JEV_MODEL}, "gemini": {"model": GEMINI_DECISION_MODEL}}
    assert judgement.task_sha256(GEMINI_FIRST, legs) != judgement.task_sha256(
        dataclasses.replace(GEMINI_FIRST, primary="jev"), legs
    )


def test_agreement_reads_a_shadowed_jev_from_its_own_row():
    confident = {"q2": _answer("a", 0.95, "proceed"), "q1": _answer("false", 0.9, "proceed")}
    found = [
        {"type": "judgement.answered", "payload": {"judgement_id": "g1", "site": GEMINI_FIRST.site, "attempts": [
            {"leg": "gemini", "outcome": "answered", "answers": confident},
            {"leg": "jev", "outcome": "shadowed", "answers": None}]}},
        {"type": "judgement.shadowed", "payload": {"judgement_id": "g1", "attempt": {
            "leg": "jev", "outcome": "answered", "answers": confident}}},
    ]  # fmt: skip
    got = judgement.agreement(found, {GEMINI_FIRST.site: GEMINI_FIRST})[GEMINI_FIRST.site]
    assert (got["judgements"], got["unsettled"], got["questions"], got["action_agree"]) == (1, 0, 2, 2)
