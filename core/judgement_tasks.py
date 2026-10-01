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
    floor={"primary": 0.70, "fallback": 0.75},  # provisional: set by 1.4's calibration record
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

GOVERNANCE = JudgementTask(
    site="governance.adds",
    questions=(
        Question(
            id="adds",
            text=(
                "Does this hunk add a check, gate, hook, validator, review round, or approval step: "
                "anything that holds, redirects, or refuses work, or a step someone must pass?"
            ),
            kind=Kind.BOOLEAN,
            labels={
                "true": "it adds a check, gate, hook, validator, review round, or approval step",
                "false": "it adds none of these",
            },
            proceed=frozenset({"false"}),
        ),
    ),
    inputs={
        "path": "the hunk's path",
        "hunk": "the hunk as `git diff -W` renders it, with its enclosing function",
        "paths": "every path the diff changes",
    },
    error_cost="high",
    floor={"primary": 0.65, "fallback": 0.70},  # provisional: set by 1.4's calibration record
    on_abstain="caution",
    on_failure="no_verdict",
    consumer={"proceed": "no instance", "caution": "a governance instance at the hunk, awaiting Tom's tap"},
    serves="the governance constraint",
    guard="the CLAUDE.md governance paragraph (correction 1)",
    calibrated=None,
)

TASKS: tuple[JudgementTask, ...] = (JUDGE, BREADTH, GOVERNANCE)
BY_SITE = {t.site: t for t in TASKS}
