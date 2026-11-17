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

from core import machine, reactions
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
    # Calibration of 2026-11-16, Jev and gemini-decision-1, on run 5's seven seed cases
    # recovered verbatim (docs/judgement-baseline.md): both legs right on 7 of 7 at their
    # own floors; Brier 0.0109 (Jev) and 0.0935 (Gemini), n = 7 each. Gemini abstained on
    # one one-line ask at p_proceed 0.74: right at its 0.75 floor, wrong at Jev's 0.70.
    # Run 5 of 2026-10-02 fitted the wording over runs 1 to 5 (docs/plans/m1-3-judgement.md);
    # nothing was reworded for Gemini.
    calibrated="0b907d123c7f6473530745a8f5d1acefcd533e7bab9efaf516f01b048da0e511",
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

# The open tasks an overlap judgement lists, numbered: a leg answers labels
# only, so the task a decision names is one of these numbers.
OPEN_LISTED = 8
OVERLAP = JudgementTask(
    site="intake.overlap",
    questions=(
        Question(
            id="decision",
            text=(
                "Read the new ask against the open tasks listed on its project. Does one of them already "
                "do what the ask asks, or would the two touch the same work? Judge scope, not wording: "
                "two asks that fix one cause overlap however they are worded."
            ),
            kind=Kind.PICK,
            labels={
                "covered": "One listed task already does what the ask asks; the ask adds nothing to it.",
                "supersedes": "The ask covers a listed task's whole scope and more.",
                "overlaps": "The ask and a listed task should both go ahead, but they touch the same code.",
                "split": "The ask holds parts that belong to different tasks.",
                "distinct": "No listed task shares the ask's scope.",
            },
            proceed=frozenset({"covered", "supersedes", "overlaps", "split", "distinct"}),
        ),
        Question(
            id="task",
            text="Which listed task does that decision name? `none` when it names none.",
            kind=Kind.PICK,
            labels={
                **{str(n): f"listed task {n}" for n in range(1, OPEN_LISTED + 1)},
                "none": "no listed task",
            },
            proceed=frozenset({*(str(n) for n in range(1, OPEN_LISTED + 1)), "none"}),
        ),
        Question(
            id="placement",
            text=(
                "Should the ask run before or after the task named, because one lands work the other "
                "needs? `none` when the order does not matter."
            ),
            kind=Kind.PICK,
            labels={
                "before": "The ask should run before the named task.",
                "after": "The ask should run after the named task, which lands what it needs first.",
                "none": "The order does not matter.",
            },
            proceed=frozenset({"before", "after", "none"}),
        ),
    ),
    inputs={
        "request": "task.started instruction, verbatim",
        "project": "the name of the repository the task works in",
        "open_tasks": (
            f"the project's open tasks, the {OPEN_LISTED} most recently active, numbered: id, state, "
            "instruction (cut), and a count of those left out"
        ),
    },
    error_cost="medium",
    # Floors set from the calibration record (docs/plans/ask-reconciliation.md, Rollout).
    floor={"primary": 0.70, "fallback": 0.75},
    on_abstain="caution",
    on_failure="caution",
    consumer={
        "proceed": "reconcile.decided records the decision; shadow: the judge runs as if distinct",
        "caution": "reconcile.decided records distinct, with a note",
    },
    serves="Mission items 6 and 1",
    guard="none in shadow: a record, no hold (the hold waits for calibration and Tom's grant)",
    calibrated=None,
)

