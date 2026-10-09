"""A stand-in for Tom: answers a replay task's open question, or reviews its
merged delivery, strictly from an answer key, and records the reply in the
ledger as role-played.

    .venv/bin/python -m tests.emulator.stand_in TASK_ID ANSWER_KEY EMULATOR_TASK \
        --base BASE_SHA [--model claude-opus-5-5] [--max-feedback 2]

The answer key is Tom's recorded intent for the request, in his words where
there are any (a Notion card, the questions he answered, the review he gave).
The stand-in answers briefly, reveals only what was asked, and says "Your
call." where the key is silent. At a merged delivery it accepts, or gives one
round of project-manager feedback naming the single most important
divergence from the key, at most `--max-feedback` rounds per task; after
that it records nothing and reports `cap`.

Its model is the `frontier` seat. Its calls go through the kernel's
gateway and are metered on the run's emulator task (`common.Meter`). It
reads the delivery from the task's kernel mirror, at its merge's head,
never from the turn's workdir. The reply goes into the ledger through
`python -m core answer|feedback --role-played`, by "stand-in (<model>)".

Prints one JSON object: `kind` (answer, feedback, accept, cap, or nothing),
`text`, `reason`.
"""

import argparse
import asyncio
import json
from pathlib import Path

from core import db, tasks
from core.settings import SEATS
from tests.emulator.common import DEMO, Meter, claude_json, core, mirror_diff, review_rev, status

DIFF_LIMIT = 80_000  # the baseline's value, kept so scores compare (5d90b4776:scripts/role_play_tom.py:37)
_, MODEL = SEATS["frontier"]  # the frontier seat's pinned id

ANSWER_SYSTEM = """You are standing in for Tom, who made the request below and is the product owner. \
Valor, the engineer building it, has sent him a message. Reply as Tom.

You know only what the answer key says: Tom's recorded intent for this request. Rules:
- Reply as Tom would in chat: brief, plain, first person, no preamble, no sign-off.
- Answer strictly from the answer key. Reveal only what each question asks; never volunteer \
other parts of the key.
- Where the key does not settle a question, answer that question with "Your call."
- If the message numbers its questions, answer by number.
- If Valor states the approach he intends, and it contradicts the key on something that changes \
what gets built, add the single most important correction, in one sentence. Otherwise say \
nothing about the approach.
- If the message asks nothing and needs no correction, reply "Go ahead."

Reply with the message text only."""

REVIEW_SYSTEM = """You are standing in for Tom, the product owner, reviewing a delivery as \
project manager. You know only what the answer key says: Tom's recorded intent for the request.

Compare the delivery (Valor's summary and the diff) with the answer key.
- Accept when it does what the key asks in substance. Naming, structure, style, and anything \
the key does not cover are Valor's call; do not hold the delivery to them.
- Otherwise give feedback naming the single most important divergence from the key, one point \
only, in one to three plain sentences, the way Tom would write it (for example "The most \
glaring miss: ..."). Do not list other problems; later rounds can raise them.

Reply with one JSON object and nothing else:
{"decision": "accept" or "feedback", "feedback": "the message to Valor, empty when accepting", \
"reason": "one line for the experiment's record"}"""


async def _brief(task_id: str) -> tasks.Brief:
    async with await db.connect() as conn:
        return await tasks.brief(conn, task_id)


def delivery_context(mirror: str | Path, base: str, rev: str) -> str:
    """The delivery as a reviewer sees it: the diff from `base` to `rev` in
    the task's kernel mirror."""
    stat = mirror_diff(mirror, base, rev, "--stat")
    diff = mirror_diff(mirror, base, rev)
    if len(diff) > DIFF_LIMIT:
        diff = diff[:DIFF_LIMIT] + f"\n... (diff truncated at {DIFF_LIMIT} characters)"
    return "\n\n".join(
        [f"## Diff stat\n\n{stat or '(nothing committed since the base)'}", f"## Diff\n\n{diff}"]
    )


def stand_in(
    task_id: str,
    answer_key: str,
    *,
    meter: Meter,
    mirror: str | Path,
    base: str,
    model: str = MODEL,
    max_feedback: int = 2,
    workdir: Path | None = None,
) -> dict:
    """Reply to whatever the task waits on: its open question, or its merged
    delivery. `base` is the commit the delivery is diffed against; `mirror` is
    the task's kernel mirror; `workdir` holds each call's fresh config."""
    state = status(task_id)
    brief = asyncio.run(_brief(task_id))
    key = Path(answer_key).read_text()
    by = f"stand-in ({model})"
    head = f"# Tom's request\n\n{brief.instruction}\n\n# Answer key\n\n{key}"
    workdir = Path(workdir or DEMO)

    if state["state"] == "waiting":
        question = next(q for q in state["attention"] if q["kind"] == "question" and q["answer"] is None)
        asked = sum(1 for a in state["attention"] if a["kind"] == "question")
        reply = claude_json(
            f"{head}\n\n# Valor's message\n\n{question['question']}",
            system=ANSWER_SYSTEM,
            model=model,
            meter=meter,
            call_id=f"stand-in.answer.{asked}",
            workdir=workdir,
        )
        text = reply["text"].strip() or "Your call."
        core("answer", task_id, text, "--by", by, "--role-played")
        return {"kind": "answer", "text": text, "reason": None}

    if state["state"] == "merged":
        rounds = sum(1 for a in state["attention"] if a["kind"] == "feedback")
        if rounds >= max_feedback:
            return {"kind": "cap", "text": None, "reason": f"{rounds} feedback rounds used"}
        rev = review_rev(task_id, state)
        reply = claude_json(
            f"{head}\n\n# Valor's delivery note\n\n{state['delivered']}\n\n"
            f"# The delivery\n\n{delivery_context(mirror, base, rev)}",
            system=REVIEW_SYSTEM,
            model=model,
            meter=meter,
            call_id=f"stand-in.review.{rounds + 1}",
            workdir=workdir,
        )
        verdict = reply["json"] or {}
        if verdict.get("decision") == "feedback" and (verdict.get("feedback") or "").strip():
            text = verdict["feedback"].strip()
            core("feedback", task_id, text, "--by", by, "--role-played")
            return {"kind": "feedback", "text": text, "reason": verdict.get("reason"), "rev": rev}
        return {"kind": "accept", "text": None, "reason": verdict.get("reason"), "rev": rev}

    return {"kind": "nothing", "text": None, "reason": f"task is {state['state']}"}


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("task_id")
    parser.add_argument("answer_key")
    parser.add_argument("emulator_task", help="the calibration task the calls are metered on")
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--max-feedback", type=int, default=2)
    parser.add_argument("--base", required=True, help="the commit a delivery is diffed against")
    args = parser.parse_args()
    shown = json.loads(core("workspace", "show", args.task_id))
    with Meter(args.emulator_task) as meter:
        reply = stand_in(
            args.task_id,
            args.answer_key,
            meter=meter,
            mirror=shown["mirror"],
            base=args.base,
            model=args.model,
            max_feedback=args.max_feedback,
        )
    print(json.dumps(reply))


if __name__ == "__main__":
    main()
