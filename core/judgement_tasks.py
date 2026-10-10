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

GOVERNANCE = JudgementTask(
    site="governance.adds",
    questions=(
        Question(
            id="adds",
            text=(
                "Does this hunk add a check, gate, hook, validator, review round, or approval step over how "
                "work is done and approved: a step that looks at a request given to the agent, a plan, a "
                "change, a commit, a merge, a send, or an agent's commands or work, and on what it finds can "
                "stop it, send it back, or redirect it (for one, judging a request too thin and sending it back "
                "for clarification), or a step such work must pass (a pre-commit or CI gate, a hook, a review "
                "or approval step, a merge check, a validator over what an agent runs or writes, a guard row), "
                "in code or in instructions a turn follows (a skill, brief, persona, or prompt)? A refusal "
                "added inside such a step (refusing a grant, an approval, a merge, or a send) adds to it, and "
                "so does calling, registering, or wiring an existing such step at a new place, or narrowing an "
                "exemption from one. Judge what the added lines add; the other lines are context. Such a step "
                "counts in any repository, the agent's own (Valor's) included. The behavior of the software "
                "being built, serving its own users, is none of these, even where it refuses or blocks what a "
                "user asks: input validation, permission and visibility checks in a view or template, "
                "eligibility rules, error handling, and the pages, messages, and banners its users see; this "
                "never covers a step over the work in the agent's own pipeline, kernel, skills, persona, or "
                "guards. Nor is the wording or formatting of a message whose sending is already decided, even "
                "one that asks for an approval; nor are tests and the code that serves them (fixtures, helpers, "
                "scripted stand-ins, recording scripts), even where they exit early or refuse to run; nor is "
                "code that makes the work itself correct (a lock or a transaction that makes a second run of "
                "the same work wait, or answers it that one is already running; a parser that refuses input it "
                "cannot read), though a step that refuses work on a judgement about the work is not; nor is "
                "prose that only describes what code does."
            ),
            kind=Kind.BOOLEAN,
            labels={
                "true": (
                    "it adds a check, gate, hook, validator, review round, or approval step over how work is "
                    "done and approved"
                ),
                "false": (
                    "it adds none of these: only behavior of the software being built, serving its own users; "
                    "tests and the code that serves them; code that makes the work itself correct; or prose "
                    "that only describes what code does"
                ),
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
    # Floors set from human labels once real tasks have produced thirty or more rows.
    floor={"primary": 0.65, "fallback": 0.70},
    on_abstain="caution",
    on_failure="no_verdict",
    consumer={"proceed": "no instance", "caution": "a governance instance at the hunk, awaiting Tom's tap"},
    serves="the governance constraint",
    guard="the CLAUDE.md governance paragraph (correction 1)",
    # Run 11 on this question (task_sha256 183b1eac42cc...) failed its entry check: Jev was
    # right on every case, and the open-weight leg answered caution on three cases Tom labelled
    # false (docs/plans/m1-4b-records.md, docs/plans/c11-governance-precision.md). A site
    # lands calibrated only on a record that passes, so this stays None and the docs runner
    # stays unregistered.
    calibrated=None,
)

TASKS: tuple[JudgementTask, ...] = (JUDGE, BREADTH, GOVERNANCE)
BY_SITE = {t.site: t for t in TASKS}
