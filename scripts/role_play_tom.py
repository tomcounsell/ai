"""A stand-in for Tom: answers a replay task's open question, or reviews its
latest delivery, strictly from an answer key, and records the reply in the
ledger as role-played.

    .venv/bin/python scripts/role_play_tom.py TASK_ID ANSWER_KEY [--model sonnet] [--max-feedback 2]

The answer key is Tom's recorded intent for the request, in his words where
there are any (a Notion card, the questions he answered, the review he gave).
The stand-in answers briefly, reveals only what was asked, and says "Your
call." where the key is silent. On a delivery it accepts, or gives one round
of project-manager feedback naming the single most important divergence from
the key, at most `--max-feedback` rounds per task; after that it records
nothing and reports `cap`.

Its model calls run outside the kernel and its sandbox, on this machine's own
credentials, and are not charged to the task's budget; each is logged to
$VALOR_DEMO/costs.jsonl. The reply goes into the ledger through
`python -m core answer|feedback --role-played`, by "stand-in (<model>)".

Prints one JSON object: `kind` (answer, feedback, accept, cap, or nothing),
`text`, `usd`, `reason`.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from replay_common import claude_json, core, status, ws_git

from core import db, tasks

DIFF_LIMIT = 80_000

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


def delivery_context(workspace: str, base: str = "origin/main") -> str:
    """The delivery as a reviewer sees it: what is committed on the
    workspace's branch since `base`, and any untracked
    files left beside it."""
    stat = ws_git(workspace, "diff", "--no-ext-diff", "--no-textconv", "--stat", base, "HEAD")
    diff = ws_git(workspace, "diff", "--no-ext-diff", "--no-textconv", base, "HEAD")
    if len(diff) > DIFF_LIMIT:
        diff = diff[:DIFF_LIMIT] + f"\n... (diff truncated at {DIFF_LIMIT} characters)"
    loose = ws_git(workspace, "ls-files", "--others", "--exclude-standard")
    parts = [f"## Diff stat\n\n{stat or '(nothing committed since the base)'}", f"## Diff\n\n{diff}"]
    if loose:
        parts.append(f"## Untracked files in the workspace, not committed\n\n{loose}")
    return "\n\n".join(parts)


def stand_in(
    task_id: str, answer_key: str, *, model: str = "sonnet", max_feedback: int = 2, base: str = "origin/main"
) -> dict:
    """Reply to whatever the task waits on. `base` is the commit the delivery
    is diffed against (the replay driver passes the base SHA)."""
    state = status(task_id)
    brief = asyncio.run(_brief(task_id))
    key = Path(answer_key).read_text()
    by = f"stand-in ({model})"
    subject = f"task {task_id}"
    head = f"# Tom's request\n\n{brief.instruction}\n\n# Answer key\n\n{key}"

    if state["state"] == "waiting for Tom":
        question = next(q for q in state["attention"] if q["kind"] == "question" and q["answer"] is None)
        reply = claude_json(
            f"{head}\n\n# Valor's message\n\n{question['question']}",
            system=ANSWER_SYSTEM,
            model=model,
            purpose="stand-in answer",
            subject=subject,
        )
        text = reply["text"].strip() or "Your call."
        core("answer", task_id, text, "--by", by, "--role-played")
        return {"kind": "answer", "text": text, "usd": reply["usd"], "reason": None}

    if state["state"] == "delivered":
        rounds = sum(1 for a in state["attention"] if a["kind"] == "feedback")
        if rounds >= max_feedback:
            return {"kind": "cap", "text": None, "usd": 0.0, "reason": f"{rounds} feedback rounds used"}
        reply = claude_json(
            f"{head}\n\n# Valor's delivery note\n\n{state['delivered']}\n\n"
            f"# The delivery\n\n{delivery_context(brief.workspace, base)}",
            system=REVIEW_SYSTEM,
            model=model,
            purpose="stand-in review",
            subject=subject,
        )
        verdict = reply["json"] or {}
        if verdict.get("decision") == "feedback" and (verdict.get("feedback") or "").strip():
            text = verdict["feedback"].strip()
            core("feedback", task_id, text, "--by", by, "--role-played")
            return {"kind": "feedback", "text": text, "usd": reply["usd"], "reason": verdict.get("reason")}
        return {"kind": "accept", "text": None, "usd": reply["usd"], "reason": verdict.get("reason")}

    return {"kind": "nothing", "text": None, "usd": 0.0, "reason": f"task is {state['state']}"}


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("task_id")
    parser.add_argument("answer_key")
    parser.add_argument("--model", default="sonnet")
    parser.add_argument("--max-feedback", type=int, default=2)
    parser.add_argument("--base", default="origin/main", help="the commit a delivery is diffed against")
    args = parser.parse_args()
    reply = stand_in(
        args.task_id, args.answer_key, model=args.model, max_feedback=args.max_feedback, base=args.base
    )
    print(json.dumps(reply))


if __name__ == "__main__":
    main()
