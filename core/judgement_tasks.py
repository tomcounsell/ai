"""Every judgement task the kernel asks, declared here and nowhere else.

One module, literal declarations, no discovery: the set of judgement sites
is `TASKS`, so a reader and a test see the same list
(docs/judgement-layer.md, Task taxonomy). Each declaration carries its
error cost, its floors per leg, what an abstain and a failure do, and what
each kernel action means, next to the question.

`calibrated` is the `task_sha256` of the calibration record the task landed
on. Every judgement row carries both that and the task's current digest, so
a declaration changed after its record shows on every row it answers.
"""

from core import machine
from core.judgement import JudgementTask, Kind, Question

JUDGE = JudgementTask(
    site="intake.underspecified",
    questions=(
        Question(
            id="request",
            text=(
                "Would asking the requester a question before building change what gets built? Judge "
                "the request as written: does it leave a decision only the requester can settle that "
                "would change the result? Ordinary implementation choices are settled by reading the "
                "code and do not count."
            ),
            kind=Kind.CHOICE,
            labels={
                "precise": (
                    "It says what outcome is wanted and its scope, briefly or at length; what remains is "
                    "for a developer reading the code. A bug report with the symptom, a reproduction or "
                    "measurements, and the wanted behavior is precise. A request that asks to improve "
                    "existing UI and lists candidate fixes or the concrete change is precise: the "
                    "developer picks among the listed fixes."
                ),
                "one_line_ask": (
                    "A single line or checklist item naming a feature, field, or API, often by a code "
                    "name, without the behavior wanted, where it lives, or why; the intent is in the "
                    'requester\'s head. A short label plus a few words of purpose ("for X", "finish '
                    'the TODO") is still a one-line ask.'
                ),
                "example_as_spec": (
                    'It leans on an example ("Example: ...") that may stand for a wider rule it does '
                    "not state."
                ),
                "existing_ui_unscoped": (
                    "It names existing UI or behavior to change or to add beside, gives neither the "
                    "concrete change nor candidate fixes, and leaves unsaid what happens to what is there. "
                    "Not this label when the request lists candidate fixes or states the change."
                ),
            },
            proceed=frozenset({"precise"}),
        ),
    ),
    inputs={
        "request": "task.started instruction, verbatim",
        "thread": "earlier messages in the same thread, oldest first (empty until the bridges)",
        "project": "the name of the repository the task works in",
    },
    error_cost="high",
    floor={"primary": 0.70, "fallback": 0.75},
    on_abstain="caution",
    on_failure="caution",
    consumer={"proceed": "judge verdict precise: to plan", "caution": "judge verdict thin: to clarify"},
    serves="Mission items 3 and 6",
    guard=machine.GUARD_JUDGE,
    # Calibration run 5 of 2026-10-02 (results/calibration/intake.underspecified-run5.json):
    # both legs 7 of 7 on the seed cases; Brier 0.0114 (Jev) and 0.0040 (fallback), n = 7 each.
    # Fitted over runs 1 to 5: the wording here and the fallback's prompt and schema were
    # changed after seeing which cases failed (docs/plans/m1-3-judgement.md, Build record).
    calibrated="32b8245e60649f4884abba82e5d02ea6cfc865acc7ec21f4afb2737af6ff9c21",
)

_GAP = "the change leaves unexercised by any test"
BREADTH = JudgementTask(
    site="checks.test.breadth",
    questions=(
        Question(
            id="gap_state",
            text=f"Does {_GAP} a record or object in a state other than the obvious one?",
            kind=Kind.BOOLEAN,
            labels={
                "true": "untested: records in states other than the obvious one",
                "false": "the tests cover the states the change handles",
            },
            proceed=frozenset({"false"}),
        ),
        Question(
            id="gap_enum",
            text=f"Does {_GAP} a member of an enumeration or set of cases the code branches on?",
            kind=Kind.BOOLEAN,
            labels={
                "true": "untested: a member of an enumeration the code branches on",
                "false": "the tests cover every case the code branches on",
            },
            proceed=frozenset({"false"}),
        ),
        Question(
            id="gap_bound",
            text=f"Does {_GAP} an existing test whose bounds or expectations encode the old behavior?",
            kind=Kind.BOOLEAN,
            labels={
                "true": "untested: existing tests whose bounds encode the old behavior",
                "false": "no existing test encodes behavior the change replaced",
            },
            proceed=frozenset({"false"}),
        ),
    ),
    inputs={
        "diff": "base to candidate, every path that is not a test",
        "tests": "base to candidate, the test paths",
    },
    error_cost="medium",
    # Floors set from human labels once real tasks have produced thirty or more rows.
    floor={"primary": 0.70, "fallback": 0.75},
    on_abstain="caution",
    on_failure="no_verdict",
    consumer={
        "proceed": "no behavior listed",
        "caution": "the question's gap listed as an untested behavior",
    },
    serves="Mission item 1",
    guard=machine.GUARD_BREADTH,
    calibrated=None,
)

_SHAPES = {
    "short_answer": "a short direct answer in a sentence or a few lines: a fact, a yes or no, a quick opinion",
    "code_change": "a change to code, a configuration, or a running system, delivered as the work itself",
    "written_report": "a written report, research, an analysis, or a document longer than a few lines",
    "question_back": "a question back to the sender, because the message cannot be acted on as it stands",
    "acknowledge": "an acknowledgement and nothing more: thanks, an approval, a go-ahead, a stop, a remark",
    "decline": "a refusal or an explanation that it cannot be done",
}
SHAPE = JudgementTask(
    site="response.shape",
    questions=(
        Question(
            id="shape",
            text=(
                "What shape would the ideal response to this message take? Judge the response Valor "
                "should give, not the message: a long message can want a short answer, and a one-line "
                "request can want a change to code. `bound_as` says what the message is to its task: a "
                "new request (`start`), an answer to Valor's question, feedback on a delivery, or a "
                "steer of running work."
            ),
            kind=Kind.PICK,
            labels=dict(_SHAPES),
            proceed=frozenset(_SHAPES),
        ),
    ),
    inputs={
        "message": "the message's text, from its message.received row",
        "thread": "the earlier messages of its reply chain, oldest first, one per line",
        "bound_as": "what message.bound bound it as: start, answer, feedback, or steer",
    },
    error_cost="low",
    # Floors set from human labels once real messages have produced thirty or more rows.
    floor={"primary": 0.55, "fallback": 0.60},
    on_abstain="caution",
    on_failure="caution",
    consumer={
        "proceed": "the picked shape's reaction replaces 👀 (core/reactions.py)",
        "caution": "no shape reaction; 👀 stays",
    },
    serves="Mission item 6",
    guard="none: a label, no gate",
    calibrated=None,
)

TASKS: tuple[JudgementTask, ...] = (JUDGE, BREADTH, SHAPE)
BY_SITE = {t.site: t for t in TASKS}
