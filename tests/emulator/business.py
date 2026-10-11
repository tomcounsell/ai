"""The business case set: what a business item's replay is scored by, and
the perspective measure read from its decision records.

    .venv/bin/python -m tests.emulator.business $VALOR_DEMO/results/<run>.json ...

A business item is an item with `"kind": "business"` and a `decision`: what
was merged, the option Tom chose, in a sentence or three. Its outcome is
unknown, so its judge scores agreement with that decision, 0 to 5, and
nothing else (`SYSTEM`, used by `judge.py`). The stand-in never sees the
decision; the answer key holds the business context Tom would answer from.

A decision record is a fenced block in a delivery's report, labelled
`decision-record`, holding one JSON object:

    ```decision-record
    {"recommended": "the option recommended before the argument",
     "chosen": "the option chosen",
     "objections": [{"perspective": "who raised it", "objection": "what it said",
                     "changed_choice": true}]}
    ```

The replay driver records the last delivery's record as the run's
`decision_record` (`replay.summarize`). The perspective measure (Business
item 5, docs/business.md) is read from that field alone: for each
perspective, the decisions where it raised an objection, and those where
an objection it raised is named as having changed the option chosen.
Nothing is counted from whether a perspective's text was used, edited, or
discarded.

Prints, per run, the item, its decision, the agreement score, and the
perspectives the record names as having changed the option; then the
measure per perspective. Live spend: none; it reads result files.
"""

import argparse
import json
import re
from pathlib import Path

FENCE = "decision-record"
_BLOCK = re.compile(r"^```" + re.escape(FENCE) + r"[ \t]*\n(.*?)^```[ \t]*$", re.M | re.S)

SYSTEM = """You are judging one replay of a business decision against the decision the business \
actually made. You get the question as it was asked, the business context the person who asked \
holds, the decision that was merged (stated, and as its diff), and the candidate's diff.

The decision's outcome is unknown: nobody knows whether it was right. Do not judge whether the \
candidate's choice is good for the business. Judge only agreement: does the candidate's diff \
commit the business to the same option as the merged decision?

Score agreement from 0 to 5:
- 5: the same option, in substance (the same price, offer, positioning, audience, channel, or \
message), whatever the wording, layout, or code.
- 3: the same direction with a material difference in one element of the option.
- 1: a different option that touches the same question.
- 0: no decision on the question, or the opposite one.

Differences of wording, layout, naming, or implementation that leave the option the same are \
not divergences. List the divergences in the option that matter, most important first.

Reply with one JSON object and nothing else:
{"scores": {"agreement": 0-5},
 "candidate_decision": "the option the candidate committed to, in one sentence",
 "divergences": ["..."],
 "rationale": "..."}"""


def is_business(item: dict) -> bool:
    return item.get("kind") == "business"


def prompt(item: dict, answer_key: str, reference: str, candidate: str) -> str:
    """The business judge's prompt: blind to the arm, the questions, the
    feedback, and Valor's own account of the work."""
    return "\n\n".join(
        [
            f"# The question as asked\n\n{item['request']}",
            f"# Business context the asker holds\n\n{answer_key}",
            f"# The merged decision\n\n{item['decision']}",
            f"# The merged decision's diff\n\n```diff\n{reference or '(none)'}\n```",
            f"# Candidate's diff\n\n```diff\n{candidate or '(the candidate changed nothing)'}\n```",
        ]
    )


def decision_record(text: str | None) -> dict | None:
    """The last `decision-record` block in a delivery's text, as
    `{recommended, chosen, objections: [{perspective, objection,
    changed_choice}]}`; None when there is none, and `{"unreadable": why,
    "text": block}` when the block is not that shape."""
    blocks = _BLOCK.findall(text or "")
    if not blocks:
        return None
    raw = blocks[-1]
    try:
        value = json.loads(raw)
    except ValueError as exc:
        return {"unreadable": f"not JSON: {exc}", "text": raw}
    if not isinstance(value, dict) or not isinstance(value.get("objections", []), list):
        return {"unreadable": "not an object with an objections list", "text": raw}
    objections = []
    for o in value.get("objections", []):
        if not isinstance(o, dict) or not str(o.get("perspective") or "").strip():
            return {"unreadable": "an objection names no perspective", "text": raw}
        objections.append(
            {
                "perspective": str(o["perspective"]).strip(),
                "objection": str(o.get("objection") or ""),
                "changed_choice": o.get("changed_choice") is True,
            }
        )
    return {
        "recommended": str(value.get("recommended") or ""),
        "chosen": str(value.get("chosen") or ""),
        "objections": objections,
    }


def latest_record(deliveries: list[str]) -> dict | None:
    """The decision record of the last delivery that carries one."""
    for text in reversed(deliveries or []):
        record = decision_record(text)
        if record is not None:
            return record
    return None


def changed_by(record: dict | None) -> list[str]:
    """The perspectives the record names as having changed the option chosen."""
    if not record or "unreadable" in record:
        return []
    return sorted({o["perspective"] for o in record["objections"] if o["changed_choice"]})


def perspective_measure(results: list[dict]) -> dict:
    """Per perspective, from each run's `decision_record` and nothing else:
    `raised`, the runs where it raised an objection, and `changed`, the runs
    where an objection it raised is named as having changed the option
    chosen. `unrecorded` lists the runs with no readable record."""
    perspectives: dict[str, dict] = {}
    unrecorded = []
    for result in results:
        record = result.get("decision_record")
        if not record or "unreadable" in record:
            unrecorded.append(result["run"])
            continue
        changed = set(changed_by(record))
        for name in sorted({o["perspective"] for o in record["objections"]}):
            entry = perspectives.setdefault(name, {"raised": [], "changed": []})
            entry["raised"].append(result["run"])
            if name in changed:
                entry["changed"].append(result["run"])
    return {"perspectives": perspectives, "unrecorded": unrecorded}


def agreement(result: dict) -> int | None:
    """The business judge's agreement score, or None before a verdict."""
    return ((result.get("judge") or {}).get("scores") or {}).get("agreement")


def report(results: list[dict]) -> dict:
    runs = [
        {
            "run": r["run"],
            "item": r["item"]["name"],
            "decision": r["item"].get("decision"),
            "agreement": agreement(r),
            "changed_by": changed_by(r.get("decision_record")),
        }
        for r in results
        if is_business(r["item"])
    ]
    return {"runs": runs, **perspective_measure([r for r in results if is_business(r["item"])])}


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("result_files", nargs="+")
    args = parser.parse_args()
    results = [json.loads(Path(p).read_text()) for p in args.result_files]
    print(json.dumps(report(results), indent=2))


if __name__ == "__main__":
    main()