# The two reaction questions pick one emoji of about forty, several of which
# often fit, so the probability splits: the top label is taken unless the
# split is near flat (about three times 1/40). A wrong pick costs a mark.
_REACTION_FLOOR = {"primary": 0.08, "fallback": 0.08}
_MESSAGE = {
    "message": "the message's text, from its message.received row",
    "thread": "the earlier messages of its reply chain, oldest first, one per line",
    "bound_as": "what message.bound bound it as: start, answer, feedback, steer, none, or stop",
}
HANDLING = JudgementTask(
    site="reaction.handling",
    questions=(
        Question(
            id="reaction",
            text=(
                "Valor has just read this message and puts one emoji reaction on it while he handles it. "
                "Pick the reaction that shows he understood what is asked: the mood of the message, or "
                "the work it plainly asks for (a request for bigger work: that activity, or the "
                "hourglass). A question or a chat is not work: pick for its mood. Make it fun and fitting."
            ),
            kind=Kind.PICK,
            labels=dict(reactions.PICKS),
            proceed=frozenset(reactions.PICKS),
        ),
    ),
    inputs=dict(_MESSAGE),
    error_cost="low",
    floor=dict(_REACTION_FLOOR),
    on_abstain="caution",
    on_failure="caution",
    consumer={
        "proceed": "the picked emoji replaces 👀 as the message's handling reaction (core/reactions.py)",
        "caution": "📝 replaces 👀",
    },
    serves="Mission item 6",
    guard="none: a label, no gate",
    calibrated=None,
)
COMPLETED = JudgementTask(
    site="reaction.completed",
    questions=(
        Question(
            id="reaction",
            text=(
                "Valor has finished handling this message and puts one emoji reaction on it. Pick the "
                "reaction that shows what came of it, fun and fitting. An activity emoji fits only when "
                "`did` names that activity (or, for `bound_as` start, the task it was handed to). "
                "`replied` says whether Valor sent text. No text sent means he agreed or acknowledged "
                "(he answers anything else in text), so the reaction is his answer: pick an affirmative "
                "or warm one (a thumbs up for a yes, a heart for thanks), never a doubtful one."
            ),
            kind=Kind.PICK,
            labels=dict(reactions.PICKS),
            proceed=frozenset(reactions.PICKS),
        ),
    ),
    inputs={
        **_MESSAGE,
        "handling": "the reaction on the message while it was handled, or none",
        "did": "what the turn that handled it did, its activities (core/reactions.py ORDER), or nothing",
        "replied": "the text the turn sent in reply to it, in its chat to another message, or no text sent",
        "outcome": "done, the turn ended in an error, or what binding did with it",
    },
    error_cost="low",
    floor=dict(_REACTION_FLOOR),
    on_abstain="caution",
    on_failure="caution",
    consumer={
        "proceed": "the picked emoji is the message's done reaction (core/reactions.py)",
        "caution": "📝 is its done reaction",
    },
    serves="Mission item 6",
    guard="none: a label, no gate",
    calibrated=None,
)

# Whether an untagged message in a mentions-only team chat is for Valor
# (docs/plans/3138d3b5-addressed.md). Four labels; the top one is taken at a
# floor over half, so an unsure leg abstains and the message stays heard.
ADDRESSED_LABELS = {
    "engaged": (
        "plainly in response to something Valor was already engaged in here (it answers or follows up "
        "his post or the work he is doing), or plainly asks him: he handles it as if he were named"
    ),
    "aside": (
        "not for Valor, but a teammate in his seat would add one short line: a question to the group "
        "he can clearly answer, or a wrong claim about his own work. Rare"
    ),
    "react": "not for Valor, but a teammate would acknowledge it with an emoji: good news, thanks, a nod",
    "none": "talk between others, a link or a share with no ask, chit-chat: Valor stays out. The usual answer",
}
ADDRESSED = JudgementTask(
    site="intake.addressed",
    questions=(
        Question(
            id="addressed",
            text=(
                "Valor is a teammate in this group chat, woken when someone names him or replies to his "
                "post. This message did neither. Who is it for, and what would a teammate in Valor's "
                "seat do? Most messages are between others: none is the usual answer, then react; aside "
                "is rare; engaged only when the message is clearly in response to something Valor was "
                "already engaged in, or clearly asks him."
            ),
            kind=Kind.PICK,
            labels=ADDRESSED_LABELS,
            proceed=frozenset(ADDRESSED_LABELS),
        ),
    ),
    inputs={
        "message": "the message, as `sender: text`, from its message.received row",
        "room": "the chat's messages before it in the session's ledger, Valor's sends as Valor, one per line",
        "valor_last": "how many messages and minutes since Valor last posted in the chat",
        "thread": "the earlier messages of its reply chain, oldest first, one per line, or none",
    },
    error_cost="low",
    floor={"primary": 0.5, "fallback": 0.5},
    on_abstain="caution",
    on_failure="caution",
    consumer={
        "proceed": (
            "engaged steers the chat session with the message as a mention does; aside steers it marked "
            "not addressed to you; react owes the message a done reaction; none leaves it heard "
            "(core/intake.py, address)"
        ),
        "caution": "the message stays heard, as before",
    },
    serves="Mission items 1 and 6",
    guard="none: a label that adds a wake or a reaction, no gate",
    calibrated=None,
    # Gemini first: on the private cases (2026-11-17) it caught 4 of 5 messages
    # meant for Valor, case 1 among them, where Jev caught 2 and missed case 1
    # (docs/plans/3138d3b5-addressed.md).
    primary="gemini",
)

TASKS: tuple[JudgementTask, ...] = (JUDGE, BREADTH, OVERLAP, HANDLING, COMPLETED, ADDRESSED)
BY_SITE = {t.site: t for t in TASKS}
